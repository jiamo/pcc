"""Valhalla-inspired value model helpers for pcc's Python frontend.

The compiler consumes the type marker (`@pcc.valueclass`) during type inference
and class lowering.  This module also exposes host-side projection helpers used
by planning tests. These helpers are not the production C runtime ValueBox,
unboxed ABI, field-flattening, or specialization implementation.
"""

from __future__ import annotations

import inspect
import operator
import sys
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Callable, Iterable, TypeVar, get_type_hints

T = TypeVar("T")


@dataclass(frozen=True)
class ValueClassDescriptor:
    name: str
    module: str
    fields: tuple[tuple[str, str], ...]
    flattened: bool = True
    nullable_fields: bool = False


@dataclass(frozen=True)
class ValuePayload:
    descriptor: ValueClassDescriptor
    values: tuple[Any, ...]


@dataclass(frozen=True)
class ValueBox:
    payload: ValuePayload

    @property
    def descriptor(self) -> ValueClassDescriptor:
        return self.payload.descriptor

    def unbox(self) -> ValuePayload:
        return self.payload

    def __hash__(self) -> int:
        return hash((self.payload.descriptor, self.payload.values))


@dataclass(frozen=True)
class SpecializedArray:
    descriptor: ValueClassDescriptor
    values: tuple[ValuePayload, ...]

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, index: int) -> ValueBox:
        return ValueBox(self.values[index])


@dataclass(frozen=True)
class GenericSpecialization:
    name: str
    type_args: tuple[str, ...]
    payload_abi: str


class _ValueArrayAlias:
    def __init__(self, element_type: type[Any], length: int) -> None:
        if not isinstance(element_type, type) or not is_valueclass(element_type):
            raise TypeError("pcc.array element type must be a valueclass")
        if not isinstance(length, int):
            raise TypeError("pcc.array length must be an integer literal")
        if length < 1 or length > 7:
            raise ValueError("pcc.array length must be between 1 and 7")
        self.element_type = element_type
        self.length = length

    def __call__(self, *values: Any) -> "array":
        return array._from_spec(self.element_type, self.length, values)


class array:
    """Host oracle for the fixed-length compiler-owned value array surface."""

    def __init__(self, *values: Any) -> None:
        raise TypeError("construct value arrays as pcc.array[ValueClass, N](...)")

    @classmethod
    def __class_getitem__(cls, params: object) -> _ValueArrayAlias:
        if not isinstance(params, tuple) or len(params) != 2:
            raise TypeError("pcc.array needs an element type and literal length")
        return _ValueArrayAlias(params[0], params[1])

    @classmethod
    def _from_spec(
        cls,
        element_type: type[Any],
        length: int,
        values: tuple[Any, ...],
    ) -> "array":
        if len(values) != length:
            raise TypeError(f"pcc.array expects exactly {length} elements")
        for index, value in enumerate(values):
            if type(value) is not element_type:
                raise TypeError(
                    f"pcc.array element {index + 1} has type "
                    f"{type(value).__name__}, expected {element_type.__name__}"
                )
        instance = object.__new__(cls)
        instance.element_type = element_type
        instance.length = length
        instance.values = tuple(values)
        return instance

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: object) -> Any:
        integer = operator.index(index)
        if integer < -sys.maxsize - 1 or integer > sys.maxsize:
            raise OverflowError("pcc.array index does not fit in a machine index")
        if integer < 0:
            integer += self.length
        if integer < 0 or integer >= self.length:
            raise IndexError("pcc.array index out of range")
        return self.values[integer]


def valueclass(
    cls: type[T] | None = None, **kwargs: Any
) -> type[T] | Callable[[type[T]], type[T]]:
    """Mark a class as identity-free and immutable.

    Host Python receives a frozen dataclass for ergonomics; pcc's compiler also
    recognizes the decorator and records a `ValueClassType`.
    """

    def wrap(inner: type[T]) -> type[T]:
        if not is_dataclass(inner):
            from dataclasses import dataclass as _dataclass

            inner = _dataclass(frozen=True, **kwargs)(inner)  # type: ignore[assignment]
        setattr(inner, "__pcc_valueclass__", True)
        setattr(inner, "__pcc_value_layout__", value_payload_layout(inner))
        return inner

    if cls is None:
        return wrap
    return wrap(cls)


def is_valueclass(obj: Any) -> bool:
    target = obj if isinstance(obj, type) else type(obj)
    return bool(getattr(target, "__pcc_valueclass__", False))


def _resolved_type_hints(cls: type[Any]) -> dict[str, Any]:
    raw_hints = getattr(cls, "__annotations__", {})
    if not isinstance(raw_hints, dict):
        return {}

    module = sys.modules.get(getattr(cls, "__module__", ""), None)
    globalns = vars(module) if module is not None else {}
    localns: dict[str, Any] = {}

    frame = inspect.currentframe()
    try:
        while frame is not None:
            localns.update(frame.f_locals)
            frame = frame.f_back
    finally:
        del frame

    for candidate_globalns in (globalns, localns, {**globalns, **localns}):
        try:
            return get_type_hints(
                cls,
                globalns=candidate_globalns,
                localns=globalns,
            )
        except Exception:
            pass
        try:
            return get_type_hints(
                cls,
                globalns=globalns,
                localns=candidate_globalns,
            )
        except Exception:
            pass
    for candidate_localns in (globalns, localns, {**globalns, **localns}):
        try:
            return get_type_hints(
                cls,
                globalns={**globalns, **candidate_localns},
                localns=candidate_localns,
            )
        except Exception:
            pass
    return dict(raw_hints)


def value_payload_layout(cls: type[Any]) -> ValueClassDescriptor:
    hints = _resolved_type_hints(cls)
    if is_dataclass(cls):
        ordered = tuple(
            (f.name, _type_name(hints.get(f.name, Any))) for f in fields(cls)
        )
    else:
        ordered = tuple((name, _type_name(ty)) for name, ty in hints.items())
    return ValueClassDescriptor(cls.__name__, cls.__module__, ordered)


def to_payload(value: Any) -> ValuePayload:
    if isinstance(value, ValueBox):
        return value.payload
    if isinstance(value, ValuePayload):
        return value
    desc = value_payload_layout(type(value))
    vals = tuple(getattr(value, name) for name, _ty in desc.fields)
    return ValuePayload(desc, vals)


def box_value(value: Any) -> ValueBox:
    return ValueBox(to_payload(value))


def unbox_value(value: ValueBox | ValuePayload | Any) -> ValuePayload:
    return to_payload(value)


def flatten_fields(cls: type[Any]) -> tuple[tuple[str, str], ...]:
    desc = value_payload_layout(cls)
    out: list[tuple[str, str]] = []
    hints = _resolved_type_hints(cls)
    for name, ty_name in desc.fields:
        ty = hints.get(name)
        if isinstance(ty, type) and is_valueclass(ty):
            for child_name, child_ty in flatten_fields(ty):
                out.append((name + "." + child_name, child_ty))
        else:
            out.append((name, ty_name))
    return tuple(out)


def specialized_array(values: Iterable[Any]) -> SpecializedArray:
    payloads = tuple(to_payload(v) for v in values)
    if payloads:
        descriptor = payloads[0].descriptor
    else:
        descriptor = ValueClassDescriptor("empty", "pcc.value_model", ())
    return SpecializedArray(descriptor, payloads)


def specialize_generic_signature(
    name: str, *type_args: type[Any] | str
) -> GenericSpecialization:
    names = tuple(_type_name(arg) for arg in type_args)
    abi = name + "[" + ",".join(names) + "]::value_payload"
    return GenericSpecialization(name, names, abi)


def value_model_status() -> dict[str, object]:
    return {
        "implemented_through": (
            "V1-direct-scalar-and-nested-payload-eq-checked-marshal-"
            "v2-pointer-and-nested-dyn-boundary-partial"
        ),
        "scaffolding_through": "V6",
        "production_runtime": False,
        "marker": "@pcc.valueclass",
        "implemented": [
            "ValueClassType frontend model",
            "frozen valueclass lowering via dataclass-compatible synthesis",
            "V0 source-shape diagnostics for unsupported valueclass forms",
            "V1 scalar-field value payload lowering for local constructor assignment and field reads",
            "V1 direct function argument and constructor-return payload ABI for scalar-field valueclasses",
            "V1 non-recursive nested valueclass direct payload ABI for focused typed calls/returns",
            "V1 direct method receiver payload ABI for scalar-field valueclasses",
            "V1 fieldwise equality for direct scalar-field valueclass payloads",
            "V1 recursive fieldwise equality for non-recursive nested valueclass direct payloads",
            "V1 scalar-field value payload to ordinary pcc object boxing at Dyn/object boundaries",
            "V1 ordinary pcc object to scalar-field value payload unboxing at typed boundaries",
            "V1 type-checked ordinary pcc object to scalar-field value payload unboxing failure path",
            "V2 selected pointer-field payload lowering for object fields "
            "in valueclass payload structs",
            "V2 selected pointer-field valuebox roundtrip across Dyn/object boundaries",
            "V2 selected pointer-field valueclass equality using object equality",
            "V2 selected boxed valueclass equality across Dyn/object boundaries",
            "V2 selected boxed valueclass hash aligned with boxed equality",
            "V2 selected nested valueclass constructor returns to Any/Dyn "
            "through ValueBox projection",
            "V2 selected dataclasses.replace keyword override projection "
            "through ValueBox",
            "V2 selected membership needle and dict-object subscript getitem "
            "key projection through ValueBox",
            "V2 selected builtin hash/repr/str/format/type object-boundary "
            "projection through ValueBox",
            "C runtime PyValueBox object and GC tracing",
            "V1 diagnostics rejecting recursive and mutually-recursive valueclass payloads",
            "host-side ValuePayload/ValueBox projection helpers for planning tests",
        ],
        "metadata_scaffolding": [
            "field flattening descriptors through flatten_fields",
            "specialized value-array descriptors through SpecializedArray",
            "generic/method specialization descriptor names",
            "pcc compiler hot-object migration candidates",
        ],
        "not_implemented": [
            "full direct LLVM struct/value payload ABI for identity escapes, "
            "complete boxing boundaries, recursive/broader nested valueclasses, and full "
            "non-scalar payload coverage",
            "full marshal_value_to_object / marshal_object_to_value coverage "
            "for all object/value boundaries",
            "class layout metadata with flattened payload slots",
            "pcc.array[ValueClass] contiguous runtime storage",
            "generic monomorphization and --explain-specialization",
            "real SourceSpan migration with pcc1 allocation benchmark evidence",
        ],
        "non_goals": [
            "full CPython typing/generic runtime fidelity",
            "all Python object optimizations",
        ],
    }


def _type_name(ty: Any) -> str:
    if isinstance(ty, str):
        return ty
    if getattr(ty, "__module__", "builtins") == "builtins":
        return getattr(ty, "__name__", repr(ty))
    return getattr(ty, "__module__", "") + "." + getattr(ty, "__name__", repr(ty))
