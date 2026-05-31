"""Real-transform tests for CalledValuePropagationPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.called_value_prop import (
    CalledValuePropagationPass,
    called_value_prop_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class CVPropTests(unittest.TestCase):
    def test_alloca_single_store_not_rewritten_here(self):
        ir = """
define i32 @target(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @f() {
entry:
  %fp = alloca ptr
  store ptr @target, ptr %fp
  %fn = load ptr, ptr %fp
  %r = call i32 %fn(i32 42)
  ret i32 %r
}
"""
        out, changed = called_value_prop_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_internal_global_known_callee_gets_metadata(self):
        ir = """
@slot = internal global ptr @target

define i32 @target(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @f() {
entry:
  %fn = load ptr, ptr @slot
  %r = call i32 %fn(i32 42)
  ret i32 %r
}
"""
        out, changed = called_value_prop_text(ir)
        self.assertTrue(changed)
        self.assertIn("call i32 %fn(i32 42), !callees !0", out)
        self.assertIn("!0 = !{ptr @target}", out)

    def test_multiple_stores_no_devirt(self):
        ir = """
define i32 @a(i32 %x) { entry: ret i32 %x }
define i32 @b(i32 %x) { entry: ret i32 %x }
define i32 @f(i1 %c) {
entry:
  %fp = alloca ptr
  br i1 %c, label %ta, label %tb
ta:
  store ptr @a, ptr %fp
  br label %join
tb:
  store ptr @b, ptr %fp
  br label %join
join:
  %fn = load ptr, ptr %fp
  %r = call i32 %fn(i32 7)
  ret i32 %r
}
"""
        _, changed = called_value_prop_text(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
@slot = internal global ptr @target

define i32 @target(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @f() {
entry:
  %fn = load ptr, ptr @slot
  %r = call i32 %fn(i32 42)
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, CalledValuePropagationPass())
        self.assertIn("!callees !0", out)
        self.assertIn("!0 = !{ptr @target}", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_internal_global_known_callee_matches_upstream_shape(self):
        report = assert_ir_parity("""
@slot = internal global ptr @target

define i32 @target(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @f() {
entry:
  %fn = load ptr, ptr @slot
  %r = call i32 %fn(i32 42)
  ret i32 %r
}
""", CalledValuePropagationPass(), "called-value-propagation")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
