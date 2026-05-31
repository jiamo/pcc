from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[2]


def test_classgen_typed_null_bool_literal_arg_uses_bool_fallback():
    from pcc.llvm_capi.compat import ir
    from pcc.py_frontend.codegen import class_gen
    from pcc.py_frontend.py_ast import BoolLit, BoolType

    class Parent:
        def _emit_expr(self, _expr):
            return ir.Value(ir.IntType(1), "null")

    expr = BoolLit(span=None, ty=BoolType(name="bool"), value=False)

    value = class_gen._classgen_emit_arg_expr(
        Parent(),
        expr,
        BoolType(name="bool"),
    )

    assert str(value.type) == "i1"
    assert class_gen._classgen_value_ref_text(value) == "false"


def test_classgen_bool_literal_ref_text_uses_i1_constant_text():
    from pcc.llvm_capi.compat import ir
    from pcc.py_frontend.codegen import class_gen
    from pcc.py_frontend.py_ast import BoolLit, BoolType

    expr = BoolLit(span=None, ty=BoolType(name="bool"), value=False)

    assert class_gen._classgen_bool_literal_ref_text(expr, ir.IntType(1)) == "false"


def test_classgen_typed_null_i1_ref_text_is_false():
    from pcc.llvm_capi.compat import ir
    from pcc.py_frontend.codegen import class_gen

    value = ir.Value(ir.IntType(1), "null")

    assert class_gen._classgen_value_ref_text(value) == "false"


def test_dataclass_constructor_attr_args_do_not_lower_to_null(tmp_path, monkeypatch):
    from pcc.py_frontend.pipeline import compile_python

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    out = tmp_path / "py_parse.ll"

    compile_python(
        str(REPO_ROOT / "pcc" / "parse" / "py_parse.py"),
        str(out),
        emit_llvm_only=True,
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )

    text = out.read_text(encoding="utf-8")
    bad_name_ctor = re.compile(
        r"@user_pcc_parse_py_parse__Name___init__"
        r"\(ptr [^,]+, ptr null, i64 null\)"
    )
    assert bad_name_ctor.search(text) is None
