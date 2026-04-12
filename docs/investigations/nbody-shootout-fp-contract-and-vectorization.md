# Investigation Report: `nbody_shootout` Was Not "Just Missing Vectorization"

## Executive Summary

`benchmarks/nbody_shootout.c` exposed a real optimization gap between `pcc` and
clang, but the original explanation was too shallow.

The bad state was:

- `pcc` `-O2` and `-O3` were around `1.8s` on this benchmark
- clang `-O2` / `-O3` were around `0.5s`
- `pcc` was therefore more than `3x` slower on a benchmark that is mostly
  floating-point arithmetic over a tiny fixed-size array

The first useful fix was not "add more passes". It was enabling floating-point
contraction on emitted LLVM arithmetic so the backend could actually form fused
multiply-add instructions.

After that change:

- `pcc` `-O2` improved from roughly `3.5x` slower to about `1.17x` slower
- `pcc` `-O3` improved from roughly `3.3x` slower to about `1.13x` slower in
  repeated benchmark runs
- the huge regression was gone

The remaining gap is real, but the evidence says it is **not** mostly one
missing switch such as:

- SLP vectorization alone
- `-ffp-contract`
- strict-aliasing metadata alone
- target-cpu selection alone

Instead, clang still wins by a collection of smaller advantages:

- better frontend IR shape
- more profitable scalar scheduling and canonicalization
- some extra inlining / unrolling wins
- small but real vectorization opportunities


## What Was Wrong

Before the fix, `pcc` generated scalar floating-point IR without contraction
flags, so LLVM had much less freedom to form fused operations in the backend.

That mattered a lot for `nbody`, which spends most of its time in patterns like:

```c
dx * dx + dy * dy + dz * dz
dt / (dist2 * dist)
bodies[i].x += dt * bodies[i].vx;
```

With the old codegen, `pcc`'s optimized IR still contained plain scalar `fmul`
and `fadd` instructions and the final assembly missed a large amount of the
`fmadd` / `fmsub` structure clang was getting.


## Landed Fix

`pcc/codegen/c_codegen.py` now routes floating-point arithmetic through helper
builders that attach the LLVM `contract` fast-math flag to emitted floating
operations.

This affects:

- binary floating-point `+`, `-`, `*`, `/`, `%`
- floating-point compound assignments
- floating-point `++` / `--`

A regression test was added in `tests/test_float_semantics.py` asserting that
generated IR contains contracted floating-point instructions.


## Validation

Focused regression checks after the change:

```bash
env -u LC_ALL uv run pytest tests/test_float_semantics.py -q -n0
env -u LC_ALL uv run pytest tests/test_pass_framework.py tests/test_clang_compat.py tests/test_char.py -q -n0
```

Observed results:

- `tests/test_float_semantics.py`: `4 passed`
- `tests/test_pass_framework.py tests/test_clang_compat.py tests/test_char.py`: `118 passed`


## Benchmark Results

### Targeted benchmark after the fix

```bash
env -u LC_ALL uv run python benchmarks/run_benchmarks.py \
  --bench nbody_shootout.c \
  --opt-level 1 --opt-level 2 --opt-level 3 \
  --runs 3
```

Representative result after the `contract` fix:

- clang `-O3`: about `513ms`
- `pcc` `-O3`: about `578ms`
- ratio: about `1.13x`

This is still slower, but it is no longer the earlier `3x+` failure.

### Repeated direct runs

Independent five-run medians produced the same shape:

- clang `-O3`: about `0.500s`
- `pcc` `-O3`: about `0.567s`


## What The IR And Assembly Showed

### Clang `-O3`

clang's optimized IR for this benchmark contains:

- many `llvm.fmuladd.f64`
- `llvm.fmuladd.v2f64`
- `<2 x double>` vector loads/stores
- extensive `!tbaa`

Its final AArch64 assembly contains both:

- scalar `fmadd`
- vector `fmla.2d`

clang also reports SLP vectorization on the position-update stores at line 71.

### `pcc` after the `contract` fix

`pcc`'s optimized IR now contains:

- many `fmul contract`
- many `fadd contract`
- many `fsub contract`

Its final AArch64 assembly now contains:

- many scalar `fmadd`
- scalar `fmsub`

So the post-fix compiler is **not** missing FMA anymore.

What it still does **not** contain by default:

- `llvm.fmuladd.*`
- `<2 x double>` vector IR
- `!tbaa`


## Experiments That Ruled Out Easy Explanations

### 1. Disable clang vectorization

Repeated runs of these variants stayed near baseline:

- `clang -O3`
- `clang -O3 -fno-slp-vectorize`
- `clang -O3 -fno-vectorize`
- `clang -O3 -fno-vectorize -fno-slp-vectorize`

Typical medians:

- baseline clang `-O3`: about `0.500s`
- no SLP: about `0.504s`
- no loop vectorization: about `0.498s`
- no vectorization and no SLP: about `0.512s`

Conclusion:

- vectorization helps a little
- it does **not** explain the original `3x` regression
- it does **not** explain most of the remaining `~13%` gap either

### 2. Disable clang FP contraction

`clang -O3 -ffp-contract=off` stayed near baseline as well.

Conclusion:

- clang's explicit frontend `llvm.fmuladd` use is not the whole story by itself
- the important part for `pcc` was that the backend previously had no
  contraction freedom at all

### 3. Disable strict aliasing

`clang -O3 -fno-strict-aliasing` showed almost no change on this benchmark.

Conclusion:

- missing `!tbaa` is a real structural difference
- but it is not the main driver for this specific benchmark

### 4. Disable unrolling or inlining

These hurt clang a little:

- `clang -O3 -fno-unroll-loops`: about `0.534s`
- `clang -O3 -fno-inline-functions`: about `0.515s`

Conclusion:

- some of the remaining gap is plausibly coming from general middle-end quality
- not one isolated frontend metadata bug

### 5. Force host CPU in llvmlite target machine

Using `create_target_machine(cpu="apple-m1")` for `pcc` on this workload was a
bad idea in practice. In a direct experiment it made the benchmark much slower,
around `1.48s`.

Conclusion:

- do not blindly switch the repository to host-cpu target machines just because
  clang annotates `target-cpu="apple-m1"`
- with the current llvmlite/LLVM setup, that change is not a free win

### 6. Explicitly enable SLP in llvmlite

`llvmlite.create_pipeline_tuning_options()` defaults to:

- `loop_vectorization=True`
- `loop_interleaving=True`
- `loop_unrolling=True`
- `slp_vectorization=False`

Turning `slp_vectorization=True` on for this benchmark did produce vector IR:

- `<2 x double>` loads
- vector `fmul contract`
- vector `sqrt`

But the runtime change was tiny:

- default: about `0.551s`
- SLP on: about `0.548s`

Conclusion:

- SLP is not the missing silver bullet here either


## Bottom Line

The useful conclusion from this benchmark is:

1. `pcc` really did have a frontend/codegen issue that blocked important
   floating-point optimization opportunities.
2. Enabling floating-point contraction in emitted IR was the right fix and
   removed the catastrophic regression.
3. The remaining gap is not well-described as "clang wins because of one FMA or
   vectorization switch".
4. Further wins will likely come from better IR shaping and canonicalization,
   not from flipping one more obvious LLVM knob.


## Next Directions Worth Pursuing

- improve frontend IR shape for small fixed-size aggregates and arithmetic
  chains so LLVM sees cleaner combine opportunities earlier
- compare `pcc` and clang optimized IR around `advance()` and `energy()` after
  simplification, not only final assembly
- quantify whether enabling SLP by default helps the full benchmark suite,
  rather than just `nbody`
- keep measuring compile-time cost when adding optimization pipeline knobs;
  `pcc` is already slower than clang on compile time


## Directions That Do Not Currently Look Worth It

- blaming the whole gap on missing vectorization
- blaming the whole gap on missing `!tbaa`
- defaulting to host-cpu target machines without broader evidence
- assuming "more passes" automatically means faster than clang on real code
