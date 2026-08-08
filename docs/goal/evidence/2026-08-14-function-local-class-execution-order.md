# Function-local class execution order — current-source evidence

## Claim boundary

Function-local class method bodies are predeclared for native code generation,
while the class object itself is constructed at the source statement on every
outer-function invocation.  The resulting binding is an owned, updateable GC
root local; instance construction reads that local class object and attaches
invocation-local captures using normal private-name mangling.

## Evidence

- Dedicated strict self/no-libpython differential: `2 passed in 3.21s`.
  The executable ran under `PCC_GC_BACKEND=0..4` and proved two-call class
  identity, per-call decorator effects, captures `3` and `7`, and unchanged
  `ValueError("class boom")` propagation.
- The IR-shape node proved the local class slot contains
  `pcc_gc_frame_enter`, `pcc_gc_store_root`, `pcc_gc_load_ptr`, balanced frame
  leave paths, and the mangled capture attribute.
- Adjacent nested-hoist, closure, decorator, symbol-collision, and class schema
  cluster: `26 passed in 10.15s`.
- Strict contextual self-host fallback probes: `class_gen`/`type_abi_lowering`
  gate `1 passed in 16.94s`; `stmt_dispatch_lowering` reported exactly zero
  fallback actions.
- Focused `py_compile` and `git diff --check` passed.

## Remaining boundary

The repository-wide final frozen-source `pcc1 -> pcc2 -> pcc3` sequence has not
yet run.  This slice therefore remains `DONE_WEAK` until the deliberate final
current-pcc1 strict self/no-libpython differential is green; no stale HARNESS
compiler was used as evidence.
