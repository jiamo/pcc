# Investigation: threading.Lock loses mutual exclusion under pcc-compiled threads

## Status
resolved

## Problem Description
User-reported lost-update bug:

```text
C pcc_mutex_lock direct call: 4000/4000
C py_threading_lock_acquire PyObject API: 4000/4000
C mimicking Python codegen whole RMW: 4000/4000
pcc-compiled lock.acquire(); counts[0]+=1; lock.release(), 4 threads:
1303-2026 / 4000
same lock, each thread writes its own slot: per-thread slots all 1000,
but shared counts[0] still loses updates
```

The suspected high-level failure is that `lock.acquire()` on the pcc-compiled
Python path sometimes does not acquire the real mutex, and the caller then
ignores the `False` return and enters the critical section anyway. This is a
GC goal blocker because backend #2 and scheduler-root tracing both require
real mutex semantics under `PCC_WITH_THREADS=1`.

## Repro
Small pcc-compiled Python reproducer:

```bash
TMP=$(mktemp -d /tmp/pcc-lock-lost-update.XXXXXX)
SRC="$TMP/incr_test.py"
cat > "$SRC" <<'PY'
from threading import Lock, Thread

counts = [0]
lock = Lock()

def worker() -> None:
    i = 0
    while i < 1000:
        lock.acquire()
        counts[0] = counts[0] + 1
        lock.release()
        i += 1

def main() -> None:
    t0 = Thread(target=worker)
    t1 = Thread(target=worker)
    t2 = Thread(target=worker)
    t3 = Thread(target=worker)
    t0.start()
    t1.start()
    t2.start()
    t3.start()
    t0.join()
    t1.join()
    t2.join()
    t3.join()
    print(counts[0])

if __name__ == "__main__":
    main()
PY
env -u LC_ALL PCC_RUNTIME_CC=cc PCC_RUNTIME_HIGH=c PCC_WITH_THREADS=1 \
  /opt/homebrew/bin/timeout 180s \
  uv run pcc --python-libpython=off --ir-scaffold=on "$SRC" -o "$TMP/incr.out"
env -u LC_ALL /opt/homebrew/bin/timeout 30s "$TMP/incr.out"
```

Expected pre-fix failure: stdout is less than `4000`.

The literal list-of-threads form from the initial report currently exposes a
second bug first: `threads[i].start()` raises `RuntimeError:
native Thread.start failed`. The local-variable form above isolates the
Lock lost-update bug and should remain this investigation's gate.

## Test [CONFIRMED]
Regression test added:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_threading_module_native.py::test_pthread_lock_serializes_shared_list_updates \
  -q -n0 -rxX
```

Observed pre-fix result: `1 xfailed in 3.65s`.

Manual reproduction with the local-variable thread form produced:

```text
run1 rc=0 out=2158
run2 rc=0 out=2427
run3 rc=0 out=2053
run4 rc=0 out=1612
run5 rc=0 out=1574
```

Expected fixed behavior: every run prints `4000`.

## Proposals
- No.1 Confirm the pcc-compiled Python lost-update path     [CONFIRMED]
- No.2 Abort or raise on failed Lock/RLock/Condition/Semaphore acquire     [CONFIRMED]
- No.3 Audit `py_instance_get_field(self, 0)` and direct native-threading method dispatch     [CONFIRMED]

## No.1 Confirm the pcc-compiled Python lost-update path
### Code Change
No source fix. Added a strict xfail regression test that builds a
`PCC_WITH_THREADS=1` runtime, compiles a no-libpython Python program with four
threads, and asserts a locked shared-list increment reaches `4000`.
### CONFIRMED
The test xfails in the current tree and the compiled binary repeatedly prints
values below `4000`. This confirms the C mutex substrate is not the whole
story; pcc-compiled Python execution reaches the critical section without
effective mutual exclusion.

## No.2 Abort or raise on failed Lock/RLock/Condition/Semaphore acquire
### Code Change
Layer1 native-threading method dispatch now checks nonzero return codes from
threading runtime helpers and raises `RuntimeError` instead of silently
continuing. This covers direct `Lock`, `RLock`, `Condition`, `Semaphore`, and
`Thread` method calls emitted through `_maybe_emit_threading_instance_method`.
### CONFIRMED
The regression no longer relies on ignoring the `False` result. If a native
threading helper fails on the direct-dispatch path, generated code branches to
the function error exit after `py_raise(RuntimeError(...))`.

## No.3 Audit `py_instance_get_field(self, 0)` and direct native-threading method dispatch
### Code Change
The failing program showed a codegen mismatch:

- module-level `lock = Lock()` was lowered to the raw runtime object returned
  by `py_threading_lock_new()`;
- function body `lock.acquire()` did not use direct native-threading method
  dispatch because `_threading_env_flags` was only populated during assignment
  emission, after user functions had already been emitted;
- the fallback call invoked `user_threading_Lock_acquire(self)` with the raw
  lock object as `self`;
- that wrapper read `_ptr` with `py_instance_get_field(self, 0)`, got `NULL`,
  called `py_threading_lock_acquire(NULL)`, returned `False`, and the caller
  ignored the boolean.

The fix records threading constructor flags during the module-global declare
pass as well as assignment emission, and allows `_maybe_emit_threading_instance_method`
to dispatch globals, not only names in the local `env`.
### CONFIRMED
Generated IR for the reproducer now calls:

```llvm
call i64 @py_threading_lock_acquire(ptr %lock...)
call i64 @py_threading_lock_release(ptr %lock...)
```

inside `worker()` instead of calling `user_threading_Lock_acquire` /
`user_threading_Lock_release`.

The regression now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_threading_module_native.py::test_pthread_lock_serializes_shared_list_updates \
  -q -n0 -rxX
```

Observed result: `1 passed in 4.18s`.

Broader gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_threading_module_native.py \
  tests/test_threading_compat_matrix.py tests/test_threading_local.py \
  -q -n0 -rxX

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_threading_substrate.py \
  tests/test_gc_backend_concurrent.py -q -n0 -rxX

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a

env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_boc_threading_proof.py -q -n0 -rxX
```

Observed results: `9 passed in 9.09s`, `16 passed in 15.24s`, and the
pcc-Python runtime-high archive rebuilt successfully. After
`bench/boc_bank_demo_serial.py` was present, the BOC parallel proof also passed:
`1 passed in 8.10s`.

Bootstrap and five-backend sanity after the fix:

- self bootstrap stage1: `9.739s` real, `PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=9834`;
- self bootstrap stage2: `11.871s` real, `PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=11992`;
- `pcc1` and `pcc2` depend only on `/usr/lib/libSystem.B.dylib`;
- `pcc1 --help` and `pcc2 --help` pass under `PCC_GC_BACKEND=0..4`;
- `pcc0`, `pcc1`, and `pcc2` compile and run the nested-list probe under
  backend `0..4`, and every binary prints `42`.

Additional non-threading regression gates after the layer1 change:

- fallback baselines: `11 passed in 50.80s`;
- multi-file/bootstrap shim: `70 passed in 141.79s`;
- focused GC root/barrier/incremental gate: `10 passed in 5.20s`.
- full GC suite: `181 passed, 14 xfailed in 154.32s`.

## Report
No.3 was the actual root cause, with No.2 landed as a necessary safety guard.
The bug was not in `pcc_mutex_lock`, not in the C `py_threading_lock_acquire`
API, and not an ARM64 weak-memory field-load issue. It was a generated-code
dispatch mismatch for module-level `from threading import Lock` bindings:
the binding held a raw native lock object, while the fallback wrapper expected
a Python `Lock` instance with a `_ptr` field.

The lock lost-update gate is now a normal passing test. The list-indexed
`threads[i].start()` failure is separate and remains tracked in
`docs/investigations/threading-list-index-start-failure.md`.
