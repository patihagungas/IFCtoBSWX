"""Triangle mesh -> BSWX planar B-rep (shared edges, oriented loops, one body per shell)."""
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from .bswx import Body

WELD_TOL = 1e-4          # mm, vertex welding grid
PLANE_DOT = 0.999999     # normals considered coplanar (~0.08 deg)
PLANE_DIST = 0.01        # mm, plane offset tolerance


def clean_mesh(V, T):
    V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
    T = np.asarray(T, dtype=np.int64).reshape(-1, 3)
    if len(T) == 0: return V[:0], T
    q = np.round(V / WELD_TOL).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    V = V[first]; T = inv[T]
    ok = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 2] != T[:, 0])
    T = T[ok]
    if len(T):
        n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
        T = T[np.linalg.norm(n, axis=1) > 1e-9]
        T = _cancel_coincident(T)
        T = fix_t_junctions(V, T)
    return V, T


def _cancel_coincident(T):
    """Triangles on the same three vertices: identical orientation -> keep one (duplicate);
    opposite orientation -> drop the pair (faces where two solids touch, e.g. nut on washer),
    which fuses the solids into one closed shell."""
    key = np.sort(T, axis=1)
    # orientation parity relative to the sorted order
    r = np.argsort(T, axis=1)
    parity = ((r[:, 0] < r[:, 1]).astype(int) + (r[:, 1] < r[:, 2]) + (r[:, 0] < r[:, 2])) % 2
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    if (cnt == 1).all(): return T
    keep = np.ones(len(T), bool)
    for g in np.nonzero(cnt > 1)[0]:
        idx = np.nonzero(inv == g)[0]
        pos = [i for i in idx if parity[i] == 0]; neg = [i for i in idx if parity[i] == 1]
        n = min(len(pos), len(neg))
        drop = pos[:n] + neg[:n]                     # cancelling pairs
        rest = pos[n:] + neg[n:]
        drop += rest[1:]                             # duplicates of the same orientation
        keep[drop] = False
    return T[keep]


def open_edges(T):
    """Directed edges (a, b, triangle, corner) whose undirected edge is used by only one triangle."""
    de = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    key = np.sort(de, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    once = cnt[inv.reshape(-1)] == 1
    idx = np.nonzero(once)[0]
    m = len(T)
    return de[idx], idx % m, idx // m


def fix_t_junctions(V, T, tol=1e-3, max_iter=20):
    """Split triangles whose open edge has another open-boundary vertex lying on it (T-junction),
    so that neighbouring faces share identical edges."""
    for _ in range(max_iter):
        E, tri, corner = open_edges(T)
        if len(E) == 0: break
        ov = np.unique(T)                 # any vertex (touching solids create T-junctions too)
        Pv = V[ov]
        splits = {}
        for (a, b), t, c in zip(E, tri, corner):
            if t in splits: continue
            ab = V[b] - V[a]; L2 = float(ab @ ab)
            if L2 < 1e-12: continue
            s = (Pv - V[a]) @ ab / L2
            d = np.linalg.norm(V[a] + s[:, None] * ab - Pv, axis=1)
            ok = (s > 1e-6) & (s < 1 - 1e-6) & (d < tol)
            if ok.any():
                k = np.nonzero(ok)[0]; v = ov[k[np.argmin(s[k])]]
                splits[t] = (c, v)
        if not splits: break
        keep = np.ones(len(T), bool); new = []
        for t, (c, v) in splits.items():
            i, j, k = T[t][[c, (c + 1) % 3, (c + 2) % 3]]       # open edge is i->j, opposite corner k
            new += [(i, v, k), (v, j, k)]; keep[t] = False
        T = np.vstack([T[keep], np.array(new, dtype=T.dtype)])
    return T


def mass_properties(V, T):
    """Volume, surface area and centroid of a closed, outward-oriented triangle mesh."""
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cr = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cr, axis=1).sum()
    v6 = np.einsum('ij,ij->i', a, np.cross(b, c))
    vol = v6.sum() / 6.0
    if abs(vol) < 1e-12:
        cen = V.mean(axis=0) if len(V) else np.zeros(3)
    else:
        cen = ((a + b + c) * v6[:, None]).sum(axis=0) / (24.0 * vol)
    return abs(vol), area, cen


def _chain_loops(dir_edges):
    """dir_edges: list of (a,b). Returns closed vertex loops [[v0,v1,...]] following edge directions."""
    out = {}
    for a, b in dir_edges: out.setdefault(a, []).append(b)
    loops = []
    while out:
        start = next(iter(out)); loop = [start]; cur = start
        while True:
            nxts = out.get(cur)
            if not nxts: break
            nxt = nxts.pop()
            if not nxts: del out[cur]
            if nxt == start: break
            loop.append(nxt); cur = nxt
        if len(loop) >= 3: loops.append(loop)
    return loops


def _signed_area(P, n):
    """Signed area of polygon P (k,3) about normal n."""
    s = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)
    return 0.5 * float(np.dot(s, n))


def _simplify(loops, V, n):
    """Remove collinear vertices shared consistently. loops: list of (face, [verts]). n = vertex count."""
    # a vertex may be dropped only if it has exactly 2 neighbours in the whole edge graph
    nbrs = {}
    for _, lp in loops:
        k = len(lp)
        for i in range(k):
            a, b = lp[i], lp[(i + 1) % k]
            nbrs.setdefault(a, set()).add(b); nbrs.setdefault(b, set()).add(a)
    drop = set()
    Vl = V.tolist() if hasattr(V, 'tolist') else V
    for v, ns in nbrs.items():
        if len(ns) != 2: continue
        a, b = ns
        (ax, ay, az), (vx, vy, vz), (bx, by, bz) = Vl[a], Vl[v], Vl[b]
        d1 = (vx - ax, vy - ay, vz - az); d2 = (bx - vx, by - vy, bz - vz)
        l1 = (d1[0] ** 2 + d1[1] ** 2 + d1[2] ** 2) ** 0.5; l2 = (d2[0] ** 2 + d2[1] ** 2 + d2[2] ** 2) ** 0.5
        if l1 < 1e-9 or l2 < 1e-9: continue
        cx = d1[1] * d2[2] - d1[2] * d2[1]; cy = d1[2] * d2[0] - d1[0] * d2[2]; cz = d1[0] * d2[1] - d1[1] * d2[0]
        if (cx * cx + cy * cy + cz * cz) ** 0.5 < 1e-7 * l1 * l2 and d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2] > 0:
            drop.add(v)
    if not drop: return loops
    out = []
    for f, lp in loops:
        nl = [v for v in lp if v not in drop]
        if len(nl) >= 3: out.append((f, nl))
    return out


def _components(n, a, b):
    """Connected-component label per node of an undirected graph given as edge arrays."""
    if len(a) == 0: return np.arange(n)
    g = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(n, n))
    return connected_components(g, directed=False)[1]


def _loop_area(Vl, lp, n):
    """Signed area of vertex loop lp (indices into list-of-tuples Vl) about normal n."""
    sx = sy = sz = 0.0
    k = len(lp)
    for i in range(k):
        x1, y1, z1 = Vl[lp[i]]; x2, y2, z2 = Vl[lp[(i + 1) % k]]
        sx += y1 * z2 - z1 * y2; sy += z1 * x2 - x1 * z2; sz += x1 * y2 - y1 * x2
    return 0.5 * (sx * n[0] + sy * n[1] + sz * n[2])


def mesh_to_bodies(V, T):
    """Build BSWX bodies from a cleaned, outward-oriented triangle mesh."""
    if len(T) == 0: return []
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    nrm = np.cross(b - a, c - a); area2 = np.linalg.norm(nrm, axis=1)
    nrm = nrm / area2[:, None]
    dist = -np.einsum('ij,ij->i', nrm, a)

    m = len(T)
    de = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])        # directed edges
    tri = np.tile(np.arange(m), 3)
    key = np.sort(de, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    ks = key[order]
    start = np.r_[True, np.any(ks[1:] != ks[:-1], axis=1)]
    run = np.cumsum(start) - 1
    run_len = np.bincount(run)
    # manifold edges: exactly two uses in opposite directions. Only these join shells and faces,
    # so solids that merely touch along an edge (bolt + nut + washer) stay separate closed bodies.
    first = np.nonzero(start & (run_len[run] == 2))[0]
    e0, e1 = order[first], order[first + 1]
    ok = de[e0, 0] == de[e1, 1]
    e0, e1 = e0[ok], e1[ok]
    t0, t1 = tri[e0], tri[e1]
    body_of = _components(m, t0, t1)
    cop = (np.einsum('ij,ij->i', nrm[t0], nrm[t1]) > PLANE_DOT) & (np.abs(dist[t0] - dist[t1]) < PLANE_DIST)
    face_of = _components(m, t0[cop], t1[cop])
    nf = face_of.max() + 1
    # face normal: area-weighted mean of its triangles
    fn = np.zeros((nf, 3)); np.add.at(fn, face_of, nrm * area2[:, None])
    fn /= np.linalg.norm(fn, axis=1)[:, None]
    face_body = np.zeros(nf, dtype=np.int64); face_body[face_of] = body_of
    # boundary directed edges of each face: every edge except the coplanar (interior) pairs
    boundary = np.ones(len(de), bool); boundary[e0[cop]] = False; boundary[e1[cop]] = False
    bidx = np.nonzero(boundary)[0]
    bface = face_of[tri[bidx]]
    o = np.argsort(bface, kind='stable'); bidx, bface = bidx[o], bface[o]
    cuts = np.nonzero(np.diff(bface))[0] + 1
    face_ids = bface[np.r_[0, cuts]] if len(bface) else []
    dl = de.tolist()

    faces_in_body = {}
    for f, grp in zip(face_ids, np.split(bidx, cuts) if len(bidx) else []):
        faces_in_body.setdefault(int(face_body[f]), []).append((int(f), [tuple(dl[k]) for k in grp]))

    Vl = V.tolist()
    bodies = []
    for flist in faces_in_body.values():
        loops = []
        for f, es in flist:
            for lp in _chain_loops(es): loops.append((f, lp))
        loops = _simplify(loops, Vl, len(V))
        used = sorted({v for _, lp in loops for v in lp})
        vmap = {v: i for i, v in enumerate(used)}
        edges, eid = [], {}
        faces_out = {}
        for f, lp in loops:
            nn = fn[f]
            entry = []
            k = len(lp)
            for i in range(k):
                va, vb = vmap[lp[i]], vmap[lp[(i + 1) % k]]
                # one edge per (forward, reverse) pair of uses; non-manifold mesh edges
                # (more than two uses) therefore become several coincident edges
                slots = eid.setdefault((min(va, vb), max(va, vb)), [])
                for s in slots:
                    d = 1 if edges[s[0]][0] == va else -1
                    if d not in s[1]: break
                else:
                    s = [len(edges), set()]; slots.append(s); edges.append((va, vb, 0, None)); d = 1
                s[1].add(d)
                entry.append((s[0], d))
            sa = _loop_area(Vl, lp, nn)
            faces_out.setdefault(f, []).append((sa, entry, lp))
        Vb = V[used]
        faces = []
        for f, lps in faces_out.items():
            nn = fn[f]
            lps.sort(key=lambda x: -x[0])            # outer (positive area) first, holes after
            vs = sorted({v for _, _, lp in lps for v in lp})
            d = -float(np.mean(V[vs] @ nn))          # n.p + d = 0
            faces.append((0, (float(nn[0]), float(nn[1]), float(nn[2]), d), [e for _, e, _ in lps]))
        faces += _cap_open_boundaries(Vb, edges, faces)
        verts = [tuple(map(float, p)) for p in Vb]
        bodies.append(Body(verts, edges, faces))
    return bodies


def _point_in_poly(p, poly):
    x, y = p; inside = False
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): inside = not inside
    return inside


def _cap_open_boundaries(Vb, edges, faces, tol=0.01):
    """Close planar holes in an open shell (typical of exported faceted B-reps, e.g. a nut without
    the face that touches its washer): edges used once are chained into loops and every planar
    loop becomes a new face (coplanar inner loops become its holes)."""
    use = {}
    for _, _, loops in faces:
        for lp in loops:
            for e, d in lp: use.setdefault(e, []).append(d)
    missing = []                                    # directed (a, b, edge, dir) still needed
    for e, ds in use.items():
        if len(ds) == 1:
            a, b = edges[e][:2]
            missing.append((b, a, e, -1) if ds[0] == 1 else (a, b, e, 1))
    if not missing: return []
    nxt = {}
    for m in missing: nxt.setdefault(m[0], []).append(m)
    loops = []
    while nxt:
        start = next(iter(nxt)); cur = start; lp = []
        while cur in nxt:
            m = nxt[cur].pop()
            if not nxt[cur]: del nxt[cur]
            lp.append(m); cur = m[1]
            if cur == start: break
        if cur == start and len(lp) >= 3: loops.append(lp)
    # plane of each loop (Newell normal), keep only planar loops
    planar = []
    for lp in loops:
        P = Vb[[m[0] for m in lp]]
        n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0); area = np.linalg.norm(n) / 2
        if area < 1e-9: continue
        n /= 2 * area; d = -float(np.mean(P @ n))
        if np.abs(P @ n + d).max() < tol: planar.append((n, d, area, lp))
    out, used = [], set()
    for i, (n, d, area, lp) in enumerate(planar):
        if i in used: continue
        used.add(i)
        holes = []
        # coplanar loops with opposite winding inside this one are its holes
        ax = np.argmax(np.abs(n)); keep = [k for k in range(3) if k != ax]
        outer2 = Vb[[m[0] for m in lp]][:, keep]
        for j, (n2, d2, area2, lp2) in enumerate(planar):
            if j in used or np.dot(n, n2) > -0.999999 or abs(d + d2) > tol: continue
            if area2 < area and _point_in_poly(Vb[lp2[0][0]][keep], outer2):
                holes.append([(m[2], m[3]) for m in lp2]); used.add(j)
        out.append((0, (float(n[0]), float(n[1]), float(n[2]), d), [[(m[2], m[3]) for m in lp]] + holes))
    return out


def body_bbox(body):
    P = np.array(body.verts)
    lo, hi = P.min(axis=0), P.max(axis=0)
    return tuple(map(float, lo)) + tuple(map(float, hi))


def check_body(body):
    """Return list of problems: edges not used exactly twice in opposite directions, bad indices."""
    use = {}
    probs = []
    for t, surf, loops in body.faces:
        for lp in loops:
            for e, d in lp:
                if not 0 <= e < len(body.edges): probs.append(f'edge index {e}'); continue
                use.setdefault(e, []).append(d)
    for e, ds in use.items():
        if sorted(ds) != [-1, 1]: probs.append(f'edge {e} used {ds}')
    if len(use) != len(body.edges): probs.append(f'{len(body.edges) - len(use)} unused edges')
    return probs


def brep_area(body):
    V = np.array(body.verts); total = 0.0
    for t, surf, loops in body.faces:
        n = np.array(surf[:3])
        for lp in loops:
            pts = [body.edges[e][0] if d == 1 else body.edges[e][1] for e, d in lp]
            total += _signed_area(V[pts], n)
    return total
