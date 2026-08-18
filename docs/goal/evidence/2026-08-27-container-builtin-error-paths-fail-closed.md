# Container-builtin walks: exception-path ownership + fail-closed dyn edges

Date: 2026-08-27
Task: `PY-P1-CONTAINER-BUILTIN-EXCEPTION-PATH-OWNERSHIP`
Claim level: host llvm-backend, red-first regressions green; closure checks
green (equal-arm). NO stage1/bootstrap claim — deferred for source-stability
(see open boundary).

## What was wrong (three layers, all reproduced red-first)

1. Reviewer's P1: dict-copy's existing error checks leaked the owned keys
   view and loop temps on the raising edges (no release_on_error).
2. any/all and zip walks had NO error checks at all; and the runtime helpers
   they call fail SILENTLY (py_obj_len -> 0, py_dict_keys -> NULL,
   py_obj_getitem -> NULL; py_obj_getitem's dict arm is the silent
   py_dict_get, not the raising py_dict_getitem), so dyn-held mappings
   produced silently wrong results: dict(dyn-nonmapping) = {},
   any(dyn-dict) = False over NULL elements, zip(_, dyn-dict) = NULL-slotted
   tuples.
3. The dict() pairs walk leaked six owned refs per iteration ON THE NORMAL
   PATH (pair, key, value, three index boxes) — py_dict_set retains.

## Fix summary (frontend only)

literal_lowering: dyn tag dispatch (dict -> py_dict_update copy
[CPython-correct]; list/tuple -> pairs walk; else TypeError), static
bad-type raise, NULL-pair guard, normal-path releases.
dict_lowering: release_on_error wired into both checks + dyn NULL guard
after py_dict_keys. numeric_builtin/tuple_zip: dyn-only err checks + elem
NULL guards releasing all live owned temps. Static shapes gain zero checks
(pinned by a cost-guard test).

## Gates run

```text
tests/python/test_native_container_builtin_error_paths.py  12 passed (new, red-first)
tests/python/test_native_set_from_dict_keys.py + probe + typeinfer
  + owned-method + refcount-variants                        27 passed
tests/python/test_py_multi_file_compile.py                  41 passed
closure check (equal-arm /tmp isolation)                    4/4 OK
```

In-repo closure-check paths report a MULTI-SOURCE artifact ("python_library
mode only supports a single Python source" via same-package auto-close) —
copy the file to /tmp for the real verdict; the HEAD-vs-worktree comparison
must use the SAME isolation on both arms.

## Open boundary

- Stage1/bootstrap gate deferred: ~16 backend files carry the in-flight
  Indexed Function Kernel restructure; a stage1 failure now cannot be
  attributed. Run once that lane reports source-stable.
- any/all/zip over dyn-held mappings fail closed (TypeError), not
  CPython-iterate-by-key: that is PY-P1-SET-FROM-DYN-MAPPING (iterator
  protocol).
- The silent runtime contracts themselves are RT-P2-SILENT-NULL-RUNTIME-
  CONTRACTS: any(dyn-int) still returns False (len silent 0) rather than
  TypeError.
- Shim quick-subset + baseline gates were still running in background at
  writing time; their summaries land in the task row when complete.
