"""Real-transform tests for LICMPass (subset)."""

import pytest

import unittest

from pcc.ir_passes.licm import LICMPass, licm_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class LICMTests(unittest.TestCase):
    def test_invariant_add_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %inv = add i32 %a, %b
  call void @sink(i32 %inv)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        # `%inv = add i32 %a, %b` should appear in the entry
        # (preheader) before the branch to header.
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%inv = add i32 %a, %b", entry_section)

    def test_variant_not_hoisted(self):
        ir = """
        declare void @sink(i32)
        define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %use = add i32 %i, 1
  call void @sink(i32 %use)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        # %use depends on %i which is a phi in the loop, so it stays.
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertIn("%use = add i32 %i, 1", body_section)

    def test_invariant_alloca_load_is_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %p, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%ld = load i32, ptr %p, align 4", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%ld = load i32, ptr %p, align 4", body_section)

    def test_noalias_arg_store_does_not_block_alloca_load_hoist(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, ptr %q, i32 %x) {
entry:
  %p = alloca i32, align 4
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  store i32 7, ptr %q, align 4
  %ld = load i32, ptr %p, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%ld = load i32, ptr %p, align 4", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%ld = load i32, ptr %p, align 4", body_section)

    def test_zero_gep_alloca_load_is_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  %q = getelementptr i32, ptr %p, i32 0
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %q, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%ld = load i32, ptr %q, align 4", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%ld = load i32, ptr %q, align 4", body_section)

    def test_multi_zero_gep_exit_load_chain_hoists_gep_like_upstream(self):
        ir = """
define i32 @f(i32 %n, ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
  %ld = load i32, ptr %q
  %sum = add i32 %i, %ld
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%q = getelementptr { i32 }, ptr %p, i32 0, i32 0", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%q = getelementptr { i32 }, ptr %p, i32 0, i32 0", body_section)
        exit_section = out[out.find("exit:"):]
        self.assertNotIn("%q.lcssa = phi ptr [ %q, %body ]", exit_section)
        self.assertIn("%ld.le = load i32, ptr %q", exit_section)
        self.assertIn("%sum.le = add i32 %i.lcssa, %ld.le", exit_section)

    def test_bitcast_alloca_load_is_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  %q = bitcast ptr %p to ptr
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %q, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%ld = load i32, ptr %q, align 4", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%ld = load i32, ptr %q, align 4", body_section)

    def test_freeze_is_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %y = freeze i32 %x
  call void @sink(i32 %y)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("%y = freeze i32 %x", entry_section)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%y = freeze i32 %x", body_section)

    def test_dead_variant_pure_instruction_is_removed(self):
        ir = """
define i32 @f(i32 %n) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %dead = add i32 %i, 1
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("%dead = add i32 %i, 1", out)
        self.assertIn("%i.next = add i32 %i, 1", out)

    def test_dead_invariant_chain_is_removed_not_hoisted(self):
        ir = """
define i32 @f(i32 %n, i32 %x, i32 %y) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %inv = add i32 %x, %y
  %dead = mul i32 %inv, 3
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("%inv = add i32 %x, %y", out)
        self.assertNotIn("%dead = mul i32 %inv, 3", out)
        self.assertIn("%i.next = add i32 %i, 1", out)

    def test_single_exit_add_is_sunk_with_lcssa_phi(self):
        ir = """
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %inv = add i32 %a, %b
  %sum = add i32 %i, %inv
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
        """
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        self.assertIn("%inv = add i32 %a, %b", out[out.find("entry:"):out.find("header:")])
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%sum = add i32 %i, %inv", body_section)
        exit_section = out[out.find("exit:"):]
        self.assertIn("%i.lcssa = phi i32 [ %i, %body ]", exit_section)
        self.assertIn("%sum.le = add i32 %i.lcssa, %inv", exit_section)
        self.assertIn("ret i32 %sum.le", exit_section)

    def test_single_exit_load_add_chain_is_sunk(self):
        ir = """
define i32 @f(i32 %n, ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %ld = load i32, ptr %p
  %sum = add i32 %i, %ld
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        body_section = out[out.find("body:"):out.find("exit:")]
        self.assertNotIn("%ld = load i32, ptr %p", body_section)
        self.assertNotIn("%sum = add i32 %i, %ld", body_section)
        exit_section = out[out.find("exit:"):]
        self.assertIn("%i.lcssa = phi i32 [ %i, %body ]", exit_section)
        self.assertIn("%ld.le = load i32, ptr %p", exit_section)
        self.assertIn("%sum.le = add i32 %i.lcssa, %ld.le", exit_section)

    def test_exit_only_invariant_chain_sinks_instead_of_hoisting(self):
        ir = """
define i32 @f(i32 %x, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %inv = add i32 %x, 1
  %sum = add i32 %inv, %i
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %sum
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("loop:")]
        self.assertNotIn("%inv = add i32 %x, 1", entry_section)
        exit_section = out[out.find("exit:"):]
        self.assertIn("%i.lcssa = phi i32 [ %i, %loop ]", exit_section)
        self.assertIn("%inv.le = add i32 %x, 1", exit_section)
        self.assertIn("%sum.le = add i32 %inv.le, %i.lcssa", exit_section)

    def test_exiting_header_invariant_with_body_use_is_hoisted(self):
        ir = """
declare void @sink(i32)
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %inv = add i32 %a, %b
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %body, label %exit
body:
  %sum = add i32 %inv, %i
  call void @sink(i32 %sum)
  %inc = add i32 %i, 1
  br label %loop
exit:
  ret i32 0
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        entry_section = out[out.find("entry:"):out.find("loop:")]
        self.assertIn("%inv = add i32 %a, %b", entry_section)
        loop_section = out[out.find("loop:"):out.find("body:")]
        self.assertNotIn("%inv = add i32 %a, %b", loop_section)

    def test_existing_exit_use_gets_lcssa_phi(self):
        ir = """
define i32 @f(i32 %x, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, %n
  br i1 %cmp, label %loop, label %exit
exit:
  %r = add i32 %inc, %x
  ret i32 %r
}
"""
        out, changed = licm_module(ir)
        self.assertTrue(changed)
        exit_section = out[out.find("exit:"):]
        self.assertIn("%inc.lcssa = phi i32 [ %inc, %loop ]", exit_section)
        self.assertIn("%r = add i32 %inc.lcssa, %x", exit_section)

    def test_pass_end_to_end_uses_sunk_result(self):
        ir = """
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %inv = add i32 %a, %b
  %sum = add i32 %i, %inv
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
"""
        p = LICMPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("%sum.le = add i32 %i.lcssa, %inv", out)
        self.assertIn("ret i32 %sum.le", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, LICMPass(), "licm")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_single_exit_add_sink_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %inv = add i32 %a, %b
  %sum = add i32 %i, %inv
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
""")

    def test_single_exit_load_chain_sink_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %n, ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %ld = load i32, ptr %p
  %sum = add i32 %i, %ld
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
""")

    def test_exit_only_invariant_chain_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %inv = add i32 %x, 1
  %sum = add i32 %inv, %i
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, %n
  br i1 %cmp, label %loop, label %exit
exit:
  ret i32 %sum
}
""")

    def test_exiting_header_invariant_with_body_use_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, i32 %a, i32 %b) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %body ]
  %inv = add i32 %a, %b
  %cmp = icmp slt i32 %i, %n
  br i1 %cmp, label %body, label %exit
body:
  %sum = add i32 %inv, %i
  call void @sink(i32 %sum)
  %inc = add i32 %i, 1
  br label %loop
exit:
  ret i32 0
}
""")

    def test_existing_exit_use_gets_lcssa_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x, i32 %n) {
entry:
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %inc, %loop ]
  %inc = add i32 %i, 1
  %cmp = icmp slt i32 %inc, %n
  br i1 %cmp, label %loop, label %exit
exit:
  %r = add i32 %inc, %x
  ret i32 %r
}
""")

    def test_dead_invariant_chain_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %n, i32 %x, i32 %y) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %inv = add i32 %x, %y
  %dead = mul i32 %inv, 3
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_invariant_alloca_load_hoists_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %p, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_noalias_arg_store_does_not_block_alloca_load_hoist_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, ptr %q, i32 %x) {
entry:
  %p = alloca i32, align 4
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  store i32 7, ptr %q, align 4
  %ld = load i32, ptr %p, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_zero_gep_alloca_load_hoists_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  %q = getelementptr i32, ptr %p, i32 0
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %q, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_multi_zero_gep_exit_load_chain_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %n, ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %q = getelementptr { i32 }, ptr %p, i32 0, i32 0
  %ld = load i32, ptr %q
  %sum = add i32 %i, %ld
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
""")

    def test_bitcast_alloca_load_hoists_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  %p = alloca i32, align 4
  %q = bitcast ptr %p to ptr
  store i32 %x, ptr %p, align 4
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %ld = load i32, ptr %q, align 4
  %sum = add i32 %ld, %i
  call void @sink(i32 %sum)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_freeze_hoists_matches_upstream(self):
        self._parity("""
declare void @sink(i32)
define i32 @f(i32 %n, i32 %x) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, label %body, label %exit
body:
  %y = freeze i32 %x
  call void @sink(i32 %y)
  %i.next = add i32 %i, 1
  br label %header
exit:
  ret i32 0
}
""")

    def test_module_corpus_matches_upstream(self):
        self._parity("""
define i32 @sink_add(i32 %n, i32 %a, i32 %b) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %inv = add i32 %a, %b
  %sum = add i32 %i, %inv
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}

define i32 @sink_load(i32 %n, ptr %p) {
entry:
  br label %header
header:
  %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
  br label %body
body:
  %ld = load i32, ptr %p
  %sum = add i32 %i, %ld
  %i.next = add i32 %i, 1
  %cond = icmp slt i32 %i.next, %n
  br i1 %cond, label %header, label %exit
exit:
  ret i32 %sum
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
