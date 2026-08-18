# Phase-isolated fallback verification and current diagnostic surfaces

Status: complete phase-isolated fallback coverage; later native qualification
for the static-export/probe-policy source changes remains separately tracked.
Date: 2026-09-05.

## Harness boundary

The eager fixture made every selected test run 210 standalone modules, the
whole multi-file compile and every contextual module before any assertion.
Both the aggregate and OFF-only 120s commands timed out without summaries;
neither is green evidence. Immediate process checks found no related children.

Fixtures now cache those three phases independently. Original node IDs,
assertions, source closure, count classification and environment restoration
are preserved. Contextual policy derives names directly from the closure.
The regression was observed red (1 failed/0.12s); 13 phase-isolation tests
pass in 0.11s. The split exposes the actual OFF action-ratchet failure in a
terminal 61.42s run rather than losing it behind the fixture timeout.

Logs: `build/span-projection-restored-v76-fallback-harness.log`,
`build/span-projection-restored-v76-fallback-off-standalone.log`.

## Repairable import regression

The newly shared field walker was missing its static native export. An exact
source/IR causal substitution identifies all class_gen +11 and type_infer +10
actions; the native Dyn -> List[Dyn] export restores their original 14/0
ceilings in both modes. No consumer ceiling is raised. The standalone pipeline
also drops 668 -> 628, below its existing 636 ceiling.

See `field-walk-static-export-fallback-regression.md` and
`build/field-walk-static-export-consumers/results.json` for the real consumer
IR and source identities. Four focused OFF/ON consumer tests pass.

## Encoding siblings' probe model

The native encoding/format siblings share arena, relocation and section
schemas with self_backend_* modules. An isolated compile lacks those schemas;
arm64_encode/native_object/macho_spec report 191/213/14 actions. The driver
needs siblings to compile. Before adjusting their probe model, all four were
explicitly compiled against the actual closure: exact zero fallback, 1 passed
in 13.30s (`build/native-encoding-contextual-probe.log`).

The policy regression was red at standalone != closed-world, then green.
The four siblings now use the existing closed-world model, with no L1CodeGen
mixin host. Their ON contextual matrix is required to remain exact zero.
No raw ceilings were added for them. The historical rationale and denials were
read in full in `pcc1-indexed-function-kernel-native-data-plane.md`.

## Four named standalone feature increments

| Module | Previous ceiling | Current OFF/ON | Exact owner/feature |
| --- | ---: | ---: | --- |
| pipeline_context | 483 | 535 | constructor/declaration precedence and separate dataclass init fields; new traversal of existing imported AST/export helpers |
| pipeline_frontend_worker_execution | 49 | 61 | PIDX/structured transport and direct section-to-PCO publication in run_codegen_worker |
| pipeline_frontend_parallel | 52 | 56 | explicit unique-name/length/membership validation in _load_noop_action_result |
| pipeline_closed_world | 118 | 124 | exact AST dependency-edge publication in _closed_world_module_dependencies |

This is program-source growth, not a change to the action classifier. Current
host emission of the baseline source named by the prior receipt (9dbb1404)
recovers 49/52/118 exactly for worker/parallel/closed_world. Function-scoped IR
comparison isolates 49->61, 30->34 and the new 0->6 dependency function.
Frozen v74's own compiler/source already produces 61/56/124; no history,
author attribution or process-session inference is used. Current host emission
of frozen v74 pipeline_context source recovers 483 exactly; current source is
535, with +1 import and +51 constructor/declaration-walk actions.

Receipts and exact source/IR:

- `build/recorded-baseline-source-fallback-control/receipt.json`
- `build/v74-own-source-fallback-control/receipt.json`
- `build/field-helper-off-attribution/pipeline_context_v74_source.json`
- `build/pipeline-feature-standalone-both/receipt.json`

The existing named-feature diagnostic recapture rule permits only these four
OFF/ON action ceilings to move. Total/plumbing maps and all linked-zero limits
remain unchanged. The new static export regression is repaired rather than
included in the recapture.

## Real closure guards

The four affected pipeline feature functions are compiled in their actual
closed-world context, with exact-zero CPython calls and no strict unavailable
stub in each named body: 1 passed/12.56s, log
`build/pipeline-feature-contextual-proof.log`.

Original strict linked gates now finish separately under 120s:

- OFF multi-file: 2 passed/94.91s, `build/fallback-off-multi-phase.log`.
- ON multi-file: 3 passed/96.09s, `build/fallback-on-multi-phase.log`.
- ON contextual matrix: 3 passed/58.00s, `build/fallback-on-contextual-phase.log`.
- IR fallback baseline: 8 passed/26.88s, `build/span-projection-restored-v76-ir-fallback.log`.

v76's raw-byte GC0 fixed point remains evidence for its exact source. The
subsequent static-export and probe-policy changes still need the appropriate
fresh native qualification; this test/metadata receipt does not relabel v76
as a compiler containing later edits.

## Final coverage readback

Fresh collection enumerates 37 fallback nodes. The successful shard logs cover
exactly those 37, with no missing/extra node; the source-hashed coverage receipt
is `build/fallback-shard-coverage.json`. All 35 original assertions plus the
two new real-context assertions are covered, and the separate IR file passes
all 8 nodes.

| Final shard | Result | Log under build/ |
| --- | --- | --- |
| OFF standalone | 2 passed / 60.39s | fallback-off-standalone-final.log |
| ON standalone | 1 passed / 56.96s | fallback-on-standalone-final.log |
| OFF multi | 2 passed / 94.91s | fallback-off-multi-phase.log |
| ON multi | 3 passed / 96.09s | fallback-on-multi-phase.log |
| OFF contextual | 1 passed / 62.04s | fallback-off-contextual-phase.log |
| ON contextual | 3 passed / 58.00s | fallback-on-contextual-phase.log |
| Independent core | 18 passed / 31.86s | fallback-independent-core.log |
| Independent contextual | 5 passed / 69.38s | fallback-independent-contextual.log |

The first combined 25-node independent packet timed out at the assignment
contextual node; it remains failed watchdog evidence with no summary and no
surviving related process. The two successful independent shards above replace
it as qualification; its log is `build/fallback-independent-nodes.log`.

Bootstrap baseline + phase isolation + static export + probe-tool contracts
pass 26 nodes with 2 intentional LLVM baseline deselections in 1.44s:
`build/fallback-final-contract-packet.log`.
OFF/ON partial-namespace controls also show that unlisted helper bindings load
from the real compiled module, and missing attrs raise explicitly; no
unavailable stub is emitted. Readback:
`build/field-walk-static-namespace-control/results.json`.
