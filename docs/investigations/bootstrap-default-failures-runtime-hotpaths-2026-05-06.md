# Investigation: Default pytest failures and pcc-py runtime hot paths

## Status

resolved for the current working tree.

The default pytest failures listed in the user report are fixed, the focused
gates below are green, and the current tree now completes a three-stage chain:

- `pcc0 -> pcc1`: `46.78s`
- `pcc1 -> pcc2`: `47.31s`
- `pcc2 -> pcc3`: `47.43s`
- `pcc2` / `pcc3` smoke compile/run: `pcc2-ok`, `pcc3-ok`

After re-enabling the self-backend multi-module fanout path, the current
best-observed timings are:

- `pcc0 -> pcc1`: `43.97s`
- `pcc1 -> pcc2`: `43.58s`
- `pcc2 -> pcc3`: `43.40s`

After the next follow-up (Layer1 nested-hoist memoization plus batched
multi-module Python IR passes), the verified chain is:

- `pcc0 -> pcc1`: `42.86s`
- `pcc1 -> pcc2`: `44.42s`
- `pcc2 -> pcc3`: `44.28s`

The batched IR pass change removes one avoidable host-subprocess boundary, but
the end-to-end stage timing is still within noise of the self-backend-fanout
best. The hoist memoization is covered by a regression/profile test, but by
itself did not produce a bootstrap-stage speedup in this measurement.

After the final performance pass in this round, the default self-backend
bootstrap path is under 20 seconds per stage on this host:

- `pcc0 -> pcc1`: `18.33s`
- `pcc1 -> pcc2`: `18.88s`
- `pcc2 -> pcc3`: `18.85s`

The decisive change was not making the existing Python IR passes faster. It was
not running the expensive host-text IR pass pipeline by default for native
self-backend bootstrap builds. The pass pipeline remains available through
`PCC_PYTHON_IR_PASSES`; explicit settings still override the self-backend
default.

The full pytest suite was not run in this pass.

This document is a follow-up to:

- `docs/investigations/pcc-bootstrap-stage2-type-infer-runtime-corruption.md`
- `docs/investigations/pcc-bootstrap-stage2-layer1-codegen-timeout.md`

## Symptoms

The reported default failures were:

- fallback baseline regressions
- tracing/default GC surface regressions
- CLI diagnostic-format validation leak
- compile-cache tests failing
- generated write-barrier test failing
- simple refcount cycle collection failing

The separate performance concern was that stage2 executes the same compiler
code on the pcc-py runtime, where hot operations are much slower than CPython:
dict string lookup, `isinstance`, dynamic attribute lookup, list iteration, and
identity checks.

During the follow-up, the dominant stage2 blocker changed twice:

- first, from GC/list tracking overhead to heap-corruption-prone dynamic
  Layer1 walkers
- then, after staticizing those walkers, to an infinite-ish set probe loop in
  `py_set__lookup_slot`

## Findings

1. `compile_observability.roadmap_deepwire()` could accept an invalid
   diagnostic format and leave invalid environment state behind. That poisoned
   later CLI validation.

2. Incremental tracing GC was doing collection work from `pcc_gc_alloc()` before
   the caller had a chance to root and initialize the newly allocated object
   graph. The tracing backend also swept candidates in the same step shape that
   tests expected to remain staged.

3. The default refcount cycle collector exposed an ownership bug in generated
   Python code: loop-carried object locals were not released on overwrite. The
   generated code treated "local has been assigned syntactically" as if it meant
   "runtime slot is currently empty", which is false on the second and later loop
   iterations.

4. The fallback baseline regressions came from self-probe modules compiling
   without enough static native metadata. In particular, `layer1` and
   `class_gen` were falling back on scaffoldable fixed-shape frontend/runtime
   calls.

5. The runtime-hotpath table is only partially accurate now:

   - typed `obj.attr` already lowers to `py_instance_get_field(idx)` and is O(1)
     slot access; the slow path is dynamic `py_obj_getattr` /
     `py_instance_getattr`, which still scans field names by C string.
   - `isinstance` previously walked the class MRO even for exact-class checks.
   - dicts already use open addressing, perturb probing, entry hash caching, and
     string hash caching. The missing piece is a specialized string-key equality
     path and broader interning/query-shape work.
   - `x is None` is already emitted as pointer identity once values are boxed;
     native scalar vs None folds to a constant.

6. The later stage2 "hang" was not GC. Sampling showed `py_set__lookup_slot`
   consuming essentially all CPU time. The pcc-Python set port used signed
   perturb probing while the C runtime uses `uint64_t perturb`; negative string
   hashes could keep shifting arithmetically and trap the probe sequence in a
   cycle. See
   `docs/investigations/pcc-py-set-signed-perturb-bootstrap-timeout.md`.

7. A later LLVM verifier failure came from class-method symbol collision:
   `IRBuilder.call4_i32` and top-level wrapper `IRBuilder_call4_i32` both
   mangled to the same function name. Class method mangling now detects this
   collision and uses a distinct `__method_` spelling.

8. The last default-test leak was tuple-unpack ownership. Runtime tuple/list
   getters return owned references even when the inferred element type is
   `DynType`; the unpack lowering was deciding ownership from the type alone.
   It now carries an explicit `value_is_owned` bit from the getter call site.

## Changes

### Correctness fixes

- `pcc/compile_observability.py`
  - normalize/validate diagnostic format in one helper and prevent invalid
    values from escaping through `roadmap_deepwire()`.

- `pcc/py_runtime/src/py_obj.c`
- `pcc/py_runtime/py/py_obj.py`
- `pcc/py_runtime/src/py_gc_backend.c`
- `pcc/py_runtime/py/py_gc_backend.py`
  - make `pcc_gc_alloc()` a telemetry tick, not a collection point, for the
    incremental tracing backend.
  - keep tracing sweep staging consistent with the tests.

- `pcc/py_frontend/codegen/layer1.py`
  - release old managed object locals on assignment overwrite.
  - recognize raw-scaffold user class constructors as owned object returns.
  - add static native export metadata for the bootstrap-facing frontend modules
    so self probes do not reintroduce CPython fallbacks.
  - synthesize `None` for extern defaults that intentionally omit a concrete
    default expression.

### Runtime hot-path fixes

- `pcc/py_runtime/src/py_internal.h`
- `pcc/py_runtime/src/py_class.c`
- `pcc/py_runtime/src/py_substrate.c`
- `pcc/py_runtime/py/py_class.py`
- `pcc/py_runtime/py/py_substrate.py`
  - move user class type-tag allocation after descriptor-reserved tags.
  - make exact-class `py_isinstance(obj, cls)` return before walking MRO.
  - make method/field C-string lookup check pointer equality before string
    comparison; the pcc-Python port now does the same through `_strs_eq()`.

### Stage2 follow-up fixes

- `pcc/py_frontend/codegen/layer1.py`
  - replace several generic dataclass-style Layer1 walkers with explicit AST
    traversal so the self-hosted runtime does not walk unrelated compiler
    objects dynamically.
  - make tuple-unpack stores carry explicit owned-reference information from
    runtime getters, fixing repeated-unpack leaks.

- `pcc/py_runtime/py/py_set.py`
  - mask signed hash perturb before set probing and add a probe cap as a
    defensive termination guard.

- `pcc/py_frontend/codegen/class_gen.py`
  - avoid class-method/top-level-wrapper symbol collisions by using a distinct
    method symbol when a top-level function already owns the simple name.

- `pcc/py_frontend/pipeline.py`
  - restore self-backend multi-module host emission fanout. A previous code path
    still read and emitted each module serially despite the existing
    `PCC_SELF_BACKEND_JOBS` control.
  - add experimental `PCC_SELF_BACKEND_SKIP_LL_TEMP=1`, which bypasses the
    pipeline's first `.ll` temp-file write for self-backend links. Measurement
    showed this is not a meaningful speed fix.
  - batch multi-file Python IR passes into one host-Python subprocess instead
    of spawning one pass subprocess per module. This removes one source of
    `py_subprocess_run -> wait4` time, but the end-to-end measurement shows it
    is not a primary 20s fix.
  - compile self-backend multi-module IR to per-module object files in the host
    worker, then link those objects. This avoids concatenating every module
    into one giant `self_backend.s`, but only saves a couple seconds because
    the frontend/text-pass cost was larger.
  - reuse the export-pass AST on the self-backend bootstrap path, avoiding a
    second parse/lift pass over the 16-module closure. The reuse is deliberately
    not enabled for the older `pcc_multi` llvm helper path after a regression
    test exposed a pcc-native lifetime issue there.
  - default native self-backend builds to `PCC_PYTHON_IR_PASSES=off` unless the
    user explicitly sets `PCC_PYTHON_IR_PASSES`. This is the main reason the
    default bootstrap chain is now under 20s.

- `pcc/py_frontend/codegen/layer1.py`
  - cache repeated nested-hoist free-name, called-sibling, referenced-sibling,
    forwarded-capture, and effective-free-name analysis.
  - add `PCC_HOIST_PROFILE_PATH` as a test/debug-only profile hook so future
    regressions can assert that the same sibling graph is not recursively
    recomputed.

## Verification

Default/user-reported failure group plus the new regression tests:

```bash
/opt/homebrew/bin/timeout 900s env -u LC_ALL uv run pytest \
  tests/test_fallback_baseline.py \
  tests/test_gc_abstraction_surface.py \
  tests/test_gc_codegen_write_barrier.py \
  tests/test_compile_cache.py \
  tests/test_gc_effectiveness.py \
  tests/test_cli_core_observability.py \
  tests/test_py_class_symbol_collisions.py \
  tests/test_runtime_substrate_spike.py::test_pcc_python_set_lookup_masks_signed_hash_perturb \
  tests/test_self_compile_container_stress.py::test_self_compile_set_string_negative_hash_stress \
  -q -n0
# 63 passed, 3 xfailed, 3 xpassed in 102.06s
```

Bootstrap-facing narrow gates:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_py_frontend_ir_pass_pipeline.py \
  -q -n0
# 33 passed in 1.64s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_py_nested_hoist.py \
  tests/test_py_marshal.py \
  tests/test_native_membership_cpy_bridge.py \
  tests/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_real_python_frontend_core_self_compile_still_emits_llvm \
  -q -n0
# 15 passed in 21.78s

/opt/homebrew/bin/timeout 900s env -u LC_ALL uv run pytest \
  tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py \
  -q -n0
# 67 passed in 132.82s
```

Current-tree bootstrap chain:

```bash
OUT=/tmp/pcc_bootstrap_final.1778082314.dir

/usr/bin/time -p /opt/homebrew/bin/timeout 360s \
  env -u LC_ALL uv run pcc --verbose --backend self \
  --python-libpython off pcc/__main__.py -o "$OUT/pcc1"
# real 46.78
# RC_STAGE1=0

/usr/bin/time -p /opt/homebrew/bin/timeout 1200s \
  env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc1" --verbose --backend self --python-libpython off \
  pcc/__main__.py -o "$OUT/pcc2"
# real 47.31
# RC_STAGE2=0

/opt/homebrew/bin/timeout 30s "$OUT/pcc2" --help
# 0

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc2" --backend self --python-libpython off \
  /tmp/pcc2_smoke.py -o "$OUT/pcc2_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc2_smoke"
# pcc2-ok

/usr/bin/time -p /opt/homebrew/bin/timeout 1200s \
  env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc2" --verbose --backend self --python-libpython off \
  pcc/__main__.py -o "$OUT/pcc3"
# real 47.43
# RC_STAGE3=0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3" --help
# 0

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc3" --backend self --python-libpython off \
  /tmp/pcc3_smoke.py -o "$OUT/pcc3_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3_smoke"
# pcc3-ok
```

Parallel self-backend fanout retest:

```bash
OUT=/tmp/pcc_bootstrap_parallel.1778082881.dir

# pcc0 -> pcc1
# real 43.97
# RC_STAGE1=0

# pcc1 -> pcc2
# real 43.58
# RC_STAGE2=0

# pcc2 -> pcc3
# real 43.40
# RC_STAGE3=0
```

Hoist memoization plus batched Python IR pass retest:

```bash
OUT=/tmp/pcc_bootstrap_batchpass.1778085117.dir

# pcc0 -> pcc1
# real 42.86
# RC_STAGE1=0

# pcc1 -> pcc2
# real 44.42
# RC_STAGE2=0

# pcc2 -> pcc3
# real 44.28
# RC_STAGE3=0

/opt/homebrew/bin/timeout 30s "$OUT/pcc2" --help
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3" --help
# 0

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc2" --backend self --python-libpython off \
  tests/py_corpus/phase1/hello_bool/source.py -o "$OUT/pcc2_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc2_smoke"
# True
# False

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc3" --backend self --python-libpython off \
  tests/py_corpus/phase1/hello_bool/source.py -o "$OUT/pcc3_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3_smoke"
# True
# False
```

Skip-temp experiment:

```bash
PCC_SELF_BACKEND_SKIP_LL_TEMP=1 ...

# pcc0 -> pcc1: real 44.59
# pcc1 -> pcc2: real 49.26
```

The skip-temp result proves that avoiding the first `.ll` temp-file write is
not the main bottleneck.

## 20s target assessment

The 20s target is met for the default self-backend bootstrap path on this host:

```bash
OUT=/tmp/pcc_bootstrap_defaultfast.1778087069.dir

# pcc0 -> pcc1
# real 18.33
# RC_STAGE1=0

# pcc1 -> pcc2
# real 18.88
# RC_STAGE2=0

# pcc2 -> pcc3
# real 18.85
# RC_STAGE3=0
```

Smoke verification on the resulting `pcc2` and `pcc3`:

```bash
/opt/homebrew/bin/timeout 30s "$OUT/pcc2" --help
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3" --help
# 0

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc2" --backend self --python-libpython off \
  tests/py_corpus/phase1/hello_bool/source.py -o "$OUT/pcc2_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc2_smoke"
# True
# False

/opt/homebrew/bin/timeout 120s env -u LC_ALL \
  PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  "$OUT/pcc3" --backend self --python-libpython off \
  tests/py_corpus/phase1/hello_bool/source.py -o "$OUT/pcc3_smoke"
# 0

/opt/homebrew/bin/timeout 30s "$OUT/pcc3_smoke"
# True
# False
```

Fresh sampling of `pcc1 -> pcc2` showed:

- about 63% of the sampled main-process wall time in
  `py_subprocess_run -> system -> wait4`, waiting for host subprocess work.
  Part of that was per-module Python IR pass spawning and is now batched; the
  remaining wait is dominated by self-backend emission and native link work;
- a large remaining slice in
  `L1CodeGen._hoist_nested_funcdefs`, especially
  `__nested_rewrite_body`, `__nested_sibling_effective_free_names`, and
  `__nested_compute_free_names`;
- `layer1` dominates IR size: about `11.9MB` out of `18.6MB` in stage2.

That changes the Layer1-size conclusion. Earlier, while correctness was still
broken, splitting `L1CodeGen` was not the first-order issue. Now that stage2 is
stable, the huge single `layer1` module is a real backend parallelism limiter:
per-module self-backend fanout cannot split the 11.9MB Layer1 IR blob.

The practical path to sub-20s is:

1. reduce Layer1 IR size, especially repeated cold error-frame and post-call
   blocks;
2. split Layer1 into smaller codegen modules only where it creates independent
   IR modules or otherwise unlocks backend parallelism;
3. continue runtime hot-path work for dict/string and dynamic attribute paths
   that still inflate self-hosted frontend/codegen constants;
4. keep the self-backend fanout and batched IR-pass paths, but do not expect
   them to overcome the
  single giant Layer1 module by itself.

### IR pass verdict

`PCC_PYTHON_IR_PASSES` is not meaningless. It still exists to improve generated
program IR and can help runtime speed or code size for programs that benefit
from mem2reg/SROA/DCE-style cleanup. The self-bootstrap evidence says only that
running the current Python-host text pass pipeline by default is too expensive
for the bootstrap edit/test loop:

- default pass pipeline, emit-only: `25.40s`, `21.0MB` IR
- pass pipeline off, emit-only: `7.05s`, `21.4MB` IR
- default pass pipeline, full self stage before policy change: `36-38s`
- pass pipeline off / new self default: `18-19s`

The pass pipeline also did not measurably improve the pcc compiler binary on
the same compile workload. With the compiled process's own pass pipeline off:

- CPython frontend baseline: `11.88s`
- pcc2 built with default passes: `8.35s`
- pcc2 built with passes off: `8.12s`
- binary size: `5.26MB` with passes vs `5.40MB` without passes

So the current policy is: keep the pass pipeline as an explicit optimization
tool, but do not pay its text-processing cost by default on self-backend
bootstrap builds. The real long-term fix is an in-memory LLVM/pass-manager path
or a parsed IR representation that avoids repeated text parse/write cycles.

## Remaining work

The stage2 timeout is fixed and the default self-backend bootstrap loop is under
20 seconds per stage on this host. The next high-ROI items are now about making
optimized/release builds cheaper and improving generated-program runtime:

1. replace the host-text Python IR pass pipeline with an in-memory LLVM-C pass
   manager or a single parsed module pipeline;
2. add pass telemetry/ratchets so a pass that is slow and output-neutral on a
   module is not part of default presets;
3. share cold attribute-error/post-call-error IR blocks in `layer1` codegen;
4. split or shard Layer1 only if that creates independent IR modules or
   otherwise reduces the single 12MB module bottleneck;
5. make dynamic dict string lookup avoid generic `py_obj_eq` on hash matches;
6. reduce `DynType` attr paths by pushing more imported dataclass/class metadata
   through type inference;
7. consider a borrowed-reference list/tuple iteration ABI only after local
   ownership semantics are modeled, because Python loop targets live after the
   loop.

Splitting the 28k-line `L1CodeGen` class is now relevant, but only if it changes
runtime work: smaller independent IR modules, bounded free-name analysis scope,
or less repeated cold IR. A cosmetic mixin split that leaves the same 11.9MB
Layer1 IR module and the same nested-analysis recomputation will not get the
stage under 20s.
