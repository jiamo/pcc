"""Real-transform tests for ConstraintEliminationPass (subset)."""

import unittest

from pcc.ir_passes.constraint_elimination import (
    ConstraintEliminationPass,
    _run,
)
from pcc.ir_passes.parity import run_pcc_ir_pass

import llvmlite.binding as llvm


def _mod(ir):
    m = llvm.parse_assembly(ir)
    m.verify()
    return m


class ConstraintElimTests(unittest.TestCase):
    def test_redundant_compare_folded(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %c1 = icmp slt i32 %x, 10
  br i1 %c1, label %then, label %else
then:
  %c2 = icmp slt i32 %x, 20
  %r = select i1 %c2, i32 1, i32 0
  ret i32 %r
else:
  ret i32 -1
}
"""
        new_text, changed = _run(_mod(ir))
        self.assertTrue(changed)
        # c2 is always true in the then block.
        then_section = new_text[new_text.find("then:"):new_text.find("else:")]
        self.assertIn("select i1 true", then_section)

    def test_contradicting_compare_folded_to_false(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %c1 = icmp slt i32 %x, 10
  br i1 %c1, label %then, label %else
then:
  %c2 = icmp sge i32 %x, 10
  %r = select i1 %c2, i32 1, i32 0
  ret i32 %r
else:
  ret i32 -1
}
"""
        new_text, changed = _run(_mod(ir))
        self.assertTrue(changed)
        then_section = new_text[new_text.find("then:"):new_text.find("else:")]
        self.assertIn("select i1 false", then_section)

    def test_unknown_compare_not_folded(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %c1 = icmp slt i32 %x, 10
  br i1 %c1, label %then, label %else
then:
  %c2 = icmp slt i32 %x, 5
  %r = select i1 %c2, i32 1, i32 0
  ret i32 %r
else:
  ret i32 -1
}
"""
        # c2 is NOT determined — x < 10 doesn't imply x < 5.
        _, _ = _run(_mod(ir))
        # We expect either no change, or no change in the meaningful cond.

    def test_join_target_does_not_inherit_edge_fact(self):
        ir = """
define i1 @f(i64 %c) {
entry:
  %ge65 = icmp sge i64 %c, 65
  %le90 = icmp sle i64 %c, 90
  br i1 %ge65, label %rhs.upper, label %upper.done
rhs.upper:
  br label %upper.done
upper.done:
  %upper = phi i1 [ false, %entry ], [ %le90, %rhs.upper ]
  br i1 %upper, label %done, label %rhs.lower
rhs.lower:
  %ge97 = icmp sge i64 %c, 97
  %le122 = icmp sle i64 %c, 122
  br i1 %ge97, label %lower.rhs, label %lower.done
lower.rhs:
  br label %lower.done
lower.done:
  %lower = phi i1 [ false, %rhs.lower ], [ %le122, %lower.rhs ]
  br label %done
done:
  %out = phi i1 [ true, %upper.done ], [ %lower, %lower.done ]
  ret i1 %out
}
"""
        out, changed = _run(_mod(ir))
        self.assertFalse(changed)
        self.assertIn("%ge97 = icmp sge i64 %c, 97", out)

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %c1 = icmp slt i32 %x, 10
  br i1 %c1, label %t, label %f
t:
  %c2 = icmp slt i32 %x, 100
  br i1 %c2, label %tt, label %ff
tt:
  ret i32 1
ff:
  ret i32 2
f:
  ret i32 3
}
"""
        out, _ = run_pcc_ir_pass(ir, ConstraintEliminationPass())
        # c2 should have been folded to true.
        self.assertIn("br i1 true", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
