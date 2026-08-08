# Task-board convergence and bounded expansion

Date: 2026-08-03

Task: `GOV-P0-TASK-BOARD-CONVERGENCE`

## Input finding

The human review identified three independent expansion risks. Direct board
inspection confirmed each number:

```text
DONE_WEAK: 14
P0 libc DONE_WEAK: 6
LINK-P1-MACHO-LINK-SWITCH open_boundary: 20,358 characters
unfinished performance rows: 13
```

The compiler-reference audit was also explicitly designed to create more task
rows but had no expansion budget.

## Structural correction

`LINK-P1-MACHO-LINK-SWITCH` now represents only the already-proven opt-in
subprocess route and is `DONE_STRONG`. Its former implementation diary is no
longer executable scheduling state. The remaining default-policy change is a
new finite `LINK-P1-MACHO-DEFAULT-LINK-ACCEPTANCE` row whose 441-character
boundary names the default flip, three runtime programs, unchanged closure,
zero silent fallback, one pcc-linked stage chain, and pcc2/pcc3 identity.

The related static-link row was also reduced from 2,777 characters to 273 and
now owns only its relocatable/archive current-source recheck.

Every execution-ready `performance/*` row now declares four machine-required
fields plus a traceable baseline artifact:

```text
scope_limit
baseline_metric
success_threshold
failure_disposition
baseline_evidence
```

The scopes exclude follow-on work rather than hiding it in prose. A review of
the first attempted backfill found that ten rows had prose describing metrics
but no recorded current-source measurements; percentage thresholds on those
rows were therefore unsupported. They are now `TODO_NEEDS_DESIGN` and contain
no invented baseline, threshold, or failure result. Their next bounded action
is to capture and link a mode-labeled baseline. Three rows remain
`TODO_READY`: register allocation has the human-reported 434.3-second stage2
anchor documented separately, frame pooling has recorded GC3/GC4 profiles, and
the compiler-reference audit uses the board's finite task counts.

The LDP/STP and MADD design rows retain explicit atomic-ordering constraints.
In particular, future memory scheduling must account for x86 TSO fences that
currently have no emitted assembly token, and address folding must not enter or
destabilize an AArch64 `ldaxr`/`stlxr` exclusive-monitor interval.

`PERF-P1-COMPILER-DESIGN-REFERENCE-AUDIT` is limited to six named reference
families, at most 30 classified techniques, and at most six new task rows. It
sets `produces_tasks: true` and `task_expansion_limit: 6`; everything outside
the top six remains evidence and the audit cannot recursively create another
audit.

## Recurrence guard

`scripts/goal_state.py validate` now rejects:

- any `open_boundary` longer than 2,000 characters;
- any unfinished `performance/*` row missing a finite `scope_limit`;
- any execution-ready `performance/*` row missing `baseline_metric`,
  `success_threshold`, `failure_disposition`, or an existing
  `baseline_evidence` path;
- any task-producing audit without a positive expansion limit no greater than
  six.

`DISCOVERED` and `TODO_NEEDS_DESIGN` performance rows still require a bounded
scope, but are deliberately exempt from metric/threshold fields: requiring
those fields before measurement would turn the validator into an incentive to
invent data.

The tests were added red first. They initially failed on missing constants and
missing budget validation, then passed after implementation:

```text
8 passed in 0.03s
```

## Convergence result

The first validation round reported exactly two oversized boundaries and
`13 * 4 = 52` missing performance fields. A mechanical backfill made validation
green but did not make the numbers real; the follow-up correction changed the
status model instead. The final review reported:

```text
oversized []
performance TODO_READY with baseline evidence 3
performance TODO_NEEDS_DESIGN awaiting baseline 10
LINK-P1-MACHO-LINK-SWITCH DONE_STRONG boundary=0
LINK-P1-MACHO-DEFAULT-LINK-ACCEPTANCE TODO_READY boundary=441
PERF-P1-COMPILER-DESIGN-REFERENCE-AUDIT task_expansion_limit=6
OK: 205 tasks validated
```

The board grew by two deliberate rows: this governance closure and the linker
default acceptance split. Growth is therefore explicit and bounded rather
than stored inside an unbounded field.
