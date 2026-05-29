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
    blast    -> add_impulse_on_particles       (L1198)  a real FORCE on a box
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
        "damping": 1.1,              # OFF — resonance must accumulate
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
        "damping": 1.1,              # OFF — let the struck region keep moving
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
    # BLAST — a real FORCE detonation in the core (particle_impulse).     #
    # Unlike the velocity puppets this injects force and lets the         #
    # material respond. force is single-digit (dv = force/mass, mass~1e-4;#
    # mag 1.5 buckled a stiff body, escaped at >=3). base=pinned so the   #
    # foundation holds while the upper structure folds. damping OFF.      #
    # NOTE: not yet video-verified through the composer — render gates it.#
    # ------------------------------------------------------------------ #
    "blast": {
        "frame_num": 100,
        "frame_dt": 0.03,
        "gravity": -15.0,
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 1.1,
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            {"kind": "blast", "height": "mid", "force": 1.5,
             "direction": [1.0, 0.0, -0.5], "size": 0.25,
             "num_dt": 6, "at": 0.2},
        ],
        "_desc": "Core force-detonation: a real impulse drives the mid-section "
                 "out + down onto the pinned base; the structure folds.",
    },

    # ------------------------------------------------------------------ #
    # TOPPLE — fell the building like a tree. base=pinned (the foot is    #
    # the PIVOT); drag the top third sideways so the column hinges about  #
    # the anchored base and lays down. The composer clamps the drag       #
    # duration so the advected box stays in-grid. damping OFF so the      #
    # toppling momentum carries. Soft material hinges; stiff would shear. #
    # ------------------------------------------------------------------ #
    "topple": {
        "frame_num": 120,            # needs time to fall past the tipping point
        "frame_dt": 0.03,
        "gravity": -15.0,
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 1.1,
        "events": [
            {"kind": "ground", "surface": "separate", "friction": 0.6,
             "height": "base"},
            {"kind": "drag", "toward": "+x", "height": "top",
             "band_frac": 0.33, "speed": 1.5, "at": 0.05, "duration": 0.3},
        ],
        "_desc": "Fell it like a tree: the top third is hauled +x while the foot "
                 "stays pinned, so the column hinges about its base and lays down.",
    },

    # ------------------------------------------------------------------ #
    # CRUSH — top-down pancake. base=pinned (the anvil); release the body #
    # layer-by-layer from the top so each freed layer drops onto the      #
    # ones below and the building eats itself down to the foundation.     #
    # gravity-driven (no escape risk); needs a soft/low-yield material to #
    # actually collapse rather than self-support. Stronger gravity helps. #
    # ------------------------------------------------------------------ #
    "crush": {
        "frame_num": 100,
        "frame_dt": 0.03,
        "gravity": -25.0,            # heavy g so the pancake is decisive
        "base": "pinned",
        "recommended_material": "watermelon",
        "damping": 0.98,             # gravity collapse — light damping is fine
        "events": [
            {"kind": "ground", "surface": "slip", "friction": 0.0,
             "height": "base"},
            {"kind": "release", "start_position": 1.5, "end_position": 0.65,
             "num_layers": 80, "start_time": 0.2, "end_time": 1.0},
        ],
        "_desc": "Top-down pancake: floors release layer-by-layer and stack onto "
                 "the pinned foundation; the building crushes itself flat.",
    },
}


def get_scenario(name: str) -> dict:
    if name not in SCENARIOS:
        raise KeyError(
            f"unknown scenario {name!r}; have {sorted(SCENARIOS)}"
        )
    return SCENARIOS[name]
