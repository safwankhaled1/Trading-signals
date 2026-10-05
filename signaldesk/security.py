"""User-bound Windows DPAPI storage for Telegram sessions and local IPC keys."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data: bytes, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("حفظ الجلسات الآمن يتطلب Windows")
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob = Blob(len(data), source)
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(blob), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def save_secret(path: Path, data: dict):
    payload = base64.b64encode(_transform(json.dumps(data).encode("utf-8")))
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def load_secret(path: Path):
    if not path.exists():
        return {}
    return json.loads(_transform(base64.b64decode(path.read_bytes()), decrypt=True))
