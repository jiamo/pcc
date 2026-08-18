# Print-consumer ownership — investigation, not a fix — 2026-08-25

## Status

**Open.**  The "exact-int print consumers borrow a NEW subscript result"
sub-gap of `PY-P0-EXACT-CONTAINER-SUBSCRIPT-FULL-OWNERSHIP` is **not** closed.
This file records what was measured, an attempted fix that was **reverted**
because it was a measured no-op, and a pre-existing compiler crash found on the
way.  No production change ships from this slice.

## Verified: `py_print` borrows

`py_print` (`pcc/py_runtime/src/py_print_fmt.c:345`) is
`py_format(stdout, o); fputc('\n', stdout);` — no incref, no decref.  It
borrows unconditionally.  `py_tuple_set_item` retains.  So any fresh object
handed to either consumer is still owned by the caller afterwards.

## Verified leak 1 — generic attribute print

```python
def f(o):
    print(o.n)
```

Emitted body (self backend, no-libpython):

```text
%attr.n = call ptr @py_obj_getattr(ptr %o, ptr @.pyattr.n)   ; NEW reference
attr.n.ok:
  call void @py_print(ptr %attr.n)
  call void @pcc_gc_frame_leave(...)                          ; frame map is "borrowed"
  ret ptr %none
```

No `pcc_gc_release` of `%attr.n` anywhere in the function, and the frame map is
the borrowed one, so the frame leave does not cover it.  One int object leaked
per call.

## Verified leak 2 — subscript print

```python
def f(a: list) -> None:
    print(a[0])
```

```text
%get = call ptr @py_list_getitem(ptr %a, i64 %i)   ; NEW reference
call void @pcc_gc_store_root(ptr %root, ptr %get)  ; root increfs  -> 2
%cur = call ptr @pcc_gc_load_ptr(ptr null, ptr %root)
call void @pcc_gc_store_root(ptr %root, ptr null)  ; root decrefs  -> 1
call void @pcc_gc_frame_leave_lifo(ptr %root)
call void @py_print(ptr %cur)                      ; borrows
call void @pcc_gc_frame_leave(...)
```

The root is balanced, but the original new reference from `py_list_getitem` is
never released.  Net one leaked reference per call.

## The attempted fix, and why it was reverted

Adding `self._gc_release_if_owned(exact_obj, arg)` after both print consumers in
`print_lowering.py` emitted **nothing**: re-emitting the IR after the change
showed no new `pcc_gc_release` for any of the shapes tried.  The ownership
classifier reports these expressions as not-owned, so the call was a silent
no-op.  A no-op edit in shared codegen is worse than no edit, because it reads
as a fix — it was reverted and `print_lowering.py` now has an empty diff.

Two further facts a successor needs:

- The value that actually reaches `py_print` on the exact-int arithmetic path is
  not the heap object.  `print(p + q)` emits
  `%int.obj = call ptr @py_int_add(...)` but then passes
  `%int.tag.result` to `py_print` — a tagged-or-heap union.  A correct release
  must apply to the heap projection only; releasing the tagged form is wrong.
- `_pcc_pointer_source_is_owned` and `_gc_release_if_owned` both return false
  for `Attr`, `BinOp` and `Subscript` in this position.  Whatever the fix is, it
  is not "call the existing classifier here"; that was tried and measured.

## Pre-existing compiler crash found on the way

```python
def f(a: list) -> None:
    x: int = a[0]
    print(x)
```

```text
error: PCC-PY-COMPILE-001: [python-frontend]
       'Subscript' object has no attribute 'func'
```

Traceback, obtained by calling the pipeline directly since the CLI surfaces
only the bare AttributeError:

```text
assignment_statement_lowering.py:726 in _emit_assign
    elif boxed_int_target and self._is_walrus_sentinel(stmt.value):
stmt_misc_lowering.py:368 in _is_walrus_sentinel
    isinstance(expr.func, Name)
AttributeError: 'Subscript' object has no attribute 'func'
```

`_is_walrus_sentinel` reads `expr.func` without first checking that `expr` is a
`Call`, so any annotated assignment with a boxed-int target and a non-`Call`
right-hand side crashes the frontend.  Control: the crash reproduces with the
`print_lowering.py` change removed, so it is pre-existing and not caused by this
slice.  Filed as `PY-P1-WALRUS-SENTINEL-NON-CALL-CRASH`.

## Nonclaims

- No leak was fixed.  Both verified leaks remain.
- The two verified shapes route through generic attr/subscript lowering, not
  the exact-int branch.  Whether the exact-int branch leaks independently was
  not established, because no tried shape reached it with an owned heap value.
- No bootstrap, stage, fixed-point or five-GC gate was run.
