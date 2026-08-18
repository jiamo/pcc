# Stage2 projection regression and restoration gate

Status: native restoration qualified; frozen GC0 Stage2 pending.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Date: 2026-09-05.
Investigation: [projection loss](../../../investigations/pcc1-native-arena-projection-loss-after-field-discovery.md).

v75's full frozen GC0 Stage2 failed the unchanged8GiB process-tree cap at
310.87s. No pcc2 or Stage3/fixed-point result exists. The terminal phase was
indexed ASM emission; all227 deferred frontend workers had finished. Historical
coordinator peak8.501GB is distinct from the final worker pair that tripped the
cap. The admission receipt predates the terminal event and is not final memory
evidence. Immediate checks found no remaining owned children.

The scheduler, lane populations and CLI admission charge are unchanged. CLI
PIDX file sizes match but hashes differ, so input identity was not assumed.
Same-input v74/v75 controls establish compiler-dependent regression:

| Frozen input | v74 | v75 |
| --- | ---: | ---: |
| old CLI ASM | completes about29s /4.47GB | timeout60s /5.425GB sampled |
| old unsafe_lowering ASM CPU | 12.30s | 36.95s |
| unsafe instructions | 180.776B | 541.569B |
| unsafe process max RSS | 1,624,735,744B | 2,987,163,648B |

Unsafe ASM is byte-identical in both completed arms, SHA256
`701b576319edb93bd83cb1819eb00c3c34495f8a09286d6a08df5a3ba92375dd`.
Receipts: `build/span-foundation-v75-{oldcli,unsafe}-{control,candidate}/`.

Source/IR attribution finds unknown constructor RHS inference replacing known
arena declarations with DynType. Fifty-nine kernel methods gain dynamic calls.
The corrected inference preserves known declarations/constructor types and only
adds Dyn for missing fields. Conditional/adopted-attribute regressions and native
getter IR tests were red before correction, then pass. Full context passes in
46.74s; all18 aggregate getters recover exact v74 instruction counts. Direct
arena sites recover91→243; dynamic sites132→4; ValueBox extraction68→0.

The remaining static cleanup additions correspond to newly typed list access
and owned field-reference releases, not continued arena projection loss. General
dynamic ValueBox result ownership is a separate source-validated task and is not
claimed fixed by returning to native dispatch.

Final focused packet:46 passed in20.81s. v76 freeze differs from v75 in exactly
one production file, `pcc/py_frontend/type_infer.py`. Source SHA256:
`82a64b625e99e0459dab0e968b0a7f2a0a69ca56d30afd835ba387c4b9200ebe`.
Snapshot: `/private/tmp/pcc-span-projection-restored-v76`.
Readiness: `build/span-projection-restored-v76-readiness.json`.

The build uses the same runtime, GC0, threads off, worker widths and8GiB cap;
watchdogs remain360/410/440s. Artifacts:
`build/span-projection-restored-v76-build-guard/` and
`build/span-projection-restored-stage1-v76/`.

Do not rerun full Stage2 until native same-input output/CPU/RSS restoration is
observed. Do not widen the cap or relax the Stage1 denominator. The helper-span
integration, residual text, ASM and verifier families remain open separately.

## v76 native restoration

v76 Stage1 is SUCCEEDED:160.98s/674.74 tree CPU seconds, libSystem-only,
function output42. Compiler SHA256:
`4bce3a9d8e012936de905c44e348daa5b0b0c2135378840afd7abc0a60543781`.
All four source-checked native canaries pass in10.89s, including aggregate
arguments, nested fields,32 constructor fields, dataclass/inheritance and
fence/HFA/cold-landing paths. Log:`build/span-projection-restored-v76-canaries.log`.

Fresh same-input v74/v76 replay under the same8GiB cap:

| Input/metric | v74 control | v76 corrected |
| --- | ---: | ---: |
| unsafe ASM CPU | 12.46s | 11.48s |
| unsafe instructions | 181.044B | 167.558B |
| unsafe process max RSS | 1,624,702,976B | 1,624,735,744B |
| CLI ASM CPU | 29.44s | 27.14s |
| CLI instructions | 429.396B | 398.017B |
| CLI process max RSS | 4,476,600,320B | 4,476,567,552B |

Every arm is COMPLETE/rc=0 and the assembly bytes match the existing exact
unsafe/CLI hashes above. Artifacts:`build/span-projection-v76-{unsafe,cli}-{control,candidate}/`.
The3x regression is removed without reverting the generic correctness fixes.
Original current-input replay and a full source-frozen Stage2 remain required.

The original v75 Stage2 CLI input now completes with v76 in 27.10s wall /
27.04s CPU, 398.041B instructions and 4,476,583,936B process max RSS.
Its guarded tree peak is 4,423,368,704B, status COMPLETE / rc=0.
Readback: `build/span-projection-v76-current-cli/{process.result.json,time.txt}`.
This qualifies the retry boundary without increasing the 8GiB cap.

Frozen GC0 Stage2 readiness: v76 source and compiler identities above remain
sealed, all relevant editors reported source-stable, the focused and full
contextual tests pass, and representative native workers restore CPU/RSS with
exact output. Run `scripts/run_pcc_stage2_from_receipt.py` with self-backend
jobs=2, frontend jobs=7, the same 8GiB cap and 600s Stage2 watchdog. Expected
450–580s is a capacity envelope based on the historical 544.963s full Stage2,
not an accepted performance result or a changed Stage2<=Stage1 contract.
Output: `build/span-projection-restored-stage2-v76/`; live log:
`build/span-projection-restored-stage2-v76.log`.
