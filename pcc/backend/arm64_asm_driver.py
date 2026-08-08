"""Directive-level driver: a full self-backend .s file -> macho_obj sections.

LINK-P1-MACHO-OBJ-SWITCH, the layer above `arm64_encode`: the instruction
encoder covers the emitter's whole vocabulary, and the object writer covers
the section set — this module parses the emitter's *file* dialect (directives,
labels, and data items) and produces the `Section` list that
`NativeObject.from_sections` consumes inside pcc, or that
`macho_obj.emit_object` materialises at an external Mach-O boundary. With it,
a self-backend .s reaches either representation with no system assembler in
the path.

The dialect is the self backend's, not gas: `.section seg,sect[,attrs]`,
`.p2align`, symbol visibility, scalar data items (a `.quad` may name a symbol
± offset, becoming an UNSIGNED relocation), data-in-code regions, labels,
and `.subsections_via_symbols`. Anything else fails closed — the emitter
gaining a directive must extend this driver and its differential test, not
get silently mis-assembled.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import macho_spec as spec
from .arm64_encode import EncodeError, assemble_text
from .macho_obj import (
    COMPACT_UNWIND_SECTION_FLAGS,
    CSTRING_SECTION_FLAGS,
    DATA_SECTION_FLAGS,
    EH_FRAME_SECTION_FLAGS,
    MOD_INIT_SECTION_FLAGS,
    PCC_STACKMAP_SECTION_FLAGS,
    Relocation,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
)

_SECTION_FLAGS = {
    ("__TEXT", "__text", "regular,pure_instructions"): TEXT_SECTION_FLAGS,
    ("__TEXT", "__cstring", "cstring_literals"): CSTRING_SECTION_FLAGS,
    ("__TEXT", "__const", ""): DATA_SECTION_FLAGS,
    ("__DATA", "__data", ""): DATA_SECTION_FLAGS,
    ("__DATA", "__const", ""): DATA_SECTION_FLAGS,
    ("__DATA", "__mod_init_func", "mod_init_funcs"):
        MOD_INIT_SECTION_FLAGS,
    ("__DATA", "__pcc_stackmaps", "regular"):
        PCC_STACKMAP_SECTION_FLAGS,
    ("__LD", "__compact_unwind", "regular,debug"):
        COMPACT_UNWIND_SECTION_FLAGS,
    ("__TEXT", "__eh_frame",
     "coalesced,no_toc+strip_static_syms+live_support"):
        EH_FRAME_SECTION_FLAGS,
}


@dataclass
class _SectionBuffer:
    segname: str
    sectname: str
    flags: int
    align_log2: int = 0
    data: bytearray = field(default_factory=bytearray)
    # text is assembled at the end so branches can cross .section switches
    text_lines: list[str] = field(default_factory=list)
    symbols: list[TextSymbol] = field(default_factory=list)
    relocations: list[Relocation] = field(default_factory=list)

    @property
    def is_text(self) -> bool:
        return bool(self.flags & spec.S_ATTR_PURE_INSTRUCTIONS)


def _parse_int(tok: str) -> int | None:
    t = tok.strip()
    try:
        return int(t, 0)
    except ValueError:
        return None


def _split_data_items(rest: str) -> list[str]:
    return [item.strip() for item in rest.split(",") if item.strip()]


def assemble_file(asm_text: str) -> tuple[list[Section], list[str]]:
    """Parse a full self-backend .s into (sections, undefined symbols)."""
    buffers: dict[tuple[str, str], _SectionBuffer] = {}
    order: list[tuple[str, str]] = []
    globl: set[str] = set()
    private_extern: set[str] = set()
    current: _SectionBuffer | None = None

    def _pad(buf: _SectionBuffer, align_log2: int) -> None:
        mask = (1 << align_log2) - 1
        while len(buf.data) & mask:
            buf.data.append(0)

    for raw in asm_text.splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        directive_name = line.split(None, 1)[0]

        if line.startswith(".section"):
            parts = [p.strip() for p in line[len(".section"):].strip().split(",")]
            if len(parts) < 2:
                raise EncodeError(f"bad .section line {line!r}")
            seg, sect = parts[0], parts[1]
            attrs = ",".join(parts[2:])
            key = (seg, sect)
            if key not in buffers:
                flags = _SECTION_FLAGS.get((seg, sect, attrs))
                if flags is None:
                    raise EncodeError(
                        f"section {seg},{sect} with attrs {attrs!r} not proven"
                    )
                buffers[key] = _SectionBuffer(seg, sect, flags)
                order.append(key)
            current = buffers[key]
            continue

        if line.startswith(".p2align"):
            if current is None:
                raise EncodeError(".p2align before any .section")
            n = _parse_int(line.split()[1])
            if n is None or n < 0 or n > 4:
                raise EncodeError(f"bad .p2align {line!r}")
            current.align_log2 = max(current.align_log2, n)
            if current.is_text:
                if n > 2:
                    raise EncodeError(".p2align > 2 in __text not proven")
                # The instruction assembler owns the running mixed
                # instruction/data offset. Usually this is a no-op; inside a
                # data region it may need to materialize padding bytes.
                current.text_lines.append(line)
            else:
                _pad(current, n)
            continue

        if line.startswith(".globl"):
            globl.add(line.split()[1].strip())
            continue

        if line.startswith(".private_extern"):
            private_extern.add(line.split()[1].strip())
            continue

        if line == ".subsections_via_symbols":
            continue  # macho_obj always sets the header flag

        if directive_name in (".data_region", ".end_data_region"):
            if current is None or not current.is_text:
                raise EncodeError(
                    f"data-region marker outside __text: {line!r}"
                )
            current.text_lines.append(line)
            continue

        if directive_name in (
            ".quad", ".long", ".short", ".byte", ".space",
            ".float", ".double",
        ):
            if current is None:
                raise EncodeError(f"data directive outside a data section: {line!r}")
            if current.is_text:
                current.text_lines.append(line)
                continue
            directive, _, rest = line.partition(" ")
            if directive == ".space":
                count = _parse_int(rest)
                if count is None or count < 0:
                    raise EncodeError(f"bad .space {line!r}")
                current.data += b"\0" * count
                continue
            if directive in (".float", ".double"):
                fmt = "<f" if directive == ".float" else "<d"
                for item in _split_data_items(rest):
                    try:
                        current.data += struct.pack(fmt, float(item))
                    except (ValueError, OverflowError) as exc:
                        raise EncodeError(f"bad {directive} item {item!r}") from exc
                continue
            width = {
                ".quad": 8, ".long": 4, ".short": 2, ".byte": 1,
            }[directive]
            for item in _split_data_items(rest):
                value = _parse_int(item)
                if value is not None:
                    current.data += (value & (1 << width * 8) - 1).to_bytes(
                        width, "little"
                    )
                    continue
                # symbol [± offset] — only proven at pointer width (UNSIGNED)
                if width != 8:
                    raise EncodeError(
                        f"symbol-valued {directive} not proven: {line!r}"
                    )
                symbol, offset = item, 0
                for sep in ("+", "-"):
                    if sep in item[1:]:
                        symbol, _, off_tok = item.partition(sep)
                        offset = _parse_int(off_tok)
                        if offset is None:
                            raise EncodeError(f"bad .quad item {item!r}")
                        if sep == "-":
                            offset = -offset
                        break
                symbol = symbol.strip()
                if len(current.data) % 8 != 0:
                    raise EncodeError(
                        f".quad {symbol} at unaligned offset {len(current.data)}"
                    )
                current.relocations.append(Relocation(
                    len(current.data), symbol,
                    spec.ARM64_RELOC_UNSIGNED, pcrel=False, length=3,
                ))
                # UNSIGNED embeds its addend in the pointer bytes
                current.data += struct.pack("<q", offset)
            continue

        if line.startswith("."):
            raise EncodeError(f"directive {line.split()[0]!r} not proven")

        if line.endswith(":"):
            name = line[:-1].strip()
            if current is None:
                raise EncodeError(f"label {name!r} before any .section")
            if current.is_text:
                current.text_lines.append(line)
                continue
            # Assembler-local L labels in data are padding-free markers; the
            # emitter does not use them, so any data label is a symbol.
            current.symbols.append(TextSymbol(
                name,
                len(current.data),
                external=(name in globl or name in private_extern),
                private_external=(name in private_extern),
            ))
            continue

        # instruction line
        if current is None or not current.is_text:
            raise EncodeError(f"instruction outside __text: {line!r}")
        current.text_lines.append(line)

    # as(1) orders sections by segment (__TEXT before __DATA) regardless of
    # where the .section directives appear; within a segment, first
    # appearance wins. Match it — section order decides vmaddr layout and
    # every symbol's n_sect/n_value, so this is load-bearing, not cosmetic.
    seg_rank = {"__TEXT": 0, "__DATA": 1, "__LD": 2}
    appearance = {key: i for i, key in enumerate(order)}
    order.sort(key=lambda key: (seg_rank.get(key[0], 2), appearance[key]))

    # Assemble each text buffer as one unit (branches may cross .section
    # re-declarations of the same text section, never different sections).
    sections: list[Section] = []
    undefined: set[str] = set()
    defined_names: set[str] = set()
    for key in order:
        buf = buffers[key]
        if buf.is_text:
            assembled = assemble_text("\n".join(buf.text_lines))
            symbols = tuple(
                TextSymbol(
                    name,
                    off,
                    external=(name in globl or name in private_extern),
                    private_external=(name in private_extern),
                )
                for name, off in assembled.labels.items()
                if not name.startswith("L")  # assembler-local, not symbols
            )
            sections.append(Section(
                sectname=buf.sectname, segname=buf.segname,
                data=assembled.code,
                align_log2=max(buf.align_log2, 2),
                flags=buf.flags,
                symbols=symbols,
                relocations=tuple(assembled.relocations),
                data_in_code=tuple(assembled.data_in_code),
            ))
            undefined.update(assembled.undefined)
            defined_names.update(s.name for s in symbols)
        else:
            symbols = tuple(
                TextSymbol(
                    symbol.name,
                    symbol.offset,
                    external=(
                        symbol.name in globl
                        or symbol.name in private_extern
                    ),
                    private_external=(symbol.name in private_extern),
                )
                for symbol in buf.symbols
            )
            sections.append(Section(
                sectname=buf.sectname, segname=buf.segname,
                data=bytes(buf.data),
                align_log2=buf.align_log2,
                flags=buf.flags,
                symbols=symbols,
                relocations=tuple(buf.relocations),
            ))
            undefined.update(
                r.symbol for r in buf.relocations
            )
            defined_names.update(s.name for s in symbols)

    missing_private = private_extern - defined_names
    if missing_private:
        raise EncodeError(
            f"private-extern symbols were not defined: {sorted(missing_private)!r}"
        )

    # as(1) represents compact-unwind function pointers to functions defined
    # by this object as section-target relocations, independent of the
    # function symbol's visibility.  Undefined personality/LSDA references
    # remain symbol relocations.  Encoding a defined function's section and
    # offset also keeps the row valid if a local symbol is stripped later.
    defined_targets: dict[str, tuple[tuple[str, str], int, int]] = {}
    for section in sections:
        key = (section.segname, section.sectname)
        for symbol in section.symbols:
            defined_targets[symbol.name] = (
                key, symbol.offset, section.vm_size,
            )
    normalized_sections: list[Section] = []
    for section in sections:
        if (section.segname, section.sectname) != (
            "__LD", "__compact_unwind"
        ):
            normalized_sections.append(section)
            continue
        payload = bytearray(section.data)
        relocations: list[Relocation] = []
        for relocation in section.relocations:
            target = defined_targets.get(relocation.symbol)
            if (
                target is None
                or relocation.type != spec.ARM64_RELOC_UNSIGNED
                or relocation.pcrel
                or relocation.length != 3
            ):
                relocations.append(relocation)
                continue
            target_section, symbol_offset, target_size = target
            embedded = int.from_bytes(
                payload[relocation.offset:relocation.offset + 8], "little"
            )
            if embedded & (1 << 63):
                embedded -= 1 << 64
            target_offset = symbol_offset + embedded
            if not 0 <= target_offset < target_size:
                raise EncodeError(
                    "compact-unwind local function relocation resolves "
                    f"outside {target_section[0]},{target_section[1]}"
                )
            payload[relocation.offset:relocation.offset + 8] = b"\0" * 8
            relocations.append(Relocation(
                relocation.offset,
                "",
                relocation.type,
                pcrel=False,
                length=3,
                section=target_section,
                target_offset=target_offset,
            ))
        normalized_sections.append(Section(
            sectname=section.sectname,
            segname=section.segname,
            data=bytes(payload),
            align_log2=section.align_log2,
            flags=section.flags,
            symbols=section.symbols,
            relocations=tuple(relocations),
            zerofill_size=section.zerofill_size,
            data_in_code=section.data_in_code,
        ))
    sections = normalized_sections

    return sections, sorted(undefined - defined_names)
