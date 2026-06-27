from __future__ import annotations

import ast
import inspect
import subprocess
import textwrap

from pcc.py_frontend.codegen.binary_op_lowering import BinaryOpLoweringMixin
from pcc.py_frontend.codegen.host_contract import L1_CODEGEN_HOST_METHODS
from pcc.py_frontend.pipeline import compile_python


def _source() -> str:
    return textwrap.dedent(
        """
        def pick(flag: bool):
            if flag:
                return 4
            return "a"

        def main():
            x = pick(True)
            y = pick(True)
            print(x + y)
            print(y - x)
            print(x * y)
            s = pick(False)
            print(s + "b")

        main()
        """
    ).lstrip()


def test_dyn_tagged_int_binop_preserves_object_semantics(tmp_path):
    src = tmp_path / "dyn_tagged_binop.py"
    src.write_text(_source(), encoding="utf-8")
    exe = tmp_path / "dyn_tagged_binop.out"

    compile_python(
        str(src),
        str(exe),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["8", "0", "16", "ab"]


def test_dyn_tagged_int_binop_emits_fast_path(tmp_path):
    src = tmp_path / "dyn_tagged_binop.py"
    src.write_text(_source(), encoding="utf-8")
    ll = tmp_path / "dyn_tagged_binop.ll"

    compile_python(
        str(src),
        str(ll),
        backend="llvm",
        libpython_mode="off",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    ir_text = ll.read_text(encoding="utf-8")

    assert "int.tag.fast" in ir_text
    assert "call ptr @py_obj_add" in ir_text


def _string_constants(fn) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_dyn_tagged_int_binop_has_one_wrapper_owner():
    helper = BinaryOpLoweringMixin._emit_dyn_tagged_int_object_binop
    helper_source = inspect.getsource(helper)
    caller = BinaryOpLoweringMixin._emit_binop_value
    caller_source = inspect.getsource(caller)

    assert {"py_obj_add", "py_obj_sub", "py_obj_mul"} <= _string_constants(helper)
    assert not ({"py_obj_add", "py_obj_sub", "py_obj_mul"} & _string_constants(caller))
    assert caller_source.count("self._emit_dyn_tagged_int_object_binop(") == 3
    assert helper_source.count("marshal.marshal_to_object(") == 2
    assert "self._emit_inline_tagged_int_binop_or_call(" in helper_source
    assert "self._emit_post_call_err_check(None)" in helper_source
    assert "_emit_dyn_tagged_int_object_binop" in L1_CODEGEN_HOST_METHODS
