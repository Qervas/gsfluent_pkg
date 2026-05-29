"""CLI: generate a flat sim recipe from MATERIAL × SCENARIO × BUILDING.

    python server/tools/compose_recipe.py \\
        --material plasticine_weak \\
        --scenario wrecking \\
        --building cluster_6_15 \\
        --out /tmp/composed.json

Prints the composed recipe to stdout (and to --out if given). Runs the Phase-0
linter on the result and reports findings — the generated recipe should always
be CFL-safe by construction (substep_dt is CFL-derived).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT / "server"))

from gsfluent.authoring import compose  # noqa: E402
from gsfluent.core import recipe_lint  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--material", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--building", required=True)
    p.add_argument("--out", default=None, help="write JSON here too")
    p.add_argument("--frame_num", type=int, default=None,
                   help="override scenario frame_num (e.g. short test render)")
    args = p.parse_args()

    recipe = compose(args.material, args.scenario, args.building)
    if args.frame_num is not None:
        recipe["frame_num"] = args.frame_num

    findings = recipe_lint.lint_recipe(recipe)
    for f in findings:
        print(f"[lint:{f.severity}] {f.rule_id} {f.param}: {f.message}",
              file=sys.stderr)
    if recipe_lint.has_errors(findings):
        print("REFUSING: composed recipe has lint errors", file=sys.stderr)
        return 1

    text = json.dumps(recipe, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
