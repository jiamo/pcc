# GC4 A3b source-death live-target finish

Date: 2026-08-23

## Claim

When `pcc_gc_note_object_freeing` removes an outgoing forwarding edge, the C
transition oracle and strict freestanding pcc-Python runtime now detach the
source index, reverse target index and main edge under the GC graph lock, then
chain the ordinary forwarding node into the caller's finish field at offset 8.
The node pins its still-live target across unlock.  Generic finish performs the
ordinary target decref and forwarding-node free only after the graph lock is
released.

This path remains distinct from target death.  Source death owes the ordinary
target reference and therefore uses offset 8; target death uses offset 40 and
suppresses the already-dying target's decref.

## RED and behavior control

The source/ABI/order test was genuinely RED on the former immediate
`pcc_gc_forwarding_remove(o)` composition:

```text
1 failed in 0.14s
```

It now proves both runtime roots detach and chain under lock and invoke the
shared finish only after unlock, with no target decref or node free in the new
detach helper.

A dynamic C/strict differential covered a non-last target control and a target
whose only remaining reference was the forwarding edge.  The baseline path
already produced the required terminal results (`2 passed in 1.01s`), so this
was not called dynamic RED.  On the final implementation the complete packet
proves the control target transitions 2->1, its child remains owned until the
external target release, and the last-owner case deallocates the target and
releases the child exactly once after the edge is gone.

The first final full-gate attempt caught an ownership-surface regression: the
now-unused public `pcc_gc_forwarding_remove` extern declaration had been
removed from the managed mirror even though that ABI remains owned.  Restoring
the declaration without calling it made the focused ownership and managed
closure nodes green; the final cold packet supersedes the failed attempt.

## Frozen source identity

```text
6437cf4c1dfebb0f63c464bec343514ad72bdce97d370a872fca928670bb4c8a  pcc/py_runtime/src/py_gc_backend.c
5bc8892b682c0e457ee19b4dcd189f59c40484791220885c855da8d030680002  pcc/py_runtime/py/py_gc_backend.py
611bb8dbdf00f3129713a2a8d83d1715fae6b463251f56f475f58f1444397773  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
178e52dbd73fcf3303df496e79c985a29b8e505be05401d7aa4c3f4d2bf808d5  pcc/py_frontend/codegen/runtime_abi.py
e44fa2392a2ac806334afe1d0dde286450c7589fff0ec3295ce432d97cfd414d  tests/python/test_freestanding_gc_forwarding_retirement.py
```

## Focused gates

Final task-card payload/forwarding packet:

```text
24 passed in 129.58s
```

Log: `build/gc4-relocation-mutator-quiescence.log`, SHA-256
`976b8b5a01bf4da629c0c5c40b6b93ac8602c358e976b6a3f2fed206af3f4ed1`.

All fourteen C/strict type-specific raw-payload cases:

```text
14 passed in 133.62s
```

Log: `build/gc4-a3b-source-death-raw.log`, SHA-256
`20d46353f4c54cba583edf0ad553bde24ef46c93f8f2de2a9a9b27929f20fb70`.

Fragmentation, stable ID, both target phase-reset roots and GC3 oldification:

```text
9 passed in 23.27s
```

Log: `build/gc4-a3b-source-death-compatibility.log`, SHA-256
`b91d645dc1b60693fcabf2b145379a2a5d89b9378495e4552edb3e23c09e6ded`.

The final Python files byte-compiled, the managed strict closure compiled,
C syntax passed with `PCC_WITH_THREADS=0` and `=1`, and `git diff --check` was
clean.

## Open boundary

The known graph-lock cleanup decref/free tails reached by normal remap, target
death and source death are now deferred through explicit finish ownership.
This does not prove every graph-lock holder is a bounded no-park region.  The
next slice must audit the remaining A3b holder inventory and, only if its
preconditions hold, connect outermost graph-lock acquire/release to the A1 TLS
no-park contract: recursive acquires change graph depth only, lock wait cannot
park while falsely claiming ownership, and the outer unlock precedes no-park
exit so a pending stop is serviced safely.  Raw list/dict/set transaction
coverage and collector-owned STW remain subsequent required work.  The parent
task stays `IN_PROGRESS`.
