"""Real-transform tests for GlobalOptPass (subset)."""

import pytest

import unittest

from pcc.ir_passes.globalopt import GlobalOptPass, globalopt_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass, run_upstream_opt


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class GlobalOptTests(unittest.TestCase):
    def test_internal_const_load_inlined(self):
        ir = """
@k = internal constant i32 42
define i32 @f() {
entry:
  %v = load i32, ptr @k
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 42", out)
        self.assertNotIn("load i32, ptr @k", out)

    def test_external_global_not_folded(self):
        ir = """
@g = global i32 42
define i32 @f() {
entry:
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = local_unnamed_addr global i32 42", out)
        self.assertIn("%v = load i32, ptr @g", out)

    def test_external_scalar_direct_uses_gain_local_unnamed_addr(self):
        ir = """
@g = global i32 42
define i32 @f() {
entry:
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = local_unnamed_addr global i32 42", out)
        self.assertIn("define i32 @f() local_unnamed_addr {", out)

    def test_external_pointer_scalar_direct_uses_gain_local_unnamed_addr(self):
        ir = """
@g = global i32 42
@p = global ptr @g
define ptr @f() {
entry:
  %v = load ptr, ptr @p
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = global i32 42", out)
        self.assertIn("@p = local_unnamed_addr global ptr @g", out)
        self.assertIn("define ptr @f() local_unnamed_addr {", out)

    def test_internal_pointer_global_with_known_init_is_inlined(self):
        ir = """
@g = internal global ptr null
define ptr @f() {
entry:
  %v = load ptr, ptr @g
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret ptr null", out)
        self.assertNotIn("load ptr, ptr @g", out)
        self.assertNotIn("@g = internal global ptr null", out)

    def test_internal_mutable_but_never_stored_loads_are_inlined(self):
        ir = """
@g = internal global i32 7
define i32 @f() {
entry:
  %v1 = load i32, ptr @g
  %v2 = load i32, ptr @g
  %sum = add i32 %v1, %v2
  ret i32 %sum
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("%sum = add i32 7, 7", out)
        self.assertNotIn("load i32, ptr @g", out)
        self.assertNotIn("@g = internal global i32 7", out)

    def test_cross_function_internal_mutable_readonly_loads_are_inlined(self):
        ir = """
@g = internal global i32 7
define i32 @f() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
define i32 @h() {
entry:
  %b = load i32, ptr @g
  ret i32 %b
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("load i32, ptr @g", out)
        self.assertNotIn("@g = internal global i32 7", out)

    def test_cross_function_single_const_store_becomes_flag_and_select(self):
        ir = """
@g = internal global i32 0
define void @set() {
entry:
  store i32 7, ptr @g
  ret void
}
define i32 @get() {
entry:
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global i1 false", out)
        self.assertIn("store i1 true, ptr @g, align 1", out)
        self.assertIn("%v.b = load i1, ptr @g, align 1", out)
        self.assertIn("%v = select i1 %v.b, i32 7, i32 0", out)
        self.assertNotIn("load i32, ptr @g", out)

    def test_cross_function_single_pointer_store_is_not_rewritten_to_flag_and_select(self):
        ir = """
@t = internal global i32 0
@g = internal global ptr null
define void @set() {
entry:
  store ptr @t, ptr @g
  ret void
}
define ptr @get() {
entry:
  %v = load ptr, ptr @g
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global ptr null", out)
        self.assertIn("store ptr @t, ptr @g", out)
        self.assertIn("%v = load ptr, ptr @g", out)
        self.assertNotIn("@g = internal unnamed_addr global i1 false", out)
        self.assertNotIn("select i1", out)

    def test_internal_mutable_zero_gep_loads_are_inlined(self):
        ir = """
@g = internal global i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("%p = getelementptr i32, ptr @g, i32 0", out)
        self.assertNotIn("load i32, ptr %p", out)
        self.assertNotIn("@g = internal global i32 7", out)

    def test_cross_function_internal_mutable_zero_gep_loads_are_inlined(self):
        ir = """
@g = internal global i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %a = load i32, ptr %p
  ret i32 %a
}
define i32 @h() {
entry:
  %q = getelementptr i32, ptr @g, i32 0
  %b = load i32, ptr %q
  ret i32 %b
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("load i32, ptr %p", out)
        self.assertNotIn("load i32, ptr %q", out)
        self.assertNotIn("@g = internal global i32 7", out)

    def test_mixed_direct_and_zero_gep_constant_loads_are_not_partially_folded(self):
        ir = """
@g = internal constant i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %a = load i32, ptr @g
  %b = load i32, ptr %p
  %c = add i32 %a, %b
  ret i32 %c
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr constant i32 7", out)
        self.assertIn("%a = load i32, ptr @g", out)
        self.assertIn("%b = load i32, ptr %p", out)
        self.assertNotIn("%c = add i32 7, %b", out)

    def test_unused_internal_scalar_global_removed(self):
        ir = """
@g = internal global i32 0
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@g = internal global i32 0", out)

    def test_unused_internal_pointer_global_removed(self):
        ir = """
@g = internal global ptr null
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@g = internal global ptr null", out)

    def test_dead_internal_global_removed_even_with_prefixed_peer_name(self):
        ir = """
@g = internal global i32 0
@g2 = internal global i32 1
define i32 @f() {
entry:
  store i32 7, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
define i32 @h() {
entry:
  %x = load i32, ptr @g2
  ret i32 %x
}
        """
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("@g = internal", out)
        self.assertIn("ret i32 7", out)
        self.assertIn("ret i32 1", out)

    def test_multifunction_private_constant_not_folded(self):
        ir = """
@g = private constant i32 3
define i32 @f() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
define i32 @h() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = private unnamed_addr constant i32 3", out)
        self.assertIn("%a = load i32, ptr @g", out)

    def test_single_const_store_then_dominated_load_folded(self):
        ir = """
@g = internal global i32 0
define i32 @f() {
entry:
  store i32 42, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 42", out)
        self.assertNotIn("store i32 42, ptr @g", out)
        self.assertNotIn("load i32, ptr @g", out)
        self.assertNotIn("@g = internal global i32 0", out)

    def test_single_pointer_symbol_store_then_dominated_load_folded(self):
        ir = """
@t = internal global i32 0
@g = internal global ptr null
define ptr @f() {
entry:
  store ptr @t, ptr @g
  %v = load ptr, ptr @g
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret ptr @t", out)
        self.assertNotIn("store ptr @t, ptr @g", out)
        self.assertNotIn("load ptr, ptr @g", out)
        self.assertNotIn("@g = internal global ptr null", out)

    def test_direct_store_then_zero_gep_load_not_folded(self):
        ir = """
@g = internal global i32 0
define i32 @f() {
entry:
  store i32 7, ptr @g
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global i32 0", out)
        self.assertIn("store i32 7, ptr @g", out)
        self.assertIn("%p = getelementptr i32, ptr @g, i32 0", out)
        self.assertIn("%v = load i32, ptr %p", out)

    def test_zero_gep_store_then_direct_load_folds_load_but_keeps_store(self):
        ir = """
@t = internal global i32 0
@g = internal global ptr null
define ptr @f() {
entry:
  %p = getelementptr ptr, ptr @g, i32 0
  store ptr @t, ptr %p
  %v = load ptr, ptr @g
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global ptr null", out)
        self.assertIn("%p = getelementptr ptr, ptr @g, i32 0", out)
        self.assertIn("store ptr @t, ptr %p", out)
        self.assertIn("ret ptr @t", out)
        self.assertNotIn("%v = load ptr, ptr @g", out)

    def test_zero_gep_store_then_direct_int_load_folds_store_and_load(self):
        ir = """
@g = internal global i32 0
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  store i32 7, ptr %p
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 7", out)
        self.assertNotIn("%p = getelementptr i32, ptr @g, i32 0", out)
        self.assertNotIn("store i32 7, ptr %p", out)
        self.assertNotIn("%v = load i32, ptr @g", out)
        self.assertNotIn("@g = internal global i32 0", out)

    def test_branch_bypassing_store_not_folded(self):
        ir = """
@g = internal global i32 0
define i32 @f(i1 %c) {
entry:
  br i1 %c, label %set, label %join
set:
  store i32 9, ptr @g
  br label %join
join:
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("store i32 9, ptr @g", out)
        self.assertIn("%v = load i32, ptr @g", out)
        self.assertIn("define i32 @f(i1 %c) local_unnamed_addr {", out)

    def test_variable_store_not_folded(self):
        ir = """
@g = internal global i32 0
define i32 @f(i32 %x) {
entry:
  store i32 %x, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global i32 0", out)
        self.assertIn("store i32 %x, ptr @g", out)
        self.assertIn("%v = load i32, ptr @g", out)
        self.assertIn("define i32 @f(i32 %x) local_unnamed_addr {", out)

    def test_mutable_internal_direct_uses_gain_unnamed_addr(self):
        ir = """
@g = internal global i32 0
define i32 @f(i32 %x) {
entry:
  store i32 %x, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr global i32 0", out)
        self.assertIn("define i32 @f(i32 %x) local_unnamed_addr {", out)
        self.assertIn("store i32 %x, ptr @g", out)
        self.assertIn("%v = load i32, ptr @g", out)

    def test_internal_direct_callee_gains_unnamed_addr(self):
        ir = """
define internal i32 @callee(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}

define i32 @caller(i32 %x) {
entry:
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("define internal i32 @callee(i32 %x) unnamed_addr {", out)
        self.assertIn("define i32 @caller(i32 %x) local_unnamed_addr {", out)

    def test_internal_constant_zero_gep_use_gains_unnamed_addr(self):
        ir = """
@g = internal constant i32 42
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr constant i32 42", out)
        self.assertIn("%p = getelementptr i32, ptr @g, i32 0", out)

    def test_internal_pointer_constant_zero_gep_use_gains_unnamed_addr(self):
        ir = """
@t = internal global i32 0
@g = internal constant ptr @t
define ptr @f() {
entry:
  %p = getelementptr ptr, ptr @g, i32 0
  %v = load ptr, ptr %p
  ret ptr %v
}
"""
        out, changed = globalopt_text(ir)
        self.assertTrue(changed)
        self.assertIn("@g = internal unnamed_addr constant ptr @t", out)
        self.assertIn("%p = getelementptr ptr, ptr @g, i32 0", out)

    def test_pass_integration(self):
        ir = """
@mask = private constant i32 255
define i32 @f(i32 %x) {
entry:
  %m = load i32, ptr @mask
  %r = and i32 %x, %m
  ret i32 %r
}
"""
        out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        self.assertIn("and i32 %x, 255", out)


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class UpstreamParityTests(unittest.TestCase):
    def _structural_parity(self, ir: str):
        report = assert_ir_parity(ir, GlobalOptPass(), "globalopt")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_internal_mutable_readonly_global_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 7
define i32 @f() {
entry:
  %v1 = load i32, ptr @g
  %v2 = load i32, ptr @g
  %sum = add i32 %v1, %v2
  ret i32 %sum
}
""")

    def test_cross_function_internal_mutable_readonly_global_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 7
define i32 @f() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
define i32 @h() {
entry:
  %b = load i32, ptr @g
  ret i32 %b
}
""")

    def test_cross_function_single_const_store_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 0
define void @set() {
entry:
  store i32 7, ptr @g
  ret void
}
define i32 @get() {
entry:
  %v = load i32, ptr @g
  ret i32 %v
}
""")

    def test_cross_function_single_pointer_store_matches_upstream_shape(self):
        self._structural_parity("""
@t = internal global i32 0
@g = internal global ptr null
define void @set() {
entry:
  store ptr @t, ptr @g
  ret void
}
define ptr @get() {
entry:
  %v = load ptr, ptr @g
  ret ptr %v
}
""")

    def test_internal_mutable_zero_gep_load_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
""")

    def test_cross_function_internal_mutable_zero_gep_loads_match_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %a = load i32, ptr %p
  ret i32 %a
}
define i32 @h() {
entry:
  %q = getelementptr i32, ptr @g, i32 0
  %b = load i32, ptr %q
  ret i32 %b
}
""")

    def test_mixed_direct_and_zero_gep_constant_loads_match_upstream_shape(self):
        self._structural_parity("""
@g = internal constant i32 7
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %a = load i32, ptr @g
  %b = load i32, ptr %p
  %c = add i32 %a, %b
  ret i32 %c
}
""")

    def test_unused_internal_scalar_global_removed_like_upstream(self):
        ir = """
@g = internal global i32 0
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertNotIn("@g = internal global i32 0", upstream.ir_text)
        self.assertNotIn("@g = internal global i32 0", pcc_out)

    def test_unused_internal_pointer_global_removed_like_upstream(self):
        ir = """
@g = internal global ptr null
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertNotIn("@g = internal global ptr null", upstream.ir_text)
        self.assertNotIn("@g = internal global ptr null", pcc_out)

    def test_dead_internal_global_removed_even_with_prefixed_peer_name_like_upstream(self):
        ir = """
@g = internal global i32 0
@g2 = internal global i32 1
define i32 @f() {
entry:
  store i32 7, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
define i32 @h() {
entry:
  %x = load i32, ptr @g2
  ret i32 %x
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertNotIn("@g = internal", upstream.ir_text)
        self.assertNotIn("@g = internal", pcc_out)
        self.assertIn("ret i32 1", upstream.ir_text)
        self.assertIn("ret i32 1", pcc_out)

    def test_external_scalar_direct_uses_gain_local_unnamed_addr_like_upstream(self):
        ir = """
@g = global i32 42
define i32 @f() {
entry:
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("@g = local_unnamed_addr global i32 42", upstream.ir_text)
        self.assertIn("@g = local_unnamed_addr global i32 42", pcc_out)
        self.assertIn("define i32 @f() local_unnamed_addr {", upstream.ir_text)
        self.assertIn("define i32 @f() local_unnamed_addr {", pcc_out)

    def test_external_pointer_scalar_direct_uses_gain_local_unnamed_addr_like_upstream(self):
        ir = """
@g = global i32 42
@p = global ptr @g
define ptr @f() {
entry:
  %v = load ptr, ptr @p
  ret ptr %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("@g = global i32 42", upstream.ir_text)
        self.assertIn("@g = global i32 42", pcc_out)
        self.assertIn("@p = local_unnamed_addr global ptr @g", upstream.ir_text)
        self.assertIn("@p = local_unnamed_addr global ptr @g", pcc_out)
        self.assertIn("define ptr @f() local_unnamed_addr {", upstream.ir_text)
        self.assertIn("define ptr @f() local_unnamed_addr {", pcc_out)

    def test_internal_pointer_global_with_known_init_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global ptr null
define ptr @f() {
entry:
  %v = load ptr, ptr @g
  ret ptr %v
}
""")

    def test_single_store_then_load_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 0
define i32 @f() {
entry:
  store i32 42, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
""")

    def test_single_pointer_symbol_store_then_load_matches_upstream_shape(self):
        self._structural_parity("""
@t = internal global i32 0
@g = internal global ptr null
define ptr @f() {
entry:
  store ptr @t, ptr @g
  %v = load ptr, ptr @g
  ret ptr %v
}
""")

    def test_direct_store_then_zero_gep_load_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 0
define i32 @f() {
entry:
  store i32 7, ptr @g
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
""")

    def test_zero_gep_store_then_direct_load_matches_upstream_shape(self):
        self._structural_parity("""
@t = internal global i32 0
@g = internal global ptr null
define ptr @f() {
entry:
  %p = getelementptr ptr, ptr @g, i32 0
  store ptr @t, ptr %p
  %v = load ptr, ptr @g
  ret ptr %v
}
""")

    def test_zero_gep_store_then_direct_int_load_matches_upstream_shape(self):
        self._structural_parity("""
@g = internal global i32 0
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  store i32 7, ptr %p
  %v = load i32, ptr @g
  ret i32 %v
}
""")

    def test_multifunction_private_constant_matches_upstream_shape(self):
        self._structural_parity("""
@g = private constant i32 3
define i32 @f() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
define i32 @h() {
entry:
  %a = load i32, ptr @g
  ret i32 %a
}
""")

    def test_mutable_internal_direct_uses_gain_unnamed_addr_like_upstream(self):
        ir = """
@g = internal global i32 0
define i32 @f(i32 %x) {
entry:
  store i32 %x, ptr @g
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("@g = internal unnamed_addr global i32 0", upstream.ir_text)
        self.assertIn("@g = internal unnamed_addr global i32 0", pcc_out)

    def test_internal_direct_callee_gains_unnamed_addr_like_upstream(self):
        ir = """
define internal i32 @callee(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}

define i32 @caller(i32 %x) {
entry:
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("define internal fastcc i32 @callee(i32 %x) unnamed_addr {", upstream.ir_text)
        self.assertIn("define internal i32 @callee(i32 %x) unnamed_addr {", pcc_out)
        self.assertIn("define i32 @caller(i32 %x) local_unnamed_addr {", upstream.ir_text)
        self.assertIn("define i32 @caller(i32 %x) local_unnamed_addr {", pcc_out)

    def test_internal_constant_zero_gep_use_gains_unnamed_addr_like_upstream(self):
        ir = """
@g = internal constant i32 42
define i32 @f() {
entry:
  %p = getelementptr i32, ptr @g, i32 0
  %v = load i32, ptr %p
  ret i32 %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("@g = internal unnamed_addr constant i32 42", upstream.ir_text)
        self.assertIn("@g = internal unnamed_addr constant i32 42", pcc_out)

    def test_internal_pointer_constant_zero_gep_use_gains_unnamed_addr_like_upstream(self):
        ir = """
@t = internal global i32 0
@g = internal constant ptr @t
define ptr @f() {
entry:
  %p = getelementptr ptr, ptr @g, i32 0
  %v = load ptr, ptr %p
  ret ptr %v
}
"""
        pcc_out, _ = run_pcc_ir_pass(ir, GlobalOptPass())
        upstream = run_upstream_opt(ir, "globalopt")
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        self.assertIn("@g = internal unnamed_addr constant ptr @t", upstream.ir_text)
        self.assertIn("@g = internal unnamed_addr constant ptr @t", pcc_out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
