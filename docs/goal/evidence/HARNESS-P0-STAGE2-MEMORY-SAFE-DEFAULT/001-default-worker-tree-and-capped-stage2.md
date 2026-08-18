# Default Stage2 memory-safety correction and first capped result

## Task and incident boundary

- Task: `HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT`.
- Host: Darwin arm64, 96 GiB physical memory.
- Incident source: `/Library/Logs/DiagnosticReports/JetsamEvent-2026-08-31-060006.ips` plus retained v34 frontend manifests.
- Corrected topology: one Stage2 coalition, coordinator/parent pcc1 PID 28786 plus ten pcc1 codegen workers.
- Retained directory `pcc2.pcc-pco.28786` binds the manifests to that coordinator. It has 225 codegen manifests and zero codegen results.

The deterministic first wave maps the three largest workers to:

```text
pcc.py_frontend.codegen.native_modules   PID 35809   28.360 GiB
pcc.llvm_capi.ir                         PID 35813   26.699 GiB
pcc.py_frontend.pipeline                 PID 35820   36.035 GiB
```

Direct `.pco` publication had moved self-backend emit, assembly and native-object encoding into frontend workers while the launcher retained numeric width ten. That numeric override also disabled automatic oversized lanes. The fault is this execution-owner/scheduler composition, not an established `.pco` codec defect.

## Implemented boundary

- Compiled-native export and codegen lanes classify source plus AST-sidecar size. Oversized work runs serially; residual work uses at most two workers. Summary workers remain at most two.
- Compiled-native self-backend automatic width is two, separately from Stage1's host-only width-eight measurement. Automatic Mach-O assembly/link width is two.
- Direct artifact workers do not stringify/write unused LLVM IR.
- Frontend, assembly and NativeObject graphs are released in phase order.
- Ordinary bootstrap stages enter a Darwin resource preflight and 8 GiB/600-second external process-tree guard by default.
- Safety-capped process-table observation has one one-second deadline; loss of observation kills the whole tree instead of waiting through the ordinary 5s->20s measurement retry.
- Receipts retain full argv, the largest process and extracted worker manifest paths.
- After the first cap trip, oversized modules keep direct indexed emit but publish assembly by path. Process exit reclaims the compiled frontend/emitter heap before a short-lived linker assembler builds `.pco`; safe modules still publish `.pco` directly. A versioned manifest preserves the original module order across mixed `ASM`/`PCO` inputs.

## First source-frozen Stage1 and Stage2

Initial source identity:

```text
source       800d51a3be8cbfb1b055c9e3459a73c48a290ba4eecdc5bdfaa33a638a7ec8a8
pcc1         ca8863b60364f85b9cd1f660eb56abaea8eed93589d6f1b99a17b7cc10b944e2
Stage1 wall  344.01 s
Stage1 CPU   656.66 s
Stage1 tree  4,748,820,480 B
linkage      libSystem only; no libpython or LLVM
```

Exactly one Stage2 was launched through `scripts/run_pcc_stage2_from_receipt.py` with frontend `auto`, self/link two, cache off, GC0, 8 GiB and 600 seconds. Result:

```text
status                 MEMORY_LIMIT
elapsed                about 218 s
tree peak               8,692,858,880 B
largest worker          8,036,073,472 B
largest manifest        worker_0.manifest
assigned module         pcc.cli_bootstrap
pcc2                    absent
surviving children      none
```

This proves fail-closed host safety and exact owner attribution. It does not prove Stage2 completion or correctness.

## Oversized handoff correction and current pcc1

```text
source       87e4bb10d206570a90c805b296495a4259e3f92cbfc38a192524b39a9899c749
pcc1         47621eac2b093abae92ae10f8d073603d4464668c7c8ea5b860793381118c7b8
Stage1 wall  350.18 s
Stage1 CPU   666.21 s
Stage1 tree  4,847,337,472 B
linkage      libSystem only; no libpython or LLVM
```

The pcc1-compiled mixed canary uses an oversized entry and safe sibling. It prints `42`, records at least one oversized assembly handoff, at least one direct `.pco`, zero direct LLVM-text bytes, invokes the linker through `--internal-input-manifest`, and peaks at 392,855,552 bytes for the whole canary tree.

## Focused gates

```text
30 passed
  pipeline frontend policy + bootstrap/sampler/stage2 receipt tools

4 passed, 22 deselected
  pure five-GC scheduler/resource/process-tree selections

131 passed
  native object, ordered mixed linker, frontend ownership, self/link,
  bootstrap defaults, sampler and Stage1 snapshot contracts

60 passed in twelve <=5-node shards
  complete test_py_multi_file_compile.py + frontend worker ownership packet

1 passed in 43.75 s
  224-module contextual strict no-libpython closure

1 passed in 1.71 s
  host direct-assembly path handoff executable

1 passed in 118.88 s, then profile-bound rerun 1 passed in 5.21 s
  current pcc1 mixed oversized-ASM/safe-PCO canary

19 passed in four bounded shards
  bootstrap and non-whole-closure fallback/source contracts

8 passed in 28.84 s
  complete ir.py fallback ratchet; the static Function-layout check now uses
  the real contextual closure instead of a standalone strict stub
```

The first profile attempt was served by the pcc1 run cache and therefore produced no profile; the final gate sets `PCC_DISABLE_PY_RUN_CACHE=1` and asserts the counters above. An earlier invocation placed `--profile-json` before `-m`; module mode only accepts `-m` first, so that harness failure is not attributed to the compiler path.

`test_closure_per_module_codegen_passes` is not claimed green. It remained CPU-active past its 180-second isolated watchdog, matching the repository's retained 2026-08-03 evidence that this exact broad node exceeds separate 60- and 180-second watchdogs. It was terminated with no children and was not widened. The changed modules instead have the 224-module contextual zero-fallback gate above.

## Claim boundary

The default crash topology, scheduler-composition root cause, hard circuit breaker, owner diagnostics and mixed oversized handoff are implemented and focused-green. The task remains `IN_PROGRESS`: no second Stage2 was launched after the first `MEMORY_LIMIT`, so current pcc1 `47621eac...` has not yet proved a runnable pcc2 below 8 GiB. The next authorized action is exactly one unchanged 8 GiB/600-second Stage2; no timeout, RSS or concurrency increase is permitted.

## 2026-08-31 v10 correction, interrupted Stage2, and performance audit

After the first deferred-checkpoint implementation, the source-frozen v10
identity and Stage1 receipt are:

```text
source                    9e6c20f9ffa99248432447760ff3df84f46cbe94bdcc9c8acd954f89e5d9805e
pcc1                      b8fc70aad81fabcea1f5a3d3088b77d2e0a0a47bdf2c4d3cfa94fc83ef196d82
Stage1 wall               369.13 s
Stage1 CPU                711.79 s
Stage1 process-tree peak  4,787,404,800 B
linkage                   libSystem only; no libpython or LLVM
```

This exposed a performance-policy error that had not been treated as a
regression soon enough.  Stage1 was built with frontend jobs two, produced
only eight codegen chunks for 224 modules, and spent 303.517 seconds in
`multi_frontend_codegen_parallel` (272.554 seconds in worker commands).
The Stage2 memory policy had been applied to host Stage1 even though the two
execution owners have different memory costs.  The 369.13-second result is not
an accepted performance baseline.

The exact v10 Stage2 was started with frontend `auto`, self/link jobs two,
the 8 GiB cap and 600-second watchdog, then stopped at the human's explicit
request after 450.426 seconds.  Its terminal facts are:

```text
status                    INTERRUPTED
process-tree peak         7,838,253,056 B
largest process           6,989,987,840 B (checkpoint coordinator)
checkpoint frontend       138.888 s
deferred lane plan        serial 1 / paired oversized 6 / heavy 8 /
                          medium 13 / small 196
completed worker results  13 / 224
pcc2                      not produced
surviving children        none
```

The imported sampler SIGINT defect is closed by this receipt: the interrupt
persisted `INTERRUPTED`, terminated the whole process group and left no pcc1,
worker, bootstrap or pytest child.  The run proves containment but not Stage2
correctness or throughput.  Thirteen results after 450 seconds is sufficient
to reject an unchanged rerun as a useful next action; the existing manifests
must first be used to size the critical path and separate host Stage1
parallelism from compiled Stage2 memory admission.

The human explicitly paused the run for a methodology review.  Do not launch
another Stage1 or Stage2 from this state merely to finish the interrupted
receipt.  Resume only with a source-level correction that restores host-only
Stage1 concurrency and a precomputed Stage2 schedule whose predicted wall and
RSS envelope can satisfy the task rather than relying on another cold run to
discover it.
