# Investigation: Backend 1 pcc-Python runtime should report pause telemetry

## Status
resolved

## Problem Description
Continue the Backend #1 Tier 5 productionization track from `goal.md`.

Backend #1 has C-runtime max-pause telemetry, and the current bootstrap matrix
confirms the pcc-Python runtime can run backend #1 allocation churn. The
remaining audit gap is that the pcc-Python mirror of `pcc_gc_step()` must expose
the same `PCC_GC_COUNTER_MAX_PAUSE_US` signal, so the goal's pause-budget stress
test is meaningful for bootstrap/runtime-high builds too.

## Repro
Run the focused pcc-Python backend #1 stress gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_incremental.py::test_incremental_backend_pcc_python_reports_pause_budget_under_churn' \
  -q -n0
```

Expected pre-fix failure: generated program reports backend #1 work steps but
`pcc_gc_telemetry(7)` remains `0`, because the pcc-Python `pcc_gc_step()` mirror
does not record pause timing.

## Test [CONFIRMED]
The focused pcc-Python pause-budget gate fails before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_incremental.py::test_incremental_backend_pcc_python_reports_pause_budget_under_churn' \
  -q -n0
```

Observed result: `1 failed in 1.30s`.

Failure:

```text
assert 0 < 0
```

The generated program has backend #1 work steps, but `pause_us` remains zero.

## Proposals
- No.1 Add shared runtime microsecond clock and mirror pause recording     [CONFIRMED]

## No.1 Add shared runtime microsecond clock and mirror pause recording
### Code Change
The landed fix:

- adds `pcc_runtime_now_us()` to the always-linked C runtime helper layer;
- declares it in `py_runtime.h`;
- imports it from `pcc/py_runtime/py/py_gc_backend.py`;
- wraps the pcc-Python `pcc_gc_step()` mirror with start/end timestamps;
- updates `pcc_gc_metric_max_pause_us` from the pcc-Python mirror just like the
  C runtime backend does.

### CONFIRMED
The focused failing gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_incremental.py::test_incremental_backend_pcc_python_reports_pause_budget_under_churn' \
  -q -n0
```

Observed result: `1 passed in 25.85s`.

The full backend #1 incremental gate passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_backend_incremental.py -q -n0
```

Observed result: `4 passed in 2.90s`.

The backend #1 verdict gate remains green:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_gc_backend_incremental.py \
    tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py \
    -q -n0 -rxX
```

Observed result: `19 passed in 12.04s`.

Both runtime archives rebuild successfully:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime libpy_runtime.a

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
    PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a
```

The current GC suite remains green:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `173 passed, 14 xfailed in 153.80s`.

The default self-bootstrap stage2 path still passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s \
  bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-pause-1778162823 \
  --backend self --stage 2
```

Observed results: stage1 `9.603s`, stage2 `11.098s`.

## Report (only when the investigation is closing)
Proposal No.1 landed. Backend #1's pcc-Python runtime mirror now exposes a real
bounded-pause telemetry signal, so the Tier 5 pause-budget stress gate covers
runtime-high/bootstrap builds instead of only the C runtime backend.
