"""MPM-specific unit tests: pattern loading, classifier, preflight."""
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mpm import (
    MPMErrorPattern,
    MPMSimulationEngine,
    check_sim_stability,
    classify_stderr,
    load_error_patterns,
)
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    SimCrashedError,
    SimEnvMissingError,
    SimGpuOomError,
    SimInterpreterMissingError,
    SimUnstableRecipeError,
)

# ---------- pattern loading ----------------------------------------------


def test_default_patterns_load_from_yaml() -> None:
    pats = load_error_patterns()
    kinds = {p.error_kind for p in pats}
    assert "sim.gpu_oom" in kinds
    assert "sim.unstable_recipe" in kinds


def test_pattern_dataclass_holds_compiled_regex() -> None:
    pats = load_error_patterns()
    for p in pats:
        assert isinstance(p, MPMErrorPattern)
        # Compiled regex pattern; .search() should be available.
        assert hasattr(p.compiled, "search")


def test_load_error_patterns_from_explicit_path(tmp_path: Path) -> None:
    yml = tmp_path / "patterns.yaml"
    yml.write_text(
        "patterns:\n"
        "  - error_kind: sim.gpu_oom\n"
        "    regex: 'totally out of memory'\n"
        "    case_insensitive: true\n"
    )
    pats = load_error_patterns(path=yml)
    assert len(pats) == 1
    assert pats[0].error_kind == "sim.gpu_oom"
    assert pats[0].compiled.search("Totally Out Of Memory") is not None


# ---------- classifier ---------------------------------------------------


def test_classify_gpu_oom() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("CUDA error: out of memory at line 42", pats)
    assert kind == "sim.gpu_oom"


def test_classify_cfl() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("step 17: CFL violation", pats)
    assert kind == "sim.unstable_recipe"


def test_classify_illegal_memory() -> None:
    pats = load_error_patterns()
    kind = classify_stderr(
        "CUDA Runtime: an illegal memory access was encountered", pats
    )
    assert kind == "sim.unstable_recipe"


def test_classify_nan_inf() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("frame 23: position contains NaN", pats)
    assert kind == "sim.unstable_recipe"


def test_classify_unmatched_returns_none() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("Segmentation fault (core dumped)", pats)
    assert kind is None


def test_classify_empty_stderr_returns_none() -> None:
    pats = load_error_patterns()
    assert classify_stderr("", pats) is None


def test_classify_first_match_wins() -> None:
    pats = load_error_patterns()
    # "out of memory" + "NaN" both present -> gpu_oom wins (declared first).
    kind = classify_stderr("Error: out of memory; NaN positions", pats)
    assert kind == "sim.gpu_oom"


# ---------- preflight ----------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_raises_sim_env_missing(tmp_path: Path) -> None:
    eng = MPMSimulationEngine(
        sim_home=tmp_path / "does_not_exist",
        sim_python="/usr/bin/python3",
        sim_env=None,
    )
    with pytest.raises(SimEnvMissingError):
        await eng.preflight()


@pytest.mark.asyncio
async def test_preflight_raises_sim_interpreter_missing(tmp_path: Path) -> None:
    (tmp_path / "sim_home").mkdir()
    eng = MPMSimulationEngine(
        sim_home=tmp_path / "sim_home",
        sim_python="/nonexistent/python_interpreter_xyz",
        sim_env=None,
    )
    with pytest.raises(SimInterpreterMissingError):
        await eng.preflight()


@pytest.mark.asyncio
async def test_preflight_passes_with_valid_env(tmp_path: Path) -> None:
    """Preflight should accept a real sim_home dir + on-PATH python."""
    sh = tmp_path / "sim_home"
    sh.mkdir()
    # Use the actual python that's running this test — guaranteed to exist.
    import sys
    eng = MPMSimulationEngine(
        sim_home=sh,
        sim_python=sys.executable,
        sim_env=None,
        require_gpu=False,  # tests run on CPU-only CI hosts
    )
    # Should not raise.
    await eng.preflight()


# ---------- sim-stability guard (NaN frame-drop detection) ----------------


def test_check_sim_stability_complete_run_is_ok() -> None:
    # Every sim frame fused → no instability.
    assert check_sim_stability(n_sim=31, n_fused=31, allowed_nonfinite=0) is None


def test_check_sim_stability_flags_diverged_run() -> None:
    # Sim wrote 11 frames, only 4 fused → 7 went NaN/Inf.
    msg = check_sim_stability(n_sim=11, n_fused=4, allowed_nonfinite=0)
    assert msg is not None
    assert "11" in msg and "diverged" in msg.lower()


def test_check_sim_stability_respects_tolerance() -> None:
    # One dropped frame within an explicit tolerance is allowed.
    assert check_sim_stability(n_sim=11, n_fused=10, allowed_nonfinite=1) is None
    # Three dropped exceeds a tolerance of one → flagged.
    assert check_sim_stability(n_sim=11, n_fused=8, allowed_nonfinite=1) is not None


def test_check_sim_stability_no_sim_frames_is_noop() -> None:
    # Empty sim output is a different failure path (handled elsewhere).
    assert check_sim_stability(n_sim=0, n_fused=0, allowed_nonfinite=0) is None
