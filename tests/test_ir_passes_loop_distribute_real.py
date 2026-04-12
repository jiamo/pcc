"""Real-transform tests for LoopDistributePass (subset)."""

import unittest

from pcc.ir_passes.loop_distribute import (
    LoopDistributePass,
    distribute_module,
)
from pcc.ir_passes.parity import run_pcc_ir_pass


class LoopDistributeTests(unittest.TestCase):
    def test_independent_stores_split(self):
        ir = """
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
        out, changed = distribute_module(ir)
        self.assertTrue(changed)
        # Two loops should exist after distribution.
        self.assertIn("dist.a", out)
        self.assertIn("dist.b", out)
        # First clone only stores to %a, second only to %b.
        # (We check the post-split shape.)
        a_section_start = out.find("dist.a.body:")
        a_section_end = out.find("dist.b.body:")
        if a_section_start != -1 and a_section_end != -1:
            a_section = out[a_section_start:a_section_end]
            self.assertIn("store i32 1, ptr %a", a_section)
            self.assertNotIn("store i32 2, ptr %b", a_section)

    def test_single_store_not_distributed(self):
        ir = """
define void @f(ptr %a, i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  store i32 1, ptr %a
  br label %latch
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret void
}
"""
        _, changed = distribute_module(ir)
        # Only one distinct pointer — not worth distributing.
        self.assertFalse(changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
