# Full-GC aggregate memory-budget closure

## Superseded claim and reopened boundary

The two-chain lease evidence below closed the outer-concurrency problem, but a
later cold-source single-GC run invalidated the broader aggregate-memory claim.
With one active slot, the planner expanded `self_backend_jobs` to 12, launched
eight multi-GiB emitters, and reached 25.3 GiB sampled RSS before a deliberate
interrupt. The task is reopened until the new unattended per-chain cap is
proved by a cold fixed-point run. The earlier measurements remain useful
history; they are not current closure evidence.

## 2026-07-22 aggregate frontend-budget repair

The exact complete integration command later invalidated the two-chain claim
again.  GC2 and GC4 reached pcc2-to-pcc3 concurrently; each chain retained a
multi-GiB parent and admitted four frontend workers.  Aggregate descendant RSS
reached 16.61 GiB, so the run was deliberately terminated at the documented
16 GiB safety boundary.  The whole watchdog process group was removed and no
pytest, bootstrap, pcc1, pcc2, or pcc3 child survived.  That interrupted run is
not green evidence.

The scheduler now treats four frontend workers as an aggregate unattended
budget: one active chain receives four, while two active chains receive two
each.  The last remaining chain recovers the full four-worker budget.  The
explicit `PCC_BOOTSTRAP_FULL_FRONTEND_JOBS` per-chain override remains
authoritative.

Current gates:

- focused scheduler and independent-xdist contract: 9 passed, 15 deselected in
  2.75s;
- forced-rebuild current-source five-GC matrix: 5 passed in 1306.29s (21m46s),
  with every GC0..4 stage2/stage3 chain and identity check retained;
- sampled complete process-tree RSS peaked at 13.13 GiB, 2.87 GiB below the
  abort boundary;
- no compiler, bootstrap, or pytest descendant survived the completed matrix.

The exact complete integration command then passed 4551 tests with 12 skips in
669.70 seconds (11m09s), inside its 1800-second watchdog.  The forced matrix is
the cold/rebuild memory proof; the complete suite reused its current-source
success manifests and proved that those artifacts compose with every other
integration test.  No pytest, bootstrap, pcc, or PCC Docker process survived.

## Change

- Reduced the default active full-bootstrap lease from three chains to two.
- Retained `PCC_BOOTSTRAP_FULL_MAX_ACTIVE_GC` as an explicit override.
- Added focused assertions for the one/two/five-slot default and override
  behavior.
- Kept independent xdist scheduling, four frontend workers per active chain,
  six self-emitter jobs per active chain, cache verification, stage2/stage3
  execution, no-libpython checks, and normalized pcc2/pcc3 identity.

## Evidence

Pre-fix observation from the exact integration suite:

- GC1, GC2, and GC4 were active together.
- sampled related RSS rose from 13.5 GiB to 18.2 GiB before falling.

Post-fix gates:

- focused scheduler contract: 7 passed, 13 deselected in 0.15s.
- forced-rebuild current-source five-GC matrix: 5 passed in 968.85s
  (16m08s), with every GC0..4 stage2/stage3 chain executed.
- sampled aggregate related RSS peaked at 12.99 GiB, below the 16 GiB abort
  budget and about 29% below the observed old default peak.
- complete integration suite: 4551 passed, 12 skipped in 874.76s (14m34s).

All commands used explicit watchdogs. Pytest remained at six workers; no test,
marker, backend, compiler stage, or identity check was removed or weakened.
