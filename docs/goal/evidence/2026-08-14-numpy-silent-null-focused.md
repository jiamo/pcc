# NumPy silent-NULL / C-extension slot focused closure

Date: 2026-08-14

Task: `BUG-P0-NUMPY-IMPORT-SILENT-NULL-CALL-BLOCKS-L4-L5`

## Claim level

Source implementation and package-independent runtime gates are green.  This
does **not** claim the pcc1 NumPy L4/L5 integration gates: the canonical
`build/bootstrap/pcc1` artifact is currently absent, so those gates remain for
the final current-source sequential bootstrap chain.

## Focused evidence

```text
gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_func_fail_closed.py \
  tests/python/test_cext_setitem_dispatch.py \
  tests/python/test_cext_inplace_number_dispatch.py
7 passed in 9.02s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_cext_len_and_str_dispatch.py
1 passed in 0.85s
```

The setitem gate also passed independently on the current pcc-Python runtime
(`1 passed in 200.21s`, including its cold build) and C oracle (`1 passed in
1.40s`).  The inplace gate passed both runtime parameters (`2 passed in
3.64s`).

The first setitem retry exposed a separate build prerequisite: the newly added
`rename()` use in `pcc/py_runtime/src/py_os_path.c` lacked its standard
`<stdio.h>` declaration.  The minimal include fix compiles with the fixture's
C flags, after which both runtime tiers completed the behavioral gate.

## Remaining boundary

- Produce the canonical current-source `build/bootstrap/pcc1` as the first
  stage of the final deliberate `pcc1 -> pcc2 -> pcc3` chain.
- Run the exact NumPy L4 gate, then L5 only after L4 is green.
- Do not substitute the stale repository-root `pcc1`, the older
  `build/bootstrap-self/pcc1`, or the HARNESS-private compiler artifact.

