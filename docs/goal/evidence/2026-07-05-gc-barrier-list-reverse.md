# Evidence: GC Barrier List Reverse

task: `AUD-P0-GC-BARRIER-WRITE-AUDIT`

status: `DONE_WEAK`

## Changed Files

- `pcc/py_runtime/src/py_list.c`
- `pcc/py_runtime/py/py_list.py`
- `tests/python/test_gc_codegen_write_barrier.py`

## Claim

`py_list_reverse` no longer swaps published list item owner slots with raw
writes. The C runtime and pcc-Python mirror retain the two borrowed items,
write both slots through `pcc_gc_store_ptr`, then release the temporaries.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_gc_codegen_write_barrier.py`
  - result: `4 passed`
- combined focused gate over barrier/list/valueclass/backend4 list mutation
  - result: `13 passed`
- `make -B -C pcc/py_runtime libpy_runtime.a`
  - result: passed with existing warnings
- pcc-Python runtime archive rebuild
  - result: passed with existing warnings

## Open Boundary

This is not a full barrier audit. The typed pointer-slot inventory beyond
`py_list_reverse` remains open, and every future omission needs a focused
regression plus backend-sensitive proof.
