# py_list TODO(phase3) raise placeholders: real exceptions

Date: 2026-07-31

Task: `BUG-P1-PY-LIST-TODO-RAISE-PLACEHOLDERS`

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
slices. Changed behavior:

- `pcc/py_runtime/src/py_list.c`: the six `TODO(phase3)` branches now act —
  `py_list_append`/`py_list_extend`(list branch)/`py_list_insert` grow
  failures raise `MemoryError`; `py_list_pop` raises
  `IndexError("pop from empty list")` and
  `IndexError("pop index out of range")`. The tuple branch of
  `py_list_extend` had the same silent grow-failure return without a TODO
  comment and now raises identically. `py_list_get` stays deliberately
  non-raising with the TODO replaced by the documented contract (the raising
  subscript paths are `py_list_getitem`/`py_list_get_checked`).
- `pcc/py_runtime/py/py_list.py`: the pcc-Python port mirrors every raise
  (tags 5/19 via the file's existing `py_raise(py_exc_new(...))` idiom).
- Frontend caller coverage: `_emit_post_call_err_check` added after every
  generated `py_list_pop` call — typed-list method
  (`list_method_lowering.py`, also guards the NULL-into-marshal path), dyn
  list method, dyn pop dispatch list branch (`dict_lowering.py`, matching the
  dict branch's existing check), and `del lst[i]` (`delete_lowering.py`).
- Both in-tree runtime archives rebuilt (`libpy_runtime.a`,
  `libpy_runtime_pcc_py.a`) so neither tier tests stale objects.

## Commands and results

```text
gtimeout 590s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_list_pop_raise_semantics.py     # new regression
2 passed in 0.92s      # parametrized: default(port) tier + PCC_RUNTIME_CC=cc tier

list family gates (index/setitem errors, methods parity, dispatch parity,
repeat, unpack, copy, from_iter):        25 passed in 19.01s
dict/bytearray pop gates:                29 passed in 42.98s

Five-backend behavior of the changed semantics (one self-backend
no-libpython binary, run under PCC_GC_BACKEND=0..4): identical correct
output on all five (IndexError pos/neg/empty/del/dyn + popped values).

Bootstrap (mandated for runtime+codegen semantics changes; self backend,
no-libpython, default GC):
  stage1 66.4s, stage2 207.5s, stage3 70.6s
  pcc2/pcc3 metadata-normalized byte-identical (standard verdict)

Commit-level ratchets:
  tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  27 passed in 260.61s
```

CPython oracle for the regression program matches the compiled output
line-for-line.

## Supported claim

Empty/out-of-range `list.pop` and `del list[i]` now raise catchable
`IndexError` (and grow failures raise `MemoryError`) in both the C runtime
and the pcc-Python port, with generated code checking the error state at
every pop call site, under the self backend without libpython, with
identical behavior across GC backends 0..4 and a green three-stage
fixed point.

## Update 2026-07-31 (same day): five-GC matrix confirmation

The queued matrix ran on the tree containing these fixes:

```text
gtimeout 900s env -u LC_ALL PCC_BOOTSTRAP_FULL_REBUILD=1 uv run pytest -q \
  -m integration tests/python/gc/test_pcc_bootstrap_full_gc0.py ... gc4.py
5 passed in 363.17s (0:06:03)
```

All five GC backends completed their real `pcc1 -> pcc2 -> pcc3` chains
with normalized fixed points under forced rebuild. The exact non-integration
suite (9604 passed in 756.78s, includes the new two-tier regression) and the
exact integration suite (4555 passed in 786.99s) are also green on this
tree. This closes the row's remaining boundary.

## Not proven

- True allocation-failure MemoryError paths are not exercised by a test
  (they require OOM); the raise idiom mirrors the tested getitem paths.
