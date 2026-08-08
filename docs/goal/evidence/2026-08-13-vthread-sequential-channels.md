# 2026-08-13 sequential virtual-thread channel implementation evidence

Task: `RUNTIME-P1-TOKIO-SEQUENTIAL-CHANNELS`

## Result

The freestanding pcc-Python scheduler now defines the second pcc-owned,
Tokio-inspired sequential slice:

- bounded MPSC with exact capacity, FIFO parked senders, direct refill on
  receive, explicit sender cloning, last-sender EOF, and receiver-close wakeup;
- oneshot with one consumed sender token and distinct value, sender-closed, and
  receiver-closed receive outcomes;
- receive-only `select2` with deterministic left bias, one composite rooted
  waiter, O(1) loser unlink, and cancellation cleanup under the scheduler lock;
- flat sequential frontend operations (`send`, `recv`, and `select2` park the
  current virtual thread) rather than Rust `Future`/`Poll`/`Waker` APIs.

Channel cores use an inline GC-traced ring. Endpoints trace the core, and the
expanded virtual-thread object traces both selected channel owners and a
blocked sender/result value. A stable 80-byte composite waiter owns one
scheduler root and transfers that root directly to the ready queue, so terminal
wakeup does not allocate. GC0..4 slot/deallocation dispatch and GC4 candidate
selection/payload checks cover the new type and reject relocation while raw
waiters or leases are active. The C runtime contains layout/deallocation oracle
compatibility only; the channel scheduling policy remains authored in
freestanding pcc-Python.

A frontend dominance bug exposed by the aggregate canary was also fixed:
multi-argument `print(...)` now persists its partially constructed argument
tuple in the generator heap frame when an argument can park, and reloads it on
resume.

## Focused evidence

The pcc-Python scheduler module emitted and compiled to a native object:

```text
gtimeout 120s env -u LC_ALL uv run make -C pcc/py_runtime \
  build_py/py_virtual_thread_runtime.o

exit 0 in 10.2s
```

The existing aggregate channel test body was compiled and executed against an
isolated temporary archive made from the cached pcc-Python archive plus the
source-current virtual-thread object. The cached generator object was retained;
its one missing `py_gen_set_may_park` symbol was supplied as a diagnostic alias
because this canary does not exercise dynamic `virtual_thread.call`.
`compile_python` was pointed directly at that isolated archive, so no shared
repository archive was overwritten:

```text
channel-canary compile+run start
channel-focused-ok
exit 0 in 4.3s
```

That one workload covered capacity-one backpressure and ordered drain,
receiver-close of a blocked sender, oneshot `None`, empty oneshot EOF, right
selection with an intact left loser, pre-ready left bias, and cancellation of a
parked select followed by successful receives from both former arms. The
default LLVM `mem2reg,sroa` pipeline and verifier also accepted its IR.

Scoped validation was green:

```text
python -m py_compile (12 changed frontend/runtime/GC files)       exit 0
python scripts/gen_port_abi_constants.py --check                 exit 0
python scripts/gen_freestanding_stdio_abi.py --check             exit 0
make -C pcc/py_runtime build/{pcc_threads,py_gc_backend,py_obj,py_obj_dealloc,py_obj_gc}.o
                                                                  exit 0
git diff --check (scoped files)                                  exit 0
python scripts/goal_state.py validate                            OK: 301 tasks validated
```

The C GC build emitted only pre-existing tautological/unused-function warnings.

## Production-gate boundary

The normal focused pytest command did not reach the test. Its source-current
pcc-Python archive fixture failed while compiling an unrelated dirty module:

```text
freestanding_gc_index_table.py:0:0: PCC-PY-COMPILE-001:
freestanding module functions require @c_abi_export:
_managed_pointer_find_slot

1 error in 171.01s
```

That unrelated file was not changed to make this slice green. Therefore the
isolated run is diagnostic host/LLVM/no-libpython behavior evidence, not a
provenance-backed production archive gate. The task remains `TODO_READY` until
the standard focused pytest runs on a source-current archive, then the
current-pcc1/self/no-libpython and GC0..4 channel matrices pass. No Rust Tokio
crate compatibility or full Tokio parity is claimed.
