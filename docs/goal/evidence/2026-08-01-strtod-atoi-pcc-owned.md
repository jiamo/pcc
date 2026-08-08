# strtod and atoi become pcc-owned (correctly-rounded decimal→double)

Date: 2026-08-01

Task: `LIBC-P3-HARD-SINGLETONS` (strtod half), with `atoi`/ctype from
`LIBC-P2-THIN-WRAPPERS`' computation set

## What landed

The vendor tree grew from one musl subdirectory to five, all compiled **by
pcc** (`PCC_NO_BUILTIN=1`, per-file optimization level), sha256-pinned in
`vendor/musl-1.2.5/string/VENDOR.json`:

```text
string/   memcpy memmove memset memchr memrchr strlen strcmp strncmp
          strchr strchrnul strrchr bzero          (LIBC-P2-MEM-STR)
stdlib/   atoi strtod
ctype/    isspace isdigit                          (atoi's dependencies)
internal/ floatscan shgetc + pcc_scan_uflow (pcc-authored fail-closed stub)
          + local stdio_impl.h / pcc_musl_features.h shims
math/     scalbn fmod copysign fabs                (floatscan's dependencies)
```

Supporting compiler/header work:

- `utils/fake_libc_include/float.h`: the IEEE-754 limit macros
  (FLT/DBL/LDBL MANT_DIG, MIN/MAX_EXP, MIN/MAX/EPSILON) with the host SDK's
  values; floatscan cannot compile without them.
- `utils/fake_libc_include/math.h`: C99 `float_t`/`double_t`.
- `pcc/py_runtime/Makefile`: vendor rule generalized over five musl
  subdirectories via `vpath`, with `VENDOR_MUSL_O0_SRCS` selecting the
  per-file optimization level — the mem primitives need `-O0`, and the scan
  helpers must NOT use `-O0`.

Local patches (all in VENDOR.json): musl `weak_alias` → plain wrappers;
musl-internal prototypes added; `long double` → `double` (darwin-arm64 long
double IS double and pcc's long-double return ABI is unimplemented);
`copysign.c`'s `libm.h` include replaced by the two standard headers;
ctype locale variants dropped; `stdio_impl.h` replaced by a scan-only FILE
declaration so musl's syscall layer is not pulled in.

## Import ratchet

`tests/libc_import_baseline.json`: **67 → 65**. `atoi`, `strtod` and
`scalbn` all become pcc-owned. scalbn briefly went the other way: it looked
like a miscompile of musl's body until a cross-TU bisect showed single-TU
compilation was correct and the wrong value was the *bits of the integer
argument* — the CALLER had no `scalbn` prototype, so the implicit-int path
made it read the result from an integer register and bit-cast it. Adding
the scalbn/scalbnf/scalbln prototypes (builtin table + fake math.h) fixed
it, same class as the 2026-07-31 implicit-declaration batch. Session total:
**78 → 65**.

## Commands and results

```text
tests/python/test_musl_string_differential.py        3 passed in 3.21s
  - mem/str/ctype/atoi group (built at -O0, matching the archive's level for
    the mem primitives): digest-identical to the cc+host-libc oracle
  - strtod group (built at the default level through the CLI, the same path
    the Makefile uses): bit-identical doubles AND endptr offsets over exact
    halfway cases, subnormals down to 4.94e-324, hex floats including
    0x1p-1074, 1e308/1e309, inf/nan spellings, and partial parses
  - VENDOR.json pins every vendored source
tests/python/test_libc_import_baseline.py            1 passed (65 symbols)
math differential (13 cases incl. scalbn overflow/subnormal paths, fmod,
  copysign, fabs): identical to host libm
tests/python/test_libc_fortify_wrappers.py           3 passed
sensitive C gates after the prototype additions      66 passed
stage1: S1=0, pcc1 --help runs
full chain: stage2 S2=0, stage3 S3=0, pcc2/pcc3 metadata-normalized
  byte-identical
```

## Supported claim

`strtod` — the correctly-rounded decimal→double conversion the LIBC track
calls one of its two numerically hard imports — is now provided by upstream
musl code compiled by pcc, bit-exact against the host libc across a corpus
built from the row's own list (subnormals, exact halfway cases, hex floats,
extreme exponents), together with `atoi` and the ctype predicates. The
three-stage fixed point is unchanged.

## Not proven

- `pow`, the second hard singleton, is untouched: llvm-libc's
  correctly-rounded pow is C++ and musl's needs its `libm.h`/`__math_*`
  closure, so it stays a libSystem import and keeps
  `LIBC-P3-HARD-SINGLETONS` open.
- `BUG-P1-API-VS-CLI-CODEGEN-DIVERGENCE` remains open: the same sources
  compiled through `pcc.api.build` still diverge from the CLI. The gate
  drives the CLI (the path the Makefile uses), so the shipped archive is the
  verified one, but the divergence itself is unexplained.

## Update: pow (the second hard singleton) is pcc-owned too

Vendored closure added under `vendor/musl-1.2.5/`: `math/pow.c`,
`math/exp_data.c`, `math/pow_data.c`, `math/__math_{oflow,uflow,xflow,invalid}.c`,
plus local `internal/libm.h` (a pow/scan subset of upstream — the double
bit-cast, eval/barrier and error-path declarations, without musl's endian.h /
fp_arch.h / long-double closure) and `internal/atomic.h`
(portable `a_clz_64`/`a_clz_32` only).

Differential: **188 pow cases bit-identical to the host libm** — 12 bases ×
15 exponents plus 2^1024 overflow, 2^-1075 underflow, negative bases,
`pow(1, inf)`, `pow(nan, 0)`, `pow(-1, inf)`, `pow(0, -1)`, `pow(-0, -3)`.

Two compiler gaps had to be fixed to get there, both real and both with
minimal repros:

- `__builtin_fma` was emitted verbatim, leaving an undefined
  `___builtin_fma` symbol at link time. Added `fma`/`fmaf` prototypes and a
  `_BUILTIN_SYMBOL_ALIASES` map (`__builtin_fma` → `fma`, and the same for
  fabs/copysign/sqrt) applied at both the implicit-declaration and call
  sites. Verified: `__builtin_fma(2,3,4)` returns 10.
- `extern const struct T { ... } g;` followed by the object's definition
  left the global UNSIZED (LLVM assert `Cannot getTypeInfo() on a type that
  is unsized!`), because the file-scope pre-pass resolves the object's type
  before the tag body embedded in that same declaration is registered.
  Minimal repro is two lines. Registration of embedded tag definitions was
  added in both the pre-pass and `codegen_Decl`, which was not sufficient,
  so the vendored data headers carry a documented split (standalone tag
  definition + extern declaration — the same C) and the compiler gap is
  filed as `BUG-P1-CC-EMBEDDED-TAG-IN-EXTERN-DECL-UNSIZED`.

Ratchet: regenerated at **65** with the trade argued in the JSON — pow
becomes pcc-owned, `fma` is imported meanwhile. musl's fma compiles and
verifies standalone (66 cases bit-identical) but hits a parse error through
the Makefile invocation of the same source, filed as
`BUG-P1-CC-MAKE-PATH-PARSE-DIVERGENCE`.

Gates after the change: LIBC gates 7 passed, sensitive C gates 66 passed
(the codegen touched the builtin tables), stage1 green, stage2/stage3
metadata-normalized byte-identical.

## Update 2: fma reclaimed — the "Makefile parse divergence" was my own comment

`BUG-P1-CC-MAKE-PATH-PARSE-DIVERGENCE` was a false alarm and is corrected
rather than left standing: bisecting fma.c by function boundary showed even
its first 14 lines failed, which pointed at the includes, and a per-header
probe pinned it to the `atomic.h` shim **I had written** — its comment
contained `arch/*/atomic_arch.h`, whose `*/` closed the block comment early
and turned the rest of the sentence into syntax. The earlier "compiles
standalone" reading was also wrong: that invocation had no `--emit-obj`, so
it took the compile-and-run path. pcc was never at fault here.

With the comment fixed, `fma` compiles, its 66-case differential is
bit-identical to the host libm, and the vendored pow closure needs nothing
from libSystem:

```text
ratchet 65 -> 64 (session 78 -> 64, 17 symbols)
LIBC gates                                          7 passed
stage1 green; stage2/stage3 metadata-normalized byte-identical
```

Both hard singletons (`strtod`, `pow`) and every function their closures
need are now pcc-owned.
