from __future__ import annotations

from .py_ast import (
    BoolType,
    ClassType,
    DictType,
    DynType,
    FloatType,
    FuncType,
    IntType,
    ListType,
    NoneType,
    StrType,
    TupleType,
    Type,
)


def encode_type(ty: Type | None):
    if ty is None:
        return None
    if isinstance(ty, IntType):
        return ("int", ty.width, ty.signed)
    if isinstance(ty, FloatType):
        return ("float", ty.width)
    if isinstance(ty, BoolType):
        return ("bool",)
    if isinstance(ty, NoneType):
        return ("none",)
    if isinstance(ty, StrType):
        return ("str",)
    if isinstance(ty, ListType):
        return ("list", encode_type(ty.elem))
    if isinstance(ty, DictType):
        return ("dict", encode_type(ty.key), encode_type(ty.value))
    if isinstance(ty, TupleType):
        return ("tuple", tuple(encode_type(t) for t in ty.elems))
    if isinstance(ty, FuncType):
        return (
            "func",
            tuple(encode_type(t) for t in ty.params),
            encode_type(ty.ret),
        )
    if isinstance(ty, ClassType):
        return (
            "class",
            ty.name,
            ty.module,
            tuple((name, encode_type(field_ty)) for name, field_ty in ty.fields),
            tuple(encode_type(base) for base in ty.bases),
        )
    if isinstance(ty, DynType):
        return ("dyn",)
    # Export metadata is an ABI hint for cross-module declarations.
    # Complex annotations that the lightweight exporter cannot encode
    # should degrade to dyn instead of blocking bootstrap.
    return ("dyn",)


def decode_type(desc):
    if desc is None or isinstance(desc, Type):
        return desc
    if not isinstance(desc, tuple) or len(desc) == 0:
        raise TypeError(f"invalid export type descriptor: {desc!r}")
    tag = desc[0]
    if tag == "int":
        return IntType(name="int", width=desc[1], signed=desc[2])
    if tag == "float":
        return FloatType(name="float", width=desc[1])
    if tag == "bool":
        return BoolType(name="bool")
    if tag == "none":
        return NoneType(name="None")
    if tag == "str":
        return StrType(name="str")
    if tag == "list":
        return ListType(name="list", elem=decode_type(desc[1]))
    if tag == "dict":
        return DictType(
            name="dict",
            key=decode_type(desc[1]),
            value=decode_type(desc[2]),
        )
    if tag == "tuple":
        return TupleType(
            name="tuple",
            elems=tuple(decode_type(t) for t in desc[1]),
        )
    if tag == "func":
        return FuncType(
            name="callable",
            params=tuple(decode_type(t) for t in desc[1]),
            ret=decode_type(desc[2]),
        )
    if tag == "class":
        return ClassType(
            name=desc[1],
            module=desc[2],
            fields=tuple((name, decode_type(field_ty)) for name, field_ty in desc[3]),
            bases=tuple(decode_type(base) for base in desc[4]),
        )
    if tag == "dyn":
        return DynType(name="dyn")
    raise TypeError(f"unknown export type descriptor tag: {tag!r}")
