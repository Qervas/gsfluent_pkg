import pytest

from gsfluent._cells import CellRef
from gsfluent._paths import gsq_for, sequence_dir_for


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


def test_direct_construction_validates_name():
    with pytest.raises(ValueError):
        CellRef(kind="sequence", name="../escape")
    with pytest.raises(ValueError):
        CellRef(kind="not_a_kind", name="ok")  # type: ignore[arg-type]
