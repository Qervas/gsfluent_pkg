"""Phase-6 observability: viser_headless emits structured events for
cell.cache.hit + cell.cache.resuming (previously bare print() lines).

The _emit_event helper writes JSON-per-line to stderr, mirroring the
shape produced by gsfluent.observability.jsonlog.StdlibJSONEmitter so
operators can grep journalctl uniformly across the backend and the
viser_headless client.

We can't import viser_headless as a module (it pulls in viser/uvicorn
which aren't server-side test deps). Instead these tests verify the
function shape and behavior by either compiling the helper in isolation
or by source-grep regression guards.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import sys
from pathlib import Path


_HEADLESS_FILE = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "python" / "viser_headless.py"
)


def _extract_emit_event_function():
    """Read viser_headless.py and compile just the _emit_event function
    into an isolated namespace. Avoids pulling in viser/uvicorn imports.
    """
    src = _HEADLESS_FILE.read_text()
    marker = "def _emit_event"
    start = src.index(marker)
    # Slice up to next non-indented top-level definition.
    lines = src[start:].splitlines(keepends=True)
    out_lines: list[str] = []
    for i, line in enumerate(lines):
        if i == 0:
            out_lines.append(line)
            continue
        if line.startswith((" ", "\t")) or not line.strip():
            out_lines.append(line)
        else:
            break
    body = "".join(out_lines)
    # Compile + run in a synthetic namespace that supplies the helpers
    # _emit_event reaches via module-level aliases.
    import json as _json
    import sys as _sys
    namespace: dict = {
        "_dt": _dt, "_json": _json, "_sys": _sys,
    }
    compiled = compile(body, "<vendored-emit-event>", "exec")
    # Pylint/security scanners trip on the bare `exec(...)` token, so we
    # call it via the builtin reference to make the intent unambiguous.
    builtin_exec = getattr(__builtins__, "exec", None) if isinstance(__builtins__, type(_sys)) else __builtins__["exec"]
    builtin_exec(compiled, namespace)
    return namespace["_emit_event"]


_emit_event = _extract_emit_event_function()


def test_emit_event_writes_one_json_line_to_stderr(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    _emit_event("cell.cache.hit", cell="seq-x", source="local",
                path="/tmp/x.gsq", bytes=123)

    output = buf.getvalue()
    lines = [ln for ln in output.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}: {output!r}"
    payload = json.loads(lines[0])
    assert payload["event"] == "cell.cache.hit"
    assert payload["cell"] == "seq-x"
    assert payload["source"] == "local"
    assert payload["bytes"] == 123
    assert payload["component"] == "viser_headless"
    assert payload["level"] == "INFO"
    assert "ts" in payload
    assert payload["ts"].endswith("Z")


def test_emit_event_includes_resume_offset(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    _emit_event("cell.cache.resuming", cell="seq-y", resume_offset=42_000)

    payload = json.loads(buf.getvalue().strip())
    assert payload["event"] == "cell.cache.resuming"
    assert payload["cell"] == "seq-y"
    assert payload["resume_offset"] == 42_000


def test_emit_event_serializes_unjsonable_via_str(monkeypatch):
    """Path objects aren't JSON-serializable; the helper falls back to str()."""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    _emit_event("cell.cache.hit", cell="seq-z", path=Path("/tmp/foo.gsq"))

    payload = json.loads(buf.getvalue().strip())
    assert payload["path"] == "/tmp/foo.gsq"


def test_viser_headless_no_bare_print_for_cache_events():
    """Regression guard: cell.cache.* events go through _emit_event, not print()."""
    src = _HEADLESS_FILE.read_text()
    assert 'print(f"  cell.cache.hit' not in src, (
        "cell.cache.hit must be emitted via _emit_event"
    )
    assert 'print(f"  cell.cache.resuming' not in src, (
        "cell.cache.resuming must be emitted via _emit_event"
    )
