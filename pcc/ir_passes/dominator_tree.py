"""Dominator-tree and post-dominator-tree analyses over llvmlite IR.

Upstream reference:

- ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/IR/Dominators.h``
  defines :cpp:class:`llvm::DominatorTree` and
  :cpp:class:`llvm::PostDominatorTree`, both instantiated from
  :cpp:class:`llvm::DominatorTreeBase`.
- ``/tmp/llvm-src/llvm-20.1.8.src/lib/IR/Dominators.cpp`` holds the
  implementation, which delegates to
  :cpp:class:`llvm::DomTreeBuilder` (Lengauer-Tarjan variants) in
  ``/tmp/llvm-src/llvm-20.1.8.src/include/llvm/Support/GenericDomTreeConstruction.h``.

We don't reproduce Lengauer-Tarjan here. We use the simpler iterative
algorithm from Cooper, Harvey, Kennedy ("A Simple, Fast Dominance
Algorithm", 2001), which matches what LLVM uses for IDF computation
and is correct for the CFGs we test against. The API we expose is a
subset of LLVM's DominatorTree:

- immediate dominator (``idom``),
- dominator set (``dominators``),
- "A dominates B" (``dominates``),
- tree children (``children``).

PostDominatorTree is computed by reversing the CFG and adding a
virtual exit node that every return/unreachable block points to —
this is LLVM's approach in ``DomTreeBuilder::Calculate`` when
``IsPostDom=true``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import llvmlite.binding as llvm

from .manager import AnalysisKey, AnalysisManager, AnalysisResult, PreservedAnalyses


# ---------------------------------------------------------------------------
# CFG extraction
# ---------------------------------------------------------------------------


_TERMINATOR_LABEL_RE = re.compile(r"label %([\w\.]+)")
_TERMINATOR_OPCODE_RE = re.compile(
    r"^\s*(ret|br|switch|indirectbr|invoke|unreachable|resume|catchret|catchswitch|cleanupret)\b"
)


def _block_label(block: llvm.ValueRef) -> str:
    name = block.name
    if name:
        return name
    return str(id(block))


def _iter_terminator(block: llvm.ValueRef) -> llvm.ValueRef | None:
    last = None
    for inst in block.instructions:
        last = inst
    return last


def _successors_of(block: llvm.ValueRef) -> list[str]:
    term = _iter_terminator(block)
    if term is None:
        return []
    return _TERMINATOR_LABEL_RE.findall(str(term))


def _is_exit_block(block: llvm.ValueRef) -> bool:
    """True if the block's terminator exits the function (ret/unreachable)."""
    term = _iter_terminator(block)
    if term is None:
        return True
    opcode = term.opcode
    if opcode in ("ret", "unreachable", "resume"):
        return True
    # Fall back to string prefix — llvmlite's opcode is usually
    # reliable but some versions report "" for some instructions.
    s = str(term).strip()
    if s.startswith("ret ") or s == "ret void" or s == "unreachable":
        return True
    return False


@dataclass(frozen=True)
class CFG:
    """Flat CFG view over a function: labels, entry, successors, predecessors."""

    entry: str
    blocks: tuple[str, ...]
    successors: dict[str, tuple[str, ...]]
    predecessors: dict[str, tuple[str, ...]]
    exit_blocks: tuple[str, ...]

    @classmethod
    def of_function(cls, function: llvm.ValueRef) -> "CFG":
        labels: list[str] = []
        succ: dict[str, list[str]] = {}
        pred: dict[str, list[str]] = {}
        exits: list[str] = []

        for block in function.blocks:
            label = _block_label(block)
            labels.append(label)
            succ.setdefault(label, [])
            pred.setdefault(label, [])
            if _is_exit_block(block):
                exits.append(label)

        for block in function.blocks:
            label = _block_label(block)
            for s in _successors_of(block):
                succ[label].append(s)
                pred.setdefault(s, []).append(label)

        if not labels:
            raise ValueError(
                f"function {function.name!r} has no basic blocks; "
                "is it a declaration?"
            )

        return cls(
            entry=labels[0],
            blocks=tuple(labels),
            successors={k: tuple(v) for k, v in succ.items()},
            predecessors={k: tuple(v) for k, v in pred.items()},
            exit_blocks=tuple(exits),
        )


# ---------------------------------------------------------------------------
# Reverse postorder
# ---------------------------------------------------------------------------


def _reverse_postorder(cfg: CFG, *, reverse_cfg: bool = False) -> list[str]:
    """DFS reverse postorder traversal from ``cfg.entry``.

    When ``reverse_cfg`` is true, traverse predecessors instead of
    successors (needed for post-dominator computation with a virtual
    exit).
    """
    visited: set[str] = set()
    order: list[str] = []

    # For post-dom we start from each exit block; add them in stable
    # order after visiting the entry side.
    def dfs(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        neighbors = cfg.predecessors[node] if reverse_cfg else cfg.successors[node]
        for nxt in neighbors:
            dfs(nxt)
        order.append(node)

    if reverse_cfg:
        for ex in cfg.exit_blocks or (cfg.entry,):
            dfs(ex)
    else:
        dfs(cfg.entry)

    # Unreachable nodes still need to appear for completeness.
    for label in cfg.blocks:
        if label not in visited:
            dfs(label)

    order.reverse()
    return order


# ---------------------------------------------------------------------------
# Cooper-Harvey-Kennedy dominator construction
# ---------------------------------------------------------------------------


def _compute_idom(
    cfg: CFG,
    *,
    post: bool = False,
) -> dict[str, str | None]:
    """Compute the immediate-dominator map.

    Implementation follows Algorithm 2 of Cooper, Harvey, Kennedy
    (2001). For post-dominator computation we operate on the
    reversed CFG with a synthetic entry fed by every exit block.
    """
    if post:
        predecessors = cfg.successors  # reversed
        start_nodes = cfg.exit_blocks or (cfg.entry,)
        synthetic_entry = "__pcc_virtual_exit__"
        # Build an augmented predecessors map so every exit block has
        # the synthetic node as a predecessor.
        aug_pred: dict[str, list[str]] = {
            b: list(cfg.successors[b]) for b in cfg.blocks
        }
        aug_pred[synthetic_entry] = []
        for ex in start_nodes:
            aug_pred[ex].append(synthetic_entry)
        all_nodes = [synthetic_entry] + list(cfg.blocks)
        rpo = _reverse_postorder_aug(
            all_nodes, synthetic_entry, aug_pred
        )
        predecessors = aug_pred
        entry = synthetic_entry
    else:
        predecessors = {k: list(v) for k, v in cfg.predecessors.items()}
        rpo = _reverse_postorder(cfg)
        entry = cfg.entry

    rpo_index = {node: i for i, node in enumerate(rpo)}
    idom: dict[str, str | None] = {node: None for node in rpo}
    idom[entry] = entry

    def intersect(b1: str, b2: str) -> str:
        finger1 = b1
        finger2 = b2
        while finger1 != finger2:
            while rpo_index[finger1] > rpo_index[finger2]:
                parent = idom[finger1]
                if parent is None or parent == finger1:
                    break
                finger1 = parent
            while rpo_index[finger2] > rpo_index[finger1]:
                parent = idom[finger2]
                if parent is None or parent == finger2:
                    break
                finger2 = parent
        return finger1

    changed = True
    while changed:
        changed = False
        for node in rpo:
            if node == entry:
                continue
            processed_preds = [
                p for p in predecessors.get(node, ())
                if idom.get(p) is not None
            ]
            if not processed_preds:
                continue
            new_idom = processed_preds[0]
            for p in processed_preds[1:]:
                new_idom = intersect(p, new_idom)
            if idom[node] != new_idom:
                idom[node] = new_idom
                changed = True

    # Clean up: the entry is its own idom in the algorithm; LLVM
    # convention is that the root has ``idom = None``.
    if idom.get(entry) == entry:
        idom[entry] = None
    return idom


def _reverse_postorder_aug(
    all_nodes: list[str],
    entry: str,
    predecessors: dict[str, list[str]],
) -> list[str]:
    """RPO over a node list with a custom predecessor map (reversed CFG)."""
    # Build successor graph from predecessors (forward direction for DFS).
    successors: dict[str, list[str]] = {n: [] for n in all_nodes}
    for node, preds in predecessors.items():
        for p in preds:
            successors.setdefault(p, []).append(node)

    visited: set[str] = set()
    order: list[str] = []

    def dfs(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for nxt in successors.get(node, ()):
            dfs(nxt)
        order.append(node)

    dfs(entry)
    for node in all_nodes:
        if node not in visited:
            dfs(node)
    order.reverse()
    return order


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DominatorTree:
    """Per-function immediate-dominator map.

    Interface parity with :cpp:class:`llvm::DominatorTree`:

    - ``idom(block)`` — immediate dominator (``None`` for the entry),
    - ``dominators(block)`` — the set of blocks that dominate it,
    - ``dominates(a, b)`` — does ``a`` dominate ``b`` (reflexive),
    - ``children(block)`` — blocks whose ``idom`` is ``block``.
    """

    function_name: str
    cfg: CFG
    _idom: dict[str, str | None] = field(default_factory=dict)

    def idom(self, block: str) -> str | None:
        return self._idom.get(block)

    def dominators(self, block: str) -> list[str]:
        out: list[str] = []
        current: str | None = block
        while current is not None:
            out.append(current)
            current = self._idom.get(current)
        return out

    def dominates(self, a: str, b: str) -> bool:
        current: str | None = b
        while current is not None:
            if current == a:
                return True
            current = self._idom.get(current)
        return False

    def children(self, block: str) -> list[str]:
        return sorted(
            n for n, parent in self._idom.items() if parent == block
        )

    def all_blocks(self) -> list[str]:
        return list(self._idom.keys())


class DominatorTreeResult(AnalysisResult):
    """Analysis-cache wrapper around :class:`DominatorTree`."""

    KEY = AnalysisKey("dominator-tree")

    def __init__(self, tree: DominatorTree) -> None:
        self.tree = tree

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        # DomTree is preserved iff the pass explicitly preserves it or
        # the pass preserves everything.
        return not preserved.preserves(type(self).KEY)


class PostDominatorTreeResult(AnalysisResult):
    KEY = AnalysisKey("post-dominator-tree")

    def __init__(self, tree: DominatorTree) -> None:
        self.tree = tree

    def invalidate(self, ir_unit, preserved: PreservedAnalyses) -> bool:
        return not preserved.preserves(type(self).KEY)


# ---------------------------------------------------------------------------
# Computations
# ---------------------------------------------------------------------------


def compute_dominator_tree(function: llvm.ValueRef) -> DominatorTree:
    cfg = CFG.of_function(function)
    idom = _compute_idom(cfg, post=False)
    return DominatorTree(
        function_name=function.name, cfg=cfg, _idom=idom
    )


def compute_post_dominator_tree(function: llvm.ValueRef) -> DominatorTree:
    cfg = CFG.of_function(function)
    idom = _compute_idom(cfg, post=True)
    # Drop the synthetic exit node from the result — callers work in
    # terms of real basic blocks only.
    idom.pop("__pcc_virtual_exit__", None)
    for k, v in list(idom.items()):
        if v == "__pcc_virtual_exit__":
            idom[k] = None
    return DominatorTree(
        function_name=function.name, cfg=cfg, _idom=idom
    )


def register_dominator_analyses(am: AnalysisManager) -> None:
    """Attach DomTree + PostDomTree computations to ``am`` for function units."""
    am.register(
        DominatorTreeResult.KEY,
        lambda fn: DominatorTreeResult(compute_dominator_tree(fn)),
    )
    am.register(
        PostDominatorTreeResult.KEY,
        lambda fn: PostDominatorTreeResult(compute_post_dominator_tree(fn)),
    )
