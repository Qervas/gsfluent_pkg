"""BUILDING library — per-scan config.

A building is the scanned 3DGS model plus the few facts the composer needs that
are genuinely scene-specific: the model path, the cube-frame bbox the body
occupies (for resolving building-relative anchors), the metric `sim_area`, and
the camera the native-render verify-to-video path reads.

`bbox` is in the cube frame the sim runs in (longest axis -> 1.0, centered at
(1,1,1)). MEASURED 2026-05-29 by replicating the sim's own preprocessing
(identity rotation -> sim_area crop -> transform2origin, the uniform
scale=1/max_extent in utils/transformation_utils.py) against the actual
683k-point scan. Everything building-relative in a scenario (base/mid/top,
+x/-x) is resolved against this bbox by compose.anchors, so it MUST be tight to
the real geometry — an over-wide bbox sizes every lateral BC wrong (verified:
the old guessed [0.35,1.65,...] footprint was 2-3.6x too wide, so the implode
core column was wider than the building itself and ejected it).
"""
from __future__ import annotations

# bbox order: [xmin, xmax, ymin, ymax, zmin, zmax] in cube frame.
BUILDINGS: dict[str, dict] = {
    "cluster_6_15": {
        "model_path": "/data/yinshaoxuan/GaussianFluent/model/cluster_6_15",
        # TRUE cube-frame extent: a TALL SLENDER SLAB. z-span 1.0 (full height,
        # the longest axis), x-span 0.60 (medium), y-span 0.36 (thin). Raw scan
        # was z=50.4 x=30.4 y=18.0 world units; uniform-scaled by 1/50.4 and
        # centered at (1,1,1). The tall aspect means topple/pancake ARE viable
        # (the earlier "squat, can't topple" was an artifact of the wrong bbox).
        "bbox": [0.698, 1.302, 0.821, 1.179, 0.5, 1.5],
        # MODEL-LOCAL sim_area: a symmetric box centered on the model's own
        # bbox center (sim_area_frame="model" -> the runner adds the model's
        # bbox center at submit time). Fixed 2026-05-30: the previous value was
        # WORLD coords [3440,3480,29030,29060,-25,35] but STILL tagged
        # frame="model", so the runner added the model center ON TOP of already-
        # world coords (~3460,29045) -> sim_area landed at ~(6900,58000), missed
        # the model entirely -> "sim_area does not overlap model bbox" 422 ->
        # the run silently halted at 0%. Model-local is also portable: it
        # adapts to whatever model is run (original, pruned, re-uploaded).
        # Symmetric +-30 (not the building's asymmetric per-axis spans) because
        # the library holds BOTH Z-up (z-span 50.4) and Y-up (y-span 50.4)
        # variants of this scan; a symmetric cube contains the building in EITHER
        # orientation (max half-span 25.2 + margin). The model holds only the
        # building's splats, so a generous box still selects exactly them.
        "sim_area": [-60, 60, -60, 60, -30, 30],
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
