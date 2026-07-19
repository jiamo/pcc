# PCC1 dynamic class descriptor fixed-layout closure — 2026-07-17

Task: `AUD-P1-PCC1-DYNAMIC-CLASS-DESCRIPTOR-REGRESSION`.

## Claim

Current-source pcc1 again treats a runtime-replaced class function as a
descriptor on instance lookup.  The fix initializes the two class-attribute
mutation-state fields required by compiled fixed-layout `L1CodeGen`; it does
not special-case the test or weaken descriptor precedence.  The stacked pcc2
hoist failure is also closed by routing `ExceptHandler` fields through the
existing stage-safe dataclass accessor.

## Causality

- Host stage0 emitted `py_obj_getattr(obj, "label")`; old/current pcc1 emitted
  a direct `Child.label` global load and returned an unbound function.
- Both the previous hoist-split pcc1 and a fresh pre-fix pcc1 failed, excluding
  the adjacent floor-division edit.
- After explicit fixed-layout initialization, pcc1 emits runtime lookup and the
  minimized case observes `replacement`, the correct `__self__`, callable
  behavior, and unbound class lookup.
- Fixed-point validation then exposed an independent pcc2
  `ExceptHandler.body` projection failure.  The old pcc2 fails the 10-line
  nested-closure/try probe with `AttributeError: body`; the rebuilt pcc2 prints
  `rootx`.

Detailed investigation:
`docs/investigations/pcc1-dynamic-class-descriptor-fixed-layout-state.md`.

## Gates

- Host initialization/source guard: 3 focused initialization tests passed;
  hoist stage-safe field guard passed.
- `scripts/bootstrap.sh --from-stage 3 --stage 3 --reuse-stage1` produced the
  current pcc3 from the already built current pcc2.  pcc2/pcc3 are byte-equal
  after Mach-O signature/UUID normalization.
- Reusing that one pcc1/pcc2/pcc3 chain, the descriptor/no-host/fixed-point and
  nested-closure/try set passed: `4 passed, 394 deselected in 3.26s`.
- The broader function-descriptor/classmethod neighborhood passed:
  `12 passed, 386 deselected in 9.94s`.
- Final fallback/IR baseline passed:
  `26 passed in 244.84s`.

The oracle fixtures now accept explicit `PCC2_BINARY` and `PCC3_BINARY` just as
they already accepted `PCC1_BINARY`, so multiple focused test selections reuse
one audited chain instead of rebuilding stage2/stage3 for every command.

## Boundary

This proves the named descriptor regression and the stacked stage-safe hoist
projection fix on the default self-host chain.  It does not claim that the
separately discovered compiled bare-re-raise active-exception gap is fixed;
that gap has its own task-board row.

