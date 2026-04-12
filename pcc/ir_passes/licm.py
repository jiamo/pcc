"""Loop Invariant Code Motion (LICM) — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LICM.cpp``
  implements :cpp:class:`llvm::LICMPass`. It hoists loop-invariant
  instructions out of a loop to the preheader, or sinks
  loop-dependent-but-constant instructions to the exit. It uses
  LoopInfo + MemorySSA to reason about memory safety and relies on
  :cpp:class:`llvm::AAResults` for disambiguation.

Subset implemented here (labelled ``subset``):

- Hoist side-effect-free, non-memory instructions (``add``, ``sub``,
  ``mul``, ``sdiv``/``udiv``, ``and``, ``or``, ``xor``, ``icmp``,
  ``shl``, ``lshr``, ``ashr``) from a loop to its preheader when
  every operand is loop-invariant (defined outside the loop, a
  function argument, or a constant).
- Require exactly one preheader.
- Iterate to a fixed point: hoisting an instruction may make
  dependent instructions newly invariant.
- When a hoistable invariant chain has no remaining non-hoisted uses,
  drop the dead chain instead of re-emitting it in the preheader.
- Sink exit-only scalar computations from a single exiting block into
  a single exit block. For the narrow shape where the exit has only
  that one loop predecessor, materialize ``*.lcssa`` phi nodes for
  loop-defined operands and re-emit the sunk instructions in the exit
  block using LLVM-style ``*.le`` names.
- When a loop has a single exiting block and single exit block, defer
  hoisting from that exiting block so exit-only scalar chains can sink
  together instead of being split between preheader and exit.

Memory-side LICM is still mostly deferred to the MemorySSA walker
track, but a narrow AA-backed subset is implemented:

- hoist a direct non-volatile/non-atomic ``load`` from a loop-invariant
  ``alloca`` when the loop has no may-aliasing store and no call that
  receives a maybe-aliasing pointer argument.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .alias_analysis import AliasAnalysis, AliasResult
from .dominator_tree import CFG
from .ir_mutator import Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .ssa_utils import build_def_use_index_per_function


_HOISTABLE_OPCODES = {
    "add", "sub", "mul", "udiv", "sdiv", "urem", "srem",
    "and", "or", "xor", "shl", "lshr", "ashr",
    "icmp", "select", "trunc", "zext", "sext", "bitcast", "freeze",
    "getelementptr",
}
_SINKABLE_OPCODES = set(_HOISTABLE_OPCODES) | {"load"}


class LICMPass(ModulePass):
    name = "pcc-licm"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = licm_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def licm_module(ir_text: str) -> tuple[str, bool]:
    current = ir_text
    any_changed = False
    for _ in range(8):
        new_text, changed = _one_round(current)
        if not changed:
            break
        any_changed = True
        current = new_text
    return current, any_changed


_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<name>[\w\.]+)\s*=\s*"
    r"(?P<opcode>[a-zA-Z_][\w.]*)\b(?P<rest>.*)$"
)


def _one_round(ir_text: str) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    aa = AliasAnalysis(module)

    any_change = False

    for fn in module.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        if not info.loops():
            continue
        cfg = CFG.of_function(fn)

        for loop in info.loops():
            preds = cfg.predecessors.get(loop.header, ())
            ext = [p for p in preds if p not in loop.blocks]
            if len(ext) != 1:
                continue
            preheader = ext[0]
            new_text, changed = _hoist_one_loop(
                ir_text, fn.name, loop, preheader, cfg, aa
            )
            if changed:
                try:
                    llvm.parse_assembly(new_text).verify()
                    ir_text = new_text
                    any_change = True
                except RuntimeError:
                    # If verification fails, leave this loop alone.
                    continue
            new_text, changed = _sink_one_loop(ir_text, fn.name, loop, cfg)
            if changed:
                try:
                    llvm.parse_assembly(new_text).verify()
                    ir_text = new_text
                    any_change = True
                except RuntimeError:
                    continue
            cleanup_scope = set(loop.blocks) | {preheader} | set(loop.exit_blocks(cfg))
            new_text, changed = _cleanup_dead_pure_in_scope(
                ir_text, fn.name, cleanup_scope
            )
            if changed:
                try:
                    llvm.parse_assembly(new_text).verify()
                    ir_text = new_text
                    any_change = True
                except RuntimeError:
                    continue

    return ir_text, any_change


def _hoist_one_loop(
    ir_text: str,
    fn_name: str,
    loop,
    preheader: str,
    cfg: CFG,
    aa: AliasAnalysis,
) -> tuple[str, bool]:
    """Hoist loop-invariant instructions from loop blocks to preheader."""
    exit_blocks = set(loop.exit_blocks(cfg))
    exiting_blocks = {
        block for block in loop.blocks
        if any(succ in exit_blocks for succ in cfg.successors.get(block, ()))
    }
    # Find SSA names defined inside the loop (variants) vs outside.
    lines = ir_text.splitlines(keepends=True)
    # Locate function body.
    fn_start = fn_end = -1
    for i, line in enumerate(lines):
        if re.match(rf"^\s*define\s+[^@]*@{re.escape(fn_name)}\b", line):
            fn_start = i
        elif fn_start >= 0 and line.strip() == "}":
            fn_end = i
            break
    if fn_start < 0 or fn_end < 0:
        return ir_text, False

    # Walk fn body; track current block.
    block_of_line: dict[int, str] = {}
    line_defines: dict[int, str] = {}
    current_block = "entry"
    label_re = re.compile(r"^\s*([\w\.]+):\s*(?:;.*)?$")
    for i in range(fn_start, fn_end + 1):
        line = lines[i]
        lm = label_re.match(line.rstrip("\n"))
        if lm:
            current_block = lm.group(1)
            continue
        block_of_line[i] = current_block
        dm = _ASSIGN_RE.match(line.rstrip("\n"))
        if dm:
            line_defines[i] = dm.group("name")

    # Names defined inside the loop (variants).
    variants: set[str] = set()
    for i, b in block_of_line.items():
        if b in loop.blocks and i in line_defines:
            variants.add(line_defines[i])

    # Candidate hoistable lines: lines in loop body, with a hoistable
    # opcode, whose operands are all outside-variants.
    candidates: list[tuple[int, str, str]] = []
    for i in sorted(block_of_line):
        if block_of_line[i] not in loop.blocks:
            continue
        line = lines[i]
        m = _ASSIGN_RE.match(line.rstrip("\n"))
        if not m:
            continue
        opcode = m.group("opcode")
        name = m.group("name")
        if (
            block_of_line[i] == loop.header
            and block_of_line[i] in exiting_blocks
            and not _header_exiting_candidate_has_non_header_loop_use(
                name, fn_start, fn_end, block_of_line, loop.blocks, loop.header, lines
            )
        ):
            continue
        # Skip phi — phi is inherently loop-dependent by construction.
        if opcode == "phi":
            continue
        # All %foo operands must NOT be loop-variants.
        ops = [g.group(1) for g in re.finditer(r"%([\w\.]+)", m.group("rest"))]
        if any(o in variants for o in ops):
            continue
        if opcode in _HOISTABLE_OPCODES:
            candidates.append((i, line, name))
            continue
        if opcode == "load" and _can_hoist_invariant_load(
            line.rstrip("\n"), fn_name, loop.blocks, block_of_line, lines, aa
        ):
            candidates.append((i, line, name))
            continue

    if not candidates:
        return ir_text, False

    candidate_lines = {i for i, _, _ in candidates}
    candidate_names = {name for _, _, name in candidates}
    candidate_deps: dict[str, set[str]] = {name: set() for name in candidate_names}
    externally_used: set[str] = set()
    for i in range(fn_start, fn_end + 1):
        line = lines[i]
        m = _ASSIGN_RE.match(line.rstrip("\n"))
        if m:
            user_name = m.group("name")
            tokens = [g.group(1) for g in re.finditer(r"%([\w\.]+)", m.group("rest"))]
        else:
            user_name = None
            tokens = [g.group(1) for g in re.finditer(r"%([\w\.]+)", line)]
        for token in tokens:
            if token not in candidate_names:
                continue
            if i in candidate_lines and user_name in candidate_names:
                candidate_deps[user_name].add(token)
            else:
                externally_used.add(token)

    keep: set[str] = set(externally_used)
    changed_keep = True
    while changed_keep:
        changed_keep = False
        for name, deps in candidate_deps.items():
            if name not in keep:
                continue
            for dep in deps:
                if dep not in keep:
                    keep.add(dep)
                    changed_keep = True

    kept_candidates = [(i, line) for i, line, name in candidates if name in keep]
    removed_candidate_indices = {i for i, _, name in candidates if name not in keep}

    # Move candidate lines to just before the preheader's terminator.
    # 1. Find preheader's terminator line index.
    preheader_term: int | None = None
    current_block = "entry"
    term_re = re.compile(
        r"^\s*(ret|br|switch|indirectbr|invoke|unreachable|resume)\b"
    )
    for i in range(fn_start, fn_end + 1):
        line = lines[i]
        lm = label_re.match(line.rstrip("\n"))
        if lm:
            current_block = lm.group(1)
            continue
        if current_block == preheader and term_re.match(line):
            preheader_term = i
            break
    if preheader_term is None:
        # Entry block's terminator — preheader might be the function
        # entry without a label. For safety, bail out.
        return ir_text, False

    # 2. Emit new lines: prefix = lines up to preheader_term, insert
    #    hoisted, append rest minus hoisted line indices.
    hoisted_indices = {i for i, _ in kept_candidates}
    hoisted_text = "".join(lines[i] for i, _ in kept_candidates)

    new_lines: list[str] = []
    for i, line in enumerate(lines):
        if i == preheader_term:
            new_lines.append(hoisted_text)
            new_lines.append(line)
            continue
        if i in hoisted_indices or i in removed_candidate_indices:
            continue
        new_lines.append(line)
    return "".join(new_lines), True


def _header_exiting_candidate_has_non_header_loop_use(
    name: str,
    fn_start: int,
    fn_end: int,
    block_of_line: dict[int, str],
    loop_blocks: set[str],
    header: str,
    lines: list[str],
) -> bool:
    token_re = re.compile(r"%" + re.escape(name) + r"(?![\w\.])")
    for i in range(fn_start, fn_end + 1):
        block = block_of_line.get(i)
        if block not in loop_blocks or block == header:
            continue
        line = lines[i].rstrip("\n")
        if token_re.search(line):
            return True
    return False


_DIRECT_LOAD_PTR_RE = re.compile(
    r"^\s*%(?P<name>[\w\.]+)\s*=\s*load\b"
    r"(?P<rest>.*?\bptr\s+[@%](?P<ptr>[\w\.]+).*)$"
)
_DIRECT_STORE_PTR_RE = re.compile(
    r"^\s*store(?:\s+volatile)?\s+.+?,\s*ptr\s+[@%](?P<ptr>[\w\.]+)"
)


def _can_hoist_invariant_load(
    line: str,
    fn_name: str,
    loop_blocks: set[str],
    block_of_line: dict[int, str],
    lines: list[str],
    aa: AliasAnalysis,
) -> bool:
    if " volatile " in line or " atomic " in line:
        return False
    m = _DIRECT_LOAD_PTR_RE.match(line)
    if not m:
        return False
    ptr_name = m.group("ptr")
    if aa.classify(ptr_name).kind != "alloca":
        return False

    for i, text in enumerate(lines):
        block = block_of_line.get(i)
        if block not in loop_blocks:
            continue
        stripped = text.rstrip("\n")
        store = _DIRECT_STORE_PTR_RE.match(stripped)
        if store and aa.alias_names(ptr_name, store.group("ptr")) != AliasResult.NoAlias:
            return False
        if re.search(r"^\s*(?:%\w[\w\.]*\s*=\s*)?(call|invoke)\b", stripped):
            for arg_ptr in re.findall(r"\bptr\s+[@%]([\w\.]+)", stripped):
                if aa.alias_names(ptr_name, arg_ptr) != AliasResult.NoAlias:
                    return False
        if re.search(r"^\s*(?:%\w[\w\.]*\s*=\s*)?(atomicrmw|cmpxchg|fence)\b", stripped):
            return False
    return True


def _sink_one_loop(
    ir_text: str,
    fn_name: str,
    loop,
    cfg: CFG,
) -> tuple[str, bool]:
    exit_blocks = loop.exit_blocks(cfg)
    if len(exit_blocks) != 1:
        return ir_text, False
    exit_block = exit_blocks[0]
    exiting_blocks = [
        block for block in loop.blocks
        if exit_block in cfg.successors.get(block, ())
    ]
    if len(exiting_blocks) != 1:
        return ir_text, False
    exiting_block = exiting_blocks[0]
    if tuple(cfg.predecessors.get(exit_block, ())) != (exiting_block,):
        return ir_text, False

    module = llvm.parse_assembly(ir_text)
    module.verify()
    fn = next((f for f in module.functions if f.name == fn_name), None)
    if fn is None or fn.is_declaration:
        return ir_text, False

    index_by_fn = build_def_use_index_per_function(module)
    index = index_by_fn.get(fn_name)
    if index is None:
        return ir_text, False

    value_types = _value_types_for_function(fn)
    exiting_insts = index.instructions_in(fn_name, exiting_block)
    selected: set[str] = set()

    for rec in reversed(exiting_insts):
        if (
            rec.name is None
            or rec.is_terminator
            or not _is_sinkable_record(rec)
        ):
            continue
        users = index.users_of(rec.name)
        if not users:
            continue
        ok = True
        has_sink_consumer = False
        for user in users:
            if user.block in loop.blocks:
                if user.block == exiting_block and user.name in selected:
                    has_sink_consumer = True
                    continue
                ok = False
                break
            has_sink_consumer = True
        if ok and has_sink_consumer:
            selected.add(rec.name)

    ordered_selected = [
        rec for rec in exiting_insts if rec.name in selected
    ]

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn_name)
    if fn_mut is None:
        return ir_text, False
    exit_mut = fn_mut.block(exit_block)
    exiting_mut = fn_mut.block(exiting_block)
    if exit_mut is None or exiting_mut is None:
        return ir_text, False

    taken_names = fn_mut.defined_names()
    selected_rename = {
        rec.name: _fresh_name(f"{rec.name}.le", taken_names)
        for rec in ordered_selected
        if rec.name is not None
    }
    lcssa_rename: dict[str, str] = {}
    lcssa_order: list[str] = []
    for rec in ordered_selected:
        for operand in rec.operands:
            if operand in selected_rename or operand in lcssa_rename:
                continue
            def_rec = index.def_of(operand)
            if def_rec is None or def_rec.block not in loop.blocks:
                continue
            if operand not in value_types:
                return ir_text, False
            lcssa_rename[operand] = _fresh_name(f"{operand}.lcssa", taken_names)
            lcssa_order.append(operand)

    existing_exit_texts = [inst.text for inst in exit_mut.instructions]
    for text in existing_exit_texts:
        if " = phi " in text:
            continue
        for operand in re.findall(r"%([\w\.]+)", text):
            if operand in selected_rename or operand in lcssa_rename:
                continue
            def_rec = index.def_of(operand)
            if def_rec is None or def_rec.block not in loop.blocks:
                continue
            if operand not in value_types:
                return ir_text, False
            lcssa_rename[operand] = _fresh_name(f"{operand}.lcssa", taken_names)
            lcssa_order.append(operand)

    if not selected and not lcssa_rename:
        return ir_text, False

    original_lines = {
        inst.result_name: inst.text
        for inst in exiting_mut.instructions
        if inst.result_name is not None
    }
    exiting_mut.instructions = [
        inst for inst in exiting_mut.instructions
        if inst.result_name not in selected_rename
    ]

    for old, new in selected_rename.items():
        mut.rename_value_in_function(fn_mut, old, new)

    insert_at = 0
    while (
        insert_at < len(exit_mut.instructions)
        and " = phi " in exit_mut.instructions[insert_at].text
    ):
        insert_at += 1

    for operand in lcssa_order:
        phi_name = lcssa_rename[operand]
        ty = value_types[operand]
        phi_text = (
            f"  %{phi_name} = phi {ty} [ %{operand}, %{exiting_block} ]\n"
        )
        exit_mut.instructions.insert(insert_at, Instruction.from_text(phi_text))
        insert_at += 1

    for idx in range(insert_at, len(exit_mut.instructions)):
        exit_mut.instructions[idx].text = _rename_values_in_line(
            exit_mut.instructions[idx].text,
            lcssa_rename,
        )

    operand_rename = dict(selected_rename)
    operand_rename.update(lcssa_rename)
    for rec in ordered_selected:
        original = original_lines.get(rec.name)
        if original is None:
            return ir_text, False
        sunk_text = _rename_values_in_line(original, operand_rename)
        exit_mut.instructions.insert(insert_at, Instruction.from_text(sunk_text))
        insert_at += 1

    new_ir = mut.serialize()
    llvm.parse_assembly(new_ir).verify()
    return new_ir, True


def _is_sinkable_record(rec) -> bool:
    if rec.opcode not in _SINKABLE_OPCODES:
        return False
    if rec.opcode == "load":
        return " volatile " not in rec.text and " atomic " not in rec.text
    return True


def _value_types_for_function(fn: llvm.ValueRef) -> dict[str, str]:
    out: dict[str, str] = {}
    for arg in fn.arguments:
        if arg.name:
            out[arg.name] = str(arg.type)
    for block in fn.blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            m = _ASSIGN_RE.match(text)
            if m:
                out[m.group("name")] = str(inst.type)
    return out


def _fresh_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    i = 1
    while True:
        candidate = f"{base}.{i}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        i += 1


def _rename_values_in_line(line: str, mapping: dict[str, str]) -> str:
    out = line
    for old, new in mapping.items():
        out = re.sub(
            r"%" + re.escape(old) + r"(?![\w\.])",
            f"%{new}",
            out,
        )
    return out


def _cleanup_dead_pure_in_scope(
    ir_text: str,
    fn_name: str,
    blocks: set[str],
) -> tuple[str, bool]:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    index_by_fn = build_def_use_index_per_function(module)
    index = index_by_fn.get(fn_name)
    if index is None:
        return ir_text, False

    removable: set[str] = set()
    for rec in index.records_by_name.values():
        if rec.name is None or rec.block not in blocks:
            continue
        if rec.is_terminator or rec.opcode == "phi":
            continue
        if rec.opcode == "load":
            if " volatile " in rec.text or " atomic " in rec.text:
                continue
            removable.add(rec.name)
            continue
        if rec.opcode in _HOISTABLE_OPCODES:
            removable.add(rec.name)

    if not removable:
        return ir_text, False

    live: set[str] = set()
    worklist: list[str] = []
    for name in removable:
        users = index.users_of(name)
        if any(user.block not in blocks or user.name not in removable for user in users):
            live.add(name)
            worklist.append(name)

    while worklist:
        name = worklist.pop()
        rec = index.def_of(name)
        if rec is None:
            continue
        for operand in rec.operands:
            if operand in removable and operand not in live:
                live.add(operand)
                worklist.append(operand)

    to_remove = removable - live
    if not to_remove:
        return ir_text, False

    mut = MutableModule.parse(ir_text)
    fn_mut = mut.function(fn_name)
    if fn_mut is None:
        return ir_text, False
    for block_name in blocks:
        block = fn_mut.block(block_name)
        if block is None:
            continue
        block.instructions = [
            inst for inst in block.instructions
            if inst.result_name not in to_remove
        ]
    return mut.serialize(), True
