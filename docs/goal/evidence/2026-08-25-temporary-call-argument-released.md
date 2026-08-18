# Temporary call arguments are released — 2026-08-25

## Claim

A temporary passed as a call argument is now consumed at the call boundary, so
`f(T())` and `f([T()])` finalize their objects when the call returns, matching
CPython.  A borrowed argument such as a plain name is unchanged.

Closes the remaining half of `PY-P1-TEMP-CONTAINER-ARGUMENT-AND-DEL-TIMING`
(the `del` half was closed earlier).  Not Stage1, Stage2, fixed-point,
five-GC, or performance evidence.

## Wider than filed

The row said "temporary **container** arguments are retained".  Measurement
shows a bare temporary leaks the same way, and neither is freed even at
teardown:

```text
                CPython              pcc (before)
takes([T()])    inside / freed elem / after   inside / after      (never freed)
takes(T())      inside / freed direct / after inside / after      (never freed)
```

So every `f(SomeObject())` leaked one object.

## Mechanism

The emitted call already pinned the argument and unpinned it afterwards, but
carried **no release on the success path** — only on the exception edge:

```llvm
%inst.T = call ptr @py_instance_new(...)
call.cont.9:
  call void @pcc_gc_pin(ptr %inst.T)
  call void @user_takes(ptr %inst.T)
call.cont.13:
  call void @pcc_gc_unpin(ptr %inst.T)      ; unpinned, never released
  ret void
call.err.cleanup.10:
  call void @pcc_gc_release(ptr %inst.T)    ; only here
```

The release machinery exists: `pinned_arg_temps` carries an `owned` flag and
`_gc_release`s when it is set.  Instrumenting showed the flag was simply never
set:

```text
[ARG] node=Call owned=False
```

`_last_call_arg_owned_temp` meant "this lowering **boxed** a native value into
an object", not "the argument expression owns a fresh reference".  `T()`
returns a pointer directly, no box is created, so the flag stayed false.

## The fix follows an existing precedent

The `IntType` branch of the same function already classified this correctly:

```python
self._last_call_arg_owned_temp = self._pcc_pointer_source_is_owned(ast_arg)
```

The object branch now does the same when the value is already a pointer, while
keeping the existing box case.  This reuses the classifier the int path already
trusts rather than inventing a second rule.

## Control

`takes(kept)` passes a borrowed local and must **not** gain a release; a wrong
classification here is a double free, not a leak.  The regression covers it and
`freed kept` appears at program end, in the CPython position.

The expected output was produced by running the program under CPython, not
written from reasoning.

## Gates

```text
tests/python/test_temporary_call_argument_released.py     1 passed
module-del, truthy-raise, print-owner, walrus, chained    7 passed
dict/list/str parity and async/await                     55 passed
tests/python/test_py_corpus.py                          177 passed in 629.94s
```

The corpus has now run at 177 passed three times across this and the two `del`
changes.

## Nonclaims

- Only direct user-function call arguments were measured.  Method calls,
  builtin calls and C-extension boundaries share the flag but were not
  separately probed.
- No bootstrap, stage, fixed-point or five-GC gate was run.
