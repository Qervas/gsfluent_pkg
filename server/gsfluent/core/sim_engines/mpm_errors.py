"""Stderr classification helpers for the MPM simulation engine."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from gsfluent.protocols.sim import (
    SimCrashedError,
    SimGpuOomError,
    SimUnstableRecipeError,
)


@dataclass(frozen=True)
class MPMErrorPattern:
    """One stderr-pattern -> error_kind mapping."""

    error_kind: str
    regex_source: str
    case_insensitive: bool
    description: str
    compiled: re.Pattern[str]


def _default_patterns_path() -> Path:
    return Path(__file__).parent / "mpm_error_patterns.yaml"


def load_error_patterns(path: Path | None = None) -> list[MPMErrorPattern]:
    """Load the operator-tunable stderr pattern file."""
    p = path if path is not None else _default_patterns_path()
    try:
        raw = yaml.safe_load(p.read_text())
    except Exception as e:
        raise RuntimeError(f"Failed to load error patterns from {p}: {e}") from e

    out: list[MPMErrorPattern] = []
    for entry in raw.get("patterns", []):
        flags = re.IGNORECASE if entry.get("case_insensitive", False) else 0
        out.append(
            MPMErrorPattern(
                error_kind=entry["error_kind"],
                regex_source=entry["regex"],
                case_insensitive=entry.get("case_insensitive", False),
                description=entry.get("description", ""),
                compiled=re.compile(entry["regex"], flags),
            )
        )
    return out


def classify_stderr(stderr: str, patterns: list[MPMErrorPattern]) -> str | None:
    """Return the first matching error_kind, or None if no pattern matches."""
    if not stderr:
        return None
    for pat in patterns:
        if pat.compiled.search(stderr) is not None:
            return pat.error_kind
    return None


def kind_to_exception(kind: str, message: str) -> Exception:
    """Map a classifier kind string to its exception class."""
    if kind == "sim.gpu_oom":
        return SimGpuOomError(message)
    if kind == "sim.unstable_recipe":
        return SimUnstableRecipeError(message)
    return SimCrashedError(message)
