from __future__ import annotations

import subprocess


def test_set_bindings_infer_first_class_set_type() -> None:
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.py_ast import SetType
    from pcc.py_frontend.type_infer import infer_module

    module = infer_module(
        parse_and_lift(
            "literal = {1, 2}\nconstructed = set([3])\n",
            "set_type.py",
            "set_type",
        )
    )

    assert all(isinstance(stmt.value.ty, SetType) for stmt in module.body)


def test_set_annotations_preserve_mutability_and_element_shape() -> None:
    from pcc.parse.py_lift import parse_and_lift
    from pcc.py_frontend.py_ast import IntType, SetType, StrType, TupleType
    from pcc.py_frontend.type_infer import infer_module

    lifted = parse_and_lift(
        "mutable: set[int] = set([1])\n"
        "immutable: frozenset[tuple[int, str]] = frozenset({(1, 'x')})\n",
        "set_annotations.py",
        "set_annotations",
    )
    mutable, immutable = (stmt.annotation for stmt in lifted.body)

    assert isinstance(mutable, SetType)
    assert mutable.name == "set"
    assert isinstance(mutable.elem, IntType)
    assert isinstance(immutable, SetType)
    assert immutable.name == "frozenset"
    assert isinstance(immutable.elem, TupleType)
    assert isinstance(immutable.elem.elems[0], IntType)
    assert isinstance(immutable.elem.elems[1], StrType)

    inferred = infer_module(lifted)
    assert [stmt.targets[0].ty.name for stmt in inferred.body] == [
        "set",
        "frozenset",
    ]


def test_cross_module_set_export_keeps_native_operator_lowering(tmp_path) -> None:
    from pcc.py_frontend.pipeline import compile_python_multi

    entry = tmp_path / "entry.py"
    provider = tmp_path / "provider.py"
    executable = tmp_path / "set_export"
    entry.write_text(
        "from pkg.provider import base\n"
        "print(sorted(base | {'right'}))\n",
        encoding="utf-8",
    )
    provider.write_text("base = {'left'}\n", encoding="utf-8")

    compile_python_multi(
        [str(entry), str(provider)],
        str(executable),
        module_names=["pkg.entry", "pkg.provider"],
        entry_module="pkg.entry",
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout == "['left', 'right']\n"


def test_generator_expression_iterates_first_class_set_type(tmp_path) -> None:
    from pcc.py_frontend.pipeline import compile_python

    source = tmp_path / "set_generator.py"
    executable = tmp_path / "set_generator"
    source.write_text(
        "class Item:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
        "    def __hash__(self):\n"
        "        return self.value\n"
        "items = {Item(1), Item(2)}\n"
        "print(sum(item.value for item in items))\n",
        encoding="utf-8",
    )

    compile_python(
        str(source),
        str(executable),
        backend="self",
        libpython_mode="off",
        ir_scaffold_mode="on",
    )
    run = subprocess.run(
        [str(executable)],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout == "3\n"


def test_set_projection_has_no_syntax_side_table_workaround() -> None:
    from pathlib import Path

    source = (
        Path.cwd()
        / "pcc"
        / "py_frontend"
        / "type_infer.py"
    ).read_text(encoding="utf-8")

    assert "set_typed_names" not in source
    assert "_restore_degraded_set_operand" not in source
