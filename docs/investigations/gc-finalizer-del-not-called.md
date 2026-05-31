# Investigation: native __del__ finalizers are not called

## Status
resolved

## Problem Description
The remaining GC xfail closure bucket includes several finalizer failures.
This investigation starts with the smallest non-resurrection symptom:
a local object with `__del__` is dropped, but the finalizer body never
runs before the program observes side effects.

This is intentionally narrower than resurrection and trashcan behavior.
Those depend on correct one-shot finalizer dispatch but also require
additional cycle/resurrection ordering.

## Repro
Run the focused xfailed node with xfail disabled:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_finalizer_corner.py::test_long_running_del \
  -q -n0 --runxfail -ra
```

Expected current result: one failure. The compiled program prints `0`
where the test expects `1`.

## Test [CONFIRMED]
The command above was run on 2026-05-08 and produced:

```text
1 failed in 0.80s
```

Failure detail:

```text
AssertionError: assert '0' == '1'
```

The finalizer body contains a 10k-iteration loop followed by
`log.append(acc)`, so printing `0` means the native `__del__` path was not
called before `main()` observed `len(log)`.

## Proposals
- No.1 Treat `_ = owned_object` as discard after confirming lifetime source     [CONFIRMED]

## No.1 Treat `_ = owned_object` as discard after confirming lifetime source
### Code Change
Add a frontend discard-assignment path for function-local `_` targets:

- if RHS produces an owned pcc object, evaluate it for side effects;
- release any previous owned `_` local;
- immediately release the new owned object;
- do not keep `_` in the local environment or GC root set.

This matches the existing compiler convention that `_` is a discard name
for closure capture and the GC finalizer tests' use of `_ = Obj()` to
force immediate drop.

### CONFIRMED
Runtime logging showed that native finalizer dispatch already worked, but
it ran after the observation point:

```text
stdout: '0\n'
stderr: '[pcc.finalizer] ... event=call ... event=done ...'
```

Changing the probe to `x = Slow(); del x; print(len(log))` printed `1`,
which confirmed that class `__del__` registration and runtime dispatch
were functional for refcount deallocation.

After adding discard-assignment lowering, the focused xfails passed:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_finalizer_corner.py::test_del_can_create_new_objects \
  tests/test_gc_finalizer_corner.py::test_long_running_del \
  -q -n0 --runxfail -ra
```

Observed result:

```text
2 passed in 1.32s
```

The xfail markers were removed from those two tests. The full finalizer
corner file now reports:

```text
9 passed, 1 xfailed in 5.99s
```

The only remaining xfail in that file is module-global finalization at
runtime shutdown, which is a separate lifecycle problem.

## Report (only when the investigation is closing)
The confirmed root cause was frontend ownership/lifetime, not missing
runtime `__del__` dispatch. Function-local `_` bindings kept owned
temporary instances alive until function cleanup, so finalizer side
effects happened after tests observed state. Treating `_ = owned_object`
as discard makes those temporaries release at the assignment site.

Follow-up investigations should cover shutdown finalizers, resurrection,
and trashcan behavior separately; they are still present in the GC xfail
closure list.
