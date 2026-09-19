"""Machine facts attached to every analytics event.

This reads
`/proc/meminfo` and `/proc/cpuinfo`, and reports the distribution rather than
the kernel version, which is the useful grouping for a desktop app.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class AnalyticsSystemConfiguration:
    ram_gb: int
    chip: str
    os_version: str

    @staticmethod
    @lru_cache(maxsize=1)
    def current() -> "AnalyticsSystemConfiguration":
        return AnalyticsSystemConfiguration(
            ram_gb=installed_ram_gigabytes(),
            chip=cpu_model_name(),
            os_version=operating_system_version(),
        )


def installed_ram_gigabytes() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 1
    bytes_per_gigabyte = 1_073_741_824
    rounded = pages * page_size + bytes_per_gigabyte // 2
    return max(1, rounded // bytes_per_gigabyte)


def cpu_model_name() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith(("model name", "hardware", "cpu model")):
                    _, _, value = line.partition(":")
                    name = value.strip()
                    if name:
                        return name
    except OSError:
        pass
    import platform

    return platform.machine() or "unknown"


def operating_system_version() -> str:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            content = handle.read()
        match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', content, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    import platform

    return platform.platform()
