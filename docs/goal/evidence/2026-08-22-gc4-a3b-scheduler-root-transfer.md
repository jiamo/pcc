# GC4 A3b scheduler queue root-transfer transaction

Status: **FOCUSED GREEN for the scheduler queue root-transfer transaction
only.** `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

For one stable selected backend, valid values and mutex lifetime, threads
enabled, and the default `ATOMIC` refcount strategy, scheduler queue
push/pop/free/release no longer run a root-transfer refcount/log/finalizer tail
while they own the GC graph lock.  This is mirrored by the C transition oracle
and strict freestanding pcc-Python production runtime and is compiled-execution
green for GC3 and GC4.

The accepted transaction is deliberately private and split phase:

1. A queue entry and its 24-byte scheduler-root node are allocated before the
   graph lock.  Each 128-byte root-store plan is initialized before that lock.
2. The caller commits the prepared retain, forwarding-aware barrier/store and
   old-value decrement token under the graph lock.  Root link/unlink and the
   queue entry's root-handle state change in the same locked structural
   transaction.
3. Pop commits the output retain before it clears the queue entry, so ownership
   transfers without a zero-count gap.  Both commits use the canonical
   forwarded value for GC3 and GC4.
4. The graph lock is released before cycle-request publication, root-node
   free, queue-entry free/recycle, refcount/runtime logging, weakrefs,
   finalizers and terminal deallocation.  Structural cleanup precedes plan
   finish and finish consumes captured state rather than rereading a stale
   slot.

The four root-transfer paths (`push`, `pop_into`, live-entry `queue_free`, and
the failed-publication/queue-mutex release path) contain no resolver, public
root register/unregister, public root/store helper, allocator/free, refcount
finish, or finalizer tail in their graph-lock regions.  Failure wiring is also
frozen: root-node allocation failure recycles the entry; failed root
publication frees the node, recycles the entry, finishes the plan and returns;
queue-mutex acquisition failure releases the published entry; and queue-entry
pool allocation/recycling maintains an exact bounded free-list count.

The cross-object plan is internal-only: `PccGcStoreRootPlan` exists in
`py_internal.h`, is absent from public `py_runtime.h` and `RUNTIME_SIGNATURES`,
and its three functions are imported only through
`FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`.  C locks its 64-bit layout to two
56-byte prepared-refcount packets plus backend/debug/state at offsets
`112/120/124`; strict allocates exactly 128 bytes and uses the same offsets.
The queue layout/reuse probe likewise locks the strict 48-byte queue offsets
`0,8,16,24,32,40`, observes a popped entry at `free_head` with count one, then
proves the next push reuses that exact address and restores a null free head
and zero count.  This is an internal layout contract, not a new public runtime
ABI.

## Final source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/py_internal.h` | `5572802bd64c82de0a523d2846b200a2237d1ea1c8de8ca7d4216e4b9905fa05` |
| `pcc/py_runtime/src/py_obj.c` | `69b90635c57cbaa95458a71ac9317c0debef2799315886ddd2069735c000df4f` |
| `pcc/py_runtime/py/py_obj.py` | `f91a8cffbe87888c4cc0323a4e26d502c24554c531617a2f2fb60ee6149e8fcf` |
| `pcc/py_runtime/src/py_gc_backend.c` | `223d0d03406ee2fc3064ec82f5af6a3cf8e1259d75c741db93da98204163cdfe` |
| `pcc/py_runtime/py/py_gc_backend.py` | `f3bf662a01b78016a0e41297ba84bf5d0622a79f51adacca114bdbdd4e27a671` |
| `pcc/py_frontend/codegen/runtime_abi.py` | `3b63737e9e0cdf2dd97fb54d92a6058badb60f7e53f3d5d3eae1967115d1ded1` |
| `tests/python/test_gc_threading_substrate.py` | `55975965b33eca5c8bd60d8e1c8b306cab11fb460bea0c978ee7763b3e91ae87` |

These are dirty-worktree content identities, not a clean commit or release
manifest.  All final gates and both independent reviews below used this exact
seven-file set.

## RED to green and review corrections

The primary genuine production RED was the exact old-production C/GC3 node:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_pop_finalizer_runs_after_outer_graph_unlock[3-c]'
```

The test's child watchdog expired after 20 seconds and pytest stopped at the
first failure: **1 failed in 20.68s**.  The handshake is deterministic, not a
timing-only sleep: the replacement object's finalizer joins a true pthread;
that worker has already announced readiness and, once released by the
finalizer, enters the real queue-mutex path and then
`pcc_gc_object_is_known`, which contends for the same graph lock.  The old
queue-pop implementation invoked that finalizer while retaining the graph
lock, creating a join-to-graph-lock cycle.  No durable log was created for
this RED.  After the first production transaction split, the same command
passed **1 passed in 6.77s**; the final combined C handshake gate below passed
both GC3 and GC4 in 0.30 seconds.

The source/order contract also failed first on the old live-entry free path:
**1 failed in 0.21s**, because its graph-locked region did not contain the new
prepared-plan commit.  Later deliberate contract-strengthening runs of that
same exact static command failed before their production counterparts were
wired: the strict cross-object ABI lookup raised
`KeyError: pcc_gc_store_root_plan_init` (**1 failed in 0.21s**), and the exact
C prepared-packet/layout assertion was absent (**1 failed in 0.21s**).  The
final static gate below is green on the private ABI, exact layout, four path
orderings and rollback edges.  The pre-existing outermost-root static neighbor
also exposed one stale test expectation after delegation moved debug capture
into plan initialization (**1 failed in 0.09s**); updating that assertion to
the delegated boundary passed **1 passed in 0.18s** and its final expanded
neighbor packet is green below.  These exact results are retained in the
runner session record; none of these short REDs created a durable log.

The first GC3 forwarding harness had two test-design REDs under this exact
single-node command, not accepted production failures:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_transfer_canonicalizes_forwarded_value_and_balances_exact_counts[3-c]'
```

Direct `pcc_gc_step(1024)` did not deterministically force minor refill (**1
failed in 0.28s**), and the first diagnostic version used the wrong target
refcount expectation (**1 failed in 0.31s**).  The exact results are retained
in the runner session record; no durable log was created.  The final probe
instead keeps an owned registered root across deterministic minor refill,
retains a stale direct source for the queue push, observes forwarding
canonicalization, releases the live source, and checks every exact count.  Its
final C and strict GC3/GC4 gates are green below.

Two independent adversarial rounds then prevented false greens:

| Review gap | Final correction |
|---|---|
| Dynamic scheduler nodes initially selected only C. | Both finalizer and forwarding/count/reuse probes select C and strict pcc-Python for GC3 and GC4. |
| One finalizer phase did not prove terminal queue-entry clearing, and the worker did not traverse both queue-mutex and graph-lock boundaries. | The final probe has replacement-output and terminal-clear phases; the helper traverses the real queue mutex before the graph-lock contender. |
| Forwarding coverage could miss a stale direct push, live-entry `queue_free`, or actual pool reuse. | The final probes cover stale-source canonicalization, live free, and exact entry-address/free-head/count reuse. |
| A passing recycle test could still be free-plus-malloc coincidence. | The private queue layout witness reads `free_head`/`free_count` between pop and push and requires the identical entry pointer. |
| Static coverage did not freeze failure rollback. | Exact C/strict assertions lock alloc-pop/count decrement, recycle-link/count increment, all fallback frees, allocation-null rollback, failed publication ordering, queue-mutex failure release, and successful publication/link. |

## Final focused gate commands and results

The final static/order/layout/rollback command was:

```bash
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_scheduler_queue_root_transfer_plans_finish_after_outer_graph_unlock
```

Result: **1 passed in 0.34s**.  This sub-60-second gate did not create a
durable log.

The final C GC3/GC4 forwarding, exact ownership/count and entry-reuse command
was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_transfer_canonicalizes_forwarded_value_and_balances_exact_counts[3-c]' 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_transfer_canonicalizes_forwarded_value_and_balances_exact_counts[4-c]'
```

Result: **2 passed in 0.68s**.  This sub-60-second gate did not create a
durable log.

The final C true-pthread finalizer/queue-mutex/graph-lock handshake command
was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_pop_finalizer_runs_after_outer_graph_unlock[3-c]' 'tests/python/test_gc_threading_substrate.py::test_scheduler_queue_pop_finalizer_runs_after_outer_graph_unlock[4-c]'
```

Result: **2 passed in 0.30s**.  This sub-60-second gate did not create a
durable log.

The final strict cold GC3 handshake command was:

```bash
gtimeout 600s zsh -o pipefail -c 'gtimeout 540s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_scheduler_queue_pop_finalizer_runs_after_outer_graph_unlock[3-pcc_python]" 2>&1 | tee build/gc4-a3b-scheduler-root-tail-strict-gc3.log'
```

Result: **1 passed in 138.75s (0:02:18)**.  The log records the node ID and
final pytest summary.  Its SHA-256 is
`01c512797a815813599ead93e2bbcca5440bc576e2de70830f762f855d279a18`.

The remaining strict GC4 handshake plus GC3/GC4 forwarding/count/reuse command
used the warm content-addressed runtime cache:

```bash
gtimeout 300s zsh -o pipefail -c 'gtimeout 240s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_scheduler_queue_pop_finalizer_runs_after_outer_graph_unlock[4-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_scheduler_queue_transfer_canonicalizes_forwarded_value_and_balances_exact_counts[3-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_scheduler_queue_transfer_canonicalizes_forwarded_value_and_balances_exact_counts[4-pcc_python]" 2>&1 | tee build/gc4-a3b-scheduler-root-tail-strict-warm.log'
```

Result: **3 passed in 1.80s**.  The log records all three node IDs and the
final summary.  Its SHA-256 is
`be6bfeca4c8d9a19d5861dd867d34f813ae518505e2877a1c1402c1392f2723d`.

The precise outermost root-store neighbor command was:

```bash
gtimeout 300s zsh -o pipefail -c 'gtimeout 240s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_root_store_prepares_inside_and_finishes_after_its_own_lock_scope tests/python/test_gc_threading_substrate.py::test_outermost_root_store_finalizer_runs_after_its_own_lock_scope tests/python/test_gc_threading_substrate.py::test_root_store_canonicalizes_forwarded_value_and_balances_exact_counts tests/python/test_gc_threading_substrate.py::test_root_store_zero_refcount_underflow_fails_stop_in_finish tests/python/test_gc_threading_substrate.py::test_c_root_store_debug_off_invalid_new_preserves_benign_update tests/python/test_gc_threading_substrate.py::test_c_root_store_debug_on_invalid_value_traps_after_expected_boundary 2>&1 | tee build/gc4-a3b-root-neighbors.log'
```

It collected and passed exactly 15 nodes: the static plan; C/strict GC3/GC4
finalizer and forwarding nodes; C/strict underflow nodes; the C debug-off node;
and three C debug-on invalid-value parameters.  Result: **15 passed in 3.57s**.
The log SHA-256 is
`ff2d83ff933a6ceaf7ebaf547915dbb909616ca1db4c8a633bbf18ab244514`.

The GC3/GC4 queue-forwarding neighbors and existing C all-backend freelist
neighbor command was:

```bash
gtimeout 600s zsh -o pipefail -c 'gtimeout 540s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy tests/python/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding tests/python/test_gc_coroutine_scheduler_roots_production.py::test_scheduler_queue_entry_freelist_preserves_roots_across_backends 2>&1 | tee build/gc4-a3b-queue-neighbors.log'
```

Result: **5 passed in 9.06s**.  The first four nodes are C/strict GC3/GC4
forwarding neighbors; the fifth is the existing C all-backend freelist test,
not a strict five-backend claim.  The log SHA-256 is
`eebe54d752eb3c9447126584359f5596de66c6c3f5dc0de957542a4fc9cf0e63`.

## Strict runtime archive and cache receipt

The cold strict gate produced the content-addressed cache directory:

`/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/923d22836074c49ce5cd8798-threaded-pcc-py`

The completeness marker is the cache receipt; there is no separate artifact
whose filename contains `receipt`.  Its schema is
`pcc.runtime-build-cache.v4`, its key is
`923d22836074c49ce5cd8798-threaded-pcc-py`, and its recorded archive,
manifest, C-API inventory and target hashes match the sidecars below.  The
provenance manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, 186 members, and 444 C-API symbols.  The target
stamp content is `darwin:arm64:arm64-apple-darwin25.5.0`.

| Artifact | SHA-256 |
|---|---|
| `.pcc-threaded-pcc-py-complete` | `9c65eadd1b954a3c613be3a4bdc703c6b793e7ee6db049ecba54aefb61085a79` |
| `libpy_runtime_pcc_py.a` | `61fe3b5ac5e49e388860f3d27912dc94f1d7723191867e13df56f9fbb0fd6a09` |
| `libpy_runtime_pcc_py.a.provenance.json` | `9f7e122d495c92f75cc03588d538642e219572ee6152aff281745f41a938bf6b` |
| `libpy_runtime_pcc_py.a.capi_syms` | `71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda` |
| `libpy_runtime_pcc_py.a.target` | `1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1` |

The archive and provenance were published at 11:06:42/43 +0800 and the marker
and cold log at 11:06:44 +0800.  These identities were inspected read-only;
no separate provenance-verifier command, production owner/closure gate, or
explicit `PCC_RUNTIME_ARCHIVE` override was run for this slice.

## Independent review

Two independent read-only reviews converged on the exact seven-file identity
above:

- `/root/a3b_root_tail_impl` reported **ZERO findings** for runtime/design:
  private plan ownership, C/strict ordering, valid queue mutex lifecycle,
  stable-backend behavior, default `ATOMIC` refcounting and the narrow GC3/GC4
  claim; and
- `/root/a3b_docs_review` reported **ZERO findings** for source/test/ABI and
  test sufficiency after the final test-only reuse and rollback corrections:
  exact layout, all four paths, true-pthread handshake, C/strict GC3/GC4,
  forwarding, exact ownership/counts and false-green controls.

Neither reviewer ran tests, builds, or profiles.  The commands above belong to
the implementation/gate runner.

## Explicit nonclaims and next boundary

This evidence does **not** prove:

- GC2 CMS graph-lock safety, its C TLS write-barrier buffer full-flush path, or
  any strict algorithmic mirror of that queue;
- lossless CMS behavior when the mark queue is full or accepts only a partial
  batch, retry/admission across an outer unlock, thread-exit draining, or a
  backend epoch that makes service safe across concurrent backend switching;
- `BIASED`/`DEFERRED` or any non-default refcount strategy;
- concurrent backend switching, unlocked public `py_decref` relocation
  synchronization, GC4 tripwire transitivity, or complete bounded-holder/
  no-park coverage;
- strict debug-invalid parity, queue-mutex corruption/unlock-failure fault
  injection, or destructive reentry into the same queue during `queue_free`;
- unthreaded/default-nonthread or GC0..4 C/strict parity; the existing C
  all-backend freelist neighbor is not such a claim;
- broader non-scheduler C `pcc_gc_store_ptr` outer holders, resurrection
  metadata restoration, graph-lock/no-park integration, complete raw container
  transactions, callback/root handoffs, constructor publication, managed
  thread handoffs, C-API raw views or buffer leases;
- physical Backend 4 relocation/retirement under concurrent mutators, stage or
  performance acceptance, broad parity, the five-GC matrix, or a fixed point.

The next A3b boundary is the **GC2 C CMS graph-lock to queue-lock/safepoint
pending-flush path**.  A buffer-full write barrier can currently be reached
inside the outer graph lock and then acquire the CMS queue lock, whose waiting
path can poll a safepoint.  The immediate bounded successor is C-only: record
exact TLS pending/overflow/epoch/count state for the 32nd/33rd-and-later
barriers while locked, service eligible work only after the **outermost** graph
unlock, ensure a nested unlock never drains, and introduce no new loss while
the queue admits the whole batch.  Sequential/serialized stop, switch-away,
reset/restart, and unregister must clear or invalidate pending/count/overflow/
epoch state (or make an epoch mismatch drop it without service), and
threads-off behavior must remain unchanged.  Simultaneous backend-switch
admission remains later.  That narrow edge
removal must not be promoted to a lossless CMS or C/strict parity claim: full
or partial queue acceptance can still leave an unaccepted suffix, thread exit
still lacks a proved partial-delivery drain, concurrent backend switching
remains a separate ownership boundary, and strict pcc-Python has no
algorithmic TLS CMS queue/buffer/lock mirror.  Those remain explicit later
blockers; the strict side of the immediate slice can only freeze the absence
of an analogous lock/safepoint edge and the parity nonclaim, not claim an
algorithmic mirror.

No broad suite, bootstrap stage, performance measurement, default/nonthread
archive, physical relocation test, or five-GC matrix was run for this A3b
scheduler slice.
