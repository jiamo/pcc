"""Real-transform tests for DeadArgElimPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.arg_opt import DeadArgElimPass, deadargelim_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class DeadArgElimTests(unittest.TestCase):
    def test_unused_arg_removed_from_internal_fn(self):
        ir = """
define internal i32 @helper(i32 %a, i32 %unused, i32 %b) {
entry:
  %r = add i32 %a, %b
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @helper(i32 10, i32 99, i32 20)
  ret i32 %r
}
"""
        out, changed = deadargelim_text(ir)
        self.assertTrue(changed)
        # Signature no longer has %unused.
        self.assertNotIn("%unused", out)
        # Call site dropped the 99.
        self.assertNotIn("i32 99", out)
        # Kept args still there.
        self.assertIn("i32 10", out)
        self.assertIn("i32 20", out)

    def test_external_fn_not_touched(self):
        # Non-internal linkage — caller set unknown, cannot modify.
        ir = """
define i32 @helper(i32 %a, i32 %unused) {
entry:
  ret i32 %a
}
"""
        _, changed = deadargelim_text(ir)
        self.assertFalse(changed)

    def test_all_args_used_no_change(self):
        ir = """
define internal i32 @helper(i32 %a, i32 %b) {
entry:
  %r = add i32 %a, %b
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @helper(i32 1, i32 2)
  ret i32 %r
}
"""
        _, changed = deadargelim_text(ir)
        self.assertFalse(changed)

    def test_unused_return_rewritten_to_void(self):
        ir = """
define internal i32 @helper(i32 %a) {
entry:
  %r = add i32 %a, 1
  ret i32 %r
}
define void @main() {
entry:
  %r = call i32 @helper(i32 10)
  ret void
}
"""
        out, changed = deadargelim_text(ir)
        self.assertTrue(changed)
        self.assertIn("define internal void @helper(i32 %a)", out)
        self.assertIn("ret void", out)
        self.assertIn("call void @helper(i32 10)", out)
        self.assertNotIn("%r = call i32 @helper", out)

    def test_used_return_not_rewritten(self):
        ir = """
define internal i32 @helper(i32 %a) {
entry:
  %r = add i32 %a, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @helper(i32 10)
  ret i32 %r
}
"""
        out, changed = deadargelim_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        ir = """
define internal i32 @helper(i32 %a, i32 %unused) {
entry:
  ret i32 %a
}
define i32 @main() {
entry:
  %r = call i32 @helper(i32 42, i32 7)
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, DeadArgElimPass())
        self.assertNotIn("%unused", out)
        self.assertNotIn("i32 7", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_unused_return_matches_upstream_shape(self):
        report = assert_ir_parity("""
define internal i32 @helper(i32 %a) {
entry:
  %r = add i32 %a, 1
  ret i32 %r
}
define void @main() {
entry:
  %r = call i32 @helper(i32 10)
  ret void
}
""", DeadArgElimPass(), "deadargelim")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
