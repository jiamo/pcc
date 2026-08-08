# Investigation: function-local class hoisting publishes the class after its first use

## Status

implemented on current source; final current-pcc1 gate pending

## Problem Description

A class statement inside a function is hoisted to module scope by
`hoist_nested_funcdefs`, but the hoisted `ClassDef` is appended after the
module's original executable statements. When a top-level call invokes the
outer function first, the generated function loads a NULL module class slot.
The entry `main` initializes the hoisted class only after that call returns.

This is not only an ordering bug. Python creates a new class object each time
the class statement executes, at that source position, and method bodies may
close over invocation-local values. The current one-shot module-global hoist is
therefore an approximation that needs an explicit representation design.

## Repro

The first failure was observed through
`tests/python/test_native_dict_fromkeys.py::test_dict_fromkeys_native_no_libpython`:

```python
def main():
    class BadIter:
        def __iter__(self):
            raise ValueError("iter boom")
    dict.fromkeys(BadIter())

main()
```

The generated IR loads `@.class.prog.BadIter` and calls `py_instance_new`
inside `user_prog_main`, while the store that initializes that global appears
later in entry `@main`, after `call @user_prog_main`. Runtime consequently
reports `py_obj_iter received NULL object` instead of the source `ValueError`.

## Test [CONFIRMED]

Confirmed on 2026-08-14 with fail-fast serial execution. The accidental
`dict.fromkeys` reproducer exited 1 with the runtime NULL-object diagnostic,
and emitted IR showed the load/use before the class-global store.

A dedicated future regression must cover two calls to the outer function,
per-call class identity, source-order side effects, a captured invocation-local
value, and exception propagation. The `dict.fromkeys` test is scoped back to
module-level helper classes so it continues to test iterator error propagation
rather than this independent compiler boundary.

## Proposals

- No.1 Lower a function-local class statement as an executable local binding
  with per-invocation class construction and closure ownership. [pending]
- No.2 Initialize the existing hoisted class once before module statements.
  [DENIED]

## No.1 Lower a function-local class statement as an executable local binding

### Code Change

Design a local class binding representation that predeclares native method
bodies but evaluates bases, metaclass, namespace, decorators, and captured
values at the class statement on every invocation. Store the resulting owned
class object in a normal rooted local slot and release/rebind it through the
existing owned-local contract.

### implemented

The hoist now keeps a rewritten executable `ClassDef` in the enclosing body
while retaining a module-level copy only for method declaration/emission.
Synthetic local classes are excluded from module initialization and globals
publication.  At the source statement, class construction stores the fresh
object into an owned, frame-registered local root; instance construction,
metaclass, base and class-attribute paths prefer that active local binding.
Capture attachment uses the class's private-name mangling rule.

The dedicated regression runs the same strict self/no-libpython executable
under GC0..4 and covers two-call identity, decorator ordering, invocation-local
captures and exception propagation.  A second node locks the root-store/load
and enter/leave IR shape.  Final current-pcc1 evidence remains intentionally
deferred to the one frozen-source bootstrap sequence.

## No.2 Initialize the existing hoisted class once before module statements

### DENIED

This would make the NULL symptom disappear but would still give repeated
function calls one shared class identity and evaluate class-body effects at
module import rather than the source statement. It weakens Python semantics
and is not an acceptable fix.
