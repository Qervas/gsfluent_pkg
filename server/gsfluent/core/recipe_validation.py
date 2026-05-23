"""Pre-spawn recipe validation helpers.

These run BEFORE submit() reaches the sim engine, so the API layer can
reject obviously-bad recipes (e.g. sim_area that misses the model
entirely) with a readable 422 error rather than letting torch crash
inside the sim subprocess with a cryptic message.

Migrated from the deleted core/runner.py in the Phase-7+ rewire.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)


def translate_sim_area_if_local(recipe_data: dict, model_dir: Path) -> dict:
    """Translate model-local sim_area to world coords when the recipe says so.

    The sim core expects sim_area in absolute world coords (the canonical
    R7.M_jelly_cluster shape: [3440, 3480, 29030, 29060, -25, 35] for a
    building near world (3460, 29045, 5)). Workbench recipes ship portable
    model-local bounds (e.g. [-30, 30, -10, 10, -2, 45]); we translate
    those to the actual model's location at run-start so the same recipe
    can run on any model.

    The recipe MUST be explicit about which frame its sim_area is in:
        "sim_area_frame": "model"   - translate by model's bbox center
        "sim_area_frame": "world"   - leave alone (or absent — that's the
                                        default for back-compat with
                                        legacy world-coord recipes that
                                        predate this field)

    The previous version used a |value| <= 200 heuristic to guess
    model-vs-world. That misfired silently for legitimately-small
    world-coord recipes (e.g. a scene centered near origin in a
    normalized COLMAP), translating them into nonsense. Now-required
    explicit declaration removes the guesswork."""
    out = dict(recipe_data)
    sim_area = out.get("sim_area")
    if not sim_area or len(sim_area) != 6:
        return out
    frame = out.get("sim_area_frame", "world")
    if frame == "world":
        return out
    if frame != "model":
        _log.warning(
            "recipe has unknown sim_area_frame=%r (expected 'model'|'world'); "
            "treating as world", frame,
        )
        return out

    center = _read_model_bbox_center(model_dir)
    if center is None:
        return out
    cx, cy, cz = center
    out["sim_area"] = [
        sim_area[0] + cx, sim_area[1] + cx,
        sim_area[2] + cy, sim_area[3] + cy,
        sim_area[4] + cz, sim_area[5] + cz,
    ]
    _log.info(
        "translated sim_area model-local %s -> world %s (model center %s)",
        sim_area, out["sim_area"], center,
    )
    return out


def _read_model_bbox(
    model_dir: Path,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Read the model's highest-iteration point_cloud.ply and return its
    axis-aligned bounding box as `((xmin, ymin, zmin), (xmax, ymax, zmax))`.
    Returns None if the ply can't be parsed. Cheap — only the xyz
    columns are touched."""
    pc_root = model_dir / "point_cloud"
    if not pc_root.is_dir():
        return None
    iter_re = re.compile(r"^iteration_(\d+)$")
    best: tuple[int, Path] | None = None
    for it in pc_root.iterdir():
        if it.is_dir():
            m = iter_re.match(it.name)
            if m and (it / "point_cloud.ply").is_file():
                n = int(m.group(1))
                if best is None or n > best[0]:
                    best = (n, it / "point_cloud.ply")
    if best is None:
        return None
    try:
        import numpy as np
        from plyfile import PlyData
        v = PlyData.read(str(best[1]))["vertex"].data
        x = np.asarray(v["x"], dtype=np.float32)
        y = np.asarray(v["y"], dtype=np.float32)
        z = np.asarray(v["z"], dtype=np.float32)
        lo = (float(x.min()), float(y.min()), float(z.min()))
        hi = (float(x.max()), float(y.max()), float(z.max()))
        return (lo, hi)
    except Exception as e:
        _log.warning("failed to read model bbox for %s: %s", model_dir, e)
        return None


def _read_model_bbox_center(model_dir: Path) -> tuple[float, float, float] | None:
    """Centroid of the model's bbox. Used to translate model-local
    sim_area bounds to world coords. Returns None on parse failure;
    caller leaves the recipe untouched in that case."""
    bb = _read_model_bbox(model_dir)
    if bb is None:
        return None
    (xmin, ymin, zmin), (xmax, ymax, zmax) = bb
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def validate_sim_area_intersects_model(
    sim_area: list[float], model_dir: Path,
) -> None:
    """Cheap preflight: ensure the recipe's sim_area (now in world
    coords after translation) actually overlaps the model's bbox. The
    upstream sim filters splats by sim_area and crashes with a cryptic
    `IndexError: min(): Expected reduction dim 0 to have non-zero
    size.` from torch when 0 splats survive the filter. We catch the
    empty-intersection case here and raise a readable error.

    `sim_area` is `[xmin, xmax, ymin, ymax, zmin, zmax]`. No-op if we
    can't read the model bbox (don't block on a flaky read)."""
    if not sim_area or len(sim_area) != 6:
        return
    bb = _read_model_bbox(model_dir)
    if bb is None:
        return
    (mx0, my0, mz0), (mx1, my1, mz1) = bb
    sx0, sx1, sy0, sy1, sz0, sz1 = (float(x) for x in sim_area)
    overlap = (
        sx0 < mx1 and sx1 > mx0 and
        sy0 < my1 and sy1 > my0 and
        sz0 < mz1 and sz1 > mz0
    )
    if not overlap:
        raise ValueError(
            f"recipe's sim_area does not overlap the model bbox — the sim "
            f"would filter every splat out and crash. "
            f"sim_area (world): x=[{sx0:.2f},{sx1:.2f}] y=[{sy0:.2f},{sy1:.2f}] z=[{sz0:.2f},{sz1:.2f}]; "
            f"model bbox: x=[{mx0:.2f},{mx1:.2f}] y=[{my0:.2f},{my1:.2f}] z=[{mz0:.2f},{mz1:.2f}]. "
            f"Either pick a recipe whose sim_area matches this model's world "
            f"coords, or set `sim_area_frame: \"model\"` in the recipe so the "
            f"runner translates model-local bounds to world."
        )
