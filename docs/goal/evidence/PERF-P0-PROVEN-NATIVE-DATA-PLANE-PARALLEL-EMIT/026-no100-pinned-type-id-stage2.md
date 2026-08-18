# No.100 pinned final type-ID plane Stage2 transfer

The v12 pcc1 caller graph attributed 70.1% of `IndexedCallPlane.intern_type`
samples to GEP publication.  The complete direct builder now pins source types,
canonical descriptors and derived pointees beside their final integer type ID;
opaque pointers use one integer slot.  This respects the repository's id-key
lifetime invariant and leaves generic parser/Dyn interning unchanged.

Real worker cProfile:

- `intern_type`: 680,403 -> 8,372 calls;
- generated `TypeDesc.__eq__`: 430,401 -> 192,744 calls;
- host assembly remains exact at `72e2f21a...`;
- focused direct/contextual tests: 9 passed;
- Stage1 harness plus single/multi direct routes: 5 passed.

Source-frozen GC0/self/no-libpython results:

```text
                                      v12             v13          change
Stage1 wall                         211.95s          212.18s        flat
Stage1 instructions                  92.945B          92.855B       -0.10%
pcc1 module1 wall                    64.65s           61.61s         1.049x
pcc1 module1 instructions           897.82B          857.48B       -4.49%
pcc1 module1 cycles                 217.96B          207.59B       -4.76%
pcc1 module1 footprint                6.649GB           6.491GB     -2.38%
Stage2 compile                      434.450s         364.616s      -16.1%
Stage2 including barrier            449.720s         380.931s      -15.3%
Stage2 peak process-tree RSS         23.037GB         22.539GB      -2.2%
```

Stage2 frontend codegen is 238.585s and safe workers 198.382s, down from
276.102s/235.211s.  The module assembly remains exact at `8a1dd249...`, the
Stage1 canary prints `42`, and pcc1/pcc2 link only libSystem.

Artifacts:

- `build/no100-direct-stage1-candidate-v13/`
- `build/no100-v13-pcc1-module1-direct/`
- `build/no100-direct-stage2-candidate-v13/`
- `build/no100-v12-pcc1-module1-cpu.svg`
- `/tmp/no100-type-id-v12.pstats`
- `/tmp/no100-type-id-current-r3.pstats`

This is not terminal proof.  Same-source Stage2/Stage1 remains 1.718x compile
(1.795x including the barrier), Stage1 remains above the 199.40s non-regressed
line, and Stage3/GC1--4 did not run.
