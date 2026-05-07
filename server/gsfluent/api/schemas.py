from fastapi import APIRouter

from ..schemas.boundary import BC_SCHEMAS
from ..schemas.material_defaults import MATERIAL_DEFAULTS

router = APIRouter(prefix="/api/schemas", tags=["schemas"])


@router.get("/boundaries")
def boundaries():
    """Returns BC types as JSON-friendly objects (tuples → field dicts)."""
    return {
        ty: [
            {"name": name, "type": typ, "default": default, "hint": hint}
            for (name, typ, default, hint) in fields
        ]
        for ty, fields in BC_SCHEMAS.items()
    }


@router.get("/materials")
def materials():
    """Per-material default parameters (E, nu, density, ...)."""
    return MATERIAL_DEFAULTS
