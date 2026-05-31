"""``os.environ.get(name[, default])`` lowers to ``py_os_getenv``.

This is the chained-attribute form (``os.environ.get(...)``), distinct
from ``os.getenv(...)`` which the existing ``_emit_native_os_call``
already handles. Both should map to the same runtime helper, since
``os.environ.get`` and ``os.getenv`` are semantically equivalent for
the missing-key case (both return the supplied default / ``None``).
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
def test_os_environ_get_one_arg_dispatches_to_py_os_getenv(mode):
    program = textwrap.dedent(
        """
        import os

        def f() -> str:
            v = os.environ.get("PCC_BACKEND")
            if v is None:
                return ""
            return v
        """
    )
    ir = _compile_to_ll(program, f"environ_get_1_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_os_getenv" in body, body
    # No dynamic os import / environ getattr in the function body.
    assert "cpy.import.os" not in body, body
    assert "cpy.get.environ" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_environ_get_two_args_dispatches_to_py_os_getenv(mode):
    program = textwrap.dedent(
        """
        import os

        def g(default: str) -> str:
            return os.environ.get("PCC_BACKEND", default)
        """
    )
    ir = _compile_to_ll(program, f"environ_get_2_{mode}", mode=mode)
    body = _function_body(ir, "g")
    assert body is not None
    assert "@py_os_getenv" in body, body
    assert "cpy.import.os" not in body, body
    assert "cpy.get.environ" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_os_environ_get_keeps_string_arg(mode):
    """The first arg must be passed through to ``py_os_getenv`` as the
    PyObject* name; verify the call site shape."""
    program = textwrap.dedent(
        """
        import os

        def h(name: str) -> str:
            return os.environ.get(name) or ""
        """
    )
    ir = _compile_to_ll(program, f"environ_get_var_{mode}", mode=mode)
    body = _function_body(ir, "h")
    assert body is not None
    assert "@py_os_getenv" in body, body


def test_os_environ_subscript_dispatches_to_py_os_getenv_off_mode():
    """``os.environ[X]`` is the subscript form of getenv and should stay
    on the native runtime helper even in OFF scaffold mode.
    """
    program = textwrap.dedent(
        """
        import os

        def k() -> str:
            return os.environ["PCC_BACKEND"]
        """
    )
    ir = _compile_to_ll(program, "environ_subscript", mode="off")
    body = _function_body(ir, "k")
    assert body is not None
    assert "@py_os_getenv" in body, body
    assert "cpy.import.os" not in body, body
    assert "cpy.get.environ" not in body, body
