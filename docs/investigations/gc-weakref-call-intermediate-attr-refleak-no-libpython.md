# Investigation: weakref-call result used as an intermediate attr-access receiver (`r().v`) leaks a strong ref on #0

## Status
active — finding CONFIRMED + SCOPED 2026-05-31 (surfaced while probing the
weakref-callback contract brick). Narrow #0-only refcount-discipline gap in the
frontend; fix deferred (frontend codegen is self-host critical). Masked on
#1/#2/#3/#4 (the tracing collect reclaims by reachability, so a leaked refcount
does not keep the object alive).

## Problem Description
`r().v` — calling a weakref to resolve it and immediately accessing an attribute
on the result, WITHOUT binding the result to a variable — leaks the temporary
strong reference returned by `r()` on backend #0 (refcount+cycle). The referent
is kept alive after its last real strong ref is dropped, so it is never freed
and the weakref keeps resolving.

## Repro
```bash
cat > /tmp/wrleak.py <<'PY'
import gc, weakref
class T:
    def __init__(self, v):
        self.v = v
def main():
    t = T(5)
    r = weakref.ref(t)
    x = r().v          # weakref-call result as an INTERMEDIATE receiver
    t = 0
    gc.collect()
    print(r() is None)  # CPython / #1-4: True ; #0: False (leaked -> not freed)
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/wrleak.py -o /tmp/wrleak_bin
for b in 0 1 2 3 4; do echo "#$b: $(PCC_GC_BACKEND=$b /tmp/wrleak_bin)"; done
python3 /tmp/wrleak.py   # True
```

## Test [CONFIRMED]
Manual repro above. Not yet a committed regression test (would be an inverted
xfail — #0 fails, #1-4 pass — which is an awkward contract shape; better gated by
a dedicated #0 refcount test once fixed). The clean weakref-callback CONTRACT
(callback fires + invalidate on 0..4) is locked separately in
`tests/python/gc_production_contract/test_weakref_callback.py`.

## Scope (discriminators)
- `y = r(); y = 0; t = 0; gc.collect()` (weakref result BOUND then dropped): #0
  -> `True` (released correctly). So binding releases the strong ref.
- `x = make().v` (a NON-weakref call result as an intermediate receiver): #0 ->
  `True` (released correctly). So the general "release intermediate call-result
  receiver" path works.
- Only `r().v` (weakref-call result as an intermediate receiver) leaks. The bug
  is the intersection: weakref `__call__` returns a strong (owned) ref, and the
  attribute-access-on-intermediate lowering does not release THAT receiver,
  although it releases a normal call's result receiver.

## Proposals
- No.1 in the frontend attribute-access lowering, ensure the receiver-release
  that already fires for a normal call-result intermediate also fires when the
  receiver is a weakref-`__call__` result (i.e. the weakref-deref result is
  marked owned for receiver-release the same way a normal call result is)
  [pending — narrow frontend codegen fix; bootstrap-critical path, so it needs a
  minimized regression + full stage1->2->3 bootstrap; low severity (#0-only
  over-retention), deferred]

## Context
Surfaced while probing the `weakref-callback-during-collect` 5-GC contract brick:
the callback mechanism itself is correct on 0..4 (locked), but the probe's
`alive = r().v` line diverged (#0 `5 0 False` vs CPython/#1-4 `5 1 True`),
isolating this refcount leak. This is NOT a GC-backend-equality gap in the same
sense as the sweep gaps — it is a frontend refcount-discipline bug that only
manifests on the refcount backend; it is filed here so the next weakref/refcount
pass has the scoped reproducer.
