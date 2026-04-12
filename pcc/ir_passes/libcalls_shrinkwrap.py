"""LibCallsShrinkWrap — IR-level boundary subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Utils/SimplifyLibCalls.cpp``

The original pcc subset here rewrote a handful of direct libcalls
(``memset``/``memcmp``/``strcmp``/``strlen``) into constants or direct
arguments. Focused upstream ``opt -passes=libcalls-shrinkwrap`` probes
showed that those standalone direct-call folds are *not* the behavior
of this pass. Keeping those rewrites here would move the IR away from
the canonical LLVM pipeline instead of toward it.

Current subset model:

- Honest IR boundary / no-op for the standalone focused shapes we
  exercise today.
- The class remains as a real IR pass so the registry surface stays on
  the IR path, but it preserves all analyses and leaves the module
  unchanged until a narrower proven-upstream transform is added.
"""

from __future__ import annotations

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class LibcallsShrinkwrapPass(ModulePass):
    name = "pcc-libcalls-shrinkwrap"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        return PreservedAnalyses.all()


def libcalls_shrinkwrap_text(ir_text: str) -> tuple[str, bool]:
    return ir_text, False
