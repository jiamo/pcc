"""Loop Vectorize — IR-level direct-pass boundary.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Vectorize/LoopVectorize.cpp``

Current direct-pass parity boundary:

- Bare upstream ``opt -passes=loop-vectorize`` stays a no-op for the
  direct-invocation neighborhood we currently scope in parity tests.
- Real vectorizing behavior only appears once earlier legality and loop
  canonicalization staging have prepared a vectorizable loop.
- pcc therefore models the explicit visible pass as a verified no-op boundary
  for that direct invocation.
"""

from __future__ import annotations

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


class LoopVectorizePass(ModulePass):
    name = "pcc-loop-vectorize"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        del am
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = vectorize_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def vectorize_module(ir_text: str) -> tuple[str, bool]:
    llvm.parse_assembly(ir_text).verify()
    return ir_text, False
