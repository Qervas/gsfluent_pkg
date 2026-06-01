"""SCENARIO library — building-agnostic timelines of destruction events.

A scenario is the WHAT-HAPPENS axis of MATERIAL x SCENARIO x BUILDING. It is a
dict with:

    frame_num, frame_dt, gravity   — clip timing + gravity (scenario-level)
    base                           — the BASE REGIME (see below)
    events                         — an ordered timeline of semantic events

It speaks in building-RELATIVE anchors (base/mid/top, +x/-x/+y/-y, fractions of
building size) and times in seconds. compose() resolves those against the
building bbox, so one scenario runs on any scanned building. The flat sim JSON
is a build artifact — never hand-authored.

BASE REGIME (the field `base`) — verified 2026-05-29 to be mutually exclusive,
because you cannot both pin a base rigid AND shake it:
    "pinned"  — bottom band rigidly fixed (enforce_translation v=0). The base
                is a non-deformable ANCHOR: pivot for topple, anvil for crush.
    "driven"  — bottom band is SHAKEN by a wide thin cuboid plate at ground
                level (the R10 earthquake mechanism). The base is the ACTUATOR.
    "free"    — no base constraint; gravity + events do everything.

ENERGY FAMILIES — every event belongs to exactly one; compose enforces each
family's verified safety law so a recipe can't be authored into a crash:
    imposed-velocity (cuboid / enforce_translation): |v| <= ~2 cube-units/s,
        or debris exceeds grid-escape velocity -> CUDA 700 (verified).
    real force (particle_impulse): force <= ~2, because the solver computes
        dv = force / particle_mass and mass ~ 1e-4, so force in the thousands
        means dv in the millions -> instant escape (verified: the # comment
        `particle_v += force/particle_mass * dt` in mpm_solver_warp.py L1199).
    gravity (release_particles_sequentially): no escape risk, but only
        dramatic if the material is near-collapse (stability S = yield/(rho*g*H)
        below ~1); otherwise the building self-supports and it looks tame.

EVENT KINDS (each maps to exactly one verified solver primitive):
    ground   -> add_surface_collider           (L918)   floor plane
    impact   -> set_velocity_on_cuboid         (L1022)  one moving box puppet
    shake    -> set_velocity_on_cuboid x N     (L1022)  alternating base plate
    drag     -> enforce_particle_velocity_..   (L1247)  rigidly haul a region
    release  -> release_particles_sequentially (L1401)  staged top-down drop
    burst    -> set_velocity_on_cuboid x 4     (L1022)  4 core slabs shoved OUT
(`blast` -> add_impulse_on_particles was REMOVED 2026-05-30: fragile real-force
 primitive, dv=force/mass with mass~1e-4 crashed 4/5 materials; superseded by
 `burst`, which gets the explosion read from robust velocity puppets.)
(The base regime injects its own ground/pin/shake BCs; events[] is the
scenario-specific action on top.)
"""
from __future__ import annotations

# Optional scenario fields beyond the core (frame_num/frame_dt/gravity/base/events):
#   recommended_material — the material these timings were verified against. The
#       composer still takes material as an argument (orthogonality preserved),
#       but a recipe composed with the wrong material may look off (verified:
#       these scenarios collapse on soft "watermelon" but eject on stiff
#       "plasticine" — the building was simply too strong to break).
#   damping — grid_v_damping_scale for this scenario (verified 2026-05-29 to be
#       SCENARIO-dependent, not a material constant). Resonant scenarios
#       (earthquake) need damping OFF (>=1.0) so energy accumulates to failure;
#       single-impact scenarios keep it ON for stability. When present it
#       overrides the material's default damping in compose().
#   substep_dt_max — optional scenario-level hard cap for recipes that are
#       CFL-safe but still unstable near the CFL edge. The curated violent
#       scenarios inherit the official R10 timestep cap because the composed
#       CFL margin alone produced CUDA-700 on verified models.
SCENARIOS: dict[str, dict] = {
    # ------------------------------------------------------------------ #
    # EARTHQUAKE — base shake -> the tower collapses into rubble in place.#
    # VERIFIED (video, 2026-05-29): with soft "watermelon" material a     #
    # footprint plate at ground level whipped +-0.5 over 3 s makes the    #
    # tall building lose structural integrity and pancake down. base=     #
    # driven (the shake plate IS the actuator; no pin — pinning fights    #
    # the shake). damping OFF (1.1) so the shake energy accumulates.      #
    # The R10 original (config/R10.EQ_earthquake_v3_G_R7base.json) is the #
    # reference; this reproduces it through the composer.                 #
    # ------------------------------------------------------------------ #
    "earthquake": {
        "frame_num": 150,            # 5.0 s @ 0.03 — full collapse window
        "frame_dt": 0.03,
        "gravity": -15.0,            # stronger g helps the soft body fall
        "base": "driven",
        "recommended_material": "watermelon",
        "damping": 0.95,            # damped (<1.0) prevents NaN runaway on real models; was 1.1 (OFF)
        "substep_dt_max": 0.0001,    # official R10 cap; avoids late grid escape
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            # Constant +-0.5 shake over the full footprint plate. Stays under
            # the |v|<=2 imposed-velocity ceiling; with soft material this is
            # enough to bring the structure down (stiff material would need
            # far more and would eject — see recommended_material note).
            {"kind": "shake", "axis": "x",
             "plate_z": "base", "plate_frac": 1.0, "plate_thick": 0.03,
             "n_halfcycles": 6, "speed_lo": 0.5, "speed_hi": 0.5,
             "window": [0.0, 3.0]},
        ],
        "_desc": "Seismic base shake -> full collapse into rubble. Soft material "
                 "+ ground-plate shaken +-0.5 over 3 s. Verified on video.",
    },

    # ------------------------------------------------------------------ #
    # WRECKING — a ball clips the building; with soft material it takes   #
    # a chunk out and the structure comes apart. imposed-velocity,       #
    # pinned base (the foundation holds while the upper mass fails).      #
    # VERIFIED on video 2026-05-29 with watermelon material.             #
    # ------------------------------------------------------------------ #
    "wrecking": {
        "frame_num": 100,            # 3.0 s @ 0.03
        "frame_dt": 0.03,
        "gravity": -15.0,
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 0.95,            # damped (<1.0) prevents NaN runaway on real models; was 1.1 (OFF)
        "substep_dt_max": 0.0001,
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            {"kind": "impact", "from": "+x", "height": "mid",
             "size": 0.30, "speed": 2.0, "at": 0.6, "duration": 0.6},
        ],
        "_desc": "Wrecking ball: pinned base, mid-height hit from +x at speed 2 "
                 "(grid-safe). Soft material -> the building comes apart at the "
                 "impact. Verified on video.",
    },

    # ------------------------------------------------------------------ #
    # BURST — internal explosion that throws the structure outward.       #
    # Replaces blast, whose particle_impulse primitive crashed 4/5        #
    # materials (frame 6, grid escape: dv=force/mass with mass~1e-4 is    #
    # intrinsically twitchy — verified 2026-05-29). burst gets the same   #
    # "explode from inside" read using the ROBUST cuboid velocity-puppet  #
    # family: four slabs at the core, each shoving its quadrant OUTWARD    #
    # (+x/-x/+y/-y). Grid-clamped, stays under the |v|<=2 ceiling.        #
    # base=pinned so the foundation holds while the mid-section bursts.    #
    # ------------------------------------------------------------------ #
    "burst": {
        "frame_num": 100,
        "frame_dt": 0.03,
        "gravity": -15.0,
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 0.95,            # damped (<1.0) prevents NaN runaway on real models; was 1.1 (OFF)
        "substep_dt_max": 0.0001,
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            # Four mid-height slabs blow the core apart at 1.8 over 0.3 s; sizes
            # are building-relative (frac of footprint) so they fit a slender
            # slab; the composer clamps duration so debris stays in-grid.
            {"kind": "burst", "height": "mid", "speed": 1.8,
             "size_frac": 0.4, "offset_frac": 0.5, "band_frac": 0.5,
             "at": 0.2, "duration": 0.3},
        ],
        "_desc": "Internal explosion: four core slabs shove the mid-section "
                 "outward in every direction; the structure bursts apart. "
                 "Replaces blast (fragile force primitive crashed 4/5).",
    },

    # ------------------------------------------------------------------ #
    # TOPPLE — fell the tower like a domino. RE-ENABLED 2026-05-29 after   #
    # the bbox fix: the building is a TALL slender slab (z-span 1.0, y-span #
    # 0.36), not the squat block the wrong bbox implied — so it CAN topple. #
    # base=pinned (the foot is the hinge); drag the top third in +y (the    #
    # THIN axis -> tips over like a wall/domino, the most dramatic fall).   #
    # robust `drag` primitive (edge-style, like wrecking); duration auto-   #
    # clamped so the advected box stays in-grid. damping OFF so momentum    #
    # carries it past the tipping point.                                    #
    # ------------------------------------------------------------------ #
    "topple": {
        "frame_num": 120,            # time to fall past the tipping point
        "frame_dt": 0.03,
        "gravity": -15.0,
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 0.95,            # damped (<1.0) prevents NaN runaway on real models; was 1.1 (OFF)
        "substep_dt_max": 0.0001,
        "events": [
            {"kind": "ground", "surface": "separate", "friction": 0.6,
             "height": "base"},
            {"kind": "drag", "toward": "+y", "height": "top",
             "band_frac": 0.33, "speed": 1.5, "at": 0.05, "duration": 0.4},
        ],
        "_desc": "Fell it like a domino: the top third is hauled +y (the thin "
                 "axis) while the foot stays pinned, so the slab hinges about "
                 "its base and topples over. Viable because the building is tall.",
    },

    # ------------------------------------------------------------------ #
    # DEMOLISH — controlled-demolition COLLAPSE. Cut the legs: two opposing #
    # impactors sweep through the LOWER section (+x and -x at base height)  #
    # so the support is blown out sideways and the whole tower crashes      #
    # straight down, breaking apart into a rubble field. This REPLACES the  #
    # impossible "crush" (verified 2026-05-30): a forced vertical pancake   #
    # cannot work here — gravity alone self-supports the tower (every       #
    # material, pinned or free) and a driven downward press traps the       #
    # near-incompressible body against the floor and ejects it (CUDA 700).  #
    # demolish gets the same building-falls-down-and-breaks result through  #
    # the ROBUST lateral-impact mechanism (material yields sideways into    #
    # open grid, exactly like wrecking). Verified dramatic on watermelon.   #
    # ------------------------------------------------------------------ #
    "demolish": {
        "frame_num": 120,            # time for the full collapse to settle
        "frame_dt": 0.03,
        "gravity": -20.0,            # strong g pulls the cut tower straight down
        "base": "pinned",            # the very foot anchors; the legs above fail
        "recommended_material": "watermelon",
        "damping": 0.95,            # damped (<1.0) prevents NaN runaway on real models; was 1.1 (OFF)
        "substep_dt_max": 0.0001,
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            # Two opposing impactors cut the lower section from +x and -x at the
            # grid-safe speed 2; the support fails and the tower drops + breaks.
            {"kind": "impact", "from": "+x", "height": "base",
             "size": 0.28, "speed": 2.0, "at": 0.3, "duration": 0.5},
            {"kind": "impact", "from": "-x", "height": "base",
             "size": 0.28, "speed": 2.0, "at": 0.3, "duration": 0.5},
        ],
        "_desc": "Controlled demolition: two impactors cut the lower section so "
                 "the tower crashes straight down and breaks into rubble. "
                 "Replaces crush (a forced vertical pancake is not achievable).",
    },
}


def get_scenario(name: str) -> dict:
    if name not in SCENARIOS:
        raise KeyError(
            f"unknown scenario {name!r}; have {sorted(SCENARIOS)}"
        )
    return SCENARIOS[name]
