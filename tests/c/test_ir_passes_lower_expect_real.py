"""Real-transform tests for LowerExpectPass (subset)."""

import unittest

from pcc.dependency_verdict import probe_executable_dependency
from pcc.ir_passes.lower_expect import LowerExpectPass, lower_expect_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT_VERDICT = probe_executable_dependency("opt")


class LowerExpectTests(unittest.TestCase):
    def test_expect_i1_call_is_removed(self):
        ir = """
declare i1 @llvm.expect.i1(i1, i1)

define i32 @f(i1 %cond) {
entry:
  %exp = call i1 @llvm.expect.i1(i1 %cond, i1 true)
  br i1 %exp, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.i1", out)
        self.assertNotIn("%exp = call", out)
        self.assertIn("br i1 %cond, label %then, label %else", out)

    def test_expect_with_probability_call_is_removed(self):
        ir = """
declare i1 @llvm.expect.with.probability.i1(i1, i1, double)

define i1 @f(i1 %cond) {
entry:
  %exp = call i1 @llvm.expect.with.probability.i1(i1 %cond, i1 false, double 9.000000e-01)
  ret i1 %exp
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.with.probability.i1", out)
        self.assertIn("ret i1 %cond", out)

    def test_tail_expect_i32_call_is_removed(self):
        ir = """
declare i32 @llvm.expect.i32(i32, i32)

define i32 @f(i32 %x) {
entry:
  %exp = tail call i32 @llvm.expect.i32(i32 %x, i32 7)
  ret i32 %exp
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.i32", out)
        self.assertIn("ret i32 %x", out)

    def test_tail_expect_with_probability_call_is_removed(self):
        ir = """
declare i1 @llvm.expect.with.probability.i1(i1, i1, double)

define i1 @f(i1 %cond) {
entry:
  %exp = tail call i1 @llvm.expect.with.probability.i1(i1 %cond, i1 false, double 9.000000e-01)
  ret i1 %exp
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.with.probability.i1", out)
        self.assertIn("ret i1 %cond", out)

    def test_unused_expect_call_is_removed(self):
        ir = """
declare i1 @llvm.expect.i1(i1, i1)

define void @f(i1 %cond) {
entry:
  call i1 @llvm.expect.i1(i1 %cond, i1 true)
  ret void
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.i1", out)
        self.assertIn("ret void", out)

    def test_unused_tail_expect_with_probability_call_is_removed(self):
        ir = """
declare i1 @llvm.expect.with.probability.i1(i1, i1, double)

define void @f(i1 %cond) {
entry:
  tail call i1 @llvm.expect.with.probability.i1(i1 %cond, i1 false, double 9.000000e-01)
  ret void
}
"""
        out, changed = lower_expect_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.expect.with.probability.i1", out)
        self.assertIn("ret void", out)

    def test_non_expect_call_is_untouched(self):
        ir = """
declare i1 @helper(i1)

define i1 @f(i1 %cond) {
entry:
  %v = call i1 @helper(i1 %cond)
  ret i1 %v
}
"""
        out, changed = lower_expect_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        out, _ = run_pcc_ir_pass("""
declare i1 @llvm.expect.i1(i1, i1)

define i32 @f(i1 %cond) {
entry:
  %exp = call i1 @llvm.expect.i1(i1 %cond, i1 true)
  br i1 %exp, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}
""", LowerExpectPass())
        self.assertIn("br i1 %cond, label %then, label %else", out)


@unittest.skipUnless(_OPT_VERDICT.available, _OPT_VERDICT.skip_reason())
class UpstreamParityTests(unittest.TestCase):
    def test_expect_branch_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare i1 @llvm.expect.i1(i1, i1)

define i32 @f(i1 %cond) {
entry:
  %exp = call i1 @llvm.expect.i1(i1 %cond, i1 true)
  br i1 %exp, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}
""", LowerExpectPass(), "lower-expect")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_tail_expect_i32_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare i32 @llvm.expect.i32(i32, i32)

define i32 @f(i32 %x) {
entry:
  %exp = tail call i32 @llvm.expect.i32(i32 %x, i32 7)
  ret i32 %exp
}
""", LowerExpectPass(), "lower-expect")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_tail_expect_with_probability_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare i1 @llvm.expect.with.probability.i1(i1, i1, double)

define i1 @f(i1 %cond) {
entry:
  %exp = tail call i1 @llvm.expect.with.probability.i1(i1 %cond, i1 true, double 8.000000e-01)
  ret i1 %exp
}
""", LowerExpectPass(), "lower-expect")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_unused_expect_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare i1 @llvm.expect.i1(i1, i1)

define void @f(i1 %cond) {
entry:
  call i1 @llvm.expect.i1(i1 %cond, i1 true)
  ret void
}
""", LowerExpectPass(), "lower-expect")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_unused_tail_expect_with_probability_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare i1 @llvm.expect.with.probability.i1(i1, i1, double)

define void @f(i1 %cond) {
entry:
  tail call i1 @llvm.expect.with.probability.i1(i1 %cond, i1 true, double 8.000000e-01)
  ret void
}
""", LowerExpectPass(), "lower-expect")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
