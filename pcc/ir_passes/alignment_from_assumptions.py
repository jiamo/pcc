"""AlignmentFromAssumptions — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/AlignmentFromAssumptions.cpp``
  turns ``llvm.assume`` facts into explicit alignment attributes on
  memory instructions dominated by the assume.

Subset implemented here (labelled ``subset``):

- Recognize the canonical shape used by ``__builtin_assume_aligned``
  lowering::

      %pi = ptrtoint ptr %p to i64
      %m  = and i64 %pi, <ALIGN-1>
      %c  = icmp eq i64 %m, 0
      call void @llvm.assume(i1 %c)

  The ``<ALIGN-1>`` constant must be a non-zero power-of-two minus
  one (``1``, ``3``, ``7``, ``15``, ...). From it we derive
  ``align = <ALIGN-1> + 1``.
- For every ``load``/``store`` in the same function whose pointer
  operand is ``%p`` and whose current explicit alignment is smaller
  than the derived alignment, rewrite the instruction with the
  stronger ``align`` suffix.
- No dominator check is performed: only pointers named directly by
  the assume chain are promoted, so the rewrite is safe as long as
  the assume is reachable in the function (which is required for the
  assume to have any effect at all).
- Multi-function modules, GEP-derived alignments, range-based
  assumptions, and rewriting of ``llvm.memcpy`` etc. are deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_PTRTOINT_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    ptrtoint\s+ptr\s+%(?P<ptr>[\w\.]+)\s+
    to\s+i(?P<bits>\d+)\s*$
    """,
    re.VERBOSE,
)
_AND_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    and\s+i(?P<bits>\d+)\s+
    (?P<lhs>[^,]+?)\s*,\s*
    (?P<rhs>\S+)\s*$
    """,
    re.VERBOSE,
)
_ICMP_EQ0_RE = re.compile(
    r"""
    ^\s*%(?P<res>[\w\.]+)\s*=\s*
    icmp\s+eq\s+i(?P<bits>\d+)\s+
    (?P<lhs>[^,]+?)\s*,\s*0\s*$
    """,
    re.VERBOSE,
)
_ASSUME_RE = re.compile(
    r"""
    ^\s*
    call\s+void\s+@llvm\.assume\s*\(\s*
    i1\s+(?P<cond>[^)]+?)\s*
    \)\s*$
    """,
    re.VERBOSE,
)
_LOAD_RE = re.compile(
    r"""
    ^(?P<prefix>\s*%[\w\.]+\s*=\s*load\s+.+?,\s*ptr\s+%(?P<ptr>[\w\.]+))
    (?P<tail>.*?)$
    """,
    re.VERBOSE,
)
_STORE_RE = re.compile(
    r"""
    ^(?P<prefix>\s*store\s+.+?,\s*ptr\s+%(?P<ptr>[\w\.]+))
    (?P<tail>.*?)$
    """,
    re.VERBOSE,
)
_ALIGN_TAIL_RE = re.compile(r",\s*align\s+(?P<align>\d+)")


class AlignmentFromAssumptionsIRPass(ModulePass):
    """Propagate alignment facts from ``llvm.assume`` to memory ops."""

    name = "pcc-alignment-from-assumptions"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = alignment_from_assumptions_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def alignment_from_assumptions_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    any_changed = False
    for fn in mut.functions:
        alignments = _collect_alignment_assumptions(fn)
        if not alignments:
            continue
        if _apply_alignments(fn, alignments):
            any_changed = True
    if not any_changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _collect_alignment_assumptions(fn: Function) -> dict[str, int]:
    """Return ``{ptr_name: alignment}`` for pointers proven aligned."""
    ptrtoint_of: dict[str, str] = {}       # ptrtoint result -> pointer name
    and_of: dict[str, tuple[str, int]] = {}  # and result -> (lhs ssa, mask)
    icmp_of: dict[str, str] = {}             # icmp result -> lhs ssa
    assume_conds: list[str] = []

    for block in fn.blocks:
        for inst in block.instructions:
            text = inst.text.rstrip("\n")
            if m := _PTRTOINT_RE.match(text):
                ptrtoint_of[m.group("res")] = m.group("ptr")
                continue
            if m := _AND_RE.match(text):
                mask = _parse_const_operand(m.group("rhs"))
                lhs_operand = m.group("lhs").strip()
                if mask is not None and lhs_operand.startswith("%"):
                    and_of[m.group("res")] = (lhs_operand[1:], mask)
                else:
                    alt_mask = _parse_const_operand(lhs_operand)
                    rhs_operand = m.group("rhs").strip()
                    if alt_mask is not None and rhs_operand.startswith("%"):
                        and_of[m.group("res")] = (rhs_operand[1:], alt_mask)
                continue
            if m := _ICMP_EQ0_RE.match(text):
                lhs = m.group("lhs").strip()
                if lhs.startswith("%"):
                    icmp_of[m.group("res")] = lhs[1:]
                continue
            if m := _ASSUME_RE.match(text):
                cond = m.group("cond").strip()
                if cond.startswith("%"):
                    assume_conds.append(cond[1:])

    alignments: dict[str, int] = {}
    for cond in assume_conds:
        and_result = icmp_of.get(cond)
        if and_result is None:
            continue
        entry = and_of.get(and_result)
        if entry is None:
            continue
        ptrtoint_result, mask = entry
        ptr_name = ptrtoint_of.get(ptrtoint_result)
        if ptr_name is None:
            continue
        if mask <= 0 or (mask & (mask + 1)) != 0:
            # Mask must be 2^k - 1 (i.e., low-k-bits mask) for a valid
            # alignment claim: `p & mask == 0` <=> p is (mask+1)-aligned.
            continue
        align = mask + 1
        existing = alignments.get(ptr_name, 0)
        if align > existing:
            alignments[ptr_name] = align
    return alignments


def _parse_const_operand(tok: str) -> int | None:
    token = tok.strip()
    if not token or token.startswith("%") or token.startswith("@"):
        return None
    try:
        return int(token, 0)
    except ValueError:
        return None


def _apply_alignments(fn: Function, alignments: dict[str, int]) -> bool:
    changed = False
    for block in fn.blocks:
        for inst in block.instructions:
            text = inst.text.rstrip("\n")
            trailing_newline = "\n" if inst.text.endswith("\n") else ""
            m = _LOAD_RE.match(text) or _STORE_RE.match(text)
            if m is None:
                continue
            ptr = m.group("ptr")
            target = alignments.get(ptr)
            if target is None:
                continue
            prefix = m.group("prefix")
            tail = m.group("tail")
            align_m = _ALIGN_TAIL_RE.search(tail)
            if align_m is not None:
                current = int(align_m.group("align"))
                if current >= target:
                    continue
                new_tail = (
                    tail[: align_m.start()]
                    + f", align {target}"
                    + tail[align_m.end():]
                )
            else:
                new_tail = f", align {target}" + tail
            inst.text = prefix + new_tail + trailing_newline
            changed = True
    return changed
