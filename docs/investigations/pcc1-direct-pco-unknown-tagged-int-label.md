# Investigation: pcc1 direct `.pco` assembly rejects a tagged-int slow-path label

## Status

active

## Problem Description

After codegen workers began assembling their own direct-indexed assembly into
validated `.pco`, the source-frozen Stage1 v24 produced a runnable pcc1 but its
mandatory function-bearing compile canary failed inside the pcc1 worker:

```text
EncodeError: branch to unknown label
'L_user_stage1_function_smoke_add_entry_to_intdottagdotslowdot22'
```

This is a second failure boundary after the separately fixed native-module
constant/class-constructor-argument bug. It proves the pcc1-compiled AArch64
assembler is now executing, but it cannot resolve a branch target emitted for
the tagged-int slow path. The same worker path must remain enabled; reverting
worker `.pco` publication is outside this investigation.

Predecessors:

- `pcc-macho-packed-linker-mold-design-transfer.md` owns the packed/validated
  Mach-O representation and current worker-assembly transfer.
- `typed-int-unboxed-overflow-silent-wraparound.md` owns tagged-int fast/slow
  semantics and records that the slow path is required for Python bignum
  correctness; deleting it is forbidden.
- `valueclass-wide-payload-aggregate-abi.md` records an older unknown-label
  failure caused by switching IR builders without switching function context.
  That mechanism is not yet established here.

## Repro

Frozen identities:

```text
bootstrap source  dff076b406c724342c8483a7c13e52a1ad5b3d525cc93b8870454dd6d2466181
pcc1              8112702e14a9629560dcb9e3b28d502356f35022b0de4b812997000115c66389
runtime archive   2f3d1c05432333061bad7a574f1ee3c264caa7a67abdfb74b5f14b120a34e37e
```

The retained Stage1 command is represented by
`build/no105-summary-pco-stage1-v24/manifest.json`. It built pcc1 in 131.47s,
then the strong canary failed. The smallest current failing boundary is:

```text
PCC_DIRECT_INDEXED_NATIVE_OBJECT=1 \
build/no105-summary-pco-stage1-v24/pcc1 \
  --backend self --python-libpython off --ir-scaffold on \
  build/no105-summary-pco-stage1-v24/work/stage1_function_smoke.py \
  -o /tmp/pcc-v24-function-smoke
```

Expected: compile exit 0, produced executable prints `42`.

Observed: compile exit 1 with the exact unknown-label diagnostic above.

## Test [CONFIRMED]

The failure was observed under the frozen v24 Stage1 strong canary. Durable
worker evidence:

- `build/no105-summary-pco-stage1-v24/function-smoke-compile.stderr`
- `build/no105-summary-pco-stage1-v24/private-state/tmp/`
  `pcc_py_frontend_workers_lc7e0q/worker_0.tsv`

A focused regression must compare pcc1-produced assembly against the host
assembler on the exact tagged-int branch/label pair, preserve both sides of
the slow-path CFG, and compile+run the function-bearing canary through worker
`.pco` publication.

## Proposals

- No.1 Capture the exact assembly and classify emitter-vs-parser ownership [pending]
- No.2 Repair the proven generic label boundary [pending]

## No.1 Capture the exact assembly and classify emitter-vs-parser ownership

### Code Change

No production change yet. Retain the pcc1 worker's exact direct assembly under
an explicit diagnostic artifact path, then prove whether the target label is
absent from emitter output, present but not recognized by the pcc1 assembler,
or placed in another section/function. Compare the same bytes with the host
`assemble_asm_text_to_encoded` oracle before selecting No.2.

## No.2 Repair the proven generic label boundary

### Code Change

Pending No.1. The repair must be generic and preserve tagged-int overflow/bignum
semantics, deterministic symbols/sections, exact relocation validation and
the worker `.pco` route. No label-name, function-name, module-name or canary
special case is admissible.
