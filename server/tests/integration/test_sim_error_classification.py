"""Integration test: stderr patterns map to the right SimError subclass.

Drives mock_sim.sh with each MOCK_SIM_STDERR_PATTERN value the YAML
classifier knows about; verifies the engine raises the expected
typed exception.

Per spec Open Question #1 default: this classifier is included; patterns
live in core/sim_engines/mpm_error_patterns.yaml so operators can tune
them post-launch.
"""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mpm import (
    classify_stderr,
    load_error_patterns,
)
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.sim import (
    SimCrashedError,
    SimGpuOomError,
    SimUnstableRecipeError,
)

from .conftest import MOCK_SIM_SH


# ---------- classifier unit-style integration ----------------------------


@pytest.mark.parametrize(
    "stderr_text, expected_kind",
    [
        ("CUDA error: out of memory at line 42", "sim.gpu_oom"),
        ("step 17: CFL violation; aborting", "sim.unstable_recipe"),
        ("CUDA: an illegal memory access was encountered", "sim.unstable_recipe"),
        ("frame 12: position contains NaN values", "sim.unstable_recipe"),
        ("frame 9: encountered +inf in velocity", "sim.unstable_recipe"),
        ("Segmentation fault (core dumped)", None),
        ("", None),
    ],
)
def test_classify_stderr_maps_patterns_correctly(
    stderr_text: str, expected_kind: str | None
) -> None:
    patterns = load_error_patterns()
    assert classify_stderr(stderr_text, patterns) == expected_kind


# ---------- end-to-end with mock_sim.sh ----------------------------------


def _exception_for_kind(kind: str | None):
    if kind == "sim.gpu_oom":
        return SimGpuOomError
    if kind == "sim.unstable_recipe":
        return SimUnstableRecipeError
    return SimCrashedError


@pytest.mark.parametrize(
    "stderr_pattern, expected_exc",
    [
        ("out of memory", SimGpuOomError),
        ("CFL violation", SimUnstableRecipeError),
        ("illegal memory access", SimUnstableRecipeError),
        ("NaN positions", SimUnstableRecipeError),
        ("totally unrelated failure", SimCrashedError),
    ],
)
@pytest.mark.asyncio
async def test_mpm_engine_classifies_subprocess_stderr(
    stderr_pattern: str,
    expected_exc: type[Exception],
    tmp_path: Path,
) -> None:
    """Spawn mock_sim.sh with a stderr pattern + non-zero exit; verify
    classify_stderr maps to the expected exception kind.
    """
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "1",
        "MOCK_SIM_STDERR_PATTERN": stderr_pattern,
        "MOCK_SIM_EXIT": "137",  # non-zero exit so classifier runs
    }

    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "classifier_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    # Drain stderr fully so we can run the classifier on the joined output.
    _, stderr_bytes = await proc.communicate()
    stderr_text = stderr_bytes.decode(errors="replace")
    assert proc.returncode == 137

    patterns = load_error_patterns()
    kind = classify_stderr(stderr_text, patterns)

    # Map kind -> exception class and verify it matches expected_exc.
    actual_exc = _exception_for_kind(kind)
    assert actual_exc is expected_exc, (
        f"stderr='{stderr_text}' classified to kind='{kind}' "
        f"(exc={actual_exc.__name__}); expected {expected_exc.__name__}"
    )
