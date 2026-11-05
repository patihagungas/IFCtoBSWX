"""Round-trip test: read every sample .bswx and check the re-written payload is byte-identical."""
import sys, zlib, glob, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from ifc2bswx.bswx import BSWX
ok = True
for path in sorted(glob.glob(os.path.join(sys.argv[1], '**', '*.bswx'), recursive=True)):
    t = time.time(); raw = open(path, 'rb').read(); d = zlib.decompress(raw[2:], 16 + zlib.MAX_WBITS)
    b = BSWX.read(path); out = b.payload()
    same = out == d; ok &= same
    if not same:
        i = next((i for i in range(min(len(out), len(d))) if out[i] != d[i]), None)
        print('   first diff at', i, 'len', len(out), len(d))
    print('OK ' if same else 'BAD', f'{time.time()-t:5.1f}s', len(b.geoms), 'parts', len(b.assemblies), 'asms', os.path.basename(path))
sys.exit(0 if ok else 1)
