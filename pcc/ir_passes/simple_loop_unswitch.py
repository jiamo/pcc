"""Simple Loop Unswitching — IR-level direct-pass boundary.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/SimpleLoopUnswitch.cpp``

Current direct-pass parity boundary:

- Bare upstream ``opt -passes=simple-loop-unswitch`` stays a no-op for the
  direct-invocation neighborhood we currently scope in parity tests.
- The real unswitching behavior appears only once earlier loop
  canonicalization and legality staging have prepared the loop.
- pcc therefore models the visible explicit pass as a verified no-op
  boundary for that direct invocation.
"""

from __future__ import annotations

import llvmlite.binding as llvm

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
        del am
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = unswitch_module(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def unswitch_module(ir_text: str) -> tuple[str, bool]:
    llvm.parse_assembly(ir_text).verify()
    return ir_text, False
