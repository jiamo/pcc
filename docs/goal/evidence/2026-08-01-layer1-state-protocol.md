# L1CodeGen shared state declared, mixin collisions linted

Date: 2026-08-01

Task: `ARCH-P3-LAYER1-STATE-PROTOCOL`

## Measured first

```text
mixin classes composed by L1CodeGen        86
distinct methods across them              924
method names defined by >1 mixin            1   ->  0 after this slice
```

The single collision was `_get_pow_function`, defined identically in
`expr_helper_lowering.py:ExprHelperLoweringMixin` and
`native_math.py:NativeMathLoweringMixin`. Identical today, which is precisely
why it was worth removing: with two copies over one `self` namespace the MRO
silently picks one, and the next edit to the losing copy is dead code that
still looks live in review. The `native_math` copy is gone; the mixin keeps
calling the helper, which now has exactly one definition.

## The shared-state surface, from measurement not guesswork

`pcc/py_frontend/codegen/layer1_state.py` (new) declares `L1CodeGenState`, a
Protocol naming what a mixin may read off `self`, with the owner and validity
window written next to each attribute. The list is the measured set of
non-underscore `self.<attr>` reads across `codegen/*_lowering.py` and
`codegen/native_*.py`, by read count:

```text
builder 3987   runtime 1825   module 381   env 195   current_function 178
class_lowering 154   ast_module 88   current_func_def 70   functions 42
env_class_hint 33   loop_stack 29   current_class 20   ...
```

It is a declaration, not a mechanism: nothing enforces it at runtime and
composing it changes no behavior. Its value is that "what may I assume about
`self`?" now has one answer to read and one diff to review when it grows.

## The lint

`tests/python/test_layer1_mixin_state_protocol.py` (4 passed):

- fails if any method name is defined by two mixins, naming both sites
- asserts the lint actually scanned the mixins (>50 classes, >500 methods) —
  a source lint that silently matches nothing passes for the wrong reason
- fails if a mixin reads shared state (≥12 read sites, non-private,
  non-`emit_*`) that `L1CodeGenState` does not declare
- pins the high-traffic attributes by name

## Commands and results

```text
tests/python/test_layer1_mixin_state_protocol.py    4 passed
tests/python/test_py_multi_file_compile.py
tests/python/test_py_class_export_schema.py        42 passed
stage1: S1=0, libc imports still 64
stage2/stage3: pcc2 and pcc3 metadata-normalized byte-identical
```

## Supported claim

The shared-state surface is declared in one reviewable place, the one
same-name helper collision across 86 mixins is gone, and a lint fails if
either regresses. Zero behavior change, with the bootstrap fixed point
re-proven because the touched file is inside the stage1 closure.

## Not proven

- The Protocol is not applied as a base class or checked at runtime; doing so
  would be a composition change, which this row explicitly excludes.
- The undeclared-state lint uses a read-count threshold (≥12) so it flags
  shared state rather than per-mixin locals. A rarely read new shared
  attribute would slip past it until it becomes common.
