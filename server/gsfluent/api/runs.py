"""Runs API — start/cancel/list active sims plus history-of-past-sequences.

Past runs live in the library at `work/library/sequences/<name>/`. We list
them by walking `library.SEQUENCES_DIR` and reading each sequence's
`_meta.json` (and, where present, the original `manifest.json` carried
over from the runner — it has `started_at`, `status`, `particles`,
`recipe_source` which the HistoryEntry frontend type expects).

The frontend HistoryEntry contract (frontend/src/lib/types.ts) is:
  { run_name, status, started_at, finished_at?, particles?, recipe_source? }
We preserve every field.

Phase 3 hardens the recipe trust boundary: every POST /api/runs body
goes through strict Pydantic validation + limits.check_recipe_caps()
BEFORE any subprocess can spawn. Rejections return the spec's 422
envelope shape `{"error": {"kind", "message", "details", "trace_id"}}`.

Phase 7+ rewire: the route handlers now drive AsyncioRunManager through
the FastAPI app.state hook the composition root wires up. The legacy
`core/runner.py` module was deleted; recipe pre-spawn validators moved
to `core/recipe_validation.py`.
"""
import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from ..api.errors import (
    new_trace_id,
    raise_cap_exceeded,
    raise_validation_error,
)
from ..core import library as lib
from ..core import recipe_validation
from ..core.library import Sequence
from ..core.limits import CapConfig, check_recipe_caps
from ..protocols.runs import CapExceededError, RunId, RunManager
from ..protocols.sim import ModelRef

router = APIRouter(prefix="/api/runs", tags=["runs"])


_SAFE_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


# Legacy run-dir fallback for the API endpoints below. In production this
# equals `lib.SEQUENCES_DIR`; tests monkeypatch it at this location so
# their `<tmp>/fused/<run>/manifest.json` fixtures still exercise the
# legacy-fallback branches in /api/runs/history and friends.
_LEGACY_RUNS_DIR: Path = lib.SEQUENCES_DIR


class StartRunRequest(BaseModel):
    """Strict-mode request body for POST /api/runs.

    Pydantic strict mode rejects unknown fields and refuses type coercion
    (string "100" will not silently become int 100). check_recipe_caps()
    runs after parse to enforce the configured maxima.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    run_name: str = Field(..., min_length=1, max_length=128)
    model_path: str = Field(..., min_length=1)
    recipe_data: dict
    recipe_source: str
    particles: int = Field(default=200_000, gt=0)
    # When True, the handler runs the same validation a real run would
    # (model_path existence, sim_area <-> model bbox overlap, etc.) but
    # never spawns the sim wrapper or touches the library. Useful for
    # compatibility-matrix sanity checks across the recipe library
    # without burning GPU time on actual runs.
    dry_run: bool = False

    @field_validator("run_name")
    @classmethod
    def _run_name_must_be_safe(cls, v: str) -> str:
        if not _SAFE_RUN_NAME_RE.match(v):
            raise ValueError("run_name must match ^[A-Za-z0-9_.-]+$")
        return v


def _caps_dep() -> CapConfig:
    """FastAPI dependency: return the active CapConfig.

    Phase 3 reads from env every request, which is cheap and dodges
    the ordering problem of importing AppConfig at module load. Phase
    6 may replace this with a singleton from the composition root.
    """
    return CapConfig.from_env()


def _get_run_mgr(request: Request) -> RunManager:
    """FastAPI dependency: pull the RunManager from app.state.

    The composition root attaches `app.state.run_mgr` at startup time.
    Tests using `TestClient(create_app())` get the production wiring;
    tests that need to swap in a stub can overwrite `app.state.run_mgr`
    before issuing the request.
    """
    return request.app.state.run_mgr


@router.get("")
def list_active(
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """Active runs only (state == 'running'). Past runs live in
    /api/runs/history (which walks the on-disk library)."""
    return [
        {"id": str(r.id), "name": r.sequence_name or "", "state": r.state.value}
        for r in run_mgr.list_active()
    ]


@router.post("")
async def start(
    raw_body: dict,
    run_mgr: RunManager = Depends(_get_run_mgr),
    caps: CapConfig = Depends(_caps_dep),
):
    """Submit a run. Validates request body in strict mode, then enforces
    recipe caps, then hands the recipe off to run_mgr.submit().

    Rejections return 422 with the standard envelope:
        {"error": {"kind", "message", "details", "trace_id"}}
    """
    trace_id = new_trace_id()

    # ---- 1. strict Pydantic parse ------------------------------------
    try:
        req = StartRunRequest.model_validate(raw_body, strict=True)
    except PydanticValidationError as e:
        # Pick the first error to surface as the kind / message; details
        # carries the full list so the client can show all of them.
        errs = e.errors()
        first = errs[0] if errs else {}
        loc = first.get("loc", ("?",))
        loc_parts = [p for p in loc if p != "body"]
        field = ".".join(str(p) for p in loc_parts) if loc_parts else "?"
        msg = first.get("msg", "validation failed")
        # Pydantic serializes ValueError back-refs containing arbitrary
        # Python objects (e.g. dicts in `input`); cast to JSON-safe types.
        safe_errs: list[dict] = []
        for entry in errs:
            safe_errs.append({
                "loc": [str(p) for p in entry.get("loc", ())],
                "type": entry.get("type", ""),
                "msg": entry.get("msg", ""),
            })
        raise_validation_error(
            kind=f"validation.{field}",
            message=f"{field}: {msg}",
            details={"errors": safe_errs, "trace_id": trace_id},
        )

    # ---- 2. cap check ------------------------------------------------
    # Compose the cap-check input from the request fields the orchestrator
    # actually consumes. recipe_data carries the customer's free-form
    # recipe; we add particle_count from the structured request field for
    # cap-checking purposes.
    cap_input = {
        **req.recipe_data,
        "particle_count": req.particles,
    }
    try:
        check_recipe_caps(cap_input, caps)
    except CapExceededError as e:
        # Translate cap-checker exception messages into typed kinds.
        msg = str(e)
        if "Particle count" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.particle_count",
                message=msg,
                details={"requested": req.particles, "limit": caps.max_particle_count},
            )
        if "Wall-time" in msg:
            wt = int(req.recipe_data.get("wall_time_sec", caps.max_wall_time_sec))
            raise_cap_exceeded(
                kind="cap_exceeded.wall_time",
                message=msg,
                details={"requested": wt, "limit": caps.max_wall_time_sec},
            )
        if "Recipe size" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.recipe_size",
                message=msg,
                details={"limit": caps.max_recipe_bytes},
            )
        # Fallback for an unmapped cap-exceeded message — still 422,
        # generic kind.
        raise_cap_exceeded(
            kind="cap_exceeded.unknown",
            message=msg,
            details={},
        )

    # ---- 3. model_path existence check -------------------------------
    model_dir = Path(req.model_path)
    if not model_dir.exists():
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path does not exist: {req.model_path}",
            details={"got": req.model_path},
        )
    if not model_dir.is_dir():
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path is not a directory: {req.model_path}",
            details={"got": req.model_path},
        )

    if req.dry_run:
        try:
            effective_recipe = recipe_validation.translate_sim_area_if_local(
                req.recipe_data, model_dir,
            )
            recipe_validation.validate_sim_area_intersects_model(
                effective_recipe.get("sim_area", []), model_dir,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
            raise_validation_error(
                kind="validation.recipe_data",
                message=f"recipe validation failed: {e}",
                details={"got": str(e)},
            )
        return {"dry_run": True, "valid": True, "run_name": req.run_name, "trace_id": trace_id}

    # ---- 4. submit ---------------------------------------------------
    # Compose the recipe shape AsyncioRunManager.submit() expects:
    # reserved underscore-prefixed shim keys carry fields not in the
    # ValidatedRecipe Protocol surface (`_run_name`, `_particles`,
    # `_recipe_source_name`). Also forward sim_area pre-validation so
    # the engine inherits the translated bounds.
    try:
        effective_recipe = recipe_validation.translate_sim_area_if_local(
            req.recipe_data, model_dir,
        )
        recipe_validation.validate_sim_area_intersects_model(
            effective_recipe.get("sim_area", []), model_dir,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
        raise_validation_error(
            kind="validation.recipe_data",
            message=f"failed to start run: {e}",
            details={"got": str(e)},
        )

    submit_recipe = {
        **effective_recipe,
        "_run_name": req.run_name,
        "_recipe_source_name": req.recipe_source,
        "_particles": req.particles,
        "particle_count": req.particles,
    }
    try:
        rid = await run_mgr.submit(
            submit_recipe,
            model=ModelRef(name=model_dir.name, path=model_dir),
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
        raise_validation_error(
            kind="validation.recipe_data",
            message=f"failed to start run: {e}",
            details={"got": str(e)},
        )
    return {"run_id": str(rid), "run_name": req.run_name, "trace_id": trace_id}


@router.delete("/{run_id}")
async def cancel(
    run_id: str,
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """Cancel an active run.

    Returns 404 if the run is unknown OR already terminal — matches the
    legacy contract (the old `runner.cancel_run` returned False in both
    cases, which the route translated to 404). The underlying
    AsyncioRunManager.cancel is idempotent on unknown / terminal runs,
    so the route checks status first to preserve the 404 signal.
    """
    rid = RunId(run_id)
    try:
        status = await run_mgr.status(rid)
    except KeyError:
        raise HTTPException(404, f"run {run_id} not active") from None
    if status.state.value in {"completed", "failed", "cancelled", "interrupted"}:
        raise HTTPException(404, f"run {run_id} not active")
    await run_mgr.cancel(rid)
    return {"status": "cancelled"}


def _seq_root() -> Path:
    """Resolve the sequences root.

    Looks first at `_LEGACY_RUNS_DIR` for backward compat with tests that
    monkeypatch it (those tests build a `<tmp>/fused/<name>/manifest.json`
    layout pre-Phase-1). If `_LEGACY_RUNS_DIR` is the real
    `lib.SEQUENCES_DIR` (the production setup) we ignore it — production
    reads from the library directly. Tests pointing `_LEGACY_RUNS_DIR` at
    a temp path keep working because their layout is what the legacy
    branch reads.
    """
    return lib.SEQUENCES_DIR


def _active_run_names(run_mgr: RunManager) -> set[str]:
    """Names of active (non-terminal) runs, for the "don't delete a
    live sequence" guard. Returns a set so callers can do O(1) lookups
    even with hundreds of in-flight runs in the state store."""
    return {
        s.sequence_name for s in run_mgr.list_active()
        if s.sequence_name
    }


@router.delete("/history/{run_name}")
def delete_history(
    run_name: str,
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """Delete a single past run from the library by name.

    Path-traversal defense: a run_name like '../../etc' is rejected
    before any rmtree. Refuses to delete a still-running run (the
    in-process registry would still hold a subprocess + log handle).
    """
    active = _active_run_names(run_mgr)
    if not Sequence.exists(run_name):
        # Fall back to the legacy fused dir for tests / pre-migration data.
        legacy = (_LEGACY_RUNS_DIR / run_name).resolve()
        try:
            legacy.relative_to(_LEGACY_RUNS_DIR.resolve())
        except ValueError:
            raise HTTPException(400, f"refusing to delete outside library: {run_name}") from None
        if not legacy.exists():
            raise HTTPException(404, f"run not found: {run_name}")
        if not legacy.is_dir():
            raise HTTPException(400, f"not a run directory: {run_name}")
        if run_name in active:
            raise HTTPException(
                409, f"run is still running: {run_name}; cancel it first",
            )
        try:
            shutil.rmtree(legacy)
        except OSError as e:
            raise HTTPException(500, f"failed to delete run dir: {e}") from e
        return {"deleted": run_name}

    # Path-traversal defense for the library path.
    target = (lib.SEQUENCES_DIR / run_name).resolve()
    seq_root = lib.SEQUENCES_DIR.resolve()
    try:
        target.relative_to(seq_root)
    except ValueError:
        raise HTTPException(400, f"refusing to delete outside library: {run_name}") from None

    if run_name in active:
        raise HTTPException(
            409, f"run is still running: {run_name}; cancel it first",
        )

    if not Sequence.delete(run_name):
        raise HTTPException(500, f"failed to delete sequence: {run_name}")
    return {"deleted": run_name}


_RUN_FRAME_RE = re.compile(r"^frame_(\d+)\.ply$")
_SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _resolve_run_log(run_name: str) -> Path:
    """Locate run.log for an active OR archived run.

    Active runs write into `_LEGACY_RUNS_DIR/<name>/run.log`; once the
    sequence is archived, the log gets copied into
    `lib.SEQUENCES_DIR/<name>/run.log`. We check both. Raises 400 on a
    bad name, 404 when neither path is a file.
    """
    if not _SAFE_RUN_NAME.match(run_name):
        raise HTTPException(400, f"invalid run name: {run_name!r}")
    # Active first (most recently written), then archived.
    candidates = [
        _LEGACY_RUNS_DIR / run_name / "run.log",
        lib.SEQUENCES_DIR / run_name / "run.log",
    ]
    seq_root = lib.SEQUENCES_DIR.resolve()
    fused_root = _LEGACY_RUNS_DIR.resolve()
    for p in candidates:
        rp = p.resolve()
        # Path-traversal defense: refuse anything that escapes the two
        # allowed roots, regardless of how `run_name` slipped past the regex.
        try:
            rp.relative_to(fused_root)
        except ValueError:
            try:
                rp.relative_to(seq_root)
            except ValueError:
                continue
        if rp.is_file():
            return rp
    raise HTTPException(404, f"no log for run: {run_name}")


@router.get("/{run_name}/log")
def get_run_log(run_name: str, offset: int = 0) -> dict:
    """Incremental tail of a run's stdout/stderr log.

    The frontend polls this every ~500 ms while a sim is active. We
    return only the bytes since `offset`, so the client can append
    chunks without re-rendering the whole log every tick.

    If `offset` is beyond the current file size (log was truncated /
    rotated), we reset to 0 and return everything. Returns
    `{content: str, offset: int, size: int}` where the next poll
    should pass `offset = response.size`.
    """
    log_path = _resolve_run_log(run_name)
    size = log_path.stat().st_size
    if offset < 0 or offset > size:
        offset = 0
    if offset == size:
        return {"content": "", "offset": size, "size": size}
    with log_path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    return {
        "content": chunk.decode("utf-8", errors="replace"),
        "offset": size,
        "size": size,
    }


@router.get("/{run_name}/frame/{frame_idx}.ply")
async def get_run_frame(run_name: str, frame_idx: int):
    """Serve a single frame .ply for a sequence.

    Used by the splat-mode playback to bootstrap the in-browser splat
    mesh: the WS pump streams xyz-only updates per frame, but the splat
    library needs the full attribute set (scales, rotations, opacity,
    SH) from frame 0 to build its render pipeline.
    """
    seq = Sequence.load(run_name)
    if seq is not None:
        ply_path = seq.frames_dir() / f"frame_{frame_idx:04d}.ply"
        if ply_path.is_file():
            return FileResponse(
                str(ply_path),
                media_type="application/octet-stream",
                filename=ply_path.name,
            )
        raise HTTPException(404, f"frame {frame_idx} not found in sequence {run_name}")

    # Fallback to the legacy fused dir for tests + pre-migration data.
    target_dir = (_LEGACY_RUNS_DIR / run_name).resolve()
    fused_root = _LEGACY_RUNS_DIR.resolve()
    try:
        target_dir.relative_to(fused_root)
    except ValueError:
        raise HTTPException(400, f"refusing to read outside run dir: {run_name}") from None
    if not target_dir.is_dir():
        raise HTTPException(404, f"run not found: {run_name}")
    candidates = [
        target_dir / f"frame_{frame_idx:04d}.ply",
        target_dir / "frames" / f"frame_{frame_idx:04d}.ply",
    ]
    for ply_path in candidates:
        if ply_path.is_file():
            return FileResponse(
                str(ply_path),
                media_type="application/octet-stream",
                filename=ply_path.name,
            )
    raise HTTPException(404, f"frame {frame_idx} not found in run {run_name}")


def _history_entry_from_sequence(seq: Sequence) -> dict | None:
    """Build a HistoryEntry-shaped dict from a Sequence + its sibling
    `manifest.json` (left there by the runner). Returns None if the
    sequence is unreadable enough that we can't produce even the minimal
    `run_name + status` shape.

    Field provenance:
      - run_name: sequence name (=== seq.name)
      - status: manifest.json:status if present, else "done" if frames
        exist, else "unknown"
      - started_at: manifest.json:started_at if present (epoch float),
        else parsed from _meta.json:created_at (ISO string -> epoch),
        else dir mtime
      - finished_at: manifest.json:finished_at if present
      - particles: manifest.json:particles if present
      - recipe_source: manifest.json:recipe_source if present
    """
    manifest_path = seq.path / "manifest.json"
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict):
                manifest = {}
        except (json.JSONDecodeError, OSError):
            manifest = {}

    meta = seq.meta or {}
    frame_count = seq.frame_count()

    started_at = manifest.get("started_at")
    if started_at is None:
        # Try _meta.json:created_at (ISO 8601 UTC).
        ca = meta.get("created_at")
        if isinstance(ca, str):
            try:
                from datetime import datetime
                started_at = datetime.strptime(
                    ca, "%Y-%m-%dT%H:%M:%SZ"
                ).timestamp()
            except (ValueError, OSError):
                started_at = None
        if started_at is None:
            try:
                started_at = seq.path.stat().st_mtime
            except OSError:
                started_at = 0.0

    status = manifest.get("status")
    if not status:
        status = "done" if frame_count > 0 else "unknown"

    out: dict = {
        "run_name": seq.name,
        "status": status,
        "started_at": started_at,
    }
    if "finished_at" in manifest:
        out["finished_at"] = manifest["finished_at"]
    if "particles" in manifest:
        out["particles"] = manifest["particles"]
    if "recipe_source" in manifest:
        out["recipe_source"] = manifest["recipe_source"]
    # Carry the new metadata fields too so future Phase-2+ frontend
    # code can read them without bumping the type. Frontend ignores
    # extras today.
    # Prefer the finished _meta.json's model_ref. Fall back to the
    # manifest's model_dir (written at run START) so an in-flight run
    # nests under its parent model in the outliner immediately, instead
    # of sitting in "Orphan sequences" until completion writes the
    # canonical _meta.json. Same key shape either way.
    if meta.get("model_ref"):
        out["model_ref"] = meta["model_ref"]
    else:
        md = manifest.get("model_dir")
        if isinstance(md, str) and md:
            out["model_ref"] = Path(md).name
    if "frame_count" in meta:
        out["frame_count"] = meta["frame_count"]
    elif frame_count:
        out["frame_count"] = frame_count
    if meta.get("source"):
        out["sequence_source"] = meta["source"]
    return out


def _history_entry_from_legacy_dir(d: Path) -> dict | None:
    """Build a HistoryEntry-shaped dict from a pre-Phase-1 fused dir.

    Used by tests that monkeypatch `_LEGACY_RUNS_DIR` to a tmp dir
    containing `<run>/manifest.json` files in the old layout, and as a
    fallback for any production data that wasn't run through the migration
    script yet (shouldn't happen, but defensive).
    """
    m = d / "manifest.json"
    if m.is_file():
        try:
            data = json.loads(m.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("run_name", d.name)
        return data
    # Legacy frame-only dir (no manifest, no _meta.json).
    frame_count = sum(1 for _ in d.glob("frame_*.ply")) + sum(
        1 for _ in d.glob("frames/frame_*.ply")
    )
    if frame_count == 0:
        return None
    try:
        mtime = d.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "run_name": d.name,
        "status": "done",
        "started_at": mtime,
        "finished_at": mtime,
        "particles": None,
        "recipe_source": None,
        "_synthetic": True,
    }


@router.get("/history")
def history():
    """List all past runs in the library, newest-first.

    Walks `library.SEQUENCES_DIR` and merges each sequence's `_meta.json`
    + (where present) `manifest.json` into a HistoryEntry-shaped dict.
    Falls back to the legacy `_LEGACY_RUNS_DIR` walk for any pre-migration
    data; tests patch `_LEGACY_RUNS_DIR` to a tmp dir.
    """
    out: list[dict] = []
    seen_names: set[str] = set()

    if lib.SEQUENCES_DIR.is_dir():
        for name in Sequence.list():
            seq = Sequence.load(name)
            if seq is None:
                continue
            entry = _history_entry_from_sequence(seq)
            if entry is None:
                continue
            out.append(entry)
            seen_names.add(name)

    # Legacy/fallback walk. Tests monkeypatch _LEGACY_RUNS_DIR to a tmp
    # path holding the old `<run>/manifest.json` layout — surface those too.
    fused = _LEGACY_RUNS_DIR
    if fused.is_dir() and fused.resolve() != lib.SEQUENCES_DIR.resolve():
        try:
            for d in sorted(
                fused.iterdir(),
                key=lambda p: (-p.stat().st_mtime, p.name),
            ):
                if not d.is_dir() or d.name in seen_names:
                    continue
                entry = _history_entry_from_legacy_dir(d)
                if entry is not None:
                    out.append(entry)
                    seen_names.add(d.name)
        except OSError:
            pass

    # Sort newest-first by `started_at` (manifest field) so the UI
    # ordering matches the previous behavior.
    out.sort(key=lambda e: e.get("started_at") or 0, reverse=True)
    return out
