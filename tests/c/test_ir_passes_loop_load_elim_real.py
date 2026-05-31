"""Real-transform tests for LoopLoadElimPass (subset)."""

import unittest

from pcc.ir_passes.loop_load_elim import LoopLoadElimPass, loop_load_elim_text
from pcc.ir_passes.parity import run_pcc_ir_pass


class LoadElimTests(unittest.TestCase):
    def test_store_forward_to_load(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = loop_load_elim_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%v = load", out)
        self.assertIn("ret i32 %x", out)

    def test_call_invalidates_forwarding(self):
        ir = """
declare void @sink()
define i32 @f(i32 %x) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  call void @sink()
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        _, changed = loop_load_elim_text(ir)
        self.assertFalse(changed)

    def test_cross_block_not_forwarded(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  br label %use
use:
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        _, changed = loop_load_elim_text(ir)
        self.assertFalse(changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
