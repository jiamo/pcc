# Second review round — five findings — 2026-08-25

All five were verified against source and reproduced where they were runtime
visible.  Four are fixed; one is fixed in half and the remaining half is
recorded rather than guessed at.  Three were regressions in my own recent work.

## 1. Deleting a local clobbered a same-named module global (P0, mine, fixed)

`_unbind_module_global` was called for **every** `Name` delete target, so:

```python
x = "global"
def f():
    x = "local"
    del x
f()
print(x)
```

```text
CPython   global
pcc       NameError: name 'x' is not defined
```

A regression I introduced when adding module-global unbinding.  My own test
missed it because its local control was named `a` — it never shadowed a global.

The fix reuses the scope test the assignment paths already apply,
`self.current_func_def is None or name in self._current_global_names`, so a
local only reaches the unbind when it genuinely denotes the module global.  The
regression now carries a `shadowed` control.

## 2. The unbind bypassed the GC root, barrier and pin protocol (P0, mine, fixed)

I cleared the slot with a raw `store null` and then `_gc_release`d the old
value.  Module globals are stored **pinned** and written through
`pcc_gc_store_root`, and teardown clears them as unpin followed by
`pcc_gc_store_root(slot, NULL)`.  The raw store left `PY_FLAG_GC_PINNED` and
its telemetry behind and skipped the GC3/GC4 slot write barrier, and it treated
a CPython-compatible global as a pcc object.

Now mirrors `module_lifecycle_lowering`: `py_cpy_decref` for a cpy global,
`_gc_unpin` then `pcc_gc_store_root(slot, NULL)` for a pcc one.

**One thing had to be checked before changing it.**  `pcc_gc_store_root`
decrefs the old value itself (`py_obj.c`), so once the unpin and store_root
were in place my separate `_gc_release` became a **double release** and had to
go.  Reading that function first is what kept this from turning a protocol bug
into a double free.

## 3. Deleting an unbound global did not raise (P1, half fixed)

```text
                     CPython              pcc now
x = 1; del x; del x  NameError            NameError      fixed
del never_bound      NameError            silently ok    still open
```

The already-deleted case now consults the `.initialized` flag and raises.  The
never-bound case does not: such a name is absent from `_module_globals`
entirely, and separating it from class names, imports and builtins is a wider
change than this round should carry.  Left open deliberately.

## 4. strict `py_dict_update` ignored root-registration failure (P1, mine, fixed)

The C path checks both `py_dict_prepare_moving_root` calls and cleans up; the
strict mirror registered and continued straight into `py_dict_set`.  Under
GC3/GC4 a failed registration meant an unregistered key or value crossing a
user hash/equality callback, then a decref of a possibly-moved pointer.  The
strict path now mirrors the C per-item check and cleanup.

## 5. The audit tool mis-detected class and nested scopes (P2, mine, fixed)

`_enclosing_end` treated only an indent-0 `def`/`class` as a boundary, so a
sibling method looked like a continuation of the previous one:

```python
class C:
    def a(self):
        exc = py_exc_new(...)
        py_raise(exc)

    def b(self):
        py_decref(exc)
```

The tool reported `a`'s `exc` as released.  This hides real leaks rather than
creating double frees, but it makes the tool unsafe to trust on a second pass.
The boundary is now the next `def`/`class`/decorator at an indent **less than
or equal to** the one that opened the enclosing function.  Two cases were added
to the contract test — the reviewer's exact shape, and a release in the *same*
method which must still count.

## Gates

```text
test_module_global_del_unbinds.py (with shadow + double-del controls)  1 passed
test_temporary_call_argument_released.py                               1 passed
dict + set parity                                                     24 passed
raise_owner_audit contract                                            12 passed
tests/python/test_py_corpus.py                            177 passed in 620.64s
```

Expected outputs for both `del` regressions were produced by running the
programs under CPython, not written from reasoning.

## Note on the review's gate observation

The review is right that a run cut off by its watchdog with no final pytest
summary is not a pass.  I hit the same 590s truncation twice this round and
moved the corpus to a backgrounded 1500s budget, which is where the 177 above
comes from.

## Nonclaims

- `del never_bound` still succeeds silently.
- No five-GC, bootstrap, stage or fixed-point gate was run for any of this.
