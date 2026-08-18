# V80 complete Stage2 and large-owner audit

Date:2026-09-06. Stage2 COMPLETE; performance target and current fixed point open.

Stage1:185.70s. Stage2:566.617s including10.143s publication barrier;
compile556.455s, timed-tree CPU1992.202s, peak tree8,031,649,792B.
Stage2/Stage1=3.05, with no speed-success claim. Pcc2 SHA256:
`8e2f7ea64054dbda7b2f4d75cc3c916ac8933a91c340472ad8c8320e8c208b85`.
The actual pcc2 generic ABI executable passes1/11.80s,
`build/preload-identity-v80-pcc2-canary.log`. Source/runtime receipts remain
under `build/preload-identity-stage{1,2}-v80/`.

The current human request asks for the large Stage2 owner. Stage3 and small
helper work are paused for that diagnosis; the unfinished parent is retained.

| Execution region | Stage1 | Stage2 |
| --- | ---: | ---: |
| Frontend plus worker-side native emission | 102.745s fused | 103.920+119.876+126.674=350.470s |
| Coordination/early work | approximately30.726s remainder | 132.399s |
| Link-driver work | 52.229s | 72.665s |

The first region accounts for247.725s (about65%) of the380.917s gap, and
coordination adds about101.673s (27%). These locate regions; they do not prove
that merging queues removes those costs. Host workers run at7; native
export/summary and safe frontend codegen are capped at2 by the current memory
policy. The prior mixed ASM/PCO experiment was denied after full transfer.

Link accounting must retain the work-placement distinction: Stage2's driver
includes21.809s assembling31 ASM inputs, whereas Stage1 produces objects
inside its workers. Thus the roughly20s link-driver difference is not evidence
that the pure linker became the main native-runtime owner.

The first algorithmic candidate in the large runtime region is tuple
completion scanning. Native N/2N/4N construction confirms near4x instruction
growth per doubling; py_tuple_set_item scans all populated slots even for
GC0..3, whose publish_initialized hook immediately returns. This prerequisite
is now `RUNTIME-P0-TUPLE-PUBLICATION-NOOP-SCAN`, with all GC store/refcount/
tracking semantics retained and GC4's publication contract unchanged.

The full native data-plane tuple/list/dict/record, helper text, ASM and verifier
closure remains unfinished. Current evidence does not authorize parity or
five-GC fixed-point claims.
