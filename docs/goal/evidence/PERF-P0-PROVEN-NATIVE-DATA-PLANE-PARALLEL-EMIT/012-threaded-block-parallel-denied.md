# Threaded pcc-Python block parallelism denied

## Runtime bridge proof

An isolated `PCC_WITH_THREADS=1` pcc-Python archive owns pthread
start/join/STW through `freestanding_thread_kernel_pthread.o`, contains no C
`pcc_threads.o`, and passed raw ownership/start/join plus high-level Thread
object gates. The candidate bridge proved two real workers rendezvous under a
Lock, both perform GC0 `gc.collect()`, a dropped local Thread reference retains
the worker, exception TLS remains isolated, and the default archive remains
synchronous. Final focused runtime gate: 3 passed in 113.54s.

## Claim-grade compiler

- exact source delta: `py_threading.py`, AArch64 dense block emitter,
  self-backend native pool, and Mach-O parallel module;
- host: CPython 3.15.0rc1;
- pcc1: `90298ddcc758ddfe4173832643da0f5baaec02053b077dcdf61ccd4edc5bbe53`;
- runtime: threaded pcc-Python `fdbabe79b1db96f8224da0ac85146b882453c6722f2b9319e1cf0919718b9c39`;
- receipt mode: `PCC_WITH_THREADS=1`, GC0, self/no-libpython, libSystem-only;
- Stage1: 277.42s wall / 1,140.65 CPU-s / 188.031B instructions /
  1,488,586,360B footprint.

The first pcc1 build is retained but invalid for threading because the old
Stage1 tool dropped ambient `PCC_WITH_THREADS`. The retained tool fix adds
explicit `--with-threads {0,1}`, defaults to zero and persists the value in the
build/smoke receipt; its focused tests pass 2/2.

## Worker verdict

```text
arm                       wall       CPU       instructions     footprint   asm
No.89 baseline            13.39s    13.36s      193.782B        1.241GB    exact
threaded candidate off    21.76s    21.72s      314.119B        1.257GB    exact
candidate/base             1.625x    1.626x       1.621x        1.0127x
threaded candidate auto   >30s then rc1: all() argument is not iterable
```

## Verdict

`[DENIED]`. Thread-safe compiler code pays a 62% instruction tax before
parallel work; the 14.8% block subtree cannot repay it. No formal pairs,
Stage2, fixed point or GC1--4 ran. All four candidate production files are
byte-identical to accepted No.89 after forward rollback. The runtime bridge
design remains documented but is not current production.
