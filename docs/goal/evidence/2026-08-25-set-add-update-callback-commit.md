# Set add/update callback commit slice — 2026-08-25

## Claim

The C and strict pcc-Python set add paths now root the set and item before
user hash/equality, restart after callback-driven owner/table/slot drift, and
publish key/hash/size/fill through a revalidated graph-locked store-plan
commit.  Set update snapshots the source before invoking destination
hash/equality callbacks, roots the destination/source/snapshot and each owned
snapshot key, and reloads all moving roots after callbacks.

This proves only the set add/update slice of
`GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`.  Dict set/update/delete remains
open.  It is not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## Probe correction

The first update probe expected a fake C-extension `tp_hash` callback, but the
current `py_obj_hash` C-extension path did not invoke that function.  Runtime
state showed `src=1`, `dst=1`, no exception, and zero callback count: update
had copied the element correctly, but the proposed mutation callback was not
an exercised control.  The final probe uses the already proven pcc-native
`__hash__` path for add relocation and the proven C-extension rich-compare
path for update relocation/source mutation.

## Focused evidence

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py::test_set_contains_uses_rooted_restartable_hash_equality_lookup \
  tests/python/test_gc_threading_substrate.py::test_set_remove_commits_tombstone_and_size_before_decref_finish \
  tests/python/test_gc_threading_substrate.py::test_set_add_commits_key_hash_and_counters_under_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_backend4_set_contains_hash_and_equality_callbacks_reload_relocated_owner \
  tests/python/test_gc_threading_substrate.py::test_backend4_set_remove_relocates_then_finalizer_observes_committed_absence \
  tests/python/test_gc_threading_substrate.py::test_backend4_set_add_and_update_survive_callback_relocation_and_source_mutation \
  tests/python/test_python_set_methods_parity.py
```

Result: `18 passed in 10.16s`.

The dynamic add/update probe passes independently in both runtime mirrors:

```text
[c]          1 passed in 0.58s
[pcc_python] 1 passed in 0.78s
```

It proves:

- ordinary pcc-native `__hash__` directly relocates an empty destination set
  during add, after which length and membership are exact;
- C-extension equality directly relocates a set during duplicate add without
  replacing the original key or increasing size;
- update snapshots its source before an equality callback relocates the
  destination and mutates the source, so the new source-only item is not
  spuriously consumed;
- all externally registered roots remain balanced (`4` expected, `4`
  observed).

No broad bootstrap or stage gate was run: the task is still open on dict
mutation and repository rules prohibit using a broad stage as discovery.
