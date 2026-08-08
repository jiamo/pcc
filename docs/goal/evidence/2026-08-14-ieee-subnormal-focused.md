# IEEE subnormal conversion focused evidence — 2026-08-14

Mode: host-side semantic/oracle and emit-only closure; no current-pcc1 build.

The task-board required focused filter completed with 8 passed and 7 unrelated
tests deselected. The broader `test_pcc_stdlib_struct.py` non-integration suite
completed with 78 passed and only the current-pcc1 strict no-libpython node
deselected.

The source uses the shared `pcc.stdlib._float_bits` contract for stdlib struct,
LLVM-CAPI constants and self-backend encoding. This evidence covers subnormal
endpoints and halfway rounding, signed zero, non-finites and finite overflow.
It does not prove the current-source pcc1 executable or sequential
pcc1 -> pcc2 -> pcc3 fixed point.
