from __future__ import annotations

"""Target-final precise stack-map planning for the owned self backends.

The frontend's registered-slot protocol remains the source of truth.  This
module does not infer managed pointers from LLVM ``ptr`` values: it follows
only explicit ``pcc_gc_frame_enter{,_lifo}`` calls, resolves their frame-map
global and slot address, and rejects an unresolved stack-derived address.

Plans are attached to labels after target allocation.  AArch64 resolves those
labels to final numeric offsets after peepholes; x86 emits assembler label
differences so the variable-length encoder owns the final PCs.
"""

from dataclasses import dataclass

from . import BackendUnavailable
from .precise_stackmap import (
    ARCH_AARCH64,
    ARCH_X86_64,
    FunctionStackMap,
    LOCATION_MANAGED,
    LOCATION_OWNED,
    LOCATION_STACK_INDIRECT,
    MAGIC,
    NO_BASE,
    NO_OFFSET,
    POINTER_SIZE,
    PreciseStackMap,
    RECORD_HAS_EXCEPTION_EDGE,
    RECORD_SUSPENDED,
    SAFEPOINT_CALL,
    SAFEPOINT_CONTINUATION,
    SAFEPOINT_ENTRY,
    SAFEPOINT_EXCEPTION,
    SAFEPOINT_KINDS,
    SAFEPOINT_LOOP,
    SafepointRecord,
    StackMapLocation,
    VERSION,
    function_id,
    render_stack_map_assembly,
    safepoint_id,
    stable_id_prefix_state,
    stable_id_resume,
    scoped_stable_id,
    validate_stack_map,
)
from .self_backend_aarch64_darwin_regs import emit_add_offset
from .self_backend_aarch64_darwin_slots import (
    load_slot_to_reg,
    store_reg_to_slot,
)
from .self_backend_ir import (
    GlobalDef,
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    SlotInfo,
    TypeDesc,
    _align_to,
)
from .self_backend_parse import const_int_from_value
from .self_backend_analysis import (
    instruction_defined_value,
    instruction_used_values,
    terminator_used_values,
)


_FRAME_ENTER = frozenset(("pcc_gc_frame_enter", "pcc_gc_frame_enter_lifo"))
_FRAME_LEAVE = frozenset(("pcc_gc_frame_leave", "pcc_gc_frame_leave_lifo"))
_FRAME_PROTOCOL = _FRAME_ENTER | _FRAME_LEAVE


@dataclass(frozen=True)
class PlannedRootLocation:
    offset: int
    owned: bool


@dataclass(frozen=True)
class PlannedManagedReload:
    source_offset: int
    destination_offset: int
    derived_offset: int = 0


@dataclass(frozen=True)
class PlannedSafepoint:
    safepoint_id: int
    label: str
    kind: int
    locations: tuple[PlannedRootLocation, ...]
    flags: int = 0
    exceptional_block: str = ""
    continuation_id: int = 0
    reloads: tuple[PlannedManagedReload, ...] = ()


@dataclass(frozen=True)
class FunctionStackMapPlan:
    function_name: str
    function_id: int
    frame_size: int
    end_label: str
    records: tuple[PlannedSafepoint, ...]
    block_entry_labels: tuple[tuple[str, str], ...]
    instruction_suffix_labels: tuple[tuple[str, int, str], ...]
    terminator_prefix_labels: tuple[tuple[str, str, bool], ...]
    target: str

    def block_entry_lines(self, block: ParsedBlock) -> list[str]:
        return [
            label + ":"
            for block_name, label in self.block_entry_labels
            if block_name == block.name
        ]

    def _reload_asm_lines(self, record: PlannedSafepoint) -> list[str]:
        lines: list[str] = []
        for reload in record.reloads:
            if self.target == "aarch64-darwin":
                pointer_type = TypeDesc(
                    "ptr", pointee=TypeDesc("void")
                )
                lines.extend(load_slot_to_reg(
                    SlotInfo(-reload.source_offset, pointer_type),
                    "x16",
                ))
                lines.extend(emit_add_offset(
                    "x16", "x16", reload.derived_offset,
                ))
                lines.extend(store_reg_to_slot(
                    "x16",
                    SlotInfo(-reload.destination_offset, pointer_type),
                ))
            else:
                source = f"[rbp - {-reload.source_offset}]"
                destination = f"[rbp - {-reload.destination_offset}]"
                lines.append(f"  mov r11, QWORD PTR {source}")
                if reload.derived_offset:
                    lines.append(f"  add r11, {reload.derived_offset}")
                lines.append(f"  mov QWORD PTR {destination}, r11")
        return lines

    def instruction_suffix_lines(
        self, block: ParsedBlock, instruction_index: int
    ) -> list[str]:
        lines: list[str] = []
        for block_name, index, label in self.instruction_suffix_labels:
            if block_name != block.name or index != instruction_index:
                continue
            lines.append(label + ":")
            record = next(
                item for item in self.records if item.label == label
            )
            lines.extend(self._reload_asm_lines(record))
        return lines

    def build_line_index(
        self,
    ) -> tuple[
        dict[str, list[str]],
        dict[str, dict[int, list[str]]],
        dict[str, list[str]],
    ]:
        """One-pass per-block/per-instruction emission-line index.

        The per-call methods above scan every label on every call; over a
        72k-block generated module top that is O(instructions x labels) and
        dominates the whole emit. Emit loops build this index once per
        function; line order matches the per-call methods exactly, so the
        emitted text is byte-identical.
        """
        entry: dict[str, list[str]] = {}
        for block_name, label in self.block_entry_labels:
            if block_name in entry:
                entry[block_name].append(label + ":")
            else:
                entry[block_name] = [label + ":"]
        records_by_label: dict[str, PlannedSafepoint] = {}
        for item in self.records:
            records_by_label[item.label] = item
        suffix: dict[str, dict[int, list[str]]] = {}
        for block_name, index, label in self.instruction_suffix_labels:
            lines = [label + ":"]
            lines.extend(self._reload_asm_lines(records_by_label[label]))
            if block_name in suffix:
                per_block = suffix[block_name]
            else:
                per_block = {}
                suffix[block_name] = per_block
            if index in per_block:
                per_block[index].extend(lines)
            else:
                per_block[index] = lines
        term: dict[str, list[str]] = {}
        for block_name, label, needs_separator in self.terminator_prefix_labels:
            lines = []
            if needs_separator:
                lines.append("  nop")
            lines.append(label + ":")
            if block_name in term:
                term[block_name].extend(lines)
            else:
                term[block_name] = lines
        return entry, suffix, term

    def terminator_prefix_lines(self, block: ParsedBlock) -> list[str]:
        lines: list[str] = []
        for block_name, label, needs_separator in self.terminator_prefix_labels:
            if block_name != block.name:
                continue
            if needs_separator:
                lines.append("  nop")
            lines.append(label + ":")
        return lines


@dataclass(frozen=True)
class _PointerOrigin:
    base: str
    offset: int


@dataclass(frozen=True)
class _RootGroup:
    key: str
    locations: tuple[PlannedRootLocation, ...]


@dataclass(frozen=True)
class _ManagedValueOrigin:
    root_offset: int
    derived_offset: int = 0


_RAW_POINTER = "raw-pointer"
_AMBIGUOUS_POINTER = "ambiguous-managed-pointer"


def _fail(func: ParsedFunction, detail: str) -> None:
    raise BackendUnavailable(
        f"self precise stack-map analysis in {func.name!r}: {detail}"
    )


def _successors(term: ParsedInstr) -> tuple[str, ...]:
    if term.kind == "br":
        return (term.data[0],)
    if term.kind == "br_cond":
        return (term.data[1], term.data[2])
    if term.kind == "switch":
        return (
            term.data[2],
            *(target for _case_value, target in term.data[3]),
        )
    return ()


def _constant_gep_offset(base_type, indices) -> int | None:
    if not indices:
        return None
    first = const_int_from_value(indices[0][1])
    if first is None:
        return None
    offset = first * base_type.slot_size
    current = base_type
    for _index_type, index_value in indices[1:]:
        index = const_int_from_value(index_value)
        if index is None:
            return None
        if current.is_array:
            if current.elem is None:
                return None
            stride = _align_to(current.elem.slot_size, current.elem.align)
            offset += index * stride
            current = current.elem
        elif current.is_struct:
            offset += current.field_offset(index)
            current = current.field_type(index)
        else:
            return None
    return offset


def _pointer_aliases(func: ParsedFunction) -> dict[str, _PointerOrigin]:
    aliases: dict[str, _PointerOrigin] = {}
    for block in func.blocks:
        for instr in block.instructions:
            if instr.kind == "cast":
                _op, dest, src_type, source, dst_type = instr.data
                if src_type.is_ptr and dst_type.is_ptr:
                    aliases[dest] = _PointerOrigin(source, 0)
            elif instr.kind == "gep":
                dest, base_type, _ptr_type, source, indices = instr.data
                offset = _constant_gep_offset(base_type, indices)
                if offset is not None:
                    aliases[dest] = _PointerOrigin(source, offset)
    return aliases


def _resolve_pointer(
    func: ParsedFunction,
    aliases: dict[str, _PointerOrigin],
    value: str,
) -> _PointerOrigin:
    current = value
    offset = 0
    seen: set[str] = set()
    while current in aliases:
        if current in seen:
            _fail(func, f"pointer alias cycle for {value!r}")
        seen.add(current)
        alias = aliases[current]
        current = alias.base
        offset += alias.offset
    return _PointerOrigin(current, offset)


def _first_i32_initializer(global_: GlobalDef) -> int | None:
    text = global_.initializer.strip()
    direct = const_int_from_value(text)
    if direct is not None:
        return direct
    # Root maps may grow descriptor words after the signed root-count word.
    # The runtime contract reads the first i32, so accept only that explicit
    # typed prefix instead of guessing from arbitrary aggregate text.
    if text.startswith("[") or text.startswith("{"):
        marker = "i32 "
        index = text.find(marker)
        if index >= 0:
            tail = text[index + len(marker) :]
            token = tail.split(",", 1)[0].split("]", 1)[0].split("}", 1)[0]
            return const_int_from_value(token.strip())
    return None


def _frame_map_count(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
    value: str,
) -> tuple[int, bool]:
    origin = _resolve_pointer(func, aliases, value)
    if origin.offset != 0 or not origin.base.startswith("@"):
        _fail(func, f"frame map {value!r} is not one direct global")
    name = origin.base[1:]
    global_ = globals_by_name.get(name)
    if global_ is None:
        _fail(func, f"frame map global {name!r} is unavailable")
    count = _first_i32_initializer(global_)
    if count is None:
        _fail(func, f"frame map global {name!r} has no signed i32 count")
    if abs(count) > 0xFFFF:
        _fail(func, f"frame map global {name!r} exceeds the ABI location bound")
    return abs(count), count > 0


def _root_group(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
    frame_map_value: str,
    slots_value: str,
) -> _RootGroup:
    count, owned = _frame_map_count(
        func, globals_by_name, aliases, frame_map_value
    )
    origin = _resolve_pointer(func, aliases, slots_value)
    key = f"{origin.base}@{origin.offset}"
    if count == 0 or origin.base == "null":
        return _RootGroup(key, ())
    alloca = func.alloca_slots.get(origin.base)
    if alloca is None:
        if origin.base.startswith("@") or any(
            arg.name == origin.base for arg in func.args
        ):
            # Explicit global/heap/continuation slot arrays are owned by the
            # existing registry, not by this function's machine stack map.
            return _RootGroup(key, ())
        _fail(
            func,
            f"managed slot address {slots_value!r} cannot be resolved to "
            "one stack alloca or explicit non-stack owner",
        )
    byte_count = count * POINTER_SIZE
    if origin.offset < 0 or origin.offset + byte_count > alloca.allocated_type.slot_size:
        _fail(func, f"managed slot range {slots_value!r} exceeds its alloca")
    locations: list[PlannedRootLocation] = []
    for index in range(count):
        frame_offset = -alloca.offset + origin.offset + index * POINTER_SIZE
        if (
            frame_offset >= 0
            or -frame_offset > func.frame_size
            or (-frame_offset) % POINTER_SIZE
        ):
            _fail(func, f"managed slot {slots_value!r} is outside the final frame")
        locations.append(PlannedRootLocation(frame_offset, owned))
    return _RootGroup(key, tuple(locations))


def _direct_call(instr: ParsedInstr) -> tuple[str, tuple] | None:
    if instr.kind != "call":
        return None
    (
        _dest,
        _ret_type,
        callee,
        is_indirect,
        args,
        _fixed_arg_count,
        _is_vararg,
        _arg_alignments,
    ) = instr.data
    if is_indirect:
        return "", args
    return callee, args


def _apply_frame_protocol(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
    active: dict[str, _RootGroup],
    instr: ParsedInstr,
) -> bool:
    call = _direct_call(instr)
    if call is None:
        return False
    callee, args = call
    if callee in _FRAME_ENTER:
        if len(args) != 2:
            _fail(func, f"{callee} has the wrong argument count")
        group = _root_group(
            func,
            globals_by_name,
            aliases,
            args[0][1],
            args[1][1],
        )
        slots_origin = _resolve_pointer(func, aliases, args[1][1])
        if (
            slots_origin.base == "null"
            or slots_origin.base.startswith("@")
            or any(arg.name == slots_origin.base for arg in func.args)
        ):
            # Global, heap/continuation and caller-owned slot arrays are
            # registered roots, but they are not locations in this function's
            # machine frame.  Their lifetime may deliberately cross this
            # function (module globals do), so including them in the local
            # control-flow state creates a false join mismatch on an
            # already-initialized fast path.
            return True
        if group.key in active:
            _fail(func, f"managed slot {group.key!r} is registered twice")
        active[group.key] = group
        return True
    if callee in _FRAME_LEAVE:
        if len(args) != 1:
            _fail(func, f"{callee} has the wrong argument count")
        origin = _resolve_pointer(func, aliases, args[0][1])
        if (
            origin.base == "null"
            or origin.base.startswith("@")
            or any(arg.name == origin.base for arg in func.args)
        ):
            return True
        key = f"{origin.base}@{origin.offset}"
        if key not in active:
            _fail(func, f"managed slot {key!r} leaves without an active enter")
        del active[key]
        return True
    return False


def _state(active: dict[str, _RootGroup]) -> tuple[_RootGroup, ...]:
    return tuple(active[key] for key in sorted(active))


def _location_sort_key(location: PlannedRootLocation) -> int:
    """Single-int stand-in for the ordering ``(offset, not owned)``.

    Ordering is identical: doubling the offset leaves a gap of at least two
    between distinct offsets, so the low bit can carry the owned-first tie
    break without ever reaching the next offset.  Negative offsets are fine for
    the same reason.

    The point is the *type*: a tuple key allocates one 2-tuple per element, and
    `list.sort` calls the key once per element.  Merging ~354 roots 12186 times
    for one function meant ~4.3 million tuple allocations, every one of which
    enters the managed-pointer index under pcc1.  An int key stays in the
    tagged lane and allocates nothing.
    """
    if location.owned:
        return location.offset * 2
    return location.offset * 2 + 1


def _locations(active: dict[str, _RootGroup]) -> tuple[PlannedRootLocation, ...]:
    locations = [location for group in active.values() for location in group.locations]
    locations.sort(key=_location_sort_key)
    return tuple(locations)


def _block_entry_states(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
) -> dict[str, tuple[_RootGroup, ...]]:
    if not func.blocks:
        return {}
    blocks = {block.name: block for block in func.blocks}
    entries: dict[str, tuple[_RootGroup, ...]] = {func.blocks[0].name: ()}
    queue = [func.blocks[0].name]
    while queue:
        name = queue.pop(0)
        block = blocks[name]
        active = {group.key: group for group in entries[name]}
        for instr in block.instructions:
            _apply_frame_protocol(
                func, globals_by_name, aliases, active, instr
            )
        assert block.terminator is not None
        outgoing = _state(active)
        for successor in _successors(block.terminator):
            previous = entries.get(successor)
            if previous is None:
                entries[successor] = outgoing
                queue.append(successor)
            elif previous != outgoing:
                _fail(
                    func,
                    f"managed root state disagrees at block join {successor!r}",
                )
    return entries


def _registered_stack_root_offsets(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
) -> dict[tuple[str, int], int]:
    """Map each explicitly registered stack slot to its final FP offset."""

    roots: dict[tuple[str, int], int] = {}
    for block in func.blocks:
        for instr in block.instructions:
            call = _direct_call(instr)
            if call is None or call[0] not in _FRAME_ENTER:
                continue
            callee, args = call
            if len(args) != 2:
                _fail(func, f"{callee} has the wrong argument count")
            count, _owned = _frame_map_count(
                func, globals_by_name, aliases, args[0][1]
            )
            group = _root_group(
                func, globals_by_name, aliases, args[0][1], args[1][1]
            )
            # Global, heap, and continuation slot arrays remain registry
            # roots.  Only an actual machine-frame location can refresh a
            # spilled SSA value after a moving-collector safepoint.
            if count == 0 or len(group.locations) == 0:
                continue
            if len(group.locations) != count:
                _fail(func, f"managed slot group {group.key!r} is incomplete")
            origin = _resolve_pointer(func, aliases, args[1][1])
            for index, location in enumerate(group.locations):
                key = (origin.base, origin.offset + index * POINTER_SIZE)
                previous = roots.get(key)
                if previous is not None and previous != location.offset:
                    _fail(func, f"managed slot {key!r} has conflicting offsets")
                roots[key] = location.offset
    return roots


def _join_pointer_states(states: list):
    origins: list[_ManagedValueOrigin] = []
    saw_raw = False
    saw_known = False
    for state in states:
        if state is None:
            continue
        saw_known = True
        if state == _AMBIGUOUS_POINTER:
            return _AMBIGUOUS_POINTER
        if state == _RAW_POINTER:
            saw_raw = True
            continue
        if state not in origins:
            origins.append(state)
    if len(origins) > 1 or (origins and saw_raw):
        return _AMBIGUOUS_POINTER
    if origins:
        return origins[0]
    if saw_raw:
        return _RAW_POINTER
    if saw_known:
        return _RAW_POINTER
    return None


def _managed_value_origins(
    func: ParsedFunction,
    globals_by_name: dict[str, GlobalDef],
    aliases: dict[str, _PointerOrigin],
) -> tuple[dict[str, _ManagedValueOrigin], frozenset[str]]:
    """Classify root-derived pointer SSA without treating every ``ptr`` as GC."""

    registered = _registered_stack_root_offsets(
        func, globals_by_name, aliases
    )
    states: dict[str, object | None] = {}
    transfers: dict[str, tuple[str, tuple]] = {}

    for arg in func.args:
        if arg.type.is_ptr:
            states[arg.name] = _RAW_POINTER
    for block in func.blocks:
        for phi in block.phis:
            if phi.type.is_ptr:
                states[phi.dest] = None
                transfers[phi.dest] = (
                    "join", tuple(item.value for item in phi.incoming)
                )
        for instr in block.instructions:
            dest = instruction_defined_value(instr)
            if dest is None:
                continue
            value_type = func.value_types.get(dest)
            if value_type is None or not value_type.is_ptr:
                continue
            if instr.kind in ("load", "load_atomic"):
                pointer_value = instr.data[3]
                pointer = _resolve_pointer(func, aliases, pointer_value)
                root_offset = registered.get((pointer.base, pointer.offset))
                if root_offset is None:
                    states[dest] = _RAW_POINTER
                else:
                    states[dest] = _ManagedValueOrigin(root_offset)
                continue
            if instr.kind == "cast":
                _op, _dest, src_type, source, _dst_type = instr.data
                if src_type.is_ptr:
                    states[dest] = None
                    transfers[dest] = ("copy", (source,))
                else:
                    states[dest] = _RAW_POINTER
                continue
            if instr.kind == "freeze":
                states[dest] = None
                transfers[dest] = ("copy", (instr.data[2],))
                continue
            if instr.kind == "gep":
                _dest, base_type, _ptr_type, source, indices = instr.data
                states[dest] = None
                transfers[dest] = (
                    "gep", (source, _constant_gep_offset(base_type, indices))
                )
                continue
            if instr.kind == "select":
                states[dest] = None
                transfers[dest] = (
                    "join", (instr.data[3], instr.data[4])
                )
                continue
            # Calls, allocas, aggregate projections, and inttoptr values have
            # no explicit root provenance.  They remain raw rather than being
            # guessed managed solely from LLVM's opaque pointer type.
            states[dest] = _RAW_POINTER

    def state_for(value: str):
        return states.get(value, _RAW_POINTER)

    def transferred(kind: str, data: tuple):
        if kind == "copy":
            return state_for(data[0])
        if kind == "gep":
            source, offset = data
            source_state = state_for(source)
            if source_state is None:
                return None
            if source_state == _AMBIGUOUS_POINTER:
                return _AMBIGUOUS_POINTER
            if source_state == _RAW_POINTER:
                return _RAW_POINTER
            if offset is None:
                return _AMBIGUOUS_POINTER
            return _ManagedValueOrigin(
                source_state.root_offset,
                source_state.derived_offset + offset,
            )
        return _join_pointer_states([state_for(value) for value in data])

    def converge() -> None:
        changed = True
        while changed:
            changed = False
            for name, (kind, data) in transfers.items():
                old = states[name]
                if old == _AMBIGUOUS_POINTER:
                    continue
                proposed = transferred(kind, data)
                if proposed is None or proposed == old:
                    continue
                if old is None:
                    states[name] = proposed
                else:
                    # The lattice is monotonic: once two incompatible raw or
                    # managed explanations reach a join, never pick one based
                    # on traversal order.
                    states[name] = _AMBIGUOUS_POINTER
                changed = True

    converge()
    for name, state in tuple(states.items()):
        if state is None:
            states[name] = _AMBIGUOUS_POINTER
    converge()
    origins = {
        name: state
        for name, state in states.items()
        if isinstance(state, _ManagedValueOrigin)
    }
    ambiguous = frozenset(
        name for name, state in states.items()
        if state == _AMBIGUOUS_POINTER
    )
    return origins, ambiguous


def _managed_live_after(
    func: ParsedFunction,
    tracked: frozenset[str],
) -> dict[tuple[str, int], frozenset[str]]:
    """Return managed/ambiguous SSA values live after each instruction."""

    blocks = {block.name: block for block in func.blocks}
    uses: dict[str, set[str]] = {}
    definitions: dict[str, set[str]] = {}
    for block in func.blocks:
        defined = {
            phi.dest for phi in block.phis if phi.dest in tracked
        }
        used: set[str] = set()
        for instr in block.instructions:
            for value in instruction_used_values(instr):
                if value in tracked and value not in defined:
                    used.add(value)
            dest = instruction_defined_value(instr)
            if dest in tracked:
                defined.add(dest)
        assert block.terminator is not None
        for value in terminator_used_values(block.terminator):
            if value in tracked and value not in defined:
                used.add(value)
        uses[block.name] = used
        definitions[block.name] = defined

    live_in = {block.name: set() for block in func.blocks}
    live_out = {block.name: set() for block in func.blocks}
    changed = True
    while changed:
        changed = False
        for block in reversed(func.blocks):
            assert block.terminator is not None
            outgoing: set[str] = set()
            for successor_name in _successors(block.terminator):
                successor = blocks.get(successor_name)
                if successor is None:
                    _fail(func, f"unknown CFG successor {successor_name!r}")
                outgoing.update(live_in[successor_name])
                for phi in successor.phis:
                    for incoming in phi.incoming:
                        if (
                            incoming.label == block.name
                            and incoming.value in tracked
                        ):
                            outgoing.add(incoming.value)
            incoming = uses[block.name] | (
                outgoing - definitions[block.name]
            )
            if outgoing != live_out[block.name]:
                live_out[block.name] = outgoing
                changed = True
            if incoming != live_in[block.name]:
                live_in[block.name] = incoming
                changed = True

    result: dict[tuple[str, int], frozenset[str]] = {}
    for block in func.blocks:
        live = set(live_out[block.name])
        assert block.terminator is not None
        live.update(
            value for value in terminator_used_values(block.terminator)
            if value in tracked
        )
        index = len(block.instructions) - 1
        while index >= 0:
            instr = block.instructions[index]
            result[(block.name, index)] = frozenset(live)
            dest = instruction_defined_value(instr)
            if dest in tracked:
                live.discard(dest)
            live.update(
                value for value in instruction_used_values(instr)
                if value in tracked
            )
            index -= 1
    return result


def _planned_managed_reloads(
    func: ParsedFunction,
    active_offsets: set,
    live_values: frozenset[str],
    origins: dict[str, _ManagedValueOrigin],
    ambiguous: frozenset[str],
    target: str,
) -> tuple[PlannedManagedReload, ...]:
    # `active_offsets` is supplied by the caller rather than rebuilt here.  It
    # is a function of `active` alone, which only changes at a block boundary or
    # on a frame-protocol instruction, while this runs once per safepoint — so
    # rebuilding it here allocated a set sized by the live-root count for every
    # record.  The caller caches it on the same version counter that guards
    # `_locations`.
    if not live_values:
        return ()
    reloads: list[PlannedManagedReload] = []
    destinations: dict[int, PlannedManagedReload] = {}
    for name in sorted(live_values):
        if name in ambiguous:
            _fail(
                func,
                f"stale managed SSA value {name!r} has ambiguous root provenance",
            )
        origin = origins.get(name)
        if origin is None:
            continue
        if origin.root_offset not in active_offsets:
            _fail(
                func,
                f"stale managed SSA value {name!r} outlives its active root",
            )
        slot = func.value_slots.get(name)
        if slot is None or not slot.type.is_ptr:
            _fail(func, f"managed SSA value {name!r} has no pointer spill slot")
        destination = -slot.offset
        if (
            destination >= 0
            or -destination > func.frame_size
            or (-destination) % POINTER_SIZE
        ):
            _fail(func, f"managed SSA value {name!r} has an invalid spill slot")
        if target == "x86_64-linux" and not (
            -(1 << 31) <= origin.derived_offset < (1 << 31)
        ):
            _fail(func, f"derived managed SSA value {name!r} exceeds x86 imm32")
        # Positional in declaration order: same reason as the PlannedSafepoint
        # construction below — this is on the per-call-site reload path.
        reload = PlannedManagedReload(
            origin.root_offset,
            destination,
            origin.derived_offset,
        )
        if destination == origin.root_offset and origin.derived_offset == 0:
            continue
        previous = destinations.get(destination)
        if previous is not None and previous != reload:
            _fail(func, f"live managed SSA values share spill offset {destination}")
        if previous is None:
            destinations[destination] = reload
            reloads.append(reload)
    reloads.sort(key=lambda item: (
        item.destination_offset, item.source_offset, item.derived_offset,
    ))
    return tuple(reloads)


def _exception_successor(
    block: ParsedBlock, instruction_index: int, call_dest: str | None
) -> str:
    if call_dest is None or block.terminator is None or block.terminator.kind != "br_cond":
        return ""
    branch_value, true_target, false_target = block.terminator.data
    for instr in block.instructions[instruction_index + 1 :]:
        if instr.kind != "icmp":
            continue
        condition, dest, _value_type, lhs, rhs = instr.data
        if dest != branch_value:
            continue
        if lhs == call_dest and const_int_from_value(rhs) == 0:
            error_when_true = condition in ("ne", "ugt", "sgt")
            no_error_when_true = condition in ("eq", "ule", "sle")
        elif rhs == call_dest and const_int_from_value(lhs) == 0:
            error_when_true = condition in ("ne", "ult", "slt")
            no_error_when_true = condition in ("eq", "uge", "sge")
        else:
            continue
        if error_when_true:
            return true_target
        if no_error_when_true:
            return false_target
    return ""


def _record_kind(
    block: ParsedBlock, instruction_index: int, instr: ParsedInstr
) -> tuple[int, str, int, int] | None:
    call = _direct_call(instr)
    if call is None:
        return None
    callee, _args = call
    if callee in _FRAME_PROTOCOL or callee.startswith("llvm."):
        return None
    dest = instr.data[0]
    if callee == "py_err_occurred":
        exceptional = _exception_successor(block, instruction_index, dest)
        return (
            SAFEPOINT_EXCEPTION,
            exceptional,
            RECORD_HAS_EXCEPTION_EDGE if exceptional else 0,
            0,
        )
    if "continuation" in callee or "__gen_resume" in callee or "__vthread_resume" in callee:
        continuation = scoped_stable_id(
            "continuation", callee or "indirect"
        ) & 0xFFFFFFFF
        return (
            SAFEPOINT_CONTINUATION,
            "",
            RECORD_SUSPENDED,
            continuation or 1,
        )
    if callee in ("pcc_thread_safepoint", "pcc_gc_safepoint"):
        return SAFEPOINT_LOOP, "", 0, 0
    return SAFEPOINT_CALL, "", 0, 0


def _local_label(function_value: int, ordinal: int, target: str) -> str:
    # `target` is deliberately positional, not keyword-only.  A call that
    # must pass a keyword goes through the generic `py_func_call_kwargs`
    # path (build a kwargs dict, resolve each name against the signature),
    # and this is called once per safepoint from `add_record`, which is
    # ~98% of emitting an oversized module.
    prefix = "L_pcc_smap" if target == "aarch64-darwin" else ".Lpcc_smap"
    return f"{prefix}_{function_value:016x}_{ordinal}"


def build_function_stack_map_plan(
    func: ParsedFunction,
    globals_: list[GlobalDef],
    *,
    target: str,
    identity_name: str = "",
) -> FunctionStackMapPlan:
    if target not in ("aarch64-darwin", "x86_64-linux"):
        _fail(func, f"unsupported stack-map target {target!r}")
    globals_by_name = {global_.name: global_ for global_ in globals_}
    aliases = _pointer_aliases(func)
    entries = _block_entry_states(func, globals_by_name, aliases)
    managed_origins, ambiguous_managed = _managed_value_origins(
        func, globals_by_name, aliases
    )
    managed_names = frozenset(managed_origins) | ambiguous_managed
    live_after = _managed_live_after(func, managed_names)
    block_indices = {block.name: index for index, block in enumerate(func.blocks)}
    identity = identity_name or func.name
    fid = function_id(identity)
    # NOTE: do not "optimize" this by streaming the identity hash from a
    # cached prefix state (`stable_id_prefix_state` / `stable_id_resume`).
    # That is bit-identical and 40% faster on host CPython, but it was a large
    # NET LOSS under a self-compiled pcc1: resuming needs `("\0" + part)` plus
    # an `encode`, i.e. three fresh objects per safepoint, and every one of
    # them enters the global managed-pointer index.  A smoke input that builds
    # in seconds took >26 minutes at 15.6 GB, with 59% of the profile in
    # `pcc_gc_managed_pointer_find_slot`.  `safepoint_id` allocates once via
    # `"\0".join`, and under pcc1 allocation count dominates interpreter-loop
    # count.  Optimize this file against pcc1 measurements, never against host
    # CPython measurements.
    # `_locations(active)` rebuilds and re-sorts the whole live-root set on
    # every safepoint, and consecutive safepoints overwhelmingly share one set
    # (the encoded stack maps measured 34x location redundancy).  `active` is
    # only ever replaced at a block boundary or mutated by
    # `_apply_frame_protocol`, and the protocol branch never emits a record,
    # so a version counter bumped at exactly those two points is enough to
    # reuse the previous tuple.  This removes both the sort and one allocation
    # per safepoint, and with them the GC index insert/probe traffic that made
    # `pcc_gc_managed_pointer_find_slot` 41% of the emit.
    active_version = 0
    cached_locations_version = -1
    cached_locations: tuple[PlannedRootLocation, ...] = ()
    # Distinct location tuples are far rarer than the states that produce
    # them: one oversized shard had 38540 records over 1446 distinct
    # tuples, and even after the version memo removes 68% of the calls,
    # 12186 merges still produced only 2465 distinct answers.
    # `_RootGroup` objects are SHARED -- `_block_entry_states` stores one
    # tuple of groups per block and the `active` dict only re-references
    # them -- so the sorted ids of the active groups are a valid
    # fingerprint costing one small int tuple, instead of flattening and
    # merge-sorting ~354 roots again.
    #
    # An id()-keyed cache is only sound while every keyed object stays
    # alive: a freed group's address can be handed to a different group,
    # and the stale fingerprint would then HIT and return a wrong
    # location tuple rather than miss.  So each entry keeps the groups it
    # was keyed on alive alongside the answer.  That is 2465 small tuples
    # for the oversized shard, against 12186 avoided merges.
    interned_locations: dict = {}
    cached_offsets_version = -1
    cached_active_offsets: set = set()
    records: list[PlannedSafepoint] = []
    block_labels: list[tuple[str, str]] = []
    instruction_labels: list[tuple[str, int, str]] = []
    terminator_labels: list[tuple[str, str, bool]] = []
    ordinal = 0

    def add_record(
        kind: int,
        active: dict[str, _RootGroup],
        exceptional_block: str = "",
        flags: int = 0,
        continuation_id: int = 0,
        reloads: tuple[PlannedManagedReload, ...] = (),
    ) -> PlannedSafepoint:
        nonlocal ordinal, cached_locations_version, cached_locations
        # Positional, in declaration order, deliberately.  A keyword call goes
        # through the generic `py_func_call_kwargs` path — build a kwargs
        # dict, then resolve every name against the signature — and this runs
        # once per safepoint.  Emitting one oversized shard spent 70% of
        # `add_record` in that path alone, and `add_record` was 98.8% of the
        # whole emit.  Keep these positional and in sync with the
        # `PlannedSafepoint` field order.
        if cached_locations_version != active_version:
            fingerprint_parts = []
            for group in active.values():
                fingerprint_parts.append(id(group))
            fingerprint_parts.sort()
            fingerprint = tuple(fingerprint_parts)
            # `in` + subscript, not `.get()`: dict.get mis-lowers in the
            # self-compiled frontend, and this runs inside pcc1's own
            # backend.
            if fingerprint in interned_locations:
                entry = interned_locations[fingerprint]
            else:
                entry = (_locations(active), tuple(active.values()))
                interned_locations[fingerprint] = entry
            cached_locations = entry[0]
            cached_locations_version = active_version
        record = PlannedSafepoint(
            safepoint_id(identity, ordinal, kind),
            _local_label(fid, ordinal, target),
            kind,
            cached_locations,
            flags,
            exceptional_block,
            continuation_id,
            reloads,
        )
        records.append(record)
        ordinal += 1
        return record

    def active_offsets_for_version() -> set:
        nonlocal cached_offsets_version, cached_active_offsets
        if cached_offsets_version != active_version:
            cached_active_offsets = {
                location.offset
                for group in active.values()
                for location in group.locations
            }
            cached_offsets_version = active_version
        return cached_active_offsets

    for block_index, block in enumerate(func.blocks):
        if block.name not in entries:
            continue
        active = {group.key: group for group in entries[block.name]}
        active_version += 1
        if block_index == 0:
            entry = add_record(SAFEPOINT_ENTRY, active)
            block_labels.append((block.name, entry.label))
        last_instruction_has_record = False
        for instruction_index, instr in enumerate(block.instructions):
            if _apply_frame_protocol(
                func, globals_by_name, aliases, active, instr
            ):
                active_version += 1
                last_instruction_has_record = False
                continue
            kind_info = _record_kind(block, instruction_index, instr)
            if kind_info is None:
                last_instruction_has_record = False
                continue
            kind, exceptional, flags, continuation = kind_info
            record = add_record(
                kind,
                active,
                exceptional,
                flags,
                continuation,
                _planned_managed_reloads(
                    func,
                    active_offsets_for_version(),
                    live_after.get((block.name, instruction_index), frozenset()),
                    managed_origins,
                    ambiguous_managed,
                    target,
                ),
            )
            instruction_labels.append(
                (block.name, instruction_index, record.label)
            )
            last_instruction_has_record = instruction_index == len(block.instructions) - 1
        assert block.terminator is not None
        is_backedge = any(
            block_indices.get(target_name, len(func.blocks)) <= block_index
            for target_name in _successors(block.terminator)
        )
        already_loop = bool(records and records[-1].kind == SAFEPOINT_LOOP and last_instruction_has_record)
        if is_backedge and not already_loop:
            loop = add_record(SAFEPOINT_LOOP, active)
            # Give every loop record a dedicated machine PC.  An empty
            # latch/merge can become fallthrough after target peepholes, so a
            # call record in its predecessor would otherwise alias this loop
            # label even though the two records have distinct stable IDs and
            # kinds.  The one-byte/x86 or one-instruction/AArch64 separator is
            # executable on the backedge and keeps the final map strictly
            # ordered without dropping either logical safepoint.
            needs_separator = True
            terminator_labels.append(
                (block.name, loop.label, needs_separator)
            )

    end_prefix = "L_pcc_smap_end" if target == "aarch64-darwin" else ".Lpcc_smap_end"
    return FunctionStackMapPlan(
        function_name=func.name,
        function_id=fid,
        frame_size=func.frame_size,
        end_label=f"{end_prefix}_{fid:016x}",
        records=tuple(records),
        block_entry_labels=tuple(block_labels),
        instruction_suffix_labels=tuple(instruction_labels),
        terminator_prefix_labels=tuple(terminator_labels),
        target=target,
    )


def build_stack_map_plans(
    functions: list[ParsedFunction],
    globals_: list[GlobalDef],
    *,
    target: str,
    function_symbol=None,
) -> tuple[FunctionStackMapPlan, ...]:
    return tuple(
        build_function_stack_map_plan(
            func,
            globals_,
            target=target,
            identity_name=(
                function_symbol(func.name)
                if function_symbol is not None
                else func.name
            ),
        )
        for func in functions
    )


def _aarch64_text_label_offsets(lines: list[str]) -> dict[str, int]:
    offsets: dict[str, int] = {}
    offset = 0
    in_text = False
    for raw in lines:
        line = raw.strip()
        if line.startswith(".section "):
            in_text = line[len(".section ") :].startswith("__TEXT,__text,")
            continue
        if not in_text or not line:
            continue
        if line.endswith(":"):
            label = line[:-1]
            if label in offsets:
                raise BackendUnavailable(
                    f"duplicate target-final stack-map label {label!r}"
                )
            offsets[label] = offset
            continue
        if line.startswith(".p2align "):
            power = int(line.split()[1], 0)
            alignment = 1 << power
            offset = _align_to(offset, alignment)
            continue
        if line.startswith((".data_region", ".end_data_region")):
            continue
        widths = ((".byte ", 1), (".short ", 2), (".long ", 4), (".quad ", 8))
        matched = False
        for prefix, width in widths:
            if line.startswith(prefix):
                offset += width * len([item for item in line[len(prefix) :].split(",") if item.strip()])
                matched = True
                break
        if matched:
            continue
        if line.startswith(".space "):
            offset += int(line.split()[1], 0)
            continue
        if line.startswith("."):
            continue
        offset += 4
    return offsets


def _stack_locations(
    locations: tuple[PlannedRootLocation, ...], *, arch: int
) -> tuple[StackMapLocation, ...]:
    register = 29 if arch == ARCH_AARCH64 else 6
    materialized: list[StackMapLocation] = []
    for location in locations:
        materialized.append(StackMapLocation(
            LOCATION_STACK_INDIRECT,
            LOCATION_MANAGED | (LOCATION_OWNED if location.owned else 0),
            POINTER_SIZE,
            register,
            NO_BASE,
            location.offset,
            POINTER_SIZE,
        ))
    return tuple(materialized)


def _validate_planned_location(
    location: PlannedRootLocation, *, frame_size: int
) -> None:
    if location.offset >= 0 or -location.offset > frame_size:
        raise BackendUnavailable(
            f"planned managed stack location {location.offset} exceeds "
            f"frame size {frame_size}"
        )
    if (-location.offset) % POINTER_SIZE:
        raise BackendUnavailable(
            f"planned managed stack location {location.offset} is not aligned"
        )


def _packed_location_words_from_fields(
    kind: int,
    flags: int,
    size: int,
    register: int,
    base_index: int,
    offset: int,
    extent: int,
) -> tuple[int, int, int, int]:
    """Pack one 16-byte stack-map location into four little-endian i32 words.

    Layout is [kind:u8][flags:u8][size:u16][reg:u16][base:u16][offset:i32]
    [extent:i32].  Writing each field on its own line made the stack-map
    section dominate self-backend output (measured: one module's .s was
    98.7% data directives, ~7 asm lines per 16-byte location record).  The
    assembler writes each .long value little-endian, so packing the record
    into four words on one directive line produces byte-identical data with
    a fraction of the lines.
    """
    word0 = kind | (flags << 8) | (size << 16)
    word1 = (register & 0xFFFF) | ((base_index & 0xFFFF) << 16)
    return word0, word1, offset, extent


_LOCATIONS_PER_LINE = 8


def _append_packed_location_lines(
    lines: list[str], locations, *, arch: int
) -> None:
    """Append packed .long lines for many locations (8 records per line).

    Accepts both PlannedRootLocation (planned path: only offset/owned, the
    remaining fields are derived per target) and the materialized
    StackMapLocation (x86_64 path: all seven fields present).
    """
    words: list[int] = []
    for location in locations:
        if hasattr(location, "owned"):
            register = 29 if arch == ARCH_AARCH64 else 6
            flags = LOCATION_MANAGED | (
                LOCATION_OWNED if location.owned else 0
            )
            words.extend(_packed_location_words_from_fields(
                LOCATION_STACK_INDIRECT,
                flags,
                POINTER_SIZE,
                register,
                NO_BASE,
                location.offset,
                POINTER_SIZE,
            ))
        else:
            words.extend(_packed_location_words_from_fields(
                location.kind,
                location.flags,
                location.size,
                location.register,
                location.base_index,
                location.offset,
                location.extent,
            ))
    per_line = _LOCATIONS_PER_LINE * 4
    for start in range(0, len(words), per_line):
        chunk = words[start:start + per_line]
        lines.append("  .long " + ", ".join(str(word) for word in chunk))


def _append_planned_location(
    lines: list[str], location: PlannedRootLocation, *, arch: int
) -> None:
    """Emit one planned stack-map location as a packed single .long line."""
    register = 29 if arch == ARCH_AARCH64 else 6
    flags = LOCATION_MANAGED | (LOCATION_OWNED if location.owned else 0)
    lines.append(
        "  .long " + ", ".join(
            str(word) for word in _packed_location_words_from_fields(
                LOCATION_STACK_INDIRECT,
                flags,
                POINTER_SIZE,
                register,
                NO_BASE,
                location.offset,
                POINTER_SIZE,
            )
        )
    )


def render_aarch64_stack_map_section(
    lines: list[str],
    plans: tuple[FunctionStackMapPlan, ...],
    *,
    function_symbol,
    block_label,
) -> list[str]:
    offsets = _aarch64_text_label_offsets(lines)
    ordered_plans = sorted(plans, key=lambda item: item.function_id)
    section = [".section __DATA,__pcc_stackmaps,regular", ".p2align 3"]
    # v2 interns the location lists into one table emitted after every
    # function, so the header carries its length and records name an index.
    # Repeating locations inline made this section 89.7% of a linked pcc1.
    location_table: list = []
    location_table_index: dict[str, int] = {}
    previous_locations = None
    previous_location_index: int = 0
    _append_bytes(section, MAGIC)
    section.extend((
        f"  .short {VERSION}",
        f"  .byte {ARCH_AARCH64}",
        f"  .byte {POINTER_SIZE}",
        f"  .long {len(ordered_plans)}",
        "  .long 0",  # patched below with the interned location count
        "  .long 0",
    ))
    location_count_line = len(section) - 2
    previous_function_id = -1
    seen_safepoints: set[int] = set()
    for plan in ordered_plans:
        if plan.function_id <= previous_function_id:
            raise BackendUnavailable(
                "stack-map functions need unique ordered stable ids"
            )
        previous_function_id = plan.function_id
        symbol = function_symbol(plan.function_name)
        if not symbol or "\0" in symbol:
            raise BackendUnavailable("stack-map function symbol is invalid")
        start = offsets.get(symbol)
        end = offsets.get(plan.end_label)
        if start is None or end is None or end <= start:
            raise BackendUnavailable(
                f"target-final stack-map range missing for {plan.function_name!r}"
            )
        code_size = end - start
        if plan.frame_size < 0 or plan.frame_size % 16:
            raise BackendUnavailable(
                f"stack-map frame size is invalid for {plan.function_name!r}"
            )
        records: list[tuple[int, int, PlannedSafepoint, int]] = []
        for record in plan.records:
            pc = offsets.get(record.label)
            if pc is None:
                raise BackendUnavailable(
                    f"target-final safepoint label missing: {record.label!r}"
                )
            exceptional_offset = NO_OFFSET
            if record.exceptional_block:
                exceptional_pc = offsets.get(
                    block_label(plan.function_name, record.exceptional_block)
                )
                if exceptional_pc is None:
                    raise BackendUnavailable(
                        "target-final exception successor label missing for "
                        f"{plan.function_name!r}/{record.exceptional_block!r}"
                    )
                exceptional_offset = exceptional_pc - start
            records.append((
                pc - start,
                record.safepoint_id,
                record,
                exceptional_offset,
            ))
        records.sort(key=lambda item: (item[0], item[1]))
        section.extend((
            f"  .quad {plan.function_id}",
            f"  .quad {symbol}",
            f"  .long {code_size}",
            f"  .long {plan.frame_size}",
            f"  .long {len(records)}",
            "  .long 0",
        ))
        previous_pc = -1
        for instruction_offset, record_id, record, exceptional_offset in records:
            if record_id <= 0 or record_id in seen_safepoints:
                raise BackendUnavailable("stack-map safepoint id is invalid or duplicate")
            seen_safepoints.add(record_id)
            if not previous_pc < instruction_offset < code_size:
                raise BackendUnavailable(
                    "stack-map safepoints are not ordered inside the function"
                )
            previous_pc = instruction_offset
            has_exception = exceptional_offset != NO_OFFSET
            if has_exception != bool(record.flags & RECORD_HAS_EXCEPTION_EDGE):
                raise BackendUnavailable(
                    "stack-map exception-edge flag and offset disagree"
                )
            is_continuation = record.kind == SAFEPOINT_CONTINUATION
            if is_continuation != bool(record.continuation_id):
                raise BackendUnavailable(
                    "stack-map continuation needs one non-zero continuation id"
                )
            if bool(record.flags & RECORD_SUSPENDED) != is_continuation:
                raise BackendUnavailable(
                    "stack-map suspended flag is reserved for continuations"
                )
            for location in record.locations:
                _validate_planned_location(location, frame_size=plan.frame_size)
            # Consecutive safepoints almost always describe the same live
            # roots, so compare against the previous record before building a
            # key at all.  Tuple equality compares the dataclass fields
            # without allocating; building the string key costs one str() per
            # location, and a self-host link carries tens of millions of them.
            # Identity FIRST: `==` on a tuple of frozen dataclasses answers
            # False under pcc1 even for equal contents (probed directly: host
            # True, pcc1 False), so on the self-compiled path this fast path
            # never fired and every record rebuilt a ~2 kB key string and
            # hashed it -- and pcc never caches a str hash.  Measured on one
            # 18 MB module: 32057 of 38540 records take this path on the host.
            # `_locations()` interns its answer per active-root fingerprint, so
            # records describing the same live set share one tuple object and
            # `is` catches exactly what `==` was meant to catch.  `==` stays as
            # the fallback for distinct-but-equal tuples.
            if previous_locations is not None and (
                record.locations is previous_locations
                or record.locations == previous_locations
            ):
                location_index = previous_location_index
            else:
                # Key the intern table on a plain string, not on a tuple of
                # PlannedRootLocation objects: this emitter is compiled into
                # the self-host closure, where hashing user objects is not
                # proven.  `key in d` + subscript, never dict.get, for the
                # same reason (`.get` mis-lowers under pcc1 into a raising
                # getitem).
                key_parts: list[str] = []
                for item in record.locations:
                    key_parts.append(str(item.offset))
                    key_parts.append("1" if item.owned else "0")
                key = ",".join(key_parts)
                if key in location_table_index:
                    location_index = location_table_index[key]
                else:
                    location_index = len(location_table)
                    location_table_index[key] = location_index
                    for item in record.locations:
                        location_table.append(item)
                previous_locations = record.locations
                previous_location_index = location_index
            section.extend((
                f"  .quad {record_id}",
                f"  .long {instruction_offset}",
                f"  .long {exceptional_offset}",
                f"  .long {record.continuation_id}",
                f"  .short {len(record.locations)}",
                "  .short 0",
                f"  .byte {record.kind}",
                f"  .byte {record.flags}",
                "  .short 0",
                f"  .long {location_index}",
            ))
    section[location_count_line] = f"  .long {len(location_table)}"
    if location_table:
        _append_packed_location_lines(
            section, tuple(location_table), arch=ARCH_AARCH64
        )
    return section


def _append_bytes(lines: list[str], values: bytes) -> None:
    for start in range(0, len(values), 16):
        lines.append("  .byte " + ", ".join(
            str(value) for value in values[start : start + 16]
        ))


def _validate_symbolic_plans(
    plans: tuple[FunctionStackMapPlan, ...], *, arch: int
) -> None:
    functions: list[FunctionStackMap] = []
    for plan in sorted(plans, key=lambda item: item.function_id):
        records: list[SafepointRecord] = []
        for index, record in enumerate(plan.records):
            records.append(SafepointRecord(
                safepoint_id=record.safepoint_id,
                instruction_offset=index,
                kind=record.kind,
                locations=_stack_locations(record.locations, arch=arch),
                flags=record.flags,
                exceptional_offset=0 if record.exceptional_block else NO_OFFSET,
                continuation_id=record.continuation_id,
            ))
        functions.append(FunctionStackMap(
            function_id=plan.function_id,
            function_address=0,
            code_size=max(1, len(records) + 1),
            frame_size=plan.frame_size,
            records=tuple(records),
        ))
    validate_stack_map(PreciseStackMap(arch=arch, functions=tuple(functions)))


def render_x86_64_stack_map_section(
    emitted_lines: list[str],
    plans: tuple[FunctionStackMapPlan, ...],
    *,
    function_symbol,
    block_label,
) -> list[str]:
    _validate_symbolic_plans(plans, arch=ARCH_X86_64)
    label_order: dict[str, int] = {}
    for index, raw in enumerate(emitted_lines):
        line = raw.strip()
        if line.endswith(":"):
            label_order[line[:-1]] = index
    ordered_plans = sorted(plans, key=lambda item: item.function_id)
    lines = ['.section .pcc_stackmaps,"a",@progbits', ".p2align 3"]
    _append_bytes(lines, MAGIC)
    lines.extend((
        f"  .short {VERSION}",
        f"  .byte {ARCH_X86_64}",
        f"  .byte {POINTER_SIZE}",
        f"  .long {len(ordered_plans)}",
    ))
    for plan in ordered_plans:
        symbol = function_symbol(plan.function_name)
        records = sorted(
            plan.records,
            key=lambda record: (
                label_order.get(record.label, 1 << 60),
                record.safepoint_id,
            ),
        )
        for required in (symbol, plan.end_label, *(record.label for record in records)):
            if required not in label_order:
                raise BackendUnavailable(
                    f"target-final x86 stack-map label missing: {required!r}"
                )
        lines.extend((
            f"  .quad {plan.function_id}",
            f"  .quad {symbol}",
            f"  .long {plan.end_label} - {symbol}",
            f"  .long {plan.frame_size}",
            f"  .long {len(records)}",
            "  .long 0",
        ))
        for record in records:
            exceptional = str(NO_OFFSET)
            if record.exceptional_block:
                target = block_label(plan.function_name, record.exceptional_block)
                if target not in label_order:
                    raise BackendUnavailable(
                        f"target-final x86 exception label missing: {target!r}"
                    )
                exceptional = f"{target} - {symbol}"
            lines.extend((
                f"  .quad {record.safepoint_id}",
                f"  .long {record.label} - {symbol}",
                f"  .long {exceptional}",
                f"  .long {record.continuation_id}",
                f"  .short {len(record.locations)}",
                "  .short 0",
                f"  .byte {record.kind}",
                f"  .byte {record.flags}",
                "  .short 0",
                "  .long 0",
            ))
            packed_locations = _stack_locations(
                record.locations, arch=ARCH_X86_64
            )
            if packed_locations:
                _append_packed_location_lines(
                    lines, packed_locations, arch=ARCH_X86_64
                )
    return lines


__all__ = [
    "FunctionStackMapPlan",
    "PlannedManagedReload",
    "PlannedRootLocation",
    "PlannedSafepoint",
    "build_function_stack_map_plan",
    "build_stack_map_plans",
    "render_aarch64_stack_map_section",
    "render_x86_64_stack_map_section",
]
