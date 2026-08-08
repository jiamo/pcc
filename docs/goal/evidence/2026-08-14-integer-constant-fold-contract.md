# LLVMREF-P3-ALIVE2-CONSTFOLD — finite integer-fold contract

Mode: host-pcc unit and IR-pass verification. The claim is limited to the
checked C/LLVM integer constant owners and their named adapters; it excludes
floating point, variable identities, backend peepholes and general optimizer
equivalence.

The inventory gate first exposed `pcc/ssa/sccp.py`. Its method name had been a
substring false positive, but review found a real second hand-written semantic
owner: signed remainder used Python `%`, and invalid shifts could raise a host
exception. The bootstrap SSA adapter now delegates binary, unary and cast
folding to the same bit-precise C integer contract, applies LP64 integer
promotion/usual-conversion rules, and keeps unknown type spellings
overdefined. Regressions cover signed remainder, unsigned wrap, mixed signed
comparison, unary promotion, signed overflow and invalid shifts. Inventory
discovery now parses actual call expressions rather than matching substrings.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/c/test_integer_constant_fold_contract.py
13 passed in 5.55s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/c/test_ssa_sccp.py tests/c/test_ssa_sccp_pass.py \
  tests/c/test_ssa_sccp_rewrite.py tests/c/test_ssa_lowering.py \
  -k 'sccp or constant'
20 passed, 75 deselected in 2.89s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 --tb=short \
  tests/c/test_ir_passes_sccp.py tests/c/test_ir_passes_instsimplify.py \
  tests/c/test_ir_passes_instcombine.py tests/c/test_ir_passes_reassociate.py \
  tests/c/test_ir_passes_loop_unroll_real.py
237 passed, 14 subtests passed in 4.33s

gtimeout 30s env -u LC_ALL uv run python -m py_compile \
  pcc/ssa/sccp.py tests/c/test_integer_constant_fold_contract.py
PASS
```

`tests/constant_fold_inventory.json` remains the machine-readable finite owner
and adapter inventory. Its exclusions bound the completed claim.
