"""PC pressure: disk free on / and the home volume, memory free percentage, 1-minute load per CPU.

FOUNDRY_PRESSURE_FAKE (JSON with the keys `measure` returns) replaces the measurement in tests.
"""

import json
import os
import re
import shutil
import subprocess


def _out(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def mem_free_pct():
    """`memory_pressure -Q` when present, else free+speculative pages from vm_stat; None when neither works."""
    if os.path.exists("/usr/bin/memory_pressure"):
        m = re.search(r"free percentage:\s*(\d+)%", _out(["/usr/bin/memory_pressure", "-Q"]) or "")
        if m:
            return int(m.group(1))
    vm = _out(["vm_stat"]) or ""
    page = re.search(r"page size of (\d+) bytes", vm)
    pages = [int(n) for n in re.findall(r"Pages (?:free|speculative):\s+(\d+)", vm)]
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        return None
    if not page or not pages or not total:
        return None
    return int(sum(pages) * int(page.group(1)) * 100 / total)


def measure():
    fake = os.environ.get("FOUNDRY_PRESSURE_FAKE")
    if fake:
        return json.loads(fake)
    disks = {}
    for p in ("/", os.path.expanduser("~")):
        try:
            disks[p] = round(shutil.disk_usage(p).free / 1e9, 1)
        except OSError:
            continue
    return {"disk_free_gb": disks, "mem_free_pct": mem_free_pct(),
            "load_per_cpu": round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)}

