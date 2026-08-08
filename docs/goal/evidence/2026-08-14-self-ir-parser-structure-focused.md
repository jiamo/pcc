# Self IR parser structure focused evidence — 2026-08-14

Mode: host parser plus selected self-backend structural cases.

The first fail-fast run stopped after 7 passes because a call argument written
as `i1 false` remained the textual alias `false` instead of the internal
numeric form `0`. The call parser now canonicalizes both `false` and `true`
for typed i1 operands and a two-alias regression was added.

Final results:

- `test_self_backend_type_parser.py`: 21 passed;
- four adjacent nested/aggregate parser cases: 4 passed.

Broader emitted-object execution, current-pcc1 and sequential fixed-point
qualification remain open.
