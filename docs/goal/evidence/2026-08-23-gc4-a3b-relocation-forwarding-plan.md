# GC4 A3b public-copy forwarding plan

Date: 2026-08-23

## Claim

For one stable Backend 4 selection and a valid managed relocation candidate,
the public relocation-copy path in both the C transition oracle and strict
freestanding pcc-Python runtime now prepares its forwarding transaction before
the final GC graph-lock tenure.

The preparation owner snapshots capacity requirements for the stable-identity,
forwarding-source and forwarding-target pointer indexes under a short graph
lock, unlocks, then allocates two identity nodes, one forwarding node and any
replacement index storage.  The final locked copy commit revalidates the
source and target, installs already-allocated index capacity, creates or
reuses stable identities, inserts the two forwarding index edges, retains the
target and publishes the forwarding-list edge.  Replaced old index tables and
all unused plan members remain owned by the plan and are freed after graph
unlock.

The private preallocated installer and its three index helpers contain no
`malloc`, `calloc`, `free` or cleanup `decref`.  A target-index insertion
failure removes the just-inserted source-index edge before returning.  The
historical shared installer remains unchanged for direct forwarding and GC3
oldification; this claim is public-copy-only.

## Frozen source identity

```text
e442b3ca2db21a42d9e306f964fd80a3c9d1a790039cb8afb3e85a844b216816  pcc/py_runtime/src/py_gc_index_table.c
810925c70f92b0c77434750cd2576a005a8acbe7eddfdf7cd7f2172ab76e3452  pcc/py_runtime/src/py_gc_backend.c
38a80a2460c9b3e9e9747604a395165f1ee64b913970b9f36629bb78b8a48bba  pcc/py_runtime/src/py_internal.h
de7a279861ce0a9542fad98afefa670c4aba17757c5616a01a5f9d67491b144f  pcc/py_runtime/py/freestanding_gc_index_table.py
7b60f11c4d8fd2bc6ca5c3fa151bc99c8f1a3a3cadc105de99f68179cef90f15  pcc/py_runtime/py/freestanding_gc_forwarding_identity.py
a826635bd18726e00937d08c5f84de172ca1fe63b5c6c3925ebea5662612e4e2  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
6240b5da637b147edd86d772363d1a08db4ff6226571628829963a6d02b549ca  pcc/py_frontend/codegen/runtime_abi.py
112ee2f30363f47cefd03052f6e6ce8d2c25d7de7a59d4b67e27621f1a8d374f  tests/python/test_freestanding_gc_index_table.py
d2c32a3e174c1cd58e81a6c01c736e026e3c92a29d56ff6630b394534326516b  tests/python/test_freestanding_gc_forwarding_identity.py
c62d25b0161169334eb7be0da80475fe1b3e45855e400c3bf86751eeae4e27ee  tests/python/test_freestanding_gc_relocation_copy.py
237b986c686cfd3025e3d3dc7efe439a5d27b9658c11c156144c0b41ffef54f3  tests/python/test_gc_backend4_production.py
```

The runtime hashes were frozen before the final focused packets and remained
unchanged through all dynamic tests.

## RED and review corrections

The new source/ABI/order regression was first run against the old direct
installer shape:

```text
tests/python/test_freestanding_gc_relocation_copy.py::test_relocation_copy_preallocates_forwarding_indexes_outside_graph_lock
```

It failed genuinely with a `KeyError` for the absent
`pcc_gc_forwarding_install_plan_prepare` ABI (`1 failed in 0.13s`).  The final
test pins C/strict plan layout and call ordering, verifies that allocation runs
after the preparation lock is released and before the final commit lock, and
rejects allocation/free/decref in the commit helpers.  The index differential
also exercises empty-table initialization, half-full insertion, refusal when
no preallocated capacity exists, undersized nonmutation, preallocated growth
and preservation of every old entry in both the C and strict roots.

The first strict closure run then failed because multiline `extern(...)`
formatting prevented the freestanding external-symbol scanner from recognizing
the three new index APIs.  Reformatting those declarations to the scanner's
canonical source shape made the exact LLVM closure pass; no runtime fallback
was added.

Local source review after the first green packet found two real mirror issues
and corrected them before final evidence:

- strict `pcc_gc_next_object_id` is an `i32` runtime global, but the new path
  initially used `load_i64`/`store_i64`; it now uses the same i32 access width
  as the established strict identity helper, with a static regression;
- a source pinned between preparation and final revalidation now increments
  `PCC_GC_COUNTER_RELOCATION_PIN_REJECTS` before returning `-2`, matching the
  retained installer in both mirrors.

## Focused gates

The complete index/forwarding/copy source, LLVM/self closure and production
archive-owner packet passed:

```text
gtimeout 330s zsh -o pipefail -c "gtimeout 300s env -u LC_ALL uv run pytest \
  -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_index_table.py \
  tests/python/test_freestanding_gc_forwarding_identity.py \
  tests/python/test_freestanding_gc_relocation_copy.py \
  2>&1 | tee build/gc4-a3b-forwarding-plan-source-owner-final.log"

20 passed in 130.84s
```

Log SHA-256:
`cca2030ce89db824051f38144a239c1f519b26fc1e56d1cd594121b1168f68b8`.

The first combined behavior attempt used a 90-second watchdog and timed out
during a cold strict exception archive build.  It produced no pytest summary
and is not evidence; immediate process inspection found no surviving
`pytest`, `pcc` or `clang` child.  The exact single strict node was then
sharded using the already measured 124--138 second cold envelope:

```text
1 passed in 124.26s
```

Log: `build/gc4-a3b-forwarding-plan-exception-strict-final.log`, SHA-256
`5c6bcf4d61a8ce7efba1f3be5c9c4d28efcf1a16a2cf48016d786782d8b5a2f5`.

With that archive warm, all fourteen type-specific raw-payload cases in both
runtime roots plus the strict payload and forwarding-retirement neighbors
passed:

```text
28 passed in 15.66s
```

Log: `build/gc4-a3b-forwarding-plan-behavior-final.log`, SHA-256
`b79c5e6c5d38ca93825852fe68aca208c69f6db9755442432df2e5e6ea15512b`.

The fragmentation/stable-ID/GC3-oldification compatibility remainder passed:

```text
7 passed in 23.47s
```

Log: `build/gc4-a3b-forwarding-plan-compatibility-remainder-final.log`,
SHA-256
`c1a0ffa06e63f079d4a166045c475126761c5cf701ea2480fe9bc5685c99d739`.

`python -m py_compile` passed for the four runtime/ABI Python files and four
focused tests.  C syntax passed with `PCC_WITH_THREADS=1` and `=0`; both modes
reported only the same five pre-existing unused-static-function warnings.
`git diff --check` was clean.

## Pre-existing stale-candidate failure

The compatibility packet initially stopped at
`test_backend4_relocation_stress_stable_ids_and_no_old_addresses`, which exits
14 because forwarding entries remain live.  Three single-variable substitution
checks against the working tree were all still red: old installer only, old
source-ZPage release only, and both old paths together.  A separate `git
archive HEAD` source tree also failed the same node before any forwarding-plan
change.

The test now prints failure-only per-round telemetry.  Current source and the
isolated `HEAD` control are identical:

```text
round=0  work=184 relocation_set=8 forwardings=56
round=1  work=64  relocation_set=8 forwardings=56
...
round=15 work=64  relocation_set=8 forwardings=56
```

The eight stale relocation candidates keep the relocation set nonempty, while
generation aging consumes work on every later step, so the idle remap path is
never reached.  This is the stale-candidate/fairness boundary already listed
as open by the parent investigation, not evidence against the forwarding-plan
implementation.  It remains red and is not reported as a passed gate.

```text
3744e39f0620dfdc1604b387341bbe4849e95ea79b419937c85d8fdecff7a914  build/gc4-a3b-forwarding-plan-preexisting-stress-current.log
c101ff953bb2ab2542dd4ca855582fc0d3fc95908ab6308f009bab565289b937  build/gc4-a3b-forwarding-plan-preexisting-stress-head.log
```

## Open boundary

This slice does not make the final graph-lock tenure wait-free.  Installing a
preallocated table still rehashes its live entries under the lock, raw object
and payload bytes are still copied there, and lock-free index-reader safety is
not newly claimed.  Source/page lifetime across the unlocked preparation
window, allocator-failure injection, partial-capacity rollback, nested callers,
concurrent drains/collectors, destroy/reuse ABA, backend switching and the
pre-existing stale-candidate/fairness failure remain open.

The shared direct-forwarding and GC3 installers still allocate and may cleanup
under their existing graph-lock holders.  Final remap/retirement, remembered
root admission, target-death cleanup, callbacks/raw leases, resurrection,
physical movement, A3c no-park integration, performance, fixed point and broad
five-GC parity are also unproved.  No broad default suite, bootstrap chain,
performance run, fixed-point gate or five-GC matrix was run for this finite
correctness slice.  The parent task remains `IN_PROGRESS`.
