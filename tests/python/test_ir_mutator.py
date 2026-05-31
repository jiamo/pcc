"""Tests for the IR mutation layer."""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.ir_mutator import MutableModule


_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  ret i32 %b
}
"""


_TWO_FN_IR = """
define i32 @a(i32 %x) {
entry:
  ret i32 %x
}

define i32 @b(i32 %y) {
entry:
  %r = add i32 %y, 1
  ret i32 %r
}
"""


_DIAMOND_IR = """
define i32 @f(i1 %c) {
entry:
  br i1 %c, label %then, label %else
then:
  br label %merge
else:
  br label %merge
merge:
  %r = phi i32 [ 1, %then ], [ 0, %else ]
  ret i32 %r
}
"""


def _verify(text: str) -> None:
    llvm.parse_assembly(text).verify()


class ParseSerializeTests(unittest.TestCase):
    def test_roundtrip_simple(self):
        m = MutableModule.parse(_IR)
        _verify(m.serialize())
        self.assertEqual(len(m.functions), 1)
        self.assertEqual(m.functions[0].name, "f")
        # Args parsed.
        fn = m.functions[0]
        self.assertEqual(len(fn.args), 1)
        self.assertEqual(fn.args[0].name, "x")
        self.assertEqual(fn.args[0].ty, "i32")
        # Blocks: only entry.
        self.assertEqual(len(fn.blocks), 1)
        # Instructions: 3 (add, mul, ret).
        self.assertEqual(len(fn.blocks[0].instructions), 3)

    def test_roundtrip_two_functions(self):
        m = MutableModule.parse(_TWO_FN_IR)
        _verify(m.serialize())
        names = [fn.name for fn in m.functions]
        self.assertEqual(names, ["a", "b"])

    def test_roundtrip_diamond(self):
        m = MutableModule.parse(_DIAMOND_IR)
        _verify(m.serialize())
        fn = m.functions[0]
        block_names = [b.name for b in fn.blocks]
        self.assertIn("entry", block_names)
        self.assertIn("then", block_names)
        self.assertIn("else", block_names)
        self.assertIn("merge", block_names)

    def test_roundtrip_function_whose_first_label_is_not_entry(self):
        ir = """
        define i32 @f(i32 %entry) {
        entry_:
          ret i32 %entry
        }
        """
        m = MutableModule.parse(ir)
        out = m.serialize()
        _verify(out)
        fn = m.functions[0]
        self.assertEqual([b.name for b in fn.blocks], ["entry_"])
        self.assertNotIn("\nentry:\nentry_:", out)

    def test_roundtrip_struct_return_and_argument(self):
        ir = """
        define { i64, i64 } @make({ i64, i64 } %p) {
        entry:
          ret { i64, i64 } %p
        }
        """
        m = MutableModule.parse(ir)
        out = m.serialize()
        _verify(out)
        fn = m.functions[0]
        self.assertEqual(fn.name, "make")
        self.assertEqual(len(fn.args), 1)
        self.assertEqual(fn.args[0].ty, "{ i64, i64 }")
        self.assertEqual(fn.args[0].name, "p")


class RenameValueTests(unittest.TestCase):
    def test_rename_preserves_structure(self):
        m = MutableModule.parse(_IR)
        fn = m.functions[0]
        m.rename_value_in_function(fn, "x", "xin")
        out = m.serialize()
        self.assertIn("%xin", out)
        self.assertNotIn("%x ", out)
        self.assertNotIn("%x,", out)
        # Still parses.
        _verify(out.replace("define i32 @f(i32 %x)", "define i32 @f(i32 %xin)"))
        # Actually our rename is function-internal, so the arg name stays
        # `%x` in the signature. We need to update args too:
        fn.args[0].name = "xin"
        _verify(m.serialize())


class CloneBlockTests(unittest.TestCase):
    def test_clone_block_renames_defs(self):
        m = MutableModule.parse(_IR)
        fn = m.functions[0]
        entry = fn.blocks[0]
        clone = m.clone_block(fn, entry, "entry.clone", "c1")
        # Defined names should get prefixed.
        defined = {i.result_name for i in clone.instructions if i.result_name}
        self.assertIn("c1.a", defined)
        self.assertIn("c1.b", defined)
        # Operands in the clone reference the new names.
        mul_line = [i for i in clone.instructions if i.opcode == "mul"][0]
        self.assertIn("%c1.a", mul_line.text)

    def test_clone_blocks_remaps_cfg_and_uses(self):
        m = MutableModule.parse(_DIAMOND_IR)
        fn = m.functions[0]
        # Clone 'then' and 'else' together.
        blocks_to_clone = [b for b in fn.blocks if b.name in ("then", "else")]
        cloned = m.clone_blocks(fn, blocks_to_clone, "clone")
        self.assertEqual({b.name for b in cloned}, {"clone.then", "clone.else"})
        # Labels inside the cloned blocks should point to the
        # external `merge` block, not to the clone-prefixed names
        # (merge isn't in the clone set).
        for b in cloned:
            term_text = b.terminator.text
            if "br label" in term_text:
                self.assertIn("%merge", term_text)


class TerminatorMutationTests(unittest.TestCase):
    def test_replace_branch_target(self):
        m = MutableModule.parse(_DIAMOND_IR)
        fn = m.functions[0]
        entry = fn.block("entry")
        changed = m.replace_branch_target(entry, "then", "else")
        self.assertTrue(changed)
        # The term should now have two `label %else` incomings.
        self.assertEqual(entry.terminator.text.count("%else"), 2)

    def test_strip_phi_incoming(self):
        m = MutableModule.parse(_DIAMOND_IR)
        fn = m.functions[0]
        merge = fn.block("merge")
        m.strip_phi_incoming(merge, "then")
        phi = merge.instructions[0]
        self.assertNotIn("%then", phi.text)
        # Still has else incoming.
        self.assertIn("%else", phi.text)


class RoundtripVerifyTests(unittest.TestCase):
    def test_verify_roundtrip_ok(self):
        m = MutableModule.parse(_IR)
        m.verify_roundtrip()

    def test_verify_roundtrip_after_rename(self):
        m = MutableModule.parse(_IR)
        fn = m.functions[0]
        m.rename_value_in_function(fn, "a", "alpha")
        m.verify_roundtrip()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
