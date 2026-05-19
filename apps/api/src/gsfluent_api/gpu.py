"""GPU info from nvidia-smi. Best-effort — returns ok=False with a reason
on non-NVIDIA hosts so /v1/system/health stays useful in dev / CI.

Uses asyncio.create_subprocess_exec (the *safe* execFile-equivalent — no
shell string interpolation, fixed argv list).
"""

from __future__ import annotations

import asyncio


async def gpu_info() -> dict[str, object]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode().strip()[:200]}
    except FileNotFoundError:
        return {"ok": False, "error": "nvidia-smi not on PATH"}

    gpus: list[dict[str, object]] = []
    for line in stdout.decode().strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            continue
        idx, name, mem_total, mem_used, util, temp = parts
        gpus.append({
            "index": int(idx),
            "name": name,
            "memory_total_mib": int(mem_total),
            "memory_used_mib": int(mem_used),
            "util_percent": int(util),
            "temperature_c": int(temp),
        })
    return {"ok": True, "gpus": gpus}
