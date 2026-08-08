# LINK-P1-MACHO-OBJ-SWITCH — current-source route evidence

Mode: host pcc, Darwin arm64.  This is focused default-route and object
differential evidence, not stage2 timing or fixed-point evidence.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_arm64_encode.py \
  tests/python/test_arm64_asm_driver.py \
  tests/python/test_self_obj_pcc_route.py
23 passed, 2 deselected in 1.70s
```

The default pcc direct-object route, explicit system-as oracle, selector
rejection, structural parity and local link/run behavior pass.  The structural
oracle now correctly interprets Mach-O non-extern `r_symbolnum` as a section
ordinal rather than a symbol-table index.

The row remains `DONE_WEAK`: stage2 before/after wall-time has not been
recorded on the frozen source, and the current-source bootstrap baseline has
not run.
