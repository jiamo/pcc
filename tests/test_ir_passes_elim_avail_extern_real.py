"""Real-transform tests for ElimAvailExternPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.elim_avail_extern import (
    ElimAvailExternPass,
    elim_avail_extern_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class ElimAvailExternTests(unittest.TestCase):
    def test_function_body_becomes_declare(self):
        ir = """
define available_externally i32 @helper(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}

define i32 @caller(i32 %x) {
entry:
  %r = call i32 @helper(i32 %x)
  ret i32 %r
}
"""
        out, changed = elim_avail_extern_text(ir)
        self.assertTrue(changed)
        self.assertIn("declare i32 @helper(i32)", out)
        self.assertNotIn("define available_externally", out)
        self.assertIn("define i32 @caller", out)

    def test_global_initializer_becomes_external(self):
        ir = """
@glob = available_externally local_unnamed_addr global i32 7, align 4
"""
        out, changed = elim_avail_extern_text(ir)
        self.assertTrue(changed)
        self.assertEqual(
            out.strip(),
            "@glob = external local_unnamed_addr global i32, align 4",
        )

    def test_constant_global_initializer_becomes_external_constant(self):
        ir = """
@glob = available_externally constant i32 7
"""
        out, changed = elim_avail_extern_text(ir)
        self.assertTrue(changed)
        self.assertEqual(out.strip(), "@glob = external constant i32")

    def test_pass_integration(self):
        ir = """
define available_externally void @helper() {
entry:
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, ElimAvailExternPass())
        self.assertIn("declare void @helper()", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def test_function_matches_upstream_text_shape(self):
        report = assert_ir_parity("""
define available_externally i32 @helper(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}

define i32 @caller(i32 %x) {
entry:
  %r = call i32 @helper(i32 %x)
  ret i32 %r
}
""", ElimAvailExternPass(), "elim-avail-extern")
        self.assertTrue(report.diff.normalized_text_equal)
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_global_matches_upstream_text_shape(self):
        report = assert_ir_parity("""
@glob = available_externally local_unnamed_addr global i32 7, align 4
""", ElimAvailExternPass(), "elim-avail-extern")
        self.assertTrue(report.diff.normalized_text_equal)
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
