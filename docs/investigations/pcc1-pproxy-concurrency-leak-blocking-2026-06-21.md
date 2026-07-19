# Investigation: pcc1 `-m pproxy` live-proxy concurrency hang, exception-churn memory leak, blocking-IO serialization, and `-vv`

## Status
IN PROGRESS — 4 fixes landed + verified; #5/#6 remain diagnosed but not fixed.
Full live-proxy rerun is currently blocked by a separate `pcc1 -m pproxy`
link failure (missing `asyncio` / `pproxy_server` module symbols); the
exception-churn memory leak itself is fixed and covered by focused regressions.

The user runs the pcc-compiled proxy as a real proxy:
```
PCC_GC_BACKEND=4 /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
  -m pproxy -l http+socks5://:8083 -r socks://<remote>:<port>
```
(`python-proxy` source MUST NOT be edited — CPython runs the same source fine, so every
defect here is pcc-runtime-side. Fixes go in `my/pcc` only.)

Symptom reported: "browser can't access" + "runs a while then crashes". Root caused to a
**cascade of pcc-runtime gaps** that a complex real async program (pproxy) exposes.

---

## ✅ FIXED + VERIFIED (commit alongside this doc)

### Fix 1 — native fd relay blocked the single-threaded event loop
`pcc/py_stdlib/asyncio.py` had `_try_stream_relay` call the **blocking** `py_asyncio_fd_relay`
(`select(NULL)`), which owned the whole connection lifetime. One open/idle connection (every
browser keep-alive) froze the loop → all other connections hung. curl single-shot worked
(closed immediately); browser (many concurrent + persistent) always hung.

Fix: cooperative non-blocking relay.
- NEW C fn `py_asyncio_fd_relay_step(fd1_in,fd1_out,fd2_in,fd2_out, mask_obj)` in
  `pcc/py_runtime/src/py_asyncio_io.c` (+ decl in `include/py_runtime.h`): one `select(timeout 0)`
  pass, forwards ready data, returns `None` when done (closes fds) / a Python-int mask when active.
  NOTE: mask is a **PyObject int** (not raw c_int64) — storing a raw i64 in a Python list hit
  `marshal_to_object: unexpected IR type i64`; comparing a c_ptr return to `0` hit
  `Layer 1 cannot coerce ClassType to int`. So the Python side uses only `_is_none(result)` + store.
- `_ACTIVE_RELAYS` list + `_drive_relays()` driven every `run_forever` iteration.
Decisive test (regressions added): hold one idle CONNECT tunnel → a 2nd request still completes
(was 8s timeout → now 0.14s). Regression test:
`tests/python/test_native_asyncio_stdlib_no_libpython.py::test_asyncio_idle_relay_does_not_block_concurrent_connection_no_libpython` (+ the existing two_stream relay test still green; full file 13 passed).

### Fix 2 — `_TASKS` leak + busy-spin in `run_forever`
`run_forever` never removed finished tasks from `_TASKS` and set `progressed=True` for every task
(even done ones) → O(N) walk per loop + never sleeps → rising CPU/RSS → livelock under load.
Fix (`asyncio.py run_forever`): drop `task.done()` tasks from `_TASKS`; only set `progressed`
when a not-done task actually ran. CPU dropped from busy-spin (~66%) to ~1%.

### Fix 3 — argparse `-vv` (stacked short flags)
`pcc/py_stdlib/argparse.py` didn't expand `-vv` → `-v -v` (only `-v` was in the option map), so
`-vv` → unknown option → `SystemExit(2)`. Added `_expand_short_clusters`/`_try_expand_cluster`
(handles count/store_true stacking + attached short-option values). `-vv` now parses.

---

## ✅ Fix 4 — exception-churn memory leak
The earlier "exception referent GC root" hypothesis was too broad for this
pproxy leak. The concrete root cause was simpler ownership drift:

1. Generated Python `raise SomeException(...)` built an owned temporary
   exception, then called `py_raise`. `py_raise` retains a TLS-owned reference;
   the generated raise path did not release the caller-owned temporary.
2. Runtime helpers did the same pattern directly:
   `py_raise(py_exc_new(...))`. In pproxy's idle loop the hot helpers were
   iterator exhaustion (`StopIteration`, tag 8) and missing-attribute fallback
   (`AttributeError`, tag 6).

Fixes:
- `pcc/py_frontend/codegen/exception_lowering.py`: release owned exception and
  cause temporaries after `py_raise` / `py_exc_set_cause`.
- `pcc/py_runtime/include/py_runtime.h`,
  `pcc/py_runtime/src/py_exc_tls.c`, and
  `pcc/py_runtime/py/py_exc_tls.py`: add `py_raise_owned(exc)`, for helpers
  that just created an owned exception object.
- `pcc/py_runtime/src/py_iter.c` + `pcc/py_runtime/py/py_iter.py`: use
  `py_raise_owned` for iterator TypeError / StopIteration helpers.
- `pcc/py_runtime/src/py_obj_ops_dispatch.c` +
  `pcc/py_runtime/py/py_obj_ops_dispatch.py`: use `py_raise_owned` for missing
  attribute and weakref ReferenceError helpers.

Focused regression:
```
env -u LC_ALL uv run pytest tests/python/test_exception_raise_lifetime.py -q -n0
# 3 passed
```

The regression covers:
- generated `raise AttributeError("x")`
- runtime `next(iter([]))` -> caught `StopIteration`
- runtime `obj.missing` -> caught `AttributeError`

pcc1 verification after rebuilding stage 1:
```
env -u LC_ALL scripts/bootstrap.sh --out-dir build/bootstrap-pytest-self --backend self --stage 1
# stage=1 succeeded; otool -L build/bootstrap-pytest-self/pcc1 shows no libpython
```

The rebuilt pcc1 compiles and runs the StopIteration and AttributeError
probes with closed lifetimes:
```
stop exception_counts {'alloc': 50, 'new': 50, 'raise': 50, 'clear': 50, 'dealloc': 50} exc_free 50
attr exception_counts {'alloc': 50, 'new': 50, 'raise': 50, 'clear': 50, 'dealloc': 50} exc_free 50
```

Full `pcc1 -m pproxy` no-log live-proxy verification is currently blocked
before server startup by a separate link failure:
```
Undefined symbols for architecture arm64:
  "_.class.asyncio.StreamReader"
  "__pcc_py_module_top_asyncio"
  "__pcc_py_module_top_pproxy_server"
  "_user_asyncio_get_event_loop"
  "_user_pproxy_server_main"
```

However, the same `pcc1 -m pproxy` command with `PCC_LOG=exception` now shows
the churned exceptions being freed up to that link boundary:
```
alloc=815217 new=815217 raise=815217 clear=815217 dealloc=815183
new tag 6(AttributeError)=487719, new tag 8(StopIteration)=327497
```

The remaining 34 non-deallocated exceptions occur at the failing process exit /
diagnostic boundary, not as the previous proportional leak. Reducing
exception-as-control-flow churn is still a performance goal, but the refcount-1
temporary exception leak is fixed.

## ⏳ #5 CONNECTION-SETUP SERIALIZATION (blocking I/O) — diagnosed, NOT fixed
`sample` of a stalled proxy shows the loop blocked in:
`run_forever → _accept_once → stream_handler → ProxySimple.prepare_connection → Socks5.connect →
StreamReader.readexactly → _fill_once → py_asyncio_fd_recv → __recvfrom` (blocking recv of the
remote SOCKS5 handshake). pcc coroutines **run to completion (no suspend/resume)** and `_accept_once`
synchronously `_py_await`s the handler, so each new connection's remote-handshake recv blocks the
whole single thread. With a responsive remote it serializes (10 concurrent → 1.3s..2.4s climbing,
all 200); with a slow/flaky remote it freezes. Real fix = a selector-based non-blocking event loop
with suspendable coroutines (CPython asyncio model) — major rework.

## ✅ Fix 5a — relay throughput: avoid sleeping after forwarding data
User-observed throughput on the same remote SOCKS path:
CPython pproxy on `:8081` reached about 340 Mbps on fast.com, while pcc1 pproxy
on `:8083` reached about 290 Mbps. Single fast.com runs are noisy, but the code
had a deterministic cap:

- `py_asyncio_fd_relay_step()` forwards at most one 64 KiB chunk per call.
- `_drive_relays()` only returned `progressed=True` when a relay finished.
- Therefore `run_forever()` called `_usleep(1000)` even immediately after a
  relay step had just forwarded bytes.

That creates a rough single-relay cap of one 64 KiB chunk per millisecond,
before runtime overhead. Fix:

- `pcc/py_runtime/src/py_asyncio_io.c`: set a progress side-channel when a
  relay step receives/sends data.
- `pcc/py_runtime/include/py_runtime.h`: expose
  `py_asyncio_fd_relay_step_last_progress()`.
- `pcc/py_stdlib/asyncio.py`: if the last relay step made progress, keep the
  event loop hot and skip the idle sleep; fully idle relays still sleep, so the
  earlier idle-connection concurrency fix does not regress into a busy-spin.

Verification:
```
env -u LC_ALL uv run pytest tests/python/test_native_asyncio_stdlib_no_libpython.py -q -n0
# 13 passed

env -u LC_ALL scripts/bootstrap.sh --out-dir build/bootstrap-pytest-self --backend self --stage 1
# stage=1 succeeded
```

External fixed-file checks through a temporary new pcc proxy on `:8103` using
the same `socks://100.118.195.46:8087` remote were still network-noisy, but no
longer supported a hard 15% pcc-only cap:
```
OVH 100MB via CPython :8081  ~8.8-9.0 MB/s
OVH 100MB via new pcc :8103  ~8.1-8.6 MB/s
```

Follow-up native drain batching (same fix area) lets one relay step drain up to
32 ready 64 KiB chunks before returning to the Python event loop. This removes
the remaining per-chunk Python loop overhead while bounding fairness impact.
The progress sentinel now returns `py_True`/`py_None` rather than allocating a
new int per active step.

Controlled local benchmark (256 MiB local HTTP file, CPython SOCKS5 remote,
front proxy alternates CPython vs rebuilt pcc1, same `-r socks://127.0.0.1:<port>`
shape as the real command):
```
direct local HTTP       ~996 MB/s
CPython front pproxy    ~366-386 MB/s
pcc1 front pproxy       ~468-491 MB/s
```

So the pcc relay hot path can now beat CPython in a controlled SOCKS topology.
Any remaining fast.com delta on `socks://100.118.195.46:8087` should be treated
as network/remote/TLS/multi-connection behavior until a same-topology repeated
benchmark proves otherwise. The larger #5 connection-setup serialization issue
remains open.

## ⏳ #6 `-vv` dynamic instance attribute — diagnosed, NOT fixed (LEAST important; debug-only)
`pproxy/verbose.py:52 args.verbose = verbose` → `AttributeError: verbose`. BUT pcc broadly supports
dynamic instance attributes — verified standalone: new attr on plain class, on a `setattr`-built
instance, on a cross-module imported class, and assigning a nested closure to a new attr ALL work.
`py_instance_setattr` already stores unknown attrs in a hidden `__dict__` slot (`dynamic_attr_slot`,
returns the slot unless `PY_CLASS_FLAG_SLOTS_ONLY`). So the failure is specific to the stdlib
`argparse.Namespace` instance in the real `-m pproxy -vv` path — a localized type-inference/codegen
quirk (likely `args.attr = x` lowering to a static field store instead of dynamic setattr for that
class), NOT a general object-model gap. Not yet reproduced standalone (`import argparse` standalone
hits the no-libpython multi-file fallback; the `-m` path differs).

---

## Environment notes / gotchas
- Remote `socks://100.118.195.46:8087` is FLAKY (went unresponsive mid-session; user restarted it).
  `t-arm1:8084` is a known-good remote. A dead remote makes #5's blocking catastrophic.
- The ~12s silent compile of `pcc1 -m pproxy` (verbose=False in `cli_bootstrap.py:6200`) → connect
  too early = "connection refused". And SIGINT/kill of the child prints a misleading
  `Error: pcc1 compiled module run failed` (cli_bootstrap.py:6222) — that is signal-exit, not a crash.
- The compiled proxy default GC backend is 0 (`py_substrate.py:143`); the user often sets `PCC_GC_BACKEND=4`.
- NEVER broad-kill: only `pkill -f pproxy.out` (the user runs their own `python -m pproxy` on :8082).

## Deep-research (general techniques) in flight
Run ID `wf_c123aca9-67e`, script `…/workflows/scripts/deep-research-wf_c123aca9-67e.js` — researching
CPython/PyPy/V8 techniques for: exception lifetime/GC-root cleanup + StopIteration/AttributeError-free
control flow (#4), selector event loop + coroutine suspension (#5), dynamic-attr (`__dict__`/hidden
classes) (#6). Report lands in the workflow transcript dir.
