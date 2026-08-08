# Investigation: module-level dict() builtin leaks its temp GC root into err.exit joins

## Status

resolved locally 2026-08-15

## Problem Description

`scripts/bootstrap.sh --stage 1` on HEAD `a2031b76` fails (after the separate
verifier OOM, see
[self-backend-verifier-dense-dominator-sets-oom.md](self-backend-verifier-dense-dominator-sets-oom.md))
with:

```text
self precise stack-map analysis in
'_pcc_py_module_top_pcc_py_frontend_codegen_layer1_support':
managed root state disagrees at block join 'err.exit'
```

Predecessor: [harness-agent-loop-self-stackmap-err-exit-join.md](harness-agent-loop-self-stackmap-err-exit-join.md)
resolved the same *message* as a stale-pcc1-artifact issue. This recurrence is
different: it is the **current source** (pcc0 host compile of the stage1
closure), and the root cause is a real frontend lowering bug.

## Repro [CONFIRMED]

Deterministic minimal reproducer — the dict() **must be module-level**
(a function-local `dict([...])` takes the owned-local path and passes):

```python
TABLE = dict(
    [
        ("a", [1, 2]),
        ("b", [3, 4]),
    ]
)
def main() -> int:
    return 0
main()
```

```bash
gtimeout 180s env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on only_table.py -o only_table
# error: ... managed root state disagrees at block join 'err.exit'
```

Harness trap hit while bisecting: running `uv run pcc` with cwd inside
`~/.cache/...` fails with `Failed to spawn: pcc` (uv cannot resolve the
project) and a narrow `rg` pattern read those runs as "pass". All probe runs
must start from the repo root. The apparent nondeterminism earlier in the
session was entirely this artifact.

## Root Cause [CONFIRMED]

Probe on the real stage1 module (`self_backend_module_77.ll`,
`layer1_support`) dumped the join states: predecessor `body` reaches
`err.exit` with no active local root groups, while
`cpy.operand.pcc.cleanup.23269` reaches it with `dict.tmp.root.13631` still
registered.

`_emit_dict_builtin` (`pcc/py_frontend/codegen/literal_lowering.py`) entered
the result dict's container temp root (`pcc_gc_frame_enter_lifo`) and then
emitted the source argument bare:

```python
out_root = self._enter_container_temp_root(out, "dict")
...
src_val = self._emit_expr(src_expr)   # nested err edges -> OUTER cleanup
```

A nested list literal's `py_list_new` error check therefore branched to the
cleanup block created *before* the dict root was entered (it leaves only the
outer roots), so the just-entered dict root was never left on that path.
`_emit_dict_literal` already wraps every nested operand in
`_emit_expr_with_cpy_operand_cleanup(..., rooted_pcc=((d, dict_root),))`;
`_emit_dict_builtin` skipped that mechanism for its source expression and for
both kwargs loops. `layer1_support` hits this via its module-level static
export tables `"pcc.parse.py_parse": dict([("ParseError", ...), ...])`.

## Test [CONFIRMED]

- Failure observed 2026-08-15 with the Repro command above and with the
  stage1 batch worker on `self_backend_module_77.ll`.
- Regression home: `tests/python/test_native_dict_builtin_module_root.py`
  (module-level `dict([...])`, self + llvm backends, output checked).

## Proposals

- No.1 Route dict() source/kwargs operand error edges through the rooted
  cleanup mechanism [CONFIRMED]

## No.1 Route dict() operand error edges through the rooted cleanup

### Code Change

`_emit_dict_builtin` now emits the source expression via
`_emit_expr_with_cpy_operand_cleanup(src_expr, (), ((out, out_root),))` and
both kwargs loops via the same wrapper with `as_pcc_object=True`. Nested
error edges now leave `out_root` (and release the dead dict) before joining
the outer error path — the same idiom `_emit_dict_literal` uses.

### CONFIRMED

- All five bisect probes compile and run correctly under
  `--backend self --python-libpython=off` (module-level table, subscript,
  print, local dict; outputs `4`, `2`, exit codes correct).
- `tests/python/test_native_dict_builtin_module_root.py`: 2 passed
  (self, llvm).
- `tests/c/test_llvm_capi_ir_parity.py` + `test_llvm_capi_end_to_end.py`:
  27 passed. `tests/python/test_bootstrap_gate_baseline.py`: 2 passed,
  2 deselected.
- Stage1 rebuild evidence: see the verifier OOM investigation's Result
  section (same rebuild proves both fixes together).

Pre-existing unrelated red gate on HEAD noted during gating:
`test_py_multi_file_compile.py::test_borrowed_object_local_rebind_keeps_gc_root`
expects the pre-GC-rework rebind IR shape (no dict(), no verifier in that
path); filed as a separate task.
