# libpy_runtime_pcc.a pure-C chain crashes (implicit decls + py_decref stack-free)

Goal task: `LIBC-P1-PCC-RUNTIME-ARCHIVE` (prove `pcc` compiles its own C
runtime end to end: `libpy_runtime_pcc.a` + stage1→3 chain).

Mode labels: self backend, no-libpython, `PCC_RUNTIME_CC=pcc
PCC_RUNTIME_HIGH=c` (pure pcc-emitted C archive). The production default
(`HIGH=py`, pcc-Python ports for high modules) is unaffected. Host cc
oracle (`PCC_RUNTIME_CC=cc`) untested at chain scale here — see Open.

## Symptom 1 [CONFIRMED FIXED]: FILE*/64-bit returns truncated by implicit declarations

First observed as: stage1 pcc1 (pure-C archive) built and passed its
publish-barrier smoke, then SIGSEGV ~16s into stage2 at
`flockfile(fp=0x00000000f20f3728)` — a real `FILE*` with the upper 32 bits
zeroed — via `py_format_float -> fwrite`. Frontend codegen workers also
aborted with `malloc: pointer being freed was not allocated` on
deterministic stack addresses.

Minimal repro (3 lines, crashed immediately, exit 139):

```bash
printf 'x = [1.5, 2.5]\ns = str(x)\nprint(s)\nprint("done")\n' > /tmp/strlist.py
env -u LC_ALL PCC_RUNTIME_HIGH=c PCC_RUNTIME_CC=pcc uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on /tmp/strlist.py -o /tmp/strlist
/tmp/strlist   # SIGSEGV before the fix
```

Root cause, proven in disassembly of `build_pcc/py_print_fmt.o`
(`py_format_obj_to_str`): `open_memstream` had NO declaration (absent from
`utils/fake_libc_include/stdio.h` and from the builtin prototype table in
`pcc/codegen/c_codegen.py`), so pcc implicitly declared it as returning
`int` and emitted `cbz w0 / mov w20, w0` — the returned `FILE*` lost its
upper 32 bits.

A probe (monkeypatching `LLVMCodeGenerator._declare_implicit_function` and
compiling all `pcc/py_runtime/src/*.c` with `pcc.api.build(kind="object")`)
found the full implicit-declaration set:

```text
open_memstream        py_print_fmt.c    FILE*  -> truncated pointer  (the crash)
realpath              py_os_substrate.c char*  -> truncated pointer
inet_ntop             py_asyncio_io.c   char*  -> truncated pointer
strtoll               py_capi_shim.c, py_gc_backend.c  long long -> i32
getline               py_os_substrate.c ssize_t -> i32
copysign, rint        py_format.c       double returned in w0 not d0 (!)
accept/bind/listen/getsockname/getpeername/setsockopt/shutdown/ntohs  py_asyncio_io.c (int-returning, width-benign)
arc4random_buf        py_os_substrate.c (void, benign)
__atomic_exchange_n   pcc_dlpack_runtime.c, pcc_gc_external_resource.c (cc-compiled in this archive; intrinsic gap, see Open)
```

Fix: prototypes added to the builtin table in `pcc/codegen/c_codegen.py`
(and `open_memstream` to `utils/fake_libc_include/stdio.h`). After a forced
re-emit (`rm -rf build_pcc && make libpy_runtime_pcc.a`) the probe reports
only `__atomic_exchange_n` (cc-compiled files), the disassembly shows
`cbz x0 / mov x20, x0`, and the 3-line repro prints `[1.5, 2.5]` matching
CPython.

Test evidence after the c_codegen change: sensitive C gates green
(`test_c_parser` 44, lua onelua+math parity 2, lz4+unsigned 20, sqlite 5;
re-run combined: 66 passed in 21.46s).

## Symptom 2 [OPEN]: py_decref frees a stack address during the stage1 smoke compile

With symptom 1 fixed, the pure-C-archive pcc1 now aborts EARLIER — during
its publish-barrier smoke compile of a trivial `def main() -> int` program
(`scripts/bootstrap.sh --stage 1` exits 134):

```text
___BUG_IN_CLIENT_OF_LIBMALLOC_POINTER_BEING_FREED_WAS_NOT_ALLOCATED
py_decref +1320
user_pcc_py_frontend_codegen_generation_lowering_GenerationLoweringMixin__generate_impl +121056
user_pcc_py_frontend_codegen_layer1_entrypoints_L1CodeGenEntrypointMixin_generate +576
user_pcc_py_frontend_pipeline_compile_python +51996
```

`py_decref` receives a deterministic stack address (e.g. 0x16fdede98,
0x16fded8b8 — stable across runs and processes). Reproduces with:

```bash
env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=c \
  build/bootstrap-pcc-c-runtime/pcc1 --ir-scaffold=on --backend self \
  --python-libpython off /tmp/smoke.py -o /tmp/smoke_bin   # SIGABRT
```

Discriminator [CONFIRMED 2026-07-31]: the same stage1 with
`PCC_BOOTSTRAP_RUNTIME_CC=cc PCC_BOOTSTRAP_RUNTIME_HIGH=c` (all-C archive
compiled by HOST cc) aborts identically (exit 134; crash report
`pcc1-2026-07-31-180209.ips` shows `py_decref +1048` under
`UserFunctionLoweringMixin__emit_user_function`). This is therefore NOT a
pcc C-codegen miscompile: the hand-written C high-module source itself has
the bug, and it is masked in production because the default
`PCC_RUNTIME_HIGH=py` links the pcc-Python ports for those modules. The
routinely exercised "cc tier" (`PCC_RUNTIME_CC=cc`) also keeps `HIGH=py`,
so the all-C configuration has been rotting untested — C-vs-port mirror
drift, same class as the `pcc_trash_should_defer` fall-through precedent.

Note the earlier worker aborts under symptom 1 had the SAME deterministic
stack addresses, so symptom 2 was present all along underneath the
truncation crash.

## Update 2026-07-31 evening: symptom 2 root cause localized [CONFIRMED]

Chain of evidence (each step observed, not inferred):

1. `PCC_LOG=refcount,alloc` on the aborting smoke compile shows the last
   event before the assert is `incref value0=1 value1=0 ptr=0x16af7f808` —
   an incref of a stack-range pointer with type_tag 0; the pointer occurs
   exactly once before the aborting decref.
2. A temporary tagged trap in `pcc_gc_store_root`/`pcc_gc_store_ptr`
   (abort when `value` is in the stack range; removed after use) moved the
   crash to the STORE site: `pcc_gc_store_root` called from
   `GenerationLoweringMixin__generate_impl +37988`.
3. Disassembly of that window shows the stored value is **the return value
   of `NativeModuleAliasMixin._rewrite_traceback_handler_bindings`** — a
   `-> None` Python method — being rooted by the caller
   (`bl _rewrite...; stur x0,[fp-0x3f8]; ...; ldur x1,[fp-0x3f8];
   bl pcc_gc_store_root`).
4. Disassembly of the callee shows **all three of its return paths call
   `pcc_gc_frame_leave(<stack frame-node address>)` and then `ret` without
   ever setting x0**. The "return value" is whatever `pcc_gc_frame_leave`
   leaves in x0.

So the defect is a **frontend cross-module return-ABI drift for
implicit-None returns**: the defining module lowers the `-> None` method
with no materialized return value (effectively void), while the calling
module (a different compilation unit — generation_lowering vs
native_modules) declares the callee as returning a PyObject* and roots the
result. The linker cannot see the mismatch, so x0 garbage becomes a rooted
"object":

- Under `PCC_RUNTIME_HIGH=py` (production default) the C-kernel
  `pcc_gc_frame_leave` implementation leaves a heap-valid value in x0, and
  the bogus root is incref'd then decref'd symmetrically — latent and
  invisible for a long time.
- Under `PCC_RUNTIME_HIGH=c` (both compiled-by-pcc AND compiled-by-cc) the
  C `pcc_gc_frame_leave` leaves its own argument — a stack frame-node
  address — in x0, so the caller roots a stack address and the balanced
  release aborts (`py_decref: refcount underflow` under asserts;
  `pointer being freed was not allocated` without).

This also retro-explains the "cc/HIGH=c also aborts" discriminator: the
miscompiled code is the GENERATED frontend code (identical in every
config); only the leftover-x0 value differs by runtime archive.

## Status

Symptom 1 fixed (implicit-declaration prototypes; sensitive C gates green).
Symptom 2 root-caused; the defining side is
`user_function_decl_lowering.py:141-144` (`-> None` maps to `ret void`,
deliberately), and the crash site calls the method DIRECTLY (`bl`), so the
mismatched ptr-return declaration comes from the CALLING module's
cross-module signature derivation (generation_lowering.py calls a method
defined in native_modules.py — separate compilation units; suspect the
class export schema / cross-module method declaration path declares
methods uniformly as pointer-returning and roots the result). Next-session
recipe: two-module toy (module A: class with a `-> None` method whose body
ends in a runtime call; module B: import A, call the method inside a
function so the result gets rooted), compile with scripts/pcc_multi.py or
the multi-file path, and diff the callee definition's IR return type
against the caller module's declaration of the same symbol. Fix on the
declaration side (honor None-return as void cross-module and do not root),
or unify the whole `-> None` ABI to materialized py_None — either way this
is bootstrap-critical: stage caches rotate and the default chain, five-GC
matrix, and the pure-C `HIGH=c` chain must be re-proven afterwards. The
temporary store-site trap has been removed and the cc archive rebuilt
(worktree clean of probes).

## Update 2026-07-31 night: host-side ABI fix landed; pcc1-side residual defined

Landed and proven on the host path:

- `class_gen._extern_class_decl_plan` now computes a per-method
  ``returns_none`` (decoded-NoneType/name-based, with indexed plan access —
  wide for-target tuple unpacking is a self-host hazard) and the extern
  declaration loop lowers it to ``void`` (async keeps PTR), matching
  ``declare_method``.
- `marshal.marshal_to_object` materializes ``py_None`` for a void SSA value
  in the DynType branch, so dyn-typed call expressions over ``-> None``/
  unannotated callees stop trying to marshal a nonexistent value.
- Generic regression: `tests/python/test_cross_module_none_return_abi.py`
  compiles a two-module program and asserts every declared symbol's return
  type matches its definition (RED reproduced `declare ptr` vs
  `define void`; GREEN now), plus a full-closure scan of
  `pcc.__main__` emit-llvm showed 5515 defines / 5243 declares / 0
  mismatches.
- Default chain re-proven: stage1/2/3 green, pcc2/pcc3
  metadata-normalized byte-identical; multi-file (42), bootstrap-shim (93),
  class-schema gates and fallback ratchets (27) green.

pcc1-side residual [CONFIRMED by probe]: under the self-hosted compiler the
same schema round trip degrades — a temporary probe printed
host `ret name=None, _is_ast_node=True` vs pcc1 `ret name=dyn,
_is_ast_node=False`: `encode_type` relies on isinstance chains that fail on
cross-module dataclass nodes under pcc1, so ``("none",)`` is never written
and the descriptor arrives as ``("dyn",)``. Carrying a plain
``"returns_none"`` bool in the export schema fixes it on the host, but
adding the key made pcc1's cross-module machinery lose method FuncDefs
wholesale ("with kwargs needs a FuncDef" across ~56 workers) — the export
schema shape is contract-pinned (fixed wire field tables in pipeline.py and
generated static tables such as
`pcc/py_frontend/codegen/_l1_codegen_static_methods.py` /
`host_contract.py`), so a new key needs the schema-regeneration flow, not an
ad-hoc insert. A bisect (writers removed, everything else kept) restored
pcc1→pcc2 builds (0 worker failures), so the two schema-writer lines are the
only blocked piece.

Consequence for the pure-C chain today: stage1 pcc1 (host-built, fixed) now
passes its publish-barrier smoke; the pcc1-built pcc2 still declares
cross-module ``-> None`` methods as ptr (dyn degradation) and its HIGH=c
smoke still aborts (134). Follow-up slice: register ``returns_none`` in the
export wire contract + regenerate the pinned static tables, then re-run
pcc1→pcc2→pcc3 under HIGH=c.

## Update 2026-08-01 (post-midnight): schema-key route bisected precisely

Four instrumented pcc1 rebuild cycles narrowed the ``returns_none``
schema-key blocker to a precise, reproducible riddle:

- Bisect B: writers emitting a literal ``False`` value — pcc1→pcc2 builds
  clean (0 worker failures). The extra schema KEY is harmless.
- Bisect C: writers calling the helper (closed-world
  ``_closed_world_is_node(ret, NoneType)`` variant, locally imported,
  same idiom as the proven typed-int closed-world check) — 56 worker
  failures again. So the failure correlates with the helper RETURNING TRUE
  (which makes the host build pcc1 WITH the void-declaration and
  ``m.dyn.none`` marshal paths active for its own cross-module ``-> None``
  calls), not with schema shape or the helper's evaluation mechanics.
- Bisect D: probe in ``_extern_class_decl_plan`` — under pcc1 the plans are
  complete and identical to the host (e.g. CoreHelperMixin 7/7 methods).
- Bisect E: probe at the ``needs a FuncDef`` raise —
  ``info.extern_method_defs`` EXISTS, has the right length, and
  ``method_name in info_defs`` is TRUE, yet
  ``info.extern_method_defs.get(method_name)`` returned None to
  ``_find_method_def``. The dict VALUE itself is apparently None under the
  fixed pcc1, i.e. ``synth_defs[mname] = _FuncDef(span=..., name=...,
  args=..., return_ty=..., body=(), decorators=(), is_async=...)``
  produced None — a kwargs dataclass construction that only misbehaves in
  a pcc1 whose own binary contains the new void-return call shapes.

Working hypothesis for the follow-up slice: the host-built fixed pcc1
contains a void-declared cross-module ``-> None`` call (or ``m.dyn.none``
marshal) somewhere in the ``_FuncDef``-construction/`` _Arg`` synthesis
path whose Python-level value is consumed, and one of those sites still
mis-lowers (e.g. a call-site consumer of a now-void callee that previously
"worked" by consuming leftover x0 as the constructed object!). In other
words: pcc1's own extern-plan code was silently RELYING on the old
ptr-declared garbage in a way that happened to evaluate truthy. The next
session should disassemble pcc1's `_extern_class_decl_plan` around the
``_FuncDef`` construction and compare the fixed vs pre-fix call shapes.

The tree landed back on the proven-green state: schema writers removed
(the helper stays, documented and unused), probes removed, stage1 green,
pcc1→pcc2 builds with 0 worker failures, ABI regression + multi-file +
class-schema gates 43 passed, fallback ratchets 27 passed.

## Update 2026-08-01 second round: three hypotheses eliminated, defect class identified

Two more probe cycles on the fixed-pcc1 configuration (writers re-landed):

- Probe FIND2 at the raise site read the value by SUBSCRIPT:
  ``probe_val is None`` = False, ``ABSENT`` = False — the synth_defs VALUE
  is a real FuncDef. The earlier "value is None" reading is dead, and so is
  plain "``.get`` broken": the SAME ``in`` + subscript expression succeeds
  inline at the raise site but ``_find_method_def`` (rewritten to ``in`` +
  subscript, the recorded dict.get-pitfall prescription) STILL returns None
  under the fixed pcc1 — identical 56-failure signature.
- Conclusion: the mis-behavior is POSITION-DEPENDENT, not
  construct-specific. Compiling the compiler with the void-return ABI fix
  active (returns_none=True schemas) produces a pcc1 whose
  ``_find_method_def`` (and likely neighbors) is miscompiled — a
  second-order host-vs-pcc1 divergence EXPOSED by the ABI fix, not caused
  by any single dict/tuple idiom. Bisect B (literal False) proving clean
  builds confirms the trigger is the void-declaration/m.dyn.none code
  shapes appearing throughout pcc1's own binary.

Route for the next slice: this is now squarely
``tests/python/test_self_host_oracle_diff.py`` territory — build the fixed
pcc1 (writers on), then oracle-diff host-vs-pcc1 on the frontend corpus to
localize the miscompiled construct; candidates are the generated call
shapes adjacent to newly-void callees (the leftover-x0 consumers that the
old ptr ABI accidentally fed). ``_find_method_def``'s ``in``+subscript
rewrite is kept (harmless, prescription-conformant).

## Update 2026-08-01: RESOLVED — the "position-dependent miscompile" was my own export semantics

The final bisect round found the true mechanism, and it was in this
investigation's own slice, not a second-order miscompile:
``_export_returns_none`` returned True for UNANNOTATED methods, but the
definition side lowers the POST-INFERENCE type — so the host declared
every unannotated cross-module method void, my marshal fix dutifully
materialized ``py_None`` as every such call's result, and
``ast_fd = self.class_lowering._find_method_def(...)`` (an unannotated,
value-returning method called cross-mixin) always "returned" None. That
explains every prior observation: literal-False writers built clean,
plans were complete, the raise-site subscript saw real values, and the
in+subscript rewrite of ``_find_method_def`` changed nothing because the
function's RESULT was discarded at the void-declared call boundary.

Fix: ``returns_none`` is True only for an explicit ``-> None`` annotation
(unannotated exports stay dyn). With that, the full pure-C chain passed:
stage1 (barrier smoke included), stage2 275.5s with zero worker failures,
stage3 60.9s, pcc2/pcc3 metadata-normalized byte-identical. One
false-positive chain failure along the way was this investigation's own
round-2 store trap left in py_obj.c (it fired on legitimate stores in the
self-backend emit path); it is now fully removed and the archives rebuilt.

## Status — CLOSED for the chain claim

`LIBC-P1-PCC-RUNTIME-ARCHIVE` is proven: see
`docs/goal/evidence/2026-08-01-pcc-runtime-archive-pure-c-chain.md`.

## Open (follow-up cards, not blockers)

- encode_type's isinstance chains degrade schema TYPE descriptors to
  ``("dyn",)`` under pcc1 (params included; masked by the boxed dyn ABI).
  The returns_none bool bypasses it for the return ABI only.
- Unannotated methods whose bodies implicitly return None keep the old
  ptr-declared latent behavior cross-module (explicit ``-> None`` is
  fixed); annotating them or inferring at export time closes it.
- The deeper self-host defect worth its own card: `encode_type`'s
  isinstance chains silently degrade EVERY schema type to ``("dyn",)``
  under pcc1 (params too — masked because dyn params share the boxed ABI).
- `__atomic_exchange_n` is not intercepted by pcc's `__atomic_*` intrinsic
  lowering (falls to implicit declaration). The two files using it are
  cc-compiled in this archive, so it does not block this task, but the
  intrinsic gap is real (same family as the fixed `__atomic_fetch_*` gap).
- pcc silently accepts implicit function declarations; every one of them is
  a latent pointer/width-truncation bug. A fail-closed diagnostic (or at
  least a warning gated by env) for implicit declarations in `--emit-obj`
  builds would have caught this class years earlier.
