"""Command line: python -m ifc2bswx input.ifc [output.bswx] [options]"""
import argparse, os, sys
from .convert import Options, convert


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ifc2bswx', description='Convert IFC models to StruWalker / BIMReview .bswx')
    ap.add_argument('input', nargs='+', help='IFC file(s)')
    ap.add_argument('-o', '--output', help='output .bswx (single input) or output folder')
    ap.add_argument('--deflection', type=float, default=Options.linear_deflection,
                    help='tessellation tolerance for curved surfaces in mm (default %(default)s)')
    ap.add_argument('--ifc-colours', action='store_true', help='use IFC surface colours (default: grey steel, blue bolts)')
    ap.add_argument('--no-extra-properties', action='store_true', help='do not copy unmapped IFC properties')
    ap.add_argument('--threads', type=int, default=Options.threads)
    a = ap.parse_args(argv)
    opts = Options(); opts.linear_deflection = a.deflection; opts.ifc_colours = a.ifc_colours
    opts.extra_properties = not a.no_extra_properties; opts.threads = a.threads
    for src in a.input:
        if a.output and (len(a.input) == 1 and not os.path.isdir(a.output)):
            dst = a.output
        else:
            folder = a.output or os.path.dirname(os.path.abspath(src))
            dst = os.path.join(folder, os.path.splitext(os.path.basename(src))[0] + '.bswx')
        convert(src, dst, opts)


if __name__ == '__main__':
    sys.exit(main())
