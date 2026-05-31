# Investigation: bootstrap chain should expose and run all five GC backends

## Status
resolved

## Problem Description
User request: audit the bootstrap to confirm all five GC backends are
exposed and that pcc0/pcc1/pcc2 each work correctly under all five.
Report each backend's efficiency, current state, and performance,
recommend which one should be the default, and list each backend's
pros and cons.

We need confirm the current tree's bootstrap/runtime state across all five GC
backends:

- backend 0: refcount-cycle
- backend 1: incremental-tricolor
- backend 2: concurrent-mark-sweep
- backend 3: generational-minor-major
- backend 4: colored-relocating

The check must distinguish stage/compiler support (`pcc0`, `pcc1`, `pcc2`)
from generated-program runtime behavior, and must report measured performance
rather than relying only on capability tables.

## Repro
Run the bootstrap/build gate, then run a GC matrix probe for:

- compiler stages: `pcc0`, `pcc1`, `pcc2`
- GC backend env values: `PCC_GC_BACKEND=0..4`

Expected current result before this audit was unknown.

## Test [CONFIRMED]
The current tree was bootstrapped through stage2 with the self backend:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-audit --backend self --stage 2
```

Observed result:

- stage1 produced `pcc1` in `30.374s`
- stage2 produced `pcc2` in `10.410s`

Both stage binaries are no-libpython at the dynamic-link level:

```bash
otool -L build/bootstrap-self-gc-audit/pcc1
otool -L build/bootstrap-self-gc-audit/pcc2
```

Observed result: both list only `/usr/lib/libSystem.B.dylib`.

The compiler startup gate passed for `pcc1` and `pcc2` under all five backend
env values:

```bash
PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-audit/pcc1 --help
PCC_GC_BACKEND=0..4 build/bootstrap-self-gc-audit/pcc2 --help
```

Observed result: all ten invocations returned `0`.

The compile/runtime matrix used a small pcc-Python runtime probe with
`PCC_RUNTIME_HIGH=py` and `--backend self --python-libpython off`.

Observed compile result: all 15 cells compiled successfully:

- compiler stages: `pcc0`, `pcc1`, `pcc2`
- GC backend env values: `0`, `1`, `2`, `3`, `4`

Observed runtime result:

| stage | backend 0 | backend 1 | backend 2 | backend 3 | backend 4 |
| --- | --- | --- | --- | --- | --- |
| `pcc0` | pass | abort `-6` | pass | pass | pass |
| `pcc1` | pass | abort `-6` | pass | pass | pass |
| `pcc2` | pass | abort `-6` | pass | pass | pass |

Control checks for backend #1's C-runtime-oriented gates still passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_backend_incremental.py -q -n0 -rxX

env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
```

Observed results: `2 passed` and `15 passed`.

Default-threshold alloc-heavy benchmark, using a `pcc2`-compiled program and
`PCC_RUNTIME_HIGH=py`, produced:

| backend | median runtime | ratio vs backend 0 | notable telemetry |
| --- | ---: | ---: | --- |
| 0 refcount-cycle | `0.073951s` | `1.00x` | `allocs=240004`, no tracing steps |
| 1 incremental-tricolor | `0.085262s` | `1.15x` | no tracing steps at default threshold |
| 2 concurrent-mark-sweep | `0.088032s` | `1.19x` | no tracing steps at default threshold |
| 3 generational-minor-major | `0.082150s` | `1.11x` | `minor_allocs=240006`, `minor_collections=10` |
| 4 colored-relocating | `0.087467s` | `1.18x` | no relocation forwards at default threshold |

A low-threshold forced-work experiment (`PCC_GC_DEBT_THRESHOLD=1024`) produced
a CPU-bound hang and was killed manually. That is a risk signal, not a complete
performance datapoint.

## Proposals
- No.1 Run a focused bootstrap GC matrix and benchmark audit     [CONFIRMED]

## No.1 Run a focused bootstrap GC matrix and benchmark audit
### Code Change
No production source change planned. A temporary or checked-in audit script may
be used only if existing gates do not expose the needed matrix clearly.
### CONFIRMED
No production source change landed. Temporary probe scripts under
`build/bootstrap-self-gc-audit/matrix/` generated the matrix and benchmark
numbers recorded above.

The audit confirms:

- The current self-bootstrap stage1/stage2 path builds no-libpython `pcc1` and
  `pcc2`.
- `pcc1` and `pcc2` start successfully with all five `PCC_GC_BACKEND` values.
- All three compiler stages can compile programs targeting all five backends.
- Generated programs run successfully for backends `0`, `2`, `3`, and `4`.
- Generated programs abort for backend `1` after allocation churn plus
  `gc.collect()` in the pcc-Python runtime path.

## Report (only when the investigation is closing)
The bootstrap chain contains and accepts all five backend IDs, but the current
production-safe default should remain backend `0` (`refcount-cycle`).

Backend `3` is the best near-term candidate after backend `0` for allocation
heavy workloads because it was the fastest non-default in the default-threshold
benchmark and actually exercised minor-heap telemetry. It is still not a
default candidate until broader bootstrap/runtime gates cover it.

Backends `1`, `2`, and `4` remain experimental in this audit. Backend `1` has
a concrete pcc-Python runtime failure tracked separately in
`docs/investigations/gc-backend1-pcc-py-runtime-collect-abort.md`; backend `2`
and backend `4` start and run this probe but did not show meaningful tracing or
relocation work under default thresholds.

## Update 2026-05-14: pcc1 GC matrix after layer1 split work

After the `layer1.py` helper extraction and cleanup work, the focused pcc1 GC
matrix was re-run against the bootstrapped `build/bootstrap-pytest-self/pcc1`
and `pcc2` binaries.

```text
tests/python/test_pcc1_gc_backend_matrix.py::test_bootstrap_stage_cli_starts_under_gc_backend
  10 passed in 0.17s

tests/python/test_pcc1_gc_backend_matrix.py::test_pcc1_self_backend_compile_smoke_under_gc_backend
  5 passed in 2.54s

tests/python/test_pcc1_gc_backend_matrix.py::test_pcc1_threading_objects_survive_gc_backend_churn
  5 passed in 2.54s
```

This covers:

- `pcc1` and `pcc2` startup under `PCC_GC_BACKEND=0..4`;
- `pcc1 --backend self --python-libpython=off --ir-scaffold=on` compiling and
  running an allocation/churn/explicit-`gc.collect()` probe under 0..4;
- `pcc1` compiling and running a `threading.Thread` / `Lock` object-lifetime
  churn probe under 0..4.
