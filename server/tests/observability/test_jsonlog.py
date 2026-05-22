"""Tests for the stdlib-based JSON EventEmitter implementation."""
import io
import json
import re

import pytest

from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.observability import EventEmitter


def _parse_lines(stream: io.StringIO) -> list[dict]:
    """Parse one JSON object per line from the in-memory stream."""
    stream.seek(0)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_stdlib_json_emitter_satisfies_event_emitter_protocol() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    assert isinstance(emitter, EventEmitter)


def test_emit_writes_one_json_line() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc", particle_count=200_000)
    events = _parse_lines(stream)
    assert len(events) == 1
    assert events[0]["event"] == "run.started"
    assert events[0]["run_id"] == "abc"
    assert events[0]["particle_count"] == 200_000


def test_emit_auto_attaches_iso_timestamp() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc")
    events = _parse_lines(stream)
    # ISO 8601 with Z suffix or +00:00 offset
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", events[0]["ts"])


def test_emit_includes_log_level() -> None:
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)
    emitter.emit("run.started", run_id="abc")
    events = _parse_lines(stream)
    assert events[0]["level"] == "INFO"


def test_child_emitter_attaches_context_to_every_event() -> None:
    stream = io.StringIO()
    parent = StdlibJSONEmitter(stream=stream)
    child = parent.child(run_id="abc", sequence_name="demo")
    child.emit("run.started")
    child.emit("run.completed", duration_sec=42.0)
    events = _parse_lines(stream)
    assert len(events) == 2
    for e in events:
        assert e["run_id"] == "abc"
        assert e["sequence_name"] == "demo"
    assert events[1]["duration_sec"] == 42.0


def test_child_context_overridable_per_event() -> None:
    stream = io.StringIO()
    parent = StdlibJSONEmitter(stream=stream)
    child = parent.child(run_id="abc")
    child.emit("run.started", run_id="xyz")
    events = _parse_lines(stream)
    assert events[0]["run_id"] == "xyz"


def test_grandchild_emitter_chains_context() -> None:
    stream = io.StringIO()
    root = StdlibJSONEmitter(stream=stream)
    a = root.child(run_id="abc")
    b = a.child(phase="sim")
    b.emit("sim.started")
    events = _parse_lines(stream)
    assert events[0]["run_id"] == "abc"
    assert events[0]["phase"] == "sim"


def test_non_json_serializable_value_is_coerced_to_string() -> None:
    """Custom objects shouldn't crash emit(); they should str() instead."""
    stream = io.StringIO()
    emitter = StdlibJSONEmitter(stream=stream)

    class CustomObj:
        def __str__(self) -> str:
            return "custom-obj-repr"

    emitter.emit("test.weird", obj=CustomObj())
    events = _parse_lines(stream)
    assert events[0]["obj"] == "custom-obj-repr"
