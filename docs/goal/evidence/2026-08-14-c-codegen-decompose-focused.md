# C codegen decomposition focused evidence — 2026-08-14

Mode: host C frontend, fail-fast serial focused gates.

Results:

- decomposition facade/owner contracts: 11 passed;
- C parser plus unsigned-load regressions: 61 passed;
- LZ4 real-project cases: 4 passed.

The gates prove the extracted SSA, initializer, declaration, type/layout,
expression/control and libc seams remain connected through the facade and that
the classic signedness-sensitive neighborhood did not regress in these
focused cases. Lua, SQLite, current-pcc1/bootstrap and final fixed-point
qualification remain open, so this is weak rather than release evidence.
