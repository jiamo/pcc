"""Issue 11.C.1: pcc-Python stdlib port registry.

When the recursive walker locates a stdlib module, it should
prefer ``pcc/stdlib/<name>.py`` over CPython's source. This lets
us provide pcc-Python ports of modules whose CPython source has
features pcc can't compile (e.g. ``struct`` with C-accelerated
internals, ``collections.OrderedDict`` with unsupported decorators).
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_STDLIB_DIR = _REPO_ROOT / "pcc" / "stdlib"


def test_pcc_stdlib_dir_exists():
    """The pcc/stdlib/ directory exists and has __init__.py so
    Python recognises it as a package."""
    assert _STDLIB_DIR.is_dir(), f"{_STDLIB_DIR} should be a directory"
    init_py = _STDLIB_DIR / "__init__.py"
    assert init_py.is_file(), f"{init_py} should exist"


def test_locator_prefers_pcc_stdlib_when_present(tmp_path):
    """When `pcc/stdlib/X.py` exists, the recursive-stdlib locator
    returns its path instead of CPython's `X.py`."""
    from pcc.py_frontend.pipeline import _locate_stdlib_module_source

    # Drop a probe port file
    probe_name = "_pcc_test_probe_module"
    probe = _STDLIB_DIR / f"{probe_name}.py"
    probe.write_text("# pcc-stdlib probe port\n")
    try:
        located = _locate_stdlib_module_source(probe_name)
        assert located is not None, "probe should resolve"
        assert str(_STDLIB_DIR) in located, (
            f"located via pcc/stdlib/, got: {located}"
        )
    finally:
        probe.unlink()


def test_locator_falls_back_to_cpython(tmp_path):
    """When `pcc/stdlib/X.py` is absent, falls back to CPython's
    source path."""
    from pcc.py_frontend.pipeline import _locate_stdlib_module_source

    located = _locate_stdlib_module_source("keyword")
    assert located is not None
    # keyword has no pcc/stdlib port, so should resolve to CPython's
    assert "stdlib/keyword.py" not in located.replace("\\", "/")


def test_pcc_stdlib_port_with_recursive_compile(tmp_path):
    """End-to-end: a port placed in pcc/stdlib/ gets pulled into
    the native closure when user code imports the module name."""
    from pcc.py_frontend.pipeline import (
        _collect_multi_source_relative_closure,
    )

    probe_name = "_pcc_test_port"
    probe = _STDLIB_DIR / f"{probe_name}.py"
    probe.write_text(
        "def hello() -> int:\n    return 42\n"
    )
    try:
        u = tmp_path / "u.py"
        u.write_text(
            f"import {probe_name}\n"
            f"def f() -> int:\n    return {probe_name}.hello()\n"
        )
        srcs, mods = _collect_multi_source_relative_closure(
            [str(u)], ["u"], recursive_stdlib=True,
        )
        assert probe_name in mods, (
            f"{probe_name} should be pulled from pcc/stdlib/, "
            f"got mods={mods}"
        )
        # Verify the source path used is the pcc/stdlib one
        idx = mods.index(probe_name)
        assert str(_STDLIB_DIR) in srcs[idx], (
            f"used wrong source for {probe_name}: {srcs[idx]}"
        )
    finally:
        probe.unlink()
