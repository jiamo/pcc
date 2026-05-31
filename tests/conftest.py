"""Test-suite path shim for migrated test layout.

Tests were moved from ``tests/test_*.py`` to ``tests/{c,python}/test_*.py``.
Legacy code that computes paths via ``os.path.dirname(__file__)`` or
``Path(__file__).resolve().parents[1]`` would otherwise shift by one directory
level, so we normalize those ``*.py`` paths back under ``tests``.
"""

from __future__ import annotations

from pathlib import Path
import os


def _legacy_test_parent(path: Path) -> Path | None:
    if path.suffix != ".py":
        return None
    if path.parent.name == "normal":
        tests_root = path.parent.parent
        if tests_root is not None and tests_root.name == "tests":
            return tests_root
        return None
    if (
        path.parent.parent is not None
        and path.parent.name in {"c", "python"}
        and path.parent.parent.name == "tests"
    ):
        return path.parent.parent
    return None


_orig_dirname = os.path.dirname
_ORIG_RESOLVE = Path.resolve


def _patched_dirname(path):
    try:
        original = _orig_dirname(path)
    except TypeError:
        return ""
    try:
        p = Path(path)
    except TypeError:
        return original
    legacy_parent = _legacy_test_parent(p)
    if legacy_parent is not None:
        return str(legacy_parent)
    return original


def _patched_resolve(self: Path, *args, **kwargs):  # type: ignore[override]
    resolved = _ORIG_RESOLVE(self, *args, **kwargs)
    try:
        legacy_parent = _legacy_test_parent(resolved)
    except TypeError:
        return resolved
    if legacy_parent is not None:
        return legacy_parent / resolved.name
    return resolved


os.path.dirname = _patched_dirname
Path.resolve = _patched_resolve
