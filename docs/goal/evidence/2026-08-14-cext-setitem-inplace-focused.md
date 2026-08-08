# C-extension setitem/in-place dispatch focused evidence (2026-08-14)

Mode: host-pcc, strict self/no-libpython executable; both the retained C
runtime oracle and production pcc-Python runtime port were exercised. This is
package-independent mechanism evidence, not a NumPy L4/L5 claim.

```text
gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_cext_setitem_dispatch.py \
  tests/python/test_cext_inplace_number_dispatch.py
4 passed in 137.48s
```

The synthetic extension proves mapping/sequence assignment for tuple, slice
and integer keys, fail-closed silent-NULL slot handling, identity-preserving
`nb_inplace_true_divide`, and owned `NotImplemented` fallback to
`nb_true_divide` on both runtime tiers. Official NumPy shape/value gates and
the sequential pcc1 fixed point remain open.
