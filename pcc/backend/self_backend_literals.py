from __future__ import annotations

"""Low-level LLVM literal classifiers shared without parser dependencies."""


_HEX_DIGITS = "0123456789abcdefABCDEF"


def _without_trailing_newline(text: str) -> str:
    if text.endswith("\n"):
        return text[:-1]
    return text


def _is_int_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if body.startswith("-"):
        body = body[1:]
    return body.isdecimal()


def _is_hex_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if not body.startswith("0x") or len(body) < 3:
        return False
    return body[2:].strip(_HEX_DIGITS) == ""


def _is_float_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if body.startswith("-"):
        body = body[1:]
    exponent_at = body.find("e")
    if exponent_at < 0:
        exponent_at = body.find("E")
    if exponent_at >= 0:
        exponent = body[exponent_at + 1 :]
        body = body[:exponent_at]
        if exponent.startswith("-") or exponent.startswith("+"):
            exponent = exponent[1:]
        if not exponent.isdecimal():
            return False
    dot_at = body.find(".")
    if dot_at < 0:
        return body.isdecimal()
    whole = body[:dot_at]
    fraction = body[dot_at + 1 :]
    if whole and not whole.isdecimal():
        return False
    if fraction and not fraction.isdecimal():
        return False
    return bool(whole) or bool(fraction)


def const_int_from_value(value: str) -> int | None:
    if value == "false":
        return 0
    if value == "true":
        return 1
    if _is_int_token(value):
        return int(value)
    return None


def is_hex_literal(value: str) -> bool:
    return _is_hex_token(value)


def is_float_literal(value: str) -> bool:
    return _is_float_token(value) and not value.startswith(".")


def is_aggregate_literal_value(value: str) -> bool:
    return value.startswith("{") or value.startswith("[") or value.startswith("<")


__all__ = [
    "const_int_from_value",
    "is_aggregate_literal_value",
    "is_float_literal",
    "is_hex_literal",
]
