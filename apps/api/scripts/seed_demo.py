"""Seed a runnable demo: proven R7-jelly recipe for the cluster_6_15 model.

Idempotent: if a recipe named 'demo-jelly' exists, leave it; otherwise
insert. Frames trimmed from the v1 reference (150 → 30) so the demo
finishes in ~3 seconds of wall time at ~11 it/s.

The recipe is the same shape v1 tested against cluster_6_15. To re-use
this seed with a different model, the sim_area + boundary_conditions
need to match that model's world coords — see runner.py's
_translate_sim_area_if_local for the model→world translation v1 does
automatically when sim_area_frame='model'. Our v2 spawn path passes
the recipe straight through; until we port that translation, the seed
assumes the model is in cluster_6_15's coordinate system.

Usage:
  python apps/api/scripts/seed_demo.py http://<host>:18000
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:18000"

# Copied verbatim from the v1 stack's
# work/library/sequences/cluster_6_15_jelly_2026-05-18T0417/recipe.json
# with frame_num shrunk from 150 → 30 for demo speed.
RECIPE = {
    "name": "demo-jelly",
    "content": {
        "sim_area": [3440, 3480, 29030, 29060, -25, 35],
        "mpm_space_vertical_upward_axis": [0, 0, 1],
        "mpm_space_viewpoint_center": [1, 1, 1],
        "default_camera_index": -1,
        "show_hint": False,
        "init_azimuthm": 140,
        "init_elevation": 18,
        "init_radius": 130,
        "move_camera": True,
        "delta_a": 1,
        "delta_e": -0.03,
        "delta_r": -0.35,
        "opacity_threshold": 0,
        "rotation_degree": [0],
        "rotation_axis": [0],
        "n_grid": 150,
        "grid_lim": 2,
        "frame_num": 30,
        "substep_dt": 1e-4,
        "frame_dt": 0.03,
        "material": "jelly",
        "E": 5000,
        "nu": 0.38,
        "density": 1,
        "g": [0, 0, -15],
        "friction_angle": 45,
        "beta": 1,
        "xi": 3,
        "hardening": 1,
        "alpha_0": -0.04,
        "flip_pic_ratio": 0.7,
        "plastic_viscosity": 0,
        "rpic_damping": 0,
        "grid_v_damping_scale": 1.1,
        "boundary_conditions": [
            {"type": "bounding_box"},
            {
                "type": "surface_collider",
                "point": [0, 0, 0.637],
                "normal": [0, 0, 1],
                "surface": "slip",
                "friction": 0,
                "start_time": 0,
                "end_time": 1000,
            },
        ],
        "particle_filling": {
            "n_grid": 200,
            "max_particles_num": 500000,
            "density_threshold": 3,
            "search_threshold": 1,
            "max_partciels_per_cell": 1,
            "search_exclude_direction": 5,
            "ray_cast_direction": 4,
            "boundary": [0.35, 1.65, 0.35, 1.65, 0.6, 1.52],
            "smooth": True,
            "visualize": False,
        },
        "_note": "R7 M_jelly + cluster_6_15 — demo seed (30 frames)",
    },
}


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_API).rstrip("/")

    existing = json.loads(_get(f"{base}/v1/recipes"))
    for item in existing.get("items", []):
        if item.get("name") == RECIPE["name"]:
            print(f"already present: {item['id']}  name={item['name']}  v={item['version']}")
            return 0

    body = json.dumps(RECIPE).encode()
    created = json.loads(_post(f"{base}/v1/recipes", body))
    print(f"created: {created['id']}  name={created['name']}  v={created['version']}")
    return 0


def _get(url: str) -> str:
    return urllib.request.urlopen(url, timeout=10).read().decode()


def _post(url: str, body: bytes) -> str:
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=10).read().decode()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"POST failed {e.code}: {e.read().decode()[:300]}\n")
        raise


if __name__ == "__main__":
    sys.exit(main())
