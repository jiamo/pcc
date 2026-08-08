"""Focused contracts for pcc's indexed object/link fast path."""

from __future__ import annotations

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_link import (
    LinkError,
    link_relocatable,
    link_relocatable_native,
)
from pcc.backend.macho_obj import (
    DATA_SECTION_FLAGS,
    TEXT_SECTION_FLAGS,
    ZEROFILL_SECTION_FLAGS,
    DataInCodeRegion,
    Relocation,
    Section,
    TextSymbol,
    emit_object,
)
from pcc.backend.native_object import (
    MAGIC,
    NativeObject,
    NativeObjectError,
    NativeRelocation,
    NativeSection,
    NativeSymbol,
    decode_native_object,
    encode_native_object,
)


_RET = b"\xc0\x03\x5f\xd6"
_BL_PLACEHOLDER = b"\x00\x00\x00\x94"


def _caller_sections() -> list[Section]:
    return [Section(
        sectname="__text",
        segname="__TEXT",
        data=_BL_PLACEHOLDER + _RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_main", 0),),
        relocations=(Relocation(
            offset=0,
            symbol="_helper",
            type=spec.ARM64_RELOC_BRANCH26,
            pcrel=True,
        ),),
    )]


def _helper_sections() -> list[Section]:
    return [Section(
        sectname="__text",
        segname="__TEXT",
        data=_RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_helper", 0),),
    )]


def test_native_codec_stores_each_symbol_once_and_relocations_by_index() -> None:
    native = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    payload = encode_native_object(native)
    restored = decode_native_object(payload)

    assert payload.startswith(MAGIC)
    assert payload.count(b"_helper") == 1
    assert restored == native
    relocation = restored.sections[0].relocations[0]
    assert relocation.symbol_index == 1
    assert restored.symbols[relocation.symbol_index].name == "_helper"


def test_native_final_link_never_parses_an_internal_macho_string_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    helper = NativeObject.from_sections(_helper_sections())

    def unexpected_parse(_data):
        raise AssertionError("pcc-native input entered the Mach-O parser")

    with monkeypatch.context() as patch:
        patch.setattr(spec, "parse_object", unexpected_parse)
        image = link_executable([
            caller,
            helper,
        ])

    parsed = spec.parse_object(image)
    assert parsed.header["filetype"] == spec.MH_EXECUTE
    assert {symbol["name"] for symbol in parsed.symbols()} >= {
        "_main", "_helper",
    }


def test_internal_and_external_object_boundaries_produce_the_same_image() -> None:
    caller_sections = _caller_sections()
    helper_sections = _helper_sections()
    internal = link_executable([
        NativeObject.from_sections(caller_sections, undefined=["_helper"]),
        NativeObject.from_sections(helper_sections),
    ])
    external = link_executable([
        emit_object(caller_sections, undefined=["_helper"]),
        emit_object(helper_sections),
    ])

    assert internal == external


def test_indexed_view_matches_macho_for_sections_symbols_and_relocations() -> None:
    sections = [
        Section(
            sectname="__text",
            segname="__TEXT",
            data=b"\0" * 8 + b"data",
            align_log2=2,
            flags=TEXT_SECTION_FLAGS,
            symbols=(TextSymbol("_main", 0),),
            relocations=(
                Relocation(
                    0,
                    "_global",
                    spec.ARM64_RELOC_PAGE21,
                    True,
                    addend=8,
                ),
                Relocation(
                    4,
                    "_global",
                    spec.ARM64_RELOC_PAGEOFF12,
                    False,
                    addend=8,
                ),
            ),
            data_in_code=(DataInCodeRegion(8, 4),),
        ),
        Section(
            sectname="__data",
            segname="__DATA",
            data=b"\0" * 16,
            align_log2=3,
            flags=DATA_SECTION_FLAGS,
            symbols=(TextSymbol("_global", 0),),
            relocations=(Relocation(
                8,
                "",
                spec.ARM64_RELOC_UNSIGNED,
                False,
                length=3,
                section=("__DATA", "__bss"),
                target_offset=0,
            ),),
        ),
        Section(
            sectname="__bss",
            segname="__DATA",
            align_log2=3,
            flags=ZEROFILL_SECTION_FLAGS,
            symbols=(TextSymbol("_scratch", 0),),
            zerofill_size=8,
        ),
    ]
    native = decode_native_object(encode_native_object(
        NativeObject.from_sections(sections)
    )).link_view()
    macho = spec.parse_object(emit_object(sections))

    native_sections = native.sections()
    macho_sections = macho.sections()
    section_fields = (
        "segname_str",
        "sectname_str",
        "flags",
        "align",
        "addr",
        "size",
        "nreloc",
    )
    assert [
        tuple(section[field] for field in section_fields)
        for section in native_sections
    ] == [
        tuple(section[field] for field in section_fields)
        for section in macho_sections
    ]
    for native_section, macho_section in zip(
        native_sections,
        macho_sections,
        strict=True,
    ):
        if (
            native_section["flags"] & spec.SECTION_TYPE
        ) == spec.S_ZEROFILL:
            continue
        native_payload = native.data[
            native_section["offset"]:
            native_section["offset"] + native_section["size"]
        ]
        macho_payload = macho.data[
            macho_section["offset"]:
            macho_section["offset"] + macho_section["size"]
        ]
        assert native_payload == macho_payload
        assert native.relocations(native_section) == macho.relocations(
            macho_section
        )

    symbol_fields = ("name", "n_type", "n_sect", "n_desc", "n_value")
    assert [
        tuple(symbol[field] for field in symbol_fields)
        for symbol in native.symbols()
    ] == [
        tuple(symbol[field] for field in symbol_fields)
        for symbol in macho.symbols()
    ]
    assert native.data_in_code() == macho.data_in_code()


def test_relocatable_public_boundary_still_materialises_standard_macho() -> None:
    native = NativeObject.from_sections(_helper_sections())
    merged_native = link_relocatable_native([native])
    external = link_relocatable([native])

    assert isinstance(merged_native, NativeObject)
    assert not external.startswith(MAGIC)
    parsed = spec.parse_object(external)
    assert parsed.header["filetype"] == spec.MH_OBJECT
    assert [symbol["name"] for symbol in parsed.symbols()] == ["_helper"]


def test_encoded_native_transport_requires_explicit_decode_before_link() -> None:
    payload = encode_native_object(
        NativeObject.from_sections(_helper_sections())
    )

    with pytest.raises(LinkError, match="decode it explicitly"):
        link_relocatable([payload])

    external = link_relocatable([decode_native_object(payload)])
    assert not external.startswith(MAGIC)
    assert spec.parse_object(external).header["filetype"] == spec.MH_OBJECT


def test_native_object_rejects_out_of_range_indices_and_bad_framing() -> None:
    with pytest.raises(NativeObjectError, match="symbol index.*out of range"):
        NativeObject(
            sections=(NativeSection(
                segname="__TEXT",
                sectname="__text",
                flags=(
                    spec.S_REGULAR
                    | spec.S_ATTR_PURE_INSTRUCTIONS
                    | spec.S_ATTR_SOME_INSTRUCTIONS
                ),
                align_log2=2,
                data=_BL_PLACEHOLDER + _RET,
                relocations=(NativeRelocation(
                    offset=0,
                    symbol_index=99,
                    type=spec.ARM64_RELOC_BRANCH26,
                    pcrel=True,
                ),),
            ),),
            symbols=(NativeSymbol("_main", 1, 0, True),),
        )

    valid = encode_native_object(
        NativeObject.from_sections(_helper_sections())
    )
    with pytest.raises(NativeObjectError, match="trailing bytes"):
        decode_native_object(valid + b"unexpected")
    with pytest.raises(NativeObjectError, match="truncated"):
        decode_native_object(valid[:-1])
