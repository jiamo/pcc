"""Real-transform parity tests for LoopDistributePass."""

import unittest

from pcc.ir_passes.loop_distribute import LoopDistributePass, distribute_module
from pcc.ir_passes.parity import normalize_ir, run_pcc_ir_pass, run_upstream_opt


DISTRIBUTE_CANDIDATE_IR = """
define void @f(ptr %a, ptr %b, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  store i32 1, ptr %a
  store i32 2, ptr %b
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret void
}
"""


class LoopDistributeTests(unittest.TestCase):
    def test_direct_pass_boundary_is_noop(self):
        out, changed = distribute_module(DISTRIBUTE_CANDIDATE_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(DISTRIBUTE_CANDIDATE_IR))

    def test_pass_integration_matches_input(self):
        out, _ = run_pcc_ir_pass(DISTRIBUTE_CANDIDATE_IR, LoopDistributePass())
        llvm_out = run_upstream_opt(DISTRIBUTE_CANDIDATE_IR, "loop-distribute").ir_text
        self.assertEqual(normalize_ir(out), normalize_ir(llvm_out))

    def test_direct_pass_boundary_matches_upstream(self):
        pcc_out, _ = run_pcc_ir_pass(DISTRIBUTE_CANDIDATE_IR, LoopDistributePass())
        llvm_out = run_upstream_opt(DISTRIBUTE_CANDIDATE_IR, "loop-distribute").ir_text
        self.assertEqual(normalize_ir(pcc_out), normalize_ir(llvm_out))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
