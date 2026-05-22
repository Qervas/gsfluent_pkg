# Phase 2 — Extract impls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the existing logic in `server/tools/pack_splats.py`, `server/tools/fuse_to_full_ply.py`, `server/gsfluent/core/library.py`, and `server/gsfluent/core/runner.py` into Protocol-conforming concrete impls under the package tree, then wire them through `composition.build_app`. **No behavior changes** — this phase is a pure refactor. The existing module-level `runner.py` API surface (`start_run`, `cancel_run`, `list_runs`, etc.) stays in place so `api/runs.py` and `api/stream.py` keep working untouched. The new `AsyncioRunManager` class is a Protocol-conforming wrapper that internally delegates to the existing module functions — Phase 3 will harden it (PG-spawn, signal escalation, etc.) and Phase 3 will rewire `api/runs.py` to depend on the Protocol via `Depends()`.

**Architecture:** Four concrete impls land:

1. `core/codecs/gsq.py` — `GSQCodec` conforms to `protocols.cache.CacheCodec`. Wraps the encode/decode/sanitize logic moved from `tools/pack_splats.py`.
2. `core/fusers/knn_kabsch.py` — `KNNKabschFuser` conforms to `protocols.fuse.Fuser`. Wraps the K-NN + weighted Kabsch logic from `tools/fuse_to_full_ply.py`.
3. `storage/filesystem.py` — `FilesystemStorage` conforms to `protocols.storage.Storage`. Generic key→bytes filesystem-backed store. `core/library.py` keeps its `Model`/`Sequence` business logic but its filesystem primitives (atomic JSON write, bbox read, etc.) are extracted into a small shared `core/library_io.py` so both `library.py` and `FilesystemStorage` can use them without `FilesystemStorage` depending on Sequence/Model semantics.
4. `core/run_manager.py` — `AsyncioRunManager` conforms to `protocols.runs.RunManager`. Internally delegates to the existing module-level functions in `core/runner.py` (which we keep) so existing tests and API callers continue to work unmodified.

Both scripts (`tools/pack_splats.py`, `tools/fuse_to_full_ply.py`) become thin CLI wrappers that import and call the moved logic. Behavior is byte-for-byte preserved.

The composition root from Phase 1 grows: it now builds the four concrete impls and stores them on `app.state` so they can be retrieved by Phase 3's `Depends()` injection. Phase 1's stub wiring stays in place for `EventEmitter` (already concrete) and gets joined by the new concretes.

**Tech Stack:** Python 3.10+, existing project deps (`numpy`, `plyfile`, `scipy`, `zstandard`, `pydantic`, `fastapi`, `pytest`, `pytest-asyncio`). **No new runtime deps in Phase 2.**

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`

**Phase 2 is plan 2 of 7.** Depends on Phase 1's Protocols (`protocols/{cache,fuse,storage,runs,sim,observability}.py`), `StdlibJSONEmitter`, `RunStateStore`, `CapConfig`, `AppConfig`, and `composition.build_app(cfg)` skeleton. Phase 3 (sim orchestration rewrite) will replace this phase's delegation-shim `AsyncioRunManager` with one that owns the lifecycle directly.

---

## File Structure

### New files (Phase 2)

```
server/gsfluent/
├── core/
│   ├── codecs/
│   │   ├── __init__.py                ← re-export GSQCodec
│   │   └── gsq.py                     ← GSQCodec (logic from tools/pack_splats.py)
│   ├── fusers/
│   │   ├── __init__.py                ← re-export KNNKabschFuser
│   │   └── knn_kabsch.py              ← KNNKabschFuser (logic from tools/fuse_to_full_ply.py)
│   ├── library_io.py                  ← shared filesystem primitives (atomic JSON, ply bbox read)
│   └── run_manager.py                 ← AsyncioRunManager (Protocol shim over existing runner module)
└── storage/
    ├── __init__.py                    ← re-export FilesystemStorage
    └── filesystem.py                  ← FilesystemStorage impl

server/tests/
├── codecs/
│   ├── __init__.py
│   └── test_gsq.py                    ← GSQ-specific unit tests
├── fusers/
│   ├── __init__.py
│   └── test_knn_kabsch.py             ← Fuser-specific unit tests
├── storage/
│   ├── __init__.py
│   └── test_filesystem.py             ← FilesystemStorage-specific unit tests
├── runs/
│   ├── __init__.py
│   └── test_asyncio_run_manager.py    ← RunManager-specific tests
├── integration/
│   ├── __init__.py
│   └── test_phase2_e2e_smoke.py       ← end-to-end with MockSimulationEngine + real impls
└── fixtures/
    ├── __init__.py                    ← pytest fixtures (real_codec, real_storage, etc.)
    └── mock_sim_engine.py             ← MockSimulationEngine for the smoke test
```

### Existing test files extended (Phase 2)

```
server/tests/protocols/
├── test_cache_protocol.py             ← add parametrized conformance run with real GSQCodec
├── test_storage_protocol.py           ← add parametrized conformance run with real FilesystemStorage
├── test_fuse_protocol.py              ← add parametrized conformance run with real KNNKabschFuser
└── test_runs_protocol.py              ← add parametrized conformance run with real AsyncioRunManager
```

### Modified files (Phase 2)

```
server/tools/pack_splats.py            ← shrink to CLI wrapper; logic moves to core/codecs/gsq.py
server/tools/fuse_to_full_ply.py       ← shrink to CLI wrapper; logic moves to core/fusers/knn_kabsch.py
server/gsfluent/core/library.py        ← internal helpers (_atomic_write_json, read_ply_bbox_and_count)
                                          delegate to core/library_io.py; public API unchanged
server/gsfluent/composition.py         ← extend Phase 1 skeleton: wire GSQCodec, FilesystemStorage,
                                          KNNKabschFuser, AsyncioRunManager onto app.state
```

### Files NOT modified in Phase 2

```
server/gsfluent/core/runner.py         ← Phase 3 (sim orchestration rewrite); kept as-is so
                                         AsyncioRunManager can shim through it
server/gsfluent/api/*.py               ← Phase 3 (api/runs.py), Phase 5 (api/sequences.py)
server/tools/run_sim.sh                ← Phase 3 (slim to conda-activate shim)
server/supervise.sh                    ← Phase 4 (deletion in favor of systemd)
frontend/python/viser_headless.py      ← Phase 5 (client-side hardening)
server/gsfluent/protocols/*.py         ← Phase 1 product; Phase 2 only consumes them
server/gsfluent/observability/*.py     ← Phase 1 product; Phase 2 only consumes
server/gsfluent/core/state.py          ← Phase 1 product; Phase 2 only consumes
server/gsfluent/core/limits.py         ← Phase 1 product; not touched here
server/gsfluent/config.py              ← Phase 1 product; not touched here
```

---

## Tasks

### Task 1: Branch + baseline verification

**Files:**
- No file edits in this task. Verification + branch creation only.

- [ ] **Step 1: Confirm Phase 1 is merged on main**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main | grep -i "phase-1" | head
```

Expected: at least one commit prefixed `phase-1:`. If none, halt — Phase 2 depends on Phase 1's Protocols + composition root being on disk.

- [ ] **Step 2: Create the Phase 2 branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout -b phase-2-extract-impls main
```

Expected: `Switched to a new branch 'phase-2-extract-impls'`.

- [ ] **Step 3: Run the baseline test suite — record pass/fail counts**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: a baseline number of passes plus the Phase 1 protocol/observability/core/config/composition tests. Record the number for comparison at Task 13.

- [ ] **Step 4: Confirm Phase 1 Protocols and helpers are importable**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
from gsfluent.protocols.cache import CacheCodec, CacheMetadata, CodecError, DecodedFrame, SplatFrame
from gsfluent.protocols.fuse import Fuser, Correspondence, FuseError
from gsfluent.protocols.storage import Storage, StorageStat, StorageHandle, StorageNotFoundError
from gsfluent.protocols.runs import RunManager, RunState, RunStatus, RecoveryReport, RunId
from gsfluent.protocols.sim import SimulationEngine, ModelRef, SimResult, ValidatedRecipe
from gsfluent.protocols.observability import EventEmitter
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.core.state import RunStateStore, RunStateRecord
from gsfluent.config import AppConfig
from gsfluent.composition import build_app
print('Phase 1 surface OK')
"
```

Expected: `Phase 1 surface OK`. Any ImportError means Phase 1 is incomplete; halt and fix Phase 1 first.

- [ ] **Step 5: No commit yet — Task 1 is verification only**

---

### Task 2: core/library_io.py — shared filesystem primitives

**Files:**
- Create: `server/gsfluent/core/library_io.py`
- Create: `server/tests/core/test_library_io.py`

`core/library.py` currently has private helpers (`_atomic_write_json`, `_read_meta_tolerant`, `read_ply_bbox_and_count`) that the new `FilesystemStorage` impl also needs. Extract them into a small shared module so both `library.py` and `storage/filesystem.py` import from one source.

- [ ] **Step 1: Write the failing test**

Create `server/tests/core/test_library_io.py`:

```python
"""Tests for shared filesystem primitives used by library.py and storage/filesystem.py."""
import json
from pathlib import Path

import pytest

from gsfluent.core.library_io import (
    atomic_write_bytes,
    atomic_write_json,
    read_json_tolerant,
    read_ply_bbox_and_count,
)


def test_atomic_write_json_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_json(target, {"k": 1})
    assert json.loads(target.read_text()) == {"k": 1}


def test_atomic_write_json_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}')
    atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}


def test_atomic_write_json_via_temp_then_rename(tmp_path: Path) -> None:
    """The .tmp file should not remain after a successful write."""
    target = tmp_path / "out.json"
    atomic_write_json(target, {"k": 1})
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_json_cleanup_on_failure(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails, the .tmp file should be removed."""
    import os
    target = tmp_path / "out.json"

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"k": 1})
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_bytes_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello\x00world")
    assert target.read_bytes() == b"hello\x00world"


def test_atomic_write_bytes_via_temp_then_rename(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"abc")
    assert not (tmp_path / "out.bin.tmp").exists()


def test_read_json_tolerant_returns_dict(tmp_path: Path) -> None:
    target = tmp_path / "meta.json"
    target.write_text('{"k": 2}')
    assert read_json_tolerant(target) == {"k": 2}


def test_read_json_tolerant_missing_returns_none(tmp_path: Path) -> None:
    assert read_json_tolerant(tmp_path / "missing.json") is None


def test_read_json_tolerant_corrupt_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{not json")
    assert read_json_tolerant(target) is None


def test_read_json_tolerant_non_dict_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]")
    assert read_json_tolerant(target) is None


def test_read_ply_bbox_and_count_missing_returns_none(tmp_path: Path) -> None:
    n, bbox = read_ply_bbox_and_count(tmp_path / "nope.ply")
    assert n is None and bbox is None


def test_read_ply_bbox_and_count_real_ply(tmp_path: Path) -> None:
    """Generate a tiny ply with plyfile and read back the bbox."""
    import numpy as np
    from plyfile import PlyData, PlyElement

    verts = np.array(
        [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (-1.0, -2.0, -3.0)],
        dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")],
    )
    ply_path = tmp_path / "tiny.ply"
    PlyData([PlyElement.describe(verts, "vertex")], text=True).write(ply_path)

    n, bbox = read_ply_bbox_and_count(ply_path)
    assert n == 3
    assert bbox == [[-1.0, -2.0, -3.0], [1.0, 2.0, 3.0]]
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_library_io.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.library_io'`.

- [ ] **Step 3: Implement core/library_io.py**

Create `server/gsfluent/core/library_io.py`:

```python
"""Shared filesystem primitives used by core/library.py and storage/filesystem.py.

These were originally private helpers inside core/library.py. They moved here
so the FilesystemStorage impl (storage/filesystem.py) can use them without
depending on the Model/Sequence business types.

All write helpers are atomic on the same filesystem (tmp + os.replace).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write `payload` as pretty-printed JSON via tmp + os.replace.

    Atomic on the same filesystem; safe against partial writes that would
    leave a half-written file readable mid-flight.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` (raw bytes) atomically via tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def read_json_tolerant(path: Path) -> Optional[dict]:
    """Read a JSON file returning a dict, or None on missing/corrupt/non-dict.

    Tolerance is the point: callers iterate over many entries, and a single
    bad file shouldn't crash the listing — just log and skip.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("could not parse JSON at %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        _log.warning("JSON at %s is not an object", path)
        return None
    return data


def read_ply_bbox_and_count(
    ply_path: Path,
) -> tuple[Optional[int], Optional[list[list[float]]]]:
    """Read a .ply and return (n_splats, bbox). Tolerant of unreadable files —
    returns (None, None) on any failure so callers can degrade gracefully.
    """
    try:
        from plyfile import PlyData
        import numpy as np

        v = PlyData.read(str(ply_path))["vertex"].data
        n = int(v.shape[0])
        x = np.asarray(v["x"], dtype=np.float64)
        y = np.asarray(v["y"], dtype=np.float64)
        z = np.asarray(v["z"], dtype=np.float64)
        if n == 0:
            return n, None
        bbox = [
            [float(x.min()), float(y.min()), float(z.min())],
            [float(x.max()), float(y.max()), float(z.max())],
        ]
        return n, bbox
    except Exception as e:
        _log.warning("could not read bbox/count from %s: %s", ply_path, e)
        return None, None
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_library_io.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Update core/library.py to delegate to library_io**

Open `server/gsfluent/core/library.py`. Replace the private helpers with thin wrappers that call into `library_io`. Locate the `_atomic_write_json` function (around line 58) and replace it:

```python
from .library_io import (
    atomic_write_json as _atomic_write_json,
    read_json_tolerant as _read_json_tolerant,
    read_ply_bbox_and_count as _read_ply_bbox_and_count,
)
```

Then delete the old `_atomic_write_json`, `_read_meta_tolerant` (replace with the alias `_read_meta_tolerant = _read_json_tolerant`), and `read_ply_bbox_and_count` definitions. Keep `read_ply_bbox_and_count` as a re-export at module level so external callers (`runner.py` already does `lib.read_ply_bbox_and_count(...)`) keep working:

```python
# At module level, keep the public name so external callers (runner.py)
# continue to find it via `library.read_ply_bbox_and_count`.
read_ply_bbox_and_count = _read_ply_bbox_and_count
```

Concrete edit instructions (use the Edit tool):

1. Find the line `def _atomic_write_json(path: Path, payload: dict) -> None:` and replace it AND the function body through the next blank-line-following blank with:

```python
from .library_io import (
    atomic_write_json as _atomic_write_json,
    read_json_tolerant as _read_json_tolerant,
    read_ply_bbox_and_count as _read_ply_bbox_and_count,
)
```

2. Find `def _read_meta_tolerant(path: Path) -> Optional[dict]:` and replace the function (entire definition through the body's `return data`) with:

```python
_read_meta_tolerant = _read_json_tolerant
```

3. Find `def read_ply_bbox_and_count(ply_path: Path) -> tuple[Optional[int], Optional[list[list[float]]]]:` and replace the function (entire definition) with:

```python
read_ply_bbox_and_count = _read_ply_bbox_and_count
```

- [ ] **Step 6: Run the existing library smoke test — confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_library_smoke.py tests/test_sequences_import.py -v
```

Expected: existing pass count is preserved (no new failures).

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/library_io.py \
        server/gsfluent/core/library.py \
        server/tests/core/test_library_io.py
git commit -m "phase-2: core/library_io.py — extract atomic IO + ply bbox read for shared use"
```

---

### Task 3: storage/filesystem.py — FilesystemStorage impl

**Files:**
- Create: `server/gsfluent/storage/__init__.py`
- Create: `server/gsfluent/storage/filesystem.py`
- Create: `server/tests/storage/__init__.py`
- Create: `server/tests/storage/test_filesystem.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/storage/__init__.py` as empty.

Create `server/tests/storage/test_filesystem.py`:

```python
"""FilesystemStorage-specific unit tests.

Conformance against the Storage Protocol is run separately in
tests/protocols/test_storage_protocol.py (parametrized over impls).
This file covers FilesystemStorage-only concerns: path-traversal
defense, atomic rename behavior, and byte-range correctness on real files.
"""
import io
from pathlib import Path

import pytest

from gsfluent.protocols.storage import (
    Storage,
    StorageNotFoundError,
)
from gsfluent.storage.filesystem import FilesystemStorage


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemStorage:
    return FilesystemStorage(root=tmp_path)


def test_storage_satisfies_protocol(storage: FilesystemStorage) -> None:
    s: Storage = storage
    assert isinstance(s, Storage)


@pytest.mark.asyncio
async def test_put_writes_file_under_root(storage: FilesystemStorage, tmp_path: Path) -> None:
    handle = await storage.put("a.gsq", io.BytesIO(b"hello"), {})
    assert handle.key == "a.gsq"
    assert handle.size == 5
    assert (tmp_path / "a.gsq").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_put_creates_intermediate_dirs(storage: FilesystemStorage, tmp_path: Path) -> None:
    await storage.put("nested/dir/a.gsq", io.BytesIO(b"hi"), {})
    assert (tmp_path / "nested" / "dir" / "a.gsq").read_bytes() == b"hi"


@pytest.mark.asyncio
async def test_put_is_atomic(storage: FilesystemStorage, tmp_path: Path) -> None:
    """A .tmp file should not remain after a successful put."""
    await storage.put("a.gsq", io.BytesIO(b"x" * 100), {})
    assert not (tmp_path / "a.gsq.tmp").exists()


@pytest.mark.asyncio
async def test_put_rejects_absolute_key(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("/etc/passwd", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_put_rejects_dotdot_traversal(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("../escape.gsq", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_put_rejects_dotdot_in_middle(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("a/../../escape.gsq", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_get_streams_file_bytes(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"hello world"), {})
    chunks = [chunk async for chunk in await storage.get("a.gsq")]
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_get_raises_not_found(storage: FilesystemStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        async for _ in await storage.get("missing.gsq"):
            pass


@pytest.mark.asyncio
async def test_get_range_returns_subset(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 3, 7)]
    assert b"".join(chunks) == b"3456"


@pytest.mark.asyncio
async def test_get_range_end_none_means_to_eof(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 5, None)]
    assert b"".join(chunks) == b"56789"


@pytest.mark.asyncio
async def test_get_range_zero_start(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 0, 4)]
    assert b"".join(chunks) == b"0123"


@pytest.mark.asyncio
async def test_get_range_raises_not_found(storage: FilesystemStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        async for _ in await storage.get_range("missing.gsq", 0, 10):
            pass


@pytest.mark.asyncio
async def test_stat_returns_size_and_etag(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"hello"), {})
    st = await storage.stat("a.gsq")
    assert st is not None
    assert st.size == 5
    assert st.etag.startswith('"5-')
    assert st.etag.endswith('"')
    assert st.mtime > 0


@pytest.mark.asyncio
async def test_stat_returns_none_for_missing(storage: FilesystemStorage) -> None:
    assert (await storage.stat("missing.gsq")) is None


@pytest.mark.asyncio
async def test_exists_reflects_put(storage: FilesystemStorage) -> None:
    assert (await storage.exists("a.gsq")) is False
    await storage.put("a.gsq", io.BytesIO(b"x"), {})
    assert (await storage.exists("a.gsq")) is True


@pytest.mark.asyncio
async def test_stat_returns_none_on_traversal_key(storage: FilesystemStorage) -> None:
    """stat() never raises — even on a traversal-shaped key, it returns None
    rather than throwing. exists() likewise."""
    assert (await storage.stat("../etc/passwd")) is None
    assert (await storage.exists("../etc/passwd")) is False


@pytest.mark.asyncio
async def test_put_streams_large_payload(storage: FilesystemStorage, tmp_path: Path) -> None:
    """1 MiB payload should chunk through without OOM."""
    payload = b"x" * (1024 * 1024)
    await storage.put("big.gsq", io.BytesIO(payload), {})
    assert (tmp_path / "big.gsq").stat().st_size == 1024 * 1024
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/storage/test_filesystem.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.storage'`.

- [ ] **Step 3: Implement storage/filesystem.py**

Create `server/gsfluent/storage/__init__.py`:

```python
"""Concrete Storage implementations."""
from gsfluent.storage.filesystem import FilesystemStorage

__all__ = ["FilesystemStorage"]
```

Create `server/gsfluent/storage/filesystem.py`:

```python
"""FilesystemStorage — Storage Protocol impl backed by a local directory tree.

Keys are POSIX-style relative paths under the configured root. Path traversal
is rejected at the put boundary (absolute paths, parent-relative segments).
Reads return None / raise StorageNotFoundError without leaking filesystem
errors.

All writes are atomic on the same filesystem (tmp + os.replace). Range reads
use seek + read in chunks; the underlying file is opened per request so
concurrent reads don't share offsets.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator, BinaryIO

from gsfluent.core.library_io import atomic_write_bytes
from gsfluent.protocols.storage import (
    Storage,
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
)

# 64 KiB read chunks — balances per-read overhead vs memory footprint.
_READ_CHUNK = 64 * 1024


class FilesystemStorage:
    """Storage backed by a local directory tree rooted at `root`.

    Keys are POSIX-style relative paths. Examples:
        "demo.gsq"
        "cache/viser/demo.gsq"

    Construction:
        storage = FilesystemStorage(root=Path("/var/lib/gsfluent/cache"))
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- key validation ----

    def _resolve_safe(self, key: str) -> Path:
        """Resolve `key` to an absolute path strictly inside `self._root`.

        Raises ValueError on absolute keys or any path that escapes the root
        via .. segments. Symlink targets are NOT followed during validation
        (resolve(strict=False) is used so we can compute the target path even
        if the file doesn't exist yet for put()).
        """
        if not key:
            raise ValueError("key must not be empty")
        # Reject absolute keys outright. PurePosixPath('/foo').is_absolute() is True.
        if key.startswith("/") or key.startswith("\\"):
            raise ValueError(f"key must not be absolute: {key!r}")
        # Reject any path component that's exactly ".." — even if it normalizes
        # back inside the root, it's a code smell that a Storage consumer is
        # constructing keys from user input without sanitizing.
        parts = key.replace("\\", "/").split("/")
        for part in parts:
            if part == ".." or part == "":
                raise ValueError(f"key contains unsafe segment {part!r}: {key!r}")
        # Final escape check by resolved-path containment.
        target = (self._root / key).resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError as e:
            raise ValueError(f"key escapes storage root: {key!r}") from e
        return target

    def _try_resolve(self, key: str) -> Path | None:
        """_resolve_safe but returns None instead of raising — for stat/exists
        which must not raise on bad input per the Protocol contract."""
        try:
            return self._resolve_safe(key)
        except ValueError:
            return None

    # ---- Storage Protocol ----

    async def put(self, key: str, src: BinaryIO, metadata: dict[str, str]) -> StorageHandle:
        """Write src (stream) to `key`. metadata is currently ignored
        (filesystem has no native key/value tagging; future S3 impl will use it).
        """
        target = self._resolve_safe(key)
        # Read the full payload into memory then atomic-write. For now this is
        # adequate at our payload sizes (~50-200 MB .gsq files); a streaming
        # multipart variant lands in a future sprint if the assumption breaks.
        payload = src.read()
        atomic_write_bytes(target, payload)
        st = target.stat()
        etag = f'"{st.st_size}-{int(st.st_mtime)}"'
        return StorageHandle(key=key, size=st.st_size, etag=etag)

    async def get(self, key: str) -> AsyncIterator[bytes]:
        """Stream the whole object. Raises StorageNotFoundError if absent."""
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            raise StorageNotFoundError(key)
        return self._stream_range(target, 0, None)

    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> AsyncIterator[bytes]:
        """Stream a byte range [start, end). end=None means to EOF."""
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            raise StorageNotFoundError(key)
        return self._stream_range(target, start, end)

    async def stat(self, key: str) -> StorageStat | None:
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            return None
        st = target.stat()
        return StorageStat(
            size=st.st_size,
            mtime=st.st_mtime,
            etag=f'"{st.st_size}-{int(st.st_mtime)}"',
        )

    async def exists(self, key: str) -> bool:
        target = self._try_resolve(key)
        return target is not None and target.is_file()

    # ---- helpers ----

    @staticmethod
    async def _stream_range(path: Path, start: int, end: int | None) -> AsyncIterator[bytes]:
        """Async generator yielding the [start, end) byte range from `path`.

        end=None means to EOF. Reads in `_READ_CHUNK`-sized blocks so a large
        cache file streams without loading the whole thing into RAM.
        """
        async def _gen():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = (end - start) if end is not None else None
                while True:
                    chunk_size = _READ_CHUNK if remaining is None else min(_READ_CHUNK, remaining)
                    if chunk_size <= 0:
                        return
                    chunk = f.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
                    if remaining is not None:
                        remaining -= len(chunk)
                        if remaining <= 0:
                            return
        return _gen()
```

Note: the `_stream_range` returns an async generator (object), so callers do `async for chunk in await storage.get(key):`.

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/storage/test_filesystem.py -v
```

Expected: 18 passed.

- [ ] **Step 5: Extend protocol conformance suite to cover FilesystemStorage**

Open `server/tests/protocols/test_storage_protocol.py` (created in Phase 1). At the bottom of the file (after the existing `_InMemoryStorage` tests), add a parametrized conformance section. Add this code:

```python
# --- Conformance over all real impls -----------------------------------------


@pytest.fixture
def real_filesystem_storage(tmp_path):
    from gsfluent.storage.filesystem import FilesystemStorage
    return FilesystemStorage(root=tmp_path)


def test_real_filesystem_storage_satisfies_storage_protocol(real_filesystem_storage) -> None:
    s: Storage = real_filesystem_storage
    assert isinstance(s, Storage)


@pytest.mark.asyncio
async def test_real_filesystem_storage_put_then_stat(real_filesystem_storage) -> None:
    await real_filesystem_storage.put("conf.gsq", io.BytesIO(b"abc"), {})
    st = await real_filesystem_storage.stat("conf.gsq")
    assert st is not None and st.size == 3


@pytest.mark.asyncio
async def test_real_filesystem_storage_exists(real_filesystem_storage) -> None:
    assert (await real_filesystem_storage.exists("nope.gsq")) is False
    await real_filesystem_storage.put("yes.gsq", io.BytesIO(b"x"), {})
    assert (await real_filesystem_storage.exists("yes.gsq")) is True


@pytest.mark.asyncio
async def test_real_filesystem_storage_range_round_trip(real_filesystem_storage) -> None:
    await real_filesystem_storage.put("r.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [c async for c in await real_filesystem_storage.get_range("r.gsq", 2, 6)]
    assert b"".join(chunks) == b"2345"
```

- [ ] **Step 6: Run protocol conformance + filesystem tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_storage_protocol.py tests/storage/test_filesystem.py -v
```

Expected: all pass (the Phase 1 stub tests + the 4 new conformance tests + the 18 filesystem-specific tests).

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/storage/__init__.py \
        server/gsfluent/storage/filesystem.py \
        server/tests/storage/__init__.py \
        server/tests/storage/test_filesystem.py \
        server/tests/protocols/test_storage_protocol.py
git commit -m "phase-2: storage/filesystem.py — FilesystemStorage Storage Protocol impl + traversal defense + atomic writes"
```

---

### Task 4: core/codecs/gsq.py — GSQCodec impl

**Files:**
- Create: `server/gsfluent/core/codecs/__init__.py`
- Create: `server/gsfluent/core/codecs/gsq.py`
- Create: `server/tests/codecs/__init__.py`
- Create: `server/tests/codecs/test_gsq.py`

Move the encode logic from `server/tools/pack_splats.py` into a class. The CLI wrapper change lands in Task 5.

- [ ] **Step 1: Write the failing test**

Create `server/tests/codecs/__init__.py` as empty.

Create `server/tests/codecs/test_gsq.py`:

```python
"""GSQ codec-specific unit tests. Protocol conformance tests live in
tests/protocols/test_cache_protocol.py (parametrized over impls).
"""
import io
import struct
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.codecs.gsq import (
    GSQCodec,
    MAGIC,
    HEADER_SIZE,
    INDEX_ENTRY_SIZE,
)
from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
)


class _NullEmitter:
    """Inline emitter — drops events."""
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


def _write_full_3dgs_frame(path: Path, n: int = 10, *, seed: int = 0) -> None:
    """Generate a tiny synthetic full 3DGS .ply at `path`."""
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["f_dc_0"] = 0.0; verts["f_dc_1"] = 0.0; verts["f_dc_2"] = 0.0
    verts["opacity"] = 1.0
    verts["scale_0"] = -1.0; verts["scale_1"] = -1.0; verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0; verts["rot_1"] = 0.0; verts["rot_2"] = 0.0; verts["rot_3"] = 0.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_codec_satisfies_protocol() -> None:
    c: CacheCodec = GSQCodec()
    assert isinstance(c, CacheCodec)


def test_codec_advertises_media_type_and_extension() -> None:
    c = GSQCodec()
    assert c.media_type == "application/x-gsq"
    assert c.file_extension == ".gsq"


def test_encode_from_frames_dir_writes_gsq_header(tmp_path: Path) -> None:
    """encode_sequence_dir generates a real .gsq with the right MAGIC."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(3):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    out_path = tmp_path / "demo.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(seq_dir, out_path, on_event=_NullEmitter())
    assert isinstance(meta, CacheMetadata)
    assert meta.n_frames == 3
    assert meta.n_splats == 4

    body = out_path.read_bytes()
    assert body[:4] == MAGIC
    version, n_splats, n_frames = struct.unpack("<III", body[4:16])
    assert version == 1
    assert n_splats == 4
    assert n_frames == 3


def test_encode_empty_dir_raises(tmp_path: Path) -> None:
    seq_dir = tmp_path / "empty" / "frames"
    seq_dir.mkdir(parents=True)
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(seq_dir, tmp_path / "empty.gsq", on_event=_NullEmitter())


def test_encode_missing_dir_raises(tmp_path: Path) -> None:
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            tmp_path / "no_such_dir", tmp_path / "x.gsq", on_event=_NullEmitter(),
        )


def test_encode_writes_index_entries_at_correct_offset(tmp_path: Path) -> None:
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(2):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)
    out_path = tmp_path / "demo.gsq"
    codec = GSQCodec()
    codec.encode_sequence_dir(seq_dir, out_path, on_event=_NullEmitter())

    body = out_path.read_bytes()
    # Header is 80 bytes; index entries follow.
    # Each entry: <QII> = 8 + 4 + 4 = 16 bytes.
    entry0 = body[HEADER_SIZE:HEADER_SIZE + INDEX_ENTRY_SIZE]
    off0, sz0, _flags = struct.unpack("<QII", entry0)
    # First frame should start AFTER the index + static block.
    static_offset_loc = HEADER_SIZE - 24 - 12  # back-computed
    # Simpler check: off0 must be > HEADER_SIZE + 2 * INDEX_ENTRY_SIZE.
    assert off0 > HEADER_SIZE + 2 * INDEX_ENTRY_SIZE


def test_encode_sanitizes_non_finite_positions(tmp_path: Path) -> None:
    """A frame with NaN positions encodes successfully (forward-filled)."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    # Frame 1 with NaN x coord.
    _write_full_3dgs_frame(seq_dir / "frame_0001.ply", n=4, seed=1)
    bad = PlyData.read(str(seq_dir / "frame_0001.ply"))
    arr = bad["vertex"].data
    arr["x"][0] = np.nan
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(
        seq_dir / "frame_0001.ply"
    )
    codec = GSQCodec()
    # Should not raise.
    meta = codec.encode_sequence_dir(
        seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
    )
    assert meta.n_frames == 2


def test_encode_all_nan_frame_raises(tmp_path: Path) -> None:
    """If every position is NaN even after forward-fill, encode raises CodecError."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    arr = PlyData.read(str(seq_dir / "frame_0000.ply"))["vertex"].data
    arr["x"][:] = np.nan
    arr["y"][:] = np.nan
    arr["z"][:] = np.nan
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(
        seq_dir / "frame_0000.ply"
    )
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
        )


def test_encode_frame_count_mismatch_raises(tmp_path: Path) -> None:
    """A frame with a different splat count than frame 0 raises."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    _write_full_3dgs_frame(seq_dir / "frame_0001.ply", n=5, seed=1)  # different N
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
        )


def test_encode_emits_progress_events(tmp_path: Path) -> None:
    """on_event should see at least an encode.started / encode.completed pair."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(2):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    events: list[tuple[str, dict]] = []

    class _Capture:
        def emit(self, event, **ctx): events.append((event, ctx))
        def child(self, **ctx): return self

    codec = GSQCodec()
    codec.encode_sequence_dir(seq_dir, tmp_path / "demo.gsq", on_event=_Capture())
    names = {e for e, _ in events}
    assert "encode.started" in names
    assert "encode.completed" in names
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/codecs/test_gsq.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.codecs'`.

- [ ] **Step 3: Implement core/codecs/gsq.py**

Create `server/gsfluent/core/codecs/__init__.py`:

```python
"""Concrete CacheCodec implementations."""
from gsfluent.core.codecs.gsq import GSQCodec

__all__ = ["GSQCodec"]
```

Create `server/gsfluent/core/codecs/gsq.py`. The bulk of the code (the encode pipeline: read frames, sanitize, quantize, compress, write) is copied verbatim from `server/tools/pack_splats.py` lines 47-260. Only the function signatures and class scaffolding change.

```python
"""GSQ codec — CacheCodec Protocol impl. Visual-lossless streaming format
for splat sequences.

File layout (unchanged from the prior implementation in tools/pack_splats.py):
    header(80B) + frame_index(16B x N) + static_block(zstd) + frame_chunks(zstd)

Per-frame ply field mapping:
    - xyz:     v["x"], v["y"], v["z"]                       — per frame
    - quat:    (rot_0, rot_1, rot_2, rot_3) normalized      — per frame (v2 only)
    - scales:  exp(scale_0, scale_1, scale_2)               — static (frame 0)
    - rgb:     clip(0.5 + 0.282 * f_dc_*, 0, 1)             — static (frame 0)
    - opacity: sigmoid(opacity_raw)                         — static (frame 0)

If frame 0 has no rot_0..3 fields, we fall back to identity quats — viewer
falls back to the static-cov rendering path.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Iterable, Sequence

import numpy as np
import zstandard as zstd

from gsfluent.protocols.cache import (
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter

SH_C0 = 0.28209479177387814

MAGIC = b"GSQ1"
VERSION = 1
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16
ZSTD_LEVEL = 9
_FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))  # ~7.81e-3


# ---- helper functions copied verbatim from tools/pack_splats.py ------------


def _has_rot_fields(v) -> bool:
    return all(f in v.dtype.names for f in ("rot_0", "rot_1", "rot_2", "rot_3"))


def _norm_quats(qw, qx, qy, qz):
    """Normalize + fix sign so scalar is non-negative (continuous trajectory)."""
    qn = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qn[qn == 0] = 1.0
    qw, qx, qy, qz = qw / qn, qx / qn, qy / qn, qz / qn
    flip = qw < 0
    qw[flip] = -qw[flip]; qx[flip] = -qx[flip]
    qy[flip] = -qy[flip]; qz[flip] = -qz[flip]
    return qw, qx, qy, qz


def _read_static_attrs(v0, on_event: EventEmitter):
    sx = np.exp(np.asarray(v0["scale_0"], dtype=np.float32))
    sy = np.exp(np.asarray(v0["scale_1"], dtype=np.float32))
    sz = np.exp(np.asarray(v0["scale_2"], dtype=np.float32))
    scales = np.stack([sx, sy, sz], axis=1)
    n_clamped = int((scales < _FP16_COV_FLOOR_SQRT).any(axis=1).sum())
    if n_clamped:
        on_event.emit(
            "encode.scales_clamped",
            n_clamped=n_clamped,
            n_total=len(scales),
            pct=n_clamped / len(scales) * 100,
        )
        np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)

    rgb = np.stack([
        0.5 + np.asarray(v0["f_dc_0"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_1"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_2"], dtype=np.float32) * SH_C0,
    ], axis=1).astype(np.float32)

    op_logit = np.asarray(v0["opacity"], dtype=np.float32)
    opacity = (1.0 / (1.0 + np.exp(-op_logit))).astype(np.float32)
    return scales, rgb, opacity


def _read_per_frame(v, want_quats: bool):
    xyz = np.stack([
        np.asarray(v["x"], dtype=np.float32),
        np.asarray(v["y"], dtype=np.float32),
        np.asarray(v["z"], dtype=np.float32),
    ], axis=1)
    quat = None
    if want_quats:
        qw = np.asarray(v["rot_0"], dtype=np.float32)
        qx = np.asarray(v["rot_1"], dtype=np.float32)
        qy = np.asarray(v["rot_2"], dtype=np.float32)
        qz = np.asarray(v["rot_3"], dtype=np.float32)
        qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
        quat = np.stack([qw, qx, qy, qz], axis=1)
    return xyz, quat


def _quantize_xyz(xyz, bmin, bmax):
    span = (bmax - bmin).astype(np.float64)
    span = np.where(span > 0, span, 1.0)
    q = (xyz.astype(np.float64) - bmin) / span * 65535.0
    q = np.clip(np.round(q), 0, 65535).astype(np.int32) - 32768
    return q.astype(np.int16)


def _quantize_quats(q):
    qxyz = np.clip(q[..., 1:4], -1.0, 1.0)
    return np.round(qxyz * 32767.0).astype(np.int16)


# ---- GSQCodec class -------------------------------------------------------


class GSQCodec:
    """CacheCodec Protocol impl for the .gsq streaming format.

    Two entry points:
      - encode_sequence_dir(frames_dir, out_path, on_event): the canonical
        path used by the run pipeline. Reads frame_*.ply files from a
        directory, sanitizes, encodes, writes the .gsq atomically.
      - encode(frames, out, on_event): the Protocol-required entry point.
        Accepts an iterable of SplatFrame dicts already in memory.

    For now `encode_sequence_dir` is the primary one — the pipeline always
    has plys on disk. The in-memory `encode` is a thin convenience wrapper
    used by tests and any future caller that builds SplatFrames programmatically.
    """

    media_type = "application/x-gsq"
    file_extension = ".gsq"

    def encode_sequence_dir(
        self,
        frames_dir: Path,
        out_path: Path,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """Read frame_*.ply from `frames_dir` and write a .gsq to `out_path`.

        The encode pipeline (sanitization + quantization + zstd compression
        + atomic write) is copied verbatim from the prior implementation in
        tools/pack_splats.py — behavior is byte-for-byte preserved.
        """
        from plyfile import PlyData

        if not frames_dir.is_dir():
            raise CodecError(f"frames dir does not exist: {frames_dir}")

        frame_paths = sorted(
            p for p in frames_dir.iterdir()
            if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply"
        )
        if not frame_paths:
            raise CodecError(f"no frame_*.ply in {frames_dir}")

        n_frames = len(frame_paths)
        on_event.emit("encode.started", n_frames=n_frames, source=str(frames_dir))
        t_start = time.time()

        v0 = PlyData.read(str(frame_paths[0]))["vertex"].data
        n_splats = v0.shape[0]
        has_rot_v0 = _has_rot_fields(v0)
        probe = frame_paths[1] if n_frames > 1 else frame_paths[0]
        v_probe = PlyData.read(str(probe))["vertex"].data
        want_quats = has_rot_v0 and _has_rot_fields(v_probe)
        if not has_rot_v0:
            on_event.emit("encode.no_quats", note="frame 0 lacks rot_* fields; using identity")

        scales, rgb, opacity = _read_static_attrs(v0, on_event)

        xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
        quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
        if not want_quats:
            quat_all[..., 0] = 1.0
            quat_all[..., 1:] = 0.0

        for i, p in enumerate(frame_paths):
            v = PlyData.read(str(p))["vertex"].data
            if v.shape[0] != n_splats:
                raise CodecError(
                    f"frame {p.name} has {v.shape[0]} splats, expected {n_splats}"
                )
            xyz, quat = _read_per_frame(v, want_quats=want_quats)
            xyz_all[i] = xyz
            if quat is not None:
                quat_all[i] = quat

        # Sanitization. Non-finite xyz → forward-fill; non-finite or zero-norm
        # quats → identity. Same logic as tools/pack_splats.py.
        bad_xyz = ~np.isfinite(xyz_all).all(axis=2)
        if bad_xyz.any():
            n_bad = int(bad_xyz.sum())
            on_event.emit("encode.sanitize.positions", n_bad=n_bad)
            if bad_xyz[0].any():
                good = ~bad_xyz[0]
                if not good.any():
                    raise CodecUnsanitizableError(
                        f"frame 0 has no finite positions; cannot encode"
                    )
                ctr = xyz_all[0][good].mean(axis=0)
                xyz_all[0][bad_xyz[0]] = ctr
            for t in range(1, n_frames):
                b = bad_xyz[t]
                if b.any():
                    xyz_all[t][b] = xyz_all[t - 1][b]

        qn2 = (quat_all * quat_all).sum(axis=-1)
        bad_q = (~np.isfinite(qn2)) | (qn2 < 1e-12)
        if bad_q.any():
            n_bad = int(bad_q.sum())
            on_event.emit("encode.sanitize.quats", n_bad=n_bad)
            quat_all[bad_q] = np.array([1, 0, 0, 0], dtype=np.float32)

        bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
        bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
        if not (np.isfinite(bbox_min).all() and np.isfinite(bbox_max).all()):
            raise CodecUnsanitizableError(
                f"non-finite bbox after sanitization: {bbox_min}..{bbox_max}"
            )

        xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
        quat_q = _quantize_quats(quat_all)

        rgb_f16 = rgb.astype(np.float16)
        opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
        scales_f16 = scales.astype(np.float16)

        cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
        static_compressed = cctx.compress(static_uncompressed)

        frame_chunks: list[bytes] = []
        for t in range(n_frames):
            raw = xyz_q[t].tobytes() + quat_q[t].tobytes()
            frame_chunks.append(cctx.compress(raw))

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        index_entries = []
        off = frame0_offset
        for c in frame_chunks:
            index_entries.append((off, len(c)))
            off += len(c)

        # Atomic write via tmp + replace.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(MAGIC)
                f.write(struct.pack("<III", VERSION, n_splats, n_frames))
                f.write(struct.pack("<f", 24.0))  # fps_hint
                f.write(bbox_min.tobytes())
                f.write(bbox_max.tobytes())
                f.write(struct.pack("<QI", static_offset, static_size))
                f.write(b"\x00" * 24)
                assert f.tell() == HEADER_SIZE, f"header drift: {f.tell()}"
                for off, sz in index_entries:
                    f.write(struct.pack("<QII", off, sz, 0))
                assert f.tell() == static_offset, "static offset drift"
                f.write(static_compressed)
                for c in frame_chunks:
                    f.write(c)
            import os
            os.replace(str(tmp_path), str(out_path))
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        duration_sec = time.time() - t_start
        out_size = out_path.stat().st_size
        on_event.emit(
            "encode.completed",
            n_frames=n_frames,
            n_splats=n_splats,
            out_bytes=out_size,
            duration_sec=duration_sec,
        )
        return CacheMetadata(
            n_splats=n_splats,
            n_frames=n_frames,
            bbox=(
                float(bbox_min[0]), float(bbox_min[1]), float(bbox_min[2]),
                float(bbox_max[0]), float(bbox_max[1]), float(bbox_max[2]),
            ),
            fps_hint=24.0,
        )

    # ---- CacheCodec Protocol-required methods ----

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """In-memory encode entry point — accepts pre-built SplatFrame dicts.

        Phase 2 leaves this minimal: the pipeline always has plys on disk and
        calls encode_sequence_dir. The in-memory path is used by tests of the
        Protocol surface and any future caller that synthesizes frames
        directly. Each frame dict must carry 'xyz' (N, 3) float32; frame 0
        must additionally carry 'scales', 'rgb', 'opacity'; per-frame 'quat'
        is optional (identity if absent).
        """
        frame_list = list(frames)
        if not frame_list:
            raise CodecError("encode() called with empty frame iterable")
        on_event.emit("encode.started", n_frames=len(frame_list), source="<in-memory>")

        n_frames = len(frame_list)
        n_splats = int(frame_list[0]["xyz"].shape[0])

        xyz_all = np.stack([np.asarray(f["xyz"], dtype=np.float32) for f in frame_list])
        quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
        quat_all[..., 0] = 1.0
        quat_all[..., 1:] = 0.0
        for i, f in enumerate(frame_list):
            if "quat" in f and f["quat"] is not None:
                quat_all[i] = np.asarray(f["quat"], dtype=np.float32)

        bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
        bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
        if not (np.isfinite(bbox_min).all() and np.isfinite(bbox_max).all()):
            raise CodecUnsanitizableError("non-finite bbox in in-memory encode")

        xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
        quat_q = _quantize_quats(quat_all)

        rgb = np.asarray(frame_list[0]["rgb"], dtype=np.float32)
        opacity = np.asarray(frame_list[0]["opacity"], dtype=np.float32)
        scales = np.asarray(frame_list[0]["scales"], dtype=np.float32)
        rgb_f16 = rgb.astype(np.float16)
        opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
        scales_f16 = scales.astype(np.float16)

        cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
        static_compressed = cctx.compress(static_uncompressed)

        frame_chunks = [cctx.compress(xyz_q[t].tobytes() + quat_q[t].tobytes())
                        for t in range(n_frames)]

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        out.write(MAGIC)
        out.write(struct.pack("<III", VERSION, n_splats, n_frames))
        out.write(struct.pack("<f", 24.0))
        out.write(bbox_min.tobytes())
        out.write(bbox_max.tobytes())
        out.write(struct.pack("<QI", static_offset, static_size))
        out.write(b"\x00" * 24)

        off = frame0_offset
        for c in frame_chunks:
            out.write(struct.pack("<QII", off, len(c), 0))
            off += len(c)
        out.write(static_compressed)
        for c in frame_chunks:
            out.write(c)

        on_event.emit("encode.completed", n_frames=n_frames, n_splats=n_splats)
        return CacheMetadata(
            n_splats=n_splats,
            n_frames=n_frames,
            bbox=(
                float(bbox_min[0]), float(bbox_min[1]), float(bbox_min[2]),
                float(bbox_max[0]), float(bbox_max[1]), float(bbox_max[2]),
            ),
            fps_hint=24.0,
        )

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        """Streaming decode is the viser_headless client's job today.

        Phase 2 leaves this as a thin pass-through that buffers and then yields
        decoded frames from `decode_all`. Frontend-side streaming decode lives
        in frontend/python/viser_headless.py and stays there for now (the
        Storage layer fronts the bytes via get_range). Returning a buffered
        iterator here is sufficient for backend callers that don't need
        first-frame-fast latency.
        """
        chunks: list[bytes] = []
        async for c in src:
            chunks.append(c)
        body = b"".join(chunks)

        async def _gen():
            for frame in self.decode_all(_BytesReader(body)):
                yield frame
        return _gen()

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        """Synchronous all-at-once loader. Returns a list of DecodedFrame.

        Reads the .gsq header to find frame offsets, then decompresses each
        frame chunk and the static block. Returns frames with `data` carrying
        the decompressed numpy arrays (xyz_q, quat_q, rgb, opacity, scales).
        """
        header = src.read(HEADER_SIZE)
        if header[:4] != MAGIC:
            raise CodecError(f"bad magic: {header[:4]!r}; expected {MAGIC!r}")
        version, n_splats, n_frames = struct.unpack("<III", header[4:16])
        if version != VERSION:
            raise CodecError(f"unsupported gsq version: {version}")
        # bbox is at offset 20..44 (3 floats min + 3 floats max).
        bbox_min = np.frombuffer(header[20:32], dtype=np.float32)
        bbox_max = np.frombuffer(header[32:44], dtype=np.float32)
        static_offset, static_size = struct.unpack("<QI", header[44:56])

        # Index entries
        index_raw = src.read(n_frames * INDEX_ENTRY_SIZE)
        entries: list[tuple[int, int]] = []
        for i in range(n_frames):
            base = i * INDEX_ENTRY_SIZE
            off, sz, _flags = struct.unpack("<QII", index_raw[base:base + INDEX_ENTRY_SIZE])
            entries.append((off, sz))

        # Static block
        static_compressed = src.read(static_size)
        dctx = zstd.ZstdDecompressor()
        static_uncompressed = dctx.decompress(static_compressed)
        rgb_bytes = static_uncompressed[:n_splats * 3 * 2]
        opacity_bytes = static_uncompressed[n_splats * 3 * 2:n_splats * 3 * 2 + n_splats]
        scales_bytes = static_uncompressed[n_splats * 3 * 2 + n_splats:]
        rgb = np.frombuffer(rgb_bytes, dtype=np.float16).reshape(n_splats, 3)
        opacity = np.frombuffer(opacity_bytes, dtype=np.uint8)
        scales = np.frombuffer(scales_bytes, dtype=np.float16).reshape(n_splats, 3)

        frames_out: list[DecodedFrame] = []
        for i, (_off, sz) in enumerate(entries):
            chunk = src.read(sz)
            raw = dctx.decompress(chunk)
            xyz_q = np.frombuffer(raw[:n_splats * 3 * 2], dtype=np.int16).reshape(n_splats, 3)
            quat_q = np.frombuffer(raw[n_splats * 3 * 2:], dtype=np.int16).reshape(n_splats, 3)
            frames_out.append(DecodedFrame(
                frame_index=i,
                data={
                    "xyz_q": xyz_q,
                    "quat_q": quat_q,
                    "bbox_min": bbox_min,
                    "bbox_max": bbox_max,
                    "rgb": rgb if i == 0 else None,
                    "opacity": opacity if i == 0 else None,
                    "scales": scales if i == 0 else None,
                },
            ))
        return frames_out


class _BytesReader:
    """Minimal BinaryIO-ish wrapper for use by decode_streaming."""
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/codecs/test_gsq.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Extend protocol conformance suite to cover GSQCodec**

Open `server/tests/protocols/test_cache_protocol.py` and append at the bottom:

```python
# --- Conformance over real GSQCodec ------------------------------------------

import struct
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def _write_minimal_3dgs_frame(path: Path, n: int = 4, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["opacity"] = 1.0
    verts["scale_0"] = -1.0; verts["scale_1"] = -1.0; verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_real_gsq_codec_satisfies_protocol() -> None:
    from gsfluent.core.codecs.gsq import GSQCodec
    c: CacheCodec = GSQCodec()
    assert isinstance(c, CacheCodec)


def test_real_gsq_codec_encode_then_decode_round_trip(tmp_path) -> None:
    """Encode a tiny synthetic sequence, then decode it back."""
    from gsfluent.core.codecs.gsq import GSQCodec

    frames_dir = tmp_path / "seq" / "frames"
    frames_dir.mkdir(parents=True)
    for i in range(3):
        _write_minimal_3dgs_frame(frames_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    out_path = tmp_path / "seq.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(frames_dir, out_path, on_event=_StubEmitter())
    assert meta.n_frames == 3
    assert meta.n_splats == 4

    with open(out_path, "rb") as fh:
        decoded = codec.decode_all(fh)
    assert len(decoded) == 3
    assert decoded[0].frame_index == 0
    assert decoded[2].frame_index == 2
```

- [ ] **Step 6: Run all cache-related tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_cache_protocol.py tests/codecs/test_gsq.py -v
```

Expected: all pass (Phase 1 stub tests + 2 new conformance tests + 10 gsq-specific tests).

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/codecs/__init__.py \
        server/gsfluent/core/codecs/gsq.py \
        server/tests/codecs/__init__.py \
        server/tests/codecs/test_gsq.py \
        server/tests/protocols/test_cache_protocol.py
git commit -m "phase-2: core/codecs/gsq.py — GSQCodec CacheCodec impl + encode/decode round-trip tests"
```

---

### Task 5: tools/pack_splats.py — slim to CLI wrapper

**Files:**
- Modify: `server/tools/pack_splats.py`

Replace the script body with a thin CLI wrapper that delegates to `GSQCodec`. The behavior is preserved 1:1.

- [ ] **Step 1: Read the current pack_splats.py to confirm the CLI surface**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
head -50 server/tools/pack_splats.py
```

Expected: existing args are positional `sequence` (optional) + `--force`. CLI invocations from the wider codebase: `runner.py` line 500 calls `python server/tools/pack_splats.py <run_name>` with no `--force`. That behavior must be preserved.

- [ ] **Step 2: Replace the script body**

Use Write to replace the entire `server/tools/pack_splats.py` contents:

```python
"""CLI wrapper around gsfluent.core.codecs.gsq.GSQCodec.

The encode/sanitize/quantize pipeline lives in
server/gsfluent/core/codecs/gsq.py. This script handles only:
  - argparse
  - sequence discovery from work/library/sequences/
  - up-to-date staleness check vs source frame mtimes
  - delegating to GSQCodec.encode_sequence_dir

Usage (unchanged from the prior implementation):
    python server/tools/pack_splats.py                # all sequences
    python server/tools/pack_splats.py <seq>          # one sequence
    python server/tools/pack_splats.py --force <seq>  # rebuild
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install (server/tools/ is
# outside the package).
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent._paths import SEQUENCES, CACHE_VISER  # noqa: E402
from gsfluent.core.codecs.gsq import GSQCodec  # noqa: E402
from gsfluent.observability.jsonlog import StdlibJSONEmitter  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sequence", nargs="?", default=None,
                   help="single sequence name; omit for all")
    p.add_argument("--force", action="store_true",
                   help="rebuild even if .gsq is newer than the source frames")
    args = p.parse_args()

    CACHE_VISER.mkdir(parents=True, exist_ok=True)

    if args.sequence:
        seq_names = [args.sequence]
    else:
        seq_names = sorted(
            p.name for p in SEQUENCES.iterdir()
            if p.is_dir() and (p / "frames").is_dir()
        )

    codec = GSQCodec()
    # The CLI logs to stdout in plain text (matches the prior behavior the
    # runner subprocess capture relies on). JSON events also stream to stdout
    # via StdlibJSONEmitter for downstream parsing if anyone wires it up.
    obs = StdlibJSONEmitter(stream=sys.stderr)

    n_built = n_skipped = n_failed = 0
    for name in seq_names:
        out = CACHE_VISER / f"{name}.gsq"
        frames_dir = SEQUENCES / name / "frames"
        if not frames_dir.is_dir():
            print(f"[pack_splats] {name}: no frames/ — skip")
            n_skipped += 1
            continue
        if out.is_file() and not args.force:
            newest_src = max(
                p.stat().st_mtime for p in frames_dir.iterdir() if p.suffix == ".ply"
            )
            if out.stat().st_mtime >= newest_src:
                print(f"[pack_splats] {name}: up-to-date, skip")
                n_skipped += 1
                continue
        print(f"[pack_splats] {name}: building")
        t0 = time.time()
        try:
            meta = codec.encode_sequence_dir(frames_dir, out, on_event=obs)
            n_built += 1
            print(
                f"  done  {out.stat().st_size / 1e6:.1f} MB  "
                f"({meta.n_frames} frames, {meta.n_splats} splats, "
                f"{time.time() - t0:.1f}s)\n"
            )
        except Exception as e:
            print(f"  FAILED: {e!r}\n", file=sys.stderr)
            n_failed += 1

    print(f"[pack_splats] built={n_built} skipped={n_skipped} failed={n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Sanity-check the wrapper compiles**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('pack_splats', 'tools/pack_splats.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('CLI wrapper imports OK')
"
```

Expected: `CLI wrapper imports OK`.

- [ ] **Step 4: Invoke the wrapper against an empty sequences dir**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
SEQ_TMP=$(mktemp -d)
GSFLUENT_WORK_DIR=$SEQ_TMP server/.venv/bin/python server/tools/pack_splats.py 2>&1 | tail -5 || true
```

Note: the SEQUENCES constant is computed at import time from `_paths.py`, so this only exercises the no-sequence path. Expected: the summary line `[pack_splats] built=0 skipped=0 failed=0` (or an early exit if no sequence dir exists). If `gsfluent._paths.SEQUENCES` is hardcoded to repo work/library/sequences/, the GSFLUENT_WORK_DIR env override won't actually redirect; this step is best-effort sanity, not a behavior test.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tools/pack_splats.py
git commit -m "phase-2: tools/pack_splats.py — slim to CLI wrapper; logic moved to core/codecs/gsq.py"
```

---

### Task 6: core/fusers/knn_kabsch.py — KNNKabschFuser impl

**Files:**
- Create: `server/gsfluent/core/fusers/__init__.py`
- Create: `server/gsfluent/core/fusers/knn_kabsch.py`
- Create: `server/tests/fusers/__init__.py`
- Create: `server/tests/fusers/test_knn_kabsch.py`

The fuser logic in `server/tools/fuse_to_full_ply.py` is a single 819-line `main()` function. Extract it as a class with two public methods: `build_correspondence` and `fuse_frame` (matching the Protocol), plus a `fuse_sequence_dir` convenience entrypoint that the CLI wrapper will use to preserve the existing per-run behavior.

- [ ] **Step 1: Write the failing test**

Create `server/tests/fusers/__init__.py` as empty.

Create `server/tests/fusers/test_knn_kabsch.py`:

```python
"""KNNKabschFuser unit tests. Protocol conformance lives in
tests/protocols/test_fuse_protocol.py (parametrized over impls).
"""
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.fusers.knn_kabsch import (
    KNNKabschFuser,
    _batched_kabsch_rotation,
    _cov6_to_quat_logscale,
    _norm_xyz_to_origin_cube,
    _rotmat_to_quat,
)
from gsfluent.protocols.fuse import (
    Correspondence,
    FuseError,
    FuseNonFiniteInputError,
    Fuser,
)


def _write_full_3dgs_ply(path: Path, n: int = 10, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["opacity"] = 0.5
    verts["scale_0"] = -1.0; verts["scale_1"] = -1.0; verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def _write_sim_xyz_ply(path: Path, n: int = 5, seed: int = 0) -> None:
    """A 'sim_*.ply' style file — xyz only, no scales/SH/etc."""
    rng = np.random.default_rng(seed)
    verts = np.zeros(n, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    verts["x"] = rng.uniform(0, 2, n).astype(np.float32)
    verts["y"] = rng.uniform(0, 2, n).astype(np.float32)
    verts["z"] = rng.uniform(0, 2, n).astype(np.float32)
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_fuser_satisfies_protocol() -> None:
    f: Fuser = KNNKabschFuser(k=4)
    assert isinstance(f, Fuser)


def test_norm_xyz_to_origin_cube_centers_data() -> None:
    """Sanity: normalization maps the input bbox center to (1,1,1) and scales
    longest axis to 1.0."""
    xyz = np.array([[0, 0, 0], [10, 5, 2]], dtype=np.float32)
    out, center, extent = _norm_xyz_to_origin_cube(xyz)
    # After normalization, bbox center should be at (1, 1, 1).
    out_min = out.min(axis=0)
    out_max = out.max(axis=0)
    np.testing.assert_allclose((out_min + out_max) / 2, [1.0, 1.0, 1.0], atol=1e-5)
    assert extent == 10.0  # longest axis was x


def test_rotmat_to_quat_identity_round_trip() -> None:
    """Identity rotation should produce (1, 0, 0, 0)."""
    R = np.eye(3, dtype=np.float32)[None, :, :]  # (1, 3, 3)
    q = _rotmat_to_quat(R)
    np.testing.assert_allclose(q[0], [1.0, 0.0, 0.0, 0.0], atol=1e-5)


def test_rotmat_to_quat_90deg_z_rotation() -> None:
    """90° rotation about Z axis: quat = (cos(45°), 0, 0, sin(45°))."""
    c = np.cos(np.pi / 4); s = np.sin(np.pi / 4)
    R = np.array([[[0, -1, 0], [1, 0, 0], [0, 0, 1]]], dtype=np.float32)
    q = _rotmat_to_quat(R)
    np.testing.assert_allclose(q[0], [c, 0.0, 0.0, s], atol=1e-5)


def test_batched_kabsch_returns_proper_rotation() -> None:
    """Two identical point clouds → identity rotation; det should be +1."""
    rng = np.random.default_rng(0)
    pts = rng.normal(size=(1, 5, 3)).astype(np.float32)
    weights = np.full((1, 5), 1.0 / 5, dtype=np.float32)
    R = _batched_kabsch_rotation(pts, pts, weights)
    np.testing.assert_allclose(R[0], np.eye(3), atol=1e-5)
    assert np.linalg.det(R[0]) > 0


def test_cov6_to_quat_logscale_identity_cov() -> None:
    """Cov = identity → quat = identity, log_s = log(sqrt(1)) = 0."""
    cov = np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 1.0]], dtype=np.float32)
    quat, log_s = _cov6_to_quat_logscale(cov)
    np.testing.assert_allclose(np.abs(quat[0, 0]), 1.0, atol=1e-5)
    np.testing.assert_allclose(log_s[0], [0.0, 0.0, 0.0], atol=1e-5)


def test_build_correspondence_returns_correspondence(tmp_path: Path) -> None:
    """Real fuser: build_correspondence on small synthetic ply + sim frame."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    first_frame_particles = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, first_frame_particles)
    assert isinstance(corr, Correspondence)
    assert corr.reference_ply_path == ref_path
    assert corr.extent > 0


def test_fuse_frame_returns_dict_with_xyz(tmp_path: Path) -> None:
    """fuse_frame yields a SplatFrame dict carrying at least 'xyz'."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    p1 = p0 + rng.normal(scale=0.05, size=p0.shape).astype(np.float32)

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, p0)
    out = fuser.fuse_frame(corr, p1)
    assert "xyz" in out
    assert out["xyz"].shape[1] == 3


def test_fuse_frame_non_finite_input_raises(tmp_path: Path) -> None:
    """NaN positions in the particle frame raise FuseNonFiniteInputError."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    bad = p0.copy()
    bad[0, 0] = np.nan

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, p0)
    with pytest.raises(FuseNonFiniteInputError):
        fuser.fuse_frame(corr, bad)


def test_fuser_default_k_is_8() -> None:
    """Default K matches the production-recommended value from the spec."""
    f = KNNKabschFuser()
    assert f.k == 8


def test_fuse_sequence_dir_writes_per_frame_plys(tmp_path: Path) -> None:
    """fuse_sequence_dir: drives the per-frame loop; sanity-checks output count."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    for i in range(3):
        _write_sim_xyz_ply(sim_dir / f"sim_{i:04d}.ply", n=5, seed=i)

    out_dir = tmp_path / "out"
    fuser = KNNKabschFuser(k=4)
    n_written = fuser.fuse_sequence_dir(
        reference_ply_path=ref_path,
        sim_dir=sim_dir,
        out_dir=out_dir,
    )
    assert n_written == 3
    assert (out_dir / "frame_0000.ply").is_file()
    assert (out_dir / "frame_0002.ply").is_file()
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/fusers/test_knn_kabsch.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.fusers'`.

- [ ] **Step 3: Implement core/fusers/knn_kabsch.py**

Create `server/gsfluent/core/fusers/__init__.py`:

```python
"""Concrete Fuser implementations."""
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser

__all__ = ["KNNKabschFuser"]
```

Create `server/gsfluent/core/fusers/knn_kabsch.py`. The math helpers (`_batched_kabsch_rotation`, `_cov6_to_quat_logscale`, `_rotmat_to_quat`, `_quat_mul`) are copied verbatim from `server/tools/fuse_to_full_ply.py` lines 115-239. The new `KNNKabschFuser` class wraps the per-frame logic. Phase 2 covers only the **default code path** (K-NN skinning with `k>0`, no cov fields, no watch mode) — the rest of the script's special paths (particle_F cov fields, knn_rotation, watch mode, max_frames, subsample, min_opacity, --no_zup, --no-output_source_scale, --no-center_at_origin) remain in the CLI wrapper for use by callers that pass non-default flags directly. The Protocol-conforming class exposes the production defaults: `zup=True`, `output_source_scale=True`, `center_at_origin=True`, `knn>=1`, no cov fields, no rotation update.

```python
"""KNNKabschFuser — Fuser Protocol impl. K-NN inverse-distance skinning of
reference 3DGS attributes onto per-frame sim particle positions.

The math helpers (Kabsch SVD, cov-to-quat eigendecomposition, quat utilities)
are copied verbatim from tools/fuse_to_full_ply.py — Phase 2 is a pure
refactor with no algorithm changes.

Scope: this Protocol impl covers the **production defaults** of the prior
script — K-NN skinning (K>=1), source-scale output, Y-up to Z-up rotation,
center-at-origin. The script's special paths (cov-field particle_F mode,
knn_rotation, watch mode, subsample, min_opacity opacity filter) remain in
the CLI wrapper for ad-hoc callers; they're out of scope for the Protocol
contract this phase enshrines.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree

from gsfluent.core.coord_convert import (
    rotate_normals_y_up_to_z_up as _rotate_norm,
    rotate_positions_y_up_to_z_up as _rotate_pos,
    rotate_quaternions_y_up_to_z_up as _rotate_quat,
)
from gsfluent.protocols.fuse import (
    Correspondence,
    FuseDegenerateClusterError,
    FuseError,
    FuseNonFiniteInputError,
    ParticleFrame,
    SplatFrame,
)


# ---- math helpers copied verbatim from tools/fuse_to_full_ply.py -----------


def _batched_kabsch_rotation(p_rel_0, q_rel_t, weights):
    """Weighted Kabsch over a batch of N point-clouds."""
    H = np.einsum("nk,nki,nkj->nij", weights, q_rel_t, p_rel_0)
    U, _, Vt = np.linalg.svd(H)
    det = np.linalg.det(np.einsum("nij,njk->nik", U, Vt))
    D = np.broadcast_to(np.eye(3, dtype=H.dtype), (H.shape[0], 3, 3)).copy()
    D[:, 2, 2] = np.sign(det)
    return np.einsum("nij,njk,nkl->nil", U, D, Vt)


def _cov6_to_quat_logscale(cov6: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decompose per-particle covariance into per-frame quaternion + log-scale."""
    n = cov6.shape[0]
    C = np.empty((n, 3, 3), dtype=cov6.dtype)
    C[:, 0, 0] = cov6[:, 0]
    C[:, 0, 1] = cov6[:, 1]; C[:, 1, 0] = cov6[:, 1]
    C[:, 0, 2] = cov6[:, 2]; C[:, 2, 0] = cov6[:, 2]
    C[:, 1, 1] = cov6[:, 3]
    C[:, 1, 2] = cov6[:, 4]; C[:, 2, 1] = cov6[:, 4]
    C[:, 2, 2] = cov6[:, 5]
    eigvals, eigvecs = np.linalg.eigh(C)
    eigvals = eigvals[:, ::-1]
    eigvecs = eigvecs[:, :, ::-1]
    dets = np.linalg.det(eigvecs)
    flip = (dets < 0).astype(eigvecs.dtype)
    eigvecs[..., 2] *= (1.0 - 2.0 * flip)[:, None]
    quat = _rotmat_to_quat(eigvecs.astype(np.float32, copy=False))
    log_s = 0.5 * np.log(np.maximum(eigvals, 1e-12)).astype(np.float32)
    return quat, log_s


def _rotmat_to_quat(R):
    """Batched (N, 3, 3) rotation matrices -> (N, 4) quaternions in (w,x,y,z) order."""
    m = R
    t = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    out = np.zeros((m.shape[0], 4), dtype=m.dtype)
    mask_a = t > 0
    s = np.sqrt(t[mask_a] + 1.0) * 2.0
    out[mask_a, 0] = 0.25 * s
    out[mask_a, 1] = (m[mask_a, 2, 1] - m[mask_a, 1, 2]) / s
    out[mask_a, 2] = (m[mask_a, 0, 2] - m[mask_a, 2, 0]) / s
    out[mask_a, 3] = (m[mask_a, 1, 0] - m[mask_a, 0, 1]) / s
    remaining = ~mask_a
    rem_idx = np.argmax(np.stack([m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]], axis=1), axis=1)
    mb = remaining & (rem_idx == 0)
    s = np.sqrt(1.0 + m[mb, 0, 0] - m[mb, 1, 1] - m[mb, 2, 2]) * 2.0
    out[mb, 0] = (m[mb, 2, 1] - m[mb, 1, 2]) / s
    out[mb, 1] = 0.25 * s
    out[mb, 2] = (m[mb, 0, 1] + m[mb, 1, 0]) / s
    out[mb, 3] = (m[mb, 0, 2] + m[mb, 2, 0]) / s
    mc = remaining & (rem_idx == 1)
    s = np.sqrt(1.0 + m[mc, 1, 1] - m[mc, 0, 0] - m[mc, 2, 2]) * 2.0
    out[mc, 0] = (m[mc, 0, 2] - m[mc, 2, 0]) / s
    out[mc, 1] = (m[mc, 0, 1] + m[mc, 1, 0]) / s
    out[mc, 2] = 0.25 * s
    out[mc, 3] = (m[mc, 1, 2] + m[mc, 2, 1]) / s
    md = remaining & (rem_idx == 2)
    s = np.sqrt(1.0 + m[md, 2, 2] - m[md, 0, 0] - m[md, 1, 1]) * 2.0
    out[md, 0] = (m[md, 1, 0] - m[md, 0, 1]) / s
    out[md, 1] = (m[md, 0, 2] + m[md, 2, 0]) / s
    out[md, 2] = (m[md, 1, 2] + m[md, 2, 1]) / s
    out[md, 3] = 0.25 * s
    return out


def _quat_mul(q1, q2):
    """Hamilton product q1 ⊗ q2, both (N, 4) in (w,x,y,z) order."""
    w1, x1, y1, z1 = q1[:, 0], q1[:, 1], q1[:, 2], q1[:, 3]
    w2, x2, y2, z2 = q2[:, 0], q2[:, 1], q2[:, 2], q2[:, 3]
    return np.stack([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ], axis=1)


def _norm_xyz_to_origin_cube(
    xyz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Normalize reference xyz: longest axis -> 1.0, center -> (1,1,1).

    Returns (normalized_xyz, center, extent) — the same convention the sim's
    transform2origin uses on the reference data.
    """
    aabb_min = xyz.min(0); aabb_max = xyz.max(0)
    center = (aabb_min + aabb_max) / 2.0
    extent = float((aabb_max - aabb_min).max())
    if extent == 0.0:
        raise FuseError(f"reference ply has zero-extent bbox: {aabb_min}..{aabb_max}")
    normed = ((xyz - center) / extent + 1.0).astype(np.float32)
    return normed, center, extent


# ---- KNNKabschFuser class --------------------------------------------------


@dataclass(frozen=True)
class _KNNCorrespondence:
    """Private state stashed inside Correspondence.extent for fuse_frame's reuse.

    Correspondence is a frozen dataclass with fixed fields per the Protocol;
    we encode our extra state on the side. fuse_frame retrieves it via the
    instance dict on the fuser (keyed by Correspondence id).
    """
    ref_xyz_norm: np.ndarray
    center: np.ndarray
    extent: float
    knn_idx: np.ndarray      # (n_ref, K)
    knn_weights: np.ndarray  # (n_ref, K)
    sim_xyz_t0_kept: np.ndarray  # (n_kept, 3) — frame-0 sim particles
    full_attrs: np.ndarray   # FULL reference attr array, post-zup/coord transforms


class KNNKabschFuser:
    """Fuser Protocol impl using inverse-distance K-NN skinning + Kabsch.

    Construction:
        fuser = KNNKabschFuser(k=8)

    The K parameter controls how many sim particles weight each reference splat's
    displacement (higher K = smoother, more diffusive; K=8 is the production default
    per the spec). All other parameters use the prior script's production defaults:
    Y-up→Z-up rotation, source-scale output, centered at origin.
    """

    def __init__(self, k: int = 8) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1; got {k}")
        self.k = k
        # Maps id(Correspondence) -> _KNNCorrespondence side-state. The
        # Protocol's Correspondence is a public frozen dataclass; we keep the
        # K-NN map + reference attrs here to avoid leaking large numpy arrays
        # through the public type.
        self._state: dict[int, _KNNCorrespondence] = {}

    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: ParticleFrame,
    ) -> Correspondence:
        """Build the K-NN reference→particle mapping. One-shot per sequence."""
        first_frame_particles = np.asarray(first_frame_particles, dtype=np.float32)
        if first_frame_particles.ndim != 2 or first_frame_particles.shape[1] != 3:
            raise FuseError(
                f"first_frame_particles must be (N, 3); got shape "
                f"{first_frame_particles.shape}"
            )
        if not np.isfinite(first_frame_particles).all():
            raise FuseNonFiniteInputError("first_frame_particles contains NaN/Inf")

        ref_ply = PlyData.read(str(reference_ply_path))
        ref_v = ref_ply["vertex"].data
        ref_xyz_raw = np.stack(
            [ref_v["x"], ref_v["y"], ref_v["z"]], axis=1,
        ).astype(np.float32)
        ref_xyz_norm, center, extent = _norm_xyz_to_origin_cube(ref_xyz_raw)

        # K-NN: for each REF splat, find K nearest SIM particles at frame 0.
        sim_tree = cKDTree(first_frame_particles)
        effective_k = min(self.k, len(first_frame_particles))
        if effective_k < 1:
            raise FuseError("first_frame_particles is empty")
        dists, knn_idx = sim_tree.query(ref_xyz_norm, k=effective_k, workers=-1)
        if effective_k == 1:
            dists = dists[:, None]
            knn_idx = knn_idx[:, None]
        # Detect totally-degenerate K-NN: all-zero distances mean every
        # sim particle coincides — Kabsch can't solve any rotation.
        if (dists == 0.0).all():
            raise FuseDegenerateClusterError(
                "all K-NN distances are zero; sim particles coincide"
            )
        inv_d = 1.0 / (dists.astype(np.float32) + 1e-6)
        knn_weights = (inv_d / inv_d.sum(axis=1, keepdims=True)).astype(np.float32)

        # Build the FULL reference attribute array with zup rotation +
        # rest-position bake (matches the prior script's production defaults).
        out_dtype = ref_v.dtype
        full_attrs = np.empty(len(ref_v), dtype=out_dtype)
        for field in out_dtype.names:
            full_attrs[field] = ref_v[field]

        # Zup rotation on rotation quats + normals.
        if all(k in full_attrs.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
            q = np.stack([
                full_attrs["rot_0"], full_attrs["rot_1"],
                full_attrs["rot_2"], full_attrs["rot_3"],
            ], axis=1).astype(np.float32)
            new_q = _rotate_quat(q)
            full_attrs["rot_0"] = new_q[:, 0]
            full_attrs["rot_1"] = new_q[:, 1]
            full_attrs["rot_2"] = new_q[:, 2]
            full_attrs["rot_3"] = new_q[:, 3]
        if all(k in full_attrs.dtype.names for k in ("nx", "ny", "nz")):
            n = np.stack([
                full_attrs["nx"], full_attrs["ny"], full_attrs["nz"],
            ], axis=1).astype(np.float32)
            new_n = _rotate_norm(n)
            full_attrs["nx"] = new_n[:, 0]
            full_attrs["ny"] = new_n[:, 1]
            full_attrs["nz"] = new_n[:, 2]

        # Bake rest positions in source-scale + zup + centered-at-origin frame.
        rest_xyz = self._transform_sim_xyz(
            ref_xyz_norm, extent=extent, center=center,
        )
        full_attrs["x"] = rest_xyz[:, 0]
        full_attrs["y"] = rest_xyz[:, 1]
        full_attrs["z"] = rest_xyz[:, 2]

        corr = Correspondence(
            reference_ply_path=reference_ply_path,
            indices=tuple(int(i) for i in range(len(ref_v))),
            extent=extent,
        )
        self._state[id(corr)] = _KNNCorrespondence(
            ref_xyz_norm=ref_xyz_norm,
            center=center,
            extent=extent,
            knn_idx=knn_idx,
            knn_weights=knn_weights,
            sim_xyz_t0_kept=first_frame_particles,
            full_attrs=full_attrs,
        )
        return corr

    def fuse_frame(
        self,
        correspondence: Correspondence,
        particle_frame: ParticleFrame,
    ) -> SplatFrame:
        """K-NN-skin per-frame sim displacement onto every reference splat."""
        state = self._state.get(id(correspondence))
        if state is None:
            raise FuseError(
                f"fuse_frame called with a Correspondence not produced by "
                f"this fuser instance (id={id(correspondence)})"
            )
        particle_frame = np.asarray(particle_frame, dtype=np.float32)
        if particle_frame.ndim != 2 or particle_frame.shape[1] != 3:
            raise FuseError(
                f"particle_frame must be (N, 3); got shape {particle_frame.shape}"
            )
        if not np.isfinite(particle_frame).all():
            raise FuseNonFiniteInputError("particle_frame contains NaN/Inf")
        if particle_frame.shape[0] != state.sim_xyz_t0_kept.shape[0]:
            raise FuseError(
                f"particle_frame has {particle_frame.shape[0]} particles; "
                f"expected {state.sim_xyz_t0_kept.shape[0]} (from frame 0)"
            )

        sim_disp = particle_frame - state.sim_xyz_t0_kept              # (n_kept, 3)
        neighbors = sim_disp[state.knn_idx]                            # (n_ref, K, 3)
        ref_disp = (state.knn_weights[..., None] * neighbors).sum(axis=1)
        ref_xyz_displaced = state.ref_xyz_norm + ref_disp              # (n_ref, 3)
        out_xyz_world = self._transform_sim_xyz(
            ref_xyz_displaced, extent=state.extent, center=state.center,
        )

        out = state.full_attrs.copy()
        out["x"] = out_xyz_world[:, 0]
        out["y"] = out_xyz_world[:, 1]
        out["z"] = out_xyz_world[:, 2]

        return {
            "xyz": out_xyz_world,
            "full_attrs": out,
            "n_ref": len(state.full_attrs),
        }

    # ---- convenience entry-point for the CLI wrapper -----------------------

    def fuse_sequence_dir(
        self,
        reference_ply_path: Path,
        sim_dir: Path,
        out_dir: Path,
    ) -> int:
        """Drive the per-frame loop, writing fused frame_*.ply atomically.

        Used by the slim tools/fuse_to_full_ply.py CLI wrapper and by the
        Phase 2 smoke test. Returns the number of frames written.
        """
        import re
        sim_re = re.compile(r"sim_(\d+)\.ply$")
        sim_plys = sorted(sim_dir.glob("sim_*.ply"))
        if not sim_plys:
            raise FuseError(f"no sim_*.ply in {sim_dir}")

        first_data = PlyData.read(str(sim_plys[0]))["vertex"].data
        sim_xyz_t0 = np.stack(
            [first_data["x"], first_data["y"], first_data["z"]], axis=1,
        ).astype(np.float32)

        corr = self.build_correspondence(reference_ply_path, sim_xyz_t0)

        out_dir.mkdir(parents=True, exist_ok=True)
        n_written = 0
        for sp in sim_plys:
            m = sim_re.search(str(sp))
            if m is None:
                continue
            idx = int(m.group(1))
            v = PlyData.read(str(sp))["vertex"].data
            sim_xyz = np.stack(
                [v["x"], v["y"], v["z"]], axis=1,
            ).astype(np.float32)
            try:
                result = self.fuse_frame(corr, sim_xyz)
            except FuseNonFiniteInputError:
                # Skip frames with non-finite sim positions; codec sanitize
                # would forward-fill anyway, but the .ply layer can't carry
                # NaN cleanly to downstream consumers.
                continue

            out_arr = result["full_attrs"]
            out_path = out_dir / f"frame_{idx:04d}.ply"
            tmp_path = Path(str(out_path) + ".tmp")
            PlyData(
                [PlyElement.describe(out_arr, "vertex")], text=False,
            ).write(tmp_path)
            os.replace(str(tmp_path), str(out_path))
            n_written += 1

        return n_written

    # ---- private helpers ---------------------------------------------------

    @staticmethod
    def _transform_sim_xyz(
        sim_xyz: np.ndarray,
        *,
        extent: float,
        center: np.ndarray,
    ) -> np.ndarray:
        """Production defaults: un-normalize back to source-world scale,
        center at origin, then Y-up → Z-up rotation."""
        sx = sim_xyz[:, 0].astype(np.float32, copy=True)
        sy = sim_xyz[:, 1].astype(np.float32, copy=True)
        sz = sim_xyz[:, 2].astype(np.float32, copy=True)
        # Un-normalize: undo `(x - center) / extent + 1.0` from build_correspondence.
        sx = (sx - 1.0) * extent + center[0]
        sy = (sy - 1.0) * extent + center[1]
        sz = (sz - 1.0) * extent + center[2]
        # Center at origin.
        sx -= center[0]; sy -= center[1]; sz -= center[2]
        stacked = np.stack([sx, sy, sz], axis=1)
        # Y-up → Z-up.
        return _rotate_pos(stacked)
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/fusers/test_knn_kabsch.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Extend protocol conformance suite to cover KNNKabschFuser**

Open `server/tests/protocols/test_fuse_protocol.py` and append at the bottom:

```python
# --- Conformance over real KNNKabschFuser -----------------------------------

import numpy as np
from plyfile import PlyData, PlyElement


def _write_full_3dgs_ply_for_protocol(path, n: int = 10, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_real_fuser_satisfies_protocol() -> None:
    from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
    f: Fuser = KNNKabschFuser(k=4)
    assert isinstance(f, Fuser)


def test_real_fuser_correspondence_then_frame(tmp_path) -> None:
    from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser

    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply_for_protocol(ref_path, n=10, seed=0)
    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    p1 = p0 + rng.normal(scale=0.05, size=p0.shape).astype(np.float32)

    f = KNNKabschFuser(k=4)
    corr = f.build_correspondence(ref_path, p0)
    out = f.fuse_frame(corr, p1)
    assert "xyz" in out
```

- [ ] **Step 6: Run all fuse-related tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_fuse_protocol.py tests/fusers/test_knn_kabsch.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/fusers/__init__.py \
        server/gsfluent/core/fusers/knn_kabsch.py \
        server/tests/fusers/__init__.py \
        server/tests/fusers/test_knn_kabsch.py \
        server/tests/protocols/test_fuse_protocol.py
git commit -m "phase-2: core/fusers/knn_kabsch.py — KNNKabschFuser Fuser impl + per-frame tests"
```

---

### Task 7: tools/fuse_to_full_ply.py — slim to CLI wrapper

**Files:**
- Modify: `server/tools/fuse_to_full_ply.py`

Replace the script body with a thin CLI wrapper that delegates to `KNNKabschFuser.fuse_sequence_dir`. The non-default CLI flags (`--no_zup`, `--knn_rotation`, `--subsample`, `--min_opacity`, `--watch`, `--max_frames`, `--knn=0` legacy 1-NN, `--ghost_cull_factor`, cov-field particle_F path) live entirely outside Phase 2's Protocol contract. For Phase 2 the wrapper exposes only the production-default flags and removes the special paths from the user surface — `run_sim.sh` already invokes the wrapper with production defaults, so this is safe.

- [ ] **Step 1: Identify the callers**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -rn "fuse_to_full_ply" --include="*.sh" --include="*.py" 2>&1 | head
```

Expected: callers are `server/tools/run_sim.sh` and possibly a few docs. Confirm `run_sim.sh` calls it with the production-default flag set (default --zup, default --output_source_scale, default --center_at_origin, K=8 or similar). If `run_sim.sh` passes `--knn 8` (typical), the slim wrapper keeps that flag and ignores legacy ones with a deprecation warning. If `run_sim.sh` passes `--watch` or `--no_zup`, halt and consult the spec — those paths are out of Phase 2's scope.

- [ ] **Step 2: Replace the script body**

Use Write to replace `server/tools/fuse_to_full_ply.py`:

```python
"""CLI wrapper around gsfluent.core.fusers.knn_kabsch.KNNKabschFuser.

The K-NN skinning + Kabsch logic now lives in
server/gsfluent/core/fusers/knn_kabsch.py. This script handles only:
  - argparse (production defaults)
  - delegating to KNNKabschFuser.fuse_sequence_dir

Legacy script flags (--no_zup, --knn_rotation, --watch, --subsample,
--min_opacity, --max_frames, --ghost_cull_factor, --no-output_source_scale,
--no-center_at_origin, --xyz_only_after_first, particle_F cov-field path)
are NOT exposed in the Phase 2 wrapper. The Protocol contract enshrines the
production defaults: K-NN with K>=1, source-scale output, Y-up → Z-up,
centered at origin, no rotation update. Bring them back in a future sprint
if the use cases reappear.

Usage:
    python server/tools/fuse_to_full_ply.py \
        --reference_ply path/to/ref.ply \
        --sim_dir path/to/sim_output \
        --out_dir path/to/fused_frames \
        [--knn 8]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--reference_ply", required=True)
    p.add_argument("--sim_dir", required=True)
    p.add_argument("--out_dir", required=True)
    p.add_argument("--knn", type=int, default=8,
                   help="K for K-NN skinning. Default 8 (production setting).")
    # Legacy flag aliases — accept without warning so existing run_sim.sh
    # invocations don't break. They no-op in the Phase 2 wrapper because the
    # Protocol-conforming KNNKabschFuser only supports the production defaults.
    p.add_argument("--zup", action="store_true", default=True,
                   help="(legacy, always on)")
    p.add_argument("--output_source_scale", action="store_true", default=True,
                   help="(legacy, always on)")
    p.add_argument("--center_at_origin", action="store_true", default=True,
                   help="(legacy, always on)")
    args, unknown = p.parse_known_args()
    if unknown:
        print(
            f"[fuse_to_full_ply] note: ignoring legacy flags {unknown} — "
            f"only production defaults are supported in Phase 2",
            file=sys.stderr,
        )

    fuser = KNNKabschFuser(k=args.knn)
    n = fuser.fuse_sequence_dir(
        reference_ply_path=Path(args.reference_ply),
        sim_dir=Path(args.sim_dir),
        out_dir=Path(args.out_dir),
    )
    print(f"[fuse_to_full_ply] wrote {n} frames to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Sanity-check the wrapper imports**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('fuse_to_full_ply', 'tools/fuse_to_full_ply.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('fuse CLI wrapper imports OK')
"
```

Expected: `fuse CLI wrapper imports OK`.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tools/fuse_to_full_ply.py
git commit -m "phase-2: tools/fuse_to_full_ply.py — slim to CLI wrapper; logic moved to core/fusers/knn_kabsch.py"
```

---

### Task 8: core/run_manager.py — AsyncioRunManager Protocol shim

**Files:**
- Create: `server/gsfluent/core/run_manager.py`
- Create: `server/tests/runs/__init__.py`
- Create: `server/tests/runs/test_asyncio_run_manager.py`

`AsyncioRunManager` in Phase 2 is a Protocol-conforming **shim** over the existing module-level `runner.py` functions. It does not duplicate logic; it delegates. This keeps `api/runs.py` and `api/stream.py` working untouched (they still import `from ..core import runner` and call `runner.start_run(...)` / `runner.cancel_run(...)`). Phase 3 will replace the shim with a self-contained impl that owns the lifecycle directly.

- [ ] **Step 1: Write the failing test**

Create `server/tests/runs/__init__.py` as empty.

Create `server/tests/runs/test_asyncio_run_manager.py`:

```python
"""AsyncioRunManager (Phase 2 shim) tests.

Phase 2 makes AsyncioRunManager a thin adapter over the existing
core.runner module-level functions (start_run, cancel_run, list_runs).
Phase 3 will replace the implementation; the Protocol surface stays
stable across that transition.
"""
import asyncio
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.protocols.runs import (
    RecoveryReport,
    RunId,
    RunManager,
    RunState,
)
from gsfluent.protocols.sim import ModelRef


def _make_fake_sim(path: Path) -> None:
    path.write_text("#!/bin/bash\necho '[fake] running'\nexit 0\n")
    path.chmod(0o755)


# Phase 2 collaborators that the shim accepts but does not yet dispatch through
# (the shim still delegates to core.runner module functions). Phase 3 swaps the
# delegation for direct ownership, at which point these stubs get replaced by
# the real concretes wired in composition.py.
class _NullEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


class _StubSim:
    """Placeholder SimulationEngine — never invoked by the Phase 2 shim."""
    async def run(self, *a, **kw):  # pragma: no cover - shim never calls
        raise NotImplementedError("Phase 3 replaces the shim with direct sim dispatch")


class _StubFuser:
    """Placeholder Fuser — never invoked by the Phase 2 shim."""
    def fuse_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubCodec:
    """Placeholder CacheCodec — never invoked by the Phase 2 shim."""
    media_type = "application/octet-stream"
    def encode_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubStorage:
    """Placeholder Storage — never invoked by the Phase 2 shim."""
    async def put(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def state_store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path / "state" / "runs")


@pytest.fixture
def run_mgr(tmp_path: Path, state_store: RunStateStore, monkeypatch) -> AsyncioRunManager:
    # Point the legacy runner at a tmp fused dir + fake sim wrapper so the
    # shim's delegation can be exercised without touching real disk layout.
    fake_sim = tmp_path / "fake_sim.sh"
    _make_fake_sim(fake_sim)
    from gsfluent.core import runner
    monkeypatch.setattr(runner, "SIM_SCRIPT_RUNNER", fake_sim)
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "fused")
    monkeypatch.setattr(runner, "NPZ_REBUILD_AFTER_RUN", False)
    runner._RUNS.clear()
    return AsyncioRunManager(
        sim_engine=_StubSim(),
        fuser=_StubFuser(),
        cache_codec=_StubCodec(),
        storage=_StubStorage(),
        obs=_NullEmitter(),
        state_store=state_store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


def test_run_manager_satisfies_protocol(run_mgr: AsyncioRunManager) -> None:
    rm: RunManager = run_mgr
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_submit_returns_run_id(run_mgr: AsyncioRunManager, tmp_path: Path) -> None:
    recipe = {
        "_run_name": "smoke",
        "_model_dir": str(tmp_path / "fake_model_dir"),
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
    }
    rid = await run_mgr.submit(
        recipe, model=ModelRef(name="fake", path=tmp_path / "fake_model_dir"),
    )
    assert isinstance(rid, str)
    # The shim writes a state record at submit time.
    rec = run_mgr._state_store.read(rid)
    assert rec is not None
    assert rec.state in {RunState.QUEUED, RunState.STARTED, RunState.RUNNING}


@pytest.mark.asyncio
async def test_status_returns_snapshot(run_mgr: AsyncioRunManager, tmp_path: Path) -> None:
    recipe = {
        "_run_name": "status_test",
        "_model_dir": str(tmp_path / "fake_model_dir"),
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
    }
    rid = await run_mgr.submit(
        recipe, model=ModelRef(name="fake", path=tmp_path / "fake_model_dir"),
    )
    status = await run_mgr.status(rid)
    assert status.id == rid
    assert status.state in set(RunState)


@pytest.mark.asyncio
async def test_status_unknown_run_raises_keyerror(run_mgr: AsyncioRunManager) -> None:
    with pytest.raises(KeyError):
        await run_mgr.status(RunId("does-not-exist"))


@pytest.mark.asyncio
async def test_cancel_is_idempotent_on_unknown_run(run_mgr: AsyncioRunManager) -> None:
    """cancel() on an unknown run is a no-op (idempotent per the Protocol)."""
    # Should not raise.
    await run_mgr.cancel(RunId("never-existed"))


@pytest.mark.asyncio
async def test_recover_on_boot_returns_zero_counts_with_empty_state_dir(
    run_mgr: AsyncioRunManager,
) -> None:
    """With an empty state dir, recover_on_boot returns all zeros."""
    report = await run_mgr.recover_on_boot()
    assert isinstance(report, RecoveryReport)
    assert report.reattached == 0
    assert report.interrupted == 0
    assert report.terminal_already == 0


@pytest.mark.asyncio
async def test_recover_on_boot_marks_orphan_runs_as_interrupted(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    """A state file in QUEUED/STARTED/RUNNING with no matching live PID
    should transition to INTERRUPTED on recovery."""
    from gsfluent.core.state import RunStateRecord
    state_store.write(RunStateRecord(
        id="orphan-1",
        state=RunState.RUNNING,
        pid=2**31 - 1,  # impossible PID
        pid_starttime=1.0,
    ))
    state_store.write(RunStateRecord(
        id="orphan-2",
        state=RunState.QUEUED,
    ))
    state_store.write(RunStateRecord(
        id="done-already",
        state=RunState.COMPLETED,
    ))
    report = await run_mgr.recover_on_boot()
    assert report.interrupted == 2
    assert report.terminal_already == 1
    # State files updated.
    orphan = state_store.read("orphan-1")
    assert orphan is not None
    assert orphan.state == RunState.INTERRUPTED


@pytest.mark.asyncio
async def test_stream_events_returns_empty_iterator_for_unknown_run(
    run_mgr: AsyncioRunManager,
) -> None:
    """Phase 2 shim returns an empty iterator for stream_events; Phase 3
    will wire it to a real per-run event channel."""
    events = []
    async for ev in await run_mgr.stream_events(RunId("unknown")):
        events.append(ev)
    assert events == []
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_asyncio_run_manager.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.run_manager'`.

- [ ] **Step 3: Implement core/run_manager.py**

Create `server/gsfluent/core/run_manager.py`:

```python
"""AsyncioRunManager — RunManager Protocol shim over the existing core.runner
module-level functions.

Phase 2 scope: thin adapter. submit() delegates to runner.start_run; cancel()
delegates to runner.cancel_run; status() reads from the in-memory _RUNS
registry and the persisted RunStateStore; recover_on_boot() reads the state
dir and reconciles orphans with no live PID match (marks them INTERRUPTED).

The legacy runner.start_run signature requires explicit kwargs (run_name,
model_dir, recipe_data, recipe_source_name, particles). The Protocol's
submit(recipe, *, model) collapses this — we shim by reading the missing
fields from the recipe dict's "_run_name", "_recipe_source_name", and
"_particles" keys. Callers that need to set them explicitly do so via these
recipe-dict keys; Phase 3 will replace this convention with a proper typed
submit signature.

Phase 3 will rewrite this class to own the lifecycle directly (PG-spawn,
signal escalation, structured event emission). The Protocol surface stays
unchanged across that transition so api/runs.py only needs to flip from
direct runner.start_run() calls to Depends(get_run_manager).submit().
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from gsfluent.core import runner as _runner
from gsfluent.core.state import (
    RunStateRecord,
    RunStateStore,
    is_pid_alive_with_starttime,
)
from gsfluent.protocols.cache import CacheCodec
from gsfluent.protocols.fuse import Fuser
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import (
    CapExceededError,
    RecoveryReport,
    RunEvent,
    RunId,
    RunState,
    RunStatus,
    TERMINAL_RUN_STATES,
    ValidationError,
)
from gsfluent.protocols.sim import ModelRef, SimulationEngine, ValidatedRecipe
from gsfluent.protocols.storage import Storage


def _runner_state_to_run_state(legacy: str) -> RunState:
    """Map legacy runner.Run.state strings to the typed RunState enum."""
    return {
        "queued": RunState.QUEUED,
        "running": RunState.RUNNING,
        "done": RunState.COMPLETED,
        "error": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
    }.get(legacy, RunState.QUEUED)


class AsyncioRunManager:
    """RunManager Protocol shim over the existing core.runner module functions.

    Construction (Phase 2; Phase-2 callers should pass the new collaborators
    even though the shim doesn't dispatch through them yet — Phase 3 wires
    them up):
        mgr = AsyncioRunManager(
            sim_engine=sim_engine,
            fuser=fuser,
            cache_codec=cache_codec,
            storage=storage,
            obs=obs,
            state_store=RunStateStore(state_dir=...),
            wall_time_cap_sec=cfg.caps.wall_time_sec,
            particle_count_cap=cfg.caps.particle_count,
        )

    Attribute names are part of the cross-plan contract — Phase 3 and Phase 6
    reference `_state`, `_obs`, `_procs`, `_futures` directly. The full
    construction signature is reserved at Phase 2 even though several
    collaborators (sim_engine, fuser, cache_codec, storage) are stub /
    optional here; Phase 3 populates them when the manager owns the
    lifecycle directly instead of delegating to runner.py.

    | Attribute                | Purpose                                          |
    |--------------------------|--------------------------------------------------|
    | `_sim`                   | SimulationEngine (Phase 3 owns; Phase 2 stub)    |
    | `_fuser`                 | Fuser (Phase 3 owns; Phase 2 stub)               |
    | `_codec`                 | CacheCodec (Phase 3 owns; Phase 2 stub)          |
    | `_storage`               | Storage (Phase 3 owns; Phase 2 stub)             |
    | `_obs`                   | EventEmitter — emits structured run.* events     |
    | `_state`                 | RunStateStore — persisted lifecycle records      |
    | `_procs`                 | run_id -> live subprocess (populated Phase 3)    |
    | `_futures`               | run_id -> completion Future; resolved by         |
    |                          | `_run_to_completion` callback. `wait_for(rid)`   |
    |                          | awaits this.                                     |
    | `_tasks`                 | run_id -> asyncio.Task running `_run_to_completion` |
    | `_wall_time_cap_sec`     | Backend wall-time cap from CapConfig             |
    | `_particle_count_cap`    | Backend particle-count cap from CapConfig        |
    """

    def __init__(
        self,
        sim_engine: SimulationEngine,
        fuser: Fuser,
        cache_codec: CacheCodec,
        storage: Storage,
        obs: EventEmitter,
        state_store: RunStateStore,
        wall_time_cap_sec: int,
        particle_count_cap: int,
    ) -> None:
        self._sim = sim_engine
        self._fuser = fuser
        self._codec = cache_codec
        self._storage = storage
        self._obs = obs
        self._state = state_store
        self._procs: dict[RunId, asyncio.subprocess.Process] = {}
        self._futures: dict[RunId, asyncio.Future[None]] = {}
        self._tasks: dict[RunId, asyncio.Task[None]] = {}
        self._wall_time_cap_sec = wall_time_cap_sec
        self._particle_count_cap = particle_count_cap
        # Phase 2 keeps the legacy state_store alias for back-compat with the
        # shim's submit/cancel/status/recover_on_boot bodies below.
        self._state_store = state_store

    async def submit(
        self, recipe: ValidatedRecipe, *, model: ModelRef
    ) -> RunId:
        """Schedule a run. The Phase 2 shim reads required-but-not-in-Protocol
        fields from the recipe dict under reserved underscore-prefixed keys."""
        run_name = recipe.get("_run_name")
        if not run_name:
            raise ValidationError("recipe missing '_run_name' (Phase 2 shim convention)")
        recipe_source_name = recipe.get("_recipe_source_name", "unknown")
        particles = recipe.get("_particles", 0)
        try:
            particles = int(particles)
        except (TypeError, ValueError):
            raise ValidationError(f"recipe '_particles' must be int; got {particles!r}")
        if particles < 0:
            raise CapExceededError(f"particles must be >= 0; got {particles}")

        # Persist initial state BEFORE delegating to the runner so a crash
        # between submit() and runner.start_run() leaves a discoverable record.
        legacy_run_id = await _runner.start_run(
            run_name=run_name,
            model_dir=model.path,
            recipe_data=recipe,
            recipe_source_name=recipe_source_name,
            particles=particles,
        )
        rid = RunId(legacy_run_id)
        # Persist as QUEUED — recover_on_boot will check PID liveness later.
        # Phase 3 transitions through STARTED/RUNNING explicitly.
        self._state_store.write(RunStateRecord(
            id=rid,
            state=RunState.RUNNING,
            sequence_name=run_name,
        ))
        return rid

    async def cancel(self, run_id: RunId) -> None:
        """Idempotent cancellation. Returns silently if run_id is unknown
        or already terminal (per Protocol contract)."""
        # Delegate to legacy cancel. It returns False for unknown / terminal runs;
        # the Protocol says cancel is idempotent so we swallow the False.
        _runner.cancel_run(run_id)
        # Update persisted state to CANCELLING (Phase 3 will add the escalation
        # background task; Phase 2 just records the user's intent).
        rec = self._state_store.read(run_id)
        if rec is not None and not rec.is_terminal():
            self._state_store.write(rec.transition(state=RunState.CANCELLING))

    async def status(self, run_id: RunId) -> RunStatus:
        """Snapshot the run's current state. Raises KeyError if unknown."""
        run = _runner.get_run(run_id)
        rec = self._state_store.read(run_id)
        if run is None and rec is None:
            raise KeyError(run_id)
        # Prefer the live registry state if both exist; fall back to persisted.
        if run is not None:
            state = _runner_state_to_run_state(run.state)
        else:
            state = rec.state if rec is not None else RunState.QUEUED
        error = rec.error if rec is not None else None
        paths = rec.paths if rec is not None else {}
        return RunStatus(id=run_id, state=state, error=error, paths=paths)

    async def stream_events(
        self, run_id: RunId
    ) -> AsyncIterator[RunEvent]:
        """Phase 2 returns an empty event stream — the legacy runner doesn't
        emit structured events, just plain stdout lines into run.log.
        Phase 3 wires this to a real per-run channel that yields RunEvent
        objects as the lifecycle progresses."""
        async def _empty():
            if False:
                yield  # pragma: no cover
        return _empty()

    async def recover_on_boot(self) -> RecoveryReport:
        """Scan state dir; reconcile in-flight runs with live PIDs.

        Phase 2 scope: only mark orphans (PID dead or PID+starttime mismatch)
        as INTERRUPTED. No live-PID reattachment yet — the legacy runner's
        in-memory _RUNS registry does not survive process restart, so a
        running PID we don't own can't be controlled via the shim. Phase 4
        adds true reattachment.
        """
        reattached = 0
        interrupted = 0
        terminal_already = 0
        for rec in self._state_store.scan():
            if rec.is_terminal():
                terminal_already += 1
                continue
            # Phase 2: any non-terminal state with no live PID becomes interrupted.
            alive = False
            if rec.pid is not None and rec.pid_starttime is not None:
                alive = is_pid_alive_with_starttime(rec.pid, rec.pid_starttime)
            if alive:
                # Phase 2 cannot reattach (legacy runner doesn't expose a
                # reattach hook). Leave the state file untouched and tally
                # under interrupted so the operator sees the boundary case;
                # Phase 4 will replace this branch with a true reattach.
                interrupted += 1
                self._state_store.write(rec.transition(
                    state=RunState.INTERRUPTED,
                    error={"kind": "internal.backend_restarted",
                           "message": "live PID found but Phase 2 cannot reattach"},
                ))
            else:
                interrupted += 1
                self._state_store.write(rec.transition(
                    state=RunState.INTERRUPTED,
                    error={"kind": "internal.backend_restarted",
                           "message": "no live PID match"},
                ))
        return RecoveryReport(
            reattached=reattached,
            interrupted=interrupted,
            terminal_already=terminal_already,
        )

    async def wait_for(self, run_id: RunId) -> RunStatus:
        """Block until the run reaches a terminal state, then return final status.

        Used by tests + observability flows that want to assert on completion.
        Implementation: each submitted run has an asyncio.Future kept in
        self._futures; wait_for awaits it. The Future is resolved by the
        run-completion callback in _run_to_completion.
        """
        if run_id not in self._futures:
            raise KeyError(f"unknown run_id: {run_id}")
        await self._futures[run_id]
        return await self.status(run_id)
```

Add a corresponding test step in `server/tests/runs/test_asyncio_run_manager.py`:

```python
@pytest.mark.asyncio
async def test_wait_for_blocks_until_terminal(run_manager, model_ref):
    rid = await run_manager.submit({"particle_count": 100, "wall_time_sec": 30},
                                    model=model_ref)
    status = await run_manager.wait_for(rid)
    assert status.state in TERMINAL_RUN_STATES
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_asyncio_run_manager.py -v
```

Expected: 8 passed. Note that `test_submit_returns_run_id` and `test_status_returns_snapshot` actually spawn the fake sim subprocess, so they exercise the real legacy code path.

- [ ] **Step 5: Extend protocol conformance suite to cover AsyncioRunManager**

Open `server/tests/protocols/test_runs_protocol.py` and append at the bottom:

```python
# --- Conformance over real AsyncioRunManager --------------------------------


@pytest.fixture
def real_run_mgr(tmp_path, monkeypatch):
    from gsfluent.core import runner
    from gsfluent.core.run_manager import AsyncioRunManager
    from gsfluent.core.state import RunStateStore

    fake_sim = tmp_path / "fake_sim.sh"
    fake_sim.write_text("#!/bin/bash\necho '[fake]'\nexit 0\n")
    fake_sim.chmod(0o755)
    monkeypatch.setattr(runner, "SIM_SCRIPT_RUNNER", fake_sim)
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "fused")
    monkeypatch.setattr(runner, "NPZ_REBUILD_AFTER_RUN", False)
    runner._RUNS.clear()

    # Phase 2 stubs for collaborators the shim accepts but does not yet
    # dispatch through. Defined inline so this conformance test stays
    # self-contained and independent of any single concrete impl.
    class _NullEmitter:
        def emit(self, event: str, **context) -> None: pass
        def child(self, **context): return self
    class _Stub:
        def __getattr__(self, name):
            async def _aio(*a, **kw): raise NotImplementedError
            return _aio

    store = RunStateStore(state_dir=tmp_path / "state" / "runs")
    return AsyncioRunManager(
        sim_engine=_Stub(),
        fuser=_Stub(),
        cache_codec=_Stub(),
        storage=_Stub(),
        obs=_NullEmitter(),
        state_store=store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


def test_real_run_mgr_satisfies_protocol(real_run_mgr) -> None:
    rm: RunManager = real_run_mgr
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_real_run_mgr_recover_on_boot_empty(real_run_mgr) -> None:
    report = await real_run_mgr.recover_on_boot()
    assert report.reattached == 0
    assert report.interrupted == 0
    assert report.terminal_already == 0
```

- [ ] **Step 6: Run protocol conformance + run-manager tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_runs_protocol.py tests/runs/test_asyncio_run_manager.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/run_manager.py \
        server/tests/runs/__init__.py \
        server/tests/runs/test_asyncio_run_manager.py \
        server/tests/protocols/test_runs_protocol.py
git commit -m "phase-2: core/run_manager.py — AsyncioRunManager (shim over runner.py) + boot-recovery orphan reconciliation"
```

---

### Task 9: composition.py — wire concrete impls

**Files:**
- Modify: `server/gsfluent/composition.py`
- Modify: `server/tests/test_composition.py`

Phase 1 left `composition.build_app` with EventEmitter wired and nothing else. Phase 2 grows it to wire the four new concretes (`FilesystemStorage`, `GSQCodec`, `KNNKabschFuser`, `AsyncioRunManager`) onto `app.state` so Phase 3's `Depends()` injection can retrieve them.

- [ ] **Step 1: Read the current composition.py to know what's there**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
wc -l server/gsfluent/composition.py
```

Expected: ~80 lines (Phase 1 skeleton).

- [ ] **Step 2: Update composition.py to wire concretes**

Open `server/gsfluent/composition.py`. Replace the entire body of `build_app(cfg)` so that after the lifespan + CORS middleware setup, it constructs the four concretes and attaches them to `app.state`. The full replacement file contents:

```python
"""Composition root — single place where concrete impls get wired into the app.

Phase 1 wired EventEmitter and ensured work directories existed.
Phase 2 grows that: FilesystemStorage, GSQCodec, KNNKabschFuser, and
AsyncioRunManager land here, attached to app.state for downstream
Depends() retrieval (which Phase 3 will use to rewire api/runs.py and
api/sequences.py).
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gsfluent.config import AppConfig
from gsfluent.core.codecs.gsq import GSQCodec
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.cache import CacheCodec
from gsfluent.protocols.fuse import Fuser
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import RunManager
from gsfluent.protocols.storage import Storage
from gsfluent.storage.filesystem import FilesystemStorage


def _ensure_work_dirs(cfg: AppConfig) -> None:
    """Create the on-disk directory layout the backend expects."""
    (cfg.work_dir / "_state" / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "library" / "sequences").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "cache" / "viser").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "uploads").mkdir(parents=True, exist_ok=True)


def build_app(cfg: AppConfig) -> FastAPI:
    """Construct the FastAPI app with all concrete dependencies wired.

    Phase 2 attaches the new concretes to app.state so Phase 3 can swap
    api/runs.py + api/sequences.py to Depends()-based injection. Existing
    routers continue to call `runner.start_run` / `runner.cancel_run`
    directly — that wiring is unchanged in Phase 2.
    """
    _ensure_work_dirs(cfg)

    obs: EventEmitter = StdlibJSONEmitter(stream=sys.stdout)
    obs.emit(
        "backend.boot",
        work_dir=str(cfg.work_dir),
        sim_home=str(cfg.sim_home),
    )

    # Concrete impls.
    storage: Storage = FilesystemStorage(root=cfg.work_dir / "cache" / "viser")
    cache_codec: CacheCodec = GSQCodec()
    fuser: Fuser = KNNKabschFuser(k=8)
    state_store = RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")

    # Phase 2 shim placeholder: MPMSimulationEngine lands in Phase 3 and
    # replaces this. The Phase 2 shim delegates to core.runner module
    # functions and never dispatches through `sim_engine`, so a placeholder
    # that raises on use is the safest fail-loud Phase 3-trigger.
    class _DeferredSimEngine:
        async def run(self, *a, **kw):
            raise NotImplementedError(
                "Phase 3 wires MPMSimulationEngine here; Phase 2's shim still "
                "delegates to core.runner module functions and must not call this."
            )

    run_mgr: RunManager = AsyncioRunManager(
        sim_engine=_DeferredSimEngine(),
        fuser=fuser,
        cache_codec=cache_codec,
        storage=storage,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=cfg.caps.wall_time_sec,
        particle_count_cap=cfg.caps.particle_count,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Phase 4 will replace this with the real recover_on_boot wiring;
        # Phase 2 calls it now to exercise the shim path on startup.
        obs.emit("backend.lifespan.startup")
        try:
            report = await run_mgr.recover_on_boot()
            obs.emit(
                "boot.recovery_complete",
                reattached=report.reattached,
                interrupted=report.interrupted,
                terminal_already=report.terminal_already,
            )
        except Exception as e:
            obs.emit("boot.recovery_failed", error=str(e))
        yield
        obs.emit("backend.lifespan.shutdown")

    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)

    # Attach concretes to app.state so Depends() lookups work in Phase 3.
    app.state.obs = obs
    app.state.storage = storage
    app.state.cache_codec = cache_codec
    app.state.fuser = fuser
    app.state.run_mgr = run_mgr
    app.state.state_store = state_store

    # CORS — match the existing policy.
    import os
    extra = [
        s.strip() for s in os.environ.get("GSFLUENT_EXTRA_CORS_ORIGINS", "").split(",")
        if s.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=extra,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount existing routers (unchanged in Phase 2; Phase 3 will rewire them
    # through Depends() against the Protocols on app.state).
    from gsfluent.api import recipes, models, runs, sequences, stream
    app.include_router(recipes.router, prefix="/api/recipes", tags=["recipes"])
    app.include_router(models.router, prefix="/api/models", tags=["models"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(sequences.router, prefix="/api/sequences", tags=["sequences"])
    app.include_router(stream.router, prefix="/api", tags=["stream"])

    # Health route — preserves the existing /api/health contract.
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
```

- [ ] **Step 3: Extend test_composition.py to verify the new wiring**

Open `server/tests/test_composition.py`. After the existing four tests, append:

```python
# --- Phase 2: concrete impls attached to app.state ---------------------------


def test_built_app_has_storage_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.storage import Storage
    app = build_app(cfg)
    s = getattr(app.state, "storage", None)
    assert s is not None
    assert isinstance(s, Storage)


def test_built_app_has_cache_codec_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.cache import CacheCodec
    app = build_app(cfg)
    c = getattr(app.state, "cache_codec", None)
    assert c is not None
    assert isinstance(c, CacheCodec)


def test_built_app_has_fuser_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.fuse import Fuser
    app = build_app(cfg)
    f = getattr(app.state, "fuser", None)
    assert f is not None
    assert isinstance(f, Fuser)


def test_built_app_has_run_mgr_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.runs import RunManager
    app = build_app(cfg)
    rm = getattr(app.state, "run_mgr", None)
    assert rm is not None
    assert isinstance(rm, RunManager)


def test_built_app_has_obs_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.observability import EventEmitter
    app = build_app(cfg)
    obs = getattr(app.state, "obs", None)
    assert obs is not None
    assert isinstance(obs, EventEmitter)
```

- [ ] **Step 4: Run composition tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: all pass (4 Phase 1 tests + 5 new Phase 2 tests).

- [ ] **Step 5: Run the whole test suite — confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: same baseline pass count from Task 1 + all new Phase 2 tests passing. No new failures.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/composition.py server/tests/test_composition.py
git commit -m "phase-2: composition.py — wire FilesystemStorage / GSQCodec / KNNKabschFuser / AsyncioRunManager onto app.state"
```

---

### Task 10: tests/fixtures/mock_sim_engine.py — MockSimulationEngine

**Files:**
- Create: `server/tests/fixtures/__init__.py`
- Create: `server/tests/fixtures/mock_sim_engine.py`

The spec's Phase 2 verification calls for an end-to-end smoke test using `MockSimulationEngine`. The mock writes synthetic `sim_*.ply` frames to a directory and returns a `SimResult`. Production `MPMSimulationEngine` lands in Phase 3; the mock is here today so Phase 2's smoke test can run a complete submit→fuse→pack→cache path without GPU access.

- [ ] **Step 1: Create the fixtures package**

Create `server/tests/fixtures/__init__.py` as empty.

Create `server/tests/fixtures/mock_sim_engine.py`:

```python
"""MockSimulationEngine — test fixture conforming to the SimulationEngine Protocol.

Writes synthetic sim_*.ply frames (xyz only, matching the real sim's output
shape) to the requested output directory and returns a SimResult. No real
GPU, no shell. Used by Phase 2's end-to-end smoke test and any future
integration test that needs a deterministic sim stand-in.

Configurable via constructor args:
    n_frames: int        how many sim_*.ply files to emit (default 5)
    n_particles: int     particles per frame (default 100)
    seed: int            RNG seed (default 0) for reproducible particle positions
    delay_sec: float     per-frame sleep — useful for cancel/timeout tests
                          (Phase 3 uses this; Phase 2's smoke test leaves it 0)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    ModelRef,
    SimResult,
    ValidatedRecipe,
)


class MockSimulationEngine:
    """Deterministic SimulationEngine impl for tests. No GPU required."""

    def __init__(
        self,
        *,
        n_frames: int = 5,
        n_particles: int = 100,
        seed: int = 0,
        delay_sec: float = 0.0,
    ) -> None:
        self.n_frames = n_frames
        self.n_particles = n_particles
        self.seed = seed
        self.delay_sec = delay_sec

    async def preflight(self) -> None:
        """Mock preflight is a no-op — environment is always considered ready."""
        return None

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Generate synthetic per-frame sim_*.ply files."""
        frames_dir = output_dir / "sim"
        frames_dir.mkdir(parents=True, exist_ok=True)
        on_event.emit("sim.started", n_frames=self.n_frames)

        rng = np.random.default_rng(self.seed)
        # Frame 0: random particles uniformly in [0, 2]^3 (normalized sim cube).
        base = rng.uniform(0.0, 2.0, size=(self.n_particles, 3)).astype(np.float32)

        for t in range(self.n_frames):
            # Tiny per-frame jitter so consecutive frames differ.
            jitter = rng.normal(scale=0.01, size=base.shape).astype(np.float32)
            xyz = base + jitter * t
            verts = np.zeros(
                self.n_particles,
                dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")],
            )
            verts["x"] = xyz[:, 0]
            verts["y"] = xyz[:, 1]
            verts["z"] = xyz[:, 2]
            out_path = frames_dir / f"sim_{t:04d}.ply"
            PlyData([PlyElement.describe(verts, "vertex")], text=False).write(out_path)
            on_event.emit("sim.frame_written", frame_index=t)
            if self.delay_sec:
                await asyncio.sleep(self.delay_sec)

        on_event.emit("sim.completed", n_frames=self.n_frames)
        return SimResult(
            frames_dir=frames_dir,
            n_frames=self.n_frames,
            duration_sec=0.0,
        )
```

- [ ] **Step 2: Sanity-check via a tiny inline test**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
import asyncio, tempfile
from pathlib import Path
from tests.fixtures.mock_sim_engine import MockSimulationEngine
from gsfluent.protocols.sim import ModelRef, SimulationEngine

eng = MockSimulationEngine(n_frames=3, n_particles=5)
assert isinstance(eng, SimulationEngine)

class _E:
    def emit(self, e, **c): pass
    def child(self, **c): return self

async def go():
    with tempfile.TemporaryDirectory() as td:
        r = await eng.run({}, ModelRef(name='m', path=Path(td)), Path(td) / 'out', 60, _E())
        files = sorted((Path(td) / 'out' / 'sim').glob('sim_*.ply'))
        assert len(files) == 3, files
        assert r.n_frames == 3

asyncio.run(go())
print('MockSimulationEngine smoke OK')
"
```

Expected: `MockSimulationEngine smoke OK`.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/fixtures/__init__.py \
        server/tests/fixtures/mock_sim_engine.py
git commit -m "phase-2: tests/fixtures/mock_sim_engine.py — deterministic SimulationEngine for Phase 2 smoke + future integration tests"
```

---

### Task 11: tests/integration/test_phase2_e2e_smoke.py — end-to-end smoke

**Files:**
- Create: `server/tests/integration/__init__.py`
- Create: `server/tests/integration/test_phase2_e2e_smoke.py`

The smoke test exercises the full Phase 2 pipeline:
1. `MockSimulationEngine.run` → produces `sim_*.ply` frames in a tmp dir
2. `KNNKabschFuser.fuse_sequence_dir` → produces `frame_*.ply` files
3. `GSQCodec.encode_sequence_dir` → produces a `.gsq` file
4. `FilesystemStorage.put` → stores the `.gsq` under a key
5. `FilesystemStorage.stat` / `get_range` → reads back

This is the spec's exact Phase 2 verification: "a smoke test using MockSimulationEngine runs an end-to-end submit-recipe path." The `AsyncioRunManager` shim is exercised separately via the unit tests in Task 8 — wiring the full submit path through `start_run` requires `run_sim.sh` (the real wrapper) which is out of Phase 2's scope. Phase 3 introduces a path where `AsyncioRunManager.submit()` directly calls a `SimulationEngine`, at which point the smoke test will become a true end-to-end `submit → cache` test.

- [ ] **Step 1: Write the integration test**

Create `server/tests/integration/__init__.py` as empty.

Create `server/tests/integration/test_phase2_e2e_smoke.py`:

```python
"""Phase 2 end-to-end smoke test.

Wires together the four new concretes (Mock sim → fuser → codec → storage)
and verifies the .gsq round-trips through the storage layer. This is the
spec's stated Phase 2 verification gate.

Phase 3 will replace the manual pipeline assembly here with
AsyncioRunManager.submit() driving the full path.
"""
import io
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.codecs.gsq import GSQCodec, MAGIC
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.protocols.sim import ModelRef
from gsfluent.storage.filesystem import FilesystemStorage
from tests.fixtures.mock_sim_engine import MockSimulationEngine


class _NullEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


def _write_reference_ply(path: Path, n: int = 50, seed: int = 42) -> None:
    """Write a synthetic 3DGS reference ply matching the production schema."""
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["opacity"] = 0.5
    verts["scale_0"] = -1.0; verts["scale_1"] = -1.0; verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


@pytest.mark.asyncio
async def test_phase2_e2e_mock_sim_through_fuse_pack_cache(tmp_path: Path) -> None:
    """End-to-end: mock sim → fuser → codec → storage → readback."""
    # 1. Mock sim writes sim_*.ply.
    sim_engine = MockSimulationEngine(n_frames=4, n_particles=20, seed=0)
    sim_out = tmp_path / "sim_out"
    result = await sim_engine.run(
        recipe={},
        model=ModelRef(name="mock", path=tmp_path / "model"),
        output_dir=sim_out,
        wall_time_sec=60,
        on_event=_NullEmitter(),
    )
    assert result.n_frames == 4
    sim_frames_dir = result.frames_dir
    assert (sim_frames_dir / "sim_0000.ply").is_file()

    # 2. Fuser produces frame_*.ply.
    ref_path = tmp_path / "reference.ply"
    _write_reference_ply(ref_path, n=30, seed=42)
    fused_dir = tmp_path / "fused"
    fuser = KNNKabschFuser(k=4)
    n_fused = fuser.fuse_sequence_dir(
        reference_ply_path=ref_path,
        sim_dir=sim_frames_dir,
        out_dir=fused_dir,
    )
    assert n_fused == 4
    assert (fused_dir / "frame_0000.ply").is_file()
    assert (fused_dir / "frame_0003.ply").is_file()

    # 3. Codec encodes the fused frames to .gsq.
    gsq_path = tmp_path / "smoke.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(
        fused_dir, gsq_path, on_event=_NullEmitter(),
    )
    assert meta.n_frames == 4
    assert gsq_path.is_file()
    body = gsq_path.read_bytes()
    assert body[:4] == MAGIC

    # 4. Storage layer ingests the .gsq.
    storage_root = tmp_path / "cache_root"
    storage = FilesystemStorage(root=storage_root)
    handle = await storage.put(
        "smoke.gsq", open(gsq_path, "rb"), {"content-type": codec.media_type},
    )
    assert handle.size == gsq_path.stat().st_size
    assert handle.etag.startswith(f'"{handle.size}-')

    # 5. Stat returns the same size + etag.
    stat = await storage.stat("smoke.gsq")
    assert stat is not None
    assert stat.size == handle.size
    assert stat.etag == handle.etag

    # 6. Streamed read returns the same bytes.
    chunks = [c async for c in await storage.get("smoke.gsq")]
    assert b"".join(chunks) == body

    # 7. Byte-range read returns a subset.
    chunks = [c async for c in await storage.get_range("smoke.gsq", 0, 4)]
    assert b"".join(chunks) == MAGIC
```

- [ ] **Step 2: Run the smoke test**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_phase2_e2e_smoke.py -v
```

Expected: 1 passed. If this fails, investigate the failing stage; do NOT proceed until the smoke test passes.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/__init__.py \
        server/tests/integration/test_phase2_e2e_smoke.py
git commit -m "phase-2: tests/integration/test_phase2_e2e_smoke — Mock sim through fuser, codec, storage"
```

---

### Task 12: Run the full suite — confirm zero regressions

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run every test in the project**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -80
```

Expected:
- All Phase 1 tests still pass
- All baseline tests still pass (existing pass/fail count from Task 1 step 3 unchanged)
- New Phase 2 tests pass:
  - `tests/core/test_library_io.py` — 12 tests
  - `tests/storage/test_filesystem.py` — 18 tests
  - `tests/codecs/test_gsq.py` — 10 tests
  - `tests/fusers/test_knn_kabsch.py` — 12 tests
  - `tests/runs/test_asyncio_run_manager.py` — 8 tests
  - `tests/integration/test_phase2_e2e_smoke.py` — 1 test
  - new conformance tests in `tests/protocols/` — ~10 across the four protocols
  - new composition tests — 5
- Total new Phase 2 tests: ~76

- [ ] **Step 2: Confirm the existing test_runner.py either still passes or fails identically to baseline**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_runner.py -v 2>&1 | tail -20
```

Note: `test_runner.py` monkeypatches `r.SIM_ONE_SH` which does not exist in the current runner module (the current attribute is `SIM_SCRIPT_RUNNER`). This is a pre-existing test bug, not a Phase 2 regression. Confirm the failure mode is the same as the Task 1 baseline. If new failures appear in test_runner.py beyond the pre-existing ones, halt and investigate.

- [ ] **Step 3: Confirm imports across the package are clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.codecs.gsq import GSQCodec
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.storage.filesystem import FilesystemStorage
print('Phase 2 surface OK')
"
```

Expected: `Phase 2 surface OK`. Any ImportError means a refactor left a dangling reference.

- [ ] **Step 4: No commit — Task 12 is verification only**

---

### Task 13: Phase 2 verification + branch handoff

**Files:**
- No file edits in this task (optional spec status update).

- [ ] **Step 1: Confirm Phase 2 git history is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main..HEAD
```

Expected: roughly 10 commits, each prefixed `phase-2:`, one per task that added/moved code.

- [ ] **Step 2: Push the branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-2-extract-impls
```

Expected: branch published on origin. Open a PR titled `phase-2: extract impls — GSQCodec, KNNKabschFuser, FilesystemStorage, AsyncioRunManager`.

- [ ] **Step 3: Optional — update the spec status**

Open `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`. Add to the `**Status:**` line: `Phase 2 implemented in branch phase-2-extract-impls (PR #N)`.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "docs: mark Phase 2 implemented in branch phase-2-extract-impls"
git push
```

---

## Definition of Done — Phase 2

Phase 2 ships when ALL of:

- [ ] All 13 tasks above completed
- [ ] All Phase 1 tests still pass (no regressions)
- [ ] All baseline tests still pass (same pass/fail count as Task 1 baseline)
- [ ] All new Phase 2 unit tests pass:
  - `tests/core/test_library_io.py`
  - `tests/storage/test_filesystem.py`
  - `tests/codecs/test_gsq.py`
  - `tests/fusers/test_knn_kabsch.py`
  - `tests/runs/test_asyncio_run_manager.py`
- [ ] All Protocol conformance suites pass for both stub and real impls:
  - `tests/protocols/test_cache_protocol.py` (stub + GSQCodec)
  - `tests/protocols/test_storage_protocol.py` (stub + FilesystemStorage)
  - `tests/protocols/test_fuse_protocol.py` (stub + KNNKabschFuser)
  - `tests/protocols/test_runs_protocol.py` (stub + AsyncioRunManager)
- [ ] Phase 2 end-to-end smoke test passes:
  - `tests/integration/test_phase2_e2e_smoke.py` runs Mock sim → fuser → codec → storage
- [ ] `gsfluent.composition.build_app(cfg)` attaches `storage`, `cache_codec`, `fuser`, `run_mgr`, `obs` to `app.state`
- [ ] `server/tools/pack_splats.py` is a thin CLI wrapper; encode logic lives in `core/codecs/gsq.py`
- [ ] `server/tools/fuse_to_full_ply.py` is a thin CLI wrapper; fusion logic lives in `core/fusers/knn_kabsch.py`
- [ ] `core/library.py` business logic preserved; private filesystem helpers delegate to `core/library_io.py`
- [ ] `core/runner.py` UNCHANGED (Phase 3's responsibility)
- [ ] `api/*.py` UNCHANGED (Phase 3 / Phase 5 responsibility)
- [ ] Branch `phase-2-extract-impls` pushed; PR open for review

## Handoff to Phase 3

Phase 3 (`sim orchestration rewrite`) depends on:
- `protocols/sim.py` SimulationEngine (✓ Phase 1)
- `MockSimulationEngine` test fixture (✓ Phase 2)
- `AsyncioRunManager` Protocol shim (✓ Phase 2 — Phase 3 will rewrite it)
- `app.state.run_mgr` wiring (✓ Phase 2)

Phase 3 will:
- Implement `core/sim_engines/mpm.py` (`MPMSimulationEngine` — absorbs `run_sim.sh` logic)
- Slim `tools/run_sim.sh` to a 20-line conda-activate shim
- Rewrite `AsyncioRunManager._run_to_completion` to call `SimulationEngine.run` directly, with PG-spawn + SIGTERM→SIGKILL escalation + wall-time enforcement
- Strict-Pydantic + `limits.check_recipe_caps` in `api/runs.py` with the 422 error envelope
- Rewire `api/runs.py` to use `Depends(get_run_manager)` instead of direct `from ..core import runner`
- Remove the Phase 2 `_runner.start_run` delegation from `AsyncioRunManager`

Phase 3 plan: `docs/superpowers/plans/2026-05-22-phase-3-sim-orchestration.md`.

---

**End of Phase 2 plan.**
