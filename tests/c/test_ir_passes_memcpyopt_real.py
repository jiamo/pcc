"""Real-transform tests for MemCpyOptIRPass (subset)."""

import pytest
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.memcpyopt import MemCpyOptIRPass, memcpyopt_text
from pcc.ir_passes.manager import AnalysisManager
from pcc.ir_passes.parity import assert_ir_parity


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class MemCpyOptTests(unittest.TestCase):
    def test_drops_same_ptr_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %p, ptr %p, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_drops_same_global_memcpy(self):
        ir = """
@g = global [64 x i8] zeroinitializer
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f() {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr @g, ptr @g, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_drops_same_null_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f() {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr null, ptr null, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_drops_same_base_bitcast_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  %a = bitcast ptr %p to ptr
  %b = bitcast ptr %p to ptr
  call void @llvm.memcpy.p0.p0.i64(ptr %a, ptr %b, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_drops_same_base_zero_gep_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  %a = getelementptr i8, ptr %p, i64 0
  %b = getelementptr i8, ptr %p, i64 0
  call void @llvm.memcpy.p0.p0.i64(ptr %a, ptr %b, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_keeps_same_nonzero_gep_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  %a = getelementptr i8, ptr %p, i64 1
  %b = getelementptr i8, ptr %p, i64 1
  call void @llvm.memcpy.p0.p0.i64(ptr %a, ptr %b, i64 64, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertFalse(changed)
        self.assertIn("call void @llvm.memcpy", out)

    def test_drops_same_ptr_memmove(self):
        ir = """
declare void @llvm.memmove.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memmove.p0.p0.i64(ptr %p, ptr %p, i64 32, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertFalse(changed)
        self.assertIn("call void @llvm.memmove", out)

    def test_drops_zero_length_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %dst, ptr %src) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %dst, ptr %src, i64 0, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memcpy", out)

    def test_drops_zero_length_memmove(self):
        ir = """
declare void @llvm.memmove.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %dst, ptr %src) {
entry:
  call void @llvm.memmove.p0.p0.i64(ptr %dst, ptr %src, i64 0, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @llvm.memmove", out)

    def test_drops_zero_length_memset(self):
        ir = """
declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memset.p0.i64(ptr %p, i8 0, i64 0, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertFalse(changed)
        self.assertIn("call void @llvm.memset", out)

    def test_keeps_distinct_ptr_memcpy(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %dst, ptr %src) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %dst, ptr %src, i64 64, i1 false)
  ret void
}
"""
        _, changed = memcpyopt_text(ir)
        self.assertFalse(changed)

    def test_keeps_nonzero_memset(self):
        ir = """
declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memset.p0.i64(ptr %p, i8 0, i64 16, i1 false)
  ret void
}
"""
        _, changed = memcpyopt_text(ir)
        self.assertFalse(changed)

    def test_mixed_calls_only_noops_dropped(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
declare void @llvm.memmove.p0.p0.i64(ptr, ptr, i64, i1)
declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)
define void @f(ptr %p, ptr %q) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %p, ptr %p, i64 32, i1 false)
  call void @llvm.memcpy.p0.p0.i64(ptr %p, ptr %q, i64 32, i1 false)
  call void @llvm.memmove.p0.p0.i64(ptr %p, ptr %p, i64 32, i1 false)
  call void @llvm.memset.p0.i64(ptr %p, i8 0, i64 0, i1 false)
  call void @llvm.memset.p0.i64(ptr %p, i8 1, i64 16, i1 false)
  ret void
}
"""
        out, changed = memcpyopt_text(ir)
        self.assertTrue(changed)
        self.assertEqual(out.count("call void @llvm.memcpy"), 1)
        self.assertEqual(out.count("call void @llvm.memmove"), 1)
        self.assertEqual(out.count("call void @llvm.memset"), 2)
        self.assertIn("ptr %p, ptr %q", out)  # surviving real copy
        self.assertIn("call void @llvm.memmove.p0.p0.i64(ptr %p, ptr %p, i64 32, i1 false)", out)
        self.assertIn("i8 0, i64 0", out)     # surviving zero-length memset
        self.assertIn("i8 1, i64 16", out)    # surviving real memset

    def test_pass_class_runs_cleanly(self):
        ir = """
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %p, ptr %p, i64 64, i1 false)
  ret void
}
"""
        module = llvm.parse_assembly(ir)
        module.verify()
        pass_ = MemCpyOptIRPass()
        pass_.run(module, AnalysisManager())
        self.assertIsNotNone(pass_.rewritten_ir)
        llvm.parse_assembly(pass_.rewritten_ir).verify()
        self.assertNotIn("call void @llvm.memcpy", pass_.rewritten_ir)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_same_ptr_memcpy_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %p, ptr %p, i64 64, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_same_global_memcpy_matches_upstream_shape(self):
        report = assert_ir_parity("""
@g = global [64 x i8] zeroinitializer
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f() {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr @g, ptr @g, i64 64, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_same_null_memcpy_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f() {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr null, ptr null, i64 64, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_same_base_bitcast_memcpy_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  %a = bitcast ptr %p to ptr
  %b = bitcast ptr %p to ptr
  call void @llvm.memcpy.p0.p0.i64(ptr %a, ptr %b, i64 64, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_same_base_zero_gep_memcpy_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %p) {
entry:
  %a = getelementptr i8, ptr %p, i64 0
  %b = getelementptr i8, ptr %p, i64 0
  call void @llvm.memcpy.p0.p0.i64(ptr %a, ptr %b, i64 64, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_zero_length_memmove_matches_upstream_shape(self):
        report = assert_ir_parity("""
declare void @llvm.memmove.p0.p0.i64(ptr, ptr, i64, i1)
define void @f(ptr %dst, ptr %src) {
entry:
  call void @llvm.memmove.p0.p0.i64(ptr %dst, ptr %src, i64 0, i1 false)
  ret void
}
""", MemCpyOptIRPass(), "memcpyopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
