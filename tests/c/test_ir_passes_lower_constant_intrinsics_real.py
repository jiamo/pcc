"""Real-transform tests for LowerConstantIntrinsicsPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.lower_constant_intrinsics import (
    LowerConstantIntrinsicsPass,
    lower_constant_intrinsics_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class LowerConstantIntrinsicsTests(unittest.TestCase):
    def test_constant_operand_folds_to_true(self):
        ir = """
declare i1 @llvm.is.constant.i32(i32)

define i1 @f() {
entry:
  %r = call i1 @llvm.is.constant.i32(i32 7)
  ret i1 %r
}
"""
        out, changed = lower_constant_intrinsics_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.is.constant.i32", out)
        self.assertIn("ret i1 true", out)

    def test_ssa_operand_folds_to_false(self):
        ir = """
declare i1 @llvm.is.constant.i32(i32)

define i1 @f(i32 %x) {
entry:
  %r = call i1 @llvm.is.constant.i32(i32 %x)
  ret i1 %r
}
"""
        out, changed = lower_constant_intrinsics_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i1 false", out)

    def test_tail_ssa_operand_folds_to_false(self):
        ir = """
declare i1 @llvm.is.constant.i32(i32)

define i1 @f(i32 %x) {
entry:
  %r = tail call i1 @llvm.is.constant.i32(i32 %x)
  ret i1 %r
}
"""
        out, changed = lower_constant_intrinsics_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@llvm.is.constant.i32", out)
        self.assertIn("ret i1 false", out)

    def test_pass_integration(self):
        out, _ = run_pcc_ir_pass("""
declare i1 @llvm.is.constant.i32(i32)

define i1 @f() {
entry:
  %r = call i1 @llvm.is.constant.i32(i32 7)
  ret i1 %r
}
""", LowerConstantIntrinsicsPass())
        self.assertIn("ret i1 true", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_constant_operand_matches_upstream(self):
        report = assert_ir_parity("""
declare i1 @llvm.is.constant.i32(i32)

define i1 @f() {
entry:
  %r = call i1 @llvm.is.constant.i32(i32 7)
  ret i1 %r
}
""", LowerConstantIntrinsicsPass(), "lower-constant-intrinsics")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_ssa_operand_matches_upstream(self):
        report = assert_ir_parity("""
declare i1 @llvm.is.constant.i32(i32)

define i1 @f(i32 %x) {
entry:
  %r = call i1 @llvm.is.constant.i32(i32 %x)
  ret i1 %r
}
""", LowerConstantIntrinsicsPass(), "lower-constant-intrinsics")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_tail_ssa_operand_matches_upstream(self):
        report = assert_ir_parity("""
declare i1 @llvm.is.constant.i32(i32)

define i1 @f(i32 %x) {
entry:
  %r = tail call i1 @llvm.is.constant.i32(i32 %x)
  ret i1 %r
}
""", LowerConstantIntrinsicsPass(), "lower-constant-intrinsics")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
