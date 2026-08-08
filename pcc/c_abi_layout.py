"""Small target-layout facts shared by the C frontend and SSA builder.

The current supported C execution targets use the LP64 data model. Aggregate
layout remains owned by the consumers; this module owns only fundamental
scalar and pointer size/alignment facts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CAbiScalarLayout:
    size: int
    alignment: int


def integer_scalar_layout(bit_width: int) -> CAbiScalarLayout:
    if bit_width <= 0:
        raise ValueError(f"invalid integer width: {bit_width}")
    size = max(1, (bit_width + 7) // 8)
    return CAbiScalarLayout(size=size, alignment=min(size, 8))


def floating_scalar_layout(bit_width: int) -> CAbiScalarLayout:
    if bit_width not in (16, 32, 64, 128):
        raise ValueError(f"unsupported floating width: {bit_width}")
    size = bit_width // 8
    return CAbiScalarLayout(size=size, alignment=size)


def pointer_scalar_layout() -> CAbiScalarLayout:
    return CAbiScalarLayout(size=8, alignment=8)


def builtin_scalar_layout(names: list[str] | tuple[str, ...]) -> CAbiScalarLayout:
    """Return LP64 layout for one C builtin spelling."""
    normalized = tuple(name for name in names if name not in ("signed", "unsigned"))

    if normalized == ("_Bool",):
        return integer_scalar_layout(8)
    if "char" in normalized:
        return integer_scalar_layout(8)
    if "short" in normalized:
        return integer_scalar_layout(16)
    if normalized.count("long") >= 2:
        return integer_scalar_layout(64)
    if normalized == ("long", "double"):
        return floating_scalar_layout(128)
    if normalized == ("_Float16",):
        return floating_scalar_layout(16)
    if "long" in normalized:
        return integer_scalar_layout(64)
    if "float" in normalized:
        return floating_scalar_layout(32)
    if "double" in normalized:
        return floating_scalar_layout(64)
    if "wchar_t" in normalized:
        return integer_scalar_layout(32)
    if "int" in normalized or not normalized:
        return integer_scalar_layout(32)
    if "void" in normalized:
        raise ValueError("void has no object size")
    raise ValueError(f"unsupported builtin scalar layout: {' '.join(names)}")
