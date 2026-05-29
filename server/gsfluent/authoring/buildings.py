"""BUILDING library — per-scan config.

A building is the scanned 3DGS model plus the few facts the composer needs that
are genuinely scene-specific: the model path, the cube-frame bbox the body
occupies (for resolving building-relative anchors), the metric `sim_area`, and
the camera the native-render verify-to-video path reads.

`bbox` is in the cube frame the sim runs in (longest axis -> 1.0, centered at
(1,1,1)). For now we read it from the recipe's particle_filling.boundary (the
"start with #1" decision); a later upgrade reads the true frame-0 particle
min/max. Everything building-relative in a scenario (base/mid/top, +x/-x) is
resolved against this bbox by compose.anchors.
"""
from __future__ import annotations

# bbox order: [xmin, xmax, ymin, ymax, zmin, zmax] in cube frame.
BUILDINGS: dict[str, dict] = {
    "cluster_6_15": {
        "model_path": "/data/yinshaoxuan/GaussianFluent/model/cluster_6_15",
        "bbox": [0.35, 1.65, 0.35, 1.65, 0.6, 1.52],
        # World-coord AABB of the scan (NOT a generic [-30,30] box — cluster_6_15
        # lives at ~(3460, 29045)). Matches recipes/*.json after commit 685c19f
        # "align sim_area with cluster_6_15 world bbox". sim_area_frame="model"
        # means the sim driver translates this against the model at run time.
        "sim_area": [3440, 3480, 29030, 29060, -25, 35],
        "sim_area_frame": "model",
        # Camera block the native renderer (--render_img) reads. Irrelevant to
        # production in-browser playback, but required for verify-to-video.
        # Values from jelly.json — the recipe that renders cluster_6_15 CLEANLY.
        # (wrecking.json's camera has init_radius=12, which puts the eye inside
        # the building and produces a streaked-mess render — verified 2026-05-29.
        # Frame the whole tower: radius 130, azimuth 140, elevation 18.)
        "camera": {
            "mpm_space_vertical_upward_axis": [0, 0, 1],
            "mpm_space_viewpoint_center": [1.0, 1.0, 1.0],
            "default_camera_index": -1,
            "show_hint": False,
            "init_azimuthm": 140,
            "init_elevation": 18,
            "init_radius": 130,
            "move_camera": True,
            "delta_a": 1.0,
            "delta_e": -0.03,
            "delta_r": -0.35,
            "opacity_threshold": 0.0,
            "rotation_degree": [0.0],
            "rotation_axis": [0],
        },
        "_desc": "Photoreal high-rise tower scan. The reference building.",
    },
}


def get_building(name: str) -> dict:
    if name not in BUILDINGS:
        raise KeyError(
            f"unknown building {name!r}; have {sorted(BUILDINGS)}"
        )
    return BUILDINGS[name]
