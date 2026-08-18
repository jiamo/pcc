# Investigation: pcc1 loses an owned object across an IfExpr local assignment

## Status

resolved 2026-08-27

## Problem Description

The first source-frozen pcc1 -> pcc2 transfer after the Indexed Function
Kernel worker acceptance fails in the self verifier while emitting
`pcc.py_frontend.codegen.ownership_lowering`:

```text
self IR verifier [operand-type] in
OwnershipLoweringMixin__return_type_is_owned_object:
'if.end.2845'/binop expects integer operands
```

The pcc1 frontend emitted `or ptr null, i1` for the source expression
`isinstance(ty, (NoneType, BoolType, IntType, FloatType, DynType))`. The host
frontend emits `or i1 0, i1` from the same frozen source.

The invalid operand is a downstream symptom. In the pcc1 implementation IR,
`_emit_if_expr` merges `ct if acc is None else builder.or_(acc, ct)` into a
pointer phi and stores it into `acc` as a borrowed local. `ct` is an owned call
result and is released at the loop step. The first selected value is therefore
freed and zeroed while `acc` still points to it; the next builder call reads its
cleared type/value fields as `ptr null`.

Predecessors:

- `pcc1-tuple-unpack-self-host-str-counter-corruption.md` establishes
  alloca-keyed roots/owned flags and records denied runtime/GC guesses;
- `2026-08-27-dyn-attr-getattr-result-registered-owned.md` pins the adjacent
  owned-RHS -> borrowed-local assignment gap;
- `contextual-per-module-fallback-gate.md` proves the preceding fallback
  failure was a probe-model issue, not this semantic failure.

## Repro

Frozen artifacts:

```text
source SHA: 99c047118fa006f4c52cfaad5abe125a518a9f6dcb33a0016e99cfed2013e4f3
pcc1 SHA:   a9a1a27486ec16eeb26f4d884b7457eb1ad7b9892d8abed2517ab963dadaea20
IR:         build/indexed-packed-record-fixed-point-v2-gc0/private/tmp/
            pcc_py_frontend_workers_ne34lu/ir/module_178.ll
```

Both the prior accepted pcc1 and rebuilt pcc1 reject that exact IR in 2.9s,
while host closed-world compilation of the same ownership Module emits valid
`or i1` and passes the self emitter. This separates the pcc1 frontend defect
from IndexedFunctionKernel/verifier behavior.

The minimized semantic regression is an ordinary class:

```python
current = Canary()
selected = current if flag else current
current = Canary()
print(selected.tag)
```

Rebinding `current` must not free the selected object; both objects must still
finalize exactly once.

## Test [CONFIRMED]

The frozen pcc1 IR failure, host/pcc1 differential and ownership sequence are
confirmed. The red-first ordinary-class gate completed normally but printed
`<null> 2` twice instead of `7 2`; both canaries finalized while the selected
local retained a dangling pointer. After the fix it passes under host and pcc1
compilation on GC0/3/4.

## Proposals

- No.1 Rewrite the tuple-isinstance caller to avoid IfExpr [DENIED]
- No.2 Normalize ownership at object-valued IfExpr joins [pending]

## No.1 Rewrite the tuple-isinstance caller to avoid IfExpr

### Code Change

Replace the two conditional assignments in `isinstance_lowering.py` with an
explicit source-level `if`.

### DENIED

This hides one pcc1 symptom while leaving ordinary user conditional
expressions, the existing dyn-getattr assignment xfail, and every future
compiler accumulator exposed to the same dangling-local bug. It fails the
generic semantics and deletion tests.

## No.2 Normalize ownership at object-valued IfExpr joins

### Code Change

At the IfExpr Module seam, give each selected object branch exactly one owner:
transfer already-owned fresh results and retain borrowed results. Mark the
resulting phi in the emitted-value ownership ledger. Assignment must consult
that ledger, transfer the phi into an owned rooted local, and balance rebind,
normal exit and error exit. CPython pointers, unsafe raw pointers, scalar/value
payloads and ordinary borrowed controls retain their existing projections.

### CONFIRMED

`control_flow_lowering._emit_if_expr` now gives every object branch exactly one
owner before the phi: a fresh/emitter-owned result transfers its owner, while
a borrowed branch is retained in that branch. The phi is recorded in the
emitted-value ownership ledger. Assignment consults that concrete value record
in addition to the AST classifier, roots/flags the target local, and balances
rebind plus normal/error cleanup. Existing dynamic-call/getattr producers now
share the same generic ledger through compatibility helpers.

The ordinary-class runtime and IR gates pass under host GC0/3/4. The formerly
strict-xfailed `x = o.c; x = None` consumer now passes. A rebuilt pcc1
(`8e94030a...`) independently compiles and runs the canary under GC0/3/4.

The exact module178 worker was replayed with identical frozen AST/export
sidecars. Old pcc1 produces four `or ptr` instructions; the new pcc1 produces
zero, emits `or i1 0` at the original function, and successfully self-emits the
complete Module. Five changed compiler Modules are zero-fallback in strict
closed-world ON mode and each passes host self emission.

## Report

No.2 landed; No.1 remains denied. The fix is an ownership-aware CFG join, not
an `isinstance` source workaround. It closes the adjacent dyn-getattr
assignment xfail without changing borrowed field-read behavior. Evidence:
`docs/goal/evidence/2026-08-27-owned-ifexpr-local-transfer.md`.
