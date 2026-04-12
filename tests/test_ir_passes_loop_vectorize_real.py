"""Real-transform tests for LoopVectorizePass (subset)."""

import unittest

from pcc.ir_passes.loop_vectorize import LoopVectorizePass, vectorize_module
from pcc.ir_passes.parity import run_pcc_ir_pass


class LoopVectorizeTests(unittest.TestCase):
    def test_simple_array_add_vectorized(self):
        ir = """
define void @f(ptr %a, ptr %b, ptr %c) {
entry:
  br label %body
body:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %pa = getelementptr i32, ptr %a, i32 %i
  %pb = getelementptr i32, ptr %b, i32 %i
  %pc = getelementptr i32, ptr %c, i32 %i
  %va = load i32, ptr %pa
  %vb = load i32, ptr %pb
  %vc = add i32 %va, %vb
  store i32 %vc, ptr %pc
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, 4
  br i1 %cond, label %body, label %exit
exit:
  ret void
}
"""
        out, changed = vectorize_module(ir)
        self.assertTrue(changed)
        self.assertIn("<4 x i32>", out)
        self.assertIn("add <4 x i32>", out)
        # Scalar loop body gone.
        self.assertNotIn("body:", out)

    def test_non_vectorizable_aliasing_not_fired(self):
        # Same buffer on load + store → aliasing, shouldn't vectorize.
        ir = """
define void @f(ptr %a, ptr %b) {
entry:
  br label %body
body:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %pa = getelementptr i32, ptr %a, i32 %i
  %pb = getelementptr i32, ptr %b, i32 %i
  %va = load i32, ptr %pa
  %vb = load i32, ptr %pb
  %vc = add i32 %va, %vb
  store i32 %vc, ptr %pa
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, 4
  br i1 %cond, label %body, label %exit
exit:
  ret void
}
"""
        # Only 2 distinct bases (a and b), not 3 — shape check fails.
        _, changed = vectorize_module(ir)
        self.assertFalse(changed)

    def test_non_matching_trip_count_not_fired(self):
        ir = """
define void @f(ptr %a, ptr %b, ptr %c, i32 %n) {
entry:
  br label %body
body:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %pa = getelementptr i32, ptr %a, i32 %i
  %pb = getelementptr i32, ptr %b, i32 %i
  %pc = getelementptr i32, ptr %c, i32 %i
  %va = load i32, ptr %pa
  %vb = load i32, ptr %pb
  %vc = add i32 %va, %vb
  store i32 %vc, ptr %pc
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %body, label %exit
exit:
  ret void
}
"""
        _, changed = vectorize_module(ir)
        self.assertFalse(changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
