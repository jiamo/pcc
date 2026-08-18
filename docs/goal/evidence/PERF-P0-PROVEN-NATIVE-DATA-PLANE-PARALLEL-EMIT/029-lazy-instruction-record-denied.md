# No.100 lazy InstructionRecord metadata denied

The candidate gave `InstructionRecord` exact Python slots and allocated its
metadata dict on first use.  Host module1 preserved exact assembly and reduced
instructions 247.38B -> 238.94B and footprint 885.98MB -> 828.65MB.

The required pcc1 measurement reversed the verdict:

```text
                         v13 control        v15 candidate
wall                        61.61s              62.62s
CPU                         61.54s              62.57s
instructions               857.48B             860.36B
cycles                     207.59B             207.78B
peak footprint               6.491GB              6.312GB
assembly                    8a1dd249...         8a1dd249...
```

Wall/CPU/instructions regress and footprint reaches only 0.9724x, missing the
registered <=0.95x line.  Stage1 v15 was correct/libSystem-only but its 233.44s
one-shot wall is not accepted evidence.  No Stage2 ran.  Production `ir.py`
was forward-restored byte-for-byte to v13 and the post-removal direct/debug
packet passes 10/10.

Artifacts:

- `build/no100-lazy-record-host-control/`
- `build/no100-lazy-record-host-candidate/`
- `build/no100-direct-stage1-candidate-v15/`
- `build/no100-v15-pcc1-module1-direct/`

Accepted timings remain Stage1 212.18s and Stage2 364.616s compile / 380.931s
total.  Stage3 and GC1--4 were not run.
