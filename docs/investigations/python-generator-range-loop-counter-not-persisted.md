# Investigation: range(...) for-loop inside a generator stops after the first yield

## Status
resolved

## Problem Description

A generator whose body iterates `for <name> in range(...)` and `yield`s inside
the loop produces only the **first** item, then raises `StopIteration`. The
same generator shape driven by a list literal or a `while` loop resumes
correctly. This shape is extremely common (`for i in range(n): ... yield ...`)
and is on the numpy generator path, so it blocks compiling real package
generators in no-libpython mode.

Distinct from, but found alongside, the `yield a, b` mis-parse
([python-yield-tuple-misparse-leaks-yield-sentinel.md](python-yield-tuple-misparse-leaks-yield-sentinel.md))
and the still-open self-backend `.owned.N` generator-emission gap (which caps
the numpy auto-mode diagnostic at 149 modules). This bug is a *correctness*
bug: the program compiles and runs, but emits wrong output.

## Repro

```python
def count(n: int):
    for i in range(n):
        yield i + 100

def main() -> None:
    for v in count(3):
        print(v)

if __name__ == "__main__":
    main()
```

```
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/g.py -o /tmp/g.out && /tmp/g.out
```

Observed (pcc): `100` only. Expected (CPython): `100 101 102`.

Reduction matrix (all `--backend self --python-libpython=off`):

| case | generator loop iterable | result |
|---|---|---|
| g1 | `for x in items:` (list arg)   | ✓ all items |
| g6 | `for x in [0,1,2]:` (list lit) | ✓ all items |
| g7 | `while i < 3:` (manual counter)| ✓ all items |
| g4 | `for i in range(3):`           | ✗ first only |
| g8 | `for i in range(3): yield 99`  | ✗ first only |
| g3/g5 | `for i in range(3): yield i, i+10` | ✗ first only |

The discriminator is **`range(...)` vs every other iterable**, not the yielded
value shape.

## Test [CONFIRMED]

`tests/python/test_python_generator_parity.py::test_generator_range_loop_resumes`
(range count, range tuple-yield, `range(start, stop, step)`). Confirmed failing
before the fix (first item only) and passing after. The full file
(`-q -n0`) is the gate: 7 passed.

## Proposals

- No.1 Route range(...) through the resumable object-iterator inside generators  [CONFIRMED]
- No.2 Persist the inline range induction counter in the generator frame  [DENIED — more invasive, no upside]

## No.1 Route range(...) through the resumable object-iterator inside generators

### Code Change

`pcc/py_frontend/codegen/for_loop_lowering.py::_emit_for`, right after
`is_range_call` is computed:

```python
if is_range_call and len(self._generator_ctx_stack) > 0:
    range_list = self._emit_range_value_call(stmt.iter)
    return self._emit_for_obj_iterator(stmt, range_list)
```

### CONFIRMED

Root cause: `for ... in range(...)` takes the inline "L1 fast path" whose loop
counter lives in a raw entry-block `alloca` (`range.value.idx.addr`). The
generator state machine persists only the names returned by
`_collect_generator_frame_names` (function args, assignment targets, the hidden
per-`For` iterator slot `__pcc_for_iter_<line>_<col>`, ...) — it does **not**
persist anonymous fast-path allocas. The generator resume function is a fresh
call each time; on resume it reloads the frame slots from the heap frame and
jumps to the post-`yield` block, but the range counter `alloca` is fresh
(reset). So the loop terminates after the first item.

List/tuple/dict/str/dyn iterables inside a generator already route to
`_emit_for_obj_iterator`, which stores the iterator pointer into the persisted
hidden frame slot (`__pcc_for_iter_<line>_<col>`) and reloads it on every
header iteration — exactly the resumable mechanism. The fix makes range reuse
it: materialise `range(...)` as a list (`_emit_range_value_call`, which already
builds `range.list`) and drive the same path. The hidden frame slot is already
allocated for *every* `For` in the generator body regardless of iterable type
(`_collect_generator_frame_names` line ~241), so no frame-layout change is
needed.

Non-generator range loops are untouched (the new branch is gated on
`_generator_ctx_stack > 0`); they keep the unboxed-i64 inline fast path.

Caveat (noted, not blocking): inside a generator, `range(n)` now materialises
an n-element list up front instead of an O(1) induction variable. Correct, but
a large `range` inside a generator costs O(n) memory. A follow-up could add a
lazy native range-iterator object so `py_obj_iter`/`py_obj_next` walk it
without the list. Generators over huge ranges are rare; correctness first.

Evidence:
- `tests/python/test_python_generator_parity.py -q -n0` -> 7 passed (incl. the
  new `test_generator_range_loop_resumes`; default llvm backend, so the fix is
  backend-independent frontend lowering).
- Reduction matrix g1–g8 + g10 (non-generator range) under `--backend self`:
  all ✓ after the fix; g10 confirms no non-generator regression.
- Mandatory self-host gate
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 323.49s (stage1 -> stage2 -> stage3 self-backed).

## No.2 Persist the inline range induction counter in the generator frame

### DENIED — more invasive, no upside

Would require teaching `_collect_generator_frame_names` to reserve a slot for
the anonymous `range.value.idx` counter (keyed by span), and the range fast
path to load/store that counter through a frame slot rather than a raw alloca,
inside generators only. That is strictly more code than No.1, touches the
unboxed-i64 fast path (risking the common non-generator case), and yields only
the memory micro-optimisation that No.1's caveat defers. No.1 reuses a path
that is already proven correct for list/tuple/etc. inside generators.

## Report

Landed No.1. The one-branch reroute is minimal, reuses the
already-correct resumable iterator mechanism, and leaves the hot non-generator
range path on the unboxed fast path. DENIED No.2 was the alternative
(frame-persisted counter) — more invasive, only a memory micro-win.

Follow-up (separate bug, same *class*, NOT fixed here): `enumerate(...)` inside
a generator. `enumerate` desugars (`_normalise_for_enumerate`) to an inner
`for v in xs` loop (resumable — now correct) plus a **synthetic counter**
`__enum_i` allocated as a raw entry-block alloca and registered directly in
`self.env`. That synthetic name is created during `_emit_for`, *after*
`_collect_generator_frame_names` already ran on the original AST, so it is not
persisted. Inside a boxed-int generator the counter slot is a `PyObject*` that
reloads as NULL on resume — so `for idx, v in enumerate(xs): yield idx, v`
yields `0 a`, then `<null> b`, `<null> c` (values resume, index does not). The
fix is to add a deterministic span-keyed counter name
(`__pcc_enum_cnt_<line>_<col>`) to `_collect_generator_frame_names` for
`enumerate`-iter `For`s and have `_normalise_for_enumerate` store its init into
that persisted frame slot instead of a fresh alloca. Tracked as the next slice.
