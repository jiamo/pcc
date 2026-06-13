"""Small native-compilable subset of :mod:`ast`.

The self-host package path currently needs ``literal_eval`` for integer shape
specifications.  This provider deliberately accepts only integer literals and
flat tuples of integers; unsupported Python literal syntax raises
``ValueError`` instead of being evaluated approximately.
"""
from __future__ import annotations


def _malformed_literal():
    raise ValueError("malformed node or string")


def _parse_integer(text):
    value = text.strip()
    if value == "":
        _malformed_literal()
    index = 0
    if value[0] == "+" or value[0] == "-":
        index = 1
    if index == len(value):
        _malformed_literal()
    while index < len(value):
        ch = value[index]
        if ch < "0" or ch > "9":
            _malformed_literal()
        index += 1
    return int(value)


def literal_eval(node_or_string):
    """Evaluate an integer or flat integer tuple without host Python."""
    if not isinstance(node_or_string, str):
        _malformed_literal()

    text = node_or_string.strip()
    if text == "":
        _malformed_literal()

    parenthesized = text[0] == "("
    if parenthesized:
        if text[-1] != ")":
            _malformed_literal()
        text = text[1:-1].strip()
        if text == "":
            return ()
    elif text[-1] == ")":
        _malformed_literal()

    if "," not in text:
        return _parse_integer(text)

    parts = text.split(",")
    values = []
    index = 0
    while index < len(parts):
        part = parts[index].strip()
        if part == "":
            if index != len(parts) - 1:
                _malformed_literal()
        else:
            values.append(_parse_integer(part))
        index += 1
    if not values:
        _malformed_literal()
    return tuple(values)
