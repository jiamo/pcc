# Lua oracle xfail claim hygiene — 2026-07-17

Task: `AUD-P1-XFAIL-ORACLE-CLAIM-AUDIT`.

## Claim

Lua compilation, link, reference-build, and runtime regressions in
`tests/c/test_lua.py` now fail hard.  Missing vendored Lua inputs or absent
system `cc`/`make` remain explicit prerequisite skips and therefore do not
count as executed compiler/backend proof.

## Changes

- Removed every dynamic `pytest.xfail` path from the Lua suite.  The former
  paths accepted any new pcc, self-backend, native compiler, Makefile, test
  module, or runtime failure as expected; each now has a stage-labeled hard
  assertion.
- A failing Makefile-built reference script is also a hard reference failure,
  not an expected pcc gap.
- Added explicit `cc`/`make` fixture guards, while retaining the existing
  vendored `onelua.c`, `makefile`, `testes/`, and `all.lua` prerequisite guards.
- Added a source guard that rejects reintroduction of dynamic Lua xfails and
  preserves the prerequisite taxonomy.
- Split the former umbrella's IR-pass self-oracle, dependency-guard, and
  platform-guard buckets into independent task-board rows.

## Gates

- Task-board validation: `OK: 104 tasks validated`.
- Source guard, all individual Lua C-source LLVM verification cases, pcc
  `onelua` compile/link, pcc-vs-native `math.lua`/`calls.lua`, and the same two
  self-backend-vs-native smoke cases: `39 passed in 25.19s`.

The long `all.lua` suite, full GCC suite, bootstrap, and five-GC matrix were
not run.  This evidence is limited to claim hygiene plus the stated
representative Lua behavior gate.
