# GC4 A3b graph-lock holder inventory and registry tails

Date: 2026-08-23

## Claim

The pre-A3c holder audit is complete enough to reject graph-lock/no-park
integration on the current source.  The graph lock still protects paths that
can allocate/free, safepoint, invoke an extension/root callback, or run an
unbounded retirement loop.  A3c therefore remains deliberately unconnected.

One finite subset of the audit is repaired in both runtime roots: frame nodes
are prepared before graph-lock acquisition and released after graph unlock;
continuation-root nodes are detached under the lock and freed after unlock.
The C transition runtime also detaches its per-thread Backend 4 medium-buffer
state under the graph lock and frees the state after unlock.

## Audit verdict

Direct source and transitive-helper inspection found these remaining blocker
families:

- `pcc_gc_backend4_try_zpage_alloc` / strict
  `pcc_gc_backend4_try_zpage_alloc` allocate page metadata and backing spans
  from a graph-lock holder.
- C/strict frame entry can still grow or rehash the frame pointer index through
  `pcc_gc_frame_index_replace`; moving the frame-node allocation alone does not
  close that separate allocator boundary.
- C/strict relocation-set reset still frees a detached list and scans object
  metadata while holding the graph lock.
- C/strict Backend 3 promotion retains safepoint, oldification/decref and
  remembered-owner cleanup paths under the graph lock.  The C transition path
  also visits extension-module roots there.
- C `pcc_gc_visit_runtime_roots` invokes caller and extension-root visitors
  while holding the graph lock.
- Other lifecycle/reset holders still require explicit allocator/free,
  tripwire/log, callback and bounded-scan review before the inventory can be
  called safe.

This is a confirmed blocker inventory, not an A3c failure and not evidence
that no-park integration was attempted.

## RED chronology

The new frame source contract failed on the previous strict ordering:

```text
AssertionError: assert 404 < 364
1 failed in 0.10s
```

The thread-unregister source contract independently failed because no detached
finish owner existed:

```text
AssertionError: assert 'PccGcStoreBufferMediumState *detached = NULL;' in unregister
1 failed in 0.31s
```

The existing generational source test then exposed its stale variable-name
expectation after release ownership moved to `released`; the test was narrowed
to the new owner without weakening its index/pool checks.

## Frozen source identity

```text
4ce2ae2e52f02a14b2078d3f197941a0a0de09d0ea91c9e32bcab82f83379f7a  pcc/py_runtime/src/py_gc_backend.c
e1b94022c1c5f2164d78d39b66fbce87bfcaf6f7ae7212221ef2d9be9e22af75  pcc/py_runtime/py/freestanding_gc_frame_registry.py
ecd81db64e30e4f72f080e6c20c4b6324ea1eb01e12d0ebf41d5c4558c76ddff  pcc/py_runtime/py/freestanding_gc_root_registry.py
ffa7e532bfa836efe6fe52b08927a00c3eb502897e1526bb6e798bb5b4f749dd  tests/python/test_freestanding_gc_frame_registry.py
18a60182f54aab82164a364c2e4274c10d58907e554d40c982e3fee71cf9a617  tests/python/test_freestanding_gc_root_registry.py
a160361a60a7a0530f50d46bc1043e8bc0088051163175d95674f25f2c26abf3  tests/python/test_gc_threading_substrate.py
49b6d0921ae2396b5ecb5ccd3a291ae4d0eb7c6916e9c7498c94a1f34d7255e6  tests/python/test_gc_backend_generational.py
```

## Focused gates

The complete frame/root registry packet covered exact raw closure, C/strict
archive ownership, GC0 through GC4 behavior and real pthread mutation:

```text
12 passed in 263.74s
```

Log: `build/gc4-a3b-registry-tail.log`, SHA-256
`b91217db906e1044e521a95971946091cf42aacc05e0fdc403aba98e63362410`.
The C transition thread-unregister adjustment landed after that packet, so the
final-source frame pthread node was rerun and passed in `126.82s`; its durable
log is `build/gc4-a3b-registry-tail-final-source.log`.

The existing partial CMS write-buffer unregister probe passed:

```text
1 passed in 7.77s
```

Log: `build/gc4-a3b-thread-unregister-tail.log`, SHA-256
`0aad881eded50231d913c1082e063fcf50f94d9e7fd2e1c0cf35658659f1af54`.

The focused static contracts pass 3/3 including the existing generational
frame-index neighbor.  Python byte compilation, C syntax with
`PCC_WITH_THREADS=0` and `=1`, and `git diff --check` are green.

## Open boundary

A3c remains blocked by the audited holders above.  The next finite A3b slice
must remove frame-index rehash allocation/free from the frame-entry graph-lock
transaction in C and strict pcc-Python without weakening duplicate-frame
semantics.  Subsequent slices must split ZPage allocation, relocation-reset
retirement, GC3 promotion/safepoints and root callbacks before the complete
inventory can be certified and the graph lock can enter no-park at outer
ownership.  Raw container transactions and collector-owned Backend 4 STW
remain later parent-task requirements.
