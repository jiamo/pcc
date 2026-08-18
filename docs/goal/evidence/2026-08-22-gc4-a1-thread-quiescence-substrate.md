# GC4 A1 thread-quiescence substrate

Status: **GREEN for the bounded A1 thread-runtime substrate only.**
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS` because this
slice does not yet connect the substrate to complete object-access transactions
or to a physical Backend 4 relocation phase.

## Supported claim

At the source identities recorded below, compiled Darwin arm64 probes and
source/ABI contracts agree for the host-C differential oracle and the
freestanding pcc-Python runtime, in both `PCC_WITH_THREADS=0` and
`PCC_WITH_THREADS=1` variants:

- a nested TLS no-park depth defers an otherwise parking safepoint, and the
  outermost safe exit services a pending stop;
- stop publication uses release stores and the runtime exposes an acquire load;
- first registration during an already-owned stopped-world epoch waits before
  returning to user code, while diagnostic waiter accounting remains under the
  world mutex;
- public raw-thread unregister is symmetric with registration and fails stop
  for recursive unregister, nonzero no-park depth, or an unregistering owner of
  the stopped world;
- the managed thread trampoline commits the public handle before its final
  unregister; and
- runtime logging/refcount-metadata lock paths establish thread registration
  before entering locks that may reach a safepoint.

The public declarations and strict runtime ABI agree with those owners.  This
is a compiled/runtime-substrate claim coordinated by host pytest.  The C
archive is explicitly a host-C oracle, not the production runtime owner.  The
strict archives establish pcc-Python runtime ownership under the recorded
provenance policy; they do not establish a whole-compiler self-backend fixed
point.

## Explicit nonclaims and open boundary

A1 does **not** prove any of the following:

- graph-lock depth or complete list/dict/set/other owner-derived raw-access
  transactions use this no-park contract;
- generated LLVM poll sites perform the acquire load or a compiled hot loop
  reliably reaches the real safepoint;
- callback-split locals are updateable roots, or managed `Thread.start`
  parent-argument/child-result handoffs are rooted across publication;
- constructor publication, unpaired UTF-8/bytes/sequence exports, borrowed
  C-API results, or nested buffer leases are safe for motion;
- public copy/drain/page-drain/idle-remap/target-dies entries share one
  collector-owned quiescent phase;
- structural payload retirement and post-resume decref ordering are complete;
- any physical Backend 4 copy/remap/retire path is safe, or any five-GC,
  bootstrap, stage2-performance, or fixed-point gate is green.

The transition C frontend boundary also remains open.  The host-C oracle now
uses libc `abort()`, but the repository fake-libc surface used when pcc itself
compiles the transitional `pcc_threads.c` translation unit does not yet provide
a proven `abort` declaration/owner and no such pcc-C compile/link gate was run.
The strict pcc-Python kernels continue to use their freestanding
`pcc_platform_abort` owner.  Therefore the host-C green below must not be
relabeled as pcc-C transition-runtime closure.

## Red-to-green fail-stop link closure

The first compiled C nonthread gate used this exact fail-fast command:

```bash
gtimeout 240s zsh -o pipefail -c 'gtimeout 210s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_thread_no_park_nonthread_depth_and_world_owner_contract[c]" 2>&1 | tee build/gc4-a1-c-nonthread-v2.log'
```

It failed before probe execution after 6.63 seconds because `pcc_threads.o`
referenced undefined `_pcc_platform_abort`.  The log SHA-256 is
`217d3b0330d5fcd2ca6d17d28a50ff356f5e7424494b883554fd52897acd9ed3`.
That red artifact was produced at `pcc_threads.c` SHA-256
`71ac4365d4e94a5013b78a88040ecf4b859af114bcf5ee53a2b9ccf161d8ece2`
and test SHA-256
`750acde877a882dde84bee6967c8b71b8e0fbc2ecc91e2b54bd4f685ccbfef20`.

The bounded correction changed only the fail-stop owner split: the host-C
oracle calls its already-owned libc `abort()`, while the strict pcc-Python
kernels retain `pcc_platform_abort`.  It deliberately does not route these
recursive/unregister/owner violations through `pcc_runtime_tripwire_fail`,
whose logging, registration, and locking work is unsafe in those states.  The
same C node then passed in 6.38 seconds; log
`build/gc4-a1-c-nonthread-v3.log`, SHA-256
`370fdfe5207944d009a5e4d1e32b13e77d76c965b2a09c3b1ffac352fd6c9e10`.

## Frozen source and test identity

Runtime/compiler sources:

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/pcc_threads.c` | `5f5e4be2416a79d4ee04d6f61e63fb7cd24dce6a64468d65c48fd3dc7e29fa8a` |
| `pcc/py_runtime/src/pcc_runtime_log.c` | `b5eba584424a65760eb0317741dbe2d10f075e0cee0aad52f98e6d2041321772` |
| `pcc/py_runtime/py/freestanding_thread_kernel.py` | `789faab4aaf55e4e8200998718cd8b2757fffe0b2bef07442d9bc09c48651d33` |
| `pcc/py_runtime/py/freestanding_thread_kernel_pthread.py` | `34a06e565b6d2e789d4c358e386055f5aec7a8339d6156c0e48fd33cc7038b57` |
| `pcc/py_runtime/py/py_runtime_log.py` | `af88ad0d0dced8629ea2be9df3586fce6c9849fab12c7eda1e3198f35b641c31` |
| `pcc/py_runtime/include/py_runtime.h` | `f76b822f25a6600092e543144a7fa3095c85eec8feb70defcc71ec481b5ebef4` |
| `pcc/py_frontend/codegen/runtime_abi.py` | `1381d8422fdb9eed9b02e65a569fb90f7a802db582bc6e79d8b36829ea1e1b90` |

Focused tests and cache infrastructure:

| Path | SHA-256 |
|---|---|
| `tests/python/test_gc_threading_substrate.py` | `63367f2f331dff52be63c15c5d8e9ba11b3f38b1679df6ac0b7d8da8fda116e6` |
| `tests/python/test_freestanding_runtime_no_c_closure.py` | `69ae4f906b28fd75bf7423360e4839e0d9a5c6a14b2910458be374843237cfbd` |
| `tests/python/test_threaded_exception_tls_isolation.py` | `884c5c2fb3b0d71c1343371a5404100b8ea12727087600a63daa68eb294ee821` |
| `tests/python/test_freestanding_gc_frame_registry.py` | `7b3f407e4f195dacd10544cde661a340deceb6d915d77b2f8f7083455f850ff6` |
| `tests/runtime_build_cache.py` | `b1c420025ceb5709fcd2c585d12010c5e1efd85bc824da8fba81cffb45bd71d0` |
| `tests/test_runtime_archive_consumers.py` | `600f94dad2ebdeeb28d536e667f6cff64a5e8fcc0674c4be66443db106a28461` |
| `tests/test_test_infrastructure_efficiency.py` | `eaf381d5c87dbb0fd8064e152f5f4c85dfbec0884fcdfeffd5a0fefb7bb0624d` |

The cache builders now always pass an explicit `PCC_WITH_THREADS=0` or `1`.
The C cache-key schema is v2 and the strict pcc-Python completion-marker schema
is v4, so older entries and opposite threading modes cannot be silently reused
as this evidence.

## Compiled and static focused gates

Every successful pytest artifact below contains a final summary.  The canonical
command core was:

```bash
env -u LC_ALL uv run pytest -vv -x -n0 --tb=short <exact-node-ids>
```

Each invocation also had an explicit watchdog and its live output was persisted
with `tee` to the named log.  Except for the red command reproduced verbatim
above, the shell did not persist the other outer/inner watchdog values; they
are intentionally not reconstructed here.  The exact pytest node selections,
results, and durable artifact identities are:

| Mode / exact node selection | Result | Durable log SHA-256 |
|---|---:|---|
| C nonthread: `tests/python/test_gc_threading_substrate.py::test_thread_no_park_nonthread_depth_and_world_owner_contract[c]` | 1 passed in 6.38s | `build/gc4-a1-c-nonthread-v3.log` — `370fdfe5207944d009a5e4d1e32b13e77d76c965b2a09c3b1ffac352fd6c9e10` |
| C pthread: `tests/python/test_gc_threading_substrate.py::test_thread_no_park_and_stopped_world_newcomers_use_real_pthreads[c]` | 1 passed in 6.50s | `build/gc4-a1-c-pthreads-v1.log` — `16896dd0255c5360579599015979fd229c16542ff8137a8362fffd52ff4f029f` |
| C trampoline: `tests/python/test_gc_threading_substrate.py::test_thread_trampoline_commits_handle_before_final_unregister[c]` | 1 passed in 0.15s | `build/gc4-a1-c-trampoline-v1.log` — `1d0f4cc5eaea76bcba538e7f6e341e1d756f165b874a567fd557c1bdea487277` |
| C unregister fail-stop: `tests/python/test_gc_threading_substrate.py::test_thread_unregister_with_live_no_park_depth_fails_stop[c]` | 1 passed in 0.16s | `build/gc4-a1-c-unregister-failstop-v1.log` — `99d1533a38b0ad8328d91341ace6eeedcf890c6e9a39f62bb8f8fb21c4bab408` |
| C exact symbol owner: `tests/python/test_freestanding_runtime_no_c_closure.py::test_c_oracle_thread_quiescence_symbols_have_exact_owner` | 1 passed in 0.40s | `build/gc4-a1-c-owner-v1.log` — `0d795120c2ade0b0b6149e580a82d65cb85b87f06696b7d08407601ba2a73e78` |
| Adjacent biased/deferred refmeta smoke: `tests/python/test_gc_refcount_strategies.py::test_biased_and_deferred_strategy_smoke` | 1 passed in 0.31s | `build/gc4-a1-c-refmeta-strategies-v1.log` — `02681468a48f139db960e1ccce1a9712631e45494404e2f301fce587931349af` |
| Strict pcc-Python nonthread: `tests/python/test_gc_threading_substrate.py::test_thread_no_park_nonthread_depth_and_world_owner_contract[pcc_python]` | 1 passed in 122.47s | `build/gc4-a1-strict-nonthread-v1.log` — `cb039606ee7bbf24fc8a43e8a9ce9176413432f16ec6a2fff77fe247eec8cc3e` |
| Strict pcc-Python default owner: `tests/python/test_freestanding_runtime_no_c_closure.py::test_thread_runtime_is_owned_by_pcc_python` | 1 passed in 0.72s | `build/gc4-a1-strict-default-owner-v1.log` — `31983e8cd192f0c7094256574552f14402bd787e2068e40dfabe6d8ed2530c81` |
| Strict pcc-Python pthread five-node shard listed below | 5 passed in 125.30s | `build/gc4-a1-gate-strict-threaded-final.log` — `a1791101962b20654ee501220cbbc3eb6a71bf9cb05467d28a5e7be460072529` |
| Four-node source/ABI/TLS/frame static shard listed below | 4 passed in 0.08s | `build/gc4-a1-static-final.log` — `38b47cc9054d3cdd12cff3c10fd90df6f0b195713735272c824861067e930a3c` |
| Two-node cache isolation shard listed below | 2 passed in 2.31s | `build/gc4-a1-cache-contract-final.log` — `e266ba5fd599ea2daa709380bdeaddd90de69e5aa3d54359c2761932a8a1d868` |

The strict pthread shard selected exactly:

```text
tests/python/test_gc_threading_substrate.py::test_thread_no_park_and_stopped_world_newcomers_use_real_pthreads[pcc_python]
tests/python/test_gc_threading_substrate.py::test_thread_trampoline_commits_handle_before_final_unregister[pcc_python]
tests/python/test_gc_threading_substrate.py::test_thread_unregister_with_live_no_park_depth_fails_stop[pcc_python]
tests/python/test_gc_threading_substrate.py::test_strict_thread_unregister_cleanup_reentry_fails_stop
tests/python/test_freestanding_runtime_no_c_closure.py::test_explicit_thread_runtime_is_owned_by_pcc_python
```

The static shard selected exactly:

```text
tests/python/test_gc_threading_substrate.py::test_threading_substrate_public_surface_is_in_header_and_runtime_abi
tests/python/test_gc_threading_substrate.py::test_thread_no_park_source_order_and_newcomer_lock_contracts
tests/python/test_threaded_exception_tls_isolation.py::test_pcc_python_exception_slot_is_compiler_owned_tls_and_registered_root
tests/python/test_freestanding_gc_frame_registry.py::test_frame_registry_has_one_strict_source_owner
```

The cache shard selected exactly:

```text
tests/test_runtime_archive_consumers.py::test_pcc_runtime_cache_reuses_only_a_bound_verified_entry
tests/test_test_infrastructure_efficiency.py::test_tests_do_not_build_or_link_mutable_repository_c_runtime_archive
```

No broad suite, bootstrap stage, physical GC4 relocation test, stage timing, or
five-GC matrix was run for this bounded A1 slice.

## Archive and provenance receipts

The complete archive-member and `nm` owner capture is
`build/gc4-a1-archive-receipts-current.json`, SHA-256
`c8eae3f52710a354734fe4a131760113c689b7dcf56058dfa2de7f2367610a89`.
Its four materialized archive identities are:

| Mode / cache key | Archive SHA-256 | Completion marker SHA-256 | Provenance manifest / member identity |
|---|---|---|---|
| C default, `8e29bb10e7465d096827801a-c-default` | `d39930ca9cbcb137813051420a5ee0d0e9d226369b510bd4fe5a6de9f5aefabd` | `a50ca2125dc45580da795b2d039bbbeb30fcdad128c1dda56f3845f99150173c` | archive-member-list SHA-256 `bf8e4756b83f0c2f849b63d1d5da8d23057fe63b89e4492386cefcaf2bed6888` |
| C pthread, `8e29bb10e7465d096827801a-c-threaded` | `561a1c5afc1ff88412fad9f31223c2960f6df00fc4bfbb1aa8b5d51f420cba3c` | `5ce7fdddd8f684abca106afaca1699a4d9e21a63d42eb1b754ce504d55497cce` | archive-member-list SHA-256 `bf8e4756b83f0c2f849b63d1d5da8d23057fe63b89e4492386cefcaf2bed6888` |
| Strict default, `d59a147806ea1b28e7eec62d-pcc-py` | `0222cb295bbd8aa8cf4d97824aa72df61d81a0360cd2154932711db8443756d3` | `90fd65a41bcf3ab970fe0636f5d5c101fd4b3011b5448cc43ebec5a3f613ea83` | manifest `6c604ae4c02a1e80a5e9342bf06cbf77720e91cf0ee46296d087df3abeaf4e77`; verified 186-member digest `416b842560c1b4adf462017ebd539c20b6fd04e2ce62d84cd6a4d071c997ed4e` |
| Strict pthread, `d59a147806ea1b28e7eec62d-threaded-pcc-py` | `9d0dfddb71dd33dd200d9569ec77b7da28fbfaeb153d9de0020f120d4a3684d0` | `a907e58238c8ed9489b63c81dd909f03cfc408510c9b201049f7c1ca31b355bb` | manifest `f1bdebbfa58cf404f070a3b5fb8670d7a0273af827e3b62b29c44f94e732b463`; verified 186-member digest `31abb9b3f4535e766624c44c846bbc33bea52d093279d989d549f487bf714921` |

All four inventories have C-API SHA-256
`71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda`.
Both strict archives bind target stamp SHA-256
`1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1`,
whose text is `darwin:arm64:arm64-apple-darwin25.5.0`.  Their verified
provenance is schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, target triple
`arm64-apple-darwin25.5.0`, and 186 members.

The owner capture attributes every A1 thread-quiescence symbol in both C
archives to `pcc_threads.o`, in the strict default archive to
`freestanding_thread_kernel.o`, and in the strict pthread archive to
`freestanding_thread_kernel_pthread.o`.

## Independent review and process hygiene

Two independent source/test reviewers reported zero findings on the final A1
hashes.  After the C-oracle abort-owner correction, two independent re-reviews
of `pcc_threads.c` SHA-256 `5f5e4be2...` and
`test_gc_threading_substrate.py` SHA-256 `63367f2f...` again reported zero
findings.  A separate gate-route review reported zero findings for the bounded
host-C-oracle plus strict-pcc-Python route while retaining the pcc-C transition
compile/link caveat above.

After the focused gates, this read-only residue check produced no matches:

```bash
gtimeout 30s ps -Ao pid=,ppid=,pgid=,etime=,command= | rg '[p]ytest|[b]ootstrap\.sh|/pcc([123])?( |$)'
```

This bounded green evidence advances the active investigation from an A1 link
failure to a compiled A1 substrate.  It does not accept Proposal No.1 as a
complete relocation-quiescence protocol; the graph/access integration and all
other nonclaims above remain the active task boundary.
