# Module-level `del` does not unbind the name — 2026-08-25

## Status

**Open, and re-scoped upward.**  I originally filed this as a garbage-collection
pacing observation ("`del` defers `__del__` to process exit").  That framing was
wrong.  Measurement shows `del` at module scope **does not delete the binding at
all**; the late finalizer is a downstream symptom.  This is a Python semantics
defect, not a collector timing preference.  Not fixed in this slice.

## The correction

My earlier note said "with an explicit `del`, pcc does run `__del__`, but at
process exit".  Splitting local from global scope shows the two behave
differently:

```text
                 CPython              pcc
local del        freed local          freed local          <- correct
                 after local del      after local del
global del       freed global         after global del     <- deferred
                 after global del     freed global
```

Function-local `del` matches CPython exactly, so the collector is not at fault.

## What is actually wrong

```python
n = 42
del n
print('n is', n)                  # CPython: NameError
print('n in globals:', 'n' in globals())
```

```text
CPython    NameError as expected      n in globals: False
pcc        n is 42                    n in globals: True
```

The deleted global is still readable and still present in `globals()`.  `del`
at module scope is effectively a no-op for the binding.

## Mechanism, read from the emitted module

Each module global gets a slot and an initialized flag:

```llvm
@.modvar.delscope.g             = global ptr null
@.modvar.delscope.g_initialized = global i1 0
```

`del g` releases the value, and then the module epilogue does:

```llvm
%init = load i1, ptr @.modvar.delscope.g_initialized
br i1 %init, label %globals.g.publish, label %globals.g.continue

globals.g.publish:
  %g = load ptr, ptr @.modvar.delscope.g
  call i64 @py_module_attr_set(module, "g", %g)
```

`del` leaves both the slot and the `_initialized` flag untouched, so the
epilogue republishes the deleted name into the module dict.  That republished
reference is what keeps the object alive until module teardown, which is why the
finalizer looked "deferred".

## The runtime is not involved

A direct probe against the current C archive under `PCC_GC_BACKEND=0`:

```text
after py_instance_new:  rc=1
after pin/unpin:        rc=1
releasing (the del)...
  [__del__ ran]
del_calls=1
```

`py_instance_new` yields refcount 1, `pcc_gc_pin`/`pcc_gc_unpin` is balanced,
and `pcc_gc_release` drops to zero and dispatches `__del__` immediately.  Prompt
reclamation works; the frontend is holding the extra reference.

## Regression

`tests/python/test_module_global_del_unbinds.py` covers local `del` (control,
must keep matching CPython), global `del` finalizer ordering, the `NameError`
after a deleted global, and `'n' in globals()`.  Currently **RED** on the last
three.

## A partial fix was tried, measured, and reverted

Clearing the module slot and the `_initialized` flag in the delete lowering
**was implemented and does emit**:

```llvm
store ptr null, ptr @.modvar.delsem.g
store i1 0, ptr @.modvar.delsem.g_initialized
```

It changed the read from the stale value to a null:

```text
before the change   g is 42            g in globals: True
with the change     g is <null>        g in globals: True
CPython             NameError          g in globals: False
```

So it removed the stale value but **fixed neither reported symptom**, and it
traded a silently-wrong read for a null read, which is a fault hazard for any
consumer less forgiving than `py_print`.  It was reverted;
`delete_lowering.py` has an empty diff.

Two measurements explain why clearing alone cannot be enough:

- **No global read consults the flag.**  In the emitted module the
  `_initialized` flag appears only at its two stores and in the publish
  epilogue — never on a read path.  So a deleted global reads the slot
  directly and cannot raise `NameError` no matter what the flag says.
- **`globals()` still reports the name** even with the flag cleared.  Measuring
  when the name enters the dict answers why:

  ```text
                  CPython   pcc
  before assign   False     False
  after assign    True      True
  after del       False     True
  ```

  The flag-gated publish runs when `globals()` is evaluated, so the entry was
  inserted by the *second* call while the flag was still set.  The publish is
  **additive** — it calls `py_module_attr_set` and never removes — so clearing
  the flag only stops future publishes and leaves the existing entry behind.
  `py_module_attr_del` already exists in the runtime
  (`py_module_attrs.c:204`); nothing new is needed on that side.

## Fix direction, in three parts

1. `del` clears the module slot and the `_initialized` flag (implemented above,
   reverted only because it is incomplete on its own).
2. Module-global **reads** consult the flag and raise `NameError` when unset.
   The earlier worry was that this adds a branch to every module-global read.
   It does not have to: **only names that appear as a `del` target somewhere in
   the module can ever be unbound**, so the check is needed only for those.  A
   module that never deletes a global pays nothing, which removes the cost
   objection that was blocking this part.
3. `del` calls `py_module_attr_del(module, name)` to drop the already-published
   entry.  The primitive exists; the publish being additive is the whole reason
   clearing the flag was not enough.

Land 1, 2 and 3 together.  1 alone produces the null read shown above; 1 and 3
together fix `globals()` and the finalizer timing but still leave a deleted read
returning null instead of raising, which is why 2 belongs in the same change.

Also unmeasured: `del` inside a function targeting a `global`-declared name,
`del obj.attr`, and `del d[k]`.

## Nonclaims

- Nothing was fixed.
- Only module-scope `del` was measured; `del obj.attr` and `del d[k]` were not.
- The interaction with a `global` statement inside a function is unmeasured.
- No bootstrap, stage, fixed-point or five-GC gate was run.
