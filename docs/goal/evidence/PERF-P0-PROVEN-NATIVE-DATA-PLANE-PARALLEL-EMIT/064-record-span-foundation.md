# Record-span foundation and generic ABI prerequisites

Status: focused/native-program checks passed; new pcc1 qualification pending.
Task: PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE.
Spec: [record-span foundation](../../../design/pcc-record-span-foundation.md).
Date: 2026-09-05.

## Prototype boundary

`CompilerRecordSpanArena` stores mutable sequence roots and immutable concat
nodes in native integer arenas. Scope-relative `CompilerInt2` keys carry an
arena-local index and generation. Snapshot extension, self-extension,
non-recursive cursor replay, reset invalidation and checked virtual length are
implemented. Normal cursor replay has zero diagnostic projections; the native
program asserts real native storage. The API is registered in the fail-closed
record inventory but is not yet the production helper carrier or kernel-owned
integration. No helper-list closure is claimed.

The first sequence test caught incorrect scalar-versus-record write indexes
before native builds. The corrected API passes sequence, deep traversal,
independent cursor, stale/invalid handle, malformed/cyclic node, partial
allocation and diagnostic-cursor cleanup tests. Virtual sequences are capped
at 2^31-1 records and checked before overflow or published-root mutation.

## Generic prerequisites exposed

The native prototype revealed two distinct existing boundaries:

1. Equal anonymous aggregate method arguments were rejected by the scalar-only
   ABI matcher. The method boundary now recursively checks literal structs and
   arrays without changing semantic marshalling, packing or address spaces.
   Task: `PY-P1-METHOD-LITERAL-AGGREGATE-ABI`.
2. Export/inference missed nested method field writes while runtime layout
   included them. The real consumer read a Boolean slot instead of its integer
   counter. One field-write traversal now aligns the three owners. Constructor
   and declaration type precedence is preserved, and dataclass constructor
   fields remain distinct from method-discovered runtime fields.
   Task: `PY-P0-NESTED-METHOD-FIELD-EXPORT-ORDER`.

The native program's unchanged `30 / 4 / 0` output now passes. It was built
from the actual arena and unsafe source through host pcc, self backend,
no libpython, using the immutable v74 runtime bundle. This is host-built native
execution, not a new pcc1 or fixed-point claim.

## Review and checks

Code-converge's plan, pattern/test-coverage and integration reviews found the
field-precedence/dataclass issues plus missing failure coverage. Four field
regressions were observed red before fixes, and targeted failure tests were
added. Round 2 read-only reviews report those findings resolved. Review does
not satisfy unexecuted gates.

Root checks: 28 changed/new Python files parse; `git diff --check` passes.
The final focused packet is 42 passed in 20.79s:
`build/span-foundation-round2-focused.log`. It covers context, inherited fields,
valueclass ABI, structural ABI type comparison and record spans, including
native storage assertions. The full compiler contextual gate is running under
`build/span-foundation-round2-context.log` before source freezing.

Earlier contextual failure evidence is retained: the helper initially hid an
exception behind `-1`. It now writes module/cause on stderr and to a per-module
error file, with a regression. This localized a cleanup tuple overwriting a
declared list type; the type-preservation regression and corrected context
passed before the second review.

## Open work

Current-source pcc1 compilation/execution and required sequential fixed point;
kernel ownership and real helper-span integration; helper lists/placeholders,
residual producer text, normal ASM publication and verifier/CFG/def-use closure.
Last fully qualified compiler remains v74 until a newer native receipt passes.

## Frozen v75 gate

The final full-context check passes in 46.71s, with the strengthened ABI and
no-stub assertions. All reviewers/editors reported source-stable before freeze.
Source SHA256:
`fe09528e78c328e9443b511278b18ff4eecd07df1e4be615c14319b5bf049158`.
Snapshot: `/private/tmp/pcc-span-foundation-v75`.
Readiness: `build/span-foundation-v75-readiness.json`.

The build uses v74's immutable runtime, GC0/threads-off, 7 host workers,
2 self-backend workers and 8 link workers. Expected wall is 165–215s;
watchdogs are 360/410/440s, with the common 8GiB tree cap and 2GiB reserve.
Artifacts: `build/span-foundation-v75-build-guard/` and
`build/span-foundation-stage1-v75/`.

The prepared native compiler canary checks a two-module value argument,
nested-try field layout and 32 constructor-owned fields with preceding typed
cleanup assignments. It verifies the compiler source manifest before use.
These checks and fixed-point verification remain pending until their actual
results are read back.

## v75 native checks and failed Stage2 transfer

v75 Stage1 is SUCCEEDED: 161.27s / 684.32 tree CPU seconds, libSystem-only,
function output42. pcc1 SHA256:
`e54947a6742ab483763fd9f14d3d3b4b961efea39fb115a5038ae662e6f4988a`.
The four native canaries pass in 11.09s, and the expanded dataclass/inheritance
canary passes in 11.21s. These verify the actual native compiler, not just host
emission. Logs: `build/span-foundation-v75-canaries.log` and
`build/span-foundation-v75-dataclass-canary.log`.

The source-frozen GC0 Stage2 was then attempted under the unchanged 8GiB tree
cap and 600s watchdog. It failed MEMORY_LIMIT at 310.87s, peak
8,616,165,376B, rc=-15; no pcc2/fixed point is claimed and Stage3 was not run.
The final sample has module_1 ASM at 6,609,207,296B plus another worker at
1,977,188,352B. The receipt's historical largest process is different: the
coordinator peaked at 8,501,411,840B near122s. Do not conflate those owners.
Immediate process inspection found no surviving owned bootstrap/compiler jobs.

Artifacts: `build/span-foundation-stage2-v75/`,
`build/span-foundation-stage2-v75.log`, and readiness
`build/span-foundation-stage2-v75-readiness.json`. The failed stage is not green
evidence. A same-input compiler/input attribution is now required before another
full run; the cap and accepted Stage1 denominator remain unchanged.
