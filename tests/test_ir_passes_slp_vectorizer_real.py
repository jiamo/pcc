"""Real-transform tests for SLPVectorizerPass (subset)."""

import unittest

from pcc.ir_passes.slp_vectorizer import (
    SLPVectorizerPass,
    slp_vectorize_module,
)
from pcc.ir_passes.parity import run_pcc_ir_pass


class SLPVectorizerTests(unittest.TestCase):
    def test_four_adjacent_stores_vectorized(self):
        ir = """
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
        out, changed = slp_vectorize_module(ir)
        self.assertTrue(changed)
        # A single `store <4 x i32>` should be present.
        self.assertIn("store <4 x i32>", out)
        # The four scalar stores are gone.
        self.assertEqual(out.count("store i32"), 0)

    def test_non_adjacent_not_vectorized(self):
        ir = """
define void @f(ptr %a) {
entry:
  %p0 = getelementptr i32, ptr %a, i32 0
  %p1 = getelementptr i32, ptr %a, i32 2
  store i32 1, ptr %p0
  store i32 2, ptr %p1
  ret void
}
"""
        _, changed = slp_vectorize_module(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
define void @f(ptr %a) {
entry:
  %p0 = getelementptr i32, ptr %a, i32 0
  %p1 = getelementptr i32, ptr %a, i32 1
  %p2 = getelementptr i32, ptr %a, i32 2
  %p3 = getelementptr i32, ptr %a, i32 3
  store i32 10, ptr %p0
  store i32 20, ptr %p1
  store i32 30, ptr %p2
  store i32 40, ptr %p3
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, SLPVectorizerPass())
        self.assertIn("<4 x i32>", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
