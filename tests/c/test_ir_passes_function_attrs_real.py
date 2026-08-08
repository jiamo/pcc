"""Real-transform tests for FunctionAttrsPass."""

import pytest
import unittest

from pcc.ir_passes.function_attrs import FunctionAttrsPass, infer_function_attrs
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class FunctionAttrsTests(unittest.TestCase):
    def test_pure_function_gets_memory_none_nounwind_norecurse(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = add i32 %x, 1
          ret i32 %r
        }
        """
        out, changed = infer_function_attrs(ir)
        self.assertTrue(changed)
        define_line = [l for l in out.splitlines() if l.startswith("define ")][0]
        self.assertIn("memory(none)", define_line)
        self.assertIn("nofree", define_line)
        self.assertIn("nosync", define_line)
        self.assertIn("nounwind", define_line)
        self.assertIn("norecurse", define_line)
        self.assertIn("willreturn", define_line)
        self.assertIn("mustprogress", define_line)

    def test_readonly_but_not_readnone(self):
        ir = """
        define i32 @f(ptr %p) {
        entry:
          %v = load i32, ptr %p
          ret i32 %v
        }
        """
        out, _ = infer_function_attrs(ir)
        define_line = [l for l in out.splitlines() if l.startswith("define ")][0]
        self.assertIn("memory(argmem: read)", define_line)
        self.assertIn("ptr nocapture readonly %p", define_line)
        self.assertNotIn("memory(none)", define_line)
        self.assertIn("nounwind", define_line)
        self.assertIn("nofree", define_line)
        self.assertIn("nosync", define_line)

    def test_writer_gets_neither_memory_none_nor_readonly(self):
        ir = """
        define void @f(ptr %p, i32 %v) {
        entry:
          store i32 %v, ptr %p
          ret void
        }
        """
        out, _ = infer_function_attrs(ir)
        define_line = [l for l in out.splitlines() if l.startswith("define ")][0]
        self.assertIn("memory(argmem: write)", define_line)
        self.assertIn("ptr nocapture writeonly %p", define_line)
        self.assertNotIn("memory(none)", define_line)
        self.assertIn("nounwind", define_line)
        self.assertIn("nofree", define_line)
        self.assertIn("nosync", define_line)
        self.assertIn("willreturn", define_line)
        self.assertIn("mustprogress", define_line)

    def test_self_call_drops_norecurse(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %c = icmp eq i32 %x, 0
          br i1 %c, label %base, label %rec
        base:
          ret i32 0
        rec:
          %n = sub i32 %x, 1
          %r = call i32 @f(i32 %n)
          ret i32 %r
        }
        """
        out, _ = infer_function_attrs(ir)
        define_line = [l for l in out.splitlines() if l.startswith("define ")][0]
        self.assertNotIn("norecurse", define_line)
        self.assertNotIn("willreturn", define_line)
        self.assertNotIn("mustprogress", define_line)
        self.assertIn("memory(none)", define_line)
        self.assertIn("nofree", define_line)
        self.assertIn("nosync", define_line)
        self.assertIn("nounwind", define_line)

    def test_direct_callee_attrs_flow_to_caller(self):
        ir = """
        define i32 @leaf(i32 %x) {
        entry:
          %y = add i32 %x, 1
          ret i32 %y
        }
        define i32 @caller(i32 %x) {
        entry:
          %r = call i32 @leaf(i32 %x)
          ret i32 %r
        }
        """
        out, _ = infer_function_attrs(ir)
        define_lines = [l for l in out.splitlines() if l.startswith("define ")]
        caller_line = [l for l in define_lines if "@caller" in l][0]
        self.assertIn("memory(none)", caller_line)
        self.assertIn("nofree", caller_line)
        self.assertIn("nosync", caller_line)
        self.assertIn("nounwind", caller_line)
        self.assertIn("norecurse", caller_line)
        self.assertIn("willreturn", caller_line)
        self.assertIn("mustprogress", caller_line)

    def test_gep_load_preserves_pointer_param_attrs(self):
        ir = """
        define i32 @f(ptr %p) {
        entry:
          %q = getelementptr i32, ptr %p, i64 1
          %v = load i32, ptr %q
          ret i32 %v
        }
        """
        out, _ = infer_function_attrs(ir)
        define_line = [l for l in out.splitlines() if l.startswith("define ")][0]
        self.assertIn("memory(argmem: read)", define_line)
        self.assertIn("ptr nocapture readonly %p", define_line)

    def test_pass_integration(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %r = mul i32 %x, 2
          ret i32 %r
        }
        """
        out, _ = run_pcc_ir_pass(ir, FunctionAttrsPass())
        self.assertIn("memory(none)", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def test_leaf_and_caller_match_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @leaf(i32 %x) {
entry:
  %y = add i32 %x, 1
  ret i32 %y
}
define i32 @caller(i32 %x) {
entry:
  %r = call i32 @leaf(i32 %x)
  ret i32 %r
}
""", FunctionAttrsPass(), "function-attrs")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_pointer_readonly_signature_matches_upstream_shape(self):
        report = assert_ir_parity("""
define i32 @f(ptr %p) {
entry:
  %q = getelementptr i32, ptr %p, i64 1
  %v = load i32, ptr %q
  ret i32 %v
}
""", FunctionAttrsPass(), "function-attrs")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
