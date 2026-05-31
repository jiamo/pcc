# Investigation: current bootstrap five-GC audit

## Status
resolved

## Problem Description
User request: audit the bootstrap to confirm all five GC backends are
exposed and that pcc0/pcc1/pcc2 each work correctly under all five.
Report each backend's efficiency, current state, and performance,
recommend which one should be the default, and list each backend's
pros and cons.

This is a follow-up to
`docs/investigations/bootstrap-five-gc-matrix-after-backend1-fix.md`. That
document is resolved, so this audit records the current tree after the later
backend 1 pause telemetry and backend 2 worker lifecycle fixes.

## Repro
Build the self-host chain through stage2:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-current-1778163708 \
  --backend self --stage 2
```

Then compile and run a pcc-Python runtime probe under:

- compiler stages: `pcc0`, `pcc1`, `pcc2`
- backend env values: `PCC_GC_BACKEND=0..4`

Each compile and run used:

```bash
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_GC_BACKEND=<0..4> \
  <pcc0|pcc1|pcc2> --backend self --python-libpython off gc_probe.py -o out

PCC_GC_BACKEND=<0..4> /opt/homebrew/bin/timeout 30s ./out
```

The runtime probe performs list/dict allocation churn, explicit `gc.collect()`,
and telemetry checks.

## Test [CONFIRMED]
Self-bootstrap:

- stage1 produced `pcc1` in `9.526s` measured by the script marker
- stage2 produced `pcc2` in `11.037s` measured by the script marker

Dynamic-link check:

```bash
otool -L build/bootstrap-self-gc-current-1778163708/pcc1
otool -L build/bootstrap-self-gc-current-1778163708/pcc2
```

Observed result: both binaries list only `/usr/lib/libSystem.B.dylib`; neither
links libpython.

Compiler startup under all five backend IDs:

```bash
PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-current-1778163708/pcc1 --help
PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-current-1778163708/pcc2 --help
```

Observed result: all ten invocations returned `0`.

Compile/runtime matrix result:

| stage | backend 0 | backend 1 | backend 2 | backend 3 | backend 4 |
| --- | --- | --- | --- | --- | --- |
| `pcc0` | pass | pass | pass | pass | pass |
| `pcc1` | pass | pass | pass | pass | pass |
| `pcc2` | pass | pass | pass | pass | pass |

All 15 cells had `compile_rc=0`, `run_rc=0`, `collect True`, and
`allocs True`.

Representative 20k matrix telemetry:

| backend | steps | debt | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0` | `0` | no tracing work |
| 1 incremental-tricolor | `7` | `120` | bounded incremental work |
| 2 concurrent-mark-sweep | `81` | `120` | CMS assist work visible |
| 3 generational-minor-major | `20` | `0` | minor/major step work visible |
| 4 colored-relocating | `1` | `0` | no relocation in this probe |

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `174 passed, 14 xfailed in 157.24s`.

Performance benchmark: pcc2-compiled 200k allocation churn with explicit
`gc.collect()`, three runs per backend, median wall time:

| backend | median | ratio vs backend 0 | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0.145666s` | `1.00x` | `allocs=400004`, no tracing steps |
| 1 incremental-tricolor | `0.162042s` | `1.11x` | `steps=12`, `pause_us=6479` |
| 2 concurrent-mark-sweep | `0.260878s` | `1.79x` | `steps=795`, `cms_queue=400011`, `cms_assists=684` |
| 3 generational-minor-major | `0.232420s` | `1.60x` | `minor_allocs=400009`, `minor_collections=18` |
| 4 colored-relocating | `0.157855s` | `1.08x` | `steps=1`, no relocations in this probe |

Backend 0 had one cold/noisy outlier at `0.419061s`, but its best and median
times still remain the strongest production-default result.

## Update 2026-05-14: pcc1/pcc2 GC liveness gate

The pcc1 GC backend matrix now includes a lightweight compiler-process
liveness gate for both bootstrapped compiler stages:

```text
PCC_GC_BACKEND=0..4 pcc1 --help
PCC_GC_BACKEND=0..4 pcc2 --help
```

This is deliberately separate from the heavier pcc1 compile/run churn tests.
It proves each bootstrapped compiler process can initialize the selected GC
backend, parse CLI options, print help, and shut down without root/finalizer
failures.

Focused validation:

```text
tests/python/test_pcc1_gc_backend_matrix.py::test_bootstrap_stage_cli_starts_under_gc_backend
  10 passed in 0.24s

tests/python/test_pcc1_gc_backend_matrix.py
  20 passed in 5.18s
```

## Proposals
- No.1 Keep backend 0 as the default     [CONFIRMED]

## No.1 Keep backend 0 as the default
### Code Change
No production code change. The current default remains backend 0:

- `pcc/py_runtime/src/py_gc_backend.c` initializes
  `pcc_gc_selected_backend = PCC_GC_KIND_REFCOUNT_CYCLE`
- `pcc/py_runtime/py/py_substrate.py` defines
  `pcc_gc_backend_selected = 0`

### CONFIRMED
Backend 0 is still the best default for the current tree. It is the only
backend marked production in `tasksV2.md`, passes the current bootstrap matrix,
and has the lowest measured median runtime in the 200k allocation churn
benchmark.

Backends 1-4 should remain opt-in. They all execute through pcc0/pcc1/pcc2
now, but each still has missing production work tracked in `tasksV2.md`:
backend 1 needs sweep/finalizer audit, backend 2 needs mark-termination and
concurrent safety proof, backend 3 lacks full copying oldification and pointer
rewriting, and backend 4 lacks general relocation/reference updating.

## Report (only when the investigation is closing)
The current self-bootstrap path includes all five GC backends, and pcc0, pcc1,
and pcc2 all compile and run the same pcc-Python runtime probe successfully
under `PCC_GC_BACKEND=0..4`.

Recommended default: keep backend 0 (`refcount-cycle`).

Backend tradeoffs:

- 0 refcount-cycle: production/default, fastest measured median, simplest and
  most covered; still inherits refcount overhead and stop-the-world cycle
  collection.
- 1 incremental-tricolor: now works in the bootstrap matrix and has bounded
  work/pause telemetry; still not production until sweep/finalizer behavior is
  audited broadly.
- 2 concurrent-mark-sweep: actual queue/assist telemetry is visible and worker
  lifecycle is fixed; current single-threaded benchmark is slower and it still
  needs TSan/concurrent sweep proof.
- 3 generational-minor-major: exercises minor allocation and collection
  telemetry, making it a useful allocation-heavy research backend; not a full
  OCaml-style copying generational collector yet.
- 4 colored-relocating: low overhead in this probe, but relocation was not
  exercised, so the timing is not evidence that moving-GC semantics are ready.
