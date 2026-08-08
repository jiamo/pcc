"""Real-transform tests for Mem2RegPass (subset)."""

import pytest

import unittest

from pcc.ir_passes.mem2reg import Mem2RegPass, mem2reg_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class Mem2RegTests(unittest.TestCase):
    def test_single_store_single_load_promoted(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("ret i32 %x", out)

    def test_multiple_loads_dominated_by_single_store(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          br label %use
        use:
          %v1 = load i32, ptr %p
          %v2 = load i32, ptr %p
          %r = add i32 %v1, %v2
          ret i32 %r
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertIn("add i32 %x, %x", out)

    def test_load_only_alloca_promoted_to_undef(self):
        ir = """
        define i32 @f() {
        entry:
          %p = alloca i32
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("load", out)
        self.assertIn("ret i32 undef", out)

    def test_load_before_single_store_promoted_to_undef(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          %v = load i32, ptr %p
          store i32 %x, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("ret i32 undef", out)

    def test_single_block_multiple_stores_and_loads_promoted_linearly(self):
        ir = """
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          %v1 = load i32, ptr %p
          store i32 %b, ptr %p
          %v2 = load i32, ptr %p
          %sum = add i32 %v1, %v2
          ret i32 %sum
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%sum = add i32 %a, %b", out)
        self.assertIn("ret i32 %sum", out)

    def test_last_store_in_single_store_block_feeds_dominated_loads(self):
        ir = """
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          store i32 %b, ptr %p
          br label %use
        use:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("ret i32 %b", out)

    def test_single_store_block_renames_loads_before_and_after_last_store(self):
        ir = """
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          %v0 = load i32, ptr %p
          store i32 %b, ptr %p
          br label %use
        use:
          %v1 = load i32, ptr %p
          %sum = add i32 %v0, %v1
          ret i32 %sum
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%sum = add i32 %a, %b", out)
        self.assertIn("ret i32 %sum", out)

    def test_multi_store_bails_out(self):
        ir = """
        define i32 @f(i32 %c, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          %cond = icmp ne i32 %c, 0
          br i1 %cond, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %a, %left ], [ %b, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_escaped_alloca_not_promoted(self):
        ir = """
        declare void @sink(ptr)
        define void @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          call void @sink(ptr %p)
          ret void
        }
        """
        _, changed = mem2reg_module(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, _ = run_pcc_ir_pass(ir, Mem2RegPass())
        self.assertNotIn("alloca", out)
        self.assertIn("ret i32 %x", out)

    def test_branch_join_phi_inserted(self):
        ir = """
        define i32 @f(i32 %c, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          %cond = icmp ne i32 %c, 0
          br i1 %cond, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          %v1 = load i32, ptr %p
          %v2 = load i32, ptr %p
          %sum = add i32 %v1, %v2
          ret i32 %sum
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertIn("%p.0 = phi i32 [ %a, %left ], [ %b, %right ]", out)
        self.assertIn("%sum = add i32 %p.0, %p.0", out)
        self.assertNotIn("load i32, ptr %p", out)

    def test_default_store_feeds_missing_phi_edge(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %join
        else:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %x, %then ], [ 0, %else ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_intermediate_predecessor_uses_nearest_dominating_store(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %mid
        mid:
          br label %join
        else:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %x, %mid ], [ 0, %else ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_direct_join_missing_edge_uses_undef_phi_incoming(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %then, label %join
        then:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %x, %then ], [ undef, %entry ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_branch_join_missing_one_store_uses_undef_phi_incoming(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join
        right:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %x, %left ], [ undef, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_same_value_incoming_phi_folds_to_direct_value(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join
        right:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertNotIn("phi i32", out)
        self.assertIn("ret i32 %x", out)

    def test_last_store_in_predecessor_block_feeds_phi(self):
        ir = """
        define i32 @f(i1 %c, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          store i32 %b, ptr %p
          br label %join
        right:
          store i32 %a, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %b, %left ], [ %a, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_phi_join_feeds_loads_in_multiple_dominated_blocks(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          br i1 %d, label %use1, label %use2
        use1:
          %v1 = load i32, ptr %p
          ret i32 %v1
        use2:
          %v2 = load i32, ptr %p
          ret i32 %v2
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %a, %left ], [ %b, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_shallow_join_phi_is_chosen_over_deeper_use_block(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          br i1 %d, label %mid, label %use
        mid:
          br label %use
        use:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %a, %left ], [ %b, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_three_way_phi_orders_deeper_predecessor_first(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br i1 %d, label %left.then, label %join
        left.then:
          store i32 %b, ptr %p
          br label %join
        right:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %b, %left.then ], [ %a, %left ], [ 0, %right ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_entry_initialized_edge_keeps_original_order_before_local_store_edge(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %join, label %then
        then:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ 0, %entry ], [ %x, %then ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_second_join_after_later_store_gets_second_phi(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join1
        right:
          store i32 %b, ptr %p
          br label %join1
        join1:
          br i1 %d, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %join2
        else:
          br label %join2
        join2:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %a, %left ], [ %b, %right ]", out)
        self.assertIn("%p.1 = phi i32 [ %x, %then ], [ %p.0, %else ]", out)
        self.assertIn("ret i32 %p.1", out)

    def test_second_join_prefers_local_store_edge_before_inherited_phi_edge(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %a, label %b
        a:
          store i32 %x, ptr %p
          br label %join1
        b:
          br label %join1
        join1:
          br i1 %d, label %c1, label %join2
        c1:
          store i32 9, ptr %p
          br label %join2
        join2:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ %x, %a ], [ undef, %b ]", out)
        self.assertIn("%p.1 = phi i32 [ 9, %c1 ], [ %p.0, %join1 ]", out)
        self.assertIn("ret i32 %p.1", out)

    def test_loop_header_phi_is_inserted_before_existing_phi(self):
        ir = """
        define i32 @f(i32 %n) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %body ]
          %v = load i32, ptr %p
          %c = icmp slt i32 %i, %n
          br i1 %c, label %body, label %exit
        body:
          %inc = add i32 %i, 1
          store i32 %inc, ptr %p
          br label %loop
        exit:
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ 0, %entry ], [ %inc, %body ]", out)
        self.assertIn("%i = phi i32 [ 0, %entry ], [ %inc, %body ]", out)
        self.assertLess(out.index("%p.0 = phi i32"), out.index("%i = phi i32"))

    def test_loop_header_load_before_store_gets_loop_phi(self):
        ir = """
        define i32 @f(i32 %n) {
        entry:
          %p = alloca i32
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
          %v = load i32, ptr %p
          store i32 %i, ptr %p
          %inc = add i32 %i, 1
          %c = icmp slt i32 %i, %n
          br i1 %c, label %loop, label %exit
        exit:
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("store", out)
        self.assertNotIn("load", out)
        self.assertIn("%p.0 = phi i32 [ undef, %entry ], [ %i, %loop ]", out)
        self.assertIn("ret i32 %p.0", out)

    def test_multi_alloca_loop_phi_orders_later_alloca_first(self):
        ir = """
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 0, ptr %p
          store i32 1, ptr %q
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %body ]
          br i1 %c, label %body, label %exit
        body:
          store i32 %x, ptr %p
          %a = load i32, ptr %q
          store i32 %y, ptr %q
          %inc = add i32 %i, 1
          %cont = icmp slt i32 %inc, 2
          br i1 %cont, label %loop, label %exit
        exit:
          %v1 = load i32, ptr %p
          %v2 = load i32, ptr %q
          %sum = add i32 %v1, %v2
          ret i32 %sum
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertLess(out.index("%q.0 = phi i32"), out.index("%p.0 = phi i32"))
        self.assertLess(out.index("%q.1 = phi i32"), out.index("%p.1 = phi i32"))

    def test_multi_alloca_nested_join_preserves_cfg_order_for_non_undef_inherited_phi(self):
        ir = """
        define i32 @f(i1 %c, i1 %d, i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 0, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join1
        right:
          store i32 %y, ptr %q
          br label %join1
        join1:
          br i1 %d, label %join2, label %else
        else:
          store i32 9, ptr %p
          br label %join2
        join2:
          %a = load i32, ptr %p
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertIn("%q.0 = phi i32 [ 1, %left ], [ %y, %right ]", out)
        self.assertIn("%p.0 = phi i32 [ %x, %left ], [ 0, %right ]", out)
        self.assertIn("%p.1 = phi i32 [ %p.0, %join1 ], [ 9, %else ]", out)

    def test_loop_backedge_and_second_join_prefers_local_latch_before_inherited_loop(self):
        ir = """
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
          br i1 %c, label %body, label %join
        body:
          store i32 %x, ptr %p
          br label %latch
        latch:
          %inc = add i32 %i, 1
          %cmp = icmp slt i32 %inc, 2
          br i1 %cmp, label %loop, label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertIn("%p.1 = phi i32 [ %x, %latch ], [ %p.0, %loop ]", out)

    def test_nested_loop_short_circuit_phi_placeholder_gets_completed(self):
        ir = """
        define i64 @f(i1 %c, i64 %n) {
        entry:
          %idx = alloca i64
          store i64 0, ptr %idx
          br label %for.cond
        for.cond:
          %cur = load i64, ptr %idx
          %cmp = icmp slt i64 %cur, %n
          br i1 %cmp, label %for.body, label %for.end
        for.body:
          br label %while.cond
        while.cond:
          br i1 %c, label %bool.rhs, label %bool.end
        bool.rhs:
          br label %bool.end
        bool.end:
          %and = phi i1 [ 0, %while.cond ], [ %c, %bool.rhs ]
          br i1 %and, label %while.body, label %while.end
        while.body:
          br label %while.cond
        while.end:
          br label %for.step
        for.step:
          %old = load i64, ptr %idx
          %next = add i64 %old, 1
          store i64 %next, ptr %idx
          br label %for.cond
        for.end:
          ret i64 %cur
        }
        """
        out, changed = mem2reg_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("alloca", out)
        self.assertNotIn("load i64, ptr %idx", out)
        self.assertNotIn("store i64", out)
        self.assertIn("%idx.0 = phi i64 [ 0, %entry ], [ %next, %for.step ]", out)
        self.assertIn("%next = add i64 %idx.0, 1", out)
        self.assertIn("ret i64 %idx.0", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, Mem2RegPass(), "mem2reg")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_single_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          store i32 %x, ptr %p
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_branch_join_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %c, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          %cond = icmp ne i32 %c, 0
          br i1 %cond, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_load_only_alloca_matches_upstream(self):
        self._parity("""
        define i32 @f() {
        entry:
          %p = alloca i32
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_load_before_single_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %x) {
        entry:
          %p = alloca i32
          %v = load i32, ptr %p
          store i32 %x, ptr %p
          ret i32 %v
        }
        """)

    def test_single_block_multiple_stores_and_loads_match_upstream(self):
        self._parity("""
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          %v1 = load i32, ptr %p
          store i32 %b, ptr %p
          %v2 = load i32, ptr %p
          %sum = add i32 %v1, %v2
          ret i32 %sum
        }
        """)

    def test_last_store_in_single_store_block_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          store i32 %b, ptr %p
          br label %use
        use:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_single_store_block_renames_loads_before_and_after_last_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 %a, ptr %p
          %v0 = load i32, ptr %p
          store i32 %b, ptr %p
          br label %use
        use:
          %v1 = load i32, ptr %p
          %sum = add i32 %v0, %v1
          ret i32 %sum
        }
        """)

    def test_default_store_branch_join_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %join
        else:
          br label %join
        join:
          %v = load i32, ptr %p
        ret i32 %v
        }
        """)

    def test_intermediate_predecessor_uses_nearest_dominating_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %mid
        mid:
          br label %join
        else:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_direct_join_missing_edge_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %then, label %join
        then:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
        ret i32 %v
        }
        """)

    def test_branch_join_missing_one_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join
        right:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_same_value_incoming_phi_folds_like_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join
        right:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_last_store_in_predecessor_block_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          store i32 %b, ptr %p
          br label %join
        right:
          store i32 %a, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_phi_join_feeds_loads_in_multiple_dominated_blocks_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          br i1 %d, label %use1, label %use2
        use1:
          %v1 = load i32, ptr %p
          ret i32 %v1
        use2:
          %v2 = load i32, ptr %p
          ret i32 %v2
        }
        """)

    def test_shallow_join_phi_is_chosen_over_deeper_use_block_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join
        right:
          store i32 %b, ptr %p
          br label %join
        join:
          br i1 %d, label %mid, label %use
        mid:
          br label %use
        use:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_three_way_phi_orders_deeper_predecessor_first_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br i1 %d, label %left.then, label %join
        left.then:
          store i32 %b, ptr %p
          br label %join
        right:
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_entry_initialized_edge_keeps_original_order_before_local_store_edge_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br i1 %c, label %join, label %then
        then:
          store i32 %x, ptr %p
          br label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_second_join_after_later_store_gets_second_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %a, i32 %b, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %left, label %right
        left:
          store i32 %a, ptr %p
          br label %join1
        right:
          store i32 %b, ptr %p
          br label %join1
        join1:
          br i1 %d, label %then, label %else
        then:
          store i32 %x, ptr %p
          br label %join2
        else:
          br label %join2
        join2:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_second_join_prefers_local_store_edge_before_inherited_phi_edge_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %a, label %b
        a:
          store i32 %x, ptr %p
          br label %join1
        b:
          br label %join1
        join1:
          br i1 %d, label %c1, label %join2
        c1:
          store i32 9, ptr %p
          br label %join2
        join2:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_loop_header_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %n) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %body ]
          %v = load i32, ptr %p
          %c = icmp slt i32 %i, %n
          br i1 %c, label %body, label %exit
        body:
          %inc = add i32 %i, 1
          store i32 %inc, ptr %p
          br label %loop
        exit:
          ret i32 %v
        }
        """)

    def test_loop_header_load_before_store_matches_upstream(self):
        self._parity("""
        define i32 @f(i32 %n) {
        entry:
          %p = alloca i32
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
          %v = load i32, ptr %p
          store i32 %i, ptr %p
          %inc = add i32 %i, 1
          %c = icmp slt i32 %i, %n
          br i1 %c, label %loop, label %exit
        exit:
          ret i32 %v
        }
        """)

    def test_multi_alloca_loop_phi_order_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 0, ptr %p
          store i32 1, ptr %q
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %body ]
          br i1 %c, label %body, label %exit
        body:
          store i32 %x, ptr %p
          %a = load i32, ptr %q
          store i32 %y, ptr %q
          %inc = add i32 %i, 1
          %cont = icmp slt i32 %inc, 2
          br i1 %cont, label %loop, label %exit
        exit:
          %v1 = load i32, ptr %p
          %v2 = load i32, ptr %q
          %sum = add i32 %v1, %v2
          ret i32 %sum
        }
        """)

    def test_multi_alloca_nested_join_preserves_cfg_order_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i1 %d, i32 %x, i32 %y) {
        entry:
          %p = alloca i32
          %q = alloca i32
          store i32 0, ptr %p
          store i32 1, ptr %q
          br i1 %c, label %left, label %right
        left:
          store i32 %x, ptr %p
          br label %join1
        right:
          store i32 %y, ptr %q
          br label %join1
        join1:
          br i1 %d, label %join2, label %else
        else:
          store i32 9, ptr %p
          br label %join2
        join2:
          %a = load i32, ptr %p
          %b = load i32, ptr %q
          %s = add i32 %a, %b
          ret i32 %s
        }
        """)

    def test_loop_join_with_missing_store_edge_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %n, i32 %x) {
        entry:
          %p = alloca i32
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %join ]
          %d = icmp slt i32 %i, %n
          br i1 %d, label %a, label %exit
        a:
          br i1 %c, label %s, label %join
        s:
          store i32 %x, ptr %p
          br label %join
        join:
          %inc = add i32 %i, 1
          %v = load i32, ptr %p
          br label %loop
        exit:
          ret i32 0
        }
        """)

    def test_loop_backedge_and_second_join_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %x) {
        entry:
          %p = alloca i32
          store i32 0, ptr %p
          br label %loop
        loop:
          %i = phi i32 [ 0, %entry ], [ %inc, %latch ]
          br i1 %c, label %body, label %join
        body:
          store i32 %x, ptr %p
          br label %latch
        latch:
          %inc = add i32 %i, 1
          %cmp = icmp slt i32 %inc, 2
          br i1 %cmp, label %loop, label %join
        join:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)

    def test_preloop_join_value_survives_loop_without_extra_self_phi_matches_upstream(self):
        self._parity("""
        define i32 @f(i1 %c, i32 %n, i32 %x) {
        entry:
          %p = alloca i32
          br i1 %c, label %a, label %b
        a:
          store i32 %x, ptr %p
          br label %pre
        b:
          br label %pre
        pre:
          br label %loop
        loop:
          %i = phi i32 [ 0, %pre ], [ %inc, %join ]
          %cmp = icmp slt i32 %i, %n
          br i1 %cmp, label %body, label %exit
        body:
          br label %join
        join:
          %inc = add i32 %i, 1
          br label %loop
        exit:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
