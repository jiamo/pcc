# Backend 4: relocation copies stop inheriting finished-cycle sweep verdicts — 2026-08-26

Slice for `GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED` (feeds the open
boundary of `GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`). Investigation:
`docs/investigations/gc-backend4-concurrent-entry-loss.md`.

## Change

The header-memcpy relocation copies propagated
`PY_FLAG_GC_SWEEP_CANDIDATE` (0x400) from source header metadata onto the
published copy. Because `pcc_gc_collect_tracing` sweeps pending candidates
verbatim without re-marking (`pcc_gc_sweep_owed()` gates straight to
`pcc_gc_sweep_unreachable`), such a copy was PASS-0-finalized while alive —
the CONFIRMED capture in the investigation. Four sites, both mirrors:

- `py_gc_backend.c` `pcc_gc_relocate_copy_preallocated_unlocked`: 0x400
  added to the destination clear mask; the immortal shell's own stale
  verdict is also cleared (it stays sweep-visible until remap retirement).
- `freestanding_gc_relocation_copy.py`: clear mask 342016 -> 343040;
  source shell cleared to &~1024.
- `py_gc_backend.c` `pcc_gc_generational_oldify_copy`: 0x400 added to the
  promotion clear mask.
- `freestanding_gc_generational_oldification.py`: 1024 added to the
  negated mask.

Rationale invariant: relocation/promotion proves the payload is live
memory being kept; a "was unreachable" verdict recorded before the copy
is obsolete for every identity derived from it.

## Focused regression (RED to GREEN, both mirrors)

`tests/python/test_gc_relocation_sweep_verdict.py`, backend-4 arms
(c + pcc_python). Deterministic, no threads: poke the verdict exactly as
a finished cycle leaves it, relocate via `pcc_gc_select_relocation_set(1)`
+ `pcc_gc_relocate_copy`, drive `pcc_gc_collect`.

```text
fix disabled (destination clear only):  c arm exit 20
  destination-inherits-verdict, copy_flags=0x12488 (0x400 set)   RED
fix restored:                           2 passed in 1.58s      GREEN
```

Scope note: the backend-3 oldify hardening is exercised by the production
contract, not a bespoke repro — refill-driven promotion cannot observe the
inheritance deterministically because refill's minor collections consume a
poked verdict on the young source before oldify copies it (that consumption
is correct semantics, measured while attempting the repro: rooted+poked
source reached `DEALLOCATING|MINOR_ARENA|YOUNG|WHITE` = swept as garbage,
exactly what a pending-verdict consumer must do).

## 20-greens gate: partial — second path found

Overlap-probe COLORED_RELOCATING arm (both kinds), consecutive loop:
iterations 1-3 green, iteration 4 reproduced the historical signature
(`seen=100 displaced=99`, `last_ctx=2` drain, mutator thread,
post-anomaly get RESOLVES) — WITHOUT any relocation in flight. This
splits the defect family:

1. Inheritance through relocation copies: CLOSED by this slice
   (deterministic red->green).
2. Cut-window: a cycle whose white->candidate cut completes while the
   survivor is an unregistered mutator-local cuts a legitimate verdict;
   if no further cycle completes before the drain, the no-re-mark sweep
   consumes it. OPEN on the row; narrowed next steps unchanged
   (per-value generation tags or finalizer backtrace capture), plus a
   new question: raw-probe locals are not stack-map roots, so this
   window may be a probe artifact rather than a real-program gap —
   compiled pcc code roots intermediates. Needs the generation-tag datum
   before any obligation claim.

## Production contract (backends 3+4 subset)

`GC_BACKENDS="3 4" bash scripts/run_gc_production_contract.sh`: backend 4
summary `2 failed, 164 passed, 10 errors`; backend 3 shares the
backend-independent clusters. Full attribution, none caused by this
slice (mechanism argument: the fix touches runtime flag masks only; the
compile-stage failures happen inside host-side frontend codegen before
any runtime archive participates):

| Cluster | Tests | Attribution |
|---|---|---|
| native-extension export anchors | test_extension_module_state_roots[0..4], ERROR at compile | bisect row cluster (a): self-link track, GC-independent |
| valueclass direct-payload method ABI | test_direct_valueclass_pointer_payload_survives_gc[0..4], L1CodegenError at compile | NEW observation, backend-independent frontend bug; needs an owner row on the frontend/valueclass track |
| valueclass pointer-payload relocation assert | test_..._updates_after_optional_relocation[4], "AssertionError: relocated" | bisect row cluster (b), deterministic pre-existing |
| vthread io-waitset auto-2 | test_production_io_waitset_modes_preserve_roots[auto-2], 30s timeout | bisect row cluster (c); still hangs after this slice, so the "downstream of concurrent-step family" hypothesis remains UNCONFIRMED |

## Gates run this slice

```text
focused regression (c + pcc_python)                     2 passed
production contract subset backends 3+4                 164 passed,
                                                        12 attributed pre-existing
investigations index regenerated                        473 entries
```

## Nonclaims

- The overlap-probe 20-green gate does NOT pass; the row stays open.
- No claim that the backend-4 production contract is green; the four
  clusters above are owned by their respective rows.
- Backend-3 oldify clearing is verified indirectly (contract + mirrors
  staying differential-equal), not by a dedicated repro.

## Cold fixed-point chain: blocked by a PRE-EXISTING stage-2 failure

The cold chain owed by `GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT` was run:
stage 1 passed (`rc=0`, 282 s) with this slice's runtime in the archive,
then stage 2 failed (`pcc1` lifting pcc's own source, systemic
`LiftError: AttributeError: obj` across unrelated modules). Attribution
experiment: with the entire sweep-verdict slice reverted to
HEAD-equivalent and stage 1 rebuilt, stage 2 fails IDENTICALLY
(`elapsed_ms=142639 rc=1`). The break is pre-existing on the current
dirty working tree and unowned by this slice. Smoke check: `pcc1`
compiles and runs a minimal function program correctly (42, exit 0).
Filed as investigation
`docs/investigations/self-host-stage2-lift-attributeerror-obj.md` with
bisect proposals; the chain gate stays blocked on that row, not on the
backend-4 reds previously suspected.
