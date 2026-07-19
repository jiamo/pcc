# Investigation: emission-site err-check audit (missing `_emit_post_call_err_check` after raise-capable runtime calls)

## Status
active — audit list produced; sites must be REVIEWED case-by-case (the
window heuristic has false positives), then fixed in focused slices.

## Problem Description

Three independent bugs in one evening shared the same class: a runtime
function sets a pending exception via `py_raise` (return-code model, no
unwinding), but the FRONTEND EMISSION SITE never emits
`_emit_post_call_err_check`, so the exception skips enclosing
try/except blocks and detonates at a later check point (or never).
Instances fixed so far: native `weakref.ref`/`weakref.proxy`
(`native_weakref.py`), weak-dict subscript stores
(`subscript_lowering.py`), and (earlier today) the J2' cpy unpack
arity check needed its own explicit error route. The runtime-holes
investigation documented this class long ago; this audit makes the
remaining surface visible.

## Audit method (re-runnable)

`/tmp/audit_errcheck.py` (inline in the session log; rewrite on
demand): (1) collect C runtime functions whose body contains
`py_raise(` (coarse function-boundary parse) -> 78 functions;
(2) for every `self.runtime["<fn>"]` emission in
`pcc/py_frontend/codegen/`, flag it when no
`_emit_post_call_err_check`/`err_check` appears within the next 8
lines -> **58 suspect sites** (2026-06-11 audit).

## Top suspects (by site count, UNREVIEWED — heuristic output)

```text
py_raise (15)            # sites that call py_raise directly — these
                         # usually branch to the err path themselves;
                         # most are likely FALSE positives, verify shape
py_obj_getattr (14)      # builtin_type_attr/attr_load/assignment paths
py_obj_next (5)          # comprehension/list_builtin/for_loop/numeric
py_obj_sub/add/mul (4)   # binary_op/list_method paths
py_gen_throw/send/close/next (5)
py_str_mod (1), py_user_matmul_dispatch (1)
py_re_findall_flags / py_re_engine_split / py_re_engine_sub (3)
py_continuation_get_slot (1), py_call_merge_kwargs (1),
py_zip_star (1), py_subprocess_check_output (1), py_pickle_dumps (1)
```

## Review rules (before fixing any site)

1. A missing check is only a BUG if the exception can actually be set
   on a reachable path AND an enclosing try/except could observe it —
   confirm with a minimal repro that prints the wrong-catch behavior
   (the weak-dict probe shape: expect `typeerror`, observe `ok` +
   late traceback).
2. Some sites NULL-check the result and route errors equivalently —
   that is a valid alternative; do not churn them.
3. Fix in FOCUSED slices (one family per slice, e.g. "py_obj_next
   consumers"), each with a red probe, regression, fallback baselines,
   and the five-GC matrix — not one mega-patch.

## Test [pending per-site]

Each fixed site gets its own observed-red probe; this file tracks the
list, not a single repro.

## Review 1: py_obj_next family (5 sites) — ALL FALSE POSITIVES

Static: all four consumer files (for_loop / comprehension /
list_builtin / numeric_builtin) carry the maybe_end/propagate routing
(`py_exc_matches` against StopIteration after a NULL item — the valid
NULL-check-equivalent shape; the audit's 8-line window simply doesn't
reach the block). Runtime red probe (`__next__` raising ValueError
after 2 items through `list()` / `sum()` / a comprehension) printed
`valueerror / valueerror-sum / valueerror-comp` == CPython, rc=0 —
propagation works end to end. Behavior pinned by
`tests/python/test_iterator_exception_propagation.py` (1 passed; no
code change, so no matrix needed). HEURISTIC NOTE for future reviews:
the block-name pair `maybe_end`/`propagate` plus a nearby
`py_exc_matches` is the signature of the equivalent routing — treat
such sites as reviewed-OK.

## Review 2: binary-op family — TRUE POSITIVE x2 (fixed, matrix pending at write time)

Red probe (`__add__`/`__sub__`/`__mul__` raising ValueError through the
dyn path): CPython prints `add-err/sub-err/mul-err`; pcc printed
`add-ok/sub-ok/mul-ok` plus a CHAINED TypeError traceback (rc=1) —
two compounded defects:

1. **Runtime dispatch hole**: `py_obj_add/sub/mul` (both tiers) never
   dispatched user dunders — instances fell straight to "unsupported
   operand" TypeError (the long-recorded binary-dunder family from the
   call_method1 note). FIX: new C-only
   `py_user_binop_dispatch(a, b, name, rname, msg)` in py_protocol.c
   (matmul shape: __op__ -> NotImplemented -> reflected __rop__ ->
   TypeError; NULL from the user dunder propagates), wired into the
   instance/user fallthrough of add/sub/mul in BOTH tiers (port calls
   it via extern — C-only helper principle).
2. **Missing emission err-check** (the audit's hit): the obj
   add/sub/mul sites in `binary_op_lowering._emit_binop_value` now
   emit `_emit_post_call_err_check(None)` (the function has no expr
   param — span None is the existing Optional contract).

Observed post-fix: `add-err/sub-err/mul-err` rc=0 on BOTH tiers ==
CPython; reflected case `1 + Radd()` -> 42 == CPython; parametrized
regression `tests/python/test_binary_dunder_dispatch_runtime.py`
(port+cc) 2 passed; contract + V suites + iterator pin 218; fallback
18. Implementation notes: a sed-style double-replace nearly misfired
(extern anchor mismatch left calls without a declaration — caught by
counting occurrences before running); py_obj_ops_dispatch is a
PY_MODULES file so BOTH the .a archives AND the OBJDIR .o caches were
invalidated before re-probing (the stale-port-object lesson).
Remaining family members (py_str_mod, matmul site, div/mod and
friends) follow the same recipe — recorded, not yet probed.

## Review 3: `%` (py_obj_mod) — TRUE POSITIVE fixed; floordiv/truediv-reflection recorded

Red probe round 2 (div/mod/floordiv dunders + reflections): `/` already
defers to `__truediv__` (div-err OK), but `%` had NO user dispatch in
either tier — fixed with the same `py_user_binop_dispatch` recipe
(`__mod__`/`__rmod__`) in `py_obj_ops_dispatch.c` and the split-out
port member `py_obj_ops_mod.py` (its own archive member — the port
edit needed its OWN .o invalidation). Observed both tiers:
`mod-err / 8` (raise caught + reflected `1 % Rmod()`) == CPython;
regression extended (`test_binary_dunder_dispatch_runtime.py`, now
add/sub/mul/radd/mod/rmod, 2 passed); contract + sorted 135; fallback
18; matrix pending at write time.

REMAINING (recorded for the next slice, same recipe):
- `a // 2` on an instance printed `floordiv-ok` (no dispatch, silently
  wrong — needs `__floordiv__`/`__rfloordiv__` wiring and possibly an
  emission-site route for `//` over Dyn).
- `1 / R()` raised `AttributeError: __truediv__` — py_obj_truediv's
  dunder defer only tries the LHS; it needs the NotImplemented +
  reflected `__rtruediv__` shape (convert to py_user_binop_dispatch).

## Review 3 completion: floordiv + truediv reflection (bootstrap-verified)

- `py_obj_floordiv` added as a C-ONLY helper in py_protocol.c (the
  proven new-symbol-safe recipe: OBJ_PY_CC_HELPERS members land in both
  archives — unlike bare SRCS symbols that broke stage2 twice in May).
  int/bool pairs keep py_int_floordiv floor semantics; any-float
  numeric pairs floor the double quotient (ZeroDivisionError on 0);
  instances dispatch __floordiv__/__rfloordiv__. The frontend Dyn `//`
  branch now exists (it used to coerce instances through the i64 fast
  path — the silently-wrong `floordiv-ok`), emitting py_obj_floordiv +
  err-check.
- `py_obj_truediv`'s non-numeric defer (both tiers) replaced: the old
  `py_obj_call_method1(a, "__truediv__", b)` only tried the LHS (and
  used the call_method1 shape from the known bound-method-receiver bug
  family); now `py_user_binop_dispatch` with the full NotImplemented +
  reflected `__rtruediv__` protocol.

Observed both tiers: `div-err / mod-err / floordiv-err / 7 / 8` rc=0
== CPython (raising dunders caught; `1 / Rdiv()` -> 7 reflected;
`1 % Rmod()` -> 8). Regression extended to the full
add/sub/mul/div/floordiv/mod + radd/rmod/rtruediv set (2 passed);
contract + sorted + V unboxed 178; fallback 18; five-GC matrix ->
5 passed in 543.90s. The binary-op family of the audit is now CLOSED
(remaining audit families: py_obj_getattr 14, generator send/throw/
close 5, re-engine 3, singles).

## Review 4: generator send/throw/close — TRUE POSITIVE + runtime hole (matrix pending at write time)

Red probe: `g.throw(ValueError)` inside try/except printed `throw-ok`
then a late uncaught ValueError (CPython: `throw-err`). Fixes:

1. The three `gen.send/throw/close` emission sites in
   `method_call_expression_lowering.py` now emit
   `_emit_post_call_err_check(expr.span)`.
2. The new check exposed a RUNTIME hole: `py_gen_close` left its
   injected GeneratorExit PENDING when the generator body did not
   catch it — in CPython that propagation IS the normal close path and
   close() swallows it. Both tiers now recognize the pending exception
   by IDENTITY with the injected object (`cur == exc` / `ptr_eq`) and
   clear it; StopIteration keeps its existing clear branch; any OTHER
   body exception still propagates. (GeneratorExit has no dedicated
   exc tag — it is PY_EXC_BASE + message — so identity is the precise
   discriminator.)
3. The two async_with/contextmanager sites (py_gen_next:246,
   py_gen_throw:283) have NULL-routed protocol handling around them
   (exit/throw swallow rules) — left as REVIEWED-DEFERRED: auditing
   the contextmanager swallow semantics is its own slice.

Observed both tiers: `1 / throw-err / 1 / after-close-stop` rc=0 ==
CPython; parity 16 passed (new pin
`test_generator_throw_close_exception_routing`); contract + fallback +
matrix running at write time, result in goal-state.

## Review 5: re-engine family (3 sites) — ALL FALSE POSITIVES

All three sites (`py_re_findall_flags`:697, `py_re_engine_split`:780,
`py_re_engine_sub`:806 in native_text_modules.py) ALREADY emit
`_emit_post_call_err_check` immediately after the call — the audit's
fixed 8-line window was consumed by the multi-line argument lists, so
the check landed past the window. No code change. HEURISTIC
IMPROVEMENT for the next audit run: scan to the end of the enclosing
statement plus N lines (or to the next `self.builder`/`return`),
not a fixed line count — multi-arg builder calls routinely span 8+
lines.

Updated family tally: binop CLOSED (Reviews 2-3+completion),
gen send/throw/close CLOSED (Review 4), py_obj_next FALSE (Review 1),
re-engine FALSE (Review 5). REMAINING: py_obj_getattr 14 (the big
one), contextmanager deep-audit (deferred), py_str_mod 1,
py_user_matmul_dispatch 1, singles (py_continuation_get_slot,
py_call_merge_kwargs, py_zip_star, py_subprocess_check_output,
py_pickle_dumps, py_gen_next/throw in async_with), and the
direct-py_raise group (15, likely different-shape false positives).

## Review 6: py_obj_getattr family — main shapes GREEN, 14 sites downgraded

Red probe (raising `__getattr__` via a Dyn attribute read, plus a
plain missing-attribute AttributeError, both inside try/except):
`attr-err / plain-attr-err` rc=0 on the strict tier == CPython — the
PRIMARY Dyn attr-load path routes errors correctly. The 14 flagged
sites spread across builtin_type_attr / assignment / attr_load
secondary paths; with the main shapes green they are DOWNGRADED to
low-priority case-by-case review (each needs its own reaching probe).

## Audit sprint tally (2026-06-11 night)

Five families dispositioned: binop (TRUE x2 -> fixed, family closed),
gen send/throw/close (TRUE + runtime GeneratorExit hole -> fixed),
py_obj_next (FALSE — equivalent routing), re-engine (FALSE — checks
past the 8-line window), py_obj_getattr (main shapes green,
sites downgraded). Plus the contextmanager deep-audit deferred and the
direct-py_raise group presumed different-shape. The audit list is now
a low-priority backlog rather than an active lead.

## Review 7 (final): contextmanager swallow/propagate — GREEN, audit CLOSED as active lead

Red probe (handler-swallowed ValueError completing the with statement
vs unhandled ValueError propagating to the enclosing except):
`in 1 / swallowed / in 2 / propagated` rc=0 on the strict tier ==
CPython line for line — the NULL-routed protocol handling around the
async_with/contextmanager py_gen_next/py_gen_throw sites is correct.
Pinned by `test_contextmanager_swallow_and_propagate` (generator
parity suite). With this, every family in the audit is dispositioned;
the remaining 14 getattr secondary sites + singles stay as a
low-priority backlog inside this file.

## Status
resolved as an active lead — two true-positive families fixed
(binop x2 defects, gen throw/close + GeneratorExit hole), four
families verified green/false-positive (py_obj_next, re-engine,
getattr main shapes, contextmanager), heuristic improvements recorded
for any future re-run.

## Addendum: augmented assignment __iop__ protocol (binop-family extension)

Red probe: a class defining ONLY `__iadd__` — `a += 1` raised
"unsupported operand type(s) for +" (the augassign Name path lowered
straight into the plain binop, so the freshly-fixed `__add__` dispatch
missed and the in-place dunder never ran; CPython tries
type(a).__iop__ FIRST). Fixed: C-only `py_obj_inplace_op(a, b,
op_code)` (op codes +,-,*,/,//,%; instance/user -> lookup
__iadd__/__isub__/... -> NotImplemented falls through to the
corresponding py_obj_* binary dispatcher) + the augassign Name branch
routes Dyn pointer targets (non-cpy) through it with an err-check.
Observed both tiers: `iadd-err / 42` rc=0 == CPython (raising __iadd__
caught; mutating __iadd__ returning self gives 42). Regression round 3
extends the binop test to the full set incl. iadd. Subscript/Attr
augassign targets keep the existing desugar path — recorded as
follow-up (same recipe through the same helper).

## Addendum 2: subscript/attr augassign __iop__ (bootstrap-verified)

`d[k] += obj` / `o.field += obj` desugared through the plain binop, so
`__iadd__`-only classes raised "unsupported operand" at module level
(CPython: 42/10/5). The generic-subscript and Attr augassign branches
now route through `py_obj_inplace_op` with err-checks (the slice
branch keeps the plain binop — slice targets have no in-place dunder
shape). Observed both tiers: `42 / 10 / 5` rc=0 == CPython. Combined
battery 155 passed in one session (contract 130 + binop regressions 2
+ fallback 18 + five-GC matrix 5); regression round 4 adds the
dict-key and attr-field cases.
