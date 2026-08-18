# GC4 py_obj_sorted callback-owner pins — 2026-08-24

## Claim

C and strict pcc-Python `py_obj_sorted` now hold a constant number of movement
pins across callback-capable operations: the input, output, iterator, strict
dict-key list, and merge scratch list.  Every failure and return path balances
the pins before releasing ownership or returning the result.

This keeps `py_obj_len`, iterator protocol calls, list appends/growth and rich
comparisons from invalidating raw list/object addresses used by the surrounding
native sort transaction.  The pin count is O(1) in input length and does not
change Python comparison, iteration or stable-sort semantics.

## Attribution correction

The first run of this identical pin shape failed during the 500-item Backend-4
merge and was recorded as denied.  A candidate-off control reproduced the same
failure before `py_obj_sorted` was entered; the independent large-list probe
localized it to payload-span growth at append index 256.  After the external
span fix, candidate-off sorting passed, then the identical pin candidate was
reapplied and passed all focused gates.  The old failed run remains in the
investigation as historical evidence, but its causal attribution is withdrawn.

## Gates

- Backend-3/4 500-item merge, static balance contract, compiled C/strict pin
  balance, and GC4 length-less custom iterator:
  `5 passed in 210.60s`.
- source/ABI/GC abstraction neighbors: `32 passed in 11.17s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 146.54s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-py-obj-sorted-callback-pins-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
67f12437d12ec19b576407a4c00550dc99fed5836d44da49680c628e398e81e1  pcc/py_runtime/src/py_obj_ops_compare.c
f9026ea9ab411dec0322bf4dfadd751446da630b8d171675c28aa8a9e411abdd  pcc/py_runtime/py/py_obj_ops_compare.py
c52252a3f8f1a1faf7f01abc043e74d471dcd55afaa94347d9b1d38a4d85b7a3  tests/python/test_gc_threading_substrate.py
c04ea1f489e971de358ff7b3357d118c74a47e5f60b69a35befdb6ef6dbb163a  tests/python/test_native_sorted_custom_iterator.py
ca327755aee85b34b3f634d9a1e22280bfc7475efe8afa1c011362d0d9a9d98b  build/gc4-py-obj-sorted-callback-pins-final.log
d25cb677ea03591c54072cf91133bfa4164fc3b1e2938b242d590ee5a8ad55fb  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.14 `py_obj_sorted` callback-owner pinning.  Other
callback families and the GC4 parent task remain open.
