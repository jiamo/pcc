"""Real-transform tests for LoopInstSimplifyPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.loop_instsimplify import (
    LoopInstSimplifyPass,
    loop_instsimplify_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class LoopInstSimplifyTests(unittest.TestCase):
    def test_loop_local_identity_simplifies(self):
        ir = """
define i32 @f(i32 %n, i32 %x) {
entry:
  %pre = add i32 %x, 0
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %t = add i32 %i, 0
  %c = icmp slt i32 %t, %n
  br i1 %c, label %latch, label %exit
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %pre
}
"""
        out, changed = loop_instsimplify_text(ir)
        self.assertTrue(changed)
        self.assertIn("%pre = add i32 %x, 0", out)
        self.assertNotIn("%t = add i32 %i, 0", out)
        self.assertIn("icmp slt i32 %i, %n", out)

    def test_live_out_value_is_not_rewritten_by_subset(self):
        ir = """
define i32 @f(i32 %n, i32 %x) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %t = add i32 %x, 0
  %c = icmp slt i32 %i, %n
  br i1 %c, label %latch, label %exit
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %t
}
"""
        out, changed = loop_instsimplify_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_non_loop_function_not_changed(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 0
  ret i32 %a
}
"""
        out, changed = loop_instsimplify_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %t = add i32 %i, 0
  %c = icmp slt i32 %t, %n
  br i1 %c, label %latch, label %exit
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopInstSimplifyPass())
        self.assertIn("icmp slt i32 %i, %n", out)
        self.assertNotIn("%t = add i32 %i, 0", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_loop_local_identity_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %t = add i32 %i, 0
  %c = icmp slt i32 %t, %n
  br i1 %c, label %latch, label %exit
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopInstSimplifyPass(), "loop-instsimplify")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
