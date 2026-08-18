# Stage2 medium emit-worker lifecycle profile

Date: 2026-08-21

Claim level: focused GC0, self-backend, no-libpython native emit-worker
diagnostic.  This is not a complete stage2 timing, a stage1/stage2 comparison,
or pcc2/pcc3 fixed-point evidence.

## Frozen input

- compiler: `build/stage2-medium-concurrency-ab-v2/input/pcc1`
  (`b2ba3969609dd0ba2b25b5c9d99cc480b606f451af57d01517001d0afda29d47`)
- runtime archive:
  `build/stage2-coordinator-live-v1/input/runtime-bundle/libpy_runtime_pcc_py.a`
  (`b42890eeca1e1387c7282be0297a9f7daadb1042c0efb65d533cf8b94375b3d0`)
- IR: `self_backend_module_98.ll`, 1,831,588 bytes,
  `47289e1d0517d365732a5d8bf420f7749082fbca9470509f4562334c12a3a23d`
- assembly oracle: `self_backend_native_262.s`, 5,666,157 bytes,
  `e536a7daf7a5c9707bbf80a2656111fc3120f964b7a67325249d3493b4515ebe`
- mode: GC0, self backend, no libpython, frontend/object caches off,
  Python IR passes off, `/usr/bin/cc` target probe.

The unsampled isolated replay completed in 17.60 seconds (16.23 user, 0.64
sys), retired 182,270,476,340 instructions, used 55,952,914,191 cycles and
reached 2,442,592,256 bytes max RSS / 2,668,218,696 bytes peak footprint.
Its result payload was byte-identical to the assembly oracle.

## Profiler correction and complete capture

The first two diagnostic captures were window-biased: `pcc_flamegraph.py`
loaded and parsed the large pcc1 symbol table before it invoked Apple
`sample`, so the nominal 0.25/1 second attach delay did not describe the true
capture start.  Real use exposed the tool defect.  The tool now captures raw
stacks first with `sample -mayDie -file`, verifies the target binary identity
before/after capture, and only then resolves symbols.  Empty reports fail
before symbol loading; a nonzero `sample` exit is accepted only when target
image and folded stacks remain valid and is labeled partial.  The focused
tool contract is `tests/python/test_pcc_flamegraph_tool.py` (14 passed before
the compiler candidate).

The corrected capture is durable at:

- `build/stage2-medium-worker-profile-v1/complete-v2.folded`, 16,032 samples,
  SHA256 `b426cb2c7e16e925ba810ab8f6c04fed20242ee6c70946a27ee668b438269ff8`
- `build/stage2-medium-worker-profile-v1/complete-v2.svg`, SHA256
  `7a2d80d40c4d72f00103f4484c1c523a106fe17464aa0dcd99a57bd63a491190`
- result SHA256 `bbd80d79d046b33542e343abbbf6bf6148354f6d0c3b6dfa97d32eda64cac3a0`;
  removing the target-id protocol line gives the exact oracle assembly hash.

The target exited normally and `sample -mayDie` retained a valid complete
lifecycle partial report.  Sampling overhead means its elapsed time is not an
acceptance wall measurement.

## Inclusive ownership

Shares are inclusive and overlap where one phase contains another.  `self`
means samples whose leaf is the named owning frame; it is near zero because
the cost is runtime/helper work requested by that owner.

| owner | samples | complete-worker share | self | infinite Amdahl ceiling |
|---|---:|---:|---:|---:|
| parse module | 3,309 | 20.64% | 0 | 1.260x |
| prepare module | 6,312 | 39.37% | 0 | 1.649x |
| assign stack slots | 1,731 | 10.80% | 4 | 1.121x |
| precise stack-map plans | 3,272 | 20.41% | 0 | 1.256x |
| function emit | 4,602 | 28.71% | 0 | 1.403x |
| instruction emit | 2,645 | 16.50% | 1 | 1.198x |
| target adjacent-memory pass | 450 | 2.81% | 3 | 1.029x |
| stack-map render | 521 | 3.25% | 0 | 1.034x |

Within `assign_stack_slots`, `collect_block_local_last_uses` owns 409 samples,
the generator-next boundary owns 297, and `collect_used_values` owns 102.
The rest is dispersed across tuple projection, owned element lifetime,
root/pin/release and hash-safe slot bookkeeping.  Removing only one child is
below the 1.08 goal; the only coherent candidate is eliminating the wide
instruction projection itself.

## Single selected source proposal and pre-registered rejection line

Implement one proposal only: stackprep-private dense instruction projection.

- Preserve `CompactParsedInstrArena` public iteration/view semantics.
- In stack-slot analysis, read the verified arena's dense kind/data arrays and
  only the tuple fields required for destination type, slot allocation and
  used-value decisions.
- Reuse a small fixed local set (`dest`, `result_type`, `allocated_type`)
  instead of binding every unused tuple member.  Preserve the old
  `value_is_used(dest)` short circuit before aggregate-return classification.
- Give used-value analysis a kind/data core with sparse indexing; do not
  reconstruct views, return helper tuples, add a cache/dict, use a mutable
  cursor, or use `typing.cast` aliases.
- Preserve duplicate-bearing `func.used_values`, stable integer text buckets,
  PHI/call-boundary lifetimes, discarded indirect aggregate slots, exact
  AArch64/x86 stack layout and every GC3/GC4 root/update invariant.

The corrected profile makes this a strict fail-first experiment.  Accept the
source candidate only if all focused semantics pass, generated native IR
shows fewer stackprep roots/projections without dynamic fallback, assembly is
byte-identical, and at least three balanced unsampled matched pairs achieve
paired median baseline/candidate wall speedup >=1.08 with user+sys and
instructions improving and RSS/footprint <=1.02x.  A first pair materially
below 1.08 may stop and DENY; no complete stage2 rebuild follows a denial.

Pre-registered focused nodes:

- `tests/python/test_pcc_flamegraph_tool.py`
- `tests/c/test_self_backend.py::test_self_backend_stack_slot_assignment_is_hash_seed_stable`
- `tests/c/test_self_backend.py::test_self_backend_shared_stackprep_assigns_arg_local_and_result_slots`
- `tests/c/test_self_backend.py::test_self_backend_stackprep_materializes_discarded_indirect_aggregate_call`
- `tests/c/test_self_backend.py::test_self_backend_text_key_recovery_survives_inconsistent_native_hashes`
- `tests/c/test_self_backend.py::test_self_backend_stackprep_treats_dot_number_values_as_ssa_names`
- `tests/c/test_self_backend.py::test_self_backend_stackprep_and_compute_keep_large_insertvalue_chains_addressable`
- new `tests/c/test_self_backend.py::test_self_backend_stackprep_dense_projection_preserves_result_types_without_public_views`
- new code-converge hardening
  `tests/c/test_self_backend.py::test_self_backend_stackprep_sparse_used_value_projection_covers_instruction_abi`
- `tests/c/test_self_backend_compact_instruction_arena.py`
- `tests/python/test_precise_stackmap_abi.py::test_target_final_planner_maps_only_explicit_registered_stack_roots`
- `tests/python/test_precise_stackmap_abi.py::test_safepoint_reloads_live_root_derived_ssa_from_rewritten_slot`
- after candidate pcc1 exists:
  `tests/python/test_pcc1_emits_native_function_binary.py::test_pcc1_compiles_and_runs_function_definitions`
  and
  `tests/python/test_pcc1_gc_backend_matrix.py::test_pcc1_self_backend_compile_smoke_under_gc_backend`,
  with both `PCC_CURRENT_PCC1` and `PCC1_BINARY` bound to the frozen candidate
  path so neither gate can reuse an older compiler.

No compiler source change had been made when this proposal and rejection line
were recorded.
