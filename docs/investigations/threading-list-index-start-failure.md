# Investigation: list-indexed Thread.start fails under pthread runtime

## Status
active

## Problem Description
While reducing `threading.Lock` lost updates, the literal list-of-threads
shape from the original report exposed a separate failure before the lock
critical section runs. A `Thread` object stored in a list and invoked as
`threads[i].start()` raises `RuntimeError: native Thread.start failed` under
`PCC_WITH_THREADS=1`, while a local variable `t.start()` succeeds.

This appears distinct from the lock mutual-exclusion failure and should not be
used as the lock regression gate.

## Repro
Small pcc-compiled Python reproducer:

```bash
TMP=$(mktemp -d /tmp/pcc-thread-list-start.XXXXXX)
SRC="$TMP/thread_list.py"
cat > "$SRC" <<'PY'
from threading import Thread

def work() -> None:
    print("worker")

def main() -> None:
    threads = [Thread(target=work)]
    print("start")
    threads[0].start()
    threads[0].join()
    print("done")

if __name__ == "__main__":
    main()
PY
env -u LC_ALL PCC_RUNTIME_CC=cc PCC_RUNTIME_HIGH=c PCC_WITH_THREADS=1 \
  /opt/homebrew/bin/timeout 180s \
  uv run pcc --python-libpython=off --ir-scaffold=on "$SRC" -o "$TMP/thread_list.out"
env -u LC_ALL /opt/homebrew/bin/timeout 30s "$TMP/thread_list.out"
```

Expected pre-fix failure:

```text
RuntimeError: native Thread.start failed
start
```

## Test [CONFIRMED]
Manual reduction observed the failure for `n=1`, `n=2`, and `n=4` list sizes.
No committed regression test yet; keep this investigation active until a
focused list-indexed method-call test exists.

## Proposals
- No.1 Add a focused list-indexed Thread.start regression test     [pending]
- No.2 Audit method dispatch when receiver is loaded from list indexing     [pending]

## No.1 Add a focused list-indexed Thread.start regression test
### Code Change
Pending.
### pending
Awaiting prioritization after the Lock lost-update fix.

## No.2 Audit method dispatch when receiver is loaded from list indexing
### Code Change
Pending. Compare `t.start()` and `threads[0].start()` lowering for receiver
ownership, type narrowing, and `_native` field reads inside
`pcc/py_stdlib/threading.py`.
### pending
Awaiting No.1.
