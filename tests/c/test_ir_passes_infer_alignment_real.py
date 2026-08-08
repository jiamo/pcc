"""Real-transform tests for InferAlignmentPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.infer_alignment import (
    InferAlignmentPass,
    infer_alignment_text,
)
from pcc.ir_passes.parity import assert_ir_parity


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class InferAlignmentTests(unittest.TestCase):
    def test_no_alloca_no_change(self):
        ir = """
define i32 @f(ptr %p) {
entry:
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        _, changed = infer_alignment_text(ir)
        self.assertFalse(changed)

    def test_adds_alignment_from_alloca(self):
        ir = """
define i32 @f() {
entry:
  %a = alloca i32, align 4
  %v = load i32, ptr %a
  ret i32 %v
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("load i32, ptr %a, align 4", out)

    def test_promotes_weaker_alignment(self):
        ir = """
define void @f() {
entry:
  %a = alloca i64, align 8
  store i64 0, ptr %a, align 1
  ret void
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("store i64 0, ptr %a, align 8", out)
        self.assertNotIn("align 1", out)

    def test_zero_offset_gep_load_inherits_alloca_alignment(self):
        ir = """
define i32 @f() {
entry:
  %a = alloca i32, align 16
  %p = getelementptr i32, ptr %a, i64 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("load i32, ptr %p, align 16", out)

    def test_all_zero_nested_gep_store_inherits_alloca_alignment(self):
        ir = """
define void @f() {
entry:
  %a = alloca [2 x i64], align 32
  %p = getelementptr [2 x i64], ptr %a, i64 0, i64 0
  store i64 0, ptr %p, align 1
  ret void
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("store i64 0, ptr %p, align 32", out)
        self.assertNotIn("align 1", out)

    def test_bitcast_load_inherits_alloca_alignment(self):
        ir = """
define i32 @f() {
entry:
  %a = alloca i32, align 16
  %p = bitcast ptr %a to ptr
  %v = load i32, ptr %p, align 1
  ret i32 %v
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("load i32, ptr %p, align 16", out)

    def test_bitcast_store_inherits_alloca_alignment(self):
        ir = """
define void @f() {
entry:
  %a = alloca i64, align 32
  %p = bitcast ptr %a to ptr
  store i64 0, ptr %p, align 1
  ret void
}
"""
        out, changed = infer_alignment_text(ir)
        self.assertTrue(changed)
        self.assertIn("store i64 0, ptr %p, align 32", out)
        self.assertNotIn("align 1", out)

    def test_keeps_stronger_alignment(self):
        ir = """
define void @f() {
entry:
  %a = alloca i32, align 4
  store i32 0, ptr %a, align 16
  ret void
}
"""
        _, changed = infer_alignment_text(ir)
        self.assertFalse(changed)

    def test_ignores_unknown_pointer(self):
        ir = """
define i32 @f(ptr %p) {
entry:
  %a = alloca i32, align 4
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        _, changed = infer_alignment_text(ir)
        self.assertFalse(changed)

    def test_pass_class_is_noop_when_llvmlite_normalized(self):
        """llvmlite auto-normalizes load/store alignment on parse, so the
        pass is typically a no-op when invoked through the ModulePass
        interface on a post-parse module. This test documents that
        behavior and ensures the pass class itself does not crash."""
        ir = """
define i32 @f() {
entry:
  %a = alloca i32, align 4
  %v = load i32, ptr %a
  ret i32 %v
}
"""
        import llvmlite.binding as llvm
        module = llvm.parse_assembly(ir)
        module.verify()
        from pcc.ir_passes.manager import AnalysisManager

        pass_ = InferAlignmentPass()
        am = AnalysisManager()
        pass_.run(module, am)
        # llvmlite already normalized to `align 4` so the pass has nothing
        # to do: rewritten_ir remains None.
        self.assertIsNone(pass_.rewritten_ir)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_alloca_load_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %a = alloca i32, align 4
  %v = load i32, ptr %a
  ret i32 %v
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_weaker_store_alignment_matches_upstream(self):
        report = assert_ir_parity("""
define void @f() {
entry:
  %a = alloca i64, align 8
  store i64 0, ptr %a, align 1
  ret void
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_zero_offset_gep_load_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %a = alloca i32, align 16
  %p = getelementptr i32, ptr %a, i64 0
  %v = load i32, ptr %p
  ret i32 %v
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_all_zero_nested_gep_store_matches_upstream(self):
        report = assert_ir_parity("""
define void @f() {
entry:
  %a = alloca [2 x i64], align 32
  %p = getelementptr [2 x i64], ptr %a, i64 0, i64 0
  store i64 0, ptr %p, align 1
  ret void
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_bitcast_load_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %a = alloca i32, align 16
  %p = bitcast ptr %a to ptr
  %v = load i32, ptr %p, align 1
  ret i32 %v
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_bitcast_store_matches_upstream(self):
        report = assert_ir_parity("""
define void @f() {
entry:
  %a = alloca i64, align 32
  %p = bitcast ptr %a to ptr
  store i64 0, ptr %p, align 1
  ret void
}
""", InferAlignmentPass(), "infer-alignment")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
