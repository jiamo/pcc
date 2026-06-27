from __future__ import annotations

import textwrap
from pathlib import Path

from pcc.py_frontend.py_ast import FuncDef, FuncType, IntType, StrType


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    return out.read_text(encoding="utf-8")


def test_llvmlite_import_used_only_in_annotations_does_not_emit_cpython_import():
    program = textwrap.dedent(
        """
        from __future__ import annotations
        import llvmlite.binding as llvm

        def f(module: llvm.ModuleRef) -> int:
            return 1
        """
    )

    ir = _compile_to_ll(program, "annotation_only_llvmlite_import")

    assert "cpy.import.llvmlite_binding" not in ir
    assert "call " not in "\n".join(
        line for line in ir.splitlines() if "@py_cpy_" in line
    )


def test_llvmlite_runtime_use_still_emits_cpython_import():
    program = textwrap.dedent(
        """
        from __future__ import annotations
        import llvmlite.binding as llvm

        def f(text: str) -> object:
            return llvm.parse_assembly(text)
        """
    )

    ir = _compile_to_ll(program, "runtime_llvmlite_import")

    assert "cpy.import.llvmlite_binding" in ir
    assert "@py_cpy_import" in ir


def test_native_lift_preserves_callable_annotation_shape():
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.type_infer import infer_module

    program = textwrap.dedent(
        """
        from typing import Callable

        def apply(fn: Callable[[int], str], value: int) -> str:
            return fn(value)
        """
    )

    typed = infer_module(parse_and_lift(program, "callable_ann.py", "probe"))
    fd = next(
        stmt for stmt in typed.body
        if isinstance(stmt, FuncDef) and stmt.name == "apply"
    )
    ann = fd.args[0].annotation

    assert isinstance(ann, FuncType)
    assert ann.params == (IntType(name="int"),)
    assert ann.ret == StrType(name="str")
