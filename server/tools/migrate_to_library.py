#!/usr/bin/env python
"""Migrate the old work/ layout to work/library/{models,sequences}/.

Usage:
    python server/tools/migrate_to_library.py [--dry-run]

Idempotent: re-running after a partial migration only moves the entries
that haven't moved yet. An existing target dir is treated as already
migrated and skipped (we never overwrite).

Sources (old):
    work/uploads/<name>/point_cloud/iteration_*/point_cloud.ply
    work/fused/<run>/{frame_*.ply, frames/frame_*.ply}
    work/_state/model_history.json    (read for `imported_at` enrichment)
    work/_state/run_history.json      (same, if present)

Targets (new):
    work/library/models/<name>/
        ├── point_cloud/iteration_*/point_cloud.ply
        ├── cameras.json (preserved if present)
        └── _meta.json
    work/library/sequences/<run>/
        ├── frames/frame_*.ply
        ├── _meta.json
        └── recipe.json (if recipe_effective.json or _effective_recipe.json
                         is found in the old run dir)

The old `work/uploads/` and `work/fused/` directories themselves are not
removed (they end up empty after migration; left in place is harmless).
The `_state/*.json` history files are deliberately preserved — the new
endpoints don't read them, but a user might want them for audit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make `gsfluent` importable when the script runs from a checkout without
# pip install (we're in server/tools/, parent server/ holds the package).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "server") not in sys.path:
    sys.path.insert(0, str(ROOT / "server"))

from gsfluent.core.library import (  # noqa: E402
    Model,
    Sequence,
    MODELS_DIR,
    SEQUENCES_DIR,
    LIBRARY_ROOT,
    read_ply_bbox_and_count,
)

_log = logging.getLogger("migrate")

WORK_DIR = ROOT / "work"
OLD_UPLOADS = WORK_DIR / "uploads"
OLD_FUSED = WORK_DIR / "fused"
OLD_STATE = WORK_DIR / "_state"

_FRAME_RE = re.compile(r"^frame_(\d+)\.ply$")
_ITER_RE = re.compile(r"^iteration_(\d+)$")


# --- enrichment from old history files --------------------------------------


def _load_state_history(path: Path) -> dict[str, dict]:
    """Read a `_state/*_history.json` file into a name-keyed dict.

    Tolerant — missing/corrupt history is fine; we just lose the
    `imported_at` enrichment. Old entries shaped {name, path, ts?} all
    get keyed by name; the newest entry wins on dup.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("could not parse %s: %s", path, e)
        return {}
    out: dict[str, dict] = {}
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and "name" in entry:
                out.setdefault(entry["name"], entry)
    return out


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# --- model migration --------------------------------------------------------


def _find_highest_iteration_ply(model_dir: Path) -> Optional[Path]:
    pc_root = model_dir / "point_cloud"
    if not pc_root.is_dir():
        return None
    best: Optional[tuple[int, Path]] = None
    for it in pc_root.iterdir():
        if not it.is_dir():
            continue
        m = _ITER_RE.match(it.name)
        if not m:
            continue
        ply = it / "point_cloud.ply"
        if ply.is_file():
            n = int(m.group(1))
            if best is None or n > best[0]:
                best = (n, ply)
    return None if best is None else best[1]


def migrate_models(
    state_models: dict[str, dict], *, dry_run: bool
) -> tuple[int, int, int]:
    """Move each `work/uploads/<name>/` -> `work/library/models/<name>/`.

    Returns (migrated, skipped, failed)."""
    if not OLD_UPLOADS.is_dir():
        return 0, 0, 0

    migrated = skipped = failed = 0
    for src in sorted(OLD_UPLOADS.iterdir()):
        if not src.is_dir():
            continue
        name = src.name
        dst = MODELS_DIR / name
        if dst.exists():
            print(f"  skipping model {name}: already in library")
            skipped += 1
            continue

        # Compute meta BEFORE the move so we can fall back gracefully if
        # the ply read fails — we want to migrate the dir even if the
        # bbox/n-splats fields end up null.
        ply = _find_highest_iteration_ply(src)
        n_splats: Optional[int] = None
        bbox = None
        if ply is not None:
            n_splats, bbox = read_ply_bbox_and_count(ply)

        # Pull `imported_at` from the old history file if available; the
        # legacy entries don't carry timestamps, so fall back to the
        # source dir's mtime — that's the closest thing to "when was this
        # imported" we have.
        ts_iso = None
        if name in state_models:
            ts = state_models[name].get("ts")
            if isinstance(ts, (int, float)):
                ts_iso = _ts_to_iso(float(ts))
        if ts_iso is None:
            try:
                ts_iso = _ts_to_iso(src.stat().st_mtime)
            except OSError:
                pass

        if dry_run:
            print(f"  [dry-run] would migrate model {name}")
            migrated += 1
            continue

        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            os.rename(str(src), str(dst))
        except OSError as e:
            _log.error("failed to move %s -> %s: %s", src, dst, e)
            failed += 1
            continue

        try:
            Model.write_meta(
                name=name,
                source="upload",
                source_path=None,  # internal-library models are self-hosted
                n_splats=n_splats,
                bbox=bbox,
                coord_convention="z-up",
                imported_at=ts_iso,
            )
        except Exception as e:
            _log.error("failed to write _meta.json for model %s: %s", name, e)
            failed += 1
            continue

        print(f"  migrated model {name}")
        migrated += 1

    return migrated, skipped, failed


# --- sequence migration -----------------------------------------------------


def _list_old_frame_plys(run_dir: Path) -> list[Path]:
    """Old layout sometimes has frames at the run root, sometimes in
    frames/. Collect both; dedupe by stem (run-root copy wins)."""
    out: list[Path] = []
    out.extend(run_dir.glob("frame_*.ply"))
    out.extend(run_dir.glob("frames/frame_*.ply"))
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in out:
        if p.stem not in seen:
            deduped.append(p)
            seen.add(p.stem)
    return deduped


def _heuristic_model_ref(run_name: str, known_models: list[str]) -> Optional[str]:
    """Best-effort match of `<run_name>` to a model name.

    We try two patterns the existing pipeline uses:
      - "<modelname>_<recipe>_<datestamp>"  (legacy run-naming default)
      - "<modelname>__<rest>" (double underscore separator, less common)

    Only return a match if exactly one known model name is a prefix; on
    ambiguity or no match, returns None and `_meta.json:model_ref=null`.
    """
    if not known_models:
        return None
    matches = []
    for m in known_models:
        if run_name == m or run_name.startswith(m + "_") or run_name.startswith(m + "__"):
            matches.append(m)
    if len(matches) == 1:
        return matches[0]
    if matches:
        # Prefer the longest exact-prefix match (so "cluster_6_15" beats
        # "cluster_6" when both register against "cluster_6_15_smash").
        matches.sort(key=len, reverse=True)
        return matches[0]
    return None


def migrate_sequences(
    state_runs: dict[str, dict], known_models: list[str], *, dry_run: bool
) -> tuple[int, int, int]:
    """Move each `work/fused/<run>/` -> `work/library/sequences/<run>/`.

    Returns (migrated, skipped, failed)."""
    if not OLD_FUSED.is_dir():
        return 0, 0, 0

    migrated = skipped = failed = 0
    for src in sorted(OLD_FUSED.iterdir()):
        if not src.is_dir():
            continue
        name = src.name
        dst = SEQUENCES_DIR / name
        if dst.exists():
            print(f"  skipping sequence {name}: already in library")
            skipped += 1
            continue

        frames = _list_old_frame_plys(src)
        # A run dir without frames is uninteresting (sim crashed before
        # producing output, or the user wiped frames manually). Skip — we
        # don't migrate empty husks.
        if not frames:
            print(f"  skipping sequence {name}: no frame_*.ply found")
            skipped += 1
            continue

        # Compute meta from frame 0.
        frame0 = min(frames, key=lambda p: int(_FRAME_RE.match(p.name).group(1)))
        n_splats, bbox = read_ply_bbox_and_count(frame0)

        # imported_at: history first, dir mtime as fallback.
        ts_iso = None
        if name in state_runs:
            for k in ("started_at", "ts"):
                ts = state_runs[name].get(k)
                if isinstance(ts, (int, float)):
                    ts_iso = _ts_to_iso(float(ts))
                    break
        if ts_iso is None:
            try:
                ts_iso = _ts_to_iso(src.stat().st_mtime)
            except OSError:
                pass

        model_ref = _heuristic_model_ref(name, known_models)

        # Find a recipe to copy into the new sequence dir, if any.
        recipe_src: Optional[Path] = None
        for cand in ("recipe_effective.json", "_effective_recipe.json"):
            if (src / cand).is_file():
                recipe_src = src / cand
                break

        if dry_run:
            print(f"  [dry-run] would migrate sequence {name} ({len(frames)} frames)")
            migrated += 1
            continue

        # Build the target dir tree by moving the source dir, then
        # re-arrange so frames end up under frames/. Two cases:
        #   - frames already in src/frames/: just rename src -> dst.
        #   - frames at src root: rename src -> dst, then move plys
        #     into dst/frames/.
        try:
            SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
            os.rename(str(src), str(dst))
        except OSError as e:
            _log.error("failed to move %s -> %s: %s", src, dst, e)
            failed += 1
            continue

        try:
            frames_dir = dst / "frames"
            frames_dir.mkdir(exist_ok=True)
            # If frames were at the root, slide them under frames/.
            for p in list(dst.glob("frame_*.ply")):
                p.rename(frames_dir / p.name)
        except OSError as e:
            _log.error("failed to organize frames under %s: %s", dst, e)
            failed += 1
            continue

        # Copy recipe.json if we found one. The old name varies, so we
        # collapse both into a single canonical `recipe.json`.
        if recipe_src is not None:
            try:
                # After the rename above, recipe_src's path no longer
                # exists; locate it inside dst/.
                rel = recipe_src.relative_to(src)
                new_recipe_src = dst / rel
                if new_recipe_src.is_file():
                    shutil.copy2(new_recipe_src, dst / "recipe.json")
            except (OSError, ValueError) as e:
                _log.warning("could not copy recipe for %s: %s", name, e)

        # Compute final frame count (some old runs had partial frames, but
        # we just moved them all so count == len(frames)).
        try:
            Sequence.write_meta(
                name=name,
                source="sim",
                source_path=None,
                model_ref=model_ref,
                frame_count=len(frames),
                fps_hint=24,
                n_splats=n_splats,
                bbox_initial=bbox,
                coord_convention="z-up",
                first_frame_full=True,
                created_at=ts_iso,
            )
        except Exception as e:
            _log.error("failed to write _meta.json for sequence %s: %s", name, e)
            failed += 1
            continue

        print(f"  migrated sequence {name} ({len(frames)} frames)")
        migrated += 1

    return migrated, skipped, failed


# --- driver -----------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print actions without modifying disk")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print(f"Library root: {LIBRARY_ROOT}")
    print(f"Source uploads: {OLD_UPLOADS}")
    print(f"Source fused: {OLD_FUSED}")
    if args.dry_run:
        print("(dry-run: no files will be moved)")

    if not args.dry_run:
        LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)

    state_models = _load_state_history(OLD_STATE / "model_history.json")
    state_runs = _load_state_history(OLD_STATE / "run_history.json")

    print("\n=== models ===")
    m_mig, m_skip, m_fail = migrate_models(state_models, dry_run=args.dry_run)

    # Re-list models AFTER model migration so the sequence step has the
    # full set for heuristic matching. In dry-run we use the pre-existing
    # library + the names we'd have moved.
    if args.dry_run:
        # Pre-migration listing — old upload dir names are what we'd see.
        known_models = sorted(
            {p.name for p in OLD_UPLOADS.iterdir() if p.is_dir()}
            if OLD_UPLOADS.is_dir() else set()
        ) + Model.list()
        # Dedupe preserving order.
        seen: set[str] = set()
        known_models = [
            x for x in known_models if not (x in seen or seen.add(x))
        ]
    else:
        known_models = Model.list()

    print("\n=== sequences ===")
    s_mig, s_skip, s_fail = migrate_sequences(
        state_runs, known_models, dry_run=args.dry_run,
    )

    print()
    print(
        f"summary: migrated={m_mig + s_mig} "
        f"skipped(already)={m_skip + s_skip} "
        f"failed={m_fail + s_fail}"
    )
    print(f"  models: migrated={m_mig} skipped={m_skip} failed={m_fail}")
    print(f"  sequences: migrated={s_mig} skipped={s_skip} failed={s_fail}")

    return 1 if (m_fail + s_fail) > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
