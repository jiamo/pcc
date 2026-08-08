"""Focused AArch64 layout contracts for canonical post-call error checks."""

from pcc.backend.self_backend import emit_aarch64_darwin_asm


_MODULE_PREFIX = """
target triple = "arm64-apple-darwin23.6.0"
target datalayout = "e-m:o-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-n32:64-S128-Fn32"

declare i64 @py_err_occurred()

define i64 @possibly_raising() {
entry:
  ret i64 7
}
"""


def _previous_nonempty(lines: list[str], label: str) -> str:
    index = lines.index(label)
    index -= 1
    while index >= 0 and not lines[index]:
        index -= 1
    assert index >= 0
    return lines[index]


def test_canonical_post_call_error_check_makes_success_fall_through() -> None:
    ir_text = _MODULE_PREFIX + """
define i32 @main() {
entry:
  %value = call i64 @possibly_raising()
  %err.flag = call i64 @py_err_occurred()
  %err.cmp = icmp ne i64 %err.flag, 0
  br i1 %err.cmp, label %error, label %success

error:
  ret i32 1

success:
  ret i32 0
}
"""

    lines = emit_aarch64_darwin_asm(ir_text).splitlines()
    success_label = "L_main_success:"
    error_label = "L_main_error:"

    assert lines.index(success_label) < lines.index(error_label)
    branch = _previous_nonempty(lines, success_label)
    assert branch.startswith(("  b.", "  cbnz "))
    assert branch.endswith("L_main_error")
    assert "  b L_main_success" not in lines


def test_noncanonical_error_query_keeps_source_block_order() -> None:
    ir_text = _MODULE_PREFIX + """
define i32 @main() {
entry:
  %value = call i64 @possibly_raising()
  %intervening = add i64 %value, 1
  %err.flag = call i64 @py_err_occurred()
  %err.cmp = icmp ne i64 %err.flag, 0
  br i1 %err.cmp, label %error, label %success

error:
  ret i32 1

success:
  ret i32 0
}
"""

    lines = emit_aarch64_darwin_asm(ir_text).splitlines()
    error_label = "L_main_error:"
    success_label = "L_main_success:"

    assert lines.index(error_label) < lines.index(success_label)
    branch = _previous_nonempty(lines, error_label)
    assert branch.startswith(("  b.", "  cbz "))
    assert branch.endswith("L_main_success")
