"""Loop Sink — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopSink.cpp``
  sinks loop-local instructions from a predecessor block into a more
  specific successor when all uses are on that successor path.

Subset implemented here (labelled ``subset``):

- Only sink side-effect-free scalar instructions.
- The defining block must end in a conditional branch.
- The sink target must be one direct successor with a single CFG
  predecessor.
- All uses of the instruction must stay within that successor block,
  and none may be phi uses.
- The sunk instruction may only depend on values defined outside the
  source block so it remains available after the move.

This is enough to match a focused loop-local shape where a guarded
arithmetic computation feeds only the ``then`` arm of a loop branch.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import Function, Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+[^,]+,\s*label\s+%(?P<t>[\w\.\$]+)\s*,\s*label\s+%(?P<f>[\w\.\$]+)\s*$"
)

_PURE_OPCODES = {
    "add",
    "sub",
    "mul",
    "and",
    "or",
    "xor",
    "icmp",
    "select",
    "trunc",
    "zext",
    "sext",
}


class LoopSinkPass(ModulePass):
    name = "pcc-loop-sink"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = loop_sink_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_sink_text(ir_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()
    mut = MutableModule.parse(ir_text)
    any_changed = False

    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        cfg = CFG.of_function(fn)
        info = compute_loop_info(fn)
        if not info.loops():
            continue
        mut_fn = mut.function(fn.name)
        if mut_fn is None:
            continue
        if _sink_in_function(mut_fn, cfg, info.loops()):
            any_changed = True

    if not any_changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _sink_in_function(fn: Function, cfg: CFG, loops) -> bool:
    changed = False
    progress = True
    while progress:
        progress = False
        use_map = _build_use_map(fn)
        local_defs = {
            block.name: {
                inst.result_name
                for inst in block.instructions
                if inst.result_name is not None
            }
            for block in fn.blocks
        }
        for loop in sorted(loops, key=lambda lp: -lp.depth()):
            if _sink_one_in_loop(fn, cfg, loop.blocks, use_map, local_defs):
                progress = True
                changed = True
                break
    return changed


def _sink_one_in_loop(
    fn: Function,
    cfg: CFG,
    loop_blocks: set[str],
    use_map: dict[str, list[tuple[str, int, Instruction]]],
    local_defs: dict[str, set[str]],
) -> bool:
    for block in fn.blocks:
        if block.name not in loop_blocks:
            continue
        term = block.terminator
        if term is None:
            continue
        cond = _COND_BR_RE.match(term.text.strip())
        if cond is None:
            continue
        for idx, inst in enumerate(block.instructions[:-1]):
            if not _movable(inst):
                continue
            if any(op in local_defs[block.name] for op in inst.operand_names()):
                continue
            uses = use_map.get(inst.result_name or "", [])
            if not uses:
                continue
            target = _single_sink_target(uses, {cond.group("t"), cond.group("f")}, cfg)
            if target is None:
                continue
            target_block = fn.block(target)
            if target_block is None:
                continue
            if any(use_block != target for use_block, _, _ in uses):
                continue
            if any(" = phi " in use_inst.text for _, _, use_inst in uses):
                continue
            moved = block.instructions.pop(idx)
            insert_at = _first_non_phi_index(target_block)
            target_block.instructions.insert(insert_at, moved)
            return True
    return False


def _build_use_map(fn: Function) -> dict[str, list[tuple[str, int, Instruction]]]:
    uses: dict[str, list[tuple[str, int, Instruction]]] = {}
    for block in fn.blocks:
        for idx, inst in enumerate(block.instructions):
            for operand in inst.operand_names():
                uses.setdefault(operand, []).append((block.name, idx, inst))
    return uses


def _movable(inst: Instruction) -> bool:
    if inst.result_name is None:
        return False
    if inst.opcode not in _PURE_OPCODES:
        return False
    if " = phi " in inst.text:
        return False
    return True


def _single_sink_target(
    uses: list[tuple[str, int, Instruction]],
    successors: set[str],
    cfg: CFG,
) -> str | None:
    blocks = {block for block, _, _ in uses}
    if len(blocks) != 1:
        return None
    target = next(iter(blocks))
    if target not in successors:
        return None
    if len(cfg.predecessors.get(target, ())) != 1:
        return None
    return target


def _first_non_phi_index(block) -> int:
    for idx, inst in enumerate(block.instructions):
        if " = phi " not in inst.text:
            return idx
    return len(block.instructions)
