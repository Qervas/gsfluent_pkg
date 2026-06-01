from __future__ import annotations

from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

from gsfluent.core.recipe_validation import translate_sim_area_if_local


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
