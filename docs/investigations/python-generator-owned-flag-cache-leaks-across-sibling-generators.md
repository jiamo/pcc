# Investigation: owned-local flag cache leaks across sibling generators — self-backend rejects undefined pointer value (closes the long-standing numpy `.owned.N` cap at 149 IR modules)

## Status
resolved

## Problem Description

When two sibling user generator functions in the same compilation share a
local-variable name whose owned reference is reassigned (e.g. a `PyStr` local
rebound across iterations), the SECOND generator's resume function emits IR
that references the FIRST generator's owned-flag alloca. The reference is
undefined in the second function, producing invalid LLVM IR. The self backend
correctly rejects it at
`materialize_pointer (self_backend_aarch64_darwin_materialize.py:529)` with:

```
pcc.backend.BackendUnavailable: self backend expected pointer value
  'pruned_directories.owned.33'
  in 'user_numpy_distutils_misc_util_general_source_directories_files__gen_resume'
```

This is the **actual root cause** of the long-standing "known self-backend
generator emission failure" that the
[numpy-first-import-libpython-fallback.md](numpy-first-import-libpython-fallback.md)
investigation has carried as a known-unknown for many entries (always referenced
as "149 IR modules dumped before the known self-backend generator emission
failure," never minimally reproduced or root-caused before now). The "149"
matches the number of pure-Python numpy modules emitted before module 90's
function fails. The failing sibling pair in numpy is
`numpy.distutils.misc_util.general_source_files` and
`general_source_directories_files`, both carrying a local `pruned_directories`
(intersection of the same module).

## Repro

```python
def gen_a():
    pruned = "a-init"
    for i in range(2):
        pruned = "a" + str(i)
        yield pruned

def gen_b():
    pruned = "b-init"
    for i in range(2):
        pruned = "b" + str(i)
        yield pruned

def main() -> None:
    for v in gen_a():
        print(v)
    for v in gen_b():
        print(v)

if __name__ == "__main__":
    main()
```

`env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on
/tmp/sib.py -o /tmp/sib.out` produced, before the fix, the same
`BackendUnavailable: self backend expected pointer value 'pruned.owned.<N>'`
error (the second generator's resume function referencing the first
generator's flag alloca). After the fix the program compiles and prints
`a0 / a1 / b0 / b1` matching CPython.

To capture the numpy ground truth (an opt-in run requiring host python and a
real numpy artifact):

```bash
# install numpy into a tmp site (uses the cpython-compat ABI shim) then
PCC_PACKAGE_SITE=$TMP/site PCC_DEBUG_SELF_IR_DUMP_DIR=$DUMPDIR \
  env -u LC_ALL uv run pcc --backend self --python-libpython=auto \
  --ir-scaffold=on /tmp/main.py -o /tmp/exe
```

Before fix: `BackendUnavailable: ... 'pruned_directories.owned.33' in
'user_numpy_distutils_misc_util_general_source_directories_files__gen_resume'`
after dumping 149 IR modules. After fix: all 149 modules emit and the run
proceeds to a *different*, downstream failure (link-stage undefined class
`__init__` symbols — separate bug, see Report).

## Test [CONFIRMED]

`tests/python/test_python_generator_parity.py::test_generator_sibling_owned_flag_isolation`
(two sibling generators sharing an owned-rebound local `pruned`). Confirmed
failing before the fix (with `BackendUnavailable` from the self backend on the
second generator's resume function), passing after. Full file `-q -n0`: 9
passed.

## Proposals

- No.1 Reset `_owned_local_flag_slots` / `_gc_rooted_local_names` to fresh in the user-function-lowering generator branch  [CONFIRMED]

## No.1 Reset the owned-flag / gc-root caches in the generator branch

### Code Change

`pcc/py_frontend/codegen/user_function_lowering.py`, the generator branch in
`_emit_user_function` (around line 1001-1008). Mirror the normal-function
reset at line 1053-1054 that the generator path was skipping:

```python
if fd.name in ... or self._funcdef_has_yield_sentinel(fd):
    ...
    self._generator_func_names.add(fd.name)
    self._owned_local_flag_slots = {}      # <-- new
    self._gc_rooted_local_names = set()    # <-- new
    self._emit_generator_wrapper_function(fd, fn)
    ...
    return
```

### CONFIRMED

Root cause: `_emit_user_function` saves the caches at line 957-958
(`saved_owned_local_flag_slots`, `saved_gc_rooted_local_names`), enters a
`try` at line 996, then dispatches on whether the function is a generator
(line 1001-1008) or a normal user function (line 1010+). The NORMAL path
resets the caches to fresh empty (line 1053-1054, `self._owned_local_flag_slots
= {}` / `self._gc_rooted_local_names = set()`) before emitting the body. The
GENERATOR path calls `_emit_generator_wrapper_function` and `return`s — never
touching the reset. The `finally` (~line 1300) restores by reassigning the
saved reference, but the dict was MUTATED in place during the generator body
emission, so subsequent emissions inherit the prior generator's entries.

`_ensure_owned_local_flag(name)` (`ownership_lowering.py:455`) is a per-name
cache: if `name` is already present, return the cached alloca *without
creating a new one*. So if generator A previously emitted a flag for
`pruned_directories`, generator B's resume function asking for that name's
flag gets back the alloca *from A's function*. B's IR then references an SSA
value that exists only in A. The self backend's `materialize_pointer` checks
`func.value_types` and `func.alloca_slots` for the function in hand and
correctly raises `BackendUnavailable` since the value is genuinely undefined
in B.

The fix mirrors the exact reset the normal path already does, applied right
before the generator-wrapper call. The `finally` restore is unchanged (it
still restores the caller's caches on the return-from-generator path). One
condition each cache — no other behavioral change.

Evidence:
- Minimal sibling-generators repro (`gen_a`/`gen_b` both with `pruned` rebound
  + yield) → ✓ matches CPython under `--backend self --python-libpython=off`.
- `tests/python/test_python_generator_parity.py -q -n0` → 9 passed (incl. new
  `test_generator_sibling_owned_flag_isolation`).
- Numpy auto-mode diagnostic
  (`pcc --backend self --python-libpython=auto --ir-scaffold=on`) on a real
  numpy site now emits ALL 149 IR modules (previously crashed at module 90's
  `general_source_directories_files__gen_resume`). The
  `BackendUnavailable: pointer value 'pruned_directories.owned.33'` is no
  longer raised; subsequent grep for `BackendUnavailable|pointer value|owned\.[0-9]`
  returns nothing.
- Mandatory self-host gate
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  → 1 passed in 38.85s (stage1 → stage2 → stage3 self-backed).

## Report

Landed No.1, a 2-line cache reset mirroring the normal-function path. This
closes the long-standing `.owned.N` self-backend generator emission cap that
`numpy-first-import-libpython-fallback.md` carried as a known-unknown for
many iterations. The cap is gone: 149/149 IR modules now emit successfully.

**Newly-exposed downstream blocker** (NOT this change; the next investigation):
the numpy auto-mode compile now reaches the link stage and fails with
undefined symbols for class `__init__` methods across modules:

```
Undefined symbols for architecture arm64:
  "_user_numpy_ma_mrecords_MAError___init__",
    referenced from: _user_numpy_ma_mrecords_MaskedRecords___new__
      in self_backend_expanded_63.ll.o
  "_user_numpy_testing__private_utils__Dummy___init__",
    referenced from: __pcc_py_module_top_numpy_testing__private_utils
      in self_backend_expanded_90.ll.o
```

So the `MAError.__init__` is referenced by `MaskedRecords.__new__` but the
init symbol was not emitted to any object file. Likely cause: class `__init__`
methods that are trivial/missing are not getting native symbol emission, or
they are emitted under a different name than the call site references. That
is the next bug class on the numpy generator path. The order of progress is
now: (closed) yield-tuple parser leak → (closed) range-in-generator counter
→ (closed) enumerate-in-generator counter → (closed) int-list boxed-slot →
(closed, this) sibling-generator owned-flag leak → (NEW) cross-module class
`__init__` symbol emission.

This was the actual hard cap the prior agents called "the known self-backend
generator emission failure"; documenting + fixing it converts a long-standing
known-unknown into a known-known.
