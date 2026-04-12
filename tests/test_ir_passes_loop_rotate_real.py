"""Real-transform tests for LoopRotatePass (subset)."""

import shutil
import unittest

from pcc.ir_passes.loop_rotate import LoopRotatePass, loop_rotate_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class LoopRotateTests(unittest.TestCase):
    def test_single_step_counting_loop_rotates(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_rotate_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("\nbody:\n", out)
        self.assertIn("%i = phi i32 [ 0, %entry ], [ %inc, %header ]", out)
        self.assertIn("br i1 %c, label %header, label %exit", out)
        self.assertIn("%i.lcssa = phi i32 [ %i, %header ]", out)

    def test_multi_instruction_body_not_rotated_by_subset(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %t = add i32 %i, 2
  %inc = add i32 %t, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_rotate_module(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_side_effecting_body_not_rotated_by_subset(self):
        ir = """
@g = global i32 0

define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  store i32 %i, ptr @g
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_rotate_module(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopRotatePass())
        self.assertIn("%i.lcssa = phi i32 [ %i, %header ]", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_single_step_counting_loop_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopRotatePass(), "loop-rotate")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_sle_counting_loop_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp sle i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopRotatePass(), "loop-rotate")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_xor_body_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %inc = xor i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopRotatePass(), "loop-rotate")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
