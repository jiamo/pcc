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


_REPO_ROOT = Path(__file__).absolute().parents[2]
_STDLIB_DIR = _REPO_ROOT / "pcc" / "stdlib"
_PY_STDLIB_DIR = _REPO_ROOT / "pcc" / "py_stdlib"


def test_pcc_stdlib_dir_exists():
    """The pcc/stdlib/ directory exists and has __init__.py so
    Python recognises it as a package."""
    assert _STDLIB_DIR.is_dir(), f"{_STDLIB_DIR} should be a directory"
    init_py = _STDLIB_DIR / "__init__.py"
    assert init_py.is_file(), f"{init_py} should exist"


def test_pcc_py_stdlib_dir_exists():
    """The primary native stdlib tree is pcc/py_stdlib."""
    assert _PY_STDLIB_DIR.is_dir(), f"{_PY_STDLIB_DIR} should be a directory"
    init_py = _PY_STDLIB_DIR / "__init__.py"
    assert init_py.is_file(), f"{init_py} should exist"


def test_locator_prefers_pcc_py_stdlib_when_present():
    """Normal CPython spelling should resolve to pcc/py_stdlib ports
    before probing the host CPython installation."""
    from pcc.py_frontend.pipeline import _locate_stdlib_module_source

    located = _locate_stdlib_module_source("string")
    assert located is not None
    assert str(_PY_STDLIB_DIR) in located, (
        f"string should resolve via pcc/py_stdlib/, got: {located}"
    )


def test_locator_supports_dotted_pcc_py_stdlib_packages():
    """Dotted CPython stdlib names should map to package paths under
    pcc/py_stdlib, not only flat ``name.py`` files."""
    from pcc.py_frontend.pipeline import _locate_stdlib_module_source

    located = _locate_stdlib_module_source("urllib.parse")
    assert located is not None
    assert located.endswith("pcc/py_stdlib/urllib/parse.py"), located


def test_import_classifier_keeps_cpython_spelling_with_native_provider():
    from pcc.py_frontend.pipeline import _classify_python_import

    assert _classify_python_import("typing") == "compile_time_only"
    assert _classify_python_import(
        "pkg.sibling", native_modules={"pkg.sibling"},
    ) == "native_user_module"
    assert _classify_python_import("os.path") == "builtin_native_dispatch"
    assert _classify_python_import("urllib.parse") == "native_stdlib"
    assert _classify_python_import("keyword") == "cpython_fallback"


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
    the native closure when user code imports the CPython module name.

    The public spelling remains ``import X``; ``pcc/stdlib/X.py`` is
    the implementation location, not a user-facing ``std.X`` namespace.
    """
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
        assert f"std.{probe_name}" not in mods
        assert f"pcc.stdlib.{probe_name}" not in mods
        # Verify the source path used is the pcc/stdlib one
        idx = mods.index(probe_name)
        assert str(_STDLIB_DIR) in srcs[idx], (
            f"used wrong source for {probe_name}: {srcs[idx]}"
        )
    finally:
        probe.unlink()
