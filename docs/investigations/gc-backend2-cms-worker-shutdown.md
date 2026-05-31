# Investigation: Backend 2 CMS worker should stop and restart cleanly

## Status
resolved

## Problem Description
Continue Backend #2 productionization from `goal.md` and `tasksV2.md`.

Backend #2 currently starts a detached pthread CMS worker. Prior slices made
the worker perform bounded trace work and protected graph access with a lock,
but `tasksV2.md` still lists worker lifecycle shutdown/join semantics as an
open production gap. The concrete requirement for this slice is that switching
away from backend #2 should request worker shutdown, join the worker, report
that shutdown via telemetry, and allow a later switch back to backend #2 to
start a fresh worker.

## Repro
Run the focused threaded backend #2 lifecycle gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_stops_and_restarts_on_backend_switch' \
  -q -n0
```

Expected pre-fix failure: the new shutdown telemetry is unavailable and the
detached worker does not go through a joinable stop/restart lifecycle.

## Test [CONFIRMED]
The focused lifecycle gate fails before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_stops_and_restarts_on_backend_switch' \
  -q -n0
```

Observed result: `1 failed in 3.28s`.

Failure:

```text
assert -1 >= 1
```

`pcc_gc_telemetry(22)` is currently not a known metric, which matches the
absence of a joinable worker stop lifecycle.

## Proposals
- No.1 Replace detached CMS worker lifetime with stop/join/restart     [CONFIRMED]

## No.1 Replace detached CMS worker lifetime with stop/join/restart
### Code Change
The landed fix:

- keeps the backend #2 worker handle instead of detaching it immediately;
- adds a `pcc_gc_cms_worker_stop_requested` flag checked by the worker loop;
- joins the worker when `pcc_gc_set_backend()` switches away from backend #2;
- clears stale CMS queue tickets after the worker is joined;
- allows a later switch back to backend #2 to start a fresh worker;
- adds `PCC_GC_COUNTER_CMS_WORKER_STOPS` telemetry and pcc-Python mirror state.

### CONFIRMED
The focused lifecycle gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_stops_and_restarts_on_backend_switch' \
  -q -n0
```

Observed result: `1 passed in 3.06s`.

The focused backend2/surface/threading gate passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_backend_concurrent.py \
    tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py \
    -q -n0 -rxX
```

Observed result: `30 passed in 41.69s`.

Runtime archives rebuild successfully:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime libpy_runtime.a

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime PCC_WITH_THREADS=1 libpy_runtime.a

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
    PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a
```

The current GC suite remains green:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `174 passed, 14 xfailed in 153.67s`.

The default self-bootstrap stage2 path still passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s \
  bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-cms-stop-1778163398 \
  --backend self --stage 2
```

Observed results: stage1 `9.587s`, stage2 `11.171s`.

## Report (only when the investigation is closing)
Proposal No.1 landed. Backend #2 no longer relies on a permanently detached CMS
worker for the focused threaded runtime path: switching away from backend #2
now requests shutdown, joins the worker, clears stale queue state, and allows a
later backend #2 selection to start a new worker.

This is still not production Go-style CMS. Remaining backend #2 work includes
robust mark termination, concurrent sweep safety proof, and race-sanitized
queue/marker validation.
