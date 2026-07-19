# pcc ds4 Inventory + External Oracle (DS4-P0-INVENTORY-ORACLE)

Status: inventory-oracle-only. **No pcc ds4 support is claimed.**

This document is the first ds4 slice. It pins the external reference commit and
inventories the C / Metal / CUDA / ROCm / GGUF / distributed / KV / SSD surfaces
plus the external oracle vectors, *before* any compile or runtime migration.
pcc does not compile, run, or GPU-execute any ds4 source in this slice.

Machine-readable form: `tests/ds4_oracle/ds4_inventory.json`
Gate: `env -u LC_ALL uv run pytest -q -n0 tests/ds4_oracle/test_ds4_inventory.py`

## Pinned reference

- Name: antirez ds4 / DwarfStar
- Local path (external, out-of-repo): `~/pcc_refs/antirez-ds4-depth1`
- Pinned commit: `80ebbc396aee40eedc1d829222f3362d10fa4c6c`
- Role: external oracle and long-term migration target only. Later
  compile / runtime / GPU-execution claims are separate slices and must not
  read this inventory as evidence of support.

## Claim boundary

- `pcc_support_claimed = false`
- `stage = "inventory-oracle-only"`
- This is inventory metadata + external oracle vector definitions, not a
  compile/runtime/GPU result.

## Surface inventory (per category)

C/runtime core (engine + frontends + deps):

- `ds4.c` (27791) engine core: model load, tokenizer, tensor graph, session
  eval, sampling, DSV4 payload save/load
- `ds4.h` (335) public engine/session API
- `ds4_kvstore.c` (1359) / `ds4_kvstore.h` (218) on-disk prompt KV cache store
- `ds4_ssd.c` (181) / `ds4_ssd.h` (36) SSD/streaming memory planning
- `ds4_server.c` (15875) HTTP / OpenAI-compatible server frontend
- `ds4_cli.c` (1707) interactive CLI frontend
- `ds4_agent.c` (10244) agent / tool-use runtime frontend
- `ds4_bench.c` (683) benchmark harness
- `ds4_eval.c` (4289) evaluation harness
- `ds4_web.c` (1385) / `ds4_web.h` (33) minimal web helper
- `ds4_help.c` (559) / `ds4_help.h` CLI help text
- dependencies: `rax.c` / `rax.h` / `rax_malloc.h` (radix tree),
  `linenoise.c` / `linenoise.h` (line editing)

GPU API surface (declared once, backend-neutral):

- `ds4_gpu.h` (1024): opaque handle `ds4_gpu_tensor`; 86 entrypoints total.
  Lifecycle: `ds4_gpu_init/cleanup`, `ds4_gpu_tensor_alloc[/_managed/_view]`,
  `_free`, `_write/_read/_copy`, command `begin/flush/end` + `synchronize`,
  and selected-readback event/fence calls. Streaming: `set_model_map[_range/
  _spans]`, `set_model_fd`, `cache_model_range`, `stream_expert_cache_*`.
  Per-primitive ops: embed, indexer score/topk, argmax, repeat, rms_norm
  (plain/weight/rows/head), rope_tail, store_raw_kv, compressor
  (update/store_batch/prefill), attention (decode/prefill, raw/mixed/masked/
  indexed heads), swiglu, add, router_select, routed_moe (one/batch),
  hc weighted-sum / expand family.

Metal host + kernels:

- `ds4_metal.m` (26819) Metal host runtime implementing `ds4_gpu.h` on macOS
- `metal/*.metal` (19 kernels): `cpy` (copy), `bin`, `dense` (q8 matmul),
  `dsv4_kv` (KV), `dsv4_rope` (RoPE), `flash_attn` (attention), `moe` (MoE),
  `norm`, `softmax`, `repeat`, `sum_rows` (sum), plus `argsort`, `concat`,
  `dsv4_hc`, `dsv4_misc`, `get_rows`, `glu`, `set_rows`, `unary`

CUDA / ROCm:

- `ds4_cuda.cu` (13256) CUDA backend implementing `ds4_gpu.h`
- `ds4_rocm.cu` (131) / `ds4_rocm.h` (142) ROCm/HIP backend
- `rocm/*.cuh` (22 headers): common, runtime, matmul, q8, attention[/_launch],
  moe[/_launch], norm_rope, indexer, compressor, router, hc, embedding, fp8_kv,
  shared_expert, hipblaslt, current_api_compat, output, misc_launch
- quant tables: `ds4_iq2_tables_cuda.inc` (77),
  `ds4_streaming_hotlist.inc` (13334)

GGUF tooling:

- `gguf-tools/deepseek4-quantize.c` (1908) quantizer entry
- `gguf-tools/quants.c` (1109) / `quants.h` (75) quant format library
- subdirs: `imatrix/`, `mixed/`, `quality-testing/`

Distributed protocol surface:

- `ds4_distributed.c` (8414) / `ds4_distributed.h` (125)
- wire protocol magic `0x44533444` ("DS4D")
- message ops: `HELLO`, `ERROR`, `WORK`, `RESULT`, `SNAPSHOT_SAVE_REQ`,
  `SNAPSHOT_BEGIN`, `SNAPSHOT_CHUNK`, `SNAPSHOT_DONE`, `SNAPSHOT_LOAD_BEGIN`
- result kinds: `ACK`, `HIDDEN_STATE`, `LOGITS`
- work flags: `INPUT_HC`, `OUTPUT_LOGITS`, `RESET_SESSION`, `ACK_ONLY`
- public functions: `ds4_dist_enabled`, `_options_create/_free`, `_usage`,
  `_parse_cli_arg`, `_prepare_engine_options`, `_session_create/_free`,
  `_session_route_ready`, `_session_sync`, `_session_eval`,
  `_session_save_payload`, `_session_load_payload`, `_run`

KV / SSD surface:

- KV store functions: `ds4_kvstore_open/close/clear`, `_evict`,
  `_entry_eviction_score`, `_find_text_prefix`, `_store_live_prefix[_text]`,
  `_maybe_store_continued`, `_try_load_text`, `_read_header`, `_fill_header`,
  `_sha1_bytes_hex`
- KV structs: `ds4_kvstore`, `ds4_kvstore_entry`, `ds4_kvstore_options`,
  `ds4_kvstore_eviction_context`, `ds4_kvstore_trailer_hooks`,
  `ds4_kvstore_load_result`
- SSD functions: `ds4_parse_gib_arg`, `ds4_parse_streaming_cache_experts_arg`,
  `ds4_ssd_cache_experts_for_byte_budget`, `ds4_ssd_auto_cache_plan`,
  `ds4_ssd_memory_lock_acquire/_release`
- SSD structs: `ds4_ssd_memory_lock`, `ds4_ssd_cache_plan`

ds4's own Makefile targets (recorded for the later CPU-only / GPU compile-subset
classification slice; pcc does NOT build them here):

- default: `ds4`, `ds4-server`, `ds4-bench`, `ds4-eval`, `ds4-agent`
- GPU backends: `cpu`, `cuda`, `cuda-spark`, `cuda-generic`, `cuda-regression`,
  `strix-halo`, `rocm`

## External oracle vectors

These are ds4's OWN tests / golden vectors. They become oracles for later pcc
migration slices (CPU oracle -> Kernel IR -> Metal source -> real Metal run ->
ds4 oracle comparison). pcc does not compile or run the ds4 tests in this
slice. The small numeric value fixtures themselves are captured byte-for-byte
under `tests/ds4_oracle/golden/` so the inventory gate no longer relies on an
external checkout merely to know the expected values.

- `tests/ds4_test.c` (2308) ds4 C unit/integration test (includes `ds4_server.c`)
- `tests/test_q4k_dot.c` (249) Q4_K dot-product numeric oracle for q8/quant matmul
- `tests/cuda_long_context_smoke.c` (166) CUDA long-context smoke (`make cuda-regression`)
- `tests/ds4_agent_test.c` (14) agent test entry (includes `ds4_agent.c`)
- `tests/test-vectors/manifest.json` golden numeric vector manifest
  (`local-golden.vec` / `official.vec` / `prompts`)

Checked-in golden capture:

- `tests/ds4_oracle/golden/official.vec`: 5 official cases / 17 selected-token
  steps, SHA-256 pinned in the capture manifest.
- `tests/ds4_oracle/golden/local-golden.vec`: 64 ranked local logits for the
  `long_story_4096` frontier, including exact token ids and float values.
- `tests/ds4_oracle/golden/manifest.json`: source commit/path/hash, expected
  counts, and explicit `pcc_support_claimed=false` boundary.

The gate parses the values rather than checking file presence only: official
hex tokens/top rows and case step counts must agree; local ranks must cover
0..63 and logits must be finite and descending. When the pinned external tree
is present, the captured bytes and source-manifest hash are also compared
directly without invoking Git.

## Next slice (not this one)

Per `docs/design/pcc-gpu-next-work.md` ds4 Route step 2: CPU-only compile
subset — classify pcc C frontend, libc, POSIX, mmap, socket, GGUF, and runtime
gaps against this inventory. That is a separate task-board slice and must carry
its own gate; nothing in this inventory slice constitutes a compile claim.
