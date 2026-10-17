"""Host resource usage (CPU, RAM, GPU)."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Any

import psutil

from .paths import data_dir

_gpu_cache: dict[str, Any] = {"t": 0.0, "data": []}


def gpus() -> list[dict[str, Any]]:
    if time.time() - _gpu_cache["t"] < 3:
        return _gpu_cache["data"]
    out: list[dict[str, Any]] = []
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            res = subprocess.run(
                [smi, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4)
            for line in res.stdout.strip().splitlines():
                name, total, used, util, temp = [x.strip() for x in line.split(",")]
                out.append({"name": name, "memory_total": float(total) * 1024 ** 2, "memory_used": float(used) * 1024 ** 2,
                            "utilization": float(util), "temperature": float(temp) if temp.replace(".", "").isdigit() else None})
        except Exception:
            pass
    _gpu_cache.update(t=time.time(), data=out)
    return out


def stats() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(str(data_dir()))
        disk_info = {"total": disk.total, "used": disk.used, "free": disk.free}
    except Exception:
        disk_info = None
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "memory": {"total": vm.total, "used": vm.total - vm.available, "percent": vm.percent},
        "gpus": gpus(),
        "disk": disk_info,
    }
