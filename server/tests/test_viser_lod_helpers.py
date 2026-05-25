import sys
from pathlib import Path
_VH = Path(__file__).resolve().parents[2] / "frontend" / "python"
sys.path.insert(0, str(_VH))
import viser_headless as vh  # noqa: E402

def test_base_url_from_full_swaps_trailing_name():
    full = "http://h:1/api/sequences/foo/cache/splats.gsq"
    assert vh._base_url_from_full(full) == "http://h:1/api/sequences/foo/cache/base.gsq"

def test_lod_decision_full_cached_short_circuits():
    assert vh._lod_decision(full_is_current=True, base_status=200) == "full-direct"

def test_lod_decision_two_tier_when_base_available():
    assert vh._lod_decision(full_is_current=False, base_status=200) == "two-tier"

def test_lod_decision_full_only_when_no_base():
    assert vh._lod_decision(full_is_current=False, base_status=404) == "full-only"
    assert vh._lod_decision(full_is_current=False, base_status=None) == "full-only"
