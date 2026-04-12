"""Loop InstSimplify — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopInstSimplify.cpp``
  simplifies instructions in loop blocks while respecting loop exit
  semantics and LCSSA.

Subset implemented here (labelled ``subset``):

- Reuse the same arithmetic/icmp/select identities as
  :mod:`pcc.ir_passes.instsimplify`.
- Only rewrite instructions that are located in loop blocks.
- Only rewrite instructions whose results are not used outside the loop
  (live-outs remain deferred because they require explicit LCSSA repair).

This is enough to match focused upstream shapes where algebraic
simplifications stay local to the loop.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .instsimplify import (
    _BINOP_RE,
    _ICMP_RE,
    _SELECT_RE,
    _simplify_binop,
    _simplify_icmp,
    _simplify_select,
)
from .ir_mutator import Instruction, MutableModule
from .loop_info import compute_loop_info
from .loop_simplifycfg import _insert_simple_exit_lcssa
from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .simplifycfg import _split_functions


class LoopInstSimplifyPass(ModulePass):
    name = "pcc-loop-instsimplify"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = loop_instsimplify_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_instsimplify_text(ir_text: str) -> tuple[str, bool]:
    out: list[str] = []
    changed = False
    for is_function, chunk in _split_functions(ir_text):
        if not is_function:
            out.append(chunk)
            continue
        new_chunk, local = _rewrite_loop_function(chunk)
        if local:
            new_chunk, lcssa_changed = _insert_simple_exit_lcssa(new_chunk)
            local = local or lcssa_changed
        out.append(new_chunk)
        changed = changed or local
    if not changed:
        return ir_text, False
    return "".join(out), True


def _rewrite_loop_function(fn_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(fn_text)
    binding_mod.verify()
    fn = next((f for f in binding_mod.functions if not f.is_declaration), None)
    if fn is None:
        return fn_text, False
    info = compute_loop_info(fn)
    loops = info.loops()
    if not loops:
        return fn_text, False

    loop_blocks = {block for loop in loops for block in loop.blocks}
    mut = MutableModule.parse(fn_text)
    if not mut.functions:
        return fn_text, False
    mut_fn = mut.functions[0]

    replacements: dict[str, str] = {}
    changed = False
    for block in mut_fn.blocks:
        if block.name not in loop_blocks:
            continue
        kept: list[Instruction] = []
        for inst in block.instructions:
            stripped = inst.text.rstrip("\n")
            rep = None
            result_name = inst.result_name
            if result_name and _used_outside_loop(mut_fn, loop_blocks, result_name):
                kept.append(inst)
                continue
            if m := _BINOP_RE.match(stripped):
                rep = _simplify_binop(
                    m.group("op"),
                    m.group("ty"),
                    m.group("lhs"),
                    m.group("rhs"),
                )
            elif m := _ICMP_RE.match(stripped):
                rep = _simplify_icmp(
                    m.group("pred"),
                    m.group("ty"),
                    m.group("lhs"),
                    m.group("rhs"),
                )
            elif m := _SELECT_RE.match(stripped):
                rep = _simplify_select(
                    m.group("cond"),
                    m.group("tval"),
                    m.group("fval"),
                )
            if rep is not None and result_name is not None:
                replacements[result_name] = rep
                changed = True
                continue
            kept.append(inst)
        block.instructions = kept

    if not changed:
        return fn_text, False

    for _ in range(8):
        any_sub = False
        for block in mut_fn.blocks:
            new_insts: list[Instruction] = []
            for inst in block.instructions:
                new_text = inst.text
                for name, rep in replacements.items():
                    new_text = re.sub(r"%" + re.escape(name) + r"\b", rep, new_text)
                if new_text != inst.text:
                    any_sub = True
                    new_insts.append(Instruction.from_text(new_text))
                else:
                    new_insts.append(inst)
            block.instructions = new_insts
        if not any_sub:
            break

    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _used_outside_loop(fn, loop_blocks: set[str], name: str) -> bool:
    pattern = re.compile(r"%" + re.escape(name) + r"\b")
    for block in fn.blocks:
        if block.name in loop_blocks:
            continue
        for inst in block.instructions:
            if pattern.search(inst.text):
                return True
    return False
