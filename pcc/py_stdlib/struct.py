"""Finite ``struct`` provider for pcc-owned compiler/runtime metadata.

The owned surface is the explicit-standard-layout integer/bytes subset used by
pcc's native object, ELF and precise-stackmap codecs.  Native alignment,
pointer-sized fields and floating-point formats are deliberately rejected
until their target ABI and shared float-bit implementation are wired into this
provider.  Unsupported shapes fail before producing partial bytes.
"""
from __future__ import annotations


class error(Exception):
    pass


_SIZES = {
    "x": 1,
    "c": 1,
    "b": 1,
    "B": 1,
    "?": 1,
    "h": 2,
    "H": 2,
    "i": 4,
    "I": 4,
    "l": 4,
    "L": 4,
    "q": 8,
    "Q": 8,
    "s": 1,
}


def _parse_format(fmt: str):
    if not isinstance(fmt, str):
        raise TypeError("Struct() argument 1 must be a str")
    if fmt == "":
        return "little", []
    prefix = fmt[0]
    if prefix == "<" or prefix == "=" :
        byteorder = "little"
        index = 1
    elif prefix == ">" or prefix == "!":
        byteorder = "big"
        index = 1
    elif prefix == "@":
        raise NotImplementedError(
            "native-aligned struct layouts are not owned by pcc.py_stdlib.struct"
        )
    else:
        raise NotImplementedError(
            "struct formats must use an explicit '<', '>', '!', or '=' prefix"
        )

    fields = []
    repeat = 0
    while index < len(fmt):
        ch = fmt[index]
        index += 1
        if ch == " ":
            continue
        if ch >= "0" and ch <= "9":
            repeat = repeat * 10 + (ord(ch) - ord("0"))
            continue
        if ch not in _SIZES:
            if ch in "efdPnN":
                raise NotImplementedError(
                    "struct format code " + repr(ch) + " is not owned yet"
                )
            raise error("bad char in struct format: " + repr(ch))
        count = repeat if repeat != 0 else 1
        fields.append((ch, count))
        repeat = 0
    if repeat != 0:
        raise error("repeat count given without format specifier")
    return byteorder, fields


def _format_size(fields) -> int:
    total = 0
    for ch, count in fields:
        total += _SIZES[ch] * count
    return total


def _integer_shape(ch: str):
    if ch == "b":
        return 1, True
    if ch == "B" or ch == "?":
        return 1, False
    if ch == "h":
        return 2, True
    if ch == "H":
        return 2, False
    if ch == "i" or ch == "l":
        return 4, True
    if ch == "I" or ch == "L":
        return 4, False
    if ch == "q":
        return 8, True
    if ch == "Q":
        return 8, False
    raise error("not an integer format code: " + repr(ch))


def _pack_fields(byteorder: str, fields, values) -> bytes:
    expected_values = 0
    for ch, count in fields:
        if ch == "x":
            continue
        if ch == "s":
            expected_values += 1
        else:
            expected_values += count
    if len(values) != expected_values:
        raise error(
            "pack expected "
            + str(expected_values)
            + " items for packing (got "
            + str(len(values))
            + ")"
        )

    out = b""
    value_index = 0
    for ch, count in fields:
        if ch == "x":
            out += b"\x00" * count
            continue
        if ch == "s":
            raw = values[value_index]
            value_index += 1
            if not isinstance(raw, (bytes, bytearray)):
                raise error("argument for 's' must be a bytes object")
            raw = bytes(raw)
            out += raw[:count]
            if len(raw) < count:
                out += b"\x00" * (count - len(raw))
            continue
        item_index = 0
        while item_index < count:
            value = values[value_index]
            value_index += 1
            item_index += 1
            if ch == "c":
                if not isinstance(value, (bytes, bytearray)) or len(value) != 1:
                    raise error("char format requires a bytes object of length 1")
                out += bytes(value)
                continue
            width, signed = _integer_shape(ch)
            if ch == "?":
                numeric = 1 if bool(value) else 0
            else:
                try:
                    numeric = int(value)
                except (TypeError, ValueError) as exc:
                    raise error("required argument is not an integer") from exc
            if width == 1:
                modulus = 0x100
            elif width == 2:
                modulus = 0x10000
            elif width == 4:
                modulus = 0x100000000
            else:
                modulus = 0x10000000000000000
            if signed:
                minimum = -(modulus // 2)
                maximum = modulus // 2 - 1
            else:
                minimum = 0
                maximum = modulus - 1
            if numeric < minimum or numeric > maximum:
                raise error("argument out of range")
            # The owned int.to_bytes lowering is the unsigned two-argument
            # form.  Convert an admitted negative signed value to its finite
            # two's-complement residue first; this also keeps signed packing
            # independent of a CPython keyword-call fallback.
            encoded = numeric + modulus if numeric < 0 else numeric
            out += encoded.to_bytes(width, byteorder)
    return out


def _normalize_offset(buffer_len: int, offset: int) -> int:
    offset = int(offset)
    if offset < 0:
        offset += buffer_len
    if offset < 0 or offset > buffer_len:
        raise error("offset out of range")
    return offset


def _unpack_fields(byteorder: str, fields, buffer, offset: int):
    raw = bytes(buffer)
    offset = _normalize_offset(len(raw), offset)
    size = _format_size(fields)
    if offset + size > len(raw):
        raise error(
            "unpack_from requires a buffer of at least "
            + str(offset + size)
            + " bytes"
        )
    values = []
    cursor = offset
    for ch, count in fields:
        if ch == "x":
            cursor += count
            continue
        if ch == "s":
            values.append(raw[cursor : cursor + count])
            cursor += count
            continue
        item_index = 0
        while item_index < count:
            item_index += 1
            if ch == "c":
                values.append(raw[cursor : cursor + 1])
                cursor += 1
                continue
            width, signed = _integer_shape(ch)
            numeric = int.from_bytes(
                raw[cursor : cursor + width],
                byteorder,
            )
            if signed:
                if width == 1:
                    modulus = 0x100
                elif width == 2:
                    modulus = 0x10000
                elif width == 4:
                    modulus = 0x100000000
                else:
                    modulus = 0x10000000000000000
                if numeric >= modulus // 2:
                    numeric -= modulus
            cursor += width
            values.append(bool(numeric) if ch == "?" else numeric)
    return tuple(values)


class Struct:
    def __init__(self, fmt: str):
        self.format = fmt
        self._byteorder, self._fields = _parse_format(fmt)
        self.size = _format_size(self._fields)

    def pack(self, *values) -> bytes:
        return _pack_fields(self._byteorder, self._fields, values)

    def unpack(self, buffer) -> tuple:
        raw = bytes(buffer)
        if len(raw) != self.size:
            raise error(
                "unpack requires a buffer of " + str(self.size) + " bytes"
            )
        return _unpack_fields(self._byteorder, self._fields, raw, 0)

    def unpack_from(self, buffer, offset: int = 0) -> tuple:
        return _unpack_fields(self._byteorder, self._fields, buffer, offset)

    def pack_into(self, buffer, offset: int, *values) -> None:
        payload = self.pack(*values)
        offset = _normalize_offset(len(buffer), offset)
        if offset + len(payload) > len(buffer):
            raise error(
                "pack_into requires a buffer of at least "
                + str(offset + len(payload))
                + " bytes"
            )
        index = 0
        while index < len(payload):
            buffer[offset + index] = payload[index]
            index += 1


def calcsize(fmt: str) -> int:
    return Struct(fmt).size


def pack(fmt: str, *values) -> bytes:
    return Struct(fmt).pack(*values)


def unpack(fmt: str, data: bytes) -> tuple:
    return Struct(fmt).unpack(data)


def pack_into(fmt: str, buffer, offset: int, *values) -> None:
    Struct(fmt).pack_into(buffer, offset, *values)


def unpack_from(fmt: str, buffer, offset: int = 0) -> tuple:
    return Struct(fmt).unpack_from(buffer, offset)
