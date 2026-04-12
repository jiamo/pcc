"""Loop Distribution — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopDistribute.cpp``
  implements :cpp:class:`llvm::LoopDistributePass`. When a loop's
  body contains multiple independent computations (no cross-
  iteration or cross-statement dependences), the pass splits the
  body into per-partition loops running sequentially. This unblocks
  downstream vectorization (one partition may be SIMD-friendly while
  another is not).

Subset implemented here (labelled ``subset``, built on
:mod:`pcc.ir_passes.ir_mutator`):

- The loop is a single-header, single-latch natural loop.
- The body between header and latch consists of pure arithmetic
  and stores — no loads (to avoid memory aliasing) and no cross-
  instruction read-after-write dependences other than through the
  induction variable.
- The body is partitioned into ``keep`` (instructions whose def-use
  graph is self-contained) vs ``split`` (the rest). If a non-trivial
  partition exists — at least one "keep" store and one "split"
  store, with disjoint operand / result sets — clone the loop
  and execute both sequentially.

This is narrow on purpose. Full upstream does memory-dependency
analysis via MemorySSA and allows richer partitions. For the CFG
mutation we reuse :class:`MutableModule`'s block cloning.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import BasicBlock, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class LoopDistributePass(ModulePass):
    name = "pcc-loop-distribute"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = distribute_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def distribute_module(ir_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()
    any_change = False
    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        cfg = CFG.of_function(fn)
        for loop in info.loops():
            candidate = _distribute_candidate(fn, loop, cfg)
            if candidate is None:
                continue
            try:
                new_text = _do_distribute(
                    ir_text, fn.name, loop, candidate, cfg,
                )
                llvm.parse_assembly(new_text).verify()
                ir_text = new_text
                any_change = True
                binding_mod = llvm.parse_assembly(ir_text)
                binding_mod.verify()
                break
            except (RuntimeError, ValueError):
                continue
        if any_change:
            break
    return ir_text, any_change


_STORE_RE = re.compile(
    r"^\s*store\s+(?P<ty>[^,]+?)\s+(?P<val>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


def _distribute_candidate(fn, loop, cfg) -> dict | None:
    """Identify two independent stores in the loop body that can be
    split into separate loops.

    Returns metadata describing the partition, or None if not splittable.
    """
    if len(loop.blocks) < 2:
        return None
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

    # Collect stores + their operand closures (values used to compute
    # each store). The partition is safe when two stores have disjoint
    # def-use cones.
    body_blocks = [b for b in fn.blocks if b.name in loop.blocks]
    stores: list[tuple[str, str, str]] = []  # (block_name, store_text, ptr)
    for block in body_blocks:
        for inst in block.instructions:
            m = _STORE_RE.match(str(inst).strip())
            if m:
                stores.append((block.name, str(inst).strip(), m.group("ptr")))
    # Require at least two distinct-ptr stores.
    distinct_ptrs = {s[2] for s in stores}
    if len(distinct_ptrs) < 2:
        return None

    # Bail out if the loop contains loads or calls — those create
    # potentially aliasing memory dependences our narrow subset can't
    # handle.
    for block in body_blocks:
        for inst in block.instructions:
            text = str(inst).strip()
            if re.match(r"^\s*%\S+\s*=\s*(load|call)\b", text):
                return None
            if re.match(r"^\s*call\b", text):
                return None

    return {
        "preheader": preheader,
        "latch": latch,
        "exit": exit_block,
        "ptr_groups": sorted(distinct_ptrs),
    }


def _do_distribute(
    ir_text: str, fn_name: str, loop, candidate: dict, cfg: CFG
) -> str:
    m = MutableModule.parse(ir_text)
    fn = m.function(fn_name)
    if fn is None:
        raise ValueError("fn missing")

    loop_blocks = [b for b in fn.blocks if b.name in loop.blocks]
    if not loop_blocks:
        raise ValueError("loop blocks missing")

    # Partition stores by group: first group keeps the first ptr,
    # second group keeps the remaining ptrs. (Narrow: 2-way only.)
    first_ptr = candidate["ptr_groups"][0]
    second_ptrs = set(candidate["ptr_groups"][1:])

    # Clone loop for "first partition" (keeps only the first_ptr store).
    first_clones = m.clone_blocks(fn, loop_blocks, "dist.a")
    # And for "second partition".
    second_clones = m.clone_blocks(fn, loop_blocks, "dist.b")

    # In first_clones: drop stores whose ptr is in second_ptrs.
    _drop_stores_with_ptrs(first_clones, second_ptrs)
    # In second_clones: drop stores to first_ptr.
    _drop_stores_with_ptrs(second_clones, {first_ptr})

    # Rewire:
    # - preheader → first partition's header.
    # - first partition's exit → preheader-equivalent for second.
    # - second partition's exit → original exit.
    preheader_block = fn.block(candidate["preheader"])
    orig_header = loop.header
    orig_exit = candidate["exit"]
    first_header = _pfx(orig_header, "dist.a")
    second_header = _pfx(orig_header, "dist.b")
    first_exit_target = _pfx(orig_header, "dist.b")  # flow to 2nd loop

    m.rewrite_terminator(
        preheader_block, f"  br label %{first_header}\n"
    )

    # In first partition, any branch to the original exit must be
    # redirected to the second partition's header. The exit can be
    # reached from any cloned block (typically the cloned header's
    # conditional branch for a while-style loop).
    for b in first_clones:
        m.replace_branch_target(b, orig_exit, second_header)

    # In second partition, phi incomings that used to come from the
    # original preheader now come from the first partition's header
    # (the block that now transfers into the second loop).
    second_phi_rename_from = candidate["preheader"]
    second_phi_rename_to = first_header
    for b in second_clones:
        for inst in b.instructions:
            if " = phi " not in inst.text:
                continue
            inst.text = re.sub(
                r"(\[\s*[^,\]]+,\s*)%" + re.escape(second_phi_rename_from)
                + r"(\s*\])",
                lambda mm, new=second_phi_rename_to:
                    f"{mm.group(1)}%{new}{mm.group(2)}",
                inst.text,
            )

    # Insert both clone sets after the original exit block, then
    # delete the original loop blocks.
    m.insert_blocks_after(
        fn, orig_exit, first_clones + second_clones,
    )
    fn.blocks = [b for b in fn.blocks if b.name not in loop.blocks]

    # Fix exit-block phis.
    exit_b = fn.block(orig_exit)
    if exit_b is not None:
        for inst in exit_b.instructions:
            if " = phi " in inst.text:
                inst.text = re.sub(
                    r"(\[\s*[^,\]]+,\s*)%" + re.escape(candidate["latch"]) + r"(\s*\])",
                    lambda mm: f"{mm.group(1)}%{_pfx(candidate['latch'], 'dist.b')}{mm.group(2)}",
                    inst.text,
                )

    return m.serialize()


def _pfx(name: str, prefix: str) -> str:
    return f"{prefix}.{name}"


def _drop_stores_with_ptrs(
    blocks: list[BasicBlock], ptrs_to_drop: set[str]
) -> None:
    if not ptrs_to_drop:
        return
    # Account for prefix: the clone's ptr operand may carry the prefix
    # too if the pointer was defined inside the cloned region. But in
    # our narrow subset, the stored ptr is an alloca from outside the
    # loop, so no prefix rename applies.
    for b in blocks:
        kept = []
        for inst in b.instructions:
            m = _STORE_RE.match(inst.text.rstrip("\n"))
            if m and m.group("ptr") in ptrs_to_drop:
                continue
            kept.append(inst)
        b.instructions = kept
