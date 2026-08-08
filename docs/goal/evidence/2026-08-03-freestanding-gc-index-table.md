# Freestanding pcc-Python GC pointer-index ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

## Claim boundary

The production pcc-Python runtime archive no longer compiles or links
`pcc/py_runtime/src/py_gc_index_table.c`.  All primary/object/forwarding/
identity/frame/zpage pointer-index ABI definitions come from
`pcc/py_runtime/py/freestanding_gc_index_table.py`, compiled to a raw native
object.  The retained C file is a differential oracle used by the C-runtime
tier and tests.

This does not complete all five GC migration.  In particular it does not yet
prove strict freestanding closure for the collector algorithm modules, remove
the C `pcc_gc_external_resource.o` helper, run the pcc2/pcc3 five-backend fixed
point, or record the long-run RSS/fragmentation/pause/throughput matrix.

## Implementation contract

The port preserves the C oracle's 24-byte open-addressed slot layout and:

- first-tombstone insertion without breaking probe chains;
- count versus used-slot accounting and load-factor rehash;
- 256-entry normal and 16384-entry object-index initial capacities;
- object-key null/tagged-int rejection versus raw frame/page pointer keys;
- insert, upsert, replace, remove, clear, and no-op TLS drain semantics;
- allocation only through the owned `calloc/free` ABI;
- no managed containers, allocation, exceptions, boxing, GC calls, or
  libpython surface.

## Differential and backend gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_index_table.py \
  tests/python/test_freestanding_module.py \
  tests/python/test_gc_backend_generational.py::test_gc_frame_index_accepts_raw_slot_pointer_keys \
  tests/python/test_gc_backend_generational.py::test_gc_open_addressed_indexes_preserve_probe_chains_after_delete \
  tests/python/test_gc_backend_generational.py::test_gc_indexes_use_open_addressed_slots_and_tombstone_delete \
  tests/python/test_gc_backend4_production.py::test_backend4_forwarding_target_lookup_is_indexed \
  tests/python/test_gc_backend4_production.py::test_backend4_zpage_owner_lookup_is_indexed
```

Result: `28 passed in 5.49s`.

The LLVM and self objects both run the same C harness as the C oracle and
produce `gc-index-ok`.  Their only undefined symbols are `calloc` and `free`.

The pcc-Python production-runtime smoke executed the same binary under every
`PCC_GC_BACKEND=0..4`:

```text
1 passed in 0.47s
```

## Production link ownership

The real archive was rebuilt.  Its relevant members are:

```text
py_gc_backend.o
freestanding_gc_index_table.o
pcc_gc_external_resource.o
```

`py_gc_index_table.o` is absent.  `nm -A` attributes sampled symbols
`py_gc_index_find`, `pcc_gc_object_index_insert`,
`pcc_gc_frame_index_replace`, and `pcc_gc_zpage_page_index_upsert` to
`freestanding_gc_index_table.o`.  The content-addressed archive test also
links and runs the full behavior harness directly against the production
archive.

## Fresh pcc1 evidence

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=57382 \
  output=build/libc-gc-index-stage1/pcc1
```

That current-source self/no-libpython pcc1 compiled the new module in 0.68
seconds.  Its object contains the full ABI family with pointer, i64, and void
signatures intact; `nm -u` contains only `_calloc` and `_free`; no defined body
contains `py_*`, managed `pcc_gc_*`, landingpad, or invoke escapes.

## Remaining task boundary

The next concrete C ownership residual is
`pcc/py_runtime/src/pcc_gc_external_resource.c`.  After that, the existing
pcc-Python algorithm objects (`py_gc_backend.o`, `py_obj_gc.o`, and telemetry/
object ABI participants) still require a strict freestanding dependency audit
and any necessary split into raw closure versus managed facade.  The final
five-GC semantic, fixed-point, and long-run gates remain deliberately deferred
until these diagnostic slices are complete.
