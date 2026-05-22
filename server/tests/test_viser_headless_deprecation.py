"""Phase-5 rename: --npz_dir is accepted as a deprecated alias for --cache-dir.

These tests verify the argparse wiring + the one-shot DeprecationWarning
emitted from viser_headless when the legacy flag is used.

We can't exercise full main() without `viser` + `uvicorn` installed in
the test venv (split-topology: those are client-host deps, not server
deps). Instead we replicate the argparse wiring and the
deprecation-resolution block exactly as it lives in main(), and assert
that the wiring matches our expectations. The single source of truth
remains the live argparse definition in viser_headless.main(); these
tests are documentation + regression detection.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

_HEADLESS_DIR = Path(__file__).resolve().parents[2] / "frontend" / "python"


def _read_main_source() -> str:
    """Slurp viser_headless.py as text so we can verify the argparse +
    deprecation snippets exist literally — cheaper than booting main()."""
    src = (_HEADLESS_DIR / "viser_headless.py").read_text()
    return src


def test_both_cli_flags_declared() -> None:
    """The CLI exposes both --cache-dir (canonical) and --npz_dir (alias)."""
    src = _read_main_source()
    assert '"--cache-dir"' in src
    assert '"--npz_dir"' in src
    # The alias is documented as deprecated in its help text.
    assert "[DEPRECATED] Use --cache-dir" in src


def test_deprecation_warning_block_present() -> None:
    """main() resolves --cache-dir vs --npz_dir and warns on the legacy flag."""
    src = _read_main_source()
    assert "args.cache_dir_legacy is not None" in src
    assert "DeprecationWarning" in src
    assert "viser_headless: --npz_dir is deprecated" in src


def test_no_bare_npz_root_references() -> None:
    """The old `npz_root` variable name is fully retired (one remaining
    deprecation-string mention in the warning text doesn't count)."""
    src = _read_main_source()
    # Anywhere `npz_root` appears as an identifier would be a regression.
    # The string `npz_dir` is allowed (in deprecation messages + CLI flag);
    # `npz_root` is NOT.
    assert "npz_root" not in src, (
        "npz_root must be fully renamed to cache_root in Phase 5; "
        "still found in viser_headless.py"
    )


def test_argparse_mutual_exclusion_matches_spec() -> None:
    """Spec: --cache-dir and --npz_dir are mutually exclusive; exactly
    one must be provided. We replicate the argparse block from main()
    and verify."""
    # Replicated from frontend/python/viser_headless.py:main()
    p = argparse.ArgumentParser()
    cache_group = p.add_mutually_exclusive_group(required=True)
    cache_group.add_argument(
        "--cache-dir", dest="cache_dir", default=None,
    )
    cache_group.add_argument(
        "--npz_dir", dest="cache_dir_legacy", default=None,
    )

    # Both flags should populate distinct attrs.
    ns = p.parse_args(["--cache-dir", "/tmp/foo"])
    assert ns.cache_dir == "/tmp/foo"
    assert ns.cache_dir_legacy is None

    ns = p.parse_args(["--npz_dir", "/tmp/bar"])
    assert ns.cache_dir is None
    assert ns.cache_dir_legacy == "/tmp/bar"

    # Both at once: argparse exits with code 2.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            p.parse_args(["--cache-dir", "/tmp/a", "--npz_dir", "/tmp/b"])
        except SystemExit as e:
            assert e.code == 2

    # Neither: argparse exits with code 2.
    try:
        p.parse_args([])
    except SystemExit as e:
        assert e.code == 2


def test_deprecation_warning_fires_when_legacy_used() -> None:
    """Replicate the deprecation-resolution block from main() in isolation
    and confirm DeprecationWarning is emitted for the legacy flag."""
    # Mirror viser_headless.main() Phase-5 block.
    class _Args:
        cache_dir = None
        cache_dir_legacy = "/tmp/legacy"

    args = _Args()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Begin replicated block ------------------------------------
        if args.cache_dir_legacy is not None:
            import warnings as _warnings
            _warnings.warn(
                "viser_headless: --npz_dir is deprecated; use --cache-dir. "
                "The old flag will be removed in the next release.",
                DeprecationWarning,
                stacklevel=2,
            )
            cache_root = Path(args.cache_dir_legacy)
        else:
            cache_root = Path(args.cache_dir)
        # End replicated block --------------------------------------

    deprecations = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "npz_dir" in str(w.message)
        and "cache-dir" in str(w.message)
    ]
    assert len(deprecations) == 1
    assert cache_root == Path("/tmp/legacy")


def test_deprecation_warning_silent_when_canonical_used() -> None:
    """No DeprecationWarning when --cache-dir is used directly."""
    class _Args:
        cache_dir = "/tmp/canonical"
        cache_dir_legacy = None

    args = _Args()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if args.cache_dir_legacy is not None:
            import warnings as _warnings
            _warnings.warn(
                "viser_headless: --npz_dir is deprecated; use --cache-dir. "
                "The old flag will be removed in the next release.",
                DeprecationWarning,
                stacklevel=2,
            )
            cache_root = Path(args.cache_dir_legacy)
        else:
            cache_root = Path(args.cache_dir)

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 0
    assert cache_root == Path("/tmp/canonical")
