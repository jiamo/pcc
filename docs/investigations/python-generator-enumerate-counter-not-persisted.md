# Investigation: enumerate(...) index inside a generator resets to <null> after the first yield

## Status
resolved

## Problem Description

`for idx, v in enumerate(xs): ... yield idx, v` inside a generator yields the
correct *value* on every iteration but the *index* is correct only on the first
item and then comes back as `<null>`:

```
0 a
<null> b
<null> c
```

Same bug *class* as
[python-generator-range-loop-counter-not-persisted.md](python-generator-range-loop-counter-not-persisted.md)
(synthetic loop state not persisted in the generator frame), but a different
touchpoint. That doc's `## Report` precisely predicted this fix; this file
records landing it.

## Repro

```python
def walk(xs):
    for idx, v in enumerate(xs):
        yield idx, v

def main() -> None:
    for i, val in walk(["a", "b", "c"]):
        print(i, val)

if __name__ == "__main__":
    main()
```

`env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on
/tmp/g.py -o /tmp/g.out && /tmp/g.out` → `0 a / <null> b / <null> c`. Expected:
`0 a / 1 b / 2 c`.

## Test [CONFIRMED]

`tests/python/test_python_generator_parity.py::test_generator_enumerate_resumes`
(enumerate, enumerate with start, whole-tuple `for pair in enumerate(xs)`).
Confirmed failing before the fix (index `<null>` after first item), passing
after. Full file `-q -n0`: 8 passed. Uses string-element lists deliberately —
int-list iteration is independently broken in the current worktree by unrelated
uncommitted edits (see Report).

## Proposals

- No.1 Reserve a deterministic frame slot for the enumerate counter  [CONFIRMED]

## No.1 Reserve a deterministic frame slot for the enumerate counter

### Code Change

1. `generator_lowering.py`: add `_generator_enum_cnt_name(stmt)` →
   `f"__pcc_enum_cnt_{span.line}_{span.col}"` (mirrors the existing
   `_generator_for_iter_name`).
2. `generator_lowering.py::_collect_generator_frame_names`: in the `For` branch,
   when `s.iter` is a `Call` to `enumerate`, append the deterministic counter
   name to `frame_names` so the resume function creates a persisted slot for it.
3. `for_normalization_lowering.py::_normalise_for_enumerate`: when inside a
   generator (`_generator_ctx_stack > 0`), use the deterministic name and a
   boxed `DynType` counter; initialise it by storing a boxed int into the
   already-created frame slot instead of a fresh entry-block alloca; set the
   index-assign `annotation=None` so the boxed value stores into the DynType
   index frame slot. The non-generator path keeps the unboxed native-int
   alloca fast path unchanged.

### CONFIRMED

Root cause: `_normalise_for_enumerate` desugars `for idx, v in enumerate(xs)` to
an inner `for v in xs` (which routes to the resumable object-iterator inside a
generator — values resume correctly) plus a synthetic running counter
`__enum_i`. That counter was allocated via `_alloca_in_entry` and registered in
`self.env` *during* `_emit_for`, i.e. after `_collect_generator_frame_names`
already walked the original AST. The fresh name was therefore never in
`frame_names`, never persisted. In a boxed-int generator the counter slot holds
a `PyObject*`; the resume function is a fresh call, the alloca is fresh, and the
post-`yield` resume reloads only `frame_names` from the heap frame — so the
counter reads back as NULL.

The fix makes both sides agree on a deterministic span-keyed counter name so the
counter becomes a real `frame_name` with a heap-backed slot that spills on yield
and reloads on resume, exactly like any other generator local. The boxed-int
arithmetic (`idx = cnt; cnt += 1`) on a DynType frame slot is the same path the
fibonacci generator test already exercises.

Evidence:
- `tests/python/test_python_generator_parity.py -q -n0` → 8 passed (incl. new
  `test_generator_enumerate_resumes`; default llvm backend → backend-independent
  frontend lowering).
- Repros g9 (enumerate), g11 (enumerate start), g13 (whole-tuple), g16
  (enumerate-with-start over a string list) all ✓ under `--backend self`.
- `tests/python/test_python_iteration_parity.py
  tests/python/test_py_for_generic_iterable.py -q -n0` → 11 passed, 2 failed,
  where the 2 failures are the **pre-existing** int-list-iteration bug (below),
  not caused by this change.
- Mandatory self-host gate
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  → 1 passed in 46.89s (stage1 → stage2 → stage3 self-backed).

## Report

Landed No.1. Mirrors the resolved range-loop fix's prediction. The non-generator
enumerate path is untouched (unboxed native-int fast path).

Separate pre-existing bug found while testing (NOT this change; tracked):
**plain `for v in [<int list>]` is broken in the current worktree.** Under the
llvm backend it fails to link (`'%for.elem.i64.N' defined with type 'i64' but
expected 'ptr'`, an i64-into-ptr store in `_emit_for_list_index`); under
`--backend self` it silently miscompiles to a zero-iteration loop (empty
output). It is independent of both generator fixes (it uses neither `range`,
`enumerate`, nor `yield`), reproduces with `for v in [10, 20]: print(v)`, and is
attributable to the concurrent stopped agent's uncommitted edits to
`call_expression_lowering.py` / `py_class.py` flagged in
`docs/current-goal-state.md`. `test_for_over_list` and
`test_for_loop_falls_back_to_object_iterator_for_pointer_typed_iterable` gate
it. Bootstrap stays green, so it does not block self-host, but it is a serious
regression in a very common construct and is the next investigation.
