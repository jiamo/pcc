# Direct instruction producer transport

Status: v69 native qualification passed; narrow memory improvement, not complete
projection closure or an end-to-end speed acceptance.
Updated: 2026-09-05.

## Contract and scale

Accepted full baseline remains v58: Stage1 164.88s, Stage2 544.963s (3.305x),
Stage2 tree CPU 1999.275s and sampled peak 7.731GB. No newer full Stage2 is
claimed. The emit owner spans ASM 122.044s and PCO 105.146s of that stage;
removing the *entire* owner would have a 1.715x Amdahl ceiling. This experiment
removes producer instruction strings and re-parsing on the PCO branch only;
it cannot alone close the end-to-end gap or the verifier projection family.

## Paused memory-only v68

`build/direct-memory-transport-stage1-v68/manifest.json` is SUCCEEDED;
pcc1 SHA256 `b655705bdf276f8181f7fb72853716f6a4bd6a6e1e14e706798c36e36a6b0988`.
Stage1 164.53s / 676.88 tree CPU seconds, libSystem-only; function compilation
and execution printed 42. This is a qualification, not an adjacent Stage1 win.

`build/direct-memory-v68-pyast-{control,candidate}/time.txt`: ABI-only v65
versus v68, exact PCO, 17.04 -> 16.86s wall, 16.93 -> 16.82s CPU,
262.170 -> 258.810B instructions. This small result does not prove the
expanded current source or full data-plane closure.

## Expanded producer family and correctness audit

The expanded WIP handles memory, register/immediate moves, arithmetic,
comparisons, direct calls, branches, symbol addresses and frame instructions
as packed words/relocations before materializing assembler spellings.
Review caught HFA and cold-stub order mismatches, eager legacy GC reloads,
capture lifetime contamination, and lost explicit `lsl #0` spelling.
See [the focused investigation](../../../investigations/aarch64-direct-instruction-capture-order.md).

Fixes generate records in final order, reuse native kernel edge IDs for cold
stubs, use lazy packed reload routes only where needed, preserve uncaptured
ASM routes, and guard every emission mode with guaranteed capture cleanup.
Part encoder validation also rejects negative movewide shifts.

Host gates observed: 314 backend/inventory tests in 7.23s; 144 structured,
stackmap and cold-path tests in 0.22s; final 123 structured/direct tests in
0.55s. These overlap and must not be summed. The 227-module contextual gate
including terminators passed in 47.16s; the final arm64 encoder standalone
strict closure passed. Logs:
`build/direct-instruction-v69-backend-gate.log`,
`build/direct-instruction-v69-context-gate.log`.

## Frozen next gate

Source: `/private/tmp/pcc-direct-instruction-v69` (read-only snapshot).
Build with the same v58 runtime bundle, host jobs 7, self-backend jobs 2,
GC0, no threads, writable private pycache and direct-indexed mode.
Expect about 165s; inner watchdog 360s, outer sampler 400s, shell 420s.
The outer `run_process_tree_sample.py` holds the performance lock, samples
tree RSS, enforces 8GiB hard RSS and 2GiB launch reserve. Inner builder disables
its duplicate lock. `--memory-budget-bytes` alone is not a tree-RSS breaker.

Before real module measurements, run the HFA and cold-landing regression with
`PCC_INDEXED_EMIT_TEST_COMPILER` pointing at that exact pcc1; require ASM and
PCO bytes equal to host oracles. Then compare the retained py_ast and cli
sidecars against ABI-only v65 under the same sampler and runtime environment.
No speed acceptance before exact outputs; no full Stage2 until this gate has
a verdict. Remaining shared placeholders/list transport, generic verifier
scratch and other projection inventories keep the parent row unfinished.

## v69 observed results

Frozen source SHA256
`41562ebdbabcbe9ffa59726f0837315d35b91527397595cce2f35f4952225206`;
pcc1 SHA256 `23750d836588f64c15f5feb5eaed61fb26e47da4918d46d0f64feedc0a1b810b`.
`build/direct-instruction-stage1-v69/manifest.json` is SUCCEEDED. Stage1
167.80s / 682.24 tree CPU seconds; libSystem-only and function smoke prints 42.
The outer guard completed with sampled peak 4,812,161,024 bytes. Compared with
v65's non-adjacent 163.24s / 669.65 CPU, this is not a Stage1 win; repeat paired
Stage1 qualification is still needed before any no-regression claim.

Actual v69 pcc1 replay passed both HFA and cold-landing tests in 0.38s,
comparing ASM and PCO against host oracles. The guard receipt is under
`build/direct-instruction-v69-canary/` (peak 118,013,952 bytes).

Adjacent frozen-input worker results, each under the same 6GiB hard cap:

| Boundary | v65 control | v69 candidate |
| --- | ---: | ---: |
| py_ast PCO wall | 17.20s | 16.39s |
| py_ast PCO CPU | 17.09s | 16.32s |
| py_ast PCO instructions | 262.089B | 252.062B |
| py_ast sampled tree peak | 1,586,921,472B | 1,395,965,952B |
| cli module1 ASM wall | 29.25s | 29.34s |
| cli module1 ASM CPU | 29.12s | 29.28s |
| cli module1 ASM instructions | 424.007B | 428.734B |
| cli module1 sampled tree peak | 4,536,745,984B | 4,416,782,336B |

PCO instruction work fell 3.83% and sampled memory 12.03%; ASM is essentially
wall/CPU-flat but has a visible 1.11% instruction increase and 2.64% lower sampled
memory. Do not hide that cost or claim a general compiler speedup. Receipts:
`build/direct-instruction-v69-{pyast,module1}-{control,candidate}/`.
Exact PCO SHA256 is
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`;
exact ASM SHA256 is
`9811ca4cb92aa9a471743bf845528e7005530b83d8c9af160691c8a44677b8ef`.

This qualifies the implemented representation for further closure work, not a
completed performance row: final producer-family inventory, generic verifier
projections, Stage1 parity, complete Stage2, fixed point and five-GC remain open.
No full Stage2 was run. Next work inventories retained sidecars and removes the
remaining producer/line-container boundary as a whole; no further adjacent
opcode helper is to be advertised as the solution to the 3.305x gap.

## Complete input inventory coverage, split execution

The existing inventory tool gained separate direct/text-encoded/final-fallback
counters, source identity, observer cleanup, input stability and bounded shard
selection. Its two focused tests pass, including a bitwise operation that has
zero final fallback but nonzero producer text encoding.

Single cli module1 inventory: 14.29s / 442,843,136B sampled peak. The full
invocation's 210s watchdog was underestimated: it stopped with 191 durably
completed module rows, not a successful full-run receipt. No child survived.
Only the remaining 36 sorted inputs were run with `--start-index 191`, completing
in 73.14s / 454,574,080B; the original 191 were not repeated. Artifacts are in
`build/direct-instruction-v69-inventory-{all,tail}/`. Both inventories identify
the same v69 source. Combined records contain 227 distinct names for the 227
available inputs, with no overlap. This is complete per-input inventory coverage,
not a green terminal result for the timed-out command or a runtime correctness
gate for every module.

Totals: 21,264,800 structured instructions; 20,822,694 direct producer records;
442,106 text-encoded instructions (2.08%); zero final assembler fallback.
Residuals include nop274,411, and110,782, asrv23,705, orr11,188, lslv8,530,
eor7,679, csel4,256 and smaller scalar/floating/indirect-call families.
The next slice is specified in
[native AArch64 emission buffer](../../../design/pcc-native-aarch64-emission-buffer.md).
