"""Smoke tests for Phase 4/5 migration-scaffold passes.

These passes are labelled ``migration-scaffold`` in the registry:
they have the pass-manager plumbing and preserve IR invariants
correctly, but the transform itself is deferred pending analysis
infrastructure (LazyValueInfo, constraint solver, ...). The tests
verify that each pass can be instantiated, run on a representative
IR snippet, and produce a parseable module back.
"""

import unittest

from pcc.ir_passes.constraint_elimination import ConstraintEliminationPass
from pcc.ir_passes.correlated_propagation import CorrelatedValuePropagationPass
from pcc.ir_passes.early_cse import EarlyCSEPass, early_cse_text
from pcc.ir_passes.jump_threading import JumpThreadingPass
from pcc.ir_passes.parity import run_pcc_ir_pass


_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  ret i32 %b
}
"""


class ScaffoldPassesTests(unittest.TestCase):
    def test_correlated_propagation_roundtrip(self):
        out, _ = run_pcc_ir_pass(_IR, CorrelatedValuePropagationPass())
        self.assertIn("define", out)

    def test_constraint_elimination_roundtrip(self):
        out, _ = run_pcc_ir_pass(_IR, ConstraintEliminationPass())
        self.assertIn("define", out)

    def test_jump_threading_roundtrip(self):
        out, _ = run_pcc_ir_pass(_IR, JumpThreadingPass())
        self.assertIn("define", out)


class EarlyCSETests(unittest.TestCase):
    def test_duplicate_binop_removed(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %x, 1
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertEqual(out.count("add i32 %x, 1"), 1)

    def test_different_args_not_cse(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %y, 1
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        _, changed = early_cse_text(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %x, 1
          ret i32 %b
        }
        """
        p = EarlyCSEPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertEqual(out.count("add i32"), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
