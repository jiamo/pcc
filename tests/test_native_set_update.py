"""Native ``set.update`` dispatch for pcc-owned sets."""
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
        str(src),
        str(out),
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
def test_set_update_uses_native_spread(mode):
    program = textwrap.dedent(
        """
        def f(items: list[str]) -> object:
            seen = set()
            seen.update(items)
            return seen
        """
    )
    ir = _compile_to_ll(program, f"set_update_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_set_new" in body, body
    assert "@py_set_add" in body, body
    assert "cpy.fn.update" not in body, body
    assert "cpy.call1.update" not in body, body
