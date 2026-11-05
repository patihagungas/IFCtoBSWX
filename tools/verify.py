"""Verify a .bswx: re-read it, validate every B-rep body, optionally compare with the source IFC
and render a wireframe preview.

    python tools/verify.py out.bswx [--ifc source.ifc] [--png preview.png]
"""
import argparse, os, sys, time, random
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np
from ifc2bswx.bswx import BSWX
from ifc2bswx.brep import check_body, brep_area


def world_points(b, part):
    M = np.array(part.matrix, dtype=np.float64).reshape(4, 4)
    g = b.geoms[part.geom]
    out = []
    for body in g.bodies:
        P = np.c_[np.array(body.verts), np.ones(len(body.verts))] @ M
        out.append(P[:, :3])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bswx'); ap.add_argument('--ifc'); ap.add_argument('--png')
    ap.add_argument('--samples', type=int, default=300)
    ap.add_argument('--offset', type=float, nargs=3, default=[0, 0, 0], help='recentring offset logged by the converter')
    ap.add_argument('--deflection', type=float, default=0.5, help='same tessellation tolerance as the conversion')
    a = ap.parse_args()
    t = time.time()
    b = BSWX.read(a.bswx)
    print(f'read OK in {time.time() - t:.1f}s: {len(b.geoms)} geometries, {len(b.parts)} parts, '
          f'{len(b.assemblies)} assemblies, {len(b.defs)} attribute definitions')
    # structure
    errors = Counter(); warnings = Counter()
    for i, p in enumerate(b.parts):
        if p.geom != i: errors['part geometry index'] += 1
        for body in b.geoms[p.geom].bodies:
            for pr in check_body(body):
                if pr.endswith('used [1]') or pr.endswith('used [-1]'): warnings['open boundary edges'] += 1
                else: errors['topology: ' + pr] += 1
            for t_, surf, loops in body.faces:
                if abs(np.linalg.norm(surf[:3]) - 1) > 1e-6: errors['non-unit normal'] += 1
                if brep_area(type(body)(body.verts, body.edges, [(t_, surf, loops[:1])])) <= 0: errors['outer loop not CCW'] += 1
        M = np.array(p.matrix).reshape(4, 4)
        if abs(abs(np.linalg.det(M[:3, :3])) - 1) > 1e-3: errors['non-rigid matrix'] += 1
    members = Counter(m for asm in b.assemblies for m in asm.members)
    if any(m >= len(b.parts) for m in members): errors['assembly member out of range'] += 1
    if any(v > 1 for v in members.values()): errors['part in several assemblies'] += 1
    nb = sum(len(g.bodies) for g in b.geoms); nf = sum(len(x.faces) for g in b.geoms for x in g.bodies)
    print(f'bodies {nb}, faces {nf}, parts in assemblies {len(members)}')
    print('structure:', 'all checks passed' if not errors else dict(list(errors.items())[:10]))
    if warnings: print('warnings :', dict(warnings), '(open shells in the source geometry)')
    et = Counter(b.attr_dict(p.attrs).get('ElementType') for p in b.parts)
    print('element types:', dict(et.most_common()))
    print('sample part :', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in list(b.attr_dict(b.parts[0].attrs).items())[:24]})
    if b.assemblies:
        print('sample asm  :', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in b.attr_dict(b.assemblies[0].attrs).items()})

    if a.ifc:
        import ifcopenshell, ifcopenshell.geom as G
        f = ifcopenshell.open(a.ifc)
        k = __import__('ifcopenshell.util.unit', fromlist=['x']).calculate_unit_scale(f) * 1000
        s = G.settings(); s.set('use-world-coords', True); s.set('weld-vertices', True); s.set('convert-back-units', True)
        s.set('mesher-linear-deflection', a.deflection / k); s.set('mesher-angular-deflection', 0.5)
        by_guid = {b.attr_dict(p.attrs)['GUID']: p for p in b.parts}
        import uuid, ifcopenshell.guid
        guid = lambda e: str(uuid.UUID(ifcopenshell.guid.expand(e.GlobalId)))
        els = [e for e in f.by_type('IfcProduct') if e.Representation and guid(e) in by_guid]
        random.seed(2); els = random.sample(els, min(a.samples, len(els)))
        ifc_parts = len([e for e in f.by_type('IfcProduct') if e.Representation])
        # world-coordinate tessellation straight from IfcOpenShell (create_shape returns empty
        # meshes for some faceted B-reps, the iterator does not)
        it = G.iterator(s, f, 4, include=els); n = 0; worst = (0.0, None); empty = 0
        if it.initialize():
            while True:
                sh = it.get(); e = f.by_id(sh.id)
                W = np.array(sh.geometry.verts).reshape(-1, 3) * k
                if len(W):
                    P = np.vstack(world_points(b, by_guid[guid(e)])) + a.offset
                    err = max(np.abs(W.min(0) - P.min(0)).max(), np.abs(W.max(0) - P.max(0)).max())
                    if err > worst[0]: worst = (err, f'{e.is_a()} {e.GlobalId}')
                    n += 1
                else: empty += 1
                if not it.next(): break
        print(f'IFC comparison: {len(by_guid)} of {ifc_parts} IFC products present; {n} random parts compared, '
              f'worst world bounding-box deviation {worst[0]:.4f} mm ({worst[1]})')

    if a.png:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.collections import LineCollection
        az, el = np.radians(-60), np.radians(30)
        R = np.array([[np.cos(az), -np.sin(az) * np.sin(el)], [np.sin(az), np.cos(az) * np.sin(el)], [0, np.cos(el)]])
        segs, cols = [], []
        for p in b.parts:
            c = np.array(p.color[2::-1]) / 255.0
            for body, P in zip(b.geoms[p.geom].bodies, world_points(b, p)):
                Q = P @ R
                for v0, v1, _, _ in body.edges:
                    segs.append((Q[v0], Q[v1])); cols.append(c)
        fig, ax = plt.subplots(figsize=(16, 10), dpi=110)
        ax.add_collection(LineCollection(segs, colors=cols, linewidths=0.25))
        ax.autoscale(); ax.set_aspect('equal'); ax.axis('off')
        ax.set_title(os.path.basename(a.bswx) + f' — {len(b.parts)} parts (re-read from .bswx)')
        fig.savefig(a.png, bbox_inches='tight'); print('preview:', a.png)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
