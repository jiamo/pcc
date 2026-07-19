# Investigation: Bootstrap fixes and PASS layer design follow-up

## Status

Current focused correctness status is improved but not finished.  The 65s cold
`PCC_PYTHON_IR_PASSES=all` / memory / LLVM `default<O2>` path was reduced to the
low-teens class, and pcc1 -> pcc2 / pcc2 -> pcc3 now complete again with pass-all
memory O2.  The 10s target is still not met for cold O2:

```text
pass-all memory O2 cold pcc0 -> pcc1: 12.33s latest repeat
pass-all memory O2 cold pcc1 -> pcc2: 13.67s latest repeat
pass-all memory O2 cold pcc2 -> pcc3: 13.13s latest repeat
pass-all memory O2 hot cache repeat:   9.49s
passes off pcc0 -> pcc1:               9.40s
passes off pcc1 -> pcc2:              10.47s
```

The current content-addressed memory-pass cache is file-backed and keyed per
post-split module/shard by input IR, canonical pass pipeline, target triple,
LLVM identity, and cache version.  This is better than whole-layer1 caching:
a small source change does not necessarily invalidate every O2 shard.  It is
still not a solution for cold compiles, and broad helper/runtime changes can
still perturb many shards.  `PCC_PYTHON_IR_PASS_CACHE=off` bypasses it for
debugging.

The important pcc0/pcc1 oracle result is that raw LLVM IR is not currently
byte-identical even with the same source and `PCC_PYTHON_IR_PASSES=off`.
The latest structural comparison is:

```text
pcc0 IR bytes: 22,533,446
pcc1 IR bytes: 20,155,064
functions:     1,446 vs 1,446
globals:       5,055 vs 5,055
missing funcs: 0
extra funcs:   0
changed instruction counts: 722
changed call sequences:      5
```

The five call-sequence differences are concentrated in the Layer1 typed-int /
low-IR helpers:

```text
L1CodeGen._emit_int_literal_object
L1CodeGen._int_expr_needs_exact_object_boundary
L1CodeGen._maybe_emit_exact_int_object
_int_literal_fits_i64
_low_ir_expr_to_value
```

Same source should eventually imply equivalent compiler output, but that only
holds once the compiled pcc runtime is behaviorally equivalent to the CPython
host for all compiler-internal operations and deterministic naming decisions.
Today that is not yet true.  The function/global set equality is encouraging;
the raw IR delta is an oracle gap that needs a dedicated regression gate.

The optimization target is not "more pass infrastructure".  The target is that
compiled PCC must be faster than Python, and preferably by several times, for
real user programs.  Bootstrap time is the first large user workload: each
self-host stage must get to the 10s class, starting with direct measurements of
`pcc0 -> pcc1`, `pcc1 -> pcc2`, and `pcc2 -> pcc3`.  Any `TEXT`/`MEMORY` or
target-pass work only counts if it moves those gates or improves compiled
program runtime.

The important point for `dict.pop(key)` is that native support is not hard; it
just cannot be only an IR-shape change.  The no-default form needs native
`KeyError` creation and propagation, and `except ... as e` must keep the
exception object alive after clearing the TLS exception slot.

The first `memory IR` pass slice now exists for the Python frontend pass pipeline
through LLVM-C `LLVMRunPasses`, and the self backend has both text and
`PreparedModule` target-pass hooks.  That is still infrastructure; it does not by
itself fix frontend lowering choices that already emitted boxed-object runtime
calls.

Two pcc1-only failures in this round had the same root shape: pcc1 was asked to
run large Python string/dict sharding code in its own runtime before delegating
to the host tool boundary.

First, pcc1 -> pcc2 with self-backend large-module split enabled failed after
about 41s with `signal: Invalid argument`.  Disabling only
`PCC_SELF_BACKEND_SPLIT_LARGE_MODULES` made the same pcc1 -> pcc2 compile pass
in 14.92s.  The fix moved self-backend large-module split into the host Python
object-emission subprocess.  pcc1 now writes unsplit module IR; host CPython
normalizes/splits it, emits objects in parallel, writes a result TSV, and the
compiled driver only reads object paths.  This keeps the existing bootstrap rule
that backend implementation imports stay behind a host-tool subprocess boundary.

Second, an O2-built pcc1 could compile pcc2 with `PCC_PYTHON_IR_PASSES=off`
in 10.58s, but failed in about 40s when pass-all memory O2 was enabled.  Turning
off only `PCC_PYTHON_IR_PASS_SPLIT_LARGE_MODULES` made the pass-all
`--emit-llvm` path complete, but it took 69.33s.  The fix moved Python IR pass
large-module sharding into the host IR-pass subprocess.  pcc/pcc1 now writes the
original module IR and a split flag; host CPython splits the large modules,
runs LLVM-C memory passes over the shards in parallel, and writes a result TSV
with the output shard paths.

Both fixes are correctness and architecture fixes, not just performance hacks:
compiled pcc should not spend its critical path doing huge ad hoc Python text
rewrites that host subprocesses already need to own for LLVM/pass/backend work.

The latest typed-loop measurement located the immediate runtime blocker:
`bench(n: int)` was generated as `define ptr @user_typed_loop_bench(ptr %n)`,
and the hot loop still called `py_int_mod`, `py_int_floordiv`, and `py_int_cmp`
on every iteration.  Only part of `+` used the tagged-int fast path.  In other
words, ordinary user `int` annotations still took the Python object ABI instead
of an unboxed integer ABI.  A pass cannot fully rescue that shape; passes can
delete redundancy, but the core fix is typed-int unboxed lowering before IR is
handed to the optimizer/backend.

The first typed-int ABI slice now emits the same benchmark as an unboxed
`i64 -> i64` function and the hot loop uses native `srem` / `sdiv` instead of
`py_int_mod` / `py_int_floordiv`.  The pcc1 user-runtime gate now measures the
compiled typed loop at about `0.060s` vs CPython at about `0.40s`, roughly
6.6x faster.  This is the right kind of win: a frontend lowering fix, not a
pass hiding boxed-object IR after the fact.

## Fixes in this round

### `dict.pop(key)` native semantics

The native no-default path now lowers to `py_dict_pop(dict, key)` and performs
a post-call error check.  On a missing key, the runtime creates a `KeyError`
with the missing key as the exception value and raises it through the existing
return-code exception model.

The regression test now checks both properties:

- generated IR contains `@py_dict_pop` and `@py_err_occurred`;
- a no-libpython binary catches the missing-key `KeyError` and prints
  `KeyError`.

### `float(int)` native lowering

`float(int)` now uses the existing `_to_double` helper instead of applying
`sitofp` directly to the value.  That avoids illegal IR when the argument is a
boxed Python int pointer rather than an unboxed integer.

### `except ... as e` lifetime

While validating `dict.pop(key)`, generated IR showed this shape:

```llvm
%try.cur_exc = call ptr @py_current_exception()
call void @py_clear_exception()
%type.name = call ptr @py_obj_type_name(ptr %try.cur_exc)
```

That was a use-after-free: `py_current_exception()` returns a borrowed
reference, and `py_clear_exception()` decrements the TLS-owned reference.

The first broad fix retained every matched handler exception, but that was too
wide.  Compiled bootstrap helpers then hit `[BAD_INCREF]` on unrelated internal
exception paths because handler slots were registered in function-level owned
local cleanup, which is not path-sensitive.

The final fix is narrower:

- retain the handler exception only when the handler binds `except ... as name`
  or contains a bare `raise`;
- bind the retained exception to the handler local;
- release it on normal handler exit;
- do not register the handler variable in the function-level owned-local set.

This preserves `type(e).__name__`, `str(e)`, and bare reraises without cleaning
an uninitialized handler slot on the normal try path.

### Static `type(x).__name__` folding

`ClassType("object")` in the frontend means "only known to be an object", not
"runtime type is exactly object".  `_static_runtime_type_name()` now refuses to
fold that case so dynamic objects use `py_obj_type_name()` at runtime.

### Bootstrap performance fixes

The bootstrap benchmark now warms up user-runtime commands before measuring
them.  Without this, macOS first-launch validation/loader cost from a freshly
linked Mach-O binary polluted the compiled-program vs CPython runtime ratio.
The gate now validates stdout on every run, discards one warmup run, and uses
the best measured run for the timing ratio.

The native Python lexer had two avoidable hot-path costs:

- `_slice()` read `os.environ["PCC_DEBUG_BOOTSTRAP"]` on every slice;
- `_read_op()` matched multi-character operators by repeatedly slicing for
  every candidate operator.

The first was changed to cache the debug flag on the `Lexer` instance and reuse
`self._src_len`.  The second was changed to fixed character-code branches.
This is intentionally bootstrap-friendly: no dynamic dict/set dispatch in the
operator hot path.

Self-backend text parsing was also doing too much failed regex work before it
recognized common instructions.  In the layer1 module, `call` is the largest
instruction class and `getelementptr` is also common.  Both now use early
string-gated parser branches before the rare instruction regexes.

`_hoist_nested_funcdefs()` now precomputes module-scope names once per hoist
pass and caches yield-sentinel scans by `id(fd)`.  One attempted lazy
`nonlocal module_scope_names_base` cache was rejected by pcc1 as an unbound
name during stage2, so the committed version avoids `nonlocal` in the
self-host path.

The current remaining dominant costs are structural:

- layer1 emits about `13.7MB` of LLVM IR and about `25.8MB` of self-backend
  assembly;
- self-backend still reparses textual LLVM IR into `ParsedModule` before
  target emission;
- the huge `pcc.py_frontend.codegen.layer1` module dominates the critical path,
  so parallel object emission helps smaller modules but cannot fully hide
  layer1.

An experiment with `PCC_PYTHON_IR_PASSES=quick` reduced output size slightly
but made stage1 much slower (`~32.8s`), so the current Python text pass pipeline
is not a bootstrap-time win.  The useful pass direction remains memory IR /
target IR with fewer text boundaries, not enabling the existing text pass
pipeline by default.

## Verification

All commands were run with `env -u LC_ALL` and hard timeouts.

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_native_dict_pop.py -q -n0
# 7 passed in 2.12s

/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  'tests/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[raise_from_cause]' \
  'tests/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[exceptions_try_except]' \
  tests/test_py_exceptions.py tests/py_corpus/phase3 -q -n0
# 6 passed in 2.83s

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest \
  tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py \
  -q -n0
# 67 passed in 139.49s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_runtime_oracle_diff.py -q -n0
# 21 passed, 6 skipped in 26.48s

/opt/homebrew/bin/timeout 360s env -u LC_ALL uv run pytest \
  tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py \
  tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py \
  tests/test_compile_cache.py -q -n0
# 46 passed in 71.98s
```

The user-reported failure group was rerun after the exception-lifetime fix:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest ... -q -n0
# 26 passed in 90.74s
```

After adding the target-specific PASS hook:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_self_backend.py \
  tests/test_self_backend_target_passes.py \
  tests/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_python_self_backend_smoke_runs_minimal_program \
  -q -n0
# 249 passed in 27.64s
```

After adding the LLVM-C memory pass hook and LLVM CodeGen sample importer:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_llvm_capi_pass_pipeline.py \
  tests/test_llvm_text_pipeline.py \
  tests/test_llvm_codegen_reference_import.py \
  -q -n0
# passed as part of the compile-cache gate below
```

The compile-cache gate was rerun after adding the pass transport to the
optimization signature:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_llvm_capi_pass_pipeline.py \
  tests/test_llvm_text_pipeline.py \
  tests/test_llvm_codegen_reference_import.py \
  tests/test_compile_cache.py \
  -q -n0
# 27 passed in 1.37s
```

Existing LLVM-C/self-backend gates were rerun:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_llvm_capi_ir_parity.py \
  tests/test_llvm_capi_end_to_end.py \
  tests/test_self_backend_target_passes.py \
  tests/test_self_backend.py \
  -q -n0
# 271 passed in 25.79s
```

The bootstrap narrow gate still passes:

```bash
/opt/homebrew/bin/timeout 360s env -u LC_ALL uv run pytest \
  tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py \
  -q -n0
# 67 passed in 131.35s
```

The bootstrap gate script now emits per-stage timings from
`scripts/bootstrap.sh`:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=... output=...
PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=... output=...
PCC_BOOTSTRAP_STAGE_RESULT stage=3 elapsed_ms=... output=...
```

`scripts/run_self_backend_bootstrap_gate.py` parses those markers and enforces
`--max-stage-elapsed`, defaulting to `10.0s`.  That is the live guardrail for
the current optimization work; `--max-stage-elapsed=0` disables it for a
diagnostic run, but not for acceptance.

```bash
/opt/homebrew/bin/timeout 120s env -u LC_ALL uv run pytest \
  tests/test_self_backend_bootstrap_gate.py \
  tests/test_roadmap_gate_script.py \
  tests/test_bootstrap_observable_script.py \
  -q -n0
# 11 passed in 0.28s
```

After the typed-int lowering and bootstrap performance fixes:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_py_typed_int_unboxed.py \
  tests/test_self_backend_bootstrap_gate.py \
  tests/test_py_lex_performance_guards.py \
  -q -n0
# 18 passed in 1.90s

/opt/homebrew/bin/timeout 360s env -u LC_ALL uv run pytest \
  tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py \
  tests/test_py_typed_int_unboxed.py \
  -q -n0
# 71 passed in 126.78s

/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_self_backend.py tests/test_self_backend_target_passes.py \
  -q -n0
# 248 passed in 26.56s
```

Bootstrap measurements with the absolute 10s check disabled for diagnosis:

```bash
/opt/homebrew/bin/timeout 1800s env -u LC_ALL uv run python \
  scripts/run_self_backend_bootstrap_gate.py \
  --backend self --stage 2 --timeout 1200 --max-stage-elapsed=0
# stage_elapsed=1:17.296s,2:17.010s
# pcc1_vs_pcc0_compile_ratio=0.697
# user_runtime_vs_python_ratio=0.153

/opt/homebrew/bin/timeout 2400s env -u LC_ALL uv run python \
  scripts/run_self_backend_bootstrap_gate.py \
  --backend self --stage 3 --timeout 1800 --max-stage-elapsed=0
# exit=0
# stage_elapsed=1:15.610s,2:16.892s,3:17.106s
# pcc1_vs_pcc0_compile_ratio=0.666
# user_runtime_vs_python_ratio=0.150
# libpython=False
```

The default `--max-stage-elapsed=10.0` acceptance gate is still expected to
fail.  Current completion is a correctness milestone, not the end of the
performance work.

## LLVM reference check

The old in-repo comments mention
`/tmp/llvm-src/llvm-20.1.8.src/include/...`.  On this machine,
`/tmp/llvm-src/llvm-20.1.8.src` exists, but currently only contains:

```text
/tmp/llvm-src/llvm-20.1.8.src/test/CodeGen/Mips
```

So `/tmp` is useful only for a small target CodeGen test sample, not for the
full LLVM implementation.

For AArch64/X86 target-specific pass design, the useful source snapshot was
originally downloaded separately under `/tmp`. That historical `/tmp` path is
no longer durable on this machine; do not use it as the active reference path.

Current durable reference:

```text
~/pcc_refs/llvm-project-20.1.8-full-depth1
tag: llvmorg-20.1.8
commit: 87f0227cb60147a26a1eeb4fb06e3b505e9c7261
checkout mode: full depth-1 clone
```

Historical path recorded by the original investigation:

```text
/tmp/llvm-project-20.1.8-targets
tag: llvmorg-20.1.8
commit: 87f0227cb60147a26a1eeb4fb06e3b505e9c7261
```

Historical sparse checkout contents:

```text
llvm/lib/Target/AArch64
llvm/lib/Target/X86
llvm/include/llvm/CodeGen
llvm/include/llvm/Target
llvm/include/llvm/Passes
llvm/include/llvm/IR/PassManager.h
llvm/test/CodeGen/AArch64
llvm/test/CodeGen/X86
```

Snapshot size and coverage:

- AArch64 target top-level files: 118
- X86 target top-level files: 150
- AArch64 CodeGen tests: 3531 files
- X86 CodeGen tests: 5129 files

Key files for reference:

- `/tmp/llvm-project-20.1.8-targets/llvm/lib/Target/AArch64/AArch64TargetMachine.cpp`
  - legacy `AArch64PassConfig`;
  - overrides `addIRPasses()`, `addPreISel()`, `addInstSelector()`,
    `addPreRegAlloc()`, `addPostRegAlloc()`, `addPreEmitPass()`, and
    `addPreEmitPass2()`.
- `/tmp/llvm-project-20.1.8-targets/llvm/lib/Target/AArch64/*Pass.cpp`
  - target-specific IR/Machine pass implementations.
- `/tmp/llvm-project-20.1.8-targets/llvm/lib/Target/X86/X86TargetMachine.cpp`
  - legacy `X86PassConfig` with the same major extension points.
- `/tmp/llvm-project-20.1.8-targets/llvm/lib/Target/X86/X86CodeGenPassBuilder.cpp`
  - newer target codegen pass builder path for X86.
- `/tmp/llvm-project-20.1.8-targets/llvm/lib/Target/X86/X86PassRegistry.def`
  - target-specific pass registry entries.
- `/tmp/llvm-project-20.1.8-targets/llvm/test/CodeGen/AArch64`
  and `/tmp/llvm-project-20.1.8-targets/llvm/test/CodeGen/X86`
  - target CodeGen behavior oracles.

The usable local LLVM reference is the Homebrew LLVM 20.1.8 install:

```text
/opt/homebrew/Cellar/llvm@20/20.1.8/include/llvm/IR/PassManager.h
/opt/homebrew/Cellar/llvm@20/20.1.8/include/llvm/Passes/CodeGenPassBuilder.h
/opt/homebrew/Cellar/llvm@20/20.1.8/include/llvm/CodeGen/TargetPassConfig.h
/opt/homebrew/Cellar/llvm@20/20.1.8/include/llvm/Target/TargetMachine.h
/opt/homebrew/Cellar/llvm@20/20.1.8/include/llvm/ADT/FloatingPointMode.h
```

The relevant LLVM shape:

- target-independent IR passes run over Module/Function/Loop IR units;
- CodeGen has separate `AddIRPass` and `AddMachinePass` paths;
- `TargetPassConfig` owns target/codegen sequencing such as `addIRPasses()`,
  `addISelPasses()`, and `addMachinePasses()`;
- `TargetMachine` is the target-specific owner for triple, CPU, features,
  data layout, subtarget info, and object/asm emission.
- `llvm.is.fpclass` masks are defined by `FPClassTest` in
  `FloatingPointMode.h`; PCC's self-backend lowering must follow those bit
  meanings instead of accepting only the masks seen in existing tests.

The important design lesson is that AArch64 and X86 are not organized
identically.  AArch64 is still centered on the legacy `AArch64PassConfig`
inside `AArch64TargetMachine.cpp`; X86 has both legacy `X86PassConfig` and a
newer `X86CodeGenPassBuilder.cpp` path.  PCC should not copy every LLVM target
pass.  It should copy the separation points: target-independent IR passes,
target pre-isel passes, instruction selection, pre/post register allocation,
pre-emit passes, and target asm/object emission.

## PCC PASS design

The design should not make "PASS" mean "one big text rewrite layer".

PCC should use one user-facing PASS umbrella with separate internal stages:

1. target-independent IR passes
   - current implementation: Python-host text IR pipeline in
     `pcc/py_frontend/ir_pass_pipeline.py` and `pcc/ir_passes`;
   - current transport: `TEXT`;
   - future transport: `MEMORY`, backed by an owned LLVM-C module handle or a
     stable in-process parsed IR.

2. target/codegen passes
   - LLVM backend: target-specific work is currently inside
     `TargetMachine.emit_object()` / LLVM CodeGen;
   - self backend: now has a default-off target-pass hook after asm emission;
   - future self-backend target IR should run over `ParsedModule` /
     `PreparedModule` before asm text is produced.
   - LLVM reference: AArch64/X86 split target-independent `addIRPasses()` from
     target/codegen hooks such as `addPreISel()`, `addInstSelector()`,
     `addPreRegAlloc()`, `addPostRegAlloc()`, and `addPreEmitPass()`.

3. object/link steps
   - not IR optimization passes;
   - still need telemetry because bootstrap time often shows up as
     subprocess wait/link time.

## Implemented PASS hook

Added:

- `pcc/backend/self_backend_target_passes.py`
- `tests/test_self_backend_target_passes.py`
- `pcc/llvm_capi/binding.py::run_passes()` /
  `run_passes_on_ir()`, backed by LLVM-C `LLVMRunPasses`
- `tests/test_llvm_capi_pass_pipeline.py`
- `tests/test_llvm_codegen_reference_import.py`

New controls:

- `PCC_SELF_TARGET_PASSES`
  - empty/default/off: no target-specific self-backend passes;
  - `strip-trailing-whitespace`: runs a harmless asm text canonicalization pass;
  - `all`: selects all registered passes for the selected target-pass transport.
- `PCC_SELF_TARGET_PASS_TRANSPORT`
  - `text`: current supported transport;
  - `memory`: runs `PreparedModule` passes before asm text is produced.  The
    first registered pass is `verify-prepared-module`.
- `PCC_PYTHON_IR_PASS_TRANSPORT`
  - `text`: existing translated Python IR pass implementation;
  - `memory`: Python frontend pass execution through LLVM-C `LLVMRunPasses`.
- `PCC_PYTHON_IR_PASSES=all` with memory transport
  - maps to LLVM PassBuilder's `default<O2>` profile;
  - this is intentionally not "enumerate every registered pass name", because
    LLVM pass names often require pass-manager nesting such as
    `function(loop-mssa(licm))`.
- `PCC_PYTHON_IR_PASS_CACHE`
  - default/on: cache memory-pass output by input IR hash, canonical pipeline,
    target triple, LLVM version, and cache version;
  - `off`/`false`/`0`: bypass the cache.
- `PCC_PYTHON_IR_PASS_CACHE_DIR`
  - overrides the default `${XDG_CACHE_HOME:-~/.cache}/pcc/python-ir-pass-cache`.
- `PCC_LLVM_PIPELINE_TRANSPORT`
  - `text`: existing external `opt -passes=...` transport;
  - `memory`: runs the same LLVM pass syntax in-process through
    `LLVMRunPasses`, serializing only at the boundary.

The compile-cache optimization signature now includes `PCC_LLVM_PIPELINE_TRANSPORT`.
Without that, `text` and `memory` could reuse the same object cache entry for
the same pass spec.

`emit_self_asm()` now resolves the target emitter, emits asm, then runs the
target-pass pipeline.  With default environment it is a no-op and existing asm
tests remain byte-for-byte stable.

The LLVM-C memory pass hook is separate from the self-backend target pass hook.
It runs target-independent LLVM IR passes over an owned `LLVMModuleRef`, so it
is the first real `TEXT` vs `MEMORY` switch for the LLVM IR pass layer.

This is still infrastructure, not a performance win by itself.  The next
bootstrap measurement must show whether avoiding the external text pipeline
reduces the stage timings.  If it does not, the correct next work is IR-size
reduction and self-backend parse/lower speed, not more pass plumbing.

The self-backend hook is intentionally small, but it creates the place where
target-specific self-backend passes belong.  It mirrors LLVM's separation
between IR pass construction and machine/codegen pass construction without
pretending that PCC already has a memory target IR.

## Converted LLVM tests

The downloaded LLVM tree contains many tests that are not directly PCC tests:

- `.mir` tests need LLVM Machine IR support, which PCC does not have;
- many `.ll` files depend on `llc` flags, target features, or FileCheck-only
  assertion patterns;
- target-specific intrinsics can be parsed only when the target and intrinsic
  declarations are available.

The first automated conversion test therefore handles the safe subset:

1. take an LLVM CodeGen `.ll` file;
2. drop lit/FileCheck comments;
3. keep a small prefix of real LLVM IR function definitions;
4. add/keep the target triple;
5. parse and verify through `pcc.llvm_capi.binding`;
6. emit target assembly through LLVM-C target machines.

Current converted samples:

- `CodeGen/AArch64/arm64-csel.ll`
- `CodeGen/X86/convert-2-addr-3-addr-inc64.ll`

This gives a real comparison path for target IR acceptance and target assembly
emission without pretending the whole LLVM test suite is already portable into
PCC.  The next useful expansion is a manifest of convertible `.ll` files with
expected target, function-count slice, required features, and whether the test
is parse-only, pass-equivalence, or asm-emission.

## MEMORY IR requirements

`TEXT` and `MEMORY` must be equivalent before MEMORY becomes the default.

Required design constraints:

- one owner for `LLVMContextRef`, `LLVMModuleRef`, pass manager, and target
  machine lifetime;
- no reintroduced `llvmlite` or libpython dependency in compiled pcc1/pcc2
  self-host paths;
- deterministic module serialization for debugging and cache fingerprints;
- `TEXT` fallback remains available for bisecting and for unsupported memory
  pass combinations;
- tests compare normalized IR for emit-only paths and compile/run behavior for
  executable paths;
- cache keys must include pass transport, pass list, target triple, and backend
  identity.

The first MEMORY implementation should be narrow:

1. parse generated IR text once into an LLVM-C module handle;
2. run a tiny target-independent pass preset in memory;
3. serialize only at explicit `--emit-llvm` or debug boundaries;
4. emit object directly from the same module handle.

That is the path that can actually beat the current repeated
text-parse/text-write model.

The first slice of this now exists for pass execution:

- `PCC_LLVM_PIPELINE_TRANSPORT=text` uses the existing external `opt` text
  pipeline;
- `PCC_LLVM_PIPELINE_TRANSPORT=memory` uses LLVM-C `LLVMRunPasses`;
- tests compare normalized IR from both transports on the same pass list;
- tests verify that memory transport actually performs `mem2reg`/`instcombine`
  in memory.
- tests verify that memory transport accepts `default<O2>` through
  `PCC_LLVM_PIPELINE=default`, not only hand-written pass lists.
- cache keys distinguish `text` and `memory` transport.

## Low IR first slice

A minimal `pcc_low_ir` data core now exists for the typed-int hot path:

- function/block/local/value representation;
- typed values for `i1`, `i64`, `ptr`, `void`;
- branch, conditional branch, return;
- direct calls and runtime calls with a `may_raise` flag;
- `PCC_PYTHON_LOW_IR=off` keeps the old Layer1 emitter as an oracle.

The implementation was deliberately constrained after the first baseline run
caught a real closure regression.  A standalone `pcc/py_frontend/low_ir.py`
with dataclasses and top-level `py_ast` imports added a new module with
`py_cpy_*` fallbacks.  The fixed shape keeps `low_ir.py` as a bootstrap-safe
data layer and leaves the current AST-to-LowIR / LowIR-to-LLVM bridge in
Layer1, which already has a fallback budget.  The gate now confirms
`pcc/py_frontend/low_ir.py` independently compiles with zero `py_cpy_*` calls.

Coverage added:

- low-ir block construction for typed `while`;
- direct typed-int call node shape;
- `may_raise` runtime-call hook;
- typed-int loop LLVM shape uses `low.while.*` blocks by default;
- `PCC_PYTHON_LOW_IR=off` still uses the old Layer1 typed-int scalar path;
- no-libpython self-backend execution for the loop and direct-call cases.

Measured results on this host:

| Metric | low-ir on | low-ir off |
|---|---:|---:|
| bootstrap LLVM IR bytes for `pcc/__main__.py` | 21,899,211 | 21,899,506 |
| self-backend IR->asm emit | 11.095s | 11.045s |
| asm bytes | 38,936,571 | 38,937,188 |
| stage1 elapsed | 16.006s | 16.887s |
| typed user runtime geomean | 0.023s | 0.061s |
| compiled/Python typed runtime ratio | 0.059 | 0.153 |

Stage2 with low-ir enabled:

- `stage_elapsed=1:23.655s,2:17.850s`;
- `pcc1_vs_pcc0_compile_ratio=0.718`;
- `user_runtime_vs_python_ratio=0.058`;
- `libpython=False`.

Interpretation: the low-ir slice improves user typed-int runtime materially,
but it does not solve the 10s bootstrap target.  PCC's own bootstrap sources
currently do not contain enough typed-int loops/direct calls for this slice to
reduce the dominant Layer1 IR module.  The remaining bootstrap time is still
the large generated LLVM text plus self-backend parse/emit/link path.

## Shared post-call frame block attempt

The next small IR-size attempt was to share post-call traceback frame blocks
inside a function by `(function, err target, source function, file, line)`.
This preserves the `py_err_occurred()` check at every call site, but lets
multiple raising call sites branch to the same
`py_current_exception()` + `py_exc_append_frame()` block when they report the
same frame.

Result:

| Metric | before | after |
|---|---:|---:|
| bootstrap LLVM IR bytes | 21,899,211 | 21,884,425 |
| `py_exc_append_frame` text occurrences | 6,765 | 6,699 |
| `err.frame` text occurrences | 9,019 | 8,784 |
| self-backend IR->asm emit | 11.095s | 12.187s |
| asm bytes | 38,936,571 | 38,907,950 |
| stage1 elapsed | 16.006s | 16.200s |

Interpretation: correct and slightly smaller, but far too small to move the
10s target.  The dominant cost is not just duplicated frame blocks; it is the
large Layer1 module and the cost of parsing/emitting it through the text
self-backend pipeline.

## Self-backend text parse fast path

The next bottleneck was not LLVM optimization.  Profiling the generated
bootstrap IR showed self-backend parsing spending most of its time repeatedly
decoding the same simple LLVM text shapes:

- `%ssa` and `@global` value tokens first went through parenthesized typed
  value / constant-cast / constant-expression decoders;
- simple call arguments still used the general leading-type scanner;
- call signatures were re-parsed for repeated runtime helper shapes;
- simple GEP indices used regex even when the token was just `i64 0`.

The parser now has fast paths for those common shapes:

- cached parsed `TypeDesc` values;
- cached call signatures;
- no-nesting `split_top_level` / call-arg / GEP-index fast paths;
- early simple value decoding for `%`, `@`, integer, float, `null`,
  `undef`, `poison`, `true`, `false`, and `zeroinitializer`.

Measured on the same 21MB bootstrap IR:

| Metric | before parser fast paths | after type/split cache | after value/call/GEP fast paths |
|---|---:|---:|---:|
| self-backend IR->asm emit | 12.187s | 7.304s | 5.720s |
| asm bytes | 38,907,950 | 38,907,950 | 38,907,950 |

Focused validation:

- `tests/test_self_backend.py`
- `tests/test_self_backend_target_passes.py`
- `tests/test_self_backend_profile.py`
- `tests/test_self_backend_bootstrap_gate.py`

Result: `260 passed`.

## Emit-LLVM pass default trap

One measurement discrepancy turned out to be a real CLI default bug:

- native self-backend compile defaults `PCC_PYTHON_IR_PASSES` to `off`;
- `--emit-llvm --backend self` did not carry that backend-specific default;
- therefore emit-only measurements were accidentally running the Python IR
  pass pipeline.

This made a frontend-only probe look much slower than the actual native
self-backend stage:

| Command shape | Time |
|---|---:|
| `--emit-llvm --backend self` before the fix | 24.63s |
| `PCC_PYTHON_IR_PASSES=off --emit-llvm --backend self` | 5.55s |
| `--emit-llvm --backend self` after the fix | 5.51s |

The fix is to derive the default pass policy from the requested backend even
when `emit_llvm_only=True`.  Explicit `PCC_PYTHON_IR_PASSES=...` still wins.
Coverage now includes emit-only self-backend default behavior.

## Layer1 object sharding

After parser fast paths, the backend-only split was:

| Phase | Time |
|---|---:|
| frontend IR generation, passes off | 5.51s |
| self-backend object emit + final link | 7.96s |
| final native link only | 0.05s |

Per-module timing showed why the total still missed 10s:

| Module | Total | self emit | `cc -c` | IR bytes | asm bytes |
|---|---:|---:|---:|---:|---:|
| `pcc.py_frontend.codegen.layer1` | 7.359s | 3.961s | 3.397s | 14,241,131 | 26,832,633 |
| `pcc.py_frontend.type_infer` | 0.704s | 0.325s | 0.379s | 1,218,661 | 2,282,476 |
| `pcc.parse.py_parse` | 0.696s | 0.349s | 0.347s | 1,324,749 | 2,469,495 |
| `pcc.py_frontend.pipeline` | 0.576s | 0.296s | 0.280s | 1,209,040 | 2,067,231 |

`layer1.py` is now 31,087 lines, and its generated IR is a single dominant
object-emission unit.  Splitting the Python class cosmetically would not have
been enough; the compile wall time needed an actual parallel object boundary.

The implemented self-backend linker now shards oversized LLVM IR modules
inside the host object-emission subprocess:

- default enabled, disable with `PCC_SELF_BACKEND_SPLIT_LARGE_MODULES=off`;
- default split threshold: `PCC_SELF_BACKEND_SPLIT_THRESHOLD_BYTES=4000000`;
- default function shard target: `PCC_SELF_BACKEND_SPLIT_SHARD_BYTES=2000000`;
- globals are emitted in one shard;
- function bodies are split into function shards;
- `internal` / `private` globals in a split module are promoted to one real
  external definition so all function shards reference the same storage.

This preserves the existing text IR contract but removes the single large
`layer1` object-emission bottleneck.  It is not a substitute for a future
in-memory `ParsedModule` pipeline; it is a direct parallelization of the
current bottleneck.  The first implementation did the split in the pcc/pcc1
driver process; pcc1 later failed in that path.  The current implementation
keeps pcc1 out of the large string-rewrite step and lets host CPython split
and emit object shards.

Measured result:

| Metric | Before sharding | After sharding |
|---|---:|---:|
| backend-only object emit + link | 7.96s | 2.23s |
| direct full compile of `pcc/__main__.py` | 13.61s | 8.11s |
| bootstrap stage1 gate | 13.57s | 7.689s |
| bootstrap stage2 gate | 9.275s before stage3 rerun | 9.395s in full stage3 run |
| bootstrap stage3 gate | not previously below target | 9.396s |

Full stage3 gate now passes with:

```text
stage_elapsed=1:7.747s,2:9.395s,3:9.396s libpython=False
```

These numbers are for the default self-backend policy with Python IR passes
off.  They do not imply pass-all LLVM O2 meets the 10s target.

Focused validation after sharding:

- `tests/test_self_backend.py tests/test_self_backend_target_passes.py tests/test_self_backend_profile.py tests/test_self_backend_bootstrap_gate.py`: `260 passed`
- `tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py tests/test_py_frontend_ir_pass_pipeline.py tests/test_py_low_ir.py tests/test_py_typed_int_unboxed.py`: `77 passed`
- `tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py`: `69 passed`
- `scripts/run_self_backend_bootstrap_gate.py --backend self --stage 3`: pass

## PASS-all memory O2 update

`PCC_PYTHON_IR_PASS_TRANSPORT=memory` now uses LLVM-C `LLVMRunPasses` directly
for Python frontend IR passes.  The semantic reference is LLVM's own PassBuilder
pipeline syntax:

- `PCC_PYTHON_IR_PASSES=all` expands to `default<O2>`;
- `default<Os>` / `default<Oz>` keep LLVM's exact spelling;
- `instcombine` and `licm` use the LLVM pass-manager spellings required by LLVM
  itself (`instcombine<no-verify-fixpoint>` and `function(loop-mssa(licm))`);
- tests compare normalized output from memory transport against
  `/opt/homebrew/opt/llvm@20/bin/opt -passes=default<O2>`.

The first full `default<O2>` run exposed two self-backend correctness holes in
LLVM-legal IR:

- call arguments can contain attributes before constant expressions, for example
  `ptr nonnull inttoptr (i64 1 to ptr)`;
- LLVM O2 can generate `llvm.is.fpclass.*` masks such as `100`, which means
  `fcNegInf | fcNegZero | fcPosZero` according to LLVM's `FPClassTest` enum.

Both are now covered by focused self-backend tests.

Initial performance result with pass-all memory transport before host-side
Python-pass sharding:

| Measurement | Time |
|---|---:|
| `pcc0 -> pcc1`, cold pass cache | 65.13s |
| `pcc0 -> pcc1`, hot identical cache | 6.72s |
| `pcc1 -> pcc2`, same cache dir | 64.35s |
| `pcc2 -> pcc3`, same cache dir | 8.01s |
| pcc2 -> pcc3 `--emit-llvm`, all 17 modules cache-hit | 6.12s |

The pcc1 stage did not hit the pcc0 cache because pcc1 generated different IR
text.  The pcc2 stage did hit the pcc1 cache, which means the content key is not
over-broad and the self-host output stabilizes after one compiled stage.

Telemetry from the cold memory run:

| Module | LLVM `default<O2>` time | IR before | IR after |
|---|---:|---:|---:|
| `pcc.py_frontend.codegen.layer1` | 47.244s | 14,241,128 | 11,954,166 |
| `pcc.llvm_capi.ir` | 1.633s | 969,136 | 760,833 |
| `pcc.py_frontend.pipeline` | 1.137s | 1,252,847 | 977,602 |
| `pcc.parse.py_parse` | 1.129s | 1,324,746 | 1,206,748 |
| `pcc.py_frontend.type_infer` | 0.845s | 1,218,658 | 1,016,283 |

Interpretation:

- `default<O2>` memory mode is semantically grounded in LLVM and now works, but
  cold pass-all bootstrap was not a 10s path.
- The cache is useful for repeated tests and stable self-host stages, and it is
  explicitly disabled by `PCC_PYTHON_IR_PASS_CACHE=off`.
- The real cold fix is still structural: reduce Layer1 IR size, introduce real
  module/incremental boundaries, and move more of the hot frontend into smaller
  typed/unboxed lowering units before LLVM O2 sees a 14MB single module.

The next fix was to shard large modules before running LLVM-C memory passes.
The first version performed that sharding in the driver process and got cold
`pcc0 -> pcc1` down to about 12.5s, but O2-built pcc1 still failed when it had
to run the same sharding code during pcc1 -> pcc2.  Disabling only
`PCC_PYTHON_IR_PASS_SPLIT_LARGE_MODULES` made pcc1 pass-all `--emit-llvm`
complete in 69.33s, proving LLVM O2 itself was not the failing component.

The current fix moves Python IR pass sharding into the host IR-pass subprocess:

- pcc/pcc1 writes original module IR files;
- host CPython applies `_split_large_modules_for_python_ir_passes()`;
- host CPython runs LLVM-C memory passes over the resulting shards in parallel;
- host CPython writes a result TSV with output shard paths;
- pcc/pcc1 reads those paths and continues.

The default Python pass shard target is currently 1.4MB.  Measurements on this
machine were worse at 1.0MB, roughly similar at 1.25-1.6MB, and best in the
sampled set at 1.4MB.  This should remain a tunable heuristic, not a semantic
contract.

Current pass-all memory O2 measurements.  The pcc0 and pcc1 cold numbers have
run-to-run variance; the table uses the latest repeat after making 1.4MB the
default shard target.

| Measurement | Time |
|---|---:|
| `pcc0 -> pcc1`, cold empty cache | 12.33s |
| `pcc1 -> pcc2`, cold empty cache | 13.67s |
| `pcc2 -> pcc3`, cold empty cache | 13.13s |
| repeat with same shard cache | 9.49s |
| `pcc1 -> pcc2`, passes off | 10.47s |

So the 65s regression is fixed, pcc1 -> pcc2 is no longer failing, and hot
cache is back under 10s.  Cold O2 is still not accepted.  The remaining 2-3s
cannot be solved by more pass plumbing alone; it requires reducing O2 input
volume or changing the frontend/lowering shape so LLVM sees smaller and more
native IR.

Focused validation after this update:

- `tests/test_self_backend.py tests/test_py_frontend_ir_pass_pipeline.py tests/test_self_backend_target_passes.py`: `293 passed`
- `tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py`: `103 passed`

## Next steps

1. Keep the 10s gate as a real benchmark, not a cached result.  The current
   no-libpython full stage3 gate is under 10s per stage with the default pass
   policy, and pass-all memory is under 10s only once the content cache is hot.
   Cold `default<O2>` remains a separate failing performance target.
2. Reduce layer1 IR volume.  Sharding fixes wall time by parallelism, but
   `layer1` still emits ~14MB IR and ~27MB asm.  The next ROI targets are fewer
   repeated error paths, more unboxed helper shapes, and less boxed-object
   lowering in PCC's own hot helpers.
3. Move self-backend target work from repeated text parse toward a stable
   `ParsedModule` / target-memory ownership path.  Sharding reduces wall time,
   but total CPU is still high because every shard still parses LLVM text.
4. Split or restructure `layer1.py` only where it creates real module/cache
   boundaries.  The file is too large, but the performance criterion is whether
   the split reduces IR/object work or enables incremental reuse.
5. Add a generic pass-layer config object that reports selected stage,
   transport, target id, and pass list in one place.
6. Broaden LLVM-C memory target-independent pass support beyond the first
   `LLVMRunPasses` slice; keep `TEXT` as default until equivalence tests pass
   for default profiles and disabled-pass pruning.
7. Move self-backend target passes from asm-text-only to `PreparedModule` once
   the self backend has a stable parsed-module mutation contract.
8. Add benchmark gates for `TEXT` vs `MEMORY` on the bootstrap closure and a
   small runtime program, but keep the headline acceptance metric as
   stage time and compiled program runtime.
