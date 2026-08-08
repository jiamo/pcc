"""Real-transform tests for TailCallElimPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass
from pcc.ir_passes.tailcallelim import TailCallElimPass, tailcallelim_text


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class TailCallElimTests(unittest.TestCase):
    def test_single_arg_tail_recursion_becomes_loop(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret i32 0
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1)
  ret i32 %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertIn("%n.tr = phi i32 [ %n, %entry ], [ %n1, %rec ]", out)
        self.assertNotIn("call i32 @f", out)

    def test_two_arg_tail_recursion_becomes_loop(self):
        ir = """
define i32 @f(i32 %n, i32 %acc) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret i32 %acc
rec:
  %n1 = sub i32 %n, 1
  %acc1 = add i32 %acc, %n
  %r = call i32 @f(i32 %n1, i32 %acc1)
  ret i32 %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("%acc.tr = phi i32 [ %acc, %entry ], [ %acc1, %rec ]", out)
        self.assertIn("ret i32 %acc.tr", out)

    def test_recursive_true_edge_also_becomes_loop(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  %c = icmp ne i32 %n, 0
  br i1 %c, label %rec, label %base
base:
  ret i32 0
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1)
  ret i32 %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertIn("%n.tr = phi i32 [ %n, %entry ], [ %n1, %rec ]", out)
        self.assertNotIn("call i32 @f", out)

    def test_zero_arg_tail_recursion_becomes_loop(self):
        ir = """
define i32 @f() {
entry:
  %c = icmp eq i1 false, true
  br i1 %c, label %base, label %rec
base:
  ret i32 0
rec:
  %r = call i32 @f()
  ret i32 %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertNotIn(" = phi ", out)
        self.assertNotIn("call i32 @f", out)

    def test_void_tail_recursion_becomes_loop(self):
        ir = """
define void @f(i32 %n) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret void
rec:
  %n1 = sub i32 %n, 1
  call void @f(i32 %n1)
  ret void
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertIn("%n.tr = phi i32 [ %n, %entry ], [ %n1, %rec ]", out)
        self.assertNotIn("call void @f", out)

    def test_pointer_arg_tail_recursion_becomes_loop(self):
        ir = """
define i32 @f(i32 %n, ptr %p) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  %v = load i32, ptr %p
  ret i32 %v
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1, ptr %p)
  ret i32 %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertIn("%n.tr = phi i32 [ %n, %entry ], [ %n1, %rec ]", out)
        self.assertNotIn("%p.tr = phi", out)
        self.assertNotIn("call i32 @f", out)

    def test_pointer_return_tail_recursion_becomes_loop(self):
        ir = """
define ptr @f(i32 %n, ptr %p) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret ptr %p
rec:
  %n1 = sub i32 %n, 1
  %r = call ptr @f(i32 %n1, ptr %p)
  ret ptr %r
}
"""
        out, changed = tailcallelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("tailrecurse:", out)
        self.assertIn("%n.tr = phi i32 [ %n, %entry ], [ %n1, %rec ]", out)
        self.assertNotIn("%p.tr = phi", out)
        self.assertNotIn("call ptr @f", out)

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret i32 0
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1)
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, TailCallElimPass())
        self.assertIn("tailrecurse:", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_single_arg_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret i32 0
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1)
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_two_arg_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n, i32 %acc) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret i32 %acc
rec:
  %n1 = sub i32 %n, 1
  %acc1 = add i32 %acc, %n
  %r = call i32 @f(i32 %n1, i32 %acc1)
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_recursive_true_edge_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  %c = icmp ne i32 %n, 0
  br i1 %c, label %rec, label %base
base:
  ret i32 0
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1)
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_zero_arg_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %c = icmp eq i1 false, true
  br i1 %c, label %base, label %rec
base:
  ret i32 0
rec:
  %r = call i32 @f()
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_void_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define void @f(i32 %n) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret void
rec:
  %n1 = sub i32 %n, 1
  call void @f(i32 %n1)
  ret void
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_one_block_zero_arg_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f() {
entry:
  %r = call i32 @f()
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_pointer_arg_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n, ptr %p) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  %v = load i32, ptr %p
  ret i32 %v
rec:
  %n1 = sub i32 %n, 1
  %r = call i32 @f(i32 %n1, ptr %p)
  ret i32 %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_pointer_return_tail_recursion_matches_upstream(self):
        report = assert_ir_parity("""
define ptr @f(i32 %n, ptr %p) {
entry:
  %c = icmp eq i32 %n, 0
  br i1 %c, label %base, label %rec
base:
  ret ptr %p
rec:
  %n1 = sub i32 %n, 1
  %r = call ptr @f(i32 %n1, ptr %p)
  ret ptr %r
}
""", TailCallElimPass(), "tailcallelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
