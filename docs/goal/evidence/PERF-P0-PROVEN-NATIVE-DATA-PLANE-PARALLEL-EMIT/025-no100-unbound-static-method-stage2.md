# No.100 unbound static publication ABI Stage2 transfer

The complete module-wrapper version was denied as a physical ABI: v11 Stage1
was 236.59s and every wrapper generated a callable/native adapter.  It was
replaced by direct unbound class-method calls with an explicit builder
receiver.  This preserves the same static compiled symbol without the second
function family.

Focused proof:

- real 219-module contextual compile: `pcc.llvm_capi.ir` and
  `pcc.llvm_capi.direct_indexed_kernel` both fallback=0;
- every publication/mutation/diagnostic caller uses a static
  `DirectIndexedFunctionBuilder_*` symbol, with zero dynamic method adapters
  and zero `_exact` wrapper definitions;
- direct/contextual packet: 8 passed in 27.76s;
- Stage1 harness plus single/multi direct routes: 5 passed in 3.28s;
- host text/direct: 21.89s/325.53B/946.4MB versus
  16.59s/241.11B/862.5MB; assembly exact at `72e2f21a...`.

Source-frozen GC0/self/no-libpython results:

```text
                                      v9             v12          change
Stage1 wall                         199.40s         211.95s        +6.3%
Stage1 instructions                  92.267B         92.945B       +0.74%
pcc1 module1 wall                    68.98s          64.65s         1.067x
pcc1 module1 instructions           942.66B         897.82B       -4.76%
Stage2 compile                      482.181s        434.450s       -9.90%
Stage2 including barrier            497.213s        449.720s       -9.55%
Stage2 peak process-tree RSS         22.644GB        23.037GB      +1.7%
```

Stage2 frontend codegen falls 338.015s -> 276.102s and link driver
76.714s -> 66.238s.  The pcc1 module assembly remains exact at `8a1dd249...`;
the Stage1 canary prints `42`; pcc1 and pcc2 link only libSystem.

Artifacts:

- `build/no100-direct-stage1-candidate-v12/`
- `build/no100-v12-pcc1-module1-direct/`
- `build/no100-direct-stage2-candidate-v12/`
- `build/no100-unbound-host-text/`
- `build/no100-unbound-host-direct/`

This is a confirmed Stage2 improvement, not task completion.  Same-source
Stage2/Stage1 is still 2.050x, Stage1 non-regression and RSS improvement are
not proven, and Stage3/GC1--4 did not run.
