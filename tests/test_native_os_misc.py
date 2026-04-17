"""Native dispatch for low-volume `os.X` (getcwd, access) + `os.{F,R,W,X}_OK`
constants.

These each have one or two callsites in the bootstrap closure, but the
chained-call savings (no ``import os`` cpy import per call site, no
``cpy.get.<name>``, no ``cpy.call*``) add up to ~10 cpy_* per use. The
dispatch lives in ``_emit_native_os_call`` (for the methods) and
``_emit_attr`` (for the access(2) mode constants).
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text()


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


@pytest.mark.parametrize("mode", ["off", "on"])
def test_getcwd_dispatches_to_native(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            return os.getcwd()
        """
    )
    ir = _compile_to_ll(program, f"getcwd_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_getcwd_str" in body, body
    assert "cpy.get.getcwd" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_access_dispatches_to_native(mode):
    program = textwrap.dedent(
        """
        import os

        def f(p: str) -> bool:
            return os.access(p, os.X_OK)
        """
    )
    ir = _compile_to_ll(program, f"access_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_access" in body, body
    assert "cpy.get.access" not in body, body
    assert "cpy.get.X_OK" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_getcwd_result_stays_native_inside_os_path_join(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            return str(os.path.join(os.getcwd(), "pcc", "py_runtime"))
        """
    )
    ir = _compile_to_ll(program, f"getcwd_join_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_getcwd_str" in body, body
    assert "@py_os_path_join" in body, body
    assert "cpy.fn.join" not in body, body
    assert "cpy.import.os" not in body, body


@pytest.mark.parametrize("const", ["F_OK", "R_OK", "W_OK", "X_OK"])
@pytest.mark.parametrize("mode", ["off", "on"])
def test_access_mode_constants_dispatch(mode, const):
    """Each access(2) mode constant must go through py_int_from_i64
    (literal) instead of py_cpy_getattr — the value is fixed by POSIX
    so it's always known at compile time."""
    program = textwrap.dedent(
        f"""
        import os

        def f() -> int:
            return os.{const}
        """
    )
    ir = _compile_to_ll(program, f"const_{const}_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert f"cpy.get.{const}" not in body, body
    assert "@py_int_from_i64" in body, body
