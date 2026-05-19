"""Engine bridge — Task 3.6 from the rebuild plan.

The v1 engine is a subprocess (`tools/run_sim.sh`), not an importable
Python module. This shim:

  1. Materializes the recipe to a JSON file + the model to a local dir
     (downloaded from MinIO).
  2. Spawns `tools/run_sim.sh <model_dir> --config <recipe> --output <run>`
     via asyncio.create_subprocess_exec (the safe execFile-equivalent —
     fixed argv, no shell string interpolation).
  3. Concurrently:
     - tails stdout/stderr, dispatches every line to `on_log`
     - periodically scans the output dir, dispatches new frames to
       `on_frame`
     - polls `should_cancel`; on True sends SIGTERM, waits 5s, then SIGKILL
  4. Awaits the subprocess; returns (success, error_summary).

Paths match the v1 stack. Override via env (GSFLUENT_PKG_ROOT,
GSFLUENT_SIM_SCRIPT_RUNNER).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeAlias

import structlog

log = structlog.get_logger("engine")


PKG_ROOT = Path(os.environ.get("GSFLUENT_PKG_ROOT", "/opt/engine"))
SIM_SCRIPT = Path(
    os.environ.get(
        "GSFLUENT_SIM_SCRIPT_RUNNER",
        str(PKG_ROOT / "tools" / "run_sim.sh"),
    )
)

FRAME_RE = re.compile(r"frame[_-]?(\d+)\.(npz|ply)$", re.IGNORECASE)
ITER_PLY_RE = re.compile(r"iteration_(\d+)\.ply$", re.IGNORECASE)

SCAN_INTERVAL_S = 1.0
CANCEL_INTERVAL_S = 0.5

OnFrame: TypeAlias = Callable[[int, str, bytes], Awaitable[None]]
OnLog: TypeAlias = Callable[[str, str], Awaitable[None]]
ShouldCancel: TypeAlias = Callable[[], Awaitable[bool]]


async def _download_model_to(model_minio_path: str, dest_dir: Path) -> Path:
    """Download model's source.ply from MinIO into dest_dir."""
    from gsfluent_api.storage import get_minio_client

    bucket, _, key = model_minio_path.partition("/")
    out_file = dest_dir / "source.ply"

    def _download() -> None:
        client = get_minio_client()
        client.fget_object(bucket, key, str(out_file))

    await asyncio.to_thread(_download)
    log.info("engine.model_downloaded", path=str(out_file),
             size=out_file.stat().st_size)
    return out_file


async def _tail_stream(stream: asyncio.StreamReader, on_log: OnLog) -> None:
    """Forward lines from a subprocess stream to on_log."""
    while True:
        try:
            line = await stream.readline()
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            return
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        lower = text.lower()
        lvl = (
            "error" if "error" in lower or "traceback" in lower
            else "warning" if "warn" in lower
            else "info"
        )
        try:
            await on_log(lvl, text)
        except Exception as e:
            log.warning("on_log_failed", error=str(e)[:200])


async def _scan_once(output_dir: Path, on_frame: OnFrame,
                    seen: set[Path]) -> None:
    """One sweep of output_dir looking for new frame files."""
    for sub in (output_dir, output_dir / "frames", output_dir / "simulation_ply"):
        if not sub.is_dir():
            continue
        for f in sorted(sub.iterdir()):
            if not f.is_file() or f in seen:
                continue
            m = FRAME_RE.search(f.name) or ITER_PLY_RE.search(f.name)
            if not m:
                continue
            idx = int(m.group(1))
            kind = "cell" if f.suffix.lower() == ".npz" else "preview"
            try:
                data = await asyncio.to_thread(f.read_bytes)
            except FileNotFoundError:
                continue
            if not data:
                continue
            seen.add(f)
            try:
                await on_frame(idx, kind, data)
            except Exception as e:
                log.warning("on_frame_failed", path=str(f),
                            error=str(e)[:200])


async def _watch_output(output_dir: Path, on_frame: OnFrame) -> None:
    """Periodically scan output_dir for new frame files; emit each once."""
    seen: set[Path] = set()
    try:
        while True:
            await _scan_once(output_dir, on_frame, seen)
            await asyncio.sleep(SCAN_INTERVAL_S)
    except asyncio.CancelledError:
        await _scan_once(output_dir, on_frame, seen)


async def _cancel_poller(proc: asyncio.subprocess.Process,
                        should_cancel: ShouldCancel) -> bool:
    """Returns True if the process was killed via cancellation."""
    while proc.returncode is None:
        try:
            cancelled = await should_cancel()
        except Exception:
            cancelled = False
        if cancelled:
            log.info("engine.cancel_received", pid=proc.pid)
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                return True
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            return True
        await asyncio.sleep(CANCEL_INTERVAL_S)
    return False


async def run_engine(
    run_id: uuid.UUID,
    model_minio_path: str,
    recipe_snapshot: dict,
    particles: int,
    *,
    on_frame: OnFrame,
    on_log: OnLog,
    should_cancel: ShouldCancel,
) -> tuple[bool, str | None]:
    """Run a sim end-to-end. Returns (success, error_summary)."""
    if not SIM_SCRIPT.is_file():
        msg = (f"sim script not found at {SIM_SCRIPT} "
               "(set GSFLUENT_SIM_SCRIPT_RUNNER or mount engine at /opt/engine)")
        return False, msg

    workspace = Path(tempfile.mkdtemp(prefix=f"gsfluent-run-{run_id}-"))
    try:
        model_dir = workspace / "model"
        model_dir.mkdir()
        await _download_model_to(model_minio_path, model_dir)

        recipe_file = workspace / "recipe.json"
        recipe_file.write_text(json.dumps(recipe_snapshot, indent=2))

        output_dir = workspace / "output"
        output_dir.mkdir()

        cmd: list[str] = [
            "bash",
            str(SIM_SCRIPT),
            str(model_dir),
            "--config", str(recipe_file),
            "--particles", str(particles),
            "--output", str(run_id),
        ]
        await on_log("info", f"engine.spawn cmd={cmd!r}")

        env = os.environ.copy()
        env.setdefault("GSFLUENT_SIM_OUTPUT_DIR", str(output_dir))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PKG_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        assert proc.stdout is not None
        tail = asyncio.create_task(_tail_stream(proc.stdout, on_log))
        watch = asyncio.create_task(_watch_output(output_dir, on_frame))
        canceller = asyncio.create_task(_cancel_poller(proc, should_cancel))

        try:
            rc = await proc.wait()
        finally:
            for t in (tail, watch, canceller):
                t.cancel()
            await asyncio.gather(tail, watch, canceller, return_exceptions=True)

        # canceller.result() on a force-cancelled task raises CancelledError;
        # guard against that. Real cancellation is signaled by canceller
        # having returned True BEFORE the finally block cancelled it.
        cancelled = False
        if canceller.done():
            try:
                cancelled = bool(canceller.result())
            except (asyncio.CancelledError, Exception):
                cancelled = False

        if cancelled:
            return False, "cancelled by user"
        if rc != 0:
            return False, f"sim subprocess exited with rc={rc}"
        return True, None

    finally:
        shutil.rmtree(workspace, ignore_errors=True)
