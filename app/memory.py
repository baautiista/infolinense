"""Conservative runtime memory maintenance for a long-lived Railway worker.

Releases unreachable Python objects and, when available on Linux/glibc,
asks the allocator to return free heap pages to the OS. This is only
best-effort: it does not enforce a memory limit or replace profiling.
Never changes application data, jobs, or publication schedules.
"""
import gc
import os
import threading
import time

_guard = threading.Lock()
_last = 0.0


def maybe_trim(interval_minutes=None, *, now=None):
    """Run maintenance at most once per interval; return whether it ran.

    A 0-minute interval disables it. The lock prevents overlapping
    collections from the scan and publication background threads.
    """
    global _last
    if interval_minutes is None:
        try:
            interval_minutes = float(os.getenv("MEMORY_TRIM_INTERVAL_MINUTES", "30"))
        except ValueError:
            interval_minutes = 30.0
    if interval_minutes <= 0:
        return False
    instant = time.monotonic() if now is None else now
    if instant - _last < interval_minutes * 60:
        return False
    if not _guard.acquire(blocking=False):
        return False
    try:
        if instant - _last < interval_minutes * 60:
            return False
        _last = instant
        gc.collect()
        if os.name == "posix":
            try:
                import ctypes
                libc = ctypes.CDLL(None)
                trim = getattr(libc, "malloc_trim", None)
                if trim is not None:
                    trim.argtypes = [ctypes.c_size_t]
                    trim.restype = ctypes.c_int
                    trim(0)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        return True
    finally:
        _guard.release()


def resident_memory_mb():
    """Approximate resident memory on Linux; null on unsupported platforms."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None
