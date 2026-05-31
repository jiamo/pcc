"""Loop SimplifyCFG — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopSimplifyCFG.cpp``
  performs loop-local CFG cleanup, constant-folds loop-local branches,
  merges trivially forwarding loop blocks, and preserves canonical
  loop-exit form.

Subset implemented here (labelled ``subset``):

- Reuse :mod:`pcc.ir_passes.simplifycfg` after extending it to fold
  conditionals in any block, not just the function entry.
- On top of that, insert a simple LCSSA phi for an exit block that
  directly returns a loop-internal SSA value and has a single incoming
  edge from the loop.

This is enough to match a focused upstream shape where a loop body
contains a constant local branch that feeds a latch through empty
forwarders.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .simplifycfg import (
    _function_chunk_module,
    _module_context_for_function,
    _split_functions,
    simplify_cfg_text,
)


_RET_SSA_RE = re.compile(r"^\s*ret\s+(?P<ty>.+?)\s+%(?P<name>[\w\.]+)\s*$")


class LoopSimplifyCFGPass(ModulePass):
    name = "pcc-loop-simplifycfg"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = loop_simplifycfg_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_simplifycfg_text(ir_text: str) -> tuple[str, bool]:
    current = ir_text
    any_changed = False
    for _ in range(4):
        current, changed_cfg = _simplify_loop_functions_only(current)
        current, changed_lcssa = _insert_simple_exit_lcssa(current)
        if not (changed_cfg or changed_lcssa):
            break
        any_changed = True
    return current, any_changed


def _simplify_loop_functions_only(ir_text: str) -> tuple[str, bool]:
    out: list[str] = []
    changed = False
    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            out.append(chunk)
            continue
        context = _module_context_for_function(ir_text, chunk)
        if not _function_has_loops(chunk, context):
            out.append(chunk)
            continue
        wrapped = _function_chunk_module(context, chunk)
        rewritten, local = simplify_cfg_text(wrapped)
        new_chunk = chunk
        if local:
            for rewritten_is_function, rewritten_chunk in _split_functions(rewritten):
                if rewritten_is_function:
                    new_chunk = rewritten_chunk
                    break
        out.append(new_chunk)
        changed = changed or local
    if not changed:
        return ir_text, False
    return "".join(out), True


def _function_has_loops(fn_text: str, module_context: str = "") -> bool:
    mod = llvm.parse_assembly(_function_chunk_module(module_context, fn_text))
    mod.verify()
    for fn in mod.functions:
        if fn.is_declaration:
            continue
        return bool(compute_loop_info(fn).loops())
    return False


def _insert_simple_exit_lcssa(ir_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()
    mut = MutableModule.parse(ir_text)
    changed = False

    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        cfg = CFG.of_function(fn)
        mut_fn = mut.function(fn.name)
        if mut_fn is None:
            continue
        for loop in info.loops():
            defined_in_loop: set[str] = set()
            for block in fn.blocks:
                if block.name not in loop.blocks:
                    continue
                for inst in block.instructions:
                    if inst.name:
                        defined_in_loop.add(inst.name)

            for exit_name in loop.exit_blocks(cfg):
                preds = list(cfg.predecessors.get(exit_name, ()))
                inside_preds = [pred for pred in preds if pred in loop.blocks]
                if len(preds) != 1 or len(inside_preds) != 1:
                    continue
                exit_block = mut_fn.block(exit_name)
                if exit_block is None or not exit_block.instructions:
                    continue
                if any(" = phi " in inst.text for inst in exit_block.instructions[:-1]):
                    continue
                ret = exit_block.instructions[-1]
                m = _RET_SSA_RE.match(ret.text.strip())
                if m is None:
                    continue
                val_name = m.group("name")
                if val_name not in defined_in_loop:
                    continue
                phi_name = _fresh_name(mut_fn, f"{val_name}.lcssa")
                pred = inside_preds[0]
                phi_ty = m.group("ty").strip()
                exit_block.instructions = [
                    Instruction.from_text(
                        f"  %{phi_name} = phi {phi_ty} [ %{val_name}, %{pred} ]\n"
                    ),
                    *exit_block.instructions[:-1],
                    Instruction.from_text(f"  ret {phi_ty} %{phi_name}\n"),
                ]
                changed = True

    if not changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _fresh_name(fn, base: str) -> str:
    names = fn.defined_names()
    if base not in names:
        return base
    idx = 1
    while f"{base}.{idx}" in names:
        idx += 1
    return f"{base}.{idx}"
