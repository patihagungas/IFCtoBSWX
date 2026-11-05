"""Convert sample IFC elements to B-rep and validate topology + area against the mesh."""
import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np, ifcopenshell, ifcopenshell.geom as G
from ifc2bswx.brep import clean_mesh, mesh_to_bodies, check_body, brep_area, mass_properties
f = ifcopenshell.open(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
s = G.settings(); s.set('use-world-coords', False); s.set('weld-vertices', True); s.set('convert-back-units', True)
els = [e for e in f.by_type('IfcProduct') if e.Representation and not e.is_a('IfcOpeningElement')]
random.seed(1); els = random.sample(els, min(n, len(els)))
bad = 0; t = time.time(); stats = []
for e in els:
    try: sh = G.create_shape(s, e)
    except Exception as ex: print('geom fail', e.is_a(), ex); continue
    V, T = clean_mesh(sh.geometry.verts, sh.geometry.faces)
    bodies = mesh_to_bodies(V, T)
    vol, area, cen = mass_properties(V, T)
    probs = [p for b in bodies for p in check_body(b)]
    ba = sum(brep_area(b) for b in bodies)
    rel = abs(ba - area) / max(area, 1e-9)
    stats.append((e.is_a(), len(T), sum(len(b.faces) for b in bodies), len(bodies)))
    if probs or rel > 1e-6:
        bad += 1
        if bad <= 8: print('BAD', e.is_a(), e.GlobalId, 'tris', len(T), 'area mesh/brep', round(area, 2), round(ba, 2), probs[:4])
print(f'{len(els)} elements, {bad} with problems, {time.time()-t:.1f}s')
from collections import defaultdict
agg = defaultdict(lambda: [0, 0, 0, 0])
for c, nt, nf, nb in stats: a = agg[c]; a[0] += 1; a[1] += nt; a[2] += nf; a[3] += nb
for c, (k, nt, nf, nb) in agg.items(): print(f'  {c:24s} n={k:4d} avg tris {nt/k:7.1f} -> faces {nf/k:6.1f}, bodies {nb/k:.1f}')
