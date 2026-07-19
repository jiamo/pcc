# Pinned TVM/TileLang execution-owner evidence

Date: 2026-07-17

Task: `GPU-P0-TVM-TILELANG-OWNER-DRIVER`

## Outcome

`get_gpu_backend_driver("tvm-tilelang")` now selects a real optional execution
owner behind the same `GpuBackendDriver` boundary as `pcc-metal`:

```text
capabilities -> validate -> compile -> package -> launch -> synchronize -> destroy
```

Semantic ownership remains in pcc.  The driver accepts canonical pcc
`plain_tir_freeze` input, hashes it before the provider, invokes the provider
out of process, packages the returned device source through `PccPackedArgs`,
launches through the pcc Metal runtime-source/fence path, compares readback to
the CPU oracle, and requires fence-safe release.  Requested and actual owner
are both `tvm-tilelang`; no unavailable, incompatible, unsupported, or
CUDA-only case probes `pcc-metal` as fallback.

The provider pin is inspectable in
`pcc/kernel_ir/tvm_tilelang_provider_pin.json`:

```text
TileLang revision ed00dfcd7f9c200e1150896b1be59c41ff3e8d9d
TileLang version  0.1.11+gited00dfcd
TVM revision      1ecfcc2e1e1fb9f75db9ed760a97aa9687372905
TVM version       0.25.dev0
```

The pin also fixes hashes for the TileLang Metal pipeline/codegen/lowering
sources and the local `libtilelang`, `libtvm_compiler`, `libtvm_ffi`, and
`libtvm_runtime` builds.  `scripts/tvm_tilelang_owner_provider.py` runs with
`python -I -S`, a sanitized environment, exact source/site-package paths, a
strict request/response schema, and the ordered `tilelang-metal-v1` pass
allowlist.  Ambient `PYTHONPATH`, `PYTHONSTARTUP`, pass-diff/plugin settings,
path escapes, pin drift, response reuse, provider nonzero exit, backend
mismatch, and unsupported semantics fail closed.

TileLang legitimately changes the device ABI spelling: it appends `_kernel`
to the entry and may order output buffers before inputs.  pcc therefore
re-imports the bounded provider source and permits only two mechanical ABI
adaptations: rename the sole Metal kernel entry to the pcc logical entry, and
rewrite each named `[[buffer(i)]]` to its canonical pcc packed-argument index.
Unknown/missing/duplicate bindings or multiple kernels are rejected.  The raw
provider source and pcc-ABI-adapted source are stored and hashed separately;
the adapted provider source is the artifact actually compiled and launched.

## Gates

Pinned provider protocol, common driver, copy/GEMM compile, dependency hashes,
ambient isolation, unavailable/incompatible pin, pass allowlist, and
CUDA-only/unsupported fail-closed behavior:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_gpu_owner_backend.py \
  tests/kernel/test_tvm_tilelang_owner_provider.py -rs
15 passed in 7.47s
```

Strict real-device differential gate.  Each workload is compiled and launched
once by `tvm-tilelang` and once by `pcc-metal`; both match the CPU oracle and
their readback matrices match each other:

```text
gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_tvm_tilelang_copy_matches_pcc_metal_owner \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_tvm_tilelang_gemm_matches_pcc_metal_owner -rs
2 passed in 5.60s
```

Level 5 uses the already-built current stage-1 compiler explicitly.  The
provider runs at device-artifact build time; the resulting pcc1 launcher is
compiled with `--python-libpython=off`, is checked with `otool`, embeds the
provider-produced source, launches real Metal work, and records the provider
process dependency separately:

```text
gtimeout 300s env -u LC_ALL PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tvm_tilelang_owner_copy -rs
1 passed in 3.81s
```

Level 6 builds that no-libpython launcher once, then runs the exact executable
under each runtime collector selection `PCC_GC_BACKEND=0..4`:

```text
gtimeout 360s env -u LC_ALL PCC_RUN_GPU_5GC_LIFETIME=1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tvm_tilelang_copy_owner_lifetime_real_or_skipped -rs
1 passed in 3.74s
```

The parameterized legacy/default owner branch was also rerun after the shared
Level-5/6 test refactor:

```text
gtimeout 360s env -u LC_ALL PCC_RUN_GPU_5GC_LIFETIME=1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_copy_owner_lifetime_real_or_skipped -rs
1 passed in 2.04s
```

## Mode and claim boundary

```text
semantic_ir_owner=pcc-kernel-ir-tirx
requested_gpu_backend=actual_gpu_backend=tvm-tilelang
codegen_owner=pinned-tvm-tilelang-provider
runtime_owner=pcc-metal-runtime-source
fallback_used=false

Level 4 launcher_links_libpython=true
Level 5/6 launcher_links_libpython=false
provider_process_links_libpython=true
Level 6 GC coverage=0,1,2,3,4
```

This proves the pinned local provider for the bounded Metal f32 copy and
non-transposed f16×f16→f32 tiled-GEMM slices.  It does not claim arbitrary
TileLang Python, arbitrary TVM passes, CUDA/ROCm, runtime `import tilelang` in
pcc-native mode, whole-program GPU ownership, an in-process/no-libpython
provider, or performance superiority.  No full GCC suite and no five-GC
compiler-bootstrap matrix was run.
