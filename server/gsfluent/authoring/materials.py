"""MATERIAL library — building-agnostic physics.

A material is everything about how the stuff *resists* deformation: the
constitutive model + its elastic/plastic params + the solver-integration knobs
that depend on stiffness. It says nothing about a specific building or a
specific force event — pick one material, reuse it across every scenario and
every scanned building.

Values seeded from the prior `schemas/material_defaults.py` (R7_diversity
baselines, known-good on cluster_6_15). `substep_dt` is NOT stored here — it's
CFL-derived per-composition from (E, nu, density, grid), so a material can't
ship a time-step that diverges. See compose._cfl_substep_dt.
"""
from __future__ import annotations

# Each entry: the solver `material` discriminator + its physics params.
# grid_v_damping_scale < 1.0 = damping ON (the solver only damps below 1.0);
# we keep all materials damped by default so violent scenarios stay stable.
MATERIALS: dict[str, dict] = {
    # The R10 demos that "feel great" (real building collapse, not eject/bend)
    # used THIS material: soft (E=2000, 25x softer than plasticine), light
    # (rho=1), no yield_stress. A soft low-stiffness body can't hold its own
    # weight once disturbed, so it PANCAKES DOWNWARD into rubble in place
    # rather than resisting + being carried out of the grid. Verified against
    # R10.EQ_earthquake_v3_G_R7base (the good collapse video). THE key finding:
    # weak material is what makes buildings break; we were tuning forces to
    # break a body that was simply too strong.
    "watermelon": {
        "material": "watermelon",
        "E": 2000.0, "nu": 0.38, "density": 1.0,
        "yield_stress": 0.0, "softening": 0.0, "plastic_viscosity": 0.0,
        "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Soft hyperelastic (R7/R10 collapse material). E=2000, no "
                 "yield, light. Collapses under its own weight when disturbed "
                 "— the 'building actually breaks' material.",
    },
    "jelly": {
        "material": "jelly",
        "E": 5000.0, "nu": 0.38, "density": 1.0,
        "yield_stress": 0.0, "softening": 0.0, "plastic_viscosity": 0.0,
        "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Soft elastic — wobbles, bounces, returns to shape. No yield.",
    },
    "metal": {
        "material": "metal",
        "E": 50000.0, "nu": 0.30, "density": 3.0,
        "yield_stress": 1000.0, "softening": 20.0, "plastic_viscosity": 0.0,
        "friction_angle": 0.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Stiff — holds shape under gravity, dents under load.",
    },
    "sand": {
        "material": "sand",
        "E": 20000.0, "nu": 0.30, "density": 2.0,
        "yield_stress": 0.0, "softening": 0.0, "plastic_viscosity": 0.0,
        "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Granular — Drucker-Prager, no cohesion, slumps into a pile.",
    },
    "foam": {
        "material": "foam",
        "E": 1000.0, "nu": 0.10, "density": 0.3,
        "yield_stress": 0.0, "softening": 0.0, "plastic_viscosity": 0.0,
        "friction_angle": 0.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Light squishy — low density + low E, slow recovery.",
    },
    "plasticine": {
        "material": "plasticine",
        "E": 50000.0, "nu": 0.2, "density": 3.0,
        "yield_stress": 500.0, "softening": 20.0, "plastic_viscosity": 0.0,
        "friction_angle": 0.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.95,
        "_desc": "Plastic clay — yields and flows, deformation is permanent.",
    },
    # A deliberately weak plasticine: same model, low yield → fragments under
    # impact instead of plastic-oozing. The 'soft' counterpart that makes
    # impact/blast scenarios visually dramatic without changing the scenario.
    "plasticine_weak": {
        "material": "plasticine",
        "E": 50000.0, "nu": 0.2, "density": 3.0,
        "yield_stress": 50.0, "softening": 20.0, "plastic_viscosity": 0.0,
        "friction_angle": 0.0, "beta": 1.0, "xi": 3.0,
        "hardening": 1.0, "alpha_0": -0.04,
        "flip_pic_ratio": 0.7, "rpic_damping": 0.0,
        "grid_v_damping_scale": 0.97,
        "_desc": "Brittle clay — low yield (50), fractures under impact.",
    },
}


def get_material(name: str) -> dict:
    if name not in MATERIALS:
        raise KeyError(
            f"unknown material {name!r}; have {sorted(MATERIALS)}"
        )
    # Return a copy minus the human description (not a sim field).
    m = {k: v for k, v in MATERIALS[name].items() if not k.startswith("_")}
    return m
