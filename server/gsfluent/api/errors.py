"""422 error envelope shape + trace_id helper.

Matches the API error response shape from the spec:

    {
      "error": {
        "kind": "cap_exceeded.particle_count",
        "message": "Particle count 800000 exceeds limit 500000",
        "details": { "requested": 800000, "limit": 500000 },
        "trace_id": "01H8K2P..."
      }
    }

Every 422 in api/runs.py routes through these helpers so the envelope
is uniform across validation.*, cap_exceeded.*, and other typed-kind
errors. trace_id is generated per-request and surfaces in the response
so customers can paste it into a support ticket and operators can grep
the structured event stream.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException


def new_trace_id() -> str:
    """Return a fresh trace identifier.

    Uses uuid4 hex (32 chars, base16). A ULID would be lexicographically
    sortable but the spec only requires uniqueness + correlatability;
    uuid4 is stdlib and avoids another dependency.
    """
    return uuid.uuid4().hex


def api_error_envelope(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the JSON shape that every 4xx/5xx error response carries.

    trace_id is auto-generated if not supplied.
    """
    return {
        "error": {
            "kind": kind,
            "message": message,
            "details": dict(details) if details else {},
            "trace_id": trace_id if trace_id is not None else new_trace_id(),
        }
    }


def raise_validation_error(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise a 422 HTTPException with the standard envelope.

    Callers in api/runs.py use this for both Pydantic strict-mode rejection
    and any post-parse validation that surfaces as `validation.<field>`.
    """
    raise HTTPException(
        status_code=422,
        detail=api_error_envelope(kind=kind, message=message, details=details),
    )


def raise_cap_exceeded(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise a 422 HTTPException for cap violations.

    Same envelope shape as validation errors; the `kind` discriminator
    is what the client uses to distinguish (`cap_exceeded.*` vs
    `validation.*`).
    """
    raise HTTPException(
        status_code=422,
        detail=api_error_envelope(kind=kind, message=message, details=details),
    )
