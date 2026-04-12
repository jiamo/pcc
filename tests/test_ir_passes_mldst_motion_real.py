"""Real-transform tests for MergedLoadStoreMotionPass (subset)."""

import unittest

from pcc.ir_passes.mldst_motion import (
    MergedLoadStoreMotionPass,
    mldst_motion_module,
)
from pcc.ir_passes.parity import run_pcc_ir_pass


class MLDSTTests(unittest.TestCase):
    def test_identical_loads_hoisted(self):
        ir = """
define i32 @f(i1 %c) {
entry:
  %p = alloca i32
  store i32 42, ptr %p
  br i1 %c, label %b, label %d
b:
  %x1 = load i32, ptr %p
  %v1 = add i32 %x1, 1
  br label %m
d:
  %x2 = load i32, ptr %p
  %v2 = add i32 %x2, 2
  br label %m
m:
  %r = phi i32 [%v1, %b], [%v2, %d]
  ret i32 %r
}
"""
        out, changed = mldst_motion_module(ir)
        self.assertTrue(changed)
        # One load in entry (the hoisted one), not in b/d anymore.
        self.assertEqual(out.count("load i32"), 1)

    def test_different_pointers_not_hoisted(self):
        ir = """
define i32 @f(i1 %c) {
entry:
  %p = alloca i32
  %q = alloca i32
  store i32 1, ptr %p
  store i32 2, ptr %q
  br i1 %c, label %b, label %d
b:
  %x1 = load i32, ptr %p
  br label %m
d:
  %x2 = load i32, ptr %q
  br label %m
m:
  %r = phi i32 [%x1, %b], [%x2, %d]
  ret i32 %r
}
"""
        _, changed = mldst_motion_module(ir)
        self.assertFalse(changed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
