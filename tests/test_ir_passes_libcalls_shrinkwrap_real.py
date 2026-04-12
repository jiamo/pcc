"""Focused parity tests for LibcallsShrinkwrapPass.

LLVM's standalone ``libcalls-shrinkwrap`` pass does not rewrite the
direct libcalls exercised here, so the current pcc IR subset models it
as an honest no-op boundary.
"""

import shutil
import unittest

from pcc.ir_passes.libcalls_shrinkwrap import (
    LibcallsShrinkwrapPass,
    libcalls_shrinkwrap_text,
)
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


_OPT = shutil.which("opt")


class LibcallsShrinkwrapTests(unittest.TestCase):
    def test_zero_length_memset_is_left_unchanged(self):
        ir = """
declare ptr @memset(ptr, i32, i64)

define ptr @f(ptr %dst) {
entry:
  %r = call ptr @memset(ptr %dst, i32 0, i64 0)
  ret ptr %r
}
"""
        out, changed = libcalls_shrinkwrap_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_zero_length_memcmp_is_left_unchanged(self):
        ir = """
declare i32 @memcmp(ptr, ptr, i64)

define i32 @f(ptr %a, ptr %b) {
entry:
  %r = call i32 @memcmp(ptr %a, ptr %b, i64 0)
  ret i32 %r
}
"""
        out, changed = libcalls_shrinkwrap_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_equal_strcmp_is_left_unchanged(self):
        ir = """
declare i32 @strcmp(ptr, ptr)

define i32 @f(ptr %s) {
entry:
  %r = call i32 @strcmp(ptr %s, ptr %s)
  ret i32 %r
}
"""
        out, changed = libcalls_shrinkwrap_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_empty_strlen_is_left_unchanged(self):
        ir = r"""
@.empty = private unnamed_addr constant [1 x i8] c"\00"
declare i64 @strlen(ptr)

define i64 @f() {
entry:
  %r = call i64 @strlen(ptr @.empty)
  ret i64 %r
}
"""
        out, changed = libcalls_shrinkwrap_text(ir)
        self.assertFalse(changed)
        self.assertEqual(out.strip(), ir.strip())

    def test_pass_integration_is_noop(self):
        ir = """
declare i32 @strcmp(ptr, ptr)

define i32 @f(ptr %s) {
entry:
  %r = call i32 @strcmp(ptr %s, ptr %s)
  ret i32 %r
}
"""
        out, pa = run_pcc_ir_pass(ir, LibcallsShrinkwrapPass())
        self.assertIn("declare i32 @strcmp(ptr, ptr)", out)
        self.assertIn("define i32 @f(ptr %s)", out)
        self.assertIn("%r = call i32 @strcmp(ptr %s, ptr %s)", out)
        self.assertEqual(repr(pa), "PreservedAnalyses.all()")


@unittest.skipUnless(_OPT, "requires LLVM opt")
class UpstreamParityTests(unittest.TestCase):
    def _assert_noop_parity(self, ir: str):
        report = assert_ir_parity(ir, LibcallsShrinkwrapPass(), "libcalls-shrinkwrap")
        self.assertEqual(report.diff.missing_functions, [])
        self.assertEqual(report.diff.extra_functions, [])
        self.assertEqual(report.diff.function_diffs, [])
        self.assertIsNone(report.diff.global_count_diff)

    def test_zero_length_memset_matches_upstream(self):
        self._assert_noop_parity("""
declare ptr @memset(ptr, i32, i64)

define ptr @f(ptr %dst) {
entry:
  %r = call ptr @memset(ptr %dst, i32 0, i64 0)
  ret ptr %r
}
""")

    def test_zero_length_memcmp_matches_upstream(self):
        self._assert_noop_parity("""
declare i32 @memcmp(ptr, ptr, i64)

define i32 @f(ptr %a, ptr %b) {
entry:
  %r = call i32 @memcmp(ptr %a, ptr %b, i64 0)
  ret i32 %r
}
""")

    def test_equal_strcmp_matches_upstream(self):
        self._assert_noop_parity("""
declare i32 @strcmp(ptr, ptr)

define i32 @f(ptr %s) {
entry:
  %r = call i32 @strcmp(ptr %s, ptr %s)
  ret i32 %r
}
""")

    def test_empty_strlen_matches_upstream(self):
        self._assert_noop_parity(r"""
@.empty = private unnamed_addr constant [1 x i8] c"\00"
declare i64 @strlen(ptr)

define i64 @f() {
entry:
  %r = call i64 @strlen(ptr @.empty)
  ret i64 %r
}
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
