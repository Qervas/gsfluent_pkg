from pathlib import Path

import pytest

from gsfluent.api import runs


def test_require_registered_model_path_accepts_known_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    monkeypatch.setattr(
        runs.m, "list_models", lambda: [{"name": "model", "path": str(model_dir)}],
    )

    assert runs._require_registered_model_path(str(model_dir)) == model_dir.resolve()


def test_require_registered_model_path_rejects_unregistered_existing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    monkeypatch.setattr(runs.m, "list_models", lambda: [])

    with pytest.raises(ValueError, match="not registered"):
        runs._require_registered_model_path(str(model_dir))
