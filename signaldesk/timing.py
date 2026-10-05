"""Keep Windows' timer resolution paired with the lifetime of the worker."""
from contextlib import contextmanager
import ctypes
import sys


@contextmanager
def high_resolution_timer():
    timer = None
    if sys.platform == "win32":
        try:
            candidate = ctypes.WinDLL("winmm")
            for function in (candidate.timeBeginPeriod, candidate.timeEndPeriod):
                function.argtypes = [ctypes.c_uint]
                function.restype = ctypes.c_uint
            if candidate.timeBeginPeriod(1) == 0:
                timer = candidate
        except (OSError, AttributeError):
            pass  # Execution still works with the operating system's default timer.
    try:
        yield timer is not None
    finally:
        if timer:
            timer.timeEndPeriod(1)
