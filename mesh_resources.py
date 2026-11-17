"""Allocation preflights based on the current machine, without mesh-size caps.

These estimates cover mesh construction buffers, not solver factorization.
They never change specimen dimensions, feature sampling or element sizes.
"""

import ctypes
import os
from pathlib import Path


def available_memory():
    """Available physical memory in bytes, or None when it cannot be queried."""
    try:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        "total_physical", "available_physical", "total_pagefile",
                        "available_pagefile", "total_virtual", "available_virtual", "reserved")]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return min(status.available_physical, status.available_virtual,
                           status.available_pagefile)
        elif Path("/proc/meminfo").is_file():
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        else:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    return None


def require_memory(required_bytes, stage):
    """Reject only a working-buffer estimate larger than currently free RAM."""
    available = available_memory()
    if available is not None and required_bytes > available:
        gib = 1024**3
        raise MemoryError(
            f"{stage} needs approximately {required_bytes / gib:,.2f} GiB of additional "
            f"working memory; {available / gib:,.2f} GiB is currently available. "
            "Run mesh generation on a machine with more free memory. "
            "Specimen dimensions and mesh sizes have not been changed.")
