# M2 NumPy L5 — array construction + scalar addition (pcc1/self/no-libpython, GC0..4)

## Claim

`M2-NUMPY-L5` is `DONE_STRONG` for pcc1 / `--backend self` /
`--python-libpython=off`. `np.array([1,2,3]) + 1` produces `[2,3,4]` through a
self-host pcc1 binary, and the identical artifact yields the same result under
`PCC_GC_BACKEND=0..4`. No package-name special-casing; both fixes are generic
C-API-shim features. Proves NumPy *array construction + ufunc add + element
unboxing*; it does not claim full NumPy array runtime semantics.

## Root cause (localized under pcc1, this session)

`np.array([1,2,3])` constructed and `.size`/`.shape`/the `+1` ufunc already
worked (the cext binary number protocol was wired). Two no-libpython gaps
remained:

1. **Iteration** — `list(a)` / `for x in a` yielded 0 items with no error.
   numpy's 1-D array `tp_iter` returns `PySeqIter_New(a)`, whose `next` calls
   `PySequence_GetItem` -> `PySequence_Check`. `PySequence_Check` only accepted
   pcc-native tags (LIST/TUPLE/STR/...), returning 0 for the cext ndarray, so
   `PySequence_GetItem` raised "expected sequence", the seqiter cleared it and
   stopped -> empty.
2. **Scalar unbox** — `a[0]` returns a foreign numpy scalar (cext tag 65555 =
   `PCC_CAPI_CEXT_TAG_BASE`+19). `py_int_to_i64` returned 0 for any
   non-`PY_TYPE_INT` object, so `int(a[0])` == 0 and the elements never became
   Python ints.

## Fixes (generic, no numpy special-casing)

- `pcc/py_runtime/src/py_capi_shim.c` `PySequence_Check`: also return 1 for a
  C-extension object whose type exposes `sq_item` or `mp_subscript` (integer
  item access) — drives the `PySeqIter_New` path for any subscriptable cext
  object.
- `pcc/py_runtime/src/py_capi_shim.c` new `py_cext_number_to_i64(o, overflow)`:
  unbox a cext number scalar to int64 via `PyNumber_Long` (its `nb_int`/
  `nb_index` slot). Returns 0/overflow=1 for any non-cext-number object,
  preserving prior behaviour.
- `pcc/py_runtime/py/py_int_convert.py` (`py_int_to_i64` port): before giving up
  on a non-`PY_TYPE_INT` object, call `py_cext_number_to_i64`. C-only helper, no
  cc baseline mirror because cext objects only exist under the no-libpython
  C-API shim that this port archive links.

## Reproduce

```bash
# runtime archive rebuild after the shim/port edits:
cd pcc/py_runtime && env -u LC_ALL PATH="$PWD/../../.venv/bin:$PATH" make libpy_runtime_pcc_py.a && cd ../..
# L5 program via pcc1:
printf 'import numpy as np\nprint([int(x) for x in np.array([1,2,3]) + 1])\n' > /tmp/l5.py
PCC_PACKAGE_SITE="build/head-truth/numpy-core/site:projects/numpy-2.4.4/build/pcc-package/meson-build:projects/numpy-2.4.4" \
  build/bootstrap/pcc1 --backend self --python-libpython=off --ir-scaffold=on /tmp/l5.py -o /tmp/l5-app
for gc in 0 1 2 3 4; do PCC_GC_BACKEND=$gc PCC_HOST_PYTHON=/usr/bin/false PYTHONPATH= PCC_PACKAGE_SITE= /tmp/l5-app; done
```

Result: every backend prints `[2, 3, 4]`, exit 0. `otool -L` on the artifact
shows only libSystem/Accelerate — no libpython, no LLVM, no `python3`.

## Gates [CONFIRMED]

- Pinned gate: `tests/integration/test_numpy_l5_pcc1_gate.py`
  (`pytest.mark.integration`, `PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION=1`) — compiles
  with pcc1, asserts `[2, 3, 4]` under `PCC_GC_BACKEND=0..4`, and no-libpython
  linkage. Observed `1 passed in 82.69s`, re-verified against the freshly
  bootstrapped pcc1 (`1 passed in 87.39s`).
- Full self-host bootstrap (stage1->stage2->stage3, `--backend self`
  `--python-libpython=off`) green with the runtime changes; pcc2 == pcc3
  (metadata-normalized). Confirms the shim/port edits do not break the fixed
  point (they are behaviourally inert for the non-cext objects that bootstrap
  uses).

## Claim boundary

Proves array construction + scalar add + integer element unboxing + iteration
under pcc1/no-libpython across GC0..4. It does NOT prove numpy scalar
`repr`/`str` (printing a bare scalar still shows the fallback `<object tag=N>`),
scalar rich-comparison against Python ints, or the broader numpy array runtime
(reductions, dtypes, multi-dim). Those are follow-on work, tracked separately.
