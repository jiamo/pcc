# Evidence: V-P1-VAL VP-S3 Identity Escape Completeness

task: `V-P1-VAL`

slice: `VP-S3`

status: `DONE_STRONG` for this finite slice; parent task is entering closing
validation.

source identity: shared local worktree on 2026-07-16; no clean-commit or
release claim.

## Proven behavior

- Direct raw valueclass payloads reject `is`, builtin `id()`, weak references,
  `__dict__`, `__weakref__`, builtin `vars()`, identity-slot
  `getattr`/`hasattr`, and direct `setattr`/`delattr` with stable diagnostics.
- The new builtin diagnostics apply only when the name is unshadowed; same-name
  user functions remain callable.
- Values deliberately widened to `Any` are ValueBox object projections and
  preserve normal box identity for `is` and `id()` under the strict self
  backend.

## Gates

- source diagnostic matrix, existing `is`/`id`, and shadowed-name guard
  - result: `9 passed in 0.25s`
- boxed identity self/no-libpython runtime plus two weakref diagnostics
  - result: `3 passed in 0.55s`
- `gtimeout 60s env -u LC_ALL uv run black pcc/py_frontend/type_infer.py tests/python/data_model/test_value_class_source_shape.py`
  - result: formatted successfully

## Claim boundary

This closes VP-S3's finite statically known raw-payload identity surface. It
does not remove identity from an explicit ValueBox object projection, and it
does not prove the parent task until the full valueclass gate and final
five-GC bootstrap matrix pass.

## Open boundary

None for VP-S3.
