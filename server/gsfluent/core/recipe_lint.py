"""Static recipe-stability linter (Phase 0 of the intelligent-recipe-
stabilization proposal — docs/proposals/intelligent-recipe-stabilization.md).

Recipes are flat JSON dicts of physics knobs. A few combos silently produce
numerically-unstable (NaN) MPM simulations — most notably:

  * `grid_v_damping_scale >= 1.0`, which is a *silent no-op*: the Warp solver
    only applies damping when the value is strictly below 1.0 (gs_simulation
    clamps/branches `if grid_v_damping_scale < 1.0`). A value of 1.1 reads like
    "more damping" but in fact disables it, so stiff/violent materials
    accumulate grid-velocity energy and diverge.

  * `substep_dt` above the CFL limit, which violates the explicit-MPM
    stability condition and blows up. The server normally clamps this, but the
    `--no_cfl_override` fast path disables that net.

This module is *pure*: a recipe dict goes in, a list of Findings comes out. No
I/O, no sim, no GPU. It is safe to call on save, on run-submit, and from a CLI
CI gate. Rules guard against missing/zero inputs and simply skip themselves
rather than crash or false-positive when their inputs are absent.

The CFL formula mirrors the solver exactly (verified against
gs_simulation_building.py on the server):

    dx          = grid_lim / n_grid
    sound_speed = sqrt(E * (1 - nu) / ((1 + nu) * (1 - 2*nu) * rho))
    cfl_dt      = 0.6 * dx / sound_speed
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# CFL safety coefficient — matches the solver's `cfl = 0.6` in
# gs_simulation_building.py. Keep in sync with the server if it ever changes.
CFL_COEFF = 0.6


@dataclass(frozen=True)
class Finding:
    """One lint result. `suggested_fix` is None when a rule has no concrete
    auto-derived value to offer (Phase 0 rules always offer one)."""

    rule_id: str          # stable id, e.g. "damping.disabled"
    severity: str         # "error" | "warn"
    param: str            # offending recipe key, e.g. "grid_v_damping_scale"
    message: str          # human, actionable
    suggested_fix: object | None = None  # value the autofixer would use

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "param": self.param,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
        }


def _num(recipe: dict, key: str):
    """Return recipe[key] as a float if it is a finite real number, else None.
    Strings, bools, missing keys, and non-finite values all yield None so a
    rule can cleanly skip itself instead of crashing or false-positiving."""
    if key not in recipe:
        return None
    val = recipe[key]
    # bool is an int subclass; a damping flag that is literally True/False is
    # not a meaningful scale, so treat it as absent.
    if isinstance(val, bool):
        return None
    if not isinstance(val, (int, float)):
        return None
    f = float(val)
    if not math.isfinite(f):
        return None
    return f


def _cfl_dt(recipe: dict) -> float | None:
    """Compute the solver's CFL dt bound, or None if any input is missing,
    non-numeric, zero, or otherwise leads to a degenerate computation.

    Mirrors gs_simulation_building.py:
        dx     = grid_lim / n_grid
        c      = sqrt(E*(1-nu) / ((1+nu)*(1-2*nu)*rho))
        cfl_dt = 0.6 * dx / c
    """
    grid_lim = _num(recipe, "grid_lim")
    n_grid = _num(recipe, "n_grid")
    E = _num(recipe, "E")
    nu = _num(recipe, "nu")
    rho = _num(recipe, "density")
    if None in (grid_lim, n_grid, E, nu, rho):
        return None
    if n_grid == 0:
        return None

    dx = grid_lim / n_grid

    # Sound-speed denominator: (1+nu)*(1-2nu)*rho. Guard the incompressible
    # singularity (nu -> 0.5 makes (1-2nu) -> 0) and any non-positive radicand
    # — these are out of the linear-elasticity regime and would crash or yield
    # an imaginary/zero sound speed. Skip rather than emit a bogus finding.
    denom = (1.0 + nu) * (1.0 - 2.0 * nu) * rho
    if denom <= 0.0:
        return None
    radicand = E * (1.0 - nu) / denom
    if radicand <= 0.0:
        return None
    sound_speed = math.sqrt(radicand)
    if sound_speed == 0.0:
        return None

    return CFL_COEFF * dx / sound_speed


# --------------------------------------------------------------------------
# Rules. Each is a pure (recipe) -> Finding | None. Add the result to the
# report only when non-None.
# --------------------------------------------------------------------------


def _rule_damping_disabled(recipe: dict) -> Finding | None:
    """R1 — `damping.disabled` (warn). `grid_v_damping_scale >= 1.0` disables
    velocity damping entirely (the solver only damps when the value is < 1.0).
    On stiff/violent materials this lets grid-velocity energy accumulate and
    diverge to NaN."""
    scale = _num(recipe, "grid_v_damping_scale")
    if scale is None:
        return None
    if scale < 1.0:
        return None
    return Finding(
        rule_id="damping.disabled",
        severity="warn",
        param="grid_v_damping_scale",
        message=(
            f"grid_v_damping_scale={scale:g} disables velocity damping: the "
            f"solver only damps when the value is < 1.0, so >= 1.0 is a no-op "
            f"(and the name misleadingly reads as 'more damping'). Stiff or "
            f"violent materials can then accumulate grid-velocity energy and "
            f"diverge to NaN. Use 0.95-0.99 (stronger damping = smaller value)."
        ),
        suggested_fix=0.95,
    )


def _rule_dt_above_cfl(recipe: dict) -> Finding | None:
    """R2 — `dt.above_cfl` (error). `substep_dt` above the CFL bound violates
    the explicit-MPM stability condition. The server clamps this, but the
    `--no_cfl_override` fast path does not — flagging it pre-run protects that
    hole and tells the user their effective dt (and run time) before they pay
    for a GPU run."""
    substep_dt = _num(recipe, "substep_dt")
    if substep_dt is None:
        return None
    cfl_dt = _cfl_dt(recipe)
    if cfl_dt is None:
        return None
    if substep_dt <= cfl_dt:
        return None
    return Finding(
        rule_id="dt.above_cfl",
        severity="error",
        param="substep_dt",
        message=(
            f"substep_dt={substep_dt:.3e} exceeds the CFL stability limit "
            f"cfl_dt={cfl_dt:.3e} (= {CFL_COEFF} * grid_lim/n_grid / "
            f"sound_speed). Above CFL the explicit MPM solver diverges. The "
            f"server normally clamps this, but the --no_cfl_override fast path "
            f"does not. Set substep_dt <= {cfl_dt:.3e}."
        ),
        suggested_fix=cfl_dt,
    )


_RULES = (
    _rule_damping_disabled,
    _rule_dt_above_cfl,
)


def lint_recipe(recipe: dict) -> list[Finding]:
    """Run every Phase 0 rule over `recipe` and return the findings.

    Pure: no I/O, no sim, no GPU. Never raises on a malformed recipe — a rule
    whose inputs are missing or non-numeric skips itself. Returns [] for a
    clean recipe (or a non-dict input)."""
    if not isinstance(recipe, dict):
        return []
    findings: list[Finding] = []
    for rule in _RULES:
        f = rule(recipe)
        if f is not None:
            findings.append(f)
    return findings


def has_errors(findings: list[Finding]) -> bool:
    """True if any finding is severity 'error' — the CI/run gate predicate."""
    return any(f.severity == "error" for f in findings)
