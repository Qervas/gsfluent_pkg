"""Per-material validated defaults.

Pulled from the existing R7_diversity configs (already known-good for
the cluster_6_15 reference building). When the React Material panel
sees `material` change in the recipe, it snaps these values into all
related fields so the user doesn't pick up a stale parameter set.
"""
MATERIAL_DEFAULTS: dict[str, dict] = {
    "jelly":      {"E": 5000.0,  "nu": 0.38, "density": 1,   "yield_stress": 0.0,    "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "metal":      {"E": 50000.0, "nu": 0.30, "density": 3,   "yield_stress": 1000.0, "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "sand":       {"E": 20000.0, "nu": 0.30, "density": 2,   "yield_stress": 0.0,    "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "foam":       {"E": 1000.0,  "nu": 0.10, "density": 0.3, "yield_stress": 0.0,    "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
    "snow":       {"E": 8000.0,  "nu": 0.30, "density": 1,   "yield_stress": 0.0,    "friction_angle": 30.0, "beta": 1.0, "xi": 10.0, "hardening": 5.0, "alpha_0": -0.01, "plastic_viscosity": 0.0},
    "plasticine": {"E": 8000.0,  "nu": 0.30, "density": 2,   "yield_stress": 100.0,  "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 100.0},
    "watermelon": {"E": 50000.0, "nu": 0.30, "density": 1,   "yield_stress": 0.0,    "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0},
}
