# Freestanding pcc-Python GC state ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

## Claim boundary

All 130 raw `py_gc_*` / `pcc_gc_*` scalar and pointer state definitions in the
production pcc-Python runtime now come from strict
`pcc/py_runtime/py/freestanding_gc_state.py`. The broad managed
`py_substrate.py` object no longer owns GC state.

This slice moves storage ownership, not the remaining collector logic. It does
not complete the strict closure for weakrefs/finalizers/resurrection,
suspended-frame/scheduler roots, concurrent synchronization, relocation, the
five-GC fixed point, or long-running performance evidence.

## Exact migration proof

An AST comparison of the old substrate declarations against the new module
reported:

```text
old definitions:              130
new definitions:              130
remaining substrate definitions: 0
missing: []
extra: []
changed: []
```

Thus names, `i32` versus pointer storage, and initial values are unchanged.
Representative preserved values include:

- `py_gc_enabled = 1` and thresholds `700 / 10 / 10`;
- `pcc_gc_backend_selected = 0`;
- `pcc_gc_pause = 1000`, `pcc_gc_stepmul = 10000`;
- minor heap size `1048576`, minor allocation maximum `256`;
- `pcc_gc_next_object_id = 1`;
- all linked-list, page, forwarding, frame and root pointers initially null.

## LLVM, self and runtime behavior

`tests/python/test_freestanding_gc_state.py` compiles the definition-only
module through both LLVM and the self backend. Each object exports exactly the
130 expected data symbols and has zero undefined symbols. A C ABI harness
checks initial values and mutable scalar/pointer storage.

The same content-addressed production archive is linked into a strict
`backend=self`, `python-libpython=off` Python program. One compiled binary runs
under `PCC_GC_BACKEND=0..4`; every backend reads the default thresholds,
updates thresholds, disables/enables GC, retains a live list across
`gc.collect()`, and emits identical output.

## Production link-map proof

Archive:

```text
~/.cache/pcc/test-artifacts/runtime-builds/
  e9e7fe1a64acd61afb953a08-pcc-py/libpy_runtime_pcc_py.a
```

`ar -t` contains both `py_substrate.o` and `freestanding_gc_state.o`.
`nm -A -g` reports:

```text
freestanding_gc_state.o: 130 GC state definitions
py_substrate.o:            0 GC state definitions
```

Every state symbol has exactly one production definition. The production
archive links directly to the mutation harness and exits successfully.

## Focused gates

```text
3 passed, 1 deselected in 1.42s
  LLVM/self raw ABI + initial-value harness and archive plan

11 passed, 237 deselected in 50.99s
  affected frame-root, generational, backend-4 and substrate parity tests

4 passed in 44.27s
  first content-addressed production archive build and ownership/link probes

1 passed in 0.65s
  current production archive runtime under PCC_GC_BACKEND=0..4

5 passed in 2.33s
  final focused freestanding GC-state suite
```

The existing no-libpython `build/bootstrap/pcc1` (built 2026-08-03 13:24:36;
60,623,856 bytes) compiled the new strict module to an object with zero
undefined symbols and all 130 definitions. A new stage1 rebuild was not used:
this slice changed runtime source and archive ownership, not compiler source;
the current content-addressed runtime archive was rebuilt and tested directly.

## Remaining task boundary

Continue splitting the production GC algorithm/telemetry closure away from
managed semantic dependencies without duplicating graph rules. Then prove
weakref/finalizer/resurrection, suspended frames and scheduler roots,
concurrent synchronization and relocation before the one final five-GC
semantic/fixed-point/long-run matrix.
