"""Parity corpus for LoopDeletionPass (subset)."""

import unittest

from pcc.ir_passes.loop_deletion import LoopDeletionPass
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass

class LoopDeletionTests(unittest.TestCase):
    def test_dead_loop_deleted(self):
        ir = """
        define i32 @f(i32 %n) {
        entry:
          br label %header
        header:
          %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
          %cond = icmp slt i32 %i, %n
          br i1 %cond, label %body, label %exit
        body:
          %i.next = add i32 %i, 1
          br label %header
        exit:
          ret i32 0
        }
        """
        out, _ = run_pcc_ir_pass(ir, LoopDeletionPass())
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("br label %exit", entry_section)

    def test_loop_with_side_effect_not_deleted(self):
        ir = """
        declare void @sink(i32)
        define void @f(i32 %n) {
        entry:
          br label %header
        header:
          %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
          %cond = icmp slt i32 %i, %n
          br i1 %cond, label %body, label %exit
        body:
          call void @sink(i32 %i)
          %i.next = add i32 %i, 1
          br label %header
        exit:
          ret void
        }
        """
        out, _ = run_pcc_ir_pass(ir, LoopDeletionPass())
        self.assertIn("call void @sink", out)
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("br label %header", entry_section)

    def test_loop_with_store_not_deleted(self):
        ir = """
        define void @f(ptr %p, i32 %n) {
        entry:
          br label %header
        header:
          %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
          %cond = icmp slt i32 %i, %n
          br i1 %cond, label %body, label %exit
        body:
          store i32 %i, ptr %p
          %i.next = add i32 %i, 1
          br label %header
        exit:
          ret void
        }
        """
        out, _ = run_pcc_ir_pass(ir, LoopDeletionPass())
        # store is a side-effect → loop preserved.
        self.assertIn("store i32", out)

    def test_loop_with_exit_phi_using_iv_not_deleted(self):
        ir = """
        define i32 @f(i32 %n) {
        entry:
          br label %header
        header:
          %i = phi i32 [ 0, %entry ], [ %i.next, %body ]
          %cond = icmp slt i32 %i, %n
          br i1 %cond, label %body, label %exit
        body:
          %i.next = add i32 %i, 1
          br label %header
        exit:
          %r = phi i32 [ %i, %header ]
          ret i32 %r
        }
        """
        out, _ = run_pcc_ir_pass(ir, LoopDeletionPass())
        entry_section = out[out.find("entry:"):out.find("header:")]
        self.assertIn("br label %header", entry_section)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
