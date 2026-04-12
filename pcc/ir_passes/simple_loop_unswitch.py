"""Simple Loop Unswitching — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp``
  implements :cpp:class:`llvm::SimpleLoopUnswitchPass`. When a loop
  header contains a ``br i1 %invariant, label %A, label %B`` where
  ``%invariant`` is defined outside the loop, the pass duplicates
  the loop and sinks the decision above it: the preheader now
  branches between two specialized loops, one always taking A, the
  other always taking B.

Subset implemented here (labelled ``subset``, built on
:mod:`pcc.ir_passes.ir_mutator`):

- The loop must have a unique preheader, a single latch, and a
  single exit block.
- The unswitch candidate is a ``br i1 %cond, label %T, label %F`` in
  the header, where ``%cond`` is defined *outside* the loop.
- No other occurrences of ``%cond`` control flow inside the loop
  (we only handle the header branch).

Transform:

1. Clone every block in the loop with prefix ``unsw.t`` (true side)
   and ``unsw.f`` (false side).
2. Rewrite each clone's header terminator: the true-clone's header
   always branches to ``%T.clone``, the false-clone's header to
   ``%F.clone``.
3. Replace the original preheader's ``br label %header`` with
   ``br i1 %cond, label %header.unsw.t, label %header.unsw.f``.
4. Rewrite exit-phi incomings to accept both clones' latches.

Full unswitch (nested conditions, partial unswitch) is deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import BasicBlock, Function, Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class SimpleLoopUnswitchPass(ModulePass):
    name = "pcc-simple-loop-unswitch"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = unswitch_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_HEADER_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+%(?P<cond>[\w\.]+)\s*,\s*"
    r"label\s+%(?P<t>[\w\.]+)\s*,\s*label\s+%(?P<f>[\w\.]+)\s*$"
)


def unswitch_module(ir_text: str) -> tuple[str, bool]:
    # Walk: for each loop in each function, try to unswitch.
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()

    any_change = False
    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        cfg = CFG.of_function(fn)
        for loop in info.loops():
            candidate = _unswitch_candidate(fn, loop, cfg)
            if candidate is None:
                continue
            cond_name, t_target, f_target, preheader, latch, exit_block = candidate
            try:
                new_ir = _do_unswitch(
                    ir_text, fn.name, loop, cond_name,
                    t_target, f_target, preheader, latch, exit_block,
                )
                llvm.parse_assembly(new_ir).verify()
                ir_text = new_ir
                any_change = True
                # After mutation, refresh the binding module for
                # the next loop iteration.
                binding_mod = llvm.parse_assembly(ir_text)
                binding_mod.verify()
                break  # move on; subsequent passes handle rest
            except (RuntimeError, ValueError):
                continue
        if any_change:
            break
    return ir_text, any_change


def _unswitch_candidate(fn, loop, cfg):
    """Return (cond, t, f, preheader, latch, exit) or None."""
    preds = cfg.predecessors.get(loop.header, ())
    ext = [p for p in preds if p not in loop.blocks]
    if len(ext) != 1:
        return None
    preheader = ext[0]
    if len(loop.latches) != 1:
        return None
    latch = loop.latches[0]
    exits = loop.exit_blocks(cfg)
    if len(exits) != 1:
        return None
    exit_block = exits[0]

    # Find the header's terminator.
    header_block = None
    for block in fn.blocks:
        if block.name == loop.header:
            header_block = block
            break
    if header_block is None:
        return None
    term = None
    for inst in header_block.instructions:
        term = inst
    if term is None:
        return None
    m = _HEADER_COND_BR_RE.match(str(term).strip())
    if not m:
        return None
    cond = m.group("cond")

    # Check %cond is defined outside the loop.
    # A value is outside-defined if no instruction in loop.blocks
    # produces it.
    for block in fn.blocks:
        if block.name not in loop.blocks:
            continue
        for inst in block.instructions:
            inst_text = str(inst).strip()
            defm = re.match(r"^%([\w\.]+)\s*=", inst_text)
            if defm and defm.group(1) == cond:
                return None

    return cond, m.group("t"), m.group("f"), preheader, latch, exit_block


def _do_unswitch(
    ir_text: str,
    fn_name: str,
    loop,
    cond: str,
    t_target: str,
    f_target: str,
    preheader: str,
    latch: str,
    exit_block: str,
) -> str:
    m = MutableModule.parse(ir_text)
    fn = m.function(fn_name)
    if fn is None:
        raise ValueError(f"function {fn_name} missing")

    loop_blocks = [b for b in fn.blocks if b.name in loop.blocks]
    if not loop_blocks:
        raise ValueError("loop blocks not found")

    true_clones = m.clone_blocks(fn, loop_blocks, "unsw.t")
    false_clones = m.clone_blocks(fn, loop_blocks, "unsw.f")

    # In each clone, rewrite the header's conditional branch to
    # an unconditional branch to the appropriate target's clone.
    def _specialize(clones: list[BasicBlock], take_true: bool) -> None:
        target = t_target if take_true else f_target
        header_clone_name = _prefixed(loop.header, "unsw.t" if take_true else "unsw.f")
        target_clone_name = _prefixed(target, "unsw.t" if take_true else "unsw.f")
        for b in clones:
            if b.name != header_clone_name:
                continue
            term = b.terminator
            if term is None:
                continue
            # Replace terminator with br label %target_clone.
            m.rewrite_terminator(
                b, f"  br label %{target_clone_name}\n"
            )

    _specialize(true_clones, take_true=True)
    _specialize(false_clones, take_true=False)

    # Insert clones into the function, after the original exit block.
    m.insert_blocks_after(fn, exit_block, true_clones + false_clones)

    # Rewrite the preheader's terminator: previously `br label %header`
    # now `br i1 %cond, label %unsw.t.header, label %unsw.f.header`.
    preheader_block = fn.block(preheader)
    if preheader_block is None:
        raise ValueError("preheader block missing")
    true_header = _prefixed(loop.header, "unsw.t")
    false_header = _prefixed(loop.header, "unsw.f")
    m.rewrite_terminator(
        preheader_block,
        f"  br i1 %{cond}, label %{true_header}, label %{false_header}\n",
    )

    # The original loop blocks are now unreachable. Remove them to
    # avoid leaving dead labels (and their latch's backward edge
    # to the now-dead header, which would fail verify).
    keep = [b for b in fn.blocks if b.name not in loop.blocks]
    fn.blocks = keep

    # Fix exit-block phis: originally had incoming from latch, now
    # we have two latches (true + false clones).
    exit_b = fn.block(exit_block)
    if exit_b is not None:
        _duplicate_phi_incoming(exit_b, latch, [
            _prefixed(latch, "unsw.t"),
            _prefixed(latch, "unsw.f"),
        ])

    return m.serialize()


def _prefixed(name: str, prefix: str) -> str:
    return f"{prefix}.{name}"


def _duplicate_phi_incoming(
    block: BasicBlock, old_pred: str, new_preds: list[str]
) -> None:
    """In every phi in ``block``, replace `[val, %old_pred]` with one
    incoming per name in ``new_preds``, each carrying the same value.
    """
    pattern = re.compile(
        r"\[\s*(?P<val>[^,\]]+?)\s*,\s*%"
        + re.escape(old_pred) + r"[ \t]*\]"
    )
    for inst in block.instructions:
        if " = phi " not in inst.text:
            continue

        def repl(m: re.Match, new_preds=new_preds) -> str:
            val = m.group("val")
            return ", ".join(f"[ {val}, %{p} ]" for p in new_preds)

        inst.text = pattern.sub(repl, inst.text)
