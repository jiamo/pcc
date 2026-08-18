# Span foundation and generic ABI: frozen stage qualification

Status: GC0 exact fixed point passed; fallback baseline shards pending.
Date: 2026-09-05.
Parent: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Prerequisites: PY-P1-METHOD-LITERAL-AGGREGATE-ABI and
PY-P0-NESTED-METHOD-FIELD-EXPORT-ORDER.

## Frozen identity and readiness

v76 source manifest SHA256:
`82a64b625e99e0459dab0e968b0a7f2a0a69ca56d30afd835ba387c4b9200ebe`.
Stage1 receipt: `build/span-projection-restored-stage1-v76/build-receipt.json`.
pcc1 SHA256:
`4bce3a9d8e012936de905c44e348daa5b0b0c2135378840afd7abc0a60543781`.
Production sources remain frozen. Evidence065 records focused, contextual,
native same-input and four execution-canary prerequisites. The only later
edit is to the ABI test receipt loader: it validates a successful hash-checked
Stage2 receipt instead of pretending pcc2 is Stage1.

## GC0 pcc1 -> pcc2

Command:

```bash
gtimeout 780s env -u LC_ALL uv run python scripts/run_pcc_stage2_from_receipt.py --stage1-dir build/span-projection-restored-stage1-v76 --output-dir build/span-projection-restored-stage2-v76 --stage2-timeout 600 --smoke-timeout 60 --self-backend-jobs 2 --frontend-jobs 7 --max-tree-rss-bytes 8589934592
```

Read back the output's `manifest.json`, `stage2-record.json`,
`stage2-process.result.json` and `stage2/profile/stage2.result.json`:
COMPLETE / rc=0, 484.762s total, 475.122s compile wall, 1,647.902s timed-tree
CPU. Peak sampled process-tree RSS is 8,566,816,768B under the unchanged
8,589,934,592B cap. All 227 frontend modules completed, followed by indexed
ASM/PCO and the pcc-owned Mach-O link. pcc2 --help succeeds. Linkage is
libSystem-only, with no libpython or LLVM library. pcc2 SHA256:
`e568d2901185c84af488ec0c809d45d8f5c001afb28009085200264b9a393838`.

The v75 type-projection regression is removed at the full Stage2 boundary.
This is correctness/capacity evidence, not an adjacent paired speed claim.
Stage2 remains substantially slower than the 160.98s Stage1; Stage2<=Stage1
remains open.

The receipt-selected pcc2 feature canary passes in 10.75s: aggregate method
arguments, nested fields, 32 precise constructor fields and inherited dataclass
fields compile to an executable, assertions pass and stdout is exactly 5/7.
Log: `build/span-projection-restored-v76-pcc2-canary.log`.

## Next boundary

Run the frozen bootstrap script with --from-stage 3 --stage 3. Preserve the
runtime/environment, self-backend jobs=2, native frontend auto policy, 8GiB
cap and 600s watchdog. The current measured Stage2 establishes the expected
Stage3 capacity envelope of roughly 475–550s. Require executable pcc3 and
exact or Mach-O-metadata-normalized equality; equal sizes alone are failure.

Helper-span integration, residual producer/instruction text, normal ASM
publication and verifier/CFG/def-use remain open. The span arena is a tested
foundation, not the production helper carrier. No parent closure follows.

## Verification bookkeeping

The combined contextual/bootstrap/fallback command reached its 120s watchdog
in the full-closure fallback fixture. It has no final pytest summary and is
not green evidence; immediate process checks found no related children. Log:
`build/span-projection-restored-v76-pre-stage3.log`.
The contextual getter ratchet plus bootstrap baseline were then isolated:
3 passed, 2 deselected in 48.35s, rc=0. Log:
`build/span-projection-restored-v76-context-bootstrap.log`.
Remaining fallback checks must run as separate fixture/mode shards before
task-level closure. Stage3 uses the existing frozen bootstrap script through
the standard process-tree sampler; artifacts are under
`build/span-projection-restored-stage3-v76/` and its sibling live log.

## GC0 pcc2 -> pcc3: exact fixed point

The Stage3 sampler terminates COMPLETE / rc=0. Readback of
`stage2/profile/stage3.result.json` reports 554.124s total, 544.456s compile
wall and 2,012.799s timed-tree CPU. Sampler peak is 8,558,559,232B under the
same 8GiB cap. The stage compiles and executes a function-bearing smoke input.
The final bootstrap comparison reports **raw byte identity**, requiring no
metadata normalization. Independent SHA256 readback of pcc2 and pcc3 agrees:
`e568d2901185c84af488ec0c809d45d8f5c001afb28009085200264b9a393838`.
This proves the source-frozen GC0 self/no-libpython pcc1->pcc2->pcc3 boundary
for the current implementation. It does not prove the remaining five-GC,
helper representation or Stage2<=Stage1 performance work.

The existing bootstrap profile reporter was run after both terminal receipts;
readback is `build/span-projection-restored-v76-stage-profile.txt`.
Coordinator profiles are 122.759s / 121.444s and link profiles 62.280s /
62.359s for Stage2/Stage3. The additional Stage3 wall therefore remains in
deferred work outside those scopes. No causal attribution or correctness-tax
claim follows from this single observation; normal worker projection/CPU
qualification remains part of the parent task.

The standalone IR fallback file passes all 8 tests in 26.88s, rc=0:
`build/span-projection-restored-v76-ir-fallback.log`.
The eager OFF closure fixture itself also hit 120s after all 210 standalone
modules while entering multi-file compilation. That run is not green evidence;
no related children remained. The phase-isolation harness repair is tracked
as HARNESS-P1-FALLBACK-PHASE-SHARDS, preserving all original assertions.
