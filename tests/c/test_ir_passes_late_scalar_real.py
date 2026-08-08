"""Parity corpus for LateScalarPass (div-rem-pairs + constmerge)."""

import pytest
import unittest

from pcc.ir_passes.late_scalar import (
    LateScalarPass,
    constmerge_text,
    divrem_pairs_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class DivRemPairsTests(unittest.TestCase):
    def test_sdiv_srem_paired(self):
        ir = """
        define void @f(i32 %a, i32 %b, ptr %out_q, ptr %out_r) {
        entry:
          %q = sdiv i32 %a, %b
          %r = srem i32 %a, %b
          store i32 %q, ptr %out_q
          store i32 %r, ptr %out_r
          ret void
        }
        """
        out, changed = divrem_pairs_text(ir)
        self.assertTrue(changed)
        self.assertIn("mul i32 %q, %b", out)
        self.assertIn("sub i32 %a, %r.mul", out)
        # Still has the sdiv.
        self.assertIn("sdiv i32 %a, %b", out)
        # Original srem is gone.
        self.assertNotIn("srem i32 %a, %b", out)

    def test_udiv_urem_paired(self):
        ir = """
        define void @f(i32 %a, i32 %b, ptr %p_q, ptr %p_r) {
        entry:
          %q = udiv i32 %a, %b
          %r = urem i32 %a, %b
          store i32 %q, ptr %p_q
          store i32 %r, ptr %p_r
          ret void
        }
        """
        out, changed = divrem_pairs_text(ir)
        self.assertTrue(changed)
        self.assertIn("udiv i32 %a, %b", out)
        self.assertNotIn("urem i32 %a, %b", out)

    def test_different_operands_not_paired(self):
        ir = """
        define void @f(i32 %a, i32 %b, i32 %c, ptr %out_q, ptr %out_r) {
        entry:
          %q = sdiv i32 %a, %b
          %r = srem i32 %a, %c
          store i32 %q, ptr %out_q
          store i32 %r, ptr %out_r
          ret void
        }
        """
        _, changed = divrem_pairs_text(ir)
        self.assertFalse(changed)

    def test_signed_unsigned_not_paired(self):
        ir = """
        define void @f(i32 %a, i32 %b, ptr %p1, ptr %p2) {
        entry:
          %q = sdiv i32 %a, %b
          %r = urem i32 %a, %b
          store i32 %q, ptr %p1
          store i32 %r, ptr %p2
          ret void
        }
        """
        _, changed = divrem_pairs_text(ir)
        self.assertFalse(changed)


class ConstMergeTests(unittest.TestCase):
    def test_identical_private_constants_merged(self):
        ir = """
        @.str1 = private unnamed_addr constant [5 x i8] c"hello"
        @.str2 = private unnamed_addr constant [5 x i8] c"hello"
        define ptr @f() {
        entry:
          ret ptr @.str2
        }
        """
        out, changed = constmerge_text(ir)
        self.assertTrue(changed)
        self.assertEqual(out.count("c\"hello\""), 1)

    def test_internal_constants_merged(self):
        ir = """
        @.k1 = internal unnamed_addr constant i32 42
        @.k2 = internal unnamed_addr constant i32 42
        define i32 @f() {
        entry:
          %v = load i32, ptr @.k2
          ret i32 %v
        }
        """
        out, changed = constmerge_text(ir)
        self.assertTrue(changed)

    def test_distinct_constants_preserved(self):
        ir = """
        @.a = private unnamed_addr constant [2 x i8] c"ab"
        @.b = private unnamed_addr constant [2 x i8] c"cd"
        define void @f() {
        entry:
          ret void
        }
        """
        _, changed = constmerge_text(ir)
        self.assertFalse(changed)

    def test_non_constant_globals_not_merged(self):
        ir = """
        @g1 = private unnamed_addr global i32 0
        @g2 = private unnamed_addr global i32 0
        define void @f() { entry: ret void }
        """
        _, changed = constmerge_text(ir)
        self.assertFalse(changed)

    def test_missing_unnamed_addr_not_merged(self):
        ir = """
        @.a = private constant [2 x i8] c"ok"
        @.b = private constant [2 x i8] c"ok"
        define void @f() { entry: ret void }
        """
        _, changed = constmerge_text(ir)
        self.assertFalse(changed)


class PassIntegrationTests(unittest.TestCase):
    def test_end_to_end(self):
        ir = """
        @.s1 = private unnamed_addr constant [3 x i8] c"hi\\00"
        @.s2 = private unnamed_addr constant [3 x i8] c"hi\\00"
        define void @f(i32 %a, i32 %b, ptr %o) {
        entry:
          %q = sdiv i32 %a, %b
          %r = srem i32 %a, %b
          store i32 %q, ptr %o
          ret void
        }
        """
        out, _ = run_pcc_ir_pass(ir, LateScalarPass())
        self.assertEqual(out.count("c\"hi"), 1)
        self.assertIn("mul i32", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_divrem_matches_upstream(self):
        ir = """
define void @f(i32 %a, i32 %b, ptr %q, ptr %r) {
entry:
  %quo = sdiv i32 %a, %b
  %rem = srem i32 %a, %b
  store i32 %quo, ptr %q
  store i32 %rem, ptr %r
  ret void
}
"""
        report = assert_ir_parity(ir, LateScalarPass(), "div-rem-pairs")
        # Both should have dropped one of (sdiv, srem) in favor of
        # the combined form. Check srem is gone on both sides.
        self.assertNotIn("srem i32 %a, %b", report.pcc_ir)

    def test_constmerge_matches_upstream(self):
        ir = """
@.a = private unnamed_addr constant [5 x i8] c"dup\\00\\00"
@.b = private unnamed_addr constant [5 x i8] c"dup\\00\\00"
define ptr @f() {
entry:
  ret ptr @.a
}
"""
        report = assert_ir_parity(ir, LateScalarPass(), "constmerge")
        # Both should have a single duplicate.
        self.assertEqual(report.pcc_ir.count("c\"dup"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
