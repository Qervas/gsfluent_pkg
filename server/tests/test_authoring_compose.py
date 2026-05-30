"""Tests for the recipe authoring composer (MATERIAL × SCENARIO × BUILDING).

Pins the contract that makes the three inputs orthogonal:
  - the composed recipe is a valid, lint-clean flat sim recipe
  - the fixed-base pin is always injected with velocity zero
  - building-relative anchors resolve into the building's cube-frame bbox
  - swapping one axis (material) changes only that axis's fields
  - substep_dt is CFL-derived (never above the bound)
"""
from __future__ import annotations

import math

import pytest

from gsfluent.authoring import compose, ComposeError
from gsfluent.authoring.buildings import get_building
from gsfluent.core import recipe_lint


def _compose():
    return compose("plasticine_weak", "wrecking", "cluster_6_15")


def test_compose_returns_lint_clean_recipe():
    r = _compose()
    findings = recipe_lint.lint_recipe(r)
    assert not recipe_lint.has_errors(findings), (
        f"composed recipe must be lint-error-free; got {findings}"
    )


def test_compose_records_provenance():
    r = _compose()
    assert r["_composed_from"] == {
        "material": "plasticine_weak",
        "scenario": "wrecking",
        "building": "cluster_6_15",
        "base_regime": "pinned",
    }


def test_substep_dt_is_cfl_safe():
    r = _compose()
    bound = recipe_lint._cfl_dt(r)
    assert r["substep_dt"] < bound, (
        f"substep_dt {r['substep_dt']} must be below CFL bound {bound}"
    )


def test_fixed_base_pin_always_present_and_zero_velocity():
    r = _compose()
    pins = [
        bc for bc in r["boundary_conditions"]
        if bc["type"] == "enforce_particle_translation"
    ]
    assert len(pins) == 1, "exactly one base pin expected"
    assert pins[0]["velocity"] == [0.0, 0.0, 0.0], "base must be pinned (v=0)"


def test_base_pin_sits_at_bottom_of_bbox():
    r = _compose()
    bbox = get_building("cluster_6_15")["bbox"]
    z0, z1 = bbox[4], bbox[5]
    band = 0.05
    pin = next(
        bc for bc in r["boundary_conditions"]
        if bc["type"] == "enforce_particle_translation"
    )
    # pin box spans z0 .. z0 + band*h
    zc, sz = pin["point"][2], pin["size"][2]
    pin_bottom = zc - sz
    pin_top = zc + sz
    assert pin_bottom == pytest.approx(z0, abs=1e-3)
    assert pin_top == pytest.approx(z0 + band * (z1 - z0), abs=1e-3)


def test_impact_comes_from_plus_x_moving_inward():
    r = _compose()
    bbox = get_building("cluster_6_15")["bbox"]
    x1 = bbox[1]
    cuboid = next(bc for bc in r["boundary_conditions"] if bc["type"] == "cuboid")
    # center is on the +x side of the building center, sweeping in
    assert cuboid["point"][0] > 0.5 * (bbox[0] + bbox[1])
    # moves inward (negative x)
    assert cuboid["velocity"][0] < 0
    assert cuboid["velocity"][1] == 0 and cuboid["velocity"][2] == 0


def test_impactor_box_stays_inside_grid():
    # The crash that motivated this test: a cuboid box (center ± half) that
    # pokes past the grid edge -> CUDA illegal memory access. Every axis of the
    # impactor box must lie within [0, grid_lim].
    r = _compose()
    grid_lim = r["grid_lim"]
    cuboid = next(bc for bc in r["boundary_conditions"] if bc["type"] == "cuboid")
    for axis in range(3):
        lo = cuboid["point"][axis] - cuboid["size"][axis]
        hi = cuboid["point"][axis] + cuboid["size"][axis]
        assert lo >= 0.0, f"impactor box axis {axis} low {lo} < 0"
        assert hi <= grid_lim, f"impactor box axis {axis} high {hi} > {grid_lim}"


def test_oversized_impactor_raises_not_clips():
    # An impactor too big to fit between the building face and the grid edge
    # must raise (loud) rather than silently overlap the far wall.
    from gsfluent.authoring.scenarios import SCENARIOS
    import copy
    huge = copy.deepcopy(SCENARIOS["wrecking"])
    for ev in huge["events"]:
        if ev["kind"] == "impact":
            ev["size"] = 1.1  # 2*1.1 > grid_lim 2 -> can't fit in the grid
    SCENARIOS["_huge_test"] = huge
    try:
        with pytest.raises(ComposeError):
            compose("metal", "_huge_test", "cluster_6_15")
    finally:
        del SCENARIOS["_huge_test"]


def test_impact_height_mid_is_bbox_center_z():
    r = _compose()
    bbox = get_building("cluster_6_15")["bbox"]
    mid_z = 0.5 * (bbox[4] + bbox[5])
    cuboid = next(bc for bc in r["boundary_conditions"] if bc["type"] == "cuboid")
    assert cuboid["point"][2] == pytest.approx(mid_z, abs=1e-3)


def test_orthogonality_material_swap_changes_only_material_fields():
    weak = compose("plasticine_weak", "wrecking", "cluster_6_15")
    metal = compose("metal", "wrecking", "cluster_6_15")
    # material fields differ
    assert weak["yield_stress"] != metal["yield_stress"]
    # scenario fields identical (same BC structure, same timeline)
    assert weak["frame_num"] == metal["frame_num"]
    assert weak["g"] == metal["g"]
    assert len(weak["boundary_conditions"]) == len(metal["boundary_conditions"])
    # building fields identical
    assert weak["sim_area"] == metal["sim_area"]
    assert weak["particle_filling"]["boundary"] == metal["particle_filling"]["boundary"]


def test_orthogonality_substep_dt_tracks_material_stiffness():
    # stiffer material (higher sound speed) -> smaller CFL substep_dt
    soft = compose("foam", "wrecking", "cluster_6_15")
    stiff = compose("metal", "wrecking", "cluster_6_15")
    assert stiff["substep_dt"] < soft["substep_dt"]


def test_unknown_names_raise():
    with pytest.raises(KeyError):
        compose("nope", "wrecking", "cluster_6_15")
    with pytest.raises(KeyError):
        compose("metal", "nope", "cluster_6_15")
    with pytest.raises(KeyError):
        compose("metal", "wrecking", "nope")


def test_bounding_box_first_bc():
    r = _compose()
    assert r["boundary_conditions"][0] == {"type": "bounding_box"}


# ---------------------------------------------------------------------------
# New structure: base regime + shake expansion + energy-family guards
# ---------------------------------------------------------------------------


def test_earthquake_is_driven_base_no_pin():
    """Driven base = shaken plate, NOT a rigid pin. There must be zero v=0
    enforce_particle_translation pins (pinning fights the shake)."""
    r = compose("plasticine", "earthquake", "cluster_6_15")
    assert r["_composed_from"]["base_regime"] == "driven"
    pins = [
        b for b in r["boundary_conditions"]
        if b["type"] == "enforce_particle_translation"
        and b.get("velocity") == [0.0, 0.0, 0.0]
    ]
    assert pins == [], "driven earthquake must not inject a rigid base pin"


def test_earthquake_shake_expands_to_alternating_plates():
    """The single `shake` event expands into N alternating cuboid plates at the
    base — the verified earthquake mechanism (constant ±0.5, back-to-back)."""
    r = compose("watermelon", "earthquake", "cluster_6_15")
    cuboids = [b for b in r["boundary_conditions"] if b["type"] == "cuboid"]
    assert len(cuboids) == 6, "6 half-cycles expected"
    vx = [c["velocity"][0] for c in cuboids]
    # alternating sign
    for a, b in zip(vx, vx[1:]):
        assert a * b < 0, f"plate velocities must alternate sign; got {vx}"
    # all under the imposed-velocity ceiling
    assert all(abs(v) <= 2.0 + 1e-9 for v in vx)
    # back-to-back time windows covering the scenario window
    for c1, c2 in zip(cuboids, cuboids[1:]):
        assert c1["end_time"] == pytest.approx(c2["start_time"], abs=1e-3)


def test_scenario_damping_overrides_material():
    """A scenario's `damping` field wins over the material default — damping is
    scenario-dependent (resonant earthquake needs it OFF)."""
    r = compose("watermelon", "earthquake", "cluster_6_15")
    # earthquake declares damping 1.1 (OFF); watermelon material default is 0.95
    assert r["grid_v_damping_scale"] == 1.1


def test_earthquake_and_wrecking_recommend_watermelon():
    """Both verified scenarios recommend the soft material that makes buildings
    actually collapse (vs eject with stiff material)."""
    from gsfluent.authoring.scenarios import get_scenario
    for name in ("earthquake", "wrecking"):
        assert get_scenario(name)["recommended_material"] == "watermelon"


# ---------------------------------------------------------------------------
# demolish — controlled-demolition base-cut (two opposing impactors + pin).
# Replaced the impossible vertical "crush" (verified 2026-05-30). This pins the
# semantic contract so a regression in the impact expansion is caught by name.
# ---------------------------------------------------------------------------


def test_demolish_has_two_opposing_low_impactors_over_pinned_base():
    r = compose("watermelon", "demolish", "cluster_6_15")
    bbox = get_building("cluster_6_15")["bbox"]
    cx = 0.5 * (bbox[0] + bbox[1])

    # exactly one rigid base pin (the foot stays while the legs are cut)
    pins = [
        b for b in r["boundary_conditions"]
        if b["type"] == "enforce_particle_translation"
        and b.get("velocity") == [0.0, 0.0, 0.0]
    ]
    assert len(pins) == 1, "demolish pins the foot (base='pinned')"

    # exactly two impactor cuboids
    cuboids = [b for b in r["boundary_conditions"] if b["type"] == "cuboid"]
    assert len(cuboids) == 2, "demolish cuts the legs with two impactors"

    # they OPPOSE on x: one moves -x, the other +x, and their x-velocities sum
    # to ~0 (mirrored speed)
    vx = sorted(c["velocity"][0] for c in cuboids)
    assert vx[0] < 0 < vx[1], f"impactors must oppose on x; got {vx}"
    assert vx[0] == pytest.approx(-vx[1], abs=1e-6), "opposing speeds mirror"

    # the +x-side impactor (point.x > center) sweeps inward (-x) and vice-versa
    for c in cuboids:
        if c["point"][0] > cx:
            assert c["velocity"][0] < 0, "the +x-side impactor sweeps inward (-x)"
        else:
            assert c["velocity"][0] > 0, "the -x-side impactor sweeps inward (+x)"

    # both fire at the LOWER section (cut the legs), well below mid-height
    mid_z = 0.5 * (bbox[4] + bbox[5])
    for c in cuboids:
        assert c["point"][2] < mid_z, "demolish impactors hit the lower section"


def test_demolish_recommends_watermelon():
    from gsfluent.authoring.scenarios import get_scenario
    assert get_scenario("demolish")["recommended_material"] == "watermelon"


def test_all_five_scenarios_compose_clean_across_materials():
    """The curated set is exactly five; every one composes on every shipped
    material without raising (the render gate further filters stiff-material
    ejects, but compose() itself must never crash)."""
    from gsfluent.authoring.scenarios import SCENARIOS
    from gsfluent.authoring.materials import MATERIALS
    assert set(SCENARIOS) == {
        "earthquake", "wrecking", "topple", "burst", "demolish"
    }
    for s in SCENARIOS:
        for m in MATERIALS:
            r = compose(m, s, "cluster_6_15")  # must not raise
            assert r["boundary_conditions"][0] == {"type": "bounding_box"}


def test_shake_plates_stay_under_imposed_speed_ceiling():
    r = compose("plasticine", "earthquake", "cluster_6_15")
    for c in r["boundary_conditions"]:
        if c["type"] == "cuboid":
            assert abs(c["velocity"][0]) <= 2.0 + 1e-9


def test_impact_speed_over_ceiling_raises():
    from gsfluent.authoring.scenarios import SCENARIOS
    import copy
    hot = copy.deepcopy(SCENARIOS["wrecking"])
    for ev in hot["events"]:
        if ev["kind"] == "impact":
            ev["speed"] = 5.0  # over the 2.0 imposed-velocity ceiling
    SCENARIOS["_hot_test"] = hot
    try:
        with pytest.raises(ComposeError):
            compose("plasticine", "_hot_test", "cluster_6_15")
    finally:
        del SCENARIOS["_hot_test"]


def test_removed_blast_event_kind_is_rejected():
    """The fragile force-based `blast` event was removed 2026-05-30 (superseded
    by `burst`). A scenario still asking for kind 'blast' must be rejected as an
    unknown event kind, not silently produce a particle_impulse BC."""
    from gsfluent.authoring.scenarios import SCENARIOS
    blast = {
        "frame_num": 100, "frame_dt": 0.03, "gravity": -15.0, "base": "free",
        "events": [{"kind": "blast", "height": "mid", "force": 1.5}],
    }
    SCENARIOS["_blast_test"] = blast
    try:
        with pytest.raises(ComposeError, match="unknown event kind"):
            compose("plasticine", "_blast_test", "cluster_6_15")
    finally:
        del SCENARIOS["_blast_test"]


def test_unknown_base_regime_raises():
    from gsfluent.authoring.scenarios import SCENARIOS
    bad = {
        "frame_num": 50, "frame_dt": 0.03, "gravity": -15.0,
        "base": "levitate", "events": [],
    }
    SCENARIOS["_bad_base"] = bad
    try:
        with pytest.raises(ComposeError):
            compose("plasticine", "_bad_base", "cluster_6_15")
    finally:
        del SCENARIOS["_bad_base"]
