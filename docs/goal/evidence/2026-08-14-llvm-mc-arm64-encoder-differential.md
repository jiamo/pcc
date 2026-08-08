# LLVM MC differential for the AArch64 encoder

Mode: Darwin arm64 host, pcc self-backend encoder compared only against the
pinned LLVM MC oracle. LLVM is not used by the production self encoder.

Oracle provenance: llvmlite `0.46.0`, bundled LLVM `(20, 1, 8)`, Darwin arm64.

Fail-fast command:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_arm64_encode.py::test_every_instruction_word_matches_pinned_llvm_mc \
  tests/python/test_arm64_encode.py::test_llvm_mc_and_pcc_objects_disassemble_to_the_same_instructions
```

Result: `2 passed in 0.41s`. The first node enumerates the current emitted
instruction corpus and reports zero byte mismatches. The second publishes both
objects and confirms their disassembled instruction sequence is identical.

Claim boundary: this proves current emitted-instruction encoding only. It does
not cover instructions pcc never emits, relocation/link ownership, or permit an
LLVM fallback in production self mode.
