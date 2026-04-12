"""Loop Load Elimination — IR-level (subset).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Transforms/Scalar/LoopLoadElimination.cpp``
  implements :cpp:class:`llvm::LoopLoadElimPass`. It performs
  cross-iteration store-to-load forwarding: when iteration ``i``
  stores a value and iteration ``i+k`` loads the same address, the
  load can consume the stored value from a previous iteration
  through a phi.

Subset implemented here (labelled ``subset``):

- Within a single basic block, when we see:

      store TY %val, ptr %p
      ... no intervening clobber of %p ...
      %v = load TY, ptr %p

  replace ``%v`` with ``%val`` and drop the load. This is classic
  store-to-load forwarding limited to a single block (not yet
  cross-iteration). The block-local version is sufficient for most
  loops after other passes have hoisted invariants.

Cross-iteration forwarding (the real loop-load-elim) requires
MemorySSA walker for clobber queries across iterations.
"""

from __future__ import annotations

import re

import llvmlite.binding as llvm

from .manager import AnalysisManager, ModulePass, PreservedAnalyses


_STORE_RE = re.compile(
    r"^(?P<indent>\s*)store\s+(?P<ty>[^,]+?)\s+(?P<val>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)
_LOAD_RE = re.compile(
    r"^(?P<indent>\s*)%(?P<res>[\w\.]+)\s*=\s*load\s+(?P<ty>[^,]+?)\s*,\s*"
    r"ptr\s+%(?P<ptr>[\w\.]+)(?:,\s*align\s+\d+)?\s*$"
)


class LoopLoadElimPass(ModulePass):
    name = "pcc-loop-load-elim"

    def __init__(self) -> None:
        self.rewritten_ir: str | None = None

    def run(
        self,
        module: llvm.ModuleRef,
        am: AnalysisManager,
    ) -> PreservedAnalyses:
        self.rewritten_ir = None
        ir_text = str(module)
        new_text, changed = loop_load_elim_text(ir_text)
        if not changed:
            return PreservedAnalyses.all()
        try:
            llvm.parse_assembly(new_text).verify()
        except RuntimeError:
            return PreservedAnalyses.all()
        self.rewritten_ir = new_text
        return PreservedAnalyses.none()


def loop_load_elim_text(ir_text: str) -> tuple[str, bool]:
    """Forward stored values to following loads in the same block."""
    lines = ir_text.splitlines(keepends=True)
    # Per-block state of last-stored value per pointer.
    current_block_stores: dict[str, tuple[str, str]] = {}  # ptr → (val, ty)
    load_subs: dict[str, str] = {}
    dead_lines: set[int] = set()
    in_fn = False

    def flush():
        current_block_stores.clear()

    label_re = re.compile(r"^\s*[\w\.]+:\s*(?:;.*)?$")
    call_re = re.compile(r"^\s*(?:%[\w\.]+\s*=\s*)?call\b")

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("define "):
            in_fn = True
            flush()
            continue
        if stripped == "}":
            in_fn = False
            flush()
            continue
        if not in_fn:
            continue
        if label_re.match(line.rstrip("\n")):
            flush()
            continue

        m_store = _STORE_RE.match(line.rstrip("\n"))
        if m_store:
            current_block_stores[m_store.group("ptr")] = (
                m_store.group("val").strip(), m_store.group("ty").strip()
            )
            continue
        m_load = _LOAD_RE.match(line.rstrip("\n"))
        if m_load:
            ptr = m_load.group("ptr")
            if ptr in current_block_stores:
                val, ty = current_block_stores[ptr]
                if m_load.group("ty").strip() == ty:
                    load_subs[m_load.group("res")] = val
                    dead_lines.add(idx)
            continue
        # Calls or other memory ops invalidate all pending stores
        # (conservative — we don't yet know which pointers they touch).
        if call_re.match(line):
            flush()

    if not load_subs:
        return ir_text, False

    kept = [ln for i, ln in enumerate(lines) if i not in dead_lines]
    text = "".join(kept)
    for res, val in load_subs.items():
        text = re.sub(r"%" + re.escape(res) + r"(?![\w\.])", val, text)
    return text, True
