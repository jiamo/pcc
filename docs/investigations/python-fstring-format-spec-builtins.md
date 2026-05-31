# Investigation: Builtin f-string format specs still fail

## Status
resolved

## Problem Description
`tests/test_python_str_methods_parity.py::test_str_fstring_format_spec` is the
last xfailed Python parity test.  The minimized program formats a float with
`.2f`, an int with `,`, and an int with `03d`; it should print `1234.50`,
`1,234,567`, and `007`.

## Repro
Run the xfailed test as a real assertion:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_str_methods_parity.py::test_str_fstring_format_spec' \
  -q -n0 --runxfail -vv
```

Expected current failure:

```text
NotImplementedError: Layer 1 unknown function '__pcc_format_spec'
```

## Test [CONFIRMED]
The repro above was run on 2026-05-08 and failed because
`__pcc_format_spec(n, ",")` reached generic call lowering after
`_emit_format_spec_builtin()` declined the comma decimal spec.

## Proposals
- No.1 Add builtin decimal format helper for comma and zero-padded d     [CONFIRMED]

## No.1 Add builtin decimal format helper for comma and zero-padded d
### Code Change
Extend the narrow builtin format path that already handles `.Nf` and hex to
cover the parity test's decimal integer specs without claiming full D6 user
`__format__` support.

### CONFIRMED
Implemented:

- `__pcc_format_spec(value, ",")` and `__pcc_format_spec(value, "0Nd")` lower
  to a new `py_int_format_decimal(value, width, zero_pad, comma)` runtime helper;
- the helper exists in both the C runtime and the pcc-Python runtime mirror;
- the final xfail marker was removed from
  `tests/test_python_str_methods_parity.py::test_str_fstring_format_spec`.

Observed results:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_str_methods_parity.py::test_str_fstring_format_spec' \
  -q -n0 -vv
# 1 passed in 0.70s

env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest \
  tests/test_python_str_methods_parity.py -q -n0 -rxX
# 12 passed in 6.98s

env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest \
  tests/test_python_int_arithmetic_parity.py \
  tests/test_python_str_methods_parity.py \
  tests/test_python_list_methods_parity.py \
  tests/test_python_dict_methods_parity.py \
  tests/test_python_set_methods_parity.py \
  tests/test_python_class_features_parity.py \
  tests/test_python_exception_parity.py \
  tests/test_python_iteration_parity.py \
  tests/test_python_function_features_parity.py \
  tests/test_python_generator_parity.py \
  tests/test_python_module_imports_parity.py -q -n0 -rxX
# 105 passed in 62.03s
```

Runtime mirror checks:

```bash
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 600s \
  make -C pcc/py_runtime libpy_runtime.a libpy_runtime_pcc_py.a
# rebuilt libpy_runtime_pcc_py.a successfully

PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py <compile and run the same fmt.py probe>
# output lines: 1234.50, 1,234,567, 007
```

## Report (only when the investigation is closing)
No.1 landed.  This closes the Python parity format-spec xfail and therefore
the No.38 parity-xfail closure gate.  Full D6 remains broader than this slice:
user-defined `__format__`, `format()` builtin dispatch, and default
non-empty-spec TypeError behavior are still roadmap work.
