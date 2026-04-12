"""Real-transform tests for SimpleLoopUnswitchPass (subset)."""

import unittest

from pcc.ir_passes.simple_loop_unswitch import (
    SimpleLoopUnswitchPass,
    unswitch_module,
)
from pcc.ir_passes.parity import run_pcc_ir_pass


class UnswitchTests(unittest.TestCase):
    def test_invariant_cond_unswitches(self):
        ir = """
define void @f(i1 %invariant, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br i1 %invariant, label %body.t, label %body.f
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
        out, changed = unswitch_module(ir)
        self.assertTrue(changed)
        # Two cloned loops should exist.
        self.assertIn("unsw.t.header", out)
        self.assertIn("unsw.f.header", out)
        # Preheader (entry) now branches on %invariant.
        entry_section = out[out.find("entry:"):out.find("unsw.t.header:")]
        self.assertIn("br i1 %invariant", entry_section)

    def test_variant_cond_not_unswitched(self):
        ir = """
define void @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %iv_cond = icmp slt i32 %i, 5
  br i1 %iv_cond, label %body.t, label %body.f
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
        _, changed = unswitch_module(ir)
        # Cond is defined inside the loop, shouldn't unswitch.
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
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
        out, _ = run_pcc_ir_pass(ir, SimpleLoopUnswitchPass())
        self.assertIn("unsw.t", out)
        self.assertIn("unsw.f", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
