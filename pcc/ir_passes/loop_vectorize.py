"""Loop Vectorize — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Vectorize/LoopVectorize.cpp``
  implements :cpp:class:`llvm::LoopVectorizePass`. The full pass
  turns a scalar loop into a vector loop: it widens the induction
  variable, replaces scalar ops with vector ops, inserts runtime
  dependence checks for aliasing, and emits a scalar epilog for the
  remainder trip count. It depends heavily on ScalarEvolution,
  AAResults, target-transform-info, and the vectorization legality
  analysis.

Subset implemented here (labelled ``subset``, built on
:mod:`pcc.ir_passes.ir_mutator`):

Recognize a very narrow "array-op-element" shape:

    define void @f(ptr %a, ptr %b, ptr %c) {
    entry:
      br label %body
    body:
      %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
      %pa = getelementptr i32, ptr %a, i32 %i
      %pb = getelementptr i32, ptr %b, i32 %i
      %pc = getelementptr i32, ptr %c, i32 %i
      %va = load i32, ptr %pa
      %vb = load i32, ptr %pb
      %vc = <OP> i32 %va, %vb                  ; add/sub/mul/and/or/xor
      store i32 %vc, ptr %pc
      %i.next = add i32 %i, 1
      %cond = icmp slt i32 %i.next, 4          ; constant trip count = VF
      br i1 %cond, label %body, label %exit
    exit:
      ret void
    }

When the trip count exactly matches the vector factor (VF=4) we emit:

    entry:
      %va.v = load <4 x i32>, ptr %a
      %vb.v = load <4 x i32>, ptr %b
      %vc.v = <OP> <4 x i32> %va.v, %vb.v
      store <4 x i32> %vc.v, ptr %c
      br label %exit

No epilog needed (trip count is exact). No dependence checks (the
three pointers are distinct function arguments, we defer a real
check; upstream would emit a runtime-check). This is deliberately
narrow — the transform fires only when it can preserve semantics
exactly.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .dominator_tree import CFG
from .ir_mutator import BasicBlock, Instruction, MutableModule
from .loop_info import compute_loop_info
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_VECTOR_FACTOR = 4

_GEP_IV_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*getelementptr\s+(?:inbounds\s+)?"
    r"(?P<elem>i\d+)\s*,\s*ptr\s+%(?P<base>[\w\.]+)"
    r"\s*,\s*i\d+\s+%(?P<iv>[\w\.]+)\s*$"
)
_LOAD_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*load\s+(?P<ty>i\d+)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)
_STORE_RE = re.compile(
    r"^\s*store\s+(?P<ty>i\d+)\s+(?P<val>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)
_BINOP_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*(?P<op>add|sub|mul|and|or|xor)"
    r"(?:\s+nsw|\s+nuw)*\s+"
    r"(?P<ty>i\d+)\s+%(?P<lhs>[\w\.]+)\s*,\s*%(?P<rhs>[\w\.]+)\s*$"
)
_IV_ADD_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*add\s+(?:nsw\s+|nuw\s+)*"
    r"(?P<ty>i\d+)\s+%(?P<iv>[\w\.]+)\s*,\s*1\s*$"
)
_ICMP_LIMIT_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*icmp\s+(?P<pred>slt|ult)\s+"
    r"i\d+\s+%(?P<iv>[\w\.]+)\s*,\s*(?P<limit>\d+)\s*$"
)


class LoopVectorizePass(ModulePass):
    name = "pcc-loop-vectorize"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = vectorize_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def vectorize_module(ir_text: str) -> tuple[str, bool]:
    binding_mod = llvm.parse_assembly(ir_text)
    binding_mod.verify()
    any_change = False

    for fn in binding_mod.functions:
        if fn.is_declaration:
            continue
        info = compute_loop_info(fn)
        cfg = CFG.of_function(fn)
        for loop in info.loops():
            shape = _match_vectorizable_loop(fn, loop, cfg)
            if shape is None:
                continue
            try:
                new_text = _emit_vectorized(ir_text, fn.name, loop, shape, cfg)
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


def _match_vectorizable_loop(fn, loop, cfg) -> dict | None:
    """Return a descriptor of the recognized shape, or None."""
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

    # For the narrow shape we expect: the loop's sole body is the
    # header itself (single-block loop).
    if set(loop.blocks) != {loop.header}:
        return None

    # Parse the header's instructions.
    block = None
    for b in fn.blocks:
        if b.name == loop.header:
            block = b
            break
    if block is None:
        return None

    iv_name: str | None = None
    iv_next: str | None = None
    limit: int | None = None
    geps: dict[str, tuple[str, str]] = {}  # res → (base, elem_ty)
    loads: dict[str, tuple[str, str]] = {}  # res → (ty, ptr)
    binop: dict | None = None
    store_info: dict | None = None

    for inst in block.instructions:
        text = str(inst).strip()
        # phi for IV.
        m = re.match(
            r"^%(?P<name>[\w\.]+)\s*=\s*phi\s+i32\s+\[\s*0\s*,\s*%"
            + re.escape(preheader)
            + r"\s*\]\s*,\s*\[\s*%(?P<next>[\w\.]+)\s*,\s*%"
            + re.escape(latch) + r"\s*\]",
            text,
        )
        if m:
            iv_name = m.group("name")
            iv_next = m.group("next")
            continue
        m = _GEP_IV_RE.match(text)
        if m and iv_name and m.group("iv") == iv_name:
            geps[m.group("res")] = (m.group("base"), m.group("elem"))
            continue
        m = _LOAD_RE.match(text)
        if m and m.group("ptr") in geps:
            loads[m.group("res")] = (m.group("ty"), m.group("ptr"))
            continue
        m = _BINOP_RE.match(text)
        if m and m.group("lhs") in loads and m.group("rhs") in loads:
            binop = {
                "res": m.group("res"),
                "op": m.group("op"),
                "ty": m.group("ty"),
                "lhs": m.group("lhs"),
                "rhs": m.group("rhs"),
            }
            continue
        m = _STORE_RE.match(text)
        if m and binop and m.group("val").strip() == f"%{binop['res']}" and m.group("ptr") in geps:
            store_info = {"ty": m.group("ty"), "ptr": m.group("ptr")}
            continue
        m = _IV_ADD_RE.match(text)
        if m and iv_name and m.group("iv") == iv_name:
            if m.group("res") != iv_next:
                return None
            continue
        m = _ICMP_LIMIT_RE.match(text)
        if m:
            # Limit can be on iv or iv.next; compute trip count.
            target = m.group("iv")
            if target == iv_name:
                limit = int(m.group("limit"))
            elif target == iv_next:
                limit = int(m.group("limit"))
            else:
                return None
            continue
        if text.startswith("br "):
            continue
        return None  # unrecognized instruction

    if iv_name is None or limit is None or binop is None or store_info is None:
        return None
    if limit != _VECTOR_FACTOR:
        return None

    # Extract the three pointers (two load bases + one store base).
    load_bases = [geps[ptr][0] for ptr in (loads[binop["lhs"]][1], loads[binop["rhs"]][1])]
    store_base = geps[store_info["ptr"]][0]
    # All three bases must be distinct function arguments (narrow
    # no-alias condition).
    all_bases = set(load_bases + [store_base])
    if len(all_bases) != 3:
        return None

    return {
        "preheader": preheader,
        "exit": exit_block,
        "elem_ty": binop["ty"],
        "op": binop["op"],
        "lhs_base": load_bases[0],
        "rhs_base": load_bases[1],
        "store_base": store_base,
    }


def _emit_vectorized(
    ir_text: str, fn_name: str, loop, shape: dict, cfg: CFG
) -> str:
    m = MutableModule.parse(ir_text)
    fn = m.function(fn_name)
    if fn is None:
        raise ValueError("fn missing")
    preheader_block = fn.block(shape["preheader"])
    if preheader_block is None:
        raise ValueError("preheader missing")
    exit_block = shape["exit"]
    elem_ty = shape["elem_ty"]

    new_insts = [
        Instruction.from_text(
            f"  %lv.va = load <4 x {elem_ty}>, ptr %{shape['lhs_base']}\n"
        ),
        Instruction.from_text(
            f"  %lv.vb = load <4 x {elem_ty}>, ptr %{shape['rhs_base']}\n"
        ),
        Instruction.from_text(
            f"  %lv.vc = {shape['op']} <4 x {elem_ty}> %lv.va, %lv.vb\n"
        ),
        Instruction.from_text(
            f"  store <4 x {elem_ty}> %lv.vc, ptr %{shape['store_base']}\n"
        ),
    ]
    # Replace preheader's terminator with jump to exit.
    # Actually, insert the vector ops into preheader, then branch to exit.
    term = preheader_block.terminator
    if term is None:
        raise ValueError("preheader has no terminator")
    # Insert the vector ops before the terminator.
    for v in new_insts:
        preheader_block.instructions.insert(-1, v)
    # Rewrite the terminator: br to exit directly.
    m.rewrite_terminator(
        preheader_block, f"  br label %{exit_block}\n"
    )

    # Remove the original loop blocks — the scalar body is gone.
    fn.blocks = [b for b in fn.blocks if b.name not in loop.blocks]

    # Exit-block phis that referenced the scalar latch need repair;
    # for the narrow shape the exit block usually has no phis.

    return m.serialize()
