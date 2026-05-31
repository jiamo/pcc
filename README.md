# pcc

[![PyPI](https://img.shields.io/pypi/v/python-cc)](https://pypi.org/project/python-cc/)
[![Python](https://img.shields.io/pypi/pyversions/python-cc)](https://pypi.org/project/python-cc/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`pcc` is a Python-authored compiler toolchain. Its most mature path is a C
frontend that lowers C to LLVM IR and runs real third-party projects. The
repository also contains an experimental typed-Python frontend, a Python runtime
being re-authored in pcc-Python, and an in-tree experimental backend that emits
native code without LLVM for selected targets.

This is a research compiler with practical integration tests, not a drop-in
replacement for Clang or CPython.

The Python-side strategy is compatibility first, then specialization:
preserve CPython semantics where supported, fail loudly where strict native
mode cannot prove them yet, and seek performance wins through typed native
lowering, value payloads, array/shape specialization, startup, GC, and
threading work. "Fully compatible with Python and faster in every scenario" is
not a credible current claim. Dynamic, reflection-heavy, or CPython-extension
heavy code must be made correct and diagnosable before it can be optimized.

## Status

| Area | Current state |
|---|---|
| C frontend | Mature relative to the rest of the repo; validated through C tests, GCC/Clang-derived suites, and real projects such as Lua, SQLite, PostgreSQL `libpq`, zlib, lz4, zstd, PCRE, OpenSSL, readline, and nginx. |
| Python frontend | Experimental. Typed code can lower to native IR; unsupported Python idioms fail by default and only route through the CPython bridge when `--python-libpython=auto/on` is explicit. |
| Runtime | Active migration from C runtime sources to pcc-Python modules under `pcc/py_runtime/py/`, using `pcc.unsafe` and `pcc.extern` for low-level operations. |
| Self backend | Experimental LLVM-free emission path for AArch64 Darwin and x86_64 Linux subsets. It is used by bootstrap/build gates, but the default public backend remains LLVM unless explicitly selected. |
| Bootstrap | macOS arm64 three-stage bootstrap completes as `pcc1 -> pcc2 -> pcc3` in both the default path and the strict self-backend path (`--backend self --python-libpython=off --ir-scaffold=on`). In the strict path, the emitted IR for `pcc2` and `pcc3` is byte-identical with 0 `py_cpy_*` calls; linked binaries have no `libpython` dependency and are byte-identical after Mach-O signature normalization. Issue 1 (strict no-libpython bootstrap) closed 2026-05-01; the authoritative current snapshot is [`tests/bootstrap_gate_baseline.json`](tests/bootstrap_gate_baseline.json). |
| pcc1 C frontend | `pcc1` can act as a driver for C inputs today by delegating `.c` files, C project directories, and C-only flags to the full host `pcc` CLI. That is a compatibility shell, not yet `pcc1` natively executing `pcc/pcc.py`, `cli_core.py`, `project.py`, `c_evaluator.py`, `c_codegen.py`, or `pycparser`. |
| Tests | Broad unit, corpus, self-backend, and integration coverage. The Python corpus contains about 180 end-to-end programs. |

The authoritative current bootstrap status is
[`tests/bootstrap_gate_baseline.json`](tests/bootstrap_gate_baseline.json).
[docs/issues/open-bootstrap-issues.md](docs/issues/open-bootstrap-issues.md) is a historical
tracker only and may lag behind the JSON baseline.

## Current Work State

This README is the public overview. The active goal and task board live in
[`codex-goal-prompt.md`](codex-goal-prompt.md) and
[`docs/current-goal-state.md`](docs/current-goal-state.md). Maintainer and agent
workflow rules live in [`AGENTS.md`](AGENTS.md).

## Install

```bash
pip install python-cc
```

For repository development:

```bash
git clone https://github.com/jiamo/pcc
cd pcc
uv sync
```

The package requires Python 3.13+. Source builds may build the Python runtime
archive at wheel time through `hatch_build.py`. That hook prefers the self
backend and falls back to LLVM if needed; a missing prebuilt archive can also be
rebuilt lazily on first use.

## Quick Start

### Compile C

```bash
pcc hello.c
pcc hello.c -o hello
pcc hello.c -- arg1 arg2
```

Common C project modes:

```bash
pcc myproject/
pcc --separate-tus myproject/
pcc --sources-from-make lua projects/lua-5.5.0
pcc --system-link --link-arg=-lm mathprog.c
pcc --emit-llvm out.ll hello.c
pcc --emit-obj out.o --target x86_64-unknown-linux-gnu hello.c
```

### Compile Python

```bash
pcc hello.py
pcc hello.py -o hello
pcc hello.py --emit-llvm
pcc hello.py --python-libpython=auto
pcc hello.py --backend self
```

`pcc hello.py` compiles the file to a native executable and runs it. Passing
`-o` writes the executable without running it. Passing `--emit-llvm` stops after
IR generation. Python inputs default to the strict no-libpython path:
`--python-libpython=off --ir-scaffold=on`. Use `--python-libpython=auto` only
when you intentionally want the experimental CPython fallback bridge.

The normal package install provides the `pcc` command:

```bash
pip install python-cc
pcc hello.py
```

There is no separate `python-cc[no-libpython]` extra; it would install the same
files without changing runtime behavior. No-libpython is the default.

For Python inputs, the most important controls are:

| Option | Meaning |
|---|---|
| `--python-libpython=off` | Default. Hard error if the program would need a CPython fallback. |
| `--python-libpython=auto` | Compatibility mode. Link `libpython` only if codegen needed a CPython fallback. |
| `--python-libpython=on` | Always allow/link the CPython fallback surface. |
| `--ir-scaffold=on` | Default. Closed-world lowering used by the strict self-host work. |
| `--ir-scaffold=off` | Compatibility escape hatch for the older Python lowering path. |
| `--backend {llvm,llvm_capi,self}` | Select the backend. `llvm` is the public default; `self` is experimental. |

### Use pcc From Python

The public Python API is for C compilation.

```python
from pcc.evaluater.c_evaluator import CEvaluator

ev = CEvaluator()
print(ev.evaluate(
    "int add(int a, int b) { return a + b; }",
    entry="add",
    args=[3, 7],
))
```

```python
from pcc import build, module

artifact = build(["src/main.c", "src/util.c"], include_dirs=["include"])
print(artifact.output_path)

m = module("arith.c")
print(m.add(3, 4))
print(m.__pcc_artifact__.exports)
```

## Architecture

```text
CLI / Python API
  -> project collection
  -> C frontend or Python frontend
  -> optimization / lowering passes
  -> LLVM, LLVM-C compatibility, or self backend
  -> MCJIT, object emission, system link, or native executable
```

| Layer | Main paths | Role |
|---|---|---|
| CLI | `pcc/cli_core.py`, `pcc/pcc.py`, `pcc/cli_bootstrap.py` | User command line, bootstrap CLI, option routing. |
| Public API | `pcc/api.py`, `pcc/evaluater/c_evaluator.py` | Embeddable C build/evaluate/module APIs. |
| Project collection | `pcc/project.py` | Directory scanning, make-derived source sets, dependency projects, translation-unit setup. |
| C frontend | `pcc/lex/`, `pcc/parse/`, `pcc/codegen/`, `pcc/evaluater/` | C preprocessing, parsing, semantic lowering, execution/emission. |
| Python frontend | `pcc/py_frontend/`, `pcc/parse/py_*` | Python parse/lift, type inference, native lowering, CPython fallback decisions. |
| Runtime | `pcc/py_runtime/`, `pcc/extern/`, `pcc/unsafe/` | Runtime objects, extern-C bridge, compiler-recognized low-level intrinsics. |
| Backends | `pcc/llvm_capi/`, `pcc/backend/` | LLVM compatibility layer and experimental self backend. |
| Tests/integrations | `tests/`, `projects/`, `benchmarks/` | Regression, corpus, real-project, and performance coverage. |

See [AGENTS.md](AGENTS.md) for the full repository map and maintainer workflow.
Some older docs predate the no-libpython default; use
[docs/current-goal-state.md](docs/current-goal-state.md) for current status.

## Capabilities

### C Frontend

The C pipeline is the production-quality part of the repository. It supports:

- C99-oriented source parsing and semantic lowering.
- Scalars, pointers, arrays, structs, unions, enums, typedefs, function
  pointers, control flow, casts, arithmetic, bitwise operations, shifts, and
  variadic functions.
- Preprocessing with macro expansion and conditional compilation.
- Merged-directory builds, separate translation units, make-derived source
  selection, dependency projects, compile caching, and host-system linking.
- LLVM IR, object, assembly, MCJIT, and executable workflows.
- Explicit signedness tracking on top of LLVM integer types, including
  compile-time constant evaluation and runtime lowering as separate semantic
  paths.

Representative C integration commands:

```bash
env -u LC_ALL uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua
```

```bash
env -u LC_ALL uv run pcc \
  --cpp-arg=-DHAVE_CONFIG_H \
  --depends-on projects/pcre-8.45=libpcre.la \
  projects/test_pcre_main.c
```

### Python Frontend

The Python frontend is intentionally described as experimental. It is useful for
typed-native programs, runtime-authoring work, and the self-host track, but it
does not implement the full Python data model.

The self-host path is stricter than normal user Python code. `pcc`'s own source
still has to avoid or isolate many dynamic idioms: runtime `getattr` /
`setattr`, string-keyed method dispatch, broad `dict[str, Any]` plumbing,
generators, decorators with runtime effects, deep closure capture, dynamic
imports, and other features outside the native lowering subset. That restriction
is a real current limitation of the bootstrap track, not just a documentation
gap.

Supported or actively exercised areas include:

- Typed functions and local variables lowered to native LLVM IR.
- Native `int`, `bool`, `float`, `str`, `list`, `tuple`, `dict`, `set`, class,
  exception, dunder, and selected stdlib/runtime paths in the corpus.
- Direct C interop through `pcc.extern`.
- Low-level runtime authoring through `pcc.unsafe`.
- Explicit CPython fallback (`--python-libpython=auto/on`) for imports and
  dynamic idioms that are not yet in the native subset.
- Multi-file/bootstrap compilation through `scripts/pcc_multi.py` and
  `pcc/cli_bootstrap.py`.

The core limitation is not parsing; it is preserving Python semantics without
falling back to CPython. The default Python gate is:

```bash
pcc program.py
```

That mode still fails on programs that need an unsupported CPython bridge.
Use `pcc program.py --python-libpython=auto` for compatibility experiments,
not for no-libpython claims. The bootstrap and fallback ratchets live in
[tests/fallback_baseline.json](tests/fallback_baseline.json) and
[tests/bootstrap_gate_baseline.json](tests/bootstrap_gate_baseline.json).

The current long-term Python compatibility roadmap is tracked in
[docs/plans/python-compat-specialization-strategy.md](docs/plans/python-compat-specialization-strategy.md)
and [docs/plans/numpy_plan.md](docs/plans/numpy_plan.md). The strategy keeps
three workstreams separate: CPython/package compatibility, no-libpython
self-backend evidence, and specialization performance. A result in one
workstream must not be reported as completion in another.

The NumPy plan treats NumPy as both an extension-ABI project and a broader
pcc1 Python-library compatibility project. Its Track B explicitly targets
no-libpython support for real-library surfaces such as `inspect`, docstring
metadata, `re`, module introspection, dynamic method discovery, heavy container
mutation, file/cache I/O, and `importlib`-style behavior. These are goals and
gated milestones, not current blanket support claims. Current local NumPy 2.4.4
L5 smoke evidence is `cpython-compat` package/build/import evidence: strict
pcc-native no-libpython correctly rejects CPython ABI extension artifacts with
`PCC-PKG-004` until the generic pcc-native NumPy C-API/extension ABI work lands.
The focused rejection gate is
[`tests/python/test_package_extension_abi.py`](tests/python/test_package_extension_abi.py),
and the active package/import work is tracked in
[`codex-goal-prompt.md`](codex-goal-prompt.md) and
[`docs/current-goal-state.md`](docs/current-goal-state.md). That blocker is
intentional; it prevents a CPython ABI artifact from being misreported as
pcc-native NumPy support. Array-core checks, `pip install` progress, extension
ABI acceptance, and `import numpy` are separate claims.

For C inputs, `pcc1` currently has two distinct modes:

- **Driver/delegation mode**: `.c` inputs, C directories, and C-specific
  flags are forwarded to the host `pcc` command. This lets pcc1 launch C
  tests and project-shaped C commands while preserving the strict Python
  self-host closure.
- **Native C frontend target**: future work will make pcc1 execute the C
  frontend library closure itself with `--python-libpython=off`. That is
  not complete today; follow the current roadmap in
  [`codex-goal-prompt.md`](codex-goal-prompt.md) and
  [`docs/current-goal-state.md`](docs/current-goal-state.md).

### Self Backend

The self backend is the in-tree LLVM-free emitter. It currently targets selected
AArch64 Darwin and x86_64 Linux IR shapes and is validated against LLVM-backed
output through dedicated tests. Use it explicitly:

```bash
pcc --backend self hello.c
pcc --backend self --target x86_64-unknown-linux-gnu --emit-obj out.o hello.c
pcc hello.py --backend self
```

The self backend is not yet the universal default. It is the default in the
macOS arm64 bootstrap script and in the runtime wheel-build hook where supported.

Implementation note: the self backend and pass framework were developed with
AI assistance from LLVM's published behavior and IR semantics. They are tested
against the LLVM-backed path; they are not a source-code port of LLVM.

## Bootstrap

The supported macOS arm64 bootstrap flow is:

```text
CPython runs pcc -> pcc1
pcc1 compiles pcc -> pcc2
pcc2 compiles pcc -> pcc3
compare pcc2 and pcc3 after Mach-O signature normalization
```

Run it with:

```bash
scripts/bootstrap.sh
scripts/bootstrap.sh --backend llvm
scripts/bootstrap.sh --backend self
scripts/bootstrap.sh --stage 1
```

After a stage1 binary exists, it can launch the repository pytest suite
itself:

```bash
./build/bootstrap/pcc1 --pytest tests -q -n0
```

The launcher delegates to `env -u LC_ALL uv run pytest ...` and sets
`PCC1_BINARY` to the running `pcc1`, so pcc1-specific pytest cases use
that same binary.

The strict no-libpython macOS arm64 path has also been verified to complete:

```bash
pcc --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc1
./pcc1 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc2
./pcc2 --ir-scaffold=on --python-libpython=off --backend self pcc/__main__.py -o pcc3
```

As of 2026-05-01 (Issue 1 closure), this strict path verifies with 0 `py_cpy_*`
calls in the `pcc2`/`pcc3` emitted IR, no `libpython` entry in `otool -L`,
byte-identical `pcc2`/`pcc3` emitted IR, and byte-identical `pcc2`/`pcc3`
binaries after Mach-O signature removal. The frozen evidence lives in
[`tests/bootstrap_gate_baseline.json`](tests/bootstrap_gate_baseline.json)
and is enforced by `tests/python/test_bootstrap_gate_baseline.py`. The public
default CLI still uses the LLVM backend, but Python inputs now default to
no-libpython with the IR scaffold enabled.

## GC Status

pcc's runtime ships five GC backend slots, each mirroring a real
reference implementation kept in tree under
[docs/refs_docs/gc-research/](docs/refs_docs/gc-research/) so the algorithm can be
read alongside pcc's port. The current default decision is recorded in
[docs/investigations/gc-backend-selection-matrix.md](docs/investigations/gc-backend-selection-matrix.md):
backend #0 remains the default; #1 and #3 are the nearest future default
challengers, while #2 and #4 remain selectable advanced backends.

| Slot | Algorithm | Reference | Status in pcc |
|---|---|---|---|
| **#0** | refcount + STW cycle | CPython (`docs/refs_docs/gc-research/python/`) | **Production / default.** Broadest bootstrap and language coverage; keep as rollback/reference path for all other backend work. |
| **#1** | incremental tricolor mark-sweep | Lua 5.4 (`docs/refs_docs/gc-research/lua/`) | **Selectable and gated.** Current pcc1 explicit-collection and runtime-subset gates are green. Remaining work is production hardening: pacer/debt tuning, finalizer/resurrection audit, and broader workload coverage. |
| **#2** | concurrent mark-sweep | Go (greentea, `docs/refs_docs/gc-research/go-greentea/`) | **Selectable and gated for the current threaded subset.** CMS worker, bounded queue, mutator assist, lifecycle, and TSan/stress slices are green. Remaining work is a fuller Go-style work-buffer/drain model and concurrent sweep policy. |
| **#3** | generational young/old | OCaml (`docs/refs_docs/gc-research/ocaml/`) | **Selectable and production-facing on focused gates.** Minor arena, remembered-set promotion, copy-oldification, owned-slot rewrite, frame/scheduler roots, and class metadata slot rewrite exist in C and pcc-Python paths. Remaining work is cross-domain/threaded object-graph proof and workload-level performance data. |
| **#4** | colored relocating / modern GenZGC | ZGC (`docs/refs_docs/gc-research/zgc/`, OpenJDK `jdk-27+21`; freshness checked against OpenJDK `master` on 2026-05-14) | **Selectable and production-facing on focused gates, not default.** Forwarding/read barriers, container relocation, scheduler-root healing, store-buffer telemetry, page-class telemetry, and first large-page policy slices exist. Remaining work is true ZPage allocation/evacuation, full GenZGC young/old policy, fragmentation policy, native-handle protocols, pcc-Python threaded mirror flushing, and full reference updating. |

Known semantic gaps versus CPython on backend #0 (production path):

- Cycle collector runs (`py_obj_gc.c::py_gc_collect` walks tracked objects,
  recomputes reachability, finalizes, clears referents, and frees), but is
  not yet auto-paced — `gc.collect()` is the only trigger; allocation-debt
  pacing comparable to CPython's generational thresholds is still pending.
- `__del__` is dispatched (`py_class.c::py_instance_dealloc` calls
  `py_user_del_dispatch`), but resurrection and per-finalizer
  exception-as-warning policy are minimal compared with CPython.
- `weakref` is implemented (`py_weakref.c`: `py_weakref_new`,
  `py_weakref_call`, `py_weakref_invalidate` hooked into instance
  dealloc and cycle clear); however `weakref.WeakValueDictionary`,
  weak-method binding, and full callback semantics are not all complete.
- Refcount is atomic only when built with `PCC_WITH_THREADS=1`; the
  default build keeps non-atomic refcount.
- Concurrent mutation of shared mutable containers under user-level
  locks is **not yet correct** — see the threading paragraph below.

The bootstrap closure (pcc compiling itself) does not construct cycles,
rely on `__del__`, or use weakrefs in a way that exercises the remaining
gaps, so these do not block `pcc1 → pcc2 → pcc3` byte-identical
reproduction. Real-world Python programs with parent-pointer trees,
heavy `__del__` use, or long-lived servers may still surface them.

Threading model: the runtime is free-threaded under
`PCC_WITH_THREADS=1`. Refcounts use `__atomic_*` operations
(`ldadd` / `ldaddal` on aarch64) rather than a process-wide GIL, so
multiple pthreads can run pcc-compiled Python code on separate cores.
The threading shim
([pcc/py_stdlib/threading.py](pcc/py_stdlib/threading.py)) is backed
by `pthread_mutex` / `pthread_cond` / `pthread_create` for `Lock`,
`RLock`, `Event`, `Condition`, `Semaphore`, and `Thread`. A
behavior-oriented-concurrency helper
([pcc/py_stdlib/boc.py](pcc/py_stdlib/boc.py)) defines `Cown` and a
`locked` context manager that acquires multiple cowns in canonical
total-id order — the BoC trick that makes deadlock impossible by
construction.

The parallel speedup proof at
[benchmarks/python/boc_bank_demo.py](benchmarks/python/boc_bank_demo.py) and
[tests/python/test_boc_threading_proof.py](tests/python/test_boc_threading_proof.py)
builds the runtime with `PCC_WITH_THREADS=1`, runs four pthreads each
performing CPU-bound integer mixing with no shared mutation, and
asserts that wall-clock(serial) / wall-clock(parallel) ≥ 2.5×. On a
4-thread macOS arm64 host this currently lands around 3.5×.

Threaded pcc1 smoke gates now cover lock-protected counter updates,
thread object lifetime under GC churn, and real-pthread explicit
`gc.collect()` across GC backends. This is still not a blanket
"free-threaded Python containers are complete" claim: unsynchronized shared
container mutation remains unsupported, backend-specific stress continues,
and the reliability record lives in
[docs/investigations/pcc1-threaded-explicit-gc-collect-gap.md](docs/investigations/pcc1-threaded-explicit-gc-collect-gap.md).

## Testing

Use `uv run ...` for repository commands.

```bash
env -u LC_ALL uv run pytest -q
env -u LC_ALL uv run pytest -m integration
env -u LC_ALL uv run pytest tests/c/test_lua.py -q -n0
env -u LC_ALL uv run pytest tests/integration/test_sqlite.py -q -n0
env -u LC_ALL uv run pytest tests/integration/test_postgres.py -q -n0
```

Focused gates:

```bash
env -u LC_ALL uv run pytest tests/c/test_c_testsuite.py tests/c/test_clang_c.py -q -n0
env -u LC_ALL uv run pytest tests/c/test_gcc_torture_execute.py -q -n0
env -u LC_ALL uv run pytest tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py -q -n0
env -u LC_ALL uv run pytest tests/c/test_self_backend.py tests/python/test_self_backend_bootstrap_gate.py -q -n0
```

Fast full-test strategy (no script needed):

```bash
# Normal lane
env -u LC_ALL uv run pytest -n auto --dist=loadgroup tests/c tests/python --maxfail=1 --durations=20

# All non-integration regression tests now live under tests/c and tests/python, so one command
# covers both buckets.

# Integration lane by directory (same as integration markers)
env -u LC_ALL uv run pytest -n0 tests/integration --maxfail=1 --durations=20

# If your machine can handle it, run integration lane in parallel:
env -u LC_ALL uv run pytest -n 4 --dist=loadgroup tests/integration --maxfail=1 --durations=20
```

All repository command examples use `env -u LC_ALL`; this is required for Codex
locale handling and harmless elsewhere.

### Performance Benches

Performance tooling lives under `benchmarks/`. For the compiled self-host
compiler specifically, use `benchmarks/bench_pcc1.py` against an existing
`pcc1` binary:

```bash
mkdir -p build/bootstrap-strict-self
env -u LC_ALL uv run pcc \
  --backend self \
  --python-libpython off \
  --ir-scaffold on \
  pcc/__main__.py -o build/bootstrap-strict-self/pcc1

env -u LC_ALL uv run python benchmarks/bench_pcc1.py \
  --pcc1 build/bootstrap-strict-self/pcc1
```

`bench_pcc1.py` defaults to the same strict settings for programs compiled by
`pcc1`: `--backend self --python-libpython off --ir-scaffold on`. It also
rejects a `pcc1` that links `libpython` unless `--allow-libpython-pcc1` is
passed explicitly.

```bash
env -u LC_ALL uv run python benchmarks/bench_pcc1.py \
  --pcc1 build/bootstrap-strict-self/pcc1 \
  --backend self \
  --python-libpython off \
  --ir-scaffold on
```

The heavy measurement is pcc1 compiling `pcc/__main__.py` into a fresh pcc2.
It is opt-in because it can take long enough to be noisy during normal
development:

```bash
env -u LC_ALL uv run python benchmarks/bench_pcc1.py \
  --pcc1 build/bootstrap-strict-self/pcc1 \
  --include-self-compile
```

For generated Python program runtime, compare the no-libpython binary against
CPython with:

```bash
env -u LC_ALL uv run python benchmarks/bench_py_runtime.py
```

This benchmark is intentionally separate from `bench_pcc1.py`: `bench_pcc1.py`
measures the compiled compiler, while `bench_py_runtime.py` measures a program
compiled by `pcc`. Current Python frontend runtime speed is not yet a CPython
replacement; this bench is the guardrail for the unboxed-loop and runtime
startup work needed to change that. Performance claims should be scoped to
benchmarked workload classes and should record correctness, fallback mode,
allocation behavior, startup/runtime time, and whether the binary linked
`libpython`.

## Documentation

Current work state is tracked by
[`codex-goal-prompt.md`](codex-goal-prompt.md) and
[`docs/current-goal-state.md`](docs/current-goal-state.md). Some older guides
below predate the no-libpython default and are retained as background rather
than current task status.

| Topic | Path |
|---|---|
| Active goal and task board | [codex-goal-prompt.md](codex-goal-prompt.md), [docs/current-goal-state.md](docs/current-goal-state.md) |
| Architecture background | [docs/system-architecture.md](docs/system-architecture.md) |
| Python tutorial background | [docs/python-tutorial.md](docs/python-tutorial.md) |
| Python how-to background | [docs/python-howto.md](docs/python-howto.md) |
| Python limitations background | [docs/python-limitations.md](docs/python-limitations.md) |
| Python scorecard background | [docs/python-scorecard.md](docs/python-scorecard.md) |
| Python compatibility/specialization strategy | [docs/plans/python-compat-specialization-strategy.md](docs/plans/python-compat-specialization-strategy.md) |
| NumPy no-libpython plan | [docs/plans/numpy_plan.md](docs/plans/numpy_plan.md) |
| NumPy ecosystem expansion plan | [docs/plans/numpy_ecosystem_plan.md](docs/plans/numpy_ecosystem_plan.md) |
| Bootstrap historical tracker | [docs/issues/open-bootstrap-issues.md](docs/issues/open-bootstrap-issues.md) |
| Python data-model gaps | [docs/issues/python-data-model-gaps.md](docs/issues/python-data-model-gaps.md) |
| Python semantics discussion | [docs/issues/python-semantics-preservation.md](docs/issues/python-semantics-preservation.md) |
| GC semantics gap | [docs/issues/gc-semantics-gap.md](docs/issues/gc-semantics-gap.md) |
| Investigation reports | [docs/investigations/](docs/investigations/) |
| Plans | [docs/plans/](docs/plans/) |
| Contributor/agent notes | [AGENTS.md](AGENTS.md) |

Recommended investigation reports:

- [docs/investigations/lua-sort-random-pivot-signedness.md](docs/investigations/lua-sort-random-pivot-signedness.md)
- [docs/investigations/pcre-op-lengths-incomplete-array-binding.md](docs/investigations/pcre-op-lengths-incomplete-array-binding.md)
- [docs/investigations/zlib-integration-static-local-arrays-and-layout.md](docs/investigations/zlib-integration-static-local-arrays-and-layout.md)
- [docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md](docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md)
- [docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md](docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md)
- [docs/investigations/nbody-shootout-fp-contract-and-vectorization.md](docs/investigations/nbody-shootout-fp-contract-and-vectorization.md)

## Repository Map

| Path | Role |
|---|---|
| `pcc/cli_core.py` | Installed `pcc` CLI entrypoint. |
| `pcc/pcc.py` | Compatibility CLI wrapper. |
| `pcc/api.py` | `build(...)` and `module(...)` APIs for C. |
| `pcc/project.py` | Source collection and build orchestration. |
| `pcc/evaluater/c_evaluator.py` | C compile/evaluate/link coordinator. |
| `pcc/codegen/c_codegen.py` | Main C semantic lowering implementation. |
| `pcc/py_frontend/` | Python type inference and native lowering. |
| `pcc/py_runtime/` | Python runtime archive sources and pcc-Python ports. |
| `pcc/backend/` | Experimental self backend. |
| `pcc/llvm_capi/` | In-repo LLVM-C compatibility path. |
| `pcc/extern/` | Python-to-C extern declarations. |
| `pcc/unsafe/` | Compiler-recognized low-level Python intrinsics. |
| `utils/fake_libc_include/` | Fake libc headers used by the C frontend. |
| `tests/` | Unit, corpus, bootstrap, and integration tests. |
| `projects/` | Third-party projects used as stress targets. |
| `benchmarks/` | Performance tooling. |

## Development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
env -u LC_ALL uv run pytest -q
```

### Environment Controls

Command-line flags are preferred when an option has both CLI and environment
forms. The variables below are the supported development/debug controls used by
the compiler, bootstrap, runtime build, and local gates.

General compiler controls:

| Variable | Values | Effect |
|---|---|---|
| `PCC_BACKEND` | `llvm`, `llvm_capi`, `self` | Default backend when `--backend` is not passed. |
| `PCC_PYTHON_LIBPYTHON` | `auto`, `on`, `off` | Default Python fallback/linkage policy when `--python-libpython` is not passed; unset means `off`. |
| `PCC_IR_SCAFFOLD` | `off`, `on`, `auto` | Default for the closed-world Python IR scaffold; unset means `on`. |
| `PCC_COMPILE_CACHE_DIR` | path | Override the translation-unit compile cache directory. |
| `PCC_DISABLE_COMPILE_CACHE` | `1`, `true`, `yes`, `on` | Disable the translation-unit compile cache. |
| `PCC_USE_PLY_C_PARSER` | `1` | Use the legacy PLY C parser instead of the native C parser. |
| `PCC_PLY_CACHE_DIR` | path | Override the legacy PLY parser table cache directory. |
| `PCC_BINARY` | path/command | Runtime build helper: choose the `pcc` command used when rebuilding runtime archives. |

LLVM, IR-builder, and pass controls:

| Variable | Values | Effect |
|---|---|---|
| `PCC_USE_LLVMLITE` | `1` | Force all LLVM compatibility shims back to `llvmlite`. |
| `PCC_USE_LLVMLITE_C` | `1` | Force only the C codegen path back to `llvmlite`. |
| `PCC_USE_LLVMLITE_PY` | `1` | Force only the Python codegen path back to `llvmlite`. |
| `PCC_USE_LLVMLITE_PASSES` | `1` | Force only the pass layer back to `llvmlite`. |
| `PCC_LIBLLVM_PATH` | path | Point the native LLVM-C binding at a specific `libLLVM-C`. |
| `PCC_DISABLE_PASSES` | comma-separated pass names | Disable managed passes by name or alias. |
| `PCC_LLVM_DISABLE_PASSES` | comma-separated LLVM pass names | Disable concrete LLVM passes in the text pipeline. |
| `PCC_CHEAP_LLVM_PIPELINE` | `1` / pass list / false value | Enable or customize the cheap LLVM pass bundle for low-opt paths. |
| `PCC_LLVM_PIPELINE` | `1`, `default`, or pipeline spec | Run an external LLVM text pipeline. |
| `PCC_LLVM_OPT_BIN` | path | LLVM `opt` binary used by external LLVM pipeline selection. |

Python runtime, linking, packaging, and bootstrap controls:

| Variable | Values | Effect |
|---|---|---|
| `PCC_RUNTIME_CC` | `pcc`, `cc` | Select whether Python runtime archives are built with pcc or the host C compiler. |
| `PCC_RUNTIME_HIGH` | `py`, `c` | Select pcc-Python or C implementations for high-level runtime modules. |
| `PCC_HOST_PYTHON` | command | Host Python used for subprocess boundaries such as self-backend emission. |
| `PCC_PYTHON_LDFLAGS` | linker flags | Override `python-config --ldflags --embed` for libpython fallback linking. |
| `PCC_PYTHON_CONFIG` | command/path | Override the `python*-config` command used for libpython flags. |
| `PCC_WITH_LIBPYTHON` | `1` | Runtime Makefile toggle for building libpython-compatible runtime archives. |
| `PCC_BUILD_SKIP` | `1` | Wheel build hook: skip runtime archive prebuild. |
| `PCC_BUILD_BACKEND` | `self`, `llvm` | Wheel build hook: backend used for runtime archive prebuild. |
| `PCC_BUILD_TARGET` | make target | Wheel build hook: runtime Makefile target to build. |
| `PCC_BOOTSTRAP_OUT_DIR` | path | `scripts/bootstrap.sh` output directory. |
| `PCC_BOOTSTRAP_RUNTIME_CC` | `pcc`, `cc` | Bootstrap wrapper default for `PCC_RUNTIME_CC`. |
| `PCC_BOOTSTRAP_RUNTIME_HIGH` | `py`, `c` | Bootstrap wrapper default for `PCC_RUNTIME_HIGH`. |
| `PCC_BOOTSTRAP_PYTHON_LIBPYTHON` | `off`, `auto`, `on` | Bootstrap wrapper default for `--python-libpython`; unset means `off`. |

Diagnostics and local gates:

| Variable | Values | Effect |
|---|---|---|
| `PCC_DUMP_BAD_IR` | directory | Dump invalid or unparsable LLVM IR snapshots on LLVM parse failures. |
| `PCC_DEBUG_PHI_TYPES` | file path | Append SSA phi type-mismatch diagnostics to a log file. |
| `PCC_DEBUG_SSA_LOWER_FAIL` | `1` | Print tracebacks when SSA lowering fails and falls back. |
| `PCC_PROBE_CLOSURE` | probe mode | Select the stage-1 closure probe mode. |
| `PCC_PROBE_VERBOSE` | truthy value | Print verbose probe output for closure-probe scripts. |
| `PCC_CSMITH_SEEDS` | integer | Number of Csmith seeds used by the Csmith pytest harness. |
| `PCC_SELF_BACKEND_LINUX_X86_64_IMAGE` | Docker image tag | Override the Linux x86_64 self-backend Docker image tag. |
| `PCC_SELF_BACKEND_DOCKER_REBUILD` | `1` | Force rebuild of the Linux x86_64 self-backend Docker image. |

Compiler changes should include a minimized regression test and, when relevant,
a real-project confirmation. Read [AGENTS.md](AGENTS.md) before making semantic
frontend or codegen changes; it documents the repository's debugging workflow
and testing policy.

## License

MIT. See [LICENSE](LICENSE).
