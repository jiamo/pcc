"""Phase 7 inter-procedural passes — migration-scaffold batch.

Each pass corresponds to an upstream LLVM IPO pass. Full
implementation requires call-graph analysis (CGSCC pass manager),
which we stage in as part of Phase 7 task #69+.

Upstream source anchors are on each class.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


from .inline import InlinePass, AlwaysInlinePass  # noqa: E402,F401


from .globalopt import GlobalOptPass  # noqa: E402,F401


class GlobalDCEPass(ModulePass):
    """Global Dead-Code Elimination (subset — unused internal globals).

    Upstream reference:
    - /tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/IPO/GlobalDCE.cpp

    Subset: remove global variables with ``private`` / ``internal``
    linkage that have no users anywhere in the module. Full upstream
    also removes dead functions and COMDATs; deferred.
    """

    name = "pcc-globaldce"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(self, module, am):
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = _globaldce_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            # Malformed rewrite — safer to bail out than emit bad IR.
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


_GLOBAL_DECL_RE = re.compile(
    r"^(?P<name>@[\w\.]+)\s*=\s*(?:dso_local\s+|local_unnamed_addr\s+)*"
    r"(?P<linkage>private|internal)\s+.*$"
)


def _globaldce_text(ir_text: str) -> tuple[str, bool]:
    lines = ir_text.splitlines(keepends=True)
    # Map global name -> line index.
    candidates: dict[str, int] = {}
    for idx, line in enumerate(lines):
        m = _GLOBAL_DECL_RE.match(line.rstrip("\n"))
        if m:
            candidates[m.group("name")] = idx
    if not candidates:
        return ir_text, False

    # A global is dead if it's not referenced anywhere outside its decl.
    text_without_decls = "".join(
        ln for i, ln in enumerate(lines) if i not in set(candidates.values())
    )
    dead = []
    for name in candidates:
        if re.search(re.escape(name) + r"\b", text_without_decls):
            continue
        dead.append(name)

    if not dead:
        return ir_text, False

    dead_idx = {candidates[n] for n in dead}
    kept = [ln for i, ln in enumerate(lines) if i not in dead_idx]
    return "".join(kept), True


from .argpromotion import ArgPromotionPass  # noqa: E402,F401


from .arg_opt import DeadArgElimPass  # noqa: E402,F401


from .ipsccp import IPSCCPPass  # noqa: E402,F401


from .function_attrs import FunctionAttrsPass  # noqa: E402,F401


from .called_value_prop import CalledValuePropagationPass  # noqa: E402,F401


from .callsite_splitting import CallSiteSplittingPass  # noqa: E402,F401


from .elim_avail_extern import ElimAvailExternPass  # noqa: E402,F401
