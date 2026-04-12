"""SLP Vectorizer — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Vectorize/SLPVectorizer.cpp``
  implements :cpp:class:`llvm::SLPVectorizerPass`. SLP stands for
  "Straight-line SLP (Superword Level Parallelism)". The pass finds
  isomorphic scalar operation chains that operate on adjacent memory
  and packs them into SIMD vector operations.

Subset implemented here (labelled ``subset``):

Recognize four consecutive ``store`` instructions in the same basic
block, all of the same integer element type, to four contiguous
addresses reached via ``getelementptr`` offsets 0, 1, 2, 3 from a
common base pointer, and storing four values that can be packed
into a ``<4 x TY>`` vector. Replace with a single vector store.

This narrow slice is enough to prove the transform works end-to-end
and composes with the rest of the pipeline. Full SLP (recursive
tree of candidate packs, cost model, reductions) is deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import BasicBlock, Instruction, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class SLPVectorizerPass(ModulePass):
    name = "pcc-slp-vectorizer"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = slp_vectorize_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def slp_vectorize_module(ir_text: str) -> tuple[str, bool]:
    m = MutableModule.parse(ir_text)
    any_change = False
    for fn in m.functions:
        for block in fn.blocks:
            while _try_pack_block(m, block):
                any_change = True
    if not any_change:
        return ir_text, False
    try:
        m.verify_roundtrip()
    except RuntimeError:
        return ir_text, False
    return m.serialize(), True


_GEP_I32_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*getelementptr\s+(?:inbounds\s+)?"
    r"(?P<elem>i\d+)\s*,\s*ptr\s+%(?P<base>[\w\.]+)"
    r"\s*,\s*i\d+\s+(?P<idx>-?\d+)\s*$"
)
_STORE_RE = re.compile(
    r"^\s*store\s+(?P<ty>i\d+)\s+(?P<val>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


def _try_pack_block(m: MutableModule, block: BasicBlock) -> bool:
    """Scan the block for 4 adjacent stores packable into a vector."""
    # Map GEP result names to (base, idx, elem_ty) when we see them
    # immediately preceding their use.
    gep_of_name: dict[str, tuple[str, int, str]] = {}
    for inst in block.instructions:
        gm = _GEP_I32_RE.match(inst.text.rstrip("\n"))
        if gm:
            gep_of_name[gm.group("res")] = (
                gm.group("base"), int(gm.group("idx")), gm.group("elem"),
            )

    # Find 4 consecutive store instructions (possibly interleaved with
    # GEPs) that match the pattern.
    insts = block.instructions
    for i in range(len(insts) - 3):
        run = _try_match_four_stores(insts, i, gep_of_name)
        if run is None:
            continue
        start_idx, store_indices, base, elem_ty, vals = run
        # Replace: remove the 4 store lines, insert single vector store.
        # Also drop the GEPs that are dead (if their only user was the
        # removed store).
        gep_indices_to_drop: list[int] = []
        for s_idx in store_indices:
            sm = _STORE_RE.match(insts[s_idx].text.rstrip("\n"))
            ptr_name = sm.group("ptr")
            # Find the GEP that defined this ptr in the block.
            for gi, g_inst in enumerate(insts[:s_idx]):
                gmm = _GEP_I32_RE.match(g_inst.text.rstrip("\n"))
                if gmm and gmm.group("res") == ptr_name:
                    # Only drop if this GEP is used nowhere else in
                    # the block.
                    still_used = False
                    for other_i, other in enumerate(insts):
                        if other_i in store_indices:
                            continue
                        if other_i == gi:
                            continue
                        if f"%{ptr_name}" in other.text:
                            still_used = True
                            break
                    if not still_used:
                        gep_indices_to_drop.append(gi)
                    break
        # The vector store uses the *base* pointer directly (offset 0
        # from the base equals ``base``). Emit a fresh GEP against
        # ``base`` to mirror upstream's explicit addressing, so the
        # resulting IR verifies even after dropping the per-lane GEPs.
        drop_set = set(store_indices) | set(gep_indices_to_drop)
        new_list: list[Instruction] = []
        inserted = False
        for idx, inst in enumerate(insts):
            if idx in drop_set:
                if not inserted:
                    splat_build = _build_vector_splat(vals, elem_ty)
                    new_list.extend(splat_build)
                    new_list.append(Instruction.from_text(
                        f"  %slp.base = getelementptr {elem_ty}, "
                        f"ptr %{base}, i32 0\n"
                    ))
                    new_list.append(Instruction.from_text(
                        f"  store <4 x {elem_ty}> %slp.vec, ptr %slp.base\n"
                    ))
                    inserted = True
                continue
            new_list.append(inst)
        block.instructions = new_list
        return True
    return False


def _try_match_four_stores(
    insts: list[Instruction],
    start: int,
    gep_of_name: dict[str, tuple[str, int, str]],
) -> tuple[int, list[int], str, str, list[str]] | None:
    """Starting near ``start``, search for 4 stores to offsets 0,1,2,3
    of the same base, with the same element type.

    Returns (start, [store_indices], base, elem_ty, [vals]) or None.
    """
    matched: list[tuple[int, str, int, str, str]] = []  # (inst_idx, base, idx, elem_ty, val)
    base_base: str | None = None
    base_ty: str | None = None
    for j in range(start, len(insts)):
        if len(matched) == 4:
            break
        inst = insts[j]
        sm = _STORE_RE.match(inst.text.rstrip("\n"))
        if not sm:
            continue
        ptr = sm.group("ptr")
        if ptr not in gep_of_name:
            return None
        base, idx, elem_ty = gep_of_name[ptr]
        if sm.group("ty") != elem_ty:
            return None
        if base_base is None:
            base_base = base
            base_ty = elem_ty
        elif base != base_base or elem_ty != base_ty:
            return None
        matched.append((j, base, idx, elem_ty, sm.group("val").strip()))
    if len(matched) != 4:
        return None
    # Sort by offset and require indices {0,1,2,3} contiguous.
    sorted_matched = sorted(matched, key=lambda t: t[2])
    if [m[2] for m in sorted_matched] != [0, 1, 2, 3]:
        return None
    # Preserve original instruction order for replacement scope.
    idx_in_block_order = sorted(m[0] for m in matched)
    vals_in_offset_order = [m[4] for m in sorted_matched]
    assert base_ty is not None and base_base is not None
    return (
        idx_in_block_order[0],
        idx_in_block_order,
        base_base,
        base_ty,
        vals_in_offset_order,
    )


def _build_vector_splat(vals: list[str], elem_ty: str) -> list[Instruction]:
    """Emit IR to build a ``<4 x elem_ty>`` from four scalar values."""
    # Start with poison vector, insertelement four times.
    out: list[Instruction] = []
    acc = "poison"
    for i, val in enumerate(vals):
        name = f"slp.ins{i}" if i < 3 else "slp.vec"
        prev = acc
        out.append(Instruction.from_text(
            f"  %{name} = insertelement <4 x {elem_ty}> {prev}, "
            f"{elem_ty} {val}, i32 {i}\n"
        ))
        acc = f"%{name}"
    return out
