# BUG-P1-API-VS-CLI-CODEGEN-DIVERGENCE — pinned by contract, original symptom no longer reproduces

Mode: host pcc, C frontend, LLVM backend.

## What was measured

The recorded repro was the strtod corpus (probe + vendored musl
`strtod`/`floatscan`/`shgetc`/`pcc_scan_uflow` + `scalbn`/`fma`/`fmod`/
`copysign`/`fabs`), which `pcc.api.build` was reported to compile into wrong
doubles while the CLI was correct. Re-run today against the host-libc oracle:

```text
optimize=0   0/44 lines differ
optimize=1   0/44 lines differ
optimize=2   0/44 lines differ
optimize=3   0/44 lines differ
```

Also with `PCC_NO_BUILTIN` unset, in case the in-process path was simply
missing the environment the CLI subprocess got: still 0 lines differ.

Object-level, for a source exercising the same failure class (doubles across
helper calls, struct returned by value, internal static):

```text
O0 objects MATCH   O1 objects MATCH   O2 objects MATCH   O3 objects MATCH
```

## What I do not know

Why it diverged when it was recorded. Two compiler defects fixed since then
touch exactly this corpus — the missing `scalbn` prototype (implicit-int
truncation) and the `__builtin_fma` alias — and either would produce wrong
doubles, but I did not reconstruct the earlier tree to confirm that the
in-process path is where they landed. Recording this as "cause unknown, symptom
gone" rather than picking the plausible story.

## What is pinned now

`tests/c/test_api_cli_object_parity.py` compiles one source through both
entry points and requires **byte-identical objects** at O0 and O2, plus
host-identical program output from the api-built executable. Byte identity is
a stronger contract than matching output and is what the recorded bug would
have broken, so a recurrence fails loudly instead of quietly making api-built
artifacts unusable as evidence.

The two entry points remain structurally different in one respect worth
knowing: `api.build` links every unit into a single object before calling the
system linker, while the CLI's non-emit path emits per-unit objects. The
contract test covers the single-source case where both must agree exactly.

The musl differential gate still drives the CLI — that is the path the runtime
Makefile uses — and its comment now records the measured status instead of the
stale "api produces different code" claim.

## Evidence

```text
tests/c/test_api_cli_object_parity.py                                 3 passed
tests/python/test_musl_string_differential.py
  tests/python/test_libc_import_baseline.py                           5 passed
```

## Note on a non-finding

While diffing the paths, `--emit-obj` produced identical bytes at O0, O1, O2
and O3, which looked like the optimization level being dropped. It is not:
`PassPipeline.run_backend_tier` deliberately runs LLVM's O1 pipeline as the
floor at O0 (documented in the code), and the probe was small enough that
O1/O2/O3 fold it identically. Checked before filing it as a bug.
