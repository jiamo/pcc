# Investigation: a tuple-unpack loop variable shadowing the enclosing method name infers as 'callable'

## Status

active

## Problem Description

Legal Python is rejected by type inference when a for-loop tuple-unpack
target inside a method reuses the METHOD's own name:

```python
class Table:
    def value_id(self, name: str) -> int:
        for existing_name, value_id in self.rows:   # value_id shadows the method
            if existing_name == name:
                return value_id                     # <- inferred 'callable'
        return -1
```

```text
error: return type mismatch: expected 'int', got 'callable'
```

CPython runs it fine (prints 2 / -1). `return value_id` must resolve to the
loop binding, not the enclosing class attribute; pcc's type_infer resolves the
name against the method (callable) instead.

Found when the in-flight `pcc/backend/self_backend_kernel.py` (indexed
function kernel lane) used exactly this shape at `value_id`/`block_id` and
broke stage1 for the whole worktree. The author's code was valid; the
frontend is wrong.

## Repro

15 lines, deterministic, seconds:

```bash
env -u LC_ALL PCC_GC_BACKEND=0 uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on /tmp/shadow.py -o /tmp/shadow_bin
# error: return type mismatch: expected 'int', got 'callable'
python3 /tmp/shadow.py   # 2 / -1
```

(program as above; also reproduces with `block_id`.)

## Test [CONFIRMED]

Observed 2026-08-27 under the command above, host pcc, current worktree and
HEAD alike (the resolver predates today's work).

## Proposals

- No.1 bind tuple-unpack for-targets into the scope, per-slot precise types `[DENIED — host-green, pcc1-red]`
- No.2 bind tuple-unpack for-targets as DYN only `[implemented; stage1 gate running]`

## Notes

Workaround for in-flight code: rename the loop variable. The real fix is in
type_infer's name resolution for comprehension/for-target bindings versus
enclosing class attributes; it needs a focused red-first regression (the repro
above) and the stage gates, since name resolution is shared codegen.

## No.1 per-slot precise binding — DENIED, host-green / pcc1-red

The root cause is broader than the report: `_infer_stmt`'s For branch binds a
single-Name target but has NO TupleExpr arm at all — unpacked names were never
bound, resolving through `lookup_name`'s dyn fallback (accidentally fine)
until one shadowed the method's recursion seed (`param_scope.define(fn.name,
ft)` in `_infer_funcdef`) and inferred 'callable'.

The first fix bound each element to its precise slot type (matching-arity
TupleType) with the single-name branch's pre-loop dyn join. Every host-side
gate passed: the shadow repro matched CPython, the new regression passed,
closure rc=0, and a clean HEAD+fix snapshot ran the four frontend gate files
at 51 passed with zero new failures. **The stage1 pcc1 then failed its own
two-line smoke** (`def main() -> int: return 0`) with the empty
PCC-PY-COMPILE-001 — the compiler compiled by the host with this typing
change is behaviorally broken. Precise slot types move names that were
dyn-boxed for the compiler's entire life into typed lanes across the whole
codebase at once; some consumer of those lanes is wrong under self-compilation
and none of the host gates can see it. DENIED as a rider; slot precision needs
its own slice, its own minimized divergence hunt, and its own stage gates.

The empty-error diagnostic hole (`SELF-P2-EMPTY-PIPELINE-ERROR-TEXT`) blocked
direct diagnosis for the third time today.

## No.2 DYN-only binding

Same binding structure, every element bound TYPE_DYN. For non-shadowed names
this is observably identical to the old fallback (they resolved dyn anyway);
the entire behavior change is that a binding now exists, so it shadows the
recursion seed. Shadow repro matches CPython (2/-1), regression 2 passed.
Stage1 gate running.
