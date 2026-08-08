"""Static-link core: merge pcc's objects, resolve symbols, fix relocations.

LINK-P1-MACHO-LINK-STATIC, first slice. The row's engine is symbol
resolution + section merging + layout + relocation application; the
*executable container* (dyld surface, stubs, GOT, entry point) is
LINK-P1-MACHO-LINK-DYLD. This module implements the engine and proves it in
the shape where it can be proven on its own: relocatable linking, the job
`ld -r` does.

What it does, per `ld -r` semantics verified against the real thing:

- sections merge by (segname, sectname), each input's payload appended at its
  own alignment; segment ordering follows the same __TEXT-before-__DATA rule
  the assembler driver uses, since it decides every symbol's address
- defined symbols keep their identity and get their merged address; a symbol
  undefined in one input and defined in another is *resolved* — it leaves the
  undefined list and the relocation now targets the defined symbol
- relocations keep their type and extern bit, shift `r_address` by the
  input's offset within the merged section, and re-point at the merged symbol
  table; ARM64_RELOC_ADDEND companions are folded on read and re-emitted on
  write, so the addend travels with the relocation it modifies
- LC_DATA_IN_CODE ranges move with their owning instruction section instead
  of disappearing or retaining stale pre-merge addresses

Fail closed: a duplicate definition, an unsupported section, or a relocation
against a symbol no input defines or declares raises `LinkError`. A linker
that guesses produces a binary that crashes far from the mistake.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import macho_spec as spec
from .macho_obj import (
    DataInCodeRegion,
    MachOEmitError,
    Relocation,
    Section,
    TextSymbol,
)
from .macho_parallel import (
    ParallelLinkError,
    ShardedSymbolDefinitions,
    SymbolDefinition,
    ordered_parallel_map,
)
from .native_object import (
    NativeObject,
    NativeObjectError,
    NativeObjectView,
    is_native_object_bytes,
)
from .precise_stackmap import (
    ARCH_AARCH64,
    PreciseStackMapError,
    _scan_stack_map_payload,
    function_address_offsets,
    merge_stack_map_payloads,
)


_U64_MASK = (1 << 64) - 1  # computed, NOT a literal: see M5-SELFHOST-BIG-INT-LITERAL


class LinkError(Exception):
    """The link job is outside what this linker proves."""


LinkInput = bytes | NativeObject | NativeObjectView | spec.MachOObject


def _coerce_link_object(value: LinkInput, input_index: int):
    """Normalize one explicitly typed input exactly once.

    Raw bytes are the external Mach-O boundary.  The pcc-native codec is a
    cache/process transport, not another platform object encoding, so callers
    must decode it to ``NativeObject`` before entering this API.  Keeping that
    distinction explicit prevents a mislabeled ``--object`` input from being
    accepted merely because its bytes happen to carry pcc's private magic.
    """
    try:
        if isinstance(value, NativeObjectView):
            return value
        if isinstance(value, NativeObject):
            return value.link_view()
        if isinstance(value, spec.MachOObject):
            return value
        if not isinstance(value, bytes):
            raise LinkError(
                f"input {input_index} is neither Mach-O nor a pcc-native object"
            )
        if is_native_object_bytes(value):
            raise LinkError(
                f"input {input_index} contains encoded pcc-native bytes; "
                "decode it explicitly before linking"
            )
        return spec.parse_object(value)
    except (NativeObjectError, spec.MachOFormatError) as exc:
        raise LinkError(f"input {input_index} is not a valid object: {exc}") from exc


@dataclass
class _MergedSection:
    segname: str
    sectname: str
    flags: int
    align_log2: int = 0
    data: bytearray = field(default_factory=bytearray)
    symbols: list[TextSymbol] = field(default_factory=list)
    relocations: list[Relocation] = field(default_factory=list)
    data_in_code: list[DataInCodeRegion] = field(default_factory=list)
    zerofill_size: int = 0


@dataclass(frozen=True)
class _InspectedLinkInput:
    obj: object
    symbols: list[dict]
    relocation_target_indices: frozenset[int]


def _is_zerofill(section) -> bool:
    return (section["flags"] & spec.SECTION_TYPE) in (
        spec.S_ZEROFILL, _S_THREAD_LOCAL_ZEROFILL
    )


# Thread-local zerofill has its own section type; it behaves the same way
# here (vm space, no file payload).
_S_THREAD_LOCAL_ZEROFILL = 0x12


def _align_up(value: int, align_log2: int) -> int:
    mask = (1 << align_log2) - 1
    return (value + mask) & ~mask


def _relocation_symbol_name(
    symbols,
    symbol_index: int,
    local_rename: dict[str, str],
    *,
    context: str,
) -> str:
    if not 0 <= symbol_index < len(symbols):
        raise LinkError(
            f"{context} names symbol index {symbol_index}, but the input has "
            f"{len(symbols)} symbols"
        )
    symbol = symbols[symbol_index]
    name = symbol["name"]
    if (
        (symbol["n_type"] & spec.N_TYPE) == spec.N_SECT
        and not (symbol["n_type"] & spec.N_EXT)
    ):
        return local_rename.get(name, name)
    return name


def _local_symbol_renames(symbol_tables) -> list[dict[str, str]]:
    """Give colliding locals output-only names without changing externs.

    Mach-O relocations carry a symbol-table index, so a local symbol and an
    external symbol may legitimately have the same spelling.  ``emit_object``
    intentionally uses a name-keyed API; rename only the local side before
    entering that API.  Reserve every external spelling up front so the
    result is independent of input order.
    """
    external_names = {
        symbol["name"]
        for symbols in symbol_tables
        for symbol in symbols
        if symbol["n_type"] & spec.N_EXT
    }
    reserved_names = {
        symbol["name"]
        for symbols in symbol_tables
        for symbol in symbols
    }
    used_names = set(external_names)
    renames: list[dict[str, str]] = []

    for input_index, symbols in enumerate(symbol_tables):
        mapping: dict[str, str] = {}
        local_names: set[str] = set()
        for symbol in symbols:
            if (symbol["n_type"] & spec.N_TYPE) != spec.N_SECT:
                continue
            if symbol["n_type"] & spec.N_EXT:
                continue
            name = symbol["name"]
            if name in local_names:
                raise LinkError(
                    f"input {input_index} defines local symbol {name!r} twice"
                )
            local_names.add(name)
            if name not in used_names:
                used_names.add(name)
                continue

            suffix = f"$link{input_index}"
            candidate = name + suffix
            collision = 1
            while candidate in reserved_names or candidate in used_names:
                candidate = name + suffix + f"${collision}"
                collision += 1
            mapping[name] = candidate
            used_names.add(candidate)
            reserved_names.add(candidate)
        renames.append(mapping)
    return renames


def _read_relocations(
    obj,
    section,
    symbols,
    local_rename: dict[str, str],
) -> list[Relocation]:
    """Relocations with ADDEND/SUBTRACTOR companions folded atomically.

    A non-extern entry targets a SECTION: `r_symbolnum` is that section's
    1-based index and the value being relocated is stored in the payload as
    an address in this object's own address space. It is carried through as a
    (segname, sectname) target so the merge can rewrite that stored address
    once the section's new home is known.
    """
    raw = obj.relocations(section)
    sections = obj.sections()
    out: list[Relocation] = []
    pending_addend = None
    pending_subtractor = None
    for entry in raw:
        if pending_subtractor is not None:
            if (
                entry["r_type"] != spec.ARM64_RELOC_UNSIGNED
                or not entry["r_extern"]
                or entry["r_address"] != pending_subtractor["r_address"]
                or entry["r_pcrel"]
                or entry["r_length"] != pending_subtractor["r_length"]
            ):
                raise LinkError(
                    "SUBTRACTOR must be followed by a same-address, "
                    "same-width extern UNSIGNED entry"
                )
            out.append(Relocation(
                offset=pending_subtractor["r_address"],
                symbol=_relocation_symbol_name(
                    symbols,
                    pending_subtractor["r_symbolnum"],
                    local_rename,
                    context="SUBTRACTOR relocation",
                ),
                type=spec.ARM64_RELOC_SUBTRACTOR,
                pcrel=False,
                length=pending_subtractor["r_length"],
                addend=0,
                minuend=_relocation_symbol_name(
                    symbols,
                    entry["r_symbolnum"],
                    local_rename,
                    context="SUBTRACTOR minuend relocation",
                ),
            ))
            pending_subtractor = None
            continue
        if entry["r_type"] == spec.ARM64_RELOC_ADDEND:
            if pending_addend is not None:
                raise LinkError("consecutive ADDEND relocation entries")
            if entry["r_extern"] or entry["r_pcrel"]:
                raise LinkError(
                    "ADDEND relocation must be non-extern and non-pcrel"
                )
            pending_addend = entry
            continue
        if pending_addend is not None and (
            not entry["r_extern"]
            or entry["r_address"] != pending_addend["r_address"]
            or entry["r_length"] != pending_addend["r_length"]
        ):
            raise LinkError(
                "ADDEND must be followed by a same-address, same-width "
                "extern relocation"
            )
        if entry["r_type"] == spec.ARM64_RELOC_SUBTRACTOR and entry["r_extern"]:
            if pending_addend is not None:
                raise LinkError("ADDEND before SUBTRACTOR is not supported")
            pending_subtractor = entry
            continue
        if not entry["r_extern"]:
            if entry["r_type"] != spec.ARM64_RELOC_UNSIGNED:
                raise LinkError(
                    f"non-extern relocation type {entry['r_type']} at "
                    f"{entry['r_address']} is outside the proven subset"
                )
            if entry["r_pcrel"] or entry["r_length"] != 3:
                raise LinkError(
                    "non-extern UNSIGNED relocation must be a non-pcrel "
                    "eight-byte section target"
                )
            index = entry["r_symbolnum"]
            if not 1 <= index <= len(sections):
                raise LinkError(
                    f"section-target relocation names section {index}"
                )
            target = sections[index - 1]
            out.append(Relocation(
                offset=entry["r_address"], symbol="",
                type=entry["r_type"], pcrel=bool(entry["r_pcrel"]),
                length=entry["r_length"], addend=0,
                section=(target["segname_str"], target["sectname_str"]),
            ))
            pending_addend = None
            continue
        addend = (
            pending_addend["r_symbolnum"]
            if pending_addend is not None
            else 0
        )
        pending_addend = None
        out.append(Relocation(
            offset=entry["r_address"],
            symbol=_relocation_symbol_name(
                symbols,
                entry["r_symbolnum"],
                local_rename,
                context="extern relocation",
            ),
            type=entry["r_type"],
            pcrel=bool(entry["r_pcrel"]),
            length=entry["r_length"],
            addend=addend,
        ))
    if pending_addend is not None:
        raise LinkError("trailing ADDEND entry with nothing to modify")
    if pending_subtractor is not None:
        raise LinkError("trailing SUBTRACTOR entry without UNSIGNED pair")
    return out


def _inspect_link_input(
    item: tuple[int, LinkInput, ShardedSymbolDefinitions],
) -> _InspectedLinkInput:
    """Parse and validate one input without mutating global link state."""

    index, data, definitions = item
    obj = _coerce_link_object(data, index)
    if obj.header["filetype"] != spec.MH_OBJECT:
        raise LinkError(f"input {index} is not an MH_OBJECT")
    if (
        obj.header["cputype"] != spec.CPU_TYPE_ARM64
        or obj.header["cpusubtype"] != spec.CPU_SUBTYPE_ARM64_ALL
    ):
        raise LinkError(
            f"input {index} is not an arm64-all object "
            f"(cputype={obj.header['cputype']}, "
            f"cpusubtype={obj.header['cpusubtype']})"
        )

    symbols = obj.symbols()
    for symbol in symbols:
        if (symbol["n_type"] & spec.N_TYPE) != spec.N_UNDF:
            continue
        if (
            not (symbol["n_type"] & spec.N_EXT)
            or symbol["n_sect"] != spec.NO_SECT
            or symbol["n_value"] != 0
        ):
            raise LinkError(
                f"input {index} symbol {symbol['name']!r} uses an "
                "unsupported local/common undefined shape"
            )

    targeted: set[int] = set()
    for section in obj.sections():
        for entry in obj.relocations(section):
            if (
                entry["r_extern"]
                and entry["r_type"] != spec.ARM64_RELOC_ADDEND
            ):
                symbol_index = entry["r_symbolnum"]
                _relocation_symbol_name(
                    symbols,
                    symbol_index,
                    {},
                    context=f"input {index} relocation",
                )
                targeted.add(symbol_index)
    for symbol_index, symbol in enumerate(symbols):
        if (
            (symbol["n_type"] & spec.N_TYPE) == spec.N_SECT
            and symbol["n_type"] & spec.N_EXT
        ):
            definitions.add(
                symbol["name"],
                SymbolDefinition(index, symbol_index),
            )
    return _InspectedLinkInput(obj, symbols, frozenset(targeted))


def _link_input_size_hint(value: LinkInput) -> int:
    """Cheap byte-volume hint used only for bounded worker selection."""

    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, NativeObject):
        return sum(len(section.data) for section in value.sections)
    if isinstance(value, NativeObjectView):
        return len(value.data)
    if isinstance(value, spec.MachOObject):
        return len(value.data)
    return 0


def _coerce_indexed_link_object(item: tuple[int, LinkInput]):
    input_index, value = item
    return _coerce_link_object(value, input_index)


def _coerce_link_objects(
    values: list[LinkInput],
    *,
    start_index: int = 0,
) -> list:
    """Parse independent external objects with deterministic result order."""

    indexed = [
        (start_index + offset, value)
        for offset, value in enumerate(values)
    ]
    try:
        return ordered_parallel_map(
            indexed,
            _coerce_indexed_link_object,
            total_bytes=sum(_link_input_size_hint(value) for value in values),
        )
    except ParallelLinkError as exc:
        raise LinkError(f"parallel input parsing failed: {exc}") from exc


def link_relocatable_native(objects: list[LinkInput]) -> NativeObject:
    """Merge inputs into pcc's indexed object without a Mach-O round trip."""
    objects = list(objects)
    if not objects:
        raise LinkError("nothing to link")

    merged: dict[tuple[str, str], _MergedSection] = {}
    order: list[tuple[str, str]] = []
    defined: dict[str, tuple[str, str]] = {}  # name -> section key
    referenced: set[str] = set()
    # Dedicated stack-map sections are semantic tables, not byte streams:
    # concatenating two versioned headers would produce an apparently valid
    # first table followed by trailing garbage.  Decode each input and rebuild
    # one deterministic table after all native symbols have been inspected.
    stack_map_payloads: list[bytes] = []
    stack_map_symbol_by_function_id: dict[int, str] = {}
    # (merged section, offset, target key, this input's section addresses)
    rebases: list = []

    # Parse/scan inputs through stable worker partitions and retain
    # symbol-index provenance.  External definitions enter a sharded table,
    # but ownership is the minimum (input index, symbol index), never the
    # thread that happened to insert first.  The merge below remains in exact
    # command-line order.  Assembler-local temporaries are dropped only when
    # THIS input has no relocation to their table entry; comparing names
    # across objects conflates distinct local symbols.
    external_definitions = ShardedSymbolDefinitions()
    indexed_objects = [
        (index, value, external_definitions)
        for index, value in enumerate(objects)
    ]
    try:
        inspected_inputs = ordered_parallel_map(
            indexed_objects,
            _inspect_link_input,
            total_bytes=sum(_link_input_size_hint(value) for value in objects),
        )
        external_definitions.freeze()
    except ParallelLinkError as exc:
        raise LinkError(f"parallel input inspection failed: {exc}") from exc
    parsed_objects = [item.obj for item in inspected_inputs]
    symbol_tables = [item.symbols for item in inspected_inputs]
    relocation_target_indices = [
        item.relocation_target_indices for item in inspected_inputs
    ]

    local_renames = _local_symbol_renames(symbol_tables)

    for index, obj in enumerate(parsed_objects):

        sections = obj.sections()
        symbols = symbol_tables[index]
        local_rename = local_renames[index]

        # LC_DATA_IN_CODE uses object-address offsets, not file offsets.
        # Assign every range to exactly one input section now so its start can
        # be rebased alongside that section's payload during the merge.
        data_regions_by_section: dict[int, list[DataInCodeRegion]] = {}
        for entry in obj.data_in_code():
            start = entry["offset"]
            end = start + entry["length"]
            owners = [
                (sec_index, sec)
                for sec_index, sec in enumerate(sections, start=1)
                if sec["addr"] <= start and end <= sec["addr"] + sec["size"]
            ]
            if len(owners) != 1:
                raise LinkError(
                    "data-in-code range does not belong to exactly one "
                    f"section: {start}..{end}"
                )
            sec_index, sec = owners[0]
            data_regions_by_section.setdefault(sec_index, []).append(
                DataInCodeRegion(
                    offset=start - sec["addr"],
                    length=entry["length"],
                    kind=entry["kind"],
                )
            )

        # Where each of this input's sections lands in the merged output.
        bases: dict[int, int] = {}
        # This input's own section addresses and their merged bases, keyed by
        # (segname, sectname): a stored address is rebased through them.
        input_section_addr: dict[tuple[str, str], tuple[int, int]] = {}
        for sec_index, sec in enumerate(sections, start=1):
            key = (sec["segname_str"], sec["sectname_str"])
            if key == ("__DATA", "__pcc_stackmaps"):
                if sec["flags"] != spec.S_REGULAR or sec["align"] < 3:
                    raise LinkError("input stack-map section has invalid flags/alignment")
                payload = bytes(obj.data[sec["offset"]:sec["offset"] + sec["size"]])
                try:
                    _function_count, scanned, _tstart, _tcount = (
                        _scan_stack_map_payload(payload)
                    )
                except PreciseStackMapError as exc:
                    raise LinkError(f"input stack-map section is malformed: {exc}") from exc
                stack_relocations = _read_relocations(
                    obj, sec, symbols, local_rename,
                )
                relocation_by_offset = {
                    relocation.offset: relocation
                    for relocation in stack_relocations
                }
                address_offsets = [
                    fn_start + 8 for _fid, fn_start, _fn_end in scanned
                ]
                if (
                    len(relocation_by_offset) != len(stack_relocations)
                    or set(relocation_by_offset) != set(address_offsets)
                ):
                    raise LinkError(
                        "input stack-map function addresses and relocations disagree"
                    )
                if any(
                    payload[offset:offset + 8] != b"\0" * 8
                    for offset in address_offsets
                ):
                    raise LinkError(
                        "relocatable stack-map function address must be zero"
                    )
                if any(
                    (sym["n_type"] & spec.N_TYPE) == spec.N_SECT
                    and sym["n_sect"] == sec_index
                    for sym in symbols
                ):
                    raise LinkError("stack-map section must not define symbols")
                for (function_id, _fn_start, _fn_end), address_offset in zip(
                    scanned, address_offsets,
                ):
                    relocation = relocation_by_offset[address_offset]
                    if (
                        relocation.type != spec.ARM64_RELOC_UNSIGNED
                        or relocation.pcrel
                        or relocation.length != 3
                        or relocation.section is not None
                        or relocation.minuend is not None
                        or relocation.addend
                    ):
                        raise LinkError(
                            "stack-map address relocation is not plain UNSIGNED64"
                        )
                    previous_symbol = stack_map_symbol_by_function_id.get(
                        function_id,
                    )
                    if previous_symbol is not None:
                        raise LinkError(
                            "duplicate stable function id in stack-map inputs"
                        )
                    stack_map_symbol_by_function_id[function_id] = (
                        relocation.symbol
                    )
                    referenced.add(relocation.symbol)
                stack_map_payloads.append(payload)
                continue
            if key not in merged:
                merged[key] = _MergedSection(key[0], key[1], sec["flags"])
                order.append(key)
            target = merged[key]
            if (target.flags & spec.SECTION_TYPE) != (sec["flags"] & spec.SECTION_TYPE):
                raise LinkError(
                    f"{key} has conflicting section TYPES across inputs "
                    f"({target.flags:#x} vs {sec['flags']:#x})"
                )
            # Attributes are a union, not an identity: an input whose __text
            # happens to contain no branch targets omits SOME_INSTRUCTIONS,
            # and ld merges the attribute sets rather than refusing. The
            # section TYPE (low byte) must still agree.
            target.flags |= sec["flags"] & spec.SECTION_ATTRIBUTES
            target.align_log2 = max(target.align_log2, sec["align"])
            if _is_zerofill(sec):
                # No file payload: only the size accumulates. Symbols still
                # need an offset, so the running vm size is the base.
                base = _align_up(target.zerofill_size, sec["align"])
                target.zerofill_size = base + sec["size"]
            else:
                base = _align_up(len(target.data), sec["align"])
                padding = base - len(target.data)
                if padding and len(target.data) % 4 != 0 and (
                    target.flags & spec.S_ATTR_PURE_INSTRUCTIONS
                ):
                    # A preceding input may end in an odd-sized inline-data
                    # tail.  The zeros needed to align the next text atom are
                    # data as well, not a partial AArch64 instruction.
                    target.data_in_code.append(DataInCodeRegion(
                        offset=len(target.data),
                        length=padding,
                        kind=spec.DICE_KIND_DATA,
                    ))
                target.data.extend(b"\0" * (base - len(target.data)))
                payload = obj.data[sec["offset"]:sec["offset"] + sec["size"]]
                target.data.extend(payload)
            bases[sec_index] = base
            input_section_addr[key] = (sec["addr"], base)
            target.data_in_code.extend(
                DataInCodeRegion(
                    offset=base + region.offset,
                    length=region.length,
                    kind=region.kind,
                )
                for region in data_regions_by_section.get(sec_index, ())
            )

            for reloc in _read_relocations(
                obj, sec, symbols, local_rename,
            ):
                target.relocations.append(Relocation(
                    offset=reloc.offset + base,
                    symbol=reloc.symbol,
                    type=reloc.type,
                    pcrel=reloc.pcrel,
                    length=reloc.length,
                    addend=reloc.addend,
                    section=reloc.section,
                    minuend=reloc.minuend,
                    target_offset=reloc.target_offset,
                ))
                if reloc.section is None:
                    referenced.add(reloc.symbol)
                    if reloc.minuend is not None:
                        referenced.add(reloc.minuend)
                else:
                    # The stored value is an address in THIS input's space;
                    # record what it has to be rebased by once the merged
                    # layout is known.
                    rebases.append((
                        target, reloc.offset + base, reloc.section,
                        input_section_addr,
                    ))

        for symbol_index, sym in enumerate(symbols):
            name = sym["name"]
            if (sym["n_type"] & spec.N_TYPE) == spec.N_UNDF:
                referenced.add(name)
                continue
            if (sym["n_type"] & spec.N_TYPE) != spec.N_SECT:
                raise LinkError(f"symbol {name!r} has an unsupported type")
            if (
                name[:1] in ("l", "L")
                and symbol_index not in relocation_target_indices[index]
            ):
                # Mach-O assembler-local temporaries that nothing references:
                # they delimit atoms inside one input and mean nothing after
                # the merge. `ld -r` drops them, and so does this — keeping
                # them would make every multi-object link collide on `ltmp0`.
                # Referenced L-labels (pcc emits cstring labels this way) are
                # real targets and are kept.
                continue
            name = local_rename.get(name, name)
            if sym["n_type"] & spec.N_EXT:
                owner = external_definitions.owner(name)
                if owner != SymbolDefinition(index, symbol_index):
                    raise LinkError(f"duplicate definition of {name!r}")
            if name in defined:
                raise LinkError(f"duplicate definition of {name!r}")
            sec_index = sym["n_sect"]
            if sec_index not in bases:
                raise LinkError(f"symbol {name!r} names section {sec_index}")
            sec = sections[sec_index - 1]
            key = (sec["segname_str"], sec["sectname_str"])
            # n_value is the section-relative address in an object file;
            # subtract the section's own addr to get the payload offset.
            offset = sym["n_value"] - sec["addr"] + bases[sec_index]
            merged[key].symbols.append(TextSymbol(
                name,
                offset,
                external=bool(sym["n_type"] & spec.N_EXT),
                private_external=bool(sym["n_type"] & spec.N_PEXT),
            ))
            defined[name] = key

    if stack_map_payloads:
        try:
            stack_payload, stack_address_offsets = merge_stack_map_payloads(
                tuple(stack_map_payloads)
            )
        except PreciseStackMapError as exc:
            raise LinkError(f"merged stack-map table is invalid: {exc}") from exc
        stack_key = ("__DATA", "__pcc_stackmaps")
        stack_section = _MergedSection(
            stack_key[0], stack_key[1], spec.S_REGULAR,
            align_log2=3,
            data=bytearray(stack_payload),
        )
        for function_id, address_offset in stack_address_offsets:
            symbol = stack_map_symbol_by_function_id[function_id]
            stack_section.relocations.append(Relocation(
                offset=address_offset,
                symbol=symbol,
                type=spec.ARM64_RELOC_UNSIGNED,
                pcrel=False,
                length=3,
            ))
        merged[stack_key] = stack_section
        order.append(stack_key)

    # An unresolved symbol is valid in an MH_OBJECT: preserve it for the
    # eventual final link rather than guessing a definition.
    unresolved = sorted(referenced - set(defined))

    # Segment order decides every symbol's address; within a segment,
    # zerofill sections must come last (ld rejects content after zerofill).
    seg_rank = {"__TEXT": 0, "__DATA_CONST": 1, "__DATA": 2}
    appearance = {key: i for i, key in enumerate(order)}
    order.sort(key=lambda key: (
        seg_rank.get(key[0], 3),
        1 if (merged[key].zerofill_size and not merged[key].data) else 0,
        appearance[key],
    ))

    # A section-target relocation stores an address in its input's address
    # space. The input's target section moved to `base` inside the merged
    # section, so the stored value is rebased by (base - old section addr).
    # The merged section's own final address is added by the consumer (the
    # executable linker) or left at the object's section addr (relocatable
    # output), exactly as ld -r leaves it.
    import struct as _struct
    # Where each defined symbol landed, so a rebased address can be named.
    symbol_at: dict[tuple[tuple[str, str], int], str] = {}
    for key, section in merged.items():
        for sym in section.symbols:
            symbol_at.setdefault((key, sym.offset), sym.name)

    for target, offset, target_key, input_addrs in rebases:
        if target_key not in input_addrs:
            raise LinkError(
                f"section-target relocation names {target_key}, which the "
                "same input does not contain"
            )
        old_addr, new_base = input_addrs[target_key]
        stored, = _struct.unpack_from("<Q", target.data, offset)
        rebased = (stored - old_addr + new_base) & _U64_MASK

        # `ld -r` re-points these at the SYMBOL that owns the address and
        # zeroes the field, rather than keeping a section target with the
        # address baked in — which survives any later reordering, so pcc
        # does the same whenever a defined symbol sits exactly there.
        owner = symbol_at.get((target_key, rebased))
        entry = next(
            r for r in target.relocations
            if r.offset == offset and r.section == target_key
        )
        target.relocations.remove(entry)
        if owner is not None:
            _struct.pack_into("<Q", target.data, offset, 0)
            target.relocations.append(Relocation(
                offset=offset, symbol=owner, type=entry.type,
                pcrel=entry.pcrel, length=entry.length, addend=0,
            ))
            referenced.add(owner)
        else:
            _struct.pack_into("<Q", target.data, offset, 0)
            target.relocations.append(Relocation(
                offset=entry.offset,
                symbol="",
                type=entry.type,
                pcrel=entry.pcrel,
                length=entry.length,
                addend=0,
                section=entry.section,
                target_offset=rebased,
            ))

    out_sections = []
    for key in order:
        m = merged[key]
        zero = m.zerofill_size if not m.data else 0
        out_sections.append(Section(
            sectname=m.sectname, segname=m.segname,
            data=bytes(m.data),
            align_log2=m.align_log2, flags=m.flags,
            symbols=tuple(sorted(m.symbols, key=lambda s: s.offset)),
            relocations=tuple(m.relocations),
            data_in_code=tuple(sorted(
                m.data_in_code, key=lambda region: region.offset,
            )),
            zerofill_size=zero,
        ))
    try:
        return NativeObject.from_sections(out_sections, undefined=unresolved)
    except (MachOEmitError, NativeObjectError) as exc:
        raise LinkError(f"merged object is outside the proven subset: {exc}") from exc


def link_relocatable(objects: list[LinkInput]) -> bytes:
    """Merge objects into a standard relocatable object, as ``ld -r`` does.

    Standard Mach-O materialisation is kept at this public/external boundary;
    final pcc-to-pcc links call :func:`link_relocatable_native` directly.
    """
    try:
        return link_relocatable_native(list(objects)).to_macho()
    except (MachOEmitError, NativeObjectError) as exc:
        raise LinkError(f"merged object is outside the proven subset: {exc}") from exc
