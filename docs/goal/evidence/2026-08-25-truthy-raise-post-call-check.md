# Truthiness post-call error check — 2026-08-25

## Claim

Every native `py_obj_truthy` call emitted by
`pcc/py_frontend/codegen/coercion_lowering.py::_truthy` is now followed by the
repository's standard `_emit_post_call_err_check`, so a raising user
`__bool__`/`__len__` (or a C-extension `nb_bool`) propagates instead of being
swallowed.

This closes the "bool unbox can raise through user dispatch without a post-call
error check" sub-gap of `PY-P0-EXACT-CONTAINER-SUBSCRIPT-FULL-OWNERSHIP`.  It is
not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## What was wrong, and it was worse than the audit note said

`py_obj_truthy` (`pcc/py_runtime/src/py_obj_ops_dispatch.c:124`) dispatches
`py_user_bool_dispatch`, `py_user_len_dispatch` and `pcc_capi_cext_truthy`, then
returns a truth value regardless.  The runtime uses the return-code exception
model, so the caller owes a `py_err_occurred()` check.  `_truthy` guarded its
`py_cpy_truthy` path with `_guard_cpy_status_not_negative` but left all four
native `py_obj_truthy` calls unguarded.

The audit recorded this as a missing check.  The measured behaviour is worse: in
condition position the generated code **took the wrong branch silently**.
Against CPython as the oracle:

```text
context        CPython            pcc (before)
if             ValueError if      NO RAISE if-false      <-- wrong branch
while          ValueError while   (loop skipped, no output at all)
__len__ in if  ValueError len     NO RAISE len-false     <-- wrong branch
not            ValueError not     ValueError not
and            ValueError and     ValueError and
or             ValueError or      ValueError or
bool()         ValueError bool()  ValueError bool()
```

That split is the mechanism, not noise: expression contexts feed their result to
a consumer that already emits an error check, so they propagated by luck.
Condition contexts feed a `cbranch` directly and had no check point at all.

## Regression

`tests/python/test_native_truthy_raise_semantics.py` covers all seven contexts
under `--backend self --python-libpython=off`, parametrized over both runtime
tiers (`port` links the pcc-Python archive, `cc` links the C sources).

RED before the fix: `At index 0 diff: 'NO RAISE if-false' != 'ValueError if'`.

GREEN after:

```text
[port] 1 passed in 56.74s
[cc]   1 passed in 1.96s
```

## Cost, measured with a control

`_truthy` is on every conditional path, so the extra blocks matter.  Same input
(`pcc/project.py`), same command, one variable — the four guard lines removed
and then restored, with the restore verified by `diff`:

```text
with guard     3339 basic blocks
without guard  3226 basic blocks
delta          +113 (+3.5%)
```

Bounded and small.  This matters because
`docs/investigations/...self-backend huge-module scaling` records that very
large modules already stress O(N^2) backend passes; +3.5% does not approach
that regime.

## Neighbor gate and the timeout it hit

```text
gtimeout ... pytest -q -x -n0 tests/python/test_native_unary_dunder.py \
  tests/python/test_native_list_pop_raise_semantics.py \
  tests/python/test_binary_dunder_dispatch_runtime.py \
  tests/python/test_native_bool_is_int_arithmetic.py \
  tests/python/test_self_host_oracle_diff.py
```

Result: `1 error in 958.04s`, at
`test_self_host_oracle_diff.py::test_000_self_host_oracle_stage_cache_warmup`.

The error is a **timeout, not a semantic failure**: the warmup's
`pcc1 ... pcc/__main__.py -o .pcc2.tmp` subprocess exceeded
`_SELF_HOST_BUILD_TIMEOUT_SECONDS = 600`.  That subprocess is stage2, and the
last recorded stage2 time is approximately `875.10s`
(`/tmp/pcc-stage2-handoff-2026-08-25.md`).  The budget is therefore below the
measured cost of the work it bounds, and any change under `pcc/` invalidates the
content-addressed stage cache and forces the cold build.  Filed as
`INFRA-P1-SELF-HOST-ORACLE-WARMUP-BUDGET`.

Attribution: the +3.5% block measurement above bounds this change's contribution;
it cannot explain a gate that was already over budget before it.  This is an
argument from measurement, not a bisect.

## Nonclaims

- The four earlier files in that batch did not report in the original run,
  because `-x` stops at the first failure and the oracle warmup is a session
  fixture.  They were rerun without the oracle and are green:
  `11 passed in 59.70s` for `test_native_unary_dunder.py`,
  `test_native_list_pop_raise_semantics.py`,
  `test_binary_dunder_dispatch_runtime.py` and
  `test_native_bool_is_int_arithmetic.py`.
- No bootstrap, stage, fixed-point or five-GC gate was run.
- Three sub-gaps of the parent row remain: operand rooting across index/key/hash
  calls, getitem error-path receiver/key cleanup, late owned-local `err.exit`
  registration, and exact-int print consumption.
