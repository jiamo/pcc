# Native final-layout branch and call fixups

Status: native prerequisite qualified; parent task remains IN_PROGRESS.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Date: 2026-09-05.

## Source and implemented boundary

Frozen source identity:
`1989dfdef765df200b1d8505c739a612f9301b081937b5262a9e50ea65e7b802`.
Snapshot: `/private/tmp/pcc-native-fixup-v71`.
Readiness receipt: `build/native-fixup-v71-readiness.json`.

The existing packed entry/relocation arenas now carry unresolved local branches
and calls. The final text builder resolves these using its actual label/data/
alignment layout and the same branch-range and atom rules used by the text
oracle. No per-instruction object or second assembler was added. Direct and
residual text-encoded producer instructions both support this boundary; the
emitter's independent label-map pass and PC accounting were removed. Recursive
and local calls resolve inline; cross-atom and external calls remain relocations.
The canonical atom lookup uses the sorted starts with binary search, avoiding a
new per-call scan of all preceding functions after moving calls to this owner.

This is a prerequisite of the module-buffer migration. Helper/module line
containers, placeholder slots, residual text, stackmap/unwind label scans and
the verifier/CFG/def-use family are still open. No full-buffer closure, speed,
Stage2, fixed-point or GC1..4 claim is made.

## Focused observations

- Missing native forward-fixup capability reproduced before production edits:
  `test_native_text_builder_resolves_forward_fixups_from_final_layout` failed
  on absent `append_branch` (1 failed in 0.10s).
- Native layout tests exercise forward/backward branches, inline data plus
  alignment, recursive/cross-atom/external calls and section reentry, and
  compare exact bytes, labels, relocations and data-in-code with the text oracle.
- Malformed fixup bases, misaligned/unknown/out-of-range targets and direct
  arena publication reject explicitly and close owned arenas.
- Focused gate: 198 passed, 1 contextual gate deselected in 2.84s:

```sh
gtimeout 30s env -u LC_ALL uv run pytest -x -n0 -q --tb=short \
  tests/python/test_arm64_structured_encoding.py \
  tests/python/test_llvm_capi_direct_indexed_kernel.py \
  tests/python/test_precise_stackmap_abi.py \
  tests/c/test_self_backend_aarch64_cold_paths.py \
  tests/python/test_pcc_record_inventory_tool.py \
  -k 'not test_direct_publication_uses_exact_static_abi_in_stage1_context'
```

- The separately logged full contextual gate passed: 1 passed in 50.89s,
  `build/native-fixup-context.log`; generated IR is under
  `build/native-fixup-context`. It requires zero contextual fallback and no
  unavailable stubs in the PCO worker modules.
- Standalone strict encoder emission passed to `build/native-fixup-encoder.ll`.
- A prior combined invocation accidentally included the 50-second contextual
  gate under a 30-second timeout. It exited 124 without a final summary and is
  not green evidence. Immediate process inspection found no surviving children.

## Native qualification envelope

v71 uses the same frozen runtime bundle as v70, GC0, threads off, 7 host workers,
2 self-backend workers, 8 link workers and direct indexed emission. The source
is frozen before launch. Expected Stage1 wall is 165–205s; inner watchdog is
360s, outer process-tree guard is 410s and shell watchdog is 440s. The guard
holds the performance lock with an 8GiB hard tree-RSS cap and 2GiB launch reserve.
Guard artifacts: `build/native-fixup-v71-build-guard/`.
Builder output: `build/native-fixup-stage1-v71/`.

v70 readback is SUCCEEDED, Stage1 200.58s / 757.65 tree CPU seconds and
libSystem-only. Its receipt-selected ASM/PCO canary passed (1 test in 0.28s).
That non-adjacent Stage1 result is slower than v69's 167.80s / 682.24 CPU and
does not support a no-regression claim or attribution to a specific change.
No denominator may be relaxed to the slower observation.

## v71 native observations

`build/native-fixup-stage1-v71/manifest.json` is SUCCEEDED. pcc1 SHA256 is
`65ae99d113f7814702447a0ed678b353c6c9c40296471d90c459184b1ea3bd74`.
Stage1 is 190.48s / 751.50 tree CPU seconds; the outer guard completed at
204.29s with a 5,066,489,856B sampled peak. Linkage is libSystem-only, and the
function compile/run canary prints 42. Comparing frozen source manifests finds
exactly two changed inputs relative to v70: `arm64_encode.py` and
`self_backend_aarch64_darwin.py`.

With `PCC_INDEXED_EMIT_TEST_COMPILER=build/native-fixup-stage1-v71/pcc1`:
the receipt-selected text-buffer ASM/PCO test passed in 0.29s, and both
`test_direct_transport_preserves_deferred_instruction_order` cases (HFA and
cold landing) passed in 0.27s. These execute native indexed workers rather than
stopping at LLVM emission. Artifacts are under `build/native-fixup-v71-canary`
and `build/native-fixup-v71-order-canary`.

Adjacent retained-input v70/v71 replays used the same 6GiB hard process-tree cap,
60s inner watchdog, 2GiB launch reserve and `run_process_tree_sample.py` lock.
They are bounded observations, not balanced repeated speed acceptance.

| Boundary | v70 control | v71 candidate |
| --- | ---: | ---: |
| py_ast PCO wall | 17.12s | 16.81s |
| py_ast PCO user+sys CPU | 16.87s | 16.68s |
| py_ast PCO instructions | 250.348B | 250.051B |
| py_ast sampled tree peak | 1,369,489,408B | 1,379,221,504B |
| py_ast process-local max RSS | 1,384,267,776B | 1,380,614,144B |
| cli ASM wall | 30.45s | 30.01s |
| cli ASM user+sys CPU | 30.21s | 29.89s |
| cli ASM instructions | 429.519B | 429.164B |
| cli sampled tree peak | 4,422,860,800B | 4,433,133,568B |

The small sampled-RSS differences disagree in direction with py_ast's process
maximum and are below 1%; memory is effectively unchanged in this observation.
No material speedup is claimed. Artifacts are
`build/native-fixup-v71-{pyast,cli}-{control,candidate}/`; every process receipt
is COMPLETE with rc=0. Exact PCO SHA256 is
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`;
exact ASM SHA256 is
`9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.

The Stage1 profiles locate the v69-to-v70 elapsed increase primarily in
frontend workers (90.745→114.483s; v71 104.156s), with linking
50.154→53.249s (v71 52.916s). These non-adjacent receipts identify a phase,
not causality. Same-condition Stage1 regression attribution remains open;
neither v70 nor v71 replaces the 164.88s accepted baseline.
