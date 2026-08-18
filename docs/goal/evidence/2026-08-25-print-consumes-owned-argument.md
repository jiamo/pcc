# Print consumes its owned argument — 2026-08-25

## Claim

The generic print tail in `pcc/py_frontend/codegen/print_lowering.py` now calls
`_gc_release_if_owned(obj, arg)` after `py_print`, so an argument expression
that produced a fresh reference is consumed instead of leaked.

This closes the confirmed `print(a[0])` leak recorded in
`2026-08-25-print-consumer-ownership-investigation.md`.  The sibling
`print(o.n)` leak is **not** closed and is explained below.  Not Stage1,
Stage2, fixed-point, five-GC, or performance evidence.

## How the classifier question was settled

The previous attempt guessed at classifiers twice and shipped a no-op.  This
time the classifiers were instrumented on the real path — a temporary probe in
the generic print tail, printing each predicate's verdict for the actual AST
node, then removed:

```text
arg=Attr      ty=DynType  _expr_returns_owned_object=False
                          _raw_scaffold_object_rhs_is_owned=True
                          _pcc_pointer_source_is_owned=False
                          owned_dynamic_call registry=False
arg=Subscript ty=DynType  _expr_returns_owned_object=True
                          _raw_scaffold_object_rhs_is_owned=True
                          _pcc_pointer_source_is_owned=True
                          owned_dynamic_call registry=False
```

So `Subscript` is already classified owned and only needed the release call;
`Attr` is classified **not** owned even though the DynType attribute path emits
`py_obj_getattr`, which returns a NEW reference.  Two different defects wearing
one symptom — which is why the earlier blanket attempt could not have worked.

## Evidence

Before, `print(a[0])` emitted zero releases.  After:

```text
%get = call ptr @py_list_getitem(ptr %a, i64 %i)
call void @pcc_gc_store_root(ptr %root, ptr %get)
%cur = call ptr @pcc_gc_load_ptr(ptr null, ptr %root)
call void @pcc_gc_store_root(ptr %root, ptr null)
call void @pcc_gc_frame_leave_lifo(ptr %root)
call void @py_print(ptr %cur)
call void @pcc_gc_release(ptr %cur)      <-- added
```

The release immediately follows `py_print` and targets the same value.

Control pair, same input, one variable:

```text
def direct(a: list) -> None:  print(a[0])      releases: 0 -> 1
def bound(a: list) -> None:   x = a[0]; print(x)  releases: 2 -> 2
```

The bound form already owned the value in a rooted local; it did not gain a
second release, so no double free was introduced.

Gate: `tests/python/test_print_consumes_owned_argument.py` — a runtime smoke
(correct output, exit 0, which is what a premature or double free would break)
plus the IR contrast above.  `2 passed in 3.63s`.

Neighbors: `9 passed in 26.02s` across truthy-raise, walrus-sentinel,
list-pop-raise, chained-assignment and unary-dunder.

Corpus: `tests/python/test_py_corpus.py` — `150 passed, 1 failed`.  The single
failure is `phase4/math_floor`, unrelated (see below).

## Why the runtime assertion was rewritten

The first version asserted `__del__` output to prove the reference was freed.
It failed for **both** arms, including the one believed correct, so the
assertion was not measuring this change.  Two controls explained it:

- CPython prints `freed d` / `freed b`; pcc printed neither, so the temporary
  list argument itself is retained — a separate ownership boundary.
- With an explicit `del`, pcc *does* run `__del__`, but at process exit rather
  than at the `del`: CPython emits `freed explicit` **before**
  `after explicit del`, pcc emits it after everything.

Reference counts are not observable from Python, so a `__del__` probe cannot
isolate one leaked reference while an enclosing container also retains.  The
runtime part is now a correctness smoke and the IR contrast carries the precise
claim.  Both observations above are filed rather than dropped.

## Adjacent defects found and filed, not absorbed

- `PY-P1-ATTR-GETATTR-OWNED-VALUE-UNREGISTERED` — the DynType attribute
  emitter does not register its `py_obj_getattr` result as an owned dynamic
  call value, although the exact-int attribute branch does
  (`exact_int_lowering.py:463`).  `print(o.n)` still leaks.
- `PY-P1-TEMP-CONTAINER-ARGUMENT-AND-DEL-TIMING` — the two CPython
  differentials above: a temporary container passed as an argument is retained,
  and `del` does not drop the last reference promptly.
- `BACKEND-P1-AARCH64-FRINT-NOT-ENCODABLE` — `math.floor` fails to link on the
  self backend: codegen emits `frintm/frintn/frintp/frintz`
  (`self_backend_aarch64_darwin_calls.py`) but `arm64_encode.py` implements
  none of them, so the assembler rejects `frintm d11, d9`.  This is the
  `phase4/math_floor` corpus failure and is pre-existing; nothing in this diff
  can emit a float-round instruction.

## Nonclaims

- Only the generic single-argument print tail was changed.  The many-argument
  path and the exact-int branch were left alone; whether they leak was not
  established by this slice.
- No bootstrap, stage, fixed-point or five-GC gate was run.
