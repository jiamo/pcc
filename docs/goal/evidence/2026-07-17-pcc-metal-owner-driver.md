# pcc Metal execution-owner driver evidence

Date: 2026-07-17

Task: `GPU-P0-PCC-METAL-OWNER-DRIVER`

## Outcome

`pcc.kernel_ir.gpu_owner_backend` is the first common GPU execution-owner
boundary.  `get_gpu_backend_driver("pcc-metal")` selects exactly the pcc Metal
owner; `auto`, the not-yet-implemented `tvm-tilelang` owner, and unknown owner
names fail closed without probing another backend.

`PccMetalGpuBackendDriver` implements:

```text
capabilities -> validate -> compile -> package -> launch -> synchronize -> destroy
```

Validation freezes Kernel IR to canonical Metal plain TIR and rejects
CUDA-only input before code generation.  Compile selects an allowlisted Metal
pipeline and writes a content-addressed source artifact.  Package validates
`PccPackedArgs` and the host/device launch boundary.  Launch uses the existing
real Metal runtime-source package path; synchronize requires a completed pcc
fence, and destroy requires fence-safe native-allocation release.

Every owner result records requested and actual owner, semantic/codegen/runtime
owners, target, provider/driver identity, canonical frozen-IR hash, pipeline,
artifact hashes, launcher/provider libpython facts, claim level, and
`fallback_used=false`.  Identity mismatch, fallback, PyObject device input,
unsupported target, and unsupported pipeline are errors.

## Gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_gpu_owner_backend.py \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
12 passed in 6.78s

gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_copy_runtime_source_device_result_or_skip \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_tilelang_gemm_runtime_source_device_result_or_skip -rs
2 passed in 2.51s

gtimeout 360s env -u LC_ALL bash scripts/bootstrap.sh --backend self \
  --out-dir build/bootstrap-pytest-shared-stage1 --stage 1
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=67917

gtimeout 600s env -u LC_ALL PCC_REQUIRE_CURRENT_PCC1=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_real_runtime_source_copy \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_imported_tilelang_gemm -rs
2 passed in 5.58s

gtimeout 600s env -u LC_ALL PCC_RUN_GPU_5GC_LIFETIME=1 \
  PCC_REQUIRE_CURRENT_PCC1=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_copy_owner_lifetime_real_or_skipped \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_lifetime_real_or_skipped -rs
2 passed in 7.35s
```

The Level-6 copy gate builds and validates the Level-5 no-libpython launcher
once, then executes that exact binary under `PCC_GC_BACKEND=0..4`.  The scalar
GEMM gate follows the same one-binary/five-runtime-selection rule.  Both prove
device readback, CPU-oracle match, completed fence, and release after the
fence.

## Mode and claim boundary

```text
Level 4 host_backend=host-Python, launcher_links_libpython=true
Level 5 host_backend=self/pcc1, launcher_links_libpython=false
Level 6 host_backend=self/pcc1, launcher_links_libpython=false, GC=0..4
gpu_backend requested=actual=pcc-metal
provider_process_links_libpython=false
fallback_used=false
```

The GEMM source starts from pcc's strict TileLang source subset, but execution
ownership is `pcc-metal`; this is not upstream TileLang/TVM runtime execution.
The evidence proves one f32 copy and one scalar tiled GEMM on the local Metal
device.  It does not claim `gpu-owner=tvm-tilelang`, arbitrary TileLang, CUDA,
ROCm, whole-program GPU execution, or cross-machine performance.  No full GCC
or five-GC compiler-bootstrap matrix was run.
