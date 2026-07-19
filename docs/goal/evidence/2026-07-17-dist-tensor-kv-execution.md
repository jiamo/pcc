# Bounded tensor gradient and KV ownership execution — 2026-07-17

Task: `DIST-P1-TRAINING-INFERENCE-EXECUTION`.

## Claim

The localhost TCP collective owner now executes one pcc-buffer-owned f64
gradient allreduce and one canonical `BlockManager` ownership transfer. Results
match the independent CPU collective/KV serialization oracles, no transport
fallback occurs, and both transfer scratch buffers are reclaimed only after
their fences complete.

This proves a bounded CPU/POD execution bridge. It does not prove framework
training, PyTorch/MLX integration, GPU tensors, a full model, elasticity,
serving, or inference throughput.

## Behavior evidence

- Two spawned ranks own distinct `PccOwnedCpuTensor` gradients and execute a
  real TCP allreduce. Both receive `[11.0, 22.0, 33.0, 44.0]`, exactly matching
  the single-process collective oracle.
- Rank 0 broadcasts a fixed-size POD packet carrying a pinned-prefix
  `BlockManager`. Rank 0's ownership is released; rank 1 reconstructs the exact
  canonical serialized state and both ranks record the same SHA-256.
- Operation sequences are 0 (gradient) and 1 (KV), requested/actual backend is
  `tcp-ring`, and `fallback_used` is false.
- Each operation creates a `PccBufferHandle` scratch buffer, schedules it on a
  `PccFenceToken`, completes the fence after synchronous transport completion,
  and verifies the final state is `freed`.

## Gate

- Task board: `OK: 104 tasks validated`.
- `tests/dist/test_tensor_kv_execution.py`: `3 passed in 0.44s`.

No GCC suite, bootstrap, five-GC matrix, framework, GPU, or multi-Mac gate was
run for this finite CPU transport-execution contract.
