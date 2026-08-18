# Investigation: `set(d)` / `frozenset(d)` on a mapping lower to an EMPTY set in pcc-compiled code

## Status

resolved

Predecessor:
[`sequence-builtins-len-getitem-not-iterator-protocol.md`](sequence-builtins-len-getitem-not-iterator-protocol.md)
— the same "len + integer getitem instead of the iterator protocol" pattern,
found from the *generator* side in 2026-05. This file is its mapping-side
sibling: a generator has no length so the loop runs zero times, while a dict
has both a length and a `__getitem__` and therefore fails more quietly — the
loop runs the right number of times and every probe misses. Found
independently, then linked.

Discovered while profiling the Stage2 native-emit worker for
[`pcc1-stage2-emit-throughput-and-memory.md`](pcc1-stage2-emit-throughput-and-memory.md)
(Update No.71). That file owns the performance storyline; this one owns the
correctness bug, because they are two different bugs.

## Problem Description

`pcc/py_frontend/codegen/set_lowering.py::_maybe_emit_set_builtin` lowered
`set(x)` / `frozenset(x)` for a statically known `DictType` argument through
the same generic loop it uses for lists and tuples:

```text
n   = py_obj_len(src)
i   = 0 .. n-1
elt = py_obj_getitem(src, py_int_from_i64(i))
py_set_add(new_set, elt)
```

For a **dict**, `py_obj_getitem(d, i)` is a **key lookup for the integer key
`i`**, not "the i-th key". A string-keyed mapping therefore missed on every
probe and the constructor returned an **empty set with no error raised** —
a silent wrong answer, not a fallback.

The docstring described this as the intended path ("any other iterable
(ListType / TupleType / DictType / DynType) → … iterate via the generic
`py_obj_len` + `py_obj_getitem`"), so the defect read as design.
`list(d)` and `tuple(d)` were already correct: `list_builtin_lowering.py`
has an explicit `DictType` branch that calls `py_dict_keys(d)`. The set
constructor simply never got that branch.

It stayed invisible for two reasons:

1. CPython answers `frozenset(some_dict)` natively, so every host-side test,
   host pcc run and `--emit-llvm` gate agreed with CPython. Only *pcc-compiled
   code* was wrong.
2. The wrong answer is an empty set, which downstream code treats as "nothing
   to do" and skips silently.

### Why this mattered: pcc1 emitted ZERO managed-value reloads

`pcc/backend/self_backend_precise_stackmaps.py::build_function_stack_map_plan`
opens with:

```python
managed_origins, ambiguous_managed = _managed_value_origins(...)
managed_names = frozenset(managed_origins) | ambiguous_managed
live_after = _managed_live_after(func, managed_names)
```

`managed_origins` is a `dict`. Inside pcc1 — which *is* pcc-compiled code —
`frozenset(managed_origins)` evaluated to the empty frozenset, so
`managed_names` collapsed to `ambiguous_managed` alone, `live_after` tracked
(almost) nothing, and `_planned_managed_reloads` hit its
`if not live_values: return ()` on every safepoint.

Managed reloads are what refresh a managed SSA value's spill slot from its
root after a safepoint the collector may have moved objects across — precisely
the stale-pointer shape backend #3 (generational forwarding) and backend #4
(relocating) exist to prevent.

**Scope of that claim, stated precisely.** What was measured is one frozen
module: item 343 went from 0 reload triples under pcc1 to 1200 under host pcc,
and after the fix pcc1 matches host byte-for-byte. `managed_names` is
`frozenset(managed_origins) | ambiguous_managed`, so `ambiguous_managed` alone
could still have produced reloads in functions this measurement never touched.
So: reload planning was **degraded** for every binary pcc1 produced, and
**measured as zero on the module examined** — not "every program had zero",
and not "every module loses 1200".

## Repro

Deterministic, ~40 s, no bootstrap required. The same program compiled by
CPython and by pcc must agree:

```bash
cat > /tmp/setdict.py <<'EOF'
def build() -> dict:
    d: dict = {}
    d["a"] = 1
    d["b"] = 2
    d["c"] = 3
    return d


def main() -> int:
    d = build()
    print("frozenset(d)=" + str(len(frozenset(d))))
    print("set(d)=" + str(len(set(d))))
    print("list(d)=" + str(len(list(d))))
    print("tuple(d)=" + str(len(tuple(d))))
    print("frozenset(d.keys())=" + str(len(frozenset(d.keys()))))
    return 0


main()
EOF
env -u LC_ALL PCC_GC_BACKEND=0 uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on /tmp/setdict.py -o /tmp/setdict
/tmp/setdict
env -u LC_ALL uv run python /tmp/setdict.py
```

Observed before the fix:

```text
pcc-native                       CPython
frozenset(d)=0                   frozenset(d)=3
set(d)=0                         set(d)=3
list(d)=3                        list(d)=3
tuple(d)=3                       tuple(d)=3
frozenset(d.keys())=3            frozenset(d.keys())=3
```

Exactly `set(dict)` and `frozenset(dict)` are wrong. `list`/`tuple` of a dict,
`frozenset(d.keys())`, `set(d.keys())`, `frozenset(list)` and `frozenset(set)`
were all already correct.

### The end-to-end symptom that led here

Emitting one real frozen Stage2 object input with two independent `pcc1`
binaries and with host pcc, from the identical IR file
`build/stage2-current-object-inputs-no62-v1/item_302.ll`:

```bash
export PCC_GC_BACKEND=0 PCC_PYTHON_IR_PASSES=off PYTHONHASHSEED=0
<pcc1> --pcc-self-backend-emit-worker <item_302.ll> out.result out.s ""
```

```text
pcc1 31d6ac3b (pre-No.67)   77453861e652b6a4…  0 reload triples
pcc1 f1526b02 (post-No.67)  77453861e652b6a4…  0 reload triples
host pcc (current source)   c665d81361e0c0d8…  1200 reload triples
```

Two pcc1 generations agree with each other and disagree with the host, so
this was a stable self-host divergence rather than a stale-source artifact.
Every pcc1-only diff line was a `.long <offset>` stack-map entry shifting
because the code got shorter; there were **no** pcc1-only instructions.

## Test [CONFIRMED]

`tests/python/test_native_set_from_dict_keys.py` (4 cases). Confirmed red on
the pre-fix lowering and green after:

```bash
gtimeout 400s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_set_from_dict_keys.py
```

```text
pre-fix   1 failed  (assert '@py_dict_keys' in body)
post-fix  4 passed in 8.25 s
```

The runtime cases deliberately use keys `10` and `20` as well as string keys:
integer keys `0..n-1` are the one shape a positional walk can accidentally get
right, so a regression that only reintroduces positional indexing would still
be caught.

## Proposals

- No.1 route a statically known `DictType` argument through `py_dict_keys` `[CONFIRMED]`
- No.2 give `DynType` the same guarantee at runtime `[pending — separate task]`
- No.3 sweep the rest of the builtin-over-mapping family `[CONFIRMED]`

## No.1 route a statically known `DictType` argument through `py_dict_keys`

### Code Change

`pcc/py_frontend/codegen/set_lowering.py`, in `_maybe_emit_set_builtin`,
before the `py_set_new` allocation:

```python
if expr.args and not isinstance(expr.args[0], (ListExpr, TupleExpr)):
    mapping = expr.args[0]
    if isinstance(mapping.ty, DictType):
        return self._materialize_dict_keys_view_set(mapping)
```

and `DictType` was removed from the generic
`(ListType, TupleType, DictType, SetType, DynType, StrType)` tuple so a
mapping can no longer reach the positional loop at all.

`_materialize_dict_keys_view_set` already existed in the same file — it is
what `d.keys()` set-operators use — and it already does the whole job:
`py_dict_keys` → `py_list_len` / `py_list_get` loop → `py_set_add`, with the
keys list and the marshalled mapping released at the end. Reusing it made the
fix six lines and inherited its ownership handling instead of re-deriving it.
The early return also avoids allocating the empty `py_set_new` the old entry
path created unconditionally.

### CONFIRMED

```bash
gtimeout 400s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_set_methods.py tests/python/test_native_set_update.py \
  tests/python/test_native_set_inplace_update.py \
  tests/python/test_native_set_symmetric_diff.py \
  tests/python/test_native_set_from_dict_keys.py
22 passed in 27.86 s
```

Strict no-libpython closure still passes for the changed file (checked on a
copy outside the package, because `--python-library` on an in-package path
fails with "python_library mode only supports a single Python source",
which is a harness limitation and not a property of the change):

```bash
cp pcc/py_frontend/codegen/set_lowering.py /tmp/set_lowering_cand.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  --python-library --emit-llvm=/tmp/chk_set_cand.ll /tmp/set_lowering_cand.py
# rc=0
```

## No.2 give `DynType` the same guarantee at runtime `[pending]`

Measured under pcc-native today, with a `DynType` local that holds a dict:

```text
                pcc-native   CPython
set(dyn)        0            2
dict(dyn)       0            2
any(dyn)        False        True
list(dyn)       2            2   <- already correct
```

`list()` is correct because its `DynType` arm was already routed through the
**iterator protocol** (`py_obj_iter` / `py_obj_next`, via
`_emit_list_append_via_iter`) by the predecessor investigation. That is the
right fix for every remaining site too, and it now has a second independent
motivation: `py_obj_iter` on a mapping yields its keys, so one dispatch change
closes the generator case and the mapping case together, and it is exactly what
CPython does.

The predecessor already recorded this as its proposal No.2 and gated it on a
**full bootstrap**, because `set()` is bootstrap-critical and a `DynType` with
`__getitem__` but no `__iter__` would change behaviour. That gate has not been
run, so the change is deliberately not made here — a cheaper
`py_obj_type_tag(x) == PY_TYPE_DICT` branch would fix the mapping case alone
while leaving the generator case broken, which is the wrong shape to add to a
shared path. Tracked as `PY-P1-SET-FROM-DYN-MAPPING`.

## Stage1 verification `[CONFIRMED]`

A Stage1 built from the fixed source (`scripts/bootstrap.sh --backend self
--stage 1`, `elapsed_ms=276827`, `rc=0`,
`build/stage1-setdict-fix-v1/pcc1`) emitting the same frozen item 302:

```text
                     assembly SHA-256                    reload triples
new pcc1 (fixed)     c665d81361e0c0d8a30dac4563a73d94…        1200
host pcc             c665d81361e0c0d8a30dac4563a73d94…        1200
old pcc1 (f1526b02)  77453861e652b6a43eb76c149e6e43d2…           0
```

The fixed pcc1's output is **byte-identical to host pcc's**. The divergence is
closed, not merely reduced.

## No.3 sweep the rest of the builtin-over-mapping family `[CONFIRMED]`

The defect is a *pattern*, not one call site: "get the length, then index
positionally" is correct for every sequence and wrong for exactly one type.
One probe program run under CPython and under pcc-native found three more
sites, two of them equally silent:

```text
                        pcc-native (before)   CPython    verdict
set(d) / frozenset(d)   empty set             3          silent wrong
dict(d)                 empty dict            3          silent wrong
any(d) / all(d)         False / False         True/True  silent wrong
zip(d, ...)             KeyError              3 pairs    loud, still wrong
```

Already correct, and left alone: `list(d)`, `tuple(d)`, `sorted(d)`, list /
set / dict comprehensions over `d`, `for k in d`, `enumerate(d)`, `in` /
`not in`, `.keys()` / `.values()` / `.items()`, `str.join(d)`, `reversed`.
`min(d)` / `max(d)` / `sum(d)` are not implemented in the no-libpython
closure and fail with a runtime `NameError` — a capability gap that fails
closed, not this defect.

### Code Change

* `literal_lowering.py::_emit_dict_builtin` — `dict(mapping)` is a shallow
  COPY, so a `DictType` argument delegates to
  `dict_lowering.py::_maybe_emit_dict_builtin`, which already implemented it
  correctly with `py_dict_keys` + `py_dict_get`. That function was
  unreachable for this shape: `call_expression_lowering.py` dispatches
  `name == "dict"` to `_emit_dict_builtin` at line 1090 and returns, long
  before the `_maybe_emit_dict_builtin` call further down.
* `numeric_builtin_lowering.py::_maybe_emit_any_all_literal` — normalise a
  `DictType` source to `py_dict_keys(src)` before the positional walk.
* `tuple_zip_lowering.py::_maybe_emit_zip_builtin` — same normalisation per
  argument.
* `for_normalization_lowering.py::_for_iter_is_zip` — `for a, b in zip(...)`
  is rewritten to `xs[i]`, `ys[i]`, which cannot be normalised in place
  without rebuilding the key list every iteration. Decline the rewrite when
  any source is a mapping and let the (now fixed) generic `zip` builtin
  materialise the pairs once.

Two sites were fixed by *delegating to an existing correct implementation*
rather than by writing a third copy of the loop. Only the two genuinely
one-line normalisations were written inline; a shared helper for two call
sites would be abstraction ahead of need.

### CONFIRMED

Both probe sweeps are byte-identical to CPython's output, and:

```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 -vv --tb=short \
  tests/python/test_native_builtin_zip.py tests/python/test_native_builtin_enumerate.py \
  tests/python/test_native_builtin_next.py tests/python/test_enumerate_value_builtin.py \
  tests/python/test_native_dict_fromkeys.py tests/python/test_native_dict_merge_splat.py \
  tests/python/test_native_dict_pop.py tests/python/test_native_dict_repr.py \
  tests/python/test_native_dict_setdefault_1arg.py tests/python/test_native_dict_update_kwargs.py \
  tests/python/test_dict_literal_temp_release.py tests/python/test_dict_update_temp_release.py \
  tests/python/test_native_comprehension_over_generator.py \
  tests/python/test_native_comprehension_scope_no_libpython.py \
  tests/python/test_native_set_from_dict_keys.py
48 passed in 222.81 s
```

Strict no-libpython closure rc=0 for all four newly changed files.
`tests/python/test_native_set_from_dict_keys.py::test_mapping_builtin_family_matches_cpython`
covers `dict()`, `any()`, `all()` and both `zip()` forms.

**The Stage1 pcc1 measured above predates this sweep** — it contains only the
`set`/`frozenset` fix. A pcc1 rebuilt now would additionally pick up whatever
`dict()`/`any()`/`all()`/`zip()`-over-a-mapping the compiler's own source
uses.

## Report

Landed: No.1 and No.3. The generic positional walk is correct for every sequence and
wrong for exactly one type, and the mapping branch that `list()` already had
is the fix.

Follow-ups this opens, none of which are claimed here:

1. **`pcc2`/`pcc3` bytes will change**, so `tests/bootstrap_gate_baseline.json`
   must be re-established from a green chain, not edited to match. Stage1 is
   confirmed above; Stage2/Stage3 have not been run.
2. **Expect Stage2 to get slower, not faster.** pcc1 was skipping real work.
   The 686.160 s baseline was measured with the bug present, so it is not a
   like-for-like predecessor of the next number.
3. **The five-GC matrix is the gate that matters here**, especially backends
   #3 and #4: this fix restores the reloads those collectors depend on, so a
   green GC3/GC4 bootstrap after the fix is the real proof.
4. `docs/investigations/INDEX.md` regenerated.

## Update (2026-08-27): the family's error paths — three silent runtime contracts and the fail-closed fix [CONFIRMED, fixed at the frontend]

The external reviewer's P1 (exception-path owned-ref leaks in the swept
walks) held, and chasing it surfaced that the walks' error EDGES mostly did
not exist at all, because three runtime helpers fail silently rather than
raising (all confirmed by reading the C source):

```text
py_obj_len(non-sized)        returns 0, no raise        (py_obj_ops_dispatch.c)
py_dict_keys(non-dict)       returns NULL, no raise     (py_dict.c)
py_obj_getitem(unsupported)  returns NULL, no raise     (default arm)
py_obj_getitem(dict, k)      calls py_dict_get = the SILENT variant;
                             the raising KeyError variant is py_dict_getitem
                             and only the subscript path uses it
```

Consequences before the fix (all reproduced in
tests/python/test_native_container_builtin_error_paths.py, red-first):
`dict(dyn-non-mapping)` silently `{}`; `dict(dyn-dict)` silently corrupt
(positional pairs walk over a mapping); `any(dyn-dict)` silently False while
looping over NULL elements; `zip(_, dyn-dict)` built NULL-slotted tuples.
A dyn-typed local with an initializer is NARROWED by type inference, so these
only fire through a genuine dyn boundary (object-typed param / return) — the
tests route values through an `object` param for exactly that reason.

Fixes (frontend only; runtime contracts untouched):

1. `literal_lowering._emit_dict_builtin`: runtime tag dispatch for dyn
   sources (dict -> py_dict_update shallow copy, CPython-correct;
   list/tuple -> pairs walk; else raise TypeError), a static-type gate that
   raises for non-List/Tuple/Dict/Dyn sources instead of walking them, a
   NULL-pair guard in the dyn pairs walk, and — normal-path — the pairs walk
   released NONE of its six per-iteration owned refs (pair, key, value, two
   pair-index boxes, loop index box); py_dict_set retains, so dict(pairs)
   leaked 3+ heap refs per entry even on success.
2. `dict_lowering` copy walk: release_on_error wired into both existing err
   checks (keys view + loop temps); dyn arm gets a NULL guard after
   py_dict_keys raising TypeError.
3. `numeric_builtin_lowering` any/all walk and `tuple_zip_lowering` zip
   walk: err checks + elem NULL guards on DYN sources only, releasing every
   live owned temp (keys views, elem, item, idx boxes, result list) on the
   edge.  Static sources gain NO checks — a pinned cost-guard test asserts
   any(static dict) emits zero py_err_occurred, so pcc1's own hot shapes
   keep byte-stable IR.

Claim boundary: any/all/zip over a DYN-HELD mapping now fail closed with
TypeError; CPython iterates mappings by key there, and that behavior belongs
to the iterator-protocol row (PY-P1-SET-FROM-DYN-MAPPING).  dict(dyn-dict)
IS CPython-correct now (py_dict_update copy).  Making the three silent
runtime contracts raise is a separate runtime C+port slice
(RT-P2-SILENT-NULL-RUNTIME-CONTRACTS): py_obj_len's silent 0 still makes
any(dyn-int) return False rather than TypeError.

Gates: 12/12 new error-path tests; 27 family/ownership tests; 41
test_py_multi_file_compile; closure check green on all four edited files
(equal-arm /tmp isolation — an in-repo path triggers same-package auto-close
and reports a MULTI-SOURCE artifact failure, not a closure verdict).
Stage1/bootstrap evidence deferred: the worktree carries the in-flight
Indexed Function Kernel restructure across ~16 backend files, so a stage1
run now cannot attribute a failure; the row holds DONE_WEAK until that lane
is source-stable.

## Update (2026-08-27, same slice): zip(strict=True) now enforces ValueError [CONFIRMED fixed]

The reviewer's P2 — both zip paths silently truncated under ``strict=True`` —
is closed at the single owner: the ``zip`` builtin evaluates the ``strict``
kwarg (``_emit_condition_value``, so literals fold and runtime expressions
work), compares every length against the first, and raises
``ValueError("zip() arguments have different lengths (strict=True)")``
before allocating the result, releasing the owned keys views on that edge.
The for-loop rewrite (``_for_iter_is_zip``) now DECLINES any kwarg'd zip and
routes it to the builtin — the indexed-loop form has nowhere to put the
enforcement, and dual implementations is how the two paths diverged last
time.  Red-first tests in
tests/python/test_native_container_builtin_error_paths.py (16/16), dedicated
zip files 4/4, family+frontend 58/58.
