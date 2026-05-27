import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import recipe_lint
from ..core import recipes as rec

router = APIRouter(prefix="/api/recipes", tags=["recipes"])

logger = logging.getLogger(__name__)


class SaveRecipeRequest(BaseModel):
    data: dict
    based_on: str | None = None


def _validate_name(name: str) -> None:
    """Reject any name that's not a plain identifier. Used on every
    endpoint that builds a filesystem path from the URL — otherwise a
    name like '../etc/passwd' lets a caller probe / delete files
    outside RECIPES_DIR / USER_RECIPES_DIR. Mirrors core/recipes._NAME_RE."""
    if not rec._NAME_RE.match(name):
        raise HTTPException(422, f"invalid recipe name: {name!r}")


@router.get("")
def list_endpoint():
    return rec.list_recipes()


@router.get("/{name}")
def get_endpoint(name: str):
    _validate_name(name)
    try:
        loaded = rec.load_recipe(name)
    except rec.RecipeReadError as e:
        raise HTTPException(409, str(e)) from e
    if loaded is None:
        raise HTTPException(404, f"recipe '{name}' not found")
    data, source = loaded
    return {"name": name, "source": source, "data": data}


@router.put("/{name}")
def save_endpoint(name: str, req: SaveRecipeRequest):
    _validate_name(name)
    try:
        path, payload = rec.save_user_recipe(name, req.data, based_on=req.based_on)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    # Phase 0 recipe-stability lint: surface known-unstable param combos
    # (damping no-op, dt > CFL) on save, but NEVER block — saving an
    # in-progress recipe is legitimate. The workbench shows these inline so
    # the author fixes them before running.
    findings = recipe_lint.lint_recipe(payload)
    if findings:
        logger.warning(
            "recipe %r saved with %d lint finding(s): %s",
            name,
            len(findings),
            ", ".join(f"{f.rule_id}({f.param})" for f in findings),
        )
    return {
        "name": name,
        "source": "user",
        "data": payload,
        "lint": [f.as_dict() for f in findings],
    }


@router.delete("/{name}")
def delete_user_recipe(name: str):
    """Delete a user preset. 403 on built-ins, 404 on unknown."""
    _validate_name(name)
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
        raise HTTPException(500, f"failed to delete: {e}") from e
    return {"deleted": name}
