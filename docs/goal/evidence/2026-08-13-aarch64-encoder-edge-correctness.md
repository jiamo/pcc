# AArch64 encoder edge correctness

Mode: Darwin arm64 self backend, pcc encoder compared byte-for-byte with
system `as(1)` and pinned llvmlite LLVM MC.

One shared inventory now owns direct scalar-FP immediate literals and their
imm8 encodings. MOV distinguishes SP/WSP from XZR/WZR and derives 32/64-bit
encoding from the operands. `float32_to_bits` performs round-to-nearest-even
for subnormal outputs, including zero/min-subnormal and max-subnormal/min-normal
boundaries. Dead/redundant encoder expressions were removed.

Gates:

- Focused fmov/mov/subnormal/fp-load-store selection: 3 passed.
- `tests/python/test_arm64_encode.py tests/python/test_arm64_asm_driver.py`: 15 passed.
- Target-triple matcher and FP materializer adjacent checks: 2 passed.

