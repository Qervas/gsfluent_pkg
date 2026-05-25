"""Tests for .gsq significance pruning."""
import numpy as np
import pytest

from gsfluent.core.codecs.gsq_prune import (
    compute_significance,
    select_keep_indices,
    retention_curve,
)


def test_significance_is_opacity_times_volume() -> None:
    # opacity normalized in [0,1], scales positive
    opacity = np.array([1.0, 0.5, 0.01], dtype=np.float32)
    scales = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    sig = compute_significance(opacity, scales)
    # equal scales → significance proportional to opacity
    assert sig[0] > sig[1] > sig[2]
    assert np.isclose(sig[0] / sig[1], 2.0, rtol=1e-5)


def test_significance_rewards_bigger_splats() -> None:
    opacity = np.array([1.0, 1.0], dtype=np.float32)
    scales = np.array([[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    sig = compute_significance(opacity, scales)
    # 2× scale per axis → 8× volume
    assert np.isclose(sig[0] / sig[1], 8.0, rtol=1e-5)


def test_select_keep_indices_keeps_top_k_by_significance() -> None:
    sig = np.array([0.1, 0.9, 0.5, 0.01, 0.7], dtype=np.float32)
    keep = select_keep_indices(sig, keep_count=3)
    # top 3 by significance are indices 1 (0.9), 4 (0.7), 2 (0.5)
    assert set(keep.tolist()) == {1, 2, 4}
    # keep is sorted ascending (so downstream slicing preserves original order)
    assert list(keep) == sorted(keep)


def test_retention_curve_reports_count_per_retention() -> None:
    # 4 splats; significance 0.97, 0.02, 0.005, 0.005 → total 1.0
    sig = np.array([0.97, 0.02, 0.005, 0.005], dtype=np.float32)
    curve = retention_curve(sig, retentions=(0.99, 0.97, 0.95))
    # to retain 0.97 of contribution, the single top splat (0.97) suffices → keep 1
    r97 = next(c for c in curve if c["retention"] == 0.97)
    assert r97["keep_count"] == 1
    assert np.isclose(r97["prune_ratio"], 0.75, rtol=1e-6)
    # to retain 0.99, need top splat + next (0.97+0.02=0.99) → keep 2
    r99 = next(c for c in curve if c["retention"] == 0.99)
    assert r99["keep_count"] == 2


def test_keep_count_clamped_to_n() -> None:
    sig = np.array([0.5, 0.5], dtype=np.float32)
    keep = select_keep_indices(sig, keep_count=10)
    assert len(keep) == 2
