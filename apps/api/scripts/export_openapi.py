"""Export FastAPI OpenAPI spec to apps/api/openapi.json.

Usage:
  python scripts/export_openapi.py            # write openapi.json
  python scripts/export_openapi.py --check    # exit 1 if drift vs committed

The committed openapi.json is the source of truth for the frontend's
generated client (Phase 6). CI runs --check to ensure it stays in sync.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Stub env so Settings() doesn't fail at import time. The export only walks
# the route table; it never connects to the actual database/redis/minio.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@x/x")
os.environ.setdefault("REDIS_URL", "redis://x")
os.environ.setdefault("MINIO_ENDPOINT", "x")
os.environ.setdefault("MINIO_ACCESS_KEY", "x")
os.environ.setdefault("MINIO_SECRET_KEY", "x")

from gsfluent_api.main import app  # noqa: E402

OUT = Path(__file__).parent.parent / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if openapi.json is stale; do not write")
    args = parser.parse_args()

    fresh = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"missing {OUT}; run without --check first")
            return 1
        committed = OUT.read_text()
        if fresh != committed:
            print("drift: openapi.json out of date. run scripts/export_openapi.py.")
            return 1
        print("openapi.json: up to date")
        return 0

    OUT.write_text(fresh)
    print(f"wrote {OUT} ({len(fresh)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
