"""Real-transform tests for SROAPass (subset)."""

import shutil

import unittest
import re

from pcc.ir_passes.sroa import SROAPass, sroa_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass, run_upstream_opt


_OPT = shutil.which("opt")


class SROATests(unittest.TestCase):
    def test_single_field_struct_flattened(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %s = alloca { i32 }
  %p = getelementptr { i32 }, ptr %s, i32 0, i32 0
  store i32 %x, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 %x", out)
        self.assertNotIn("alloca { i32 }", out)
        self.assertNotIn("getelementptr", out)
        self.assertNotIn("load i32", out)

    def test_two_field_struct_split_and_promoted(self):
        ir = """
define i32 @f(i32 %x, i32 %y) {
entry:
  %s = alloca { i32, i32 }
  %p0 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 0
  %p1 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 %x, %y", out)
        self.assertNotIn("alloca { i32, i32 }", out)
        self.assertNotIn("getelementptr { i32, i32 }", out)

    def test_small_array_split_and_promoted(self):
        ir = """
define i32 @f(i32 %x, i32 %y) {
entry:
  %a = alloca [2 x i32]
  %p0 = getelementptr [2 x i32], ptr %a, i32 0, i32 0
  %p1 = getelementptr [2 x i32], ptr %a, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 %x, %y", out)
        self.assertNotIn("alloca [2 x i32]", out)
        self.assertNotIn("getelementptr [2 x i32]", out)

    def test_nested_fixed_aggregate_split_and_promoted(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %s = alloca { i32, [2 x i32] }
  %p0 = getelementptr { i32, [2 x i32] }, ptr %s, i32 0, i32 0
  %p1 = getelementptr { i32, [2 x i32] }, ptr %s, i32 0, i32 1, i32 1
  store i32 %x, ptr %p0
  store i32 5, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 %x, 5", out)
        self.assertNotIn("alloca { i32, [2 x i32] }", out)
        self.assertNotIn("getelementptr { i32, [2 x i32] }", out)

    def test_identified_struct_type_split_and_promoted(self):
        ir = """
%S = type { i32, i32 }
define i32 @f(i32 %x, i32 %y) {
entry:
  %s = alloca %S
  %p0 = getelementptr %S, ptr %s, i32 0, i32 0
  %p1 = getelementptr %S, ptr %s, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 %x, %y", out)
        self.assertNotIn("alloca %S", out)
        self.assertNotIn("getelementptr %S", out)

    def test_identified_nested_aggregate_split_and_promoted(self):
        ir = """
%A = type { [2 x i32], i32 }
define i32 @f(i32 %x, i32 %y) {
entry:
  %a = alloca %A
  %p0 = getelementptr %A, ptr %a, i32 0, i32 0, i32 0
  %p1 = getelementptr %A, ptr %a, i32 0, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 %x, %y", out)
        self.assertNotIn("alloca %A", out)
        self.assertNotIn("getelementptr %A", out)

    def test_single_slot_array_direct_aggregate_load_rewritten(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %a = alloca [1 x i32]
  %p = getelementptr [1 x i32], ptr %a, i32 0, i32 0
  store i32 %x, ptr %p
  %agg = load [1 x i32], ptr %a
  %q = extractvalue [1 x i32] %agg, 0
  ret i32 %q
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("insertvalue [1 x i32] poison, i32 %x, 0", out)
        self.assertIn("%q = extractvalue [1 x i32] %agg.fca.0.insert, 0", out)
        self.assertIn("ret i32 %q", out)
        self.assertNotIn("alloca [1 x i32]", out)

    def test_single_slot_array_direct_aggregate_store_rewritten(self):
        ir = """
define i32 @f(i32 %x) {
entry:
  %a = alloca [1 x i32]
  %agg = insertvalue [1 x i32] poison, i32 %x, 0
  store [1 x i32] %agg, ptr %a
  %p = getelementptr [1 x i32], ptr %a, i32 0, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%agg.fca.0.extract = extractvalue [1 x i32] %agg, 0", out)
        self.assertIn("ret i32 %agg.fca.0.extract", out)
        self.assertNotIn("store [1 x i32] %agg, ptr %a", out)
        self.assertNotIn("alloca [1 x i32]", out)

    def test_named_single_slot_direct_aggregate_load_rewritten(self):
        ir = """
%S = type { i32 }
define i32 @f(i32 %x) {
entry:
  %a = alloca %S
  %p = getelementptr %S, ptr %a, i32 0, i32 0
  store i32 %x, ptr %p
  %agg = load %S, ptr %a
  %q = extractvalue %S %agg, 0
  ret i32 %q
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%agg.fca.0.insert = insertvalue %S poison, i32 %x, 0", out)
        self.assertIn("%q = extractvalue %S %agg.fca.0.insert, 0", out)
        self.assertIn("ret i32 %q", out)
        self.assertNotIn("alloca %S", out)

    def test_named_single_slot_direct_aggregate_store_rewritten(self):
        ir = """
%S = type { i32 }
define i32 @f(i32 %x) {
entry:
  %a = alloca %S
  %agg = insertvalue %S poison, i32 %x, 0
  store %S %agg, ptr %a
  %p = getelementptr %S, ptr %a, i32 0, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%agg.fca.0.extract = extractvalue %S %agg, 0", out)
        self.assertIn("ret i32 %agg.fca.0.extract", out)
        self.assertNotIn("store %S %agg, ptr %a", out)
        self.assertNotIn("alloca %S", out)

    def test_unsafe_direct_aggregate_reference_bails_out(self):
        ir = """
define void @f() {
entry:
  %s = alloca { i32, i32 }
  call void @sink(ptr %s)
  ret void
}
declare void @sink(ptr)
"""
        _, changed = sroa_text(ir)
        self.assertFalse(changed)

    def test_field_gep_pointer_escape_bails_out(self):
        ir = """
define ptr @f() {
entry:
  %s = alloca { i32, i32 }
  %p = getelementptr { i32, i32 }, ptr %s, i32 0, i32 1
  ret ptr %p
}
"""
        out, changed = sroa_text(ir)
        self.assertFalse(changed)
        self.assertIn("alloca { i32, i32 }", out)
        self.assertIn("getelementptr { i32, i32 }, ptr %s, i32 0, i32 1", out)

    def test_partial_struct_aggregate_load_rewritten(self):
        ir = """
define i32 @f(i32 %x, i32 %y) {
entry:
  %p = alloca { i32, i32 }
  %e0 = getelementptr { i32, i32 }, ptr %p, i32 0, i32 0
  store i32 %x, ptr %e0
  %agg = load { i32, i32 }, ptr %p
  %v = extractvalue { i32, i32 } %agg, 0
  ret i32 %v
}
        """
        out, changed = sroa_text(ir)
        self.assertTrue(changed)
        self.assertIn("%v = extractvalue { i32, i32 } %agg.fca.1.insert, 0", out)
        self.assertIn("insertvalue { i32, i32 } poison, i32 %x, 0", out)
        self.assertIn("insertvalue { i32, i32 } %agg.fca.0.insert, i32 undef, 1", out)
        self.assertNotIn("alloca { i32, i32 }", out)

    def test_pass_integration(self):
        ir = """
define i32 @f(i32 %x, i32 %y) {
entry:
  %s = alloca { i32, i32 }
  %p0 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 0
  %p1 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
"""
        out, _ = run_pcc_ir_pass(ir, SROAPass())
        self.assertIn("%sum = add i32 %x, %y", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _parity(self, ir: str):
        report = assert_ir_parity(ir, SROAPass(), "sroa")
        self.assertTrue(
            report.is_equivalent,
            f"mismatch:\n---pcc---\n{report.pcc_ir}\n---opt---\n{report.opt_ir}",
        )

    def test_single_field_struct_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x) {
entry:
  %s = alloca { i32 }
  %p = getelementptr { i32 }, ptr %s, i32 0, i32 0
  store i32 %x, ptr %p
  %v = load i32, ptr %p
  ret i32 %v
}
""")

    def test_two_field_struct_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x, i32 %y) {
entry:
  %s = alloca { i32, i32 }
  %p0 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 0
  %p1 = getelementptr { i32, i32 }, ptr %s, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
""")

    def test_array_two_i32_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x, i32 %y) {
entry:
  %a = alloca [2 x i32]
  %p0 = getelementptr [2 x i32], ptr %a, i32 0, i32 0
  %p1 = getelementptr [2 x i32], ptr %a, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
""")

    def test_nested_fixed_aggregate_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x) {
entry:
  %s = alloca { i32, [2 x i32] }
  %p0 = getelementptr { i32, [2 x i32] }, ptr %s, i32 0, i32 0
  %p1 = getelementptr { i32, [2 x i32] }, ptr %s, i32 0, i32 1, i32 1
  store i32 %x, ptr %p0
  store i32 5, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
""")

    def test_identified_struct_type_matches_upstream(self):
        self._parity("""
%S = type { i32, i32 }
define i32 @f(i32 %x, i32 %y) {
entry:
  %s = alloca %S
  %p0 = getelementptr %S, ptr %s, i32 0, i32 0
  %p1 = getelementptr %S, ptr %s, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
""")

    def test_identified_nested_aggregate_matches_upstream(self):
        self._parity("""
%A = type { [2 x i32], i32 }
define i32 @f(i32 %x, i32 %y) {
entry:
  %a = alloca %A
  %p0 = getelementptr %A, ptr %a, i32 0, i32 0, i32 0
  %p1 = getelementptr %A, ptr %a, i32 0, i32 0, i32 1
  store i32 %x, ptr %p0
  store i32 %y, ptr %p1
  %v0 = load i32, ptr %p0
  %v1 = load i32, ptr %p1
  %sum = add i32 %v0, %v1
  ret i32 %sum
}
""")

    def test_single_slot_array_direct_aggregate_load_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x) {
entry:
  %a = alloca [1 x i32]
  %p = getelementptr [1 x i32], ptr %a, i32 0, i32 0
  store i32 %x, ptr %p
  %agg = load [1 x i32], ptr %a
  %q = extractvalue [1 x i32] %agg, 0
  ret i32 %q
}
""")

    def test_single_slot_array_direct_aggregate_store_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x) {
entry:
  %a = alloca [1 x i32]
  %agg = insertvalue [1 x i32] poison, i32 %x, 0
  store [1 x i32] %agg, ptr %a
  %p = getelementptr [1 x i32], ptr %a, i32 0, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
""")

    def test_named_single_slot_direct_aggregate_load_matches_upstream(self):
        ir = """
%S = type { i32 }
define i32 @f(i32 %x) {
entry:
  %a = alloca %S
  %p = getelementptr %S, ptr %a, i32 0, i32 0
  store i32 %x, ptr %p
  %agg = load %S, ptr %a
  %q = extractvalue %S %agg, 0
  ret i32 %q
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, SROAPass())
        upstream = run_upstream_opt(ir, "sroa")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertRegex(pcc_out, r"%S(?:\.\d+)? = type \{ i32 \}")
        self.assertRegex(pcc_out, r"insertvalue %S(?:\.\d+)? poison, i32 %x, 0")
        self.assertRegex(pcc_out, r"extractvalue %S(?:\.\d+)? %agg\.fca\.0\.insert, 0")
        self.assertIn("ret i32 %q", pcc_out)
        self.assertIn("insertvalue %S poison, i32 %x, 0", upstream.ir_text)
        self.assertIn("extractvalue %S %agg.fca.0.insert, 0", upstream.ir_text)

    def test_named_single_slot_direct_aggregate_store_matches_upstream(self):
        ir = """
%S = type { i32 }
define i32 @f(i32 %x) {
entry:
  %a = alloca %S
  %agg = insertvalue %S poison, i32 %x, 0
  store %S %agg, ptr %a
  %p = getelementptr %S, ptr %a, i32 0, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, SROAPass())
        upstream = run_upstream_opt(ir, "sroa")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertRegex(pcc_out, r"%S(?:\.\d+)? = type \{ i32 \}")
        self.assertRegex(pcc_out, r"insertvalue %S(?:\.\d+)? poison, i32 %x, 0")
        self.assertRegex(pcc_out, r"extractvalue %S(?:\.\d+)? %agg, 0")
        self.assertIn("ret i32 %agg.fca.0.extract", pcc_out)
        self.assertIn("insertvalue %S poison, i32 %x, 0", upstream.ir_text)
        self.assertIn("extractvalue %S %agg, 0", upstream.ir_text)

    def test_field_gep_pointer_escape_matches_upstream(self):
        self._parity("""
define ptr @f() {
entry:
  %s = alloca { i32, i32 }
  %p = getelementptr { i32, i32 }, ptr %s, i32 0, i32 1
  ret ptr %p
}
""")

    def test_partial_struct_aggregate_load_matches_upstream(self):
        self._parity("""
define i32 @f(i32 %x, i32 %y) {
entry:
  %p = alloca { i32, i32 }
  %e0 = getelementptr { i32, i32 }, ptr %p, i32 0, i32 0
  store i32 %x, ptr %e0
  %agg = load { i32, i32 }, ptr %p
  %v = extractvalue { i32, i32 } %agg, 0
  ret i32 %v
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
