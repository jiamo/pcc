# py_gen raise-owner slice — 2026-08-25

## Claim

Every raise site in `py_gen.c` and `py_gen.py` that raises a **freshly created**
exception now uses `py_raise_owned`, so it leaves exactly one pending
reference.  Two sites that raise a **borrowed** exception were deliberately left
alone.

This is the first file of the sweep scoped by
`PY-P1-STRICT-RAISE-SITE-EXCEPTION-OWNER-PARITY`.  The remaining C files are
unchanged.  Not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## Gate

`tests/python/test_raise_site_exception_owner.py` reads the refcount of the
object left in the thread-local exception slot after a raise.  A correct caller
leaves 1 — the slot's own reference.

RED before:

```text
pending refcounts: control=1 gen_state=2 gen_set_state=2 (expected 1 1 1)
```

`control` is `py_dict_getitem`, which already released; the two generator paths
each leaked one reference.

GREEN after, both runtime tiers: `2 passed in 125.87s`.

Neighbors: `tests/python/test_async_await.py` and
`tests/python/test_gc_coroutine_roots.py` -> `21 passed in 74.39s`.
`py_gen.c` compiles clean under `-Wall -Wextra`.

## What changed, with counts

```text
py_gen.c   inline py_raise(py_exc_new(...))   7 -> 0
           py_raise_owned(py_exc_new(...))    3 -> 10
py_gen.py  fresh-exception py_raise sites    11 converted
           borrowed sites left alone          2
```

Every replacement asserted its expected before/after count, so a pattern that
silently matched too much or too little would have failed rather than landing.

## The two sites left alone — the hazard, concretely

The task warned that a blind sweep turns a leak into a double free.  Both
instances are in this one file:

- **`py_gen.py:272`** — `py_raise(exc)` where `exc` is a **parameter** of
  `py_gen_throw`.  The caller owns it; releasing here would free someone else's
  exception.
- **`py_gen.py:404`** — `py_raise(saved)` where `saved` is loaded from a rooted
  slot, and the next lines clear the root and unregister the handle.  The root
  owns the reference.

Both would have been converted by a naive `py_raise(` -> `py_raise_owned(`
substitution.

## Method that made this safe

Sites were classified name-agnostically rather than by grepping for
`py_decref(exc)`:

1. find every `py_raise(X)` that is not already `py_raise_owned`;
2. drop those with `py_decref(X)` within the next two lines, for **that
   variable**, whatever it is named;
3. of the rest, convert only those where `X = py_exc_new(...)` appears in the
   preceding few lines with no intervening rebinding of `X`;
4. inspect the remainder by hand.

Step 2 is what my earlier scan got wrong — it looked for the literal
`py_decref(exc)`, so `py_gen.py:185`'s correct `py_raise(stop); py_decref(stop)`
was miscounted as a leak.  Step 3 is what protects the two borrowed sites.

## Second file: py_bytes, and a mirror lesson

`py_bytes.c` was the clean case — all 40 of its `py_raise(` calls were the
inline `py_exc_new` form and none used a variable, so the whole file was the
safe mechanical transformation with nothing to hand-inspect.

Adding a `py_bytes_fromhex` case to the gate then caught something the C change
alone would have hidden: the C arm passed while the **strict arm still reported
`bytes_fromhex=2`**.  There is no `py_bytes.py`; the strict tier implements
these entry points in `py_obj_stubs.py`, which had its own 33 inline sites —
27 single-line and 6 spanning several lines.  Converting the C file without its
strict counterpart would have left the leak in the tier that actually ships the
pcc-Python runtime.

Both inline forms were converted with asserted counts, and the multi-line form
was matched structurally (`py_raise(` alone on a line, next non-blank line
starting `py_exc_new(`) rather than by a text pattern that could drift.

Counts:

```text
py_bytes.c        inline 40 -> 0,  py_raise_owned 0 -> 40
py_obj_stubs.py   inline 33 -> 0  (27 single-line, 6 multi-line)
```

Gate after both: `2 passed` on both tiers, with
`control=1 gen_state=1 gen_set_state=1 bytes_fromhex=1`.
Neighbors: `test_native_bytes_str_encode.py` + `test_async_await.py` ->
`18 passed in 70.72s`.

## Third and fourth files: py_format and py_list

Both C files were the clean case again — every variable-form site in them was
already releasing, so only the inline form needed converting.  Their strict
counterparts had to be located rather than guessed: `py_format.c` maps to
`py_format_runtime.py`, not `py_format.py`.

`py_list.py` was the one file where the variable form *did* leak: lines 2012
and 2076 raise a freshly created `ValueError` for `list.remove` with no release,
while their C counterparts `py_list_remove` and `py_list_index` already did
`py_raise(exc); if (exc) py_decref(exc);`.  Those two were verified against C
before conversion.

```text
py_format.c            inline 37 -> 0
py_list.c              inline 24 -> 0   (4 variable sites already correct)
py_list.py             inline 25 -> 0   + 2 leaking variable sites converted
py_format_runtime.py   inline 21 -> 0   (1 variable site already correct)
```

A `py_list_remove` case was added to the gate.  Both tiers: `2 passed`, with
`control=1 gen_state=1 gen_set_state=1 bytes_fromhex=1 list_remove=1`.
Neighbors: list methods + list-pop-raise `16 passed`, str methods `12 passed`.

## The method became a tool

After six files by hand the classification was stable, so it moved into
`scripts/raise_owner_audit.py` with `report` / `convert` / `counts` modes.  A
wrong classifier here produces double frees, so its boundaries are pinned by
`tests/python/test_raise_owner_audit_tool.py` (7 cases) using the four shapes
that actually occur in this runtime: inline single-line, inline multi-line, a
fresh variable, a released variable under a *different* name, a borrowed
parameter (`py_gen_throw`), and a rooted value.  It also asserts that `convert`
leaves a borrowed site untouched and adds the `extern` declaration only to
non-C files.

The tool reproduced the hand analysis on the next three C files exactly before
it was used to change anything.

## Files seven through thirteen

```text
py_re_engine_obj.c        21 inline
py_obj_ops_dispatch.c     15 inline
py_coroutine.c            12 inline
py_re_engine_runtime.py    6 inline +  9 multi-line
py_re.py                   0 inline +  3 multi-line
py_coroutine.py            4 fresh variable sites
py_obj_ops_dispatch.py    12 inline
```

Strict counterparts were again found by exported symbol rather than base name:
`py_re_engine_obj.c` maps to `py_re_engine_runtime.py` **and** `py_re.py`, and
`py_coroutine.py` had no inline form at all — only variable sites, all four
freshly created.

Gate after: `9 passed` (refcount differential on both tiers plus the tool
contract).  Neighbors: async/await + binary dunder dispatch `19 passed`.

## Sweep complete

```text
C runtime      inline py_raise(py_exc_new(...)) sites remaining: 0
strict ports   160 inline + 5 multi-line + 4 variable converted
```

All 250 C sites and 169 strict sites are done.  **Three sites are
deliberately left, and they are correct as they stand**:

```text
py_capi_shim.c          PyErr_Restore  py_raise(value)
py_capi_shim_oracle.c   PyErr_Restore  py_raise(value)
py_capi_exc_runtime.py  PyErr_Restore  py_raise(value)
```

The tool refused to convert them because `value` is a parameter.  Verifying the
contract end to end shows refusing was right:

- `PyErr_Fetch` increfs both `type` and `value` and hands out **owned**
  references, matching CPython.
- Both in-tree callers use the `PyErr_Fetch` -> work -> `PyErr_Restore` pattern
  and never decref afterwards, so `PyErr_Restore` must consume what it is given.
- `PyErr_Restore` already does exactly that: `py_raise(value)` increfs for the
  TLS slot, and the explicit `py_decref(type); py_decref(value);
  py_decref(traceback);` at the end consumes the stolen references.

`py_raise` plus that decref is balanced.  Converting these to `py_raise_owned`
would release one reference too many — a double free.

**Correction to an earlier note in this file.**  An earlier revision said
`py_raise_owned` was "probably correct" at these sites and claimed the strict
mirror "decrefs `type` but not `value`, an asymmetry supporting the stealing
reading".  That was wrong: I read a snippet that happened to end at
`py_decref(type)` and inferred an asymmetry from the truncation.  Both mirrors
decref all three.  The stealing reading was right; the conclusion drawn from it
was not.

The contract is now a gate rather than an argument.  The refcount differential
performs a `PyErr_Fetch` / `PyErr_Restore` round trip and asserts the pending
exception's refcount is unchanged at 1 on both sides, so anyone who later
"fixes" these three sites will see it fail.

Two other sites remain untouched for the reasons established earlier:
`py_gen.py:275` (borrowed parameter of `py_gen_throw`) and `py_gen.py:407`
(value owned by a root).  The tool independently flagged exactly those two,
which cross-checks the earlier hand analysis.

## A defect in the tool itself

Running the sweep found a bug in `raise_owner_audit.py`: it added the
`py_raise_owned` extern declaration even to files where it converted nothing.
`py_exc_tls.py` has no sites to convert *and* no `py_raise` extern to anchor to
— it **defines** `py_raise` — so the tool aborted there.  Aborting was the right
behaviour, but the declaration should never have been attempted.  Fixed to
declare only when something actually changed, and pinned by an eighth case in
`tests/python/test_raise_owner_audit_tool.py`.

## Final gates

```text
refcount differential + tool contract        10 passed
dict/set/list/str method parity              49 passed
async/await, truthy-raise, list-pop-raise,
dict missing-key owner parity                21 passed
```

## Remaining

Nothing in this sweep.  The only outstanding item is the five-GC gate: this
change altered release behaviour on every error path in the runtime, so
`PCC_GC_BACKEND=0..4` must be exercised before the row can be `DONE_STRONG`.
That is a long run and has not been started.
(`py_protocol.c` 10, `py_class.c` 9, `py_int_bytes.c` 8, `py_class_attrs.c` 8,
`pcc_threads.c` 7, `py_str_accessors.c` 6, `py_pickle_copy.c` 6, ...), plus
their strict mirrors.  Use `scripts/raise_owner_audit.py`.  Apply the same
method and the same per-file refcount gate — and locate each file's strict
counterpart explicitly, since it is not always the same base name.

## Nonclaims

- Only `py_gen` was changed and measured.  The other files still leak.
- The gate exercises two generator entry points, not all ten converted C sites.
- No bootstrap, stage, fixed-point or five-GC gate was run.
