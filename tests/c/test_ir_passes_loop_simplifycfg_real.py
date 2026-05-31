"""Real-transform tests for LoopSimplifyCFGPass (subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.loop_simplifycfg import (
    LoopSimplifyCFGPass,
    loop_simplifycfg_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class LoopSimplifyCFGTests(unittest.TestCase):
    def test_loop_local_constant_branch_simplifies(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  br i1 true, label %fast, label %slow
fast:
  br label %latch
slow:
  br label %latch
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_simplifycfg_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("fast:", out)
        self.assertNotIn("slow:", out)
        self.assertNotIn("latch:", out)
        self.assertIn("body:", out)
        self.assertIn("%inc = add i32 %i, 1", out)
        self.assertIn("%i.lcssa = phi i32 [ %i, %header ]", out)

    def test_non_loop_cfg_not_changed(self):
        ir = """
define i32 @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  ret i32 1
b:
  ret i32 2
}
"""
        out, changed = loop_simplifycfg_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_loop_local_cleanup_keeps_module_context(self):
        ir = """
@.class.Node = external global ptr

define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  %.cls = load ptr, ptr @.class.Node
  br i1 true, label %fast, label %slow
fast:
  br label %latch
slow:
  br label %latch
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = loop_simplifycfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("@.class.Node", out)
        llvm.parse_assembly(out).verify()

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  br i1 true, label %fast, label %slow
fast:
  br label %latch
slow:
  br label %latch
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, _ = run_pcc_ir_pass(ir, LoopSimplifyCFGPass())
        self.assertIn("%i.lcssa = phi i32 [ %i, %header ]", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_loop_local_constant_branch_matches_upstream(self):
        report = assert_ir_parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  %c = icmp slt i32 %i, %n
  br i1 %c, label %body, label %exit
body:
  br i1 true, label %fast, label %slow
fast:
  br label %latch
slow:
  br label %latch
latch:
  %inc = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""", LoopSimplifyCFGPass(), "loop-simplifycfg")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
