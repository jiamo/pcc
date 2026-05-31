"""Real-transform parity tests for LoopVectorizePass."""

import unittest

from pcc.ir_passes.loop_vectorize import LoopVectorizePass, vectorize_module
from pcc.ir_passes.parity import normalize_ir, run_pcc_ir_pass, run_upstream_opt


VECTOR_CANDIDATE_IR = """
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


class LoopVectorizeTests(unittest.TestCase):
    def test_direct_pass_boundary_is_noop(self):
        out, changed = vectorize_module(VECTOR_CANDIDATE_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(VECTOR_CANDIDATE_IR))

    def test_pass_integration_matches_input(self):
        out, _ = run_pcc_ir_pass(VECTOR_CANDIDATE_IR, LoopVectorizePass())
        llvm_out = run_upstream_opt(VECTOR_CANDIDATE_IR, "loop-vectorize").ir_text
        self.assertEqual(normalize_ir(out), normalize_ir(llvm_out))

    def test_direct_pass_boundary_matches_upstream(self):
        pcc_out, _ = run_pcc_ir_pass(VECTOR_CANDIDATE_IR, LoopVectorizePass())
        llvm_out = run_upstream_opt(VECTOR_CANDIDATE_IR, "loop-vectorize").ir_text
        self.assertEqual(normalize_ir(pcc_out), normalize_ir(llvm_out))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
