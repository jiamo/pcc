"""Tests for DSEPass (block-local subset)."""

import shutil
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.alias_analysis import AliasAnalysis
from pcc.ir_passes.dse import DSEPass, dse_module_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


def _parse(ir_text: str) -> llvm.ModuleRef:
    m = llvm.parse_assembly(ir_text)
    m.verify()
    return m


class DSETests(unittest.TestCase):
    def test_overwritten_store_removed(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          store i32 1, ptr %p
          store i32 2, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertEqual(out.count("store i32"), 1)
        self.assertIn("store i32 2", out)

    def test_store_before_load_kept(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          store i32 1, ptr %p
          %v = load i32, ptr %p
          store i32 2, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertIn("store i32 1, ptr %p", out)
        self.assertNotIn("store i32 2, ptr %p", out)

    def test_distinct_allocas_independent(self):
        ir = """
        define void @f() {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 1, ptr %p
          store i32 2, ptr %q
          store i32 3, ptr %p
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        # Both stores are dead by the function exit; DCE also cleans up allocas.
        self.assertNotIn("store i32", out)
        self.assertNotIn("alloca i32", out)

    def test_call_flushes_pending_stores(self):
        ir = """
        declare void @sink()
        define void @f() {
        entry:
          %p = alloca i32
          store i32 1, ptr %p
          call void @sink()
          store i32 2, ptr %p
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32", out)
        self.assertIn("call void @sink()", out)

    def test_trailing_dead_store_removed(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          store i32 %y, ptr %p
          ret i32 0
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32", out)
        self.assertNotIn("alloca i32", out)

    def test_store_after_last_load_removed(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          %a = load i32, ptr %p
          store i32 %y, ptr %p
          ret i32 %a
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertIn("store i32 %x, ptr %p", out)
        self.assertNotIn("store i32 %y, ptr %p", out)

    def test_volatile_store_preserved(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store volatile i32 %x, ptr %p
          store i32 %y, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertFalse(changed)
        self.assertIn("store volatile i32 %x, ptr %p", out)
        self.assertIn("store i32 %y, ptr %p", out)

    def test_single_pred_successor_overwrite_removed(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          br label %next
        next:
          store i32 %y, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 %x, ptr %p", out)
        self.assertIn("store i32 %y, ptr %p", out)

    def test_pass_integration(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          store i32 1, ptr %p
          store i32 2, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        p = DSEPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertEqual(out.count("store i32"), 1)

    def test_bitcast_alias_overwrite_removed(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          %q = bitcast ptr %p to ptr
          store i32 1, ptr %p
          store i32 2, ptr %q
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 1, ptr %p", out)
        self.assertIn("store i32 2, ptr %q", out)

    def test_zero_gep_alias_overwrite_removed(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          %q = getelementptr i32, ptr %p, i32 0
          store i32 1, ptr %p
          store i32 2, ptr %q
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 1, ptr %p", out)
        self.assertIn("store i32 2, ptr %q", out)

    def test_noalias_arg_store_does_not_block_local_dse(self):
        ir = """
        define void @f(ptr %p) {
        entry:
          %q = alloca i32
          store i32 1, ptr %q
          store i32 9, ptr %p
          store i32 2, ptr %q
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertIn("store i32 9, ptr %p", out)
        self.assertNotIn("store i32 1, ptr %q", out)
        self.assertNotIn("store i32 2, ptr %q", out)

    def test_direct_global_store_overwrite_removed(self):
        ir = """
        @g = global i32 0
        define i32 @f() {
        entry:
          store i32 1, ptr @g
          store i32 2, ptr @g
          %v = load i32, ptr @g
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 1, ptr @g", out)
        self.assertIn("store i32 2, ptr @g", out)

    def test_bitcast_global_alias_overwrite_removed(self):
        ir = """
        @g = global i32 0
        define i32 @f() {
        entry:
          %q = bitcast ptr @g to ptr
          store i32 1, ptr @g
          store i32 2, ptr %q
          %v = load i32, ptr @g
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 1, ptr @g", out)
        self.assertIn("store i32 2, ptr %q", out)

    def test_zero_gep_global_alias_overwrite_removed(self):
        ir = """
        @g = global i32 0
        define i32 @f() {
        entry:
          %q = getelementptr i32, ptr @g, i32 0
          store i32 1, ptr @g
          store i32 2, ptr %q
          %v = load i32, ptr @g
          ret i32 %v
        }
        """
        aa = AliasAnalysis(_parse(ir))
        out, changed = dse_module_text(ir, aa)
        self.assertTrue(changed)
        self.assertNotIn("store i32 1, ptr @g", out)
        self.assertIn("store i32 2, ptr %q", out)


_CORPUS = """
define i32 @overwrite_same_slot(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  store i32 %y, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}

define i32 @overwrite_then_return_const(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  store i32 %y, ptr %p
  ret i32 0
}

define i32 @store_load_store(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  %a = load i32, ptr %p
  store i32 %y, ptr %p
  ret i32 %a
}

define i32 @two_slots(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  %q = alloca i32
  store i32 %x, ptr %p
  store i32 %y, ptr %q
  store i32 7, ptr %p
  %v = load i32, ptr %q
  ret i32 %v
}

define i32 @volatile_store(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  store volatile i32 %x, ptr %p
  store i32 %y, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}

define i32 @single_pred_successor(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  store i32 %x, ptr %p
  br label %next
next:
  store i32 %y, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}

define i32 @bitcast_alias_overwrite(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  %q = bitcast ptr %p to ptr
  store i32 %x, ptr %p
  store i32 %y, ptr %q
  %v = load i32, ptr %p
  ret i32 %v
}

define i32 @zero_gep_alias_overwrite(i32 %x, i32 %y) {
entry:
  %p = alloca i32
  %q = getelementptr i32, ptr %p, i32 0
  store i32 %x, ptr %p
  store i32 %y, ptr %q
  %v = load i32, ptr %p
  ret i32 %v
}

define void @noalias_arg_store_does_not_block_local_dse(ptr %p) {
entry:
  %q = alloca i32
  store i32 1, ptr %q
  store i32 9, ptr %p
  store i32 2, ptr %q
  ret void
}

@g = global i32 0

define i32 @direct_global_store_overwrite() {
entry:
  store i32 1, ptr @g
  store i32 2, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}

define i32 @bitcast_global_alias_overwrite() {
entry:
  %q = bitcast ptr @g to ptr
  store i32 1, ptr @g
  store i32 2, ptr %q
  %v = load i32, ptr @g
  ret i32 %v
}

define i32 @zero_gep_global_alias_overwrite() {
entry:
  %q = getelementptr i32, ptr @g, i32 0
  store i32 1, ptr @g
  store i32 2, ptr %q
  %v = load i32, ptr @g
  ret i32 %v
}
"""


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, DSEPass(), "dse")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_trailing_dead_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          store i32 %y, ptr %p
          ret i32 0
        }
        """)

    def test_store_after_last_load_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          %a = load i32, ptr %p
          store i32 %y, ptr %p
          ret i32 %a
        }
        """)

    def test_two_slots_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 %x, ptr %p
          store i32 %y, ptr %q
          store i32 7, ptr %p
          %v = load i32, ptr %q
          ret i32 %v
        }
        """)

    def test_volatile_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store volatile i32 %x, ptr %p
          store i32 %y, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_single_pred_successor_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          br label %next
        next:
          store i32 %y, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_bitcast_alias_overwrite_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = bitcast ptr %p to ptr
          store i32 %x, ptr %p
          store i32 %y, ptr %q
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_zero_gep_alias_overwrite_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = getelementptr i32, ptr %p, i32 0
          store i32 %x, ptr %p
          store i32 %y, ptr %q
          %v = load i32, ptr %p
        ret i32 %v
        }
        """)

    def test_noalias_arg_store_does_not_block_local_dse_matches_upstream(self):
        self._parity("""
        define void @f(ptr %p) {
        entry:
          %q = alloca i32
          store i32 1, ptr %q
          store i32 9, ptr %p
          store i32 2, ptr %q
          ret void
        }
        """)

    def test_direct_global_store_overwrite_matches_upstream(self):
        self._parity("""
        @g = global i32 0
        define i32 @f() {
        entry:
          store i32 1, ptr @g
          store i32 2, ptr @g
          %v = load i32, ptr @g
          ret i32 %v
        }
        """)

    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
