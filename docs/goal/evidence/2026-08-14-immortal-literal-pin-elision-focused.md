# Immortal literal pin elision — focused evidence

Date: 2026-08-14

Task: `PERF-P1-IMMORTAL-LITERAL-PIN-ELISION`

## Source contract

`StrLit` lowers to an internal object global with `PY_FLAG_IMMORTAL` in its
header. `BoolLit` and `NoneLit` resolve to the runtime's immortal singleton
globals. Their addresses cannot be relocated, and container insertion borrows
them, so treating each occurrence as a movable fresh heap temporary only
duplicates pin/unpin and release error paths. Dynamic strings and every other
pointer-producing expression remain conservative.

The implementation adds one explicit ownership/relocation predicate and uses
it at native list, tuple, dict and splat operand boundaries. Container roots,
source-order checks, call-error branches and dynamic-pointer pinning are
unchanged. The generated L1 host-method table was regenerated from the live
contract.

## Focused gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/test_test_infrastructure_efficiency.py::test_l1_codegen_static_method_table_matches_host_contract \
  tests/python/test_cpy_call_argument_ownership.py::test_immortal_string_literals_skip_container_operand_pins \
  tests/python/test_cpy_call_argument_ownership.py::test_dynamic_string_dict_operands_remain_pinned \
  tests/python/test_cpy_call_argument_ownership.py::test_native_dyn_dict_key_error_short_circuits_value_operand \
  tests/python/test_cpy_call_argument_ownership.py::test_mixed_list_literal_error_cleanup_unpins_prior_native_temp \
  tests/python/test_cpy_call_argument_ownership.py::test_cpython_tuple_bridge_failure_cleans_remaining_refs_and_container

6 passed in 1.13s
```

Both edited production files, the generated table, host contract and focused
test file also passed `python -m py_compile`.

## Measured standalone IR change

The same parse -> infer -> L1 `ir_scaffold=on` probe compiled
`pcc/py_frontend/codegen/_l1_codegen_static_methods.py` before and after the
change:

```text
metric                    before       after        change
IR bytes                  48,790,052   43,116,892   -5,673,160 (-11.63%)
@pcc_gc_unpin refs           100,798       52,581      -48,217 (-47.84%)
@pcc_gc_pin refs              (not recorded) 7,675
@pcc_gc_release refs          (not recorded) 48,748
standalone wall               (not recorded) 4.138s
```

The reduction exceeds the 8% focused threshold. The remaining unpin expansion
is dominated by real nested container roots and owned container temporaries;
this slice deliberately does not weaken those error/GC paths.

## Claim boundary

This is focused host-source and IR-shape evidence. It does not prove the
source-current compiled pcc1 path, relocating-GC execution or the final
sequential pcc1 -> pcc2 -> pcc3 fixed point. Those remain the open boundary,
so the row is `DONE_WEAK`.
