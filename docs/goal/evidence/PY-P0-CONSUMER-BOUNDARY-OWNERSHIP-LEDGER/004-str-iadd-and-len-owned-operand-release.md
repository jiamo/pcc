# 004 — str/bytes `+=` releases the previous value; `len()` releases an owned operand

Date: 2026-09-03.  Found while attributing a pcc1 codegen worker's memory
(PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY evidence 003); full record in
`docs/investigations/pcc-codegen-ownership-leaks-str-iadd-and-call-result.md`.

## Measured (compiled binary max RSS, 300k iterations unless noted)

```text
cur = cur + ch  (20k chars)                3 MB
cur += ch       (20k chars)              314 MB  -> fixed, < 90 MB
len(f(i)), f -> list                     122 MB  -> fixed, < 90 MB
len(f(i)), f -> str  (control)            36 MB  -> unchanged, correct
```

## Change

- `pcc/py_frontend/codegen/assignment_statement_lowering.py`: str/bytes Name
  targets of `+=` reuse the Assign path (owned-result replacement).
- `pcc/py_frontend/codegen/builtin_type_attr_lowering.py`: `_emit_len_call`
  releases an owned operand after the typed/generic length call.
- `tests/python/test_ownership_str_iadd_and_len_call_result.py` (red-first;
  three cases including the str control).

## Gates

28 focused ownership/augassign tests; `test_bootstrap_gate_baseline.py` 2;
`test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py` 43 (568 s,
complete summary); Stage1 v8 (host -> pcc1) rc 0, canary 42, libSystem-only.
Not run: pcc1 -> pcc2 (a capped Stage2 needs authorization), GC1-4.

## Open

Owned str temporaries consumed by method receivers and other argument
positions still leak ~110 B per evaluation (36 MB vs 3 MB baseline in the
probes); the ledger row owns the generic consumer-boundary release.
