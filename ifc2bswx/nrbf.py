"""Minimal MS-NRBF (.NET BinaryFormatter) reader/writer that preserves record structure,
so a parsed stream can be modified and written back byte-for-byte."""
import struct, zlib

PRIM = {1: 'Boolean', 2: 'Byte', 3: 'Char', 5: 'Decimal', 6: 'Double', 7: 'Int16', 8: 'Int32', 9: 'Int64',
        10: 'SByte', 11: 'Single', 12: 'TimeSpan', 13: 'DateTime', 14: 'UInt16', 15: 'UInt32', 16: 'UInt64',
        17: 'Null', 18: 'String'}
PFMT = {1: '<?', 2: '<B', 6: '<d', 7: '<h', 8: '<i', 9: '<q', 10: '<b', 11: '<f', 12: '<q', 13: '<q',
        14: '<H', 15: '<I', 16: '<Q'}


def write_lps(s):
    b = s.encode('utf-8'); n = len(b); out = bytearray()
    while True:
        x = n & 0x7f; n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n: break
    return bytes(out) + b


class Node:
    libs = ()          # BinaryLibrary records emitted immediately before this record


class Ref(Node):
    __slots__ = ('id',)
    def __init__(s, i): s.id = i
    def __repr__(s): return f'Ref({s.id})'


class NullRec(Node):
    """ObjectNull (rt 10) when n is None, otherwise ObjectNullMultiple256 (13) / ObjectNullMultiple (14)."""
    __slots__ = ('rt', 'n')
    def __init__(s, rt=10, n=1): s.rt = rt; s.n = n


class StrRec(Node):
    __slots__ = ('id', 's', 'libs')
    def __init__(s, i, v): s.id = i; s.s = v; s.libs = ()
    def __repr__(s): return f'Str({s.id},{s.s!r})'


class Prim(Node):
    """MemberPrimitiveTyped (rt 8)."""
    __slots__ = ('t', 'v', 'libs')
    def __init__(s, t, v): s.t = t; s.v = v; s.libs = ()


class ClassInfo:
    __slots__ = ('name', 'members', 'types', 'lib', 'rt', 'defid')


class Obj(Node):
    __slots__ = ('id', 'cls', 'lib', 'members', 'values', 'info', 'rt', 'metaid', 'libs')
    def __repr__(s): return f'Obj({s.id},{s.cls})'
    def get(s, m): return s.values[s.members.index(m)]
    def set(s, m, v): s.values[s.members.index(m)] = v


class Arr(Node):
    __slots__ = ('id', 'rt', 'etype', 'raw', 'atype', 'lens', 'lowers', 'bt', 'ai', 'ptype', 'libs')
    def __repr__(s): return f'Arr({s.id},{s.etype},{len(s.items)})'

    @property
    def items(s):
        if s.rt == 15 or (s.rt == 7 and s.bt == 0): return s.raw
        out = []
        for r in s.raw:
            if isinstance(r, NullRec): out.extend([None] * (r.n if r.rt != 10 else 1))
            else: out.append(r)
        return out


class Reader:
    def __init__(s, data):
        s.d = data; s.p = 0; s.objs = {}; s.classes = {}; s.libs = {}; s.records = []; s.header = None

    def u8(s): v = s.d[s.p]; s.p += 1; return v
    def i32(s): v = struct.unpack_from('<i', s.d, s.p)[0]; s.p += 4; return v

    def lps(s):
        n = 0; sh = 0
        while True:
            b = s.u8(); n |= (b & 0x7f) << sh; sh += 7
            if not b & 0x80: break
        v = s.d[s.p:s.p + n].decode('utf-8'); s.p += n; return v

    def prim(s, t):
        if t == 18 or t == 5: return s.lps()
        if t == 3:
            b = s.d[s.p]; l = 1 if b < 0x80 else 2 if b < 0xe0 else 3 if b < 0xf0 else 4
            v = s.d[s.p:s.p + l].decode('utf-8'); s.p += l; return v
        f = PFMT[t]; v = struct.unpack_from(f, s.d, s.p)[0]; s.p += struct.calcsize(f); return v

    def addinfo(s, bt):
        if bt in (0, 7): return s.u8()
        if bt == 3: return s.lps()
        if bt == 4: n = s.lps(); lib = s.i32(); return (n, lib)
        return None

    def readvals(s, info):
        vals = []
        for (bt, ai) in info.types:
            vals.append(s.prim(ai) if bt == 0 else s.record())
        return vals

    def record(s):
        libs = []
        while True:
            rt = s.u8()
            if rt != 12: break
            lid = s.i32(); name = s.lps(); s.libs[lid] = name; libs.append((lid, name))
        n = s._record(rt)
        if libs: n.libs = tuple(libs)
        return n

    def _record(s, rt):
        if rt == 0:
            s.header = struct.unpack_from('<iiii', s.d, s.p); s.p += 16; return 'HDR'
        if rt in (4, 5):
            oid = s.i32(); info = ClassInfo(); info.rt = rt; info.name = s.lps()
            n = s.i32(); info.members = [s.lps() for _ in range(n)]
            bts = [s.u8() for _ in range(n)]
            info.types = [(bt, s.addinfo(bt)) for bt in bts]
            info.lib = s.i32() if rt == 5 else None
            info.defid = oid; s.classes[oid] = info
            return s._obj(oid, info, rt, None)
        if rt == 1:
            oid = s.i32(); mid = s.i32(); info = s.classes[mid]; s.classes[oid] = info
            return s._obj(oid, info, rt, mid)
        if rt in (2, 3): raise ValueError('untyped class records not supported')
        if rt == 6:
            oid = s.i32(); v = StrRec(oid, s.lps()); s.objs[oid] = v; return v
        if rt == 9: return Ref(s.i32())
        if rt == 10: return NullRec(10, 1)
        if rt == 13: return NullRec(13, s.u8())
        if rt == 14: return NullRec(14, s.i32())
        if rt == 8:
            t = s.u8(); return Prim(t, s.prim(t))
        if rt == 15:
            oid = s.i32(); n = s.i32(); t = s.u8()
            a = s._arr(oid, rt); a.ptype = t; a.etype = PRIM[t]; a.raw = s._prims(t, n); return a
        if rt in (16, 17):
            oid = s.i32(); n = s.i32(); a = s._arr(oid, rt)
            a.etype = 'Object' if rt == 16 else 'String'; a.raw = s._arritems(n); return a
        if rt == 7:
            oid = s.i32(); a = s._arr(oid, rt); a.atype = s.u8(); rank = s.i32()
            a.lens = [s.i32() for _ in range(rank)]
            a.lowers = [s.i32() for _ in range(rank)] if a.atype in (3, 4, 5) else None
            a.bt = s.u8(); a.ai = s.addinfo(a.bt); a.etype = (a.bt, a.ai, a.lens)
            n = 1
            for l in a.lens: n *= l
            a.raw = s._prims(a.ai, n) if a.bt == 0 else s._arritems(n)
            return a
        if rt == 11: return 'END'
        raise ValueError(f'unknown record {rt} at {s.p - 1}')

    def _obj(s, oid, info, rt, mid):
        o = Obj(); o.id = oid; o.cls = info.name; o.lib = info.lib; o.members = info.members
        o.info = info; o.rt = rt; o.metaid = mid; o.libs = ()
        s.objs[oid] = o; o.values = s.readvals(info); return o

    def _arr(s, oid, rt):
        a = Arr(); a.id = oid; a.rt = rt; a.libs = (); s.objs[oid] = a; return a

    def _prims(s, t, n):
        f = PFMT.get(t)
        if f:
            sz = struct.calcsize(f)
            v = list(struct.unpack_from('<%d%s' % (n, f[1]), s.d, s.p)); s.p += sz * n; return v
        return [s.prim(t) for _ in range(n)]

    def _arritems(s, n):
        out = []; cnt = 0
        while cnt < n:
            r = s.record(); out.append(r)
            cnt += (r.n if r.rt != 10 else 1) if isinstance(r, NullRec) else 1
        return out

    def parse(s):
        while True:
            r = s.record()
            if r == 'END': break
            if r == 'HDR': continue
            s.records.append(r)
        return s.records


class Writer:
    def __init__(s): s.b = bytearray()

    def prim(s, t, v):
        if t in (18, 5): s.b += write_lps(v)
        elif t == 3: s.b += v.encode('utf-8')
        else: s.b += struct.pack(PFMT[t], v)

    def addinfo(s, bt, ai):
        if bt in (0, 7): s.b.append(ai)
        elif bt == 3: s.b += write_lps(ai)
        elif bt == 4: s.b += write_lps(ai[0]) + struct.pack('<i', ai[1])

    def record(s, n):
        for lid, name in getattr(n, 'libs', ()) or ():
            s.b.append(12); s.b += struct.pack('<i', lid) + write_lps(name)
        b = s.b
        if n == 'HDR':
            b.append(0); b += struct.pack('<iiii', *s.header)
        elif isinstance(n, Obj):
            if n.rt == 1:
                b.append(1); b += struct.pack('<ii', n.id, n.metaid)
            else:
                i = n.info; b.append(n.rt); b += struct.pack('<i', n.id) + write_lps(i.name)
                b += struct.pack('<i', len(i.members))
                for m in i.members: b += write_lps(m)
                for bt, _ in i.types: b.append(bt)
                for bt, ai in i.types: s.addinfo(bt, ai)
                if n.rt == 5: b += struct.pack('<i', i.lib)
            for (bt, ai), v in zip(n.info.types, n.values):
                if bt == 0: s.prim(ai, v)
                else: s.record(v)
        elif isinstance(n, StrRec):
            b.append(6); b += struct.pack('<i', n.id) + write_lps(n.s)
        elif isinstance(n, Ref):
            b.append(9); b += struct.pack('<i', n.id)
        elif isinstance(n, NullRec):
            b.append(n.rt)
            if n.rt == 13: b.append(n.n)
            elif n.rt == 14: b += struct.pack('<i', n.n)
        elif isinstance(n, Prim):
            b.append(8); b.append(n.t); s.prim(n.t, n.v)
        elif isinstance(n, Arr):
            b.append(n.rt); b += struct.pack('<i', n.id)
            if n.rt == 15:
                b += struct.pack('<i', len(n.raw)); b.append(n.ptype)
                for v in n.raw: s.prim(n.ptype, v)
            elif n.rt in (16, 17):
                b += struct.pack('<i', len(n.items))
                for r in n.raw: s.record(r)
            else:
                b.append(n.atype); b += struct.pack('<i', len(n.lens))
                for l in n.lens: b += struct.pack('<i', l)
                if n.lowers is not None:
                    for l in n.lowers: b += struct.pack('<i', l)
                b.append(n.bt); s.addinfo(n.bt, n.ai)
                for r in n.raw:
                    if n.bt == 0: s.prim(n.ai, r)
                    else: s.record(r)
        else:
            raise TypeError(f'cannot write {n!r}')

    def stream(s, header, records):
        s.header = header; s.record('HDR')
        for r in records: s.record(r)
        s.b.append(11)
        return bytes(s.b)


class Builder:
    """Creates new records that reuse class metadata already present in a parsed stream."""

    def __init__(s, reader):
        s.r = reader
        ids = [k for k in reader.objs if isinstance(k, int)]
        s.next_pos = max(ids) + 1; s.next_neg = min(min(ids), 0) - 1

    def pos_id(s):
        s.next_pos += 1; return s.next_pos - 1

    def neg_id(s):
        s.next_neg -= 1; return s.next_neg + 1

    def string(s, v):
        n = StrRec(s.pos_id(), v); s.r.objs[n.id] = n; return n

    def obj_like(s, template, values, value_type=None):
        """New instance of template's class (written as ClassWithId). Value types get negative ids."""
        o = Obj(); info = template.info
        neg = template.id < 0 if value_type is None else value_type
        o.id = s.neg_id() if neg else s.pos_id()
        o.cls = info.name; o.lib = info.lib; o.members = info.members; o.info = info
        o.rt = 1; o.metaid = info.defid; o.values = list(values); o.libs = ()
        s.r.objs[o.id] = o; return o


def load(path):
    b = open(path, 'rb').read()
    if b[2:4] == b'\x1f\x8b': b = zlib.decompress(b[2:], 16 + zlib.MAX_WBITS)
    r = Reader(b); r.parse(); return r
