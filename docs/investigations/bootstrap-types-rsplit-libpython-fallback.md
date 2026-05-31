# Investigation: types dotted-name rsplit reintroduced libpython fallback

## Status
resolved

## Problem Description
Fix the bootstrap. The current bootstrap/fallback gates regressed: the tight stage1 closure
compiled successfully but emitted 9 `py_cpy_*` calls in the combined IR, so
`--python-libpython=off` self-bootstrap was no longer clean.

## Repro
```bash
env -u LC_ALL uv run pytest tests/test_fallback_baseline.py -q -n0
```

Observed before the fix:

```text
fallback total grew past ratchet: 9 vs baseline 0 (+5.0%)
ON-mode fallback total grew past ratchet: 9 vs baseline 0 (+5.0%)
```

The generated `/tmp/stage1_closure_probe.ll` showed all 9 calls inside
`user_pcc_py_frontend_types__class_type_from_dotted`, caused by
`name.rsplit(".", 1)`.

## Test [CONFIRMED]
```bash
env -u LC_ALL uv run pytest tests/test_type_annotations_optional_dotted.py -q -n0
env -u LC_ALL uv run pytest \
  'tests/test_fallback_baseline.py::test_total_fallbacks_under_ratchet' \
  'tests/test_fallback_baseline.py::test_on_mode_total_fallbacks_under_ratchet' \
  -q -n0
```

Observed after the fix:

```text
tests/test_type_annotations_optional_dotted.py: 6 passed
fallback total nodes: 2 passed
```

## Proposals
- No.1 Replace `str.rsplit(".", 1)` in `types._class_type_from_dotted` with a native-loop last-dot scan [CONFIRMED]

## No.1 Replace `rsplit` with native-loop scan

### Code Change
`pcc/py_frontend/types.py::_class_type_from_dotted` now scans `name` once,
records the last `.` index, and slices `module` / `leaf` explicitly. This
keeps the same observable behavior for dotted annotations while avoiding a
string method without native lowering.

### CONFIRMED
The focused regression in `tests/test_type_annotations_optional_dotted.py`
multi-compiles `pcc.py_frontend.py_ast` and `pcc.py_frontend.types` together
with `ir_scaffold_mode="on"` and `libpython_mode="off"`, then asserts
`user_pcc_py_frontend_types__class_type_from_dotted` contains no `py_cpy_*`
call.

The fallback baseline total and ON-mode total tests both pass after the
change, confirming the combined stage1 closure returned to zero fallback
calls for this regression.

## Report
No.1 landed. Replacing `rsplit(".", 1)` with an explicit last-dot scan was the
smallest source-level fix because the bootstrap closure already has native
string indexing, slicing, length, and equality support, while `rsplit` still
lowers through libpython. The regression is covered by a focused two-module IR
test plus the stage1 fallback baseline gate.
