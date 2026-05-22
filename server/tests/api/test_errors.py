"""Tests for the 422 error envelope shape + trace_id helper."""
import re
import uuid

import pytest
from fastapi import HTTPException

from gsfluent.api.errors import (
    api_error_envelope,
    new_trace_id,
    raise_validation_error,
    raise_cap_exceeded,
)
from gsfluent.protocols.runs import CapExceededError, ValidationError


def test_new_trace_id_is_a_ulid_or_uuid_string() -> None:
    tid = new_trace_id()
    # Spec example uses a ULID; UUID4 is acceptable as long as it is
    # a 26-32 char alphanumeric token.
    assert isinstance(tid, str)
    assert re.match(r"^[A-Za-z0-9]{20,40}$", tid)


def test_two_trace_ids_differ() -> None:
    assert new_trace_id() != new_trace_id()


def test_api_error_envelope_shape() -> None:
    env = api_error_envelope(
        kind="cap_exceeded.particle_count",
        message="Particle count 800000 exceeds limit 500000",
        details={"requested": 800_000, "limit": 500_000},
        trace_id="01H8K2P",
    )
    assert env == {
        "error": {
            "kind": "cap_exceeded.particle_count",
            "message": "Particle count 800000 exceeds limit 500000",
            "details": {"requested": 800_000, "limit": 500_000},
            "trace_id": "01H8K2P",
        }
    }


def test_api_error_envelope_default_details_is_empty_dict() -> None:
    env = api_error_envelope(
        kind="validation.run_name",
        message="run_name must match ^[A-Za-z0-9_.-]+$",
        trace_id="t1",
    )
    assert env["error"]["details"] == {}


def test_raise_validation_error_produces_422_with_envelope() -> None:
    with pytest.raises(HTTPException) as ei:
        raise_validation_error(
            kind="validation.particle_count",
            message="particle_count must be a positive int",
            details={"got": "abc"},
        )
    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert detail["error"]["kind"] == "validation.particle_count"
    assert detail["error"]["details"] == {"got": "abc"}
    assert "trace_id" in detail["error"]


def test_raise_cap_exceeded_produces_422() -> None:
    with pytest.raises(HTTPException) as ei:
        raise_cap_exceeded(
            kind="cap_exceeded.wall_time",
            message="wall_time_sec 7200 exceeds backend max 3600",
            details={"requested": 7200, "limit": 3600},
        )
    assert ei.value.status_code == 422
    assert ei.value.detail["error"]["kind"] == "cap_exceeded.wall_time"
