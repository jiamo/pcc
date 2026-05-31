"""Dead Store Elimination (DSE) — IR-level, conservative local subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/DeadStoreElimination.cpp``
  implements :cpp:class:`llvm::DSEPass`. Upstream uses MemorySSA to
  walk backwards from each store, looking for a later store that
  kills the same location before any reads — if found, the earlier
  store is dead. It also handles partial overlap with the demanded-
  bits analysis, cross-block stores via dominator walks, and a
  number of noalias-library-call hints.

Staged subset implemented here (labelled ``subset``):

- within a single basic block, scan forward; record the latest
  store to each pointer,
- whenever we see a new store to the same pointer with no
  intervening load/call that may read it, the previous store is dead
  and removed,
- trailing stores to local stack slots (``alloca`` and exact no-op
  aliases) are removed at function exits,
- pending local stack-slot stores may cross one unconditional edge
  only when the successor has that block as its single predecessor,
- any instruction that might-alias (load/call) flushes the pending
  stores; calls are treated as full barriers,
- ``volatile store`` acts as a barrier and is never removed.

General cross-block DSE still requires a real MemorySSA/escape model.
The Python self-host pipeline constructs many short-lived container/AST
objects; the previous optimistic subset misclassified some of those
stores as dead and compiled empty lists. Keep non-local pointers and
multi-predecessor control flow deliberately conservative until the alias
model is strong enough to prove those cases.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .alias_analysis import AliasAnalysis, AliasResult
from .dce import dce_module_text as run_local_dce
from .manager import AnalysisManager, ModulePass, PreservedAnalyses

_STORE_RE = re.compile(
    r"^(?P<indent>\s*)store(?P<volatile>\s+volatile)?\b.*?,\s*ptr\s+[%@](?P<ptr>[\w\.]+)"
)
_LOAD_RE = re.compile(r"^\s*%[\w\.]+\s*=\s*load\b.*?,\s*ptr\s+[%@](?P<ptr>[\w\.]+)")
_LABEL_RE = re.compile(r"^(?P<label>[\w\.]+):(?:\s*;.*)?\s*$")
_BITCAST_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*bitcast\s+ptr\s+[%@](?P<src>[\w\.]+)\s+to\s+ptr\b"
)
_ZERO_GEP_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*getelementptr(?:\s+inbounds)?\s+[^,]+,\s+ptr\s+[%@](?P<src>[\w\.]+)\s*,\s*i\d+\s+0\s*$"
)
_ALLOCA_RE = re.compile(r"^\s*%(?P<ptr>[\w\.]+)\s*=\s*alloca\b")
_UNCOND_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<label>[\w\.]+)\s*$")
_COND_BR_RE = re.compile(
    r"^\s*br\s+i1\s+[^,]+,\s*label\s+%(?P<t>[\w\.]+),\s*label\s+%(?P<f>[\w\.]+)\s*$"
)


BlockKey = tuple[int, str]


class DSEPass(ModulePass):
    name = "pcc-dse"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        aa = AliasAnalysis(module)
        new_text, changed = dse_module_text(ir_text, aa)
        if not changed:
            return PreservedAnalyses.all()
        llvm.parse_assembly(new_text).verify()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def dse_module_text(ir_text: str, aa: AliasAnalysis) -> tuple[str, bool]:
    """Drop redundant intra-block stores."""
    lines = ir_text.splitlines(keepends=True)
    dead_lines: set[int] = set()
    pending: dict[str, int] = {}  # pointer name → line index of last store
    in_fn = False
    fn_id = -1
    current_block: BlockKey | None = None
    exact_aliases: dict[str, str] = {}
    successors, predecessors = _scan_cfg(lines)
    local_allocas = _scan_local_allocas(lines)

    def canonical_ptr(ptr: str) -> str:
        current = ptr
        seen: set[str] = set()
        while current in exact_aliases and current not in seen:
            seen.add(current)
            nxt = exact_aliases[current]
            if nxt == current:
                break
            current = nxt
        return current

    def exact_alias_source(line: str) -> tuple[str, str] | None:
        match = _BITCAST_ALIAS_RE.match(line)
        if match is not None:
            return match.group("dst"), match.group("src")
        match = _ZERO_GEP_ALIAS_RE.match(line)
        if match is not None:
            return match.group("dst"), match.group("src")
        return None

    def flush_on_may_read(ptr_read: str | None):
        """Clear pending stores that might alias with a read."""
        to_clear = []
        for ptr in pending:
            if ptr_read is None:
                to_clear.append(ptr)
                continue
            if aa.alias_names(ptr, ptr_read) != AliasResult.NoAlias:
                to_clear.append(ptr)
        for p in to_clear:
            pending.pop(p, None)

    def flush_dead_locals_at_block_end():
        for ptr, line_idx in list(pending.items()):
            if is_local_alloca(ptr):
                dead_lines.add(line_idx)
        pending.clear()

    def is_local_alloca(ptr: str) -> bool:
        return canonical_ptr(ptr) in local_allocas.get(fn_id, set())

    def keep_only_dead_local_candidates() -> None:
        for ptr in list(pending.keys()):
            if not is_local_alloca(ptr):
                pending.pop(ptr, None)

    def can_carry_to_successor(
        pred: BlockKey | None,
        succ: BlockKey,
    ) -> bool:
        if pred is None:
            return False
        return successors.get(pred) == [succ] and predecessors.get(succ) == {pred}

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("define "):
            in_fn = True
            fn_id += 1
            current_block = None
            pending.clear()
            exact_aliases.clear()
            continue
        if stripped == "}":
            in_fn = False
            current_block = None
            pending.clear()
            exact_aliases.clear()
            continue
        if not in_fn:
            continue
        # New basic block — anything beginning with `<label>:` line-alone.
        label_match = _LABEL_RE.match(stripped)
        if label_match is not None:
            next_block = (fn_id, label_match.group("label"))
            if can_carry_to_successor(current_block, next_block):
                keep_only_dead_local_candidates()
            else:
                pending.clear()
            current_block = next_block
            continue

        alias = exact_alias_source(stripped)
        if alias is not None:
            dst, src = alias
            exact_aliases[dst] = canonical_ptr(src)
            continue

        m = _STORE_RE.match(line)
        if m:
            ptr = canonical_ptr(m.group("ptr"))
            if m.group("volatile"):
                flush_on_may_read(ptr)
                continue
            prior = pending.get(ptr)
            if prior is not None:
                dead_lines.add(prior)
            # Other stores in pending that may alias need to be flushed
            # since we don't know what overlapping bytes mean here.
            for other_ptr in list(pending.keys()):
                if other_ptr == ptr:
                    continue
                if aa.alias_names(ptr, other_ptr) != AliasResult.NoAlias:
                    pending.pop(other_ptr, None)
            pending[ptr] = idx
            continue

        m = _LOAD_RE.match(line)
        if m:
            ptr = canonical_ptr(m.group("ptr"))
            flush_on_may_read(ptr)
            continue

        # Calls / other memory-touching ops — be conservative.
        if "call " in line:
            flush_on_may_read(None)
            continue
        if stripped.startswith("ret ") or stripped == "unreachable":
            flush_dead_locals_at_block_end()
            continue
        if (
            stripped.startswith("atomicrmw")
            or stripped.startswith("fence")
            or stripped.startswith("cmpxchg")
        ):
            flush_on_may_read(None)
            continue

    if not dead_lines:
        return ir_text, False

    new_lines = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    new_text = "".join(new_lines)
    new_text, _ = run_local_dce(new_text)
    return new_text, True


def _scan_local_allocas(lines: list[str]) -> dict[int, set[str]]:
    local_allocas: dict[int, set[str]] = {}
    in_fn = False
    fn_id = -1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("define "):
            in_fn = True
            fn_id += 1
            local_allocas.setdefault(fn_id, set())
            continue
        if stripped == "}":
            in_fn = False
            continue
        if not in_fn:
            continue
        match = _ALLOCA_RE.match(stripped)
        if match is not None:
            local_allocas.setdefault(fn_id, set()).add(match.group("ptr"))
    return local_allocas


def _scan_cfg(
    lines: list[str],
) -> tuple[dict[BlockKey, list[BlockKey]], dict[BlockKey, set[BlockKey]]]:
    successors: dict[BlockKey, list[BlockKey]] = {}
    predecessors: dict[BlockKey, set[BlockKey]] = {}
    in_fn = False
    fn_id = -1
    current_block: BlockKey | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("define "):
            in_fn = True
            fn_id += 1
            current_block = None
            continue
        if stripped == "}":
            in_fn = False
            current_block = None
            continue
        if not in_fn:
            continue

        label_match = _LABEL_RE.match(stripped)
        if label_match is not None:
            current_block = (fn_id, label_match.group("label"))
            successors.setdefault(current_block, [])
            predecessors.setdefault(current_block, set())
            continue
        if current_block is None:
            continue

        targets: list[BlockKey] = []
        uncond = _UNCOND_BR_RE.match(stripped)
        if uncond is not None:
            targets.append((fn_id, uncond.group("label")))
        else:
            cond = _COND_BR_RE.match(stripped)
            if cond is not None:
                targets.append((fn_id, cond.group("t")))
                targets.append((fn_id, cond.group("f")))
        if not targets:
            continue
        successors[current_block] = targets
        for target in targets:
            predecessors.setdefault(target, set()).add(current_block)

    return successors, predecessors
