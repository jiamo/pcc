"""Issue 11.B.1 part 2: codegen-side wiring for recursive stdlib.

When ``recursive_stdlib=True`` causes the closure walker to pull
e.g. ``keyword`` into the multi-file compile set, the codegen for an
``import keyword`` statement in user code must:
  - NOT emit ``py_cpy_import("keyword")`` (that pulls libpython)
  - register keyword as a native module alias so subsequent
    ``keyword.X`` accesses route to native ``user_keyword_X`` symbols
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, recursive: bool) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
        emit_llvm_only=True,
        recursive_stdlib=recursive,
    )
    return out.read_text()


def _count_py_cpy_import_for(ir_text: str, mod_name: str) -> int:
    """Count call sites that ``py_cpy_import(@.cpy.mod.<mod_name>)``."""
    # Match a call followed by a getelementptr pulling the module name
    # global. LLVM emits the GEP on the line just above the call.
    pattern = re.compile(
        r"%\.\w+\s*=\s*getelementptr[^\n]+@\.cpy\.mod\."
        + re.escape(mod_name)
        + r"\b[^\n]*\n[^\n]*=\s*call[^\n]+@py_cpy_import",
        re.MULTILINE,
    )
    return len(pattern.findall(ir_text))


def test_recursive_import_skips_py_cpy_import():
    """``import keyword`` with recursive_stdlib=True should NOT
    emit ``py_cpy_import("keyword")`` because keyword is now in the
    native compile closure."""
    program = textwrap.dedent(
        """
        import keyword
        def f(s: str):
            pass
        """
    )
    ir_text = _compile_to_ll(program, "rec_import_keyword_on", recursive=True)
    n = _count_py_cpy_import_for(ir_text, "keyword")
    assert n == 0, (
        f"recursive=True should produce ZERO py_cpy_import for keyword; "
        f"got {n} call sites"
    )


def test_off_mode_preserves_py_cpy_import():
    """recursive_stdlib=False (default) keeps the historical
    ``py_cpy_import`` path."""
    program = textwrap.dedent(
        """
        import keyword
        def f(s: str):
            pass
        """
    )
    ir_text = _compile_to_ll(program, "rec_import_keyword_off", recursive=False)
    # Without recursive_stdlib, status quo: py_cpy_import is emitted.
    assert "@py_cpy_import" in ir_text
