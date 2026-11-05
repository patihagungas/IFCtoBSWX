"""Replace project-specific values in ifc2bswx/template_header.bin with neutral ones."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from ifc2bswx.header import Header, TEMPLATE
from ifc2bswx.nrbf import Writer, StrRec

h = Header()
h.set_model('Model.ifc', (0, 0, 0), (10000, 10000, 10000),
            model_id='00000000-0000-0000-0000-000000000001', doc_id='00000000-0000-0000-0000-000000000002')
h.set_grid([], [('+0', 0.0)])
data = Writer().stream(*h.records())
open(TEMPLATE, 'wb').write(data)
left = [o.s for o in h.r.objs.values() if isinstance(o, StrRec) and ('Portal' in o.s or '.db1' in o.s)]
print('template written,', len(data), 'bytes; project strings left:', left)
