# Investigation: backend-4 concurrent flake — entry-loss lead RETRACTED as probe artifact

## Status
active

## Problem Description

Escalated from `GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED`. What looked
like an intermittent premature finalization is now a deterministic
container-entry loss with a writer/reader visibility split:

On `PCC_GC_KIND_COLORED_RELOCATING`, with a second thread running
`pcc_gc_step(256)` in a loop, the mutator's `py_dict_set` returns
successfully, yet `py_dict_contains` immediately returns 0 — while
`py_dict_len` returns 1. The miss persists across root reloads, yields,
and `pcc_gc_collect`: the writer-committed entry is invisible to reader
probes from then on. Measured: 298 losses in 300 rounds (single run),
every one permanent.

This reframes the original symptom: a lost entry orphans its value, and
the collector then CORRECTLY reclaims it — which surfaced earlier as
"more finalizations than displaced values / survivor freed". The GC was
behaving correctly on a container whose committed entry had vanished;
the defect is entry loss under concurrent steps, not premature free.

Also newly established: the no-worker control
(`tests/python/test_backend4_no_worker_control.py`, 10/10 green) proves
accounting is exact WITHOUT the tracer, so the concurrent step is
necessary.

## Repro

Diagnostic source preserved at `/tmp/b4attr/attr4.c` (pattern below);
compiled against the cached threaded C archive
(`runtime-builds/77973be0786ba65b95355550-c-threaded`):

```text
loop 300 rounds on backend 4, worker thread stepping pcc_gc_step(256):
    py_dict_set(d, int_key(9), fresh_instance)
    post = py_dict_contains(d, k)        # via pcc_gc_load_ptr reload
observed: losses at rounds 2..299 (298 total); each prints
    retry=0 len=1 after_gc=0
expected: post==1 always (CPython semantics)
```

Single-threaded sanity (`attr3.c` pattern): 200 sets + contains checks,
no worker → clean, ×5 runs. Worker is necessary.

## Findings

1. Writer/reader split-brain: `py_dict_len` sees the entry; keyed probe
   (`contains`) does not. Both operate on the same reloaded `dict_root`.
   So either the entry landed in a stale table copy while len/probe read
   different generations, or the entry's stored hash/key fields are
   corrupted by the concurrent step (probe compares `entry->hash == hash`
   first, py_dict.c:414).
2. Loss is permanent (retry after reload, yield, and collect all miss).
3. No finalization accompanies the loss window (`fin_delta=0` in earlier
   bracketing runs): values orphaned by the loss are reclaimed later,
   explaining the historic count anomaly.
4. First loss can occur at the very first insert into an empty dict.

## Test [CONFIRMED]

`/tmp/b4attr/attr4.c` run personally 2026-08-26: rc=20,
`rounds=300 losses=298 transient_after_retry_or_gc=0 final_len=1`.
Failure observed, not inferred.

## Proposals

- No.1 Audit the backend-4 step path (relocation selector/page drain) for
  dict-object relocation that does not carry the registered-root handle
  forward when the owner was freshly written — i.e. writer commits into
  pre-relocation storage, readers resolve post-relocation     [pending]
- No.2 Compare entry hash/key fields of the surviving generation against
  the probe's recomputed hash on a failing run (generation-tag the value,
  dump the entry)     [pending]

## Nonclaims

- Which mirror generation is at fault (C archive used here; strict
  untested for this shape).
- Whether non-int keys behave identically.

## Update (2026-08-26, later): mechanism observed — forwarding vs copy race

Dump probe (`attr5.c`) captures both generations on the first failing
round:

```text
LOSS r0 d_pre=0x1024da988 d_post=0x94ac05488 moved=1
  writer-view: size=0 used=0 cap=0        <- zombie: zeroed PyDictObject
  reader-view: size=0 used=0 cap=8        <- the real dict, never written
  reader live-index-slots=0
```

The mutator's FIRST `pcc_gc_load_ptr` after root registration resolved to
an object with a valid header but zeroed payload (`cap=0`, `entries=NULL`)
- the destination of a relocation whose payload copy had not happened (or
had not completed) when the forwarding pointer was installed. The
mutator's `set` then "succeeded" into the zombie (no crash only because
capacity-0 paths happen to no-op), while readers resolving through the
root handle saw the real dict that never received the write.

Diagnosis: **forwarding-installed-before-payload-visible race between the
concurrent relocation step and a mutator touching a freshly registered
root.** Not collection misbehavior, not finalization accounting.

Proposals No.1/No.2 remain the fix path: audit install/copy ordering and
the missing barrier that lets a mutator resolve a forwarding target before
its payload is visible (likely needs the copy to complete - or the
forwarding install deferred - under the graph lock the step already holds).

## Fix-site anchors (for the implementing slice)

All in `pcc/py_runtime/src/py_gc_backend.c`:

- `pcc_gc_forwarding_install_plan_prepare` / `..._finish` (line ~8796 /
  ~8783): plan split already exists — the finish leg is where visibility
  ordering must be enforced.
- `pcc_gc_install_forwarding_preallocated_unlocked` (~8877) and
  `pcc_gc_install_forwarding_unlocked` (~8998): install sites.
- Copy path feeding them: `pcc_gc_relocate_copy_payload_prepared_locked`
  (~7200) → `pcc_gc_relocate_copy_slots` (~6869) → retargets.
- Tag gate: `pcc_gc_colored_relocate_copy_supported_tag` (~6119) — dict
  tags reach the copy path, so the zombie-dict observation is consistent
  with dicts being relocation candidates.

Hypothesis to test first: the finish leg publishes forwarding for a batch
while some destinations' payload copies are still in flight (or lack a
release/acquire pair visible to the MUTATOR's load_ptr, which may run
outside the step's graph lock); a mutator resolving through such a
forwarding entry observes the zeroed destination — exactly the captured
`cap=0 size=0 entries=NULL` generation. Strict mirror
(`pcc/py_runtime/py/freestanding_gc_*`) needs the identical ordering.

## RETRACTED (2026-08-26, latest): entry loss was a stale-pointer probe artifact

Re-run with reload-inside-call semantics (`attr6.c`: every
`py_dict_set`/`py_dict_contains` argument re-resolves through
`pcc_gc_load_ptr`, exactly as the passing substrate probes do):
**0 losses in 5 x 300 rounds** while the worker steps continuously and
displaced values are reclaimed asynchronously (fin counters 1..170 across
runs).

The attr4/attr5 "losses" and the zombie-dict dump were artifacts of the
diagnostic holding a top-of-round container pointer across relocation
boundaries — the exact convincing-false-finding pattern documented in the
update/delete probe docstring. Consequences:

- The "forwarding-installed-before-payload-visible race" diagnosis is
  RETRACTED; the fix-site anchors remain as reference material but no
  defect is attributed to them.
- The historic overlap-probe flake (seen > displaced under proper reload
  discipline) returns to UNEXPLAINED. Last valid datum: the survivor's
  `__del__` ran on the mutator thread (thread-attribution capture,
  earlier update).
- Lesson recorded: backend-4 diagnostics MUST reload through
  `pcc_gc_load_ptr` at every use; any pointer held across an await point
  measures relocation itself, not the container.

## Update (2026-08-26, final): decisive capture — drain collect frees a REACHABLE value

Instrumented the ACTUAL overlap probe (`last_ctx` classification +
post-anomaly reachability recheck) and looped its COLORED_RELOCATING arm;
captured on run 3 of ~5s each — reproduction is now effectively on-demand:

```text
seen=100, displaced=99            # exactly ONE extra: the survivor
last_fin_thread=1 main_thread=1   # mutator thread
last_ctx=2                        # inside the end-of-run pcc_gc_collect drain
post-anomaly get=RESOLVES         # dict still resolves the survivor!
```

## Verdict

**Backend-4 drain-phase `pcc_gc_collect` finalizes a value that is still
reachable from a registered scheduler root.** The dict resolves the
survivor AFTER the anomalous drain, so reachability was never lost — this
is a collector MARK failure under COLORED_RELOCATING when concurrent
steps ran during the mutation window, not entry loss and not probe
accounting. Exactly one extra finalization per occurrence.

Fix direction: audit the backend-4 collect's mark/colouring for values
whose pages were touched by concurrent steps during the mutation window
(stale mark state trusted by the drain's collect), in BOTH mirrors,
starting from the anchors recorded above. Gate: overlap-probe backend-4
arm 20 consecutive greens plus five-backend production contract.

## Update (2026-08-26, CONFIRMED capture): SWEEP_CANDIDATE set on the reachable survivor

Instrumented the ACTUAL overlap probe (last_ctx classification +
post-anomaly reachability recheck) and, separately, an env-gated dump at
`pcc_gc_sweep_unreachable` PASS 0 (temporarily; removed after capture).
Captured on a COLORED_RELOCATING arm run:

```text
pre-drain:  survivor = 0x8f2c0bc00          (get resolves, len==1)
PASS-0:     del obj=0x8f2c0d000 tag=105 flags=0x30d09
            (tag=105 = user-class instance; flags include
             PY_FLAG_GC_SWEEP_CANDIDATE)
post-drain: post-anomaly get=RESOLVES       (reachability intact)
seen=100 vs displaced=99                   (exactly one extra: survivor)
last_fin_thread=1 main_thread=1 last_ctx=2 (mutator drain phase)
```

The address difference (bc00 → d000) shows the survivor was RELOCATED
between the pre-drain print and PASS-0 — and its new location carried
`PY_FLAG_GC_SWEEP_CANDIDATE` despite being reachable through the
registered root chain (dict still resolves it). PASS-0 therefore ran
`__del__` on a live object; the object itself was not freed.

## Refined defect statement

On COLORED_RELOCATING with a concurrent stepping worker, a freshly
relocated copy of a reachable value can carry `GC_SWEEP_CANDIDATE` —
either copied from stale source metadata or left set by the relocation
path. The next explicit collect's PASS 0 then finalizes the live object.
Exactly-once holds only because FINALIZED suppresses re-dispatch; the
user-visible effect is `__del__` firing while the object is alive.

Reproduction cost is now low (~3 arm runs, ~5s) using the instrumented
overlap probe with the ctx/recheck diagnostics that remain in place
(`last_ctx`, `last_fin_thread`, post-anomaly recheck are promoted as
permanent probe diagnostics; the env-gated runtime dump was removed).

## Update (2026-08-26, fix slice): relocation-copy inheritance fixed; a second cut-window path remains

Implemented the prescribed fix in both mirrors: the header-memcpy
relocation copies now clear `PY_FLAG_GC_SWEEP_CANDIDATE` on the
destination AND on the sweep-visible source shell, because relocation
proves liveness and a finished-cycle verdict predating the copy is stale.
Sites: `pcc_gc_relocate_copy_preallocated_unlocked` and
`pcc_gc_generational_oldify_copy` in `py_gc_backend.c`, plus
`freestanding_gc_relocation_copy.py` (mask 342016 -> 343040) and
`freestanding_gc_generational_oldification.py`.

Focused deterministic regression:
`tests/python/test_gc_relocation_sweep_verdict.py` (backend-4 arms,
c + pcc_python). The probe pokes the verdict directly, relocates via
`pcc_gc_select_relocation_set` + `pcc_gc_relocate_copy`, then drives the
explicit-collect boundary. RED to GREEN measured: with the destination
clear temporarily disabled the probe fails at code 20
(`destination-inherits-verdict`, copy flags contain 0x400); with the fix,
both mirror arms pass and exactly-once teardown holds. The backend-3
oldify hardening rides the five-backend production contract: a bespoke
deterministic repro is not expressible through refill-driven promotion
because refill's minor collections consume a poked verdict on the young
source before oldify copies it -- that consumption is correct verdict
semantics, not the inheritance defect.

The 20-consecutive-greens gate for the overlap probe COLORED_RELOCATING
arm failed at iteration 4 with the same signature as before the fix:
`seen=100 displaced=99`, `last_ctx=2` (drain), mutator thread,
post-anomaly get RESOLVES. Crucially this occurrence needed NO
relocation: it exposes a second path to the same symptom. A cycle whose
white->candidate cut completes while the survivor exists only as an
unregistered mutator-local (between creation and `py_dict_set`) cuts the
candidate onto the object legitimately; once stored, no further cycle may
complete before the drain, and the pending-candidate sweep consumes the
stale verdict without re-marking (`pcc_gc_collect_tracing` sweeps owed
candidates verbatim). The relocation-inheritance subcase is closed by
this slice; the cut-window subcase is open. Narrowed next steps stay as
recorded on the row: per-value generation tags or finalizer backtrace
capture to prove which object identity the extra dispatch targets, plus
a decision on whether raw-probe locals-not-rooted semantics make the
cut-window occurrence a probe artifact or a real C-API contract gap
(owned references are collectable at any safepoint unless registered;
compiled pcc code roots intermediates via stack maps, so real programs
may not expose this window at all).
