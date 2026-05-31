# Investigation: key= callables via inlining (no first-class functions, no-libpython)

## Status
active — `sorted`/`min`/`max(key=<simple lambda>)` RESOLVED 2026-05-30 for
attr-chain, int-subscript, tuple-of-those (multi-key sort), AND no-arg str-method
keys: #56 (sorted scalar), #57 (min/max scalar), #58 (tuple key, all three; the
runtime `py_obj_lt` orders tuples lexicographically), #59 (str-method key e.g.
`lambda s: s.lower()` → `py_str_<m>`, gated on str elements — case-insensitive
sort), #60 (NEGATED key `-kv[1]` → `0 - key` via `py_obj_sub`, descending sort;
composes inside a tuple key for mixed-sign `(-count, name)`). Still falling
back: named-fn key (`key=str.lower`/`key=func` — a non-Lambda kwarg shape),
str-subscript key, method-call key on non-str elements, other arithmetic key
bodies (only unary `-` of attr/index is handled). `map`/`filter` over a simple
lambda (No.3) remains.

## Problem Description
no-libpython pcc (`--backend self --python-libpython=off`) has no general
first-class-function boxing: a function/lambda used as a *value* (passed to a
builtin, stored in a variable) forces the libpython fallback, which under
`=off` is a hard `PCC-PY-COMPILE-001` error. This blocks the whole
`key=`/callable-argument builtin family:

```
sorted(xs, key=lambda x: x.attr)   # PyPipelineError: generated IR still calls py_cpy_*
min(xs, key=...) / max(xs, key=...)
map(f, xs) / filter(f, xs)
```

`_maybe_emit_simple_lambda` (lambda_helpers_lowering.py) already recognised the
simple shapes (`x.a.b` → attrgetter, `x[N]` → itemgetter, `x.m()` →
methodcaller) but lowered them to **CPython** callables (tagged in
`_cpy_values`), i.e. still libpython.

## Repro
```bash
printf 'def main():\n    print(sorted([3,1,2], key=lambda x: -x))\nmain()\n' > /tmp/k.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/k.py -o /tmp/k_bin
# Error: PCC-PY-COMPILE-001 ... generated IR still calls py_cpy_* helpers
```

## Key insight
The no-libpython answer to "a callable passed as a value" is usually **inline
the callable's body at the use site**, not box a function pointer. The call
site here is a known builtin (`sorted`) and the callable is a statically
resolvable shape, so the key extraction can be emitted inline — exactly the
philosophy that closed gap-a (#54/#55 inlined a user `__lt__`; here we inline a
key lambda). No runtime change, no fn boxing, contained blast radius.

## Proposals
- No.1 FRONTEND: inline a simple attr/index key lambda into a key-comparing sort  [CONFIRMED #56]
- No.2 min/max(key=<simple lambda>) via the same inline key in an object fold     [CONFIRMED #57]
- No.3 map()/filter() over a simple lambda                                        [pending — harder, returns iterators]

## No.1 sorted(xs, key=<simple lambda>) [CONFIRMED #56]
### Code Change
list_method_lowering.py:
- `_sorted_key_spec_from_lambda(key_lambda)` → `('attr', (parts,…))` for a
  single-param `lambda x: x.a.b`, `('index', N)` for `lambda x: x[N]` (int
  literal), else `None`.
- `_emit_key_of(obj, key_spec)` → inline `py_obj_getattr` chain /
  `py_obj_getitem(py_int_from_i64(N))`, returning the key PyObject*.
- `_emit_sorted_with_key_lambda(expr, key_lambda, reverse_const)` → copy the
  list (`py_list_new`+`py_list_extend`; sorted() is non-mutating), then
  `_emit_list_insertion_sort_by_key`, then optional `py_list_reverse`.
- `_emit_list_insertion_sort_by_key(recv, key_spec)` → a FRESH insertion sort
  (mirrors `_emit_list_sort_with_dunder_lt`'s CFG so the bootstrap-critical
  original is untouched) whose per-pair compare is
  `py_obj_lt(key(cur), key(prev))` (correct for int/str/float keys).

call_expression_lowering.py `sorted` handler: capture `key=<Lambda>`; if simple
→ inline path; non-simple lambda / non-lambda key (`key=str.lower`) / any other
kwarg → fall through to the libpython path (must NOT run the plain
`py_obj_sorted`, which would silently ignore the key).
### CONFIRMED
`/tmp/gap_probe/sortkey.py` (int-attr, str-attr, +reverse, index-key over
tuples, plain int/str regressions) IDENTICAL to python3.
`tests/python/test_native_sorted_key_lambda.py` 3 passed. FULL three-stage
bootstrap 18 passed / 4 skipped (159s) — call_expression_lowering.py and
list_method_lowering.py are bootstrap-critical and stay green.

### Scope / not yet covered (fall back today)
- str-subscript key `x['k']`, method-call key `x.lower()`, arithmetic key `-x`,
  tuple key `(x.a, x.b)`.
- named-function key `key=func`, `key=str.lower` (need first-class fn / bound
  builtin as value).
- a custom-`__lt__` *key object* would hit the same cmp_threeway limitation as
  gap-a, but sort keys are virtually always primitives.

## No.2 min/max(key=<simple lambda>) [CONFIRMED #57]
### Code Change (numeric_builtin_lowering.py)
- `_emit_min_max_by_key_fold(expr, name, key_spec)` — materialise the arg to a
  list via `_emit_list_append_via_iter` (handles list/tuple/generator/range),
  then an object-accumulator linear scan (the #55 shape) tracking the extreme
  ELEMENT, comparing `py_obj_lt(key(lhs), key(rhs))` where the keys are
  inline-extracted by `_emit_key_of` (reused from #56). Strict `<` keeps the
  FIRST extreme element (CPython stability). `default=` seeds the empty case.
- Early branch in `_maybe_emit_min_max_iter` (before the #55 obj-`__lt__` route
  — `key=` takes precedence, Python ignores `__lt__` when a key is given): if
  any `key=` kwarg, accept only `key=<Lambda>` (+ optional `default=`) that
  `_sorted_key_spec_from_lambda` recognises → key fold; otherwise `return None`
  → libpython (must NOT run the key-blind folds below).
### CONFIRMED
`/tmp/gap_probe/minmax_key.py` (attr key min/max, index key min/max) IDENTICAL;
`/tmp/gap_probe/minmax_key_reg.py` (tie → first extreme kept, single element,
empty `default=`, and the no-key regressions: int fold / str py_obj_min_max /
#55 custom-`__lt__`) IDENTICAL. `tests/python/test_native_min_max_key_lambda.py`
3 passed. FULL three-stage bootstrap 18 passed / 4 skipped (168s) —
numeric_builtin_lowering.py is bootstrap-critical and stays green.
### Scope (same as #56)
attr-chain + int-subscript key lambdas only. str-subscript / method / arithmetic
/ tuple / named-function keys still fall back.

## No.3 map()/filter() over a simple lambda [pending]
Harder: they produce iterators. A bounded first cut is `list(map(lambda x:
x.attr, xs))` / `[... for ...]`-style materialisation, inlining the body per
element. Investigate after No.2.
