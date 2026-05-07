from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import recipes as rec

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


class SaveRecipeRequest(BaseModel):
    data: dict
    based_on: str | None = None


@router.get("")
def list_endpoint():
    return rec.list_recipes()


@router.get("/{name}")
def get_endpoint(name: str):
    data = rec.load_recipe(name)
    if data is None:
        raise HTTPException(404, f"recipe '{name}' not found")
    builtin = rec.RECIPES_DIR / f"{name}.json"
    return {
        "name": name,
        "source": "builtin" if builtin.exists() else "user",
        "data": data,
    }


@router.put("/{name}")
def save_endpoint(name: str, req: SaveRecipeRequest):
    try:
        rec.save_user_recipe(name, req.data, based_on=req.based_on)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {
        "name": name,
        "source": "user",
        "data": rec.load_recipe(name) or req.data,
    }
