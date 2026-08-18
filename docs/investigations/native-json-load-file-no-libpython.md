# Investigation: native `json.load(file)` falls into a no-libpython function stub

## Status

active

## Problem Description

pcc lowers `json.loads(str)` and `json.dumps(obj)` natively but does not lower
the standard file-like `json.load(fp)` surface. In strict no-libpython mode a
function containing `json.load(stream)` is therefore replaced wholesale by a
fail-closed `NotImplementedError` stub.

This became a bootstrap blocker when the Mach-O phase profiler used
`json.load(stream)`: the linker completed, then pcc1 raised
`no-libpython function unavailable: pcc.py_frontend.pipeline._record_macho_link_profile`.
The same surface occurs in compiler cache and action-DAG readers, so a linker-
specific source rewrite would hide a generic capability gap.

## Repro

`tests/python/test_python_module_imports_parity.py::`
`test_import_json_load_reads_native_file_object` compiles a program that opens
a JSON file through pcc's native file object and calls `json.load(stream)`.

Expected output:

```text
link
41
```

Observed output before the fix:

```text
NotImplementedError: no-libpython function unavailable: imp_json_load.main
```

The current 225-module contextual IR independently shows
`user_pcc_py_frontend_pipeline__record_macho_link_profile` containing only a
`strict.nolib.stub` block.

## Test [CONFIRMED]

The focused run failed deterministically on 2026-08-31:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_python_module_imports_parity.py::\
  test_import_json_load_reads_native_file_object

1 failed in 1.23s
```

## Proposals

- No.1 Lower `json.load(native_file)` through native read-all plus `json.loads` [pending]

## No.1 Lower `json.load(native_file)` through native read-all plus `json.loads`

### Code Change

Extend the existing generic native-json call lowering. For a single positional
argument proven to be a pcc native file object, call `py_file_read_all`, check
the runtime exception channel, then pass the returned Python string to the
existing `py_json_loads` runtime and check again. Unknown file-like objects keep
their existing CPython/libpython path in compatible mode and fail closed in
strict mode; no module- or caller-name special case is added.
