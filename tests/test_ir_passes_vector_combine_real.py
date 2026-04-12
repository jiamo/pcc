"""Real-transform tests for VectorCombinePass (subset)."""

import unittest

from pcc.ir_passes.vector_combine import VectorCombinePass, vector_combine_text
from pcc.ir_passes.parity import run_pcc_ir_pass


class VectorCombineTests(unittest.TestCase):
    def test_extract_of_insert_folded(self):
        ir = """
define i32 @f(<4 x i32> %v, i32 %x) {
entry:
  %a = insertelement <4 x i32> %v, i32 %x, i32 2
  %b = extractelement <4 x i32> %a, i32 2
  ret i32 %b
}
"""
        out, changed = vector_combine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%b = extractelement", out)

    def test_different_index_not_folded(self):
        ir = """
define i32 @f(<4 x i32> %v, i32 %x) {
entry:
  %a = insertelement <4 x i32> %v, i32 %x, i32 2
  %b = extractelement <4 x i32> %a, i32 3
  ret i32 %b
}
"""
        _, changed = vector_combine_text(ir)
        self.assertFalse(changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
