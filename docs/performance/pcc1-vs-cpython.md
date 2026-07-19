# pcc1 vs CPython Performance Comparison

Date: 2026-06-15
Host: macOS 26.5.1, Darwin 25.5.0, arm64
Python: 3.14.5
pcc1: [/Users/jiamo/my/pcc/build/bootstrap-pytest-self-gc4/pcc1](/Users/jiamo/my/pcc/build/bootstrap-pytest-self-gc4/pcc1)
Mode: strict no-libpython unless stated otherwise (`--python-libpython=off --ir-scaffold=on`)

This report compares several different things. They are not interchangeable:

- **pcc1 compiler speed**: native pcc1 compiling Python inputs.
- **host pcc speed**: CPython running the pcc source through `uv run pcc`.
- **target-program speed**: a pcc-emitted binary running the benchmark.
- **C frontend speed**: pcc compiling C compared with clang.
- **runtime/GC behavior**: pcc-emitted Python binaries under `PCC_GC_BACKEND=0..4`.
- **feature evidence**: correctness and IR-shape gates are listed separately from performance results.

## Concrete Conclusions

1. pcc1 remains faster than host pcc on the small compiler smoke benchmark:
   self-backed smoke compile is **0.787x** host pcc, and LLVM-backed smoke
   compile is **0.774x** host pcc.
2. pcc-emitted Python binaries are strict no-libpython in the sampled runs:
   pcc1 and the typed-loop binaries link only `/usr/lib/libSystem.B.dylib`.
3. Typed integer loops are faster than CPython: the large scenario is
   **0.423x** CPython with the self backend and **0.202x** CPython with LLVM.
4. Dict-heavy Python is faster than CPython in the sampled GC #0/#3/#4 runs.
   Latest ratios: self GC #0 **0.638x**, self GC #3 **0.940x**, self GC #4
   **0.915x**; LLVM GC #0 **0.623x**, LLVM GC #3 **0.945x**, LLVM GC #4
   **0.962x**. The earlier GC #3/#4 outliers (**1.702x** and **3.680x**) are
   no longer present on this short workload.
5. Closure-heavy Python is much faster than CPython in this benchmark:
   **0.161x** CPython with the self backend and **0.143x** with LLVM.
6. The five-GC full self-host bootstrap matrix is green after the GC fixes:
   latest post-matrix validation is **5 passed in 297.55s**, with `pcc2` and
   `pcc3` byte-identical for GC #0/#1/#2/#3/#4.
7. The five-GC long-run smoke matrix exits cleanly for all 20 workload/backend
   pairs. GC #4 still pays a visible RSS/heap tax, but it no longer has a
   catastrophic short-run throughput gap.
8. Each GC backend now has one encoded advantage workload with 9-run median
   evidence. See
   [/Users/jiamo/my/pcc/docs/performance/gc-advantage-workloads.md](/Users/jiamo/my/pcc/docs/performance/gc-advantage-workloads.md).
9. The C frontend emits correct binaries for the sampled C benchmarks and has
   near-clang execution speed (**0.94x** clang geomean), but pcc compile time is
   still slower (**2.44x** clang geomean).
10. BoC/ring parallelism is still not performance-green: correctness passed,
   but speedup was **1.42x**, below the **1.5x** gate.
11. Valueclass/valuebox has correctness and IR-shape evidence in this run
    (**58 passed** across representative gates), but no runtime valueclass
    benchmark exists yet. Do not infer value-model performance from typed-int
    or closure-heavy results.

## Linkage Evidence

`otool -L` showed no libpython dependency for pcc1:

| Artifact | Linked libraries |
|---|---|
| pcc1 | `/usr/lib/libSystem.B.dylib` only |

The sampled typed-loop binaries also reported `binary_links_libpython=False`.

## Compiler: pcc1 vs Host pcc

Command shape:

```bash
env -u LC_ALL uv run python benchmarks/bench_pcc1.py \
  --pcc1 build/bootstrap-pytest-self-gc4/pcc1 \
  --runs 3 \
  --baseline-cmd 'env -u LC_ALL uv run pcc' \
  --backend self \
  --python-libpython off \
  --ir-scaffold on
```

The same command was also run with `--backend llvm`.

| Backend | Metric | pcc1 | host pcc | pcc1 / host |
|---|---|---:|---:|---:|
| self | `--help` median | 0.008s | 0.093s | 0.088x |
| self | smoke compile geomean | 0.332s | 0.422s | 0.787x |
| self | smoke target run geomean | 0.004s | 0.004s | 0.856x |
| LLVM | `--help` median | 0.008s | 0.092s | 0.091x |
| LLVM | smoke compile geomean | 0.400s | 0.517s | 0.774x |
| LLVM | smoke target run geomean | 0.003s | 0.004s | 0.760x |

Interpretation: pcc1 is locally faster than host pcc on this small smoke set.
This does not prove full compiler-workload superiority. The benchmark source is
[/Users/jiamo/my/pcc/benchmarks/bench_pcc1.py](/Users/jiamo/my/pcc/benchmarks/bench_pcc1.py).

## Target Python Runtime vs CPython

Typed-loop command shape:

```bash
env -u LC_ALL uv run python benchmarks/bench_py_runtime.py \
  --runs 5 \
  --n 1000000 \
  --pcc-cmd 'uv run pcc' \
  --backend self \
  --python-libpython off \
  --ir-scaffold on
```

| Backend | Compile | CPython median | pcc median | pcc / CPython |
|---|---:|---:|---:|---:|
| self | 0.422018s | 0.060505s | 0.040688s | 0.672x |
| LLVM | 0.504921s | 0.060795s | 0.017633s | 0.290x |

Scenario benchmarks from
[/Users/jiamo/my/pcc/benchmarks/python/scenarios](/Users/jiamo/my/pcc/benchmarks/python/scenarios):

| Scenario | Backend | GC | Compile | CPython median | pcc median | pcc / CPython | Result |
|---|---|---:|---:|---:|---:|---:|---|
| `typed_loop.py` | self | 0 | 0.425s | 1.383453s | 0.584725s | 0.423x | faster |
| `typed_loop.py` | LLVM | 0 | 0.553s | 1.383453s | 0.278847s | 0.202x | faster |
| `dict_heavy.py` | self | 0 | 0.429s | 0.035917s | 0.022900s | 0.638x | faster |
| `dict_heavy.py` | self | 3 | 0.429s | 0.035917s | 0.033750s | 0.940x | faster |
| `dict_heavy.py` | self | 4 | 0.429s | 0.035917s | 0.032857s | 0.915x | faster |
| `dict_heavy.py` | LLVM | 0 | 0.488s | 0.035917s | 0.022364s | 0.623x | faster |
| `dict_heavy.py` | LLVM | 3 | 0.488s | 0.035917s | 0.033953s | 0.945x | faster |
| `dict_heavy.py` | LLVM | 4 | 0.488s | 0.035917s | 0.034541s | 0.962x | faster |
| `closure_heavy.py` | self | 0 | 0.459s | 0.023319s | 0.003757s | 0.161x | faster |
| `closure_heavy.py` | LLVM | 0 | 0.508s | 0.023319s | 0.003333s | 0.143x | faster |

Interpretation: pcc has real wins on typed arithmetic, closure-heavy code, and
this short dict-heavy sample. The GC #3/#4 dict-heavy improvement came from a
common `pcc_gc_release(NULL/tagged-int)` fast path that avoids backend queries
for values with no heap lifetime. This is not a broad claim that arbitrary
dynamic Python is faster than CPython.

## Five-GC Bootstrap

Command:

```bash
gtimeout 1200s env -u LC_ALL PCC_BOOTSTRAP_FULL_REBUILD=1 uv run pytest -q -n0 -s \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py
```

Latest post-matrix validation result: **5 passed in 297.55s (0:04:57)**. Each
backend completed `pcc1 -> pcc2 -> pcc3`, and each backend's `pcc2`/`pcc3`
outputs were byte-identical after the gate's normalisation.

Earlier same-turn pre-`pcc_gc_release` fast-path validation:
**5 passed in 354.24s (0:05:54)**.

Additional focused evidence from the same fix slice:

| Gate | Result |
|---|---|
| GC1 full bootstrap | 1 passed in 60.49s |
| GC2 full bootstrap | 1 passed in 50.61s |
| GC0/GC3/GC4 full bootstrap rerun | 3 passed in 166.28s |
| GC production contract | 130 passed in 51.08s |
| Focused GC / hot-path regressions | 9 passed in 12.63s |
| Post final hot-path full bootstrap validation | 5 passed in 297.55s |

The GC #4 performance fix was not a semantic shortcut. It bounds reusable
zpage cache/search, marks malloc-origin objects so they skip retained zpage
address scans, retains old zpage spans when physical release is not yet safe,
and adds a common tagged-int release fast path. Slot/root/barrier contracts,
weakrefs, finalizers, owned-local cleanup, relocation read barriers, and
no-libpython bootstrap checks remain enabled. The GC #1/#2 bootstrap crash was
a separate stale-shell refcount bug in the pcc-Python `py_decref` mirror; the
fix is gated by the full bootstrap matrix above.

## Five-GC Long-Run Smoke

Command:

```bash
env -u LC_ALL bash scripts/gc_longrun.sh \
  /tmp/pcc-gc-longrun-perf-20260615 20000 400 10000 20000
```

All 20 workload/backend pairs exited with code 0.

| Workload | GC | Ops/s | RSS MiB | Heap MiB | Capacity MiB | Pauses | Max Pause us | Canary Gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| churn | 0 | 785276 | 2.9 | 1.1 | 16.0 | 0 | 0 |  |
| churn | 1 | 550064 | 5.9 | 1.8 | 32.0 | 156 | 198 |  |
| churn | 2 | 526749 | 5.6 | 1.8 | 24.0 | 156 | 183 |  |
| churn | 3 | 507333 | 4.3 | 1.7 | 20.0 | 0 | 0 |  |
| churn | 4 | 407903 | 5.5 | 3.0 | 24.0 | 0 | 0 |  |
| growshrink | 0 | 1069333 | 5.0 | 2.1 | 16.0 | 0 | 0 |  |
| growshrink | 1 | 843170 | 7.6 | 4.4 | 20.0 | 55 | 318 |  |
| growshrink | 2 | 886877 | 7.5 | 4.4 | 20.0 | 99 | 303 |  |
| growshrink | 3 | 785132 | 7.5 | 4.1 | 24.0 | 0 | 0 |  |
| growshrink | 4 | 641099 | 11.7 | 7.9 | 32.0 | 0 | 0 |  |
| finalizers | 0 | 1114983 | 1.8 | 0.1 | 16.0 | 0 | 0 | 1 |
| finalizers | 1 | 1072027 | 2.0 | 0.5 | 16.0 | 1374 | 81 | 1 |
| finalizers | 2 | 1107266 | 2.0 | 0.5 | 16.0 | 1374 | 12 | 1 |
| finalizers | 3 | 978593 | 2.0 | 0.5 | 20.0 | 0 | 0 | 1 |
| finalizers | 4 | 742459 | 2.1 | 0.5 | 24.0 | 0 | 0 | 1 |
| pointer_mutator | 0 | 2346471 | 2.7 | 0.9 | 20.0 | 0 | 0 |  |
| pointer_mutator | 1 | 2165821 | 3.4 | 1.5 | 20.0 | 209 | 276 |  |
| pointer_mutator | 2 | 2151261 | 3.4 | 1.5 | 20.0 | 209 | 257 |  |
| pointer_mutator | 3 | 2151261 | 3.4 | 1.5 | 20.0 | 0 | 0 |  |
| pointer_mutator | 4 | 1970747 | 4.2 | 2.2 | 24.0 | 0 | 0 |  |

Interpretation:

- Backend #0 remains the fastest reference in this short run.
- Backends #1/#2 show pause telemetry where incremental/concurrent work fires.
- Backend #4 is now bootstrap-correct and much less pathological on short
  workloads, but it still pays more heap/RSS than #0/#3.

## Five-GC Advantage Matrix

Command:

```bash
gtimeout 900s env -u LC_ALL uv run python benchmarks/run_gc_advantage_matrix.py \
  --outdir /tmp/pcc-gc-advantage-matrix-20260615-final-v3 \
  --reps 9
```

Result: all five target workloads ran under all five backends. Each target
backend won its encoded metric:

| Target GC | Workload | Winning metric | Median value |
|---:|---|---|---:|
| 0 | `gc0_refcount_steady_churn` | `elapsed_us` | 7134 |
| 1 | `gc1_incremental_explicit_churn` | `elapsed_us` | 7231 |
| 2 | `gc2_cms_heap_under_high_collect_churn` | `heap_bytes` | 1376592 |
| 3 | `gc3_generational_high_frequency_collect` | `elapsed_us` | 48302 |
| 4 | `gc4_colored_low_total_pause` | `pause_sum_us` | 91 |

Full details and reproduction links are in
[/Users/jiamo/my/pcc/docs/performance/gc-advantage-workloads.md](/Users/jiamo/my/pcc/docs/performance/gc-advantage-workloads.md).

## C Frontend: pcc vs Clang

Command:

```bash
env -u LC_ALL uv run python benchmarks/run_benchmarks.py \
  --runs 3 \
  --opt-level 2 \
  --bench fib35.c \
  --bench matmul.c \
  --bench hash_table.c \
  --bench stringsearch.c
```

Compiler: Homebrew clang 20.1.8.

| Benchmark | Compile pcc/clang | Exec pcc/clang | Output match |
|---|---:|---:|---|
| `fib35.c` | 2.33x | 1.04x | true |
| `matmul.c` | 2.40x | 1.01x | true |
| `hash_table.c` | 2.53x | 0.72x | true |
| `stringsearch.c` | 2.53x | 1.04x | true |
| geomean | 2.44x | 0.94x | 4/4 |

Interpretation: pcc's C frontend is execution-competitive on this subset, but
compile throughput remains significantly behind clang.

## Feature Matrix

| Feature area | Snapshot result | Performance conclusion |
|---|---|---|
| pcc1 no-libpython compiler | pcc1 smoke compile/run passed; no libpython link | locally faster than host pcc on smoke compile |
| self backend | Python scenarios compiled and ran | typed/closure wins; dict-heavy GC #0/#3/#4 wins on this sample |
| LLVM backend | Python scenarios compiled and ran | best typed-loop result; dict-heavy GC #0/#3/#4 wins on this sample |
| self-host fixed point | full GC #0..#4 bootstrap matrix passed | correctness green; latest post-matrix validation is 297.55s on this host |
| C frontend | 4 sampled C benchmarks matched clang output | runtime near clang, compile slower |
| GC backend #0 | bootstrap, long-run smoke, and advantage matrix passed | fastest reference for cycle-free churn |
| GC backend #1 | bootstrap, long-run smoke, and advantage matrix passed | explicit-collection churn throughput window |
| GC backend #2 | bootstrap, long-run smoke, and advantage matrix passed | smallest heap/RSS in high-frequency node churn |
| GC backend #3 | bootstrap, long-run smoke, and advantage matrix passed | high-frequency explicit-collect throughput window |
| GC backend #4 | bootstrap, long-run smoke, and advantage matrix passed | lowest total pause in sparse explicit-collection case; RSS/heap tax remains |
| BoC/parallelism | `test_boc_benchmarks.py`: 2 passed, ring failed speedup gate at 1.42x | correctness mostly present; speedup not reliable |
| valueclass/valuebox | representative correctness/IR gates: 58 passed | no runtime performance claim |
| package/C-API ecosystem | not benchmarked here | no ecosystem performance claim |
| x86_64 Linux self backend | not run here | no Linux performance claim |

## Follow-Up Targets

1. Turn valueclass/valuebox into a real runtime benchmark, not only IR-shape
   evidence.
2. Restore BoC ring speedup above the 1.5x gate or lower the claim if the
   workload no longer reflects the intended parallelism target.
3. Continue reducing GC #3/#4 collector bookkeeping and GC #4 heap/RSS
   pressure. Dict-heavy is no longer slower than CPython in the sampled short
   scenario, but that does not prove arbitrary dict/string-heavy programs.
4. Improve C frontend compile throughput; execution speed is already near clang
   on this subset.
5. Add a stable report script that emits this matrix directly instead of
   stitching together several benchmark tools by hand.
