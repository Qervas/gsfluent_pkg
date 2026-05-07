"""Reads and writes recipe JSONs from disk.

Built-in recipes live at <pkg_root>/tools/recipes/<name>.json (read-only).
User saves go to <pkg_root>/work/_user_recipes/<name>.json.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from ..server import PKG_ROOT

RECIPES_DIR = PKG_ROOT / "tools" / "recipes"
USER_RECIPES_DIR = PKG_ROOT / "work" / "_user_recipes"


def list_recipes() -> list[dict]:
    out: list[dict] = []
    for p in sorted(RECIPES_DIR.glob("*.json")):
        out.append({"name": p.stem, "source": "builtin"})
    if USER_RECIPES_DIR.exists():
        for p in sorted(USER_RECIPES_DIR.glob("*.json")):
            out.append({"name": p.stem, "source": "user"})
    return out


def resolve_path(name: str) -> Path | None:
    for d in (RECIPES_DIR, USER_RECIPES_DIR):
        p = d / f"{name}.json"
        if p.exists():
            return p
    return None


def load_recipe(name: str) -> dict | None:
    p = resolve_path(name)
    return None if p is None else json.loads(p.read_text())


def save_user_recipe(name: str, data: dict, based_on: str | None = None) -> Path:
    USER_RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    if not safe:
        raise ValueError(f"invalid recipe name: {name!r}")
    out = USER_RECIPES_DIR / f"{safe}.json"
    payload = dict(data)
    payload["_provenance"] = {
        "based_on": based_on or "(unknown)",
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out)
    return out
