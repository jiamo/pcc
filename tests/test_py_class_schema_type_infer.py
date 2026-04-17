from __future__ import annotations

import textwrap

from pcc.parse.py_lift import parse_and_lift
from pcc.py_frontend import type_infer
from pcc.py_frontend.export_meta import encode_type
from pcc.py_frontend.py_ast import (
    ClassDef,
    ClassType,
    DictType,
    FuncDef,
    ListType,
    Return,
    StrType,
    TupleType,
)


def _infer(src: str, *, external_exports=None):
    mod = parse_and_lift(textwrap.dedent(src).lstrip(), "schema.py", "schema")
    return type_infer.infer_module(mod, external_exports=external_exports)


def _func(mod, name: str) -> FuncDef:
    for stmt in mod.body:
        if isinstance(stmt, FuncDef) and stmt.name == name:
            return stmt
    raise AssertionError(f"function {name!r} not found")


def _method(mod, class_name: str, name: str) -> FuncDef:
    for stmt in mod.body:
        if isinstance(stmt, ClassDef) and stmt.name == class_name:
            for body_stmt in stmt.body:
                if isinstance(body_stmt, FuncDef) and body_stmt.name == name:
                    return body_stmt
    raise AssertionError(f"method {class_name}.{name!r} not found")


def _return_type(fn: FuncDef):
    for stmt in fn.body:
        if isinstance(stmt, Return) and stmt.value is not None:
            return stmt.value.ty
    raise AssertionError(f"function {fn.name!r} has no value return")


def test_local_class_schema_resolves_inherited_fields_and_upcasts():
    mod = _infer(
        """
        class Base:
            base: str

        class Child(Base):
            own: int

        def get_base(x: Child) -> str:
            return x.base

        def as_base(x: Child) -> Base:
            return x
        """
    )

    assert isinstance(_func(mod, "get_base").args[0].annotation, ClassType)
    assert isinstance(_return_type(_func(mod, "get_base")), StrType)
    assert isinstance(_func(mod, "as_base").return_ty, ClassType)


def test_init_assignment_adds_instance_field_type():
    mod = _infer(
        """
        class Node:
            def __init__(self, name: str):
                self.name = name

        def get_name(n: Node) -> str:
            return n.name
        """
    )

    assert isinstance(_return_type(_func(mod, "get_name")), StrType)


def test_method_self_uses_enclosing_class_schema():
    mod = _infer(
        """
        class Parent:
            label: str

        class Holder:
            def __init__(self, parent: Parent):
                self.parent = parent

            def get_parent(self) -> Parent:
                return self.parent
        """
    )

    assert isinstance(_return_type(_method(mod, "Holder", "get_parent")), ClassType)


def test_isinstance_and_condition_narrows_positive_branch():
    mod = _infer(
        """
        class Stmt:
            pass

        class FuncDef(Stmt):
            name: str

        def pick(stmt: Stmt) -> FuncDef:
            if isinstance(stmt, FuncDef) and stmt.name == "target":
                return stmt
            return FuncDef()
        """
    )

    assert isinstance(_return_type(_func(mod, "pick")), ClassType)


def test_imported_class_schema_uses_exported_bases_and_fields():
    stmt_ty = ClassType(name="Stmt", module="", fields=(), bases=())
    external_exports = {
        "ast_pkg": {
            "Stmt": {
                "kind": "class",
                "class_name": "Stmt",
                "base_names": (),
                "field_types": (("kind", encode_type(StrType(name="str"))),),
            },
            "FuncDef": {
                "kind": "class",
                "class_name": "FuncDef",
                "base_names": ("Stmt",),
                "field_types": (
                    (
                        "body",
                        encode_type(
                            TupleType(name="tuple_variadic", elems=(stmt_ty,))
                        ),
                    ),
                ),
            },
        },
    }
    mod = _infer(
        """
        from ast_pkg import FuncDef, Stmt

        def get_kind(fd: FuncDef) -> str:
            return fd.kind

        def as_stmt(fd: FuncDef) -> Stmt:
            return fd

        def get_body(fd: FuncDef) -> tuple[Stmt, ...]:
            return fd.body
        """,
        external_exports=external_exports,
    )

    assert isinstance(_return_type(_func(mod, "get_kind")), StrType)
    assert isinstance(_func(mod, "as_stmt").return_ty, ClassType)
    assert isinstance(_return_type(_func(mod, "get_body")), TupleType)


def test_bare_builtin_container_annotations_are_not_user_classes():
    mod = _infer(
        """
        def make_list() -> list:
            xs: list = []
            return xs

        def make_dict() -> dict:
            d: dict = {}
            return d
        """
    )

    assert isinstance(_func(mod, "make_list").return_ty, ListType)
    assert isinstance(_return_type(_func(mod, "make_list")), ListType)
    assert isinstance(_func(mod, "make_dict").return_ty, DictType)
    assert isinstance(_return_type(_func(mod, "make_dict")), DictType)
