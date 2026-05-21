"""Client-side sync daemon.

Mirrors the server's per-sequence caches (`viser.npz` for Splats mode,
`frames.bin` for Points mode) plus the per-sequence `_meta.json` so the
client's outliner surfaces sim runs produced on the server without a
manual stub. See ../docs/ARCHITECTURE.md for the split-topology
rationale — short version: pushing per-frame xyz over WAN at 30 fps is
~2 Gbps, hopeless. A one-time .npz download per sequence then local
playback is the only path that scales.

Loop, every `--interval` seconds:
    1. GET ${GSFLUENT_SERVER}/api/sequences
    2. For each sequence: write `<library>/<name>/_meta.json` from the
       response (atomic, compare-and-skip if unchanged) so the client's
       /api/sequences walk surfaces the run with proper source / model
       / bbox metadata.
    3. If server's cache mtime > local file's mtime (or local file is
       missing), download .npz via HTTP Range.
    4. Atomic write: stream to <name>.npz.partial, then rename in place.
       Partial downloads on the next pass resume via Range.
    5. After successful .npz download, POST to viser_headless's
       /reload?cell=<name> so it re-mmaps the new file.
    6. Write a status snapshot to --status-file (default
       /tmp/gsfluent_sync_status.json) for the UI's offline indicator.

Failure modes:
    - Server unreachable: status flips to {"online": false}, retry next tick.
    - Download interrupted (e.g., network drop): partial file stays, next
      pass resumes with Range header.
    - viser_headless unreachable: warning logged, daemon continues. The
      next viser_headless start will mmap the latest local file anyway.

Usage:
    python frontend/python/sync_daemon.py \\
        --server http://<server-host>:8080 \\
        --cache-root work/cache \\
        --viser-control http://localhost:8092 \\
        --interval 10
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Allowlist regex for sequence names received from the server. We don't
# trust the server unconditionally — a compromised or buggy backend
# could try to write `name="../../.ssh/authorized_keys"` and we'd
# happily write its bytes to the client. Library sequence names already
# pass through the same regex on the server side; this enforces it on
# the wire, too.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _active_run_present(server_base: str) -> bool:
    """True iff the server has at least one active sim run.

    Used by the poll loop to switch cadence between idle (slow,
    bandwidth-friendly) and during-sim (fast, ~1s updates so the
    workbench sees per-batch frame progress instead of 10s lag).
    """
    try:
        with urllib.request.urlopen(
            f"{server_base.rstrip('/')}/api/runs", timeout=5,
        ) as r:
            return len(json.loads(r.read())) > 0
    except Exception:
        return False


@dataclass
class SyncStatus:
    """Snapshot written to --status-file each tick. The React UI reads
    this (via a small /sync-status endpoint TBD) to render an offline
    badge + last-sync timestamp."""
    online: bool = False
    last_check_unix: float = 0.0
    last_success_unix: float = 0.0
    server_url: str = ""
    sequences_seen: int = 0
    files_synced: int = 0
    bytes_downloaded: int = 0
    error: Optional[str] = None
    per_sequence: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "online":            self.online,
            "last_check_unix":   self.last_check_unix,
            "last_success_unix": self.last_success_unix,
            "server_url":        self.server_url,
            "sequences_seen":    self.sequences_seen,
            "files_synced":      self.files_synced,
            "bytes_downloaded":  self.bytes_downloaded,
            "error":             self.error,
            "per_sequence":      self.per_sequence,
        }


def _fetch_json(url: str, timeout: float) -> dict | list:
    """One-shot GET → JSON. Raises urllib's HTTPError/URLError on failure;
    callers translate to status flips."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, timeout: float) -> None:
    """Fire-and-forget POST (no body). Used for viser's /reload — return
    value is uninteresting, but we want to know if the call failed."""
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _download_resumable(url: str, dst: Path, expected_bytes: Optional[int],
                        chunk: int = 1024 * 1024, timeout: float = 60.0,
                        on_progress=None) -> int:
    """Download `url` to `dst` with HTTP Range resume.

    The file is written to `<dst>.partial` and renamed in place on
    completion (atomic on POSIX). If `<dst>.partial` already exists from
    a previous run, we send `Range: bytes=<size>-` and append. If the
    server doesn't support Range (HTTP 200 instead of 206), we start
    from zero — slightly wasteful but always correct.

    Returns the number of bytes downloaded *this call* (for status
    accounting). Caller is responsible for deciding whether to call
    based on mtime/size comparison.

    `on_progress(bytes_so_far, total)` (optional) is invoked periodically
    (~2 s or ~16 MB cadence, whichever fires first) so the caller can
    surface progress to the workbench. Errors raised inside the
    callback are swallowed — progress is best-effort UX, not correctness."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    partial = dst.with_suffix(dst.suffix + ".partial")
    have = partial.stat().st_size if partial.is_file() else 0
    headers = {}
    if have > 0:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    written = 0
    # Heartbeat: emit progress every 2 s or every 16 MB, whichever first.
    # Two limits because either alone misses common cases — slow links
    # under-emit on byte-count and fast bursts under-emit on time.
    PROG_INTERVAL_S = 2.0
    PROG_INTERVAL_B = 16 * 1024 * 1024
    last_emit_t = time.time()
    last_emit_b = 0

    def _emit(force: bool = False) -> None:
        nonlocal last_emit_t, last_emit_b
        if on_progress is None:
            return
        now = time.time()
        bytes_so_far = have + written
        if not force and (
            now - last_emit_t < PROG_INTERVAL_S and
            bytes_so_far - last_emit_b < PROG_INTERVAL_B
        ):
            return
        last_emit_t = now
        last_emit_b = bytes_so_far
        try:
            on_progress(bytes_so_far, expected_bytes)
        except Exception:
            pass

    try:
        # Initial heartbeat — emits 0% (or have-bytes for resumes) so
        # the UI shows the download just started before any chunks land.
        _emit(force=True)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # HTTP 206 = Partial Content (server honored Range).
            # HTTP 200 = server ignored Range; restart from scratch.
            if resp.status == 200 and have > 0:
                have = 0
                if partial.is_file():
                    partial.unlink()
            mode = "ab" if have > 0 else "wb"
            with open(partial, mode) as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    written += len(buf)
                    _emit()
    except (urllib.error.URLError, TimeoutError) as e:
        # Leave partial in place — next pass will resume.
        raise IOError(f"download failed for {url}: {e}") from e

    # Sanity check: if server told us the full size, the partial should match.
    final_size = partial.stat().st_size
    if expected_bytes is not None and final_size != expected_bytes:
        # Don't rename, and DELETE the partial so the next pass starts
        # fresh. Leaving it on disk plus Range-resume would keep
        # appending to a known-bad file forever (e.g., if a proxy
        # injected an error page and the server's `Content-Length` got
        # confused on the next attempt).
        try:
            partial.unlink()
        except OSError:
            pass
        raise IOError(
            f"size mismatch on {url}: got {final_size} bytes, "
            f"server reported {expected_bytes} — partial dropped"
        )

    partial.replace(dst)
    return written


# Fields the server's /api/sequences response carries that belong in
# the canonical `_meta.json` (everything the `_SequenceMeta` pydantic
# model defines). We mirror exactly these so writing the local file is
# byte-stable against a server that augments the response with extra
# UI-only fields (is_broken, cache, path, frame_count from a live walk).
_META_FIELDS = (
    "name", "kind", "source", "source_path", "model_ref",
    "frame_count", "fps_hint", "n_splats", "bbox_initial",
    "coord_convention", "first_frame_full", "created_at",
    "converted_from",
)


def _mirror_meta(seq_dict: dict, library_root: Path) -> bool:
    """Write `<library_root>/<name>/_meta.json` from the server's
    sequence entry. Returns True if a write happened (file created or
    contents changed), False if the local copy already matched or the
    server response was strictly less informative than the local file.

    The library walk on the client's /api/sequences requires the
    `<name>/` directory to exist for the sequence to surface — we
    create it if missing so the .npz arriving in cache/viser/ becomes
    discoverable without a separate `migrate` step.

    Anti-clobber guard: if the server returns source="unknown" (server
    has the dir but no `_meta.json` for it — e.g. a sequence produced
    before the runner started writing meta) and a local meta already
    exists, leave the local file alone. The server has nothing
    authoritative to say; overwriting with defaults would strictly
    lose information. This is the bug that destroyed
    `jelly_cluster_server_v2`'s curated meta on the first run of this
    daemon."""
    name = seq_dict.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.match(name):
        return False
    payload: dict = {}
    for k in _META_FIELDS:
        if k in seq_dict:
            payload[k] = seq_dict[k]
    payload.setdefault("name", name)
    payload.setdefault("kind", "sequence")
    payload.setdefault("source", "unknown")

    seq_dir = library_root / name
    meta_path = seq_dir / "_meta.json"
    if payload.get("source") == "unknown" and meta_path.is_file():
        # Server has nothing real to say; preserve whatever the client
        # already knows (could be a curated hand-written meta).
        return False

    seq_dir.mkdir(parents=True, exist_ok=True)
    new_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    if meta_path.is_file():
        try:
            old_bytes = meta_path.read_bytes()
        except OSError:
            old_bytes = b""
        if old_bytes == new_bytes:
            return False
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_bytes(new_bytes)
    tmp.replace(meta_path)
    return True


def _local_mtime(p: Path) -> Optional[float]:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _needs_sync(server_mtime: Optional[float], server_bytes: Optional[int],
                local_path: Path) -> bool:
    """True if we should download. Cases:
    - Server reports the file exists (server_mtime not None) and either
      local doesn't exist OR server is newer.
    - We use bytes as a secondary sanity check — if local matches server
      mtime but bytes differ, force a re-sync.

    Comparing mtimes across a server↔client boundary is iffy (clock skew,
    fs precision). We accept a 1-second slop so identical files don't
    re-download on every poll just because of fs timestamp rounding."""
    if server_mtime is None:
        return False
    local = _local_mtime(local_path)
    if local is None:
        return True
    if server_mtime - local > 1.0:
        return True
    if server_bytes is not None and local_path.stat().st_size != server_bytes:
        return True
    return False


def sync_once(server: str, cache_root: Path, library_root: Path,
              viser_control: Optional[str], status: SyncStatus,
              verbose: bool, status_file: Optional[Path] = None) -> None:
    """One pass over /api/sequences. Mutates `status` in place so the
    caller can write it to --status-file after each tick.

    Layout we mirror to:
      <cache_root>/viser/<name>.npz         (Splats mode, mmap'd by viser_headless)
      <cache_root>/frames-bin/<name>.bin    (Points mode, mmap'd by local_stream)
      <library_root>/<name>/_meta.json      (Outliner metadata)
    """
    viser_dir = cache_root / "viser"
    frames_bin_dir = cache_root / "frames-bin"

    status.last_check_unix = time.time()
    status.error = None

    try:
        seqs = _fetch_json(f"{server}/api/sequences", timeout=10.0)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        status.online = False
        status.error = f"server unreachable: {e}"
        if verbose:
            print(f"[sync] {status.error}", file=sys.stderr)
        return

    status.online = True
    status.last_success_unix = status.last_check_unix
    if not isinstance(seqs, list):
        status.error = "unexpected /api/sequences response shape"
        return
    status.sequences_seen = len(seqs)

    for s in seqs:
        name = s.get("name")
        if not isinstance(name, str) or not _SAFE_NAME.match(name):
            # Hostile / buggy server attempting `name='../...'`. Skip
            # cleanly so we keep syncing the rest of the (valid) list.
            if verbose:
                print(f"[sync] rejecting unsafe sequence name: {name!r}",
                      file=sys.stderr)
            continue
        # Per-sequence cache descriptor. `None` from the server means
        # "cache torn down" (could opt into delete-local), missing means
        # "the server doesn't know about cache". For now we treat both
        # the same — skip — and document the distinction below.
        cache_info = s.get("cache") or {}
        per: dict = status.per_sequence.setdefault(name, {})
        # URL-encode the name for the GET path even though we've
        # allowlisted it — defense in depth against URL parsers that
        # accept dot-segments differently from our regex.
        name_q = urllib.parse.quote(name, safe="")

        # ---- _meta.json -----------------------------------------------
        # Mirror first so the seq dir + meta exist before the .npz
        # arrives in the parallel cache tree. /api/sequences on the
        # client walks library_root to surface entries; without this
        # the .npz lands in cache/viser/ unreachable from the outliner.
        try:
            wrote = _mirror_meta(s, library_root)
            if wrote:
                per["meta"] = {"ok": True, "synced_unix": time.time()}
                if verbose:
                    print(f"[sync] {name}/_meta.json: updated")
            else:
                per.setdefault("meta", {"ok": True, "synced_unix": 0.0})
        except OSError as e:
            per["meta"] = {"ok": False, "error": str(e)}
            if verbose:
                print(f"[sync] {name}/_meta.json: FAILED — {e}", file=sys.stderr)

        # ---- viser .npz ------------------------------------------------
        npz_mtime = cache_info.get("viser_npz_mtime")
        npz_bytes = cache_info.get("viser_npz_bytes")
        npz_local = viser_dir / f"{name}.npz"
        if _needs_sync(npz_mtime, npz_bytes, npz_local):
            url = f"{server}/api/sequences/{name_q}/cache/viser.npz"

            # Per-chunk progress: stamp the in-memory status with the
            # latest bytes-so-far and flush to disk so the workbench's
            # /sync-status poller sees the updates mid-download. Cadence
            # is controlled by _download_resumable (~2 s or 16 MB).
            def _on_dl_progress(b: int, total: Optional[int], _name=name, _per=per) -> None:
                _per["download"] = {
                    "bytes": int(b),
                    "total": int(total) if total else None,
                    "updated_unix": time.time(),
                }
                if status_file is not None:
                    write_status(status, status_file)

            try:
                w = _download_resumable(url, npz_local, npz_bytes,
                                        on_progress=_on_dl_progress)
                status.files_synced += 1
                status.bytes_downloaded += w
                # Clear the in-flight entry; the viser_npz block carries
                # the "done" signal. Leaving "download" around would
                # confuse the workbench into thinking another tick is
                # pending.
                per.pop("download", None)
                per["viser_npz"] = {"ok": True, "bytes": npz_bytes, "synced_unix": time.time()}
                if verbose:
                    print(f"[sync] {name}/viser.npz: {w/1e6:.1f} MB downloaded")
                # Tell viser to re-mmap, if reachable.
                if viser_control:
                    try:
                        _http_post(f"{viser_control}/reload?cell={name}", timeout=5.0)
                    except (urllib.error.URLError, TimeoutError):
                        # Not fatal; viser will pick up the new file on next start.
                        if verbose:
                            print(f"[sync]   viser /reload?cell={name} unreachable")
            except IOError as e:
                per.pop("download", None)
                per["viser_npz"] = {"ok": False, "error": str(e)}
                if verbose:
                    print(f"[sync] {name}/viser.npz: FAILED — {e}", file=sys.stderr)

        # ---- frames.bin -----------------------------------------------
        bin_mtime = cache_info.get("frames_bin_mtime")
        bin_bytes = cache_info.get("frames_bin_bytes")
        bin_local = frames_bin_dir / f"{name}.bin"
        if _needs_sync(bin_mtime, bin_bytes, bin_local):
            url = f"{server}/api/sequences/{name_q}/cache/frames.bin"
            try:
                w = _download_resumable(url, bin_local, bin_bytes)
                status.files_synced += 1
                status.bytes_downloaded += w
                per["frames_bin"] = {"ok": True, "bytes": bin_bytes, "synced_unix": time.time()}
                if verbose:
                    print(f"[sync] {name}/frames.bin: {w/1e6:.1f} MB downloaded")
            except IOError as e:
                per["frames_bin"] = {"ok": False, "error": str(e)}
                if verbose:
                    print(f"[sync] {name}/frames.bin: FAILED — {e}", file=sys.stderr)


def write_status(status: SyncStatus, status_file: Path) -> None:
    """Best-effort atomic write of the status JSON. Failures here aren't
    fatal — the daemon's correctness doesn't depend on the status file
    being readable."""
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = status_file.with_suffix(status_file.suffix + ".tmp")
        tmp.write_text(json.dumps(status.to_dict(), indent=2))
        tmp.replace(status_file)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--server", default=os.environ.get("GSFLUENT_SERVER", ""),
                    help="Base URL of the gsfluent backend (or set $GSFLUENT_SERVER)")
    ap.add_argument("--cache-root", required=True, type=Path,
                    help="Local cache root (typically <pkg>/work/cache)")
    ap.add_argument("--library-root", type=Path, default=None,
                    help="Local library sequences root "
                         "(default: <cache-root>/../library/sequences). "
                         "Where per-sequence _meta.json files are mirrored "
                         "so the client's /api/sequences walk picks them up.")
    ap.add_argument("--viser-control",
                    default=os.environ.get("VISER_CONTROL_URL", "http://localhost:8092"),
                    help="Viser headless control URL for /reload notifications")
    ap.add_argument("--interval", type=float, default=10.0,
                    help="Poll interval in seconds")
    # Per-user status file. We prefer $XDG_RUNTIME_DIR (mode 0700,
    # cleaned on logout, single-user) over /tmp (world-readable,
    # collision-prone if multiple daemons run for different users on
    # the same box). Falls back to /tmp/<uid>/ if XDG isn't set.
    _xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{os.getuid()}"
    ap.add_argument("--status-file", type=Path,
                    default=Path(_xdg) / "gsfluent_sync_status.json",
                    help="Where to write per-tick status JSON")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    if not args.server:
        print("ERROR: --server (or $GSFLUENT_SERVER) is required.", file=sys.stderr)
        return 2

    # Strip a trailing slash so all our f-strings are well-formed.
    server = args.server.rstrip("/")
    viser_control = args.viser_control.rstrip("/") if args.viser_control else None
    library_root = (
        args.library_root
        if args.library_root is not None
        else args.cache_root.parent / "library" / "sequences"
    )

    status = SyncStatus(server_url=server)
    write_status(status, args.status_file)  # initial empty snapshot

    stop_requested = [False]
    def _stop(_signum, _frame):
        stop_requested[0] = True
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f">>> sync_daemon: server={server} cache={args.cache_root} "
          f"interval={args.interval}s")
    print(f">>>             library → {library_root}")
    if viser_control:
        print(f">>>             viser reload → {viser_control}/reload?cell=<name>")
    print(f">>>             status → {args.status_file}")

    while not stop_requested[0]:
        sync_once(server, args.cache_root, library_root, viser_control,
                  status, args.verbose, status_file=args.status_file)
        write_status(status, args.status_file)
        # Cadence: 1s when a sim is running (so users see frames advance
        # in viser without the 10s lag), interval (default 10s) otherwise.
        # The /api/runs endpoint is cheap (~5ms); polling it every tick
        # is fine.
        next_sleep = 1.0 if _active_run_present(server) else float(args.interval)
        # Sleep in 0.5s slices so SIGINT is responsive.
        slept = 0.0
        while slept < next_sleep and not stop_requested[0]:
            time.sleep(0.5)
            slept += 0.5

    print(">>> sync_daemon: shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
