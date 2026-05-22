"""CLI entry point: `python -m gsfluent.core.sim_engines.mpm`.

Used by the slim run_sim.sh shim so the conda activation block in shell
hands control to Python as soon as possible. Argument parsing here
mirrors the old shell script's CLI so existing callers keep working.

Usage:
    python -m gsfluent.core.sim_engines.mpm \\
        <model_dir> --config <recipe.json> \\
        --particles N --output <run_name>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from gsfluent.core.sim_engines.mpm import MPMSimulationEngine
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.sim import ModelRef, SimError


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gsfluent.core.sim_engines.mpm")
    p.add_argument("model_dir", type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--particles", required=True, type=int)
    p.add_argument("--output", required=True, type=str)
    p.add_argument(
        "--wall-time-sec",
        type=int,
        default=int(os.environ.get("GSFLUENT_MAX_WALL_TIME_SEC", "3600")),
    )
    return p.parse_args()


async def _amain() -> int:
    args = _parse_args()

    recipe = json.loads(args.config.read_text())
    recipe["_run_name"] = args.output
    recipe.setdefault("particle_count", args.particles)

    sim_home = Path(os.environ.get("GSFLUENT_SIM_HOME", ""))
    sim_python = os.environ.get("GSFLUENT_SIM_PYTHON", "python")
    sim_env = os.environ.get("GSFLUENT_SIM_ENV") or None
    sim_fast = os.environ.get("GSFLUENT_SIM_FAST", "0") == "1"

    eng = MPMSimulationEngine(
        sim_home=sim_home,
        sim_python=sim_python,
        sim_env=sim_env,
        sim_fast=sim_fast,
        require_gpu=False,  # CLI is also used in tests; let preflight be lenient here
    )

    emitter = StdlibJSONEmitter(stream=sys.stdout).child(run_name=args.output)

    try:
        await eng.preflight()
        result = await eng.run(
            recipe=recipe,
            model=ModelRef(name=args.model_dir.name, path=args.model_dir),
            output_dir=Path("work/library/sequences") / args.output,
            wall_time_sec=args.wall_time_sec,
            on_event=emitter,
        )
    except SimError as e:
        emitter.emit("cli.failed", error_kind=type(e).__name__, message=str(e))
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    emitter.emit("cli.completed", n_frames=result.n_frames, frames_dir=str(result.frames_dir))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
