# Stage2 memory-safety circuit breaker

## Task and source boundary

- Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`.
- Compiler source receipt: frozen v34 Stage1, pcc1
  `898f567d931dc7867f44321bbbd1b5d5592479c33a2dc93c31637a19406f001c`.
- v34 Stage1 is independently successful at 132.65 seconds, links only
  libSystem, and its direct-`.pco` function canary prints `42`.
- The safety changes in this receipt are later dirty-worktree tooling/task
  changes. They do not change the frozen v34 compiler identity.

## Incident evidence

The first receipt-bound v34 Stage2 was terminated at 151 seconds when the old
sampler let a transient five-second `ps` timeout abort the compiler. A second
jobs=10 run remained compiler-green through 121 seconds but reached a sampled
39,238,189,056-byte process-tree RSS peak before its controlling conversation
turn was interrupted. Neither run produced a terminal Stage2 receipt or pcc2,
so neither is performance/correctness evidence.

The human supplied a Darwin panic plus Jetsam analysis from the same pcc work.
The corrected topology is one Stage2 coalition: coordinator/parent pcc1 PID
28786 plus ten pcc1 workers, not independent benchmark processes. Their
process-RSS sum was approximately 136 GiB; the compressor reported 100%
segment use, 100 swapfiles and low swap space, and watchdogd then missed
check-ins for 92 seconds. This establishes a host-safety failure, not a normal
benchmark miss. The panicked `ShortcutsViewService` task is not treated as the
memory owner.

The retained frontend directory is bound to that coordinator by its output
path `pcc2.pcc-pco.28786`. It contains 225 codegen manifests and zero codegen
results. The deterministic first wave maps the three largest Jetsam workers to
`pcc.py_frontend.codegen.native_modules` (28.360 GiB), `pcc.llvm_capi.ir`
(26.699 GiB), and `pcc.py_frontend.pipeline` (36.035 GiB). Direct `.pco`
publication had moved self-backend emit and assembly into these frontend
workers while retaining numeric frontend width ten, which also disabled the
automatic oversized lane. This is the confirmed scheduling-composition root
cause; the `.pco` encoding itself is not blamed.

## Implemented safety boundary

- `run_process_tree_sample.py` keeps its five-second `ps` watchdog, retries one
  transient timeout with a bounded twenty-second watchdog, records retries,
  and writes terminal `SAMPLER_ERROR` receipts on persistent failure.
- Samples are appended and flushed immediately; the RUNNING receipt is updated
  at each progress interval, so an interruption retains the observed high
  water instead of only an initial empty receipt.
- `--max-tree-rss-bytes` terminates the complete owned process group and emits
  a terminal `MEMORY_LIMIT` receipt. Exit code 125 distinguishes it from the
  command timeout.
- Safety-capped sampling uses one one-second process-table deadline and kills
  the tree if observation fails; it never waits through the ordinary 5s->20s
  telemetry retry while an unobserved compiler may still be growing. Full
  worker argv and manifest paths are retained for the largest process.
- Ordinary `bootstrap.sh` stages now enter the 8 GiB/600-second sampler and
  Darwin resource preflight by default. An outer receipt runner marks its
  guard explicitly to prevent nesting.
- Compiled-native export/codegen oversized lanes are serial, residual lanes
  are at most two, compiled self-backend auto width is two, and automatic
  Mach-O link width is two. Stage1's eight-worker evidence is no longer reused
  as a Stage2 premise.
- Direct `.pco` workers skip LLVM text rendering/writing when validation does
  not require it, and release each preceding representation before allocating
  the next assembler/native-object graph.
- Stage A/B and the new single-arm receipt runner default to two frontend,
  self-backend and linker workers and enforce the existing 8 GiB aggregate
  Stage2 ceiling. These runners reject a jobs value above two or a cap above
  8 GiB.
- The single-arm runner validates the Stage1 compiler/runtime/source receipt
  and performs a Darwin launch preflight: reclaimable memory and root-volume
  free space must each cover `cap + 8 GiB`; already-more-than-half-used swap
  with less than 4 GiB free rejects the run.
- Every unfinished immediate task whose exit path requires Stage2/Stage3/five
  GC now depends on this existing unfinished memory-budget row before a heavy
  gate can be selected.

## Focused gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_process_tree_sample_tool.py \
  tests/python/test_bootstrap_performance_manifest.py \
  tests/python/test_stage2_from_receipt_tool.py

15 passed in 0.96s

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 457 tasks validated
```

The tests cover ordinary completion, signal cleanup, transient and persistent
`ps` timeouts, incremental sample persistence, process-tree memory-limit
cleanup/receipt status, receipt identity rejection, jobs=2/8-GiB defaults and
Darwin memory/swap observation policy.

The bootstrap/GC scheduling follow-up also passes without launching a compiler:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_bootstrap_performance_manifest.py \
  tests/python/test_pipeline_frontend_workers.py \
  tests/python/test_process_tree_sample_tool.py \
  tests/python/test_stage2_from_receipt_tool.py \
  tests/python/test_pcc_bootstrap_full.py \
  -k 'matrix_plan or parallel_slots or active_gc or backend_plan or resource \
      or process_tree or stage2_runner or stage_pair or bootstrap_defaults \
      or stage_sampler'

27 passed, 24 deselected in 1.07s
```

This proves Stage2+ bootstrap defaults activate `auto` oversized-source lanes,
Stage1 uses two frontend workers, self-backend/link pools stay at two, ordinary
wide overrides fail closed, GC0..4 chains are admitted strictly sequentially,
and each GC stage is wrapped by the 600-second/8-GiB process-tree sampler.

One read-only launch preflight after the host reboot reported 72,273,281,024
reclaimable bytes, 642,468,802,560 free disk bytes and zero configured/used
swap, against a 17,179,869,184-byte required reserve. No native pcc1, Stage or
GC process followed the preflight.

## Current-source lifecycle narrowing

After the safety gates, the direct worker was changed to release its already
frozen AST/type/codegen/direct-module references before constructing assembler
Sections/Relocations, and to release assembly text plus the authoring graph
before encoding `.pco`. This is not the denied multi-module recycling shape:
native workers still own one module, and no persistent worker was introduced.
The change only prevents two phase-specific object graphs from being live at
the same time so freed allocator cells may be reused within that one process.

Focused worker/native-object tests passed 26 cases before the contextual gate;
the updated contextual closure then passed separately in 38.62 seconds with
zero fallback across 224 reachable modules. The count fell from 225 because
`pcc.backend.macho_assemble_worker` is now only a host process-pool wrapper;
the pcc1 worker directly reaches `arm64_asm_driver` and `native_object`. Its own
native-object differential remains green. No pcc1 binary was rebuilt and no
memory improvement is claimed from source shape alone.

## Claim boundary

This proves the safety tooling/task ordering and output-neutral host/contextual
source shape only. It does **not** prove
that current Stage2 stays below 8 GiB, completes inside 600 seconds, reaches
Stage2 <= Stage1, has no single-worker leak, or reaches a GC0/five-GC fixed
point. No heavy compiler or GC process was launched after the incident while
closing this slice. The next real Stage2 requires the bounded runner and must
be stopped/optimized if it reaches `MEMORY_LIMIT`; the threshold must not be
raised.
