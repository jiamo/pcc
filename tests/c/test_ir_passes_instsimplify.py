"""Parity tests for InstSimplifyPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.instsimplify import InstSimplifyPass, simplify_module_text
from pcc.ir_passes.parity import assert_ir_parity


_OPT = shutil.which("opt")


def _fold(ir: str) -> str:
    out, _ = simplify_module_text(ir)
    return out


_SCALAR_CORPUS_IR = """
define i32 @add_zero(i32 %x) {
entry:
  %r = add i32 %x, 0
  ret i32 %r
}

define i32 @sub_self(i32 %x) {
entry:
  %r = sub i32 %x, %x
  ret i32 %r
}

define i32 @xor_self(i32 %x) {
entry:
  %r = xor i32 %x, %x
  ret i32 %r
}

define i32 @mul_zero(i32 %x) {
entry:
  %r = mul i32 %x, 0
  ret i32 %r
}

define i32 @add_const() {
entry:
  %r = add i32 7, 32
  ret i32 %r
}

define i32 @or_const() {
entry:
  %r = or i32 7, 32
  ret i32 %r
}

define i32 @shift_zero_lhs(i32 %x) {
entry:
  %r = shl i32 0, %x
  ret i32 %r
}

define i32 @shift_oversize(i32 %x) {
entry:
  %r = shl i32 %x, 32
  ret i32 %r
}

define i32 @ashr_all_ones(i32 %x) {
entry:
  %r = ashr i32 -1, %x
  ret i32 %r
}

define i32 @udiv_self(i32 %x) {
entry:
  %r = udiv i32 %x, %x
  ret i32 %r
}

define i32 @udiv_zero(i32 %x) {
entry:
  %r = udiv i32 %x, 0
  ret i32 %r
}

define i32 @srem_self(i32 %x) {
entry:
  %r = srem i32 %x, %x
  ret i32 %r
}

define i1 @icmp_const() {
entry:
  %r = icmp slt i32 3, 5
  ret i1 %r
}

define i32 @select_false(i32 %x, i32 %y) {
entry:
  %r = select i1 false, i32 %x, i32 %y
  ret i32 %r
}
"""


class FoldingTests(unittest.TestCase):
    def test_add_constant_fold(self):
        ir = """
        define i32 @f() {
        entry:
          %r = add i32 7, 32
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("add i32", out)
        self.assertIn("ret i32 39", out)

    def test_sub_self_is_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = sub i32 %x, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("sub i32", out)
        self.assertIn("ret i32 0", out)

    def test_and_self_is_self(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = and i32 %x, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("and i32", out)

    def test_xor_self_is_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = xor i32 %x, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("xor i32", out)
        self.assertIn("ret i32 0", out)

    def test_or_constant_fold(self):
        ir = """
        define i32 @f() {
        entry:
          %r = or i32 7, 32
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("or i32", out)
        self.assertIn("ret i32 39", out)

    def test_shift_zero_lhs_is_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = shl i32 0, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("shl i32", out)
        self.assertIn("ret i32 0", out)

    def test_ashr_all_ones_is_all_ones(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = ashr i32 -1, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("ashr i32", out)
        self.assertIn("ret i32 -1", out)

    def test_overshift_becomes_poison(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = shl i32 %x, 32
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("shl i32", out)
        self.assertIn("ret i32 poison", out)

    def test_udiv_self_is_one(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = udiv i32 %x, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("udiv i32", out)
        self.assertIn("ret i32 1", out)

    def test_udiv_by_zero_becomes_poison(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = udiv i32 %x, 0
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("udiv i32", out)
        self.assertIn("ret i32 poison", out)

    def test_srem_self_is_zero(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = srem i32 %x, %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("srem i32", out)
        self.assertIn("ret i32 0", out)

    def test_icmp_same_val_eq(self):
        ir = """
        define i1 @f(i32 %x) {
        entry:
          %r = icmp eq i32 %x, %x
          ret i1 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("icmp", out)
        self.assertIn("ret i1 true", out)

    def test_icmp_const_fold(self):
        ir = """
        define i1 @f() {
        entry:
          %r = icmp slt i32 3, 5
          ret i1 %r
        }
        """
        out = _fold(ir)
        self.assertIn("ret i1 true", out)

    def test_select_true(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %r = select i1 true, i32 %x, i32 %y
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("select", out)
        self.assertIn("ret i32 %x", out)

    def test_select_same_branches(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %r = select i1 %c, i32 %x, i32 %x
          ret i32 %r
        }
        """
        out = _fold(ir)
        self.assertNotIn("select", out)
        self.assertIn("ret i32 %x", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, InstSimplifyPass(), "instsimplify")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_add_zero(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = add i32 %x, 0
        ret i32 %r }
        """)

    def test_sub_self(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = sub i32 %x, %x
        ret i32 %r }
        """)

    def test_xor_self(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = xor i32 %x, %x
        ret i32 %r }
        """)

    def test_mul_zero(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = mul i32 %x, 0
        ret i32 %r }
        """)

    def test_add_constant_fold(self):
        self._parity("""
        define i32 @f() { entry: %r = add i32 7, 32
        ret i32 %r }
        """)

    def test_or_constant_fold(self):
        self._parity("""
        define i32 @f() { entry: %r = or i32 7, 32
        ret i32 %r }
        """)

    def test_shift_zero_lhs(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = shl i32 0, %x
        ret i32 %r }
        """)

    def test_shift_oversize_becomes_poison(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = shl i32 %x, 32
        ret i32 %r }
        """)

    def test_ashr_all_ones(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = ashr i32 -1, %x
        ret i32 %r }
        """)

    def test_udiv_self(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = udiv i32 %x, %x
        ret i32 %r }
        """)

    def test_udiv_by_zero(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = udiv i32 %x, 0
        ret i32 %r }
        """)

    def test_srem_self(self):
        self._parity("""
        define i32 @f(i32 %x) { entry: %r = srem i32 %x, %x
        ret i32 %r }
        """)

    def test_scalar_integer_corpus_matches_upstream(self):
        self._parity(_SCALAR_CORPUS_IR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
