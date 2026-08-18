# Investigation: dict-splat operands bypass rooted unwind cleanup

## Status

resolved locally 2026-08-20

## Problem

The existing five-GC owned-temporary regression failed before execution while
the current source was compiled for the AArch64 Darwin self backend:

```text
self precise stack-map analysis in 'user_dict_release_prog_splat_branch':
managed root state disagrees at block join 'err.exit'
```

The reproducing source shape was a dict merge followed by an ordinary owned
value in the same literal:

```python
base = {"x": Tracked(i)}
d = {**base, "y": Tracked(i + 1)}
```

This is distinct from the resolved module-level `dict([...])` builtin bug in
`dict-builtin-module-top-stackmap-err-exit-join.md`: this failure is in the
dedicated `DictExpr` splat lowering.

## Root cause [CONFIRMED]

`_emit_dict_literal_with_splat` created a dict and entered its temporary GC
root, then evaluated every mapping/key/value with bare
`_emit_expr_as_pcc_object`.  A nested constructor could therefore branch to
the outer `err.exit` without first leaving the dict root.  The same function
also omitted post-call error checks for `py_dict_new`, `py_dict_update`, and
`py_dict_set`, and reused owned managed operands after allocating calls without
the pin/unpin cleanup used by the ordinary dict-literal path.

The precise stack-map join rejection was correct: one predecessor reached
`err.exit` with the dict root active and another reached it after ordinary
cleanup.

## Fix [CONFIRMED]

The splat path now uses the same ownership contract as the ordinary native
dict-literal path:

- nested mapping, key, and value evaluation is routed through
  `_emit_expr_with_cpy_operand_cleanup(..., rooted_pcc=((d, dict_root),))`;
- every raise-capable dict runtime call has a post-call error edge;
- movable operands are pinned across later evaluation/runtime calls;
- success and failure paths both balance pin, owned-reference release, and the
  dict temporary root.

No stack-map verifier rule, GC barrier, or Python dict behavior was weakened.

## Rejected shortcuts

- [DENIED] Relax or merge inconsistent precise root states at `err.exit`.
  That would hide a real active-root leak and permit moving collectors to scan
  a path-dependent frame layout.
- [DENIED] Disable splat error checks or special-case the regression program.
  `__hash__`, `__eq__`, allocation, mapping update, and constructors remain
  raise-capable Python operations.
- [DENIED] Release only on the normal path.  Nested operand and runtime-call
  errors require the same LIFO cleanup before joining the outer error target.

## Evidence

Working-tree source identity:

```text
literal_lowering.py sha256 a1f8a6e7fb38e66341b61fb49fa75ee272d394a207bb700e910df8289c61b161
```

Focused AArch64 Darwin self/no-libpython results, using the existing
integrity-valid explicit runtime archive so unrelated archive regeneration did
not enter the diagnostic loop:

```text
tests/python/test_dict_literal_temp_release.py             6 passed in 2.82s
tests/python/test_native_dict_merge_splat.py
tests/python/test_native_dict_update_kwargs.py             2 passed in 4.97s
```

The first formerly failing GC0 node has a durable live log at
`build/test-logs/dict-splat-stackmap-gc0.log`.  The five parametrized runtime
arms prove equal created/finalized counts under GC0..4; the adjacent tests pin
merge ordering and `dict.update`/kwargs behavior.  This evidence does not claim
that the mutable checked-in production archive is current for all other
compiler edits; final archive publication remains a separate task-board gate.
