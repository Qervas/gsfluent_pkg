"""Post-fuse stability classification for MPM runs."""
from __future__ import annotations

from dataclasses import dataclass

MIN_USABLE_FRAMES_DEFAULT = 24


@dataclass(frozen=True)
class StabilityVerdict:
    """Outcome of the post-fuse stability check."""

    outcome: str
    message: str | None = None
    usable_frames: int = 0
    requested_frames: int | None = None
    dropped_frames: int = 0

    @property
    def is_clean(self) -> bool:
        return self.outcome == "clean"

    @property
    def is_partial(self) -> bool:
        return self.outcome == "partial"

    @property
    def is_failed(self) -> bool:
        return self.outcome == "failed"


def check_sim_stability(
    *,
    n_sim: int,
    n_fused: int,
    allowed_nonfinite: int,
    expected_frames: int | None = None,
    min_usable_frames: int = MIN_USABLE_FRAMES_DEFAULT,
) -> StabilityVerdict:
    """Classify a finished sim/fuse pass as clean / partial / failed."""
    requested = (
        expected_frames
        if (expected_frames and expected_frames > 0)
        else (n_sim if n_sim > 0 else None)
    )
    if n_sim <= 0:
        return StabilityVerdict("clean", usable_frames=n_fused, requested_frames=requested)

    diverged_msg: str | None = None
    if expected_frames is not None and expected_frames > 0:
        missing = expected_frames - n_sim
        if missing > allowed_nonfinite:
            diverged_msg = (
                f"simulation diverged: only {n_sim} of {expected_frames} "
                f"requested frames were produced before the solver stopped "
                f"({missing} missing)"
            )
    if diverged_msg is None:
        dropped = n_sim - n_fused
        if dropped > allowed_nonfinite:
            diverged_msg = (
                f"simulation diverged: {dropped} of {n_sim} frames had "
                f"non-finite (NaN/Inf) positions and were dropped "
                f"({n_fused} usable)"
            )

    if diverged_msg is None:
        return StabilityVerdict(
            "clean", usable_frames=n_fused, requested_frames=requested,
        )

    dropped_total = max(0, (requested - n_fused) if requested else (n_sim - n_fused))
    if n_fused >= min_usable_frames:
        return StabilityVerdict(
            "partial",
            message=f"{diverged_msg}; kept {n_fused} usable frames as a partial result.",
            usable_frames=n_fused,
            requested_frames=requested,
            dropped_frames=dropped_total,
        )
    return StabilityVerdict(
        "failed",
        message=(
            f"{diverged_msg}; only {n_fused} usable frames "
            f"(< {min_usable_frames} minimum). The recipe is numerically unstable."
        ),
        usable_frames=n_fused,
        requested_frames=requested,
        dropped_frames=dropped_total,
    )
