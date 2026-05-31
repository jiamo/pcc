"""Real-transform tests for LoopSimplifyPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.loop_simplify import LoopSimplifyPass, loop_simplify_module
from pcc.ir_passes.parity import assert_ir_parity


_OPT = shutil.which("opt")


class LoopSimplifyTests(unittest.TestCase):
    def test_canonical_loop_no_change(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        _, changed = loop_simplify_module(ir)
        self.assertFalse(changed)

    def test_multiple_external_preds_gets_preheader(self):
        ir = """
define i32 @f(i1 %c, i32 %n) {
entry:
  br i1 %c, label %prehead1, label %prehead2
prehead1:
  br label %header
prehead2:
  br label %header
header:
  %i = phi i32 [ 0, %prehead1 ], [ 5, %prehead2 ], [ %i.next, %latch ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_simplify_module(ir)
        self.assertTrue(changed)
        # prehead1/prehead2 should now br to the new preheader, not header.
        self.assertIn(".preheader", out)

    def test_multiple_external_preds_same_value_avoids_extra_preheader_phi(self):
        ir = """
define i32 @f(i1 %c, i32 %n) {
entry:
  br i1 %c, label %prehead1, label %prehead2
prehead1:
  br label %header
prehead2:
  br label %header
header:
  %i = phi i32 [ 0, %prehead1 ], [ 0, %prehead2 ], [ %i.next, %latch ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_simplify_module(ir)
        self.assertTrue(changed)
        self.assertIn("header.preheader", out)
        self.assertNotIn("%i.ph = phi i32 [ 0, %prehead1 ], [ 0, %prehead2 ]", out)

    def test_multiple_latches_gets_dedicated_latch(self):
        ir = """
define i32 @f(i1 %c, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next.a, %latch.a ], [ %i.next.b, %latch.b ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %body, label %exit
body:
  br i1 %c, label %latch.a, label %latch.b
latch.a:
  %i.next.a = add i32 %i, 1
  br label %header
latch.b:
  %i.next.b = add i32 %i, 2
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_simplify_module(ir)
        self.assertTrue(changed)
        self.assertIn(".backedge", out)
        self.assertIn("br label %header.backedge", out)
        self.assertIn("%i.ph = phi i32", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_multiple_external_preds_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i32 %n) {
entry:
  br i1 %c, label %prehead1, label %prehead2
prehead1:
  br label %header
prehead2:
  br label %header
header:
  %i = phi i32 [ 0, %prehead1 ], [ 5, %prehead2 ], [ %i.next, %latch ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopSimplifyPass(), "loop-simplify")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_same_value_external_preds_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i32 %n) {
entry:
  br i1 %c, label %prehead1, label %prehead2
prehead1:
  br label %header
prehead2:
  br label %header
header:
  %i = phi i32 [ 0, %prehead1 ], [ 0, %prehead2 ], [ %i.next, %latch ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopSimplifyPass(), "loop-simplify")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_multiple_latches_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next.a, %latch.a ], [ %i.next.b, %latch.b ]
  %cc = icmp slt i32 %i, %n
  br i1 %cc, label %body, label %exit
body:
  br i1 %c, label %latch.a, label %latch.b
latch.a:
  %i.next.a = add i32 %i, 1
  br label %header
latch.b:
  %i.next.b = add i32 %i, 2
  br label %header
exit:
  ret i32 %i
}
""", LoopSimplifyPass(), "loop-simplify")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
