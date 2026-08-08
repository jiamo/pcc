# Guarded loop plan focused evidence — 2026-08-14

Mode: host semantics plus emit-only LLVM/AArch64/x86-64 self contracts.

`tests/python/test_guarded_loop_plan.py` with the two runtime/performance
families deselected completed with 30 passed and 4 deselected.

The focused result proves the fixed `pcc.i64_buffer[N]` model, exact guard
order, alias/overflow scalar restart, deterministic plan validation, invalid
candidate rejection, zero-`py_cpy` production IR and acceptance by both self
target owners.

It does not prove executable LLVM/self parity or profitability. The two runtime
nodes and two integration benchmark nodes, followed by current pcc1 and
sequential fixed-point evidence, remain open after the final runtime build.
