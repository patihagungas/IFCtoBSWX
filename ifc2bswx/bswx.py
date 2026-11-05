"""Read/write StruWalker / BIMReview .bswx files. Format notes: BSWX_FORMAT.md."""
import struct, zlib
from dataclasses import dataclass, field
from .nrbf import Reader, Writer, Obj, Ref, StrRec, write_lps

DOUBLE_TYPES = ('System.Double', 'AC.Shared.Utility.Length', 'AC.Shared.Utility.Area',
                'AC.Shared.Utility.Volume', 'AC.Shared.Utility.Mass')
NO_INDEX = b'\xff' * 4


@dataclass
class Body:
    verts: list                 # [(x,y,z)] float64, local coords
    edges: list                 # [(v0, v1, type, circle|None)]  circle = (cx,cy,cz, ax,ay,az, r)
    faces: list                 # [(type, surface, loops)]  loops = [[(edge, dir)]]
    flag: int = 1


@dataclass
class Geometry:
    bodies: list
    boxes: list                 # one f32 bbox per body
    gid: int = 0
    z: int = 0
    attrs: list = field(default_factory=list)       # [(tag, value)]
    raw: bytes = None           # pre-serialised body block (u16 nBodies + bodies), replaces `bodies`


@dataclass
class Part:
    matrix: tuple               # 16 f32, row-major, row vectors (translation in row 3)
    geom: int
    attrs: list                 # [(tag, value)]
    guid: str
    color: tuple = (128, 128, 128, 255)
    color2: tuple = (128, 128, 128, 255)
    flag: int = 3
    pre: bytes = b'\xff\xff'    # 2 bytes before the weld index
    weld_index: bytes = NO_INDEX
    term: int = 0xff
    tail: bytes = b'\x00\x00\x00\xff\x00\x00\x00\xff'
    extra: bytes = None         # undecoded Weld block (only when weld_index != -1)


@dataclass
class Assembly:
    members: list
    attrs: list
    parent: int = -1
    key: str = ''


class DateTimeRaw(bytes):
    """Inline BinaryFormatter blob kept verbatim."""


def datetime_blob(ticks, kind=0):
    """BinaryFormatter serialisation of System.DateTime (kind: 0 unspecified, 1 utc, 2 local)."""
    data = ticks | (kind << 62)
    return DateTimeRaw(b'\x00\x01\x00\x00\x00\xff\xff\xff\xff\x01\x00\x00\x00\x00\x00\x00\x00'
                       b'\x04\x01\x00\x00\x00\x0fSystem.DateTime\x02\x00\x00\x00\x05ticks\x08dateData\x00\x00\x09\x10'
                       + struct.pack('<qQ', ticks, data) + b'\x0b')


class BSWX:
    """In-memory .bswx document. `header` is the NRBF object graph (nrbf.Reader)."""

    def __init__(self):
        self.prefix = b'\x00\x10'
        self.header = None
        self.geom_version = 1
        self.geoms, self.geom_flags, self.parts, self.assemblies = [], [], [], []
        self.tail = b''

    # ------------------------------------------------------------------ header helpers
    def objs(self, suffix):
        return [o for o in self.header.objs.values() if isinstance(o, Obj) and o.cls.endswith(suffix)]

    def val(self, v):
        if isinstance(v, Ref): v = self.header.objs.get(v.id, v)
        if isinstance(v, StrRec): return v.s
        if isinstance(v, Obj) and len(v.members) == 1: return self.val(v.values[0])
        if isinstance(v, Obj): return {m: self.val(x) for m, x in zip(v.members, v.values)}
        return v

    def deref(self, v):
        return self.header.objs[v.id] if isinstance(v, Ref) else v

    @property
    def model(self):
        return self.objs('.StruWalkerModel')[0]

    def attribute_defs(self):
        lst = self.deref(self.model.get('mAttributeDefinitions'))
        arr = self.deref(lst.get('_items'))
        out = []
        for it in arr.items[:lst.get('_size')]:
            a = self.deref(it)
            out.append((self.val(a.get('mName')), self.val(a.get('mDataType'))['Data']))
        return out

    def _defs(self):
        self.defs = self.attribute_defs()
        self.tag_of = {n: i for i, (n, t) in enumerate(self.defs)}

    # ------------------------------------------------------------------ reading
    @classmethod
    def read(cls, path):
        self = cls(); raw = open(path, 'rb').read()
        self.prefix = raw[:2]
        self.d = zlib.decompress(raw[2:], 16 + zlib.MAX_WBITS)
        r = Reader(self.d); r.parse(); self.header = r; self.p = r.p
        self._defs()
        mn = self.val(self.model.get('mName')).encode('utf-8')
        U = self._U
        self.geom_version, n = U('<ii')
        self.geoms = [self._read_geom() for _ in range(n)]
        n, = U('<i'); self.geom_flags = list(U('<%dH' % n))
        self.parts = []
        for k in range(n):
            self.parts.append(self._read_part(k == n - 1, n, mn))
        n, = U('<i'); self.assemblies = []
        for k in range(n):
            nm, = U('<i'); mem = list(U('<%di' % nm)); attrs = self._read_attrs(); par, = U('<i')
            self.assemblies.append(Assembly(mem, attrs, par, self._lps()))
        self.tail = self.d[self.p:]
        del self.d
        return self

    def _U(self, f):
        v = struct.unpack_from(f, self.d, self.p); self.p += struct.calcsize(f); return v

    def _lps(self):
        d = self.d; n = 0; sh = 0
        while True:
            b = d[self.p]; self.p += 1; n |= (b & 0x7f) << sh; sh += 7
            if not b & 0x80: break
        s = d[self.p:self.p + n].decode('utf-8'); self.p += n; return s

    def _read_body(self, flag):
        U = self._U
        nv, = U('<i'); verts = [U('<3d') for _ in range(nv)]
        ne, = U('<i'); edges = []
        for _ in range(ne):
            a, b, t = U('<iiB')
            if t == 0: edges.append((a, b, 0, None))
            elif t == 1: edges.append((a, b, 1, U('<7d')))
            else: raise ValueError(f'edge type {t} at {self.p}')
        nf, = U('<i'); faces = []
        for _ in range(nf):
            t, = U('<B')
            if t == 0: surf = U('<4d')
            elif t == 1: surf = U('<8d')
            else: raise ValueError(f'face type {t} at {self.p}')
            nl, = U('<i'); loops = []
            for _ in range(nl):
                m, = U('<i'); loops.append([U('<ib') for _ in range(m)])
            faces.append((t, surf, loops))
        return Body(verts, edges, faces, flag)

    def _read_geom(self):
        U = self._U
        z, nb = U('<HH'); boxes = [U('<6f') for _ in range(nb)]
        gid, nbod = U('<iH'); bodies = []
        for _ in range(nbod):
            fl, = U('<H'); bodies.append(self._read_body(fl))
        return Geometry(bodies, boxes, gid, z, self._read_attrs())

    def _read_value(self, t):
        U = self._U
        if t == 'System.String': return self._lps()
        if t == 'System.Int32': return U('<i')[0]
        if t in DOUBLE_TYPES: return U('<d')[0]
        if t == 'System.Single': return U('<f')[0]
        if t == 'System.Boolean': return U('<?')[0]
        if t == 'AC.Shared.Geometry.Vector3': return U('<3d')
        st = self.p; rr = Reader(self.d); rr.p = st; rr.parse(); self.p = rr.p
        return DateTimeRaw(self.d[st:self.p])

    def _read_attrs(self):
        na, = self._U('<H'); out = []
        for _ in range(na):
            tag, = self._U('<H'); out.append((tag, self._read_value(self.defs[tag][1])))
        return out

    def _read_part(self, last, nparts, model_name):
        U = self._U; d = self.d
        M = U('<16f'); flag, = U('<B'); c1 = U('<4B'); c2 = U('<4B')
        pre = d[self.p:self.p + 2]; widx = d[self.p + 2:self.p + 6]; self.p += 6
        idx, = U('<i'); attrs = self._read_attrs()
        term, = U('<B'); guid = self._lps(); tail = d[self.p:self.p + 8]; self.p += 8
        extra = self._skip_weld(last, nparts, model_name) if widx != NO_INDEX else None
        return Part(M, idx, attrs, guid, c1, c2, flag, pre, widx, term, tail, extra)

    def _skip_weld(self, last, nparts, mn):
        # TODO: decode the Weld serialisation; for now locate the next record boundary
        d = self.d; st = self.p
        if last:
            for q in range(st, len(d) - 16):
                na, nm = struct.unpack_from('<ii', d, q)
                if 0 < na <= nparts and 0 < nm <= nparts:
                    o = q + 8 + 4 * nm
                    if o + 5 + len(mn) < len(d) and d[o + 2:o + 4] == b'\x00\x00' and d[o + 4] == len(mn) \
                            and d[o + 5:o + 5 + len(mn)] == mn \
                            and all(0 <= x < nparts for x in struct.unpack_from('<%di' % nm, d, q + 8)):
                        self.p = q; return d[st:q]
            raise ValueError('assembly table not found after weld block')
        q = st
        while True:
            q = d.find(b'\x00\x00\x80\x3f\x03', q + 1)
            if q < 0: raise ValueError('next part not found after weld block')
            M = struct.unpack_from('<16f', d, q - 60)
            if all(abs(M[i]) < 1e-6 for i in (3, 7, 11)) and all(abs(v) <= 1.0001 for v in M[:12]):
                self.p = q - 60; return d[st:self.p]

    # ------------------------------------------------------------------ writing
    def payload(self):
        self._defs()
        out = bytearray(Writer().stream(self.header.header, self.header.records))
        P = lambda f, *v: out.extend(struct.pack(f, *v))
        P('<ii', self.geom_version, len(self.geoms))
        for g in self.geoms:
            P('<HH', g.z, len(g.boxes))
            for b in g.boxes: P('<6f', *b)
            body = g.raw if g.raw is not None else self.body_block(g.bodies)
            P('<i', len(body)); out += body          # gid == byte length of the body block
            self._write_attrs(out, g.attrs)
        flags = self.geom_flags or [0] * len(self.parts)
        P('<i', len(flags)); P('<%dH' % len(flags), *flags)
        for p in self.parts:
            P('<16f', *p.matrix); P('<B', p.flag); P('<4B', *p.color); P('<4B', *p.color2)
            out += p.pre + p.weld_index; P('<i', p.geom)
            self._write_attrs(out, p.attrs)
            P('<B', p.term); out += write_lps(p.guid) + p.tail
            if p.extra is not None: out += p.extra
        P('<i', len(self.assemblies))
        for a in self.assemblies:
            P('<i', len(a.members)); P('<%di' % len(a.members), *a.members)
            self._write_attrs(out, a.attrs); P('<i', a.parent); out += write_lps(a.key)
        out += self.tail
        return bytes(out)

    def save(self, path):
        c = zlib.compressobj(6, zlib.DEFLATED, 31)
        data = self.prefix + c.compress(self.payload()) + c.flush()
        with open(path, 'wb') as f: f.write(data)

    @classmethod
    def body_block(cls, bodies):
        out = bytearray(struct.pack('<H', len(bodies)))
        for b in bodies:
            out += struct.pack('<H', b.flag); cls._write_body(out, b)
        return bytes(out)

    @staticmethod
    def _write_body(out, b):
        P = lambda f, *v: out.extend(struct.pack(f, *v))
        P('<i', len(b.verts))
        for v in b.verts: P('<3d', *v)
        P('<i', len(b.edges))
        for v0, v1, t, c in b.edges:
            P('<iiB', v0, v1, t)
            if t == 1: P('<7d', *c)
        P('<i', len(b.faces))
        for t, surf, loops in b.faces:
            P('<B', t); P('<%dd' % (4 if t == 0 else 8), *surf); P('<i', len(loops))
            for lp in loops:
                P('<i', len(lp))
                for e, dr in lp: P('<ib', e, dr)

    def _write_attrs(self, out, attrs):
        out += struct.pack('<H', len(attrs))
        for tag, v in attrs:
            out += struct.pack('<H', tag); t = self.defs[tag][1]
            if t == 'System.String': out += write_lps(v)
            elif t == 'System.Int32': out += struct.pack('<i', v)
            elif t in DOUBLE_TYPES: out += struct.pack('<d', v)
            elif t == 'System.Single': out += struct.pack('<f', v)
            elif t == 'System.Boolean': out += struct.pack('<?', v)
            elif t == 'AC.Shared.Geometry.Vector3': out += struct.pack('<3d', *v)
            elif isinstance(v, (bytes, DateTimeRaw)): out += v
            else: raise TypeError(f'no encoder for attribute {self.defs[tag][0]} ({t})')

    # convenience
    def attr_dict(self, attrs):
        return {self.defs[t][0]: v for t, v in attrs}
