# Investigation: list()/sum()/tuple()/set() consume DynType via len+getitem, silently yielding empty/wrong results for iterator-only objects (generators)

## Status
active

## Problem Description
Under strict no-libpython (`--backend self --python-libpython=off`, DEFAULT
runtime ports), `list(gen())` and `sum(gen())` over a generator return an empty
list / `0` instead of the generator's values. The generator itself is fine — a
direct `for v in gen(): ...` loop iterates it correctly via the iterator
protocol (`py_obj_iter`/`py_obj_next`). The gap is in the **sequence builtins'
consumption of a DynType argument**.

Root cause: `list_builtin_lowering._maybe_emit_list_builtin`'s DynType/Tuple/
Class arm (≈ line 188) iterates the source via `py_obj_len(src)` +
`py_obj_getitem(src, i)` (length + integer index). A generator is iterator-only:
it has no length and no `__getitem__`, so `py_obj_len(gen)` returns 0 and the
copy loop runs zero times → empty list. `sum()` (`_maybe_emit_sum_literal`)
has the same shape. This diverges from CPython, where `list(x)`/`sum(x)` always
use `iter(x)`/`next()`.

This is a silent WRONG-OUTPUT correctness gap (obligation 1): the program
compiles and runs under `=off`, producing wrong results, with no diagnostic.

## Repro
```python
def gen(limit):
    i = 0
    while i < limit:
        yield i * i
        i += 1
def main():
    for v in gen(3):
        print(v)            # pcc: 0,1,4  CPython: 0,1,4   (IDENTICAL — generator works)
    print(list(gen(3)))     # pcc: []          CPython: [0, 1, 4]   WRONG
    print(sum(gen(4)))      # pcc: 0           CPython: 14          WRONG
main()
```
```
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/gen.py -o /tmp/gen_bin
/tmp/gen_bin           # list(gen())==[] , sum(gen())==0
python3 /tmp/gen.py    # list(gen())==[0,1,4] , sum(gen())==14
```

## Update (2026-05-30): scope is BROADER + exact root cause localized
The gap is not limited to `list()`/`sum()`. A discriminating probe shows EVERY
DynType-iterable consumer except the statement `for`-loop is broken on a
generator under `=off`:

| consumer | pcc | CPython |
|---|---|---|
| `for x in gen(): t+=x` (stmt, var or inline) | 14 | 14 (WORKS) |
| `[x for x in gen()]` (comprehension) | `[]` | `[0,1,4,9]` |
| `sorted(gen())` | `[]` | `[0,1,4,9]` |
| `set(gen())` | `set()` | `{0,1,4,9}` |
| `tuple(gen())` | `()` | `(0,1,4,9)` |
| `list(gen())` | `[]` | `[0,1,4,9]` |
| `sum(gen())` | `0` | `14` |
| `max(gen())` | `0` | `9` |

Root cause (single shared pattern): the DynType iterable dispatch routes to a
**len + integer-getitem** loop instead of the **iterator protocol**
(`py_obj_iter`/`py_obj_next`). A generator has no length / `__getitem__`, so the
loop runs zero times.

- `comprehension_lowering._emit_comprehension_generator` line 910:
  `isinstance(iter_ty, DynType)` -> `_emit_comprehension_obj_indexed` (len+getitem,
  BROKEN). Line 924 `ClassType` -> `_emit_comprehension_obj_iterator`
  (`py_obj_iter`/`next`, CORRECT). So ClassType already iterates correctly;
  DynType does not.
- `list_builtin_lowering._maybe_emit_list_builtin` DynType arm (~188): same
  len+getitem.
- `for_loop_lowering` routes DynType through `py_obj_iter`/`py_obj_next` — which
  is exactly why the statement `for`-loop WORKS. **for_loop is the correct
  reference**; the comprehension + builtins should match it for DynType.

Revised recommended fix: make the DynType dispatch use the iterator protocol
(the `_emit_comprehension_obj_iterator` path / `py_obj_iter`+`py_obj_next`),
matching `for_loop` and the existing `ClassType` arm. This is a small dispatch
change per site (comprehension line 910 is one line: DynType -> obj_iterator),
but the behavioral blast radius is large (DynType comprehensions are pervasive
in pcc's own source), so the FULL bootstrap is the required safety net (the
for-loop already proves `py_obj_iter` works for the DynType iterables in the
pcc closure). Per-site, in priority order: comprehension (910), list builtin
(188 arm), then sum/sorted/set/tuple. If the full bootstrap regresses, fall back
to the tag-branch (No.1): only route generators (tag 20) through iter/next.

## Test [CONFIRMED]
Run 2026-05-30: the direct for-loop is diff-IDENTICAL; `list(gen())` and
`sum(gen())` DIVERGE (empty/0). Localized: the for-loop path uses the iterator
protocol (`for_loop_lowering` py_obj_iter/next) and works; `_maybe_emit_list_builtin`
DynType arm uses py_obj_len+py_obj_getitem and fails for iterator-only objects.
A focused regression must assert `list(gen())`/`sum(gen())` match CPython.

## Proposals
- No.1 runtime tag-branch in the builtins' DynType arm (iter protocol for tag-20 generators)  [pending — SAFEST]
- No.2 route DynType through the iterator protocol unconditionally  [pending — most CPython-correct, higher regression risk]
- No.3 add a `py_list_from_iterable(obj)` runtime helper, route DynType list()/tuple()/set()/sum() through it  [pending — cleanest, runtime change]

## No.1 runtime tag-branch (iter protocol for generators only)
### Code Change (described, NOT yet applied)
In the DynType arm of `_maybe_emit_list_builtin` (and the sum/tuple/set
equivalents), emit a runtime `tag = py_type_of(src)`; if `tag == 20`
(PY_TYPE_COROUTINE = generator/coroutine) emit an iterator-protocol loop
(py_obj_iter + py_obj_next until NULL/StopIteration, appending), else keep the
existing len+getitem loop. Lowest regression risk: only the currently-broken
generator case changes; indexable DynType (lists/tuples/`__getitem__`-only
classes) keep working exactly as today.
### Trade-off
Per-builtin duplication of the two-path branch; covers generators but not other
iterator-only objects (custom `__iter__`-only classes). Bounded + safe.

## No.2 iterator protocol unconditionally for DynType
### Code Change (described)
Replace the DynType len+getitem loop with a single iter/next loop. Matches
CPython exactly and fixes ALL iterator-only objects.
### Trade-off
`list()` is bootstrap-critical; this changes behavior for EVERY `list(DynType)`
in pcc's own source. Risk: a DynType with `__getitem__` but no `__iter__`
(sequence-protocol-only) would break, where CPython's list() falls back to the
sequence protocol. Must prove via full bootstrap that no such object exists in
the pcc closure. Higher risk; do only with the full bootstrap as the safety net.

## No.3 py_list_from_iterable runtime helper
### Code Change (described)
Add `py_list_from_iterable(obj)` (C + py-port mirror) doing CPython's list()
logic: try iter/next; this is the single source of truth, called from
list()/tuple()/set()/sum() DynType arms. Runtime change -> rebuild + full
bootstrap (~6 min gate).
### Trade-off
Cleanest and most reusable, but a runtime change (expensive gate, both C and
the pcc-Python port must mirror). Best long-term answer.

## Notes
Found by the realistic-program CPython-diff methodology (a closures/generators/
%-format program), same session as #18-#21 idiom shrinks. Distinct from those
additive fixes: this is a silent-wrong-output correctness hole in shared,
bootstrap-critical builtin lowering, so it gets the investigation workflow + a
careful proposal choice + full-bootstrap gating rather than a tail-of-loop
patch. The `max(3,7,2)` NameError found in the same program was the bounded,
low-risk sibling and was fixed separately (#21, frontend-only).
Recommended start: No.1 (safe tag-branch) for list() + sum(), gated by the repro
+ a focused regression + full bootstrap; escalate to No.3 if tuple()/set() and
custom-iterator classes need the same treatment.

## Update (2026-05-30): related but distinct gap — __getitem__-only iteration fallback
A realistic-program probe (real5.py: custom-class dunders) confirmed a related
but DISTINCT gap: `for x in obj` / `[x for x in obj]` where `obj` defines
`__getitem__` but NOT `__iter__` raises `TypeError` under =off, whereas CPython
falls back to the SEQUENCE protocol (calls `obj[0]`, `obj[1]`, ... until
`IndexError`). This is the `__getitem__`-only iterable case flagged as a risk in
the #22 fix — confirmed it was ALREADY broken (the fixes #22/#23/#24 route
DynType through `py_obj_iter`, which itself lacks the `__getitem__` sequence-
iterator fallback; the bootstrap passed because pcc's own code has no
`__getitem__`-only iterables).

- WORKS (verified same probe): `__len__`, `__getitem__` direct (`obj[i]`),
  `__contains__` (`x in obj`), `__repr__`, `__eq__`, `__bool__` (`bool(obj)`,
  `if obj:`, `if not obj:`) — all DEFAULT-mode diff-IDENTICAL.
- GAP: `py_obj_iter` does not synthesize a sequence-iterator for a
  `__getitem__`-only object. FIX (runtime, deferred): `py_obj_iter` (py_obj
  runtime + port) should, when the object has no `__iter__` but has
  `__getitem__`, return a small sequence-iterator object whose `__next__` calls
  `obj[i++]` and converts `IndexError` to `StopIteration`. Niche (modern code
  defines `__iter__`); lower priority than the generator-consumption sites.
