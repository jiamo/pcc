"""Parity tests for SimplifyCFGPass (subset)."""

import shutil
import unittest

from pcc.ir_passes.parity import assert_ir_parity
from pcc.ir_passes.simplifycfg import SimplifyCFGPass, simplify_cfg_text


_OPT = shutil.which("opt")


_CORPUS_IR = """
define i32 @branch_on_true() {
entry:
  br i1 true, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}

define i32 @branch_on_false() {
entry:
  br i1 false, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}

define i32 @branch_on_icmp_const() {
entry:
  %c = icmp eq i32 4, 4
  br i1 %c, label %then, label %else
then:
  ret i32 1
else:
  ret i32 0
}

define i32 @empty_forwarder(i1 %c) {
entry:
  br i1 %c, label %mid, label %else
mid:
  br label %then
then:
  ret i32 1
else:
  ret i32 0
}

define i32 @phi_forwarder(i1 %c, i32 %x) {
entry:
  br i1 %c, label %mid, label %else
mid:
  br label %then
then:
  %p = phi i32 [ %x, %mid ], [ 0, %else ]
  ret i32 %p
else:
  br label %then
}

define i32 @identical_returns(i1 %c) {
entry:
  br i1 %c, label %then, label %else
then:
  ret i32 7
else:
  ret i32 7
}

define i32 @phi_add_ret(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %merge
e:
  br label %merge
merge:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  %a = add i32 %p, 1
  ret i32 %a
}

define i32 @phi_ret_with_pure_then_value(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %else
then:
  %a = add i32 %x, 1
  br label %merge
else:
  br label %merge
merge:
  %p = phi i32 [ %a, %then ], [ %x, %else ]
  ret i32 %p
}

define i32 @pure_ret_on_both_sides(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %then, label %else
then:
  %a = add i32 %x, 1
  ret i32 %a
else:
  %b = add i32 %y, 2
  ret i32 %b
}

define i32 @pure_then_direct_merge_phi(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %then, label %merge
then:
  %a = add i32 %x, 1
  br label %merge
merge:
  %p = phi i32 [ %a, %then ], [ %y, %entry ]
  ret i32 %p
}

define i32 @phi_br_pure_ret_after_merge(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %m
e:
  br label %m
m:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  br label %r
r:
  %a = add i32 %p, 1
  ret i32 %a
}

define i32 @two_pure_ops_then_phi_single_use_ret(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %then, label %else
then:
  %a = add i32 %x, 1
  br label %merge
else:
  %b = sub i32 %y, 2
  br label %merge
merge:
  %p = phi i32 [ %a, %then ], [ %b, %else ]
  %m = mul i32 %p, 3
  ret i32 %m
}

define i32 @one_pure_then_direct_merge_phi_single_use_ret(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %then, label %merge
then:
  %a = add i32 %x, 1
  br label %merge
merge:
  %p = phi i32 [ %a, %then ], [ %y, %entry ]
  %m = mul i32 %p, 3
  ret i32 %m
}

define i32 @one_pure_then_direct_merge_phi_two_use_chain_ret(i1 %c, i32 %x, i32 %y) {
entry:
  br i1 %c, label %then, label %merge
then:
  %a = add i32 %x, 1
  br label %merge
merge:
  %p = phi i32 [ %a, %then ], [ %y, %entry ]
  %m = mul i32 %p, 3
  %n = add i32 %m, 4
  ret i32 %n
}

define i32 @dead_arm_direct_merge_phi_single_use_ret(i1 %c, i32 %x) {
entry:
  br i1 %c, label %dead, label %merge
dead:
  unreachable
merge:
  %p = phi i32 [ %x, %entry ]
  %m = mul i32 %p, 3
  ret i32 %m
}

define i32 @dead_arm_pure_then_merge_phi_single_use_ret(i1 %c, i32 %x) {
entry:
  br i1 %c, label %then, label %dead
then:
  %a = add i32 %x, 1
  br label %merge
dead:
  unreachable
merge:
  %p = phi i32 [ %a, %then ]
  %m = mul i32 %p, 3
  ret i32 %m
}
"""


class FoldingTests(unittest.TestCase):
    def test_br_true_collapses_to_return(self):
        ir = """
        define i32 @f() {
        entry:
          br i1 true, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 1", out)
        self.assertNotIn("br i1 true", out)
        self.assertNotIn("else:", out)

    def test_branch_to_two_returns_becomes_select(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("select i1 %c, i32 1, i32 0", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_branch_to_unreachable_becomes_assume_and_ret(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %then, label %else
        then:
          unreachable
        else:
          ret i32 0
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("declare void @llvm.assume(i1)", out)
        self.assertIn("%assume.not = xor i1 %c, true", out)
        self.assertIn("call void @llvm.assume(i1 %assume.not)", out)
        self.assertIn("ret i32 0", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)
        self.assertNotIn("unreachable", out)
        self.assertNotIn("br i1 %c", out)

    def test_identical_returns_collapse_to_single_return(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 7
        else:
          ret i32 7
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("select", out)
        self.assertNotIn("then:", out)

    def test_empty_forwarder_is_threaded(self):
        ir = """
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %mid, label %else
        mid:
          br label %then
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("select i1 %c, i32 1, i32 0", out)
        self.assertNotIn("mid:", out)

    def test_phi_merge_becomes_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %mid, label %else
        mid:
          br label %then
        then:
          %p = phi i32 [ %x, %mid ], [ 0, %else ]
          ret i32 %p
        else:
          br label %then
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("select i1 %c, i32 %x, i32 0", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("mid:", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_pure_then_ret_and_direct_else_ret_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          ret i32 %x
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %x", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_phi_add_ret_becomes_select_then_add(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %merge
        e:
          br label %merge
        merge:
          %p = phi i32 [ %x, %t ], [ 0, %e ]
          %a = add i32 %p, 1
          ret i32 %a
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("select i1 %c, i32 %x, i32 0", out)
        self.assertIn("add i32 %p, 1", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("t:", out)
        self.assertNotIn("e:", out)
        self.assertNotIn("merge:", out)

    def test_phi_ret_with_pure_then_value_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %x, %else ]
          ret i32 %p
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %x", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)
        self.assertNotIn("merge:", out)

    def test_const_branch_prunes_dead_phi_incoming(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          br i1 true, label %mid, label %else
        mid:
          br label %merge
        else:
          br label %merge
        merge:
          %p = phi i32 [ %x, %mid ], [ %y, %else ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("%else", out)
        self.assertIn("ret i32 %m", out)

    def test_ret_vs_single_incoming_phi_ret_uses_common_ret_shape(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          ret i32 %p
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %p, %merge ], [ %x, %entry ]", out)
        self.assertIn("%p = phi i32 [ %y, %entry ]", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_pure_ret_on_both_sides_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          %b = add i32 %y, 2
          ret i32 %b
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("%b = add i32 %y, 2", out)
        self.assertIn("select i1 %c, i32 %a, i32 %b", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_identical_pure_ret_on_both_sides_becomes_single_op_and_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          %b = add i32 %x, 1
          ret i32 %b
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("ret i32 %a", out)
        self.assertNotIn("select i1", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_identical_two_step_pure_ret_on_both_sides_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %b
        else:
          %u = add i32 %x, 1
          %v = mul i32 %u, 3
          ret i32 %v
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("%b = mul i32 %a, 3", out)
        self.assertIn("%u = add i32 %x, 1", out)
        self.assertIn("%v = mul i32 %u, 3", out)
        self.assertIn("select i1 %c, i32 %b, i32 %v", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)

    def test_identical_three_step_pure_returns_use_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          %c1 = add i32 %b, 3
          ret i32 %c1
        else:
          %d = add i32 %x, 1
          %e = mul i32 %d, 2
          %f1 = add i32 %e, 3
          ret i32 %f1
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %c1, %then ], [ %f1, %else ]", out)
        self.assertNotIn("select i1 %c", out)

    def test_branch_to_unreachable_with_pure_chain_becomes_assume_and_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %dead
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          ret i32 %b
        dead:
          unreachable
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("call void @llvm.assume(i1 %c)", out)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("%b = mul i32 %a, 2", out)
        self.assertIn("ret i32 %b", out)
        self.assertNotIn("unreachable", out)

    def test_dead_arm_direct_merge_phi_single_use_ret_becomes_assume_then_mul(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %dead, label %merge
        dead:
          unreachable
        merge:
          %p = phi i32 [ %x, %entry ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%assume.not = xor i1 %c, true", out)
        self.assertIn("call void @llvm.assume(i1 %assume.not)", out)
        self.assertIn("mul i32 %x, 3", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("dead:", out)
        self.assertNotIn("merge:", out)
        self.assertNotIn("unreachable", out)

    def test_dead_arm_pure_then_merge_phi_single_use_ret_becomes_assume_add_mul(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %dead
        then:
          %a = add i32 %x, 1
          br label %merge
        dead:
          unreachable
        merge:
          %p = phi i32 [ %a, %then ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("call void @llvm.assume(i1 %c)", out)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("mul i32 %a, 3", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("dead:", out)
        self.assertNotIn("merge:", out)
        self.assertNotIn("unreachable", out)

    def test_pure_ret_vs_direct_merge_phi_chain_becomes_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          ret i32 %a
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          ret i32 %n
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %a, %then ], [ %n, %merge ]", out)
        self.assertNotIn("ret i32 %a", out)

    def test_direct_ret_vs_direct_merge_three_step_phi_chain_branches_entry_to_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          ret i32 %x
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          %o = xor i32 %n, 7
          ret i32 %o
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("entry:\n  br i1 %c, label %common.ret, label %merge", out)
        self.assertIn("%common.ret.op = phi i32 [ %o, %merge ], [ %x, %entry ]", out)
        self.assertNotIn("then:\n  br label %common.ret", out)

    def test_pure_chain_ret_vs_direct_merge_same_chain_uses_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %b
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = add i32 %p, 1
          %n = mul i32 %m, 3
          ret i32 %n
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %b, %then ], [ %n, %merge ]", out)
        self.assertIn("then:\n  %a = add i32 %x, 1\n  %b = mul i32 %a, 3\n  br label %common.ret", out)
        self.assertIn("merge:\n  %p = phi i32 [ %y, %entry ]", out)
        self.assertIn("br label %common.ret", out)

    def test_direct_ret_vs_direct_merge_phi_two_identical_uses_uses_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          ret i32 %x
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = add i32 %p, 1
          %n = add i32 %p, 1
          %s = add i32 %m, %n
          ret i32 %s
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("entry:\n  br i1 %c, label %common.ret, label %merge", out)
        self.assertIn("%common.ret.op = phi i32 [ %s, %merge ], [ %x, %entry ]", out)
        self.assertIn("merge:\n  %p = phi i32 [ %y, %entry ]", out)
        self.assertIn("%m = add i32 %p, 1", out)
        self.assertIn("%n = add i32 %p, 1", out)
        self.assertIn("%s = add i32 %m, %n", out)
        self.assertNotIn("then:\n  ret i32 %x", out)

    def test_pure_then_direct_merge_phi_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          ret i32 %p
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %y", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("merge:", out)

    def test_phi_br_pure_ret_after_merge_becomes_select_then_add(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %m
        e:
          br label %m
        m:
          %p = phi i32 [ %x, %t ], [ 0, %e ]
          br label %r
        r:
          %a = add i32 %p, 1
          ret i32 %a
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("select i1 %c, i32 %x, i32 0", out)
        self.assertIn("add i32 %p, 1", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("br label %m", out)
        self.assertNotIn("m:", out)
        self.assertNotIn("r:", out)

    def test_two_pure_ops_then_phi_single_use_ret_becomes_select_then_mul(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          %b = sub i32 %y, 2
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %b, %else ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("%b = sub i32 %y, 2", out)
        self.assertIn("select i1 %c, i32 %a, i32 %b", out)
        self.assertIn("mul i32 %p, 3", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)
        self.assertNotIn("merge:", out)

    def test_one_pure_ret_one_merge_phi_ret_becomes_hoisted_select(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          ret i32 %p
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %y", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)
        self.assertNotIn("merge:", out)

    def test_one_pure_then_direct_merge_phi_single_use_ret_becomes_select_then_mul(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %y", out)
        self.assertIn("mul i32 %p, 3", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("merge:", out)

    def test_one_pure_then_direct_merge_phi_two_step_chain_ret_becomes_select_then_chain(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          ret i32 %n
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%a = add i32 %x, 1", out)
        self.assertIn("select i1 %c, i32 %a, i32 %y", out)
        self.assertIn("mul i32 %p, 3", out)
        self.assertIn("add i32 %m, 4", out)
        self.assertNotIn("phi i32", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("merge:", out)

    def test_one_pure_ret_one_merge_phi_single_use_ret_becomes_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("entry:\n  br i1 %c, label %then, label %merge", out)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %a, %then ], [ %m, %merge ]", out)
        self.assertIn("merge:\n  %p = phi i32 [ %y, %entry ]", out)
        self.assertNotIn("else:", out)

    def test_pure_ret_vs_single_incoming_phi_chain_with_reused_else_local_becomes_select_then_chain(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 0
          ret i32 %t
        else:
          %e = add i32 %y, 1
          br label %merge
        merge:
          %p = phi i32 [ %e, %else ]
          %a = add i32 %p, %p
          %b = xor i32 %a, 7
          ret i32 %b
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("%t = add i32 %x, 0", out)
        self.assertIn("%e = add i32 %y, 1", out)
        self.assertIn("%a = add i32 %e, %e", out)
        self.assertIn("%b = xor i32 %a, 7", out)
        self.assertIn("select i1 %c, i32 %t, i32 %b", out)
        self.assertNotIn("then:", out)
        self.assertNotIn("else:", out)
        self.assertNotIn("merge:", out)

    def test_pure_ret_vs_direct_phi_chain_with_reused_phi_becomes_common_ret(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 1
          ret i32 %t
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %a = add i32 %p, 1
          %b = add i32 %a, %p
          ret i32 %b
        }
        """
        out, changed = simplify_cfg_text(ir)
        self.assertTrue(changed)
        self.assertIn("entry:\n  br i1 %c, label %then, label %merge", out)
        self.assertIn("common.ret:", out)
        self.assertIn("%common.ret.op = phi i32 [ %t, %then ], [ %b, %merge ]", out)
        self.assertIn("merge:\n  %p = phi i32 [ %y, %entry ]", out)
        self.assertNotIn("select i1 %c", out)
        self.assertNotIn("else:", out)


@unittest.skipUnless(_OPT, "requires opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, SimplifyCFGPass(), "simplifycfg")
        self.assertFalse(
            report.diff.missing_functions or report.diff.extra_functions,
            f"function set mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )
        self.assertIsNone(
            report.diff.global_count_diff,
            f"global mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )
        self.assertEqual(
            report.diff.function_diffs,
            [],
            f"structural mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_branch_on_true(self):
        self._parity("""
        define i32 @f() {
        entry:
          br i1 true, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """)

    def test_branch_on_false(self):
        self._parity("""
        define i32 @f() {
        entry:
          br i1 false, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """)

    def test_branch_to_unreachable_becomes_assume_like_upstream(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %then, label %else
        then:
          unreachable
        else:
          ret i32 0
        }
        """)

    def test_branch_on_icmp_const(self):
        self._parity("""
        define i32 @f() {
        entry:
          %c = icmp eq i32 4, 4
          br i1 %c, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """)

    def test_const_branch_prunes_dead_phi_incoming_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x, i32 %y) {
        entry:
          br i1 true, label %mid, label %else
        mid:
          br label %merge
        else:
          br label %merge
        merge:
          %p = phi i32 [ %x, %mid ], [ %y, %else ]
          %m = mul i32 %p, 3
        ret i32 %m
        }
        """)

    def test_ret_vs_single_incoming_phi_ret_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          ret i32 %p
        }
        """)

    def test_direct_ret_vs_single_incoming_phi_ret_with_else_local_value_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          %a = add i32 %y, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %else ]
          ret i32 %p
        }
        """)

    def test_pure_ret_vs_direct_phi_chain_with_reused_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 1
          ret i32 %t
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %a = add i32 %p, 1
          %b = add i32 %a, %p
          ret i32 %b
        }
        """)

    def test_direct_ret_vs_single_incoming_phi_chain_with_const_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          br label %merge
        merge:
          %p = phi i32 [ 5, %else ]
          %q = add i32 %p, 1
          %r = mul i32 %q, 3
          ret i32 %r
        }
        """)

    def test_direct_ret_vs_single_incoming_phi_ret_with_else_local_two_step_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          %a = add i32 %y, 1
          %b = mul i32 %a, 7
          br label %merge
        merge:
          %p = phi i32 [ %b, %else ]
          ret i32 %p
        }
        """)

    def test_pure_ret_vs_single_incoming_phi_chain_with_else_local_value_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 2
          ret i32 %t
        else:
          %a = add i32 %y, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %else ]
          %q = mul i32 %p, 3
          ret i32 %q
        }
        """)

    def test_pure_ret_vs_single_incoming_phi_chain_with_else_local_two_step_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 2
          ret i32 %t
        else:
          %a = add i32 %y, 1
          %b = mul i32 %a, 7
          br label %merge
        merge:
          %p = phi i32 [ %b, %else ]
          %q = mul i32 %p, 3
          ret i32 %q
        }
        """)

    def test_pure_ret_vs_single_incoming_phi_chain_four_step_else_local_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 2
          ret i32 %t
        else:
          %a = add i32 %y, 1
          %b = mul i32 %a, 7
          %c1 = add i32 %b, 9
          %d = mul i32 %c1, 5
          br label %merge
        merge:
          %p = phi i32 [ %d, %else ]
          %q = mul i32 %p, 3
          ret i32 %q
        }
        """)

    def test_pure_ret_vs_single_incoming_phi_chain_with_reused_else_local_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %t = add i32 %x, 0
          ret i32 %t
        else:
          %e = add i32 %y, 1
          br label %merge
        merge:
          %p = phi i32 [ %e, %else ]
          %a = add i32 %p, %p
          %b = xor i32 %a, 7
          ret i32 %b
        }
        """)

    def test_direct_ret_vs_single_incoming_phi_chain_with_reused_else_local_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          %e = add i32 %y, 1
          br label %merge
        merge:
          %p = phi i32 [ %e, %else ]
          %a = add i32 %p, %p
          %b = xor i32 %a, 7
          ret i32 %b
        }
        """)

    def test_identical_four_step_pure_returns_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          %d = add i32 %b, 9
          %e = mul i32 %d, 5
          ret i32 %e
        else:
          %u = add i32 %x, 1
          %v = mul i32 %u, 3
          %w = add i32 %v, 9
          %z = mul i32 %w, 5
          ret i32 %z
        }
        """)

    def test_identical_three_step_pure_returns_match_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          %c1 = add i32 %b, 3
          ret i32 %c1
        else:
          %d = add i32 %x, 1
          %e = mul i32 %d, 2
          %f1 = add i32 %e, 3
          ret i32 %f1
        }
        """)

    def test_branch_to_unreachable_with_pure_chain_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %dead
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          ret i32 %b
        dead:
          unreachable
        }
        """)

    def test_pure_ret_vs_direct_merge_phi_chain_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          ret i32 %a
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          ret i32 %n
        }
        """)

    def test_empty_forwarder(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %mid, label %else
        mid:
          br label %then
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """)

    def test_phi_forwarder(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %mid, label %else
        mid:
          br label %then
        then:
          %p = phi i32 [ %x, %mid ], [ 0, %else ]
          ret i32 %p
        else:
          br label %then
        }
        """)

    def test_identical_returns(self):
        self._parity("""
        define i32 @f(i1 %c) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 7
        else:
          ret i32 7
        }
        """)

    def test_identical_pure_returns(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          %b = add i32 %x, 1
          ret i32 %b
        }
        """)

    def test_identical_two_step_pure_returns(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %b
        else:
          %u = add i32 %x, 1
          %v = mul i32 %u, 3
          ret i32 %v
        }
        """)

    def test_phi_add_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %merge
        e:
          br label %merge
        merge:
          %p = phi i32 [ %x, %t ], [ 0, %e ]
          %a = add i32 %p, 1
        ret i32 %a
        }
        """)

    def test_phi_ret_with_pure_then_value(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %x, %else ]
        ret i32 %p
        }
        """)

    def test_pure_then_ret_and_direct_else_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          ret i32 %x
        }
        """)

    def test_pure_ret_on_both_sides(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          %b = add i32 %y, 2
          ret i32 %b
        }
        """)

    def test_dead_arm_direct_merge_phi_single_use_ret_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %dead, label %merge
        dead:
          unreachable
        merge:
          %p = phi i32 [ %x, %entry ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """)

    def test_dead_arm_pure_then_merge_phi_single_use_ret_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %then, label %dead
        then:
          %a = add i32 %x, 1
          br label %merge
        dead:
          unreachable
        merge:
          %p = phi i32 [ %a, %then ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """)

    def test_pure_then_direct_merge_phi(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          ret i32 %p
        }
        """)

    def test_phi_br_pure_ret_after_merge(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          br i1 %c, label %t, label %e
        t:
          br label %m
        e:
          br label %m
        m:
          %p = phi i32 [ %x, %t ], [ 0, %e ]
          br label %r
        r:
          %a = add i32 %p, 1
          ret i32 %a
        }
        """)

    def test_two_pure_ops_then_phi_single_use_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          br label %merge
        else:
          %b = sub i32 %y, 2
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %b, %else ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """)

    def test_one_pure_ret_one_merge_phi_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          ret i32 %p
        }
        """)

    def test_one_pure_then_direct_merge_phi_single_use_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """)

    def test_one_pure_ret_one_merge_phi_single_use_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          ret i32 %a
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %m = mul i32 %p, 3
          ret i32 %m
        }
        """)

    def test_one_pure_chain_ret_one_merge_phi_chain_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %b
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %q = add i32 %p, 1
          %r = mul i32 %q, 3
          ret i32 %r
        }
        """)

    def test_direct_ret_one_merge_phi_chain_ret_branches_entry_to_common_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %else
        then:
          ret i32 %x
        else:
          br label %merge
        merge:
          %p = phi i32 [ %y, %else ]
          %q = add i32 %p, 1
          %r = mul i32 %q, 3
          ret i32 %r
        }
        """)

    def test_direct_ret_vs_direct_merge_three_step_phi_chain_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          ret i32 %x
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          %o = xor i32 %n, 7
          ret i32 %o
        }
        """)

    def test_pure_chain_ret_vs_direct_merge_same_chain_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          %b = mul i32 %a, 3
          ret i32 %b
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = add i32 %p, 1
          %n = mul i32 %m, 3
          ret i32 %n
        }
        """)

    def test_direct_ret_vs_direct_merge_phi_two_identical_uses_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          ret i32 %x
        merge:
          %p = phi i32 [ %y, %entry ]
          %m = add i32 %p, 1
          %n = add i32 %p, 1
          %s = add i32 %m, %n
          ret i32 %s
        }
        """)


    def test_module_corpus_matches_upstream(self):
        self._parity(_CORPUS_IR)

    def test_one_pure_then_direct_merge_phi_two_step_chain_ret(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          br i1 %c, label %then, label %merge
        then:
          %a = add i32 %x, 1
          br label %merge
        merge:
          %p = phi i32 [ %a, %then ], [ %y, %entry ]
          %m = mul i32 %p, 3
          %n = add i32 %m, 4
          ret i32 %n
        }
        """)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
