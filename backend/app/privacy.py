from __future__ import annotations

import os
import stat
from pathlib import Path


def secure_private_directory(path: Path) -> None:
    """Restrict a private directory to its owner on POSIX.

    Windows builds rely on the inherited ACL of the configured data directory;
    POSIX mode bits are not an ACL substitute there.
    """
    if os.name == "posix":
        path.chmod(0o700)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PermissionError("Private directory permissions could not be enforced.")


def secure_private_file(path: Path) -> None:
    """Restrict an existing private file to its owner on POSIX."""
    if os.name == "posix":
        path.chmod(0o600)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PermissionError("Private file permissions could not be enforced.")
