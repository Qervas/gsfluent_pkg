import pytest

from gsfluent._cells import CellRef
from gsfluent._paths import gsq_for, sequence_dir_for


def test_parse_wire_sequence_roundtrip():
    r = CellRef.parse_wire("sequence:demo_run")
    assert r.kind == "sequence"
    assert r.name == "demo_run"
    assert r.wire == "sequence:demo_run"


def test_parse_wire_model_roundtrip():
    r = CellRef.parse_wire("model:my-model.v2")
    assert r.kind == "model"
    assert r.name == "my-model.v2"
    assert r.wire == "model:my-model.v2"


def test_gsq_path_matches_paths_helper():
    r = CellRef(kind="sequence", name="abc")
    assert r.gsq_path == gsq_for("abc")
    assert r.library_dir == sequence_dir_for("abc")


def test_model_has_no_gsq_path():
    r = CellRef(kind="model", name="foo")
    with pytest.raises(ValueError):
        _ = r.gsq_path
    with pytest.raises(ValueError):
        _ = r.library_dir


@pytest.mark.parametrize("bad", [
    "no_colon_here",
    "bogus:foo",
    "sequence:",          # empty name fails the safe-name regex
    "sequence:../etc",    # path traversal
    "sequence:has space",
    "sequence:has/slash",
    ":nameonly",
])
def test_parse_wire_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        CellRef.parse_wire(bad)


def test_direct_construction_validates_name():
    with pytest.raises(ValueError):
        CellRef(kind="sequence", name="../escape")
    with pytest.raises(ValueError):
        CellRef(kind="not_a_kind", name="ok")  # type: ignore[arg-type]
