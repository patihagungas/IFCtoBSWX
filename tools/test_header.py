"""Header generation test: grid lines beyond template capacity, levels, custom attributes; parse back."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from ifc2bswx.header import Header, TYPE_STRING, TYPE_DOUBLE, TYPE_INT, TYPE_BOOL
from ifc2bswx.nrbf import Reader, Writer, Obj, StrRec, Ref

h = Header()
h.set_model('Test.ifc', (-10, -20, -30), (1000, 2000, 3000), model_id='11111111-2222-3333-4444-555555555555')
lines = [(f'G{i}', (i * 1000.0, 0.0, 0.0), (i * 1000.0, 5000.0, 0.0)) for i in range(21)]
levels = [('Ground', 0.0), ('+3500', 3500.0), ('Roof', 7200.5)]
h.set_grid(lines, levels)
tags = h.add_attributes([('Pset.Text', TYPE_STRING), ('Pset.Num', TYPE_DOUBLE), ('Pset.Int', TYPE_INT), ('Pset.Flag', TYPE_BOOL)])
data = Writer().stream(*h.records())

r = Reader(data); r.parse(); assert r.p == len(data), 'trailing bytes'
d = lambda v: r.objs[v.id] if isinstance(v, Ref) else v
s = lambda v: d(v).s if isinstance(d(v), StrRec) else None
model = [o for o in r.objs.values() if isinstance(o, Obj) and o.cls.endswith('.StruWalkerModel')][0]
assert s(model.get('mName')) == 'Test.ifc'
assert s(model.get('mIdentifier')) == '11111111-2222-3333-4444-555555555555'
grid = d(model.get('mGrid')); lst = d(grid.get('mGridLines')); arr = d(lst.get('_items'))
assert lst.get('_size') == 21 and len(arr.items) >= 21
got = [(s(g.get('mLabel')), d(g.get('mStartPosition')).values[:3], d(g.get('mEndPosition')).values[:3]) for g in arr.items[:21]]
assert got == [(l, list(a), list(b)) for l, a, b in lines], got[:3]
dic = d(grid.get('mGridLevels')); kv = d(dic.get('KeyValuePairs'))
assert [(s(p.get('key')), p.get('value')) for p in kv.items] == levels
defs = d(d(model.get('mAttributeDefinitions')).get('_items'))
size = d(model.get('mAttributeDefinitions')).get('_size')
names = [s(d(x).get('mName')) for x in defs.items[:size]]
assert names[-4:] == ['Pset.Text', 'Pset.Num', 'Pset.Int', 'Pset.Flag'] and tags['Pset.Flag'] == size - 1
types = [d(d(x).get('mDataType')).values for x in defs.items[size - 4:size]]
print('header OK:', len(data), 'bytes,', size, 'attribute definitions, types', [s(t[0]) for t in types])
