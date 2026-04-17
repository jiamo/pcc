"""pcc-Python port of the stdlib ``struct`` module.

Minimal — covers exactly the format codes used by
``pcc/llvm_capi/ir.py`` so that ``ir.py`` can be compiled natively
in the bootstrap closure without pulling libpython for ``struct``
(Issue 11.C.2 → unblocks Phase 9.2).

Supported format codes:

  ===  ==========================  =====
  ``>d``  big-endian binary64 (double)   8 bytes
  ``>f``  big-endian binary32 (single)   4 bytes
  ``>e``  big-endian binary16 (half)     2 bytes
  ``>Q``  big-endian uint64              8 bytes
  ===  ==========================  =====

Anything else raises :class:`StructPortError` so callers see a clear
unsupported-format message rather than silently producing wrong
bytes. Add format codes here if a future module needs them.
"""
from __future__ import annotations


class StructPortError(ValueError):
    """Raised when this port encounters a format code it doesn't
    cover. Distinct from CPython's ``struct.error`` so callers can
    tell "pcc port limitation" apart from "real input error"."""


# IEEE 754 helpers live in ``pcc.stdlib._float_bits`` — shared with
# ``pcc/llvm_capi/ir.py`` which uses the int-form interface directly
# (skipping the bytes intermediate). Re-exported here for any caller
# that needs them alongside the bytes-API ``pack``/``unpack``.
from pcc.stdlib._float_bits import (
    _float64_to_bits,
    _bits_to_float64,
    _round_to_float32,
    _round_to_float16,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pack(fmt: str, value) -> bytes:
    """Pack ``value`` according to ``fmt`` (subset of CPython struct).

    Supported: ``>d`` (binary64), ``>f`` (binary32), ``>e`` (binary16),
    ``>Q`` (uint64-be).
    """
    if fmt == ">d":
        bits = _float64_to_bits(float(value))
        return bits.to_bytes(8, "big")
    if fmt == ">f":
        rounded = _round_to_float32(float(value))
        bits = _float64_to_bits(rounded)
        # binary32 packing: extract sign + 8-bit exp + 23-bit mantissa
        # from the rounded double's bit pattern.
        sign = (bits >> 63) & 1
        biased_exp = (bits >> 52) & 0x7FF
        mantissa = bits & ((1 << 52) - 1)
        if biased_exp == 0x7FF:
            f32_exp = 0xFF
            f32_mantissa = 0 if mantissa == 0 else 1 << 22
        elif biased_exp == 0 and mantissa == 0:
            f32_exp = 0
            f32_mantissa = 0
        else:
            f32_exp = biased_exp - 1023 + 127
            if f32_exp <= 0:
                f32_exp = 0
                f32_mantissa = 0
            elif f32_exp >= 0xFF:
                f32_exp = 0xFF
                f32_mantissa = 0
            else:
                f32_mantissa = mantissa >> (52 - 23)
        f32_bits = (sign << 31) | (f32_exp << 23) | f32_mantissa
        return f32_bits.to_bytes(4, "big")
    if fmt == ">e":
        rounded = _round_to_float16(float(value))
        bits = _float64_to_bits(rounded)
        sign = (bits >> 63) & 1
        biased_exp = (bits >> 52) & 0x7FF
        mantissa = bits & ((1 << 52) - 1)
        if biased_exp == 0x7FF:
            f16_exp = 0x1F
            f16_mantissa = 0 if mantissa == 0 else 1 << 9
        elif biased_exp == 0 and mantissa == 0:
            f16_exp = 0
            f16_mantissa = 0
        else:
            f16_exp = biased_exp - 1023 + 15
            if f16_exp <= 0:
                f16_exp = 0
                f16_mantissa = 0
            elif f16_exp >= 0x1F:
                f16_exp = 0x1F
                f16_mantissa = 0
            else:
                f16_mantissa = mantissa >> (52 - 10)
        f16_bits = (sign << 15) | (f16_exp << 10) | f16_mantissa
        return f16_bits.to_bytes(2, "big")
    if fmt == ">Q":
        return int(value).to_bytes(8, "big")
    raise StructPortError(
        f"pcc.stdlib.struct doesn't support format {fmt!r} "
        f"(only '>d', '>f', '>e', '>Q')"
    )


def unpack(fmt: str, buf: bytes) -> tuple:
    """Unpack ``buf`` according to ``fmt`` and return a tuple."""
    if fmt == ">d":
        bits = int.from_bytes(buf, "big")
        return (_bits_to_float64(bits),)
    if fmt == ">f":
        f32_bits = int.from_bytes(buf, "big")
        sign = (f32_bits >> 31) & 1
        f32_exp = (f32_bits >> 23) & 0xFF
        f32_mantissa = f32_bits & ((1 << 23) - 1)
        if f32_exp == 0xFF:
            biased_exp = 0x7FF
            mantissa = 0 if f32_mantissa == 0 else 1 << 51
        elif f32_exp == 0 and f32_mantissa == 0:
            return (-0.0 if sign else 0.0,)
        else:
            biased_exp = f32_exp - 127 + 1023
            mantissa = f32_mantissa << (52 - 23)
        bits = (sign << 63) | (biased_exp << 52) | mantissa
        return (_bits_to_float64(bits),)
    if fmt == ">e":
        f16_bits = int.from_bytes(buf, "big")
        sign = (f16_bits >> 15) & 1
        f16_exp = (f16_bits >> 10) & 0x1F
        f16_mantissa = f16_bits & ((1 << 10) - 1)
        if f16_exp == 0x1F:
            biased_exp = 0x7FF
            mantissa = 0 if f16_mantissa == 0 else 1 << 51
        elif f16_exp == 0 and f16_mantissa == 0:
            return (-0.0 if sign else 0.0,)
        else:
            biased_exp = f16_exp - 15 + 1023
            mantissa = f16_mantissa << (52 - 10)
        bits = (sign << 63) | (biased_exp << 52) | mantissa
        return (_bits_to_float64(bits),)
    if fmt == ">Q":
        return (int.from_bytes(buf, "big"),)
    raise StructPortError(
        f"pcc.stdlib.struct doesn't support format {fmt!r} "
        f"(only '>d', '>f', '>e', '>Q')"
    )
