"""Per-cell sliding-window decode of a .gsq splat sequence.

Replaces the original "decode the whole .gsq into RAM" model that
viser_headless used to follow. A 1.2 GB .gsq with 200k splats × 150
frames cost ~2 GB resident in dequantized float32 arrays — multiplied
by the LRU cap (5 cells) that was 10 GB worst case.

The SplatRing keeps the source bytes on disk and decodes a small ring
buffer (default K=32, configurable via ``GSFLUENT_DECODE_WINDOW_FRAMES``)
of frames around the current playback cursor. A background daemon
thread services decode requests posted to a queue. The render loop
calls ``get_frame(i)`` to read; if the frame is not in the ring it
returns None and the render loop is expected to HOLD (stutter) rather
than skip — a load-bearing invariant explained in the project notes.

Two exemptions to the no-skip rule live above this class:
  - Initial seek: render frame 0 when it is ready.
  - Scrub jump: ``request_window(idx)`` clears the ring and seeds a
    fresh K/2-wide window around ``idx``.

This module is intentionally free of viser/uvicorn imports so unit
tests can import it directly. Only ``numpy`` + the stdlib are used.
"""
from __future__ import annotations

import os
import struct as _struct
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np


# Decoded frame value: (xyz, quat).
FrameTuple = tuple[np.ndarray, np.ndarray]


def _read_window_size_from_env(default: int = 32) -> int:
    """Resolve GSFLUENT_DECODE_WINDOW_FRAMES with a safe fallback."""
    raw = os.environ.get("GSFLUENT_DECODE_WINDOW_FRAMES")
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if v <= 0:
        return default
    return v


def _parse_gsq_header(buf: bytes) -> dict:
    """Parse the 80-byte .gsq header + frame index.

    Vendored here so the ring module has no upward dependency on
    viser_headless (which itself imports viser/uvicorn).
    """
    if len(buf) < 80:
        raise ValueError(f"short header: {len(buf)} bytes")
    if buf[:4] != b"GSQ1":
        raise ValueError(f"not a .gsq: magic={buf[:4]!r}")
    (version, n_splats, n_frames) = _struct.unpack_from("<III", buf, 4)
    if version not in (1, 2):
        raise ValueError(f"unsupported .gsq version {version}")
    (fps_hint,) = _struct.unpack_from("<f", buf, 16)
    bbox_min = np.frombuffer(buf[20:32], dtype=np.float32).copy()
    bbox_max = np.frombuffer(buf[32:44], dtype=np.float32).copy()
    (static_offset, static_size) = _struct.unpack_from("<QI", buf, 44)

    index_end = 80 + n_frames * 16
    if len(buf) < index_end:
        raise ValueError(
            f"header read but index incomplete: have {len(buf)} need {index_end}"
        )
    # Per-entry layout is <QII> = (offset, size, flags). v1 always writes
    # flags=0; v2 sets bit0 on keyframes. We keep frame_index as (off, sz)
    # 2-tuples for backward compat and surface flags separately so the v2
    # reconstruction path can find the nearest keyframe.
    frame_index = []
    frame_flags: list[int] = []
    for i in range(n_frames):
        off, sz, fl = _struct.unpack_from("<QII", buf, 80 + i * 16)
        frame_index.append((off, sz))
        frame_flags.append(fl)
    return {
        "version": version,
        "n_splats": n_splats,
        "n_frames": n_frames,
        "fps_hint": fps_hint,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "static_offset": static_offset,
        "static_size": static_size,
        "frame_index": frame_index,
        "frame_flags": frame_flags,
    }


def _decompress_payload_i16(
    blob: bytes, n_splats: int
) -> tuple[np.ndarray, np.ndarray]:
    """Decompress a stored frame chunk into its two int16 arrays.

    Returns ``(xyz_i16(n,3), quat_i16(n,3))`` as plain int16 — no
    dequantization, no float conversion. For a v1 frame or a v2 keyframe
    these are absolute; for a v2 delta they are modular int16 deltas from
    the previous frame. The reshape uses the same split point as the
    encoder: ``xyz`` occupies the first ``n*3*2`` bytes, ``quat`` the next.
    """
    import zstandard as _zstd

    raw = _zstd.ZstdDecompressor().decompress(blob)
    xyz_i16 = np.frombuffer(raw[: n_splats * 3 * 2], dtype=np.int16).reshape(
        n_splats, 3
    )
    quat_i16 = np.frombuffer(
        raw[n_splats * 3 * 2 : n_splats * 3 * 2 * 2], dtype=np.int16
    ).reshape(n_splats, 3)
    return xyz_i16, quat_i16


def _dequantize_i16(
    xyz_i16: np.ndarray,
    quat_i16: np.ndarray,
    bbox_min: np.ndarray,
    span: np.ndarray,
) -> FrameTuple:
    """Dequantize ABSOLUTE int16 arrays to float (xyz, quat).

    Split out of the old ``_dequantize_frame`` so the v2 reconstruction
    path (which produces absolute int16 by accumulating deltas) can share
    the exact same float math. Inputs must already be absolute int16.
    """
    n_splats = xyz_i16.shape[0]
    xyz = bbox_min + (xyz_i16.astype(np.float32) + 32768.0) / 65535.0 * span
    qxyz = quat_i16.astype(np.float32) / 32767.0
    qw = np.sqrt(np.clip(1.0 - (qxyz * qxyz).sum(axis=1), 0.0, 1.0))
    quat = np.empty((n_splats, 4), dtype=np.float32)
    quat[:, 0] = qw
    quat[:, 1:4] = qxyz
    return xyz, quat


def _dequantize_frame(
    blob: bytes,
    n_splats: int,
    bbox_min: np.ndarray,
    span: np.ndarray,
) -> FrameTuple:
    """Dequantize a .gsq frame payload (vendored; no dependency on the server codec).

    Composes decompress + dequant. Valid for v1 chunks and v2 keyframes
    (both store absolute int16). v2 delta frames must NOT go through here —
    they need accumulation first (see ``SplatRing._decode_one``).
    """
    xyz_i16, quat_i16 = _decompress_payload_i16(blob, n_splats)
    return _dequantize_i16(xyz_i16, quat_i16, bbox_min, span)


def _decode_static_block(
    fh, header: dict
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode the static (rgb, opacity, scales) block from an open file."""
    import zstandard as _zstd

    fh.seek(header["static_offset"])
    raw = fh.read(header["static_size"])
    blob = _zstd.ZstdDecompressor().decompress(raw)
    n_sp = header["n_splats"]
    rgb_bytes = n_sp * 3 * 2
    rgb_f16 = np.frombuffer(blob[:rgb_bytes], dtype=np.float16).reshape(n_sp, 3).copy()
    opacity_u8 = np.frombuffer(blob[rgb_bytes : rgb_bytes + n_sp], dtype=np.uint8).copy()
    scales_f16 = np.frombuffer(
        blob[rgb_bytes + n_sp : rgb_bytes + n_sp + n_sp * 3 * 2],
        dtype=np.float16,
    ).reshape(n_sp, 3).copy()
    return rgb_f16, opacity_u8, scales_f16


class SplatRing:
    """A per-cell ring buffer of decoded (xyz, quat) frames.

    Owns:
      - A path to the source .gsq file (only opened when decoding).
      - Frame index + bbox (parsed once at construction).
      - A small ring of decoded frames keyed by absolute frame index.
      - One daemon decoder thread serving requests from a deque.

    Thread-safety: ``has_frame`` / ``get_frame`` / ``request_*`` /
    ``advance`` / ``close`` are all safe to call concurrently with the
    decoder thread. Internal mutation is serialized through ``_lock``.

    The class is intentionally NOT a context manager — viser_headless'
    cell LRU eviction path calls ``close()`` explicitly so the lifecycle
    is observable and a misuse can't silently leak threads.
    """

    # ---------- construction / shutdown ----------

    def __init__(
        self,
        gsq_path: Path,
        window_size: int | None = None,
        prefetch_ahead: int = 4,
    ) -> None:
        """Open and parse the .gsq header at ``gsq_path``.

        Decodes the static block (rgb, opacity, scales) eagerly because
        it's small (~6 MB for 200k splats) and required for every render.
        Frames are NOT decoded here — request frame 0 separately if you
        want it warm immediately (the constructor schedules that for you).
        """
        self._path = Path(gsq_path)
        if window_size is None:
            window_size = _read_window_size_from_env()
        if window_size <= 0:
            raise ValueError(f"window_size must be > 0, got {window_size}")
        self._window_size = int(window_size)
        self._prefetch_ahead = max(1, int(prefetch_ahead))

        # Parse header + frame index from disk. Cheap (~80 + 16 × n_frames
        # bytes) and lets every later op compare against ``n_frames``.
        with open(self._path, "rb") as f:
            head_buf = f.read(80)
            if len(head_buf) < 80:
                raise ValueError(f"{self._path}: short header")
            (n_frames_peek,) = _struct.unpack_from("<I", head_buf, 12)
            idx_buf = f.read(n_frames_peek * 16)
            self._header = _parse_gsq_header(head_buf + idx_buf)

            # Decode the static block once. Render loop reads from
            # ``static`` directly, no decode path on the hot tick.
            rgb_f16, opacity_u8, scales_f16 = _decode_static_block(f, self._header)

        bbox_min = self._header["bbox_min"]
        bbox_max = self._header["bbox_max"]
        self._span = (bbox_max - bbox_min).astype(np.float32)
        self._span[self._span == 0] = 1.0

        self._static = {
            "rgb_f16": rgb_f16,
            "opacity_u8": opacity_u8,
            "scales_f16": scales_f16,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "n_splats": self._header["n_splats"],
            "n_frames": self._header["n_frames"],
            "fps_hint": self._header["fps_hint"],
        }

        # Ring: OrderedDict mapping absolute frame idx -> (xyz, quat).
        # Eviction picks the frame farthest from ``_cursor``, NOT pure
        # LRU — playback wants the K frames closest to "where we are".
        self._ring: OrderedDict[int, FrameTuple] = OrderedDict()
        self._cursor = 0       # latest absolute frame the render loop pushed
        self._requests: list[int] = []  # queue of absolute frame indices
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        # Stats — read by tests and (optionally) by /state for diagnostics.
        self._stutter_count = 0
        self._decoded_count = 0
        self._evicted_count = 0

        # v2 sequential fast-path cache. Holds (idx, xyz_i16, quat_i16) of the
        # most recently reconstructed ABSOLUTE frame. When the next requested
        # frame is idx+1 and it's a delta, we add one decompressed delta onto
        # this cached absolute instead of re-walking from the keyframe — the
        # common case during forward playback. Best-effort and lock-free: a
        # stale or raced cache only ever costs a redundant keyframe-walk, never
        # correctness, because we re-validate ``cache_idx == idx - 1`` and only
        # the int16 (lossless) is reused.
        self._last_abs: tuple[int, np.ndarray, np.ndarray] | None = None

        self._thread = threading.Thread(
            target=self._decoder_loop,
            name=f"SplatRingDecode[{self._path.name}]",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the decoder thread and release the ring.

        Idempotent: safe to call from any thread, multiple times. Does
        NOT join the daemon thread with a long timeout — the file handle
        is opened per-decode in the decoder loop, so there's nothing to
        leak if the thread is still mid-decode at shutdown.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake.set()
        # Best-effort join. Decoder loop checks ``_closed`` between
        # frame decodes; a long join would block the eviction path on
        # the laptop where zstd decode of one frame can take ~50 ms.
        self._thread.join(timeout=1.0)
        with self._lock:
            self._ring.clear()
            self._requests.clear()

    # ---------- public properties ----------

    @property
    def gsq_path(self) -> Path:
        return self._path

    @property
    def n_frames(self) -> int:
        return int(self._static["n_frames"])

    @property
    def n_splats(self) -> int:
        return int(self._static["n_splats"])

    @property
    def fps_hint(self) -> float:
        return float(self._static["fps_hint"])

    @property
    def bbox_min(self) -> np.ndarray:
        return self._static["bbox_min"]

    @property
    def bbox_max(self) -> np.ndarray:
        return self._static["bbox_max"]

    @property
    def static(self) -> dict:
        """Read-only view of static attrs. Treat values as immutable."""
        return self._static

    @property
    def window_size(self) -> int:
        return self._window_size

    def stats(self) -> dict:
        """Snapshot of decode counters for diagnostics / tests."""
        with self._lock:
            return {
                "decoded": self._decoded_count,
                "evicted": self._evicted_count,
                "stutter": self._stutter_count,
                "ring_size": len(self._ring),
                "cursor": self._cursor,
            }

    # ---------- query / hint API ----------

    def has_frame(self, idx: int) -> bool:
        """True iff frame ``idx`` is currently decoded and in the ring."""
        if not (0 <= idx < self.n_frames):
            return False
        with self._lock:
            return idx in self._ring

    def get_frame(self, idx: int) -> FrameTuple | None:
        """Return decoded (xyz, quat) for frame ``idx`` or ``None``.

        Render loop's hot path; must be branch-light + lock-cheap. The
        ring lookup is O(1) on the OrderedDict; eviction never runs
        from this side.
        """
        if not (0 <= idx < self.n_frames):
            return None
        with self._lock:
            return self._ring.get(idx)

    def note_stutter(self) -> None:
        """Account a render-loop stutter for diagnostics.

        Called by the render loop when ``get_frame`` returns None for
        the next desired frame and playback holds instead of skipping.
        Cheap; the lock is uncontended on the render thread.
        """
        with self._lock:
            self._stutter_count += 1

    def request_frame(self, idx: int) -> None:
        """Queue an async decode for frame ``idx``. Idempotent.

        Returns immediately; caller polls ``has_frame``. Decoder will
        drop the request if the frame is already in the ring by the
        time it's picked up.
        """
        if not (0 <= idx < self.n_frames):
            return
        with self._lock:
            if self._closed:
                return
            if idx in self._ring:
                return
            if idx not in self._requests:
                self._requests.append(idx)
        self._wake.set()

    def request_window(self, center_idx: int) -> None:
        """Reseed the ring around ``center_idx`` (scrub-jump entry point).

        Clears the in-progress request queue, evicts ring entries that
        fall outside the new [center - K/2, center + K/2] window, and
        enqueues missing frames in distance-from-center order so the
        center frame decodes first.

        The cursor is also moved to ``center_idx`` so subsequent
        ``advance(n)`` calls grow the window forward as usual.
        """
        idx = max(0, min(int(center_idx), self.n_frames - 1))
        with self._lock:
            if self._closed:
                return
            half = max(1, self._window_size // 2)
            lo = max(0, idx - half)
            hi = min(self.n_frames - 1, idx + half)
            # Evict anything outside the new window.
            for k in list(self._ring.keys()):
                if k < lo or k > hi:
                    self._ring.pop(k, None)
                    self._evicted_count += 1
            # Replace request queue with an in-window list, ordered
            # nearest-to-center first.
            wanted = [
                j for j in range(lo, hi + 1)
                if j not in self._ring
            ]
            wanted.sort(key=lambda j: abs(j - idx))
            self._requests = wanted
            self._cursor = idx
        self._wake.set()

    def advance(self, current_idx: int) -> None:
        """Hint that playback is at ``current_idx`` and moving forward.

        Decoder pre-decodes the next ``prefetch_ahead`` frames. If the
        ring would overflow, the farthest-from-cursor frames are evicted
        first (most often: frames N below the cursor that were just
        watched).
        """
        idx = max(0, min(int(current_idx), self.n_frames - 1))
        with self._lock:
            if self._closed:
                return
            self._cursor = idx
            need = []
            for d in range(self._prefetch_ahead + 1):
                j = idx + d
                if j >= self.n_frames:
                    break
                if j in self._ring or j in self._requests:
                    continue
                need.append(j)
            if need:
                self._requests.extend(need)
                self._wake.set()

    # ---------- decoder thread ----------

    def _pick_next_request(self) -> int | None:
        """Pull the next request from the queue under the lock.

        Skips frames that are already in the ring (happens when scrub
        clears + re-enqueues races with a still-in-flight decode).
        """
        while self._requests:
            idx = self._requests.pop(0)
            if idx in self._ring:
                continue
            return idx
        return None

    def _evict_one_for_insert(self) -> None:
        """Make room for one insert: drop the entry farthest from cursor.

        Called under ``_lock``. If the ring is below capacity this is a
        no-op. Otherwise we always make room and let the caller insert
        the new frame (which may replace a slightly-closer one; the next
        advance trims it again if so).
        """
        if len(self._ring) < self._window_size:
            return
        # Find farthest-from-cursor key.
        farthest_key = None
        farthest_dist = -1
        cursor = self._cursor
        for k in self._ring.keys():
            d = abs(k - cursor)
            if d > farthest_dist:
                farthest_dist = d
                farthest_key = k
        if farthest_key is None:
            return
        # If the new frame would be even farther than the current farthest,
        # don't bother inserting — but it's still fine to let it in; the
        # next eviction (on the NEXT advance) will trim it again. Simpler
        # to just always evict + insert.
        self._ring.pop(farthest_key, None)
        self._evicted_count += 1

    def _decoder_loop(self) -> None:
        """Service decode requests until ``close()``.

        Opens the .gsq once per request (not once for the lifetime of
        the ring) — the OS keeps the inode in page cache, so re-open
        cost is negligible compared to the zstd decode (~50 ms for a
        200k-splat frame). One-shot opens make the close path trivial.
        """
        while True:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while True:
                with self._lock:
                    if self._closed:
                        return
                    idx = self._pick_next_request()
                if idx is None:
                    break
                try:
                    xyz, quat = self._decode_one(idx)
                except Exception:
                    # Decode failure (corrupt file, partial download race).
                    # Drop the request; render loop will keep stuttering
                    # on this index until the file fixes itself, which is
                    # acceptable since this is far off the happy path.
                    continue
                with self._lock:
                    if self._closed:
                        return
                    self._evict_one_for_insert()
                    self._ring[idx] = (xyz, quat)
                    self._decoded_count += 1
            with self._lock:
                if self._closed:
                    return

    def _read_payload_i16(self, f, idx: int) -> tuple[np.ndarray, np.ndarray]:
        """Read + decompress one stored frame chunk into int16 arrays.

        ``f`` is an already-open binary file handle; we seek to the frame's
        offset and decompress. The arrays are absolute (v1 / v2 keyframe) or
        modular deltas (v2 delta) depending on the frame's flags — callers
        decide how to interpret them.
        """
        off, sz = self._header["frame_index"][idx]
        f.seek(off)
        blob = f.read(sz)
        return _decompress_payload_i16(blob, self._static["n_splats"])

    def _decode_one(self, idx: int) -> FrameTuple:
        """Decode a single frame from disk. Called only from decoder thread.

        v1: each chunk is absolute — decompress + dequant directly.
        v2: reconstruct the absolute int16 (keyframe directly, sequential
        fast-path via the cache, or a keyframe-walk for cold/scrub jumps),
        then dequant. The reconstructed absolute int16 is stashed in
        ``self._last_abs`` so the NEXT sequential frame is one decompress.
        """
        bbox_min = self._static["bbox_min"]

        if self._header["version"] == 1:
            with open(self._path, "rb") as f:
                xyz_i16, quat_i16 = self._read_payload_i16(f, idx)
            return _dequantize_i16(xyz_i16, quat_i16, bbox_min, self._span)

        # ---- v2 reconstruction ----
        flags = self._header["frame_flags"]
        # Snapshot the cache once (it may be mutated by the other path).
        cache = self._last_abs

        with open(self._path, "rb") as f:
            if flags[idx] & 1:
                # Keyframe: the stored payload IS the absolute frame.
                xyz_i16, quat_i16 = self._read_payload_i16(f, idx)
                xyz_abs = xyz_i16.copy()
                quat_abs = quat_i16.copy()
            elif cache is not None and cache[0] == idx - 1:
                # Sequential fast path: absolute = prev_absolute + delta(idx).
                dx, dq = self._read_payload_i16(f, idx)
                xyz_abs = (cache[1] + dx).astype(np.int16)
                quat_abs = (cache[2] + dq).astype(np.int16)
            else:
                # Cold / scrub: walk forward from the nearest keyframe <= idx.
                kf = idx
                while kf > 0 and not (flags[kf] & 1):
                    kf -= 1
                xyz_i16, quat_i16 = self._read_payload_i16(f, kf)
                xyz_abs = xyz_i16.copy()
                quat_abs = quat_i16.copy()
                for j in range(kf + 1, idx + 1):
                    dx, dq = self._read_payload_i16(f, j)
                    xyz_abs = (xyz_abs + dx).astype(np.int16)
                    quat_abs = (quat_abs + dq).astype(np.int16)

        # Update the sequential fast-path cache (best-effort, lock-free).
        self._last_abs = (idx, xyz_abs, quat_abs)
        return _dequantize_i16(xyz_abs, quat_abs, bbox_min, self._span)

    # ---------- synchronous helpers (used by tests + initial paint) ----------

    def decode_blocking(self, idx: int, timeout: float = 5.0) -> FrameTuple:
        """Decode + insert frame ``idx`` synchronously.

        Convenience for tests and the initial "make sure frame 0 is
        ready" path. Bypasses the decoder thread by doing the work on
        the caller's thread, then inserts the result into the ring under
        the lock.
        """
        if not (0 <= idx < self.n_frames):
            raise IndexError(f"frame {idx} out of range [0, {self.n_frames})")
        # Fast path: already in ring.
        cached = self.get_frame(idx)
        if cached is not None:
            return cached
        xyz, quat = self._decode_one(idx)
        with self._lock:
            if not self._closed:
                self._evict_one_for_insert()
                self._ring[idx] = (xyz, quat)
                self._decoded_count += 1
        return xyz, quat
