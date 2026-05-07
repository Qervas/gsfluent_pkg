"""Reads and writes recipe JSONs from disk.

Built-in recipes live at <pkg_root>/tools/recipes/<name>.json (read-only).
User saves go to <pkg_root>/work/_user_recipes/<name>.json.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

from ..server import PKG_ROOT

RECIPES_DIR = PKG_ROOT / "tools" / "recipes"
USER_RECIPES_DIR = PKG_ROOT / "work" / "_user_recipes"

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class RecipeReadError(Exception):
    """A recipe file exists on disk but cannot be read or parsed."""


def list_recipes() -> list[dict]:
    out: list[dict] = []
    for p in sorted(RECIPES_DIR.glob("*.json")):
        out.append({"name": p.stem, "source": "builtin"})
    if USER_RECIPES_DIR.exists():
        for p in sorted(USER_RECIPES_DIR.glob("*.json")):
            out.append({"name": p.stem, "source": "user"})
    return out


def resolve_path(name: str) -> tuple[Path, str] | None:
    """Returns (path, 'builtin' | 'user') or None if not found.
    Builtin takes precedence when a name exists in both directories."""
    builtin = RECIPES_DIR / f"{name}.json"
    if builtin.exists():
        return (builtin, "builtin")
    user = USER_RECIPES_DIR / f"{name}.json"
    if user.exists():
        return (user, "user")
    return None


def load_recipe(name: str) -> tuple[dict, str] | None:
    """Returns (data, source) or None if not found.
    Raises RecipeReadError if the file exists but can't be parsed."""
    resolved = resolve_path(name)
    if resolved is None:
        return None
    p, source = resolved
    try:
        return (json.loads(p.read_text()), source)
    except (json.JSONDecodeError, OSError) as e:
        raise RecipeReadError(f"failed to read recipe '{name}' at {p}: {e}") from e


def save_user_recipe(name: str, data: dict, based_on: str | None = None) -> tuple[Path, dict]:
    """Save a user preset. Raises ValueError if `name` has invalid chars.
    Returns (path_written, payload_with_provenance)."""
    if not name or not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid recipe name {name!r}: must be non-empty and contain only "
            f"alphanumerics, dashes, and underscores"
        )
    USER_RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    out = USER_RECIPES_DIR / f"{name}.json"
    payload = dict(data)
    payload["_provenance"] = {
        "based_on": based_on or "(unknown)",
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = out.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(out)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return (out, payload)
