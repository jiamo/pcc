# No.102 dense opcode consumer `[DENIED]`

## Claim boundary

The candidate kept the existing indexed-kernel opcode ID through direct
finalization, kernel construction, verifier, stackprep, precise stackmap,
AArch64 register allocation and dense AArch64 emission.  String projection
remained only in malformed/cold diagnostics and legacy/x86 APIs.  No cache,
new per-record object, provenance bypass or semantic relaxation was added.

## Sizing and correctness

A label-specific read proxy on the frozen module1 host worker measured
3,653,049 `PARSED_INSTRUCTION_KINDS[id]` reads over 329,129 records.  The
candidate reduced that count to four, exactly the four cold `extractvalue`
records, while retaining byte-identical `72e2f21a...` host assembly.

Focused direct/kernel/inventory passed 29/29; verifier/regalloc/stackmap/arena
passed 128/128; the complete AArch64 self-backend file passed 301/301; direct
binary/x86/bootstrap focused gates passed 15/15; eleven standalone strict
closures and the complete thirteen-module contextual closure passed.  One
focused cold-layout test was stale: an independent v13 control proved the
authoritative layout already lives in `kernel.block_layout_ids`, while the
test still inspected the empty compatibility list.  The test-only assertion
was corrected independently; no production behavior changed for it.

The source-frozen Stage1 was semantically green, printed `42`, linked only
libSystem and produced a complete receipt.  Its 235.87s wall was contaminated
by 1,394,155 involuntary context switches, but its deterministic 93.926B
instructions were still 1.15% above v13, so it did not establish Stage1
non-regression.

## pcc1 transfer

The lower-cost frozen pcc1 module1 discriminator was exact but missed the
pre-registered transfer:

```text
metric                    v13 control       dense-ID candidate    C / B
wall                         61.61s               62.74s           1.0183
CPU                          61.54s               62.56s           1.0166
instructions                857.478B             828.943B          0.9667
cycles                      207.595B             207.252B          0.9983
peak footprint                6.491GB               6.379GB         0.9827
assembly                    8a1dd249...           8a1dd249...       exact
```

The change removes 3.33% of retired instructions but does not reduce CPU or
cycles, misses the required 1.10x wall/CPU and 0.95x instruction lines, and
adds deterministic Stage1 work.  No Stage2 or Stage3 ran.  The twelve
production files and candidate-only tests were forward-restored byte-for-byte
to v13.

## Interpretation

The 3.65M projections are a large repeated *count*, but not a large CPU owner:
their impossible whole deletion moved only 3.33% of pcc1 instructions and no
cycles.  Do not infer eliminable time from the 55% parent-pass coverage, and
do not retry the same global-constant/numeric-dispatch rewrite.  Future work
must choose a measured child lifecycle or algorithmic scan with its own
ceiling, not another representation-wide dispatch substitution.

Artifacts:

- `build/no102-kind-projection-sizing-run/`
- `build/no102-kind-projection-sizing-r2-run/`
- `build/no102-dense-opcode-host/`
- `build/no102-dense-opcode-stage1-candidate-v17/`
- `build/no102-dense-opcode-pcc1-module1/`

Accepted timings remain Stage1 212.18s and Stage2 364.616s compile / 380.931s
total.  Stage3 and GC1--4 were not run.
