# Mixed ASM/PCO emit scheduling `[DENIED]`

## Proposal and focused replay

The v58 Stage2 ran 31 ASM emit workers for 122.044s and 196 PCO workers for
105.146s behind a hard phase barrier.  A mixed scheduler kept each item's
measured floor/artifact mode but admitted both families through one width-12,
7-GiB-soft/8-GiB-hard first-fit window.

The initial order starved three large ASM items until completion positions
223/225/226 and took 225.850s.  Closing that complete failure class with a
stable floor-descending order removed the large tail: 227/227 artifacts were
byte-identical, the replay completed in 208.642s and peaked at 6.170GB with no
suspension/failure.  Focused scheduler/orchestration gates passed 22 tests.

## Full Stage2 transfer

v60 binds the sorted scheduler into a frozen source receipt.  Its Stage1 was
abnormally slow (206.98s / 737.38 tree CPU seconds, chiefly link and worker
wall; no proportional coordinator-instruction change), so that single Stage1
is not a performance acceptance baseline.

The hard-capped Stage2 completed correctly, but the focused scheduler gain did
not transfer:

```text
metric                         v58 sequential       v60 mixed
emit phase(s)                  122.044+105.146s     227.076s
Stage2 compile wall            535.345s             550.177s
Stage2 total wall              544.963s             560.184s
Stage2 timed-tree CPU         1999.275s            2005.342s
Stage2 process-tree peak         7.731GB               7.733GB
```

The mixed phase is only 0.05% below the two sequential phases, while total
wall regresses 2.8% and CPU is flat/slightly worse.  Contention increased ASM
worker-wall enough to erase overlap.  Both pcc2 binaries pass `--help` and are
libSystem-only; correctness does not rescue a failed performance claim.

## Disposition

Per-item mode support, mixed ordering, merged phase wiring and tests were
forward-removed.  `scripts/run_pcc_deferred_link.py` again equals the accepted
v58 snapshot; packed stack-map records and the complete-population PCO floor
remain.  Do not retry phase mixing or order variants without a different
resource/producer model.

Together with evidence 051 and 052 this is the third consecutive adjacent
candidate below the required scale.  The 3.3x Stage2/Stage1 gap now forces an
architectural zoom-out: pcc1 uses about 1999 tree CPU seconds versus Stage1's
674, and current evidence attributes the broad gap to generic Python
list/dict/str/allocation plus GC/root protocol across every worker.  The next
slice must replace a complete data-plane owner, not tune another scheduler or
codec helper.

