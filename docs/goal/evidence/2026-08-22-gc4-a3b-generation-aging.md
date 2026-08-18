# GC4 A3b generation-aging graph-lock tenure

Date: 2026-08-22

## Claim

For a stable Backend 4 selection, valid tracked objects, threads enabled, and
the default `ATOMIC` refcount strategy, the C transition oracle and strict
pcc-Python runtime now age pending young objects through one shared intrusive
worklist.  Each graph-lock tenure examines at most 16 nodes, detaches each
examined node exactly once, performs only the valid `YOUNG -> OLD` transition,
and releases the graph lock before the next safepoint.  One public step may use
multiple tenures up to its caller budget; its work result counts examined nodes,
while promotion telemetry counts only real transitions.

The strict owner of `pcc_gc_backend4_step_generation_aging` moved from the
ordinary managed runtime module to the existing freestanding generational
scheduler without changing the symbol or C ABI signature.  This is material:
ordinary `py_gc_backend.py` functions receive compiler-injected entry/backedge
polls, whereas the freestanding closure does not.  The dispatcher still calls
the same symbol in the same GC4 phase order.

Tracked allocations whose final header is explicitly `YOUNG` join the worklist
under GC1/GC2 as well as GC3/GC4, so a later stable switch to GC4 does not lose
pending generation work.  Explicit `OLD` allocations do not join.  Promotion
uses one atomic add across the adjacent `YOUNG`/`OLD` bits, preserving unrelated
header flags, and updates a containing ZPage directly through the object node.

## Frozen source identity

```text
1280533c4e85afe15468500ee12e9f0621130a7d6ae6e63e00f19d3d5fbfa479  pcc/py_runtime/src/py_gc_backend.c
2e34ae561df7fc541f7cf3e466413fd3c98c5abccfea93058187e78542a71e8f  pcc/py_runtime/py/py_gc_backend.py
f6a3af7a5c6f793268f57cbe3c4c38324863482527d8612363e0aa5760e22a8b  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
a2dc955391aae693801df8b83f05395a11061b909b0cc90d5a8dd4835063ab8d  tests/python/test_gc_threading_substrate.py
f09958c65de759c928ab158559145cdde745bb3a13ec5838780b574bf7341179  tests/python/test_freestanding_gc_generational_scheduler.py
82641925610b6820c09901ba4ee5327e45f6c20ea161a7e04f19490c5b90e310  tests/python/test_freestanding_gc_barrier_dispatcher.py
```

`git diff --check` is clean for these six files.  Isolated `py_compile` passed
for the two runtime Python files and three changed test files.  C
`-fsyntax-only -Wall -Wextra` passed with `PCC_WITH_THREADS=1` and `0`; both
reported the same 10 pre-existing warnings and no new warning.

## Genuine RED evidence

The original C holder polled at its 16th promotion while retaining the graph
lock.  The deterministic true-pthread probe used:

```bash
gtimeout 30s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_polls_only_after_releasing_graph_lock
```

On the old C algorithm the child watchdog expired after 10 seconds and pytest
reported `1 failed in 10.74s`.  The worker could reach the old in-lock poll only
after the owner published a real stop request; the stopped-world owner then
blocked on the same public graph lock.  The test has no sleep or yield.

The first strict mirror still lived in non-freestanding `py_gc_backend.py`.
Calling its exact exported helper under the same handshake produced
`mid-stop promotions=0 aged=0` and `1 failed in 0.78s`: the compiler-injected
function-entry poll parked before generation aging began.  This was not fixed
by weakening the assertion; the final implementation moved the unchanged ABI
owner into the freestanding scheduler.

A lifecycle probe then exposed two independent worklist-loss cases:

- GC4 -> GC1 -> GC4 cleared an existing pending young entry; the first probe
  returned code 10 and pytest reported `1 failed in 0.35s`.
- An explicitly `YOUNG` object allocated while GC1 was selected did not join
  the worklist, so a later GC4 step promoted only one of two objects.  The
  expanded C probe returned code 12 and pytest reported `1 failed in 0.37s`.

The final allocation rule records every tracked final-`YOUNG` object and
preserves the list across trackable backend changes.  Backend 0 still destroys
tracking and clears the head.

Source-owner/closure tests also failed first when the strict export moved: the
owner set had one unexpected export, the object closure had the new promotion
global, and the dispatcher still assumed every raw GC4 provider lived in
`py_gc_backend.py`.  The strengthened gates now pin the new unique owner,
undefined-symbol closure and dispatcher/provider split.

## Final focused gates

The final C/strict behavior and source contract packet was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short \
  tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_polls_only_after_releasing_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_worklist_survives_trackable_backend_switch \
  tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_counts_examined_work_not_only_promotions \
  tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_has_bounded_c_and_strict_graph_tenures
```

Result: `7 passed in 1.77s`.  This covers C and strict runtime behavior for the
three parameterized nodes plus the source/order contract.  The true-pthread
mid-stop observation is exactly 16 promotions and 16 test objects aged; after
resume the worker returns 32 and all 32 are `OLD`.  The work probe observes 40
examined nodes but only 38 promotions, retains a concurrent `PINNED` flag,
checks exact young/old ZPage counts, and excludes an explicit-`OLD` allocation.

The final strict cold packet used live node IDs and a durable log:

```bash
gtimeout 600s zsh -o pipefail -c 'gtimeout 540s env -u LC_ALL -u PCC_RUNTIME_ARCHIVE -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short "tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_polls_only_after_releasing_graph_lock[pcc_python]" "tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_worklist_survives_trackable_backend_switch[pcc_python]" "tests/python/test_gc_threading_substrate.py::test_colored_generation_aging_counts_examined_work_not_only_promotions[pcc_python]" 2>&1 | tee build/gc4-a3b-generation-aging-strict-final.log'
```

Result: `3 passed in 123.85s`; log SHA-256
`a0e192ce0dab674d26a3e3825c13859c4bea40ab30c8cb37e1c73468148c5eef`.

Focused GC4 allocation/aging, ZPage-age, old-to-young store barrier, object-node
worklist, and C/strict GC3 budgeted-worklist neighbors passed `7 in 7.76s`.
The strict owner/LLVM+self closure, dispatcher order/provider closure, and
archive unique-owner files passed `13 in 3.80s` against the explicit current
threaded archive.

One earlier seven-neighbor command was interrupted after six dots when its last
strict fixture unexpectedly triggered a cold archive build; it exited 130 and
is not green evidence.  The failed node was rerun with `-vv`, a durable log and
a final summary (`1 passed in 125.11s`), then the complete seven-node packet was
rerun warm to obtain the final `7 in 7.76s` summary.  No compiler, pytest or
probe process remained after the interruption.

## Strict archive receipt

The final archive key is
`bbfb4be4511e10c79a8f6836-threaded-pcc-py`.  The production provenance verifier
completed successfully against the current `pcc/py_runtime` source root.

```text
12c45b5324065efd4f98fbe4952c19bcc0e359422246bf5fdd02b27aeae333f6  libpy_runtime_pcc_py.a
79e782286af6dcfbe650fbc278101e8d1dde53896481ef7f1f479a10a3529714  libpy_runtime_pcc_py.a.provenance.json
71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda  libpy_runtime_pcc_py.a.capi_syms
1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1  libpy_runtime_pcc_py.a.target
ab021c47e376a0e8372a262badbffa31a58679c0a79130048733eb2b80c744ea  .pcc-threaded-pcc-py-complete
```

The manifest reports schema `pcc.runtime-archive-provenance.v2`, policy
`pcc-production-no-handwritten-c.v1`, 186 members and 444 C-API symbols.  The
completion marker is schema `pcc.runtime-build-cache.v4` and matches the
archive, manifest, C-API inventory and target hashes above.

## Review and boundaries

At the user's request to minimize sub-agent cost, the final six-file identity
was reviewed locally in two explicit passes: production lock/lifecycle/flag
ordering, then test/ABI/owner-closure false-green analysis.  No sub-agent was
running for the final implementation or review, so this evidence deliberately
does not claim an independent dual-review verdict.

This slice does **not** prove that the whole graph lock is a bounded no-park
leaf.  It does not close GC3's holder, the preceding GC4 remembered-root
free/decref/poll path, relocation selection/drain, armed invalid-state tripwire
parity, unrelated atomic wait-freedom, `BIASED`/`DEFERRED`, concurrent/unstable
backend switching, raw-access transactions, physical relocation/retirement,
global GC termination or performance, stage/bootstrap, fixed point, or broad
five-GC parity.  A3c graph-lock/no-park integration remains blocked on the
remaining GC3/GC4 holders.

