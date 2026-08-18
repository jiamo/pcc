# Incremental AArch64 module builder

Status: incremental driver prerequisite qualified; parent remains IN_PROGRESS.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Date: 2026-09-05.
Predecessor: [060](060-native-final-layout-fixups.md).

## Implemented prerequisite

`AArch64ModuleBuilder` owns one module's existing directive parser, section
buffers and final section normalization. It accepts raw chunks or packed
instructions incrementally and exposes the canonical text labels and size.
`assemble_lines` is now the input-index adapter over that same implementation;
there is no second directive parser. Input-index preflight still precedes
parsing, driver errors precede deferred text errors, and success/failure closes
the native text arenas. The new class is a registered per-module `phase_shell`;
its instruction storage remains in `PackedAArch64TextBuilder`.

This interface does not yet remove emitter helper/module lists. It is the
driver prerequisite for handing producer instructions directly to their owner.
No full representation closure, Stage2 or speed claim is made.

## Gates and source identity

The incremental full-driver test failed before implementation with missing
`AArch64ModuleBuilder` (1 failed in 0.09s). After extraction all 10 directive
driver tests passed, including the system-assembler object/link/run oracle.
The record inventory then rejected the new unclassified class before it was
registered with its actual module lifetime and storage policy.

Focused packet: 210 passed, 1 contextual test deselected in 3.65s:

```sh
gtimeout 30s env -u LC_ALL uv run pytest -x -n0 -q --tb=short \
  tests/python/test_arm64_structured_encoding.py \
  tests/python/test_arm64_asm_driver.py \
  tests/python/test_llvm_capi_direct_indexed_kernel.py \
  tests/python/test_precise_stackmap_abi.py \
  tests/c/test_self_backend_aarch64_cold_paths.py \
  tests/python/test_pcc_record_inventory_tool.py \
  tests/python/test_structured_instruction_inventory_tool.py \
  -k 'not test_direct_publication_uses_exact_static_abi_in_stage1_context'
```

The separately logged contextual test passed in 51.54s:
`build/native-module-builder-context.log`, with generated IR in
`build/native-module-builder-context`. Standalone library emission contains
unavailable imported-dependency stubs and is not closure proof; the contextual
test explicitly checks their absence in the actual PCO worker modules.

Frozen source SHA256:
`09fe2188fb9fbc73258bf57bc930615d10570e8f4eea79dccf2d7dd8807c74b4`.
Snapshot: `/private/tmp/pcc-native-module-builder-v72`.
Readiness: `build/native-module-builder-v72-readiness.json`.

v72 uses v71's frozen runtime bundle, GC0/threads-off, 7 host workers,
2 self-backend workers and 8 link workers. Expected wall is 165–205s; the
inner/outer/shell watchdogs are 360/410/440s. The process-tree guard owns the
performance lock, limits aggregate RSS to 8GiB, and reserves 2GiB at launch.
Artifacts: `build/native-module-builder-v72-build-guard/` and
`build/native-module-builder-stage1-v72/`.

## Adjacent gate limitation

The separate encoder file's LLVM-MC differential is gated on llvmlite 0.46.0;
the installed version is 0.47.0, so 12 encoder tests were deselected. A mistaken
`-m integration` selection also ran no tests (exit 5), not green evidence.
The directive driver's system-assembler and executable-output oracle did run.
No gate pin, dependency or baseline was weakened to conceal the unavailable
LLVM-MC oracle.

## Native correctness observations

v72's manifest is SUCCEEDED. Its pcc1 SHA256 is
`73d85018b2f4de7808e3427f305464a69e5912a70fafb54ef6f1718e178c3ed4`.
Stage1 wall is 168.96s and tree CPU 697.35s. The outer guard is COMPLETE/rc=0
at 181.68s, sampled peak 4,804,280,320B. pcc1 is libSystem-only and its
function compile/run canary prints 42.

All three receipt-selected native worker tests passed in 0.41s (text buffer,
HFA, cold landing), comparing actual ASM and PCO bytes to the host oracle.
Artifacts: `build/native-module-builder-v72-canary`. This is native interface
qualification, not producer-list closure or a fixed-point proof.

The 168.96s Stage1 observation is near v69's 167.80s, while CPU remains
697.35s versus 682.24s; the earlier 190–200s observations remain without
controlled causal attribution. No Stage1 speedup or relaxed baseline is claimed.

The adjacent v71/v72 real py_ast PCO replays also completed with exact bytes
(`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`).
Wall is 16.26→16.14s, CPU 16.14→16.11s, instructions 250.059→250.484B
(+0.17%), and sampled tree peak 1,369,702,400→1,376,911,360B. These are
effectively flat observations, including the small instruction increase;
they support proceeding with the structural migration, not a speed claim.
Process-local maximum RSS is 1,380,515,840→1,381,023,744B. Both sampler
receipts are COMPLETE/rc=0 under the same 6GiB cap and 60s watchdog, in
`build/native-module-builder-v72-pyast-{control,candidate}/`.

The next slice must connect this append interface to the actual producer and
remove retained instruction lists. The existing interfaces alone do not close
the parent row. ASM-lane/whole-module worker publication, residual text,
verifier/CFG/def-use, Stage1 paired attribution, Stage2 and fixed point remain.
