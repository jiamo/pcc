"""Real-transform tests for SpeculativeExecutionIRPass (subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.speculative_execution import (
    SpeculativeExecutionIRPass,
    speculative_execution_text,
)
from pcc.ir_passes.manager import AnalysisManager
from pcc.ir_passes.parity import assert_ir_parity


_OPT = shutil.which("opt")


class SpeculativeExecutionTests(unittest.TestCase):
    def test_hoists_add_into_predecessor(self):
        ir = """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = add i32 %x, 1
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        entry_chunk = out.split("entry:", 1)[1].split("t:", 1)[0]
        self.assertIn("%v = add", entry_chunk)
        # Successor block should now only hold the terminator.
        t_chunk = out.split("t:", 1)[1].split("f:", 1)[0]
        self.assertNotIn("%v = add", t_chunk)
        self.assertIn("br label %join", t_chunk)

    def test_no_change_when_operand_is_defined_in_successor(self):
        ir = """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %w = add i32 %x, 1
  %v = add i32 %w, 2
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        _, changed = speculative_execution_text(ir)
        self.assertFalse(changed)

    def test_no_change_when_result_used_outside_join(self):
        ir = """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = add i32 %x, 1
  br label %leak
f:
  br label %leak
leak:
  %u = add i32 %v, %v
  ret i32 %u
}
"""
        _, changed = speculative_execution_text(ir)
        self.assertFalse(changed)

    def test_hoists_icmp(self):
        ir = """
define i1 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = icmp slt i32 %x, 5
  br label %join
f:
  br label %join
join:
  %r = phi i1 [ %v, %t ], [ false, %f ]
  ret i1 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = icmp slt", out.split("entry:", 1)[1].split("t:", 1)[0])

    def test_hoists_select(self):
        ir = """
define i32 @f(i1 %c, i1 %p, i32 %x, i32 %y) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = select i1 %p, i32 %x, i32 %y
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = select", out.split("entry:", 1)[1].split("t:", 1)[0])

    def test_hoists_when_operand_is_defined_in_predecessor(self):
        ir = """
define i32 @f(i1 %c, i32 %x) {
entry:
  %base = add i32 %x, 2
  br i1 %c, label %t, label %f
t:
  %v = add i32 %base, 1
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        entry_chunk = out.split("entry:", 1)[1].split("t:", 1)[0]
        self.assertIn("%base = add i32 %x, 2", entry_chunk)
        self.assertIn("%v = add i32 %base, 1", entry_chunk)
        t_chunk = out.split("t:", 1)[1].split("f:", 1)[0]
        self.assertNotIn("%v = add", t_chunk)

    def test_hoists_zext(self):
        ir = """
define i32 @f(i1 %c, i1 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = zext i1 %x to i32
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = zext i1 %x to i32", out.split("entry:", 1)[1].split("t:", 1)[0])

    def test_hoists_trunc(self):
        ir = """
define i8 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = trunc i32 %x to i8
  br label %join
f:
  br label %join
join:
  %r = phi i8 [ %v, %t ], [ 0, %f ]
  ret i8 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = trunc i32 %x to i8", out.split("entry:", 1)[1].split("t:", 1)[0])

    def test_hoists_sext(self):
        ir = """
define i32 @f(i1 %c, i8 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = sext i8 %x to i32
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        out, changed = speculative_execution_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = sext i8 %x to i32", out.split("entry:", 1)[1].split("t:", 1)[0])

    def test_no_change_for_plain_loop(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %header ]
  %c = icmp slt i32 %i, %n
  %i.next = add i32 %i, 1
  br i1 %c, label %header, label %exit
exit:
  ret i32 %i
}
"""
        _, changed = speculative_execution_text(ir)
        self.assertFalse(changed)

    def test_pass_class_runs_cleanly(self):
        ir = """
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %f
t:
  %v = add i32 %x, 1
  br label %join
f:
  br label %join
join:
  %r = phi i32 [ %v, %t ], [ 0, %f ]
  ret i32 %r
}
"""
        module = llvm.parse_assembly(ir)
        module.verify()
        pass_ = SpeculativeExecutionIRPass()
        pass_.run(module, AnalysisManager())
        self.assertIsNotNone(pass_.rewritten_ir)
        self.assertIn("%v = add", pass_.rewritten_ir)
        # Verify the rewritten IR still parses.
        llvm.parse_assembly(pass_.rewritten_ir).verify()


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_simple_diamond_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %merge
then:
  %t = add i32 %x, 1
  br label %merge
merge:
  %r = phi i32 [ %t, %then ], [ %x, %entry ]
  ret i32 %r
}
""", SpeculativeExecutionIRPass(), "speculative-execution")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_predecessor_defined_operand_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i32 %x) {
entry:
  %base = add i32 %x, 2
  br i1 %c, label %then, label %merge
then:
  %t = add i32 %base, 1
  br label %merge
merge:
  %r = phi i32 [ %t, %then ], [ 0, %entry ]
  ret i32 %r
}
""", SpeculativeExecutionIRPass(), "speculative-execution")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_zext_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i1 %x) {
entry:
  br i1 %c, label %then, label %merge
then:
  %t = zext i1 %x to i32
  br label %merge
merge:
  %r = phi i32 [ %t, %then ], [ 0, %entry ]
  ret i32 %r
}
""", SpeculativeExecutionIRPass(), "speculative-execution")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_trunc_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i8 @f(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %merge
then:
  %t = trunc i32 %x to i8
  br label %merge
merge:
  %r = phi i8 [ %t, %then ], [ 0, %entry ]
  ret i8 %r
}
""", SpeculativeExecutionIRPass(), "speculative-execution")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_sext_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @f(i1 %c, i8 %x) {
entry:
  br i1 %c, label %then, label %merge
then:
  %t = sext i8 %x to i32
  br label %merge
merge:
  %r = phi i32 [ %t, %then ], [ 0, %entry ]
  ret i32 %r
}
""", SpeculativeExecutionIRPass(), "speculative-execution")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
