"""Tests for DominatorTree / PostDominatorTree analyses.

Upstream reference:
- /tmp/llvm-src/llvm-20.1.8.src/lib/IR/Dominators.cpp
"""

import shutil
import subprocess
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes import AnalysisManager
from pcc.ir_passes.dominator_tree import (
    CFG,
    DominatorTreeResult,
    PostDominatorTreeResult,
    compute_dominator_tree,
    compute_post_dominator_tree,
    register_dominator_analyses,
)


_OPT = shutil.which("opt")


def _parse_fn(ir_text: str, name: str = "f") -> llvm.ValueRef:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    for fn in module.functions:
        if fn.name == name:
            return fn
    raise LookupError(f"no function {name!r} in module")


_DIAMOND = """
define i32 @f(i32 %c) {
entry:
  %t = icmp ne i32 %c, 0
  br i1 %t, label %then, label %else
then:
  br label %join
else:
  br label %join
join:
  %r = phi i32 [1, %then], [0, %else]
  ret i32 %r
}
"""


_LOOP = """
define i32 @g(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [0, %entry], [%i.next, %body]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""


_CHAIN = """
define void @h() {
a:
  br label %b
b:
  br label %c
c:
  ret void
}
"""


class CFGTests(unittest.TestCase):
    def test_successors_and_predecessors(self):
        fn = _parse_fn(_DIAMOND)
        cfg = CFG.of_function(fn)
        self.assertEqual(cfg.entry, "entry")
        self.assertEqual(set(cfg.successors["entry"]), {"then", "else"})
        self.assertEqual(set(cfg.predecessors["join"]), {"then", "else"})
        self.assertEqual(cfg.exit_blocks, ("join",))


class DominatorTreeTests(unittest.TestCase):
    def test_diamond_has_single_dominator_root(self):
        fn = _parse_fn(_DIAMOND)
        tree = compute_dominator_tree(fn)
        # entry dominates everything; then/else/join's idom is entry.
        self.assertIsNone(tree.idom("entry"))
        self.assertEqual(tree.idom("then"), "entry")
        self.assertEqual(tree.idom("else"), "entry")
        self.assertEqual(tree.idom("join"), "entry")

    def test_diamond_dominators_set(self):
        fn = _parse_fn(_DIAMOND)
        tree = compute_dominator_tree(fn)
        self.assertEqual(tree.dominators("join"), ["join", "entry"])

    def test_diamond_dominates_relation(self):
        fn = _parse_fn(_DIAMOND)
        tree = compute_dominator_tree(fn)
        self.assertTrue(tree.dominates("entry", "join"))
        self.assertTrue(tree.dominates("entry", "then"))
        self.assertFalse(tree.dominates("then", "join"))
        # Reflexive:
        self.assertTrue(tree.dominates("join", "join"))

    def test_loop_header_dominates_body_and_exit(self):
        fn = _parse_fn(_LOOP, name="g")
        tree = compute_dominator_tree(fn)
        self.assertEqual(tree.idom("header"), "entry")
        self.assertEqual(tree.idom("body"), "header")
        self.assertEqual(tree.idom("exit"), "header")
        self.assertTrue(tree.dominates("header", "body"))
        self.assertTrue(tree.dominates("header", "exit"))

    def test_chain_idom_is_predecessor(self):
        fn = _parse_fn(_CHAIN, name="h")
        tree = compute_dominator_tree(fn)
        self.assertEqual(tree.idom("b"), "a")
        self.assertEqual(tree.idom("c"), "b")


class PostDominatorTreeTests(unittest.TestCase):
    def test_diamond_join_postdominates_all(self):
        fn = _parse_fn(_DIAMOND)
        tree = compute_post_dominator_tree(fn)
        # In post-dom: join post-dominates then/else/entry.
        self.assertTrue(tree.dominates("join", "then"))
        self.assertTrue(tree.dominates("join", "else"))
        self.assertTrue(tree.dominates("join", "entry"))
        # join's own idom should be None (real root after removing virt exit).
        self.assertIsNone(tree.idom("join"))

    def test_loop_exit_postdominates_header(self):
        fn = _parse_fn(_LOOP, name="g")
        tree = compute_post_dominator_tree(fn)
        # exit post-dominates everything since there's only one return.
        self.assertTrue(tree.dominates("exit", "header"))
        self.assertTrue(tree.dominates("exit", "entry"))


class AnalysisManagerTests(unittest.TestCase):
    def test_register_and_retrieve_both_trees(self):
        fn = _parse_fn(_DIAMOND)
        am = AnalysisManager()
        register_dominator_analyses(am)

        dom = am.get(DominatorTreeResult.KEY, fn).tree
        post = am.get(PostDominatorTreeResult.KEY, fn).tree

        self.assertEqual(dom.idom("then"), "entry")
        self.assertTrue(post.dominates("join", "then"))


@unittest.skipUnless(_OPT, "requires LLVM 'opt' on PATH")
class UpstreamParityTests(unittest.TestCase):
    """Diff our idom map against ``opt -passes=print<domtree>``.

    The upstream textual format is:

        DominatorTree for function: f
        ...
          [1] %entry { ... } [0]
            [2] %then { ... } [1]
            [2] %join { ... } [1]
            [2] %else { ... } [1]
    """

    def _upstream_domtree_parents(self, ir_text: str, fn: str) -> dict[str, str | None]:
        proc = subprocess.run(
            [_OPT, "-passes=print<domtree>", "-disable-output"],
            input=ir_text, capture_output=True, text=True,
        )
        return _parse_upstream_tree(proc.stderr, fn)

    def _upstream_postdom_parents(self, ir_text: str, fn: str) -> dict[str, str | None]:
        proc = subprocess.run(
            [_OPT, "-passes=print<postdomtree>", "-disable-output"],
            input=ir_text, capture_output=True, text=True,
        )
        return _parse_upstream_tree(proc.stderr, fn, post=True)

    def test_diamond_domtree_matches_upstream(self):
        fn = _parse_fn(_DIAMOND)
        ours = compute_dominator_tree(fn)
        upstream = self._upstream_domtree_parents(_DIAMOND, "f")
        self._compare_idom(ours, upstream)

    def test_loop_domtree_matches_upstream(self):
        fn = _parse_fn(_LOOP, name="g")
        ours = compute_dominator_tree(fn)
        upstream = self._upstream_domtree_parents(_LOOP, "g")
        self._compare_idom(ours, upstream)

    def test_chain_domtree_matches_upstream(self):
        fn = _parse_fn(_CHAIN, name="h")
        ours = compute_dominator_tree(fn)
        upstream = self._upstream_domtree_parents(_CHAIN, "h")
        self._compare_idom(ours, upstream)

    def test_diamond_postdom_matches_upstream(self):
        fn = _parse_fn(_DIAMOND)
        ours = compute_post_dominator_tree(fn)
        upstream = self._upstream_postdom_parents(_DIAMOND, "f")
        self._compare_idom(ours, upstream)

    def _compare_idom(self, ours, upstream: dict[str, str | None]):
        for block, parent in upstream.items():
            self.assertEqual(
                ours.idom(block),
                parent,
                f"idom mismatch at block {block!r}: "
                f"ours={ours.idom(block)!r} upstream={parent!r}",
            )


# ---------------------------------------------------------------------------
# Upstream text parsing — works for both DomTree and PostDomTree output.
# ---------------------------------------------------------------------------


def _parse_upstream_tree(text: str, fn: str, *, post: bool = False) -> dict[str, str | None]:
    """Extract idom map from ``opt -passes=print<domtree>`` output.

    Each line looks like ``  [depth] %blockname {x,y} [children]``; a
    deeper-indented block is a child of the most recent ancestor with
    smaller depth. The synthetic ``<<exit node>>`` root in
    post-dominator output is treated as None.
    """
    lines = text.splitlines()
    idom: dict[str, str | None] = {}
    stack: list[tuple[int, str | None]] = []
    inside = False
    tag = "PostDominatorTree" if post else "DominatorTree"
    target = f"{tag} for function: {fn}"
    for raw in lines:
        if target in raw:
            inside = True
            continue
        if not inside:
            continue
        if raw.startswith(("DominatorTree", "PostDominatorTree")):
            # Another function section starts — stop.
            break
        line = raw.rstrip()
        if not line.strip():
            continue
        # Parse depth markers like `[1]`, block label `%foo` or `<<exit node>>`.
        import re
        m = re.match(r"^(\s*)\[(\d+)\]\s+(%[\w\.]+|<<exit node>>)", line)
        if not m:
            continue
        depth = int(m.group(2))
        label_raw = m.group(3)
        if label_raw == "<<exit node>>":
            label = None
        else:
            label = label_raw[1:]  # strip '%'

        # Pop stack until we find the parent at depth-1.
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if label is not None:
            # Upstream reports the virtual exit as parent for post-dom;
            # we treat that as None (matches our real-block-only view).
            idom[label] = parent
        stack.append((depth, label))
    return idom


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
