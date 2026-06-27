"""``print(*args, file=sys.stderr|sys.stdout)`` lowers natively to
``py_sys_X_write`` instead of falling back through CPython's print.

Restrictions: ``sep`` / ``end`` must be string literals when present
(non-literal forms still fall back). Other kwargs (e.g. ``flush``)
also fall back — verify the negative case so future relaxation is
deliberate.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text(encoding="utf-8")


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
def test_print_to_stderr_dispatches_natively(mode):
    program = textwrap.dedent(
        """
        import sys

        def f(msg: str) -> None:
            print(msg, file=sys.stderr)
        """
    )
    ir = _compile_to_ll(program, f"perr_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_sys_stderr_write" in body, body
    assert "cpy.builtin.print" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_print_to_stdout_dispatches_natively(mode):
    program = textwrap.dedent(
        """
        import sys

        def f(msg: str) -> None:
            print(msg, file=sys.stdout)
        """
    )
    ir = _compile_to_ll(program, f"pout_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_sys_stdout_write" in body, body
    assert "cpy.builtin.print" not in body, body


def test_print_with_flush_kwarg_still_falls_back():
    """``flush=True`` is not a kwarg we can lower through py_sys_X_write
    (no native flush primitive), so confirm the dispatch correctly
    declines and the cpy fallback fires."""
    program = textwrap.dedent(
        """
        import sys

        def f(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)
        """
    )
    ir = _compile_to_ll(program, "pflush", mode="off")
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_sys_stderr_write" not in body, body
    assert "cpy.builtin.print" in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_sys_stream_flush_dispatches_natively(mode):
    program = textwrap.dedent(
        """
        import sys
        from sys import stderr, stdout

        def f() -> None:
            sys.stdout.flush()
            stdout.flush()
            stderr.flush()
        """
    )
    ir = _compile_to_ll(program, f"sflush_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_cpy_" not in body, body
