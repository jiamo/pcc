"""Tests for NewGVNPass (subset)."""

import pytest
import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.dominator_tree import compute_dominator_tree
from pcc.ir_passes.newgvn import NewGVNPass, newgvn_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


def _run_newgvn_text(ir: str):
    module = llvm.parse_assembly(ir)
    module.verify()
    fn_doms = {}
    for fn in module.functions:
        if fn.is_declaration:
            continue
        dom = compute_dominator_tree(fn)
        fn_doms[fn.name] = {block: dom.dominators(block) for block in dom.all_blocks()}
    return newgvn_text(ir, fn_doms)


class NewGVNTests(unittest.TestCase):
    def test_redundant_binop_then_sub_self_folds_to_zero(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %x, %y
          %c = sub i32 %a, %b
          ret i32 %c
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)

    def test_redundant_icmp_then_and_self_folds_to_single_value(self):
        ir = """
        define i1 @f(i32 %x, i32 %y) {
        entry:
          %a = icmp eq i32 %x, %y
          %b = icmp eq i32 %x, %y
          %c = and i1 %a, %b
          ret i1 %c
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i1 %a", out)
        self.assertNotIn("%b = icmp eq i32 %x, %y", out)
        self.assertNotIn("%c = and i1 %a, %a", out)

    def test_phi_same_constant_incomings_folded(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %left, label %right
        left:
          br label %join
        right:
          br label %join
        join:
          %p = phi i32 [ 7, %left ], [ 7, %right ]
          %r = add i32 %p, 1
          ret i32 %r
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%p = phi i32", out)
        self.assertIn("ret i32 8", out)

    def test_phi_same_ssa_incomings_folded(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %left, label %right
        left:
          br label %join
        right:
          br label %join
        join:
          %p = phi i32 [ %x, %left ], [ %x, %right ]
          %r = add i32 %p, 1
          ret i32 %r
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%p = phi i32", out)
        self.assertIn("%r = add i32 %x, 1", out)

    def test_bitcast_alias_load_eliminated(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_noalias_alloca_store_does_not_block_load_elimination(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = alloca i32
          %a = load i32, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr @g
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr @g", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_bitcast_alias_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = bitcast ptr @g to ptr
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_zero_gep_alias_load_eliminated(self):
        ir = """
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr i32, ptr @g, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_multi_zero_gep_alias_load_eliminated(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_global_multi_zero_gep_alias_load_eliminated(self):
        ir = """
        @g = global { i32 } { i32 0 }
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr { i32 }, ptr @g, i32 0, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %q", out)
        self.assertIn("%s = add i32 %a, %a", out)

    def test_different_typed_loads_not_merged(self):
        ir = """
        define i64 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i64, ptr %p
          ret i64 %b
        else:
          ret i64 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%a = load i32, ptr %p", out)
        self.assertIn("%b = load i64, ptr %p", out)

    def test_redundant_select_then_sub_self_folds_to_zero(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = select i1 %c, i32 %x, i32 %y
          %b = select i1 %c, i32 %x, i32 %y
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%b = select i1 %c, i32 %x, i32 %y", out)

    def test_redundant_zext_then_sub_self_folds_to_zero(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          %a = zext i1 %c to i32
          %b = zext i1 %c to i32
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("%b = zext i1 %c to i32", out)

    def test_redundant_gep_is_eliminated(self):
        ir = """
        define ptr @f(ptr %p) {
        entry:
          %a = getelementptr i32, ptr %p, i32 0
          %b = getelementptr i32, ptr %p, i32 0
          ret ptr %b
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret ptr %p", out)
        self.assertNotIn("%b = getelementptr i32, ptr %p, i32 0", out)

    def test_out_of_order_blocks_still_use_dominating_binop(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = add i32 %x, %y
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = add i32 %x, %y
          br label %use
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = add i32 %x, %y", out)
        self.assertIn("%r = add i32 %a, %a", out)

    def test_out_of_order_blocks_still_use_dominating_load(self):
        ir = """
        define i32 @f(i1 %c, ptr %p) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = load i32, ptr %p
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = load i32, ptr %p
          br label %use
        else:
          ret i32 0
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = load i32, ptr %p", out)
        self.assertIn("%r = add i32 %a, %a", out)

    def test_out_of_order_blocks_still_use_dominating_icmp(self):
        ir = """
        define i1 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = icmp eq i32 %x, %y
          %r = and i1 %a, %b
          ret i1 %r
        pre:
          %a = icmp eq i32 %x, %y
          br label %use
        else:
          ret i1 false
        }
        """
        out, changed = _run_newgvn_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%b = icmp eq i32 %x, %y", out)
        self.assertIn("ret i1 %a", out)

    def test_pass_end_to_end(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %left, label %right
        left:
          br label %join
        right:
          br label %join
        join:
          %p = phi i32 [ 7, %left ], [ 7, %right ]
          %r = add i32 %p, 1
          ret i32 %r
        }
        """
        out, _ = run_pcc_ir_pass(ir, NewGVNPass())
        self.assertIn("ret i32 8", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, NewGVNPass(), "newgvn")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_phi_same_constant_incomings_match_upstream(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %left, label %right
        left:
          br label %join
        right:
          br label %join
        join:
          %p = phi i32 [ 7, %left ], [ 7, %right ]
          %r = add i32 %p, 1
          ret i32 %r
        }
        """)

    def test_redundant_binop_then_sub_self_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %a = add i32 %x, %y
          %b = add i32 %x, %y
          %c = sub i32 %a, %b
          ret i32 %c
        }
        """)

    def test_redundant_icmp_then_and_self_matches_upstream(self):
        self._parity("""
        define i1 @f(i32 %x, i32 %y) {
        entry:
          %a = icmp eq i32 %x, %y
          %b = icmp eq i32 %x, %y
          %c = and i1 %a, %b
          ret i1 %c
        }
        """)

    def test_bitcast_alias_load_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = bitcast ptr %p to ptr
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_noalias_alloca_store_does_not_block_load_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = alloca i32
          %a = load i32, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %p
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_global_load_matches_upstream(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr @g
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_global_bitcast_alias_load_matches_upstream(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = bitcast ptr @g to ptr
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_global_zero_gep_alias_load_matches_upstream(self):
        self._parity("""
        @g = global i32 0
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr i32, ptr @g, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_multi_zero_gep_alias_load_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_global_multi_zero_gep_alias_load_matches_upstream(self):
        self._parity("""
        @g = global { i32 } { i32 0 }
        define i32 @f(i1 %c) {
        entry:
          %q = getelementptr { i32 }, ptr @g, i32 0, i32 0
          %a = load i32, ptr @g
          br i1 %c, label %then, label %else
        then:
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        else:
          ret i32 0
        }
        """)

    def test_different_typed_loads_do_not_merge(self):
        self._parity("""
        define i64 @f(i1 %c, ptr %p) {
        entry:
          %a = load i32, ptr %p
          br i1 %c, label %then, label %else
        then:
          %b = load i64, ptr %p
          ret i64 %b
        else:
          ret i64 0
        }
        """)

    def test_redundant_select_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %a = select i1 %c, i32 %x, i32 %y
          %b = select i1 %c, i32 %x, i32 %y
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_redundant_zext_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          %a = zext i1 %c to i32
          %b = zext i1 %c to i32
          %r = sub i32 %a, %b
          ret i32 %r
        }
        """)

    def test_redundant_gep_matches_upstream(self):
        self._parity("""
        define ptr @f(ptr %p) {
        entry:
          %a = getelementptr i32, ptr %p, i32 0
          %b = getelementptr i32, ptr %p, i32 0
          ret ptr %b
        }
        """)

    def test_out_of_order_blocks_use_true_dominator(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = add i32 %x, %y
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = add i32 %x, %y
          br label %use
        else:
          ret i32 0
        }
        """)

    def test_out_of_order_load_blocks_use_true_dominator(self):
        self._parity("""
        define i32 @f(i1 %c, ptr %p) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = load i32, ptr %p
          %r = add i32 %a, %b
          ret i32 %r
        pre:
          %a = load i32, ptr %p
          br label %use
        else:
          ret i32 0
        }
        """)

    def test_out_of_order_icmp_blocks_use_true_dominator(self):
        self._parity("""
        define i1 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %else, label %pre
        use:
          %b = icmp eq i32 %x, %y
          %r = and i1 %a, %b
          ret i1 %r
        pre:
          %a = icmp eq i32 %x, %y
          br label %use
        else:
          ret i1 false
        }
        """)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
