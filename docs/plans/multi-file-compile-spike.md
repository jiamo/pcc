# Multi-file compile spike (#157 blocker)

**Status:** design sketch — the implementation blocks #138.5
three-stage bootstrap.

## Problem

`pcc` compiles a single `.py` file per invocation today. The
bootstrap target `pcc/__main__.py` does

```python
from .pcc import main
main()
```

which routes `main` through `py_cpy_import` and then fails at
`Layer 1 unknown function 'main'` because `main`'s definition
lives in a sibling file (`pcc/pcc.py`) that the current
compilation unit never sees.

For stage 1 bootstrap (`python -m pcc pcc.py -o pcc1`) to make
sense, pcc must compile **all** of its own Python sources in one
invocation and link the resulting object code into a single
native executable — without a libpython runtime dependency.

## Minimum viable design

```
compile_python_multi(
    src_paths: list[str],           # e.g. ['pcc/__main__.py', 'pcc/pcc.py', ...]
    out_path: str,                  # native exe
    *,
    entry_module: str = '__main__', # which module's main() runs
)
```

Passes:

1. **Parse all** — native parser lifts every `src_path` into
   `pa.Module` with its full dotted module name
   (`pcc.__main__`, `pcc.pcc`, `pcc.ir_passes.dce`, …).

2. **Signature collection** — for each module walk the top level
   and record `{module.funcname: FuncType}` into a
   cross-module table. Classes are recorded by qualified name
   into a shared `ClassType` registry.

3. **Type inference per module**, supplying the shared signature
   table so `foo.bar(x)` in module A can resolve `bar`'s return
   type when `bar` is defined in module B.

4. **Codegen per module**. ImportFrom of a native sibling emits:

   - for functions: `declare external <ret> @user_<mod>_<fn>(<params>)`
     and binds the local name to the extern symbol.
   - for classes: declare the class global
     `@.cls.user_<mod>_<cls>` as external and bind accordingly.
   - for constants: a global with matching linkage.

   Non-native imports (pip packages, cpython-only stdlib) keep
   the `py_cpy_import` path.

5. **Link** — concatenate every module's `.ll` into a single
   clang invocation alongside `libpy_runtime.a`. Clang resolves
   the cross-module extern symbols at link time.

## Incremental delivery

| Step | Scope | Deliverable | Status |
|---|---|---|---|
| 1 | Infrastructure | `compile_python_multi` API + CLI entry; signature table; still uses CPython for cross-module calls (keeps libpython dep) | ✅ landed |
| 2 | Native cross-module function imports | `ImportFrom` of known-native modules → extern function decl; drop the `py_cpy_import` on those lines | ✅ landed |
| 3 | Native cross-module class imports | Same for `from .foo import MyClass` | ✅ landed |
| 4 | Shared type registry | Inference sees cross-module function / class types so DynType leaks shrink | ✅ landed |
| 5 | Bootstrap stage-1 gate | `scripts/bootstrap.sh --stage 1` produces a runnable `pcc1` binary (CPython-backed stage-1 can still link libpython transitively via `click`, but native pcc code is self-sufficient) | open — blocked on steps 3-4 + builtin constructors |
| 6 | Bootstrap stage-2/3 gates | `pcc1` compiles `pcc.py` → `pcc2`; `cmp pcc2 pcc3` structural / byte-identical | open — blocked on 5 |

### Steps 1-2 landing notes

- New API: `pcc.py_frontend.pipeline.compile_python_multi(src_paths,
  out_path, *, entry_module=None, module_names=None, …)`.
- CLI: `scripts/pcc_multi.py --entry pkg.main --out pcc1
  pkg/main.py=pkg.main pkg/util.py=pkg.util …`.
- Codegen: non-entry modules emit `_pcc_py_module_top_<mod>()`
  void initialiser in place of `@main`. Entry module's `@main`
  calls each sibling initialiser in source-path order before its
  own body runs.
- Cross-module function imports: pre-pass builds
  `{module: {fn_name: signature}}`. `_emit_import_from` in layer1
  resolves relative (`level > 0`) imports against
  `self.ast_module.name`, and when the resolved name is in the
  native-exports table it emits `declare external <ret>
  @user_<mod>_<fn>(<params>)` and binds the local name in
  `self.functions` so calls lower to `call @user_<mod>_<fn>`.
- Link step: `_module_needs_libpython` takes the native module set
  and stops pretending libpython is required for resolved sibling
  imports — produced binaries link only `libSystem + libc++`.
- Tests: `tests/test_py_multi_file_compile.py` covers
  entry-only, top-init ordering, cross-module call (no-arg + with
  args), and an otool-based link-lib check.

### Step 4 landing notes

- `infer_module(m, external_exports=…)` consults the pre-pass
  `{mod: {name: info}}` table inside its ImportFrom handling.
  Native sibling imports bind each name to the sibling's
  declared `FuncType` / `ClassType` in scope.
- Pipeline split into two pre-passes: parse + export-extraction
  (annotation-only, no body inference), then inference with the
  full table, then codegen. Exports for `def f(x: int) -> str:`
  get param_types + return_ty directly from the annotations.
- Downstream: `total = remote_fn(x) * 2` types `total` as int
  rather than Dyn, so the `*` is a native integer multiply. New
  regression `test_cross_module_return_type_flows_into_arithmetic`
  covers this.

### Step 3 landing notes

- Pre-pass now records class exports alongside functions:
  `{name, class_name, field_names, methods}` with per-method
  kind / return_ty / param_types.
- `class_gen.declare_extern_class` creates a `ClassInfo` with
  external linkage on the class global and extern function
  declarations for each method. Synthesises `_FuncDef` stubs on
  the info so `_find_method_def` + `emit_instantiate` find
  argument annotations for parameter marshalling.
- Multi-file mode switches class globals from `internal` to
  default (external) linkage so sibling modules link cleanly.
- Regression: `tests/test_py_multi_file_bootstrap_shim.py` adds
  `test_cross_module_class_instantiate_and_method_call` —
  `Point(3, 4).area()` across `entry.py` / `lib.py` runs
  libpython-free.

## Scope estimate

1-2 focused weeks. Most of the risk is in step 4 (cross-module
inference) because pcc's existing single-pass inference assumes
all user functions are visible in the current scope. Splitting
into a "declare-first / body-later" phase is straightforward but
touches the whole `_infer_module` flow.

## Why not done in this session

The 20+ commit Path-A push this week focused on single-file
codegen gaps. Multi-file compile is a separate layer change
that merits its own session with the full test matrix
(phase1/2/3/4/6c + audit + self-host survey + csmith) run on
every intermediate commit.

Its readiness also depends on:

- The `tuple()` / `set()` / `int()` builtin constructor runtime
  helpers (see #138.1 remaining long-tail).
- An argparse-based CLI entry (or a `click`-as-no-op shim that
  still parses args — the current whitelist drops them silently).
- A survey pass on any pcc source that uses `inspect`,
  `hashlib`, or other stdlib modules still on the
  CPython-import path.

Track follow-ups under #157 with `blocked-by` links to
`#138-longtail-builtins` (new) and `#138-full-argparse` (not yet
created).
