"""Tests for LoopInfo analysis.

Upstream reference:
- /tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/LoopInfo.cpp
"""

import shutil
import subprocess
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.loop_info import compute_loop_info


_OPT = shutil.which("opt")


def _parse(ir_text: str, name: str) -> llvm.ValueRef:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    for fn in module.functions:
        if fn.name == name:
            return fn
    raise LookupError(name)


_SINGLE_LOOP = """
define i32 @single(i32 %n) {
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


_NESTED = """
define i32 @nested(i32 %n, i32 %m) {
entry:
  br label %outer
outer:
  %i = phi i32 [0, %entry], [%i.next, %outer.latch]
  br label %inner
inner:
  %j = phi i32 [0, %outer], [%j.next, %inner]
  %j.next = add i32 %j, 1
  %c = icmp slt i32 %j.next, %m
  br i1 %c, label %inner, label %outer.latch
outer.latch:
  %i.next = add i32 %i, 1
  %c2 = icmp slt i32 %i.next, %n
  br i1 %c2, label %outer, label %done
done:
  ret i32 %i
}
"""


_NO_LOOPS = """
define i32 @straight(i32 %x) {
entry:
  %t = add i32 %x, 1
  ret i32 %t
}
"""


class LoopInfoTests(unittest.TestCase):
    def test_single_loop_detected(self):
        fn = _parse(_SINGLE_LOOP, "single")
        info = compute_loop_info(fn)
        self.assertEqual(len(info.top_level_loops), 1)
        loop = info.top_level_loops[0]
        self.assertEqual(loop.header, "header")
        self.assertEqual(loop.latches, ["body"])
        self.assertIn("body", loop.blocks)
        self.assertNotIn("entry", loop.blocks)
        self.assertNotIn("exit", loop.blocks)

    def test_nested_loops(self):
        fn = _parse(_NESTED, "nested")
        info = compute_loop_info(fn)
        self.assertEqual(len(info.top_level_loops), 1)
        outer = info.top_level_loops[0]
        self.assertEqual(outer.header, "outer")
        self.assertEqual(len(outer.children), 1)
        inner = outer.children[0]
        self.assertEqual(inner.header, "inner")
        self.assertEqual(inner.parent, outer)
        self.assertEqual(inner.depth(), 2)
        self.assertEqual(outer.depth(), 1)

    def test_no_loops(self):
        fn = _parse(_NO_LOOPS, "straight")
        info = compute_loop_info(fn)
        self.assertEqual(info.top_level_loops, [])

    def test_loop_for_block_returns_innermost(self):
        fn = _parse(_NESTED, "nested")
        info = compute_loop_info(fn)
        # 'inner' block belongs to inner, not outer.
        loop = info.loop_for_block("inner")
        self.assertIsNotNone(loop)
        self.assertEqual(loop.header, "inner")

        loop = info.loop_for_block("outer.latch")
        self.assertIsNotNone(loop)
        self.assertEqual(loop.header, "outer")

    def test_exit_blocks(self):
        fn = _parse(_SINGLE_LOOP, "single")
        info = compute_loop_info(fn)
        loop = info.top_level_loops[0]
        self.assertIn("exit", loop.exit_blocks(loop_cfg := _cfg(fn)))


def _cfg(function):
    from pcc.ir_passes.dominator_tree import CFG
    return CFG.of_function(function)


@unittest.skipUnless(_OPT, "requires LLVM 'opt' on PATH")
class UpstreamParityTests(unittest.TestCase):
    def _upstream_loops(self, ir_text: str, fn: str) -> list[str]:
        proc = subprocess.run(
            [_OPT, "-passes=print<loops>", "-disable-output"],
            input=ir_text, capture_output=True, text=True,
        )
        return _parse_opt_loops(proc.stderr, fn)

    def test_single_loop_matches_upstream(self):
        fn = _parse(_SINGLE_LOOP, "single")
        ours = [l.header for l in compute_loop_info(fn).loops()]
        upstream = self._upstream_loops(_SINGLE_LOOP, "single")
        self.assertEqual(set(ours), set(upstream))

    def test_nested_loops_match_upstream(self):
        fn = _parse(_NESTED, "nested")
        ours = [l.header for l in compute_loop_info(fn).loops()]
        upstream = self._upstream_loops(_NESTED, "nested")
        self.assertEqual(set(ours), set(upstream))


def _parse_opt_loops(text: str, fn: str) -> list[str]:
    """Extract loop headers from ``opt -passes=print<loops>`` output.

    Output format looks like:

        Loop info for function 'f':
            Loop at depth 1 containing: %header<header>,%body<latch,exiting>
                Loop at depth 2 containing: %inner<header,latch,exiting>
    """
    import re
    headers: list[str] = []
    inside = False
    target = f"Loop info for function '{fn}'"
    for raw in text.splitlines():
        if target in raw:
            inside = True
            continue
        if not inside:
            continue
        if raw.startswith("Loop info for function"):
            break
        m = re.search(r"containing:\s*%([\w\.]+)", raw)
        if m:
            headers.append(m.group(1))
    return headers


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
