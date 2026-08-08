"""Finite, owned ELF64 object writer and static x86_64 linker.

``LINK-P3-ELF-LINUX`` starts with the static, no-interpreter boundary used by
the freestanding Linux gate.  This module deliberately does not try to be a
general GNU linker.  It owns:

* little-endian ELF64 ``ET_REL`` objects for x86_64;
* strict parsing of the same finite object model, including ``SHT_RELA``;
* GNU/BSD ``ar`` member selection by unresolved-symbol closure; and
* fixed-address ``ET_EXEC`` publication with no ``PT_INTERP``, dynamic table,
  or residual relocation section.

Every unsupported section, symbol shape, or relocation is rejected before an
image is returned.  The first executable slice supports absolute and
PC-relative references plus the static GOT/initial-exec TLS relocations emitted
by pcc's x86_64 backend.  Shared objects, PIE, copy relocations, symbol
versioning, COMDAT, dynamic TLS, and linker scripts are intentionally outside
this contract.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .precise_stackmap import (
    ARCH_X86_64,
    PreciseStackMapError,
    decode_stack_map,
    function_address_offsets,
    function_id,
)


class ElfError(Exception):
    """Input or requested output is outside the proven ELF boundary."""


ELF_MAGIC = b"\x7fELF"
ELFCLASS64 = 2
ELFDATA2LSB = 1
EV_CURRENT = 1
ELFOSABI_SYSV = 0

ET_REL = 1
ET_EXEC = 2
EM_X86_64 = 62

PT_LOAD = 1
PT_TLS = 7
PF_X = 1
PF_W = 2
PF_R = 4

SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOTE = 7
SHT_NOBITS = 8
# LLVM's discardable address-significance metadata.  It informs later
# dead-stripping; this static slice keeps every selected section and therefore
# can safely consume-and-drop the table after validating its symtab link.
SHT_LLVM_ADDRSIG = 0x6FFF4C03

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHF_TLS = 0x400

SHN_UNDEF = 0
SHN_ABS = 0xFFF1
SHN_COMMON = 0xFFF2

STB_LOCAL = 0
STB_GLOBAL = 1
STB_WEAK = 2

STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3
STT_TLS = 6

STV_DEFAULT = 0
STV_HIDDEN = 2

R_X86_64_NONE = 0
R_X86_64_64 = 1
R_X86_64_PC32 = 2
R_X86_64_PLT32 = 4
R_X86_64_GOTPCREL = 9
R_X86_64_32 = 10
R_X86_64_32S = 11
R_X86_64_GOTTPOFF = 22
R_X86_64_TPOFF32 = 23
R_X86_64_GOTPCRELX = 41
R_X86_64_REX_GOTPCRELX = 42

_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
_PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
_SYMBOL = struct.Struct("<IBBHQQ")
_RELA = struct.Struct("<QQq")

_PAGE = 0x1000
_BASE = 0x400000
_AR_MAGIC = b"!<arch>\n"
_AR_HEADER_SIZE = 60
_MAX_SECTIONS = 1_000_000
_MAX_SYMBOLS = 4_000_000


def _align(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ElfError(f"alignment must be a positive power of two, got {alignment}")
    return (value + alignment - 1) & ~(alignment - 1)


def _cstring(table: bytes, offset: int, *, owner: str) -> str:
    if offset < 0 or offset >= len(table):
        raise ElfError(f"{owner} string offset {offset} is outside its table")
    end = table.find(b"\0", offset)
    if end < 0:
        raise ElfError(f"{owner} string at {offset} is not NUL terminated")
    try:
        return table[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ElfError(f"{owner} contains a non-UTF-8 name") from exc


class _StringTable:
    def __init__(self) -> None:
        self.data = bytearray(b"\0")
        self.offsets: dict[str, int] = {"": 0}

    def add(self, value: str) -> int:
        if not isinstance(value, str) or "\0" in value:
            raise ElfError(f"invalid ELF string {value!r}")
        if value in self.offsets:
            return self.offsets[value]
        encoded = value.encode("utf-8")
        offset = len(self.data)
        self.data.extend(encoded)
        self.data.append(0)
        self.offsets[value] = offset
        return offset


@dataclass(frozen=True)
class ElfRelocation:
    offset: int
    symbol_index: int
    type: int
    addend: int = 0


@dataclass(frozen=True)
class ElfSection:
    name: str
    type: int
    flags: int
    align: int
    data: bytes = b""
    mem_size: int = 0
    relocations: tuple[ElfRelocation, ...] = ()

    @property
    def size(self) -> int:
        return self.mem_size if self.type == SHT_NOBITS else len(self.data)


@dataclass(frozen=True)
class ElfSymbol:
    name: str
    section_index: int
    value: int
    size: int
    binding: int
    type: int
    visibility: int = STV_DEFAULT

    @classmethod
    def null(cls) -> "ElfSymbol":
        return cls("", SHN_UNDEF, 0, 0, STB_LOCAL, STT_NOTYPE)


@dataclass(frozen=True)
class ElfObject:
    sections: tuple[ElfSection, ...]
    symbols: tuple[ElfSymbol, ...]

    def __post_init__(self) -> None:
        _validate_object(self)


def _validate_object(obj: ElfObject) -> None:
    if not obj.symbols or obj.symbols[0] != ElfSymbol.null():
        raise ElfError("ELF object symbol zero must be the canonical null symbol")
    if len(obj.sections) > _MAX_SECTIONS or len(obj.symbols) > _MAX_SYMBOLS:
        raise ElfError("ELF object exceeds the finite section/symbol limit")
    names: set[str] = set()
    saw_global = False
    for index, section in enumerate(obj.sections, start=1):
        if not section.name or "\0" in section.name or section.name in names:
            raise ElfError(f"invalid or duplicate section name {section.name!r}")
        names.add(section.name)
        if section.type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            raise ElfError(
                f"section {section.name!r} has unsupported type {section.type}"
            )
        if section.align <= 0 or section.align & (section.align - 1):
            raise ElfError(f"section {section.name!r} has invalid alignment")
        if section.type == SHT_NOBITS:
            if section.data or section.mem_size < 0:
                raise ElfError(f"NOBITS section {section.name!r} carries file data")
        elif section.mem_size not in (0, len(section.data)):
            raise ElfError(f"file-backed section {section.name!r} has size drift")
        for relocation in section.relocations:
            if relocation.symbol_index < 0 or relocation.symbol_index >= len(obj.symbols):
                raise ElfError(
                    f"section {section.name!r} relocation has invalid symbol index"
                )
            width = _relocation_width(relocation.type)
            if relocation.offset < 0 or relocation.offset + width > len(section.data):
                raise ElfError(
                    f"section {section.name!r} relocation is outside its payload"
                )
    for index, symbol in enumerate(obj.symbols):
        if index == 0:
            continue
        if symbol.binding not in (STB_LOCAL, STB_GLOBAL, STB_WEAK):
            raise ElfError(f"symbol {symbol.name!r} has unsupported binding")
        if symbol.type not in (STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION, STT_TLS):
            raise ElfError(f"symbol {symbol.name!r} has unsupported type")
        if symbol.visibility not in (STV_DEFAULT, STV_HIDDEN):
            raise ElfError(f"symbol {symbol.name!r} has unsupported visibility")
        if symbol.binding == STB_LOCAL:
            if saw_global:
                raise ElfError("local symbols must precede global symbols")
        else:
            saw_global = True
        if symbol.section_index == SHN_COMMON:
            raise ElfError(f"COMMON symbol {symbol.name!r} is not supported")
        if symbol.section_index not in (SHN_UNDEF, SHN_ABS) and not (
            1 <= symbol.section_index <= len(obj.sections)
        ):
            raise ElfError(f"symbol {symbol.name!r} has invalid section index")
        if symbol.section_index == SHN_UNDEF and symbol.value != 0:
            raise ElfError(f"undefined symbol {symbol.name!r} has a value")
        if 1 <= symbol.section_index <= len(obj.sections):
            section = obj.sections[symbol.section_index - 1]
            if symbol.value < 0 or symbol.value + symbol.size > section.size:
                raise ElfError(f"symbol {symbol.name!r} is outside its section")
            if symbol.type == STT_TLS and not (section.flags & SHF_TLS):
                raise ElfError(f"TLS symbol {symbol.name!r} is outside a TLS section")

    for section in obj.sections:
        if section.name != ".pcc_stackmaps":
            continue
        if (
            section.type != SHT_PROGBITS
            or section.flags != SHF_ALLOC
            or section.align < 8
        ):
            raise ElfError(
                ".pcc_stackmaps must be an 8-byte-aligned read-only alloc section"
            )
        try:
            address_offsets = function_address_offsets(section.data)
            decoded_stackmap = decode_stack_map(
                section.data,
                expected_arch=ARCH_X86_64,
                final_image=False,
            )
        except PreciseStackMapError as exc:
            raise ElfError(f"invalid .pcc_stackmaps payload: {exc}") from exc
        relocation_by_offset = {
            relocation.offset: relocation for relocation in section.relocations
        }
        if len(relocation_by_offset) != len(section.relocations):
            raise ElfError("duplicate .pcc_stackmaps relocation offset")
        if set(relocation_by_offset) != set(address_offsets):
            raise ElfError(
                "every stack-map function address needs exactly one relocation"
            )
        for address_offset in address_offsets:
            relocation = relocation_by_offset[address_offset]
            if relocation.type != R_X86_64_64 or relocation.addend != 0:
                raise ElfError(
                    "stack-map function address needs a plain R_X86_64_64 relocation"
                )
            if section.data[address_offset:address_offset + 8] != b"\0" * 8:
                raise ElfError(
                    "relocatable stack-map function address must be zero"
                )
        for function, address_offset in zip(
            decoded_stackmap.functions, address_offsets
        ):
            relocation = relocation_by_offset[address_offset]
            symbol = obj.symbols[relocation.symbol_index]
            if symbol.section_index in (SHN_UNDEF, SHN_ABS):
                raise ElfError("stack-map function target must be section-defined")
            if function.function_id != function_id(symbol.name):
                raise ElfError(
                    "stack-map function id does not match its relocation symbol"
                )


def _relocation_width(reloc_type: int) -> int:
    if reloc_type in (R_X86_64_NONE,):
        return 0
    if reloc_type in (R_X86_64_64,):
        return 8
    if reloc_type in (
        R_X86_64_PC32,
        R_X86_64_PLT32,
        R_X86_64_GOTPCREL,
        R_X86_64_32,
        R_X86_64_32S,
        R_X86_64_GOTTPOFF,
        R_X86_64_TPOFF32,
        R_X86_64_GOTPCRELX,
        R_X86_64_REX_GOTPCRELX,
    ):
        return 4
    raise ElfError(f"x86_64 relocation type {reloc_type} is not supported")


def emit_relocatable(obj: ElfObject) -> bytes:
    """Serialize one validated object as a standard ELF64 ``ET_REL`` file."""
    _validate_object(obj)
    section_names = _StringTable()
    string_table = _StringTable()
    for section in obj.sections:
        section_names.add(section.name)
    for symbol in obj.symbols:
        string_table.add(symbol.name)

    relocation_targets = [
        index
        for index, section in enumerate(obj.sections, start=1)
        if section.relocations
    ]
    for target in relocation_targets:
        section_names.add(".rela" + obj.sections[target - 1].name)
    section_names.add(".symtab")
    section_names.add(".strtab")
    section_names.add(".shstrtab")

    user_count = len(obj.sections)
    rela_base = 1 + user_count
    symtab_index = rela_base + len(relocation_targets)
    strtab_index = symtab_index + 1
    shstrtab_index = strtab_index + 1

    payloads: list[bytes] = [b""]
    headers: list[list[int]] = [[0] * 10]
    for section in obj.sections:
        payloads.append(b"" if section.type == SHT_NOBITS else bytes(section.data))
        headers.append([
            section_names.add(section.name),
            section.type,
            section.flags,
            0,
            0,
            section.size,
            0,
            0,
            section.align,
            0,
        ])

    for target_index in relocation_targets:
        target = obj.sections[target_index - 1]
        payload = bytearray()
        for relocation in target.relocations:
            info = (relocation.symbol_index << 32) | relocation.type
            payload += _RELA.pack(relocation.offset, info, relocation.addend)
        payloads.append(bytes(payload))
        headers.append([
            section_names.add(".rela" + target.name),
            SHT_RELA,
            0,
            0,
            0,
            len(payload),
            symtab_index,
            target_index,
            8,
            _RELA.size,
        ])

    symbol_payload = bytearray()
    first_global = len(obj.symbols)
    for index, symbol in enumerate(obj.symbols):
        if first_global == len(obj.symbols) and symbol.binding != STB_LOCAL:
            first_global = index
        info = (symbol.binding << 4) | symbol.type
        symbol_payload += _SYMBOL.pack(
            string_table.add(symbol.name),
            info,
            symbol.visibility,
            symbol.section_index,
            symbol.value,
            symbol.size,
        )
    payloads.append(bytes(symbol_payload))
    headers.append([
        section_names.add(".symtab"), SHT_SYMTAB, 0, 0, 0,
        len(symbol_payload), strtab_index, first_global, 8, _SYMBOL.size,
    ])
    payloads.append(bytes(string_table.data))
    headers.append([
        section_names.add(".strtab"), SHT_STRTAB, 0, 0, 0,
        len(string_table.data), 0, 0, 1, 0,
    ])
    payloads.append(bytes(section_names.data))
    headers.append([
        section_names.add(".shstrtab"), SHT_STRTAB, 0, 0, 0,
        len(section_names.data), 0, 0, 1, 0,
    ])

    offset = _ELF_HEADER.size
    image = bytearray(b"\0" * offset)
    for index in range(1, len(headers)):
        header = headers[index]
        alignment = max(1, header[8])
        offset = _align(offset, alignment)
        header[4] = offset
        if header[1] != SHT_NOBITS:
            if len(image) < offset:
                image.extend(b"\0" * (offset - len(image)))
            image.extend(payloads[index])
            offset += len(payloads[index])
    section_header_offset = _align(offset, 8)
    if len(image) < section_header_offset:
        image.extend(b"\0" * (section_header_offset - len(image)))
    for header in headers:
        image.extend(_SECTION_HEADER.pack(*header))

    ident = ELF_MAGIC + bytes((ELFCLASS64, ELFDATA2LSB, EV_CURRENT, ELFOSABI_SYSV))
    ident += b"\0" * (16 - len(ident))
    image[:_ELF_HEADER.size] = _ELF_HEADER.pack(
        ident,
        ET_REL,
        EM_X86_64,
        EV_CURRENT,
        0,
        0,
        section_header_offset,
        0,
        _ELF_HEADER.size,
        0,
        0,
        _SECTION_HEADER.size,
        len(headers),
        shstrtab_index,
    )
    return bytes(image)


def _unpack_elf_header(data: bytes) -> tuple:
    if len(data) < _ELF_HEADER.size:
        raise ElfError("ELF file is shorter than its header")
    values = _ELF_HEADER.unpack_from(data, 0)
    ident = values[0]
    if ident[:4] != ELF_MAGIC:
        raise ElfError("not an ELF file")
    if ident[4] != ELFCLASS64 or ident[5] != ELFDATA2LSB:
        raise ElfError("only little-endian ELF64 is supported")
    if ident[6] != EV_CURRENT or values[3] != EV_CURRENT:
        raise ElfError("unsupported ELF version")
    if values[2] != EM_X86_64:
        raise ElfError(f"ELF machine {values[2]} is not x86_64")
    return values


def parse_relocatable(data: bytes) -> ElfObject:
    """Parse a finite external ELF64 x86_64 relocatable object."""
    header = _unpack_elf_header(data)
    if header[1] != ET_REL:
        raise ElfError(f"expected ET_REL, got ELF type {header[1]}")
    phoff, shoff = header[5], header[6]
    ehsize, phentsize, phnum = header[8], header[9], header[10]
    shentsize, shnum, shstrndx = header[11], header[12], header[13]
    if ehsize != _ELF_HEADER.size or phoff != 0 or phnum != 0 or phentsize not in (0, _PROGRAM_HEADER.size):
        raise ElfError("relocatable object has an unsupported header layout")
    if shentsize != _SECTION_HEADER.size or not (0 < shnum <= _MAX_SECTIONS):
        raise ElfError("relocatable object has an invalid section table")
    if shoff < _ELF_HEADER.size or shoff + shnum * shentsize > len(data):
        raise ElfError("ELF section table is outside the file")
    if not (0 < shstrndx < shnum):
        raise ElfError("ELF section-name table index is invalid")

    raw_headers = [
        _SECTION_HEADER.unpack_from(data, shoff + index * shentsize)
        for index in range(shnum)
    ]

    def section_payload(index: int) -> bytes:
        raw = raw_headers[index]
        section_type, offset, size = raw[1], raw[4], raw[5]
        if section_type == SHT_NOBITS:
            return b""
        if offset > len(data) or size > len(data) - offset:
            raise ElfError(f"ELF section {index} is outside the file")
        return data[offset:offset + size]

    shstr = section_payload(shstrndx)
    raw_names = [
        _cstring(shstr, raw[0], owner="section-name table")
        for raw in raw_headers
    ]
    if raw_headers[0] != (0,) * 10:
        raise ElfError("ELF section zero is not the canonical null header")

    symtab_indices = [
        index for index, raw in enumerate(raw_headers) if raw[1] == SHT_SYMTAB
    ]
    if len(symtab_indices) != 1:
        raise ElfError("ELF object must contain exactly one SHT_SYMTAB")
    symtab_index = symtab_indices[0]
    symtab_header = raw_headers[symtab_index]
    if symtab_header[9] != _SYMBOL.size or symtab_header[5] % _SYMBOL.size:
        raise ElfError("ELF symbol table has an invalid entry size")
    strtab_index = symtab_header[6]
    if not (0 < strtab_index < shnum) or raw_headers[strtab_index][1] != SHT_STRTAB:
        raise ElfError("ELF symbol table has an invalid linked string table")
    symbol_strings = section_payload(strtab_index)

    addrsig_indices = {
        index
        for index, raw in enumerate(raw_headers)
        if raw[1] == SHT_LLVM_ADDRSIG
    }
    for index in addrsig_indices:
        raw = raw_headers[index]
        if raw[6] not in (0, symtab_index) or raw[7] or raw[9] not in (0, 1):
            raise ElfError(
                f"LLVM address-significance section {raw_names[index]!r} is malformed"
            )
        section_payload(index)
    meta_indices = {
        0, symtab_index, strtab_index, shstrndx,
        *addrsig_indices,
        *(
            index for index, raw in enumerate(raw_headers)
            if raw[1] == SHT_RELA
        ),
    }
    source_to_model: dict[int, int] = {}
    sections: list[ElfSection] = []
    for source_index, raw in enumerate(raw_headers):
        if source_index in meta_indices:
            continue
        section_type = raw[1]
        if section_type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            raise ElfError(
                f"section {raw_names[source_index]!r} has unsupported type {section_type}"
            )
        if raw[6] or raw[7] or raw[9]:
            raise ElfError(
                f"section {raw_names[source_index]!r} has unsupported link/info/entsize"
            )
        alignment = raw[8] or 1
        if alignment & (alignment - 1):
            raise ElfError(f"section {raw_names[source_index]!r} has invalid alignment")
        source_to_model[source_index] = len(sections) + 1
        sections.append(ElfSection(
            name=raw_names[source_index],
            type=section_type,
            flags=raw[2],
            align=alignment,
            data=section_payload(source_index),
            mem_size=raw[5] if section_type == SHT_NOBITS else 0,
        ))

    symbols: list[ElfSymbol] = []
    raw_symbols = section_payload(symtab_index)
    symbol_count = len(raw_symbols) // _SYMBOL.size
    if not (0 < symbol_count <= _MAX_SYMBOLS):
        raise ElfError("ELF symbol table has an invalid size")
    for index in range(symbol_count):
        name_off, info, other, shndx, value, size = _SYMBOL.unpack_from(
            raw_symbols, index * _SYMBOL.size
        )
        binding, symbol_type = info >> 4, info & 0xF
        if shndx not in (SHN_UNDEF, SHN_ABS, SHN_COMMON):
            if shndx not in source_to_model:
                raise ElfError(
                    f"symbol {index} refers to unsupported section {shndx}"
                )
            shndx = source_to_model[shndx]
        symbols.append(ElfSymbol(
            _cstring(symbol_strings, name_off, owner="symbol table"),
            shndx,
            value,
            size,
            binding,
            symbol_type,
            other & 3,
        ))

    mutable_sections = list(sections)
    for rela_index, raw in enumerate(raw_headers):
        if raw[1] != SHT_RELA:
            continue
        if raw[6] != symtab_index or raw[9] != _RELA.size or raw[5] % _RELA.size:
            raise ElfError(f"relocation section {raw_names[rela_index]!r} is malformed")
        source_target = raw[7]
        if source_target not in source_to_model:
            raise ElfError(
                f"relocation section {raw_names[rela_index]!r} targets unsupported section"
            )
        target = source_to_model[source_target] - 1
        if mutable_sections[target].relocations:
            raise ElfError(f"section {mutable_sections[target].name!r} has multiple RELA tables")
        relocations: list[ElfRelocation] = []
        payload = section_payload(rela_index)
        for offset in range(0, len(payload), _RELA.size):
            r_offset, r_info, addend = _RELA.unpack_from(payload, offset)
            symbol_index, reloc_type = r_info >> 32, r_info & 0xFFFFFFFF
            relocations.append(ElfRelocation(r_offset, symbol_index, reloc_type, addend))
        old = mutable_sections[target]
        mutable_sections[target] = ElfSection(
            old.name, old.type, old.flags, old.align, old.data, old.mem_size,
            tuple(relocations),
        )
    return ElfObject(tuple(mutable_sections), tuple(symbols))


@dataclass(frozen=True)
class ElfArchiveMember:
    name: str
    object: ElfObject
    defines: frozenset[str]
    undefined: frozenset[str]


def _object_symbol_sets(obj: ElfObject) -> tuple[frozenset[str], frozenset[str]]:
    defined: set[str] = set()
    undefined: set[str] = set()
    for symbol in obj.symbols[1:]:
        if symbol.binding not in (STB_GLOBAL, STB_WEAK) or not symbol.name:
            continue
        if symbol.section_index == SHN_UNDEF:
            if symbol.binding != STB_WEAK:
                undefined.add(symbol.name)
        else:
            defined.add(symbol.name)
    return frozenset(defined), frozenset(undefined)


def read_archive(data: bytes) -> tuple[ElfArchiveMember, ...]:
    """Read GNU or BSD ``ar`` members and validate every ELF object."""
    if not data.startswith(_AR_MAGIC):
        raise ElfError("not an ar archive")
    offset = len(_AR_MAGIC)
    gnu_names = b""
    pending: list[tuple[str, bytes]] = []
    while offset < len(data):
        if offset + _AR_HEADER_SIZE > len(data):
            raise ElfError(f"truncated ar header at {offset}")
        header = data[offset:offset + _AR_HEADER_SIZE]
        if header[58:60] != b"`\n":
            raise ElfError(f"bad ar member magic at {offset}")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise ElfError(f"bad ar member size at {offset}") from exc
        body = offset + _AR_HEADER_SIZE
        end = body + size
        if size < 0 or end > len(data):
            raise ElfError(f"ar member at {offset} runs past end of archive")
        raw_name = header[:16].rstrip()
        payload = data[body:end]
        offset = end + (end & 1)
        if raw_name == b"//":
            gnu_names = payload
            continue
        if raw_name in (b"/", b"/SYM64/") or raw_name.startswith(b"__.SYMDEF"):
            continue
        if raw_name.startswith(b"#1/"):
            try:
                length = int(raw_name[3:])
            except ValueError as exc:
                raise ElfError("bad BSD ar extended name") from exc
            if length > len(payload):
                raise ElfError("BSD ar extended name exceeds member")
            name = payload[:length].rstrip(b"\0").decode("utf-8", "surrogateescape")
            payload = payload[length:]
        elif raw_name.startswith(b"/") and raw_name[1:].isdigit():
            if not gnu_names:
                raise ElfError("GNU ar member uses a missing string table")
            name_offset = int(raw_name[1:])
            if name_offset >= len(gnu_names):
                raise ElfError("GNU ar member name offset is out of range")
            name_end = gnu_names.find(b"/\n", name_offset)
            if name_end < 0:
                raise ElfError("GNU ar long member name is unterminated")
            name = gnu_names[name_offset:name_end].decode("utf-8", "surrogateescape")
        else:
            name = raw_name.rstrip(b"/").decode("utf-8", "surrogateescape")
        pending.append((name, payload))

    members: list[ElfArchiveMember] = []
    for name, payload in pending:
        try:
            obj = parse_relocatable(payload)
        except ElfError as exc:
            raise ElfError(f"archive member {name!r} is not a proven ELF object: {exc}") from exc
        defined, undefined = _object_symbol_sets(obj)
        members.append(ElfArchiveMember(name, obj, defined, undefined))
    return tuple(members)


def select_archive_members(
    members: tuple[ElfArchiveMember, ...],
    undefined: set[str],
    *,
    already_defined: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[ElfObject], set[str]]:
    pending = set(undefined) - set(already_defined)
    provided = set(already_defined)
    selected: set[int] = set()
    changed = True
    while changed:
        changed = False
        for index, member in enumerate(members):
            if index in selected or not (member.defines & pending):
                continue
            selected.add(index)
            provided.update(member.defines)
            pending.difference_update(member.defines)
            pending.update(member.undefined - provided)
            changed = True
    return [member.object for index, member in enumerate(members) if index in selected], pending


@dataclass(frozen=True)
class _Placement:
    file_offset: int
    address: int


def _global_state(objects: list[ElfObject]) -> tuple[dict[str, tuple[int, int]], set[str]]:
    definitions: dict[str, tuple[int, int]] = {}
    weak_definitions: dict[str, tuple[int, int]] = {}
    undefined: set[str] = set()
    for object_index, obj in enumerate(objects):
        for symbol_index, symbol in enumerate(obj.symbols[1:], start=1):
            if symbol.binding not in (STB_GLOBAL, STB_WEAK) or not symbol.name:
                continue
            if symbol.section_index == SHN_UNDEF:
                if symbol.binding != STB_WEAK:
                    undefined.add(symbol.name)
                continue
            target = (object_index, symbol_index)
            if symbol.binding == STB_WEAK:
                weak_definitions.setdefault(symbol.name, target)
                continue
            if symbol.name in definitions:
                raise ElfError(f"duplicate strong definition of {symbol.name!r}")
            definitions[symbol.name] = target
    for name, target in weak_definitions.items():
        definitions.setdefault(name, target)
    return definitions, undefined - set(definitions)


def _symbol_identity(
    objects: list[ElfObject],
    definitions: dict[str, tuple[int, int]],
    object_index: int,
    symbol_index: int,
) -> tuple[str, object]:
    symbol = objects[object_index].symbols[symbol_index]
    if symbol.binding == STB_LOCAL or symbol.section_index != SHN_UNDEF:
        return ("local", (object_index, symbol_index))
    if symbol.name in definitions:
        return ("global", symbol.name)
    if symbol.binding == STB_WEAK:
        return ("weak-zero", symbol.name)
    raise ElfError(f"undefined symbol {symbol.name!r}")


def _write_int(image: bytearray, offset: int, value: int, bits: int, signed: bool) -> None:
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - (1 if signed else 0))) - 1
    if value < minimum or value > maximum:
        kind = "signed" if signed else "unsigned"
        raise ElfError(f"relocation result {value} does not fit {kind} i{bits}")
    image[offset:offset + bits // 8] = int(value).to_bytes(
        bits // 8, "little", signed=signed
    )


def link_static_executable(
    objects: list[ElfObject],
    *,
    archives: list[bytes] = (),
    entry: str = "_start",
    base_address: int = _BASE,
) -> bytes:
    """Link a static, fixed-address ELF executable with no dynamic surface."""
    if not objects:
        raise ElfError("static ELF link requires at least one explicit object")
    if base_address < 0x10000 or base_address % _PAGE:
        raise ElfError("static ELF base address must be page-aligned and >= 0x10000")
    objects = list(objects)
    definitions, undefined = _global_state(objects)
    for archive_data in archives:
        selected, _remaining = select_archive_members(
            read_archive(archive_data),
            undefined,
            already_defined=set(definitions),
        )
        objects.extend(selected)
        definitions, undefined = _global_state(objects)
    if undefined:
        raise ElfError("undefined static ELF symbols: " + ", ".join(sorted(undefined)))
    if entry not in definitions:
        raise ElfError(f"entry symbol {entry!r} is not defined")

    alloc_sections: list[tuple[int, int, ElfSection]] = []
    for object_index, obj in enumerate(objects):
        for section_index, section in enumerate(obj.sections, start=1):
            if not section.flags & SHF_ALLOC:
                if section.relocations:
                    raise ElfError(f"non-alloc section {section.name!r} carries relocations")
                if section.name not in (".note.GNU-stack", ".comment", ".llvm_addrsig"):
                    raise ElfError(f"non-alloc section {section.name!r} is not proven")
                continue
            if section.type == SHT_NOTE:
                raise ElfError(f"allocated NOTE section {section.name!r} is not proven")
            if section.type == SHT_NOBITS and not section.flags & SHF_WRITE:
                raise ElfError(f"NOBITS section {section.name!r} must be writable")
            if section.flags & SHF_TLS and not section.flags & SHF_WRITE:
                raise ElfError(f"TLS section {section.name!r} must be writable")
            alloc_sections.append((object_index, section_index, section))

    got_identities: list[tuple[tuple[str, object], str]] = []
    got_seen: set[tuple[tuple[str, object], str]] = set()
    got_reloc_types = {
        R_X86_64_GOTPCREL,
        R_X86_64_GOTTPOFF,
        R_X86_64_GOTPCRELX,
        R_X86_64_REX_GOTPCRELX,
    }
    for object_index, section_index, section in alloc_sections:
        for relocation in section.relocations:
            if relocation.type not in got_reloc_types:
                continue
            identity = _symbol_identity(
                objects, definitions, object_index, relocation.symbol_index
            )
            kind = "tpoff" if relocation.type == R_X86_64_GOTTPOFF else "address"
            key = (identity, kind)
            if key not in got_seen:
                got_seen.add(key)
                got_identities.append(key)

    rx_sections = [item for item in alloc_sections if not item[2].flags & SHF_WRITE]
    rw_file_sections = [
        item for item in alloc_sections
        if item[2].flags & SHF_WRITE
        and not item[2].flags & SHF_TLS
        and item[2].type != SHT_NOBITS
    ]
    tdata_sections = [
        item for item in alloc_sections
        if item[2].flags & SHF_TLS and item[2].type != SHT_NOBITS
    ]
    tbss_sections = [
        item for item in alloc_sections
        if item[2].flags & SHF_TLS and item[2].type == SHT_NOBITS
    ]
    bss_sections = [
        item for item in alloc_sections
        if not item[2].flags & SHF_TLS and item[2].type == SHT_NOBITS
    ]
    if any(item[2].flags & SHF_EXECINSTR for item in rw_file_sections + tdata_sections + tbss_sections + bss_sections):
        raise ElfError("writable executable ELF sections are not supported")

    has_rw = bool(rw_file_sections or tdata_sections or tbss_sections or bss_sections or got_identities)
    has_tls = bool(tdata_sections or tbss_sections)
    phnum = 1 + int(has_rw) + int(has_tls)
    header_bytes = _ELF_HEADER.size + phnum * _PROGRAM_HEADER.size
    placements: dict[tuple[int, int], _Placement] = {}

    cursor = _align(header_bytes, _PAGE)
    for object_index, section_index, section in rx_sections:
        cursor = _align(cursor, section.align)
        placements[(object_index, section_index)] = _Placement(cursor, base_address + cursor)
        cursor += section.size
    rx_file_end = cursor

    rw_start = _align(rx_file_end, _PAGE) if has_rw else 0
    cursor = rw_start
    for object_index, section_index, section in rw_file_sections:
        cursor = _align(cursor, section.align)
        placements[(object_index, section_index)] = _Placement(cursor, base_address + cursor)
        cursor += section.size
    got_offset = _align(cursor, 8)
    got_address = base_address + got_offset
    cursor = got_offset + len(got_identities) * 8

    tls_align = max((item[2].align for item in tdata_sections + tbss_sections), default=1)
    tls_file_start = _align(cursor, tls_align) if has_tls else 0
    cursor = tls_file_start
    for object_index, section_index, section in tdata_sections:
        cursor = _align(cursor, section.align)
        placements[(object_index, section_index)] = _Placement(cursor, base_address + cursor)
        cursor += section.size
    tls_file_end = cursor
    memory_cursor = cursor
    for object_index, section_index, section in tbss_sections:
        memory_cursor = _align(memory_cursor, section.align)
        placements[(object_index, section_index)] = _Placement(
            tls_file_end, base_address + memory_cursor
        )
        memory_cursor += section.size
    tls_memory_end = memory_cursor
    for object_index, section_index, section in bss_sections:
        memory_cursor = _align(memory_cursor, section.align)
        placements[(object_index, section_index)] = _Placement(
            cursor, base_address + memory_cursor
        )
        memory_cursor += section.size
    rw_file_end = cursor
    rw_memory_end = memory_cursor

    image_size = max(rx_file_end, rw_file_end, header_bytes)
    image = bytearray(b"\0" * image_size)
    for object_index, section_index, section in alloc_sections:
        if section.type == SHT_NOBITS:
            continue
        placement = placements[(object_index, section_index)]
        image[placement.file_offset:placement.file_offset + len(section.data)] = section.data

    def resolve_symbol(object_index: int, symbol_index: int) -> tuple[int, ElfSymbol]:
        identity = _symbol_identity(objects, definitions, object_index, symbol_index)
        if identity[0] == "weak-zero":
            return 0, objects[object_index].symbols[symbol_index]
        target_object, target_symbol = (
            identity[1] if identity[0] == "local" else definitions[str(identity[1])]
        )
        symbol = objects[target_object].symbols[target_symbol]
        if symbol.section_index == SHN_ABS:
            return symbol.value, symbol
        if symbol.section_index == SHN_UNDEF:
            return 0, symbol
        placement = placements.get((target_object, symbol.section_index))
        if placement is None:
            raise ElfError(f"symbol {symbol.name!r} is defined in a non-alloc section")
        return placement.address + symbol.value, symbol

    tls_tp = _align(base_address + tls_memory_end, max(tls_align, 16)) if has_tls else 0

    def tls_offset(object_index: int, symbol_index: int) -> int:
        address, symbol = resolve_symbol(object_index, symbol_index)
        if symbol.type != STT_TLS:
            raise ElfError(f"TLS relocation targets non-TLS symbol {symbol.name!r}")
        if not has_tls:
            raise ElfError("TLS relocation exists without a PT_TLS image")
        return address - tls_tp

    got_offsets = {identity: index * 8 for index, identity in enumerate(got_identities)}
    for (identity, kind), relative in got_offsets.items():
        if identity[0] == "weak-zero":
            if kind == "tpoff":
                raise ElfError("GOTTPOFF cannot target an undefined weak symbol")
            value = 0
        else:
            target_object, target_symbol = (
                identity[1] if identity[0] == "local" else definitions[str(identity[1])]
            )
            symbol = objects[target_object].symbols[target_symbol]
            if kind == "tpoff":
                value = tls_offset(target_object, target_symbol)
            else:
                if symbol.type == STT_TLS:
                    raise ElfError(
                        f"ordinary GOT relocation targets TLS symbol {symbol.name!r}"
                    )
                value = resolve_symbol(target_object, target_symbol)[0]
        _write_int(image, got_offset + relative, value, 64, value < 0)

    for object_index, section_index, section in alloc_sections:
        if section.type == SHT_NOBITS and section.relocations:
            raise ElfError(f"NOBITS section {section.name!r} carries relocations")
        placement = placements[(object_index, section_index)]
        for relocation in section.relocations:
            if relocation.type == R_X86_64_NONE:
                continue
            patch = placement.file_offset + relocation.offset
            place = placement.address + relocation.offset
            symbol_address, _symbol = resolve_symbol(
                object_index, relocation.symbol_index
            )
            if relocation.type == R_X86_64_64:
                _write_int(image, patch, symbol_address + relocation.addend, 64, False)
            elif relocation.type in (R_X86_64_PC32, R_X86_64_PLT32):
                _write_int(image, patch, symbol_address + relocation.addend - place, 32, True)
            elif relocation.type == R_X86_64_32:
                _write_int(image, patch, symbol_address + relocation.addend, 32, False)
            elif relocation.type == R_X86_64_32S:
                _write_int(image, patch, symbol_address + relocation.addend, 32, True)
            elif relocation.type in (
                R_X86_64_GOTPCREL,
                R_X86_64_GOTPCRELX,
                R_X86_64_REX_GOTPCRELX,
                R_X86_64_GOTTPOFF,
            ):
                identity = _symbol_identity(
                    objects, definitions, object_index, relocation.symbol_index
                )
                kind = (
                    "tpoff"
                    if relocation.type == R_X86_64_GOTTPOFF
                    else "address"
                )
                slot = got_address + got_offsets[(identity, kind)]
                _write_int(image, patch, slot + relocation.addend - place, 32, True)
            elif relocation.type == R_X86_64_TPOFF32:
                value = tls_offset(object_index, relocation.symbol_index)
                _write_int(image, patch, value + relocation.addend, 32, True)
            else:
                # _relocation_width made this unreachable unless a newly
                # accepted writer relocation was not deliberately linked.
                raise ElfError(f"relocation type {relocation.type} has no link rule")

    # Validate the exact bytes that will become executable metadata after all
    # function-address relocations have been applied.  The final ET_EXEC drops
    # section headers, so this is the last owned publication boundary at which
    # a malformed or unresolved PC table can still be attributed precisely.
    for object_index, section_index, section in alloc_sections:
        if section.name != ".pcc_stackmaps":
            continue
        placement = placements[(object_index, section_index)]
        payload = bytes(
            image[
                placement.file_offset:
                placement.file_offset + len(section.data)
            ]
        )
        try:
            decode_stack_map(
                payload,
                expected_arch=ARCH_X86_64,
                final_image=True,
            )
        except PreciseStackMapError as exc:
            raise ElfError(
                f"invalid final .pcc_stackmaps payload: {exc}"
            ) from exc

    entry_object, entry_symbol = definitions[entry]
    entry_record = objects[entry_object].symbols[entry_symbol]
    if entry_record.section_index in (SHN_UNDEF, SHN_ABS):
        raise ElfError(f"entry symbol {entry!r} is not section-defined")
    entry_section = objects[entry_object].sections[entry_record.section_index - 1]
    if not entry_section.flags & SHF_EXECINSTR:
        raise ElfError(f"entry symbol {entry!r} is not in executable code")
    entry_address, _ = resolve_symbol(entry_object, entry_symbol)
    program_headers: list[bytes] = [
        _PROGRAM_HEADER.pack(
            PT_LOAD, PF_R | PF_X, 0, base_address, base_address,
            rx_file_end, rx_file_end, _PAGE,
        )
    ]
    if has_rw:
        program_headers.append(_PROGRAM_HEADER.pack(
            PT_LOAD, PF_R | PF_W, rw_start, base_address + rw_start,
            base_address + rw_start, rw_file_end - rw_start,
            rw_memory_end - rw_start, _PAGE,
        ))
    if has_tls:
        program_headers.append(_PROGRAM_HEADER.pack(
            PT_TLS, PF_R, tls_file_start, base_address + tls_file_start,
            base_address + tls_file_start, tls_file_end - tls_file_start,
            tls_memory_end - tls_file_start, tls_align,
        ))
    ident = ELF_MAGIC + bytes((ELFCLASS64, ELFDATA2LSB, EV_CURRENT, ELFOSABI_SYSV))
    ident += b"\0" * (16 - len(ident))
    image[:_ELF_HEADER.size] = _ELF_HEADER.pack(
        ident, ET_EXEC, EM_X86_64, EV_CURRENT, entry_address,
        _ELF_HEADER.size, 0, 0, _ELF_HEADER.size,
        _PROGRAM_HEADER.size, len(program_headers), 0, 0, 0,
    )
    phoff = _ELF_HEADER.size
    for program_header in program_headers:
        image[phoff:phoff + _PROGRAM_HEADER.size] = program_header
        phoff += _PROGRAM_HEADER.size
    return bytes(image)


def parse_static_executable(data: bytes) -> dict[str, int]:
    """Validate the final no-dynamic ELF shape and return useful fields."""
    header = _unpack_elf_header(data)
    if header[1] != ET_EXEC:
        raise ElfError("final image is not ET_EXEC")
    if header[6] != 0 or header[12] != 0:
        raise ElfError("final static image unexpectedly has section headers")
    phoff, phentsize, phnum = header[5], header[9], header[10]
    if phentsize != _PROGRAM_HEADER.size or phnum not in (1, 2, 3):
        raise ElfError("final static image has an unsupported program-header table")
    if phoff + phentsize * phnum > len(data):
        raise ElfError("program-header table is truncated")
    load_count = 0
    tls_count = 0
    for index in range(phnum):
        values = _PROGRAM_HEADER.unpack_from(data, phoff + index * phentsize)
        p_type, _flags, offset, _vaddr, _paddr, filesz, memsz, align = values
        if p_type == PT_LOAD:
            load_count += 1
        elif p_type == PT_TLS:
            tls_count += 1
        else:
            raise ElfError(f"unexpected program header type {p_type}")
        if filesz > memsz or offset > len(data) or filesz > len(data) - offset:
            raise ElfError("program header maps bytes outside the image")
        if align <= 0 or align & (align - 1):
            raise ElfError("program header has invalid alignment")
    if load_count not in (1, 2) or tls_count > 1:
        raise ElfError("final static image has an invalid LOAD/TLS topology")
    return {
        "entry": header[4],
        "program_headers": phnum,
        "load_segments": load_count,
        "tls_segments": tls_count,
    }
