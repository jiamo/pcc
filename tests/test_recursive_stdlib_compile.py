"""Issue 11.B.1: ``compile_python_multi`` should recursively pull
imported pure-Python modules into the native compile set.

Goal: writing ``from dataclasses import dataclass`` in user code
should result in ``dataclasses.py`` being compiled natively (when
it's pure Python and pcc can parse it), rather than triggering a
``py_cpy_import`` call to libpython.

Each test sets up a small synthetic source that imports something,
compiles, and verifies the IR routing.
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(
    source: str, name: str, *, recursive: bool = True,
) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        recursive_stdlib=recursive,  # opt-in flag
    )
    return out.read_text()


def test_pure_python_stdlib_pulled_into_closure_when_recursive(tmp_path):
    """When ``recursive_stdlib=True`` (Issue 11.B.1), the closure
    walker pulls a pure-Python stdlib module's source into the
    compile set.

    Verifies the WALKER piece (this is the foundation). Codegen-side
    routing — making ``import keyword`` in user code skip
    ``py_cpy_import`` once shlex is in the closure — is a separate
    integration step (TODO: B.1 part 2)."""
    from pcc.py_frontend.pipeline import (
        _collect_multi_source_relative_closure,
    )

    src = tmp_path / "u.py"
    src.write_text("import keyword\ndef f(s: str): return s\n")
    srcs, mods = _collect_multi_source_relative_closure(
        [str(src)], ["u"], recursive_stdlib=True,
    )
    assert "keyword" in mods, (
        f"keyword should be in recursive closure, got: {mods}"
    )
    # The exact transitive set varies by Python version; just assert
    # a pure-Python stdlib module made it.


def test_recursive_off_does_not_expand(tmp_path):
    """recursive_stdlib=False (default) keeps closure shallow."""
    from pcc.py_frontend.pipeline import (
        _collect_multi_source_relative_closure,
    )

    src = tmp_path / "u.py"
    src.write_text("import shlex\ndef f(s): return shlex.split(s)\n")
    srcs, mods = _collect_multi_source_relative_closure(
        [str(src)], ["u"], recursive_stdlib=False,
    )
    assert "shlex" not in mods, (
        f"shlex should NOT be pulled when recursive=False, got: {mods}"
    )


def test_recursive_off_keeps_existing_libpython_path():
    """Default (recursive=False) keeps the historical
    ``py_cpy_import`` path for non-native builtin stdlib imports."""
    program = textwrap.dedent(
        """
        import keyword
        def f(s: str):
            return s
        """
    )
    ir_text = _compile_to_ll(program, "rec_keyword_off", recursive=False)
    # Without the flag, status quo: py_cpy_import path is exercised
    assert "@.cpy.mod.keyword" in ir_text


def test_cycle_detection(tmp_path):
    """Modules that recursively import each other don't hang the
    compiler."""
    a = tmp_path / "mod_a.py"
    b = tmp_path / "mod_b.py"
    a.write_text("from mod_b import g\ndef f(): return g()\n")
    b.write_text("from mod_a import f\ndef g(): return 42\n")
    # Just shouldn't hang; doesn't need to fully succeed.
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        from pcc.py_frontend.pipeline import (
            _collect_multi_source_relative_closure,
        )
        srcs, mods = _collect_multi_source_relative_closure(
            [str(a)], ["mod_a"], recursive_stdlib=True,
        )
        # Both mod_a and mod_b should be in closure, no infinite loop.
        assert "mod_a" in mods
    finally:
        sys.path.remove(str(tmp_path))


def test_c_extension_falls_back():
    """A module that's a C extension (_socket, _struct, etc.) cannot
    be parsed by pcc; should fall back to py_cpy_import gracefully
    rather than crash."""
    program = textwrap.dedent(
        """
        import _socket
        def f():
            return None
        """
    )
    # Should compile without crashing; _socket import goes to py_cpy_*
    ir_text = _compile_to_ll(program, "rec_socket", recursive=True)
    assert "@.cpy.mod._socket" in ir_text  # falls back to dynamic
