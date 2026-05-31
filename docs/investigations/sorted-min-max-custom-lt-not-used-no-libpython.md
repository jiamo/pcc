# Investigation: sorted()/min()/max() ignore a custom __lt__ (no-libpython)

## Status
resolved 2026-05-30 — `sorted()` (fix #54, proposal No.4) and `min()`/`max()`
over a list of custom-`__lt__` objects (fix #55, proposal No.5) both routed in
the FRONTEND to static-`__lt__` paths that sidestep the runtime comparison
primitive entirely. The underlying runtime defect (cmp_threeway can't dispatch
a user `__lt__`; bound-method double-self in py_obj_call_method1) is documented
but intentionally NOT patched — see Report. (`min`/`max` over a custom-`__lt__`
object that is a non-list iterable is a possible follow-on, not part of this.)

## Problem Description
`sorted([Ver(3), Ver(1), Ver(2)])` for a class with `__lt__` returns the list
unsorted (`[3, 1, 2]`, original order) instead of `[1, 2, 3]`. `min()`/`max()`
of such a list misbehave too (`min(...).v` raised `AttributeError: v` — the
result was not a `Ver`). The `<` operator and an explicit `a.__lt__(b)` call
**do** work (they go through the frontend's static method resolution, not the
runtime comparison primitive). Found 2026-05-30 by real19.

`list.sort()` (the *method*) WORKS for elements with a class hint — it uses
`_emit_list_sort_with_dunder_lt` (list_method_lowering.py). Only the builtin
`sorted()`/`min()`/`max()` (which route through the runtime) are wrong.

## Repro
```bash
cat > /tmp/s.py <<'PY'
class Ver:
    def __init__(self, v): self.v = v
    def __lt__(self, other): return self.v < other.v
def main():
    print([x.v for x in sorted([Ver(3), Ver(1), Ver(2)])])  # want [1,2,3]
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/s.py -o /tmp/s_bin
/tmp/s_bin            # [3, 1, 2]  (unsorted)
python3 /tmp/s.py     # [1, 2, 3]
```

## Test [CONFIRMED]
/tmp/gap_probe/sorted_lt.py (sorted + .sort + int regression) and lt_direct.py
(`<`, explicit `__lt__`, min/max). Observed `[3,1,2]` and the min `.v`
AttributeError before any fix.

## Root cause (CONFIRMED)
`py_obj_sorted` (py_obj_ops_compare.c) sorts with `py_obj_cmp_threeway`. For a
user instance, `py_obj_cmp_threeway` (py_obj_ops_compare.c:188-192, and the port
`_cmp_threeway` final `return 0`) falls to a raw pointer-address comparison (C)
/ returns 0 (port) — it never dispatches `__lt__`. So `py_obj_lt/le/gt/ge`
(defined as `cmp_threeway </<=/>/>= 0`) are all wrong for instances; only the
frontend `<` path (static dunder resolution) is correct.

## Proposals
- No.1 cmp_threeway dispatches __lt__ via py_obj_call_method1   [DENIED — ineffective]
- No.2 cmp_threeway: getattr(bound) + 1-arg call                [DENIED — see No.1 update]
- No.3 cmp_threeway: py_class_lookup + py_obj_call               [DENIED — reached-but-ineffective]
- No.4 FRONTEND: route sorted(list-of-custom-__lt__) to the static __lt__ insertion sort  [CONFIRMED #54]
- No.5 FRONTEND: route min/max(list-of-custom-__lt__) to a static __lt__ object fold  [CONFIRMED #55]

## No.1 cmp_threeway dispatches __lt__ via py_obj_call_method1
### Code Change (attempted, then reverted)
Added, before the pointer-compare fallback in both `py_obj_cmp_threeway` (C) and
`_cmp_threeway` (port): for `tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER`,
`py_obj_call_method1(a, "__lt__", b)` (a<b -> -1), then `b.__lt__(a)` (-> 1),
else fall through. (Port hit a variable-name collision with the existing
`r: int` — renamed to `lt_ab`/`lt_ba` — after which it compiled and linked; the
.ll/.a contained the dispatch.)
### DENIED — ineffective
Even with the dispatch compiled into the archive (verified: `__lt__` appears in
build_py/py_obj_ops_compare.ll; py_obj_call_method1 in libpy_runtime_pcc_py.a),
`sorted()` still returned `[3, 1, 2]`. So `py_obj_call_method1(ver, "__lt__",
ver2)` inside cmp_threeway evidently returns NULL (no-__lt__ path taken → falls
through → no reorder). REFINED root cause (2026-05-30, diagnostic): runtime getattr DOES resolve the
dunder — `getattr(Ver(1), "__lt__")(Ver(2))` returns True (probe getattr_lt.py
IDENTICAL). So `py_obj_getattr(instance, "__lt__")` returns a **bound method**
(self already bound). But `py_obj_call_method1(o, name, arg)` builds a 2-tuple
`(o, arg)` and calls the method with BOTH — so a bound `__lt__` receives
`(o, o, arg)` => 3 args for a 2-param `def __lt__(self, other)` => arg-count
error => NULL => cmp_threeway falls through. The proper fix for cmp_threeway
is to dispatch `__lt__` with the correct arity for a bound method (call with
just `arg`), or fetch the UNBOUND function (pcc_user_dunder_lookup-style, as
py_user_str_dispatch does) and pass `(self, other)`. (Note: this also implies
py_obj_call_method1 may be subtly wrong for any bound dunder — verify whether
py_obj_truediv's __truediv__ defer is actually exercised, or also latent.) Reverted to keep the comparison primitive
clean and the #40-green tree intact; py_obj_ops_compare is the comparison
primitive (hot, used everywhere) so an unverified change is not left in place.

## Root cause of the ineffectiveness — CONFIRMED 2026-05-30
`py_instance_getattr_default` (py_class.c:71) returns a **bound method**:
`return py_instance_bind_method(method, (PyObject *)inst, name);`. So
`py_obj_getattr(instance, "__lt__")` is already bound to `self`. But
`py_obj_call_method1(o, name, arg)` (py_obj_ops_dispatch.c) builds a 2-tuple
`(o, arg)` and calls the (bound) method with BOTH — so `def __lt__(self, other)`
receives `(o /*=self again*/, o, arg)` => 3 args for 2 params => arg-count error
=> NULL. cmp_threeway then took the no-__lt__ fall-through. This is a latent bug
in `py_obj_call_method1` for ALL bound dunders (e.g. py_obj_truediv's
`__truediv__` defer is the same shape and is also latently broken; it just isn't
exercised by the current corpus).

### Specified fix (for the focused session)
In cmp_threeway (C `py_obj_ops_compare.c` + port `_cmp_threeway`), do NOT use
py_obj_call_method1. Instead: `m = py_obj_getattr(a, "__lt__")` (bound); if
non-NULL, `py_obj_call(m, <1-tuple (b)>, py_None)` (one arg, since `m` is bound);
truthy => -1. Then the same with (b, a) => +1; else fall through to address
order. Mind the refcount: py_obj_getattr's method return is documented "borrowed"
(py_class.c:67) — do NOT decref `m`; the 1-tuple is owned (decref it). Verify
`getattr(Ver(1),"__lt__")(Ver(2))` already works (probe getattr_lt.py IDENTICAL),
confirming the bound-method + 1-arg call is correct. Consider ALSO fixing
py_obj_call_method1 itself (call a bound method with `(arg,)`, not `(o, arg)`) —
but that is a shared helper; verify its other callers first.

## Related gaps (same real19 probe, separate)
- `sum(custom_iterator)` (a class with __iter__/__next__) -> `NameError: name
  'sum' is not defined` — the sum() builtin lowering bails for a custom-class
  arg and the fallback is an undefined-name lookup. (numeric_builtin_lowering
  _maybe_emit_sum_literal / _emit_sum_via_iter — the iter path likely doesn't
  accept a user-instance arg.)
- `min()/max()` of custom-__lt__ objects: depends on the cmp_threeway fix above,
  AND `min(...).v` raised AttributeError (min may return a non-element) — verify
  after the cmp_threeway fix lands.

## Update 2026-05-30 — 3rd attempt (py_class_lookup) ALSO ineffective; needs LLDB
A cleaner attempt (avoiding the bound-method refcount question) added a static
`cmp_call_lt(a, b)` to py_obj_ops_compare.c + port `_cmp_call_lt`:
`cls = inst->cls; lt = py_class_lookup(cls, "__lt__")` (borrowed, MRO-walked) +
`py_obj_call(lt, (a, b), <kwargs>)` — `__lt__(self=a, other=b)` at correct arity
(py_obj_call ignores kwargs, so NULL vs py_None is irrelevant). Built into the
archive, but `sorted([Ver(3),Ver(1),Ver(2)])` STILL returned `[3,1,2]` — the
dispatch is reached-but-ineffective or not-reached for reasons not visible from
source. The building blocks verified working in isolation: `getattr(v,"__lt__")(other)`
and `v.__lt__(other)` both return correct bools (probe getattr_lt.py / lt_direct.py
lines 1-3). REVERTED again to keep the comparison primitive (hot path) clean and
the #44-green tree intact (#41–#44 custom-iterator coverage retained, 3 tests pass).
NEXT: this needs LLDB on the compiled stage — set a breakpoint in
`py_obj_cmp_threeway`/`_cmp_call_lt` while running sorted([Ver,...]) to see whether
the instance branch is entered, what `ta`/`cls`/`lt` are, and what `py_obj_call`
returns. Three source-level attempts (py_obj_call_method1; py_class_lookup C+port)
have not cracked it; do not attempt a 4th blind source edit. SEPARATELY, min/max
over a *list* of custom objects (lt_direct.py min(...).v -> AttributeError) is the
min/max-iter int-assumption (marshal_from_object to int), a distinct limitation.

## No.4 FRONTEND re-route — sorted() to the static __lt__ insertion sort [CONFIRMED #54]
### Insight
The 3 prior attempts all tried to teach the *runtime* comparison primitive
(`py_obj_cmp_threeway`, behind `py_obj_sorted`) to dispatch a Python `__lt__`,
and all failed (bound-method double-self; two reached-but-ineffective lookups).
But the FRONTEND already has a *working* static-`__lt__` sort:
`list.sort()` uses `_emit_list_sort_with_dunder_lt` (list_method_lowering.py),
an insertion sort whose compare is `_emit_direct_method_value_call(lt_fn, …)`
— the SAME static dunder resolution the `<` operator uses (and which the doc
confirmed works). So instead of fixing cmp_threeway, route `sorted()` to that
mechanism. This needed no LLDB and no runtime change.
### Code Change (call_expression_lowering.py, `sorted` lowering)
Before the existing `py_obj_sorted` call, when the argument's element class is
known and has a resolvable `__lt__`:
- `elem_hint = self._list_elem_class_hint_for_expr(arg)` (handles a list
  literal of constructor calls) `or self.env_list_elem_class_hint.get(name)`
  (handles a named list variable);
- guard on `self._resolve_method_mro(elem_hint, "__lt__") is not None`;
- `sorted()` is NON-mutating, so sort a COPY:
  `new = py_list_new(0); py_list_extend(new, src_obj)`;
- `self._emit_list_sort_with_dunder_lt(new, elem_hint, elem_ty)`
  (`elem_ty = arg.ty.elem` when `arg.ty` is a `ListType`, else `DynType`);
- `if reverse_const: py_list_reverse(new)`; `return new`.
Int / str / non-class lists fall through to `py_obj_sorted` unchanged.
### CONFIRMED
`/tmp/gap_probe/sorted_lt.py` (list literal + named var + non-mutation check +
`reverse=True` + int/str regression) diffs IDENTICAL to python3.
`tests/python/test_native_sorted_custom_lt.py` 2 passed. FULL three-stage
bootstrap 18 passed / 4 skipped (154s) — call_expression_lowering.py is
bootstrap-critical and stays green. `[3,1,2]` is now `[1,2,3]`.
### Still open (next session)
`min()`/`max()` over a *list* of custom-`__lt__` objects: `_maybe_emit_min_max_iter`
(numeric_builtin_lowering.py) takes the int-accumulator fold for a
`ListType(ClassType)` arg (`_min_max_needs_object_compare` returns False for it),
reading instance pointers as i64 and comparing addresses. The same
`py_obj_min_max`/`cmp_threeway` route is ALSO `__lt__`-blind. The clean fix
mirrors No.4: an object-accumulator fold that compares with
`_emit_direct_method_value_call(__lt__)` and returns the extreme ELEMENT object
(not an i64). New CFG (~40 lines) — kept separate from #54 to protect the
validated sorted() win.

## No.5 FRONTEND object fold — min/max(list-of-custom-__lt__) [CONFIRMED #55]
### Code Change (numeric_builtin_lowering.py)
Two methods + one early branch in `_maybe_emit_min_max_iter`:
- `_min_max_obj_lt_class(arg)` — same element-class detection as #54
  (`_list_elem_class_hint_for_expr` for a list literal, `env_list_elem_class_hint`
  for a named var), guarded by `_resolve_method_mro(cls, "__lt__")`. Returns
  `(class_name, elem_ty)` or `None`. Pure AST/registry inspection, no IR.
- Early branch (before the i64-fold setup and before the str
  `_min_max_needs_object_compare` block): if `_min_max_obj_lt_class` matches AND
  there is no non-`default` kwarg, call the object fold. (A `key=` kwarg falls
  through to the existing `None`-return → libpython path; checked before any IR
  is emitted, so no orphaned IR.)
- `_emit_min_max_obj_lt_fold(expr, name, class_name, elem_ty)` — linear scan
  with an OBJECT accumulator (`_CSTR` alloca): seed from `py_list_get(0)`, then
  for each element compare via `_emit_direct_method_value_call(lt_fn, …)`
  (`min`: replace when `elem < acc`; `max`: when `acc < elem`), truthy-normalise
  the result exactly as the insertion sort does, `select` the element pointer.
  Returns the extreme ELEMENT object. `default=` seeds the empty case; no
  exception wiring (matches the int fold's empty behaviour).
### CONFIRMED
`/tmp/gap_probe/minmax_lt.py` (`min(xs).v`/`max(xs).v`, list-literal + named var)
IDENTICAL; `/tmp/gap_probe/minmax_reg.py` (int i64-fold, str→py_obj_min_max,
single element, custom-iterator #43 NOT hijacked, `default=` empty/non-empty)
IDENTICAL. `tests/python/test_native_min_max_custom_lt.py` 3 passed. FULL
three-stage bootstrap 18 passed / 4 skipped (159s) — numeric_builtin_lowering.py
is bootstrap-critical and stays green. `min(xs).v` is now `1` (was AttributeError).

## Report
gap-a is closed by routing the THREE builtin comparison consumers
(`sorted` #54, `min`/`max` #55) in the FRONTEND to the static-`__lt__` dunder
resolution that the `<` operator and `list.sort()` already used — NOT by fixing
the runtime comparison primitive.

Why the frontend route won over the three runtime attempts (No.1–No.3): the
runtime defect is real and confirmed — `py_instance_getattr_default` returns a
*bound* method, and `py_obj_call_method1(o, name, arg)` calls it with `(o, arg)`,
double-passing `self` → arg-count error → NULL, so `py_obj_cmp_threeway` never
dispatched `__lt__`. Two further attempts to dispatch correctly inside
cmp_threeway (bound 1-arg; `py_class_lookup` + `py_obj_call`) were
reached-but-ineffective for reasons that needed LLDB. Rather than spend that on a
HOT, blast-radius-wide comparison primitive, the frontend already had a proven
static-`__lt__` mechanism for the sibling op; re-routing to it is smaller, needs
no runtime change, and keeps the #40/#44-green tree intact.

Left for a future runtime cleanup (tracked here, not blocking): the
`py_obj_call_method1` bound-method double-self bug is LATENT for *all* bound
dunders (e.g. `py_obj_truediv`'s `__truediv__` defer has the same shape). It is
not exercised by the current corpus because the frontend resolves these dunders
statically. If a path ever needs runtime `cmp_threeway`/`call_method1` dispatch
of a user dunder (e.g. comparing custom objects stored in a `DynType` the
frontend can't type), this must be fixed then — see No.1's "Specified fix".
