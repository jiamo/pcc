"""Real-transform tests for CorrelatedValuePropagationPass."""

import unittest

from pcc.ir_passes.correlated_propagation import (
    CorrelatedValuePropagationPass,
    _cvp_module,
)
from pcc.ir_passes.parity import run_pcc_ir_pass

import llvmlite.binding as llvm


def _mod(ir: str) -> llvm.ModuleRef:
    m = llvm.parse_assembly(ir)
    m.verify()
    return m


class CVPTests(unittest.TestCase):
    def test_eq_branch_substitutes_var(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %c = icmp eq i32 %x, 42
          br i1 %c, label %then, label %else
        then:
          %r = add i32 %x, 0
          ret i32 %r
        else:
          ret i32 %x
        }
        """
        new_text, changed = _cvp_module(_mod(ir))
        self.assertTrue(changed)
        # In 'then', %x becomes 42 for the add.
        self.assertIn("add i32 42", new_text)
        # In 'else', %x is not substituted.
        then_section_end = new_text.find("else:")
        else_section = new_text[then_section_end:]
        self.assertIn("ret i32 %x", else_section)

    def test_rhs_const_canonical(self):
        # Same fact, but compare written as `icmp eq 42, %x`.
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %c = icmp eq i32 42, %x
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          ret i32 %x
        }
        """
        new_text, changed = _cvp_module(_mod(ir))
        self.assertTrue(changed)
        # The 'then' return should get 42.
        then_start = new_text.find("then:")
        else_start = new_text.find("else:")
        then_section = new_text[then_start:else_start]
        self.assertIn("ret i32 42", then_section)

    def test_non_constant_compare_leaves_alone(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %c = icmp eq i32 %x, %y
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          ret i32 %y
        }
        """
        _, changed = _cvp_module(_mod(ir))
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %c = icmp eq i32 %x, 5
          br i1 %c, label %then, label %else
        then:
          %r = mul i32 %x, %x
          ret i32 %r
        else:
          ret i32 0
        }
        """
        out, _ = run_pcc_ir_pass(ir, CorrelatedValuePropagationPass())
        self.assertIn("mul i32 5, 5", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
