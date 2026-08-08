"""Real-transform tests for LoopUnrollPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.loop_unroll import LoopUnrollPass
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class LoopUnrollTests(unittest.TestCase):
    def test_trip_count_one_loop_loses_backedge(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 1
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
        """
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        latch_section = out[out.find("latch:"):]
        self.assertNotIn("br label %header", latch_section)

    def test_non_trivial_trip_count_not_unrolled(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        # Trip count is dynamic (%n); should stay a loop.
        latch_section = out[out.find("latch:"):out.find("exit:")]
        self.assertIn("br label %header", latch_section)

    def test_unrelated_constant_icmp_does_not_imply_trip_one(self):
        ir = """
define i32 @f(i32 %n, i64 %x) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %flag = icmp ne i64 %x, 0
  br i1 %flag, label %then, label %latch
then:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        latch_section = out[out.find("latch:"):out.find("exit:")]
        self.assertIn("br label %header", latch_section)

    def test_single_block_const_trip_three_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %s = phi i32 [ 0, %entry ], [ %s2, %loop ]
  %s2 = add i32 %s, %i
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, 3
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %s2
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertNotIn("phi i32", out)
        self.assertNotIn("br i1 %cmp", out)
        self.assertIn("ret i32 3", out)

    def test_single_block_const_trip_two_void_is_fully_unrolled(self):
        ir = """
define void @f(ptr %p) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  store i32 %i, ptr %p
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, 2
  br i1 %cmp, label %loop, label %exit
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertNotIn("phi i32", out)
        self.assertIn("store i32 0, ptr %p", out)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertNotIn("br i1 %cmp", out)

    def test_two_block_const_trip_three_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %s = phi i32 [ 0, %entry ], [ %s2, %body ]
  %cmp = icmp slt i32 %i, 3
  br i1 %cmp, label %body, label %exit
body:
  %s2 = add i32 %s, %i
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %s
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertNotIn("phi i32 [ 0, %entry ], [ %inc, %body ]", out)
        self.assertIn("body.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("%s.lcssa = phi i32 [ 3, %body.2 ]", out)
        self.assertIn("ret i32 %s.lcssa", out)

    def test_two_block_const_trip_two_void_is_fully_unrolled(self):
        ir = """
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %cmp = icmp slt i32 %i, 2
  br i1 %cmp, label %body, label %exit
body:
  store i32 %i, ptr %p
  %inc = add i32 %i, 1
  br label %header
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("store i32 0, ptr %p", out)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertNotIn("br i1 %cmp", out)

    def test_three_block_trip_one_header_cmp_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 1
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("%i.lcssa = phi i32 [ 1, %latch ]", out)
        self.assertIn("ret i32 %i.lcssa", out)

    def test_three_block_trip_one_latch_cmp_cleans_to_direct_return(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 1
  br i1 %c, label %header, label %exit
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("latch:", out)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("br i1 %c", out)

    def test_three_block_trip_two_header_cmp_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 2
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("%i.lcssa = phi i32 [ 2, %latch.1 ]", out)
        self.assertIn("ret i32 %i.lcssa", out)

    def test_three_block_trip_two_header_cmp_void_is_fully_unrolled(self):
        ir = """
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 2
  br i1 %c, label %body, label %exit
body:
  store i32 %i, ptr %p
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("store i32 0, ptr %p", out)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertIn("store i32 2, ptr %p", out)
        self.assertIn("ret void", out)

    def test_three_block_trip_two_latch_cmp_cleans_to_direct_return(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 2
  br i1 %c, label %header, label %exit
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("ret i32 1", out)
        self.assertNotIn("br i1 %c", out)

    def test_three_block_trip_two_latch_cmp_void_cleans_to_direct_return(self):
        ir = """
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  store i32 %i, ptr %p
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 2
  br i1 %c, label %header, label %exit
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("store i32 0, ptr %p", out)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertIn("ret void", out)
        self.assertNotIn("br i1 %c", out)

    def test_three_block_trip_two_latch_eq_cmp_return_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %body
body:
  %inc = add i32 %i, 1
  br label %latch
latch:
  %done = icmp eq i32 %inc, 2
  br i1 %done, label %exit, label %header
exit:
  ret i32 %inc
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("ret i32 2", out)
        self.assertNotIn("br i1 %done", out)

    def test_three_block_trip_three_latch_eq_cmp_void_is_fully_unrolled(self):
        ir = """
@g = internal global i32 0

define void @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %body
body:
  store i32 %i, ptr @g
  %inc = add i32 %i, 1
  br label %latch
latch:
  %done = icmp eq i32 %inc, 3
  br i1 %done, label %exit, label %header
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("store i32 0, ptr @g", out)
        self.assertIn("store i32 1, ptr @g", out)
        self.assertIn("store i32 2, ptr @g", out)
        self.assertIn("ret void", out)
        self.assertNotIn("br i1 %done", out)

    def test_two_block_latch_cmp_trip_four_void_is_fully_unrolled(self):
        ir = """
define void @f(ptr %p) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  store i32 %i, ptr %p
  br label %latch
latch:
  %inc = add i32 %i, 1
  %c = icmp slt i32 %inc, 4
  br i1 %c, label %loop, label %exit
exit:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("store i32 0, ptr %p", out)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertIn("store i32 2, ptr %p", out)
        self.assertIn("store i32 3, ptr %p", out)
        self.assertIn("latch.1:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("latch.3:", out)
        self.assertIn("ret void", out)
        self.assertNotIn("br i1 %c", out)

    def test_three_block_trip_three_header_cmp_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 3
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("body.3:", out)
        self.assertIn("latch.3:", out)
        self.assertIn("%i.lcssa = phi i32 [ 3, %latch.2 ]", out)
        self.assertIn("ret i32 %i.lcssa", out)

    def test_three_block_symbolic_const_limit_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  %limit = add i32 0, 2
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, %limit
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("%i.lcssa = phi i32 [ 2, %latch.1 ]", out)
        self.assertIn("ret i32 %i.lcssa", out)

    def test_three_block_trip_three_header_eq_cmp_is_fully_unrolled(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %done = icmp eq i32 %i, 3
  br i1 %done, label %exit, label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopUnrollPass())
        self.assertIn("body.1:", out)
        self.assertIn("latch.1:", out)
        self.assertIn("body.2:", out)
        self.assertIn("latch.2:", out)
        self.assertIn("br i1 true, label %exit, label %body.3", out)
        self.assertIn("%i.lcssa = phi i32 [ 3, %latch.2 ]", out)
        self.assertIn("ret i32 %i.lcssa", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, LoopUnrollPass(), "loop-unroll")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_single_block_const_trip_three_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %s = phi i32 [ 0, %entry ], [ %s2, %loop ]
  %s2 = add i32 %s, %i
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, 3
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %s2
}
""")

    def test_single_block_const_trip_two_void_matches_upstream(self):
        self._parity("""
define void @f(ptr %p) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  store i32 %i, ptr %p
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, 2
  br i1 %cmp, label %loop, label %exit
exit:
  ret void
}
""")

    def test_two_block_const_trip_three_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %s = phi i32 [ 0, %entry ], [ %s2, %body ]
  %cmp = icmp slt i32 %i, 3
  br i1 %cmp, label %body, label %exit
body:
  %s2 = add i32 %s, %i
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %s
}
""")

    def test_two_block_const_trip_two_void_matches_upstream(self):
        self._parity("""
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %cmp = icmp slt i32 %i, 2
  br i1 %cmp, label %body, label %exit
body:
  store i32 %i, ptr %p
  %inc = add i32 %i, 1
  br label %header
exit:
  ret void
}
""")

    def test_three_block_trip_two_header_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 2
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_three_block_trip_two_header_cmp_void_matches_upstream(self):
        self._parity("""
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 2
  br i1 %c, label %body, label %exit
body:
  store i32 %i, ptr %p
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret void
}
""")

    def test_three_block_trip_two_latch_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 2
  br i1 %c, label %header, label %exit
exit:
  ret i32 %i
}
""")

    def test_three_block_trip_two_latch_cmp_void_matches_upstream(self):
        self._parity("""
define void @f(ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  store i32 %i, ptr %p
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 2
  br i1 %c, label %header, label %exit
exit:
  ret void
}
""")

    def test_three_block_trip_two_latch_eq_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %body
body:
  %inc = add i32 %i, 1
  br label %latch
latch:
  %done = icmp eq i32 %inc, 2
  br i1 %done, label %exit, label %header
exit:
  ret i32 %inc
}
""")

    def test_three_block_trip_three_latch_eq_cmp_void_matches_upstream(self):
        self._parity("""
@g = internal global i32 0

define void @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %body
body:
  store i32 %i, ptr @g
  %inc = add i32 %i, 1
  br label %latch
latch:
  %done = icmp eq i32 %inc, 3
  br i1 %done, label %exit, label %header
exit:
  ret void
}
""")

    def test_two_block_latch_cmp_trip_four_void_matches_upstream(self):
        self._parity("""
define void @f(ptr %p) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  store i32 %i, ptr %p
  br label %latch
latch:
  %inc = add i32 %i, 1
  %c = icmp slt i32 %inc, 4
  br i1 %c, label %loop, label %exit
exit:
  ret void
}
""")

    def test_three_block_trip_one_header_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 1
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_three_block_trip_one_latch_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, 1
  br i1 %c, label %header, label %exit
exit:
  ret i32 %i
}
""")

    def test_three_block_trip_three_header_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, 3
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_three_block_symbolic_const_limit_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  %limit = add i32 0, 2
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %c = icmp slt i32 %i, %limit
  br i1 %c, label %body, label %exit
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_three_block_trip_three_header_eq_cmp_matches_upstream(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %done = icmp eq i32 %i, 3
  br i1 %done, label %exit, label %body
body:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
