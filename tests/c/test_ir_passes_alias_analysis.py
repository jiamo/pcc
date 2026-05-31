"""Tests for AliasAnalysis boundary."""

import unittest

import llvmlite.binding as llvm

from pcc.ir_passes.alias_analysis import AliasAnalysis, AliasResult


def _parse(ir_text: str) -> llvm.ModuleRef:
    m = llvm.parse_assembly(ir_text)
    m.verify()
    return m


_TWO_ALLOCAS = """
define i32 @f() {
entry:
  %a = alloca i32
  %b = alloca i32
  store i32 1, ptr %a
  store i32 2, ptr %b
  %v = load i32, ptr %a
  ret i32 %v
}
"""


_GLOBALS = """
@g = global i32 0
@h = global i32 0
define void @f() {
entry:
  store i32 1, ptr @g
  store i32 2, ptr @h
  ret void
}
"""


class AliasTests(unittest.TestCase):
    def test_same_pointer_is_must_alias(self):
        aa = AliasAnalysis(_parse(_TWO_ALLOCAS))
        self.assertEqual(aa.alias_names("a", "a"), AliasResult.MustAlias)

    def test_distinct_allocas_are_no_alias(self):
        aa = AliasAnalysis(_parse(_TWO_ALLOCAS))
        self.assertEqual(aa.alias_names("a", "b"), AliasResult.NoAlias)

    def test_distinct_globals_are_no_alias(self):
        aa = AliasAnalysis(_parse(_GLOBALS))
        self.assertEqual(aa.alias_names("g", "h"), AliasResult.NoAlias)

    def test_unknown_defaults_to_may_alias(self):
        aa = AliasAnalysis(_parse(_TWO_ALLOCAS))
        # Neither %x nor %y is known — must assume they may alias.
        self.assertEqual(aa.alias_names("x", "y"), AliasResult.MayAlias)

    def test_alloca_vs_global_is_no_alias(self):
        combined = """
        @g = global i32 0
        define void @f() {
        entry:
          %a = alloca i32
          ret void
        }
        """
        aa = AliasAnalysis(_parse(combined))
        self.assertEqual(aa.alias_names("a", "g"), AliasResult.NoAlias)

    def test_alloca_vs_argument_is_no_alias(self):
        ir = """
        define void @f(ptr %p) {
        entry:
          %a = alloca i32
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        self.assertEqual(aa.alias_names("a", "p"), AliasResult.NoAlias)

    def test_zero_gep_alias_of_alloca_is_must_alias(self):
        ir = """
        define void @f() {
        entry:
          %a = alloca i32
          %q = getelementptr i32, ptr %a, i32 0
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        self.assertEqual(aa.alias_names("a", "q"), AliasResult.MustAlias)

    def test_bitcast_alias_of_alloca_is_must_alias(self):
        ir = """
        define void @f() {
        entry:
          %a = alloca i32
          %q = bitcast ptr %a to ptr
          ret void
        }
        """
        aa = AliasAnalysis(_parse(ir))
        self.assertEqual(aa.alias_names("a", "q"), AliasResult.MustAlias)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
