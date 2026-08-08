# Self IR verifier focused convergence — 2026-08-14

Mode: host pcc, self-backend focused tests, Darwin arm64, serial fail-first.

Commands and results:

- `gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend_verifier.py`
  — 9 passed.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend.py`
  — 294 passed in 6.20s.

The full-file convergence exposed one production defect: concatenating
per-translation-unit assembly concatenated complete versioned precise-stackmap
tables into one Mach-O section. The AArch64 object path now assembles each unit
to `NativeObject` and invokes the semantic relocatable linker, which merges and
re-encodes one canonical stackmap table while resolving cross-TU symbols.

Three other first failures were stale focused expectations: internal globals
now carry deterministic module prefixes, parsed calls carry the current tuple
shape, and coalesced phi slots/safepoint anchors intentionally suppress unsafe
or redundant copies. Each exact node passed before the full file was rerun.

This evidence does not claim pcc1/bootstrap success or fixed-point identity.
Those remain the task's explicit open boundary until current source is frozen.
