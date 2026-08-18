# Typed list mutators return None — 2026-08-24

## Claim

The typed-list lowering path now returns the `py_None` singleton from append,
extend, insert, remove, clear and reverse, matching Python and the already
correct dynamic-list path.  Their runtime mutations are unchanged.

The change is limited to the six prior `ir.Constant(i1, 0)` success
placeholders.  Pop, index, count, copy, membership, sorting and boolean results
retain their existing result lowering.

## Regression and causality

Before the change, a native/no-libpython program cleared and reused its list
but printed `result is None` as `False`.  The expanded regression compiles one
typed list program and proves all six mutators both return `None` and perform
their expected mutation.

The broad multi-file gate stops on the repository's recorded pre-existing
`test_borrowed_object_local_rebind_keeps_gc_root` IR-shape assertion.  A
single-variable A/B restored all six old `i1 0` returns and reproduced the
identical failure: the test expects a plain store while current codegen emits
the stricter `pcc_gc_store_root`.  The list-None change was restored after that
denial.  This known gate red is routed in
`docs/investigations/pcc1-stage2-emit-throughput-and-memory.md` and is not
claimed green here.

## Gates

- focused source plus compiled six-mutator regression: `2 passed in 0.91s`.
- complete list-method parity file: `14 passed in 8.28s`.
- bootstrap baseline: `2 passed, 2 deselected in 0.74s`.
- fallback and IR fallback ratchets: `40 passed in 491.36s`.
- A/B denial for the known multi-file IR-shape red: identical first failure
  with the candidate enabled and with all six old returns restored.
- `git diff --check`: pass.

Durable logs:

- `build/typed-list-mutator-none-bootstrap-baseline.log`
- `build/typed-list-mutator-none-fallback-baseline.log`
- `build/typed-list-mutator-none-multifile.log` (known pre-existing first red)

## Frozen identities

```text
15428311b1a6c4725b9590f5ec17b8812cb16fe1412e1efec4ef907cab99b65c  pcc/py_frontend/codegen/list_method_lowering.py
5053558063744856d10aaf4cec3ac2d1d42483b3464177485ba3d644cb157761  tests/python/test_python_list_methods_parity.py
5ecf302bb09328d8aac5763b32a396fbc00a537ff6cbe7e14ee9d41c0ee434d7  build/typed-list-mutator-none-bootstrap-baseline.log
57aa707c775f977836a8b6fa26177f38a484a8380795487d44eefbcaad6afd40  build/typed-list-mutator-none-fallback-baseline.log
```

## Status

`DONE_STRONG` for typed void-list mutator result semantics.  This does not
claim the unrelated known multi-file IR-shape assertion is current or green.
