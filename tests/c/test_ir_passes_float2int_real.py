"""Real-transform tests for Float2IntIRPass (subset).

Folds bit-exact ``fptosi(sitofp(%x))`` (and ``fptoui(uitofp(%x))``)
round-trips when the floating-point type can represent every integer
value of that width exactly.
"""

import pytest
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.float2int import Float2IntIRPass, float2int_text
from pcc.ir_passes.manager import AnalysisManager
from pcc.ir_passes.parity import assert_ir_parity


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class Float2IntRoundTripTests(unittest.TestCase):
    def test_i32_via_double_folds(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %t = sitofp i32 %x to double
  %v = fptosi double %t to i32
  ret i32 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("sitofp", out)
        self.assertNotIn("fptosi", out)
        self.assertIn("sext i32 %x to i64", out)
        self.assertIn("trunc i64 %", out)

    def test_i16_via_float_folds(self):
        ir = """
define i16 @f(i16 %x) {
entry:
  %t = sitofp i16 %x to float
  %v = fptosi float %t to i16
  ret i16 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertTrue(changed)
        self.assertIn("sext i16 %x to i32", out)
        self.assertIn("trunc i32 %", out)

    def test_i8_via_float_folds(self):
        ir = """
define i8 @f(i8 %x) {
entry:
  %t = sitofp i8 %x to float
  %v = fptosi float %t to i8
  ret i8 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertTrue(changed)
        self.assertIn("sext i8 %x to i32", out)
        self.assertIn("trunc i32 %", out)

    def test_unsigned_roundtrip_folds(self):
        ir = """
define i16 @f(i16 %x) {
entry:
  %t = uitofp i16 %x to float
  %v = fptoui float %t to i16
  ret i16 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertTrue(changed)
        self.assertIn("zext i16 %x to i32", out)
        self.assertIn("trunc i32 %", out)

    def test_i32_via_float_NOT_folded(self):
        # float has only 24 mantissa bits — i32 is too wide.
        ir = """
define i32 @f(i32 %x) {
entry:
  %t = sitofp i32 %x to float
  %v = fptosi float %t to i32
  ret i32 %v
}
"""
        _, changed = float2int_text(ir)
        self.assertFalse(changed)

    def test_i64_via_double_NOT_folded(self):
        # double has only 53 mantissa bits — i64 is too wide.
        ir = """
define i64 @f(i64 %x) {
entry:
  %t = sitofp i64 %x to double
  %v = fptosi double %t to i64
  ret i64 %v
}
"""
        _, changed = float2int_text(ir)
        self.assertFalse(changed)

    def test_mixed_signed_unsigned_NOT_folded(self):
        ir = """
define i16 @f(i16 %x) {
entry:
  %t = sitofp i16 %x to float
  %v = fptoui float %t to i16
  ret i16 %v
}
"""
        _, changed = float2int_text(ir)
        self.assertFalse(changed)

    def test_bails_out_if_source_cast_has_other_users(self):
        # Upstream keeps the round-trip intact when the source sitofp
        # value has another user.
        ir = """
define i32 @f(i32 %x, ptr %p) {
entry:
  %t = sitofp i32 %x to double
  store double %t, ptr %p
  %v = fptosi double %t to i32
  ret i32 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertFalse(changed)
        self.assertIn("sitofp i32 %x to double", out)
        self.assertIn("fptosi double %t to i32", out)

    def test_constant_operand_folds(self):
        ir = """
define i32 @f() {
entry:
  %t = sitofp i32 42 to double
  %v = fptosi double %t to i32
  ret i32 %v
}
"""
        out, changed = float2int_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 42", out)

    def test_pass_class_runs_cleanly(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %t = sitofp i32 %x to double
  %v = fptosi double %t to i32
  ret i32 %v
}
"""
        module = llvm.parse_assembly(ir)
        module.verify()
        pass_ = Float2IntIRPass()
        pass_.run(module, AnalysisManager())
        self.assertIsNotNone(pass_.rewritten_ir)
        llvm.parse_assembly(pass_.rewritten_ir).verify()
        self.assertIn("sext i32 %x to i64", pass_.rewritten_ir)
        self.assertIn("trunc i64 %", pass_.rewritten_ir)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_i32_via_double_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %x) {
entry:
  %t = sitofp i32 %x to double
  %v = fptosi double %t to i32
  ret i32 %v
}
""", Float2IntIRPass(), "float2int")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_i16_unsigned_via_float_matches_upstream(self):
        report = assert_ir_parity("""
define i16 @f(i16 %x) {
entry:
  %t = uitofp i16 %x to float
  %v = fptoui float %t to i16
  ret i16 %v
}
""", Float2IntIRPass(), "float2int")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_constant_operand_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %t = sitofp i32 42 to double
  %v = fptosi double %t to i32
  ret i32 %v
}
""", Float2IntIRPass(), "float2int")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_extra_source_user_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %x, ptr %p) {
entry:
  %t = sitofp i32 %x to double
  store double %t, ptr %p
  %v = fptosi double %t to i32
  ret i32 %v
}
""", Float2IntIRPass(), "float2int")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
