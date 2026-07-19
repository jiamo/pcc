# DS4-P2 GPU API mapping evidence — 2026-07-17

Mode: pinned ds4 source inventory + host-Python CPU lifecycle state machine.

Pinned source:

- tree: `~/pcc_refs/antirez-ds4-depth1`
- commit: `80ebbc396aee40eedc1d829222f3362d10fa4c6c`
- `ds4_gpu.h` SHA-256:
  `1a6c5760c10251250cf1838ac2452186e938e927070c5ce30471eeef9f49baa2`

Implemented:

- exact, fail-closed inventory and mapping of all 21 declarations in the
  `GPU Tensor and Command Lifetime` section;
- pcc-owned storage through `PccBufferHandle`, bounded tensor aliases,
  selected-readback event/fence checks, and final-alias deferred free;
- explicit rejection of unknown upstream APIs, out-of-range views/readback,
  device-only raw host access, early readback, and unsubmitted-use release.

Required gate:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/ds4/test_ds4_gpu_api_mapping.py tests/kernel/test_hmm_fence.py
17 passed in 0.45s
```

Claim boundary: this is `PCC_OWNER_LIFECYCLE_MAPPING_ONLY`. The adapter does not
import/link/execute ds4 and submits no GPU command. It proves no ds4 operator,
model, cache, device-result, performance, pcc1, or five-GC support.
