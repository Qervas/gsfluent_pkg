#!/usr/bin/env python3
"""Headless model × recipe compatibility check.

For every (model, recipe) combination the server knows about, POST a
dry-run to /api/runs and print the result. The dry-run path runs the
same validation a real run would (model_path existence, sim_area ↔
model bbox overlap, sim_area_frame translation) without spawning the
sim wrapper. Lets us find broken recipes / mismatched pairings in
seconds instead of waiting for a real sim crash.

Usage:
    python server/tools/check_recipe_compat.py
    python server/tools/check_recipe_compat.py --server http://localhost:8080
    python server/tools/check_recipe_compat.py --models cluster_6_15 --recipes jelly,metal
    python server/tools/check_recipe_compat.py --json    # machine-readable output

Defaults to the workbench's local tunnel at http://localhost:8080.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def fetch_json(server: str, path: str) -> object:
    with urllib.request.urlopen(f"{server.rstrip('/')}{path}", timeout=30) as r:
        return json.loads(r.read())


def dry_run(server: str, model_path: str, recipe_name: str, recipe_data: dict) -> tuple[bool, str | None]:
    """POST a dry-run. Returns (valid, error_message_or_None)."""
    body = json.dumps({
        "run_name": f"_dryrun_{int(time.time() * 1000)}",
        "model_path": model_path,
        "recipe_data": recipe_data,
        "recipe_source": recipe_name,
        "particles": 200_000,
        "dry_run": True,
    }).encode()
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/runs",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            return bool(data.get("valid")), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            detail = body.get("detail", str(e))
        except Exception:
            detail = f"HTTP {e.code}"
        return False, detail
    except Exception as e:
        return False, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--server", default="http://localhost:8080",
                    help="Backend base URL (default: http://localhost:8080)")
    ap.add_argument("--models", default=None,
                    help="Comma-separated model names to test (default: all)")
    ap.add_argument("--recipes", default=None,
                    help="Comma-separated recipe names to test (default: all)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of a matrix")
    args = ap.parse_args()

    print(f"connecting: {args.server}", file=sys.stderr)
    try:
        models = fetch_json(args.server, "/api/models")
        recipes_list = fetch_json(args.server, "/api/recipes")
    except Exception as e:
        print(f"ERROR: can't reach API at {args.server}: {e}", file=sys.stderr)
        return 2

    if args.models:
        wanted = set(args.models.split(","))
        models = [m for m in models if m["name"] in wanted]
    if args.recipes:
        wanted = set(args.recipes.split(","))
        recipes_list = [r for r in recipes_list if r["name"] in wanted]

    # Fetch each recipe's full data.
    recipes: list[dict] = []
    for r in recipes_list:
        full = fetch_json(args.server, f"/api/recipes/{r['name']}")
        recipes.append({"name": full["name"], "source": full["source"], "data": full["data"]})

    print(f"models:  {len(models)}", file=sys.stderr)
    print(f"recipes: {len(recipes)}", file=sys.stderr)
    print(f"running {len(models) * len(recipes)} dry-runs…", file=sys.stderr)

    results: dict[str, dict[str, dict]] = {}
    for m in models:
        results[m["name"]] = {}
        for r in recipes:
            ok, err = dry_run(args.server, m["path"], r["name"], r["data"])
            results[m["name"]][r["name"]] = {"valid": ok, "error": err}

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    # Pretty matrix.
    name_w = max((len(r["name"]) for r in recipes), default=8)
    model_w = max((len(m["name"]) for m in models), default=8)
    print()
    print(f"{'recipe':<{name_w}}  ", end="")
    for m in models:
        print(f"{m['name']:<{model_w}}  ", end="")
    print()
    print("-" * (name_w + (model_w + 2) * len(models) + 2))
    for r in recipes:
        print(f"{r['name']:<{name_w}}  ", end="")
        for m in models:
            cell = results[m["name"]][r["name"]]
            mark = "✓" if cell["valid"] else "✗"
            print(f"{mark:<{model_w}}  ", end="")
        print()
    print()

    # Print errors grouped by recipe.
    print("=" * 60)
    print("failures")
    print("=" * 60)
    any_fail = False
    for r in recipes:
        for m in models:
            cell = results[m["name"]][r["name"]]
            if not cell["valid"]:
                any_fail = True
                err = cell["error"] or "(no detail)"
                # Truncate ridiculous lines for the summary.
                if len(err) > 200:
                    err = err[:197] + "..."
                print(f"  {r['name']} × {m['name']}")
                print(f"    {err}")
    if not any_fail:
        print("  none — every combination validates 🎉")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
