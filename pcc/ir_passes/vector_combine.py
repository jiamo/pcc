"""Vector Combine — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Vectorize/VectorCombine.cpp``
  implements :cpp:class:`llvm::VectorCombinePass`. It runs many
  vector-specific peepholes: fold shufflevector chains, widen
  extracts of a scalar chain back to a vector op, fold
  extract-of-insert, combine insertelement chains, etc.

Subset implemented here (labelled ``subset``):

- **Fold extract-of-insert**: when an ``extractelement`` reads the
  same slot that an ``insertelement`` wrote, forward the inserted
  value directly.

    %a = insertelement <N x TY> %v, TY %x, i32 K
    %b = extractelement <N x TY> %a, i32 K
    ⇒  substitute ``%b`` with ``%x``, drop both if trivially unused.

Other vector peepholes are deferred.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_INSERT_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*insertelement\s+"
    r"<\s*(?P<n>\d+)\s*x\s*(?P<ty>[\w\s\*]+?)\s*>\s+"
    r"(?P<vec>[^,]+?)\s*,\s*\S+\s+(?P<val>[^,]+?)\s*,\s*"
    r"i32\s+(?P<idx>\d+)\s*$"
)
_EXTRACT_RE = re.compile(
    r"^\s*%(?P<res>[\w\.]+)\s*=\s*extractelement\s+"
    r"<\s*(?P<n>\d+)\s*x\s*(?P<ty>[\w\s\*]+?)\s*>\s+"
    r"%(?P<src>[\w\.]+)\s*,\s*i32\s+(?P<idx>\d+)\s*$"
)


class VectorCombinePass(ModulePass):
    name = "pcc-vector-combine"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = vector_combine_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def vector_combine_text(ir_text: str) -> tuple[str, bool]:
    """Fold extractelement-of-insertelement when indices match."""
    # Collect insertelement info: result → (n, idx, val)
    insert_info: dict[str, tuple[int, int, str]] = {}
    for line in ir_text.splitlines():
        m = _INSERT_RE.match(line.rstrip("\n"))
        if m:
            insert_info[m.group("res")] = (
                int(m.group("n")), int(m.group("idx")),
                m.group("val").strip(),
            )

    if not insert_info:
        return ir_text, False

    # Look for extractelements whose source is an insert with matching
    # index.
    subs: dict[str, str] = {}
    dead_lines: set[int] = set()
    lines = ir_text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        m = _EXTRACT_RE.match(line.rstrip("\n"))
        if not m:
            continue
        src = m.group("src")
        if src not in insert_info:
            continue
        n, insert_idx, val = insert_info[src]
        if int(m.group("idx")) != insert_idx:
            continue
        subs[m.group("res")] = val
        dead_lines.add(idx)

    if not subs:
        return ir_text, False

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    for res, val in subs.items():
        text = re.sub(r"%" + re.escape(res) + r"(?![\w\.])", val, text)
    return text, True
