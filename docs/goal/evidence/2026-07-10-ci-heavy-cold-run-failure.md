# CI heavy cold-run failure evidence

Date: 2026-07-10

Milestone: `M0`

Tasks: `M0-CI-WORKFLOW-CONTRACT`, `M0-GITHUB-STATUS-CHECKS`

Source: clean published commit
`27eabf2ab716f11030ec2d206de8f28c965bab76`

## External result

The push-triggered light workflow is green:

```text
run 29094565455
fallback-ratchet: 21 passed in 183.86s
control-plane-ratchets: 37 passed in 0.34s
artifact: 8228262758
final enforcement: PASS
```

The manually dispatched heavy workflow finished unsuccessfully:

```text
run: 29094810339
job: 86368660696
artifact: 8229140020
artifact name: head-truth-heavy-27eabf2ab716f11030ec2d206de8f28c965bab76
manifest source.worktree_dirty: false
manifest complete: false
manifest claimable_commit: false
```

The uploaded manifest is complete enough to preserve all selected gate
results even though the final enforcement step correctly rejected it:

| Gate | Result | Evidence |
|---|---|---|
| `fallback-ratchet` | `TIMEOUT` | 300.061s, return code 124, no pytest summary |
| `control-plane-ratchets` | `PASS` | 37 passed in 0.83s |
| `gc-production-contract` | `FAIL` | 15 passed, 125 errors in 49.32s |
| `llvm-bootstrap` | `FAIL` | 900.212s, return code 124; `pcc1` exists, `pcc2`/`pcc3` missing |
| `self-five-gc-bootstrap` | `FAIL` | 5 failed in 811.37s |

## Failure boundaries

1. The first failing boundary is the strict fallback ratchet timing out at its
   300-second gate limit.  The same gate passed in the light workflow, so this
   is a cold-run/heavy sequencing problem, not evidence that the fallback
   baseline itself regressed.
2. The GC production contract repeatedly reports that the lazy
   `libpy_runtime_pcc_py.a` make command exited 2, then fails to link the
   missing runtime symbols.  The compiler warning suppresses the captured make
   output, so the artifact does not expose the underlying make error.  This is
   a distinct diagnostic and setup boundary, not 125 independent GC semantic
   failures.
3. The LLVM three-stage bootstrap reaches `pcc1` but exceeds 900 seconds before
   producing `pcc2` or `pcc3`.  It remains a separate stage boundary until a
   clean prebuilt runtime proves or rules out setup contamination.
4. The five self-GC files were visibly assigned to five xdist workers despite
   the repository conftest intending a single `xdist_group`.
   `bootstrap_gc_parallel_slots=5`, with each test also selecting three frontend
   and three self-backend jobs.  GC0/1/2 timed out in stage2 at 600 seconds;
   GC3/4 frontend workers exited with signal 11.  These results prove runner
   oversubscription, but do not by themselves prove a GC semantic or compiler
   correctness regression.

## Claim boundary

This evidence proves that the workflow now survives all selected gates,
uploads its failure manifest, and rejects an unclaimable commit.  It does not
prove the heavy truth matrix or any bootstrap fixed point.  M0 therefore stays
active: `M0-CI-WORKFLOW-CONTRACT` is `IN_PROGRESS`, the downstream GitHub card
cannot become `DONE_STRONG`, and `M0-EXIT` cannot activate M1.

Git publication, commit creation, and push state are explicitly not blockers
for the current work.  The open boundary is implementation and validation of
the heavy cold-run contract.

## Local repair progress

- A required `runtime-archive-preflight` now runs first in the all-suite
  registry, captures the complete make output, and is included in manifest
  claimability.
- Its exact make command passed from an isolated Python 3.13 locked candidate.
- The heavy timeout envelope is registry-safe at 65 minutes for the truth step
  and 85 minutes for the job.
- A real nested xdist regression first reproduced the broken split across
  `gw0` and `gw1`; `pytest.hookimpl(tryfirst=True)` now makes all five matching
  bootstrap nodes execute on one loadgroup worker.
- Manifest/workflow contracts pass (`14 passed`) and the focused xdist
  scheduling regression passes (`1 passed`).

The complete post-repair local all-suite now passes; see successor evidence
`docs/goal/evidence/2026-07-10-ci-heavy-cold-run-local-repair.md`.  The remaining
external boundary is a successful clean published-source heavy run whose
uploaded manifest has `claimable_commit=true`.

See `docs/investigations/ci-head-truth-heavy-cold-run-cascade.md`.
