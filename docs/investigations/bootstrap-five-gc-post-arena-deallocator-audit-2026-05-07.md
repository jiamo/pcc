# Investigation: bootstrap five-GC audit after arena deallocator fix

## Status
resolved

This supersedes
`docs/investigations/bootstrap-five-gc-current-audit-2026-05-07.md` for the
current tree after `gc-backend3-pcc-py-arena-deallocator.md`.

## Problem Description
User request: audit the bootstrap to confirm all five GC backends are
exposed and that pcc0/pcc1/pcc2 each work correctly under all five.
Report each backend's efficiency, current state, and performance,
recommend which one should be the default, and list each backend's
pros and cons.

## Repro
Build a fresh stage2 self-bootstrap:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-arena-dealloc-1778166124 \
  --backend self --stage 2
```

Then verify:

```bash
otool -L build/bootstrap-self-gc-arena-dealloc-1778166124/pcc1
otool -L build/bootstrap-self-gc-arena-dealloc-1778166124/pcc2

PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-arena-dealloc-1778166124/pcc1 --help
PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-arena-dealloc-1778166124/pcc2 --help
```

Compile/run matrix:

```bash
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_GC_BACKEND=<0..4> \
  <pcc0|pcc1|pcc2> --backend self --python-libpython off gc_probe.py -o out
PCC_GC_BACKEND=<0..4> /opt/homebrew/bin/timeout 30s ./out
```

The probe allocates list/dict churn, calls `gc.collect()`, and checks backend,
value, collection, and allocation telemetry.

Performance probe:

```bash
build/bootstrap-self-gc-arena-dealloc-1778166124/pcc2 \
  --backend self --python-libpython off gc_perf.py -o gc_perf.out

# one warmup, then five measured runs per backend
PCC_GC_BACKEND=<0..4> /opt/homebrew/bin/timeout 30s ./gc_perf.out
```

## Test [CONFIRMED]
Self-bootstrap result:

- stage1 elapsed marker: `9420ms`
- stage2 elapsed marker: `11420ms`
- `pcc1` and `pcc2` link only `/usr/lib/libSystem.B.dylib`
- `pcc1` and `pcc2` `--help` return `0` under `PCC_GC_BACKEND=0..4`

Compile/runtime matrix result:

| stage | backend 0 | backend 1 | backend 2 | backend 3 | backend 4 |
| --- | --- | --- | --- | --- | --- |
| `pcc0` | pass | pass | pass | pass | pass |
| `pcc1` | pass | pass | pass | pass | pass |
| `pcc2` | pass | pass | pass | pass | pass |

Representative 20k matrix telemetry:

| backend | work steps | debt | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0` | `0` | no tracing work |
| 1 incremental-tricolor | `30` | `240` | bounded incremental work |
| 2 concurrent-mark-sweep | `14490` | `240` | CMS queue `40008` |
| 3 generational-minor-major | `1` | `0` | minor allocs `40007` |
| 4 colored-relocating | `1` | `0` | no relocation in this probe |

Warmed pcc2-compiled 200k allocation churn, five measured runs per backend,
median wall time:

| backend | median | ratio vs backend 0 | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0.161743s` | `1.00x` | `allocs=400004`, no tracing steps |
| 1 incremental-tricolor | `0.168007s` | `1.04x` | `steps=293`, `pause_us=2` |
| 2 concurrent-mark-sweep | `0.187113s` | `1.16x` | `steps=149490`, `cms_queue=400011`, `cms_assists=149489` |
| 3 generational-minor-major | `0.182317s` | `1.13x` | `minor_allocs=400008`, `minor_collections=21` |
| 4 colored-relocating | `0.182133s` | `1.13x` | `steps=1`, no relocations in this probe |

Runtime oracle subset:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  'tests/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence' \
  -q -n0
```

Observed result: `7 passed, 6 skipped in 9.55s`.

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `176 passed, 14 xfailed in 154.51s`.

## Proposals
- No.1 Keep backend 0 as default     [CONFIRMED]

## No.1 Keep backend 0 as default
### Code Change
No default-selection code change.

The current default remains backend 0:

- `pcc/py_runtime/src/py_gc_backend.c` initializes
  `pcc_gc_selected_backend = PCC_GC_KIND_REFCOUNT_CYCLE`
- `pcc/py_runtime/py/py_substrate.py` defines
  `pcc_gc_backend_selected = 0`

### CONFIRMED
Backend 0 remains the best default for the current tree. It is still the only
production-grade backend in `tasksV2.md`, it passed the current pcc0/pcc1/pcc2
matrix, and it had the best warmed median in the pcc2-compiled allocation
churn benchmark.

Backends 1-4 now all execute in the current bootstrap matrix, but they remain
opt-in because their production semantics are still incomplete.

## Report (only when the investigation is closing)
The current self-bootstrap includes all five GC backend implementations in the
runtime surface, and fresh `pcc1`/`pcc2` binaries can start, compile, and run
the probe under `PCC_GC_BACKEND=0..4`.

Recommended default: keep backend 0 (`refcount-cycle`).

Backend tradeoffs:

- 0 refcount-cycle: production/default, fastest warmed median, simplest
  behavior, best covered; still pays refcount overhead and uses stop-the-world
  cycle collection.
- 1 incremental-tricolor: working opt-in backend with bounded incremental work
  telemetry; still needs broader sweep/finalizer production audit.
- 2 concurrent-mark-sweep: real queue/assist telemetry is visible; slower on
  this single-threaded churn and still needs stronger concurrent safety proof.
- 3 generational-minor-major: real minor arena allocation/collection is now
  visible in both C and pcc-Python runtime-high paths; still lacks full
  copying oldification, pointer rewriting, and domain-local threaded heap
  ownership.
- 4 colored-relocating: runtime path is selectable and low-overhead in this
  non-moving probe, but this benchmark does not exercise relocation; general
  reference updating remains incomplete.
