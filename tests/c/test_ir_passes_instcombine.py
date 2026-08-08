"""Tests for InstCombinePass (subset)."""

import pytest
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.instcombine import InstCombinePass, instcombine_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


_CORPUS_IR = """
define i32 @add_self(i32 %x) {
entry:
  %r = add i32 %x, %x
  ret i32 %r
}

define i32 @sub_neg_const(i32 %x) {
entry:
  %r = sub i32 %x, -7
  ret i32 %r
}

define i32 @mul_neg1(i32 %x) {
entry:
  %r = mul i32 %x, -1
  ret i32 %r
}

define i32 @mul_pow2_neglhs(i32 %x) {
entry:
  %r = mul i32 8, %x
  ret i32 %r
}

define i32 @mul_then_add_self(i32 %x) {
entry:
  %a = mul i32 %x, 2
  %b = add i32 %a, %x
  ret i32 %b
}

define i32 @add_neg_then_const(i32 %x) {
entry:
  %a = sub i32 0, %x
  %b = add i32 %a, 1
  ret i32 %b
}

define i32 @double_neg(i32 %x) {
entry:
  %a = sub i32 0, %x
  %b = sub i32 0, %a
  ret i32 %b
}

define i32 @add_neg_cancel(i32 %x) {
entry:
  %a = sub i32 0, %x
  %b = add i32 %x, %a
  ret i32 %b
}

define i32 @add_sub_const_cancel(i32 %x) {
entry:
  %a = sub i32 5, %x
  %b = add i32 %a, %x
  ret i32 %b
}

define i32 @shl_minus_base(i32 %x) {
entry:
  %a = shl i32 %x, 1
  %b = sub i32 %a, %x
  ret i32 %b
}

define i32 @sub_self(i32 %x) {
entry:
  %r = sub i32 %x, %x
  ret i32 %r
}

define i32 @shl2_plus_base(i32 %x) {
entry:
  %a = shl i32 %x, 2
  %b = add i32 %a, %x
  ret i32 %b
}

define i32 @mul3_minus_base(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %b = sub i32 %a, %x
  ret i32 %b
}

define i32 @mul3_plus_base(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %b = add i32 %a, %x
  ret i32 %b
}

define i32 @mul3_plus_mul4(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %b = mul i32 %x, 4
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @mul3_plus_shl2(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %b = shl i32 %x, 2
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @shl3_minus_mul3(i32 %x) {
entry:
  %a = shl i32 %x, 3
  %b = mul i32 %x, 3
  %c = sub i32 %a, %b
  ret i32 %c
}

define i32 @sub_x_neg_y(i32 %x, i32 %y) {
entry:
  %a = sub i32 0, %y
  %b = sub i32 %x, %a
  ret i32 %b
}

define i32 @sub_x_shl1(i32 %x) {
entry:
  %a = shl i32 %x, 1
  %b = sub i32 %x, %a
  ret i32 %b
}

define i32 @add_shl_neg_base(i32 %x) {
entry:
  %a = sub i32 0, %x
  %b = shl i32 %a, 1
  %r = add i32 %b, %x
  ret i32 %r
}

define i32 @sub_zero_xor_not(i32 %x) {
entry:
  %a = xor i32 %x, -1
  %r = sub i32 0, %a
  ret i32 %r
}

define i32 @add_sub_const_base(i32 %x) {
entry:
  %a = sub i32 %x, 5
  %b = add i32 %a, 5
  ret i32 %b
}

define i32 @add_sub_const_delta(i32 %x) {
entry:
  %a = sub i32 %x, 5
  %b = add i32 %a, 3
  ret i32 %b
}

define i32 @sub_add_const_base(i32 %x) {
entry:
  %a = add i32 %x, 5
  %b = sub i32 %a, 5
  ret i32 %b
}

define i32 @sub_add_const_delta(i32 %x) {
entry:
  %a = add i32 %x, 5
  %b = sub i32 %a, 3
  ret i32 %b
}

define i32 @sub_const_add_delta(i32 %x) {
entry:
  %a = add i32 %x, 3
  %b = sub i32 10, %a
  ret i32 %b
}

define i32 @sub_const_sub_const_base(i32 %x) {
entry:
  %a = sub i32 7, %x
  %b = sub i32 10, %a
  ret i32 %b
}

define i32 @sub_const_sub_var_delta(i32 %x) {
entry:
  %a = sub i32 %x, 7
  %b = sub i32 10, %a
  ret i32 %b
}

define i32 @xor_not_not(i32 %x) {
entry:
  %a = xor i32 %x, -1
  %b = xor i32 %a, -1
  ret i32 %b
}

define i32 @zext_true() {
entry:
  %r = zext i1 true to i32
  ret i32 %r
}

define i32 @sext_true() {
entry:
  %r = sext i1 true to i32
  ret i32 %r
}

define i32 @mul_one(i32 %x) {
entry:
  %r = mul i32 %x, 1
  ret i32 %r
}

define i32 @mul_zero(i32 %x) {
entry:
  %r = mul i32 %x, 0
  ret i32 %r
}

define i32 @xor_zero(i32 %x) {
entry:
  %r = xor i32 %x, 0
  ret i32 %r
}

define i32 @sub_zero(i32 %x) {
entry:
  %r = sub i32 %x, 0
  ret i32 %r
}

define i32 @or_zero(i32 %x) {
entry:
  %r = or i32 %x, 0
  ret i32 %r
}

define i32 @and_neg_one(i32 %x) {
entry:
  %r = and i32 %x, -1
  ret i32 %r
}

define i32 @and_zero(i32 %x) {
entry:
  %r = and i32 %x, 0
  ret i32 %r
}

define i32 @xor_self(i32 %x) {
entry:
  %r = xor i32 %x, %x
  ret i32 %r
}

define i32 @or_self(i32 %x) {
entry:
  %r = or i32 %x, %x
  ret i32 %r
}

define i32 @and_self(i32 %x) {
entry:
  %r = and i32 %x, %x
  ret i32 %r
}

define i32 @or_all_ones(i32 %x) {
entry:
  %r = or i32 %x, -1
  ret i32 %r
}

define i32 @negate_sub(i32 %x, i32 %y) {
entry:
  %a = sub i32 %x, %y
  %r = sub i32 0, %a
  ret i32 %r
}

define i32 @negate_mul(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %r = sub i32 0, %a
  ret i32 %r
}

define i32 @negate_shl(i32 %x) {
entry:
  %a = shl i32 %x, 2
  %r = sub i32 0, %a
  ret i32 %r
}

define i32 @add_neg_var(i32 %x, i32 %y) {
entry:
  %a = sub i32 0, %x
  %r = add i32 %a, %y
  ret i32 %r
}

define i32 @add_var_neg(i32 %x, i32 %y) {
entry:
  %a = sub i32 0, %x
  %r = add i32 %y, %a
  ret i32 %r
}
"""


class InstCombineTests(unittest.TestCase):
    def test_mul_power_of_two_becomes_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, 4
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("mul i32", out)
        self.assertIn("shl i32", out)
        self.assertIn("shl i32 %x, 2", out)

    def test_mul_power_of_two_const_on_lhs_becomes_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 8, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 3", out)

    def test_add_self_becomes_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = add i32 %x, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 1", out)

    def test_local_cleanup_keeps_module_context(self):
        ir = """
        @.pystr.0 = internal constant [2 x i8] c"x\\00"
        declare void @sink(ptr)

        define ptr @helper(ptr %p) {
        entry:
          ret ptr %p
        }

        define i64 @f(i64 %x) {
        entry:
          %p = getelementptr inbounds [2 x i8], ptr @.pystr.0, i32 0, i32 0
          %q = call ptr @helper(ptr %p)
          call void @sink(ptr %q)
          %r = add i64 %x, %x
          ret i64 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("@.pystr.0", out)
        self.assertIn("@helper", out)
        self.assertIn("shl i64 %x, 1", out)
        llvm.parse_assembly(out).verify()

    def test_sub_negative_constant_becomes_add(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, -7
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("add i32 %x, 7", out)

    def test_mul_neg_one_becomes_sub_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, -1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 0, %x", out)

    def test_mul_one_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, 1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("mul i32", out)
        self.assertIn("ret i32 %x", out)

    def test_mul_zero_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("mul i32", out)
        self.assertIn("ret i32 0", out)

    def test_xor_zero_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = xor i32 %x, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("xor i32 %x, 0", out)
        self.assertIn("ret i32 %x", out)

    def test_sub_zero_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("sub i32 %x, 0", out)
        self.assertIn("ret i32 %x", out)

    def test_add_add_const_becomes_single_add(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %r = add i32 %a, 3
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = add i32 %x, 8", out)
        self.assertNotIn("%a = add i32 %x, 5", out)

    def test_add_const_sub_x_const_becomes_single_add(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %r = add i32 3, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = add i32 %x, -2", out)
        self.assertNotIn("%a = sub i32 %x, 5", out)

    def test_sub_sub_const_becomes_single_add_negative_const(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %r = sub i32 %a, 3
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = add i32 %x, -8", out)
        self.assertNotIn("%a = sub i32 %x, 5", out)

    def test_or_zero_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = or i32 %x, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("or i32 %x, 0", out)
        self.assertIn("ret i32 %x", out)

    def test_and_neg_one_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = and i32 %x, -1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("and i32 %x, -1", out)
        self.assertIn("ret i32 %x", out)

    def test_and_zero_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = and i32 %x, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("and i32 %x, 0", out)
        self.assertIn("ret i32 0", out)

    def test_xor_self_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = xor i32 %x, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("xor i32 %x, %x", out)
        self.assertIn("ret i32 0", out)

    def test_or_self_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = or i32 %x, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("or i32 %x, %x", out)
        self.assertIn("ret i32 %x", out)

    def test_and_self_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = and i32 %x, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("and i32 %x, %x", out)
        self.assertIn("ret i32 %x", out)

    def test_or_all_ones_becomes_all_ones(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = or i32 %x, -1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("or i32 %x, -1", out)
        self.assertIn("ret i32 -1", out)

    def test_negate_sub_swaps_operands(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 %x, %y
          %r = sub i32 0, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("sub i32 0, %a", out)
        self.assertIn("sub i32 %y, %x", out)
        self.assertIn("ret i32 %a.neg", out)

    def test_negate_mul_becomes_negative_scale(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %r = sub i32 0, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("sub i32 0, %a", out)
        self.assertIn("%a.neg = mul i32 %x, -3", out)
        self.assertIn("ret i32 %a.neg", out)

    def test_negate_shl_becomes_negative_scale(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %r = sub i32 0, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("sub i32 0, %a", out)
        self.assertIn("%a.neg = mul i32 %x, -4", out)
        self.assertIn("ret i32 %a.neg", out)

    def test_add_neg_var_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %x
          %r = add i32 %a, %y
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32 %a, %y", out)
        self.assertIn("%r = sub i32 %y, %x", out)

    def test_add_var_neg_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %x
          %r = add i32 %y, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32 %y, %a", out)
        self.assertIn("%r = sub i32 %y, %x", out)

    def test_add_shl_and_base_becomes_mul_three(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 2
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("mul i32 %x, 3", out)
        self.assertNotIn("%a = shl", out)

    def test_add_neg_value_and_const_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 %a, 1
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 1, %x", out)
        self.assertNotIn("%a = sub i32 0, %x", out)

    def test_add_const_and_neg_value_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 1, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 1, %x", out)
        self.assertNotIn("%a = sub i32 0, %x", out)

    def test_double_neg_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = sub i32 0, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = sub i32 0, %x", out)
        self.assertNotIn("%b = sub i32 0, %a", out)

    def test_add_negated_value_cancels_to_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 %x, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%a = sub i32 0, %x", out)
        self.assertNotIn("%b = add i32 %x, %a", out)

    def test_add_sub_const_and_base_cancels_to_const(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 5, %x
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 5", out)
        self.assertNotIn("%a = sub i32 5, %x", out)
        self.assertNotIn("%b = add i32 %a, %x", out)

    def test_shl_minus_base_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %b = sub i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = shl i32 %x, 1", out)
        self.assertNotIn("%b = sub i32 %a, %x", out)

    def test_sub_self_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%r = sub i32 %x, %x", out)

    def test_add_shl2_and_base_becomes_mul_five(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("mul i32 %x, 5", out)
        self.assertNotIn("%a = shl i32 %x, 2", out)

    def test_add_shl_and_one_becomes_or_disjoint(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %r = add i32 %a, 1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = or disjoint i32 %a, 1", out)
        self.assertNotIn("%r = add i32 %a, 1", out)

    def test_add_one_and_shl_becomes_or_disjoint(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %r = add i32 1, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = or disjoint i32 %a, 1", out)
        self.assertNotIn("%r = add i32 1, %a", out)

    def test_add_negative_shift_scale_and_negative_mul_collapse(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %n = sub i32 0, %x
          %a = shl i32 %n, 2
          %b = mul i32 %x, -5
          %r = add i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = mul i32 %x, -9", out)
        self.assertNotIn("%a = shl i32 %n, 2", out)
        self.assertNotIn("%b = mul i32 %x, -5", out)

    def test_sub_negative_shift_scale_and_negative_mul_collapse_to_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %n = sub i32 0, %x
          %a = shl i32 %n, 2
          %b = mul i32 %x, -5
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = shl i32 %n, 2", out)
        self.assertNotIn("%b = mul i32 %x, -5", out)

    def test_sub_mul3_and_base_becomes_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = sub i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 1", out)
        self.assertNotIn("%a = mul i32 %x, 3", out)

    def test_add_mul3_and_base_becomes_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 2", out)
        self.assertNotIn("%a = mul i32 %x, 3", out)

    def test_sub_x_negative_y_becomes_add(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %y
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("add i32 %x, %y", out)
        self.assertNotIn("%a = sub i32 0, %y", out)

    def test_sub_x_shl1_becomes_neg(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 0, %x", out)
        self.assertNotIn("%a = shl i32 %x, 1", out)

    def test_add_shl_neg_base_becomes_neg(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = shl i32 %a, 1
          %r = add i32 %b, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = sub i32 0, %x", out)
        self.assertIn("ret i32 %r", out)
        self.assertNotIn("%b = shl i32 %a, 1", out)

    def test_sub_zero_xor_not_becomes_add_one(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 0, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a.neg = add i32 %x, 1", out)
        self.assertIn("ret i32 %a.neg", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_sub_x_minus_x_minus_const_folds_to_const(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 7
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("%a = sub i32 %x, 7", out)

    def test_sub_two_adds_same_base_folds_to_const_delta(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = add i32 %x, 7
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 -4", out)
        self.assertNotIn("%a = add i32 %x, 3", out)
        self.assertNotIn("%b = add i32 %x, 7", out)

    def test_sub_two_subs_same_base_folds_to_const_delta(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 3
          %b = sub i32 %x, 7
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 4", out)
        self.assertNotIn("%a = sub i32 %x, 3", out)
        self.assertNotIn("%b = sub i32 %x, 7", out)

    def test_add_two_subs_same_base_reassociates(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 3
          %b = sub i32 %x, 7
          %r = add i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%b = shl i32 %x, 1", out)
        self.assertIn("%r = add i32 %b, -10", out)

    def test_sub_zero_sub_const_x_becomes_add_negative_const(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 7, %x
          %r = sub i32 0, %a
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a.neg = add i32 %x, -7", out)
        self.assertIn("ret i32 %a.neg", out)
        self.assertNotIn("%a = sub i32 7, %x", out)

    def test_sub_zero_sub_zero_x_then_sub_x_uses_legal_ssa_name(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%.neg = mul i32 %x, -2", out)
        self.assertIn("ret i32 %.neg", out)
        self.assertNotIn("%0.neg", out)

    def test_add_xor_not_and_one_becomes_neg(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = add i32 %a, 1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%r = sub i32 0, %x", out)
        self.assertIn("ret i32 %r", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_add_sub_const_and_const_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %b = add i32 %a, 5
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = sub i32 %x, 5", out)

    def test_add_sub_commuted_cancels_to_other_operand(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 %y, %x
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %y", out)
        self.assertNotIn("%a = sub i32 %y, %x", out)

    def test_add_sub_const_and_const_becomes_delta(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %b = add i32 %a, 3
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("add i32 %x, -2", out)
        self.assertNotIn("%a = sub i32 %x, 5", out)

    def test_sub_add_const_and_const_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %b = sub i32 %a, 5
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = add i32 %x, 5", out)

    def test_sub_add_commuted_cancels_to_other_operand(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %y, %x
          %b = sub i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %y", out)
        self.assertNotIn("%a = add i32 %y, %x", out)

    def test_sub_add_const_and_const_becomes_delta(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %b = sub i32 %a, 3
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("add i32 %x, 2", out)
        self.assertNotIn("%a = add i32 %x, 5", out)

    def test_sub_const_add_and_const_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = sub i32 10, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 7, %x", out)
        self.assertNotIn("%a = add i32 %x, 3", out)

    def test_sub_const_sub_const_and_base_becomes_add(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 7, %x
          %b = sub i32 10, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("add i32 %x, 3", out)
        self.assertNotIn("%a = sub i32 7, %x", out)

    def test_sub_const_sub_var_and_const_becomes_sub(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 7
          %b = sub i32 10, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("sub i32 17, %x", out)
        self.assertNotIn("%a = sub i32 %x, 7", out)

    def test_xor_not_not_becomes_identity(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = xor i32 %a, -1
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_xor_not_with_original_becomes_all_ones(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = xor i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 -1", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_and_not_with_original_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = and i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_or_not_with_original_becomes_all_ones(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = or i32 %a, %x
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 -1", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_add_not_with_base_plus_one_becomes_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 1
          %r = add i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)
        self.assertNotIn("%b = add i32 %x, 1", out)

    def test_add_not_with_base_plus_const_becomes_const_minus_one(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 3
          %r = add i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 2", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)
        self.assertNotIn("%b = add i32 %x, 3", out)

    def test_sub_not_with_base_plus_one_becomes_neg_two_minus_shl(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 1
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 1", out)
        self.assertIn("-2, %0", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)
        self.assertNotIn("%b = add i32 %x, 1", out)

    def test_sub_not_minus_neg_one_becomes_neg_x(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, -1
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %", out)
        self.assertIn("sub i32 0, %x", out)
        self.assertNotIn("%a = xor i32 %x, -1", out)

    def test_sub_not_minus_one_keeps_not(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, 0
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %a", out)

    def test_sub_base_plus_const_with_not_becomes_shl_plus_const(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = xor i32 %x, -1
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("shl i32 %x, 1", out)
        self.assertIn("= add i32 %", out)
        self.assertIn(", 4", out)
        self.assertNotIn("%b = xor i32 %x, -1", out)

    def test_sub_x_add_y_x_becomes_neg_y(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %y, %x
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("%b = sub i32 0, %y", out)
        self.assertNotIn("%a = add i32 %y, %x", out)

    def test_zext_i1_true_becomes_one(self):
        ir = """
        define i32 @f() {
        entry:
          %r = zext i1 true to i32
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 1", out)

    def test_sext_i1_true_becomes_neg_one(self):
        ir = """
        define i32 @f() {
        entry:
          %r = sext i1 true to i32
          ret i32 %r
        }
        """
        out, changed = instcombine_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 -1", out)

    def test_pass_end_to_end(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 0
          %b = mul i32 %a, 4
          ret i32 %b
        }
        """
        p = InstCombinePass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("shl i32", out)
        self.assertNotIn("mul i32", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, InstCombinePass(), "instcombine")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_add_self(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = add i32 %x, %x
          ret i32 %r
        }
        """)

    def test_sub_neg_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, -7
          ret i32 %r
        }
        """)

    def test_add_add_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %r = add i32 %a, 3
          ret i32 %r
        }
        """)

    def test_add_const_sub_x_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %r = add i32 3, %a
          ret i32 %r
        }
        """)

    def test_sub_sub_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %r = sub i32 %a, 3
          ret i32 %r
        }
        """)

    def test_mul_neg_one(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, -1
          ret i32 %r
        }
        """)

    def test_mul_pow2_const_lhs(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 8, %x
          ret i32 %r
        }
        """)

    def test_mul_then_add_self(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 2
          %b = add i32 %a, %x
          ret i32 %b
        }
        """)

    def test_add_neg_then_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 %a, 1
          ret i32 %b
        }
        """)

    def test_add_const_then_neg(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 1, %a
          ret i32 %b
        }
        """)

    def test_add_neg_cancel(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = add i32 %x, %a
          ret i32 %b
        }
        """)

    def test_add_sub_const_cancel(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 5, %x
          %b = add i32 %a, %x
          ret i32 %b
        }
        """)

    def test_double_neg(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = sub i32 0, %a
          ret i32 %b
        }
        """)

    def test_shl_minus_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %b = sub i32 %a, %x
          ret i32 %b
        }
        """)

    def test_sub_self(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, %x
          ret i32 %r
        }
        """)

    def test_shl2_plus_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %b = add i32 %a, %x
          ret i32 %b
        }
        """)

    def test_mul3_minus_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = sub i32 %a, %x
          ret i32 %b
        }
        """)

    def test_mul3_plus_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = add i32 %a, %x
        ret i32 %b
        }
        """)

    def test_mul3_plus_mul4(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = mul i32 %x, 4
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_mul3_plus_shl2(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = shl i32 %x, 2
          %c = add i32 %a, %b
        ret i32 %c
        }
        """)

    def test_add_shl_and_one(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %r = add i32 %a, 1
          ret i32 %r
        }
        """)

    def test_add_one_and_shl(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %r = add i32 1, %a
        ret i32 %r
        }
        """)

    def test_add_negative_shift_scale_and_negative_mul(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %n = sub i32 0, %x
          %a = shl i32 %n, 2
          %b = mul i32 %x, -5
          %r = add i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_negative_shift_scale_and_negative_mul(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %n = sub i32 0, %x
          %a = shl i32 %n, 2
          %b = mul i32 %x, -5
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_shl3_minus_mul3(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 3
          %b = mul i32 %x, 3
          %c = sub i32 %a, %b
          ret i32 %c
        }
        """)

    def test_sub_x_neg_y(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %y
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """)

    def test_sub_x_shl1(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 1
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """)

    def test_add_sub_const_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %b = add i32 %a, 5
          ret i32 %b
        }
        """)

    def test_sub_add_const_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %b = sub i32 %a, 5
          ret i32 %b
        }
        """)

    def test_add_sub_const_delta(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 5
          %b = add i32 %a, 3
          ret i32 %b
        }
        """)

    def test_sub_add_const_delta(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 5
          %b = sub i32 %a, 3
          ret i32 %b
        }
        """)

    def test_xor_not_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = xor i32 %a, -1
          ret i32 %b
        }
        """)

    def test_zext_true(self):
        self._parity("""
        define i32 @f() {
        entry:
          %r = zext i1 true to i32
          ret i32 %r
        }
        """)

    def test_or_all_ones(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %r = or i32 %x, -1
          ret i32 %r
        }
        """)

    def test_negate_sub(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 %x, %y
          %r = sub i32 0, %a
          ret i32 %r
        }
        """)

    def test_negate_mul(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %r = sub i32 0, %a
          ret i32 %r
        }
        """)

    def test_negate_shl(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 2
          %r = sub i32 0, %a
          ret i32 %r
        }
        """)

    def test_add_neg_var(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %x
          %r = add i32 %a, %y
          ret i32 %r
        }
        """)

    def test_add_var_neg(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 0, %x
          %r = add i32 %y, %a
          ret i32 %r
        }
        """)

    def test_add_shl_neg_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %b = shl i32 %a, 1
          %r = add i32 %b, %x
          ret i32 %r
        }
        """)

    def test_mul4_minus_mul3(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 4
          %b = mul i32 %x, 3
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_shl3_minus_mul7(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = shl i32 %x, 3
          %b = mul i32 %x, 7
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_mul4_plus_mulneg3(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 4
          %b = mul i32 %x, -3
          %r = add i32 %a, %b
          ret i32 %r
        }
        """)

    def test_mul3_minus_shl1(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = shl i32 %x, 1
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_zero_xor_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 0, %a
          ret i32 %r
        }
        """)

    def test_sub_neg_one_xor_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 -1, %a
          ret i32 %r
        }
        """)

    def test_sub_const_xor_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 1, %a
          ret i32 %r
        }
        """)

    def test_sub_x_xor_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """)

    def test_sub_x_add_x_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 7
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """)

    def test_sub_x_minus_x_minus_const(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 7
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """)

    def test_sub_two_adds_same_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = add i32 %x, 7
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_two_subs_same_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 3
          %b = sub i32 %x, 7
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_add_two_subs_same_base(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 %x, 3
          %b = sub i32 %x, 7
          %r = add i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_const_minus_x_then_x(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 7, %x
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """)

    def test_sub_x_minus_const_minus_x(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 7, %x
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """)

    def test_sub_x_minus_neg_one_minus_x(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 -1, %x
          %r = sub i32 %x, %a
          ret i32 %r
        }
        """)

    def test_sub_zero_sub_const_x(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 7, %x
          %r = sub i32 0, %a
          ret i32 %r
        }
        """)

    def test_sub_zero_sub_zero_x_then_sub_x(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = sub i32 0, %x
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """)

    def test_add_xor_not_and_one(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = add i32 %a, 1
          ret i32 %r
        }
        """)

    def test_add_xor_not_and_two(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = add i32 %a, 2
          ret i32 %r
        }
        """)

    def test_add_xor_not_and_zero(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = add i32 %a, 0
          ret i32 %r
        }
        """)

    def test_add_sub_commuted_cancel(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = sub i32 %y, %x
          %r = add i32 %a, %x
          ret i32 %r
        }
        """)

    def test_sub_add_commuted_cancel(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %y, %x
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """)

    def test_xor_not_with_original(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = xor i32 %a, %x
          ret i32 %b
        }
        """)

    def test_and_not_with_original(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = and i32 %a, %x
          ret i32 %b
        }
        """)

    def test_or_not_with_original(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = or i32 %a, %x
          ret i32 %b
        }
        """)

    def test_add_not_with_original(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %a, %x
          ret i32 %b
        }
        """)

    def test_add_original_with_not(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, %a
          ret i32 %b
        }
        """)

    def test_add_not_with_base_plus_one_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 1
          %r = add i32 %a, %b
          ret i32 %r
        }
        """)

    def test_add_not_with_base_plus_const_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 3
          %r = add i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_not_with_base_plus_one_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = add i32 %x, 1
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_not_minus_neg_one_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, -1
          ret i32 %r
        }
        """)

    def test_sub_not_minus_zero_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, 0
          ret i32 %r
        }
        """)

    def test_sub_base_plus_const_with_not_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = xor i32 %x, -1
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_not_with_original_keeps_upstream_shape(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """
        out, _ = run_pcc_ir_pass(ir, InstCombinePass())
        self.assertIn("%a = xor i32 %x, -1", out)
        self.assertIn("%r = sub i32 %a, %x", out)
        self.assertNotIn("shl i32 %x, 1", out)

    def test_sub_not_with_sub_zero_keeps_upstream_shape(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = sub i32 %x, 0
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, _ = run_pcc_ir_pass(ir, InstCombinePass())
        self.assertIn("%a = xor i32 %x, -1", out)
        self.assertIn("%r = sub i32 %a, %x", out)
        self.assertNotIn("shl i32 %x, 1", out)

    def test_sub_not_with_original_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %r = sub i32 %a, %x
          ret i32 %r
        }
        """)

    def test_sub_not_with_sub_zero_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %a = xor i32 %x, -1
          %b = sub i32 %x, 0
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_sub_x_add_y_x(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %y, %x
          %b = sub i32 %x, %a
          ret i32 %b
        }
        """)

    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS_IR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
