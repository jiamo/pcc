# Evidence: V-P1-VAL VP-S2 Aggregate ABI and Slot Schema

task: `V-P1-VAL`

slice: `VP-S2`

status: `DONE_STRONG` for this finite slice; parent task remains `IN_PROGRESS`.

source identity: shared local worktree on 2026-07-16; no clean-commit or
release claim.

## Proven behavior

- The selected self-host scaffold valueclass payload ABI now covers one
  through seven scalar/aggregate fields instead of silently boxing after four.
- A five-field payload crosses direct typed return and argument boundaries on
  the self backend without `py_instance_new`/`py_valuebox_new` in the hot
  function bodies; the executable prints `15`.
- Native function-value adapters unbox aggregate arguments and box aggregate
  returns through the established valueclass projection, with control-flow
  blocks and allocas owned by the adapter function.
- Nested pointer-bearing direct payloads survive and remain updateable across
  explicit collection under GC0, GC1, GC2, GC3, and GC4.
- Instance and ValueBox trace, update, and promotion consume the same
  `py_obj_visit_slots` instance-owner slot walker.

## Gates

- `gtimeout 150s env -u LC_ALL uv run pytest -q -n0` with the five-field self
  runtime test, six/seven-field IR test, and nested aggregate self test
  - result: `3 passed in 1.17s`
- `gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed in 1.53s`
- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/python/test_gc_update_referents.py::test_trace_update_and_promotion_share_instance_owner_slot_walker_source`
  - result: `1 passed in 0.06s`
- `gtimeout 60s env -u LC_ALL uv run black` on the four modified Python files
  - result: formatted successfully

## Claim boundary

This closes VP-S2's selected one- through seven-field self-host scaffold ABI
and its recursive nested pointer-slot evidence. It does not claim an unbounded
aggregate arity, recursive type cycles, VP-S3 identity-escape completeness, or
the parent task's closing five-GC bootstrap matrix.

## Open boundary

None for VP-S2.
