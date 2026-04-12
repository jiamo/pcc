"""InferAlignment — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/InferAlignment.cpp``
  walks every load/store, uses ``KnownBits`` plus dominating
  ``llvm.assume`` facts to compute a tighter alignment, and rewrites
  the instruction with the stronger ``align`` if one is derivable.

Subset implemented here (labelled ``subset``):

- For each load/store whose pointer operand is the direct SSA result
  of an ``alloca`` in the same function, use the ``alloca``'s
  declared ``align N``.
- If the ``load``/``store`` either has no ``align`` or has a strictly
  smaller one, rewrite it to the stronger ``align``.
- Only handles the straightforward ``load ... ptr %a`` and
  ``store ... ptr %a`` shapes; GEPs, bitcasts, ``llvm.assume``, and
  dominator-directed inference remain LLVM territory.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .ir_mutator import Function, MutableModule
from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_ALLOCA_RE = re.compile(
    r"""
    ^\s*
    %(?P<name>[\w\.]+)\s*=\s*
    alloca\s+
    (?P<ty>.+?)
    (?:,\s*align\s+(?P<align>\d+))?
    \s*$
    """,
    re.VERBOSE,
)
_ZERO_GEP_RE = re.compile(
    r"""
    ^\s*
    %(?P<name>[\w\.]+)\s*=\s*
    getelementptr(?:\s+inbounds)?\s+
    .+?,\s*ptr\s+%(?P<base>[\w\.]+)
    (?P<indices>(?:\s*,\s*i\d+\s+[^,\s]+)+)
    \s*$
    """,
    re.VERBOSE,
)
_BITCAST_RE = re.compile(
    r"""
    ^\s*
    %(?P<name>[\w\.]+)\s*=\s*
    bitcast\s+ptr\s+%(?P<base>[\w\.]+)\s+to\s+ptr
    \s*$
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


class InferAlignmentPass(ModulePass):
    name = "pcc-infer-alignment"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        new_text, changed = infer_alignment_text(str(module))
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def infer_alignment_text(ir_text: str) -> tuple[str, bool]:
    mut = MutableModule.parse(ir_text)
    changed = False
    for fn in mut.functions:
        if _infer_alignment_in_function(fn):
            changed = True
    if not changed:
        return ir_text, False
    new_text = mut.serialize()
    llvm.parse_assembly(new_text).verify()
    return new_text, True


def _infer_alignment_in_function(fn: Function) -> bool:
    alloca_align = _collect_alloca_alignments(fn)
    if not alloca_align:
        return False
    changed = False
    for block in fn.blocks:
        for inst in block.instructions:
            if _promote_alignment(inst, alloca_align):
                changed = True
    return changed


def _collect_alloca_alignments(fn: Function) -> dict[str, int]:
    out: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for block in fn.blocks:
            for inst in block.instructions:
                text = inst.text.rstrip("\n")
                m = _ALLOCA_RE.match(text)
                if m is not None:
                    align = m.group("align")
                    if align is None:
                        continue
                    name = m.group("name")
                    value = int(align)
                    if out.get(name) != value:
                        out[name] = value
                        changed = True
                    continue
                m = _ZERO_GEP_RE.match(text)
                if m is not None:
                    if not _all_indices_zero(m.group("indices")):
                        continue
                    base_align = out.get(m.group("base"))
                    if base_align is None:
                        continue
                    name = m.group("name")
                    if out.get(name) != base_align:
                        out[name] = base_align
                        changed = True
                    continue
                m = _BITCAST_RE.match(text)
                if m is None:
                    continue
                base_align = out.get(m.group("base"))
                if base_align is None:
                    continue
                name = m.group("name")
                if out.get(name) != base_align:
                    out[name] = base_align
                    changed = True
    return out


def _all_indices_zero(indices_text: str) -> bool:
    for part in indices_text.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if not pieces:
            continue
        if pieces[-1] != "0":
            return False
    return True


def _promote_alignment(inst, alloca_align: dict[str, int]) -> bool:
    text = inst.text.rstrip("\n")
    trailing_newline = "\n" if inst.text.endswith("\n") else ""
    m = _LOAD_RE.match(text) or _STORE_RE.match(text)
    if m is None:
        return False
    ptr = m.group("ptr")
    target = alloca_align.get(ptr)
    if target is None:
        return False
    prefix = m.group("prefix")
    tail = m.group("tail")
    align_m = _ALIGN_TAIL_RE.search(tail)
    if align_m is not None:
        current = int(align_m.group("align"))
        if current >= target:
            return False
        new_tail = (
            tail[: align_m.start()]
            + f", align {target}"
            + tail[align_m.end():]
        )
    else:
        new_tail = f", align {target}" + tail
    inst.text = prefix + new_tail + trailing_newline
    return True
