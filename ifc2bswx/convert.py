"""IFC -> BSWX conversion."""
import math, multiprocessing, os, re, time, uuid
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED, ALL_COMPLETED
import numpy as np
import ifcopenshell, ifcopenshell.geom, ifcopenshell.guid
import ifcopenshell.util.element as ue, ifcopenshell.util.placement as up, ifcopenshell.util.unit as uu

from .bswx import BSWX, Geometry, Part, Assembly
from .geomworker import build_batch
from .header import Header, TYPE_STRING, TYPE_DOUBLE, TYPE_INT, TYPE_BOOL

SKIP = ('IfcOpeningElement', 'IfcSpace', 'IfcGrid', 'IfcAnnotation', 'IfcSpatialStructureElement',
        'IfcElementAssembly', 'IfcVirtualElement', 'IfcStructuralItem', 'IfcProject', 'IfcPort')
BOLTS = ('IfcMechanicalFastener',)
STEEL_DENSITY = 7850.0                         # kg/m3, used when the IFC carries no weight
GREY, BLUE = (0x80, 0x80, 0x80, 0xff), (0xff, 0x00, 0x00, 0xff)   # colours are stored B,G,R,A

PROFILE_TYPES = {'IfcIShapeProfileDef': 'I', 'IfcUShapeProfileDef': 'U', 'IfcCShapeProfileDef': 'C',
                 'IfcLShapeProfileDef': 'L', 'IfcTShapeProfileDef': 'T', 'IfcZShapeProfileDef': 'Z',
                 'IfcCircleHollowProfileDef': 'RO', 'IfcRectangleHollowProfileDef': 'M',
                 'IfcCircleProfileDef': 'D', 'IfcRectangleProfileDef': 'PL'}


class Cancelled(Exception):
    pass


class Options:
    linear_deflection = 0.5        # mm, tessellation of curved surfaces
    angular_deflection = 0.5       # rad
    ifc_colours = False            # use IFC surface colours instead of grey parts / blue bolts
    extra_properties = True        # copy every IFC property into custom BSWX attributes
    recentre_limit = 1e6           # mm; models with coordinates beyond this are shifted near the origin
    threads = max(1, min(8, multiprocessing.cpu_count()))   # each worker process costs memory
    progress = None                # callback(done, total)
    cancelled = None               # callable returning True to abort
    log = staticmethod(lambda *a: print(*a, flush=True))


# --------------------------------------------------------------------------- property helpers
def _norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


class Props:
    """Case/spacing-insensitive lookup over all property sets and quantity sets of an element."""

    def __init__(self, el):
        self.psets = {k: {p: v for p, v in d.items() if p != 'id' and v is not None and v != ''}
                      for k, d in ue.get_psets(el).items()}
        self.flat = {}
        for ps, d in self.psets.items():
            for p, v in d.items():
                self.flat.setdefault(_norm(p), v)
                self.flat[_norm(ps + '.' + p)] = v
        self.used = set()

    def get(self, *names):
        for n in names:
            v = self.flat.get(_norm(n))
            if v is not None:
                self.used.add(_norm(n)); return v
        return None

    def num(self, *names):
        v = self.get(*names)
        try: return float(v) if v is not None else None
        except (TypeError, ValueError): return None


def ifc_guid_to_uuid(g):
    return str(uuid.UUID(ifcopenshell.guid.expand(g)))


def _material_name(el):
    m = ue.get_material(el, should_skip_usage=True)
    if m is None: return None
    if m.is_a('IfcMaterial'): n = m.Name
    elif m.is_a('IfcMaterialList') and m.Materials: n = m.Materials[0].Name
    elif hasattr(m, 'ForLayerSet') or m.is_a('IfcMaterialLayerSetUsage'): return None
    else: n = getattr(m, 'Name', None)
    if n and '/' in n: n = n.split('/', 1)[1]        # Tekla: "STEEL/S355JR"
    return None if not n or n.lower() == 'undefined' else n


def _profile_def(el):
    """First swept-solid profile in the body representation (follows mapped items)."""
    rep = el.Representation
    if not rep: return None
    stack = [i for r in rep.Representations for i in r.Items]
    while stack:
        it = stack.pop(0)
        if it.is_a('IfcMappedItem'): stack.extend(it.MappingSource.MappedRepresentation.Items)
        elif it.is_a('IfcBooleanResult'): stack.append(it.FirstOperand)
        elif it.is_a('IfcSweptAreaSolid'): return it.SweptArea
    return None


def _profile_entry(pd, k):
    """Profile catalogue entry (type, doubles) for parameterised IFC profiles, else None."""
    g = lambda a, d=0.0: float(getattr(pd, a, None) or d) * k
    t = pd.is_a()
    if t == 'IfcIShapeProfileDef':
        return 0, [g('OverallWidth'), g('OverallDepth'), g('WebThickness'), g('FlangeThickness'), g('FilletRadius'), 0, 0]
    if t in ('IfcUShapeProfileDef',):
        return 1, [g('FlangeWidth'), g('Depth'), g('WebThickness'), g('FlangeThickness'), g('FilletRadius'), 0, 0]
    if t == 'IfcTShapeProfileDef':
        return 2, [g('FlangeWidth'), g('Depth'), g('WebThickness'), g('FlangeThickness'), g('FilletRadius'), 0, 0, 0, 0]
    if t == 'IfcLShapeProfileDef':
        return 3, [g('Depth'), g('Width') or g('Depth'), g('Thickness'), g('FilletRadius'), g('EdgeRadius')]
    if t == 'IfcCircleHollowProfileDef':
        return 4, [g('Radius'), g('WallThickness')]
    if t == 'IfcRectangleHollowProfileDef':
        return 5, [g('XDim'), g('YDim'), g('WallThickness'), g('OuterFilletRadius'), g('InnerFilletRadius')]
    return None


# --------------------------------------------------------------------------- converter
class Converter:
    def __init__(self, ifc_path, opts=None):
        self.opts = opts or Options()
        self.log = self.opts.log
        t = time.time()
        self.f = ifcopenshell.open(ifc_path)
        self.ifc_path = ifc_path
        self.k = uu.calculate_unit_scale(self.f) * 1000.0         # file length unit -> mm
        self.log(f'opened {os.path.basename(ifc_path)} ({self.f.schema}) in {time.time() - t:.1f}s, '
                 f'length unit = {self.k:g} mm')

    # ---------------------------------------------------------------- geometry
    def _settings(self):
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', False); s.set('weld-vertices', True); s.set('convert-back-units', True)
        s.set('mesher-linear-deflection', self.opts.linear_deflection / self.k)
        s.set('mesher-angular-deflection', self.opts.angular_deflection)
        return s

    def elements(self):
        return [e for e in self.f.by_type('IfcProduct')
                if e.Representation and not any(e.is_a(c) for c in SKIP)]

    def shapes(self, elements):
        it = ifcopenshell.geom.iterator(self._settings(), self.f, self.opts.threads, include=elements)
        if not it.initialize(): return
        while True:
            yield it.get()
            if not it.next(): break

    def _shape_input(self, shape):
        """Plain data from an iterator shape, to be processed by build_geometry in a worker."""
        g = shape.geometry
        M = np.array(shape.transformation.matrix, dtype=np.float64).reshape(4, 4)   # row vectors
        M[3, :3] *= 1000.0                                                         # metres -> mm
        colour = None
        if g.materials:
            m = g.materials[0]
            if m.diffuse is not None:
                r, gg, b = (int(round(255 * max(0.0, min(1.0, c)))) for c in m.diffuse.components)
                colour = (b, gg, r, 255)
        return (shape.id, np.asarray(g.verts, dtype=np.float64) * self.k,
                np.asarray(g.faces, dtype=np.int64), M, colour)

    # ---------------------------------------------------------------- attributes
    def _part_attrs(self, el, g, props):
        P = props; a = {}
        is_bolt = el.is_a('IfcMechanicalFastener')
        a['GUID'] = ifc_guid_to_uuid(el.GlobalId)
        tid = P.get('Tekla Common.ID', 'ID')
        a['ID'] = int(tid) if isinstance(tid, (int, float)) and abs(tid) < 2**31 else el.id()
        a['Name'] = el.Name or el.is_a()[3:]
        dims = sorted(g['hi'] - g['lo'], reverse=True)
        L, H, W = float(g['hi'][0] - g['lo'][0]), float(g['hi'][1] - g['lo'][1]), float(g['hi'][2] - g['lo'][2])
        weight = P.num('Tekla Quantity.Weight', 'BaseQuantities.NetWeight', 'NetWeight', 'Weight', 'WEIGHT')
        if is_bolt:
            a['ElementType'] = 'IfcMechanicalFastener'
            a['Size'] = P.num('Bolt size', 'BOLT SIZE', 'NominalDiameter', 'Diameter')
            a['Length'] = P.num('Bolt length', 'BOLT LENGTH', 'NominalLength') or dims[0]
            a['Gross Length'] = a['Length']
            a['Bolt Standard'] = P.get('Bolt standard', 'BOLT STANDARD', 'BOLT DESCRIPTION')
            a['Bolt Type'] = P.get('Bolt Name', 'BOLT TYPE', 'BOLT CONDITION')
            bc = P.num('Bolt count', 'BOLT COUNT')
            a['Bolt Count'] = int(bc) if bc is not None else None
            a['Nut Type'] = P.get('Nut name', 'Nut type')
            a['Washer Type'] = P.get('Washer name', 'Washer type')
            a['Profile'] = P.get('Bolt Name', 'BOLT CONDITION') or el.Name
            a['Material'] = P.get('Bolt material', 'Material') or _material_name(el)
        else:
            a['ElementType'] = el.is_a()
            a['Part Number'] = P.get('Tekla Common.PART MARK', 'PART MARK', 'Part mark', 'PART_POS', 'Preliminary mark') \
                or P.get('Reference') or (el.Tag if el.Tag and not el.Tag.startswith('ID') else None)
            a['Mark'] = a['Part Number']
            a['Profile'] = P.get('Tekla Common.PROFILE', 'PROFILE', 'Profile') or el.Description or el.ObjectType
            pd = _profile_def(el)
            ptype = PROFILE_TYPES.get(pd.is_a()) if pd is not None else None
            if el.is_a('IfcPlate'): ptype = 'PL'
            a['Profile Type'] = ptype
            a['Material'] = P.get('Tekla Common.MATERIAL', 'MATERIAL', 'Grade') or _material_name(el)
            a['Finish'] = P.get('PAINT CODE', 'FINISH', 'Finish', 'Surface treatment')
            a['Class'] = None if P.get('Class', 'CLASS') is None else str(P.get('Class', 'CLASS'))
            a['Length'] = P.num('Tekla Quantity.Length', 'BaseQuantities.Length', 'Tekla Common.LENGTH', 'LENGTH') or L
            a['Gross Length'] = a['Length']
            a['Height'] = P.num('Tekla Quantity.Height', 'BaseQuantities.Height', 'HEIGHT') or H
            a['Width'] = P.num('Tekla Quantity.Width', 'BaseQuantities.Width', 'WIDTH') or W
            a['Density'] = STEEL_DENSITY
            a['Cambering'] = P.num('CAMBER') or 0.0
        a['Area'] = g['area']; a['Volume'] = g['vol']
        if weight is None: weight = g['vol'] * STEEL_DENSITY * 1e-9
        a['Weight'] = a['Net Weight'] = a['Gross Weight'] = weight
        if is_bolt: a.pop('Net Weight')
        a['Centre Of Gravity'] = g['cog']
        ph = P.num('Tekla Common.Phase', 'Phase', 'PHASE')
        if ph is not None:
            a['Phase Number'] = int(ph); a['Phase Name'] = str(P.get('Phase name') or f'Phase {int(ph)}')
        return a

    # ---------------------------------------------------------------- assemblies
    def _assemblies(self, part_index):
        """[(assembly entity or None, [part indices])] - top-level IfcElementAssembly, flattened."""
        def leaves(a):
            out = []
            for rel in a.IsDecomposedBy:
                for o in rel.RelatedObjects:
                    if o.is_a('IfcElementAssembly'): out += leaves(o)
                    elif o.id() in part_index: out.append(part_index[o.id()])
            return out
        groups, assigned = [], set()
        for a in self.f.by_type('IfcElementAssembly'):
            if any(r.RelatingObject.is_a('IfcElementAssembly') for r in a.Decomposes): continue
            mem = sorted(set(leaves(a)))
            if mem: groups.append((a, mem)); assigned.update(mem)
        return groups, assigned

    # ---------------------------------------------------------------- grid / levels
    def _grid(self):
        lines = []
        for grid in self.f.by_type('IfcGrid'):
            Mg = np.array(up.get_local_placement(grid.ObjectPlacement)) if grid.ObjectPlacement else np.eye(4)
            for axis in list(grid.UAxes or []) + list(grid.VAxes or []) + list(grid.WAxes or []):
                c = axis.AxisCurve; pts = None
                if c.is_a('IfcPolyline'): pts = [p.Coordinates for p in (c.Points[0], c.Points[-1])]
                elif c.is_a('IfcTrimmedCurve') and c.BasisCurve.is_a('IfcLine'):
                    pts = [t[0].Coordinates for t in (c.Trim1, c.Trim2) if t and t[0].is_a('IfcCartesianPoint')]
                if not pts or len(pts) != 2: continue
                w = []
                for p in pts:
                    p = list(p) + [0.0] * (3 - len(p))
                    q = Mg @ np.array(p + [1.0])
                    w.append(tuple(float(x) * self.k for x in q[:3]))
                lines.append((axis.AxisTag or '', w[0], w[1]))
        levels, seen = [], set()
        for s in sorted(self.f.by_type('IfcBuildingStorey'), key=lambda s: s.Elevation or 0.0):
            e = float(s.Elevation or 0.0) * self.k
            name = s.Name if s.Name and s.Name.lower() != 'undefined' else f'{e:+.0f}'
            if name in seen: name = f'{name} ({e:+.0f})'
            if name in seen: continue
            seen.add(name); levels.append((name, e))
        return lines, levels

    # ---------------------------------------------------------------- main
    def run(self, out_path):
        t0 = time.time(); opts = self.opts
        model_name = os.path.basename(self.ifc_path)
        els = self.elements()
        self.log(f'{len(els)} elements with geometry')
        by_id = {e.id(): e for e in els}
        results = {}
        n = 0; last = time.time(); tris = 0
        workers = max(1, opts.threads - 1)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending, batch = set(), []

            def collect(block):
                nonlocal pending, tris
                done, pending = wait(pending, return_when=FIRST_COMPLETED if block else ALL_COMPLETED)
                for fut in done:
                    for eid, g in fut.result():
                        el = by_id[eid]
                        if isinstance(g, str): self.log(f'  geometry failed for {el.is_a()} {el.GlobalId}: {g}')
                        elif g is not None: results[eid] = g; tris += g['ntris']

            tick = 0.0
            for sh in self.shapes(els):
                n += 1
                if time.time() - tick > 0.25:
                    tick = time.time()
                    if opts.cancelled and opts.cancelled():
                        for fut in pending: fut.cancel()
                        raise Cancelled('conversion cancelled')
                    if opts.progress: opts.progress(n, len(els))
                if sh.id not in by_id: continue
                batch.append(self._shape_input(sh))
                if len(batch) >= 32:
                    pending.add(pool.submit(build_batch, batch)); batch = []
                    if len(pending) >= 4 * workers: collect(True)
                if time.time() - last > 5:
                    last = time.time(); self.log(f'  {n}/{len(els)} shapes ({time.time() - t0:.0f}s)')
            if batch: pending.add(pool.submit(build_batch, batch))
            collect(False)
        parts = [(e, results[e.id()]) for e in els if e.id() in results]   # (element, geometry dict)
        missing = len(els) - len(parts)
        self.log(f'geometry: {len(parts)} parts, {tris} triangles, {missing} without usable geometry '
                 f'({time.time() - t0:.0f}s)')

        # BSWX stores placements as float32: georeferenced coordinates (e.g. 6e9 mm) would lose
        # hundreds of mm, so models far from the origin are shifted by a whole-metre offset.
        self.offset = np.zeros(3)
        if parts:
            T = np.array([g['matrix'][3, :3] for _, g in parts])
            lo, hi = T.min(axis=0), T.max(axis=0)
            far = np.maximum(np.abs(lo), np.abs(hi)) > opts.recentre_limit
            self.offset = np.where(far, np.floor(lo / 1000.0) * 1000.0, 0.0)
            if far.any():
                for _, g in parts:
                    g['matrix'][3, :3] -= self.offset
                    g['cog'] = tuple(np.array(g['cog']) - self.offset)
                self.log(f'model is far from the origin: coordinates shifted by '
                         f'{-self.offset[0]:.0f}, {-self.offset[1]:.0f}, {-self.offset[2]:.0f} mm')

        hdr = Header()
        doc = BSWX(); doc.header = hdr.r
        doc._defs(); tag = dict(doc.tag_of)
        part_index = {el.id(): i for i, (el, g) in enumerate(parts)}

        # attributes (built first so custom attribute definitions can be registered)
        part_attrs, extra_defs, extras, profiles = [], {}, [], {}
        for el, g in parts:
            P = Props(el)
            a = self._part_attrs(el, g, P)
            part_attrs.append(a)
            ex = {}
            if opts.extra_properties:
                for ps, d in P.psets.items():
                    for p, v in d.items():
                        name = f'{ps}.{p}'
                        if isinstance(v, bool): t = TYPE_BOOL
                        elif isinstance(v, int) and abs(v) < 2**31: t = TYPE_INT
                        elif isinstance(v, (int, float)): t = TYPE_DOUBLE; v = float(v)
                        else: t = TYPE_STRING; v = str(v)
                        if extra_defs.setdefault(name, t) != t:
                            continue                       # same property with mixed types: keep first type
                        ex[name] = v
            extras.append(ex)
            pd = _profile_def(el) if not el.is_a('IfcMechanicalFastener') else None
            if pd is not None and a.get('Profile') and a['Profile'] not in profiles:
                e = _profile_entry(pd, self.k)
                if e: profiles[a['Profile']] = e
        new_tags = hdr.add_attributes(sorted(extra_defs.items()))
        doc._defs(); tag = dict(doc.tag_of)

        def encode(a):
            out = [(tag['Model'], model_name)]
            for k, v in a.items():
                if v is None or k not in tag: continue
                out.append((tag[k], v))
            return out

        # parts and geometry
        for i, ((el, g), a, ex) in enumerate(zip(parts, part_attrs, extras)):
            colour = g['colour'] if opts.ifc_colours and g['colour'] else (BLUE if el.is_a('IfcMechanicalFastener') else GREY)
            doc.geoms.append(g['geom'])
            attrs = encode(a) + [(tag[k], v) for k, v in ex.items() if k in tag]
            doc.parts.append(Part(matrix=tuple(map(float, g['matrix'].reshape(-1))), geom=i, attrs=attrs,
                                  guid=a['GUID'], color=colour, color2=colour, tail=bytes(colour) * 2))

        # assemblies
        groups, assigned = self._assemblies(part_index)
        for i, (el, g) in enumerate(parts):
            if i not in assigned and not el.is_a('IfcMechanicalFastener'):
                groups.append((None, [i]))
        for asm, mem in groups:
            pa = [part_attrs[m] for m in mem]
            main_i = max(mem, key=lambda m: (not parts[m][0].is_a('IfcMechanicalFastener'), part_attrs[m]['Weight']))
            main = part_attrs[main_i]
            P = Props(asm) if asm is not None else None
            mark = (P.get('Assembly/Cast unit Mark', 'ASSEMBLY_POS', 'Assembly mark') if P else None) \
                or (asm.Tag if asm is not None and asm.Tag and not asm.Tag.startswith('ID') else None) \
                or Props(parts[main_i][0]).get('Tekla Common.ASSEMBLY MARK', 'ASSEMBLY MARK', 'Assembly mark') \
                or main.get('Part Number')
            w = sum(x['Weight'] for x in pa)
            cog = tuple(sum(x['Weight'] * x['Centre Of Gravity'][j] for x in pa) / w if w else main['Centre Of Gravity'][j]
                        for j in range(3))
            a = {'GUID': ifc_guid_to_uuid(asm.GlobalId) if asm is not None else str(uuid.uuid4()),
                 'ID': asm.id() if asm is not None else main['ID'],
                 'Assembly Number': mark, 'Mark': mark, 'Assembly Type': 'Steel Assembly',
                 'Main Part Number': main.get('Part Number'), 'Name': (asm.Name if asm is not None and asm.Name else main['Name']),
                 'Length': main.get('Length'), 'Gross Length': main.get('Length'),
                 'Width': main.get('Width'), 'Height': main.get('Height'),
                 'Area': sum(x['Area'] for x in pa), 'Volume': sum(x['Volume'] for x in pa),
                 'Weight': w, 'Gross Weight': w, 'Centre Of Gravity': cog,
                 'Phase Number': main.get('Phase Number'), 'Phase Name': main.get('Phase Name')}
            for m in mem:                                   # parts inherit their assembly number
                if mark and parts[m][0] is not None:
                    pt = doc.parts[m]
                    if tag['Assembly Number'] not in [t for t, _ in pt.attrs]:
                        pt.attrs.insert(1, (tag['Assembly Number'], mark))
            doc.assemblies.append(Assembly(members=mem, attrs=encode(a), parent=-1, key=a['GUID']))

        # header: bounding box, camera, grid, levels
        if parts:
            corners = []
            for el, g in parts:
                lo, hi = g['lo'], g['hi']
                c = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
                corners.append(c @ g['matrix'])
            C = np.vstack(corners)[:, :3]; lo, hi = C.min(axis=0), C.max(axis=0)
        else:
            lo, hi = np.zeros(3), np.ones(3)
        hdr.set_model(model_name, lo, hi)
        lines, levels = self._grid()
        o = self.offset
        lines = [(lab, tuple(np.array(a) - o), tuple(np.array(b) - o)) for lab, a, b in lines]
        levels = [(name, e - o[2]) for name, e in levels]
        hdr.set_grid(lines, levels)
        doc.geom_flags = [0] * len(doc.parts)
        doc.tail = self._tail(profiles)
        doc.save(out_path)
        self.log(f'wrote {out_path.removesuffix(".part")}: {len(doc.parts)} parts, {len(doc.assemblies)} assemblies, '
                 f'{len(new_tags)} custom attributes, {len(lines)} grid lines, {len(levels)} levels, '
                 f'{os.path.getsize(out_path) / 1e6:.1f} MB in {time.time() - t0:.0f}s')
        return doc

    @staticmethod
    def _tail(profiles):
        import struct
        from .nrbf import write_lps
        out = bytearray(struct.pack('<ii', 0, len(profiles)))
        for name, (t, vals) in profiles.items():
            out += bytes([t]) + write_lps(name) + struct.pack('<%dd' % len(vals), *vals)
        out += b'\x00' * 32            # empty drawing / attachment sections (as in files without PDFs)
        return bytes(out)


def convert(ifc_path, out_path, opts=None):
    return Converter(ifc_path, opts).run(out_path)
