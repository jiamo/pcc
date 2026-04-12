"""Dead Store Elimination (DSE) — IR-level, block-local subset.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/DeadStoreElimination.cpp``
  implements :cpp:class:`llvm::DSEPass`. Upstream uses MemorySSA to
  walk backwards from each store, looking for a later store that
  kills the same location before any reads — if found, the earlier
  store is dead. It also handles partial overlap with the demanded-
  bits analysis, cross-block stores via dominator walks, and a
  number of noalias-library-call hints.

Staged subset implemented here (block-local; labelled ``subset``):

- within a single basic block, scan forward; record the latest
  store to each pointer,
- whenever we see a new store to the same pointer with no
  intervening load/call that may read it, the previous store is dead
  and removed,
- when a block ends in ``ret`` / ``unreachable``, drop trailing stores
  to non-escaping local ``alloca`` slots that are never read again,
- any instruction that might-alias (load/call) flushes the pending
  stores,
- ``volatile store`` acts as a barrier and is never removed.

Cross-block DSE (which requires MemorySSAWalker) is deferred; our
MemorySSA staging only models per-block clobber chains, plus one
additional narrow case: pending stores are carried across an
unconditional jump into a single-predecessor successor block, so a
later overwriting store in that linear successor can kill the earlier
store.
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
_LOAD_RE = re.compile(
    r"^\s*%[\w\.]+\s*=\s*load\b.*?,\s*ptr\s+[%@](?P<ptr>[\w\.]+)"
)
_ALLOCA_RE = re.compile(r"^\s*%(?P<ptr>[\w\.]+)\s*=\s*alloca\b")
_LABEL_RE = re.compile(r"^(?P<label>[\w\.]+):\s*$")
_BR_RE = re.compile(r"^\s*br\s+label\s+%(?P<label>[\w\.]+)\s*$")
_BITCAST_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*bitcast\s+ptr\s+[%@](?P<src>[\w\.]+)\s+to\s+ptr\b"
)
_ZERO_GEP_ALIAS_RE = re.compile(
    r"^\s*%(?P<dst>[\w\.]+)\s*=\s*getelementptr(?:\s+inbounds)?\s+[^,]+,\s+ptr\s+[%@](?P<src>[\w\.]+)\s*,\s*i\d+\s+0\s*$"
)
_SSA_TOKEN_RE = re.compile(r"%([\w\.]+)\b")


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


def dse_module_text(
    ir_text: str, aa: AliasAnalysis
) -> tuple[str, bool]:
    """Drop redundant intra-block stores."""
    lines = ir_text.splitlines(keepends=True)
    # Find basic block boundaries by the `label:` prefix or `define`.
    dead_lines: set[int] = set()
    pending: dict[str, int] = {}  # pointer name → line index of last store
    in_fn = False
    fn_start = 0
    local_allocas: set[str] = set()
    escaping_allocas: set[str] = set()
    preds_by_fn: dict[int, dict[str, int]] = {}
    carry_edges_by_fn: dict[int, dict[str, str]] = {}
    exact_aliases: dict[str, str] = {}

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
                if ptr in local_allocas and ptr not in escaping_allocas:
                    continue
                to_clear.append(ptr)
                continue
            if aa.alias_names(ptr, ptr_read) != AliasResult.NoAlias:
                to_clear.append(ptr)
        for p in to_clear:
            pending.pop(p, None)

    def collect_local_allocas(fn_lines: list[str]) -> tuple[set[str], set[str]]:
        allocas: set[str] = set()
        aliases_to_alloca: dict[str, str] = {}

        def alias_base(name: str) -> str:
            current = name
            seen: set[str] = set()
            while current in aliases_to_alloca and current not in seen:
                seen.add(current)
                nxt = aliases_to_alloca[current]
                if nxt == current:
                    break
                current = nxt
            return current

        for line in fn_lines:
            match = _ALLOCA_RE.match(line.strip())
            if match is not None:
                allocas.add(match.group("ptr"))
                aliases_to_alloca[match.group("ptr")] = match.group("ptr")
                continue
            alias = exact_alias_source(line.strip())
            if alias is None:
                continue
            dst, src = alias
            base = aliases_to_alloca.get(src)
            if base is not None:
                aliases_to_alloca[dst] = base
        escaping: set[str] = set()
        for raw_line in fn_lines:
            line = raw_line.strip()
            if not line:
                continue
            if _ALLOCA_RE.match(line):
                continue
            if exact_alias_source(line) is not None:
                continue
            names = _SSA_TOKEN_RE.findall(line)
            if not names:
                continue
            store_match = _STORE_RE.match(line)
            load_match = _LOAD_RE.match(line)
            for name in names:
                base = aliases_to_alloca.get(name)
                if base is None:
                    continue
                if store_match is not None and alias_base(store_match.group("ptr")) == base:
                    continue
                if load_match is not None and alias_base(load_match.group("ptr")) == base:
                    continue
                escaping.add(base)
        return allocas, escaping

    def collect_cfg_hints(fn_lines: list[str]) -> tuple[dict[str, int], dict[str, str]]:
        labels: list[str] = []
        preds: dict[str, int] = {}
        carry_from_pred: dict[str, str] = {}
        current_label: str | None = None
        current_insts: list[str] = []

        def finish_block() -> None:
            nonlocal current_label, current_insts
            if current_label is None:
                return
            preds.setdefault(current_label, 0)
            if current_insts:
                term = current_insts[-1]
                m = _BR_RE.match(term)
                if m is not None:
                    dst = m.group("label")
                    preds[dst] = preds.get(dst, 0) + 1
                    carry_from_pred[dst] = current_label
                elif term.startswith("br "):
                    for dst in re.findall(r"label\s+%([\w\.]+)", term):
                        preds[dst] = preds.get(dst, 0) + 1
                else:
                    for dst in re.findall(r"label\s+%([\w\.]+)", term):
                        preds[dst] = preds.get(dst, 0) + 1
            current_label = None
            current_insts = []

        for raw in fn_lines:
            stripped = raw.strip()
            m = _LABEL_RE.match(stripped)
            if m is not None:
                finish_block()
                current_label = m.group("label")
                labels.append(current_label)
                preds.setdefault(current_label, 0)
                continue
            if current_label is None:
                continue
            if stripped:
                current_insts.append(stripped)
        finish_block()
        if labels:
            preds[labels[0]] = preds.get(labels[0], 0)
        single_pred_carry = {
            label: pred
            for label, pred in carry_from_pred.items()
            if preds.get(label, 0) == 1
        }
        return preds, single_pred_carry

    def flush_dead_locals_at_block_end():
        for ptr, line_idx in list(pending.items()):
            if ptr in local_allocas and ptr not in escaping_allocas:
                dead_lines.add(line_idx)
                pending.pop(ptr, None)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("define "):
            in_fn = True
            fn_start = idx
            pending.clear()
            exact_aliases.clear()
            end_idx = idx + 1
            while end_idx < len(lines):
                if lines[end_idx].strip() == "}":
                    break
                end_idx += 1
            local_allocas, escaping_allocas = collect_local_allocas(
                lines[idx + 1 : end_idx]
            )
            preds, carry_edges = collect_cfg_hints(lines[idx + 1 : end_idx])
            preds_by_fn[fn_start] = preds
            carry_edges_by_fn[fn_start] = carry_edges
            continue
        if stripped == "}":
            in_fn = False
            pending.clear()
            exact_aliases.clear()
            local_allocas = set()
            escaping_allocas = set()
            continue
        if not in_fn:
            continue
        # New basic block — anything beginning with `<label>:` line-alone.
        label_match = _LABEL_RE.match(stripped)
        if label_match is not None:
            carry_edges = carry_edges_by_fn.get(fn_start, {})
            label = label_match.group("label")
            prev_label = None
            # Find the label of the previous block by scanning back to the
            # nearest preceding label line within the function body.
            back = idx - 1
            while back > fn_start:
                prev_match = _LABEL_RE.match(lines[back].strip())
                if prev_match is not None:
                    prev_label = prev_match.group("label")
                    break
                back -= 1
            if carry_edges.get(label) != prev_label:
                pending.clear()
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
        if stripped.startswith("atomicrmw") or stripped.startswith("fence") or stripped.startswith("cmpxchg"):
            flush_on_may_read(None)
            continue

    if not dead_lines:
        return ir_text, False

    new_lines = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    new_text = "".join(new_lines)
    new_text, _ = run_local_dce(new_text)
    return new_text, True
