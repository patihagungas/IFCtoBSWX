"""Geometry worker (runs in child processes; imports no IfcOpenShell to keep them small)."""
import numpy as np
from .bswx import BSWX, Geometry
from .brep import clean_mesh, mesh_to_bodies, mass_properties, body_bbox


def build_geometry(verts, faces, M, colour):
    """Mesh (mm, local) + placement -> serialised BSWX geometry and mass properties."""
    V, T = clean_mesh(verts, faces)
    if len(T) < 4: return None
    M = M.copy()
    if np.linalg.det(M[:3, :3]) < 0:                # mirrored placement: bake the mirror into the mesh
        S = np.diag([1.0, 1.0, -1.0])
        V = V @ S; T = T[:, ::-1].copy(); M[:3, :3] = S @ M[:3, :3]
    bodies = mesh_to_bodies(V, T)
    if not bodies: return None
    vol, area, cen = mass_properties(V, T)
    cen_w = np.append(cen, 1.0) @ M
    geom = Geometry(bodies=[], boxes=[body_bbox(b) for b in bodies], raw=BSWX.body_block(bodies))
    return dict(geom=geom, matrix=M, vol=vol, area=area, cog=tuple(cen_w[:3]),
                lo=V.min(axis=0), hi=V.max(axis=0), colour=colour, ntris=len(T))


def build_batch(batch):
    out = []
    for eid, verts, faces, M, colour in batch:
        try: out.append((eid, build_geometry(verts, faces, M, colour)))
        except Exception as ex: out.append((eid, f'{type(ex).__name__}: {ex}'))
    return out
