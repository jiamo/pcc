# No.100 topology tracer exactness and prefilter verdict

No.100's first tracer now carries llvm_capi module/function/block topology into
the existing indexed seed and emits it in the frontend worker.  Direct/text
focused differentials pass, and real host module7/module1 assemblies are
byte-identical.

The representative module1 same-process pair is decisive for this finite
slice: text control is 25.69s wall / 25.18s CPU / 810.664MB process-tree RSS;
transitional direct is 25.29s / 24.97s / 849.052MB.  The 1.016x wall result and
1.047x RSS ratio fail the pre-registered 1.25x and 1.02x gates.  Direct capture
still reparses every instruction text and costs 8.605s; capture plus direct
emit (21.066s) is effectively the text emitter's 21.450s.

The source-frozen pcc1 tracer also exposed a real contextual-mixin layout bug:
constructor slot 14 versus writer slot 1 for `_direct_indexed_module`.  The
field was absent from `L1_CODEGEN_HOST_ATTRS`; the existing constructor-state
test reproduced that omission, and the generic host-contract addition makes it
green.  The earlier eager-call publication experiment was removed after it
increased the full validation worker from 54.904s to 57.466s.

Evidence artifacts:

- `build/no100-module1-host-text-prefilter-pair1/`
- `build/no100-module1-host-direct-prefilter-pair1/`
- `build/no100-module7-host-text-prefilter-v1/`
- `build/no100-module7-host-direct-prefilter-v1/`
- `build/no100-module7-import-gate-check-lldb.log`
- `build/no100-generate-impl-disassembly.txt`

The 217-module schema pytest hit its 130s watchdog without a final summary and
is not counted as green.  No new Stage1 was built because the prefilter failed;
no Stage2, Stage3 or GC1--4 gate ran.  The next slice replaces instruction-text
reparse with structured builder publication and repeats this same control.
