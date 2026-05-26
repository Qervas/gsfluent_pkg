"""Drive continuous playback of any .gsq in the viser cache dir.

The React SPA only lists *library* sequences, so loose cache files (e.g.
pruned A/B variants) don't show up there. But viser_headless boot-scans
the whole cache dir and keys each file as `sequence:<stem>`, so we can
load + play any of them by poking its control API directly.

Usage (with `npm start` already running, or a standalone viser_headless):

    # play a pruned variant, watch http://localhost:8091
    python server/tools/view_gsq.py cluster_6_15_demolition_p980

    # the original, looping, at 24fps
    python server/tools/view_gsq.py cluster_6_15_demolition_2026-05-21T0843 --loop

    # slower, to inspect detail frame by frame
    python server/tools/view_gsq.py cluster_6_15_demolition_p999 --fps 8

`name` is the .gsq filename WITHOUT the .gsq extension, living in
--cache-dir (default work/cache/viser). Open http://localhost:8091 in a
browser to watch; this script just advances the frame cursor that
viser_headless renders.
"""
from __future__ import annotations

import argparse
import json
import struct
import time
import urllib.request
from pathlib import Path

_BOOTSTRAP = Path(__file__).resolve().parents[2]


# Bypass any http_proxy/https_proxy env vars. The control API is on
# loopback and must NOT be routed through a proxy — otherwise a request to
# 127.0.0.1:8092 gets forwarded to $http_proxy and comes back 502. This
# mirrors viser_headless's own httpx `trust_env=False`.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(control: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{control}/set",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _NO_PROXY_OPENER.open(req, timeout=60) as r:
        return json.loads(r.read())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("name", help="gsq filename stem in --cache-dir (no .gsq)")
    p.add_argument("--control", default="http://127.0.0.1:8092",
                   help="viser_headless control API (default :8092)")
    p.add_argument("--cache-dir", default=str(_BOOTSTRAP / "work" / "cache" / "viser"))
    p.add_argument("--fps", type=float, default=24.0)
    p.add_argument("--loop", action="store_true", help="loop forever (Ctrl-C to stop)")
    args = p.parse_args()

    gsq = Path(args.cache_dir) / f"{args.name}.gsq"
    if not gsq.is_file():
        print(f"ERROR: {gsq} not found. Available:")
        for f in sorted(Path(args.cache_dir).glob("*.gsq")):
            print(f"  {f.stem}")
        return 1

    # n_frames lives at byte offset 12 of the header (<III after the 4-byte magic).
    with open(gsq, "rb") as f:
        head = f.read(16)
    if head[:4] != b"GSQ1":
        print(f"ERROR: {gsq} is not a GSQ1 file")
        return 1
    n_frames = struct.unpack_from("<I", head, 12)[0]

    cell = f"sequence:{args.name}"
    print(f"loading {cell}  ({n_frames} frames)")
    print(f"→ open http://127.0.0.1:8091 in a browser to watch\n")
    try:
        resp = _post(args.control, {"cell": cell, "frame": 0})
    except Exception as e:
        print(f"ERROR: can't reach viser control API at {args.control}: {e}")
        print("Is `npm start` (or viser_headless) running?")
        return 1
    if not resp.get("ok", True):
        print(f"ERROR: viser rejected the cell: {resp}")
        return 1

    # Give the ring a moment to decode frame 0 before advancing.
    time.sleep(2.0)

    dt = 1.0 / args.fps
    print(f"playing at {args.fps:.0f} fps (Ctrl-C to stop)…")
    try:
        while True:
            for fidx in range(n_frames):
                t0 = time.perf_counter()
                _post(args.control, {"frame": fidx})
                slack = dt - (time.perf_counter() - t0)
                if slack > 0:
                    time.sleep(slack)
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
