# Investigation: valueclass raw payload reaches instance identity surfaces

## Status

resolved 2026-07-16

## Problem Description

`V-P1-VAL` VP-S3 requires every statically known value payload identity escape
to box deliberately or fail with a stable diagnostic. The frontend already
rejects direct `is`, `id()`, and weakref construction, but accepts direct
instance-dictionary/weakref-slot access and attribute mutation operations.
Those operations can silently turn a raw payload into an ordinary instance or
expose mutable identity state that valueclasses explicitly do not own.

The finite missing surface is: `p.__dict__`, `p.__weakref__`, `vars(p)`,
literal-name `getattr`/`hasattr` for those identity slots, and direct
`setattr`/`delattr` on a statically known valueclass. Values explicitly widened
to `Any` remain object projections and may use ordinary object identity.

## Repro

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_source_shape.py::test_valueclass_rejects_instance_identity_surfaces
```

Expected: seven stable `PyFrontendError` diagnostics.

Observed 2026-07-16: all seven cases were accepted (`7 failed in 0.24s`, each
with `DID NOT RAISE`).

## Test [CONFIRMED]

The parameterized source-shape regression above deterministically exposes the
missing frontend rules without entering codegen or runtime.

## Proposals

- No.1 Reject statically known raw-valueclass instance identity slots and
  mutation builtins during type inference. [CONFIRMED]

## No.1 Reject raw-valueclass instance identity surfaces

### Code Change

In Attr inference, reject `__dict__` and `__weakref__` on a resolved
valueclass receiver. In builtin Call inference, reject `vars(valueclass)`,
literal-name `getattr`/`hasattr` for those two slots, and `setattr`/`delattr`
on a statically known valueclass. Reuse the existing `PyFrontendError` claim
style; do not add package/codegen special cases or reject an `Any` object
projection.

### CONFIRMED

Attr inference now rejects direct `__dict__` and `__weakref__` access on a
resolved valueclass. Call inference rejects unshadowed builtin `vars`, direct
attribute mutation, and literal identity-slot `getattr`/`hasattr`. The existing
`id` diagnostic now also checks that the builtin name is unshadowed, matching
the weakref alias discipline and avoiding package/name special cases.

Focused results:

- new diagnostics + existing `is`/`id` + shadowed-name guard:
  `9 passed in 0.25s`;
- boxed identity runtime + weakref diagnostic pair:
  `3 passed in 0.55s`.

## Report

Proposal No.1 closes the finite direct identity surface without changing
object-projection behavior. A statically known raw value cannot acquire an
instance dictionary, weakref slot, or mutable attributes. A value first
widened to `Any` is an explicit ValueBox object projection and keeps ordinary
box identity; user functions merely named `id`, `vars`, `getattr`, `hasattr`,
`setattr`, or `delattr` remain ordinary calls.
