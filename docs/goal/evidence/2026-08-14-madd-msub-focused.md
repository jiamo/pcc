# PERF-P2-MADD-FOLD focused evidence — 2026-08-14

Mode: host pcc source, AArch64 Darwin self-backend assembly emission; no
bootstrap or measured performance claim.

Command:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend_target_passes.py::test_emit_self_asm_fuses_only_proven_i64_madd_msub_shapes
```

Result: `1 passed in 0.11s`.  Proven single-use plain i64 multiply-add/subtract
shapes emit `madd`/`msub`; multi-use, arithmetic-flags, and fence controls do
not fuse.

Open: source-current runtime parity, stage2 wall-time/RSS comparison, and
bootstrap baseline.
