"""Tests for the Phase 0 static recipe-stability linter.

Covers each rule firing on bad values + passing on good, missing-key safety
(never crash, never false-positive on absent inputs), and a sweep over the 8
shipped built-in recipes asserting the expected findings.
"""
import json

import pytest

from gsfluent.core import recipe_lint
from gsfluent.core.recipe_lint import Finding, lint_recipe
from gsfluent.core.recipes import RECIPES_DIR


# A minimal recipe whose CFL inputs are well inside the stable band so the
# dt rule never fires unless we deliberately push substep_dt over the limit.
# With these values cfl_dt = 0.6 * (2/150) / sqrt(2000*0.62/(1.38*0.24*1))
# ~= 1.31e-4, comfortably above substep_dt=1e-5.
def _base_recipe(**overrides) -> dict:
    r = {
        "n_grid": 150,
        "grid_lim": 2,
        "substep_dt": 1e-5,
        "E": 2000.0,
        "nu": 0.38,
        "density": 1.0,
        "grid_v_damping_scale": 0.95,
    }
    r.update(overrides)
    return r


def _ids(findings: list[Finding]) -> set[str]:
    return {f.rule_id for f in findings}


# --------------------------------------------------------------------------
# R1 — damping.disabled (warn)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.0, 1.1, 2.0, 1.0001])
def test_damping_disabled_fires_at_or_above_one(scale):
    findings = lint_recipe(_base_recipe(grid_v_damping_scale=scale))
    f = next(f for f in findings if f.rule_id == "damping.disabled")
    assert f.severity == "warn"
    assert f.param == "grid_v_damping_scale"
    # Phase 0 suggests a value in the 0.95-0.99 stiff/violent band.
    assert 0.95 <= f.suggested_fix <= 0.99


@pytest.mark.parametrize("scale", [0.95, 0.99, 0.5, 0.999])
def test_damping_disabled_clean_below_one(scale):
    findings = lint_recipe(_base_recipe(grid_v_damping_scale=scale))
    assert "damping.disabled" not in _ids(findings)


def test_damping_disabled_skips_when_missing():
    r = _base_recipe()
    del r["grid_v_damping_scale"]
    assert "damping.disabled" not in _ids(lint_recipe(r))


# --------------------------------------------------------------------------
# R2 — dt.above_cfl (error)
# --------------------------------------------------------------------------


def test_dt_above_cfl_fires_when_over_limit():
    # Stiff material (E=50000) -> small cfl_dt (~5.9e-5); substep_dt=1e-4 is
    # above it, so the rule fires.
    r = _base_recipe(E=50000.0, nu=0.2, density=3.0, substep_dt=1e-4)
    f = next(f for f in lint_recipe(r) if f.rule_id == "dt.above_cfl")
    assert f.severity == "error"
    assert f.param == "substep_dt"
    # Suggested fix is the CFL bound and must be below the offending dt.
    assert f.suggested_fix < 1e-4
    assert f.suggested_fix == pytest.approx(
        recipe_lint._cfl_dt(r), rel=1e-9
    )


def test_dt_above_cfl_clean_when_under_limit():
    # Same stiff material but substep_dt safely below the ~5.9e-5 bound.
    r = _base_recipe(E=50000.0, nu=0.2, density=3.0, substep_dt=5e-5)
    assert "dt.above_cfl" not in _ids(lint_recipe(r))


def test_dt_above_cfl_matches_solver_formula():
    import math

    r = _base_recipe(E=50000.0, nu=0.2, density=3.0, substep_dt=1e-4)
    dx = r["grid_lim"] / r["n_grid"]
    c = math.sqrt(
        r["E"] * (1 - r["nu"])
        / ((1 + r["nu"]) * (1 - 2 * r["nu"]) * r["density"])
    )
    expected = 0.6 * dx / c
    assert recipe_lint._cfl_dt(r) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("missing", ["E", "nu", "density", "n_grid", "grid_lim", "substep_dt"])
def test_dt_above_cfl_skips_on_missing_input(missing):
    r = _base_recipe(E=50000.0, nu=0.2, density=3.0, substep_dt=1e-4)
    del r[missing]
    # No crash, and no dt finding because an input is absent.
    assert "dt.above_cfl" not in _ids(lint_recipe(r))


def test_dt_above_cfl_skips_on_zero_ngrid():
    r = _base_recipe(n_grid=0, substep_dt=1e-4)
    assert "dt.above_cfl" not in _ids(lint_recipe(r))


def test_dt_above_cfl_skips_at_incompressible_singularity():
    # nu -> 0.5 makes (1-2nu) -> 0; the rule must skip, not divide by zero.
    r = _base_recipe(nu=0.5, substep_dt=1e-4)
    findings = lint_recipe(r)  # must not raise
    assert "dt.above_cfl" not in _ids(findings)


# --------------------------------------------------------------------------
# General safety
# --------------------------------------------------------------------------


def test_empty_recipe_no_crash_no_findings():
    assert lint_recipe({}) == []


def test_non_dict_input_returns_empty():
    assert lint_recipe(None) == []          # type: ignore[arg-type]
    assert lint_recipe("nope") == []        # type: ignore[arg-type]
    assert lint_recipe([1, 2, 3]) == []     # type: ignore[arg-type]


def test_non_numeric_values_skip_rules():
    r = _base_recipe(
        grid_v_damping_scale="lots",
        substep_dt="fast",
        E=None,
    )
    assert lint_recipe(r) == []  # all rules skip, no crash


def test_bool_values_treated_as_absent():
    # bool is an int subclass; True/False is not a meaningful scale/dt.
    r = _base_recipe(grid_v_damping_scale=True, substep_dt=False)
    assert lint_recipe(r) == []


def test_clean_recipe_has_no_findings():
    assert lint_recipe(_base_recipe()) == []


def test_has_errors_predicate():
    assert recipe_lint.has_errors([]) is False
    warn_only = lint_recipe(_base_recipe(grid_v_damping_scale=1.1))
    assert _ids(warn_only) == {"damping.disabled"}
    assert recipe_lint.has_errors(warn_only) is False
    with_error = lint_recipe(
        _base_recipe(E=50000.0, nu=0.2, density=3.0, substep_dt=1e-4)
    )
    assert recipe_lint.has_errors(with_error) is True


def test_finding_as_dict_round_trips():
    f = lint_recipe(_base_recipe(grid_v_damping_scale=1.1))[0]
    d = f.as_dict()
    assert set(d) == {"rule_id", "severity", "param", "message", "suggested_fix"}
    assert d["rule_id"] == "damping.disabled"


# --------------------------------------------------------------------------
# Sweep over the 8 shipped built-in recipes
# --------------------------------------------------------------------------


def _load_builtin(name: str) -> dict:
    return json.loads((RECIPES_DIR / f"{name}.json").read_text())


# Expected findings per shipped recipe, derived from the actual JSON values:
#   damping.disabled fires on the 1.1 recipes (jelly, earthquake).
#   dt.above_cfl is now clean across the library — the four recipes that
#   shipped substep_dt=1e-4 above their CFL bound (jelly/metal/plasticine/
#   sand) were lowered to CFL-safe values, so only the two damping warnings
#   remain (both warn-severity, so the CI gate passes with zero errors).
_EXPECTED = {
    "demolition": set(),                          # 0.95 damping, dt safe
    "earthquake": {"damping.disabled"},           # 1.1 damping, dt safe
    "foam": set(),                                # 0.95 damping, soft, dt safe
    "jelly": {"damping.disabled"},                # 1.1 damping; dt now CFL-safe
    "metal": set(),                               # dt lowered to CFL-safe
    "plasticine": set(),                          # dt lowered to CFL-safe
    "sand": set(),                                # dt lowered to CFL-safe
    "wrecking": set(),                            # 0.95 damping, dt safe
}


@pytest.mark.parametrize("name,expected", sorted(_EXPECTED.items()))
def test_builtin_recipe_expected_findings(name, expected):
    findings = lint_recipe(_load_builtin(name))
    assert _ids(findings) == expected, (
        f"{name}: got {_ids(findings)}, expected {expected}"
    )


def test_builtin_library_is_present_and_eight_files():
    # Guards against the sweep silently passing if recipes move/disappear.
    files = sorted(p.stem for p in RECIPES_DIR.glob("*.json"))
    assert set(files) == set(_EXPECTED), (
        f"shipped recipes {files} != expected {sorted(_EXPECTED)}"
    )


def test_jelly_and_earthquake_flagged_damping_at_1_1():
    # The headline case the linter exists to catch.
    for name in ("jelly", "earthquake"):
        r = _load_builtin(name)
        assert r["grid_v_damping_scale"] == 1.1
        assert "damping.disabled" in _ids(lint_recipe(r))


def test_the_0_95_recipes_are_clean_on_damping():
    for name in ("demolition", "foam", "metal", "plasticine", "sand", "wrecking"):
        r = _load_builtin(name)
        assert r["grid_v_damping_scale"] == 0.95
        assert "damping.disabled" not in _ids(lint_recipe(r))
