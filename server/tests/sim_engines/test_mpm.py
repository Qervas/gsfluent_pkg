"""MPM-specific unit tests: pattern loading, classifier, preflight."""
import os
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mpm import (
    MPMErrorPattern,
    MPMSimulationEngine,
    _auto_gpu_enabled,
    _expected_sim_frames,
    _resolve_sim_gpu_env,
    check_sim_stability,
    classify_stderr,
    load_error_patterns,
    pick_free_gpu,
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


def test_check_sim_stability_flags_truncated_sim() -> None:
    # The headline production bug: a diverged solver stops EARLY, so it
    # writes fewer sim frames than the recipe requested. The fuser keeps
    # every frame the sim emitted (n_sim == n_fused), so the NaN-drop
    # signature alone misses it — but expected_frames catches the shortfall.
    msg = check_sim_stability(
        n_sim=8, n_fused=8, allowed_nonfinite=0, expected_frames=13
    )
    assert msg is not None
    assert "diverged" in msg.lower()
    assert "8" in msg and "13" in msg


def test_check_sim_stability_complete_run_with_expected_is_ok() -> None:
    # A complete run: sim wrote all requested frames, all fused. No flag.
    assert (
        check_sim_stability(
            n_sim=13, n_fused=13, allowed_nonfinite=0, expected_frames=13
        )
        is None
    )


def test_check_sim_stability_truncation_respects_tolerance() -> None:
    # One missing frame within tolerance is allowed; two exceeds it.
    assert (
        check_sim_stability(
            n_sim=12, n_fused=12, allowed_nonfinite=1, expected_frames=13
        )
        is None
    )
    assert (
        check_sim_stability(
            n_sim=11, n_fused=11, allowed_nonfinite=1, expected_frames=13
        )
        is not None
    )


def test_check_sim_stability_expected_none_is_backward_compatible() -> None:
    # Legacy callers pass no expected_frames: behaviour is exactly the old
    # NaN-drop-only check.
    assert (
        check_sim_stability(n_sim=13, n_fused=13, allowed_nonfinite=0) is None
    )
    assert (
        check_sim_stability(n_sim=13, n_fused=5, allowed_nonfinite=0) is not None
    )


def test_expected_sim_frames_is_frame_num_plus_one() -> None:
    # A complete sim writes frame_num + 1 plys (frame 0 = initial state).
    # Empirically confirmed: frame_num=30 -> 31, 150 -> 151, 12 -> 13.
    assert _expected_sim_frames({"frame_num": 12}) == 13
    assert _expected_sim_frames({"frame_num": 30}) == 31


def test_expected_sim_frames_missing_or_invalid_is_none() -> None:
    # No usable frame_num -> None so the guard falls back to NaN-drop only
    # (never a false positive from a missing field).
    assert _expected_sim_frames({}) is None
    assert _expected_sim_frames({"frame_num": "nope"}) is None
    assert _expected_sim_frames({"frame_num": 0}) is None


# ---------- sim argv building (CFL clamp safety) -------------------------


def _make_engine(*, sim_fast: bool) -> MPMSimulationEngine:
    import sys
    return MPMSimulationEngine(
        sim_home=Path("/tmp/nonexistent_sim_home"),
        sim_python=sys.executable,
        sim_env=None,
        require_gpu=False,
        sim_fast=sim_fast,
    )


def _build_argv(eng: MPMSimulationEngine) -> list[str]:
    return eng._build_sim_argv(
        model_dir=Path("/tmp/model"),
        sim_output_dir=Path("/tmp/out"),
        config_path=Path("/tmp/recipe.json"),
        particles=200_000,
    )


def test_slow_path_never_passes_no_cfl_override() -> None:
    # Default (non-fast) path must always let the solver clamp dt.
    argv = _build_argv(_make_engine(sim_fast=False))
    assert "--no_cfl_override" not in argv
    assert "--graph_capture" not in argv


def test_fast_path_does_not_disable_cfl_clamp() -> None:
    # The fast path must NOT pass --no_cfl_override: doing so disables the
    # solver's `substep_dt = min(recipe_dt, cfl_dt)` safety net, letting a
    # too-large recipe substep_dt diverge silently. The clamp only ever
    # tightens dt, so it's always safe to leave on.
    argv = _build_argv(_make_engine(sim_fast=True))
    assert "--no_cfl_override" not in argv


def test_fast_path_still_enables_graph_capture() -> None:
    # --graph_capture is an orthogonal perf optimization (CUDA graph fusion)
    # with no bearing on time-step stability — it stays on the fast path.
    argv = _build_argv(_make_engine(sim_fast=True))
    assert "--graph_capture" in argv


def test_build_sim_argv_has_required_invariant_flags() -> None:
    # Sanity: the core argv shape is unchanged regardless of fast/slow.
    for fast in (False, True):
        argv = _build_argv(_make_engine(sim_fast=fast))
        assert "--model_path" in argv
        assert "--output_path" in argv
        assert "--config" in argv
        assert "--target_particles" in argv
        assert "--output_ply" in argv
        assert "--async_io" in argv
        # GPU sim-R rotation output (Track-1): each particle's polar R emitted
        # per frame for the fuser to consume instead of CPU Kabsch SVD.
        assert "--output_rot" in argv


# ---------- auto-GPU selection: pick_free_gpu (pure parser) ---------------

# A representative shared 8-GPU box, exactly as
#   nvidia-smi --query-gpu=index,utilization.gpu,memory.free \
#              --format=csv,noheader,nounits
# prints it (index, util%, free_MiB):
_REAL_CSV = (
    "0, 36, 57214\n"
    "1, 23, 47713\n"
    "2, 0, 47349\n"
    "3, 18, 47339\n"
    "4, 4, 47339\n"
    "5, 34, 47339\n"
    "6, 0, 55582\n"
    "7, 100, 47483\n"
)


def test_pick_free_gpu_picks_lowest_util_with_enough_mem() -> None:
    # GPUs 2 and 6 are both at 0% util and clear the 20 GiB floor. Tie on util
    # breaks toward most free memory -> GPU 6 (55582 > 47349 MiB).
    assert pick_free_gpu(_REAL_CSV, min_free_mib=20 * 1024) == 6


def test_pick_free_gpu_skips_gpus_below_free_floor() -> None:
    # Only GPU 0 (57214 MiB) clears a 55 GiB (56320 MiB) floor — every other
    # GPU, including the otherwise-idle GPU 6 (55582), is below it. Memory is a
    # hard filter applied before the util sort, so the busier-but-roomier GPU 0
    # wins.
    assert pick_free_gpu(_REAL_CSV, min_free_mib=55 * 1024) == 0


def test_pick_free_gpu_lowest_util_wins_when_mem_ample() -> None:
    csv = "0, 80, 60000\n1, 10, 60000\n2, 50, 60000\n"
    assert pick_free_gpu(csv, min_free_mib=20 * 1024) == 1


def test_pick_free_gpu_util_tie_breaks_to_lowest_index() -> None:
    # Equal util AND equal free memory -> deterministic: lowest index wins.
    csv = "3, 0, 50000\n1, 0, 50000\n2, 0, 50000\n"
    assert pick_free_gpu(csv, min_free_mib=20 * 1024) == 1


def test_pick_free_gpu_returns_none_when_all_full() -> None:
    # Every GPU is below the free-memory floor -> no qualifying GPU.
    csv = "0, 0, 1000\n1, 5, 2000\n2, 0, 500\n"
    assert pick_free_gpu(csv, min_free_mib=20 * 1024) is None


def test_pick_free_gpu_returns_none_when_all_busy_and_full() -> None:
    csv = "0, 100, 100\n1, 99, 200\n"
    assert pick_free_gpu(csv, min_free_mib=20 * 1024) is None


def test_pick_free_gpu_empty_input_is_none() -> None:
    assert pick_free_gpu("", min_free_mib=20 * 1024) is None
    assert pick_free_gpu("\n\n  \n", min_free_mib=20 * 1024) is None


def test_pick_free_gpu_malformed_rows_are_skipped_not_fatal() -> None:
    # Garbage / short / non-numeric rows are skipped; the one good row wins.
    csv = (
        "this is not csv\n"
        "0, notanint, 60000\n"        # non-numeric util
        "1, 5\n"                       # too few columns
        "2, 5, 60000, extra\n"         # too many columns
        "3, 12, 60000\n"               # the only valid, qualifying row
    )
    assert pick_free_gpu(csv, min_free_mib=20 * 1024) == 3


def test_pick_free_gpu_all_malformed_is_none() -> None:
    assert pick_free_gpu("garbage\nmore garbage\n", min_free_mib=1024) is None


def test_pick_free_gpu_exactly_at_floor_qualifies() -> None:
    # free == min_free_mib is acceptable (>= comparison).
    csv = "0, 50, 20480\n"
    assert pick_free_gpu(csv, min_free_mib=20480) == 0
    assert pick_free_gpu("0, 50, 20479\n", min_free_mib=20480) is None


# ---------- auto-GPU flag gating ------------------------------------------


@pytest.fixture
def _clear_gpu_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GSFLUENT_AUTO_GPU", raising=False)
    monkeypatch.delenv("GSFLUENT_GPU_MIN_FREE_MIB", raising=False)


def test_auto_gpu_enabled_default_on(_clear_gpu_env: None) -> None:
    assert _auto_gpu_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", "Off", ""])
def test_auto_gpu_disabled_values(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    monkeypatch.setenv("GSFLUENT_AUTO_GPU", val)
    assert _auto_gpu_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "anything"])
def test_auto_gpu_enabled_values(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    monkeypatch.setenv("GSFLUENT_AUTO_GPU", val)
    assert _auto_gpu_enabled() is True


# ---------- auto-GPU resolution + event emission --------------------------


class _RecordingEmitter:
    """Minimal EventEmitter test double — records (event, context) tuples."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, **context: object) -> None:
        self.events.append((event, dict(context)))

    def child(self, **context: object) -> "_RecordingEmitter":
        return self

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


def test_resolve_picks_gpu_and_emits_event(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    em = _RecordingEmitter()
    overlay = _resolve_sim_gpu_env(on_event=em, query=lambda: _REAL_CSV)
    # GPU 6 is the least-busy with ample memory (see pick_free_gpu tests).
    assert overlay == {"CUDA_VISIBLE_DEVICES": "6"}
    assert "sim.gpu_autopicked" in em.names()
    ev = next(c for n, c in em.events if n == "sim.gpu_autopicked")
    assert ev["gpu_index"] == 6
    assert ev["util"] == 0
    assert ev["free_mib"] == 55582


def test_resolve_respects_custom_min_free_floor(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    # With a 55 GiB floor only GPU 0 qualifies (see pick_free_gpu test).
    monkeypatch.setenv("GSFLUENT_GPU_MIN_FREE_MIB", str(55 * 1024))
    em = _RecordingEmitter()
    overlay = _resolve_sim_gpu_env(on_event=em, query=lambda: _REAL_CSV)
    assert overlay == {"CUDA_VISIBLE_DEVICES": "0"}


def test_resolve_flag_off_returns_none_and_skips(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    monkeypatch.setenv("GSFLUENT_AUTO_GPU", "0")
    em = _RecordingEmitter()

    # query must NOT be consulted when the flag is off.
    def _boom() -> str:
        raise AssertionError("nvidia-smi should not be queried when disabled")

    overlay = _resolve_sim_gpu_env(on_event=em, query=_boom)
    assert overlay is None
    skipped = next(c for n, c in em.events if n == "sim.gpu_autopick_skipped")
    assert skipped["reason"] == "disabled"


def test_resolve_query_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    em = _RecordingEmitter()
    overlay = _resolve_sim_gpu_env(on_event=em, query=lambda: None)
    assert overlay is None
    skipped = next(c for n, c in em.events if n == "sim.gpu_autopick_skipped")
    assert skipped["reason"] == "query_failed"


def test_resolve_no_qualifying_gpu_falls_back(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    em = _RecordingEmitter()
    busy = "0, 100, 100\n1, 90, 200\n"
    overlay = _resolve_sim_gpu_env(on_event=em, query=lambda: busy)
    assert overlay is None
    skipped = next(c for n, c in em.events if n == "sim.gpu_autopick_skipped")
    assert skipped["reason"] == "no_gpu_qualified"


def test_resolve_query_exception_never_crashes(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    # A query that raises must be swallowed -> fall back, never propagate.
    em = _RecordingEmitter()

    def _raise() -> str:
        raise RuntimeError("nvidia-smi blew up")

    overlay = _resolve_sim_gpu_env(on_event=em, query=_raise)
    assert overlay is None
    skipped = next(c for n, c in em.events if n == "sim.gpu_autopick_skipped")
    assert skipped["reason"].startswith("error:")


def test_resolve_malformed_min_free_env_uses_default(
    monkeypatch: pytest.MonkeyPatch, _clear_gpu_env: None
) -> None:
    # A garbage GSFLUENT_GPU_MIN_FREE_MIB must not crash; default floor applies.
    monkeypatch.setenv("GSFLUENT_GPU_MIN_FREE_MIB", "not-a-number")
    em = _RecordingEmitter()
    overlay = _resolve_sim_gpu_env(on_event=em, query=lambda: _REAL_CSV)
    # Default 20 GiB floor -> GPU 6 still picked.
    assert overlay == {"CUDA_VISIBLE_DEVICES": "6"}


# ---------- spawn env wiring ----------------------------------------------


@pytest.mark.asyncio
async def test_spawn_passes_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify _spawn_in_new_pg forwards `env` (the CUDA_VISIBLE_DEVICES override
    # for an auto-picked GPU) down to the spawn call untouched, and that the
    # default (env=None) inherits the parent environment.
    import gsfluent.core.sim_engines.mpm as mpm

    captured: dict[str, object] = {}

    async def _fake_spawn(*argv: str, **kwargs: object) -> object:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(mpm, "_spawn", _fake_spawn)
    eng = _make_engine(sim_fast=False)

    overlay = {**os.environ, "CUDA_VISIBLE_DEVICES": "6"}
    await eng._spawn_in_new_pg(argv=["echo", "hi"], cwd="/tmp", env=overlay)
    assert captured["kwargs"]["env"] == overlay
    assert captured["kwargs"]["env"]["CUDA_VISIBLE_DEVICES"] == "6"
    assert captured["kwargs"]["start_new_session"] is True

    # Default: no env override -> env=None -> child inherits parent's env.
    captured.clear()
    await eng._spawn_in_new_pg(argv=["echo", "hi"], cwd="/tmp")
    assert captured["kwargs"]["env"] is None
