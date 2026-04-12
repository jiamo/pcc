"""Real-transform tests for ArgPromotionPass (subset)."""

import shutil

import unittest

from pcc.ir_passes.argpromotion import ArgPromotionPass, argpromotion_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class ArgPromotionTests(unittest.TestCase):
    def test_single_load_ptr_arg_promoted(self):
        ir = """
define internal i32 @helper(ptr %p) {
entry:
  %v = load i32, ptr %p
  %r = add i32 %v, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %slot = alloca i32
  store i32 42, ptr %slot
  %r = call i32 @helper(ptr %slot)
  ret i32 %r
}
"""
        out, changed = argpromotion_module(ir)
        self.assertTrue(changed)
        # helper's signature changes to take i32.
        self.assertIn("define internal i32 @helper(i32", out)
        # The load inside helper is gone.
        helper_section = out[out.find("@helper"):out.find("define i32 @main")]
        self.assertNotIn("load i32", helper_section)
        # Call site loads the value before calling.
        main_section = out[out.find("@main"):]
        self.assertIn("load i32, ptr %slot", main_section)
        self.assertIn("call i32 @helper(i32 %argprom", main_section)

    def test_stored_arg_not_promoted(self):
        ir = """
define internal void @writer(ptr %p) {
entry:
  store i32 7, ptr %p
  ret void
}
define void @main() {
entry:
  %s = alloca i32
  call void @writer(ptr %s)
  ret void
}
"""
        _, changed = argpromotion_module(ir)
        self.assertFalse(changed)

    def test_external_fn_not_promoted(self):
        ir = """
define i32 @helper(ptr %p) {
entry:
  %v = load i32, ptr %p
  ret i32 %v
}
define i32 @main() {
entry:
  %s = alloca i32
  %r = call i32 @helper(ptr %s)
  ret i32 %r
}
"""
        _, changed = argpromotion_module(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
define internal i32 @load_add(ptr %p, i32 %k) {
entry:
  %v = load i32, ptr %p
  %r = add i32 %v, %k
  ret i32 %r
}
define i32 @main() {
entry:
  %slot = alloca i32
  store i32 10, ptr %slot
  %r = call i32 @load_add(ptr %slot, i32 5)
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, ArgPromotionPass())
        # Both arg positions handled: ptr promoted, i32 passed through.
        self.assertIn("define internal i32 @load_add(i32 %p, i32 %k)", out)
        self.assertIn("call i32 @load_add(i32 %argprom", out)

    def test_global_pointer_actual_is_promoted(self):
        ir = """
@g = internal global i32 7
define internal i32 @helper(ptr %p) {
entry:
  %v = load i32, ptr %p
  %r = add i32 %v, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @helper(ptr @g)
  ret i32 %r
}
"""
        out, changed = argpromotion_module(ir)
        self.assertTrue(changed)
        self.assertIn("define internal i32 @helper(i32 %p)", out)
        self.assertIn("load i32, ptr @g", out)
        self.assertIn("call i32 @helper(i32 %argprom", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _structural_parity(self, ir: str):
        report = assert_ir_parity(ir, ArgPromotionPass(), "argpromotion")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_global_pointer_actual_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 7
define internal i32 @helper(ptr %p) {
entry:
  %v = load i32, ptr %p
  %r = add i32 %v, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @helper(ptr @g)
  ret i32 %r
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
