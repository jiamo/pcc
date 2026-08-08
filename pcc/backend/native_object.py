"""Indexed internal object representation for pcc-to-pcc links.

LINK-P2-NATIVE-OBJ-FASTPATH: keep standard Mach-O at external boundaries while
the owned assembler/linker path carries already-resolved symbol indices.

``macho_obj.Section`` is the authoring model and Mach-O remains the external
object-file ABI.  Between pcc's assembler and linker, however, serialising an
``MH_OBJECT`` only to parse its load commands, nlist/string table, and
relocation table again is pure overhead.  ``NativeObject`` is that internal
seam: symbols occur once, relocations carry validated integer indices, and a
small versioned codec permits cache/process transport without becoming a
platform object format.

The codec is deliberately not accepted as Mach-O.  Conversion to a standard
object is explicit through :meth:`NativeObject.to_macho`; final executable
images remain ordinary Mach-O and external ``.o``/``.a`` inputs keep using the
normal parser and validation boundary.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import macho_spec as spec
from .macho_obj import (
    DataInCodeRegion,
    MachOEmitError,
    Relocation,
    Section,
    TextSymbol,
    _validate_relocation,
    _validate_section,
    emit_object,
)


class NativeObjectError(Exception):
    """The internal object is malformed or outside the proven object model."""


MAGIC = b"PCCNOBJ\x01"
_NONE_INDEX = 0xFFFFFFFF
_MAX_COUNT = 8_000_000  # merged pcc compiler closure exceeds 1M relocations
_MAX_NAME_BYTES = 1_048_576

_HEADER = struct.Struct("<8sII")
_U32 = struct.Struct("<I")
_SYMBOL = struct.Struct("<IQI")
_SECTION = struct.Struct("<IIQQII")
_RELOCATION = struct.Struct("<QIIBB2xqIIq")
_DATA_IN_CODE = struct.Struct("<QII")

_SYMBOL_EXTERNAL = 1
_SYMBOL_PRIVATE_EXTERNAL = 2
_UINT64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class NativeSymbol:
    name: str
    # Zero denotes undefined; defined sections use Mach-O's one-based index.
    section_index: int
    offset: int
    external: bool
    private_external: bool = False


@dataclass(frozen=True)
class NativeRelocation:
    offset: int
    # Indices below are zero-based indices into ``NativeObject.symbols``.
    symbol_index: int | None
    type: int
    pcrel: bool
    length: int = 2
    addend: int = 0
    # Section targets retain Mach-O's one-based section numbering.
    target_section_index: int | None = None
    minuend_index: int | None = None
    target_offset: int | None = None


@dataclass(frozen=True)
class NativeSection:
    segname: str
    sectname: str
    flags: int
    align_log2: int
    data: bytes
    relocations: tuple[NativeRelocation, ...] = ()
    zerofill_size: int = 0
    data_in_code: tuple[DataInCodeRegion, ...] = ()

    @property
    def vm_size(self) -> int:
        section_type = self.flags & spec.SECTION_TYPE
        if section_type in (spec.S_ZEROFILL, 0x12):
            return self.zerofill_size
        return len(self.data)


@dataclass(frozen=True)
class NativeObject:
    sections: tuple[NativeSection, ...]
    symbols: tuple[NativeSymbol, ...]

    def __post_init__(self) -> None:
        _validate_native_object(self)

    @classmethod
    def from_sections(
        cls,
        sections: list[Section] | tuple[Section, ...],
        *,
        undefined: list[str] | tuple[str, ...] = (),
    ) -> "NativeObject":
        source_sections = tuple(sections)
        undefined_names = tuple(undefined)
        _validate_source_sections(source_sections, undefined_names)

        defined: list[tuple[TextSymbol, int]] = []
        for section_index, section in enumerate(source_sections, start=1):
            for symbol in section.symbols:
                defined.append((symbol, section_index))
        defined.sort(key=lambda pair: (pair[1], pair[0].offset))
        local_defined = [pair for pair in defined if not pair[0].external]
        external_defined = [pair for pair in defined if pair[0].external]

        symbols: list[NativeSymbol] = []
        for symbol, section_index in local_defined + external_defined:
            symbols.append(NativeSymbol(
                name=symbol.name,
                section_index=section_index,
                offset=symbol.offset,
                external=symbol.external,
                private_external=symbol.private_external,
            ))
        for name in sorted(undefined_names):
            symbols.append(NativeSymbol(
                name=name,
                section_index=0,
                offset=0,
                external=True,
            ))

        symbol_index = {symbol.name: index for index, symbol in enumerate(symbols)}
        section_index = {
            (section.segname, section.sectname): index
            for index, section in enumerate(source_sections, start=1)
        }
        native_sections: list[NativeSection] = []
        for section in source_sections:
            relocations: list[NativeRelocation] = []
            for relocation in section.relocations:
                is_section_target = relocation.section is not None
                relocations.append(NativeRelocation(
                    offset=relocation.offset,
                    symbol_index=(
                        None if is_section_target
                        else symbol_index[relocation.symbol]
                    ),
                    type=relocation.type,
                    pcrel=relocation.pcrel,
                    length=relocation.length,
                    addend=relocation.addend,
                    target_section_index=(
                        section_index[relocation.section]
                        if is_section_target else None
                    ),
                    minuend_index=(
                        symbol_index[relocation.minuend]
                        if relocation.minuend is not None else None
                    ),
                    target_offset=relocation.target_offset,
                ))
            native_sections.append(NativeSection(
                segname=section.segname,
                sectname=section.sectname,
                flags=section.flags,
                align_log2=section.align_log2,
                data=bytes(section.data),
                relocations=tuple(relocations),
                zerofill_size=section.zerofill_size,
                data_in_code=tuple(section.data_in_code),
            ))
        return cls(tuple(native_sections), tuple(symbols))

    def to_sections(self) -> tuple[list[Section], list[str]]:
        symbols_by_section: list[list[TextSymbol]] = [
            [] for _section in self.sections
        ]
        undefined: list[str] = []
        for symbol in self.symbols:
            if symbol.section_index == 0:
                undefined.append(symbol.name)
                continue
            symbols_by_section[symbol.section_index - 1].append(TextSymbol(
                name=symbol.name,
                offset=symbol.offset,
                external=symbol.external,
                private_external=symbol.private_external,
            ))

        sections: list[Section] = []
        for source_index, native_section in enumerate(self.sections):
            relocations: list[Relocation] = []
            for native_relocation in native_section.relocations:
                target_section = None
                symbol_name = ""
                if native_relocation.target_section_index is not None:
                    target = self.sections[
                        native_relocation.target_section_index - 1
                    ]
                    target_section = (target.segname, target.sectname)
                else:
                    assert native_relocation.symbol_index is not None
                    symbol_name = self.symbols[
                        native_relocation.symbol_index
                    ].name
                minuend = None
                if native_relocation.minuend_index is not None:
                    minuend = self.symbols[
                        native_relocation.minuend_index
                    ].name
                relocations.append(Relocation(
                    offset=native_relocation.offset,
                    symbol=symbol_name,
                    type=native_relocation.type,
                    pcrel=native_relocation.pcrel,
                    length=native_relocation.length,
                    addend=native_relocation.addend,
                    section=target_section,
                    minuend=minuend,
                    target_offset=native_relocation.target_offset,
                ))
            sections.append(Section(
                sectname=native_section.sectname,
                segname=native_section.segname,
                data=native_section.data,
                align_log2=native_section.align_log2,
                flags=native_section.flags,
                symbols=tuple(symbols_by_section[source_index]),
                relocations=tuple(relocations),
                zerofill_size=native_section.zerofill_size,
                data_in_code=native_section.data_in_code,
            ))
        return sections, undefined

    def to_macho(self) -> bytes:
        """Materialise a standard MH_OBJECT at an explicit external boundary."""
        sections, undefined = self.to_sections()
        return emit_object(sections, undefined=undefined)

    def link_view(self) -> "NativeObjectView":
        """Return the cached-table interface consumed by the Mach-O linker."""
        return NativeObjectView(self)


def _validate_source_sections(
    sections: tuple[Section, ...],
    undefined: tuple[str, ...],
) -> None:
    try:
        if not sections:
            raise MachOEmitError("at least one section is required")
        if len({(s.segname, s.sectname) for s in sections}) != len(sections):
            raise MachOEmitError("duplicate section names")
        seen: set[str] = set()
        seen_zerofill_segments: set[str] = set()
        for section in sections:
            _validate_section(section)
            if section.is_zerofill:
                seen_zerofill_segments.add(section.segname)
            elif section.segname in seen_zerofill_segments:
                raise MachOEmitError(
                    f"content section {section.sectname} after a zerofill "
                    "section"
                )
            for symbol in section.symbols:
                if "\0" in symbol.name:
                    raise MachOEmitError(
                        f"symbol name {symbol.name!r} contains NUL"
                    )
                if symbol.name in seen:
                    raise MachOEmitError(
                        f"duplicate symbol {symbol.name!r}"
                    )
                seen.add(symbol.name)
        if not seen:
            raise MachOEmitError("at least one defined symbol is required")
        for name in undefined:
            if not isinstance(name, str) or not name or not name.isascii():
                raise MachOEmitError(f"bad undefined symbol name {name!r}")
            if "\0" in name:
                raise MachOEmitError(f"symbol name {name!r} contains NUL")
            if name in seen:
                raise MachOEmitError(
                    f"symbol {name!r} both defined and undefined"
                )
            seen.add(name)
        section_by_name = {
            (section.segname, section.sectname): section
            for section in sections
        }
        known = {name: index for index, name in enumerate(seen)}
        for section in sections:
            relocation_offsets: set[int] = set()
            for relocation in section.relocations:
                _validate_relocation(
                    section, relocation, known, section_by_name,
                )
                if relocation.offset in relocation_offsets:
                    raise MachOEmitError(
                        "multiple relocation requests at offset "
                        f"{relocation.offset} in {section.segname},"
                        f"{section.sectname}"
                    )
                relocation_offsets.add(relocation.offset)
    except MachOEmitError as exc:
        raise NativeObjectError(str(exc)) from exc


def _validate_native_object(obj: NativeObject) -> None:
    if not isinstance(obj, NativeObject):
        raise NativeObjectError("expected a NativeObject instance")
    if not isinstance(obj.sections, tuple):
        raise NativeObjectError("native object sections must be a tuple")
    if not obj.sections or len(obj.sections) > _MAX_COUNT:
        raise NativeObjectError("native object has an invalid section count")
    if not isinstance(obj.symbols, tuple):
        raise NativeObjectError("native object symbols must be a tuple")
    if not obj.symbols or len(obj.symbols) > _MAX_COUNT:
        raise NativeObjectError("native object has an invalid symbol count")

    seen_names: set[str] = set()
    saw_external_definition = False
    saw_undefined = False
    last_defined_key: dict[bool, tuple[int, int] | None] = {
        False: None,
        True: None,
    }
    last_undefined_name: str | None = None
    for symbol in obj.symbols:
        if not isinstance(symbol, NativeSymbol):
            raise NativeObjectError("native object has a non-symbol entry")
        _validate_name(symbol.name, "symbol")
        if symbol.name in seen_names:
            raise NativeObjectError(
                f"native object duplicates symbol {symbol.name!r}"
            )
        seen_names.add(symbol.name)
        if not isinstance(symbol.section_index, int) or isinstance(
            symbol.section_index, bool
        ):
            raise NativeObjectError("symbol section index must be an integer")
        if not isinstance(symbol.offset, int) or isinstance(symbol.offset, bool):
            raise NativeObjectError("symbol offset must be an integer")
        if not 0 <= symbol.offset <= _UINT64_MAX:
            raise NativeObjectError("symbol offset is outside uint64 range")
        if not isinstance(symbol.external, bool) or not isinstance(
            symbol.private_external, bool
        ):
            raise NativeObjectError("symbol visibility flags must be boolean")
        if symbol.section_index == 0:
            saw_undefined = True
            if not symbol.external or symbol.private_external or symbol.offset != 0:
                raise NativeObjectError(
                    f"undefined symbol {symbol.name!r} has invalid attributes"
                )
            if (
                last_undefined_name is not None
                and symbol.name < last_undefined_name
            ):
                raise NativeObjectError(
                    "undefined symbols are not in canonical name order"
                )
            last_undefined_name = symbol.name
        else:
            if saw_undefined:
                raise NativeObjectError(
                    "defined symbol appears after the undefined partition"
                )
            if not 1 <= symbol.section_index <= len(obj.sections):
                raise NativeObjectError(
                    f"symbol {symbol.name!r} names section "
                    f"{symbol.section_index}"
                )
            if symbol.private_external and not symbol.external:
                raise NativeObjectError(
                    f"private-extern symbol {symbol.name!r} is not external"
                )
            if symbol.external:
                saw_external_definition = True
            elif saw_external_definition:
                raise NativeObjectError(
                    "local symbol appears after the external partition"
                )
            defined_key = (symbol.section_index, symbol.offset)
            previous_key = last_defined_key[symbol.external]
            if previous_key is not None and defined_key < previous_key:
                visibility = "external" if symbol.external else "local"
                raise NativeObjectError(
                    f"{visibility} symbols are not in canonical section order"
                )
            last_defined_key[symbol.external] = defined_key

    section_names: set[tuple[str, str]] = set()
    vm_cursor = 0
    for section in obj.sections:
        if not isinstance(section, NativeSection):
            raise NativeObjectError("native object has a non-section entry")
        _validate_name(section.segname, "segment")
        _validate_name(section.sectname, "section")
        if len(section.segname.encode("ascii")) > 16 or len(
            section.sectname.encode("ascii")
        ) > 16:
            raise NativeObjectError("segment/section name exceeds 16 bytes")
        key = (section.segname, section.sectname)
        if key in section_names:
            raise NativeObjectError(f"native object duplicates section {key!r}")
        section_names.add(key)
        _validate_uint(section.flags, 32, "section flags")
        _validate_uint(section.align_log2, 32, "section alignment")
        _validate_uint(section.zerofill_size, 64, "section zerofill size")
        if not isinstance(section.data, bytes):
            raise NativeObjectError("native section payload must be bytes")
        if not isinstance(section.relocations, tuple):
            raise NativeObjectError("native section relocations must be a tuple")
        if len(section.relocations) > _MAX_COUNT:
            raise NativeObjectError("native section has too many relocations")
        if not isinstance(section.data_in_code, tuple):
            raise NativeObjectError("native data-in-code entries must be a tuple")
        if len(section.data_in_code) > _MAX_COUNT:
            raise NativeObjectError("native section has too many data-in-code entries")
        for region in section.data_in_code:
            if not isinstance(region, DataInCodeRegion):
                raise NativeObjectError("native object has a bad data-in-code entry")

        # Mach-O's section addresses and sizes are uint64. Validate the whole
        # internal layout here so a malformed cache artifact cannot escape as
        # a late struct.pack OverflowError at the external boundary.
        if section.align_log2 > 31:
            raise NativeObjectError("native section alignment exceeds 31")
        vm_cursor = _align_up(vm_cursor, section.align_log2)
        if vm_cursor > _UINT64_MAX - section.vm_size:
            raise NativeObjectError("native object virtual layout exceeds uint64")
        vm_cursor += section.vm_size

        for relocation in section.relocations:
            if not isinstance(relocation, NativeRelocation):
                raise NativeObjectError("native object has a bad relocation entry")
            _validate_uint(relocation.offset, 64, "relocation offset")
            _validate_uint(relocation.type, 32, "relocation type")
            _validate_uint(relocation.length, 8, "relocation length")
            _validate_sint(relocation.addend, 64, "relocation addend")
            if not isinstance(relocation.pcrel, bool):
                raise NativeObjectError("relocation pcrel must be boolean")
            if relocation.target_offset is not None:
                _validate_sint(
                    relocation.target_offset,
                    64,
                    "relocation target offset",
                )
            has_symbol = relocation.symbol_index is not None
            has_section = relocation.target_section_index is not None
            if has_symbol == has_section:
                raise NativeObjectError(
                    "relocation must name exactly one symbol or section"
                )
            if has_symbol:
                _validate_index(
                    relocation.symbol_index,
                    len(obj.symbols),
                    "relocation symbol",
                )
            if has_section:
                _validate_index(
                    relocation.target_section_index,
                    len(obj.sections),
                    "relocation target section",
                    one_based=True,
                )
            if relocation.minuend_index is not None:
                _validate_index(
                    relocation.minuend_index,
                    len(obj.symbols),
                    "relocation minuend symbol",
                )

    sections, undefined = obj.to_sections()
    _validate_source_sections(tuple(sections), tuple(undefined))


def _validate_index(
    value: object,
    count: int,
    context: str,
    *,
    one_based: bool = False,
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise NativeObjectError(f"{context} index must be an integer")
    lower = 1 if one_based else 0
    upper = count + 1 if one_based else count
    if not lower <= value < upper:
        raise NativeObjectError(f"{context} index {value} is out of range")


def _validate_uint(value: object, bits: int, context: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise NativeObjectError(f"{context} must be an integer")
    limit = (1 << bits) - 1
    if not 0 <= value <= limit:
        raise NativeObjectError(f"{context} is outside uint{bits} range")


def _validate_sint(value: object, bits: int, context: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise NativeObjectError(f"{context} must be an integer")
    lower = -(1 << (bits - 1))
    upper = (1 << (bits - 1)) - 1
    if not lower <= value <= upper:
        raise NativeObjectError(f"{context} is outside int{bits} range")


def _validate_name(value: str, context: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or "\0" in value
    ):
        raise NativeObjectError(f"bad {context} name {value!r}")
    if len(value.encode("ascii")) > _MAX_NAME_BYTES:
        raise NativeObjectError(f"{context} name is unreasonably large")


def _align_up(value: int, align_log2: int) -> int:
    mask = (1 << align_log2) - 1
    return (value + mask) & ~mask


class NativeObjectView:
    """MachOObject-shaped view backed by indexed, already-decoded tables."""

    is_pcc_native_object = True

    def __init__(self, native: NativeObject):
        self.native = native
        addrs: list[int] = []
        vm_cursor = 0
        for section in native.sections:
            vm_cursor = _align_up(vm_cursor, section.align_log2)
            addrs.append(vm_cursor)
            vm_cursor += section.vm_size

        payloads = [bytearray(section.data) for section in native.sections]
        for section_index, section in enumerate(native.sections):
            for relocation in section.relocations:
                if relocation.target_section_index is None:
                    continue
                width = 1 << relocation.length
                start = relocation.offset
                stored = int.from_bytes(
                    payloads[section_index][start:start + width], "little"
                )
                target_index = relocation.target_section_index - 1
                target_addr = addrs[target_index]
                target_size = native.sections[target_index].vm_size
                if relocation.target_offset is not None:
                    if stored:
                        raise NativeObjectError(
                            "section-target relocation with target_offset "
                            "must have a zero-filled field"
                        )
                    target_value = target_addr + relocation.target_offset
                elif target_addr <= stored < target_addr + target_size:
                    target_value = stored
                elif 0 <= stored < target_size:
                    target_value = target_addr + stored
                else:
                    raise NativeObjectError(
                        f"section-target value {stored} is outside section "
                        f"{relocation.target_section_index}"
                    )
                if target_value >= 1 << (width * 8):
                    raise NativeObjectError(
                        "section-target value does not fit relocation width"
                    )
                payloads[section_index][start:start + width] = (
                    target_value.to_bytes(width, "little")
                )

        data = bytearray()
        self._sections: list[dict] = []
        for index, section in enumerate(native.sections):
            if section.vm_size == section.zerofill_size and section.zerofill_size:
                file_offset = 0
            else:
                file_offset = _align_up(len(data), section.align_log2)
                data.extend(b"\0" * (file_offset - len(data)))
                data.extend(payloads[index])
            self._sections.append({
                "segname_str": section.segname,
                "sectname_str": section.sectname,
                "flags": section.flags,
                "align": section.align_log2,
                "addr": addrs[index],
                "offset": file_offset,
                "size": section.vm_size,
                "nreloc": _raw_relocation_count(section),
                "_pcc_native_index": index,
            })
        self.data = bytes(data)

        self._symbols: list[dict] = []
        for symbol in native.symbols:
            if symbol.section_index:
                n_type = spec.N_SECT
                if symbol.external:
                    n_type |= spec.N_EXT
                if symbol.private_external:
                    n_type |= spec.N_PEXT
                n_value = addrs[symbol.section_index - 1] + symbol.offset
            else:
                n_type = spec.N_UNDF | spec.N_EXT
                n_value = 0
            self._symbols.append({
                "name": symbol.name,
                "n_type": n_type,
                "n_sect": symbol.section_index,
                "n_desc": 0,
                "n_value": n_value,
            })

        self._relocations = [
            _raw_relocations(section)
            for section in native.sections
        ]
        self._data_in_code: list[dict] = []
        for index, section in enumerate(native.sections):
            for region in section.data_in_code:
                self._data_in_code.append({
                    "offset": addrs[index] + region.offset,
                    "length": region.length,
                    "kind": region.kind,
                })
        self.header = {
            "filetype": spec.MH_OBJECT,
            "cputype": spec.CPU_TYPE_ARM64,
            "cpusubtype": spec.CPU_SUBTYPE_ARM64_ALL,
        }
        self.commands: tuple = ()

    def sections(self) -> list[dict]:
        return self._sections

    def symbols(self) -> list[dict]:
        return self._symbols

    def relocations(self, section: dict) -> list[dict]:
        index = section.get("_pcc_native_index")
        if not isinstance(index, int) or not 0 <= index < len(self._relocations):
            raise NativeObjectError("section does not belong to this native object")
        return self._relocations[index]

    def data_in_code(self) -> list[dict]:
        return self._data_in_code


def _raw_relocation_count(section: NativeSection) -> int:
    return len(section.relocations) + sum(
        1 for relocation in section.relocations if relocation.addend
    ) + sum(
        1 for relocation in section.relocations
        if relocation.type == spec.ARM64_RELOC_SUBTRACTOR
    )


def _raw_relocations(section: NativeSection) -> list[dict]:
    entries: list[dict] = []
    for relocation in sorted(
        section.relocations, key=lambda item: item.offset, reverse=True
    ):
        if relocation.addend:
            entries.append({
                "r_address": relocation.offset,
                "r_symbolnum": relocation.addend,
                "r_pcrel": 0,
                "r_length": relocation.length,
                "r_extern": 0,
                "r_type": spec.ARM64_RELOC_ADDEND,
            })
        if relocation.type == spec.ARM64_RELOC_SUBTRACTOR:
            assert relocation.symbol_index is not None
            assert relocation.minuend_index is not None
            entries.append({
                "r_address": relocation.offset,
                "r_symbolnum": relocation.symbol_index,
                "r_pcrel": 0,
                "r_length": relocation.length,
                "r_extern": 1,
                "r_type": spec.ARM64_RELOC_SUBTRACTOR,
            })
            entries.append({
                "r_address": relocation.offset,
                "r_symbolnum": relocation.minuend_index,
                "r_pcrel": 0,
                "r_length": relocation.length,
                "r_extern": 1,
                "r_type": spec.ARM64_RELOC_UNSIGNED,
            })
            continue
        if relocation.target_section_index is not None:
            entries.append({
                "r_address": relocation.offset,
                "r_symbolnum": relocation.target_section_index,
                "r_pcrel": 1 if relocation.pcrel else 0,
                "r_length": relocation.length,
                "r_extern": 0,
                "r_type": relocation.type,
            })
            continue
        assert relocation.symbol_index is not None
        entries.append({
            "r_address": relocation.offset,
            "r_symbolnum": relocation.symbol_index,
            "r_pcrel": 1 if relocation.pcrel else 0,
            "r_length": relocation.length,
            "r_extern": 1,
            "r_type": relocation.type,
        })
    return entries


def encode_native_object(obj: NativeObject) -> bytes:
    """Encode a validated object for cache/subprocess transport."""
    _validate_native_object(obj)
    out = bytearray(_HEADER.pack(MAGIC, len(obj.sections), len(obj.symbols)))
    for symbol in obj.symbols:
        _append_name(out, symbol.name)
        flags = (
            (_SYMBOL_EXTERNAL if symbol.external else 0)
            | (
                _SYMBOL_PRIVATE_EXTERNAL
                if symbol.private_external else 0
            )
        )
        out.extend(_SYMBOL.pack(symbol.section_index, symbol.offset, flags))
    for section in obj.sections:
        _append_name(out, section.segname)
        _append_name(out, section.sectname)
        out.extend(_SECTION.pack(
            section.flags,
            section.align_log2,
            section.zerofill_size,
            len(section.data),
            len(section.relocations),
            len(section.data_in_code),
        ))
        out.extend(section.data)
        for relocation in section.relocations:
            out.extend(_RELOCATION.pack(
                relocation.offset,
                _encode_index(relocation.symbol_index),
                relocation.type,
                1 if relocation.pcrel else 0,
                relocation.length,
                relocation.addend,
                _encode_index(relocation.target_section_index),
                _encode_index(relocation.minuend_index),
                (
                    -1 if relocation.target_offset is None
                    else relocation.target_offset
                ),
            ))
        for region in section.data_in_code:
            out.extend(_DATA_IN_CODE.pack(
                region.offset, region.length, region.kind,
            ))
    return bytes(out)


def decode_native_object(data: bytes) -> NativeObject:
    """Decode one exact, versioned pcc-native object and reject trailing data."""
    if not isinstance(data, bytes):
        raise NativeObjectError("native object payload must be bytes")
    reader = _Reader(data)
    magic, section_count, symbol_count = reader.unpack(_HEADER)
    if magic != MAGIC:
        raise NativeObjectError("bad pcc-native object magic/version")
    _validate_count(section_count, "section")
    _validate_count(symbol_count, "symbol")

    symbols: list[NativeSymbol] = []
    for _index in range(symbol_count):
        name = reader.name("symbol")
        section_index, offset, flags = reader.unpack(_SYMBOL)
        if flags & ~(_SYMBOL_EXTERNAL | _SYMBOL_PRIVATE_EXTERNAL):
            raise NativeObjectError("native symbol has unknown flag bits")
        symbols.append(NativeSymbol(
            name=name,
            section_index=section_index,
            offset=offset,
            external=bool(flags & _SYMBOL_EXTERNAL),
            private_external=bool(flags & _SYMBOL_PRIVATE_EXTERNAL),
        ))

    sections: list[NativeSection] = []
    for _index in range(section_count):
        segname = reader.name("segment")
        sectname = reader.name("section")
        (
            flags,
            align_log2,
            zerofill_size,
            data_size,
            relocation_count,
            data_in_code_count,
        ) = reader.unpack(_SECTION)
        _validate_count(relocation_count, "relocation", allow_zero=True)
        _validate_count(data_in_code_count, "data-in-code", allow_zero=True)
        section_data = reader.take(data_size)
        relocations: list[NativeRelocation] = []
        for _relocation_index in range(relocation_count):
            (
                offset,
                symbol_index,
                relocation_type,
                pcrel,
                length,
                addend,
                target_section_index,
                minuend_index,
                target_offset,
            ) = reader.unpack(_RELOCATION)
            if pcrel not in (0, 1):
                raise NativeObjectError("relocation pcrel byte is not boolean")
            relocations.append(NativeRelocation(
                offset=offset,
                symbol_index=_decode_index(symbol_index),
                type=relocation_type,
                pcrel=bool(pcrel),
                length=length,
                addend=addend,
                target_section_index=_decode_index(target_section_index),
                minuend_index=_decode_index(minuend_index),
                target_offset=None if target_offset == -1 else target_offset,
            ))
        regions: list[DataInCodeRegion] = []
        for _region_index in range(data_in_code_count):
            offset, length, kind = reader.unpack(_DATA_IN_CODE)
            regions.append(DataInCodeRegion(offset, length, kind))
        sections.append(NativeSection(
            segname=segname,
            sectname=sectname,
            flags=flags,
            align_log2=align_log2,
            data=section_data,
            relocations=tuple(relocations),
            zerofill_size=zerofill_size,
            data_in_code=tuple(regions),
        ))
    if not reader.at_end:
        raise NativeObjectError("trailing bytes after pcc-native object")
    return NativeObject(tuple(sections), tuple(symbols))


def is_native_object_bytes(data: object) -> bool:
    return isinstance(data, bytes) and data.startswith(MAGIC)


def _append_name(out: bytearray, value: str) -> None:
    _validate_name(value, "object")
    raw = value.encode("ascii")
    out.extend(_U32.pack(len(raw)))
    out.extend(raw)


def _encode_index(value: int | None) -> int:
    if value is None:
        return _NONE_INDEX
    if not isinstance(value, int) or isinstance(value, bool):
        raise NativeObjectError("native object index must be an integer")
    if not 0 <= value < _NONE_INDEX:
        raise NativeObjectError("native object index exceeds uint32")
    return value


def _decode_index(value: int) -> int | None:
    return None if value == _NONE_INDEX else value


def _validate_count(value: int, context: str, *, allow_zero: bool = False) -> None:
    lower = 0 if allow_zero else 1
    if not lower <= value <= _MAX_COUNT:
        raise NativeObjectError(f"native object has invalid {context} count")


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if not isinstance(size, int) or size < 0:
            raise NativeObjectError("native object field has invalid size")
        end = self.offset + size
        if end < self.offset or end > len(self.data):
            raise NativeObjectError("truncated pcc-native object")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def unpack(self, layout: struct.Struct):
        raw = self.take(layout.size)
        return layout.unpack(raw)

    def name(self, context: str) -> str:
        length, = self.unpack(_U32)
        if not 0 < length <= _MAX_NAME_BYTES:
            raise NativeObjectError(f"invalid {context} name length")
        raw = self.take(length)
        try:
            value = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise NativeObjectError(f"non-ASCII {context} name") from exc
        _validate_name(value, context)
        return value

    @property
    def at_end(self) -> bool:
        return self.offset == len(self.data)


__all__ = [
    "MAGIC",
    "NativeObject",
    "NativeObjectError",
    "NativeObjectView",
    "NativeRelocation",
    "NativeSection",
    "NativeSymbol",
    "decode_native_object",
    "encode_native_object",
    "is_native_object_bytes",
]
