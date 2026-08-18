# pcc1 emitted zero managed-value reloads; `frozenset(dict)` was the cause

Date: 2026-08-27
Tasks: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION` (where it was found),
`PY-P0-SET-FROM-MAPPING-EMPTY` (where the fix lives)
Claim level: focused frontend-lowering correctness fix with a confirmed-red
regression, plus one Stage1 build proving host/pcc1 parity on a frozen item.
**No Stage2, Stage3, fixed-point or five-GC claim is made here.**

## What was measured

Re-profiling the Stage2 native-emit worker (item 302, current-source pcc1
`f1526b0262cd17fe…`, 7452 on-CPU samples) put 45.1% of the worker in
`build_function_stack_map_plan` and 13.0% in `py_dict_get` called directly
from it. Establishing the host baseline for the resulting A/B showed host pcc
and pcc1 do not produce the same assembly from the same IR:

```text
input   build/stage2-current-object-inputs-no62-v1/item_302.ll
env     PCC_GC_BACKEND=0 PCC_PYTHON_IR_PASSES=off PYTHONHASHSEED=0

pcc1 31d6ac3b (pre-No.67)   77453861e652b6a4…   0 reload triples
pcc1 f1526b02 (post-No.67)  77453861e652b6a4…   0 reload triples
host pcc (current source)   c665d81361e0c0d8…   1200 reload triples
```

Two pcc1 generations agree with each other and disagree with the host, so this
is a stable self-host divergence rather than a stale-source artifact. Every
pcc1-only diff line is a `.long <offset>` stack-map entry shifting because the
code is shorter; there are no pcc1-only instructions.

## Cause

`set(x)` / `frozenset(x)` for a statically known `DictType` argument used the
generic `py_obj_len` + `py_obj_getitem(src, i)` walk. For a dict that indexing
is a **key lookup for the integer key `i`**, so a string-keyed mapping missed
every probe and the constructor returned an **empty set with no error**.

Inside pcc1 that made
`managed_names = frozenset(managed_origins) | ambiguous_managed` collapse to
`ambiguous_managed`, so `_managed_live_after` tracked nothing and
`_planned_managed_reloads` returned `()` at every safepoint. Managed reloads
refresh a managed SSA value's spill slot from its root after a safepoint the
collector may have moved objects across — the exact thing backend #3
(generational forwarding) and backend #4 (relocating) depend on.

CPython answers `frozenset(some_dict)` natively, so every host-side test and
`--emit-llvm` gate agreed with CPython. Only pcc-compiled code was wrong.

## Fix

`pcc/py_frontend/codegen/set_lowering.py::_maybe_emit_set_builtin` routes a
`DictType` argument to the file's existing `_materialize_dict_keys_view_set`
helper (`py_dict_keys` → list walk → `py_set_add`), and `DictType` was removed
from the generic positional-walk tuple so a mapping cannot reach it. Six
lines, reusing the helper `d.keys()` set-operators already use, so the
ownership/release handling is inherited rather than re-derived.

`list(d)` and `tuple(d)` already had this branch in
`list_builtin_lowering.py`; the set constructor simply never got it.

## Gates run

```text
gtimeout 400s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_set_from_dict_keys.py
  pre-fix   1 failed   (assert '@py_dict_keys' in body)
  post-fix  4 passed in 8.25 s

gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_set_methods.py tests/python/test_native_set_update.py \
  tests/python/test_native_set_inplace_update.py \
  tests/python/test_native_set_symmetric_diff.py \
  tests/python/test_native_set_from_dict_keys.py
  22 passed in 27.86 s

strict no-libpython closure, changed file, rc=0
  (run on a copy outside the package: --python-library on an in-package path
   fails with "python_library mode only supports a single Python source",
   a harness limitation, not a property of the change)
```

## Stage1 on the fixed source restored host/pcc1 parity `[CONFIRMED]`

```bash
gtimeout 900s env -u LC_ALL \
  PCC_BOOTSTRAP_OUT_DIR=$PWD/build/stage1-setdict-fix-v1 \
  PCC_BOOTSTRAP_PROFILE_DIR=$PWD/build/stage1-setdict-fix-v1/profile \
  PCC_GC_BACKEND=0 scripts/bootstrap.sh --backend self --stage 1
# PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=276827 rc=0
```

Emitting the same frozen item 302 with that pcc1:

```text
                     assembly SHA-256                    reload triples
new pcc1 (fixed)     c665d81361e0c0d8a30dac4563a73d94…        1200
host pcc             c665d81361e0c0d8a30dac4563a73d94…        1200
old pcc1 (f1526b02)  77453861e652b6a43eb76c149e6e43d2…           0
```

The fixed pcc1's output is **byte-identical to host pcc's**. This is stronger
than "the fix works": a real host-versus-pcc1 divergence closed, so the two
compiler generations now agree on this input rather than merely both running.

## The same defect existed at three more call sites `[CONFIRMED]`

"Get the length, then index positionally" is a *pattern*, not one call site.
One probe run under CPython and under pcc-native found three more:

```text
                        pcc-native (before)   CPython     verdict
set(d) / frozenset(d)   empty set             3           silent wrong
dict(d)                 empty dict            3           silent wrong
any(d) / all(d)         False / False         True/True   silent wrong
zip(d, ...)             KeyError              3 pairs      loud, still wrong
```

Already correct and untouched: `list(d)`, `tuple(d)`, `sorted(d)`, list/set/
dict comprehensions, `for k in d`, `enumerate(d)`, `in`, `.keys()/.values()/
.items()`, `str.join(d)`. `min(d)`/`max(d)`/`sum(d)` are unimplemented in the
no-libpython closure and fail closed with a `NameError` — a capability gap,
not this defect.

Fixed in `literal_lowering.py` (delegates `dict(mapping)` to the correct but
previously unreachable `dict_lowering.py::_maybe_emit_dict_builtin`),
`numeric_builtin_lowering.py`, `tuple_zip_lowering.py`, and
`for_normalization_lowering.py` (declines the `for … in zip(…)` index rewrite
for mapping sources rather than rebuilding the key list per iteration).

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 -vv --tb=short \
  <15 zip/enumerate/dict/comprehension/set files>
48 passed in 222.81 s

strict no-libpython closure, all four newly changed files: rc=0
```

Both probe sweeps now produce output byte-identical to CPython's.

**The Stage1 pcc1 measured above predates this sweep** — it carries only the
`set`/`frozenset` fix.

## Review findings acted on, and what the first Stage2 actually showed

An independent review of the sweep found three blocking defects in it. All
three are confirmed and fixed; the review was right on each.

**Owned-reference leaks in the normalisation itself.** `py_dict_keys`,
`py_list_get` and `py_dict_get` all return NEW refs (`py_runtime.h`), and
`py_dict_set` retains what it stores rather than stealing it — verified in
`_dict_insert_rooted_slot`, which commits through
`pcc_gc_store_ptr_plan_commit_locked` (balanced: increfs the new value), and in
the C mirror's `py_incref(key)` / `py_incref(value)`. The first cut leaked one
keys list per `any`/`all`, one per `zip` argument, and for `dict(mapping)` a
keys list plus one key and one value **per entry**. Fixed: released at the
single join in `numeric_builtin_lowering.py`, at the single exit in
`tuple_zip_lowering.py`, and per entry plus at the exit in `dict_lowering.py`.
Output-only tests cannot see a leak, so six IR-shape ownership assertions were
added and **confirmed red first** — 5 failed with the releases neutered, 13
pass with them. `frozenset(d)`/`set(d)` stayed green in the red check, which is
correct: they route through `_materialize_dict_keys_view_set`, which already
released.

**`zip(mapping, ..., strict=True)` reached no lowering.** The for-loop rewrite
accepts `strict` and drops it, and now declines mapping sources; the generic
zip builtin rejected every kwarg. The combination died at runtime with
`NameError: name 'zip' is not defined`. The builtin now accepts and drops
`strict` exactly as the rewrite already did. A regression covers both forms.
Known semantic gap, pre-existing and now shared by both paths and written down
rather than papered over: CPython's `strict=True` raises `ValueError` on
unequal lengths, while both pcc paths truncate to the shortest input.

**Task routing let the optimisation row outrank the correctness row.** The PERF
Stage2 row had `depends_on: []`, so `resume` selected it while
`PY-P0-SET-FROM-MAPPING-EMPTY` was still `IN_PROGRESS`. It now depends on the
correctness rows, and `resume` selects `PY-P0-SET-FROM-MAPPING-EMPTY`.

### The first Stage2 on the fixed source FAILED

```text
PCC_BOOTSTRAP_STAGE_FAILED stage=2 elapsed_ms=655403 rc=1
19 x  self precise stack-map analysis: stale managed SSA value ... outlives its active root
18 of those 19 are the method receiver `self`
```

Restoring `frozenset(mapping)` populated `managed_names`, which turned on a
`_planned_managed_reloads` validation that had never executed. Stage1 passed on
the same source with host pcc (271.721 s, rc=0), so this is a second
host-versus-pcc1 divergence, not a property of the fix. Own investigation and
`SELF-P0-STALE-MANAGED-SELF-OUTLIVES-ROOT`:
`docs/investigations/pcc1-stage2-stale-managed-self-outlives-root.md`.

**That run is diagnostic data, not a baseline.** It failed, so it has no
comparable wall time, and it was launched without an outer `gtimeout`, which
violates the repository's hard timeout rule — recorded rather than glossed. No
stray children survived; `ps` was clean after exit.

## Honest open boundary

1. **686.160 s is retired as a baseline.** It was measured with a compiler
   omitting a real phase. It stays in the record as history; it must not be
   quoted as the predecessor of any post-fix number.
2. **Stage2 should be expected to get slower, in direction only.** How much,
   and whether the added time is necessary reload work or an inefficient
   implementation of it, has to be shown by a new profile. Slower is not
   automatically legitimate.
   **The "1200 reloads" number is item 302 only and does not generalise.**
   `managed_names = frozenset(managed_origins) | ambiguous_managed`, so even
   with the first set wrongly empty, `ambiguous_managed` could still produce
   reloads in other functions. The honest claim is: for this frozen item the
   count went 0 -> 1200 and the assembly became byte-identical to host pcc's.
   Nothing here measures any other module, and nothing here says every program
   had zero.
3. **Only Stage1 ran.** No Stage2/Stage3 and no five-GC matrix. `pcc2`/`pcc3`
   bytes will change, so `tests/bootstrap_gate_baseline.json` has to be
   re-established from a green chain — never edited to match an output.
4. **The five-GC matrix is the gate that matters**, particularly backends #3
   and #4, since this restores the reloads those collectors rely on.
5. `DynType` holding a dict at runtime still takes the positional walk and
   still yields an empty set. The fix shape is known
   (`py_obj_type_tag(x) == PY_TYPE_DICT` selects `py_dict_keys`) but it adds a
   runtime branch to a shared lowering path and gets its own row, proposal and
   verdict.

Investigation:
`docs/investigations/set-and-frozenset-of-dict-lower-to-empty.md`
Stage2 storyline: `pcc1-stage2-emit-throughput-and-memory.md` Update No.71.
