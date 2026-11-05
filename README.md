# IFC → BSWX converter

Converts IFC models into StruWalker / BIMReview `.bswx` files. Tested with IFC2X3 exports from
Tekla Structures 2021–2025; IFC4 goes through the same IfcOpenShell code paths but has not been
tested. The BSWX format was reverse-engineered from the sample files in `BSWX/`; see
[docs/BSWX_FORMAT.md](docs/BSWX_FORMAT.md).

## Install the app (no Python needed)
1. Extract `release\IFCtoBSWX-<version>-Setup.zip`.
2. Double-click `Install.cmd` — installs to `%LOCALAPPDATA%\Programs\IFCtoBSWX`, adds Start menu
   and Desktop shortcuts and an "Open with" entry for `.ifc` files. No admin rights needed.
3. Uninstall from *Settings › Apps › Installed apps*.

Rebuild the installer after code changes:
`powershell -ExecutionPolicy Bypass -File packaging\build.ps1` (creates a clean venv in `build\`).

## Requirements (running from source)
Python 3.10+ with `ifcopenshell` (0.8), `numpy`, `scipy` (and `matplotlib` for previews).

## Usage
```
python -m ifc2bswx model.ifc                      # writes model.bswx next to the IFC
python -m ifc2bswx model.ifc -o out\model.bswx
python -m ifc2bswx IFC\*.ifc -o out               # several files into a folder
```
Options:

| option | meaning |
|---|---|
| `--deflection 0.5` | tessellation tolerance (mm) for curved surfaces; smaller = smoother & bigger files |
| `--ifc-colours` | use IFC surface colours instead of the StruWalker convention (grey steel, blue bolts) |
| `--no-extra-properties` | don't copy unmapped IFC properties into custom attributes |
| `--threads N` | worker processes / geometry threads (default: all cores) |

## What is converted
* **Geometry** – every IFC product with a body representation (beams, columns, members, plates,
  bolts, proxies, slabs…) is tessellated by IfcOpenShell and rebuilt as a closed planar B-rep:
  coplanar triangles are merged into faces with holes, collinear points removed, one body per
  solid. Faceted source geometry is repaired (T-junctions, coincident faces between touching
  solids, planar holes in open shells). Placement is kept as the part matrix, like StruWalker.
* **Attributes** – GUID, ID, Name, Part/Assembly mark, Profile & profile type, Material, Finish,
  Class, Phase, Length/Width/Height, Area, Volume, Weight, Centre of gravity; bolt size/length/
  standard/count; ElementType (= IFC class). Every other IFC property is added as a custom
  attribute named `PropertySet.Property`.
* **Assemblies** – top-level `IfcElementAssembly` (nested assemblies flattened) incl. bolts;
  parts not in an assembly get a single-part assembly. Assembly weight/area/volume/CoG are summed.
* **Grids and levels** – `IfcGrid` axes and `IfcBuildingStorey` elevations.
* **Profile catalogue** – parameterised IFC profiles (I, C, T, L, CHS, RHS).
* Georeferenced models (coordinates beyond 1 km) are shifted near the origin by a whole-metre
  offset that is printed in the log, because BSWX stores placements in single precision.

Not produced: welds, embedded drawings (PDF), CAM files, clashes.

## Verification
StruWalker itself was not available while building this, so correctness is checked by:
```
python tools/rt_test.py BSWX          # every sample .bswx is read and re-written byte-identical
python tools/test_header.py           # grid/levels/custom attributes survive a write/read cycle
python tools/verify.py out\model.bswx --ifc model.ifc --png preview.png [--offset X Y Z]
```
`verify.py` re-reads the output with the independent reader, checks every B-rep body
(each edge used exactly twice in opposite directions, outer loops counter-clockwise, unit
normals, rigid matrices), compares part positions with IfcOpenShell world coordinates and
renders a wireframe preview.
