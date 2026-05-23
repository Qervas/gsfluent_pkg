"""Render-loop strict-sequential no-skip invariant tests.

Exercises the ``decide_next_idx_and_push`` function plus the full
SplatRing + render-decide composition. These guard the rule:

  During continuous playback, frames render in 1→2→3→4 order. NEVER skip.
  If the next frame isn't decoded in time, HOLD (stutter) instead of
  advancing past it.

Exemptions: scrub jumps and the initial frame-0 paint.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest


# ----- import viser_headless's decide function without viser/uvicorn -----
# The module DOES import viser/uvicorn at the top — those are deps of the
# venv, so we can just import the full module. The dependency cost at
# test time (~150 ms one-time) is acceptable given the bench coverage
# this gives us over the render-loop invariant.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VIS_PATH = _REPO_ROOT / "frontend" / "python"
sys.path.insert(0, str(_VIS_PATH))

import viser_headless as vh  # noqa: E402
import splat_ring as sr  # noqa: E402

SplatRing = vh.SplatRing
decide = vh.decide_next_idx_and_push
make_static_cell = vh.make_static_cell


# ----- minimal .gsq fixture (same shape as test_splat_ring.py) ------------


def _write_minimal_gsq(path: Path, n_splats: int = 6, n_frames: int = 16) -> dict:
    import zstandard as zstd

    rng = np.random.default_rng(seed=0xCAFE)
    xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
    for t in range(n_frames):
        xyz_all[t] = (
            np.full(3, t * 0.1, dtype=np.float32)
            + rng.standard_normal((n_splats, 3)).astype(np.float32) * 0.01
        )
    quat_all = np.zeros((n_frames, n_splats, 4), dtype=np.float32)
    quat_all[..., 0] = 1.0
    rgb = rng.uniform(0, 1, size=(n_splats, 3)).astype(np.float32)
    opacity = rng.uniform(0, 1, size=(n_splats,)).astype(np.float32)
    scales = np.exp(rng.standard_normal((n_splats, 3)).astype(np.float32) * 0.3)

    bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
    span = (bbox_max - bbox_min).astype(np.float32)
    span[span == 0] = 1.0
    xyz_q = (
        np.clip(((xyz_all - bbox_min) / span) * 65535.0 - 32768.0, -32768, 32767)
        .round().astype(np.int16)
    )
    quat_q = np.clip(quat_all[..., 1:4] * 32767.0, -32767, 32767).round().astype(np.int16)
    rgb_f16 = rgb.astype(np.float16)
    opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
    scales_f16 = scales.astype(np.float16)
    cctx = zstd.ZstdCompressor(level=3)
    static_compressed = cctx.compress(
        rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
    )
    frame_chunks = [
        cctx.compress(xyz_q[t].tobytes() + quat_q[t].tobytes())
        for t in range(n_frames)
    ]
    HEADER_SIZE = 80
    INDEX_ENTRY_SIZE = 16
    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    frame0_offset = static_offset + static_size
    index_entries = []
    off = frame0_offset
    for c in frame_chunks:
        index_entries.append((off, len(c)))
        off += len(c)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"GSQ1")
        f.write(struct.pack("<III", 1, n_splats, n_frames))
        f.write(struct.pack("<f", 24.0))
        f.write(bbox_min.tobytes())
        f.write(bbox_max.tobytes())
        f.write(struct.pack("<QI", static_offset, static_size))
        f.write(b"\x00" * 24)
        for o, s in index_entries:
            f.write(struct.pack("<QII", o, s, 0))
        f.write(static_compressed)
        for c in frame_chunks:
            f.write(c)
    return {"n_frames": n_frames, "n_splats": n_splats}


def _wait_until(predicate, timeout=2.0, poll=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


# ----- pure decide() unit tests --------------------------------------------


class _FakeRing:
    """Drop-in stand-in for SplatRing with controllable ready-frames set."""

    def __init__(self, n_frames: int, ready: set[int]):
        self.n_frames = n_frames
        self.n_splats = 4
        self._ready = set(ready)
        self.window_requests: list[int] = []

    def get_frame(self, idx: int):
        if idx in self._ready:
            return np.zeros((4, 3), dtype=np.float32), np.tile([1.0, 0, 0, 0], (4, 1))
        return None

    def has_frame(self, idx: int) -> bool:
        return idx in self._ready

    def request_window(self, idx: int) -> None:
        self.window_requests.append(idx)

    def request_frame(self, idx: int) -> None:
        pass

    def advance(self, idx: int) -> None:
        pass

    def note_stutter(self) -> None:
        pass


def _cell_from_ring(ring) -> dict:
    return {
        "version": 2,
        "ring": ring,
        "n_frames": ring.n_frames,
        "rgb": np.zeros((4, 3), dtype=np.float32),
        "opacity": np.zeros((4, 1), dtype=np.float32),
        "scales_sq": np.ones((4, 3), dtype=np.float32),
        "bbox_lo": np.zeros(3, dtype=np.float32),
        "bbox_hi": np.ones(3, dtype=np.float32),
    }


def test_decide_initial_paint_waits_for_frame_zero():
    ring = _FakeRing(n_frames=10, ready={0})
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=5, pushed=-1, scrub_pending=False)
    assert next_idx == 0
    assert push_now is True
    assert clear_scrub is False


def test_decide_initial_paint_holds_when_frame_zero_not_ready():
    ring = _FakeRing(n_frames=10, ready=set())
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=5, pushed=-1, scrub_pending=False)
    assert next_idx == 0
    assert push_now is False
    assert clear_scrub is False


def test_decide_continuous_advances_by_one_when_ready():
    ring = _FakeRing(n_frames=20, ready={5, 6})
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=10, pushed=5, scrub_pending=False)
    assert next_idx == 6
    assert push_now is True
    assert clear_scrub is False


def test_decide_continuous_stutters_when_next_frame_not_ready():
    """The load-bearing invariant: hold pushed=5 when 6 is not decoded.

    Even though SPA's desired=10, render loop refuses to skip past 6.
    decide() reports next_idx=6 so the render loop knows which frame
    to request; push_now=False keeps the held frame visible.
    """
    ring = _FakeRing(n_frames=20, ready={5})   # 6 NOT ready
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=10, pushed=5, scrub_pending=False)
    assert next_idx == 6              # the frame we're WAITING on
    assert push_now is False           # but it's not ready yet
    assert clear_scrub is False


def test_decide_paused_holds():
    """When desired <= pushed (SPA paused), do nothing."""
    ring = _FakeRing(n_frames=20, ready={5, 6, 7})
    cell = _cell_from_ring(ring)
    next_idx, push_now, _ = decide(cell, desired=5, pushed=5, scrub_pending=False)
    assert next_idx is None
    assert push_now is False
    # Same for desired strictly less than pushed (clock briefly behind).
    next_idx, push_now, _ = decide(cell, desired=3, pushed=5, scrub_pending=False)
    assert next_idx is None
    assert push_now is False


def test_decide_end_of_sequence_holds():
    ring = _FakeRing(n_frames=10, ready={9})
    cell = _cell_from_ring(ring)
    # pushed at last frame, desired wraps back via SPA — render loop sees
    # desired <= pushed and holds.
    next_idx, push_now, _ = decide(cell, desired=0, pushed=9, scrub_pending=False)
    assert next_idx is None
    assert push_now is False
    # pushed at last frame, desired beyond end (impossible if SPA clamps,
    # but check the boundary).
    next_idx, push_now, _ = decide(cell, desired=15, pushed=9, scrub_pending=False)
    assert next_idx is None  # next would be 10 >= n_frames → hold
    assert push_now is False


def test_decide_scrub_jumps_directly_to_desired_when_ready():
    ring = _FakeRing(n_frames=100, ready={42})
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=42, pushed=5, scrub_pending=True)
    assert next_idx == 42
    assert push_now is True
    assert clear_scrub is True
    # Scrub path also pings the ring to expand the window.
    assert 42 in ring.window_requests


def test_decide_scrub_holds_when_target_not_ready():
    ring = _FakeRing(n_frames=100, ready=set())
    cell = _cell_from_ring(ring)
    next_idx, push_now, clear_scrub = decide(cell, desired=42, pushed=5, scrub_pending=True)
    # The target frame is reported as next_idx (so the SPA could surface
    # "loading frame 42…") but push_now=False keeps the previous frame
    # visible. Scrub stays pending until the ring catches up.
    assert next_idx == 42
    assert push_now is False
    assert clear_scrub is False  # don't clear until landed
    assert 42 in ring.window_requests   # but we did request the window


def test_decide_empty_cell_holds():
    ring = _FakeRing(n_frames=0, ready=set())
    cell = _cell_from_ring(ring)
    next_idx, push_now, _ = decide(cell, desired=0, pushed=-1, scrub_pending=False)
    assert next_idx is None
    assert push_now is False


def test_decide_works_on_legacy_cells_too():
    """The decide function must also handle the model-cell shape (no
    ring; frames + quats arrays directly on the dict). Otherwise model
    previews break."""
    legacy_cell = {
        "version": 2,
        "frames": np.zeros((1, 4, 3), dtype=np.float32),
        "quats": np.tile([1.0, 0, 0, 0], (1, 4, 1)).astype(np.float32),
        "rgb": np.zeros((4, 3), dtype=np.float32),
        "opacity": np.zeros((4, 1), dtype=np.float32),
        "scales_sq": np.ones((4, 3), dtype=np.float32),
        "bbox_lo": np.zeros(3, dtype=np.float32),
        "bbox_hi": np.ones(3, dtype=np.float32),
    }
    # n_frames = 1; initial paint should find frame 0 ready.
    next_idx, push_now, _ = decide(legacy_cell, desired=0, pushed=-1, scrub_pending=False)
    assert next_idx == 0
    assert push_now is True


# ----- monotonic-advance property test ------------------------------------


def test_continuous_playback_is_strictly_monotonic():
    """Drive decide() through a simulated playback session and assert
    that the sequence of (pushed) frames is monotonic: N+1 follows N,
    never N+2 or N+k."""
    n_frames = 20
    # All frames ready (decoder is fast enough).
    ring = _FakeRing(n_frames=n_frames, ready=set(range(n_frames)))
    cell = _cell_from_ring(ring)

    pushed = -1
    desired = 0
    pushed_sequence = []
    # 50 ticks of "SPA advances desired by 1 each tick".
    for _ in range(60):
        next_idx, push_now, _ = decide(cell, desired, pushed, scrub_pending=False)
        if push_now and next_idx is not None:
            pushed_sequence.append(next_idx)
            pushed = next_idx
        if desired < n_frames - 1:
            desired += 1
    # The pushed sequence should be 0, 1, 2, 3, ...
    assert pushed_sequence[0] == 0
    for i in range(1, len(pushed_sequence)):
        delta = pushed_sequence[i] - pushed_sequence[i - 1]
        assert delta == 1, (
            f"frame {i}: jumped from {pushed_sequence[i-1]} to "
            f"{pushed_sequence[i]} (delta={delta}); strict-sequential broken"
        )


def test_continuous_playback_stutters_never_skips_under_slow_decode():
    """Same as above but the ring is missing some frames — decode is
    slow. The render loop should HOLD on the gap, never skip past it.

    Stutter accounting: every tick where push_now is False while desired
    has moved past pushed counts as one stutter; the test asserts
    pushed_sequence is still monotonic (never jumps the gap)."""
    n_frames = 20
    # Decoder only has 0..7 ready initially.
    ring = _FakeRing(n_frames=n_frames, ready={0, 1, 2, 3, 4, 5, 6, 7})
    cell = _cell_from_ring(ring)

    pushed = -1
    desired = 0
    pushed_sequence = []
    stutter_count = 0
    for tick in range(60):
        next_idx, push_now, _ = decide(cell, desired, pushed, scrub_pending=False)
        if push_now and next_idx is not None:
            pushed_sequence.append(next_idx)
            pushed = next_idx
        elif desired > pushed and pushed >= 0:
            # We wanted to advance but couldn't.
            stutter_count += 1
            # Simulate decoder catching up at tick 20.
            if tick >= 20:
                ring._ready.update(range(8, 14))
        if desired < n_frames - 1:
            desired += 1

    # Pushed never skipped a frame.
    for i in range(1, len(pushed_sequence)):
        assert pushed_sequence[i] - pushed_sequence[i - 1] == 1
    # Some stutter happened (decoder was slow).
    assert stutter_count > 0
    # Eventually pushed got past the gap once decode caught up.
    assert max(pushed_sequence) >= 10


# ----- integration with real SplatRing ----------------------------------


def test_real_ring_initial_paint_lands_frame_zero(tmp_path):
    """Build a real .gsq, instantiate a SplatRing, and confirm the first
    decide() call reports frame 0 ready (because load_cell_gsq calls
    decode_blocking(0) eagerly)."""
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=10)
    ring = SplatRing(gsq, window_size=4)
    try:
        ring.decode_blocking(0)
        cell = make_static_cell(ring, viser_k=1.0)
        next_idx, push_now, _ = decide(cell, desired=0, pushed=-1, scrub_pending=False)
        assert next_idx == 0
        assert push_now is True
    finally:
        ring.close()


def test_real_ring_scrub_from_zero_to_far_frame(tmp_path):
    """Scrub from frame 0 to frame 50 — should not walk 1..49. The
    decide() call returns the target frame directly once it's decoded."""
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=100)
    ring = SplatRing(gsq, window_size=8)
    try:
        ring.decode_blocking(0)
        cell = make_static_cell(ring, viser_k=1.0)

        # Initial paint at 0.
        next_idx, push_now, _ = decide(cell, desired=0, pushed=-1, scrub_pending=False)
        assert (next_idx, push_now) == (0, True)
        pushed = 0

        # User scrubs to frame 50. /set sets scrub_pending=True and
        # calls ring.request_window(50). The decide() function will
        # request a window and hold until frame 50 lands.
        ring.request_window(50)
        scrub_pending = True
        landed = False
        # Up to 2s for the ring to decode frame 50.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not landed:
            next_idx, push_now, clear_scrub = decide(
                cell, desired=50, pushed=pushed, scrub_pending=scrub_pending,
            )
            if push_now and next_idx == 50:
                landed = True
                pushed = 50
                if clear_scrub:
                    scrub_pending = False
                break
            time.sleep(0.01)
        assert landed, "scrub never landed within 2s"
        # The scrub took us from 0 to 50 directly — no intermediate
        # pushed frames were generated by decide() (it would only emit
        # them if we walked through continuous mode).
        assert pushed == 50
    finally:
        ring.close()


def test_real_ring_continuous_playback_through_window_boundary(tmp_path):
    """Decode a longer sequence through the window boundary to confirm
    the ring evicts old frames + decodes new ones without breaking the
    sequential push order."""
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=40)
    ring = SplatRing(gsq, window_size=4, prefetch_ahead=2)
    try:
        ring.decode_blocking(0)
        cell = make_static_cell(ring, viser_k=1.0)

        pushed = -1
        pushed_sequence = []
        stutter_count = 0
        # Walk 0 -> 20.
        for desired in range(0, 21):
            # Give the decoder a tiny grace period per tick.
            deadline = time.monotonic() + 0.5
            advanced = False
            while time.monotonic() < deadline:
                next_idx, push_now, _ = decide(cell, desired, pushed, scrub_pending=False)
                if push_now and next_idx is not None:
                    pushed_sequence.append(next_idx)
                    pushed = next_idx
                    ring.advance(pushed)
                    advanced = True
                    break
                elif next_idx is not None and not push_now:
                    # Stutter — wait for decode to catch up.
                    ring.request_frame(next_idx)
                    stutter_count += 1
                    time.sleep(0.005)
                else:
                    break
            assert advanced or desired == pushed, (
                f"desired={desired} failed to advance within 500ms"
                f" (pushed={pushed}, stutters={stutter_count})"
            )

        # The sequence must be strictly +1 deltas.
        assert pushed_sequence[0] == 0
        for i in range(1, len(pushed_sequence)):
            assert pushed_sequence[i] - pushed_sequence[i - 1] == 1
        # We progressed past the window boundary.
        assert max(pushed_sequence) >= 15
    finally:
        ring.close()


# ----- close releases resources ------------------------------------------


def test_real_ring_close_does_not_break_decide(tmp_path):
    """After close(), decide() should still return holds rather than crash."""
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=8)
    ring = SplatRing(gsq, window_size=3)
    ring.decode_blocking(0)
    cell = make_static_cell(ring, viser_k=1.0)
    ring.close()
    # has_frame may still return True for frame 0 (the ring was cleared)
    # — but get_frame after close returns None. decide() must hold.
    next_idx, push_now, _ = decide(cell, desired=5, pushed=0, scrub_pending=False)
    # next_idx is 1 (we want to advance), but push_now is False because
    # the ring was cleared in close(). Render loop holds.
    assert push_now is False
