"""Real-transform tests for IndVarSimplifyPass (subset)."""

import shutil

import unittest

from pcc.ir_passes.indvars import IndVarSimplifyPass, indvars_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class IndVarsTests(unittest.TestCase):
    def test_duplicate_ivs_merged(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %j = phi i32 [ 0, %entry ], [ %j.next, %latch ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  %j.next = add i32 %j, 1
  br label %header
exit:
  ret i32 %j
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        # %j and %j.next should be gone; uses replaced with %i and %i.next.
        self.assertNotIn("%j = phi", out)
        self.assertNotIn("%j.next = add", out)
        self.assertTrue(
            "ret i32 %i" in out or "call i32 @llvm.smax.i32(i32 %n, i32 0)" in out
        )

    def test_different_start_not_merged(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %j = phi i32 [ 1, %entry ], [ %j.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %j.next = add i32 %j, 1
  %c = icmp slt i32 %i, 10
  br i1 %c, label %header, label %exit
exit:
  ret i32 %j
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("%i = phi i32 [ 0, %entry ], [ %i.next, %latch ]", out)
        self.assertIn("%j = phi i32 [ 1, %entry ], [ %j.next, %latch ]", out)
        self.assertIn("%j.lcssa = phi i32 [ %j, %latch ]", out)
        self.assertIn("ret i32 %j.lcssa", out)

    def test_live_out_cmp_gets_lcssa_phi(self):
        ir = """
define i1 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i1 %cmp
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("%cmp.lcssa = phi i1 [ %cmp, %header ]", out)
        self.assertIn("ret i1 %cmp.lcssa", out)

    def test_simple_liveout_iv_rewritten_to_smax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.smax.i32(i32 %n, i32 0)", out)
        self.assertIn("ret i32 %smax", out)

    def test_simple_liveout_iv_nonzero_start_rewritten_to_smax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.smax.i32(i32 %n, i32 1)", out)
        self.assertIn("ret i32 %smax", out)

    def test_simple_liveout_iv_with_named_preheader_rewritten_to_smax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %pre
pre:
  br label %header
header:
  %i = phi i32 [ 0, %pre ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.smax.i32(i32 %n, i32 0)", out)
        self.assertIn("ret i32 %smax", out)

    def test_simple_unsigned_liveout_iv_rewritten_to_limit(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %n", out)
        self.assertNotIn("@llvm.smax.i32", out)

    def test_simple_unsigned_liveout_iv_nonzero_start_rewritten_to_umax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.umax.i32(i32 %n, i32 1)", out)
        self.assertIn("ret i32 %umax", out)

    def test_simple_unsigned_liveout_iv_constant_limit_folds_to_constant(self):
        ir = """
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, 9
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 9", out)
        self.assertNotIn("@llvm.umax.i32", out)

    def test_latch_exit_incremented_liveout_rewritten_to_smax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.smax.i32(i32 %n, i32 1)", out)
        self.assertIn("ret i32 %smax", out)

    def test_latch_exit_incremented_liveout_rewritten_to_umax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp ult i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.umax.i32(i32 %n, i32 1)", out)
        self.assertIn("ret i32 %umax", out)

    def test_latch_exit_incremented_liveout_nonzero_start_rewritten_to_smax(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 @llvm.smax.i32(i32 %n, i32 2)", out)
        self.assertIn("ret i32 %smax", out)

    def test_latch_exit_incremented_liveout_eq_rewritten_to_direct_limit(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %latch
latch:
  %inc = add i32 %i, 1
  %done = icmp eq i32 %inc, %n
  br i1 %done, label %exit, label %header
exit:
  ret i32 %inc
}
"""
        out, changed = indvars_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %n", out)
        self.assertIn("br i1 true, label %exit, label %header", out)
        self.assertNotIn("@llvm.smax.i32", out)

    def test_pass_integration(self):
        ir = """
define i1 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i1 %cmp
}
"""
        out, _ = run_pcc_ir_pass(ir, IndVarSimplifyPass())
        self.assertIn("%cmp.lcssa = phi i1 [ %cmp, %header ]", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, IndVarSimplifyPass(), "indvars")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_live_out_cmp_matches_upstream_shape(self):
        self._parity("""
define i1 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i1 %cmp
}
""")

    def test_simple_liveout_iv_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_simple_liveout_iv_nonzero_start_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_simple_liveout_iv_named_preheader_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %pre
pre:
  br label %header
header:
  %i = phi i32 [ 0, %pre ], [ %i.next, %latch ]
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_simple_unsigned_liveout_iv_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_simple_unsigned_liveout_iv_nonzero_start_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, %n
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_simple_unsigned_liveout_iv_constant_limit_matches_upstream_shape(self):
        self._parity("""
define i32 @f() {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  %cmp = icmp ult i32 %i, 9
  br i1 %cmp, label %latch, label %exit
latch:
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 %i
}
""")

    def test_latch_exit_incremented_liveout_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
""")

    def test_latch_exit_incremented_liveout_unsigned_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp ult i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
""")

    def test_latch_exit_incremented_liveout_nonzero_start_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 1, %entry ], [ %i.next, %latch ]
  br label %latch
latch:
  %i.next = add i32 %i, 1
  %cmp = icmp slt i32 %i.next, %n
  br i1 %cmp, label %header, label %exit
exit:
  ret i32 %i.next
}
""")

    def test_latch_exit_incremented_liveout_eq_matches_upstream_shape(self):
        self._parity("""
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
  br label %latch
latch:
  %inc = add i32 %i, 1
  %done = icmp eq i32 %inc, %n
  br i1 %done, label %exit, label %header
exit:
  ret i32 %inc
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
