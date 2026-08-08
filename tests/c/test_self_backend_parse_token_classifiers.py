"""Differential: the IR token classifiers must equal the regexes they replaced.

`_is_int_token` / `_is_hex_token` / `_is_float_token` replaced three compiled
patterns that ran 7.8M of the 11.0M regex calls needed to parse one 27 MB
module (the pcc-Python regex engine made that per-call cost dominate
`pcc1 -> pcc2`).  They are only a valid substitution while they agree with the
retired patterns on every token shape the IR parser can see, so keep the
originals here as the oracle.
"""

from __future__ import annotations

import re

import pytest

from pcc.backend.self_backend_parse import (
    _is_float_token,
    _is_hex_token,
    _is_int_token,
)

_INT_RE = re.compile(r"^-?\d+$")
_HEX_RE = re.compile(r"^0x[0-9A-Fa-f]+$")
_FLOAT_RE = re.compile(
    r"^-?(?:(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)$"
)

_TOKENS = [
    "", "-", "+", ".", "-.", "0", "7", "-7", "007", "12345678901234567890",
    "1_0", "1 ", " 1", "1\n", "-1\n", "0x\n", "1.5\n",
    "0x0", "0xff", "0xFF", "0xDEADbeef", "0x", "0X10", "00x1", "0x1g", "-0x1",
    "1.", ".5", "1.5", "-1.5", "1.5e3", "1.5E3", "1.5e+3", "1.5e-3", "1e10",
    "-1e10", "1.e5", ".5e5", "1.2.3", "1e", "1e+", "e5", "1.5f", "--1", "1-",
    "nan", "inf", "-inf", "true", "null", "undef", "%1", "@g", "i64", "0.0",
    "-0.0", "1e1000", "0b101", "1E-0", ".", "..", "1..2", "+1", "+1.5",
    # non-ASCII digits: `\d`/isdecimal both accept these, hex/float must not
    "١٢٣", "１２３", "²",
]


@pytest.mark.parametrize("token", _TOKENS)
def test_classifiers_match_retired_regexes(token):
    assert _is_int_token(token) == bool(_INT_RE.match(token)), token
    assert _is_hex_token(token) == bool(_HEX_RE.match(token)), token
    assert _is_float_token(token) == bool(_FLOAT_RE.match(token)), token


def test_classifiers_match_on_generated_token_space():
    alphabet = "0-.xXeE+7aF"
    tokens = [""]
    for first in alphabet:
        tokens.append(first)
        for second in alphabet:
            tokens.append(first + second)
            for third in alphabet:
                tokens.append(first + second + third)
    mismatches = []
    for token in tokens:
        if _is_int_token(token) != bool(_INT_RE.match(token)):
            mismatches.append(("int", token))
        if _is_hex_token(token) != bool(_HEX_RE.match(token)):
            mismatches.append(("hex", token))
        if _is_float_token(token) != bool(_FLOAT_RE.match(token)):
            mismatches.append(("float", token))
    assert not mismatches, mismatches[:20]
