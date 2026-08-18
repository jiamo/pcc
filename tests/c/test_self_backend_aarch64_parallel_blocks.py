from __future__ import annotations

from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


_MULTI_BLOCK_IR = r'''
target triple = "arm64-apple-macosx13.0.0"

define i64 @parallel_blocks(i64 %x) {
entry:
  br label %b0
b0:
  %v0 = add i64 %x, 1
  br label %b1
b1:
  %v1 = add i64 %v0, 2
  br label %b2
b2:
  %v2 = add i64 %v1, 3
  br label %b3
b3:
  %v3 = add i64 %v2, 4
  br label %b4
b4:
  %v4 = add i64 %v3, 5
  br label %b5
b5:
  %v5 = add i64 %v4, 6
  br label %b6
b6:
  %v6 = add i64 %v5, 7
  br label %b7
b7:
  %v7 = add i64 %v6, 8
  br label %b8
b8:
  %v8 = add i64 %v7, 9
  br label %b9
b9:
  %v9 = add i64 %v8, 10
  br label %done
done:
  ret i64 %v9
}
'''


def test_dense_multi_block_emit_is_repeatable() -> None:
    first = emit_aarch64_darwin_asm(_MULTI_BLOCK_IR, optimize=False)
    second = emit_aarch64_darwin_asm(_MULTI_BLOCK_IR, optimize=False)

    assert second == first
