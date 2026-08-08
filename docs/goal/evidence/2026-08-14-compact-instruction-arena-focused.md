# Compact instruction arena focused evidence — 2026-08-14

Mode: host self-backend data-structure and full focused tests.

- `tests/c/test_self_backend_compact_instruction_arena.py` — 13 passed.
- Adjacent `tests/c/test_self_backend.py` — 294 passed.

This proves dense kind/record projection, corrupt-ID rejection, source-span
diagnostics and current target-emitter behavior. It does not prove the task's
performance threshold: before/after retained bytes, phase wall, peak RSS and
sequential fixed-point measurements remain open.
