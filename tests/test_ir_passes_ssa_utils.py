"""Tests for SSA def-use indexing."""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.ssa_utils import build_def_use_index


def _parse(ir_text: str) -> llvm.ModuleRef:
    module = llvm.parse_assembly(ir_text)
    module.verify()
    return module


class DefUseIndexTests(unittest.TestCase):
    def test_simple_add_chain(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = add i32 %a, 2
          ret i32 %b
        }
        """
        idx = build_def_use_index(_parse(ir))
        self.assertIsNotNone(idx.def_of("a"))
        self.assertIsNotNone(idx.def_of("b"))
        self.assertEqual(idx.def_of("a").opcode, "add")
        self.assertIn("a", idx.operands_of("b"))
        # x is used by a but not b (directly).
        users_of_x = [r.name for r in idx.users_of("x")]
        self.assertIn("a", users_of_x)
        users_of_a = [r.name for r in idx.users_of("a")]
        self.assertIn("b", users_of_a)

    def test_unused_value_has_empty_users(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %unused = add i32 %x, 1
          ret i32 %x
        }
        """
        idx = build_def_use_index(_parse(ir))
        self.assertEqual(idx.users_of("unused"), [])

    def test_self_reference_on_phi(self):
        ir = """
        define i32 @f(i32 %n) {
        entry:
          br label %loop
        loop:
          %i = phi i32 [0, %entry], [%i.next, %loop]
          %i.next = add i32 %i, 1
          %c = icmp slt i32 %i.next, %n
          br i1 %c, label %loop, label %exit
        exit:
          ret i32 %i
        }
        """
        idx = build_def_use_index(_parse(ir))
        # i's users should include i.next (the add reading it) and
        # the ret (via the phi pattern).
        users_of_i = {r.name for r in idx.users_of("i")}
        self.assertIn("i.next", users_of_i)

    def test_side_effecting_call_indexed(self):
        ir = """
        declare void @sink(i32)
        define void @f(i32 %x) {
        entry:
          call void @sink(i32 %x)
          ret void
        }
        """
        idx = build_def_use_index(_parse(ir))
        # `x` has a user even though the call doesn't define a value.
        self.assertEqual(len(idx.users_of("x")), 1)

    def test_block_enumeration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          br label %tail
        tail:
          ret i32 %a
        }
        """
        idx = build_def_use_index(_parse(ir))
        entry_insts = idx.instructions_in("f", "entry")
        self.assertEqual([i.opcode for i in entry_insts], ["add", "br"])
        tail_insts = idx.instructions_in("f", "tail")
        self.assertEqual([i.opcode for i in tail_insts], ["ret"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
