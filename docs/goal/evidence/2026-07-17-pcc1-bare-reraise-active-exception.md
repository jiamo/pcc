# pcc1 bare re-raise active-exception state

Date: 2026-07-17

Task: `AUD-P1-PCC1-BARE-RERAISE-ACTIVE-EXCEPTION`

## First divergent boundary

A minimized nested `try` / `except ValueError: raise` program was compiled to
LLVM IR by both host pcc and the previously current pcc1. Both forms retained
the caught exception before clearing runtime TLS, so handler ownership and the
AST `body_has_raise` predicate were not the failing boundary. The pcc1 IR then
called `py_raise(py_current_exception())`, while host pcc selected the retained
handler exception when TLS was null.

The lost state was compiler object state: `_active_handler_excs` was created
lazily. That works on the host Python `L1CodeGen` object, but is not a valid
field contract for self-hosted `L1CodeGen`'s fixed object layout. pcc1 therefore
did not observe the handler-stack push when `_emit_raise` read the field.

## Generic fix and regression

- `Layer1InitMixin` now initializes `_active_handler_excs` as an empty list.
- `L1_CODEGEN_HOST_ATTRS` declares the field in the closed-world host schema.
- Handler cleanup restores the empty state as a list, keeping the initialized
  representation stable.
- The pcc1 oracle now has an explicitly named `bare_reraise` test. It exercises
  a nested handler, restores the outer caught `ValueError("inner")`, re-raises
  it to an outer matching handler, and then proves that a later bare raise sees
  no leaked handler state. The artifact runs with `PCC_HOST_PYTHON=false` and
  `--python-libpython off`.

The rebuilt pcc1 IR contains the required generic shape:

```llvm
%retained = call ptr @pcc_gc_retain(ptr %caught)
%active = select i1 %tls_has_exception, ptr %tls_exception, ptr %retained
call void @py_raise(ptr %active)
```

No compiler-source rewrite or package/module special case was added.

## Gates

Current-source stage1 only (no pcc2/pcc3 and no GC matrix):

```bash
gtimeout 360s env -u LC_ALL scripts/bootstrap.sh \
  --out-dir build/bootstrap-bare-reraise-pcc1 --backend self --stage 1
```

Result: stage1 succeeded in `153378 ms`.

Required task gates:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_python_exception_parity.py -k 'bare_raise or reraise'

gtimeout 180s env -u LC_ALL \
  PCC1_BINARY=build/bootstrap-bare-reraise-pcc1/pcc1 \
  uv run pytest -q -n0 tests/python/test_self_host_oracle_diff.py \
  -k 'bare_reraise'
```

Results: `1 passed, 11 deselected in 1.25s`; `1 passed, 398 deselected in
0.86s`.

Fixed-layout contract checks: `2 passed, 22 deselected in 0.17s`.

## Claim boundary

This proves current-source pcc1, self backend, no-libpython artifact behavior
for nested bare re-raise, original exception type/message delivery, and handler
stack cleanup. It does not claim a pcc2/pcc3 fixed point, any GC matrix result,
or general exception completeness. CPython capitalizes the separate
outside-handler diagnostic as `No active ...`; the pcc runtime currently uses
`no active ...`. The cleanup probe intentionally compares that diagnostic
case-insensitively and does not claim capitalization parity.
