# Multi-argument print LIFO exception unwind

Date: 2026-08-14

Task: `PY-P0-PRINT-LIFO-EXCEPTION-UNWIND`

## Claim

For non-generator native `print(...)`, the fixed-positional and splat forms
root their temporary argument containers through the managed temporary-root
protocol.  Operand and `sep`/`end` failures in either the pcc or CPython error
domain leave and release the exact active root before continuing to the
surrounding handler.  Success paths also balance the container owner.  The
self backend therefore sees consistent managed-root state at handler and
function-error joins.

Generator print uses its existing persistent heap-frame slot and is not
claimed as a stack-LIFO lifetime.

## Implementation

- `pcc/py_frontend/codegen/print_lowering.py`
  - checks tuple/list construction failures before publishing a root;
  - uses the existing container temporary-root protocol;
  - installs pcc and CPython cleanup predecessors around later source operands;
  - restores the original error targets after operand lowering;
  - balances list/tuple roots and their owned references on success and error.
- `tests/python/test_native_print_exception_cleanup.py`
  - exercises a later fixed operand raising inside `try`;
  - exercises `print(*values, sep=raising_call())`;
  - runs precise self stack-map analysis and the resulting no-libpython binary.

## Evidence

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_print_exception_cleanup.py::test_print_operand_cleanup_has_consistent_self_stackmap
1 passed in 0.54s

gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_print_exception_cleanup.py::test_print_operand_errors_leave_lifo_roots_and_reach_handler
1 passed in 131.51s

gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  'tests/python/test_cext_setitem_dispatch.py::test_cext_mapping_assignment_for_vander_shaped_keys[port]'
1 passed in 200.21s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  'tests/python/test_cext_setitem_dispatch.py::test_cext_mapping_assignment_for_vander_shaped_keys[cc]'
1 passed in 1.40s
```

The long port timings include one current-source pcc-Python runtime cold build.
The application itself reaches the real extension mapping-assignment slots and
prints the expected caught silent-NULL diagnostics.

