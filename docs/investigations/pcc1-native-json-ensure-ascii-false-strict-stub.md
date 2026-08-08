# Investigation: json ensure_ascii false falls back to a no-libpython stub

## Status

resolved

## Problem Description

After native identity persistence was restored, the current-pcc1 Harness advanced to Session saving and raised `NotImplementedError: no-libpython function unavailable: session_persistence.save`. The save method serializes every JSONL record with `json.dumps(..., ensure_ascii=False)`.

PCC's native JSON writer already emits non-ASCII text as UTF-8, which is the requested `ensure_ascii=False` behavior. The frontend recognized only no-keyword `json.dumps` and one literal `sort_keys` keyword, so the valid Unicode-preserving call unnecessarily imported CPython and caused strict no-libpython compilation to stub the entire save method.

## Repro

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_harness_session_composition.py::test_current_pcc1_persist_resume_and_fork \
  -m integration
```

Expected: the first native process writes the Session JSONL log. Observed 2026-08-14: the process reached identity persistence, then `session_persistence.save` raised the unavailable-function error.

With `PCC_DEBUG_STRICT_NOLIB_STUB=session_persistence.save`, the first fallback is `py_cpy_ensure_init`; libpython-enabled IR identifies the call as `json.dumps(..., ensure_ascii=False)`.

## Test [CONFIRMED]

The new native JSON regression writes Chinese text and a supplementary-plane emoji with literal `ensure_ascii=False`, also combined with `sort_keys=True`, and compares output with CPython. Strict no-libpython compilation of `session_persistence.py` and the durable Session integration are the product gates.

## Proposals

- No.1 Recognize literal ensure_ascii false as native UTF-8 output [implemented]

## No.1 Recognize literal ensure_ascii false as native UTF-8 output

### Code Change

Permit literal `ensure_ascii=False` in native `json.dumps` lowering and allow it alongside the existing literal `sort_keys` keyword. Keep `ensure_ascii=True`, dynamic booleans, duplicate keywords, and unsupported keywords on the general path because the current runtime does not provide ASCII escaping.

### Validation

The focused Unicode JSON regression passed as part of a 5-test native JSON/filesystem/file-iteration group. Current pcc1 self-bootstrap and strict no-libpython Harness compilation passed. The durable Session integration then persisted, resumed, and forked JSONL logs across three native processes.
