"""Phase-5 rename: GSFLUENT_CACHE_REBUILD supersedes GSFLUENT_NPZ_REBUILD."""
from __future__ import annotations

import importlib
import warnings


def _reload_runner():
    """Force re-evaluation of the module-level env-var read."""
    from gsfluent.core import runner
    return importlib.reload(runner)


def test_new_var_is_honored_when_set(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_CACHE_REBUILD", "0")
    monkeypatch.delenv("GSFLUENT_NPZ_REBUILD", raising=False)
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is False
    # Back-compat alias mirrors the canonical name.
    assert runner.NPZ_REBUILD_AFTER_RUN is False


def test_legacy_var_is_honored_when_new_var_unset(monkeypatch) -> None:
    monkeypatch.delenv("GSFLUENT_CACHE_REBUILD", raising=False)
    monkeypatch.setenv("GSFLUENT_NPZ_REBUILD", "0")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is False
    # One deprecation warning recorded.
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)
                    and "GSFLUENT_NPZ_REBUILD" in str(w.message)]
    assert len(deprecations) >= 1


def test_new_var_wins_over_legacy_when_both_set(monkeypatch) -> None:
    """If a deployment sets both during a transition, the new one wins."""
    monkeypatch.setenv("GSFLUENT_CACHE_REBUILD", "1")
    monkeypatch.setenv("GSFLUENT_NPZ_REBUILD", "0")
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is True


def test_default_when_neither_set(monkeypatch) -> None:
    monkeypatch.delenv("GSFLUENT_CACHE_REBUILD", raising=False)
    monkeypatch.delenv("GSFLUENT_NPZ_REBUILD", raising=False)
    runner = _reload_runner()
    assert runner.CACHE_REBUILD_AFTER_RUN is True
