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
    try:
        loaded = rec.load_recipe(name)
    except rec.RecipeReadError as e:
        raise HTTPException(409, str(e))
    if loaded is None:
        raise HTTPException(404, f"recipe '{name}' not found")
    data, source = loaded
    return {"name": name, "source": source, "data": data}


@router.put("/{name}")
def save_endpoint(name: str, req: SaveRecipeRequest):
    try:
        path, payload = rec.save_user_recipe(name, req.data, based_on=req.based_on)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"name": name, "source": "user", "data": payload}


@router.delete("/{name}")
def delete_user_recipe(name: str):
    """Delete a user preset. 403 on built-ins, 404 on unknown."""
    p = rec.USER_RECIPES_DIR / f"{name}.json"
    if not p.exists():
        # Built-in or unknown — surface differently.
        builtin = rec.RECIPES_DIR / f"{name}.json"
        if builtin.exists():
            raise HTTPException(
                403, f"'{name}' is a built-in recipe and cannot be deleted"
            )
        raise HTTPException(404, f"user preset '{name}' not found")
    try:
        p.unlink()
    except OSError as e:
        raise HTTPException(500, f"failed to delete: {e}")
    return {"deleted": name}
