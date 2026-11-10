"""Store remembered Zotero credentials outside the project checkout."""

import hashlib
import os
from pathlib import Path
import stat
import tempfile


CACHE_DIR = Path.home() / ".atomdeeplit" / "zotero-authorizations"


def cache_path(server):
    return CACHE_DIR / (hashlib.sha256(server.encode("utf-8")).hexdigest() + ".key")


def windows_dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.addressof(buffer))
    target = DataBlob()
    try:
        # User-scoped DPAPI. CRYPTPROTECT_UI_FORBIDDEN prevents a second prompt.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            kernel32.LocalFree(target.data)


def load(server):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(cache_path(server), flags)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or (
            os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
        ):
            raise PermissionError("Insecure Zotero authorization file")
        data = file.read(4097)
    if not data or len(data) > 4096:
        raise ValueError("Invalid Zotero authorization file")
    key = (windows_dpapi(data, protect=False) if os.name == "nt" else data).decode("ascii")
    if not key.isalnum():
        raise ValueError("Invalid Zotero authorization key")
    return key


def save(server, key):
    if not isinstance(key, str) or not key.isascii() or not key.isalnum():
        raise ValueError("Invalid Zotero authorization key")
    data = key.encode("ascii")
    if os.name == "nt":
        data = windows_dpapi(data, protect=True)
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=CACHE_DIR, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.close()
            os.replace(temporary, cache_path(server))
        finally:
            temporary.unlink(missing_ok=True)
