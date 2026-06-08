"""Helpers for validating and preparing POST /api/runs submissions."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..api.errors import raise_cap_exceeded, raise_validation_error
from ..core import recipe_validation
from ..core.limits import CapConfig, check_recipe_caps
from ..protocols.runs import CapExceededError

_SAFE_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class StartRunRequest(BaseModel):
    """Strict-mode request body for POST /api/runs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_name: str = Field(..., min_length=1, max_length=128)
    model_path: str = Field(..., min_length=1)
    recipe_data: dict
    recipe_source: str
    particles: int = Field(default=200_000, gt=0)
    dry_run: bool = False

    @field_validator("run_name")
    @classmethod
    def _run_name_must_be_safe(cls, v: str) -> str:
        if not _SAFE_RUN_NAME_RE.match(v):
            raise ValueError("run_name must match ^[A-Za-z0-9_.-]+$")
        return v


def parse_start_request(raw_body: dict, trace_id: str) -> StartRunRequest:
    """Parse a run-start request and map Pydantic errors to API envelopes."""
    try:
        return StartRunRequest.model_validate(raw_body, strict=True)
    except ValidationError as e:
        errs = e.errors()
        first = errs[0] if errs else {}
        loc = first.get("loc", ("?",))
        loc_parts = [p for p in loc if p != "body"]
        field = ".".join(str(p) for p in loc_parts) if loc_parts else "?"
        msg = first.get("msg", "validation failed")
        safe_errs: list[dict] = [
            {
                "loc": [str(p) for p in entry.get("loc", ())],
                "type": entry.get("type", ""),
                "msg": entry.get("msg", ""),
            }
            for entry in errs
        ]
        raise_validation_error(
            kind=f"validation.{field}",
            message=f"{field}: {msg}",
            details={"errors": safe_errs, "trace_id": trace_id},
        )


def enforce_submission_caps(req: StartRunRequest, caps: CapConfig) -> None:
    """Check configured recipe caps and translate failures to stable 422 kinds."""
    cap_input = {
        **req.recipe_data,
        "particle_count": req.particles,
    }
    try:
        check_recipe_caps(cap_input, caps)
    except CapExceededError as e:
        msg = str(e)
        if "Particle count" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.particle_count",
                message=msg,
                details={"requested": req.particles, "limit": caps.max_particle_count},
            )
        if "Wall-time" in msg:
            raw = req.recipe_data.get("wall_time_sec", caps.max_wall_time_sec)
            try:
                requested = int(raw)
            except (TypeError, ValueError):
                requested = raw
            raise_cap_exceeded(
                kind="cap_exceeded.wall_time",
                message=msg,
                details={"requested": requested, "limit": caps.max_wall_time_sec},
            )
        if "Recipe size" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.recipe_size",
                message=msg,
                details={"limit": caps.max_recipe_bytes},
            )
        raise_cap_exceeded(
            kind="cap_exceeded.unknown",
            message=msg,
            details={},
        )


def require_registered_model_path(
    path: str,
    *,
    list_models: Callable[[], list[dict]],
) -> Path:
    """Return a model path only when it exists and is registered."""
    model_dir = Path(path).resolve()
    if not model_dir.exists():
        raise FileNotFoundError(f"model_path does not exist: {path}")
    if not model_dir.is_dir():
        raise NotADirectoryError(f"model_path is not a directory: {path}")
    known_paths = {
        Path(entry["path"]).resolve()
        for entry in list_models()
        if entry.get("path")
    }
    if model_dir not in known_paths:
        raise ValueError(f"model_path is not registered: {path}")
    return model_dir


def load_registered_model_path(
    req: StartRunRequest,
    *,
    list_models: Callable[[], list[dict]],
) -> Path:
    """Load model path for a request and map path errors to API envelopes."""
    try:
        return require_registered_model_path(req.model_path, list_models=list_models)
    except FileNotFoundError:
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path does not exist: {req.model_path}",
            details={"got": req.model_path},
        )
    except NotADirectoryError:
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path is not a directory: {req.model_path}",
            details={"got": req.model_path},
        )
    except ValueError as e:
        raise_validation_error(
            kind="validation.model_path",
            message=str(e),
            details={"got": req.model_path},
        )


def prepare_effective_recipe(
    req: StartRunRequest,
    model_dir: Path,
    *,
    error_prefix: str,
) -> dict:
    """Translate and validate model-relative recipe fields before a run starts."""
    try:
        effective_recipe = recipe_validation.translate_sim_area_if_local(
            req.recipe_data, model_dir,
        )
        recipe_validation.validate_sim_area_intersects_model(
            effective_recipe.get("sim_area", []), model_dir,
        )
        recipe_validation.validate_model_orientation(
            effective_recipe, model_dir,
        )
        return effective_recipe
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
        raise_validation_error(
            kind="validation.recipe_data",
            message=f"{error_prefix}: {e}",
            details={"got": str(e)},
        )
