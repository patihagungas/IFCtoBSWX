# IFC → BSWX Converter — project memory

Handover notes for continuing this project. Last updated 2026-11-06.

## Goal
Convert IFC models (Tekla Structures exports) into `.bswx` files for **AceCad StruWalker / BIMReview**.
There is no public BSWX spec; the format was reverse-engineered from the sample files in `BSWX/`.

## Current status
- ✅ Format fully decoded except the Weld block → `docs/BSWX_FORMAT.md`
- ✅ Reader/writer round-trips all 5 sample `.bswx` **byte-identical** (`tools/rt_test.py`)
- ✅ Converter works: GUI app + CLI, multi-process, progress/cancel
- ✅ Installer built and installed on this PC (`%LOCALAPPDATA%\Programs\IFCtoBSWX`, Start menu + Desktop,
  "Open with" for .ifc, Apps & features uninstall). Release zip: `release/IFCtoBSWX-1.0.0-Setup.zip` (73 MB)
- ⚠️ **Never opened in StruWalker/BIMReview** (not available). The user has no StruWalker and no matching
  IFC/BSWX pair. Verification is done with our own reader + checks only.
- ⚠️ Big models **MTC (115 MB)** and **QUBE (136 MB)** not converted yet: the first MTC attempt was killed
  because the PC ran low on memory (it used 15 workers then). Since then: default workers = min(8, cores) and
  workers no longer import ifcopenshell. Retry with 4–6 workers.

| IFC | parts | assemblies | time | result |
|---|---|---|---|---|
| 25-335 LES Traffic Decking (5.6 MB) | 6041 | 3610 | ~25–31 s | all checks pass, 0.008 mm |
| P196-RPA-MODEL EAST (31 MB) | 9529 | 1306 | ~3 min (old, single-process) | 0.002 mm after recentring; **re-convert** with current code to get closed shells |

## Project layout
```
ifc2bswx/            the package
  nrbf.py            .NET BinaryFormatter reader/writer (structure-preserving) + Builder for new records
  bswx.py            BSWX read/write (dataclasses Body, Geometry, Part, Assembly); body_block(); datetime_blob()
  header.py          document header from template_header.bin (taken from "UK Portal Frame Rev1.bswx"):
                     set_model (name, ids, bbox, camera), set_grid (lines + levels), add_attributes (custom)
  brep.py            triangle mesh → closed planar B-rep (weld, cancel coincident faces, fix T-junctions,
                     merge coplanar faces, manifold-only shells, collinear removal, cap planar holes)
  geomworker.py      worker-process function (no ifcopenshell import → small processes)
  convert.py         Converter: ifcopenshell iterator → workers → attributes, assemblies, grid, profiles, save
  gui.py             tkinter window (settings saved in %APPDATA%\IFCtoBSWX\settings.json)
  __main__.py        CLI:  python -m ifc2bswx in.ifc -o out [--deflection --ifc-colours --no-extra-properties --threads]
app.py               exe entry (GUI; `--cli` for headless; freeze_support)
packaging/           IFCtoBSWX.spec, build.ps1 (clean venv → PyInstaller → zip), install.ps1, uninstall.ps1,
                     Install.cmd, INSTALL.txt, app.ico
tools/               rt_test.py, test_header.py, test_brep.py, test_gui.py, verify.py
docs/BSWX_FORMAT.md  the format spec
BSWX/  IFC/          sample inputs (do not modify)   out/  scratch outputs   build/ release/  build artefacts
```

## How to run / test
```
python -m ifc2bswx "IFC\25-335 LES Traffic Decking.ifc" -o out
python tools\rt_test.py BSWX                        # must print OK for all 5
python tools\test_header.py
python tools\verify.py out\X.bswx --ifc IFC\X.ifc --png out\preview.png [--offset X Y Z]
python tools\test_gui.py "IFC\25-335 LES Traffic Decking.ifc" out\gui_test
powershell -ExecutionPolicy Bypass -File packaging\build.ps1     # rebuild installer (uses build\venv)
```
Run Python scripts as files (not via stdin) — Windows multiprocessing spawn needs a real `__main__`.
Use `python` without `-I` for the package (isolated mode drops the cwd from sys.path).

## Key format facts (details in docs/BSWX_FORMAT.md)
- File = `00 10` + gzip. Payload = NRBF header (StruWalkerDocument) + custom binary sections:
  geometry → u16 flag table → parts → assemblies → tail (profiles, PDFs).
- Geometry: local coords, f64 B-rep; faces plane(0)/cylinder(1), edges line(0)/circle(1).
  The int before the bodies ("gid") = byte length of the body block (writer computes it).
- Part matrix = 16 × **float32**, row-vector, translation in row 3 → models must be near the origin.
- Colours are **B,G,R,A** bytes. Sample convention: steel Gray #808080, bolts Blue #0000FF
  (ElementType `IfcMechanicalFastener`), welds `IfcFastener`. Units mm / mm² / mm³ / kg.
- Attribute tag = index into the header's AttributeDefinition list (156 built-in in the template);
  custom attributes = extra definitions appended (array holds Refs, objects appended as top-level records).

## Important decisions / gotchas
- ifcopenshell: use the **iterator**, not `create_shape` (returns empty meshes for some faceted B-reps).
  Matrix translation comes in **metres** while verts (convert-back-units) are in file units → ×1000.
- Georeferenced models (|coord| > 1 km) are shifted by a whole-metre offset, printed in the log
  Offset is NOT stored in the file (pass it to verify.py --offset).
- Curved surfaces are faceted (like the samples' CHS/RHS); linear deflection 0.5 mm default.
- Tekla bolts: nut/washer coincident faces and missing nut faces → handled in brep.py.
- Build the exe only from the clean venv (global Python has torch/transformers → 900 MB bundle).
- Assemblies: top-level IfcElementAssembly, flattened; loose non-bolt parts get single-part assemblies.
  IFCs exported with Assemblies:Off (25-335) only have ASSEMBLY MARK → no real grouping possible.

## Open items / ideas
1. Convert MTC and QUBE (memory!). If still too heavy: stream geometry blobs to a temp file instead of RAM,
   free per-part Props, lower in-flight batches.
2. Test the output in StruWalker/BIMReview as soon as possible (biggest remaining risk).
3. Grids: code exists (IfcGrid + storeys) but untested on a real model (MTC/QUBE have grids).
4. Optional: analytic cylinders for holes/bolt shanks (samples use them), weld export, real setup.exe
   (Inno Setup not installed), code signing to avoid SmartScreen.
5. Unknown format fields: geom `z`, body flag, u16 flag table, part flag 3, cylinder 8th value, Weld block.
