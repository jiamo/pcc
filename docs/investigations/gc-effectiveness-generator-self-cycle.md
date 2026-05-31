# Investigation: generator self-cycle effectiveness gate

## Status
resolved

## Problem Description
`tests/test_gc_effectiveness.py::test_generator_referencing_self_collected`
is still xfail-marked as Phase G1. The test builds a cycle where a generator
is stored on an object that the generator frame can reach:

```text
box -> generator -> frame/captures -> box
```

`gc.collect()` currently returns no collected objects.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_effectiveness.py::test_generator_referencing_self_collected -q -n0 --runxfail
```

Expected current failure before the fix:

```text
AssertionError: assert 'False' == 'True'
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_effectiveness.py::test_closure_cell_cycle_collected tests/test_gc_effectiveness.py::test_generator_referencing_self_collected -q -n0 --runxfail
```

Observed result for this test:

```text
stdout was False instead of True
```

## Proposals
- No.1 Release unused owned expression-statement results     [CONFIRMED]

## No.1 Release unused owned expression-statement results
### Code Change
`py_gen_new()` tracks generator objects, and `py_obj_gc.c` traverses
`PY_TYPE_GEN -> frame/send_value`. The generated IR for the repro showed the
actual leak:

```text
%next = call ptr @py_obj_next(ptr %g)
```

The bare expression statement `next(g)` discarded that owned result without
calling `pcc_gc_release()`. Since the yielded value is `box`, this left an
external reference to the cycle and made `gc.collect()` return 0.

Patch expression-statement lowering to release owned pcc-object results after
evaluating side-effect-only expressions.
### CONFIRMED
The focused gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_effectiveness.py::test_generator_referencing_self_collected -q -n0 --runxfail
# 1 passed
```

After removing the stale xfail marker, the focused files pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s uv run pytest tests/test_gc_effectiveness.py -q -n0 -rxX
# 26 passed, 1 xfailed

env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_g1_cycle_collector.py -q -n0 -rxX
# 8 passed
```

## Report
No.1 landed. The generator self-cycle was blocked by an owned temporary from a
discarded `next(g)` expression statement, not by missing generator traversal in
the default cycle collector.
