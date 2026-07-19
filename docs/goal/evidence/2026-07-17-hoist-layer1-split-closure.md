# AUD-P1-HOIST-LAYER1-SPLIT closure

Date: 2026-07-17

Claim boundary: split state-free nested-hoist work out of the Layer1
inheritance surface without changing Python closure semantics, and prove the
current host and no-libpython pcc1 paths. This does not claim a new closure
representation or a full rewrite of the remaining stateful rewrite pass.

## Implemented boundary

- Removed `HoistLoweringMixin` from `L1CodeGenMixinStack`; generation now calls
  the explicit `hoist_nested_funcdefs(codegen)` composition entry.
- Split closure-cell AST rewriting, free-name analysis, and yield/rewrite
  predicates into `hoist_boxing.py`, `hoist_free_names.py`, and
  `hoist_predicates.py`.
- Reduced the stateful pass method from about 2,890 lines to 1,770 lines while
  keeping the combined hoist implementation at 4,307 lines (no copied second
  implementation). Architecture tests cap both boundaries.
- Kept analyzer callbacks inside the stateful pass. Passing the local analyzer
  across a compiled-stage module boundary exposed a real pcc1 closure-capture
  misbinding; the final boxing API accepts precomputed capture names instead.
- Closed two pre-existing ratchet gaps exposed by the required gates:
  optional-float IR-pass timeouts are narrowed before native subprocess
  lowering, and pipeline default native exports now use
  `_default_native_module_exports` as their single source of truth. No fallback
  baseline was raised.

## Evidence

- Focused host hoist + timeout set: `16 passed in 3.42s`.
- Strict current-source pcc1 build completed with backend `self`,
  `--python-libpython=off`, and `--ir-scaffold=on` at
  `build/bootstrap-hoist-split-pcc1/pcc1`.
- Current-source pcc1 smoke: `54 passed, 1 deselected in 44.00s`.
- Fallback and IR-Python ratchets: `25 passed in 236.42s`; the OFF multi-file
  closure independently reported zero `py_cpy_*` calls, and all three new
  helper modules reported zero ON-mode contextual fallback calls.
- Task-board validation is recorded by the final board update. No GC matrix or
  GCC suite was run for this frontend-only structural slice.
