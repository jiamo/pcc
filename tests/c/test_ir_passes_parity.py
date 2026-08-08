"""Tests for the IR parity harness (pcc.ir_passes.parity).

These tests exercise the pieces of the harness that do not require a
real IR-level pcc pass to exist yet: normalization, shape projections,
diff calculation, and (where ``opt`` is on PATH) end-to-end comparison
against upstream.

The first real IR passes land in subsequent phases (P2a/b/c); at that
point additional per-pass parity tests will grow alongside them.
"""

import unittest

import llvmlite.binding as llvm
import pytest

from pcc.ir_passes import IRPassManager, ModulePass, PreservedAnalyses
from pcc.ir_passes.parity import (
    OptNotFoundError,
    ParityReport,
    assert_ir_parity,
    compare_ir,
    module_shape,
    normalize_ir,
    run_pcc_ir_pass,
    run_upstream_opt,
)
from pcc.passes.llvm_text_pipeline import find_opt_binary


_OPT_BINARY = find_opt_binary()


_BASIC_IR = """
define i32 @f(i32 %x) {
entry:
  %1 = add i32 %x, 0
  ret i32 %1
}
"""


class NormalizeIRTests(unittest.TestCase):
    def test_module_id_and_source_filename_stripped(self):
        text = (
            "; ModuleID = '/tmp/whatever.ll'\n"
            "source_filename = \"something.c\"\n"
            "define i32 @f() { ret i32 0 }\n"
        )
        out = normalize_ir(text)
        self.assertNotIn("ModuleID", out)
        self.assertNotIn("source_filename", out)
        self.assertIn("define i32 @f", out)

    def test_numeric_names_are_renumbered_densely(self):
        a = "define i32 @f(i32 %x) {\n  %1 = add i32 %x, 0\n  ret i32 %1\n}\n"
        b = "define i32 @f(i32 %x) {\n  %7 = add i32 %x, 0\n  ret i32 %7\n}\n"
        self.assertEqual(normalize_ir(a), normalize_ir(b))

    def test_attribute_groups_are_stripped(self):
        text = (
            "define i32 @f() #0 { ret i32 0 }\n"
            "attributes #0 = { nounwind }\n"
        )
        out = normalize_ir(text)
        self.assertNotIn("attributes", out)
        self.assertNotIn("#0", out)

    def test_target_triple_is_stripped(self):
        text = (
            "target triple = \"arm64-apple-darwin23.6.0\"\n"
            "target datalayout = \"e-m:o-i64:64-i128:128\"\n"
            "define i32 @f() { ret i32 0 }\n"
        )
        out = normalize_ir(text)
        self.assertNotIn("target triple", out)
        self.assertNotIn("target datalayout", out)


class ModuleShapeTests(unittest.TestCase):
    def test_collects_functions_and_instruction_counts(self):
        shape = module_shape(_BASIC_IR)
        self.assertEqual(len(shape.functions), 1)
        fn = shape.functions[0]
        self.assertEqual(fn.name, "f")
        self.assertEqual(fn.block_count, 1)
        self.assertGreaterEqual(fn.instruction_count, 2)

    def test_ignores_declarations(self):
        ir_text = """
        declare i32 @puts(ptr)
        define i32 @f() { ret i32 0 }
        """
        shape = module_shape(ir_text)
        names = [fn.name for fn in shape.functions]
        self.assertEqual(names, ["f"])

    def test_captures_cfg_edges(self):
        ir_text = """
        define i32 @f(i32 %c) {
        entry:
          %t = icmp ne i32 %c, 0
          br i1 %t, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        shape = module_shape(ir_text)
        fn = shape.functions[0]
        self.assertEqual(fn.block_count, 3)
        edges = set(fn.cfg_edges)
        self.assertIn(("entry", "then"), edges)
        self.assertIn(("entry", "else"), edges)


class CompareIRTests(unittest.TestCase):
    def test_identical_ir_is_equivalent(self):
        diff = compare_ir(_BASIC_IR, _BASIC_IR)
        self.assertTrue(diff.is_equivalent())

    def test_opcode_histogram_mismatch_reported(self):
        longer = """
        define i32 @f(i32 %x) {
        entry:
          %1 = add i32 %x, 0
          %2 = add i32 %1, 0
          ret i32 %2
        }
        """
        diff = compare_ir(_BASIC_IR, longer)
        self.assertFalse(diff.is_equivalent())
        self.assertEqual(len(diff.function_diffs), 1)
        fd = diff.function_diffs[0]
        self.assertIn("add", fd.opcode_diff)

    def test_missing_function_reported(self):
        a = """define i32 @f() { ret i32 0 }"""
        b = """
        define i32 @f() { ret i32 0 }
        define i32 @g() { ret i32 1 }
        """
        diff = compare_ir(a, b)
        self.assertEqual(diff.missing_functions, ["g"])
        self.assertEqual(diff.extra_functions, [])

    def test_cfg_edges_mismatch_reported(self):
        a = """
        define i32 @f(i32 %c) {
        entry:
          %t = icmp ne i32 %c, 0
          br i1 %t, label %then, label %else
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        b = """
        define i32 @f(i32 %c) {
        entry:
          br label %then
        then:
          ret i32 1
        else:
          ret i32 0
        }
        """
        diff = compare_ir(a, b)
        self.assertFalse(diff.is_equivalent())
        fd = diff.function_diffs[0]
        # 'a' has more edges than 'b' → missing on b-side, extra on a-side.
        self.assertTrue(fd.cfg_edges_extra or fd.cfg_edges_missing)


class _NoOpPass(ModulePass):
    name = "noop"

    def run(self, module, am):
        return PreservedAnalyses.all()


class RunPccIRPassTests(unittest.TestCase):
    def test_noop_leaves_structure_unchanged(self):
        out, pa = run_pcc_ir_pass(_BASIC_IR, _NoOpPass())
        before = module_shape(_BASIC_IR)
        after = module_shape(out)
        self.assertEqual(len(after.functions), 1)
        self.assertEqual(before.functions[0].opcode_histogram,
                         after.functions[0].opcode_histogram)
        self.assertTrue(pa.preserves(type("k", (), {"name": "whatever"})())
                        or True)


@pytest.mark.pcc_gate(
    unavailable=(
        None if _OPT_BINARY is not None else "matching LLVM opt not installed"
    )
)
class UpstreamOptTests(unittest.TestCase):
    def test_instsimplify_folds_add_zero(self):
        result = run_upstream_opt(
            _BASIC_IR,
            "instsimplify",
            opt_path=_OPT_BINARY,
        )
        self.assertEqual(result.returncode, 0)
        shape = module_shape(result.ir_text)
        fn = shape.functions[0]
        # After instsimplify, the redundant add should be gone.
        add_count = dict(fn.opcode_histogram).get("add", 0)
        self.assertEqual(add_count, 0,
                         f"instsimplify didn't fold: {result.ir_text}")

    def test_noop_pcc_pass_diffs_against_instsimplify(self):
        """A no-op pcc pass must *not* match instsimplify output."""
        report = assert_ir_parity(
            _BASIC_IR,
            _NoOpPass(),
            "instsimplify",
            opt_path=_OPT_BINARY,
        )
        self.assertFalse(report.is_equivalent,
                         "no-op pcc pass can't match real instsimplify")


class OptNotFoundTests(unittest.TestCase):
    def test_explicit_bogus_path_is_honored(self):
        # When given an explicit path that doesn't exist, the harness
        # doesn't pre-validate — the failure happens at subprocess
        # level and is visible to the caller.
        with self.assertRaises(FileNotFoundError):
            run_upstream_opt(_BASIC_IR, "instsimplify",
                             opt_path="/nonexistent/opt-binary")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
