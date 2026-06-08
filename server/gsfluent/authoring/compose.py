"""The composer: MATERIAL × SCENARIO × BUILDING -> flat sim recipe dict.

This is the only place that knows how to turn the three orthogonal inputs into
the flat 39-field JSON the sim eats. Responsibilities:

  1. Resolve building-relative anchors (base/mid/top, +x/-x) into cube-frame
     point/size using the building bbox.
  2. Translate each semantic event into its solver BC dict.
  3. Inject the auto base-pin (the fixed base) from the fix_base event.
  4. CFL-derive substep_dt from the material so a recipe can't ship a
     divergent time-step.
  5. Emit grid + camera + sim_area so the result is runnable AND
     verify-to-video-renderable.

The flat dict is a build artifact. Re-running compose() with the same inputs is
deterministic.
"""
from __future__ import annotations

import math

from gsfluent.authoring.buildings import get_building
from gsfluent.authoring.materials import get_material
from gsfluent.authoring.scenarios import get_scenario

# Grid is fixed across the library today (n_grid=150, grid_lim=2). If a future
# building needs a different grid, promote these to the building config.
_N_GRID = 150
_GRID_LIM = 2

# CFL safety margin: substep_dt = margin * cfl_bound. 0.9 keeps us comfortably
# under the divergence threshold (the lint gate uses the bound itself).
_CFL_MARGIN = 0.9

# IMPOSED-VELOCITY SAFETY CEILING (verified 2026-05-29 on the size-2 grid): a
# cuboid/drag speed above this launches debris past the grid edge -> CUDA 700
# (v=4 crashed; v=2 contained). All five live scenarios are imposed-velocity
# (shake/impact/drag/burst); the force-based `blast` (particle_impulse) was
# removed as a fragile footgun — see the event-kind dispatch note below.
_MAX_IMPOSED_SPEED = 2.0


class ComposeError(Exception):
    """Raised when the three inputs can't be assembled into a valid recipe."""


# ---- anchor resolution -----------------------------------------------------
#
# bbox = [xmin, xmax, ymin, ymax, zmin, zmax] in cube frame.


def _height_z(bbox: list[float], height: str) -> float:
    """Resolve a semantic height to a cube-frame z."""
    z0, z1 = bbox[4], bbox[5]
    h = z1 - z0
    if height == "base":
        return z0 + 0.05 * h
    if height == "mid":
        return 0.5 * (z0 + z1)
    if height == "top":
        return z1 - 0.05 * h
    raise ComposeError(f"unknown height anchor {height!r} (base|mid|top)")


def _side_point(
    bbox: list[float], side: str, z: float, half: float,
) -> tuple[list[float], list[float]]:
    """Resolve a 'from' side to an impactor START center + inward unit velocity.

    The impactor is a cube of half-extent `half`. Its box (center ± half) MUST
    stay inside the grid [0, grid_lim] on every axis, or the solver indexes a
    grid cell out of bounds -> CUDA illegal memory access (verified 2026-05-29:
    a 0.3-half box centered at x=1.75 spanned [1.45, 2.05], past the grid edge
    2.0, and crashed ~6 frames after the impactor fired).

    We place the impactor against the chosen grid edge: its outer wall sits a
    hair inside the edge, so the box hugs the building's +x/−x/… face and
    sweeps inward. The box deliberately OVERLAPS the building face (that's the
    contact) — the only hard constraint is grid containment, so we raise only
    when the impactor is too big to fit in the grid at all (2*half > grid_lim
    is physically nonsensical for an impactor on a unit-sized building)."""
    x0, x1, y0, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
    xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    edge = 0.02  # keep the box's outer wall this far inside the grid edge
    lo, hi = 0.0, _GRID_LIM

    if 2.0 * half >= (hi - lo):
        raise ComposeError(
            f"impactor (half={half}) too large for grid [{lo},{hi}]; "
            f"shrink size below {0.5 * (hi - lo)}"
        )

    # Center so the box's OUTER wall sits `edge` inside the grid edge.
    # +axis side: center = hi - edge - half ; −axis side: center = lo + edge + half
    cpos = hi - edge - half
    cneg = lo + edge + half
    if side == "+x":
        return [cpos, yc, z], [-1.0, 0.0, 0.0]
    if side == "-x":
        return [cneg, yc, z], [1.0, 0.0, 0.0]
    if side == "+y":
        return [xc, cpos, z], [0.0, -1.0, 0.0]
    if side == "-y":
        return [xc, cneg, z], [0.0, 1.0, 0.0]
    raise ComposeError(f"unknown side anchor {side!r} (+x|-x|+y|-y)")


def _base_pin_box(bbox: list[float], band: float) -> tuple[list[float], list[float]]:
    """The fixed-base box: bottom `band` fraction of the building height,
    spanning the full xy footprint. Returns (center point, half-extents size)
    for enforce_particle_velocity_translation."""
    x0, x1, y0, y1, z0, z1 = bbox
    h = z1 - z0
    ztop = z0 + band * h
    xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    # xy half-extents: full footprint plus a hair so edge particles are caught.
    sx = 0.5 * (x1 - x0) + 0.02
    sy = 0.5 * (y1 - y0) + 0.02
    sz = 0.5 * (ztop - z0)
    zc = 0.5 * (z0 + ztop)
    return [round(xc, 4), round(yc, 4), round(zc, 4)], [round(sx, 4), round(sy, 4), round(sz, 4)]


# ---- CFL ------------------------------------------------------------------


def _cfl_substep_dt(material: dict) -> float:
    """CFL-stable substep_dt for this material on the library grid, with a
    safety margin. cfl = 0.6 * dx / sound_speed."""
    E = float(material["E"])
    nu = float(material["nu"])
    rho = float(material["density"])
    dx = _GRID_LIM / _N_GRID
    denom = (1.0 + nu) * (1.0 - 2.0 * nu) * rho
    if denom <= 0:
        raise ComposeError(f"degenerate material elastic constants: nu={nu} rho={rho}")
    sound = math.sqrt(E * (1.0 - nu) / denom)
    cfl = 0.6 * dx / sound
    return cfl * _CFL_MARGIN


# ---- event -> BC translation ----------------------------------------------


def _event_to_bcs(ev: dict, bbox: list[float], scenario: dict) -> list[dict]:
    """Translate one semantic event into one or more solver BC dicts."""
    kind = ev.get("kind")

    if kind == "ground":
        z = _height_z(bbox, ev.get("height", "base"))
        return [{
            "type": "surface_collider",
            "point": [0, 0, round(z, 4)],
            "normal": [0.0, 0.0, 1.0],
            "surface": ev.get("surface", "slip"),
            "friction": ev.get("friction", 0.0),
            "start_time": 0,
            "end_time": 1000.0,
        }]

    if kind == "fix_base":
        point, size = _base_pin_box(bbox, ev.get("band", 0.05))
        return [{
            "type": "enforce_particle_translation",
            "point": point,
            "size": size,
            "velocity": [0.0, 0.0, 0.0],
            "start_time": 0.0,
            "end_time": 1000.0,
        }]

    if kind == "impact":
        z = _height_z(bbox, ev.get("height", "mid"))
        s = float(ev.get("size", 0.3))
        start, direction = _side_point(bbox, ev.get("from", "+x"), z, s)
        speed = float(ev["speed"])
        if speed > _MAX_IMPOSED_SPEED:
            raise ComposeError(
                f"impact speed {speed} exceeds the imposed-velocity ceiling "
                f"{_MAX_IMPOSED_SPEED} (debris would escape the grid). Lower it."
            )
        vel = [round(direction[i] * speed, 4) for i in range(3)]
        return [{
            "type": "cuboid",
            "point": [round(v, 4) for v in start],
            "size": [s, s, s],
            "velocity": vel,
            "start_time": ev.get("at", 0.0),
            "end_time": ev.get("at", 0.0) + ev.get("duration", 0.5),
            "reset": 0,
        }]

    if kind == "shake":
        # Expand into N alternating cuboid plates at the base (the R10
        # earthquake mechanism). One plate per half-cycle, speed ramping
        # speed_lo -> speed_hi, evenly splitting the time window. The plate
        # spans `plate_frac` of the footprint at the base, `plate_thick` tall.
        x0, x1, y0, y1, z0, z1 = bbox
        xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        pz = _height_z(bbox, ev.get("plate_z", "base"))
        frac = float(ev.get("plate_frac", 0.6))
        sx = frac * 0.5 * (x1 - x0)
        sy = frac * 0.5 * (y1 - y0)
        sthick = float(ev.get("plate_thick", 0.03))
        n = int(ev.get("n_halfcycles", 6))
        lo = float(ev.get("speed_lo", 0.6))
        hi = float(ev.get("speed_hi", 1.6))
        if hi > _MAX_IMPOSED_SPEED:
            raise ComposeError(
                f"shake speed_hi {hi} exceeds the imposed-velocity ceiling "
                f"{_MAX_IMPOSED_SPEED}."
            )
        w0, w1 = ev.get("window", [0.0, 3.0])
        step = (w1 - w0) / n
        axis_i = {"x": 0, "y": 1}.get(ev.get("axis", "x"))
        if axis_i is None:
            raise ComposeError(f"shake axis must be x|y; got {ev.get('axis')!r}")
        plates = []
        for k in range(n):
            speed = lo + (hi - lo) * (k / max(n - 1, 1))
            sign = 1.0 if k % 2 == 0 else -1.0
            vel = [0.0, 0.0, 0.0]
            vel[axis_i] = round(sign * speed, 4)
            plates.append({
                "type": "cuboid",
                "point": [round(xc, 4), round(yc, 4), round(pz, 4)],
                "size": [round(sx, 4), round(sy, 4), round(sthick, 4)],
                "velocity": vel,
                "start_time": round(w0 + k * step, 4),
                "end_time": round(w0 + (k + 1) * step, 4),
                "reset": 0,
            })
        return plates

    if kind == "drag":
        # Rigidly haul a box region at a constant velocity (topple/yank). Uses
        # enforce_particle_translation (the v!=0 form). The box ADVECTS at its
        # velocity, so its far wall = point+size+v*duration must stay in-grid.
        z = _height_z(bbox, ev.get("height", "top"))
        x0, x1, y0, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
        xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        # box: footprint-wide, `band_frac` of height tall, centered at z
        band = float(ev.get("band_frac", 0.33))
        sz = band * 0.5 * (bbox[5] - bbox[4])
        sx = 0.5 * (x1 - x0) + 0.02
        sy = 0.5 * (y1 - y0) + 0.02
        axis = ev.get("toward", "+x")
        speed = float(ev["speed"])
        if speed > _MAX_IMPOSED_SPEED:
            raise ComposeError(
                f"drag speed {speed} exceeds imposed-velocity ceiling "
                f"{_MAX_IMPOSED_SPEED}."
            )
        dir_map = {"+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1)}
        if axis not in dir_map:
            raise ComposeError(f"drag toward must be +x|-x|+y|-y; got {axis!r}")
        ai, sgn = dir_map[axis]
        vel = [0.0, 0.0, 0.0]
        vel[ai] = round(sgn * speed, 4)
        at = float(ev.get("at", 0.05))
        # Clamp duration so the advected far wall stays inside the grid.
        ctr = [xc, yc, z]
        half = [sx, sy, sz]
        if sgn > 0:
            max_dur = (_GRID_LIM - 0.02 - (ctr[ai] + half[ai])) / speed
        else:
            max_dur = ((ctr[ai] - half[ai]) - 0.02) / speed
        dur = min(float(ev.get("duration", 0.2)), max(max_dur, 0.0))
        if dur <= 0:
            raise ComposeError(
                f"drag region already at grid edge on {axis}; cannot advect"
            )
        return [{
            "type": "enforce_particle_translation",
            "point": [round(c, 4) for c in ctr],
            "size": [round(h, 4) for h in half],
            "velocity": vel,
            "start_time": round(at, 4),
            "end_time": round(at + dur, 4),
        }]

    # NOTE: the force-based `blast` event (particle_impulse) was REMOVED
    # 2026-05-30. It crashed 4/5 materials (dv=force/mass, mass~1e-4 is
    # intrinsically twitchy -> grid escape) and was superseded by `burst`, which
    # gets the explosion read from the robust cuboid velocity-puppet family.

    if kind == "release":
        # Staged top-down gravity collapse (release_particles_sequentially).
        z0, z1 = bbox[4], bbox[5]
        return [{
            "type": "release_particles_sequentially",
            "normal": [0, 0, 1],
            "start_position": round(ev.get("start_position", z1), 4),
            "end_position": round(ev.get("end_position", z0 + 0.08 * (z1 - z0)), 4),
            "num_layers": int(ev.get("num_layers", 80)),
            "start_time": float(ev.get("start_time", 0.2)),
            "end_time": float(ev.get("end_time", 1.0)),
        }]

    if kind == "burst":
        # Internal explosion via 4 cuboid velocity puppets (NOT the fragile
        # particle_impulse): four slabs offset around the core, each shoving
        # its half OUTWARD (+x/-x/+y/-y). Bursts the structure apart from
        # inside while staying stable + grid-contained. All extents are
        # BUILDING-RELATIVE (fractions of the footprint half-extent) so the
        # slabs sit inside the body regardless of its proportions — critical
        # for a slender slab where the thin axis is far smaller than the wide.
        x0, x1, y0, y1, z0, z1 = bbox
        xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        hx, hy = 0.5 * (x1 - x0), 0.5 * (y1 - y0)
        z = _height_z(bbox, ev.get("height", "mid"))
        speed = float(ev.get("speed", 1.8))
        if speed > _MAX_IMPOSED_SPEED:
            raise ComposeError(
                f"burst speed {speed} exceeds imposed-velocity ceiling "
                f"{_MAX_IMPOSED_SPEED}."
            )
        off_frac = float(ev.get("offset_frac", 0.45))   # slab center, frac of half
        size_frac = float(ev.get("size_frac", 0.4))     # slab half-extent, frac of half
        at = float(ev.get("at", 0.2))
        sz = 0.5 * (z1 - z0) * float(ev.get("band_frac", 0.5))  # vertical reach
        out = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            # per-axis half-extent this slab spans (thin in the building's
            # thin axis), and its outward offset from center
            sx_ = size_frac * hx if dx else size_frac * hx
            sy_ = size_frac * hy if dy else size_frac * hy
            cx = xc + dx * off_frac * hx
            cy = yc + dy * off_frac * hy
            # clamp duration so this slab's leading wall stays in-grid
            if dx > 0:
                room = (_GRID_LIM - 0.02) - (cx + sx_)
            elif dx < 0:
                room = (cx - sx_) - 0.02
            elif dy > 0:
                room = (_GRID_LIM - 0.02) - (cy + sy_)
            else:
                room = (cy - sy_) - 0.02
            dur = min(float(ev.get("duration", 0.3)), max(room / speed if speed > 0 else 0.0, 0.0))
            if dur <= 0:
                raise ComposeError("burst slab already at grid edge")
            vel = [round(dx * speed, 4), round(dy * speed, 4), 0.0]
            out.append({
                "type": "cuboid",
                "point": [round(cx, 4), round(cy, 4), round(z, 4)],
                "size": [round(sx_, 4), round(sy_, 4), round(sz, 4)],
                "velocity": vel,
                "start_time": round(at, 4),
                "end_time": round(at + dur, 4),
                "reset": 0,
            })
        return out

    raise ComposeError(f"unknown event kind {kind!r}")


# ---- public ---------------------------------------------------------------


def compose(material_name: str, scenario_name: str, building_name: str) -> dict:
    """Assemble the flat sim recipe from the three orthogonal inputs."""
    material = get_material(material_name)
    scenario = get_scenario(scenario_name)
    building = get_building(building_name)
    bbox = building["bbox"]

    # Build the BC list: bounding box, then the base regime, then the events.
    bcs: list[dict] = [{"type": "bounding_box"}]

    # BASE REGIME — mutually exclusive (verified: can't pin AND shake a base).
    #   pinned -> inject a rigid v=0 foot (the anchor for topple/crush/shear)
    #   driven -> inject nothing here; the scenario's `shake` event drives it
    #   free   -> inject nothing
    base = scenario.get("base", "free")
    if base == "pinned":
        point, size = _base_pin_box(bbox, scenario.get("base_band", 0.05))
        bcs.append({
            "type": "enforce_particle_translation",
            "point": point, "size": size,
            "velocity": [0.0, 0.0, 0.0],
            "start_time": 0.0, "end_time": 1000.0,
        })
    elif base not in ("driven", "free"):
        raise ComposeError(
            f"unknown base regime {base!r} (pinned|driven|free)"
        )

    stability_notes: list[str] = []
    for ev in scenario["events"]:
        if (
            scenario_name == "burst"
            and material_name != scenario.get("recommended_material")
            and ev.get("kind") == "burst"
        ):
            stability_notes.append(
                "burst event disabled for non-recommended material; internal "
                "burst cuboids grid-escape on this material family"
            )
            continue
        bcs.extend(_event_to_bcs(ev, bbox, scenario))

    substep_dt = _cfl_substep_dt(material)
    if "substep_dt_max" in scenario:
        substep_dt = min(substep_dt, float(scenario["substep_dt_max"]))

    recipe: dict = {
        # --- provenance: how this artifact was generated ---
        "_composed_from": {
            "material": material_name,
            "scenario": scenario_name,
            "building": building_name,
            "base_regime": scenario.get("base", "free"),
        },
        # --- spatial (building) ---
        "sim_area": building["sim_area"],
        "sim_area_frame": building["sim_area_frame"],
        # Out-of-bounds particle handling (solver boundary clamp). "drop"
        # deactivates escapers (debris flies out freely); "clamp" pins them at
        # the wall (debris piles). Both keep the grid finite (no escape NaN).
        "boundary_mode": "drop",
        # --- grid + time ---
        "n_grid": _N_GRID,
        "grid_lim": _GRID_LIM,
        "frame_num": scenario["frame_num"],
        "frame_dt": scenario["frame_dt"],
        "substep_dt": substep_dt,
        # --- forces ---
        "g": [0.0, 0.0, float(scenario["gravity"])],
        # --- material ---
        **material,
        # --- boundary conditions (the scenario timeline) ---
        "boundary_conditions": bcs,
        # --- particle filling (reuse the library default; bbox-derived) ---
        "particle_filling": {
            "n_grid": 200,
            "max_particles_num": 500000,
            "density_threshold": 3.0,
            "search_threshold": 1.0,
            "max_partciels_per_cell": 1,
            "search_exclude_direction": 5,
            "ray_cast_direction": 4,
            "boundary": list(bbox),
            "smooth": True,
            "visualize": False,
        },
    }
    # Scenario-level damping override. Damping is SCENARIO-dependent, but the
    # undamped values were only verified on each scenario's recommended
    # material. Non-recommended materials keep their damped material default so
    # the full material x scenario grid favors numerical stability over drama.
    if "damping" in scenario and material_name == scenario.get("recommended_material"):
        recipe["grid_v_damping_scale"] = float(scenario["damping"])
    if stability_notes:
        recipe["_stability_notes"] = stability_notes

    # Camera block (native-render verify-to-video). Spread last so it can't
    # collide with physics keys.
    recipe.update(building["camera"])
    return recipe
