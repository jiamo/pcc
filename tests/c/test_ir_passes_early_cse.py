"""Tests for EarlyCSEPass (subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.early_cse import EarlyCSEPass, early_cse_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


_CORPUS_IR = """
define i32 @repeat_add(i32 %x, i32 %y) {
entry:
  %a = add i32 %x, %y
  %b = add i32 %x, %y
  %c = add i32 %a, %b
  ret i32 %c
}

define i32 @repeat_load_same_ptr(ptr %p) {
entry:
  %a = load i32, ptr %p
  %b = load i32, ptr %p
  %c = add i32 %a, %b
  ret i32 %c
}

define i1 @repeat_icmp(i32 %x, i32 %y) {
entry:
  %a = icmp slt i32 %x, %y
  %b = icmp slt i32 %x, %y
  %c = and i1 %a, %b
  ret i1 %c
}

define i32 @commuted_add(i32 %x, i32 %y) {
entry:
  %a = add i32 %x, %y
  %b = add i32 %y, %x
  %c = add i32 %a, %b
  ret i32 %c
}
"""


class EarlyCSETests(unittest.TestCase):
    def test_repeat_add_eliminated(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %x, %y
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %x, %y", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_repeat_load_same_ptr_eliminated(self):
        ir = """
        define i32 @f(ptr %p) {
        entry:
          %a = load i32, ptr %p
          %b = load i32, ptr %p
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_repeat_load_through_bitcast_alias_eliminated(self):
        ir = """
        define i32 @f(ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          %b = load i32, ptr %q
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_commuted_add_eliminated(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %y, %x", out)
        self.assertIn("%c = add i32 %a, %a", out)

    def test_repeat_icmp_then_and_simplifies(self):
        ir = """
        define i1 @f(i32 %x, i32 %y) {
        entry:
          %a = icmp slt i32 %x, %y
          %b = icmp slt i32 %x, %y
          %c = and i1 %a, %b
          ret i1 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertEqual(out.count("icmp slt i32 %x, %y"), 1)
        self.assertIn("ret i1 %a", out)

    def test_call_flushes_load_table(self):
        ir = """
        declare void @sink()
        define i32 @f(ptr %p) {
        entry:
          %a = load i32, ptr %p
          call void @sink()
          %b = load i32, ptr %p
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        _, changed = early_cse_text(ir)
        self.assertFalse(changed)

    def test_load_after_store_uses_stored_value(self):
        ir = """
        define i32 @f(ptr %p, i32 %x) {
        entry:
          %a = load i32, ptr %p
          store i32 %x, ptr %p
          %b = load i32, ptr %p
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = early_cse_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%c = add i32 %a, %x", out)

    def test_comment_labels_reset_local_value_tables(self):
        ir = """
        define i32 @f(ptr %p, i1 %c) {
        entry:
          br i1 %c, label %left, label %right
        left: ; preds = %entry
          %a = load i32, ptr %p
          ret i32 %a
        right: ; preds = %entry
          %b = load i32, ptr %p
          ret i32 %b
        }
        """
        out, changed = early_cse_text(ir)
        self.assertFalse(changed)
        llvm.parse_assembly(out).verify()

    def test_pass_end_to_end(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """
        p = EarlyCSEPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("%c = add i32 %a, %a", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, EarlyCSEPass(), "early-cse")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_repeat_add(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %x, %y
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_repeat_load_same_ptr(self):
        self._parity("""
        define i32 @f(ptr %p) {
        entry:
          %a = load i32, ptr %p
          %b = load i32, ptr %p
          %c = add i32 %a, %b
        ret i32 %c
        }
        """)

    def test_repeat_load_through_bitcast_alias(self):
        self._parity("""
        define i32 @f(ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          %b = load i32, ptr %q
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_load_after_store_uses_stored_value(self):
        self._parity("""
        define i32 @f(ptr %p, i32 %x) {
        entry:
          %a = load i32, ptr %p
          store i32 %x, ptr %p
          %b = load i32, ptr %p
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_repeat_icmp(self):
        self._parity("""
        define i1 @f(i32 %x, i32 %y) {
        entry:
          %a = icmp slt i32 %x, %y
          %b = icmp slt i32 %x, %y
          %c = and i1 %a, %b
          ret i1 %c
        }
        """)

    def test_commuted_add(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %y, %x
          %c = add i32 %a, %b
          ret i32 %c
        }
        """)

    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS_IR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
