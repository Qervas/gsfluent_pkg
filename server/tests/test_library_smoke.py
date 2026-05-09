"""Smoke test that the migrated library has data the new endpoints
can read. Runs against the real `work/library/` (not isolated to
tmp_path) — the migration script must have run before this test passes.

Skipped automatically if the library hasn't been populated yet, so a
fresh checkout doesn't fail on a missing fixture.
"""
import pytest

from gsfluent.core.library import LIBRARY_ROOT, Model, Sequence


pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(),
    reason=(
        "library/ not populated — run `python tools/migrate_to_library.py` "
        "first if you want this smoke test to run"
    ),
)


def test_model_list_nonempty():
    names = Model.list()
    assert names, f"Model.list() should be non-empty after migration; got {names!r}"


def test_sequence_list_nonempty():
    names = Sequence.list()
    assert names, f"Sequence.list() should be non-empty after migration; got {names!r}"


def test_model_load_returns_meta():
    name = Model.list()[0]
    m = Model.load(name)
    assert m is not None
    assert m.meta is not None, f"model {name} has no _meta.json"
    assert m.meta.get("kind") == "model"
    d = m.meta_dict()
    # Frontend ModelItem contract: at minimum `name` and `path`.
    assert "name" in d
    assert "path" in d


def test_sequence_load_returns_meta_and_frames():
    name = Sequence.list()[0]
    s = Sequence.load(name)
    assert s is not None
    assert s.meta is not None, f"sequence {name} has no _meta.json"
    assert s.meta.get("kind") == "sequence"
    frames = s.frame_paths()
    assert frames, f"sequence {name} has no frames"
    # Frame names must sort numerically (frame_10 after frame_2).
    indices = [int(p.stem.split("_")[1]) for p in frames]
    assert indices == sorted(indices)
