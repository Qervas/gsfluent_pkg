from __future__ import annotations

from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

from gsfluent.core.recipe_validation import (
    translate_sim_area_if_local,
    validate_model_orientation,
)


def _write_model(root: Path) -> Path:
    model = root / "model"
    ply_dir = model / "point_cloud" / "iteration_1"
    ply_dir.mkdir(parents=True)
    arr = np.zeros(2, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    arr["x"] = [10.0, 14.0]
    arr["y"] = [20.0, 26.0]
    arr["z"] = [30.0, 38.0]
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(ply_dir / "point_cloud.ply"))
    return model


def test_translate_sim_area_marks_translated_recipe_world(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    recipe = {
        "sim_area": [-1, 1, -2, 2, -3, 3],
        "sim_area_frame": "model",
    }

    out = translate_sim_area_if_local(recipe, model)

    assert out["sim_area"] == [11.0, 13.0, 21.0, 25.0, 31.0, 37.0]
    assert out["sim_area_frame"] == "world"
    assert recipe["sim_area_frame"] == "model"


def test_translate_sim_area_leaves_world_recipe_unchanged(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    recipe = {
        "sim_area": [1, 2, 3, 4, 5, 6],
        "sim_area_frame": "world",
    }

    out = translate_sim_area_if_local(recipe, model)

    assert out == recipe


# ---- model orientation hook ---------------------------------------------
#
# Orientation used to be auto-rejected when the submitted model's longest axis
# differed from the building's longest axis. That was too brittle for varied
# buildings/objects, so orientation is now a user-controlled reorient operation.

# Building longest on z (like cluster_6_15): extents x0.6 y0.4 z1.0.
_BUILDING_Z_TALL = {"particle_filling": {"boundary": [0.7, 1.3, 0.8, 1.2, 0.5, 1.5]}}


def _write_model_ext(root: Path, xe: float, ye: float, ze: float) -> Path:
    model = root / "model"
    ply_dir = model / "point_cloud" / "iteration_1"
    ply_dir.mkdir(parents=True)
    arr = np.zeros(2, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    arr["x"] = [0.0, xe]
    arr["y"] = [0.0, ye]
    arr["z"] = [0.0, ze]
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(ply_dir / "point_cloud.ply"))
    return model


def test_orientation_no_longer_rejects_lying_model(tmp_path: Path) -> None:
    # The model may be lying down relative to the curated tower recipe, but
    # shape-based inference is not reliable enough to block the run.
    model = _write_model_ext(tmp_path, 30.0, 50.0, 18.0)
    validate_model_orientation(_BUILDING_Z_TALL, model)


def test_orientation_accepts_upright_model(tmp_path: Path) -> None:
    # Correctly oriented (b5036643: ext 30/18/50, longest z = building) → ok.
    model = _write_model_ext(tmp_path, 30.0, 18.0, 50.0)
    validate_model_orientation(_BUILDING_Z_TALL, model)  # no raise


def test_orientation_skips_near_cubic(tmp_path: Path) -> None:
    # Longest axis differs but isn't dominant (33 < 1.4*31) → ambiguous, no block.
    model = _write_model_ext(tmp_path, 30.0, 33.0, 31.0)
    validate_model_orientation(_BUILDING_Z_TALL, model)  # no raise


def test_orientation_skips_without_expected_bbox(tmp_path: Path) -> None:
    # No particle_filling.boundary (hand-written recipe) → fail open.
    model = _write_model_ext(tmp_path, 30.0, 50.0, 18.0)
    validate_model_orientation({}, model)
    validate_model_orientation({"particle_filling": {}}, model)
