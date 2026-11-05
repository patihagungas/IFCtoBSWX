# BSWX file format (StruWalker / BIMReview, AceCad) — reverse-engineered notes

Verified against the 5 sample files in `BSWX/` (fileVersion 16, StruWalker.Controls 8.3.1.0):
`ifc2bswx/bswx.py` reads each of them and writes it back **byte-identical** (`tools/rt_test.py`).
All integers little-endian. `lps` = .NET BinaryWriter string (7-bit varint length + UTF-8).

## Container
```
u8[2]  00 10                 (constant prefix — possibly the file version 16)
gzip   stream                (everything below is the decompressed payload)
```

## 1. Document header — .NET BinaryFormatter (MS-NRBF)
Root `AC.Applications.StruWalker.Controls.StruWalkerDocument` (camera, lights, clip planes,
units, bbox, mModelID GUID, mFileVersion=16) → `mModels: List<StruWalkerModel>`.
`StruWalkerModel`: mName, mIdentifier GUID, mBoundingBox, mModelTranslation/Rotation/Scale,
mGrid (GridLine list + Dictionary<string,double> levels), Materials, LayerColors,
CamFileContainer, ModelSource enum (9 in all samples), **mAttributeDefinitions**
(List<AttributeDefinition>: mKey, mName, mDataType(Type), mVisibleInToolTip, mPropagateToChildren).
The index in that list is the *attribute tag* used by every attribute block below. Custom
attributes are simply extra AttributeDefinitions (the array holds references; the objects follow
as top-level records). Camera: mTarget, mRotation, mScale (= 0.3785 × mModelSize in the sample),
mModelSize (= bounding-box diagonal).
Ends with NRBF MessageEnd (0x0B); the custom binary sections follow immediately.

## 2. Geometry section
```
i32 version (=1)   i32 nGeom
nGeom × {
  u16 z (=0)  u16 nBoxes(=nBodies)
  nBoxes × f32[6] bbox (minx,miny,minz,maxx,maxy,maxz)   -- LOCAL coords, one per body
  i32 blockSize                 -- byte length of the following block (u16 + bodies)
  u16 nBodies
  nBodies × { u16 flag(=1)  BODY }
  ATTRS                         -- usually empty; e.g. Drawing, CamFile
}
BODY (B-rep, double precision, local coords):
  i32 nv   nv × f64[3]
  i32 ne   ne × { i32 v0, i32 v1, u8 type,
                  type 1 (circle): f64[7] centre(3), axis(3), radius }   -- circle: v0==v1
  i32 nf   nf × { u8 type,
                  type 0 plane:    f64[4] nx,ny,nz,d   (n·p + d = 0)
                  type 1 cylinder: f64[8] centre(3), axis(3), radius, ? (seen -90)
                  i32 nLoops, nLoops × { i32 n, n × (i32 edgeIndex, i8 dir ±1) } }
  Loops: outer loop first, counter-clockwise about the face normal; holes clockwise.
  Every edge is used by exactly two loops in opposite directions (closed shells).
```
Only face types 0/1 and edge types 0/1 occur in any sample. Curved profiles (CHS, RHS corners)
are faceted planes in the samples; analytic cylinders appear only for holes and bolt shanks.

## 3. Geometry flag table
`i32 n` (=nGeom) then `n × u16` (all 0 in samples).

## 4. Part instances (n = nGeom, part i → geometry i)
```
f32[16] matrix, row-major, row vectors: world = [x y z 1] · M (translation in row 3)
u8 flag (=3)   u8[4] colour   u8[4] colour2          -- colours are B,G,R,A (System.Drawing ARGB int)
u16 (=0xFFFF)  i32 weldIndex (-1 = none)
i32 geometryIndex
ATTRS
u8 0xFF   lps GUID   u8[8] two more colours (B,G,R,A)
if weldIndex != -1: Weld block follows (custom serialisation with an embedded B-rep and weld
                    parameters, ends with 05 FF; NOT decoded — the reader skips it)
```
Float32 matrices: models must be near the origin (precision ≈ 0.06 mm at 1 km, 512 mm at 6000 km).

Sample conventions: steel parts Gray #808080 (no ElementType), bolts Blue #0000FF with
ElementType `IfcMechanicalFastener`, welds `IfcFastener` (Magenta/Orchid/Gold). Units mm, mm²,
mm³, kg.

## 5. Assemblies
```
i32 n
n × { i32 m, m × i32 partIndex, ATTRS, i32 parent(-1), lps key (assembly GUID, or "") }
```

## 6. Tail
```
i32 0
i32 nProfiles, nProfiles × { u8 type, lps name, f64[k] }
     type 0 I  (b, h, tw, tf, r, 0, 0)        type 1 C  (b, h, tw, tf, r, 0, 0)
     type 2 T  (b, h, tw, tf, r, 0, 0, 0, 0)  type 3 L  (h, b, t, r1, r2)
     type 4 CHS (radius, t)                   type 5 RHS (h, b, t, r_out, r_in)
i32 0, i32 0, i32 0
i32 nDrawings, nDrawings × { lps name, lps name, i32 ?, i32 size, PDF bytes }
i32 0 × 4    (in files without drawings)
```

## ATTRS block
`u16 count`, then `count × { u16 tag, value }`; type from the AttributeDefinition:
String→lps · Int32→i32 · Double/Length/Area/Volume/Mass→f64 · Single→f32 ·
Boolean→u8 · Vector3→f64[3] · anything else (DateTime) → inline NRBF stream
(`ifc2bswx.bswx.datetime_blob` writes one).

## Open / unknown
geometry `z` / body flag / geom flag table semantics; part `flag`=3; cylinder 8th value;
Weld block layout; the i32 before each PDF.
