# pcc

[![PyPI](https://img.shields.io/pypi/v/python-cc)](https://pypi.org/project/python-cc/)
[![Python](https://img.shields.io/pypi/pyversions/python-cc)](https://pypi.org/project/python-cc/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python-authored compiler toolchain that targets both **C** and **Python**: the C frontend is mature, the Python frontend lowers typed Python to native code, the runtime is being progressively re-authored in pcc-Python itself, and the experimental "self" backend (no LLVM dependency) compiles real C and is the default for the macOS arm64 bootstrap.

`pcc` started as a compiler experiment and has grown into a large repository with:

- a real C compilation pipeline
- an experimental Python-to-native pipeline
- a self-hosting Python runtime archive (pcc-Python re-authoring of the original C runtime)
- a three-stage bootstrap that builds `pcc1 → pcc2 → pcc3` byte-identically on macOS arm64
- an experimental in-tree self backend (LLVM-free, AArch64 / x86\_64) covering 4000+ C tests
- project collection/build orchestration for multi-file codebases
- a multi-tier optimization/pass framework
- compile caching and system-link workflows
- large integration targets such as Lua, SQLite, PostgreSQL `libpq`, nginx, zlib, lz4, zstd, PCRE, OpenSSL, and readline
- thousands of regression and corpus tests against native toolchains

The C frontend is the most mature subsystem. The Python frontend is actively evolving and already supports typed-native code, a CPython fallback for imported modules, direct C interop through `pcc.extern`, and a low-level intrinsic surface (`pcc.unsafe`) that lets runtime code be authored in Python and lowered to the same operations the original C used.

---

## Why `pcc`

`pcc` is designed for people who want more than a toy parser but still want a compiler they can read, debug, and extend quickly.

### What makes it different

- **Compiler implementation in Python** for fast iteration
- **LLVM backend** for object emission, optimization, and native execution
- **Real-project workflows**: single-file, merged-directory, separate-TU, make-derived source selection, dependency builds, and host linking
- **Compiler as a library**: use `CEvaluator`, `build(...)`, or `module(...)`
- **Large-scale validation**: Lua, SQLite, PostgreSQL, nginx, GCC torture, Clang C tests, and more
- **A pass framework with measurable tiers** instead of treating optimization as a single opaque backend step

> **Disclosure: AI-assisted modelling of LLVM behaviour.** The
> experimental in-tree self backend (LLVM-free emit path, AArch64 +
> partial x86\_64) and the multi-tier pass framework were authored
> with substantial AI assistance, working from LLVM's published
> behaviour and IR semantics rather than a code port. Every emitted
> instruction sequence and every pass transformation is checked
> against the LLVM-backed baseline through the test corpus
> (4 000+ self-backend cases, microbenchmark matrix, regression gate
> with a hard 2.0× wall-clock threshold). This is "AI implementing a
> from-scratch compiler that *behaves* like LLVM on a known input
> set", not "LLVM rewritten in Python". The dependency on observable
> LLVM behaviour during development is intentional and disclosed; if
> you find a divergence from the LLVM-backed reference path, please
> open an issue.

---

## Project status at a glance

| Area | Status | Notes |
|---|---|---|
| C frontend | advanced | real-project tested, most mature part of the repo |
| Python frontend | experimental | typed-native path + CPython fallback + extern-C bridge + `pcc.unsafe` intrinsics |
| Build orchestration | strong | directory mode, `--separate-tus`, `--sources-from-make`, `--depends-on`, `--system-link` |
| Validation | broad | thousands of tests passing across unit, corpus, integration, and self-backend coverage |
| Python corpus | active | 177 end-to-end programs across 5 phases; majority pass under both default and `PCC_RUNTIME_HIGH=py` runtimes |
| Three-stage bootstrap | landed on macOS arm64 | `pcc1 → pcc2 → pcc3` byte-identical after Mach-O signature normalization |
| Runtime self-host | active | 36 runtime modules re-authored as pcc-Python via `pcc.unsafe`; libpython only on the optional compatibility archive |
| Self backend (no LLVM) | experimental | passes 4054 cases across `c-testsuite` + GCC torture; macOS arm64 bootstrap default; not yet a global default |
| Performance tooling | mature | microbenchmark matrix + standalone benchmark suite + pass attribution |

---

## Bootstrap / self-host status

`pcc` runs a three-stage self-host bootstrap for its Python frontend:

1. CPython-hosted `pcc` compiles `pcc/__main__.py` → `pcc1` (native)
2. `./pcc1` compiles the same source → `pcc2`
3. `./pcc2` compiles the same source → `pcc3`
4. `cmp pcc2 pcc3` is byte-identical after Mach-O code-signature normalization

This is verified today on macOS arm64 (the supported development host). It is
not yet claimed for other hosts.

### What is actually shipped

- `scripts/bootstrap.sh` completes stage 1 / 2 / 3 on macOS arm64.
- On that host the script now defaults to `--backend self` and keeps
  `--backend llvm` as the explicit escape hatch; on every other host the
  default is still `llvm`.
- `scripts/run_self_backend_bootstrap_gate.py` runs both stacks side-by-side
  and reports compile/link wall time, binary size, libpython linkage,
  `--help` latency, and small-benchmark compile/run geomeans, with a hard
  `2.0×` regression threshold against the LLVM-backed baseline.
- The compiled bootstrap binary's `--help` path exits cleanly under a hard
  timeout and no longer prints embedded-CPython shutdown noise.
- The default `python -m pcc` / `uv run pcc` entry path goes through
  `pcc.cli_core` (no `click` dependency on the runtime surface).

### Runtime self-host (Phase 4c)

The default no-libpython runtime archive used by the bootstrap binaries is now
primarily authored in pcc-Python rather than C:

- 36 runtime modules live in `pcc/py_runtime/py/*.py` — the substrate itself
  (`py_substrate.py`), object/dict/list/set/tuple/str/int families,
  exception machinery, class/MRO, formatted print, env/path helpers — all
  compiled by `pcc` and linked into `libpy_runtime_pcc_py.a`.
- These modules use the `pcc.unsafe` intrinsic surface (35 compiler-recognized
  helpers covering `malloc`/`load`/`store`/`memcpy`/`getenv`/global address
  forms/tagged-int packing/etc.) plus the existing `pcc.extern` C bridge.
  Each `pcc.unsafe` call is lowered by the pcc compiler to the same raw
  operation the original C used; calling them under CPython traps loudly.
- The pcc-Python frontend now boxes user-level `int` values by default so
  that programs like `2 ** 100` correctly produce a bignum across the runtime;
  internal pcc / runtime modules opt back to the raw `i64` fast path via the
  `pcc.unsafe` import marker.
- The `cc-C` and `pcc-C` runtime archives are still kept for differential
  oracle comparison, and a separate `libpy_runtime_libpython.a` exists only
  when `PCC_WITH_LIBPYTHON=1` is requested.

### Backend choice for the bootstrap

Two backends can drive native emission for the bootstrap stages:

- `llvm` — the production path, llvmlite-based, used as the cross-host default
  and the regression baseline.
- `self` — the experimental in-tree LLVM-free backend, asm-first via the host
  assembler/linker, AArch64-darwin focused. It currently passes 4054 cases
  across `c-testsuite` and GCC torture, and is now the bootstrap default on
  macOS arm64.

### Open issues (authoritative tracker)

By default (`--python-libpython=auto`, `--ir-scaffold=off`), the
bootstrap binary still **links `libpython`** because the frontend
falls through CPython for any module whose codegen still emits
`py_cpy_*`. `pcc1`/`pcc2`/`pcc3` are byte-identical (after Mach-O
signature normalization on macOS arm64) under that default.

Under the explicit flag combination
`--python-libpython=off --ir-scaffold=on`, `pcc` already produces a
`pcc1` binary that links **only `libSystem`** — the link-level
Issue 1 gate is reachable today and verified via `otool -L`. The
remaining work is *functional*: that no-libpython `pcc1` can
compile small Python programs (`print(1)`, `print(1.5)`, while/for
loops, basic functions) but cannot yet self-compile `pcc.py`. Open
self-compile bugs at the time of writing include phi-instruction
codegen for `add_incoming` (workaround in flight), 2+-argument
function-call resolution failing through a list-comprehension /
generator path inside `_resolve_call_kwargs`, and list-comp
codegen SIGSEGV at compile time.

For the full list of unresolved bootstrap / self-host issues with
data, file paths, and what hasn't been tried yet, see:

**[docs/issues/open-bootstrap-issues.md](docs/issues/open-bootstrap-issues.md)**

That document is the authoritative tracker. If anything else in
this README or in `docs/plans/` claims something is "done" that the
issues file says is open, the issues file is right.

Highest-load-bearing open issues at the time of writing:

- Issue 1 — link-level no-libpython gate **reachable** under
  `--python-libpython=off --ir-scaffold=on` (binary links only
  `libSystem.B.dylib`); functional self-compile of `pcc.py` via
  that binary still blocked by phi / call-resolution / list-comp
  codegen bugs in pcc1's emitted IR
- Issue 4 — `ir`-as-scaffold codegen subsystem in production (`--ir-scaffold=on` covers IRBuilder method dispatch, runtime\[\] desugar, and chained native arg sites)
- Issue 6 — self-backend AArch64 not yet source-anchored to upstream LLVM
- Issue 7 — x86\_64 Linux self-backend covers only ~29 tests
- Issue 8 — bootstrap not verified outside macOS arm64
- Issue 9 — closure-probe baseline locked under CI ratchet (OFF + ON, total + per-module + bridge vs non-bridge)

---

## Roadmap / next work

The bootstrap is reproducible on macOS arm64; the remaining work is sequenced
by what unblocks the most user-visible value. Each item below points at the
in-repo plan that drives it.

### Phase 1 — finish closing Issue 1 (estimated: 2–3 weeks)

The link-level gate (`pcc1` doesn't link `libpython`) is already
reachable under `--python-libpython=off --ir-scaffold=on`. The
remaining Phase 1 work is to make that binary **functionally**
re-compile `pcc.py` end-to-end:

- Fix the pcc1 self-emitted phi-instruction codegen so
  `IRBuilder.phi(...).add_incoming(...)` produces a phi with its
  incoming list intact (workaround in flight; root cause may be the
  `_block` property accessor under self-compile).
- Fix the call-argument resolution path
  (`_resolve_call_kwargs` + `_check_call_args`) so 2+-argument user
  functions don't raise spurious "missing required argument" — the
  current failure surface is consistent with list-comprehension /
  generator-expression runtime returning empty under self-compile.
- Fix list-comprehension codegen so user-level `[x for x in xs]`
  doesn't SIGSEGV at compile time.
- Flip `--ir-scaffold=auto` from off-equivalent to on-equivalent
  once the above is stable.
- See **[docs/issues/open-bootstrap-issues.md](docs/issues/open-bootstrap-issues.md)**
  for the authoritative tracker.

### Phase 2 — cross-platform reproducibility (1–2 months)

- Bring up Linux x86\_64 stage-1/2/3 byte-equality alongside macOS arm64.
- Expand the self-backend test surface on x86\_64 from ~29 cases toward
  c-testsuite + GCC torture parity.
- CI matrix runs both platforms with the closure-probe ratchet enforced.

### Phase 3 — drop the clang dependency at install time (3–6 months)

- Finish migrating C runtime helpers to pcc-Python where possible
  (`pcc/py_runtime/py/*.py` is mostly there; `dirent.h`-shaped helpers are
  blocked on the pcc C-frontend parser).
- Promote the experimental Python self-backend to default for packaging
  paths so `pip install python-cc` can build the runtime archive without
  invoking clang. System linker (`ld64` / `ld`) is still required — same
  expectation as Go / Rust / Zig.
- Ship platform wheels (`cibuildwheel`) with the runtime archive
  pre-bundled, so users on supported hosts don't need a toolchain at all.

### How does pcc preserve Python semantics without bytecode?

Short answer: bytecode is CPython's implementation detail, not a
semantic definition. pcc preserves semantics through four
independent checks — runtime contract mirroring CPython, cross-
archive byte-equal oracle, bootstrap fix-point, and a program
corpus diffed against CPython. Full discussion (with worked
examples, comparison to PyPy / Nuitka / mypyc / Cython, and the
explicit list of gaps) lives in
**[docs/issues/python-semantics-preservation.md](docs/issues/python-semantics-preservation.md)**.

### Phase 3.5 — Python data-model gaps (13–17 weeks)

- pcc's data model has known partial coverage outside GC:
  descriptor protocol (`@property` / `@classmethod` /
  `staticmethod` / `__set_name__` / `__slots__`), generators,
  `async`/`await`, full context-manager chaining, format-spec
  passthrough (`__format__`), pickle/copy, dynamic import, and
  `inspect`-style introspection. Some metaclass cases too.
- Eight-phase plan ordered by dependency, descriptor first
  because attribute lookup is the root of the data model.
- Sequencing, acceptance criteria, test-corpus design, and
  comparison with PyPy / GraalPy / MicroPython on each axis live
  in **[docs/issues/python-data-model-gaps.md](docs/issues/python-data-model-gaps.md)**.
- **[docs/issues/self-host-oracle-test-layer.md](docs/issues/self-host-oracle-test-layer.md)**
  is a prerequisite — the per-function differential test layer
  is what catches silent regressions in attribute lookup and
  protocol dispatch as those phases land.

### Phase 4 — GC semantics gap (core path 3–5 weeks; research track optional)

- pcc currently ships **refcount-only** memory management. Cycle collection,
  `__del__` finalizers, weakrefs, and atomic refcount are all stub or absent.
- **Core path (G0–G5)**: lock the gap with documenting tests (G0), then land
  the tricolor cycle collector (G1, biggest correctness win), `__del__` (G2),
  weakrefs (G3), atomic refcount (G4, gated on free-threading direction),
  native `gc` module surface (G5). Result: pcc has CPython-equivalent GC.
- **Research track (G6–G10, optional)**: region-based allocator (G6),
  concurrent marking + write barrier (G7), incremental pause budget (G8),
  colored-pointer experiments (G9), generational (G10). Borrows targets
  from Go's runtime / ZGC without inheriting their hardware dependencies
  or scale. Result: pcc becomes a viable GC research platform on top of an
  AOT Python toolchain.
- Sequencing, ZGC-port-rejection rationale, and full plan: **[docs/issues/gc-semantics-gap.md](docs/issues/gc-semantics-gap.md)**.

### Phase 5 — self-host ergonomics (ongoing)

- Make writing pcc in pcc-Python feel like writing Python: native builtin
  dispatch, recursive stdlib compilation, fewer per-helper plumbing files.
- The original three "concrete asks" (native builtin dispatch /
  pcc/stdlib/ port registry / recursive multi-file compile) have all
  shipped. Outstanding follow-ups:
  - Helper-tier framework (declarative `@dispatch` + auto ABI/dispatch
    generation) to remove the 4–5-file edit cost per new native helper.
  - `field_types`-aware isinstance narrowing for AST walkers (sequencing
    issue: needs flow-aware downstream sinks, not just type narrowing).
  - Generic `cpy → pcc` marshaller extension to remaining cpy-boundary
    sites once Issue 1 is closed.
- Driving doc: **[docs/issues/self-host-ergonomics.md](docs/issues/self-host-ergonomics.md)**.

### Phase 6 — performance positioning (deferred until Issue 1 closes)

- pcc emits native machine code, not bytecode, so typed Python should run
  closer to mypyc / Cython than to CPython. **There is no benchmark suite
  yet**, so the README does not claim performance numbers.
- When prioritised: add `bench/` covering pyperformance microbenchmarks,
  compare CPython 3.13 / Nuitka / mypyc / pcc on the same workloads,
  publish results in `BENCHMARKS.md`.
- Inline caching, small-int / single-char-str caches, and dict-key caching
  are all unimplemented today; runtime data structures are simple-and-
  correct rather than micro-optimised.

### Non-goals (called out so they don't drift in)

- A GIL-equivalent. pcc's runtime is not currently thread-safe; multi-
  threaded Python is a Phase 4 G4 / Phase 6 conversation, not an immediate
  goal.
- `freestanding` (no libc) builds. The bootstrap binary will continue to
  link libc / libSystem the same way Go / Rust / Zig binaries do; "no
  libpython" is the relevant boundary for Issue 1, not "no system C
  library".
- Bytecode emission. pcc's output is native object files via LLVM IR
  (or via the experimental self backend), not `.pyc`-style bytecode.

### Pointers

- [docs/plans/self-backend-bootstrap-default-plan.md](docs/plans/self-backend-bootstrap-default-plan.md) — promotion ladder for the self backend as the supported-host bootstrap default
- [docs/plans/python-runtime-no-c-plan.md](docs/plans/python-runtime-no-c-plan.md) — runtime self-host migration
- [docs/plans/self-backend-translation-plan.md](docs/plans/self-backend-translation-plan.md) — source-anchored backend translation plan
- [docs/plans/p6c6-bootstrap-spike-report.md](docs/plans/p6c6-bootstrap-spike-report.md) — original three-stage bootstrap spike
- [docs/plans/python-frontend-plan.md](docs/plans/python-frontend-plan.md) — Python frontend roadmap

---

## Quick start

### Install

```bash
pip install python-cc
```

The wheel build runs pcc itself (with `--backend self` by default)
to produce `libpy_runtime_pcc_py.a`, so a working install needs
only Python and the system linker (`ld64` on macOS, `ld` on Linux);
clang is **not** required on the supported host platforms. See
[`hatch_build.py`](hatch_build.py) for the build hook.

For repository development:

```bash
git clone https://github.com/jiamo/pcc
cd pcc
uv sync
```

### Three ways to use pcc

pcc surfaces the same compiler core through three usage modes. Pick
the one that matches what you have on disk.

#### Mode 1 — Python library (`import pcc`)

Compile C from inside a Python program and call into it directly,
or use the evaluator for one-off snippets. No separate compile step,
no temp files visible to the user.

```python
# Use the evaluator on a literal source string.
from pcc.evaluater.c_evaluator import CEvaluator

cc = CEvaluator()
cc.evaluate(r'''
#include <stdio.h>
int main(void) {
    printf("hello from pcc\n");
    return 0;
}
''')
print(cc.evaluate('int add(int a, int b) { return a + b; }',
                  entry="add", args=[3, 7]))  # 10
```

```python
# Or compile a C file as a callable Python module.
# arith.c:
#   int add(int a, int b) { return a + b; }
#   int mul(int a, int b) { return a * b; }
from pcc import module

m = module("arith.c")
print(m.add(3, 4))   # 7
print(m.mul(5, 6))   # 30
print(m.__pcc_artifact__.exports)
```

You can also point `module(...)` at a directory or a list of `.c`
files; pcc handles project collection, dependency resolution, and
host link.

#### Mode 2 — C compiler (`pcc x.c`)

Compile C source like you would with `cc` / `clang`. Single file,
directory, make-derived sources, separate translation units, and
host linking are all supported through one entry point.

```bash
pcc hello.c                       # compile + link an executable
pcc hello.c -o hello              # explicit output
pcc myproject/                    # whole-directory build
pcc --separate-tus myproject/     # one .o per source, then link
pcc --llvmdump hello.c            # dump LLVM IR alongside the binary
pcc myproject/ -- arg1 arg2       # forward args to the produced exe
```

The C frontend is the most mature subsystem in pcc and is what
drives the integration tests against Lua / SQLite / PostgreSQL /
nginx / GCC torture / Clang's C tests.

#### Mode 3 — Python compiler (`pcc x.py`)

Compile typed Python to a native binary. The compiled binary
embeds pcc's own runtime archive (`libpy_runtime_pcc_py.a`); on the
supported host it links neither libpython nor clang at runtime.

```bash
pcc hello.py                      # compile + link an executable
pcc hello.py -o hello
pcc hello.py --emit-llvm          # dump LLVM IR alongside the binary
pcc hello.py --backend self       # use the LLVM-free self backend
pcc hello.py --python-libpython=off
                                  # hard-fail if any module would
                                  # need a CPython fallback
```

This is the path the three-stage bootstrap exercises:
`pcc1 → pcc2 → pcc3` is `pcc` compiling its own source under Mode 3
across three nested invocations until the result is fix-pointed
byte-identical (after Mach-O signature normalization on macOS arm64).

The Python frontend is experimental — typed-native code is the
fast path, and it falls back to a CPython bridge for idioms it
doesn't yet lower natively. The `--python-libpython` flag lets you
gate that fallback explicitly:

| flag | meaning |
|---|---|
| `--python-libpython=auto` (default) | link libpython only when codegen actually needed it |
| `--python-libpython=on`              | always link libpython, even if unused |
| `--python-libpython=off`             | hard error if codegen would have needed libpython — the gate Issue 1 drives toward closing |

To produce a `pcc1` that doesn't link `libpython` at all, combine
`--python-libpython=off` with `--ir-scaffold=on`. Without
`--ir-scaffold=on`, the closed-world dispatch for `ir.Module(...)`
/ `ir.FunctionType(...)` / `self.runtime["..."]` / etc. doesn't
fire, so `pcc/py_frontend/codegen/layer1.py` and a handful of
sibling modules still emit thousands of `py_cpy_*` calls and the
gate rejects the build. Under both flags, on macOS arm64, the
emitted binary today links only `libSystem.B.dylib`.

---

## System architecture

`pcc` is organized as a layered compiler platform rather than a single monolithic script.

```text
CLI / API
  -> project collection
  -> C frontend or Python frontend
  -> pass framework
  -> LLVM optimization / emission
  -> MCJIT, object emission, or system-link execution
  -> tests / integrations / benchmarks
```

### Core layers

| Layer | Main paths | Responsibility |
|---|---|---|
| CLI and public API | `pcc/pcc.py`, `pcc/api.py` | end-user commands and embeddable build/module APIs |
| Project collection | `pcc/project.py` | collect translation units, infer source sets from make, handle dependencies |
| C frontend | `pcc/evaluater/`, `pcc/codegen/`, `pcc/parse/`, `pcc/lex/` | preprocess, parse, analyze, and lower C to LLVM IR |
| Pass framework | `pcc/passes/` | HighTier / MidTier / LowTier / BackendTier optimization plumbing |
| Python frontend | `pcc/py_frontend/`, `pcc/py_runtime/`, `pcc/extern/` | parse typed Python, infer types, emit LLVM IR, bridge to runtime or CPython |
| Validation | `tests/`, `projects/`, `bench/`, `benchmarks/` | correctness, integration, and performance coverage |

Read the full architecture guide here:

- [docs/system-architecture.md](docs/system-architecture.md)

---

## Build modes for real projects

`pcc` supports several compilation models because large C projects are not all built the same way.

| Mode | Command shape | Best for |
|---|---|---|
| Single file | `pcc hello.c` | small programs, reproducers |
| Directory merge | `pcc myproject/` | quick project experiments |
| Separate translation units | `pcc --separate-tus myproject/` | more realistic C semantics |
| Make-derived source set | `pcc --sources-from-make lua projects/lua-5.5.0` | upstream projects with real build logic |
| Driver + dependency project | `pcc --depends-on projects/pcre-8.45=libpcre.la projects/test_pcre_main.c` | library integration testing |
| Host linking | `pcc --system-link ...` | large binaries and realistic final link behavior |

---

## C frontend capabilities

The C pipeline is built on preprocessing + parsing + semantic lowering to LLVM IR.

### Highlights

- C99-oriented frontend with support for the features needed by real projects in this repo
- multi-file compilation in merged or separate-TU modes
- explicit signedness tracking on top of LLVM integer types
- compile-time constant evaluation and runtime lowering handled as separate semantic layers
- translation-unit compile cache
- object, assembly, and LLVM IR emission
- MCJIT execution for evaluator workflows and system-link workflows for larger binaries

### Public C APIs

#### `CEvaluator`

Use the compiler as an in-process evaluator for C source strings.

#### `build(...)`

```python
from pcc.api import build

artifact = build(
    ["src/main.c", "src/util.c"],
    include_dirs=["include"],
    libs=["m"],
    optimize=2,
    kind="exe",
)
print(artifact.output_path)
```

#### `module(...)`

Compile one or more C files into a shared library and load it with `ctypes`.

```python
from pcc import module

m = module(["src/a.c", "src/b.c"], include_dirs=["include"], libs=["z"])
print(m.__pcc_artifact__.pass_report)
```

---

## Python frontend

`pcc hello.py` uses an experimental Python frontend that lowers Python through LLVM as well.

### What works today

- typed Python lowered directly to native LLVM IR
- no PyObject layer for pure typed/native programs
- CPython C-API fallback when `import` is used
- direct C calls through `pcc.extern`
- low-level intrinsics through `pcc.unsafe` (compiler-recognized
  `malloc`/`load`/`store`/`memcpy`/`getenv`/global address forms/tagged-int
  packing/...): used to author the no-libpython runtime in pcc-Python itself
- boxed-int representation for user code so values like `2 ** 100` produce a
  real bignum instead of overflowing
- classes, exceptions, dunders, and selected stdlib coverage in the current corpus

### Python path selection

For `.py` inputs, the main CLI surface is intentionally small. The Python path
dispatch happens before most of the C-specific validation, so flags such as
`--jobs`, `--separate-tus`, `--target`, and `--system-link` do not currently
change Python compilation behavior. `--backend` (and `PCC_BACKEND`) *is*
honored on the Python path: the value is threaded through `pcc.cli_core`
and `pcc.cli_bootstrap` into native emission, so `--backend=self` routes
the produced `.ll` through the self backend instead of llvmlite.

#### Main `pcc foo.py` controls

| Surface | Choices | Effect | Notes |
|---|---|---|---|
| invocation mode | `pcc foo.py` | compile to a temporary native executable and run it immediately | arguments after `--` are forwarded to the produced program |
| output mode | `pcc foo.py -o prog` | compile and save a native executable to `prog` | does not auto-run after build |
| IR mode | `pcc foo.py --emit-llvm` | emit LLVM IR only and stop before linking | bare form writes `<stem>.ll`; `-o` can override the output path |
| logging | `pcc foo.py --verbose` | print parse / type inference / codegen / link timings | Python pipeline only |

#### Automatic routing inside the Python pipeline

| Trigger | Route selected | Result |
|---|---|---|
| no `import` and the code stays in the typed/native subset | typed-native path | lowers directly to LLVM IR and can stay libpython-free |
| any `import` is present | CPython fallback path | links `libpython` and routes imported values through CPython C-API shims |
| default parser configuration | native parser + lift | uses `pcc.parse.py_parse` + `pcc.parse.py_lift` |

#### Internal / debug toggles for `.py`

| Env var | Choices | Effect |
|---|---|---|
| `PCC_USE_CPYTHON_AST` | unset / `1` | when set to `1`, opts out of the native Python parser and uses the legacy stdlib-`ast` parser path |
| `PCC_USE_LLVMLITE` | unset / `1` | when set to `1`, forces all subsystems, including Python codegen, back to `llvmlite` |
| `PCC_USE_LLVMLITE_PY` | unset / `1` | when set to `1`, forces only the Python frontend codegen path back to `llvmlite` |

#### Experimental multi-file Python entry

| Surface | Choices | Effect | Notes |
|---|---|---|---|
| command | `python scripts/pcc_multi.py` | compile several `.py` files into one native output | separate from the main `pcc` CLI |
| required flags | `--entry`, `--out` | choose the entry module and output path | `--entry` uses dotted module names |
| optional flags | `--emit-llvm`, `--verbose` | emit combined LLVM IR or print timings | mirrors the single-file Python pipeline options |
| source mapping syntax | `path.py` or `path.py=module.name` | lets callers assign explicit dotted module names | useful for `__main__.py`, `__init__.py`, and relative imports |
| current limitation | unresolved imports / pure self-host closure | known native sibling function/class imports are supported, and the repository bootstrap script now completes stage 1/2/3 on the supported macOS arm64 dev host; unresolved imports may still fall back to the CPython import path and pull `libpython`, so the pure self-host boundary is still open | bootstrap packaging and dependency removal work is still in progress |

#### Related: `.c` path environment controls

For `.c` inputs, the main execution mode is still selected by CLI flags such as
`--separate-tus`, `--system-link`, `--sources-from-make`, `--depends-on`,
`--target`, `--emit-obj`, `--emit-asm`, and `--emit-llvm`. Environment
variables mainly affect backend selection, parser choice, caching, LLVM
pipeline behavior, and diagnostics.

#### Default component selection with no env vars

When you do **not** set any `PCC_*` environment variables, the current
system pipeline is a mixed default:

| Layer | Default selection | Notes |
|---|---|---|
| C parser | native `pcc.parse.CParseDriver` | `PCC_USE_PLY_C_PARSER=1` reverts to the legacy PLY parser |
| Python parser | native `pcc.parse.py_parse` + `pcc.parse.py_lift` | `PCC_USE_CPYTHON_AST=1` reverts to the legacy stdlib-`ast` path |
| C IR builder / type layer | `pcc.llvm_capi.compat.ir_c` routed to `pcc.llvm_capi` | `PCC_USE_LLVMLITE=1` or `PCC_USE_LLVMLITE_C=1` forces this layer back to `llvmlite` |
| Python IR builder | `pcc.llvm_capi.compat.ir_py` routed to `pcc.llvm_capi` | `PCC_USE_LLVMLITE=1` or `PCC_USE_LLVMLITE_PY=1` forces this layer back to `llvmlite` |
| CLI/backend identity | `llvm` | this is the default value behind `--backend` / `PCC_BACKEND` |
| Run / emit-object / MCJIT path | current llvmlite-backed evaluator pipeline | `self` is the only backend that currently switches to a different emit/run implementation |
| External LLVM text pipeline | off | only enabled when `PCC_LLVM_PIPELINE` or explicit LLVM pass selection requests it |

In other words, a zero-env run currently defaults to native parsers and
native `pcc.llvm_capi` IR construction where the compat shim is wired
in, but still uses the current `llvm` execution/object-emission path
unless you explicitly select a different backend.

Common controls:

| Env var | Effect | Notes |
|---|---|---|
| `PCC_BACKEND` | choose the C backend (`llvm`, `llvm_capi`, `self`) | same surface as `--backend`; default is `llvm` |
| `PCC_USE_PLY_C_PARSER=1` | opt out of the native C parser and use the legacy PLY parser | parser compatibility / regression isolation |
| `PCC_PLY_CACHE_DIR` | override the PLY lextab / yacctab cache directory | only matters on the legacy PLY parser path |
| `PCC_COMPILE_CACHE_DIR` | override the translation-unit compile cache directory | defaults under `~/.cache` or `XDG_CACHE_HOME` |
| `PCC_DISABLE_COMPILE_CACHE=1` | disable the translation-unit compile cache | useful for debugging cache-key issues |
| `PCC_DISABLE_PASSES` | disable named managed passes | comma-separated pass names |
| `PCC_LLVM_DISABLE_PASSES` | disable named concrete LLVM passes | comma-separated pass names |
| `PCC_CHEAP_LLVM_PIPELINE` | enable the cheap LLVM pass bundle, or provide a custom cheap-pass list | affects the low-opt / O0-style backend path |
| `PCC_LLVM_PIPELINE` | run an external text LLVM pipeline | `1`, `true`, or `default` selects the default pipeline; custom specs are also accepted |
| `PCC_LLVM_OPT_BIN` | point at a matching LLVM `opt` binary | required when using the external LLVM text pipeline or LLVM pass selection that needs `opt` |
| `PCC_LIBLLVM_PATH` | point at `libLLVM-C` explicitly | used by the native LLVM-C binding path |
| `PCC_USE_LLVMLITE=1` | force all subsystems back to `llvmlite` | reverse-opt-out from the native LLVM-C path |
| `PCC_USE_LLVMLITE_C=1` | force only the C codegen path back to `llvmlite` | useful for C-only regression isolation |
| `PCC_USE_LLVMLITE_PASSES=1` | force only the pass layer back to `llvmlite` | useful for pass-only regression isolation |

Diagnostics:

| Env var | Effect | Notes |
|---|---|---|
| `PCC_DUMP_BAD_IR=/path` | dump invalid or unparsable LLVM IR to disk when LLVM parsing fails | writes per-TU `.ll` snapshots for inspection |
| `PCC_DEBUG_PHI_TYPES=/path` | append SSA phi type-mismatch diagnostics to a log file | supports parallel builds by appending |
| `PCC_DEBUG_SSA_LOWER_FAIL=1` | print traceback when SSA lowering fails and falls back | diagnostic only; does not change correctness behavior |

### Example: typed-native Python

```python
def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

def main() -> None:
    for i in range(10):
        print(fib(i))

main()
```

### Example: pure native FFI with `pcc.extern`

```python
from pcc.extern import extern, c_int

getpid = extern("getpid", (), c_int)

pid: int = getpid()
print(pid)
```

### Python corpus status

`tests/py_corpus/` now ships **177** end-to-end programs. The corpus is run
in two runtime modes — the default no-libpython archive and
`PCC_RUNTIME_HIGH=py` — and the majority pass in both. Recent recorded
checkpoints from the runtime self-host plan:

| Phase | Cases | Coverage |
|---|---:|---|
| phase1 | 30 | typed Python MVP |
| phase2 | 57 | core Python data semantics; 53/53 attempted pass under `PCC_RUNTIME_HIGH=py` |
| phase3 | 49 | classes, MRO, dunders, exceptions; 49/49 pass under `PCC_RUNTIME_HIGH=py` |
| phase4 | 39 | CPython fallback path; 39/39 pass under `PCC_RUNTIME_HIGH=py` |
| phase6c | 2 | extern-C direct calls |

A few phase-1/phase-2 programs are still gated on language-feature work; see
[docs/plans/python-runtime-no-c-plan.md](docs/plans/python-runtime-no-c-plan.md)
for the live counts.

Related docs:

- [docs/python-tutorial.md](docs/python-tutorial.md)
- [docs/python-howto.md](docs/python-howto.md)
- [docs/python-limitations.md](docs/python-limitations.md)
- [docs/python-scorecard.md](docs/python-scorecard.md)
- [docs/changelog.md](docs/changelog.md)

---

## Real-world integration coverage

One of `pcc`'s strengths is that correctness work is validated against real software, not just toy examples.

| Integration | Representative path | Typical workflow |
|---|---|---|
| Lua 5.5.0 | `projects/lua-5.5.0/` | single-file amalgamation or make-derived source list |
| PCRE 8.45 | `projects/pcre-8.45/` + `projects/test_pcre_main.c` | driver + dependency-project build |
| zlib 1.3.1 | `projects/zlib-1.3.1/` + `projects/test_zlib_main.c` | make-derived dependency build |
| SQLite 3.49.1 | `projects/sqlite-amalgamation-3490100/sqlite3.c` + `projects/test_sqlite_main.c` | amalgamation + driver |
| PostgreSQL 17.4 `libpq` | `projects/postgresql-17.4/` + `projects/test_postgres_main.c` | make-goal discovery + support archives |
| nginx 1.28.3 | `projects/nginx-1.28.3/` | compile all project sources and system-link |
| Other libraries | `tests/test_lz4.py`, `tests/test_zstd.py`, `tests/test_openssl.py`, `tests/test_readline.py` | focused integration suites |

### Representative commands

#### Lua 5.5.0

```bash
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua
```

```bash
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  --separate-tus --sources-from-make lua --jobs 2 \
  projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
```

#### PCRE 8.45

```bash
uv run pcc \
  --cpp-arg=-DHAVE_CONFIG_H \
  --depends-on projects/pcre-8.45=libpcre.la \
  projects/test_pcre_main.c
```

#### zlib 1.3.1

```bash
uv run pcc \
  --cpp-arg=-DHAVE_UNISTD_H \
  --cpp-arg=-DHAVE_STDARG_H \
  --cpp-arg=-U__ARM_FEATURE_CRC32 \
  --depends-on projects/zlib-1.3.1=libz.a \
  projects/test_zlib_main.c
```

#### SQLite 3.49.1

```bash
uv run pcc \
  --cpp-arg=-U__APPLE__ \
  --cpp-arg=-U__MACH__ \
  --cpp-arg=-U__DARWIN__ \
  --cpp-arg=-DSQLITE_THREADSAFE=0 \
  --cpp-arg=-DSQLITE_OMIT_WAL=1 \
  --cpp-arg=-DSQLITE_MAX_MMAP_SIZE=0 \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c /tmp/pcc_sqlite.db
```

#### PostgreSQL 17.4 `libpq`

```bash
uv run pcc --system-link --jobs 2 \
  --depends-on projects/postgresql-17.4/src/interfaces/libpq=libpq.a \
  --depends-on projects/zlib-1.3.1=libz.a \
  --link-arg=projects/postgresql-17.4/src/common/libpgcommon_shlib.a \
  --link-arg=projects/postgresql-17.4/src/port/libpgport_shlib.a \
  --link-arg=-lm \
  projects/test_postgres_main.c
```

#### nginx 1.28.3

```bash
cd projects/nginx-1.28.3 && ./configure --with-cc-opt=-Wno-error && cd ../..
uv run pytest tests/test_nginx.py -v
```

---

## Performance and optimization

`pcc` keeps a substantial amount of optimization and benchmark infrastructure in-tree.

### Benchmark harnesses

- `bench/bench.py` — 80-case microbenchmark matrix, pass ablations, clean exec-only summaries
- `benchmarks/run_benchmarks.py` — 46 standalone C programs, compile/exec/total timings
- `benchmarks/quantify_passes.py` — aggregate pass-cost attribution

### Current headline numbers

As documented in the benchmark sections of the repo, recent one-run macOS results include:

- **80-case microbenchmark**, `pcc -O2` vs `clang -O2`: compile `1.12x`, exec `1.00x`, total `1.08x`, with `78/80` matched and clean
- **46-file standalone suite**, `pcc/clang` geomeans at `O2`: compile `3.53x`, exec `1.00x`, total `2.00x`
- **pcc-only O2/O0** on the 46-file suite: compile `1.02x`, exec `0.41x`, total `0.71x`

Interpretation:

- `pcc` is already near `clang` at runtime on the microbenchmark suite once LLVM `-O2` is enabled
- compile-time cost is still meaningfully higher on the standalone suite
- the pass framework is measured separately from LLVM backend optimization rather than being conflated with it

### Reproduce benchmark runs

```bash
uv run python bench/bench.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1 --top-passes 12
uv run python bench/bench.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1 --group-matrix
uv run python bench/bench.py --opt-level 0 --opt-level 2 --runs 1 --top-passes 12
uv run python benchmarks/run_benchmarks.py --opt-level 0 --opt-level 2 --runs 1
uv run python benchmarks/run_benchmarks.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1
uv run python benchmarks/quantify_passes.py --top 12
```

---

## Testing and quality gates

`pcc` is validated with both focused regressions and large external suites.

### Major suites

| Suite | Scale | Purpose |
|---|---:|---|
| `tests/test_c_testsuite.py` | 220 cases | C conformance-style corpus |
| `tests/test_clang_c.py` | 161 cases | Clang-derived C coverage |
| `tests/test_gcc_torture_execute.py` | 1684 cases | GCC torture runtime stress |
| `tests/test_lua.py` | 130+ Lua scripts / modes | real interpreter integration |
| `tests/test_sqlite.py`, `tests/test_postgres.py`, `tests/test_nginx.py` | project-scale | large software validation |
| `tests/py_corpus/` | 139 programs | Python frontend end-to-end corpus |

### Common commands

```bash
uv run pytest                # default suite (excludes expensive integration tests)
uv run pytest -m integration # expensive end-to-end integration suite
uv run pytest tests/test_lua.py -q -n0
uv run pytest tests/test_sqlite.py -q -n0
uv run pytest tests/test_postgres.py -q -n0
uv run pytest tests/test_nginx.py -q -n0
```

---

## Compile cache

`pcc` keeps a translation-unit compile cache on disk by default.

### CLI

```bash
uv run pcc hello.c
uv run pcc --cache-dir .pcc-cache hello.c
uv run pcc --no-cache hello.c
```

### Library usage

```python
from pcc.evaluater.c_evaluator import CEvaluator

ev = CEvaluator()
ev.evaluate("int main(void) { return 0; }\n")
ev.evaluate("int main(void) { return 0; }\n")  # cache hit
```

### Cache characteristics

- keyed from source/preprocess context and compiler fingerprint inputs
- reused across evaluator runs, translation-unit compilation, and CLI workflows
- useful for repeated large-project iteration
- controlled via `--cache-dir`, `--no-cache`, and `PCC_COMPILE_CACHE_DIR`

---

## Documentation map

| Topic | Path |
|---|---|
| System architecture | [docs/system-architecture.md](docs/system-architecture.md) |
| Python tutorial | [docs/python-tutorial.md](docs/python-tutorial.md) |
| Python how-to | [docs/python-howto.md](docs/python-howto.md) |
| Python limitations | [docs/python-limitations.md](docs/python-limitations.md) |
| Python scorecard | [docs/python-scorecard.md](docs/python-scorecard.md) |
| Changelog | [docs/changelog.md](docs/changelog.md) |
| Investigation reports | [docs/investigations/](docs/investigations/) |
| Design plans | [docs/plans/](docs/plans/) |
| Contributor/agent notes | [AGENTS.md](AGENTS.md) |

Recommended deep-dive investigation docs:

- [docs/investigations/lua-sort-random-pivot-signedness.md](docs/investigations/lua-sort-random-pivot-signedness.md)
- [docs/investigations/pcre-op-lengths-incomplete-array-binding.md](docs/investigations/pcre-op-lengths-incomplete-array-binding.md)
- [docs/investigations/zlib-integration-static-local-arrays-and-layout.md](docs/investigations/zlib-integration-static-local-arrays-and-layout.md)
- [docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md](docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md)
- [docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md](docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md)
- [docs/investigations/nbody-shootout-fp-contract-and-vectorization.md](docs/investigations/nbody-shootout-fp-contract-and-vectorization.md)

---

## Repository map

| Path | Role |
|---|---|
| `pcc/pcc.py` | CLI entrypoint |
| `pcc/api.py` | build/module APIs |
| `pcc/project.py` | source collection and build orchestration |
| `pcc/evaluater/c_evaluator.py` | C compilation/execution coordinator |
| `pcc/codegen/c_codegen.py` | C semantic lowering |
| `pcc/passes/` | optimization framework |
| `pcc/py_frontend/` | Python frontend |
| `pcc/py_runtime/` | Python runtime archive (`src/*.c` and `py/*.py` siblings; pcc-Python ports replace selected `.o` entries in `libpy_runtime_pcc_py.a`) |
| `pcc/extern/` | extern-C bridge |
| `pcc/unsafe/` | compiler-recognized low-level intrinsics consumed by runtime authoring |
| `utils/fake_libc_include/` | fake libc headers |
| `tests/` | correctness and integration suites |
| `projects/` | third-party software used as stress targets |
| `bench/`, `benchmarks/` | performance tooling |

---

## Supported C feature set

`pcc` supports the C features needed by the real-world integrations in this repo, including:

- scalar types, pointers, arrays, structs, unions, enums, typedefs, and function pointers
- arithmetic, comparison, casts, bitwise ops, shifts, and control flow
- variadic functions
- preprocessing with macro expansion and conditional compilation
- multi-file builds and project-style source collection

The practical standard here is not “can it parse a feature in isolation”, but “can it preserve the right semantics once the code is lowered to LLVM IR and exercised by real software”.

---

## Development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run pytest -m integration
```

If you are contributing compiler changes, read [AGENTS.md](AGENTS.md) first. It documents the repository's debugging playbook, testing policy, C signedness model, project workflows, and definition of done.

---

## License

MIT. See [LICENSE](LICENSE).
