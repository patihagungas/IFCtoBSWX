"""Document header (BinaryFormatter part of a .bswx) built from a template taken from a real file."""
import math, os, uuid
from .nrbf import Reader, Builder, Obj, Ref, StrRec, NullRec

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template_header.bin')
PRIMES = [3, 7, 11, 17, 23, 29, 37, 47, 59, 71, 89, 107, 131, 163, 197, 239, 293, 353, 431, 521, 631, 761,
          919, 1103, 1327, 1597, 1931, 2333, 2801, 3371, 4049, 4861, 5839, 7013, 8419, 10103]

# .NET type names for custom attribute definitions
TYPE_STRING, TYPE_DOUBLE, TYPE_INT, TYPE_BOOL = 'System.String', 'System.Double', 'System.Int32', 'System.Boolean'


class Header:
    def __init__(self, path=TEMPLATE):
        r = Reader(open(path, 'rb').read()); r.parse()
        self.r = r; self.b = Builder(r)
        self.doc = self._one('.StruWalkerDocument')
        self.model = self._one('.StruWalkerModel')

    def _one(self, suffix):
        return [o for o in self.r.objs.values() if isinstance(o, Obj) and o.cls.endswith(suffix)][0]

    def deref(self, v):
        return self.r.objs[v.id] if isinstance(v, Ref) else v

    def _str(self, v):
        v = self.deref(v)
        return v.s if isinstance(v, StrRec) else None

    @staticmethod
    def _set_all(o, member, value):
        for i, m in enumerate(o.members):
            if m == member: o.values[i] = value

    @staticmethod
    def _vec(o, xyz):
        o.values[:3] = [float(c) for c in xyz]

    def _box(self, box, lo, hi):
        box = self.deref(box)
        self._vec(self.deref(box.get('mMinPoint')), lo); self._vec(self.deref(box.get('mMaxPoint')), hi)
        box.set('mIsDefined', True)

    # ---------------------------------------------------------------- simple fields
    def set_model(self, name, lo, hi, model_id=None, doc_id=None):
        self.deref(self.model.get('mName')).s = name
        self.deref(self.model.get('mIdentifier')).s = model_id or str(uuid.uuid4())
        self.deref(self.doc.get('mModelID')).s = doc_id or str(uuid.uuid4())
        self._box(self.doc.get('mBoundingBox'), lo, hi)
        self._box(self.model.get('mBoundingBox'), lo, hi)
        # camera: look at the model centre, zoom to fit (template ratio scale/size = 0.3785)
        cam = self.deref(self.deref(self.doc.get('mAdditionalSerialisedData')).get('Camera'))
        centre = [(a + b) / 2 for a, b in zip(lo, hi)]
        size = max(math.dist(lo, hi), 1.0)
        for i, m in enumerate(cam.members):
            if m == 'mTarget': self._vec(self.deref(cam.values[i]), centre)
        self._set_all(cam, 'mModelSize', size)
        self._set_all(cam, 'mScale', size * 0.3785)

    # ---------------------------------------------------------------- grid
    def set_grid(self, lines, levels):
        """lines: [(label, (x,y,z), (x,y,z))]; levels: [(name, elevation)]"""
        grid = self.deref(self.model.get('mGrid'))
        lst = self.deref(grid.get('mGridLines')); arr = self.deref(lst.get('_items'))
        proto = arr.raw[0]
        vec_proto = self.deref(proto.get('mStartPosition'))
        cap = max(16, 1 << max(0, len(lines) - 1).bit_length())
        items = []
        for i in range(cap):
            label, a, b = lines[i] if i < len(lines) else (None, (0, 0, 0), (0, 0, 0))
            lab = self.b.string(label) if label is not None else NullRec()
            if i == 0:                               # class-defining record must stay first
                o = proto
                o.set('mLabel', lab)
                self._vec(self.deref(o.get('mStartPosition')), a); self._vec(self.deref(o.get('mEndPosition')), b)
            else:
                o = self.b.obj_like(proto, [lab, self.b.obj_like(vec_proto, list(map(float, a))),
                                            self.b.obj_like(vec_proto, list(map(float, b)))])
            items.append(o)
        arr.raw = items; arr.lens = [cap]
        lst.set('_size', len(lines)); lst.set('_version', len(lines))
        grid.set('mDisplay', bool(lines))

        dic = self.deref(grid.get('mGridLevels'))
        kv = self.deref(dic.get('KeyValuePairs')); kproto = kv.raw[0]
        if levels:
            items = []
            for i, (name, elev) in enumerate(levels):
                if i == 0:
                    kproto.set('key', self.b.string(name)); kproto.set('value', float(elev)); items.append(kproto)
                else:
                    items.append(self.b.obj_like(kproto, [self.b.string(name), float(elev)]))
            kv.raw = items; kv.lens = [len(items)]
            dic.set('HashSize', next(p for p in PRIMES if p >= len(items)))
        else:
            dic.set('HashSize', 0)                   # KeyValuePairs ignored when HashSize == 0
        dic.set('Version', len(levels))
        grid.set('mCurrentLevel', 0)

    # ---------------------------------------------------------------- attribute definitions
    def attribute_names(self):
        lst = self.deref(self.model.get('mAttributeDefinitions')); arr = self.deref(lst.get('_items'))
        return [self._str(self.deref(it).get('mName')) for it in arr.items[:lst.get('_size')]]

    def add_attributes(self, defs):
        """defs: [(name, dotnet_type)]. Returns {name: tag} for the added definitions."""
        lst = self.deref(self.model.get('mAttributeDefinitions')); arr = self.deref(lst.get('_items'))
        size = lst.get('_size')
        existing = [self.deref(it) for it in arr.items[:size]]
        holders = {}
        for a in existing:
            h = a.get('mDataType'); hid = h.id
            holders.setdefault(self._str(self.deref(h).get('Data')), hid)
        names = self.attribute_names(); out = {}
        new = []
        by_ref = isinstance(arr.raw[0], Ref)    # items stored as references, objects follow at top level
        for name, t in defs:
            if name in names or name in out: continue
            key = self.b.string(name)
            o = self.b.obj_like(existing[0], [key, Ref(key.id), Ref(holders[t]), True, True], value_type=False)
            if by_ref:
                self.r.records.append(o); o = Ref(o.id)
            new.append(o)
            out[name] = size + len(new) - 1
        total = size + len(new)
        cap = max(arr.lens[0], 1 << (total - 1).bit_length())
        raw = list(arr.raw[:size]) if len(arr.raw) >= size and not any(isinstance(x, NullRec) for x in arr.raw[:size]) \
            else list(arr.items[:size])
        raw += new
        pad = cap - total
        while pad > 0:
            n = min(pad, 255); raw.append(NullRec(13, n) if n > 1 else NullRec(10, 1)); pad -= n
        arr.raw = raw; arr.lens = [cap]
        lst.set('_size', total); lst.set('_version', lst.get('_version') + len(new))
        return out

    def records(self):
        return self.r.header, self.r.records
