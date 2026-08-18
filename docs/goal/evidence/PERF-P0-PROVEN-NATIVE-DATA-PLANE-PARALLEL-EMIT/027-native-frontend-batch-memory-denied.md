# No.100 native frontend batch memory denial and streamed-recycle boundary

Five representative safe frontend modules were replayed through source-frozen
v13 pcc1.  Five fresh singleton processes took 26.76s wall / 26.06s CPU and
peaked at 1.326GB.  One process handling all five took 22.94s / 22.61s
(1.166x wall, 1.153x CPU) but peaked at 2.687GB, or 2.03x control.  All five
result rows, LLVM files and assemblies were exact; both arms returned zero
with empty stderr.

Artifacts:

- `build/no100-v13-frontend-batch-probe/fresh-run/`
- `build/no100-v13-frontend-batch-probe/batch-run/`
- `build/no100-v13-frontend-batch-probe/fresh/ir/`
- `build/no100-v13-frontend-batch-probe/batch/ir/`

The five-module worker is denied against the registered <=1.5x RSS line.  The
worker currently decodes every assigned AST before its module loop, so direct
frontend release cannot reclaim the other AST graphs.  The next bounded slice
streams one AST at a time and adds an explicit recycle-count knob whose default
remains singleton.  It must repeat this probe under a rebuilt pcc1 with exact
output, >=1.10x wall/CPU and <=1.5x/2GB RSS before any Stage2 run.

This evidence does not change current Stage timings, prove safe production
recycling, or authorize Stage3/GC1--4.
