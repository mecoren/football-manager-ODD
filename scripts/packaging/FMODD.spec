# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the FMODD single-file desktop EXE.

Not part of the published source: the official release spec, protection
config and integrity manifest live in the maintainer's private build chain.
This spec reproduces the observable packaging layout of the shipped
FMODD-V2.x.exe (one-file, windowed, python3xx.dll, tools/*.dll, desktop_host).

Build from the repository root:
    python scripts\\build_release.py
"""

import os
from pathlib import Path

# SPECPATH is the directory holding this spec (scripts/packaging); the
# repository root is two levels up.
REPO = Path(SPECPATH).resolve().parent.parent
SRC = REPO / "src"
BUILD = SRC / "build"
PACKAGING = Path(SPECPATH).resolve()

# scripts/build_release.py sets both values per release so the version is not
# duplicated in this file. The defaults keep a hand-run spec working.
exe_name = os.environ.get("FMODD_RELEASE_NAME", "FMODD-V2.7.0beta")
version_file = os.environ.get("FMODD_RELEASE_VERSION_FILE", str(BUILD / "FMODD.version"))

web_root = SRC / "web"
assets_root = SRC / "assets"
desktop_host = BUILD / "desktop_host"
rust_dll = BUILD / "rust_native" / "fmodd_native_core.dll"
hook_dll = BUILD / "cpp_native" / "fmodd_hook_core.dll"
icon_path = SRC / "assets" / "desktop" / "fmodd.ico"

for required in (web_root, assets_root, desktop_host, rust_dll, hook_dll, icon_path):
    if not required.exists():
        raise SystemExit(
            f"missing build input: {required}\n"
            "Run scripts\\restore_webview2.py, scripts\\build_rust_native.py, "
            "scripts\\build_cpp_hook_core.py and the WebViewHost compile step first."
        )

datas = [
    (str(web_root), "web"),
    (str(assets_root), "assets"),
    (str(desktop_host), "desktop_host"),
    (str(rust_dll), "tools"),
    (str(hook_dll), "tools"),
]

# The diagnostics Tk UI and the research-only PE tools are not reachable from
# the desktop entry point; excluding them keeps the one-file archive small.
excludes = [
    "tkinter",
    "capstone",
    "pytest",
    "tests",
    "unittest",
    "pydoc_data",
]

hiddenimports = [
    "tools.embedded_web_assets",
]

a = Analysis(
    [str(SRC / "fmodd_desktop.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PACKAGING / "pyi_rth_fmodd_integrity.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=version_file,
)
