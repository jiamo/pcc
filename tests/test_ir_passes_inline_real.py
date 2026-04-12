"""Real-transform tests for InlinePass (subset)."""

import shutil

import unittest

from pcc.ir_passes.inline import AlwaysInlinePass, InlinePass, inline_module
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class InlineTests(unittest.TestCase):
    def test_trivial_inline(self):
        ir = """
define internal i32 @dbl(i32 %x) {
entry:
  %r = mul i32 %x, 2
  ret i32 %r
}
define i32 @main() {
entry:
  %a = call i32 @dbl(i32 21)
  ret i32 %a
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        # The call should be gone and replaced with an inlined mul.
        self.assertNotIn("call i32 @dbl", out)
        main_section = out[out.find("@main"):]
        self.assertIn("ret i32 42", main_section)

    def test_pointer_return_single_block_inline(self):
        ir = """
define internal ptr @id(ptr %p) {
entry:
  ret ptr %p
}
define ptr @f(ptr %p) {
entry:
  %r = call ptr @id(ptr %p)
  ret ptr %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call ptr @id", out)
        self.assertNotIn("define internal ptr @id", out)
        self.assertIn("ret ptr %p", out[out.find("@f"):])

    def test_float_return_single_block_inline(self):
        ir = """
define internal float @id(float %x) {
entry:
  ret float %x
}
define float @f(float %x) {
entry:
  %r = call float @id(float %x)
  ret float %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call float @id", out)
        self.assertNotIn("define internal float @id", out)
        self.assertIn("ret float %x", out[out.find("@f"):])

    def test_four_instruction_single_block_inline(self):
        ir = """
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 3
  %c = add i32 %b, 4
  %d = xor i32 %c, 7
  ret i32 %d
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertNotIn("define internal i32 @g", out)
        f_section = out[out.find("@f"):]
        self.assertIn("add i32 %x, 1", f_section)
        self.assertIn("mul i32", f_section)
        self.assertIn("xor i32", f_section)

    def test_five_instruction_single_block_inline(self):
        ir = """
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  ret i32 %e
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertNotIn("define internal i32 @g", out)
        f_section = out[out.find("@f"):]
        self.assertIn("add i32 %x, 1", f_section)
        self.assertIn("add i32 %inl1.a, 1", f_section)
        self.assertIn("ret i32 %inl1.e", f_section)

    def test_six_instruction_single_block_inline(self):
        ir = """
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  %f = add i32 %e, 1
  ret i32 %f
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertNotIn("define internal i32 @g", out)
        f_section = out[out.find("@f"):]
        self.assertIn("add i32 %x, 1", f_section)
        self.assertIn("add i32 %inl1.e, 1", f_section)
        self.assertIn("ret i32 %inl1.f", f_section)

    def test_ten_instruction_single_block_inline(self):
        ir = """
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  %f = add i32 %e, 1
  %g = add i32 %f, 1
  %h = add i32 %g, 1
  %i = add i32 %h, 1
  %j = add i32 %i, 1
  ret i32 %j
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertNotIn("define internal i32 @g", out)
        f_section = out[out.find("@f"):]
        self.assertIn("add i32 %x, 1", f_section)
        self.assertIn("add i32 %inl1.i, 1", f_section)
        self.assertIn("ret i32 %inl1.j", f_section)

    def test_external_callee_not_inlined(self):
        ir = """
define i32 @dbl(i32 %x) {
entry:
  %r = mul i32 %x, 2
  ret i32 %r
}
define i32 @main() {
entry:
  %a = call i32 @dbl(i32 21)
  ret i32 %a
}
"""
        _, changed = inline_module(ir)
        self.assertFalse(changed)

    def test_multi_block_not_inlined(self):
        ir = """
define internal i32 @abs(i32 %x) {
entry:
  %c = icmp slt i32 %x, 0
  br i1 %c, label %neg, label %pos
neg:
  %nx = sub i32 0, %x
  ret i32 %nx
pos:
  ret i32 %x
}
define i32 @main() {
entry:
  %v = call i32 @abs(i32 -5)
  ret i32 %v
}
"""
        _, changed = inline_module(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
define internal i32 @inc(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main() {
entry:
  %v = call i32 @inc(i32 10)
  ret i32 %v
}
"""
        out, _ = run_pcc_ir_pass(ir, InlinePass())
        self.assertIn("ret i32 11", out)
        self.assertNotIn("call i32 @inc", out)

    def test_void_single_block_inline(self):
        ir = """
@g = internal global i32 0

define internal void @setg(i32 %x) {
entry:
  store i32 %x, ptr @g
  ret void
}
define i32 @main() {
entry:
  call void @setg(i32 7)
  %v = load i32, ptr @g
  ret i32 %v
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @setg", out)
        main_section = out[out.find("@main"):]
        self.assertIn("store i32 7, ptr @g", main_section)

    def test_void_single_block_inline_elides_zero_gep(self):
        ir = """
define internal void @setp(ptr %p, i32 %x) {
entry:
  %a = getelementptr i32, ptr %p, i64 0
  store i32 %x, ptr %a
  ret void
}
define void @main(ptr %p, i32 %x) {
entry:
  call void @setp(ptr %p, i32 %x)
  ret void
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @setp", out)
        main_section = out[out.find("@main"):]
        self.assertIn("store i32 %x, ptr %p", main_section)
        self.assertNotIn("getelementptr i32, ptr %p, i64 0", main_section)

    def test_unused_nonvoid_call_inlined(self):
        ir = """
define internal i32 @inc(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main() {
entry:
  call i32 @inc(i32 10)
  ret i32 0
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @inc", out)
        self.assertIn("ret i32 0", out[out.find("@main"):])

    def test_unused_nonvoid_call_keeps_inlined_body(self):
        ir = """
define internal i32 @inc(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main(i32 %x) {
entry:
  call i32 @inc(i32 %x)
  ret i32 %x
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @inc", out)
        main_section = out[out.find("@main"):]
        self.assertIn("add i32 %x, 1", main_section)
        self.assertIn("ret i32 %x", main_section)

    def test_void_call_keeps_unused_inlined_body(self):
        ir = """
define internal void @touch(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret void
}
define i32 @main(i32 %x) {
entry:
  call void @touch(i32 %x)
  ret i32 %x
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @touch", out)
        main_section = out[out.find("@main"):]
        self.assertIn("add i32 %x, 1", main_section)
        self.assertIn("ret i32 %x", main_section)

    def test_small_multiblock_single_exit_return_is_inlined(self):
        ir = """
define internal i32 @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  ret i32 %p
}
define i32 @f(i1 %c, i32 %x) {
entry:
  %r = call i32 @g(i1 %c, i32 %x)
  ret i32 %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertNotIn("define internal i32 @g", out)
        self.assertIn("phi i32 [ %x", out)

    def test_small_multiblock_unused_return_is_inlined(self):
        ir = """
define internal i32 @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  ret i32 %p
}
define i32 @f(i1 %c, i32 %x) {
entry:
  call i32 @g(i1 %c, i32 %x)
  ret i32 %x
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call i32 @g", out)
        self.assertIn("ret i32 %x", out[out.find("@f"):])

    def test_small_multiblock_void_is_inlined(self):
        ir = """
define internal void @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  %a = add i32 %x, 1
  br label %join
e:
  br label %join
join:
  ret void
}
define i32 @f(i1 %c, i32 %x) {
entry:
  call void @g(i1 %c, i32 %x)
  ret i32 %x
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call void @g", out)
        self.assertIn("add i32 %x, 1", out[out.find("@f"):])

    def test_small_multiblock_pointer_return_is_inlined(self):
        ir = """
define internal ptr @g(i1 %c, ptr %p) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %r = phi ptr [ %p, %t ], [ null, %e ]
  ret ptr %r
}
define ptr @f(i1 %c, ptr %p) {
entry:
  %x = call ptr @g(i1 %c, ptr %p)
  ret ptr %x
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call ptr @g", out)
        self.assertNotIn("define internal ptr @g", out)
        self.assertIn("phi ptr [ %p, %t.i ], [ null, %e.i ]", out)

    def test_two_exit_pointer_return_is_inlined(self):
        ir = """
define internal ptr @leaf(i1 %c, ptr %p, ptr %q) {
entry:
  br i1 %c, label %t, label %e
t:
  ret ptr %p
e:
  ret ptr %q
}
define ptr @main(i1 %c, ptr %p, ptr %q) {
entry:
  %r = call ptr @leaf(i1 %c, ptr %p, ptr %q)
  ret ptr %r
}
"""
        out, changed = inline_module(ir)
        self.assertTrue(changed)
        self.assertNotIn("call ptr @leaf", out)
        self.assertNotIn("define internal ptr @leaf", out)
        self.assertIn("br i1 %c, label %t.i, label %e.i", out)
        self.assertIn("phi ptr [ %p, %t.i ], [ %q, %e.i ]", out)


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _structural_parity(self, ir: str, pass_, pass_name: str):
        report = assert_ir_parity(ir, pass_, pass_name)
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_internal_single_block_inline_matches_upstream(self):
        self._structural_parity("""
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
""", InlinePass(), "inline")

    def test_pointer_return_single_block_matches_upstream(self):
        self._structural_parity("""
define internal ptr @id(ptr %p) {
entry:
  ret ptr %p
}
define ptr @f(ptr %p) {
entry:
  %r = call ptr @id(ptr %p)
  ret ptr %r
}
""", InlinePass(), "inline")

    def test_float_return_single_block_matches_upstream(self):
        self._structural_parity("""
define internal float @id(float %x) {
entry:
  ret float %x
}
define float @f(float %x) {
entry:
  %r = call float @id(float %x)
  ret float %r
}
""", InlinePass(), "inline")

    def test_four_instruction_single_block_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 3
  %c = add i32 %b, 4
  %d = xor i32 %c, 7
  ret i32 %d
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
""", InlinePass(), "inline")

    def test_five_instruction_single_block_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  ret i32 %e
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
""", InlinePass(), "inline")

    def test_six_instruction_single_block_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  %f = add i32 %e, 1
  ret i32 %f
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
""", InlinePass(), "inline")

    def test_ten_instruction_single_block_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i32 %x) {
entry:
  %a = add i32 %x, 1
  %b = add i32 %a, 1
  %c = add i32 %b, 1
  %d = add i32 %c, 1
  %e = add i32 %d, 1
  %f = add i32 %e, 1
  %g = add i32 %f, 1
  %h = add i32 %g, 1
  %i = add i32 %h, 1
  %j = add i32 %i, 1
  ret i32 %j
}
define i32 @f(i32 %x) {
entry:
  %r = call i32 @g(i32 %x)
  ret i32 %r
}
""", InlinePass(), "inline")

    def test_internal_single_block_void_inline_matches_upstream(self):
        self._structural_parity("""
@g = internal global i32 0

define internal void @setg(i32 %x) {
entry:
  store i32 %x, ptr @g
  ret void
}
define i32 @main() {
entry:
  call void @setg(i32 7)
  %v = load i32, ptr @g
  ret i32 %v
}
""", InlinePass(), "inline")

    def test_void_single_block_zero_gep_elided_matches_upstream(self):
        self._structural_parity("""
define internal void @setp(ptr %p, i32 %x) {
entry:
  %a = getelementptr i32, ptr %p, i64 0
  store i32 %x, ptr %a
  ret void
}
define void @main(ptr %p, i32 %x) {
entry:
  call void @setp(ptr %p, i32 %x)
  ret void
}
""", InlinePass(), "inline")

    def test_unused_nonvoid_call_matches_upstream(self):
        self._structural_parity("""
define internal i32 @inc(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main() {
entry:
  call i32 @inc(i32 10)
  ret i32 0
}
""", InlinePass(), "inline")

    def test_unused_nonvoid_body_is_preserved_like_upstream(self):
        self._structural_parity("""
define internal i32 @inc(i32 %x) {
entry:
  %r = add i32 %x, 1
  ret i32 %r
}
define i32 @main(i32 %x) {
entry:
  call i32 @inc(i32 %x)
  ret i32 %x
}
""", InlinePass(), "inline")

    def test_unused_void_body_is_preserved_like_upstream(self):
        self._structural_parity("""
define internal void @touch(i32 %x) {
entry:
  %a = add i32 %x, 1
  ret void
}
define i32 @main(i32 %x) {
entry:
  call void @touch(i32 %x)
  ret i32 %x
}
""", InlinePass(), "inline")

    def test_alwaysinline_attribute_matches_upstream(self):
        self._structural_parity("""
; Function Attrs: alwaysinline
define i32 @callee(i32 %x) #0 {
entry:
  %a = add i32 %x, 1
  ret i32 %a
}
define i32 @caller(i32 %x) {
entry:
  %r = call i32 @callee(i32 %x)
  ret i32 %r
}

attributes #0 = { alwaysinline }
""", AlwaysInlinePass(), "always-inline")

    def test_small_multiblock_single_exit_return_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  ret i32 %p
}
define i32 @f(i1 %c, i32 %x) {
entry:
  %r = call i32 @g(i1 %c, i32 %x)
  ret i32 %r
}
""", InlinePass(), "inline")

    def test_small_multiblock_unused_return_matches_upstream(self):
        self._structural_parity("""
define internal i32 @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %p = phi i32 [ %x, %t ], [ 0, %e ]
  ret i32 %p
}
define i32 @f(i1 %c, i32 %x) {
entry:
  call i32 @g(i1 %c, i32 %x)
  ret i32 %x
}
""", InlinePass(), "inline")

    def test_small_multiblock_void_matches_upstream(self):
        self._structural_parity("""
define internal void @g(i1 %c, i32 %x) {
entry:
  br i1 %c, label %t, label %e
t:
  %a = add i32 %x, 1
  br label %join
e:
  br label %join
join:
  ret void
}
define i32 @f(i1 %c, i32 %x) {
entry:
  call void @g(i1 %c, i32 %x)
  ret i32 %x
}
""", InlinePass(), "inline")

    def test_small_multiblock_pointer_return_matches_upstream(self):
        self._structural_parity("""
define internal ptr @g(i1 %c, ptr %p) {
entry:
  br i1 %c, label %t, label %e
t:
  br label %join
e:
  br label %join
join:
  %r = phi ptr [ %p, %t ], [ null, %e ]
  ret ptr %r
}
define ptr @f(i1 %c, ptr %p) {
entry:
  %x = call ptr @g(i1 %c, ptr %p)
  ret ptr %x
}
""", InlinePass(), "inline")

    def test_two_exit_pointer_return_matches_upstream(self):
        self._structural_parity("""
define internal ptr @leaf(i1 %c, ptr %p, ptr %q) {
entry:
  br i1 %c, label %t, label %e
t:
  ret ptr %p
e:
  ret ptr %q
}
define ptr @main(i1 %c, ptr %p, ptr %q) {
entry:
  %r = call ptr @leaf(i1 %c, ptr %p, ptr %q)
  ret ptr %r
}
""", InlinePass(), "inline")

    def test_two_exit_void_multiblock_inline(self):
        ir = """
define internal void @leaf(i1 %c) {
entry:
  br i1 %c, label %t, label %e
t:
  ret void
e:
  ret void
}
define void @f(i1 %c) {
entry:
  call void @leaf(i1 %c)
  ret void
}
"""
        out, _ = run_pcc_ir_pass(ir, InlinePass())
        self.assertNotIn("call void @leaf", out)
        self.assertNotIn("define internal void @leaf", out)
        self.assertIn("leaf.exit:", out)
        self.assertIn("ret void", out[out.find("@f"):])

    def test_two_exit_void_multiblock_matches_upstream(self):
        self._structural_parity("""
define internal void @leaf(i1 %c) {
entry:
  br i1 %c, label %t, label %e
t:
  ret void
e:
  ret void
}
define void @f(i1 %c) {
entry:
  call void @leaf(i1 %c)
  ret void
}
""", InlinePass(), "inline")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
