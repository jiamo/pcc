"""Real-transform tests for IPSCCPPass (subset)."""

import shutil

import unittest

from pcc.ir_passes.ipsccp import IPSCCPPass, ipsccp_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class IPSCCPTests(unittest.TestCase):
    def test_same_const_at_all_calls_propagated(self):
        ir = """
define internal i32 @helper(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @a() {
entry:
  %v = call i32 @helper(i32 42)
  ret i32 %v
}
define i32 @b() {
entry:
  %v = call i32 @helper(i32 42)
  ret i32 %v
}
"""
        out, changed = ipsccp_module(ir)
        self.assertTrue(changed)
        # In the body of @helper, %x is substituted with 42.
        helper_section = out[out.find("@helper"):out.find("define i32 @a")]
        self.assertIn("ret i32 43", helper_section)

    def test_different_consts_no_prop(self):
        ir = """
define internal i32 @helper(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @a() {
entry:
  %v = call i32 @helper(i32 42)
  ret i32 %v
}
define i32 @b() {
entry:
  %v = call i32 @helper(i32 7)
  ret i32 %v
}
"""
        _, changed = ipsccp_module(ir)
        self.assertFalse(changed)

    def test_non_internal_not_touched(self):
        ir = """
define i32 @helper(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @a() {
entry:
  %v = call i32 @helper(i32 42)
  ret i32 %v
}
"""
        _, changed = ipsccp_module(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
define internal i32 @dbl(i32 %x) {
entry:
  %r = mul i32 %x, 2
  ret i32 %r
}
define i32 @main() {
entry:
  %a = call i32 @dbl(i32 5)
  %b = call i32 @dbl(i32 5)
  %r = add i32 %a, %b
  ret i32 %r
}
        """
        out, _ = run_pcc_ir_pass(ir, IPSCCPPass())
        self.assertIn("ret i32 20", out)
        self.assertIn("%a = call i32 @dbl(i32 5)", out)
        self.assertIn("%b = call i32 @dbl(i32 5)", out)

    def test_constant_return_propagates_to_callers(self):
        ir = """
define internal i32 @helper(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %a = call i32 @helper(i32 5)
  %b = call i32 @helper(i32 5)
  %r = add i32 %a, %b
  ret i32 %r
}
"""
        out, changed = ipsccp_module(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 12", out)
        self.assertIn("%a = call i32 @helper(i32 5)", out)
        self.assertIn("%b = call i32 @helper(i32 5)", out)

    def test_constant_return_propagates_through_internal_call_chain(self):
        ir = """
define internal i32 @leaf(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define internal i32 @mid() {
entry:
  %a = call i32 @leaf(i32 5)
  ret i32 %a
}
define i32 @main() {
entry:
  %b = call i32 @mid()
  ret i32 %b
}
"""
        out, changed = ipsccp_module(ir)
        self.assertTrue(changed)
        self.assertIn("define internal i32 @mid()", out)
        self.assertIn("ret i32 6", out[out.find("@main"):])
        self.assertIn("%b = call i32 @mid()", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _structural_parity(self, ir: str):
        report = assert_ir_parity(ir, IPSCCPPass(), "ipsccp")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.global_count_diff, None)
        main_diff = [fd for fd in report.diff.function_diffs if fd.name == "main"]
        self.assertEqual(main_diff, [])

    def test_constant_return_propagation_matches_main_shape(self):
        self._structural_parity("""
define internal i32 @helper(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %a = call i32 @helper(i32 5)
  %b = call i32 @helper(i32 5)
  %r = add i32 %a, %b
  ret i32 %r
}
""")

    def test_internal_call_chain_matches_main_shape(self):
        self._structural_parity("""
define internal i32 @leaf(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define internal i32 @mid() {
entry:
  %a = call i32 @leaf(i32 5)
  ret i32 %a
}
define i32 @main() {
entry:
  %b = call i32 @mid()
  ret i32 %b
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
