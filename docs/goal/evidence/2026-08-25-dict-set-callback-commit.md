# Dict set/update callback commit slice — 2026-08-25

## Claim

The C and strict pcc-Python `dict[k] = v` paths now share one rooted,
restartable probe with the already-proven dict read path.  Owner, key and
value are registered as updateable Backend-3/4 scheduler roots before user
`__hash__`; the table snapshot (indices/entries/capacity/entries_used) and the
current probe slot are revalidated after every callback, and the probe restarts
from the hash origin on drift.  A fresh insert publishes key, value,
`entries_used`, `indices[slot]` and `size` inside one graph-locked transaction
built from two store plans; a replacement publishes only the value slot and
keeps the original stored key object.  Both forms finish their plans after the
lock is dropped, so a displaced value's finalizer can only observe a fully
committed table.

This proves only the dict set/update slice of
`GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`.  **Dict delete remains open** and
is a separate proposal, as the handoff requires.  It is not Stage1, Stage2,
fixed-point, five-GC, or performance evidence.

## What was wrong

`py_dict_set` computed `PyDictObject *d`, then called `py_obj_hash(key)` and
`py_dict_lookup()` — which itself invokes `py_obj_eq` — while holding only raw
`d` / `entries` / `slot` locals.  A Backend-4 relocation inside either callback
left every one of those pointers pointing at a forwarding shell, so the insert
or replacement committed into the dead copy.  The strict mirror had the same
shape.

## Localization, not guesswork

The first strict run of the slice failed the existing Proposal No.19 read probe
with exit `7` in the `pcc_python` mirror while `c` passed.  Rather than
hypothesizing, a minimal two-mirror probe printed the intermediate state:

```text
[c]          rc=0
[pcc_python] before len=0 used=0
             after  len=0 used=0 err=1
             got=0x0 int=-1
```

`err=1` after `py_dict_set` is the port unresolved-name fingerprint:
`pcc/py_runtime/py/py_dict.py` declared `pcc_py_gc_minor_graph_lock/unlock`
but had never declared the three `pcc_gc_store_ptr_plan_*` externs that
`py_set.py` already had.  One extern block fixed it.  The temporary probe was
removed after it localized the defect.

A second real defect was found by review, not by a gate: the strict mirror set
`done = 1` after a successful replacement and then fell through to the insert
block (C returns immediately there), so a replacement could also insert a
duplicate entry.  A `mutated` flag now makes the two paths exclusive.

## Focused evidence

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py::test_dict_set_commits_key_value_index_and_size_under_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_dict_get_contains_use_rooted_restartable_hash_equality_lookup
```

Result: `2 passed in 0.44s`.

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py -k "dict or set"
```

Result: `19 passed, 170 deselected in 12.82s`.

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_python_dict_methods_parity.py \
  tests/python/test_python_set_methods_parity.py
```

Result: `23 passed in 36.18s`.

```text
gtimeout 400s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py -k "dict_set_survives_callback"
```

Result: `2 passed, 189 deselected in 2.53s` (C and strict pcc-Python).

C `py_dict.c` compiles clean with `-Wall -Wextra` in both thread modes; the one
remaining warning (`py_exc_builtin_class` at the StopIteration site) is
pre-existing and outside this diff.  `git diff --check` is clean.

## Callback shapes used, and the ones deliberately avoided

The dynamic probe follows the callback-capability boundaries already recorded
as `[DENIED]` in the investigation:

- fresh-insert relocation uses a **pcc-native `__hash__`** (an ordinary
  instance method added with `py_class_add_method`);
- replacement relocation uses a **C-extension `tp_richcompare`**;
- the displaced-value finalizer uses a **pcc-native `__del__`**.

Not used, because prior slices proved they record zero callbacks: fake
C-extension `tp_hash`, and ordinary user-instance `__eq__` / `__lt__`.

The probe is self-validating rather than merely green: `relocate_dict` returns
0 unless the copy target actually differs from the source (which makes the
callback fail loudly), and the probe asserts `hash_relocations == 1`,
`equality_relocations == 1`, `del_calls == 1` and a balanced scheduler-root
count of 3.

## Proven by the dynamic probe

- a fresh insert whose `__hash__` relocates the dict still yields `len == 1`
  and `get` returns `11`;
- a replacement whose equality callback relocates the dict yields `len == 1`,
  `get` returns the new `22`, and `py_dict_entry_key_at(d, 0)` is still the
  **original stored key pointer** — replacement never rebinds the key;
- the displaced value's `__del__` re-enters the dict and observes
  `len == 1`, value `33` already published, and no pending exception — i.e. the
  release ran after unlock, against the committed table.

## Nonclaims

- Dict delete is untouched and still decrefs key and value before clearing the
  slot and decrementing size.  That is the next proposal, not this claim.
- No reverted-baseline negative control was run for the dynamic probe; the
  stale-pointer mechanism is argued from source plus the empirically observed
  `len=0` failure mode in the strict mirror before the extern fix.
- No broad bootstrap, stage, fixed-point or five-GC gate was run.  The task row
  is still open, and repository rules forbid using a broad gate as discovery.
