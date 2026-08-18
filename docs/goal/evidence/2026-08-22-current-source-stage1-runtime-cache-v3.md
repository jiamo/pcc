# Current-source stage1 and runtime-cache v3 receipt

Status: **GREEN for the bounded stage1 construction slice; module98 and stage2 remain open.**

This evidence follows the current allocator S1 closure in
`2026-08-21-granule-map-v2-correctness-gates-and-stage-proof.md`.  It proves
that the frozen current compiler source can produce and execute a no-libpython
stage1 compiler.  It is not a hot-stage timing comparison, an S2 acceptance,
or a pcc2/pcc3 fixed point.

## Runtime-cache publication repair

The content-addressed pcc-Python runtime cache previously omitted the required
`${archive}.target` sidecar.  The cache publication schema is now v3 and binds
the archive, provenance manifest, C-API inventory, and target stamp.  The
publication-format schema participates in the cache key, so upgrading the
format publishes a new immutable directory instead of deleting an older cache
path that another consumer may still hold.

Focused command:

```bash
gtimeout 150s zsh -o pipefail -c \
  'gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
   tests/test_runtime_archive_consumers.py -k pcc_runtime_cache \
   2>&1 | tee build/runtime-cache-target-stamp-focused-v2.log'
```

Result: `10 passed, 14 deselected in 21.90s`; log SHA256
`da8f318cfcc342664555fa6b76c0f02636a902d3516ac266c623237e8520d88e`.
The focused gate includes the literal historical `source_key + "-pcc-py"`
legacy path and proves it is left byte-for-byte untouched by v3 publication.
Independent code review reported zero blocking findings.

The materialized v3 cache is:

```text
/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/ccc2ad00328cf2b0b81bb403-pcc-py
archive SHA256   f360c2a1af9019bf9cd0062402248b13ba21640ad12671287115782226d99a26
manifest SHA256  f2d64ffaae0d3e2820d9d9c6676c8385c1602043cfaff1919781b4af232a193b
C-API SHA256     71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda
target SHA256    1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1
marker SHA256    a5a3be76d5d0d7b3f6f2a1b4f21106283d1b6c7e4fa50b115b929b0124d3ef91
target           darwin:arm64:arm64-apple-darwin25.5.0
members          186
policy           pcc-production-no-handwritten-c.v1
allocator source 76a996a36a01d399bf3ac5d5dcd91b27ce6f36a2e6bbb391b2fd78f72db90781
```

The provenance verifier passed.  Every archive member is produced from
pcc-Python source with `uses_host_cc=false`, but the object emitter is
`llvmlite-target-machine`; this archive is no-libpython pcc-Python runtime
evidence, not a fully self-backed runtime claim.  The prior cache directory
`120492e512191cf83498799d-pcc-py` retained its archive, manifest, and C-API
hashes and still has no target stamp.

## Frozen stage1 construction

The build consumed the output-owned immutable source snapshot recorded by:

```text
build/stage1-current-a76-20260822-v1/source-manifest.json
bootstrap source SHA256 f3d6e03e2458597155bb3d333eb6c8e4e01a617608e6f2c10beb994ea77c457c
source files            1132
source-manifest SHA256  a80000e444f3db769c34a655bb32c9eacbcb3afdf67ff31973a5658704afd8c1
```

Mode and scheduler controls were GC0, `--backend self`,
`--python-libpython off`, `PCC_SELF_LINK=pcc`, frontend jobs 10,
self-backend/link jobs 8, Python IR passes off, frontend and object caches off,
and private HOME/TMP/cache roots.  The v3 receipt loader independently
accepted the completed receipt.

Durable artifacts:

```text
output                build/stage1-current-a76-20260822-v1
build receipt SHA256  3faf7eebc5112793642ab70911f6dbc814c8cd764cf6fc60f4580c71ca69547f
stage result SHA256   8c394d97724627f5e8eba0a65fb38d3ffd557f5ac1c18cabf1089b816e9d36c6
run manifest SHA256   d757337b5bb030e9f78cb13ee3cb495856138eee0a539293acf41118f26b7f32
pcc1 SHA256           b7b196ae8f730ef17ab1b5feb095294b530a08e3359a799d3164bd82d60e47df
pcc1 size             183823816 bytes
status / return code  SUCCEEDED / 0
```

`otool -L` reports only `/usr/lib/libSystem.B.dylib`; the result receipt has
`links_libpython=false` and `links_llvm=false`.  The compiler is a signed arm64
Mach-O.  The link warning about dropped unwind metadata is retained in
`stage1.stderr` and is not hidden.

Measured construction metrics:

```text
wall                 272.68 s
user + system        930.65 + 50.56 s
max RSS              6,393,004,032 bytes
peak footprint       1,423,918,592 bytes
frontend parallel     34.026 s
self native emit      96.025 s
self link driver     105.539 s
```

This is a cold, cache-off, single-arm construction receipt.  It is not
comparable to the retained 63.908 s hot stage1 control and does not establish
that the stage1 regression signal near 133.6 s is resolved.

## Produced-compiler execution gate

With `PCC_CURRENT_PCC1` bound to the compiler above and
`PCC_RUNTIME_ARCHIVE` bound to the v3 cache archive:

```bash
gtimeout 450s zsh -o pipefail -c \
  'env -u LC_ALL \
   PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/stage1-current-a76-20260822-v1/pcc1 \
   PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/ccc2ad00328cf2b0b81bb403-pcc-py/libpy_runtime_pcc_py.a \
   uv run pytest -vv -x -n0 --tb=short \
   tests/python/test_pcc1_emits_native_function_binary.py::test_pcc1_compiles_and_runs_function_definitions \
   2>&1 | tee build/stage1-current-a76-20260822-v1.function-gate.log'
```

Result: `1 passed in 11.64s`; log SHA256
`143043079287d4a07b752b9d30bc224dec0e8028a8ebc4c51d2f83f22ff567b6`.
The single node compiles and runs no-function, bare-def, annotated-def, and
main-def programs and checks their outputs and `BAD_INCREF`; it is not merely
`pcc1 --help` or a linkage inspection.  Its command above records the exact
compiler and runtime paths because the pytest log alone does not print their
identities.

## Remaining boundary

There is not yet a legal S2 A/B pair.  Compiler `b7b196ae...` is S1 exact-set
production despite the build receipt's generic `candidate` arm label, and no
v3 S2 build or v3 baseline build exists.  Old v2 compilers and the prior
DENIED module98 manifest cannot be relabeled as current evidence.  The frozen
module98 workload remains usable (`47289e1d...`, 1,831,588-byte IR;
`e536a7da...`, 5,666,157-byte historical assembly oracle), but only after both
new arms independently reproduce it.

The frozen general Python compile A/B tool is intentionally not widened for
this experiment: it admits only the `pcc/llvm_capi/ir.py` source variable,
requires the same runtime bundle in both arms, and compiles Python to a runnable
binary rather than replaying raw self-backend IR.  The initial proposal -- put
granule exact-positive before the managed-pointer read probe and skip five
ordinary-object exact-set writes -- is not yet safe to activate.  The
allocator currently writes live-cell magic before the object header is
initialized and retires it only after exact unregister, so a lock-free positive
can observe a half-initialized or already-retiring cell.  There is also a sixth
lifecycle mutation at forwarded-source retirement: a GC3/GC4 object that fell
back to the ordinary object slab must retire its live-cell marker as well as
its forwarding/index state.

The next explicitly experimental Python+C mirror slice is therefore the full
bounded lifecycle: reserve a structural object cell in a non-managed state,
initialize its header, release-publish live, acquire-query live, and
release-retire before free-list reuse.  Normal unregister and forwarded-source
retirement must both retire the marker.  Only a proven object-family positive
may avoid exact-set traffic; every negative/unknown, foreign, large, raw/pool,
minor/zpage, type-object, forwarding-source/target lookup and other moving-GC
case retains the complete exact/index path.  This is an internal provenance
slow path, not an execution fallback.  The slice must pass real-pthread
reserve/publish/retire/reuse tests, focused provenance/layout/GC0..4, forced
moving fallback/retirement, finalizer/weakref/resurrection/trashcan, and the
current-archive GC3/4 moving gates before building two new v3 stage1 arms
through identical canonical source and output aliases.

Only then may the pre-registered frozen module98 A/B run with receipt-bound
compiler/runtime/source identities and balanced pairs.  After focused
acceptance, recapture comparable hot/cold stage1, stage2, and host-CPython
controls.  S2 production acceptance, stage2 <= non-regressed stage1, pcc1
versus host pcc0, pcc2/pcc3 fixed point, final five-GC resource acceptance,
page-allocation fault injection, and exact-index origin rollback remain open.
