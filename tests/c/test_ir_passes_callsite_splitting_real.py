"""Real-transform tests for CallSiteSplittingPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.callsite_splitting import (
    CallSiteSplittingPass,
    callsite_splitting_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class CallSiteSplittingTests(unittest.TestCase):
    def test_phi_fed_direct_call_splits(self):
        ir = """
define i32 @callee(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}

define i32 @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %x = phi i32 [ 1, %a ], [ 2, %b ]
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        out, changed = callsite_splitting_text(ir)
        self.assertTrue(changed)
        self.assertIn("a.split:", out)
        self.assertIn("b.split:", out)
        self.assertIn("%css.a = call i32 @callee(i32 1)", out)
        self.assertIn("%css.b = call i32 @callee(i32 2)", out)
        self.assertIn("%phi.call = phi i32", out)

    def test_void_call_splits(self):
        ir = """
define void @callee(i32 %x) {
entry:
  ret void
}

define void @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %x = phi i32 [ 1, %a ], [ 2, %b ]
  call void @callee(i32 %x)
  ret void
}
"""
        out, changed = callsite_splitting_text(ir)
        self.assertTrue(changed)
        self.assertIn("a.split:", out)
        self.assertIn("call void @callee(i32 1)", out)
        self.assertIn("call void @callee(i32 2)", out)
        self.assertNotIn("%phi.call", out)

    def test_non_phi_call_not_split(self):
        ir = """
define i32 @callee(i32 %x) {
entry:
  ret i32 %x
}

define i32 @f(i32 %x, i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        out, changed = callsite_splitting_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_declaration_only_callee_not_split(self):
        ir = """
declare i32 @callee(i32)

define i32 @f(i1 %c, i32 %a, i32 %b) {
entry:
  br i1 %c, label %t, label %f
t:
  br label %join
f:
  br label %join
join:
  %x = phi i32 [ %a, %t ], [ %b, %f ]
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        out, changed = callsite_splitting_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration(self):
        ir = """
define i32 @callee(i32 %x) {
entry:
  ret i32 %x
}

define i32 @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %x = phi i32 [ 1, %a ], [ 2, %b ]
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, CallSiteSplittingPass())
        self.assertIn("a.split:", out)
        self.assertIn("%phi.call = phi i32", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _structural_parity(self, ir: str):
        report = assert_ir_parity(ir, CallSiteSplittingPass(), "callsite-splitting")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_phi_fed_direct_call_matches_upstream(self):
        self._structural_parity("""
define i32 @callee(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}

define i32 @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %x = phi i32 [ 1, %a ], [ 2, %b ]
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
""")

    def test_void_phi_fed_call_matches_upstream(self):
        self._structural_parity("""
define void @callee(i32 %x) {
entry:
  ret void
}

define void @f(i1 %c) {
entry:
  br i1 %c, label %a, label %b
a:
  br label %join
b:
  br label %join
join:
  %x = phi i32 [ 1, %a ], [ 2, %b ]
  call void @callee(i32 %x)
  ret void
}
""")

    def test_declaration_only_callee_matches_upstream(self):
        self._structural_parity("""
declare i32 @callee(i32)

define i32 @f(i1 %c, i32 %a, i32 %b) {
entry:
  br i1 %c, label %t, label %f
t:
  br label %join
f:
  br label %join
join:
  %x = phi i32 [ %a, %t ], [ %b, %f ]
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
