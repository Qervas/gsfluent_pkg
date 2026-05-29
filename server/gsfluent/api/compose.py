"""Structured recipe composition API.

A recipe is composed from three orthogonal inputs — MATERIAL x SCENARIO x
BUILDING — instead of being hand-edited. The composer (gsfluent.authoring)
turns the three choices into the flat sim recipe the existing POST /api/runs
already accepts, so this endpoint is purely additive: it does not run a sim and
changes no existing behaviour.

  POST /api/compose          {material, scenario, building} -> {recipe_data, ...}
  GET  /api/compose/library  -> {materials[], scenarios[], buildings[]}

The composer enforces verified safety ceilings (impact/blast speed-force,
CFL-derived timestep, grid containment). Over-ceiling or unknown picks come
back as a 422 validation error so the UI can surface the exact reason — we do
NOT silently clamp.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..api.errors import raise_validation_error
from ..authoring import ComposeError, compose
from ..authoring.buildings import BUILDINGS
from ..authoring.materials import MATERIALS
from ..authoring.scenarios import SCENARIOS

router = APIRouter(prefix="/api/compose", tags=["compose"])


class ComposeRequest(BaseModel):
    # Strict: reject unknown fields so a typo'd key is a clear 422, mirroring
    # the StartRunRequest contract in api/runs.py.
    model_config = ConfigDict(extra="forbid")

    material: str
    scenario: str
    building: str


@router.post("")
def compose_endpoint(req: ComposeRequest):
    """Compose a flat sim recipe from material x scenario x building.

    Returns the recipe_data the frontend then submits to POST /api/runs
    unchanged. Pure + deterministic — no sim, no model access here; the
    model-bound sim_area check happens at run-submit time.
    """
    try:
        recipe_data = compose(req.material, req.scenario, req.building)
    except KeyError as e:
        # get_material/get_scenario/get_building raise KeyError with a
        # "have [...]" message listing the valid names — surface it verbatim.
        raise_validation_error(
            kind="validation.recipe_data",
            message=str(e).strip('"'),
        )
    except ComposeError as e:
        # Safety-ceiling / geometry rejection (speed over the grid-escape
        # limit, blast force over the mass-scaled limit, impactor too large).
        raise_validation_error(
            kind="validation.recipe_data",
            message=str(e),
        )
    return {
        "material": req.material,
        "scenario": req.scenario,
        "building": req.building,
        "recipe_data": recipe_data,
    }


@router.get("/library")
def library_endpoint():
    """List the composer's three libraries for the UI dropdowns.

    Surfaces only the fields the picker needs — NOT the full recipe internals
    (particle_filling, grid constants, camera block) which are library
    invariants the composer fills in.
    """
    materials = [
        {
            "name": name,
            "material": m["material"],
            "E": m["E"],
            "nu": m["nu"],
            "density": m["density"],
            "yield_stress": m.get("yield_stress", 0.0),
            "friction_angle": m.get("friction_angle", 0.0),
            "desc": m.get("_desc", ""),
        }
        for name, m in MATERIALS.items()
    ]
    scenarios = [
        {
            "name": name,
            "base": s.get("base", "free"),
            "frame_num": s["frame_num"],
            "gravity": s["gravity"],
            "recommended_material": s.get("recommended_material"),
            "damping": s.get("damping"),
            "num_events": len(s.get("events", [])),
            "desc": s.get("_desc", ""),
        }
        for name, s in SCENARIOS.items()
    ]
    buildings = [
        {
            "name": name,
            "model_path": b["model_path"],
            "bbox": b["bbox"],
            "sim_area": b["sim_area"],
            "desc": b.get("_desc", ""),
        }
        for name, b in BUILDINGS.items()
    ]
    return {
        "materials": materials,
        "scenarios": scenarios,
        "buildings": buildings,
    }
