"""MemorySSA (staged subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Analysis/MemorySSA.h``
  and ``.../lib/Analysis/MemorySSA.cpp`` define
  :cpp:class:`llvm::MemorySSA`. Upstream builds a virtual SSA for
  memory by assigning a :cpp:class:`MemoryDef` to every store/call
  that may write, a :cpp:class:`MemoryUse` to every load/call that
  may read, and :cpp:class:`MemoryPhi` at join points. The whole
  structure is walked via :cpp:class:`llvm::MemorySSAWalker` which
  can answer "what is the clobbering definition of this use?"

Staging policy for pcc (labelled ``migration-scaffold`` in the
status taxonomy):

- we recognize stores, loads, and calls as memory events,
- we assign a unique id to each event in program order per function,
- we build the simple "last write in the same BB" use→def edge,
- we emit MemoryPhi nodes at the header of every block with multiple
  predecessors,
- we do **not** yet walk through phi-merged clobbers (that is what
  ``MemorySSAWalker`` does); a later pass (Phase 5) will add the
  clobber walker.

This subset is already useful for per-block DSE (Phase 4a) and for
a limited GVN-subset on loads with a unique dominating store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import llvmlite.binding as llvm

from .alias_analysis import AliasAnalysis, AliasResult
from .dominator_tree import CFG, compute_dominator_tree
from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses


@dataclass
class MemoryAccess:
    """One node in the memory SSA form."""

    kind: str           # "def" | "use" | "phi" | "liveOnEntry"
    id: int
    block: str
    pointer: str | None = None     # target pointer name if known
    instruction_text: str = ""
    clobber_id: int | None = None  # dominating def observed from this access
    phi_incoming: dict[str, int] = field(default_factory=dict)


@dataclass
class MemorySSAForm:
    """Per-function memory SSA — a flat list of accesses plus an index."""

    function_name: str
    accesses: list[MemoryAccess] = field(default_factory=list)
    by_id: dict[int, MemoryAccess] = field(default_factory=dict)
    per_block: dict[str, list[MemoryAccess]] = field(default_factory=dict)

    def live_on_entry(self) -> MemoryAccess:
        return self.accesses[0]

    def access_for_instruction(self, inst_text: str) -> MemoryAccess | None:
        for a in self.accesses:
            if a.instruction_text == inst_text.strip():
                return a
        return None


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


_STORE_RE = re.compile(r"^\s*store\b.*?,\s*(?:ptr|.*?\*)\s+%([\w\.]+)")
_LOAD_RE = re.compile(r"^\s*%[\w\.]+\s*=\s*load\b.*?,\s*(?:ptr|.*?\*)\s+%([\w\.]+)")
_CALL_RE = re.compile(r"^\s*(?:%[\w\.]+\s*=\s*)?call\b")


def _classify_inst(text: str) -> tuple[str, str | None]:
    text = text.strip()
    m = _STORE_RE.match(text)
    if m:
        return "def", m.group(1)
    m = _LOAD_RE.match(text)
    if m:
        return "use", m.group(1)
    if _CALL_RE.match(text):
        # Treat every call as a may-def+may-use until we have MemAttr.
        return "def", None
    return "other", None


def build_memory_ssa(
    function: llvm.ValueRef,
    aa: AliasAnalysis | None = None,
) -> MemorySSAForm:
    cfg = CFG.of_function(function)
    form = MemorySSAForm(function_name=function.name)
    counter = 0

    # Slot 0: liveOnEntry sentinel.
    entry_access = MemoryAccess(kind="liveOnEntry", id=counter, block="")
    form.accesses.append(entry_access)
    form.by_id[counter] = entry_access
    counter += 1

    # Phase 1: Phi placeholders at every block with >1 predecessor.
    phi_for_block: dict[str, MemoryAccess] = {}
    for block_name in cfg.blocks:
        preds = cfg.predecessors[block_name]
        if len(preds) > 1:
            phi = MemoryAccess(kind="phi", id=counter, block=block_name)
            phi_for_block[block_name] = phi
            form.accesses.append(phi)
            form.by_id[counter] = phi
            counter += 1

    # Phase 2: walk each block in program order, emit def/use records.
    last_in_block: dict[str, int] = {}
    for block in function.blocks:
        block_name = block.name or str(id(block))
        current = (
            phi_for_block.get(block_name, entry_access).id
        )
        for inst in block.instructions:
            text = str(inst).strip()
            kind, pointer = _classify_inst(text)
            if kind == "other":
                continue
            acc = MemoryAccess(
                kind=kind,
                id=counter,
                block=block_name,
                pointer=pointer,
                instruction_text=text,
                clobber_id=current,
            )
            form.accesses.append(acc)
            form.by_id[counter] = acc
            form.per_block.setdefault(block_name, []).append(acc)
            if kind == "def":
                current = counter
            counter += 1
        last_in_block[block_name] = current

    # Phase 3: resolve phi incoming edges.
    for block_name, phi in phi_for_block.items():
        for pred in cfg.predecessors[block_name]:
            phi.phi_incoming[pred] = last_in_block.get(pred, entry_access.id)
        form.per_block.setdefault(block_name, []).insert(0, phi)

    return form


class MemorySSAResult(AnalysisResult):
    KEY = AnalysisKey("memory-ssa")

    def __init__(self, form: MemorySSAForm) -> None:
        self.form = form

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


def register_memory_ssa(am: AnalysisManager) -> None:
    am.register(
        MemorySSAResult.KEY,
        lambda fn: MemorySSAResult(build_memory_ssa(fn)),
    )
