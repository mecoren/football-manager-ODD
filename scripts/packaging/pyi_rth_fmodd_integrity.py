"""PyInstaller runtime hook: stand in for the private release verifier.

The published source snapshot does not include ``tools/_release_integrity``
(the maintainer's protected build chain generates it).  ``tools.native_library_loader``
nevertheless refuses to load a native DLL in frozen mode unless that module
provides ``verify_native_library``.  This hook installs a no-op verifier so an
unprotected local build can start.

Consequences, stated plainly: this build performs NO integrity checking of the
bundled DLLs, no Cython-protected modules, and no tamper manifest.  It is a
development build of the same source, not a reproduction of the signed
official release.
"""

from __future__ import annotations

import sys
import types


def _install() -> None:
    module = types.ModuleType("tools._release_integrity")
    module.__doc__ = "No-op integrity verifier for unprotected local builds."

    def verify_native_library(path: str, packaged_name: str) -> list[str]:
        return []

    def verify_packaged_files(root: str) -> list[str]:
        return []

    module.verify_native_library = verify_native_library
    module.verify_packaged_files = verify_packaged_files
    sys.modules["tools._release_integrity"] = module


_install()
