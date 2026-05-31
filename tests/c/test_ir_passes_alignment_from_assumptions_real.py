"""Real-transform tests for AlignmentFromAssumptionsIRPass (subset)."""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.alignment_from_assumptions import (
    AlignmentFromAssumptionsIRPass,
    alignment_from_assumptions_text,
)
from pcc.ir_passes.manager import AnalysisManager


class AlignmentFromAssumptionsTests(unittest.TestCase):
    def test_promotes_load_alignment_from_assume(self):
        ir = """
declare void @llvm.assume(i1)
define i32 @f(ptr %p) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 15
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  %v = load i32, ptr %p, align 4
  ret i32 %v
}
"""
        out, changed = alignment_from_assumptions_text(ir)
        self.assertTrue(changed)
        self.assertIn("load i32, ptr %p, align 16", out)
        self.assertNotIn("align 4", out)

    def test_promotes_store_alignment_from_assume(self):
        ir = """
declare void @llvm.assume(i1)
define void @f(ptr %p, i32 %v) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 7
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  store i32 %v, ptr %p, align 1
  ret void
}
"""
        out, changed = alignment_from_assumptions_text(ir)
        self.assertTrue(changed)
        self.assertIn("store i32 %v, ptr %p, align 8", out)

    def test_keeps_stronger_existing_alignment(self):
        ir = """
declare void @llvm.assume(i1)
define i32 @f(ptr %p) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 3
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  %v = load i32, ptr %p, align 16
  ret i32 %v
}
"""
        _, changed = alignment_from_assumptions_text(ir)
        self.assertFalse(changed)

    def test_ignores_non_power_of_two_minus_one_mask(self):
        # Mask 5 (binary 101) is NOT of the form 2^k - 1 so it does
        # not encode an alignment fact — we must not promote.
        ir = """
declare void @llvm.assume(i1)
define i32 @f(ptr %p) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 5
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  %v = load i32, ptr %p, align 4
  ret i32 %v
}
"""
        _, changed = alignment_from_assumptions_text(ir)
        self.assertFalse(changed)

    def test_unrelated_pointer_not_promoted(self):
        ir = """
declare void @llvm.assume(i1)
define i32 @f(ptr %p, ptr %q) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 15
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  %v = load i32, ptr %q, align 4
  ret i32 %v
}
"""
        out, changed = alignment_from_assumptions_text(ir)
        self.assertFalse(changed)
        self.assertIn("load i32, ptr %q, align 4", out)

    def test_no_assume_no_change(self):
        ir = """
define i32 @f(ptr %p) {
entry:
  %v = load i32, ptr %p, align 4
  ret i32 %v
}
"""
        _, changed = alignment_from_assumptions_text(ir)
        self.assertFalse(changed)

    def test_inserts_missing_align(self):
        # llvmlite adds a natural alignment on parse, so the pre-pass
        # text is guaranteed to already have an align suffix. Test
        # the case where no explicit alignment is present in the
        # text-level input.
        ir = (
            "declare void @llvm.assume(i1)\n"
            "define i32 @f(ptr %p) {\n"
            "entry:\n"
            "  %pi = ptrtoint ptr %p to i64\n"
            "  %m = and i64 %pi, 31\n"
            "  %c = icmp eq i64 %m, 0\n"
            "  call void @llvm.assume(i1 %c)\n"
            "  %v = load i32, ptr %p\n"
            "  ret i32 %v\n"
            "}\n"
        )
        out, changed = alignment_from_assumptions_text(ir)
        self.assertTrue(changed)
        self.assertIn("align 32", out)

    def test_pass_class_runs_cleanly(self):
        ir = """
declare void @llvm.assume(i1)
define i32 @f(ptr %p) {
entry:
  %pi = ptrtoint ptr %p to i64
  %m = and i64 %pi, 15
  %c = icmp eq i64 %m, 0
  call void @llvm.assume(i1 %c)
  %v = load i32, ptr %p, align 4
  ret i32 %v
}
"""
        module = llvm.parse_assembly(ir)
        module.verify()
        pass_ = AlignmentFromAssumptionsIRPass()
        pass_.run(module, AnalysisManager())
        self.assertIsNotNone(pass_.rewritten_ir)
        llvm.parse_assembly(pass_.rewritten_ir).verify()
        self.assertIn("align 16", pass_.rewritten_ir)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
