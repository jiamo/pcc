# Full-GC aggregate memory-budget closure

## Claim

The unattended five-GC bootstrap resource lease no longer admits three
multi-gigabyte stage2/stage3 chains at once. It defaults to two active chains,
keeps the explicit operator override, preserves the GC0 cache-warmer order,
and retains all five real no-libpython fixed-point checks. This is a test-suite
resource bound, not a reduction in GC or bootstrap coverage.

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
