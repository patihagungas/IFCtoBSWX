# PyInstaller build:  python -m PyInstaller packaging/IFCtoBSWX.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules
root = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(root, 'app.py')],
    pathex=[root],
    binaries=collect_dynamic_libs('ifcopenshell'),
    datas=[(os.path.join(root, 'ifc2bswx', 'template_header.bin'), 'ifc2bswx'),
           (os.path.join(SPECPATH, 'app.ico'), '.')],
    hiddenimports=['ifcopenshell.ifcopenshell_wrapper', 'ifcopenshell.geom', 'ifcopenshell.util.element',
                   'ifcopenshell.util.placement', 'ifcopenshell.util.unit', 'ifcopenshell.guid',
                   'scipy.sparse.csgraph', 'scipy.sparse.csgraph._validation']
                  + collect_submodules('ifcopenshell.express', filter=lambda n: False),
    excludes=['matplotlib', 'PyQt5', 'PySide6', 'IPython', 'pandas', 'pytest', 'notebook', 'sympy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='IFCtoBSWX', console=False,
          icon=os.path.join(SPECPATH, 'app.ico'), version=None)
coll = COLLECT(exe, a.binaries, a.datas, name='IFCtoBSWX')
