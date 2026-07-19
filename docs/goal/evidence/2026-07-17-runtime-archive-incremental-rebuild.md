# M0-P0-RUNTIME-ARCHIVE-INCREMENTAL-REBUILD closure evidence

Runtime staleness no longer means unconditional `make -B`. Normal
source/header/helper-list changes now use Make's dependency graph; full rebuild
is reserved for target, runtime configuration, or pcc compiler changes.
pcc-Python objects depend on their own source instead of the whole Makefile, so
adding one C-kernel helper does not recompile every runtime-high module.

The triggering archive repair rebuilt one changed pcc-Python module plus the
header-dependent C helpers in **3.8s**. The previous path attempted the entire
runtime and left pcc1 linking without an archive. During the repair, a duplicate
`_ptr_can_have_header` definition was removed: old pcc1 had concatenated both
bodies into one invalid LLVM function, while the retained definition exactly
matches the C runtime's pointer validity window.

Gates:

- archive staleness/incremental policy — **5 passed in 0.42s**;
- old-pcc1 `py_obj.py` IR emit plus LLVM verify — **passed in 0.7s**;
- incremental pcc1 runtime archive build — **passed in 3.8s**;
- canonical pcc1/Metal GC0..4 workload after repair — **1 passed in 2.27s**.

No bootstrap chain or GCC suite was run.

