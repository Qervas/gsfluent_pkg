#!/usr/bin/env python3
"""Static recipe-stability linter — CI gate for the built-in recipe library.

Lints recipe JSON for known numerically-unstable parameter combos (chiefly
`grid_v_damping_scale >= 1.0`, a silent damping no-op, and `substep_dt` above
the CFL limit) at zero GPU cost. Pure static analysis — no sim, no GPU, no
backend. See docs/proposals/intelligent-recipe-stabilization.md (Phase 0).

Findings are grouped by file. Exits non-zero if any `error`-severity finding
is present, so it is suitable as a CI gate: this one command, run over
server/recipes/*.json, catches the bad built-ins before they ship.

Usage:
    python server/tools/lint_recipes.py                 # lint server/recipes/*.json
    python server/tools/lint_recipes.py path/to/r.json  # lint specific file(s)
    python server/tools/lint_recipes.py some/dir         # lint *.json under a dir
    python server/tools/lint_recipes.py --json           # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Bootstrap so `gsfluent` is importable without pip install.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent.core import recipe_lint  # noqa: E402

# Default target: the shipped built-in library.
_DEFAULT_RECIPES_DIR = _BOOTSTRAP_ROOT / "server" / "recipes"


def _resolve_targets(args: list[str]) -> list[Path]:
    """Expand CLI path args into a sorted list of *.json files. With no args,
    lint the built-in recipes dir."""
    if not args:
        roots = [_DEFAULT_RECIPES_DIR]
    else:
        roots = [Path(a) for a in args]

    files: list[Path] = []
    for r in roots:
        if r.is_dir():
            files.extend(sorted(r.glob("*.json")))
        elif r.exists():
            files.append(r)
        else:
            print(f"warning: path not found, skipping: {r}", file=sys.stderr)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="recipe .json files or directories (default: server/recipes/)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of grouped text",
    )
    ns = parser.parse_args(argv)

    files = _resolve_targets(ns.paths)

    results: list[dict] = []
    any_error = False

    for path in files:
        try:
            recipe = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            # A file we can't read/parse is itself a gate failure.
            any_error = True
            results.append({
                "file": str(path),
                "read_error": str(e),
                "findings": [],
            })
            continue

        findings = recipe_lint.lint_recipe(recipe)
        if recipe_lint.has_errors(findings):
            any_error = True
        results.append({
            "file": str(path),
            "read_error": None,
            "findings": [f.as_dict() for f in findings],
        })

    if ns.json:
        print(json.dumps({"ok": not any_error, "results": results}, indent=2))
        return 1 if any_error else 0

    # Grouped text output.
    total_findings = 0
    for res in results:
        rel = res["file"]
        if res["read_error"] is not None:
            print(f"{rel}")
            print(f"  ERROR  could not read recipe: {res['read_error']}")
            print()
            continue
        findings = res["findings"]
        if not findings:
            print(f"{rel}  OK")
            continue
        print(f"{rel}")
        for f in findings:
            total_findings += 1
            tag = "ERROR" if f["severity"] == "error" else "warn "
            print(f"  {tag}  [{f['rule_id']}] {f['param']}")
            print(f"         {f['message']}")
            if f["suggested_fix"] is not None:
                print(f"         suggested_fix: {f['suggested_fix']}")
        print()

    n_err = sum(
        1
        for res in results
        for f in res["findings"]
        if f["severity"] == "error"
    ) + sum(1 for res in results if res["read_error"] is not None)
    n_warn = sum(
        1
        for res in results
        for f in res["findings"]
        if f["severity"] == "warn"
    )
    print(
        f"summary: {len(files)} file(s), "
        f"{n_err} error(s), {n_warn} warning(s)"
    )
    if any_error:
        print("FAIL: error-severity findings present.")
    else:
        print("PASS: no error-severity findings.")
    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
