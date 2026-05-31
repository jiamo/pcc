"""Real-transform parity tests for SLPVectorizerPass."""

import unittest

from pcc.ir_passes.slp_vectorizer import SLPVectorizerPass, slp_vectorize_module
from pcc.ir_passes.parity import normalize_ir, run_pcc_ir_pass, run_upstream_opt


STORE_PACK_CANDIDATE_IR = """
define void @f(ptr %a) {
entry:
  %p0 = getelementptr i32, ptr %a, i32 0
  %p1 = getelementptr i32, ptr %a, i32 1
  %p2 = getelementptr i32, ptr %a, i32 2
  %p3 = getelementptr i32, ptr %a, i32 3
  store i32 1, ptr %p0
  store i32 2, ptr %p1
  store i32 3, ptr %p2
  store i32 4, ptr %p3
  ret void
}
"""


class SLPVectorizerTests(unittest.TestCase):
    def test_direct_pass_boundary_is_noop(self):
        out, changed = slp_vectorize_module(STORE_PACK_CANDIDATE_IR)
        self.assertFalse(changed)
        self.assertEqual(normalize_ir(out), normalize_ir(STORE_PACK_CANDIDATE_IR))

    def test_pass_integration_matches_input(self):
        out, _ = run_pcc_ir_pass(STORE_PACK_CANDIDATE_IR, SLPVectorizerPass())
        llvm_out = run_upstream_opt(STORE_PACK_CANDIDATE_IR, "slp-vectorizer").ir_text
        self.assertEqual(normalize_ir(out), normalize_ir(llvm_out))

    def test_direct_pass_boundary_matches_upstream(self):
        pcc_out, _ = run_pcc_ir_pass(STORE_PACK_CANDIDATE_IR, SLPVectorizerPass())
        llvm_out = run_upstream_opt(STORE_PACK_CANDIDATE_IR, "slp-vectorizer").ir_text
        self.assertEqual(normalize_ir(pcc_out), normalize_ir(llvm_out))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
