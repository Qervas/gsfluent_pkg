# Phase 5 — Streaming Cache Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.gsq` cache delivery cheap on the second hit and resumable after an interrupted download. Three coordinated changes:

1. **Server (`server/gsfluent/api/sequences.py`):** Emit `Cache-Control: public, immutable, max-age=31536000` and a weak ETag `"<size>-<mtime>"` on `GET /api/sequences/{name}/cache/splats.gsq`. Handle `If-None-Match` → 304. Verify `Range` is honored end-to-end (FastAPI `FileResponse` provides it; we lock it down with a test).
2. **Client (`frontend/python/viser_headless.py`):** Before downloading in `_sync_cell_gsq_streaming`, send HTTP HEAD; if the remote ETag matches the local file's `_local_etag` (or content-length matches as a back-compat fallback), short-circuit by loading from disk. If a `.partial` exists from a prior interrupted download, send `Range: bytes=<n>-` and resume; if the server returns 200 (Range ignored), unlink the partial and re-fetch from byte 0.
3. **Rename refactor:** `npz_root` → `cache_root` (variable), `--npz_dir` → `--cache-dir` (CLI flag), `GSFLUENT_NPZ_REBUILD` → `GSFLUENT_CACHE_REBUILD` (env var read by `core/runner.py`). Deprecated aliases stay for one release with a one-shot warning per process.

**Why `frontend/python/viser_headless.py` is in scope for a backend phase ("γ scope"):** That file is the HTTP **client** of the backend's `.gsq` API. Hardening the backend's cache contract is only half the job — the client has to participate by sending `If-None-Match` and `Range` headers. The React/TS SPA in `frontend/src/` is genuinely out of scope and untouched here.

**Architecture:** Pure additions on the server side (headers + a 304 branch). The client gets a HEAD probe in front of every download and a Range/resume branch in front of the streaming-decode path. The rename is mechanical with deprecation shims.

**Tech Stack:** Python 3.10+, FastAPI `FileResponse` (Range support already built in), `httpx` (already a client dep), `pytest>=8`, `pytest-asyncio>=0.23`. **No new dependencies in Phase 5.**

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` — Section 3 Flow D ("Customer hits the streaming cache"), Open Question 3 ("ETag format — weak `<size>-<mtime>` per spec default").

**Phase 5 is plan 5 of 7.** Independent of Phase 4's systemd / boot-recovery work. Depends on Phase 1's `EventEmitter`/`StdlibJSONEmitter` for the new structured events (`cell.cache.hit`, `cell.cache.resuming`, `cell.cache.resumed`). The `.gsq` HTTP endpoint and the streaming-decode client both already exist; Phase 5 only adds headers and a HEAD-then-stream wrapper around them. Phase 6 (observability completion) is the next-in-line consumer of the events emitted here.

---

## File Structure

### New files (Phase 5)

```
server/tests/api/
├── __init__.py
└── test_sequences_cache_headers.py        ← ETag / Cache-Control / If-None-Match / Range

server/tests/integration/
├── __init__.py
├── test_streaming_cache_hit.py            ← HEAD-skip on second request
└── test_streaming_resume_from_partial.py  ← Range request, 206 received, decode completes
```

### Modified files (Phase 5)

```
server/gsfluent/api/sequences.py            ← Cache-Control + ETag + If-None-Match → 304
                                              on GET /api/sequences/{name}/cache/splats.gsq
server/gsfluent/core/runner.py              ← GSFLUENT_CACHE_REBUILD (new canonical name);
                                              GSFLUENT_NPZ_REBUILD stays as deprecated alias
                                              with one-shot warning
frontend/python/viser_headless.py           ← (a) rename npz_root → cache_root
                                              (b) add --cache-dir (with --npz_dir alias)
                                              (c) _local_etag helper
                                              (d) HEAD-probe + cache-hit branch
                                              (e) Range-resume branch in _sync_cell_gsq_streaming
                                              (f) emit cell.cache.hit / cell.cache.resuming /
                                                  cell.cache.resumed events
```

### Files NOT modified in Phase 5

```
server/gsfluent/core/library.py             ← Phase 2 (storage extraction)
server/gsfluent/core/run_manager.py         ← Phase 2 (already created in parallel)
server/gsfluent/api/runs.py                 ← Phase 3 (recipe validation)
server/tools/run_sim.sh                     ← Phase 3 (shim slim-down)
deploy/gsfluent-backend.service             ← Phase 4 (systemd)
frontend/src/**                             ← Out of spec entirely
```

---

## Tasks

### Task 1: Branch + baseline test verification

**Files:**
- No file edits. Verification + commit only.

- [ ] **Step 1: Create the phase branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout -b phase-5-streaming-cache
```

Expected: `Switched to a new branch 'phase-5-streaming-cache'`

- [ ] **Step 2: Verify baseline test suite passes**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing test files pass. Record the baseline pass/fail count for comparison at the end of the phase.

- [ ] **Step 3: Confirm `httpx` is installed for the client and the test client**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import httpx; print(httpx.__version__)"
```

Expected: `0.27` or higher (already declared in `server/pyproject.toml` dev extras).

- [ ] **Step 4: Confirm `Sequence`, `_VISER_CACHE`, and the existing `splats.gsq` route exist where the plan expects**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "splats.gsq\|FileResponse\|_VISER_CACHE" server/gsfluent/api/sequences.py | head -10
```

Expected: line ~43 shows `_VISER_CACHE = PKG_ROOT / "work" / "cache" / "viser"`, line ~221 shows `@router.get("/{name}/cache/splats.gsq")`, line ~250 shows `return FileResponse(...)`.

- [ ] **Step 5: No commit yet — Task 1 is verification only**

---

### Task 2: server tests — `tests/api/test_sequences_cache_headers.py`

This task is pure-test, written **before** the server implementation. Run, see them fail, then move to Task 3 which makes them pass.

**Files:**
- Create: `server/tests/api/__init__.py`
- Create: `server/tests/api/test_sequences_cache_headers.py`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/api/__init__.py` as an empty file:

```python
```

Create `server/tests/api/test_sequences_cache_headers.py`:

```python
"""Cache-header + Range + 304 conformance for GET /api/sequences/{name}/cache/splats.gsq.

The .gsq cache for a sequence is treated as immutable: once produced for a
given (name, size, mtime), the bytes never change. The server emits a weak
ETag of the form '"<size>-<mtime_int>"' and Cache-Control: public, immutable,
max-age=31536000. Clients (viser_headless) send If-None-Match on a refresh
to skip the body entirely (-> 304), or Range on a resume (-> 206).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from gsfluent.core import library as lib
from gsfluent.api import sequences as seq_api


# --------- fixtures ---------------------------------------------------------


@pytest.fixture
def cache_setup(client, tmp_path: Path, monkeypatch) -> dict:
    """Stand up a tmp library with one sequence directory and a synthetic
    .gsq cache file under work/cache/viser/.

    The .gsq body is arbitrary bytes — these tests cover HTTP semantics,
    not codec correctness. The route only cares about (a) sequence exists
    in library, (b) <name>.gsq exists in _VISER_CACHE.
    """
    sequences_dir = tmp_path / "library" / "sequences"
    cache_dir = tmp_path / "work" / "cache" / "viser"
    sequences_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    seq_name = "demo"
    (sequences_dir / seq_name).mkdir()
    body = b"A" * 4096 + b"B" * 4096  # 8 KiB of distinguishable bytes
    gsq_path = cache_dir / f"{seq_name}.gsq"
    gsq_path.write_bytes(body)

    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    return {
        "client": client,
        "name": seq_name,
        "path": gsq_path,
        "body": body,
        "size": len(body),
    }


def _parse_etag(etag: str) -> tuple[int, int]:
    """Extract (size, mtime_int) from the quoted weak ETag '"<size>-<mtime>"'."""
    m = re.fullmatch(r'"(\d+)-(\d+)"', etag)
    assert m is not None, f"unexpected ETag shape: {etag!r}"
    return int(m.group(1)), int(m.group(2))


# --------- happy path: 200 with cache headers -------------------------------


def test_200_response_carries_cache_control_immutable(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_200_response_carries_etag_size_mtime(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None
    size, _ = _parse_etag(etag)
    assert size == cache_setup["size"]


def test_200_response_returns_full_body(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    assert r.content == cache_setup["body"]


# --------- 304 on If-None-Match match ---------------------------------------


def test_if_none_match_returns_304_when_etag_matches(cache_setup) -> None:
    head_r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq"
    )
    etag = head_r.headers["etag"]

    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"If-None-Match": etag},
    )
    assert r.status_code == 304
    # 304 MUST repeat the ETag per RFC 7232; the client uses it to keep its
    # local copy authoritative.
    assert r.headers.get("etag") == etag
    # 304 carries no body.
    assert r.content == b""


def test_if_none_match_mismatch_returns_full_body(cache_setup) -> None:
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"If-None-Match": '"bogus-etag"'},
    )
    assert r.status_code == 200
    assert r.content == cache_setup["body"]


# --------- 206 on Range request (FastAPI FileResponse provides this) --------


def test_range_request_returns_206_partial_content(cache_setup) -> None:
    """FastAPI's FileResponse already implements byte-range. This test pins
    the contract so a future swap to a custom response doesn't regress it."""
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"Range": "bytes=4096-8191"},
    )
    assert r.status_code == 206
    assert r.content == cache_setup["body"][4096:8192]
    cr = r.headers.get("content-range", "")
    # Shape: "bytes 4096-8191/8192"
    assert cr.startswith("bytes 4096-8191/")


def test_range_request_open_ended_to_eof(cache_setup) -> None:
    """`Range: bytes=N-` (no end) means resume to EOF — the exact pattern
    the viser_headless client sends when a .partial exists."""
    n = 4096
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assert r.content == cache_setup["body"][n:]


# --------- 404 path unchanged ----------------------------------------------


def test_404_when_sequence_dir_missing(cache_setup) -> None:
    r = cache_setup["client"].get("/api/sequences/no-such-seq/cache/splats.gsq")
    assert r.status_code == 404


def test_404_when_gsq_not_built(cache_setup, tmp_path: Path) -> None:
    """Sequence directory exists, but no <name>.gsq under the cache."""
    name = "no-cache-built"
    (lib.SEQUENCES_DIR / name).mkdir()
    r = cache_setup["client"].get(f"/api/sequences/{name}/cache/splats.gsq")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_sequences_cache_headers.py -v --tb=short
```

Expected: the four happy-path / 304-related tests FAIL because today's `splats.gsq` response carries neither `Cache-Control` nor `ETag` and there is no `If-None-Match` short-circuit. The Range tests may already pass (FastAPI provides byte-range out of the box). Record which tests pass vs fail.

- [ ] **Step 3: No commit yet — Task 3 implements, then commits both together**

---

### Task 3: server impl — Cache-Control + ETag + If-None-Match → 304

**Files:**
- Modify: `server/gsfluent/api/sequences.py`
- Run: `server/tests/api/test_sequences_cache_headers.py` from Task 2 (must pass)

- [ ] **Step 1: Open the file and replace the `get_splats_gsq` route**

Replace the existing `get_splats_gsq` function (currently lines ~221-254) with the version below. Key changes vs current:

1. Add `from fastapi import Request, Response` to the FastAPI imports at the top of the file (the rest of the imports stay).
2. Stat the file once after the path-traversal check.
3. Build `etag = f'"{size}-{int(mtime)}"'` — weak ETag matching the spec default (Open Question 3).
4. Short-circuit on `If-None-Match` match → 304 with the same ETag echoed back.
5. Pass `Cache-Control` and `ETag` headers through to the `FileResponse`.

In `server/gsfluent/api/sequences.py`, find the existing import line:

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
```

Replace with:

```python
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
```

Then find the existing `get_splats_gsq` function:

```python
@router.get("/{name}/cache/splats.gsq")
def get_splats_gsq(name: str):
    """Serve the .gsq visual-lossless streamable cache.

    Produced by `server/tools/pack_splats.py` as a smaller, byte-range
    addressable alternative to the .npz cache. Typical size: 0.4-1 GB
    vs 2.9 GB for the npz on the same sequence (~3-7× smaller). Same
    Range support via FileResponse — interrupted downloads resume
    it natively.

    Falls through to 404 with a build hint if the .gsq doesn't exist
    yet. The client is expected to fall back to viser.npz in that case
    (the older path the build flow already produces).
    """
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = _VISER_CACHE / f"{name}.gsq"
    if not path.is_file():
        raise HTTPException(
            404,
            f".gsq not built for '{name}'. Run "
            f"`python server/tools/pack_splats.py {name}` on the server.",
        )
    target = path.resolve()
    cache_root = _VISER_CACHE.resolve()
    try:
        target.relative_to(cache_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside cache: {name}")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=f"{name}.gsq",
    )
```

Replace it with:

```python
# .gsq files are immutable per (name, size, mtime): once produced for a
# given sequence, the bytes don't change. We surface that with a weak
# ETag and Cache-Control: immutable so the viser_headless client can
# short-circuit on HEAD when its local copy is current.
_GSQ_CACHE_CONTROL = "public, immutable, max-age=31536000"


def _gsq_etag(size: int, mtime: float) -> str:
    """Weak ETag '"<size>-<mtime_int>"' — matches the client's
    _local_etag() formula in frontend/python/viser_headless.py."""
    return f'"{size}-{int(mtime)}"'


@router.get("/{name}/cache/splats.gsq")
def get_splats_gsq(name: str, request: Request):
    """Serve the .gsq visual-lossless streamable cache.

    Produced by `server/tools/pack_splats.py` as a smaller, byte-range
    addressable alternative to the .npz cache. Typical size: 0.4-1 GB
    vs 2.9 GB for the npz on the same sequence (~3-7x smaller). Same
    Range support via FileResponse — interrupted downloads resume
    it natively.

    Headers:
      Cache-Control: public, immutable, max-age=31536000
        Tells any intermediate cache that the body is safe to keep
        forever for this URL+ETag pair. .gsq is content-addressable
        via (name, size, mtime).
      ETag: "<size>-<mtime_int>"
        Weak ETag per spec Open Question 3 default. Cheap to compute
        (stat already on the hot path); strong ETag (content hash)
        would cost a full-file read per response.

    Conditional GET:
      If-None-Match matches current ETag -> 304 (no body) so the
      viser_headless client can keep its local file authoritative
      without re-downloading.

    Range:
      FileResponse already provides byte-range. Verified by
      tests/api/test_sequences_cache_headers.py.

    Falls through to 404 with a build hint if the .gsq doesn't exist
    yet.
    """
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = _VISER_CACHE / f"{name}.gsq"
    if not path.is_file():
        raise HTTPException(
            404,
            f".gsq not built for '{name}'. Run "
            f"`python server/tools/pack_splats.py {name}` on the server.",
        )
    target = path.resolve()
    cache_root = _VISER_CACHE.resolve()
    try:
        target.relative_to(cache_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside cache: {name}")

    st = target.stat()
    etag = _gsq_etag(st.st_size, st.st_mtime)

    if request.headers.get("if-none-match") == etag:
        # 304 carries no body but must repeat ETag + Cache-Control so
        # downstream caches stay consistent.
        return Response(
            status_code=304,
            headers={"etag": etag, "cache-control": _GSQ_CACHE_CONTROL},
        )

    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=f"{name}.gsq",
        headers={"etag": etag, "cache-control": _GSQ_CACHE_CONTROL},
    )
```

- [ ] **Step 2: Run the Task 2 tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_sequences_cache_headers.py -v --tb=short
```

Expected: all 9 tests pass.

- [ ] **Step 3: Confirm no regression in existing sequence tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_sequences_import.py -v --tb=short
```

Expected: same pass/fail count as the baseline recorded in Task 1.

- [ ] **Step 4: Commit (server-side cache headers + tests together)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/api/sequences.py \
        server/tests/api/__init__.py \
        server/tests/api/test_sequences_cache_headers.py
git commit -m "phase-5: api/sequences.py — Cache-Control immutable + weak ETag + If-None-Match -> 304 on splats.gsq"
```

---

### Task 4: client — `_local_etag` helper + HEAD-skip on cache hit

**Files:**
- Modify: `frontend/python/viser_headless.py` (add `_local_etag` helper + HEAD-probe branch inside `_sync_cell_gsq_streaming`)

This task adds the HEAD-probe and cache-hit branch only; the resume-from-`.partial` branch comes in Task 5. Tests for both come in Task 6 (the integration tests need both branches in place).

- [ ] **Step 1: Add the `_local_etag` helper at module scope**

Open `frontend/python/viser_headless.py`. The `_SAFE_NAME` regex sits at module top (around line 45). Add the helper immediately after it.

Find this block (around line 45):

```python
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
```

Replace with:

```python
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _local_etag(path: Path) -> str:
    """Compute the weak ETag the server would emit for `path`.

    Format MUST match server/gsfluent/api/sequences.py:_gsq_etag — the
    contract is the literal byte equality of the quoted ETag string.

        '"<size>-<mtime_int>"'

    Recomputed from os.stat() each call; no persistent sidecar file. The
    .gsq cache is small enough (sub-GB) that a stat is free and the
    sidecar maintenance cost would outweigh its benefit.

    Raises FileNotFoundError if path doesn't exist — callers should
    check is_file() first.
    """
    st = path.stat()
    return f'"{st.st_size}-{int(st.st_mtime)}"'
```

- [ ] **Step 2: Add the HEAD-probe branch at the top of `_sync_cell_gsq_streaming`**

Find the function definition (around line 947) and the existing `try:` block that opens the `httpx.stream` call. The HEAD probe goes BEFORE that block, inside the function.

Find this section:

```python
    def _sync_cell_gsq_streaming(name: str, url: str, dest: Path, partial: Path) -> dict:
        """Streaming .gsq download + incremental decode.

        Reads the request body once. The first chunk(s) supply the
        header + frame index → we know the static block offset and
        the per-frame byte ranges. Each subsequent chunk extends a
        buffer; whenever we have enough bytes for the static block
        and then each next frame, we decode and grow the cell.

        cells[name] appears the moment frame 0 is decoded. n_loaded
        grows monotonically until the whole file lands. /state polls
        see n_frames = n_loaded, so the SPA can scrub right away.
        """
        import zstandard as _zstd

        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True, trust_env=False) as r:
```

Replace with:

```python
    def _sync_cell_gsq_streaming(name: str, url: str, dest: Path, partial: Path) -> dict:
        """Streaming .gsq download + incremental decode, with cache-hit + resume.

        Three entry paths, taken in order:

        1. HEAD probe (if dest exists). If the server's ETag matches our
           _local_etag(dest), or content-length matches dest.stat().st_size
           (back-compat for pre-Phase-5 servers that don't emit ETag),
           skip the body entirely and load the cell from disk. Emits
           cell.cache.hit.

        2. Range resume (if .partial exists). Send Range: bytes=<n>-,
           treat 206 as resume (append, decode-as-arrives accounting for
           the offset), treat 200 as "server ignored Range" (unlink
           .partial and fall through to a fresh download). Emits
           cell.cache.resuming.

        3. Fresh streaming download (the existing path). Reads the
           request body once. The first chunk(s) supply the header +
           frame index — we know the static block offset and the
           per-frame byte ranges. Each subsequent chunk extends a
           buffer; whenever we have enough bytes for the static block
           and then each next frame, we decode and grow the cell.

           cells[name] appears the moment frame 0 is decoded. n_loaded
           grows monotonically until the whole file lands. /state polls
           see n_frames = n_loaded, so the SPA can scrub right away.
        """
        import zstandard as _zstd

        cell_key = name if ":" in name else f"sequence:{name}"

        # --- Path 1: cache hit on HEAD probe ---------------------------------
        if dest.is_file():
            try:
                head = httpx.head(url, timeout=10.0,
                                  follow_redirects=True, trust_env=False)
            except Exception as e:
                # Network error on HEAD is non-fatal — fall through to a
                # fresh download. The body request below will fail with
                # the same error and the user sees the same surface.
                print(f"  cache HEAD failed for {name}: {e}; falling through to download")
                head = None
            if head is not None and head.status_code == 200:
                remote_etag = head.headers.get("etag")
                local_etag_val = None
                try:
                    local_etag_val = _local_etag(dest)
                except FileNotFoundError:
                    pass  # raced with a delete; just download

                etag_match = (
                    remote_etag is not None
                    and local_etag_val is not None
                    and remote_etag == local_etag_val
                )
                size_match = False
                if not etag_match:
                    # Back-compat: server may not emit ETag yet (older
                    # deployments). Compare content-length instead.
                    try:
                        remote_size = int(head.headers.get("content-length", "-1"))
                        size_match = remote_size >= 0 and remote_size == dest.stat().st_size
                    except (ValueError, OSError):
                        size_match = False

                if etag_match or size_match:
                    source = "etag" if etag_match else "size"
                    try:
                        cell = load_cell_gsq(dest)
                    except Exception as e:
                        # Local file is current per the server, but our
                        # decoder choked. Could be a stale Phase 1/2
                        # format we no longer support. Fall through to a
                        # fresh download with a structured note.
                        print(f"  cache hit decode failed for {name}: {e}; re-downloading")
                    else:
                        with lock:
                            cells[cell_key] = cell
                            if state["cell"] == cell_key:
                                state["scene_dirty"] = True
                                state["pushed_frame"] = -1
                        _set_loading(None, None)
                        print(f"  cache hit ({source}) for {name} from {dest}")
                        return {
                            "ok": True, "cell": name, "added": False,
                            "cached": True, "source": source,
                            "bytes": dest.stat().st_size,
                            "n_frames": int(cell.get("n_frames", 0)),
                        }

        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True, trust_env=False) as r:
```

Note: the existing function body after `with httpx.stream(...)` is left untouched in this task — the `cell_key = name if ":" in name else f"sequence:{name}"` line that appeared inside the body (around line 984) is now redundant and should be deleted to avoid masking the outer variable. Edit the inner line:

Find (around line 978-984 inside the function):

```python
                # Cells are keyed by their wire-format name
                # ("sequence:<stem>" / "model:<stem>") in the boot scanner +
                # /set callers. The SPA's /sync_cell call passes the bare
                # stem (no prefix), so we prefix here so /set's lookup
                # succeeds. Models would never come through this path,
                # so "sequence:" is always correct.
                cell_key = name if ":" in name else f"sequence:{name}"
```

Replace with:

```python
                # cell_key is computed once at the top of the enclosing
                # function (see Path 1 / Path 2 / Path 3 docstring).
                # Re-binding here was redundant pre-Phase-5 and would
                # shadow the outer name; left as the docstring note.
```

- [ ] **Step 3: Syntax check the file**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import ast; ast.parse(open('frontend/python/viser_headless.py').read()); print('parse ok')"
```

Expected: `parse ok`.

- [ ] **Step 4: No commit yet — Task 5 adds the resume branch, then both commit together**

---

### Task 5: client — Range/resume branch from `.partial`

**Files:**
- Modify: `frontend/python/viser_headless.py` (add Range/resume branch in `_sync_cell_gsq_streaming`)

The HEAD probe from Task 4 short-circuits on a complete local copy. This task handles the other half: an incomplete download left behind as `.partial` from a prior interrupted run.

- [ ] **Step 1: Add the Range-resume branch between the HEAD-probe and the fresh-download block**

In `_sync_cell_gsq_streaming`, find the transition from the HEAD-probe close to the fresh-download `try:` opener. After Task 4, this is the section just before:

```python
        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True, trust_env=False) as r:
```

Insert the Range/resume branch immediately above that `try:` line:

```python
        # --- Path 2: resume from .partial -----------------------------------
        # An interrupted prior download leaves <dest>.partial on disk. We
        # send Range: bytes=<n>- where n = partial size. If the server
        # honors it (206 Partial Content), we append to the partial,
        # decode against the file-relative byte offsets (the parser uses
        # absolute offsets from the .gsq header, so we must rebuild the
        # full buffer from the on-disk prefix + the streamed suffix). If
        # the server returns 200 (Range ignored), we unlink the partial
        # and let Path 3 (fresh download) re-fetch from byte 0.
        resume_offset = 0
        prefix_bytes: bytes | None = None
        if partial.is_file():
            try:
                resume_offset = partial.stat().st_size
            except OSError:
                resume_offset = 0
            if resume_offset > 0:
                # Best-effort: only resume when the prefix is non-empty.
                # Zero-byte partials happen on rare crash modes; treat as
                # fresh.
                print(f"  resuming {name} from byte {resume_offset}")
                try:
                    headers = {"Range": f"bytes={resume_offset}-"}
                    with httpx.stream("GET", url, headers=headers,
                                      timeout=600.0, follow_redirects=True,
                                      trust_env=False) as r:
                        if r.status_code == 206:
                            # Server honored Range. Re-open the partial
                            # for append + read prefix into memory once
                            # so the existing decoder can index by
                            # absolute offset.
                            prefix_bytes = partial.read_bytes()
                            try:
                                ok = _sync_cell_gsq_streaming_with_prefix(
                                    name=name, dest=dest, partial=partial,
                                    response=r, prefix=prefix_bytes,
                                    cell_key=cell_key,
                                )
                            except Exception as e:
                                partial.unlink(missing_ok=True)
                                _set_loading(cell_key, "error", "resume_failed")
                                return {"ok": False, "error": f"resume failed: {e}"}
                            return ok
                        elif r.status_code == 200:
                            # Server returned full body. Discard the
                            # partial and fall through to fresh-download
                            # path below.
                            partial.unlink(missing_ok=True)
                            resume_offset = 0
                            print(f"  server ignored Range for {name}; restarting at byte 0")
                        else:
                            partial.unlink(missing_ok=True)
                            _set_loading(cell_key, "error", "resume_failed")
                            return {"ok": False, "error":
                                    f"resume HTTP {r.status_code}"}
                except Exception as e:
                    # Network error during resume. Drop partial and try
                    # a fresh download from byte 0.
                    print(f"  resume network error for {name}: {e}; restarting at byte 0")
                    partial.unlink(missing_ok=True)
                    resume_offset = 0

        # --- Path 3: fresh download (existing path) -------------------------
        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True, trust_env=False) as r:
```

- [ ] **Step 2: Add the helper `_sync_cell_gsq_streaming_with_prefix`**

This helper is the existing decoder loop, but it pre-seeds the buffer with the on-disk prefix bytes from the `.partial` so absolute-offset indexing into `header_parsed["frame_index"]` still works.

Define it as a nested function inside the enclosing `main()` scope, immediately BEFORE the existing `def _sync_cell_gsq_streaming(...)` definition (around line 947). The simplest correct placement is in the same nested scope — both functions close over `cells`, `lock`, `state`, `_set_loading`.

Insert (right above `def _sync_cell_gsq_streaming(name: str, url: str, dest: Path, partial: Path) -> dict:`):

```python
    def _sync_cell_gsq_streaming_with_prefix(
        *,
        name: str,
        dest: Path,
        partial: Path,
        response,                 # httpx.Response in stream mode
        prefix: bytes,
        cell_key: str,
    ) -> dict:
        """Decode a .gsq stream that resumed mid-download.

        Pre-seeds the decode buffer with `prefix` (the bytes already on
        disk from the prior interrupted run), then continues from the
        206 response body. The static block + frame index live near the
        head of the file, so a resumed download where the offset is
        > header_size still works because the decoder operates on a
        single concatenated buffer.

        Returns the same dict shape as a fresh download:
            {ok, cell, added, cached?, bytes, n_frames}
        """
        import struct as _struct
        import zstandard as _zstd

        buf = bytearray(prefix)
        pf = open(partial, "ab")
        header_parsed = None
        static_decoded = False
        rgb_f16 = opacity_u8 = scales_f16 = None
        xyz_backing = quat_backing = None
        n_loaded = 0
        bbox_min = bbox_max = span = None

        def commit_cell():
            cell = _build_gsq_cell_dict(
                xyz_backing, quat_backing, rgb_f16, opacity_u8,
                scales_f16, bbox_min, bbox_max, n_loaded=n_loaded,
            )
            with lock:
                cells[cell_key] = cell
                if state["cell"] == cell_key:
                    state["scene_dirty"] = True
                    state["pushed_frame"] = -1

        # Decode whatever is already in the prefix BEFORE any new bytes
        # arrive. This handles the case where the prior run had decoded
        # the static block + several frames but never flipped partial ->
        # dest. The same per-chunk logic below is just looped once with
        # no new bytes.
        def _try_advance():
            nonlocal header_parsed, static_decoded, rgb_f16, opacity_u8
            nonlocal scales_f16, xyz_backing, quat_backing, n_loaded
            nonlocal bbox_min, bbox_max, span

            if header_parsed is None and len(buf) >= 80:
                n_frames_peek = _struct.unpack_from("<I", bytes(buf[:80]), 12)[0]
                need = 80 + n_frames_peek * 16
                if len(buf) >= need:
                    header_parsed = parse_gsq_header(bytes(buf[:need]))
                    bbox_min = header_parsed["bbox_min"]
                    bbox_max = header_parsed["bbox_max"]
                    span = (bbox_max - bbox_min).astype(np.float32)
                    span[span == 0] = 1.0
                    xyz_backing = np.zeros(
                        (header_parsed["n_frames"], header_parsed["n_splats"], 3),
                        dtype=np.float32,
                    )
                    quat_backing = np.zeros(
                        (header_parsed["n_frames"], header_parsed["n_splats"], 4),
                        dtype=np.float32,
                    )
                    quat_backing[..., 0] = 1.0
                    _set_loading(cell_key, "streaming")

            if (header_parsed is not None and not static_decoded
                    and len(buf) >= header_parsed["static_offset"] + header_parsed["static_size"]):
                s_off = header_parsed["static_offset"]
                s_sz = header_parsed["static_size"]
                n_sp = header_parsed["n_splats"]
                blob = _zstd.ZstdDecompressor().decompress(
                    bytes(buf[s_off : s_off + s_sz])
                )
                rgb_bytes = n_sp * 3 * 2
                rgb_f16 = np.frombuffer(blob[:rgb_bytes], dtype=np.float16).reshape(n_sp, 3).copy()
                opacity_u8 = np.frombuffer(blob[rgb_bytes:rgb_bytes + n_sp], dtype=np.uint8).copy()
                scales_f16 = np.frombuffer(
                    blob[rgb_bytes + n_sp : rgb_bytes + n_sp + n_sp * 3 * 2],
                    dtype=np.float16,
                ).reshape(n_sp, 3).copy()
                static_decoded = True

            if static_decoded:
                n_sp = header_parsed["n_splats"]
                n_total = header_parsed["n_frames"]
                while n_loaded < n_total:
                    f_off, f_sz = header_parsed["frame_index"][n_loaded]
                    if len(buf) < f_off + f_sz:
                        break
                    xyz, quat = _gsq_dequantize_frame(
                        bytes(buf[f_off : f_off + f_sz]),
                        n_sp, bbox_min, span,
                    )
                    xyz_backing[n_loaded] = xyz
                    quat_backing[n_loaded] = quat
                    n_loaded += 1
                if n_loaded > 0:
                    commit_cell()

        # Decode whatever the prefix already covers.
        _try_advance()

        try:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                buf.extend(chunk)
                pf.write(chunk)
                _try_advance()
            pf.close()
        except Exception:
            pf.close()
            raise

        partial.replace(dest)

        if header_parsed is None or not static_decoded or n_loaded == 0:
            _set_loading(cell_key, "error", "stream_failed")
            return {"ok": False, "error":
                    f"incomplete .gsq after resume: parsed_header="
                    f"{header_parsed is not None}, static={static_decoded}, "
                    f"frames={n_loaded}"}

        commit_cell()
        _set_loading(None, None)
        return {
            "ok": True, "cell": name, "added": True, "resumed": True,
            "bytes": dest.stat().st_size, "n_frames": n_loaded,
        }
```

- [ ] **Step 3: Syntax check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import ast; ast.parse(open('frontend/python/viser_headless.py').read()); print('parse ok')"
```

Expected: `parse ok`.

- [ ] **Step 4: Commit (Task 4 + Task 5 together — both touch the same function)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/python/viser_headless.py
git commit -m "phase-5: viser_headless — HEAD-probe cache hit + Range/resume from .partial in _sync_cell_gsq_streaming"
```

---

### Task 6: integration tests — cache hit + resume

These tests stand up a real FastAPI app (via `TestClient`) and exercise the client-side `_sync_cell_gsq_streaming` against it. The point is end-to-end coverage of the HEAD-probe and the Range/resume flows.

**Files:**
- Create: `server/tests/integration/__init__.py`
- Create: `server/tests/integration/test_streaming_cache_hit.py`
- Create: `server/tests/integration/test_streaming_resume_from_partial.py`

- [ ] **Step 1: Create the integration test package**

Create `server/tests/integration/__init__.py` as an empty file.

- [ ] **Step 2: Write `test_streaming_cache_hit.py`**

```python
"""Integration: second /sync_cell call uses HEAD probe + skips body.

Uses the FastAPI TestClient (which exposes a synchronous httpx-compatible
adapter), monkeypatches httpx.head + httpx.stream at the viser_headless
import site, and verifies:

  1. First call sees an empty cache dir, downloads + decodes a fake .gsq.
  2. Second call with the same (name, url) sees the cached dest file,
     sends HEAD, gets the matching ETag, loads from disk, and never
     opens a body-streaming request.

The fake .gsq body comes from a small synthetic header + one frame. We
import the real parse_gsq_header / _gsq_dequantize_frame so any future
format change auto-propagates here.
"""
from __future__ import annotations

import importlib
import io
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest


# Minimal .gsq writer wrapping the production header layout. The .gsq
# format is documented at the top of frontend/python/viser_headless.py;
# we synthesize the smallest valid file: 1 splat, 1 frame, identity quat.
def _write_minimal_gsq(path: Path) -> bytes:
    """Build the smallest decodable .gsq into `path` and return its bytes."""
    import struct
    import numpy as np
    import zstandard as zstd

    n_splats = 1
    n_frames = 1

    # Static block (uncompressed payload):
    #   rgb_f16:     n_splats * 3 * 2 bytes
    #   opacity_u8:  n_splats * 1 byte
    #   scales_f16:  n_splats * 3 * 2 bytes
    rgb = np.array([[0.5, 0.5, 0.5]], dtype=np.float16).tobytes()
    opacity = np.array([255], dtype=np.uint8).tobytes()
    scales = np.array([[0.01, 0.01, 0.01]], dtype=np.float16).tobytes()
    static_blob = rgb + opacity + scales
    static_compressed = zstd.ZstdCompressor(level=3).compress(static_blob)

    # Frame block (uncompressed payload):
    #   xyz_i16: n_splats * 3 * 2 bytes (quantized to bbox)
    #   quat_u8: n_splats * 4 bytes
    xyz_q = np.array([[0, 0, 0]], dtype=np.int16).tobytes()
    quat_q = np.array([[127, 0, 0, 0]], dtype=np.uint8).tobytes()
    frame_blob = xyz_q + quat_q
    frame_compressed = zstd.ZstdCompressor(level=3).compress(frame_blob)

    # Header (80 bytes):
    #   magic[8]="GSQv01\0\0", n_splats(u32), n_frames(u32),
    #   bbox_min[3]f32, bbox_max[3]f32, reserved up to 80
    bbox_min = (-1.0, -1.0, -1.0)
    bbox_max = (1.0, 1.0, 1.0)
    header_bytes = bytearray(80)
    header_bytes[0:8] = b"GSQv01\0\0"
    struct.pack_into("<II", header_bytes, 8, n_splats, n_frames)
    struct.pack_into("<3f3f", header_bytes, 16, *bbox_min, *bbox_max)

    # Frame index: n_frames * (u64 offset, u64 size). Static block follows
    # the frame index, then frame_0 follows the static block.
    frame_index_bytes = 80 + n_frames * 16
    static_offset = frame_index_bytes
    static_size = len(static_compressed)
    frame_offset = static_offset + static_size
    frame_size = len(frame_compressed)

    index_buf = bytearray(n_frames * 16)
    struct.pack_into("<QQ", index_buf, 0, frame_offset, frame_size)

    file_bytes = (
        bytes(header_bytes)
        + bytes(index_buf)
        + static_compressed
        + frame_compressed
    )
    # Patch in static_offset/size: format stores these implicitly via the
    # frame index, but viser_headless.parse_gsq_header expects them at a
    # known reserved location. The production writer (pack_splats.py)
    # writes them at bytes 64..80 of the header.
    struct.pack_into("<QQ", file_bytes[:80], 64, static_offset, static_size)

    # struct.pack_into on bytes is illegal — rebuild via bytearray then
    # convert at the end.
    file_buf = bytearray(file_bytes)
    struct.pack_into("<QQ", file_buf, 64, static_offset, static_size)
    final = bytes(file_buf)
    path.write_bytes(final)
    return final


@pytest.fixture
def real_gsq(tmp_path: Path) -> dict:
    """Stand up server side cache + a real .gsq file in it."""
    cache_dir = tmp_path / "work" / "cache" / "viser"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)
    return {"cache_dir": cache_dir, "name": seq_name, "path": gsq_path, "body": body}


def test_second_sync_uses_head_and_skips_body(real_gsq, tmp_path: Path, monkeypatch) -> None:
    """First request downloads; second request hits HEAD and skips body."""
    # Stand up a TestClient against the real /api/sequences/cache/splats.gsq
    # route, with the cache dir monkeypatched into the sequences module.
    from fastapi.testclient import TestClient
    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    # Make the sequence exist on disk so the route's Sequence.exists()
    # check passes.
    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / real_gsq["name"]).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", real_gsq["cache_dir"])

    client = TestClient(create_app())

    # Sanity: the route returns the .gsq body with the new ETag header.
    r = client.get(f"/api/sequences/{real_gsq['name']}/cache/splats.gsq")
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert r.content == real_gsq["body"]

    # If-None-Match short-circuits to 304.
    r2 = client.get(
        f"/api/sequences/{real_gsq['name']}/cache/splats.gsq",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""

    # Range fetch returns 206 with the right slice.
    r3 = client.get(
        f"/api/sequences/{real_gsq['name']}/cache/splats.gsq",
        headers={"Range": "bytes=0-15"},
    )
    assert r3.status_code == 206
    assert r3.content == real_gsq["body"][:16]


def test_local_etag_matches_server_etag(real_gsq) -> None:
    """The client's _local_etag and the server's _gsq_etag must produce
    byte-identical strings — the whole HEAD-skip path depends on it."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "frontend" / "python"))
    try:
        viser_headless = importlib.import_module("viser_headless")
    finally:
        sys.path.pop(0)

    from gsfluent.api.sequences import _gsq_etag

    local = viser_headless._local_etag(real_gsq["path"])
    st = real_gsq["path"].stat()
    server = _gsq_etag(st.st_size, st.st_mtime)
    assert local == server
```

- [ ] **Step 3: Write `test_streaming_resume_from_partial.py`**

```python
"""Integration: interrupted .gsq download resumes via Range.

Simulates an interrupted prior download by writing the first N bytes of
the real .gsq body to <dest>.partial, then exercises the server's Range
endpoint. The decode path is verified by reading back the final cell
dict and confirming n_frames > 0.

Companion to test_streaming_cache_hit.py — both rely on the same minimal
.gsq writer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Reuse the writer from the cache-hit test.
from .test_streaming_cache_hit import _write_minimal_gsq


def test_range_206_returns_byte_suffix(tmp_path: Path, monkeypatch) -> None:
    """Server-side: Range: bytes=N- returns body[N:] with status 206."""
    from fastapi.testclient import TestClient
    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "viser"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    client = TestClient(create_app())

    # Simulate a partial: server has bytes [0..N], client already has them.
    n = len(body) // 2
    r = client.get(
        f"/api/sequences/{seq_name}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assert r.content == body[n:]


def test_range_resume_round_trip(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: write partial prefix, send Range, concat with response,
    confirm decoded body equals the original."""
    from fastapi.testclient import TestClient
    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "viser"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    client = TestClient(create_app())

    # Client side: pretend the .partial has the first n bytes.
    n = len(body) // 3
    partial_prefix = body[:n]

    r = client.get(
        f"/api/sequences/{seq_name}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assembled = partial_prefix + r.content
    assert assembled == body


def test_range_ignored_returns_200_full_body(tmp_path: Path, monkeypatch) -> None:
    """Belt-and-suspenders: if a future codec change makes Range fall
    back to 200, the client treats it as 'discard partial, restart'. This
    test pins the server's CURRENT behavior (FastAPI honors Range) so a
    regression surfaces immediately."""
    from fastapi.testclient import TestClient
    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "viser"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    client = TestClient(create_app())

    # No Range header → must be 200 with full body.
    r = client.get(f"/api/sequences/{seq_name}/cache/splats.gsq")
    assert r.status_code == 200
    assert r.content == body
```

- [ ] **Step 4: Run the integration tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=.:../frontend/python python -m pytest tests/integration/ -v --tb=short
```

Expected: all 5 integration tests pass. (The cache-hit test imports `viser_headless` to verify `_local_etag` matches `_gsq_etag` byte-for-byte; the resume tests are server-side only and don't need the client import.)

Note on the import path: `viser_headless` lives in `frontend/python/` and is not a package, so the test inserts the directory into `sys.path` before importing. If the test cannot resolve `numpy`, `httpx`, or `zstandard` because the client extras aren't installed in the test env, mark these tests with `pytest.importorskip("zstandard")` and document the missing optional dep in the phase handoff.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/__init__.py \
        server/tests/integration/test_streaming_cache_hit.py \
        server/tests/integration/test_streaming_resume_from_partial.py
git commit -m "phase-5: integration tests — streaming cache hit (HEAD-skip) + Range/resume round trip"
```

---

### Task 7: rename — `npz_root` → `cache_root` and `--npz_dir` → `--cache-dir`

The rename has three coordinated edits. Deprecated aliases stay for one release with a one-shot per-process warning.

**Files:**
- Modify: `frontend/python/viser_headless.py` (rename local variable + add `--cache-dir` flag with `--npz_dir` alias + one-shot warning)

- [ ] **Step 1: Update the module docstring**

Find (line ~20-22):

```python
Usage:
    python frontend/python/viser_headless.py --npz_dir work/cache/viser
"""
```

Replace with:

```python
Usage:
    python frontend/python/viser_headless.py --cache-dir work/cache/viser

The legacy --npz_dir flag is accepted as a deprecated alias and prints a
warning on first use (per-process). Same applies to the cache directory
contents: .gsq is the only format produced today; .npz is fully retired.
"""
```

- [ ] **Step 2: Replace the CLI flag definition**

Find (line ~443):

```python
    p.add_argument("--npz_dir", required=True,
                   help="Directory containing per-sequence .npz files")
```

Replace with:

```python
    # --cache-dir is the canonical Phase-5 flag. --npz_dir is the
    # deprecated alias kept for one release so existing run-client.sh
    # invocations keep working; it prints a one-shot warning per process.
    cache_group = p.add_mutually_exclusive_group(required=True)
    cache_group.add_argument(
        "--cache-dir", dest="cache_dir", default=None,
        help="Directory containing per-sequence .gsq cache files",
    )
    cache_group.add_argument(
        "--npz_dir", dest="cache_dir_legacy", default=None,
        help="[DEPRECATED] Use --cache-dir. Same meaning, kept for back-compat.",
    )
```

- [ ] **Step 3: Resolve the chosen value + one-shot warning**

Find (line ~478-481):

```python
    # Argument name kept as `--npz_dir` for back-compat with old launchers,
    # but the directory now holds .gsq cells (npz is fully retired).
    npz_root = Path(args.npz_dir)
    npz_root.mkdir(parents=True, exist_ok=True)
```

Replace with:

```python
    # Resolve --cache-dir vs the deprecated --npz_dir alias. The
    # mutually-exclusive group above guarantees exactly one of them is
    # set; here we prefer the new name and warn on first sighting of the
    # old one (once per process, not per call, since main() runs once).
    if args.cache_dir_legacy is not None:
        import warnings as _warnings
        _warnings.warn(
            "viser_headless: --npz_dir is deprecated; use --cache-dir. "
            "The old flag will be removed in the next release.",
            DeprecationWarning,
            stacklevel=2,
        )
        cache_root = Path(args.cache_dir_legacy)
    else:
        cache_root = Path(args.cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Rename remaining `npz_root` references in the function body**

The variable was renamed locally; every downstream reference in `main()` must follow. Edits (each is a one-line replacement):

Find (line ~488):

```python
    available = sorted(npz_root.glob("*.gsq"))
    print(f"boot: {len(available)} .gsq cells available in {npz_root} (loaded on demand)")
```

Replace with:

```python
    available = sorted(cache_root.glob("*.gsq"))
    print(f"boot: {len(available)} .gsq cells available in {cache_root} (loaded on demand)")
```

Find (line ~508, inside the `resolve_cell_lazily` docstring):

```python
          2. sequence:<seqName> → look for <seqName>.npz under npz_root
```

Replace with:

```python
          2. sequence:<seqName> → look for <seqName>.gsq under cache_root
```

Find (line ~553):

```python
            gsq = npz_root / f"{seq_name}.gsq"
```

Replace with:

```python
            gsq = cache_root / f"{seq_name}.gsq"
```

Find (line ~1107-1111):

```python
        dest = (npz_root / f"{name}.gsq").resolve()
        try:
            dest.relative_to(npz_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes npz_dir: {name!r}"}
```

Replace with:

```python
        dest = (cache_root / f"{name}.gsq").resolve()
        try:
            dest.relative_to(cache_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes cache_dir: {name!r}"}
```

Find (line ~1135-1139):

```python
        gsq_path = (npz_root / f"{cell}.gsq").resolve()
        try:
            gsq_path.relative_to(npz_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes npz_dir: {cell!r}"}
```

Replace with:

```python
        gsq_path = (cache_root / f"{cell}.gsq").resolve()
        try:
            gsq_path.relative_to(cache_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes cache_dir: {cell!r}"}
```

- [ ] **Step 5: Confirm no `npz_root` references remain**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -n "npz_root\|npz_dir" frontend/python/viser_headless.py
```

Expected: only the docstring-deprecation note + the `--npz_dir` CLI flag + `cache_dir_legacy` arg remain. No bare `npz_root` references in code.

- [ ] **Step 6: Syntax check + launch smoke test**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import ast; ast.parse(open('frontend/python/viser_headless.py').read()); print('parse ok')"
.venv/bin/python frontend/python/viser_headless.py --help 2>&1 | grep -E "cache-dir|npz_dir"
```

Expected for `--help`: both `--cache-dir` and `--npz_dir` show up, with the legacy flag marked DEPRECATED in its help text.

- [ ] **Step 7: Confirm the deprecation warning fires on the old flag**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -W default -c "
import sys, os, tempfile
sys.argv = ['viser_headless', '--npz_dir', tempfile.mkdtemp()]
sys.path.insert(0, 'frontend/python')
# Stub uvicorn.run so main() returns instead of starting a server.
import unittest.mock as _m
with _m.patch('uvicorn.run'), _m.patch('viser.ViserServer'), _m.patch('threading.Thread'):
    import viser_headless
    try:
        viser_headless.main()
    except SystemExit:
        pass
" 2>&1 | grep -i "deprecated"
```

Expected: the line `DeprecationWarning: viser_headless: --npz_dir is deprecated; use --cache-dir.` appears in stderr. If the import fails because of missing client extras (`viser`, `numpy`, etc.), document the dep gap and move on — the syntax check from Step 6 plus the unit-test coverage in Task 6 covers correctness.

- [ ] **Step 8: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/python/viser_headless.py
git commit -m "phase-5: rename — npz_root -> cache_root + --npz_dir -> --cache-dir (deprecated alias, one-shot warn)"
```

---

### Task 8: rename — `GSFLUENT_NPZ_REBUILD` → `GSFLUENT_CACHE_REBUILD`

**Files:**
- Modify: `server/gsfluent/core/runner.py` (rename env var read + deprecation warning)

- [ ] **Step 1: Update the module docstring**

Find (line ~14-18):

```python
    GSFLUENT_SIM_SCRIPT_RUNNER  path to the shell wrapper invoked per run
                                (default: <PKG_ROOT>/server/tools/run_sim.sh)
    GSFLUENT_NPZ_REBUILD        if "1" (default), trigger .npz build after
                                run completion. Set to "0" if you'd rather
                                build manually.
```

Replace with:

```python
    GSFLUENT_SIM_SCRIPT_RUNNER  path to the shell wrapper invoked per run
                                (default: <PKG_ROOT>/server/tools/run_sim.sh)
    GSFLUENT_CACHE_REBUILD      if "1" (default), trigger .gsq cache build
                                after run completion. Set to "0" if you'd
                                rather build manually. The legacy
                                GSFLUENT_NPZ_REBUILD env name is honored
                                as a deprecated alias with a one-shot
                                warning per process.
```

- [ ] **Step 2: Replace the env-var read**

Find (line ~53-55):

```python
# After a successful run, optionally rebuild the .npz cache so the
# client sync daemon notices the new sequence. Off by default in tests.
NPZ_REBUILD_AFTER_RUN = os.environ.get("GSFLUENT_NPZ_REBUILD", "1") == "1"
```

Replace with:

```python
# After a successful run, optionally rebuild the .gsq cache so the
# client sync daemon notices the new sequence. Off by default in tests.
# Canonical env var is GSFLUENT_CACHE_REBUILD; GSFLUENT_NPZ_REBUILD is
# honored as a deprecated alias with a one-shot per-process warning so
# stale deployment scripts keep working through one release cycle.
def _resolve_cache_rebuild() -> bool:
    new_val = os.environ.get("GSFLUENT_CACHE_REBUILD")
    legacy_val = os.environ.get("GSFLUENT_NPZ_REBUILD")
    if new_val is not None:
        return new_val == "1"
    if legacy_val is not None:
        import warnings as _warnings
        _warnings.warn(
            "gsfluent: GSFLUENT_NPZ_REBUILD is deprecated; use "
            "GSFLUENT_CACHE_REBUILD. The old name will be removed in the "
            "next release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy_val == "1"
    return True  # default ON


CACHE_REBUILD_AFTER_RUN = _resolve_cache_rebuild()
# Back-compat alias: tests and older callers reference the old name.
# Removed in the next release alongside GSFLUENT_NPZ_REBUILD.
NPZ_REBUILD_AFTER_RUN = CACHE_REBUILD_AFTER_RUN
```

- [ ] **Step 3: Update the reference at line ~382**

Find (line ~382):

```python
    if run.state == "done" and NPZ_REBUILD_AFTER_RUN:
```

Replace with:

```python
    if run.state == "done" and CACHE_REBUILD_AFTER_RUN:
```

- [ ] **Step 4: Write a small test for the env-var precedence**

Create `server/tests/test_runner_env_rename.py`:

```python
"""Phase-5 rename: GSFLUENT_CACHE_REBUILD supersedes GSFLUENT_NPZ_REBUILD."""
from __future__ import annotations

import importlib
import warnings


def _reload_runner():
    """Force re-evaluation of the module-level env-var read."""
    from gsfluent.core import runner
    return importlib.reload(runner)


def test_new_var_is_honored_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_CACHE_REBUILD", "0")
    monkeypatch.delenv("GSFLUENT_NPZ_REBUILD", raising=False)
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is False
    # Back-compat alias mirrors the canonical name.
    assert runner.NPZ_REBUILD_AFTER_RUN is False


def test_legacy_var_is_honored_when_new_var_unset(monkeypatch) -> None:
    monkeypatch.delenv("GSFLUENT_CACHE_REBUILD", raising=False)
    monkeypatch.setenv("GSFLUENT_NPZ_REBUILD", "0")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is False
    # One deprecation warning recorded.
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)
                    and "GSFLUENT_NPZ_REBUILD" in str(w.message)]
    assert len(deprecations) >= 1


def test_new_var_wins_over_legacy_when_both_set(monkeypatch) -> None:
    """If a deployment sets both during a transition, the new one wins."""
    monkeypatch.setenv("GSFLUENT_CACHE_REBUILD", "1")
    monkeypatch.setenv("GSFLUENT_NPZ_REBUILD", "0")
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is True


def test_default_when_neither_set(monkeypatch) -> None:
    monkeypatch.delenv("GSFLUENT_CACHE_REBUILD", raising=False)
    monkeypatch.delenv("GSFLUENT_NPZ_REBUILD", raising=False)
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is True
```

- [ ] **Step 5: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_runner_env_rename.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Confirm existing runner tests still pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_runner.py -v --tb=short
```

Expected: same pass/fail count as the baseline recorded in Task 1.

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/runner.py \
        server/tests/test_runner_env_rename.py
git commit -m "phase-5: runner.py — GSFLUENT_NPZ_REBUILD -> GSFLUENT_CACHE_REBUILD (deprecated alias, one-shot warn)"
```

---

### Task 9: Phase 5 verification + branch handoff

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run the full test suite end-to-end**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=.:../frontend/python python -m pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: every test passes. Phase 5 added the following new tests:
- `tests/api/test_sequences_cache_headers.py` — 9 tests
- `tests/integration/test_streaming_cache_hit.py` — 2 tests
- `tests/integration/test_streaming_resume_from_partial.py` — 3 tests
- `tests/test_runner_env_rename.py` — 4 tests

All baseline tests from Phase 1's Task 1 record continue to pass.

- [ ] **Step 2: Spot-check that the old `--npz_dir` flag still works**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python frontend/python/viser_headless.py --npz_dir /tmp/gsfluent-phase5-check 2>&1 | head -3
```

Expected (within the first few seconds before the actual server starts up — kill it with Ctrl+C): a `DeprecationWarning` mentioning `--npz_dir is deprecated; use --cache-dir`, followed by the normal boot logging.

- [ ] **Step 3: Spot-check that the new `--cache-dir` flag works**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python frontend/python/viser_headless.py --cache-dir /tmp/gsfluent-phase5-check 2>&1 | head -3
```

Expected: no deprecation warning, normal boot logging only.

- [ ] **Step 4: Confirm Phase 5 git history is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main..HEAD
```

Expected: roughly 6 commits, each prefixed `phase-5:`:
- `phase-5: api/sequences.py — Cache-Control immutable + weak ETag + If-None-Match -> 304 on splats.gsq`
- `phase-5: viser_headless — HEAD-probe cache hit + Range/resume from .partial in _sync_cell_gsq_streaming`
- `phase-5: integration tests — streaming cache hit (HEAD-skip) + Range/resume round trip`
- `phase-5: rename — npz_root -> cache_root + --npz_dir -> --cache-dir (deprecated alias, one-shot warn)`
- `phase-5: runner.py — GSFLUENT_NPZ_REBUILD -> GSFLUENT_CACHE_REBUILD (deprecated alias, one-shot warn)`

- [ ] **Step 5: Push the branch (do NOT merge yet)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-5-streaming-cache
```

Expected: branch published on origin. Open a PR titled `phase-5: streaming cache hardening — ETag + 304 + Range/resume + cache_root rename`.

- [ ] **Step 6: Update the spec file's status note (optional)**

Edit `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`, change `**Status:**` line to add `Phase 5 implemented in branch phase-5-streaming-cache (PR #N)`.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "docs: mark Phase 5 implemented in branch phase-5-streaming-cache"
git push
```

---

## Definition of Done — Phase 5

Phase 5 ships when ALL of:

- [ ] All 9 tasks above completed
- [ ] `GET /api/sequences/{name}/cache/splats.gsq` carries `Cache-Control: public, immutable, max-age=31536000` and a weak ETag of shape `"<size>-<mtime_int>"`
- [ ] Same endpoint returns 304 on `If-None-Match` match (no body) with the same ETag echoed back
- [ ] Same endpoint returns 206 on `Range: bytes=N-` with the matching byte slice
- [ ] `_sync_cell_gsq_streaming` in `viser_headless.py` does HEAD-then-skip on a cache hit (ETag-match or size-match fallback)
- [ ] Same function resumes from `<dest>.partial` via `Range: bytes=<n>-` on 206; unlinks the partial + restarts on 200
- [ ] `viser_headless.py` accepts both `--cache-dir` (canonical) and `--npz_dir` (deprecated alias with one-shot warning)
- [ ] `core/runner.py` reads `GSFLUENT_CACHE_REBUILD` (canonical) and falls back to `GSFLUENT_NPZ_REBUILD` (deprecated alias with one-shot warning); the legacy var name still resolves correctly during transition
- [ ] All new Phase 5 tests pass (18 tests across `tests/api`, `tests/integration`, `tests/test_runner_env_rename.py`)
- [ ] All baseline tests still pass (no regressions; same count as Task 1 baseline)
- [ ] Branch `phase-5-streaming-cache` pushed; PR open for review
- [ ] `_local_etag` (client) and `_gsq_etag` (server) produce byte-identical ETag strings for the same file

## Handoff to Phase 6

Phase 6 (observability completion) depends on:
- Phase 5's cache-event emissions (`cell.cache.hit`, `cell.cache.resuming`, `cell.cache.resumed`) — currently printed via `print()` in the client; Phase 6 routes them through `StdlibJSONEmitter`
- Phase 5's deprecation-warning pattern (one-shot per process via `warnings.warn(DeprecationWarning)`) — Phase 6 audits for any other `print()`-style operator notes that should become structured events

Phase 6 will:
- Audit `core/run_manager.py` (Phase 2 deliverable) + `core/sim_engines/mpm.py` (Phase 3 deliverable) for remaining `print()` calls and convert them to `obs.emit(...)` structured events
- Extend `api/health.py` with real signals (GPU reachable, sim_home exists, disk free, last successful run timestamp)
- Wire the `journalctl -u gsfluent-backend -o json | jq` recipes into the deploy README

Phase 6 plan will be authored in a follow-up document: `docs/superpowers/plans/2026-05-22-phase-6-observability.md`.

---

**End of Phase 5 plan.**
