# PyInstaller spec for ESDetect.
# This bundles the desktop UI as a standalone app while still allowing
# processing subprocesses to use an external suite2p Python environment.

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

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

SPEC_DIR = Path.cwd()
APP_BUNDLE_NAME = "ESDetect"
APP_BUNDLE_ID = "com.sannyslaps.esdetect"
APP_VERSION = "0.0.0"


def collect_tree(src_rel, dest_root):
    src_root = (SPEC_DIR / src_rel).resolve()
    collected = []
    for path in src_root.rglob("*"):
        if path.is_file():
            rel_parent = path.parent.relative_to(src_root)
            target_dir = Path(dest_root) / rel_parent
            collected.append((str(path), str(target_dir).replace("\\", "/")))
    return collected


datas = []
datas += collect_tree("../suite2p_sandbox/scripts", "suite2p_sandbox/scripts")
datas += collect_tree("../suite2p_sandbox/configs", "suite2p_sandbox/configs")
datas += collect_tree("../suite2p_sandbox/external", "suite2p_sandbox/external")
datas += collect_tree("./presets", "suite2p_frontend/presets")
datas += collect_tree("../Acquisition and Stim", "Acquisition and Stim")

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
    name=f"{APP_BUNDLE_NAME}.app",
    icon=None,
    bundle_identifier=APP_BUNDLE_ID,
    info_plist={
        "CFBundleName": APP_BUNDLE_NAME,
        "CFBundleDisplayName": APP_BUNDLE_NAME,
        "CFBundleIdentifier": APP_BUNDLE_ID,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
    },
)
