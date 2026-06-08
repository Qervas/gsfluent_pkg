"""GPU discovery and per-run CUDA pinning helpers for MPM simulations."""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable

from gsfluent.protocols.observability import EventEmitter

_DEFAULT_MIN_FREE_MIB = 20 * 1024


def pick_free_gpu(nvidia_smi_csv_text: str, min_free_mib: int) -> int | None:
    """Pick the least-busy GPU index that has >= min_free_mib free memory."""
    candidates: list[tuple[int, int, int]] = []
    for line in nvidia_smi_csv_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [c.strip() for c in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            index = int(parts[0])
            util = int(parts[1])
            free_mib = int(parts[2])
        except (ValueError, TypeError):
            continue
        if free_mib < min_free_mib:
            continue
        candidates.append((util, -free_mib, index))
    if not candidates:
        return None
    _, _, index = min(candidates)
    return index


def _query_nvidia_smi_csv() -> str | None:
    """Return raw index,util,free CSV from local nvidia-smi, or None."""
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return None
    try:
        result = subprocess.run(
            [
                nvsmi,
                "--query-gpu=index,utilization.gpu,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _auto_gpu_enabled() -> bool:
    """Whether auto-GPU selection is on. Default ON."""
    raw = os.environ.get("GSFLUENT_AUTO_GPU")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _resolve_sim_gpu_env(
    *,
    on_event: EventEmitter,
    query: Callable[[], str | None] = _query_nvidia_smi_csv,
) -> dict[str, str] | None:
    """Compute the env overlay for the sim subprocess's GPU pin, or None."""
    if not _auto_gpu_enabled():
        on_event.debug("sim.gpu_autopick_skipped", reason="disabled")
        return None
    try:
        min_free_mib = int(
            os.environ.get("GSFLUENT_GPU_MIN_FREE_MIB", _DEFAULT_MIN_FREE_MIB)
        )
    except (TypeError, ValueError):
        min_free_mib = _DEFAULT_MIN_FREE_MIB
    try:
        csv_text = query()
        if not csv_text:
            on_event.debug("sim.gpu_autopick_skipped", reason="query_failed")
            return None
        index = pick_free_gpu(csv_text, min_free_mib)
        if index is None:
            on_event.info(
                "sim.gpu_autopick_skipped",
                reason="no_gpu_qualified",
                min_free_mib=min_free_mib,
            )
            return None
        util, free_mib = _gpu_stats_for_index(csv_text, index)
        on_event.info(
            "sim.gpu_autopicked",
            gpu_index=index,
            util=util,
            free_mib=free_mib,
            min_free_mib=min_free_mib,
        )
        return {"CUDA_VISIBLE_DEVICES": str(index)}
    except Exception as exc:  # noqa: BLE001 - GPU selection must never fail a run
        on_event.error("sim.gpu_autopick_skipped", reason=f"error:{exc!r}")
        return None


def _gpu_stats_for_index(
    csv_text: str, index: int
) -> tuple[int | None, int | None]:
    """Return (util, free_mib) for `index` from the CSV, or (None, None)."""
    for line in csv_text.splitlines():
        parts = [c.strip() for c in line.strip().split(",")]
        if len(parts) != 3:
            continue
        try:
            if int(parts[0]) == index:
                return int(parts[1]), int(parts[2])
        except (ValueError, TypeError):
            continue
    return None, None


def _gpu_reachable() -> bool:
    """Return True iff nvidia-smi reports at least one CUDA-capable device."""
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return False
    try:
        result = subprocess.run(
            [nvsmi, "-L"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    return any(line.startswith("GPU ") for line in result.stdout.splitlines())
