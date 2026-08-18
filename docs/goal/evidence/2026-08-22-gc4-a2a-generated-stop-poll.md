# GC4 A2a generated stop-poll acquire contract

Status: **GREEN for the two generated stop-poll load sites and their focused
host-compiled runtime proof only.**
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

The two Python-frontend paths that emit implicit thread-safepoint polls now
load the 32-bit `pcc_thread_stop_requested` flag atomically with acquire
ordering and four-byte alignment:

1. `CoreHelperMixin._emit_thread_safepoint` uses
   `self.builder.load_atomic(flag_gv, "acquire", 4, ...)`.
2. `_emit_thread_safepoint_poll_llvm`, the low-builder LLVM path, uses
   `builder.load_atomic(flag_gv, "acquire", 4, ...)`.

Generated IR contains `load atomic i32 ... acquire, align 4` at function-entry
and loop poll sites and contains no ordinary `load i32` for the stop flag.  The
existing `PCC_WITH_THREADS=0` policy remains fail-closed in the other
direction: it emits neither implicit safepoint calls nor stop-flag loads.

The dynamic gate is host-coordinated pcc-Python compilation with
`ir_scaffold_mode=on`, `libpython_mode=off`, `backend=self`, and a threaded
host-C differential runtime archive.  Its worker enters an unbounded loop
whose explicit gate load is deliberately `relaxed`; after entry, the generated
poll is therefore the worker's only acquire and only safepoint.  While the gate
remains closed, the main thread stops the world, opens the gate, yields 256
times, and proves the worker cannot leave the loop until `pcc_resume_world`.
It then joins the worker and observes the exact output
`generated-stop-poll-ok`.  Natural loop completion cannot satisfy this proof.

## Frozen source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_frontend/codegen/core_helpers.py` | `4091c6b4642c5fb683d503eaa97e92efc38ddfbe3efc59d92e58196b380d2e2a` |
| `pcc/py_frontend/codegen/user_function_lowering.py` | `2e769c623c9cfad98a6a2c58e93f8df42f6af745bdb4ea43e1f43c24373c6b1a` |
| `tests/python/test_gc_threading_substrate.py` | `90084ea77389b727c2866b2de24f420b91a9c820178f338df059f0e9d5b8165e` |

These are dirty-worktree content identities, not a clean commit or release
manifest.  The A2a implementation change is exactly the two atomic-load
replacements above; adjacent ABI/runtime edits in the shared worktree belong
to the already-recorded A1 substrate, not to this slice.

## Exact focused generated-poll gate

```bash
gtimeout 180s zsh -o pipefail -c 'gtimeout 150s env -u LC_ALL -u PCC_RUNTIME_ARCHIVE -u PCC_WITH_LIBPYTHON -u PCC_REFCOUNT_KIND -u PCC_WITH_THREADS uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_python_codegen_emits_thread_safepoint_at_loop_backedges_and_function_entry tests/python/test_gc_threading_substrate.py::test_python_codegen_ir_contains_loop_and_entry_thread_safepoints tests/python/test_gc_threading_substrate.py::test_python_codegen_zero_thread_env_disables_implicit_safepoints tests/python/test_gc_threading_substrate.py::test_python_codegen_compiled_hot_loop_parks_via_generated_acquire_poll 2>&1 | tee build/gc4-a2a-generated-stop-poll-final-root.log'
```

Observed result: **4 passed in 1.24 seconds**, with a final pytest summary.
The durable log is
`build/gc4-a2a-generated-stop-poll-final-root.log`, SHA-256
`a1ee5f534eb605770ca7bbd0ca7d406f5665965bacf902df8ab58540d3a8ab6f`.

An earlier source-identical four-node v4 capture also passed, **4 passed in
1.40 seconds**.  Its log is
`build/gc4-a2a-generated-stop-poll-final-v4.log`, SHA-256
`b176bbe71ceba9bfac25b150fb714a893d43e53fdd6d42f6d345f60b5c80fc5b`.
The root rerun above is the canonical final result.

## Atomic-backend neighbor gate

```bash
gtimeout 150s zsh -o pipefail -c 'gtimeout 120s env -u LC_ALL -u PCC_RUNTIME_ARCHIVE -u PCC_WITH_LIBPYTHON -u PCC_REFCOUNT_KIND -u PCC_WITH_THREADS uv run pytest -vv -x -n0 --tb=short tests/c/test_llvm_capi_ir_parity.py::test_atomic_load_store_ops tests/python/test_unsafe_atomics.py::test_atomic_intrinsics_emit_ordered_llvm_shapes tests/python/test_unsafe_atomics.py::test_atomic_intrinsics_lower_to_aarch64_exclusives 2>&1 | tee build/gc4-a2a-atomic-backend-neighbors.log'
```

Observed result: **3 passed in 0.50 seconds**, with a final pytest summary.
The durable log is
`build/gc4-a2a-atomic-backend-neighbors.log`, SHA-256
`d07d5f74dde8dd8441893f2cd10abc28ff6a739d99fec9abd862069d8c969d59`.
These neighboring nodes cover llvm_capi atomic-load/store IR parity, ordered
Python unsafe-atomic LLVM shapes, and AArch64 self-backend exclusive lowering.
They are backend-neighbor evidence, not a broad backend suite.

## Failed llvmlite-PY oracle attempt

A separate llvmlite-PY IR-oracle attempt did not reach the generated poll.  It
failed in the pre-existing runtime-ABI attribute path because
`_apply_runtime_function_attrs` accessed
`FunctionAttributes._attrs`, which that llvmlite object does not expose:

```text
AttributeError: 'FunctionAttributes' object has no attribute '_attrs'
```

The artifact is `build/gc4-a2a-llvmlite-ir-oracle.log`, SHA-256
`b5146e2205483fee3109e0bdc3129e6ccaaf69a443dd68cb22c75a9ed72e89d2`;
its final summary is **1 failed in 0.16 seconds**.  This is not an A2a
atomic-poll regression, but it remains a red adjacent boundary.  No
llvmlite-PY green claim follows from this evidence.

## Independent review

An independent read-only review of the two atomic-load changes and the exact
focused test boundary at the hashes above reported **ZERO findings**.

## Explicit nonclaims

This evidence does **not** prove:

- the pre-existing llvmlite-PY runtime-ABI attribute failure is fixed;
- graph-lock depth or any complete list/dict/set/other raw-access transaction
  uses the A1 no-park protocol;
- callback-split roots, managed thread argument/result roots, constructor
  publication, C-API raw-view lifetimes, or balanced buffer leases;
- collector-owned copy/drain/page-drain/idle-remap/target-dies phase coverage;
- forwarded-source payload retirement or any physical Backend 4 motion under
  concurrent mutators;
- the strict pcc-Python runtime, pcc1 execution ownership, GC0..4 parity,
  stage1/stage2 timing, stage2 performance, pcc2/pcc3 equality, or the
  self-hosted fixed point.

No broad suite, bootstrap stage, physical relocation test, stage measurement,
or five-GC matrix was run for this bounded A2a slice.
