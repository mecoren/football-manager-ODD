# FMODD contributor and agent rules

These rules apply to this source repository.

- Read `README.md`, `docs/README.md`, `docs/ARCHITECTURE.md`, and the relevant `docs/FEATURE_INDEX.md` entry before changing unfamiliar code.
- Application code and runtime assets live in `src/`; tests and helper scripts live in `tests/` and `scripts/`. Run development commands from the repository root.
- Check Git status and the target diff first; preserve other work. Make changes only within the requested scope.
- Keep `data/`, saves, accounts, logs, local configurations, research payloads, and build outputs outside Git. Never commit credentials or proprietary game/editor binaries.
- The runtime remains Windows-only. Do not operate a live Football Manager process, install a hook, change a save/account, or launch an executable without explicit user authorization.
- Use the active `GameLayout` for version-dependent offsets. Preserve object identity checks, expected-old-value checks, readback, and rollback.
- Prefer focused, isolated tests. Do not automatically run all tests, restart services, or build a release for a documentation or source-publication change.
- Development serves `src/web/` directly. Do not hand-edit generated asset bundles. User-visible font sizes must be at least 13px.
- User-visible copy must support en-GB, zh-CN, zh-TW, ko-KR, de-DE, es-ES, fr-FR, ru-RU, ja-JP, pt-BR, and pt-PT.
- Update architecture, runtime contracts, or feature-index entries when their corresponding behavior or ownership changes.
- This repository provides development source and source runtime instructions. A passing static or mock test does not prove live-game behavior.

## Local release packaging

The published snapshot omits the maintainer's release chain: `build/*.spec`, `build/protection.toml`, `tools/_release_integrity.py` and the Cython protection step are absent or gitignored. A local build reproduces the observable packaging layout of the shipped `FMODD-V2.x.exe` (one-file, windowed, bundled `python3xx.dll`, `tools/*.dll`, `desktop_host/`) but is not an official release artifact.

### When the user says 发版 / build a release

`scripts/build_release.py` is the single entry point. It runs the whole chain in dependency order and verifies the result; do not reassemble the steps by hand.

```powershell
python scripts\build_release.py                # build + verify
python scripts\build_release.py --smoke-test   # also launch and wait for the local service
python scripts\build_release.py --skip-native   # reuse the existing Rust/C++ DLLs
```

Expected outcome: `dist/FMODD-<version>.exe` with the version taken from `PRODUCT_VERSION` in `src/fm_odds_web.py`. The script prints the version, size and payload verification, and fails loudly if the version resource or any bundled payload is wrong.

Bumping a release means editing `PRODUCT_VERSION` in `src/fm_odds_web.py` only; the file name and the Windows version resource are derived from it. Do not hand-write a version resource.

Before reporting a release as done, state explicitly: the artifact path and size, that payload verification passed, whether `--smoke-test` ran, and that a local build carries no Cython protection or tamper manifest. Never describe it as the official release.

Launching the EXE for UI verification still needs explicit user authorization; `--smoke-test` only starts it, waits for the local HTTP service and stops it again.

### Verified prerequisites

A Python interpreter, the Rust MSVC toolchain (`cargo`), Visual Studio C++ x64 build tools (`vswhere.exe` and `cl.exe`), and the system .NET Framework compiler `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`. The script creates its own PyInstaller venv at `src/build/release-venv` on first use instead of mutating the developer environment; `--no-venv` uses the current interpreter's PyInstaller instead.

### Tracked packaging sources

`scripts/build_release.py` plus `scripts/packaging/` (the PyInstaller spec and the integrity runtime hook) are tracked because they hold no vendor binaries and no secrets. Everything they produce or consume stays gitignored: `src/build/`, `dist/`, `dist-compatible*/`.

### Two failures this chain exists to absorb

- `dotnet build src\desktop\WebViewHost.csproj` fails because the `net48` targeting pack is not installed (`Reference Assemblies\...\v4.8` is missing). The script compiles the same WinForms host with the system `csc.exe` instead, passing `/win32manifest:src\desktop\FMODD.manifest`, `/win32icon:src\assets\desktop\fmodd.ico` and the restored WebView2 SDK references.
- Frozen mode requires `tools._release_integrity`. `native_library_loader._verify_frozen_library()` raises `FMODD native integrity verifier is missing` during startup, so a build without that module cannot even load the native cores. `scripts/packaging/pyi_rth_fmodd_integrity.py` supplies a no-op verifier exposing `verify_native_library(path, packaged_name)` and `verify_packaged_files(root)`; it must run before `fmodd_desktop` imports anything that loads a native library, which is why it is registered as a PyInstaller runtime hook rather than a normal import.

Local builds differ from the official release: no Cython `.pyd` protection, no tamper manifest (integrity checking is a no-op), a larger one-file archive, and `python3xx.dll` matches whichever interpreter performed the build rather than the shipped 3.13. A successful build and launch prove packaging only — never live Football Manager behavior.
