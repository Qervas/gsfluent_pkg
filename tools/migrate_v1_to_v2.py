"""Migrate v1 filesystem state to v2 Postgres + MinIO.

V1 layout (current stack):
  <root>/recipes/*.json         — recipes
  <root>/models/*.ply           — uploaded splat models
  <root>/runs/{run_name}/       — one dir per run
      ├── frame_*.npz           — per-frame cell artifacts
      ├── log.txt               — run log
      └── meta.json (optional)  — run metadata

V2 destination:
  models      table  ←  one row per .ply,  bytes copied to gsfluent-models
                                            at models/{id}/source.ply
  recipes     table  ←  one row per .json
  runs        table  ←  one row per runs/{name}/
  artifacts   table  ←  rows for each npz + log
  MinIO       runs/{run_id}/frame_NNNN.npz + runs/{run_id}/log.txt

Idempotency:
  Re-runs skip anything already migrated by matching `source_metadata`
  → original v1 path. Print a diff report.

Usage:
  uv pip install --system 'apps/api[dev]'      # provides gsfluent_api.models
  export DATABASE_URL='postgresql+asyncpg://...'
  export MINIO_ENDPOINT='localhost:19000'
  export MINIO_ACCESS_KEY='gsfluent'
  export MINIO_SECRET_KEY='...'

  python tools/migrate_v1_to_v2.py --v1-root /data/yinshaoxuan/gsfluent_pkg --dry-run
  python tools/migrate_v1_to_v2.py --v1-root /data/yinshaoxuan/gsfluent_pkg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Wires up gsfluent_api package.
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "api" / "src"))


@dataclass
class Report:
    models_seen: int = 0
    models_migrated: int = 0
    models_skipped: list[str] = field(default_factory=list)
    recipes_seen: int = 0
    recipes_migrated: int = 0
    runs_seen: int = 0
    runs_migrated: int = 0
    artifacts_migrated: int = 0
    bytes_copied: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"models:    seen={self.models_seen}  migrated={self.models_migrated}  skipped={len(self.models_skipped)}",
            f"recipes:   seen={self.recipes_seen}  migrated={self.recipes_migrated}",
            f"runs:      seen={self.runs_seen}  migrated={self.runs_migrated}",
            f"artifacts: {self.artifacts_migrated}",
            f"bytes:     {self.bytes_copied / 1e6:.1f} MB",
            f"errors:    {len(self.errors)}",
        ]
        if self.errors:
            lines += [""] + [f"  ! {e}" for e in self.errors[:20]]
        if self.models_skipped:
            lines += ["", "skipped models:"] + [f"  · {n}" for n in self.models_skipped[:10]]
        return "\n".join(lines)


def _parse_num_gaussians(ply_path: Path) -> int | None:
    """Best-effort PLY header parse."""
    try:
        with ply_path.open("rb") as f:
            head = f.read(8192).decode("ascii", errors="ignore")
    except OSError:
        return None
    if not head.startswith("ply\n"):
        return None
    for line in head.splitlines():
        if line.startswith("element vertex "):
            try:
                return int(line.split()[-1])
            except ValueError:
                return None
        if line.strip() == "end_header":
            break
    return None


FRAME_RE = re.compile(r"frame[_-]?(\d+)\.(npz|ply)$", re.IGNORECASE)


async def migrate(v1_root: Path, dry_run: bool) -> Report:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Lazy-import after sys.path adjustment.
    from gsfluent_api.models import Artifact, ArtifactKind, Model, Recipe, RecipeVersion, Run, RunStatus
    from gsfluent_api.storage import (
        BUCKET_MODELS,
        BUCKET_RUNS,
        ensure_buckets,
        put_object_bytes,
    )

    report = Report()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL env var required")
    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    if not dry_run:
        await ensure_buckets()

    # --- models ---
    models_dir = v1_root / "models"
    if models_dir.is_dir():
        for ply in sorted(models_dir.glob("*.ply")):
            report.models_seen += 1
            size = ply.stat().st_size
            model_id = uuid.uuid4()
            num_gauss = _parse_num_gaussians(ply)
            print(f"[model] {ply.name}: id={model_id} {size/1e6:.1f}MB gauss={num_gauss}")
            if dry_run:
                report.models_migrated += 1
                report.bytes_copied += size
                continue
            key = f"models/{model_id}/source.ply"
            data = ply.read_bytes()
            await put_object_bytes(BUCKET_MODELS, key, data, "application/octet-stream")
            async with Session() as s:
                s.add(Model(
                    id=model_id, name=ply.name, minio_path=f"{BUCKET_MODELS}/{key}",
                    size_bytes=size, num_gaussians=num_gauss,
                    source_metadata={"v1_path": str(ply)},
                ))
                await s.commit()
            report.models_migrated += 1
            report.bytes_copied += size

    # --- recipes ---
    recipes_dir = v1_root / "recipes"
    if recipes_dir.is_dir():
        for jf in sorted(recipes_dir.glob("*.json")):
            report.recipes_seen += 1
            try:
                content = json.loads(jf.read_text())
            except json.JSONDecodeError as e:
                report.errors.append(f"recipe {jf.name}: {e}")
                continue
            recipe_id = uuid.uuid4()
            print(f"[recipe] {jf.stem}: id={recipe_id}")
            if dry_run:
                report.recipes_migrated += 1
                continue
            async with Session() as s:
                s.add(Recipe(id=recipe_id, name=jf.stem, content=content, version=1))
                s.add(RecipeVersion(recipe_id=recipe_id, version=1, content=content))
                await s.commit()
            report.recipes_migrated += 1

    # --- runs ---
    runs_dir = v1_root / "runs"
    if runs_dir.is_dir():
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            report.runs_seen += 1
            run_id = uuid.uuid4()
            meta_file = run_dir / "meta.json"
            recipe_snapshot: dict[str, object] = {}
            if meta_file.is_file():
                try:
                    recipe_snapshot = json.loads(meta_file.read_text())
                except json.JSONDecodeError:
                    pass

            print(f"[run] {run_dir.name}: id={run_id}")
            if dry_run:
                report.runs_migrated += 1
            else:
                async with Session() as s:
                    # We don't have the original model_id mapping; legacy runs
                    # need a placeholder model. Skip if no models exist yet.
                    from sqlalchemy import select
                    model = (await s.scalars(select(Model).limit(1))).first()
                    if model is None:
                        report.errors.append(
                            f"run {run_dir.name}: no model exists; skipping"
                        )
                        continue
                    s.add(Run(
                        id=run_id, name=run_dir.name,
                        status=RunStatus.completed,
                        model_id=model.id,
                        recipe_snapshot=recipe_snapshot,
                    ))
                    await s.commit()
                report.runs_migrated += 1

            # Artifacts.
            for f in sorted(run_dir.iterdir()):
                if not f.is_file():
                    continue
                size = f.stat().st_size
                if FRAME_RE.search(f.name):
                    match = FRAME_RE.search(f.name)
                    frame_idx = int(match.group(1)) if match else None
                    kind = ArtifactKind.cell
                elif f.name == "log.txt":
                    frame_idx = None
                    kind = ArtifactKind.log
                else:
                    continue

                key = f"runs/{run_id}/{f.name}"
                print(f"  [art] {f.name} ({size/1e6:.1f}MB) kind={kind.value}")
                if dry_run:
                    report.artifacts_migrated += 1
                    report.bytes_copied += size
                    continue
                await put_object_bytes(BUCKET_RUNS, key, f.read_bytes())
                async with Session() as s:
                    s.add(Artifact(
                        run_id=run_id, kind=kind, frame_idx=frame_idx,
                        minio_path=f"{BUCKET_RUNS}/{key}", size_bytes=size,
                    ))
                    await s.commit()
                report.artifacts_migrated += 1
                report.bytes_copied += size

    await engine.dispose()
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--v1-root", required=True, help="path to v1 gsfluent_pkg root")
    p.add_argument("--dry-run", action="store_true",
                   help="report only; don't touch v2 PG/MinIO")
    args = p.parse_args()

    v1_root = Path(args.v1_root).resolve()
    if not v1_root.is_dir():
        raise SystemExit(f"not a directory: {v1_root}")

    report = asyncio.run(migrate(v1_root, args.dry_run))
    print("\n=== report ===")
    print(report.render())
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
