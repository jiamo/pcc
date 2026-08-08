"""Natural-loop detection (LoopInfo analysis).

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/LoopInfo.cpp`` and
  ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Analysis/LoopInfo.h``
  define :cpp:class:`llvm::LoopInfo`. The upstream algorithm walks
  dominator-tree postorder looking for back-edges — an edge
  ``A -> B`` where ``B`` dominates ``A`` — and populates each natural
  loop with the blocks reachable backward from ``A`` within the loop.

We implement the same definition directly. The output of this
analysis is a set of :class:`Loop` records, each with:

- ``header``   — the loop entry (target of the back-edge),
- ``latches``  — blocks with back-edges to the header,
- ``blocks``   — every block dominated by the header that can reach
                 a latch without leaving the loop,
- ``parent``   — enclosing loop, for nested loops,
- ``children`` — directly nested subloops.

This is a subset of LLVM's interface sufficient for LICM, SCEV-free
indvar detection, loop-deletion, and the loop-unroll logic we need in
Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import llvmlite.binding as llvm

from .dominator_tree import CFG, DominatorTree, compute_dominator_tree
from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses


@dataclass
class Loop:
    """A single natural loop."""

    header: str
    latches: list[str] = field(default_factory=list)
    blocks: set[str] = field(default_factory=set)
    parent: "Loop | None" = None
    children: list["Loop"] = field(default_factory=list)

    def is_innermost(self) -> bool:
        return not self.children

    def depth(self) -> int:
        d = 1
        p = self.parent
        while p is not None:
            d += 1
            p = p.parent
        return d

    def contains(self, block: str) -> bool:
        return block in self.blocks

    def exit_blocks(self, cfg: CFG) -> list[str]:
        """Blocks outside the loop with a predecessor inside it."""
        out: list[str] = []
        for b in self.blocks:
            for s in cfg.successors[b]:
                if s not in self.blocks and s not in out:
                    out.append(s)
        return out


@dataclass
class LoopInfo:
    """Top-level analysis result: all top-level loops in a function."""

    function_name: str
    top_level_loops: list[Loop] = field(default_factory=list)
    _loop_by_header: dict[str, Loop] = field(default_factory=dict)

    def loops(self) -> list[Loop]:
        """All loops, top-level plus nested."""
        out: list[Loop] = []
        stack = list(self.top_level_loops)
        while stack:
            loop = stack.pop()
            out.append(loop)
            stack.extend(loop.children)
        return out

    def loop_for_block(self, block: str) -> Loop | None:
        """Innermost loop that contains ``block``, or None."""
        best: Loop | None = None
        for loop in self.loops():
            if loop.contains(block):
                if best is None or loop.depth() > best.depth():
                    best = loop
        return best

    def header(self, block: str) -> Loop | None:
        return self._loop_by_header.get(block)


class LoopInfoResult(AnalysisResult):
    KEY = AnalysisKey("loop-info", scope="function")

    def __init__(self, info: LoopInfo) -> None:
        self.info = info

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


def compute_loop_info(function: llvm.ValueRef) -> LoopInfo:
    """Compute LoopInfo for a function via dominator-based back-edge scan."""
    cfg = CFG.of_function(function)
    dom = compute_dominator_tree(function)
    return _build_loop_info(cfg, dom, function.name)


def _build_loop_info(cfg: CFG, dom: DominatorTree, fn_name: str) -> LoopInfo:
    # 1. Find back-edges: A → B where B dominates A. Each unique
    #    header accumulates multiple latches.
    header_to_latches: dict[str, list[str]] = {}
    for a in cfg.blocks:
        for b in cfg.successors[a]:
            if dom.dominates(b, a):
                header_to_latches.setdefault(b, []).append(a)

    if not header_to_latches:
        return LoopInfo(function_name=fn_name)

    # 2. For each header, compute the loop body via reverse reachable
    #    from any latch, staying within blocks that the header
    #    dominates. This is Tarjan's natural-loop definition.
    loops: dict[str, Loop] = {}
    for header, latches in header_to_latches.items():
        body: set[str] = {header}
        worklist = list(latches)
        while worklist:
            node = worklist.pop()
            if node in body:
                continue
            if not dom.dominates(header, node):
                # Irreducible / non-natural edge — skip.
                continue
            body.add(node)
            worklist.extend(cfg.predecessors[node])
        loops[header] = Loop(
            header=header, latches=sorted(latches), blocks=body
        )

    # 3. Establish parent/child relationships. A loop L1 is nested
    #    inside L2 iff L1's header is in L2's blocks and L1 != L2.
    headers = list(loops.keys())
    for inner_h in headers:
        inner = loops[inner_h]
        best_outer: Loop | None = None
        for outer_h in headers:
            if outer_h == inner_h:
                continue
            outer = loops[outer_h]
            if inner_h in outer.blocks:
                if best_outer is None or len(outer.blocks) < len(best_outer.blocks):
                    best_outer = outer
        if best_outer is not None:
            inner.parent = best_outer
            best_outer.children.append(inner)

    top_level = [loop for loop in loops.values() if loop.parent is None]
    # Deterministic ordering for reproducible output.
    top_level.sort(key=lambda l: l.header)
    for loop in loops.values():
        loop.children.sort(key=lambda l: l.header)

    return LoopInfo(
        function_name=fn_name,
        top_level_loops=top_level,
        _loop_by_header={h: l for h, l in loops.items()},
    )


def register_loop_info(am: AnalysisManager) -> None:
    am.register(
        LoopInfoResult.KEY,
        lambda fn: LoopInfoResult(compute_loop_info(fn)),
    )
