# PyInstaller spec for ESDetect.
# This bundles the desktop UI as a standalone app while still allowing
# processing subprocesses to use an external suite2p Python environment.

from PyInstaller.utils.hooks import Tree, collect_submodules

hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_tkagg",
    "scipy.special.cython_special",
]

for pkg in ("PIL", "matplotlib", "numpy", "scipy", "openpyxl"):
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except Exception:
        pass

datas = [
    Tree("../suite2p_sandbox/scripts", prefix="suite2p_sandbox/scripts"),
    Tree("../suite2p_sandbox/configs", prefix="suite2p_sandbox/configs"),
    Tree("../suite2p_sandbox/external", prefix="suite2p_sandbox/external"),
    Tree("./presets", prefix="suite2p_frontend/presets"),
    Tree("../Acquisition and Stim", prefix="Acquisition and Stim"),
]

a = Analysis(
    ["external_soma_frontend_app/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "tensorflow",
        "jupyter",
        "notebook",
        "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ESDetect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ESDetect",
)

app = BUNDLE(
    coll,
    name="ESDetect.app",
    icon=None,
    bundle_identifier=None,
)
