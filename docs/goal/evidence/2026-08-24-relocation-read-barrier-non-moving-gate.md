# The relocation read barrier paid a provenance lookup under non-moving GCs

Date: 2026-08-24

Rows: `PERF-P1-RELOCATION-READ-BARRIER-NONMOVING-GATE`,
`PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST` (route)

Status: one accepted runtime slice, measured on a heavy-object workload with
all focused correctness gates green. No stage1/stage2 timing, no module98 A/B,
no fixed point.

## What was wrong

`pcc_gc_note_relocation_read` is the relocation read barrier. Every pointer
read that can observe a moved object routes through it -- `pcc_gc_ln` /
`pcc_gc_n` call sites are spread across `py_obj.py`, `py_class.py`,
`py_weakref.py`, `py_exc_tls.py` and their C mirrors.

Its old fast path was:

```python
    if pcc_gc_object_is_known_no_lock(obj) != 0:
        if (load_i32(obj, 12) & 2048) == 0:
            return obj
    _graph_lock()
    resolved = _note_relocation_read_unlocked(obj)
    _graph_unlock()
```

So answering "has this pointer moved?" cost a **full provenance lookup**, and
whenever the pointer was not a known object it additionally took the **graph
lock** -- on every pointer read, under every backend. Under backends 0/1/2
nothing can ever have moved, so all of that work provably returned `obj`.

The asymmetry was visible in the tree: `pcc_gc_load_ptr` (`py_obj.py:383`)
already gates on `pcc_gc_read_barrier_enabled` and the selected backend before
doing any resolution work. The barrier it guards did not. This slice is the
missing half of that pair.

## Why the gate is exact, not optimistic

Three source facts, each now asserted by a regression:

1. `pcc_gc_install_forwarding_unlocked` returns `-1` before touching anything
   unless the selected backend is 3 or 4
   (`freestanding_gc_forwarding_identity.py`, `if backend != 3 and backend != 4`).
2. `pcc_gc_set_backend` refuses a backend *change* while
   `_forwarding_head()` is non-null or `pcc_gc_forwarding_population != 0`
   (`py_gc_backend.py:2224`). So "selected backend is not 3/4" implies the
   forwarding list and the forwarding index are both empty, which means
   `pcc_gc_forwarding_find` cannot match.
3. Flag `2048` (`PY_FLAG_GC_RELOCATION_CANDIDATE`) is set only on the two
   install paths, so it cannot be set under a non-moving backend either. Its
   only consumers are the GC3 promotion and GC4 selector paths.

A pre-config read of `0` from the global is safe for the same reason as (1): an
install must itself observe 3/4 in that same global, so nothing can be
forwarded before config runs.

Therefore under 0/1/2 the entire chain was the identity, and backends 3/4 reach
byte-identical code. The one skipped action is clearing a stale `2048` hint on
an object under a non-moving backend, where no code reads that flag.

## The change

Both mirrors, gate placed before the provenance lookup:

```python
    selected: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if selected != 3 and selected != 4:
        return obj
```

```c
    if (
        pcc_gc_selected_backend != PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        && pcc_gc_selected_backend != PCC_GC_KIND_COLORED_RELOCATING
    ) {
        return o;
    }
```

A first version also required `pcc_gc_config_initialized != 0`. That was
removed: it is redundant by the argument above, it added a second global load
to the hot path, and it would have widened the module's declared global-import
closure. The final version adds no new global import at all --
`pcc_gc_backend_selected` was already in `RAW_GLOBAL_IMPORTS`.

## Measured

Workload: `benchmarks/python/granule_heavy_object.py`, compiled by pcc in
DEFAULT mode so the pcc-Python runtime ports are linked (`PCC_RUNTIME_CC=cc`
would measure the C sources instead). Both arms are that same source compiled
against runtime archives differing only by this change. Backend 0.

Profile before/after, `sample` 4s aggregated with
`scripts/pcc_sample_aggregate.py`:

```text
before (3260 self samples)          after (3238 self samples)
  pcc_gc_note_relocation_read  114    -- gone --
  ..._read_unlocked             77    -- gone --
  pcc_gc_object_index_find     101    -- gone --
  pcc_gc_object_is_known_no_lock 96   -- gone --
  pcc_gc_index_py_find          68    -- gone --
  pcc_py_gc_minor_graph_lock    68    -- gone --
  pcc_py_gc_minor_graph_unlock  68    -- gone --
  pcc_gc_forwarding_index_find  54    -- gone --
  category gc_index          4.8%     category gc_index absent
```

Alternating paired runs (`BC/CB/...`), one discarded warmup per arm, wall clock
per process, output asserted equal (`19206400000`) on every run:

```text
pair  base   cand   C/B
   1  6.064  4.297 0.7087
   2  5.768  4.376 0.7586
   3  5.772  4.315 0.7477
   4  5.789  4.309 0.7443
   5  5.817  4.300 0.7392
   6  5.913  4.281 0.7240
   7  5.887  4.415 0.7500
   8  5.776  4.289 0.7425

base median        5.803
cand median        4.304
paired-median C/B  0.7434   =>  1.3452x     8/8 pairs favour the candidate
```

An earlier arm that still carried the redundant `config_initialized` load
measured 0.7632 => 1.3102x over 10 pairs, 10/10 favouring it.

## Gates

Run on the final source unless noted:

```text
tests/python/test_freestanding_gc_forwarding_identity.py     6 passed 143.22s
tests/python/test_freestanding_gc_relocation_payload.py
  + test_freestanding_gc_forwarding_retirement.py           24 passed 190.57s
tests/python/test_gc_granule_map.py
  + test_runtime_pointer_provenance.py
  + test_runtime_layout_contract.py                         13 passed 155.30s
tests/python/test_bootstrap_gate_baseline.py
  + test_fallback_baseline.py
  + test_ir_py_fallback_baseline.py           42 passed, 2 deselected 544.90s
five-backend finalizer/resurrection/weakref/trashcan, PCC_GC_BACKEND=0..4
  backend 0  44 passed 114.58s    backend 3  44 passed  91.33s
  backend 1  44 passed  93.98s    backend 4  44 passed  97.16s
  backend 2  44 passed  92.29s
```

The provenance/layout and baseline runs above were captured on the version
carrying the redundant `config_initialized` load; the final source differs only
by removing that condition, which *widens* the early exit to the pre-config
window. The five-backend and forwarding/relocation gates were therefore re-run
on the final source and are green there too
(`build/reloc-read-final-gates.log`, exit 0):

```text
backend 0  44 passed  95.39s     backend 3  44 passed  91.73s
backend 1  44 passed  91.97s     backend 4  44 passed  93.97s
backend 2  44 passed  90.86s
relocation payload + forwarding retirement    24 passed 10.94s
```

Note backend 1 now passes all 44. The one resurrection reclaim-count failure
recorded on 2026-08-23 as `GC-P0-BACKEND1-RESURRECTED-RECLAIM-LAG` is green
here, consistent with
`docs/goal/evidence/2026-08-23-gc-backend1-resurrection-phantom-cycle-fix.md`.

## Regression

`tests/python/test_freestanding_gc_forwarding_identity.py::test_relocation_read_barrier_is_gated_on_a_moving_backend`
asserts all three legs of the proof plus the gate's position in both mirrors.
Deleting the installer's backend refusal, the `set_backend` forwarding guard,
or either gate fails it. This is deliberately a source-contract test: the
behavioral coverage already exists in the 24 forwarding/relocation cases and
the five-backend matrix, but nothing was pinning the *reason* the fast exit is
sound.

## Nonclaims

This is a runtime-workload measurement on one machine under backend 0. It is
not a pcc1 profile, not a stage1 or stage2 timing, not the frozen module98
A/B, and not a fixed-point or five-GC-matrix claim. The 1.3452x is this
workload's number; the barrier's share of a stage2 emit worker has not been
measured. Nothing here says the stage2/stage1 ratio moved.
