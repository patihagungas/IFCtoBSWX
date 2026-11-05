"""Entry point of the installed application.

    IFCtoBSWX.exe                     opens the window
    IFCtoBSWX.exe a.ifc b.ifc         opens the window with these files (Open with / drop on icon)
    IFCtoBSWX.exe --cli a.ifc -o out  converts without a window (same options as python -m ifc2bswx)
"""
import multiprocessing, sys

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == '--cli':
        log = None
        if sys.stdout is None:                    # windowed build: keep a log file next to the output
            import os, tempfile
            log = open(os.path.join(tempfile.gettempdir(), 'IFCtoBSWX-cli.log'), 'w', encoding='utf-8')
            sys.stdout = sys.stderr = log
        from ifc2bswx.__main__ import main
        code = main(sys.argv[2:])
        if log: log.close()
        sys.exit(code)
    from ifc2bswx.gui import run
    run([a for a in sys.argv[1:] if a.lower().endswith('.ifc')])
