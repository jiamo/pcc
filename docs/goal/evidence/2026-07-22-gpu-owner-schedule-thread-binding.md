# GPU owner schedule thread-binding evidence

Task: `GPU-P1-OWNER-SCHEDULE-THREAD-BINDING`

Date: 2026-07-22

## Source identity

- Repository HEAD at validation: `672e3cccbb72f82b8998637ae83b4c3080691604`
- Worktree was dirty; this evidence applies only to the recorded source hashes.
- `pcc/kernel_ir/schedule.py`: `9456b9a65201c70d66922011938d4c413ea08f4ea7274081821e18a56b225675`
- `pcc/kernel_ir/gpu_owner_backend.py`: `80b8a6639d41ad28d537411f6c3c7ee7c0152fe52ab68a55db176e4794ff15a3`
- `tests/kernel/test_gpu_schedule.py`: `2117237cf32c96e642c2ca26d24f8106c16503c721045654766444cd9532a748`
- `tests/gpu_hardware/test_metal_claim_levels.py`: `8d57cba238240573cf7a26aee035a6e436ed3048b7ffeb6836aa9814e74f0eee`
- `tests/kernel/test_tvm_tilelang_owner_provider.py`: `b9cb763f2eb5a9b6cc1f4d83d6c381ecb79ba9710779d76d589233e69a320602`

Reference source identities:

- Apache TVM: `~/pcc_refs/apache-tvm-full-depth1` at `cfb98e938c8d9525648c75fbebcb8944edb952fe`
- TileLang: `~/pcc_refs/tilelang-full-depth1` at `dff136d4da552389b0a41f394edfa1a9fe47a590`

## Changed behavior

- Semantic `KernelModule` and transform `KernelSchedule` are separate,
  versioned records.
- A Metal `BindThreads` plan is content-addressed and guarded by its exact
  input Kernel IR digest, canonical target, function selector, expected old
  thread count, and the target-independent Metal upper legality bound.
- Replay returns a new KernelModule plus a deterministic trace. It rejects
  stale payloads/bindings, missing or duplicate selectors, target mismatch,
  invalid thread counts, and post-freeze application.
- Both `pcc-metal` and `tvm-tilelang` apply the same schedule module before
  plain-TIR freeze. The schedule digest participates in artifact identity and
  the digest plus trace appear in the owner manifest.
- Invalid schedules fail before either owner code generator and never retry
  through an unscheduled or different backend.

## Gates

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_gpu_schedule.py tests/kernel/test_gpu_owner_backend.py
..............                                                           [100%]
14 passed in 1.55s
```

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tvm_tilelang_owner_provider.py -k 'schedule or pinned_provider_compiles'
...                                                                      [100%]
3 passed, 5 deselected in 4.50s
```

The second gate invoked the pinned out-of-process TileLang/TVM provider and
proved that its input frozen IR carries the scheduled `threads=32` binding.

```text
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -k 'scheduled_copy'
.                                                                        [100%]
1 passed, 14 deselected in 0.80s
```

The strict Darwin gate submitted the scheduled copy through the pcc-metal
owner, completed its fence, matched the CPU copy oracle, released allocations,
and classified the result as `GPU_LEVEL_4_DEVICE_RESULT`. Requested and actual
owner were `pcc-metal`; `fallback_used=false`.

Black completed with all six touched Python files unchanged on the final run.

An additional `tests/kernel` adjacency run was not used as evidence because
the orchestration layer lost its final pytest summary after 37% output. The
watchdog-owned process completed and a process check found no surviving test
children. The three required gates above all have final summaries.

## Supported claim

PCC now owns one reusable GPU scheduling instruction: exact, replayable Metal
thread binding applied through the same pre-freeze module for both pcc-metal
and the pinned tvm-tilelang execution-owner adapters. The scheduled pcc-metal
copy has a real device-result proof.

## Not proven

This does not prove general schedule IR, tiling, layout transforms, software
pipelining, a Metal cost model, autotuning, binary-archive caching, CUDA/ROCm,
whole-program GPU execution, or a scheduled tvm-tilelang Level-4 device result.
Those require separately bounded task rows and gates.
