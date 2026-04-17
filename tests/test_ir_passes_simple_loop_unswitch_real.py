"""Real-transform parity tests for SimpleLoopUnswitchPass."""

import unittest

from pcc.ir_passes.parity import normalize_ir, run_pcc_ir_pass, run_upstream_opt
from pcc.ir_passes.simple_loop_unswitch import SimpleLoopUnswitchPass, unswitch_module


HEADER_BRANCH_IR = """
define void @f(i1 %inv, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br i1 %inv, label %body.t, label %body.f
body.t:
  br label %latch
body.f:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %header, label %exit
exit:
  ret void
}
"""


BODY_BRANCH_IR = """
define i32 @f(i1 %inv, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %body
body:
  br i1 %inv, label %then, label %else
then:
  %a = add i32 %i, 1
  br label %latch
else:
  %b = add i32 %i, 2
  br label %latch
latch:
  %v = phi i32 [ %a, %then ], [ %b, %else ]
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %header, label %exit
exit:
  %out = phi i32 [ %v, %latch ]
  ret i32 %out
}
"""


REUSED_COND_IR = """
define void @f(i1 %inv, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br i1 %inv, label %body.t, label %body.f
body.t:
  br i1 %inv, label %latch, label %latch
body.f:
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %c = icmp slt i32 %i.next, %n
  br i1 %c, label %header, label %exit
exit:
  ret void
}
"""


class UnswitchTests(unittest.TestCase):
    def test_header_invariant_branch_is_noop(self):
        out, changed = unswitch_module(HEADER_BRANCH_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(HEADER_BRANCH_IR))

    def test_body_invariant_branch_is_noop(self):
        out, changed = unswitch_module(BODY_BRANCH_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(BODY_BRANCH_IR))

    def test_reused_cond_control_flow_is_noop(self):
        out, changed = unswitch_module(REUSED_COND_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(REUSED_COND_IR))

    def test_pass_integration_is_noop(self):
        out, _ = run_pcc_ir_pass(HEADER_BRANCH_IR, SimpleLoopUnswitchPass())
        self.assertEqual(normalize_ir(out), normalize_ir(HEADER_BRANCH_IR))

    def test_header_branch_matches_upstream_direct_pass_boundary(self):
        pcc_out, _ = run_pcc_ir_pass(HEADER_BRANCH_IR, SimpleLoopUnswitchPass())
        llvm_out = run_upstream_opt(HEADER_BRANCH_IR, "simple-loop-unswitch").ir_text
        self.assertEqual(normalize_ir(pcc_out), normalize_ir(llvm_out))

    def test_body_branch_matches_upstream_direct_pass_boundary(self):
        pcc_out, _ = run_pcc_ir_pass(BODY_BRANCH_IR, SimpleLoopUnswitchPass())
        llvm_out = run_upstream_opt(BODY_BRANCH_IR, "simple-loop-unswitch").ir_text
        self.assertEqual(normalize_ir(pcc_out), normalize_ir(llvm_out))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
