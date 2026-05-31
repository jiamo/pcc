# Investigation: bootstrap five-GC matrix after backend 1 fix

## Status
resolved

## Problem Description
Follow-up to `docs/investigations/bootstrap-five-gc-matrix.md` after fixing the
backend 1 pcc-Python runtime abort in
`docs/investigations/gc-backend1-pcc-py-runtime-collect-abort.md`.

Confirm that the self-bootstrap chain contains all five GC backends and that
`pcc0`, `pcc1`, and `pcc2` can compile and run generated programs under
`PCC_GC_BACKEND=0..4`. Also report current performance and the recommended
default backend.

## Repro
Build the self-host chain:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-final-1778159837 \
  --backend self --stage 2
```

Then compile and run a small pcc-Python runtime probe under every compiler
stage and backend:

```bash
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  <pcc0|pcc1|pcc2> --backend self --python-libpython off gc_probe.py -o out

PCC_GC_BACKEND=<0..4> /opt/homebrew/bin/timeout 30s ./out
```

The probe imports `gc`, allocates list/dict churn, calls `gc.collect()`, and
prints backend, sum, collect result, allocation telemetry, and work steps.

## Test [CONFIRMED]
Self-bootstrap:

- stage1 produced `pcc1` in `9.484s` real time
- stage2 produced `pcc2` in `11.409s` real time

Dynamic-link check:

```bash
otool -L build/bootstrap-self-gc-final-1778159837/pcc1
otool -L build/bootstrap-self-gc-final-1778159837/pcc2
```

Observed result: both binaries list only `/usr/lib/libSystem.B.dylib`; neither
links libpython.

Compile/runtime matrix result:

| stage | backend 0 | backend 1 | backend 2 | backend 3 | backend 4 |
| --- | --- | --- | --- | --- | --- |
| `pcc0` | pass | pass | pass | pass | pass |
| `pcc1` | pass | pass | pass | pass | pass |
| `pcc2` | pass | pass | pass | pass | pass |

All 15 cells had `compile_rc=0` and `run_rc=0`. Runtime output for every cell
included `collect True` and `allocs True`.

Representative work-step telemetry from the matrix:

| backend | steps |
| --- | ---: |
| 0 refcount-cycle | `0` |
| 1 incremental-tricolor | `38641` |
| 2 concurrent-mark-sweep | `81` |
| 3 generational-minor-major | `20` |
| 4 colored-relocating | `1` |

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `172 passed, 14 xfailed in 165.31s`.

Performance benchmark: pcc2-compiled allocation churn with 20k iterations,
explicit `gc.collect()`, five runs per backend, median wall time:

| backend | median | ratio vs 0 | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0.014157s` | `1.00x` | `allocs=40004`, no work steps |
| 1 incremental-tricolor | `6.063663s` | `428.30x` | `steps=38643`, `debt=1920320` |
| 2 concurrent-mark-sweep | `0.016619s` | `1.17x` | `steps=81`, `cms_queue=40014`, `cms_assists=69` |
| 3 generational-minor-major | `0.016676s` | `1.18x` | `minor_allocs=40010`, `minor_collections=1` |
| 4 colored-relocating | `0.015773s` | `1.11x` | `steps=1`, no relocations in this probe |

A larger 200k-iteration probe timed out for backend 1 at 90s during default
explicit collection, so backend 1 still has a serious forced-sweep performance
risk even though correctness is fixed.

## Proposals
- No.1 Keep backend 0 as the default     [CONFIRMED]

## No.1 Keep backend 0 as the default
### Code Change
No default change. The current default remains backend 0:

- `pcc/py_runtime/py/py_substrate.py` defines
  `pcc_gc_backend_selected = 0`
- `pcc/py_runtime/src/py_gc_backend.c` initializes
  `pcc_gc_selected_backend = PCC_GC_KIND_REFCOUNT_CYCLE`

### CONFIRMED
Backend 0 is still the best default for the current tree:

- It is the fastest and most stable measured backend.
- It has the smallest behavioral surface: immediate refcounting plus the
  existing cycle collector.
- It is the only backend whose forced `gc.collect()` path does not add
  pluggable-tracing/relocation complexity.

Backends 2, 3, and 4 are now good opt-in experimental choices for continued
work. Backend 1 is correct after the duplicate-registration fix but is not a
default candidate because explicit collection is much slower on the benchmark.

## Report (only when the investigation is closing)
The after-fix matrix confirms that the bootstrap chain contains all five GC
backends and that `pcc0`, `pcc1`, and `pcc2` all compile and run the same
pcc-Python runtime probe successfully under `PCC_GC_BACKEND=0..4`.

Recommended default: keep backend 0 (`refcount-cycle`).

Backend tradeoffs:

- 0 refcount-cycle: fastest, most mature, least risky; weaker long-term story
  for reducing refcount overhead.
- 1 incremental-tricolor: correctness now fixed, but forced collect is very
  slow; useful for algorithm experiments, not default.
- 2 concurrent-mark-sweep: low overhead in this single-threaded probe and
  telemetry shows queue/assist activity; needs broader concurrent/runtime
  validation before default.
- 3 generational-minor-major: realistic candidate for allocation-heavy future
  work; minor collection telemetry is active; still needs broader bootstrap and
  leak/regression validation.
- 4 colored-relocating: low overhead here, but this probe does not exercise
  actual relocation, so current performance is not proof of moving-GC readiness.
