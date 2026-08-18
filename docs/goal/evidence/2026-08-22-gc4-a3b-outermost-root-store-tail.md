# GC4 A3b outermost root-store tail deferral

Status: **FOCUSED GREEN for the outermost root-store helper's own lock scope
only.** `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

For one stable selected backend, the non-GC0 branch of an **outermost**
`pcc_gc_store_root` invocation no longer runs runtime logging, refcount debug
sinks, weakref invalidation, finalizers/deallocation, or other last-decref
tails while that helper owns its root/graph lock.  The source contract is
mirrored in the C transition oracle and strict freestanding pcc-Python
production runtime.  Compiled execution covers GC3 and GC4 in both runtimes
with threads enabled and the default `ATOMIC` refcount strategy.

The helper now has one prepare/commit/finish sequence:

1. It captures the debug-runtime predicate before its own lock, then acquires
   the root/graph lock and records the store.
2. It canonicalizes and retains the incoming value in a private prepared
   packet, runs the write barrier on that canonical value, snapshots the old
   root, commits the new slot, and prepares exactly one old-value decrement.
3. A terminal prepared decrement publishes `PY_FLAG_GC_DEALLOCATING` before
   the lock is released, preventing Backend 4 add, page scoring, or copy from
   selecting the logically dying object.
4. The helper releases its own lock, emits the root-store event, finishes the
   prepared incref event/debug action, and then finishes the captured old
   decrement, including weakrefs, finalizer/deallocator, GC forgetting and
   terminal logging.

Finish consumes only the captured packet: it does not reread the slot,
re-resolve a moving pointer, or decrement a second time.  A non-positive
pre-decrement count is captured without mutating it and fail-stops only after
unlock.  Invalid-value debug handling likewise records a separate bad-token
flag and tag, avoiding the rejected `INT32_MIN` sentinel collision.  Public
valid `py_incref`/`py_decref` calls retain lazy debug-environment behavior:
valid values do not add an eager `getenv` under a caller-owned graph lock, while
an invalid value defers the predicate and fatal sink to finish.  A legitimate
`PY_TYPE_CPY_HANDLE` remains valid and releases its foreign value exactly once.

This is deliberately narrower than “root-store tails are outside every graph
lock.”  Strict scheduler queue push/pop already call `pcc_gc_store_root` while
holding an outer recursive graph lock, and the C scheduler mirror calls
`pcc_gc_store_ptr` under its outer graph lock.  In those nested cases the
helper's unlock changes depth two to one, so its finish still runs inside the
caller's lock region.  A source inventory test freezes that open boundary.

## Final source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/py_obj.c` | `ff35059572b8a5ddb087ae4ab00e7e9374c65292a1221421ad80348e33169de3` |
| `pcc/py_runtime/py/py_obj.py` | `b65f97cbbd0b52e2062d55b54d143e835d350975874792036e8689abf52acdc6` |
| `pcc/py_runtime/src/py_gc_backend.c` | `55abde41ca8cac48d01c053e1c9a5894a9382b38a1de0ae96dca16eacd6141a7` |
| `pcc/py_runtime/py/py_gc_backend.py` | `9c90df0bd52a33f4ef0aa5b58f4bd8fb2b42cc0d4a3c4318b16b18438d64c414` |
| `pcc/py_runtime/py/freestanding_gc_relocation_selector.py` | `da8de4fbe9e5f689dddb2b5b5755bfeff34b3de79a791528bc30a8edd6f150eb` |
| `pcc/py_runtime/py/freestanding_gc_relocation_copy.py` | `84ea106f8a655e2366f901f0cdd1791147ab0424fc710c3fe2b7adb7599bfd7c` |
| `pcc/py_runtime/py/freestanding_gc_root_operations.py` | `e0449b4a8f3018149690b4fb197c1205adb2d7a975ab46a9492ba07c3bb37ef0` |
| `tests/python/test_gc_threading_substrate.py` | `ebc4153426f256830637fccd3c3679bb4dafd563e4a47767ca66dc07b2c1d471` |
| `tests/python/test_gc_backend4_production.py` | `af341e21323a22719b8bebd2e31113a411bce22298346af38f2e1efd767a012e` |
| `tests/python/test_freestanding_gc_root_operations.py` | `189f4ae8bc03151008b1e73804c88f2c6a9d5f386d1c8cf9dc88754f15569f1e` |

These are dirty-worktree content identities, not a clean commit or release
manifest.  The final test-only hash includes the `<sys/mman.h>` harness include
added after the focused archive had already been built; production sources did
not change across that harness correction.

## RED to green and review corrections

The genuine fail-first command was:

```bash
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_gc_threading_substrate.py::test_root_store_prepares_refcounts_under_graph_lock_and_finishes_after_unlock
```

It failed **1 failed in 0.17s** because the prepare helper did not exist.  The
final test was renamed to describe the accepted boundary:
`test_root_store_prepares_inside_and_finishes_after_its_own_lock_scope`.

Subsequent adversarial review and focused execution found and corrected these
specific gaps before the freeze:

| Finding | Final correction |
|---|---|
| A stale static expectation treated `DEALLOCATING` as still known/selectable. | Both mirrors now quarantine logical-death state; tests assert independent add, page-score, copy-source, and strict known-object rejection. |
| The first test plan had no real strict C/GC3/GC4 forwarding and count execution. | Compiled C and strict default-threaded `ATOMIC` probes cover GC3/GC4 canonicalization, finalizer lock contention, and exact retain/release counts. |
| Store/incref/decref logging could be duplicated or executed under lock. | Prepare is log-free; finish owns exactly one captured event per performed refcount update, after helper unlock. |
| Strict helpers risked becoming public ABI owners. | Prepare/finish remain private inside `py_obj` in both mirrors; public `py_incref`/`py_decref` delegate once. |
| Backend 4 add, score, and copy checks could mutually mask each other. | Each production path independently rejects `PY_FLAG_GC_DEALLOCATING`. |
| Strict GC3 known-object logic could read an arbitrary header before proving index membership. | Index-node and freeing-state checks precede the object-header load; a `PROT_NONE` unknown-pointer probe executes this ordering. |
| Debug sinks and refcount underflow handling could execute or mutate under lock. | Prepare records a bad/underflow token; finish queries/fails outside the helper lock, and `pre_rc <= 0` is never decremented. |
| `INT32_MIN` was used as both a legitimate invalid tag and an internal sentinel. | A genuine focused RED (**1 failed in 0.29s**) led to separate `debug_bad`, tag, and deferred-mode fields. |
| Public valid refcount paths could eagerly query the debug environment under an outer graph lock. | The public wrappers pass a lazy mode; only an invalid finish evaluates the predicate. |
| A valid CPython-compat handle was rejected by debug validation. | `PY_TYPE_CPY_HANDLE` is accepted; the focused probe observes `1 -> 2 -> 1` and one release hook. |
| The initial prose implied nested caller locks were also closed. | The claim was narrowed to an outermost helper's **own** lock scope, with scheduler push/pop sites inventoried as a mandatory successor. |

The final archive-reuse shard exposed one harness-only compile RED after its
first node passed: the GC3 `PROT_NONE` probe omitted `<sys/mman.h>`, so `mmap`,
`PROT_NONE`, `MAP_PRIVATE`, `MAP_ANON`, `MAP_FAILED`, and `munmap` were
undeclared.  The run stopped at first failure as required: **1 passed, 1 failed
in 0.67s**.  Adding only the missing include left every production hash and the
compiled archive unchanged; rerunning only that failed node passed **1 passed
in 0.74s**.

## Short focused execution record

The sub-60-second execution notes retained packet names, counts and durations,
but not every original shell argv or a durable raw log.  They all used the
canonical `env -u LC_ALL uv run pytest -x -n0` shape where applicable.  They
are recorded as corroborating focused results without inventing command
receipts:

| Packet | Observed result |
|---|---|
| syntax, strict `py_compile`, and diff check | green |
| final static source/order packet | `4 passed in 0.08s` |
| C invalid-debug boundary packet | `6 passed in 0.37s` |
| exact B4/B5 lazy-debug and `INT32_MIN` bad-tag packet | `2 passed in 6.67s` |
| C GC3/GC4 finalizer, forwarding, underflow, quarantine/index and resurrection neighbors | `9 passed in 8.47s` |
| default threaded `ATOMIC` strategy checks | `2 passed in 0.20s` |
| legitimate `CPY_HANDLE` debug-on check | `1 passed in 0.26s` |
| nested-boundary inventory plus renamed C handshake | `3 passed in 0.36s` |
| existing last-decref resurrection neighbor | `1 passed in 21.50s` |

These short results do not substitute for the receipt-bearing strict archive
execution below.  In particular, the resurrection neighbor does not prove the
separate metadata-restoration P0.

## Strict pcc-Python threaded execution

The one cold content-addressed build/run used this exact five-node command:

```bash
gtimeout 1020s zsh -o pipefail -c 'gtimeout 960s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_outermost_root_store_finalizer_runs_after_its_own_lock_scope[3-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_outermost_root_store_finalizer_runs_after_its_own_lock_scope[4-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_root_store_canonicalizes_forwarded_value_and_balances_exact_counts[3-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_root_store_canonicalizes_forwarded_value_and_balances_exact_counts[4-pcc_python]" "tests/python/test_gc_threading_substrate.py::test_root_store_zero_refcount_underflow_fails_stop_in_finish[pcc_python]" 2>&1 | tee build/a3b-strict-threaded-focused.log'
```

Observed result: **5 passed in 125.24s**, with all five node IDs and a final
pytest summary in `build/a3b-strict-threaded-focused.log`, SHA-256
`feda19341069cabcb6d03dfa19784c20183e0e74e2a81834023b6c0f097d769d`.

The final archive was then reused explicitly for the two quarantine/known
neighbors:

```bash
gtimeout 360s zsh -o pipefail -c 'gtimeout 300s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3d027872e125bdfaa52aa4e0-threaded-pcc-py/libpy_runtime_pcc_py.a uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_backend4_strict_runtime_quarantines_deallocating_objects_from_selection_and_copy tests/python/test_freestanding_gc_root_operations.py::test_strict_gc3_known_object_gate_executes_deallocating_rejection 2>&1 | tee build/gc4-a3b-strict-threaded-reuse-final.log'
```

As recorded above, this stopped with **1 passed, 1 failed in 0.67s** solely on
the missing harness include.  The durable log SHA-256 is
`93704a4d1acdc8f4cf0f8f6c6ca5c8e087c78ba9771d1802c09e610e236c2faf`.
After the include-only correction, the exact failed-node rerun was:

```bash
gtimeout 120s zsh -o pipefail -c 'gtimeout 90s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3d027872e125bdfaa52aa4e0-threaded-pcc-py/libpy_runtime_pcc_py.a uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_root_operations.py::test_strict_gc3_known_object_gate_executes_deallocating_rejection 2>&1 | tee build/gc4-a3b-strict-gc3-known-rerun.log'
```

Observed result: **1 passed in 0.74s**, with a final summary.  The log SHA-256
is `3c91782c44be654a25f3ab15cee52aea9de2e76af985357e50f01e57881fd6b3`.

## Runtime archive provenance

The strict gates consumed:

`/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3d027872e125bdfaa52aa4e0-threaded-pcc-py/libpy_runtime_pcc_py.a`

The read-only verifier command was:

```bash
gtimeout 120s env -u LC_ALL uv run python -m pcc.tools.runtime_archive_provenance verify --archive /Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3d027872e125bdfaa52aa4e0-threaded-pcc-py/libpy_runtime_pcc_py.a --runtime-root /Users/jiamo/my/pcc/pcc/py_runtime
```

It exited `0` in `0.34s` with no output.  Receipt fields are schema
`pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target
`arm64-apple-darwin25.5.0`, target stamp
`darwin:arm64:arm64-apple-darwin25.5.0`, 186 members, and 444 C-API symbols.
The C-API inventory contains 444 nonempty lines.

| Artifact | SHA-256 |
|---|---|
| `.pcc-threaded-pcc-py-complete` | `34bdcf395a33899136640f394f4092611416af5f740494f41a1eeb1cd5bb1022` |
| `libpy_runtime_pcc_py.a` | `b3f575bfb292d7e191190501867b8ccfa0635e1e3c38a233bf87b9a2ac424b94` |
| `libpy_runtime_pcc_py.a.provenance.json` | `16b863398e80e45b067f7c4f21b725a5292d667bcfa62f51f4cf008f3e88830c` |
| `libpy_runtime_pcc_py.a.capi_syms` | `71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda` |
| `libpy_runtime_pcc_py.a.target` | `1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1` |

No separate production owner/closure node ran for this slice, and none is
claimed.  The fresh content-addressed archive, direct path execution and
successful provenance verification bind the strict focused results.

## Independent review

Two independent read-only reviews converged on the final production/test
hashes:

- the runtime/design review reported **ZERO findings** for the narrowed
  outermost-helper claim, prepared-token ownership, debug/underflow behavior,
  terminal deallocation quarantine, C/strict mirroring, and explicit nested
  caller nonclaim; and
- the test-sufficiency review reported **ZERO findings** after the final
  `<sys/mman.h>` test-only correction, covering static order, C and strict
  GC3/GC4 execution, exact counts, debug modes, underflow fail-stop,
  independent quarantine checks, and false-green controls.

Neither reviewer ran tests, builds, or profiles.  The execution results above
belong to the implementation/gate runners.

## Explicit nonclaims and next boundary

This evidence does **not** prove:

- the default/nonthread runtime path; GC0's existing root-store branch is not a
  blanket A3b claim, even though shared `CPY_HANDLE` validation and underflow
  corrections also apply to public refcount wrappers;
- any `BIASED` or `DEFERRED` refcount mode, including their refmeta locks,
  safepoints, waits, or allocation paths;
- concurrent backend switching, or an unlocked public `py_decref` relocation
  synchronization contract;
- scheduler queue push/pop tails in strict pcc-Python or the analogous C
  `pcc_gc_store_ptr` callers that still hold an outer graph lock;
- GC2 CMS queue/flush lock regions, GC4 armed tripwires under the lock, or the
  absence of callbacks, frees, blocking I/O, CAS waits and safepoints from all
  remaining graph-lock holders;
- resurrection metadata restoration after a last-decref finalizer; that is a
  separate P0 and this slice intentionally does not clear or reconstruct that
  metadata;
- graph-lock/no-park integration, complete owner-derived list/dict/set or other
  raw-span transactions, callback-split updateable roots, constructor
  publication, managed thread handoffs, C-API raw views, buffer leases, or one
  collector-owned relocation/retirement phase;
- physical GC4 movement under concurrent mutators, forwarded-source payload
  retirement, CPython parity, GC0..4 parity, stage1/stage2 timing or
  performance, pcc2/pcc3 equality, the five-GC matrix, or a fixed point.

The next bounded A3b slice starts with the nested strict scheduler root-store
and C `store_ptr` outer graph-lock holders.  It must move their refcount/log/
finalizer tails outside the **outermost caller** region without rereading stale
slots or weakening ownership.  The same holder audit then continues through
GC2 CMS queue/safepoint flush, GC4 tripwires, and `BIASED`/`DEFERRED` refmeta
paths.  Only after every holder region is a bounded non-parking leaf may A3c
connect the outermost graph-lock acquire/release to no-park.

No broad suite, bootstrap stage, performance measurement, default/nonthread
archive build, production owner/closure gate, physical relocation test, or
five-GC matrix was run for this A3b slice.
