"""Drives a real conversion through the window code (no clicking needed)."""
import sys, time, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from ifc2bswx.gui import App

if __name__ == '__main__':
    src, out = sys.argv[1], os.path.abspath(sys.argv[2])
    app = App([src])
    app.out_mode.set('folder'); app.out_dir.set(out); app._toggle_out()
    app._start(); app.update(); t = time.time(); seen = set()
    while str(app.convert_btn['state']) == 'disabled' and time.time() - t < 900:
        app.update(); time.sleep(0.05)
        b = int(app.bar['value']) // 250
        if b not in seen: seen.add(b); print(f'{time.time() - t:5.1f}s bar={int(app.bar["value"])} {app.status["text"]}')
    print('LOG:\n' + app.log.get('1.0', 'end').strip())
    app.destroy()
    print(os.listdir(out))
