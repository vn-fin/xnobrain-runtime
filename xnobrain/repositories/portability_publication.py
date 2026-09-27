"""Atomic no-clobber publication of staged import resources on supported hosts."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

from .base import StoreError


def rename_without_replace(source: Path, target: Path) -> None:
    """Never implement this as exists() followed by ordinary rename().

    Linux renameat2 and macOS renamex_np both atomically reject any existing
    destination, including an empty directory created by another profile writer.
    Unsupported filesystems/hosts fail closed rather than overwrite a resource.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux" and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(target), 4)
    else:
        raise StoreError(
            "Atomic import publication unavailable", status=503, code="publication_unavailable"
        )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise StoreError(
                "Import target already exists", status=409, code="import_recovery_required"
            )
        raise StoreError(
            "Atomic import publication failed", status=503, code="publication_unavailable"
        )


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
