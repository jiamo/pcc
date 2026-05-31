"""Tests for ReassociatePass (subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.reassociate import ReassociatePass, reassociate_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


_CORPUS_IR = """
define i32 @const_tail(i32 %x) {
entry:
  %a = add i32 %x, 3
  %b = add i32 %a, 4
  ret i32 %b
}

define i32 @const_lhs(i32 %x) {
entry:
  %a = add i32 3, %x
  %b = add i32 4, %a
  ret i32 %b
}

define i32 @mul_tail(i32 %x) {
entry:
  %a = mul i32 %x, 3
  %b = mul i32 %a, 4
  ret i32 %b
}

define i32 @mixed_add(i32 %x, i32 %y) {
entry:
  %a = add i32 %x, 3
  %b = add i32 %y, 4
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @three_consts(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 2
  %c = add i32 %b, 3
  ret i32 %c
}

define i32 @and_tail(i32 %x) {
entry:
  %a = and i32 %x, 7
  %b = and i32 %a, 3
  ret i32 %b
}
"""


class ReassociateTests(unittest.TestCase):
    def test_constant_tail_folded(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 3
          %b = add i32 %a, 4
          ret i32 %b
        }
        """
        out, changed = reassociate_text(ir)
        self.assertTrue(changed)
        self.assertIn("%b = add i32 %x, 7", out)
        self.assertNotIn("%a = add i32 %x, 3", out)

    def test_constant_on_lhs_is_sunk_and_folded(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 3, %x
          %b = add i32 4, %a
          ret i32 %b
        }
        """
        out, changed = reassociate_text(ir)
        self.assertTrue(changed)
        self.assertIn("%b = add i32 %x, 7", out)

    def test_non_constant_chain_untouched(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %a, %x
          ret i32 %b
        }
        """
        _, changed = reassociate_text(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %a, 2
          ret i32 %b
        }
        """
        p = ReassociatePass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("add i32 %x, 3", out)

    def test_mul_constant_chain_folded(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = mul i32 %x, 3
          %b = mul i32 %a, 4
          ret i32 %b
        }
        """
        out, changed = reassociate_text(ir)
        self.assertTrue(changed)
        self.assertIn("%b = mul i32 %x, 12", out)
        self.assertNotIn("%a = mul i32 %x, 3", out)

    def test_sibling_add_chains_merge_constants(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, 3
          %b = add i32 %y, 4
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = reassociate_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 7", out)
        self.assertIn("%c = add i32 %a, %y", out)
        self.assertNotIn("%b = add i32 %y, 4", out)

    def test_local_cleanup_keeps_module_context(self):
        ir = """
        @.class.Exc = external global ptr

        define ptr @helper(ptr %p) {
        entry:
          ret ptr %p
        }

        define i32 @f(i32 %x) {
        entry:
          %cls = load ptr, ptr @.class.Exc
          %obj = call ptr @helper(ptr %cls)
          %a = add i32 %x, 3
          %b = add i32 %a, 4
          ret i32 %b
        }
        """
        out, changed = reassociate_text(ir)
        self.assertTrue(changed)
        self.assertIn("@.class.Exc", out)
        self.assertIn("@helper", out)
        self.assertIn("%b = add i32 %x, 7", out)
        llvm.parse_assembly(out).verify()


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, ReassociatePass(), "reassociate")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_constant_tail_fold(self):
        self._parity("""
        define i32 @f(i32 %x) { entry:
          %a = add i32 %x, 3
          %b = add i32 %a, 4
          ret i32 %b
        }
        """)

    def test_constant_lhs_fold(self):
        self._parity("""
        define i32 @f(i32 %x) { entry:
          %a = add i32 3, %x
          %b = add i32 4, %a
          ret i32 %b
        }
        """)

    def test_mul_tail_fold(self):
        self._parity("""
        define i32 @f(i32 %x) { entry:
          %a = mul i32 %x, 3
          %b = mul i32 %a, 4
          ret i32 %b
        }
        """)

    def test_mixed_add_fold(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) { entry:
          %a = add i32 %x, 3
          %b = add i32 %y, 4
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_three_const_chain(self):
        self._parity("""
        define i32 @f(i32 %x) { entry:
          %a = add i32 %x, 1
          %b = add i32 %a, 2
          %c = add i32 %b, 3
          ret i32 %c
        }
        """)

    def test_and_chain(self):
        self._parity("""
        define i32 @f(i32 %x) { entry:
          %a = and i32 %x, 7
          %b = and i32 %a, 3
          ret i32 %b
        }
        """)

    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS_IR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
