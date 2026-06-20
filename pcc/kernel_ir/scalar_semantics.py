"""Shared POD scalar coercion for Kernel IR CPU and device paths."""

from __future__ import annotations

import math
import struct
from typing import Any


class KernelScalarError(ValueError):
    """A literal cannot be represented by the requested Kernel IR dtype."""


_INTEGER_RANGES = {
    "i8": (-(1 << 7), (1 << 7) - 1),
    "u8": (0, (1 << 8) - 1),
    "i16": (-(1 << 15), (1 << 15) - 1),
    "u16": (0, (1 << 16) - 1),
    "i32": (-(1 << 31), (1 << 31) - 1),
    "u32": (0, (1 << 32) - 1),
    "i64": (-(1 << 63), (1 << 63) - 1),
    "u64": (0, (1 << 64) - 1),
}
_FLOAT_CODES = {"f16": "e", "f32": "f", "f64": "d"}


def coerce_pod_scalar(dtype: str, value: Any) -> bool | int | float:
    """Apply one explicit, checked POD literal conversion contract."""
    if dtype == "bool":
        if type(value) is not bool:
            raise KernelScalarError("bool literal requires a bool value")
        return value
    if dtype in _INTEGER_RANGES:
        if type(value) is not int:
            raise KernelScalarError(f"{dtype} literal requires an int value")
        lo, hi = _INTEGER_RANGES[dtype]
        if value < lo or value > hi:
            raise KernelScalarError(
                f"{dtype} literal {value} is outside [{lo}, {hi}]"
            )
        return value
    code = _FLOAT_CODES.get(dtype)
    if code is not None:
        if type(value) not in {int, float}:
            raise KernelScalarError(f"{dtype} literal requires a numeric value")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise KernelScalarError(f"{dtype} literal must be finite")
        try:
            converted = struct.unpack("<" + code, struct.pack("<" + code, numeric))[0]
        except (OverflowError, struct.error) as exc:
            raise KernelScalarError(
                f"{dtype} literal {value!r} is not representable"
            ) from exc
        if not math.isfinite(converted):
            raise KernelScalarError(f"{dtype} literal {value!r} overflows to non-finite")
        return converted
    raise KernelScalarError(f"unsupported Kernel IR scalar dtype {dtype!r}")


__all__ = ["KernelScalarError", "coerce_pod_scalar"]
