# musl string/memory functions compiled by pcc: 9 libc imports removed

Date: 2026-08-01

Task: `LIBC-P2-MEM-STR`

## Source identity

Tree of `2026-08-01-sdk-struct-helpers-pcc.md` plus this slice.

## What landed

- `pcc/py_runtime/vendor/musl-1.2.5/string/` (new): 11 upstream musl 1.2.5
  sources — memcpy, memmove, memset, memchr, memrchr, strlen, strcmp,
  strncmp, strchr, strchrnul, strrchr — with `VENDOR.json` recording the
  upstream, per-file sha256, every local patch, and the compile note. They
  are compiled **by pcc** (`$(PCC) -O0 --emit-obj`), never rewritten in
  pcc-Python, so upstream correctness is inherited and the artifact is
  pcc-produced.
- `pcc/py_runtime/Makefile`: vendored objects join `libpy_runtime_pcc.a`
  and `libpy_runtime_pcc_py.a` (the cc oracle archive stays pure host libc
  so differential tests keep a reference).
- `pcc/py_runtime/src/py_libc_fortify.c` (new, pcc-authored not vendored —
  musl has no _FORTIFY_SOURCE layer): `__memcpy_chk`, `__memmove_chk`,
  `__memset_chk` perform the operation when it fits the destination's known
  size and abort when it does not, archived into the pcc and default port
  archives via `FORTIFY_OBJ_PCC`.
- `tests/libc_import_baseline.json`: ratchet tightened **78 → 67**;
  memchr, memcpy, memmove, memset, strchr, strcmp, strlen, strncmp,
  strrchr, bzero and __memcpy_chk are no longer imported from libSystem.
- `pcc/codegen/c_codegen.py`: new `PCC_NO_BUILTIN=1` (pcc's `-fno-builtin`,
  required for any unit that DEFINES a libc primitive) — it skips the
  secure-clear `llvm.memset` conversion and marks functions with clang's
  `"no-builtins"` attribute. This is what let bzero be vendored at all;
  see the fixed bug row below.
- `tests/python/test_musl_string_differential.py` (new): compiles one C
  probe twice — with cc against host libc (oracle) and with pcc together
  with the vendored sources — and requires digest-identical output over
  alignment offsets 0/1/3/7/8, lengths 0…512, memmove overlap in both
  directions, embedded NULs, the byte range, and a strcmp/strncmp matrix.
  A second case pins the VENDOR.json manifest against the files on disk.

Local patches (all recorded in VENDOR.json):

- `strchrnul.c`, `memrchr.c`: musl's `weak_alias(...)` replaced by a plain
  wrapper (pcc's C frontend has no GNU alias attribute).
- `strchr.c`, `strrchr.c`: added the musl-internal prototypes
  (`__strchrnul`, `__memrchr`) — without them pcc's implicit-declaration
  path truncated the returned pointer (the class fixed earlier today).
- `memmove.c`: the non-overlap `return memcpy(...)` fast path removed; the
  existing word/byte loops cover it. Calling memcpy from libc's own memmove
  becomes an `llvm.memcpy` libcall (same family as the bzero case below).
- `bzero.c` vendored **unpatched** once `PCC_NO_BUILTIN=1` landed. Before
  that fix every bzero body shape (memset call, byte loop, even a volatile
  byte loop) became an `llvm.memset` libcall that LLVM's Darwin lowering
  turns back into a `bzero` call — libc's own bzero tail-branching to
  itself, observed as a hang inside `re_compile`. With the flag the object
  contains a normal `U _memset` external call. Vendored objects must still
  compile at **-O0**: at -O2 LLVM's own pipeline re-applies the rewrite
  even with `"no-builtins"`, which is the residual boundary recorded on
  `BUG-P1-SELF-MEM-INTRINSIC-LIBCALL-SELF-BRANCH`.

## Commands and results

```text
tests/python/test_musl_string_differential.py        2 passed in 1.77s
  (probe-binary comparison: cc+host-libc oracle vs pcc+vendored musl,
   digest-identical over lengths 0..512 x offsets 0/1/3/7/8, memmove
   overlap both directions, embedded NULs, byte range, strcmp/strncmp
   matrix; a ctypes/dylib harness was rejected because a library
   exporting memset/bzero interposes the test process's own libc)
tests/python/test_libc_import_baseline.py            1 passed (67-symbol baseline)
tests/python/test_libc_fortify_wrappers.py           3 passed in 1.43s
  (copy-through, exact-fit allowed, overflow aborts, archive membership)
sensitive C gates after the c_codegen no-builtin change   66 passed
stage1 pcc1 with the vendored objects: S1=0, `pcc1 --help` runs
full chain: stage2 S2=0, stage3 S3=0,
  pcc2/pcc3 metadata-normalized byte-identical
per-object check: all 12 vendored objects clean — every GOT branch has a
  matching undefined symbol (legitimate tail call), none self-references
```

## Supported claim

Eleven libc string/memory symbols are now provided by pcc-owned code — ten
from upstream musl **compiled by pcc**, plus pcc's own fortify wrappers —
linked from pcc's own runtime archive instead of imported from libSystem, with host-libc differential equivalence and an
unchanged three-stage fixed point (darwin-arm64, self backend,
no-libpython). The import ratchet records the drop as the authoritative
state.

## Not proven

- The 67 remaining imports (allocator, stdio, syscall wrappers, pthread,
  strtod/pow) belong to the other LIBC-P2/P3 rows.
- The vendored objects are built at -O0 (see the -O2 residual above);
  stage1 wall time was unchanged (17s cached), so no compile-time
  regression was observed, but block-op throughput is `PERF-P3-SIMD`'s row.
- Performance parity for memcpy/memset is explicitly out of scope
  (`PERF-P3-SIMD` owns block-op performance); this slice is correctness and
  ownership only.
- Linux: the vendored sources are portable but only the darwin-arm64 link
  is proven here.

## Update: the -O0 requirement is gone (and was partly my own misreading)

The slice originally pinned the vendored mem primitives to `-O0` because
`bzero` self-branched at `-O2` even with the `"no-builtins"` attribute. Re-measured
on the current compiler, that is no longer true:

```text
bzero at -O2, PCC_NO_BUILTIN=1:  branch=1  undef=1  ->  U _memset
bzero at -O0, PCC_NO_BUILTIN=1:  branch=1  undef=1  ->  U _memset
```

Both levels emit a normal external tail call to `memset`. The earlier `-O2`
reading came from a build made before `PCC_NO_BUILTIN` was complete.
`VENDOR_MUSL_O0_SRCS` is now empty and every vendored source compiles at the
default level.

The audit criterion also needed correcting: "branch count > undefined symbol
count" flagged `fma`, which has **two** tail calls to the **same** symbol
(`_scalbn`). A self-branch is specifically a branch with *zero* undefined
symbols; by that rule all 31 vendored objects are clean at -O2.

Re-verified after the change: libc imports still 64, differential and ratchet
gates 4 passed, stage1 green, stage2/stage3 metadata-normalized
byte-identical.
