"""Seed a demo recipe with a valid MPM material name.

Idempotent: if a recipe named 'demo-jelly' already exists, do nothing.

The engine's vocabulary (mpm_solver_warp.set_parameters_dict) accepts:
  jelly | metal | sand | foam | snow | plasticine | watermelon

Anything else fails with 'Undefined material type' at sim init. New users
don't need to know this — this seed gives them a runnable starter.

Usage (against a live api):
  curl -fsS http://<host>:18000/v1/system/health   # verify api is up
  python apps/api/scripts/seed_demo.py http://<host>:18000
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:18000"

RECIPE = {
    "name": "demo-jelly",
    "content": {
        "material": "jelly",
        "grid_lim": 4.0,
        "n_grid": 64,
        "substep_dt": 1e-4,
        "frame_dt": 1e-2,
        "frame_num": 10,
        # Soft solid (low E so it visibly deforms).
        "E": 1e4,
        "nu": 0.3,
        "density": 200.0,
        # No boundary conditions — let it free-fall under gravity by default.
        "g": [0.0, -9.8, 0.0],
    },
}


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_API).rstrip("/")

    existing = json.loads(_get(f"{base}/v1/recipes"))
    for item in existing.get("items", []):
        if item.get("name") == RECIPE["name"]:
            print(f"already present: {item['id']}  name={item['name']}  v={item['version']}")
            return 0

    body = json.dumps(RECIPE).encode()
    created = json.loads(_post(f"{base}/v1/recipes", body))
    print(f"created: {created['id']}  name={created['name']}  v={created['version']}")
    return 0


def _get(url: str) -> str:
    return urllib.request.urlopen(url, timeout=10).read().decode()


def _post(url: str, body: bytes) -> str:
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=10).read().decode()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"POST failed {e.code}: {e.read().decode()[:300]}\n")
        raise


if __name__ == "__main__":
    sys.exit(main())
