# Complete fallback qualification; fragment integration remains

Date: 2026-09-05. Status: parent remains IN_PROGRESS.

v76 passed the GC0 raw-byte fixed point documented in evidence066. Subsequent
baseline qualification exposed and repaired a missing standalone static export
for the shared field walker, and extended the existing closed-world probe model
to the native object/encoding siblings. Four other standalone action increments
were source/IR-attributed to named features; only their diagnostic ceilings
were recaptured. Strict linked-zero limits were retained.

All 37 fallback nodes (35 original + 2 new context guards), 8 IR fallback nodes,
and the 26-node bootstrap/harness/export/tool packet now pass. Fresh collection
and successful-log readback prove complete fallback coverage:
`build/fallback-shard-coverage.json`.
Detailed gates, failure receipts and source controls:
[phase-isolation evidence](../HARNESS-P1-FALLBACK-PHASE-SHARDS/001-phase-isolation-and-current-fallback-surfaces.md).

The only new pcc source changes after v76 are the helper static export in
layer1_support.py and the encoding-sibling probe classification in
host_contract.py. Their current host/contextual gates pass; v76 is not relabeled
as containing these later edits. Fresh native qualification remains necessary.

Next implement the first complete fragment vertical in
[the fragment contract](../../../design/pcc-native-emission-fragments.md): the
three native packed-stackmap seams and seven helper chain. Preserve explicit
record identity, unchanged output on unhandled dispatch, native cursor lifetime,
canonical encoding/layout and exact ASM/PCO oracles. No per-word list or anonymous
placeholder is allowed on the migrated path. This is a structural prerequisite;
do not present it as the solution to the Stage2/Stage1 gap.

The parent still has the complete helper-chain list/placeholder family,
residual producer/instruction text, normal ASM publication and verifier/CFG/
def-use projections. The inventory must close every supported normal family;
the span foundation alone is not production integration or task completion.
