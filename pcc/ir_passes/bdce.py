"""Bit-tracking dead-code elimination (BDCE), subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/BDCE.cpp``
  implements :cpp:class:`llvm::BDCEPass` on top of the
  :cpp:class:`llvm::DemandedBits` analysis. The full pass tracks
  which bits of each SSA value are eventually consumed by a
  side-effecting instruction (return / store / call-with-arg), and
  removes any instruction whose demanded mask is zero. When an
  instruction is only partially demanded, BDCE can narrow it (e.g.
  drop nsw/nuw flags that no longer apply, or replace with a
  truncation-friendly form).

Staged subset implemented here:

- propagate a demanded mask *per SSA name* (all-bits or zero),
- an instruction whose result is not used by any still-demanded
  instruction *and* has no side effects is dead and gets removed.

This overlaps DCE (Phase 3c) but via a different cursor: BDCE starts
from effect-ful sinks and pulls demand backwards, while DCE starts
from definitions and pushes liveness forwards. The eventual goal is
to extend this module to real bit-level demand; for now it's a
subset labelled as such.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses
from .ssa_utils import build_def_use_index


_SINKS = {
    "ret", "store", "call", "invoke", "br", "switch",
    "indirectbr", "atomicrmw", "cmpxchg", "fence",
}


class BDCEPass(ModulePass):
    name = "pcc-bdce"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = bdce_module_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def bdce_module_text(ir_text: str) -> tuple[str, bool]:
    from .ssa_utils import build_def_use_index_per_function
    module = llvm.parse_assembly(ir_text)
    module.verify()
    per_fn = build_def_use_index_per_function(module)

    fn_to_remove: dict[str, set[str]] = {}
    for fn_name, index in per_fn.items():
        demanded: set[str] = set()
        worklist: list[str] = []
        for rec in index.records_by_name.values():
            if rec.opcode in _SINKS:
                for op in rec.operands:
                    if op not in demanded:
                        demanded.add(op)
                        worklist.append(op)
        while worklist:
            name = worklist.pop()
            rec = index.def_of(name)
            if rec is None:
                continue
            for op in rec.operands:
                if op not in demanded:
                    demanded.add(op)
                    worklist.append(op)
        remove: set[str] = set()
        for name, rec in index.records_by_name.items():
            if name.startswith("__inst_"):
                continue
            if rec.opcode in _SINKS:
                continue
            if name not in demanded:
                remove.add(name)
        fn_to_remove[fn_name] = remove

    if not any(fn_to_remove.values()):
        return ir_text, False

    define_re = re.compile(r"^\s*define\s+[^@]*@(?P<name>[\w\.]+)")
    current_fn: str | None = None
    new_lines: list[str] = []
    assign_re = re.compile(r"^\s*%([\w\.]+)\s*=")
    for line in ir_text.splitlines(keepends=True):
        dm = define_re.match(line)
        if dm:
            current_fn = dm.group("name")
        if line.strip() == "}":
            new_lines.append(line)
            current_fn = None
            continue
        if current_fn is not None:
            m = assign_re.match(line)
            if m and m.group(1) in fn_to_remove.get(current_fn, set()):
                continue
        new_lines.append(line)
    return "".join(new_lines), True
