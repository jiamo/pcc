"""Issue 11.C.2: ``pcc/stdlib/struct.py`` parity tests.

The port must produce byte-identical results to CPython's ``struct``
for the formats actually used by ``pcc/llvm_capi/ir.py``:

  - ``>d`` (8-byte big-endian double)
  - ``>f`` (4-byte big-endian single)
  - ``>e`` (2-byte big-endian half)
  - ``>Q`` (8-byte big-endian unsigned 64-bit int)

These are the only ``pack``/``unpack`` shapes ir.py needs. Other
format codes are out of scope (the port is intentionally minimal).
"""
from __future__ import annotations

import struct as _cpy_struct

import pytest


@pytest.fixture(scope="module")
def pcc_struct():
    """Import the pcc-stdlib port directly (not via the recursive
    walker; that's tested elsewhere)."""
    import importlib
    import pcc.stdlib  # ensures the package is on sys.path
    return importlib.import_module("pcc.stdlib.struct")


# ---------------------------------------------------------------------------
# pack: float / double / half  (round-trip via unpack to verify bytes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,value", [
    (">d", 0.0),
    (">d", 1.0),
    (">d", -1.0),
    (">d", 3.141592653589793),
    (">d", 11.1),
    (">d", 1.5e308),
    (">f", 0.0),
    (">f", 1.0),
    (">f", -1.0),
    (">f", 11.1),
    (">f", 3.141592653589793),
    (">e", 0.0),
    (">e", 1.0),
    (">e", -1.0),
    (">e", 0.5),
])
def test_pack_matches_cpython(pcc_struct, fmt, value):
    expected = _cpy_struct.pack(fmt, value)
    actual = pcc_struct.pack(fmt, value)
    assert actual == expected, (
        f"pcc_struct.pack({fmt!r}, {value}) → {actual.hex()}, "
        f"cpython → {expected.hex()}"
    )


# ---------------------------------------------------------------------------
# unpack: round-trip + integer formats
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,value", [
    (">d", 1.5707963267948966),
    (">f", 1.5707963267948966),
    (">e", 1.0),
])
def test_pack_then_unpack_round_trips(pcc_struct, fmt, value):
    """Pack via pcc_struct then unpack via pcc_struct gives the same
    rounded value as CPython's round-trip."""
    expected_bytes = _cpy_struct.pack(fmt, value)
    expected_unpacked = _cpy_struct.unpack(fmt, expected_bytes)[0]
    actual_bytes = pcc_struct.pack(fmt, value)
    actual_unpacked = pcc_struct.unpack(fmt, actual_bytes)[0]
    if expected_unpacked != expected_unpacked:  # NaN
        assert actual_unpacked != actual_unpacked
    else:
        assert actual_unpacked == expected_unpacked


@pytest.mark.parametrize("value", [0, 1, 42, 0xFF, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF])
def test_pack_unpack_Q_uint64(pcc_struct, value):
    """``>Q`` must produce the exact 8-byte big-endian uint64."""
    expected = _cpy_struct.pack(">Q", value)
    actual = pcc_struct.pack(">Q", value)
    assert actual == expected, f"{value:#x}: {actual.hex()} vs {expected.hex()}"

    unpacked = pcc_struct.unpack(">Q", expected)
    assert unpacked == (value,)


# ---------------------------------------------------------------------------
# Critical compatibility test: ir.py's specific usage pattern
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    0.0, 1.0, -1.0, 11.1, 3.141592653589793, 1e-10, 1e10,
])
def test_ir_py_double_bits_pattern(pcc_struct, value):
    """The exact pattern used by ir.py's Constant.__init__:
    ``unpack('>Q', pack('>d', f))[0]`` to extract the 64-bit IEEE 754
    double bit pattern."""
    expected = _cpy_struct.unpack(">Q", _cpy_struct.pack(">d", value))[0]
    actual = pcc_struct.unpack(">Q", pcc_struct.pack(">d", value))[0]
    assert actual == expected, (
        f"value={value}: pcc=0x{actual:016X}, cpython=0x{expected:016X}"
    )


@pytest.mark.parametrize("value", [
    1.0, 11.1, 3.141592653589793,
])
def test_ir_py_float32_round_pattern(pcc_struct, value):
    """ir.py's float32-rounding pattern:
    ``unpack('>f', pack('>f', f))[0]``."""
    expected = _cpy_struct.unpack(">f", _cpy_struct.pack(">f", value))[0]
    actual = pcc_struct.unpack(">f", pcc_struct.pack(">f", value))[0]
    assert actual == expected
