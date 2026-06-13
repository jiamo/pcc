from __future__ import annotations

"""pcc-native IEEE-754 helpers for the self backend.

The self emitter cannot depend on CPython's ``struct`` module: pcc1 must be
able to execute it without libpython.  These arithmetic conversions mirror the
helpers already used by the no-libpython LLVM-C IR builder.
"""


def float64_to_bits(value: float) -> int:
    if value != value:
        return 0x7FF8000000000000
    inf = 1e309
    if value == inf:
        return 0x7FF0000000000000
    if value == -inf:
        return 0xFFF0000000000000
    if value == 0.0:
        text = str(value)
        if len(text) > 0 and text[0] == "-":
            return 0x8000000000000000
        return 0
    sign = 0
    if value < 0.0:
        sign = 1
        value = -value
    exponent = 0
    while value >= 2.0:
        value *= 0.5
        exponent += 1
    while value < 1.0:
        value *= 2.0
        exponent -= 1
    mantissa = int((value - 1.0) * 4503599627370496.0)
    biased = exponent + 1023
    if biased <= 0:
        return sign << 63
    if biased >= 0x7FF:
        return (sign << 63) | 0x7FF0000000000000
    return (sign << 63) | (biased << 52) | mantissa


def bits_to_float64(bits: int) -> float:
    sign = (bits >> 63) & 1
    biased = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    inf = 1e309
    if biased == 0x7FF:
        if mantissa == 0:
            return -inf if sign else inf
        return inf - inf
    if biased == 0:
        if mantissa == 0:
            return -0.0 if sign else 0.0
        value = mantissa / 4503599627370496.0
        i = 0
        while i < 11:
            value *= 0.0009765625
            i += 1
        value *= 2.0
        return -value if sign else value
    value = 1.0 + mantissa / 4503599627370496.0
    exponent = biased - 1023
    if exponent >= 0:
        i = 0
        while i < exponent:
            value *= 2.0
            i += 1
    else:
        i = 0
        while i < -exponent:
            value *= 0.5
            i += 1
    return -value if sign else value


def float32_to_bits(value: float) -> int:
    bits = float64_to_bits(value)
    sign = (bits >> 63) & 1
    biased = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    if biased == 0x7FF:
        return (sign << 31) | (0x7F800000 if mantissa == 0 else 0x7FC00000)
    if biased == 0:
        return sign << 31
    exponent = biased - 1023 + 127
    drop = 52 - 23
    kept = mantissa >> drop
    halfway = 1 << (drop - 1)
    remainder = mantissa & ((1 << drop) - 1)
    if remainder > halfway or (remainder == halfway and (kept & 1) != 0):
        kept += 1
    if kept >= (1 << 23):
        kept = 0
        exponent += 1
    if exponent >= 255:
        return (sign << 31) | 0x7F800000
    if exponent <= 0:
        return sign << 31
    return (sign << 31) | (exponent << 23) | kept
