# Module-level `del` unbinds correctly — 2026-08-25

## Claim

`del` on a module-level name now unbinds it: the name raises `NameError` when
read, disappears from `globals()`, and its value is finalized at the `del`
rather than at module teardown.  `global x; del x` inside a function behaves the
same.  Output is identical to CPython for every form measured except
`del obj.attr`, which is a separate pre-existing defect filed below.

Closes the `del` half of `PY-P1-TEMP-CONTAINER-ARGUMENT-AND-DEL-TIMING` (now
P0).  Not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## Three parts, and each was necessary

The earlier attempt cleared the slot and the flag and fixed **nothing** —
`globals()` still reported the name and reads still returned null.  All three
pieces are required:

1. **Clear the slot and release its reference.**  Storing null on top of the
   slot orphans the value: with only that, `freed global` stopped printing
   *at all*, which is worse than the original late finalization.  The value must
   be loaded, the slot nulled, and the old value released.
2. **Clear the `.initialized` flag** so the module epilogue stops republishing.
3. **Call `py_module_attr_del`.**  The flag-gated publish is additive — it
   inserts via `py_module_attr_set` when `globals()` is evaluated and never
   removes — so clearing the flag only prevents *future* publishes.

Plus the read side: names this module deletes somewhere consult the flag and
raise `NameError`.  Only deleted names carry the check, so every other module
global keeps a plain load and pays nothing.

Ordering matters: the dict entry is removed before the slot's reference is
released, so a finalizer re-entering the module namespace never observes the
deleted name still present.

## Result

```text
                        CPython              pcc
del <module global>     NameError / False    identical
global x; del x         NameError / False    identical
del d['k']              len 0 / False        identical
del obj.attr            AttributeError       <null>   <- separate defect
```

Finalizer ordering also matches:

```text
freed local        after local del
freed global       after global del
```

## Two wrong turns, both found by instrumenting

- **`py_module_attr_del` never emitted.**  I used `self.module_name`, which does
  not exist.  The `mod.fini.attr.del` call visible in the IR was the
  pre-existing teardown one, not mine.  The module teardown path already had the
  right accessors: `self.ast_module.name`, `_pooled_cstr_ptr`, `_attr_name_ptr`.
- **The del-target scan silently skipped every function body.**  `global gv;
  del gv` had no effect and the collected target set printed empty even though
  the collector ran and saw the `FuncDef`.  The walk tested
  `isinstance(child, list)`, but these AST nodes carry their bodies as
  **tuples**, so nothing was ever pushed.  This failed silently — no error, just
  a check that was never generated.  A second scoping bug compounded it: the
  hook sat inside an `elif isinstance(stmt, (ExprStmt, Assign, ..., Delete))`
  branch that excludes `FuncDef`.

Neither was findable by reading; both took a probe that printed what the code
actually saw.

## Gates

```text
tests/python/test_module_global_del_unbinds.py        1 passed
module/assignment neighbours                          5 passed
tests/python/test_py_corpus.py                      177 passed in 626.29s
```

The corpus was run twice — before and after the collector rescope — at 177
passed both times.

## Nonclaims

- `del obj.attr` still returns null instead of raising `AttributeError`.  It is
  a different lowering path, was failing before this change, and is filed as
  `PY-P1-DEL-ATTR-DOES-NOT-UNBIND`.
- The temporary-container-argument retention half of the original row is
  untouched and still open.
- No bootstrap, stage, fixed-point or five-GC gate was run.
