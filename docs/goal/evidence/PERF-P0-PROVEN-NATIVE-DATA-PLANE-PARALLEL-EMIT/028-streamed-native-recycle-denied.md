# No.100 streamed native frontend recycling denied

Source-frozen v14 added one-at-a-time AST wire decoding and an explicit native
modules-per-worker knob whose default stayed singleton.  Focused policy and
ownership gates passed 104/104; strict closures passed; Stage1 built a
libSystem-only pcc1 and its strong canary printed `42`.

Same-binary five-module results, with all result rows/LLVM/assembly exact:

```text
                         five fresh       streamed batch       ratio
wall                       24.64s              21.22s           1.161x
CPU                        24.57s              21.21s           1.158x
peak process-tree RSS       1.318GB             2.664GB          2.02x
```

The batch peak is effectively unchanged from the pre-stream 2.687GB result.
Simultaneous AST graphs were not the owner; allocator/compiler high water
accumulates until process exit.  The candidate missed its <=1.5x and <=2GB
lines, so no Stage2 ran.  Production source was forward-restored byte-for-byte
to v13 and the post-removal packet passes 103/103.

Artifacts:

- `build/no100-direct-stage1-candidate-v14/`
- `build/no100-v14-frontend-batch-probe/fresh-run/`
- `build/no100-v14-frontend-batch-probe/batch-run/`

Current accepted timings remain v13 Stage1 212.18s and Stage2 364.616s compile
/ 380.931s total.  Stage3 and GC1--4 were not run.
