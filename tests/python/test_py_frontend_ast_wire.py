from __future__ import annotations

import json
import textwrap


def test_pipeline_facade_reexports_ast_wire_codec() -> None:
    from pcc.py_frontend import pipeline, pipeline_ast_wire

    assert pipeline._py_ast_field_names is pipeline_ast_wire._py_ast_field_names
    assert pipeline._py_ast_to_wire is pipeline_ast_wire._py_ast_to_wire
    assert pipeline._py_ast_from_wire is pipeline_ast_wire._py_ast_from_wire
    assert pipeline._write_py_ast_wire is pipeline_ast_wire._write_py_ast_wire
    assert pipeline._read_py_ast_wire is pipeline_ast_wire._read_py_ast_wire


def test_ast_wire_field_names_use_the_stable_table_and_ignore_primitive_leaves() -> None:
    from pcc.py_frontend import pipeline_ast_wire, py_ast

    node = py_ast.Name(
        span=py_ast.SourceSpan("probe.py", 1, 0, 1, 1),
        ty=py_ast.IntType("int"),
        ident="value",
    )
    assert pipeline_ast_wire._py_ast_field_names(node) == (
        "span",
        "ty",
        "ident",
    )
    assert pipeline_ast_wire._py_ast_field_names("leaf") == ()


def test_py_ast_wire_roundtrip_preserves_full_lifted_ast() -> None:
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.pipeline import _py_ast_from_wire, _py_ast_to_wire

    source = textwrap.dedent(
        r'''
        import json

        class Box:
            label: str = "line1\nline2"

            def __init__(self, value: int = 7) -> None:
                self.value = value

            def render(self) -> str:
                quote = 'a"b'
                slash = "a\\b"
                raw = b"abc"
                return self.label + quote + slash + str(raw)

        def main() -> None:
            box = Box()
            print(json.dumps({"x": box.render()}))
        '''
    ).lstrip()
    ast_mod = parse_and_lift(source, "wire_probe.py", "wire_probe")
    wire_text = json.dumps(_py_ast_to_wire(ast_mod))
    recovered = _py_ast_from_wire(json.loads(wire_text))
    assert recovered == ast_mod


def test_parallel_self_codegen_enables_ast_wire_sidecars(tmp_path, monkeypatch) -> None:
    from pcc.py_frontend.pipeline import compile_python_multi

    entry = tmp_path / "entry.py"
    lib = tmp_path / "lib.py"
    out_ll = tmp_path / "out.ll"
    entry.write_text(
        textwrap.dedent(
            """
            def main() -> None:
                print(1)

            if __name__ == "__main__":
                main()
            """
        ).lstrip()
    , encoding="utf-8")
    lib.write_text(
        textwrap.dedent(
            """
            def helper() -> int:
                return 42
            """
        ).lstrip()
    , encoding="utf-8")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "2")
    monkeypatch.setenv("PCC_PY_FRONTEND_AST_WIRE", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    profile = {}
    compile_python_multi(
        [str(entry), str(lib)],
        str(out_ll),
        module_names=["entry", "lib"],
        entry_module="entry",
        backend="self",
        libpython_mode="off",
        emit_llvm_only=True,
        profile=profile,
    )
    assert out_ll.exists()
    counters = profile.get("counters", {})
    assert counters.get("multi_frontend_ast_wire_enabled") == 1
    assert counters.get("multi_frontend_chunks") == 2


def test_py_ast_wire_is_opt_in_by_default(monkeypatch) -> None:
    from pcc.py_frontend.pipeline import _python_frontend_ast_wire_enabled

    monkeypatch.delenv("PCC_PY_FRONTEND_AST_WIRE", raising=False)
    assert _python_frontend_ast_wire_enabled() is False

    monkeypatch.setenv("PCC_PY_FRONTEND_AST_WIRE", "1")
    assert _python_frontend_ast_wire_enabled() is True


def test_py_ast_wire_normalizes_legacy_classtype_null_fields() -> None:
    from pcc.py_frontend.pipeline import _py_ast_from_wire, _py_ast_to_wire
    from pcc.py_frontend.py_ast import ClassType

    cls = ClassType("PointerType", "", (), (), None, None)
    recovered = _py_ast_from_wire(_py_ast_to_wire(cls))
    assert recovered.fields == ()
    assert recovered.bases == ()
    assert recovered.properties == ()
    assert recovered.valueclass is False


def test_py_ast_wire_roundtrips_first_class_set_type() -> None:
    from pcc.py_frontend.pipeline import _py_ast_from_wire, _py_ast_to_wire
    from pcc.py_frontend.py_ast import DynType, SetType

    set_ty = SetType(name="set", elem=DynType(name="dyn"))

    assert _py_ast_from_wire(_py_ast_to_wire(set_ty)) == set_ty


def test_stdlib_ast_lifter_preserves_interleaved_call_operand_order() -> None:
    from pcc.py_frontend.parser import parse
    from pcc.py_frontend.py_ast import Call, ExprStmt

    module = parse(
        "target(named=value, *items, **mapping)\n",
        "operand_order_probe.py",
    )
    stmt = module.body[0]
    assert isinstance(stmt, ExprStmt)
    assert isinstance(stmt.expr, Call)
    assert stmt.expr.operand_order == (
        ("kw", 0),
        ("arg", 0),
        ("kw", 1),
    )
