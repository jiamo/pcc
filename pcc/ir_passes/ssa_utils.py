"""SSA utilities: def-use and use-def indexing over llvmlite IR.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/Value.h`` — every
  SSA ``Value`` owns a linked list of ``Use`` records; ``Use`` points
  back to the user ``User``. This gives O(1) def→uses enumeration in
  upstream.
- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/User.h`` — a User
  holds the list of operand ``Use``s it consumes.

llvmlite's ``ValueRef`` doesn't expose the Use list directly from
Python, so we build our own def-use index by walking every
instruction, parsing its operands textually, and populating:

- :attr:`DefUseIndex.def_of`       — name → defining instruction text,
- :attr:`DefUseIndex.uses_of`      — name → list of users (inst text),
- :attr:`DefUseIndex.operands_of`  — inst name → operands it reads,
- :attr:`DefUseIndex.block_of`     — inst name → defining block.

This is enough for SCCP/ADCE/DCE/GVN-subset passes that need to walk
use-def chains and invalidate dead values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import llvmlite.binding as llvm

from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses


_VALUE_REF_RE = re.compile(r"%([\w\.]+)")
_ASSIGN_RE = re.compile(r"^\s*%([\w\.]+)\s*=")
_OPCODE_RE = re.compile(r"^\s*(?:%[\w\.]+\s*=\s*)?(\w+)")


@dataclass
class InstructionRecord:
    """A normalized view of a single instruction."""

    name: str | None          # %foo if the instruction defines a value
    opcode: str
    text: str                  # full instruction text
    block: str                 # containing basic block label
    function: str              # containing function name
    operands: list[str] = field(default_factory=list)
    is_terminator: bool = False
    is_side_effecting: bool = False


_SIDE_EFFECTING = {
    "store", "call", "invoke", "ret", "br", "switch",
    "fence", "atomicrmw", "cmpxchg", "unreachable",
}
_TERMINATORS = {
    "ret", "br", "switch", "indirectbr", "invoke",
    "unreachable", "resume", "catchswitch", "catchret", "cleanupret",
}


@dataclass
class DefUseIndex:
    """Per-module def-use map."""

    records_by_name: dict[str, InstructionRecord] = field(default_factory=dict)
    records_by_block: dict[tuple[str, str], list[InstructionRecord]] = \
        field(default_factory=dict)
    uses_of: dict[str, list[str]] = field(default_factory=dict)

    def def_of(self, name: str) -> InstructionRecord | None:
        return self.records_by_name.get(name)

    def operands_of(self, name: str) -> list[str]:
        rec = self.records_by_name.get(name)
        return list(rec.operands) if rec else []

    def users_of(self, name: str) -> list[InstructionRecord]:
        return [
            self.records_by_name[user]
            for user in self.uses_of.get(name, ())
            if user in self.records_by_name
        ]

    def instructions_in(self, function: str, block: str) -> list[InstructionRecord]:
        return list(self.records_by_block.get((function, block), ()))


def build_def_use_index(module: llvm.ModuleRef) -> DefUseIndex:
    """Build a def-use index for every defined function in the module.

    **Note**: SSA names are function-local; this module-level index
    collapses same-name values across different functions, which is
    only correct when callers scope their queries by function. Prefer
    :func:`build_def_use_index_per_function` when cross-function
    isolation matters.
    """
    index = DefUseIndex()
    for fn in module.functions:
        if fn.is_declaration:
            continue
        _scan_function(fn, index)
    _dedupe_uses(index)
    return index


def build_def_use_index_per_function(
    module: llvm.ModuleRef,
) -> dict[str, DefUseIndex]:
    """Return one :class:`DefUseIndex` per defined function.

    Two functions that define the same SSA name (e.g. both have
    ``%x``) keep those entries isolated, which is what most passes
    actually want.
    """
    out: dict[str, DefUseIndex] = {}
    for fn in module.functions:
        if fn.is_declaration:
            continue
        idx = DefUseIndex()
        _scan_function(fn, idx)
        _dedupe_uses(idx)
        out[fn.name] = idx
    return out


def _dedupe_uses(index: DefUseIndex) -> None:
    for name, users in index.uses_of.items():
        seen: set[str] = set()
        deduped: list[str] = []
        for u in users:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        index.uses_of[name] = deduped


def _scan_function(fn: llvm.ValueRef, index: DefUseIndex) -> None:
    fn_name = fn.name
    for block in fn.blocks:
        block_name = block.name or str(id(block))
        block_records: list[InstructionRecord] = []
        for inst in block.instructions:
            text = str(inst).strip()
            name = None
            m = _ASSIGN_RE.match(text)
            if m:
                name = m.group(1)
            opcode = inst.opcode or ""
            if not opcode:
                om = _OPCODE_RE.match(text)
                if om:
                    opcode = om.group(1)
            operands = _parse_operands(text, self_name=name)
            rec = InstructionRecord(
                name=name,
                opcode=opcode,
                text=text,
                block=block_name,
                function=fn_name,
                operands=operands,
                is_terminator=opcode in _TERMINATORS,
                is_side_effecting=opcode in _SIDE_EFFECTING,
            )
            block_records.append(rec)
            if name is not None:
                index.records_by_name[name] = rec
            for op in operands:
                index.uses_of.setdefault(op, [])
                if name is not None:
                    index.uses_of[op].append(name)
                else:
                    # Side-effecting instruction with no result; use a
                    # synthetic id so we don't lose the edge.
                    synthetic = f"__inst_{id(rec)}__"
                    index.records_by_name[synthetic] = rec
                    rec.name = synthetic
                    index.uses_of[op].append(synthetic)
        index.records_by_block[(fn_name, block_name)] = block_records


def _parse_operands(text: str, *, self_name: str | None) -> list[str]:
    """Return value-operand names referenced by an instruction."""
    operands: list[str] = []
    for m in _VALUE_REF_RE.finditer(text):
        name = m.group(1)
        # Skip the defined name at the start of the instruction.
        if self_name is not None and name == self_name:
            # The first occurrence is the LHS; skip it but keep
            # subsequent self-references (rare in well-formed SSA).
            if operands.count(name) == 0 and text.startswith(f"%{name}"):
                continue
        operands.append(name)
    return operands


class DefUseResult(AnalysisResult):
    KEY = AnalysisKey("def-use-index")

    def __init__(self, index: DefUseIndex) -> None:
        self.index = index

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


def register_def_use_index(am: AnalysisManager) -> None:
    am.register(
        DefUseResult.KEY,
        lambda module: DefUseResult(build_def_use_index(module)),
    )
