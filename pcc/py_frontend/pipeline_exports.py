"""Closed-world native-export types, defaults, and wire format.

The compilation driver consumes this module through compatibility aliases.  It
contains no compile orchestration or backend selection, only deterministic
export metadata and its bootstrap-safe serialization contract.
"""
from __future__ import annotations

import json
import os

from . import pipeline_ast_wire as _pipeline_ast_wire
from . import pipeline_ir_text as _pipeline_ir_text
from .export_meta import encode_type
from .pipeline_modes import PyPipelineError


_py_ast_field_value = _pipeline_ast_wire._py_ast_field_value
_find_substring = _pipeline_ir_text.find_substring


def _export_param_types(args):
    """Return normalized runtime param types for cross-module exports.

    Multi-file extern declarations only need the lowered runtime
    signature shape. Treat missing annotations as DynType and skip the
    bare ``*`` separator, matching codegen's own parameter handling.
    """
    param_tys = []
    for a in args:
        name = _py_ast_field_value(a, "name", "")
        if not isinstance(name, str) or name == "":
            continue
        ann = _export_annotation_or_none(a)
        param_tys.append(encode_type(ann) if ann is not None else ("dyn",))
    return param_tys


def _export_return_type(ret_ty):
    if ret_ty is None:
        return ("dyn",)
    return encode_type(ret_ty)


def _export_returns_none(ret_ty) -> bool:
    """True when the definition lowers this return to ``ret void``.

    Only explicit ``-> None`` uses the void ABI. The result is carried as a
    plain bool in the export schema because type descriptors can round-trip
    through encode_type/isinstance as ("dyn",) under the self-hosted compiler.
    A missing annotation uses the dynamic object ABI in both the definition
    and every cross-module declaration.
    """
    if ret_ty is None:
        # Unannotated: the DEFINITION lowers the post-inference type, while
        # exports see the raw AST — declaring these void made every
        # unannotated cross-module method call return py_None (56-worker
        # FuncDef-resolution collapse, bisected 2026-08-01). Export dyn and
        # let only explicit -> None annotations go void.
        return False
    from .py_ast import NoneType as _NoneType

    return _closed_world_is_node(ret_ty, _NoneType)


def _export_typed_int_unboxed_abi_mode() -> str:
    mode = os.environ.get("PCC_PYTHON_TYPED_INT_ABI", "auto").strip().lower()
    if mode == "0":
        return "off"
    if mode == "off":
        return "off"
    if mode == "false":
        return "off"
    if mode == "boxed":
        return "off"
    if mode == "unsafe-i64":
        return "unsafe-i64"
    if mode == "unsafe_i64":
        return "unsafe-i64"
    if mode == "raw-i64":
        return "unsafe-i64"
    if mode == "raw_i64":
        return "unsafe-i64"
    if mode == "i64":
        return "unsafe-i64"
    return "auto"


def _export_typed_int_unboxed_abi_enabled() -> bool:
    return _export_typed_int_unboxed_abi_mode() != "off"


def _export_int_literal_fits_i64(expr) -> bool:
    value = int(_py_ast_field_value(expr, "value", 0))
    return -(1 << 63) <= value <= (1 << 63) - 1


def _export_literal_value_or_none(expr):
    return _py_ast_field_value(expr, "value", None)


def _closed_world_node_kind(node) -> str:
    try:
        return type(node).__name__
    except AttributeError:
        return ""


def _closed_world_expected_kind(expected_type) -> str:
    try:
        name = expected_type.__name__
        if isinstance(name, str) and name:
            return name
    except Exception:
        pass
    try:
        text = str(expected_type)
    except Exception:
        return ""
    dot = text.rfind(".")
    end = text.rfind("'")
    if dot >= 0 and end > dot:
        return text[dot + 1 : end]
    return text


def _closed_world_is_node(node, expected_types) -> bool:
    if node is None:
        return False
    if isinstance(expected_types, tuple):
        for expected_type in expected_types:
            if _closed_world_is_node(node, expected_type):
                return True
        return False
    if isinstance(node, expected_types):
        return True
    expected_kind = _closed_world_expected_kind(expected_types)
    return expected_kind != "" and _closed_world_node_kind(node) == expected_kind


def _export_default_is_native_typed_int_shape(expr) -> bool:
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import IntLit as _IntLit

    if _closed_world_is_node(expr, _IntLit):
        return _export_int_literal_fits_i64(expr)
    if _closed_world_is_node(expr, _BoolLit):
        return True
    return False


def _export_func_uses_unboxed_typed_int_abi(fd) -> bool:
    """Small export-table mirror of the typed-int ABI signature gate.

    This intentionally stays local to ``pipeline.py``: importing
    ``layer1`` here pulls the whole codegen package into the compiled
    pcc_multi+pipeline closure and reintroduces no-libpython fallback.
    """
    from .py_ast import BoolType as _BoolType
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntType as _IntType

    mode = _export_typed_int_unboxed_abi_mode()
    if mode == "off":
        return False
    if (
        _py_ast_field_value(fd, "is_async", False)
        or _py_ast_field_value(fd, "is_method", False)
        or len(_py_ast_field_value(fd, "decorators", ())) != 0
    ):
        return False
    if mode == "unsafe-i64":
        if not _closed_world_is_node(
            _export_return_ty_or_none(fd),
            (_IntType, _FloatType),
        ):
            return False
    else:
        if not _closed_world_is_node(_export_return_ty_or_none(fd), _FloatType):
            return False
    for arg in _py_ast_field_value(fd, "args", ()):
        arg_name = _py_ast_field_value(arg, "name", "")
        if arg_name == "":
            continue
        if _py_ast_field_value(arg, "kind", "pos") not in (
            "pos",
            "pos_only",
            "kw_only",
        ):
            return False
        if mode == "unsafe-i64":
            if not _closed_world_is_node(
                _export_annotation_or_none(arg),
                (_IntType, _BoolType, _FloatType),
            ):
                return False
        else:
            if not _closed_world_is_node(_export_annotation_or_none(arg), _FloatType):
                return False
        arg_default = _py_ast_field_value(arg, "default", None)
        if arg_default is not None and not _export_default_is_native_typed_int_shape(
            arg_default
        ):
            return False
    return True


def _export_static_literal_type(expr):
    """Return a shallow static type for top-level literal containers.

    This feeds the multi-file export table for module globals such as
    ``VALUES = {"a": 1}``.  The defining module still owns and initializes
    the real object; importers only need the storage type so name lookup can
    load the native extern module-global slot instead of falling back to
    CPython import.
    """
    from .py_ast import BinOp as _BinOp
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BoolType as _BoolType
    from .py_ast import Call as _Call
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntLit as _IntLit
    from .py_ast import IntType as _IntType
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import ListType as _ListType
    from .py_ast import SetType as _SetType
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import NoneType as _NoneType
    from .py_ast import StrLit as _StrLit
    from .py_ast import StrType as _StrType
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import TupleType as _TupleType

    if _closed_world_is_node(expr, _StrLit):
        return _StrType("str")
    if _closed_world_is_node(expr, _IntLit):
        return _IntType("int")
    if _closed_world_is_node(expr, _BoolLit):
        return _BoolType("bool")
    if _closed_world_is_node(expr, _FloatLit):
        return _FloatType("float")
    if _closed_world_is_node(expr, _NoneLit):
        return _NoneType("None")
    if _closed_world_is_node(expr, _BinOp):
        lhs_ty = _export_static_literal_type(
            _py_ast_field_value(expr, "lhs", None)
        )
        rhs_ty = _export_static_literal_type(
            _py_ast_field_value(expr, "rhs", None)
        )
        if (
            _closed_world_is_node(lhs_ty, (_IntType, _BoolType))
            and _closed_world_is_node(rhs_ty, (_IntType, _BoolType))
            and _py_ast_field_value(expr, "op", "")
            in ("+", "-", "*", "//", "%", "&", "|", "^", "<<", ">>")
        ):
            return _IntType("int")
    if _closed_world_is_node(expr, _Name):
        return _DynType("dyn")
    if _closed_world_is_node(expr, _Attr):
        # ``X = other.attr`` — pcc does not statically know the precise
        # type but the export is real (the defining module's init code
        # populates it).  Register as DynType so downstream
        # ``mod.attr`` access on this name resolves via the
        # ``.modvar.<mod>.<name>`` extern instead of falling back to
        # ``py_obj_getattr`` on the module-name string.  Surfaced by
        # numpy/matrixlib/__init__.py:7 ``__all__ = defmatrix.__all__``
        # which made ``_mat.__all__`` in numpy/__init__.py:681 fail with
        # AttributeError.  See investigation
        # ``docs/investigations/python-native-module-alias-module-global-attr-attribute-error.md``.
        return _DynType("dyn")
    if _closed_world_is_node(expr, _Call):
        func = _py_ast_field_value(expr, "func", None)
        if _closed_world_is_node(func, _Name):
            func_name = _py_ast_field_value(func, "ident", "")
            if func_name in ("set", "frozenset", "_set_comp", "__setcomp__"):
                set_name = "frozenset" if func_name == "frozenset" else "set"
                return _SetType(name=set_name, elem=_DynType("dyn"))
    if _closed_world_is_node(expr, _TupleExpr):
        elems = []
        for item in _py_ast_field_value(expr, "elems", ()):
            item_ty = _export_static_literal_type(item)
            elems.append(item_ty if item_ty is not None else _DynType("dyn"))
        return _TupleType(name="tuple", elems=tuple(elems))
    if _closed_world_is_node(expr, _ListExpr):
        elem_ty = _export_common_static_type(
            tuple(
                _export_static_literal_type(item)
                for item in _py_ast_field_value(expr, "elems", ())
            )
        )
        return _ListType(name="list", elem=elem_ty)
    if _closed_world_is_node(expr, _DictExpr):
        key_types = []
        value_types = []
        for key, value in _py_ast_field_value(expr, "pairs", ()):
            key_types.append(_export_static_literal_type(key))
            value_types.append(_export_static_literal_type(value))
        return _DictType(
            name="dict",
            key=_export_common_static_type(tuple(key_types)),
            value=_export_common_static_type(tuple(value_types)),
        )
    return None


def _export_static_all_names(expr):
    from .py_ast import BinOp as _BinOp
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import StrLit as _StrLit
    from .py_ast import TupleExpr as _TupleExpr

    if _closed_world_is_node(expr, (_ListExpr, _TupleExpr)):
        names = []
        for item in _py_ast_field_value(expr, "elems", ()):
            if not _closed_world_is_node(item, _StrLit):
                return None
            value = _export_literal_value_or_none(item)
            if not isinstance(value, str):
                return None
            names.append(value)
        return tuple(names)
    if (
        _closed_world_is_node(expr, _BinOp)
        and _py_ast_field_value(
            expr,
            "op",
            "",
        )
        == "+"
    ):
        lhs = _export_static_all_names(_py_ast_field_value(expr, "lhs", None))
        rhs = _export_static_all_names(_py_ast_field_value(expr, "rhs", None))
        if lhs is None or rhs is None:
            return None
        return lhs + rhs
    return None


def _export_common_static_type(types):
    from .py_ast import DynType as _DynType

    concrete = []
    for ty in types:
        if ty is not None:
            concrete.append(ty)
    if not concrete:
        return _DynType("dyn")
    first = concrete[0]
    first_key = encode_type(first)
    for ty in concrete[1:]:
        if encode_type(ty) != first_key:
            return _DynType("dyn")
    return first


def _decorator_name(dec):
    from .py_ast import Attr, Call, Name

    if _closed_world_is_node(dec, Call):
        return _decorator_name(_py_ast_field_value(dec, "func", None))
    if _closed_world_is_node(dec, Name):
        return _py_ast_field_value(dec, "ident", "")
    if _closed_world_is_node(dec, Attr):
        base = _decorator_name(_py_ast_field_value(dec, "obj", None))
        if base:
            return base + "." + _py_ast_field_value(dec, "name", "")
    return None


def _split_top_level_type_args(text: str) -> tuple[str, ...]:
    out = []
    start = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            part = text[start:i].strip()
            if part:
                out.append(part)
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return tuple(out)


def _normalise_export_annotation_text(text: str):
    from .py_ast import BoolType as _BoolType
    from .py_ast import ByteArrayType as _ByteArrayType
    from .py_ast import BytesType as _BytesType
    from .py_ast import ClassType as _ClassType
    from .py_ast import ComplexType as _ComplexType
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntType as _IntType
    from .py_ast import ListType as _ListType
    from .py_ast import SetType as _SetType
    from .py_ast import MemoryViewType as _MemoryViewType
    from .py_ast import NoneType as _NoneType
    from .py_ast import StrType as _StrType
    from .py_ast import TupleType as _TupleType

    text = text.strip()
    if not text:
        return None
    if text == "..." or text == "Ellipsis":
        return None
    if text.startswith("typing."):
        text = text[len("typing.") :]
    if text == "list" or text == "List":
        return _ListType("list", _DynType("dyn"))
    if text in ("set", "Set", "frozenset", "FrozenSet"):
        name = "frozenset" if text in ("frozenset", "FrozenSet") else "set"
        return _SetType(name, _DynType("dyn"))
    if text == "dict" or text == "Dict":
        return _DictType("dict", _DynType("dyn"), _DynType("dyn"))
    if text == "tuple" or text == "Tuple":
        return _TupleType("tuple", (_DynType("dyn"),))
    if text == "str":
        return _StrType("str")
    if text == "int":
        return _IntType("int")
    if text == "bool":
        return _BoolType("bool")
    if text == "float":
        return _FloatType("float")
    if text == "complex":
        return _ComplexType("complex")
    if text == "bytes":
        return _BytesType("bytes")
    if text == "bytearray":
        return _ByteArrayType("bytearray")
    if text == "memoryview":
        return _MemoryViewType("memoryview")
    if text == "None" or text == "NoneType":
        return _NoneType("None")
    if text == "object" or text == "Any":
        return _DynType("dyn")
    open_bracket = _find_substring(text, "[", 0)
    if open_bracket >= 0 and text.endswith("]"):
        head = text[:open_bracket].strip()
        inner = text[open_bracket + 1 : -1]
        if head.startswith("typing."):
            head = head[len("typing.") :]
        args = _split_top_level_type_args(inner)
        if head == "list" or head == "List":
            elem = (
                _normalise_export_annotation_text(args[0]) if len(args) == 1 else None
            )
            return _ListType("list", elem or _DynType("dyn"))
        if head in ("set", "Set", "frozenset", "FrozenSet"):
            elem = (
                _normalise_export_annotation_text(args[0]) if len(args) == 1 else None
            )
            name = "frozenset" if head in ("frozenset", "FrozenSet") else "set"
            return _SetType(name, elem or _DynType("dyn"))
        if head == "dict" or head == "Dict":
            key = _normalise_export_annotation_text(args[0]) if len(args) == 2 else None
            value = (
                _normalise_export_annotation_text(args[1]) if len(args) == 2 else None
            )
            return _DictType(
                "dict",
                key or _DynType("dyn"),
                value or _DynType("dyn"),
            )
        if head == "tuple" or head == "Tuple":
            elems = []
            for arg in args:
                if arg == "..." or arg == "Ellipsis":
                    continue
                elem = _normalise_export_annotation_text(arg)
                elems.append(elem or _DynType("dyn"))
            if not elems:
                elems.append(_DynType("dyn"))
            return _TupleType("tuple", tuple(elems))
        if head == "Optional" and len(args) == 1:
            return _normalise_export_annotation_text(args[0])
        return _DynType("dyn")
    if "." in text:
        last_dot = -1
        i = 0
        while i < len(text):
            if text[i] == ".":
                last_dot = i
            i += 1
        return _ClassType(text[last_dot + 1 :], text[:last_dot], (), ())
    return _ClassType(text, "", (), ())


def _normalise_export_annotation(ann):
    if ann is None:
        return None
    from .py_ast import ClassType as _ClassType
    from .py_ast import DynType as _DynType
    from .py_ast import Type as _Type

    if isinstance(ann, _ClassType):
        class_name = str(getattr(ann, "name", "") or "")
        if class_name == "list":
            from .py_ast import ListType as _ListType

            return _ListType("list", _DynType("dyn"))
        if class_name in ("set", "frozenset"):
            from .py_ast import SetType as _SetType

            return _SetType(class_name, _DynType("dyn"))
        if class_name == "dict":
            from .py_ast import DictType as _DictType

            return _DictType("dict", _DynType("dyn"), _DynType("dyn"))
        if class_name == "tuple":
            from .py_ast import TupleType as _TupleType

            return _TupleType("tuple", (_DynType("dyn"),))
        if class_name == "object":
            return _DynType("dyn")
    if isinstance(ann, _Type):
        return ann
    if isinstance(ann, str):
        text = ann.strip()
    else:
        try:
            text = str(ann.__name__)
        except Exception:
            text = str(ann)
    return _normalise_export_annotation_text(text)


def _export_annotation_or_none(obj):
    return _normalise_export_annotation(_py_ast_field_value(obj, "annotation", None))


def _export_return_ty_or_none(obj):
    return _py_ast_field_value(obj, "return_ty", None)


def _class_is_dataclass(cd) -> bool:
    for dec in _py_ast_field_value(cd, "decorators", ()):
        name = _decorator_name(dec)
        if name in ("dataclass", "dataclasses.dataclass"):
            return True
    return False


def _export_default_native_func_ref(expr, owning_module, top_level_func_names):
    if expr is None or not owning_module:
        return None
    from .py_ast import Name as _Name

    if not _closed_world_is_node(expr, _Name):
        return None
    ident = str(_py_ast_field_value(expr, "ident", ""))
    if ident not in top_level_func_names:
        return None
    return {
        "owning_module": str(owning_module),
        "name": ident,
    }


def _export_default_native_global_ref(expr, owning_module, top_level_func_names):
    """Record a default rooted at the defining module's own global.

    This covers both ``def f(x=MODULE_CONST)`` and attribute chains such as
    ``def f(match=WHITESPACE.match)``.  A cross-module caller cannot re-emit
    the bare root Name in its own namespace; it must load the defining
    module's export first and then apply the recorded attributes.
    """
    if expr is None or not owning_module:
        return None
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name

    attrs = []
    root = expr
    while _closed_world_is_node(root, _Attr):
        attr_name = str(_py_ast_field_value(root, "name", ""))
        if not attr_name:
            return None
        attrs.append(attr_name)
        root = _py_ast_field_value(root, "obj", None)
    if not _closed_world_is_node(root, _Name):
        return None
    ident = str(_py_ast_field_value(root, "ident", ""))
    if not ident or ident in top_level_func_names:
        return None
    ref = {
        "owning_module": str(owning_module),
        "name": ident,
    }
    if attrs:
        ref["attrs"] = tuple(reversed(attrs))
    return ref


def _export_call_sig(args, owning_module=None, top_level_func_names=()):
    sig = []
    top_level_func_names = set(top_level_func_names or ())
    for a in args:
        ann = _export_annotation_or_none(a)
        default = _py_ast_field_value(a, "default", None)
        item = {
            "name": _py_ast_field_value(a, "name", ""),
            "kind": _py_ast_field_value(a, "kind", "pos"),
            "annotation": encode_type(ann),
            "default": default,
            "has_default": _py_ast_field_value(a, "has_default", False),
        }
        default_native_func = _export_default_native_func_ref(
            default,
            owning_module,
            top_level_func_names,
        )
        if default_native_func is not None:
            item["default_native_func"] = default_native_func
        else:
            default_native_global = _export_default_native_global_ref(
                default,
                owning_module,
                top_level_func_names,
            )
            if default_native_global is not None:
                item["default_native_global"] = default_native_global
        sig.append(item)
    return tuple(sig)


_EXPORT_DEFAULT_WIRE_KEY = "__pcc_export_default_v1__"


def _export_default_to_wire(expr):
    if expr is None:
        return {_EXPORT_DEFAULT_WIRE_KEY: "absent"}
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BytesLit as _BytesLit
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import IntLit as _IntLit
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import StrLit as _StrLit
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import UnaryOp as _UnaryOp

    if isinstance(expr, _NoneLit):
        return {_EXPORT_DEFAULT_WIRE_KEY: "none"}
    if isinstance(expr, _Name):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "name",
            "ident": str(_py_ast_field_value(expr, "ident", "")),
        }
    if isinstance(expr, _Attr):
        obj_wire = _export_default_to_wire(_py_ast_field_value(expr, "obj", None))
        if not _export_default_wire_is_safe(obj_wire):
            return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "attr",
            "obj": obj_wire,
            "name": str(_py_ast_field_value(expr, "name", "")),
        }
    if isinstance(expr, _UnaryOp):
        operand_wire = _export_default_to_wire(
            _py_ast_field_value(expr, "operand", None)
        )
        if not _export_default_wire_is_safe(operand_wire):
            return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "unary",
            "op": str(_py_ast_field_value(expr, "op", "")),
            "operand": operand_wire,
        }
    if isinstance(expr, _BoolLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "bool",
            "value": bool(_py_ast_field_value(expr, "value", False)),
        }
    if isinstance(expr, _IntLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "int",
            "value": int(_py_ast_field_value(expr, "value", 0)),
        }
    if isinstance(expr, _FloatLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "float",
            "value": float(_py_ast_field_value(expr, "value", 0.0)),
        }
    if isinstance(expr, _StrLit):
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "str",
            "value": str(_py_ast_field_value(expr, "value", "")),
        }
    if isinstance(expr, _BytesLit):
        raw = _py_ast_field_value(expr, "value", b"")
        values = []
        i = 0
        while i < len(raw):
            values.append(int(raw[i]))
            i += 1
        return {
            _EXPORT_DEFAULT_WIRE_KEY: "bytes",
            "value": values,
        }
    if isinstance(expr, _TupleExpr):
        elems = []
        for elem in _py_ast_field_value(expr, "elems", ()):
            elem_wire = _export_default_to_wire(elem)
            if not _export_default_wire_is_safe(elem_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            elems.append(elem_wire)
        return {_EXPORT_DEFAULT_WIRE_KEY: "tuple", "elems": elems}
    if isinstance(expr, _ListExpr):
        elems = []
        for elem in _py_ast_field_value(expr, "elems", ()):
            elem_wire = _export_default_to_wire(elem)
            if not _export_default_wire_is_safe(elem_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            elems.append(elem_wire)
        return {_EXPORT_DEFAULT_WIRE_KEY: "list", "elems": elems}
    if isinstance(expr, _DictExpr):
        pairs = []
        for key, item in _py_ast_field_value(expr, "pairs", ()):
            key_wire = _export_default_to_wire(key)
            item_wire = _export_default_to_wire(item)
            if not _export_default_wire_is_safe(
                key_wire
            ) or not _export_default_wire_is_safe(item_wire):
                return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}
            pairs.append((key_wire, item_wire))
        return {_EXPORT_DEFAULT_WIRE_KEY: "dict", "pairs": pairs}
    return {_EXPORT_DEFAULT_WIRE_KEY: "complex"}


def _export_default_wire_is_safe(wire) -> bool:
    if not isinstance(wire, dict):
        return False
    kind = wire.get(_EXPORT_DEFAULT_WIRE_KEY)
    if kind == "complex":
        return False
    if kind in ("tuple", "list"):
        for elem in wire.get("elems", ()):
            if not _export_default_wire_is_safe(elem):
                return False
    if kind == "dict":
        for key, item in wire.get("pairs", ()):
            if not _export_default_wire_is_safe(
                key
            ) or not _export_default_wire_is_safe(item):
                return False
    return True


def _export_default_from_wire(wire):
    if not isinstance(wire, dict):
        return None
    kind = wire.get(_EXPORT_DEFAULT_WIRE_KEY)
    if kind == "absent" or kind == "complex":
        return None
    from .py_ast import BoolLit as _BoolLit
    from .py_ast import BoolType as _BoolType
    from .py_ast import BytesLit as _BytesLit
    from .py_ast import BytesType as _BytesType
    from .py_ast import DictExpr as _DictExpr
    from .py_ast import DictType as _DictType
    from .py_ast import DynType as _DynType
    from .py_ast import FloatLit as _FloatLit
    from .py_ast import FloatType as _FloatType
    from .py_ast import IntLit as _IntLit
    from .py_ast import IntType as _IntType
    from .py_ast import ListExpr as _ListExpr
    from .py_ast import ListType as _ListType
    from .py_ast import Attr as _Attr
    from .py_ast import Name as _Name
    from .py_ast import NoneLit as _NoneLit
    from .py_ast import NoneType as _NoneType
    from .py_ast import SourceSpan as _SourceSpan
    from .py_ast import StrLit as _StrLit
    from .py_ast import StrType as _StrType
    from .py_ast import TupleExpr as _TupleExpr
    from .py_ast import TupleType as _TupleType
    from .py_ast import UnaryOp as _UnaryOp

    span = _SourceSpan("<extern-default>", 0, 0, 0, 0)
    if kind == "name":
        return _Name(span, _DynType("dyn"), str(wire.get("ident", "")))
    if kind == "attr":
        obj = _export_default_from_wire(wire.get("obj"))
        if obj is None:
            return None
        return _Attr(span, _DynType("dyn"), obj, str(wire.get("name", "")))
    if kind == "unary":
        operand = _export_default_from_wire(wire.get("operand"))
        if operand is None:
            return None
        return _UnaryOp(span, _DynType("dyn"), str(wire.get("op", "")), operand)
    if kind == "none":
        return _NoneLit(span, _NoneType("None"))
    if kind == "bool":
        return _BoolLit(span, _BoolType("bool"), bool(wire.get("value", False)))
    if kind == "int":
        return _IntLit(span, _IntType("int"), int(wire.get("value", 0)))
    if kind == "float":
        return _FloatLit(span, _FloatType("float"), float(wire.get("value", 0.0)))
    if kind == "str":
        return _StrLit(span, _StrType("str"), str(wire.get("value", "")))
    if kind == "bytes":
        return _BytesLit(span, _BytesType("bytes"), bytes(wire.get("value", ())))
    if kind == "tuple":
        elems = tuple(_export_default_from_wire(elem) for elem in wire.get("elems", ()))
        elem_types = tuple(getattr(elem, "ty", _DynType("dyn")) for elem in elems)
        return _TupleExpr(span, _TupleType("tuple", elem_types), elems)
    if kind == "list":
        elems = tuple(_export_default_from_wire(elem) for elem in wire.get("elems", ()))
        elem_ty = getattr(elems[0], "ty", _DynType("dyn")) if elems else _DynType("dyn")
        return _ListExpr(span, _ListType("list", elem_ty), elems)
    if kind == "dict":
        pairs = tuple(
            (
                _export_default_from_wire(pair[0]),
                _export_default_from_wire(pair[1]),
            )
            for pair in wire.get("pairs", ())
        )
        if pairs:
            key_ty = getattr(pairs[0][0], "ty", _DynType("dyn"))
            value_ty = getattr(pairs[0][1], "ty", _DynType("dyn"))
        else:
            key_ty = _DynType("dyn")
            value_ty = _DynType("dyn")
        return _DictExpr(span, _DictType("dict", key_ty, value_ty), pairs)
    return None


def _native_export_arg_to_wire(arg):
    out = {}
    default_safe = True
    for key, value in arg.items():
        if key == "default":
            default_wire = _export_default_to_wire(value)
            default_safe = _export_default_wire_is_safe(default_wire)
            out[key] = default_wire
        else:
            out[key] = _native_export_to_wire(value)
    if not default_safe:
        out["has_default"] = False
    return out


def _native_export_to_wire(value):
    if isinstance(value, dict):
        if (
            "name" in value
            and "kind" in value
            and "annotation" in value
            and "default" in value
            and "has_default" in value
        ):
            return _native_export_arg_to_wire(value)
        out = {}
        for key, item in value.items():
            out[str(key)] = _native_export_to_wire(item)
        return out
    if isinstance(value, (tuple, list)):
        out = []
        for item in value:
            out.append(_native_export_to_wire(item))
        return out
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _native_export_from_wire(value):
    if isinstance(value, dict):
        if _EXPORT_DEFAULT_WIRE_KEY in value:
            return _export_default_from_wire(value)
        out = {}
        for key, item in value.items():
            out[key] = _native_export_from_wire(item)
        return out
    if isinstance(value, list):
        return tuple(_native_export_from_wire(item) for item in value)
    return value


def _write_native_exports_wire(
    path: str,
    native_exports,
    derived_class_map,
    function_object_uses=(),
) -> None:
    native_exports_wire = _native_export_to_wire(native_exports)
    derived_class_map_wire = _native_export_to_wire(derived_class_map)
    payload = {
        "schema": "pcc.py_frontend.native_exports.v1",
        "native_exports": native_exports_wire,
        "derived_class_map": derived_class_map_wire,
        "function_object_uses": _native_export_to_wire(function_object_uses),
    }
    text = json.dumps(payload)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read_native_exports_wire(path: str, include_function_object_uses: bool = False):
    with open(path, "r", encoding="utf-8") as f:
        payload = json.loads(f.read())
    if payload.get("schema") != "pcc.py_frontend.native_exports.v1":
        raise PyPipelineError("invalid frontend native exports file")
    native_exports = _native_export_from_wire(payload.get("native_exports", {}))
    derived_class_map = _native_export_from_wire(payload.get("derived_class_map", {}))
    if include_function_object_uses:
        function_object_uses = _native_export_from_wire(
            payload.get("function_object_uses", ())
        )
        return native_exports, derived_class_map, function_object_uses
    return native_exports, derived_class_map


def _export_method_symbol(
    module_name: str,
    class_name: str,
    method_name: str,
    top_level_func_names,
) -> str:
    sanitised_mod = module_name.replace(".", "_").replace("-", "_")
    if class_name + "_" + method_name in top_level_func_names:
        return f"user_{sanitised_mod}_{class_name}__method_{method_name}"
    return f"user_{sanitised_mod}_{class_name}_{method_name}"
