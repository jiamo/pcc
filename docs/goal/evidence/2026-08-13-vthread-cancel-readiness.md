# Virtual-thread cancellation and sequential readiness slice (2026-08-13)

Claim level: host frontend + C runtime, `ir_scaffold=on`, `libpython=off`,
Darwin arm64, plus a host-pcc build of the pcc-Python runtime archive. This
proves a narrow pcc-owned, Tokio-inspired sequential API and that its mirrored
pcc-Python runtime sources compile into an auditable archive; it does not prove
Rust Tokio compatibility, current-pcc1/self execution, the full GC0..4 matrix,
channels/select, async DNS/files/processes, or multi-carrier production parity.

Implemented:

- `cancel(task)` is cooperative. Parked timer, IO, and join registrations are
  detached under the scheduler lock without a lost-wakeup allocation window.
  Cancellation becomes terminal only on the target carrier; entered
  synchronous `finally` blocks run first. Cleanup failure wins as `RAISED`.
- Pre-start cancellation does not enter the task body. Nested sequential child
  generators are closed before the parent cleanup runs. Cancellation cleanup
  is deliberately non-parking in this slice.
- `readable(fd)` and `writable(fd)` preserve sequential virtual-thread source
  style while lowering to the runtime fd park contract. Readiness is only a
  wake signal: callers must retry their nonblocking operation. This raw-fd
  slice requires the descriptor to remain open and not be numerically reused
  until the wait completes or is cancelled; scheduler-level resource
  generation/close notification remains open.
- An immediately ready fd now continues the current `RUNNING` task in place;
  it is not duplicated in the ready queue and does not gain a synthetic yield.
- On Darwin the active runtime reports backend `1` and uses the existing
  kqueue waitset. The backend owns an interruptible single live wait and the
  scheduler lock is not held across its blocking kernel wait.

Focused gates:

```text
gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_sequential_readable_writable_use_platform_reactor
```

Result: `1 passed in 8.56s`. The process printed backend `1`; `writable()`
completed before a separately queued observer, while `readable()` resumed only
after a pipe writer ran.

```text
gtimeout 180s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  'tests/python/gc_production_contract/test_vthread_io_waitset_runtime.py::test_production_io_waitset_modes_preserve_roots[auto-0]'
```

Result: `1 passed in 7.62s`. This is the Darwin auto/kqueue C production
runtime under GC0 only; the test covers live pipe/socket wake, timeout,
same-fd waiters, root-only reachability, early completion, and node reuse.

The cancellation regression also passed earlier in this source snapshot:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_virtual_thread_frontend.py::test_virtual_thread_cancel_is_cooperative_and_runs_sync_cleanup
```

Result: `1 passed in 0.96s`. It covers timer cancellation, duplicate request,
pre-start cancellation, cleanup failure, nested child/parent cleanup, and a
parked join waiter.

The same cancellation node was rerun after the final generator-root reload
change, with `PCC_GC_BACKEND=4` and an explicit `gc.collect()` inside the
entered timer cleanup. Result: `1 passed in 10.37s`.

The exact pcc-Python runtime snapshot was also compiled from a clean temporary
copy with live output retained in `/tmp/pcc-vthread-pccpy-build.log`:

```text
gtimeout 900s env -u LC_ALL make -C /tmp/pcc-vthread-pccpy.NomRP4/py_runtime \
  PCC=/Users/jiamo/my/pcc/.venv/bin/pcc \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python \
  PCC_REPO_ROOT=/Users/jiamo/my/pcc libpy_runtime_pcc_py.a
```

Result: exit `0`; the 5.4 MiB archive and its provenance manifest were
published. This is a host-pcc archive-build claim, not a pcc1 execution claim.

Current-pcc1 note: the required focused integration node was first attempted
after the host/runtime gates above, but its outer 600-second watchdog expired
while the fixture was cold-building the pcc-Python runtime and pytest emitted
no final summary. The durable log is
`/tmp/pcc-tokio-sequential-current-pcc1.log`. Localization then proved the
runtime archive build independently, as recorded above. A source-current
stage1 build was subsequently run with live output under a 900-second watchdog:

```text
gtimeout 900s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
```

It remained in active high-CPU parallel frontend compilation but exhausted the
watchdog with exit `124`, emitted no completion summary, and published no
`build/bootstrap/pcc1`. Its log is
`/tmp/pcc-tokio-sequential-stage1.log`. This is not green evidence. All
matching bootstrap, pcc and worker children exited after the watchdog.

Post-review hardening in the same source snapshot:

- timer and IO wakes reserve the replacement ready node/root before removing
  their existing scheduler registration; allocation failure preserves and
  re-arms the wait instead of losing it;
- pcc-Python IO delivery intersects each waiter's own interest mask, so one
  fd's writable event cannot wake a readable-only waiter;
- a task parked in join rejects unrelated public start/unpark/sleep/fd-wait
  mutations, preserving one active wait and its rooted backlink;
- live kqueue/epoll waits reserve result capacity before the one-shot kernel
  drain, so finish has no fallible allocation after consuming readiness;
- lexical effect analysis now respects parameters/local rebinding and
  function-local virtual-thread imports instead of silently changing the
  wrong callable ABI.

The final narrow regression set for this snapshot completed with
`8 passed in 15.86s` (`-vv -x -n0`). It included the two transparent-park
nodes, task-local failure, join, GC4 cancellation, the public sequential IO
node, the self-backend waitset node, and Darwin auto/kqueue GC0 IO roots.
