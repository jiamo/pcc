# No.100 exact call/GEP wrapper verdict

The pcc1 caller profile attributed 30.8% inclusive samples to the dynamic
`publish_call` method adapter and 9.7% to the GEP adapter.  Two module-level
wrappers preserve the exact `DirectIndexedFunctionBuilder` receiver across
that compiled boundary.  Contextual generated IR shows static calls at both
the caller and wrapper, with zero dynamic attribute calls.  The 65-test focused
packet passed before the source was frozen.

The source-frozen GC0/no-libpython/self v10b Stage1 passed its strong canary and
libSystem-only linkage but took 223.17s versus the accepted v9 199.40s.  Its
92.380B instructions are only 0.12% above v9's 92.267B, so this one parallel
run is not evidence of an accepted whole-stage speedup.

The same pcc1 module1 input gives direct evidence at the changed boundary:

```text
                         v9 direct       exact-wrapper v10b       change
wall                       68.98s              65.37s             1.055x
CPU                        68.71s              65.29s             1.052x
instructions                 942.66B             914.94B          0.971x
peak footprint                6.649GB              6.649GB         neutral
assembly sha256           8a1dd249...         8a1dd249...          exact
```

Artifacts:

- `build/no100-direct-stage1-candidate-v10b/`
- `build/no100-v10b-pcc1-module1-direct/`
- `build/no100-v10b-pcc1-module1-work/ir/module_1.direct.s`

Verdict: exact receiver provenance removes real pcc1 work and is retained as
the first member of the complete static publication ABI conversion.  The
partial two-wrapper batch is not yet a whole-stage acceptance claim.  Every
hot publication operation must cross the same exact ABI before the next
source-frozen Stage1/Stage2 measurement.  Stage3 and GC1--4 were not run.
