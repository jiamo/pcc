"""Real-transform tests for LoopSinkPass (subset)."""

import re
import shutil
import unittest

from pcc.ir_passes.loop_sink import LoopSinkPass, loop_sink_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


def _block_text(ir: str, block: str) -> str:
    m = re.search(
        rf"^{re.escape(block)}:\s*(?:;[^\n]*)?\n(?P<body>.*?)(?=^[\w\.\$]+:\s*(?:;[^\n]*)?\n|\Z)",
        ir,
        re.MULTILINE | re.DOTALL,
    )
    if m is None:
        raise AssertionError(f"block {block!r} missing from IR:\n{ir}")
    return m.group("body")


class LoopSinkTests(unittest.TestCase):
    def test_guarded_add_sinks_into_single_pred_then_block(self):
        ir = """
define i32 @f(i32 %x, i32 %n, i1 %cond) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %sum = phi i32 [ 0, %entry ], [ %sum.next, %latch ]
  %t = add i32 %x, 1
  br i1 %cond, label %then, label %else
then:
  %sum.then = add i32 %sum, %t
  br label %latch
else:
  br label %latch
latch:
  %sum.next = phi i32 [ %sum.then, %then ], [ %sum, %else ]
  %inc = add i32 %i, 1
  %more = icmp slt i32 %inc, %n
  br i1 %more, label %loop, label %exit
exit:
  ret i32 %sum.next
}
"""
        out, changed = loop_sink_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%t = add i32 %x, 1", _block_text(out, "loop"))
        then_text = _block_text(out, "then")
        self.assertIn("%t = add i32 %x, 1", then_text)
        self.assertLess(
            then_text.index("%t = add i32 %x, 1"),
            then_text.index("%sum.then = add i32 %sum, %t"),
        )

    def test_multi_pred_sink_target_is_skipped(self):
        ir = """
define i32 @f(i32 %x, i32 %n, i1 %cond) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %sum = phi i32 [ 0, %entry ], [ %sum.next, %latch ]
  %t = add i32 %x, 1
  br i1 %cond, label %then, label %else
then:
  %sum.then = add i32 %sum, %t
  br label %latch
else:
  br label %then
latch:
  %sum.next = phi i32 [ %sum.then, %then ]
  %inc = add i32 %i, 1
  %more = icmp slt i32 %inc, %n
  br i1 %more, label %loop, label %exit
exit:
  ret i32 %sum.next
}
"""
        out, changed = loop_sink_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_same_block_dependency_is_skipped(self):
        ir = """
define i32 @f(i32 %x, i32 %n, i1 %cond) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %sum = phi i32 [ 0, %entry ], [ %sum.next, %latch ]
  %a = add i32 %x, 1
  %t = mul i32 %a, 2
  br i1 %cond, label %then, label %else
then:
  %sum.then = add i32 %sum, %t
  br label %latch
else:
  br label %latch
latch:
  %sum.next = phi i32 [ %sum.then, %then ], [ %sum, %else ]
  %inc = add i32 %i, 1
  %more = icmp slt i32 %inc, %n
  br i1 %more, label %loop, label %exit
exit:
  ret i32 %sum.next
}
"""
        out, changed = loop_sink_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        out, _ = run_pcc_ir_pass("""
define i32 @f(i32 %x, i32 %n, i1 %cond) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %sum = phi i32 [ 0, %entry ], [ %sum.next, %latch ]
  %t = add i32 %x, 1
  br i1 %cond, label %then, label %else
then:
  %sum.then = add i32 %sum, %t
  br label %latch
else:
  br label %latch
latch:
  %sum.next = phi i32 [ %sum.then, %then ], [ %sum, %else ]
  %inc = add i32 %i, 1
  %more = icmp slt i32 %inc, %n
  br i1 %more, label %loop, label %exit
exit:
  ret i32 %sum.next
}
""", LoopSinkPass())
        self.assertIn("%t = add i32 %x, 1", _block_text(out, "then"))


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_guarded_add_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %x, i32 %n, i1 %cond) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %sum = phi i32 [ 0, %entry ], [ %sum.next, %latch ]
  %t = add i32 %x, 1
  br i1 %cond, label %then, label %else
then:
  %sum.then = add i32 %sum, %t
  br label %latch
else:
  br label %latch
latch:
  %sum.next = phi i32 [ %sum.then, %then ], [ %sum, %else ]
  %inc = add i32 %i, 1
  %more = icmp slt i32 %inc, %n
  br i1 %more, label %loop, label %exit
exit:
  ret i32 %sum.next
}
""", LoopSinkPass(), "loop-sink")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
