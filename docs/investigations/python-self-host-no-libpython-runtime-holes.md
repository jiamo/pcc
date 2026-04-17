# Investigation Report: No-libpython Self-host Debugging Exposed Runtime Semantic Holes

## Executive Summary

Issue 1's original target is simple to state:

> the bootstrap binary should not link `libpython`.

The practical closure criterion is stricter:

1. build `pcc1` with `--python-libpython off`
2. verify the resulting binary has no `libpython` dependency
3. run that `pcc1` as a compiler
4. have it compile a tiny Python program and produce a working binary

This investigation found that the first two criteria can pass while the third
and fourth still expose real self-host bugs. That is the important lesson.

Counting `py_cpy_*` calls was useful for reducing the CPython fallback surface,
but it was not a sufficient self-host test. The remaining failures were not
"one more dispatch case". They were gaps between code that works while hosted
by CPython and the same code executing inside the pcc runtime.

As of 2026-04-29, the no-libpython `pcc1` can report real Python tracebacks for
compiler exceptions and can compile/run simple integer arithmetic such as
`print(2 + 3)`. The current active blockers are narrower:

- tuple/list literal compilation can still segfault inside pcc1
- float literals still compile as `0.0`
- pcc2 compilation reaches cross-module export decoding before failing

The important debugging result is that these are now ordinary compiler/runtime
bugs with visible evidence, not silent "success with no output" failures.


## Initial Goal

The desired ground-truth command is:

```bash
timeout 180s env -u LC_ALL uv run pcc \
  --ir-scaffold=on \
  --python-libpython off \
  --backend self \
  pcc/__main__.py \
  -o /tmp/pcc1_issue1_probe
```

The linkage check on macOS is:

```bash
otool -L /tmp/pcc1_issue1_probe | rg -i 'python|libpython'
```

Expected output is empty.

But this only proves the binary links without `libpython`. It does not prove the
binary is a correct compiler.

The next gate is:

```bash
cat > /tmp/pcc_tiny.py <<'PY'
def main():
    return 0
PY

timeout 90s /tmp/pcc1_issue1_probe \
  --verbose \
  --ir-scaffold=on \
  --python-libpython off \
  --backend self \
  -o /tmp/pcc_tiny_bin \
  /tmp/pcc_tiny.py
```

That is the gate still exposing bugs.


## What Was Misleading

### `py_cpy_* == 0` is necessary, not sufficient

The fallback ratchet drove the right behavior: CPython fallback calls went down
sharply. It also caught the distinction between ordinary fallback calls and
bridge calls such as `py_cpy_to_pcc_obj`.

But a `py_cpy_*` count says little about whether pcc's own runtime implements
the Python behavior the compiler source relies on.

The no-libpython binary can still fail because:

- class attribute storage differs from CPython
- properties or descriptors do not behave the same way
- method dispatch mutates the wrong object
- side-effecting parser helper calls execute more than once
- pending exceptions are swallowed and the CLI returns success

Those are self-host runtime correctness bugs, not CPython fallback bugs.

### A pending pcc-runtime exception is not a failed process by itself

pcc's Python runtime does not use C++/Itanium unwinding. `py_raise(exc)` stores
the exception in a TLS slot and returns normally. Generated code is responsible
for checking `py_err_occurred()` after calls that may raise, branching to an
error path, and eventually surfacing the exception.

That model is reasonable for portability, but the current top-level bootstrap
path has a bad failure mode: an internal compile-time exception can propagate to
the function error epilogue, return a sentinel integer value, and let
`bootstrap_cli_sys_argv_exit()` see code `0`. The process then exits
successfully even though compilation failed and no artifact was produced.

That is why `lldb` was needed. The runtime had the exception; the CLI did not
print it or turn it into a nonzero exit.

For compiler work this distinction matters:

- **compile error:** bad user input or unsupported frontend construct; should be
  printed by pcc with a nonzero exit
- **compiler execution error:** pcc1 itself raised internally while compiling;
  should also be printed with a nonzero exit, ideally marked as an internal
  compiler error
- **target execution error:** the binary emitted by pcc ran and failed; belongs
  to the target program's exit/status

The current behavior collapses the second category into silent success.

### The self backend does not remove the frontend IR object model

The self backend replaces native object emission. It does not remove the Python
frontend's use of `pcc.llvm_capi.ir` as the in-memory IR builder API.

That means `pcc1 --backend self` still executes Python code that constructs:

- `ir.Module`
- `ir.FunctionType`
- `ir.Function`
- `ir.IRBuilder`
- `ir.Block`
- `ir.GlobalVariable`

So even after LLVM-native emission is replaced, the `llvm_capi.ir` object model
is still on the self-host critical path.

This is why bugs in `FunctionType.return_type`, `Module.globals`,
`Block.is_terminated`, or `IRBuilder.append_basic_block()` can break the self
backend.


## Failure Classes Found

### 1. Bridge calls hid distance to the real endpoint

The generic `py_cpy_to_pcc_obj` bridge was a good intermediate step: it turned a
large chain of `cpy_call` / `cpy_getattr` operations into fewer explicit
boundary conversions.

But the bridge itself still depends on CPython. A binary that still needs the
bridge still needs `libpython`.

The fix was not to ban the bridge, but to count it separately from non-bridge
fallback so progress stays honest.

### 2. Parser/module globals used Python idioms that were not pcc-runtime-safe

Several module-level constants were harmless under CPython but unsafe under the
pcc runtime:

- `frozenset({...})`
- `set("...")`
- generic set/frozenset copies during module initialization

One symptom was keyword recognition breaking: tokens such as `return` could be
classified as ordinary names.

The narrow fix was to make those lexer constants pcc-friendly, for example
using tuples or strings where the runtime path is simpler.

The broader lesson is that "module imports successfully under CPython" is not a
test for "module initialization executes correctly under pcc1".

### 3. Chained parser helper calls duplicated side effects

Parser code such as:

```python
name = self._expect("NAME").text
```

is idiomatic Python. But in the self-host path, similar chained expressions
exposed side-effect duplication in generated code. Calling `_expect()` or
`_advance()` more than once consumes extra tokens and corrupts the parse stream.

The practical fix was to split side effects from attribute reads:

```python
tok = self._expect("NAME")
name = tok.text
```

The broader lesson is that expression-lowering tests need downstream-sensitive
runtime checks. Merely checking that the compiler emitted some IR for
`call.attr` is not enough; the test must prove the receiver call executes once.

### 4. `Module.globals` as a property was too dynamic for the current runtime

`pcc.llvm_capi.ir.Module.globals` originally behaved like a computed property.
That matched the shape expected by llvmlite users, but it was not robust when
the pcc-compiled compiler repeatedly used it as a mutable registry.

The fix moved it to a real dictionary field initialized by `Module.__init__`,
with `Function` and `GlobalVariable` registration updating the same live dict.

The test gap was that unit tests checked pieces of scaffold symbol emission, but
did not execute the compiled compiler far enough to prove the object registry
remained live while building a real module.

### 5. `append_basic_block()` receiver identity mattered

`IRBuilder.append_basic_block()` and `Function.append_basic_block()` look
similar but mean different things:

- builder receiver: create a block in the builder's current function
- function receiver: create a block in that function directly

The scaffold path initially routed these too generically and could call the
wrong symbol. The fix split receiver-specific wrappers:

- `scaffold_IRBuilder_append_basic_block`
- `scaffold_Function_append_basic_block`

The test gap was again stateful: symbol-name tests can pass while the runtime
object mutation is wrong.

### 6. `Block.is_terminated` and `FunctionType.return_type` must be runtime invariants

The current tiny-program failure shows the compiler can call
`IRBuilder.ret(value)` and then still enter the fall-through default return path.

`lldb` confirmed:

- `_emit_return()` is called
- `IRBuilder.ret()` is called
- `_zero_of()` is later called from the fall-through path
- `_zero_of()` receives `NULL` for `fn.function_type.return_type`

That narrows the next issue to pcc-runtime object semantics around the IR model:

- is `Function.function_type` pointing at the right object?
- does `FunctionType.__init__` store `return_type` correctly?
- is attribute lookup on pcc-defined objects losing fields?
- is `Block.is_terminated` a property path the runtime cannot handle safely?

These invariants deserve direct pcc-compiled tests.

After fixing one initialization-order issue, the failure sharpened:
`_zero_of()` received a real `PointerType` object, but the generated
`isinstance(ir_ty, ir.PointerType)` path still fell through. Direct `lldb`
probing with the C runtime's `py_isinstance()` returned true, so the remaining
problem is likely in the frontend's lowering of `isinstance` for scaffold class
objects, not in the runtime class relationship itself.

### 7. Module top-init order must preserve filtered scaffold dependencies

ON-mode scaffold filtering removes `pcc.llvm_capi.compat` from the source
closure and links `pcc.llvm_capi.ir` as the real provider. That is correct for
symbols, but it originally lost the module initialization dependency:

```text
pcc.py_frontend.codegen.runtime_abi imports pcc.llvm_capi.compat.ir
compat is filtered out
pcc.llvm_capi.ir is added later as a provider
runtime_abi top-init runs before pcc.llvm_capi.ir class init
VoidType() sees an uninitialized class object and returns NULL
```

The fix is to keep the logical dependency edge even after filtering:

```text
pcc.llvm_capi.compat -> pcc.llvm_capi.ir
```

That ensures `pcc.llvm_capi.ir` class objects are initialized before modules
such as `runtime_abi`, `marshal`, `class_gen`, and `layer1` instantiate IR
types at module top level.

### 8. LLDB evidence from the tuple-literal crash must be kept

After the exception-reporting path was made useful, a remaining pcc1 crash was
reduced to compiling this shape:

```python
t = (1, 2)
print(t)
```

The pcc1 command was run under `lldb` with `--python-libpython off` and the self
backend. The useful stack was:

```text
* frame #0 py_incref + 68
  frame #1 py_instance_set_field + 236
  frame #2 user_pcc_llvm_capi_ir_Constant___init__ + 112
  frame #3 user_pcc_llvm_capi_ir_scaffold_Constant_obj + 96
  frame #4 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_tuple_literal + 7952
  frame #5 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_expr + 2708
  frame #6 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_assign + 9852
  frame #7 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_stmt + 632
  frame #8 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_stmts + 676
  frame #9 user_pcc_py_frontend_codegen_layer1_L1CodeGen__emit_program_main + 5176
  frame #10 user_pcc_py_frontend_codegen_layer1_L1CodeGen_generate + 19520
  frame #11 user_pcc_py_frontend_pipeline_compile_python + 8272
  frame #12 user_pcc_cli_bootstrap_bootstrap_cli_main + 5880
  frame #13 user_pcc_cli_bootstrap_bootstrap_cli_sys_argv_exit + 36
  frame #14 main + 2124
```

The emitted IR for `pcc/py_frontend/codegen/layer1.py` showed the root cause:
inside `_emit_tuple_literal`, `ir.Constant(_I64, i)` lowered as:

```llvm
%i = load i64, ptr %i.addr
%bad = inttoptr i64 %i to ptr
call ptr @user_pcc_llvm_capi_ir_scaffold_Constant_obj(ptr %_I64, ptr %bad)
```

That is invalid. `i` is a native integer value, not a Python object handle.
Passing it through `scaffold_Constant_obj` stores a bogus pointer in
`Constant.value`; `py_instance_set_field` then increfs that bogus pointer and
crashes. The correct lowering is `scaffold_Constant_i64(ptr %_I64, i64 %i)`.


## Why So Many Errors Happened

The errors are numerous because self-hosting moves the compiler across an
execution boundary that the existing tests mostly did not cross.

Most unit tests exercised one of these shapes:

- CPython runs pytest
- pytest calls pcc frontend code
- pcc emits IR or an executable
- the emitted target program is checked

That is valuable, but it does not test this shape:

- CPython builds `pcc1`
- `pcc1` runs without `libpython`
- `pcc1` executes parser, type inference, codegen, IR object construction, and
  backend linking inside pcc's own runtime
- the binary emitted by `pcc1` is checked

The second shape is where these bugs live.

The specific missing coverage was:

1. **No mandatory no-libpython stage1 compiler smoke.**
   There were tests that compiled repo entrypoints and tests that used the self
   backend, but not a hard gate that built `pcc1` with `--python-libpython off`,
   verified no linkage, ran it, and required it to compile a tiny program.

2. **Too many tests stopped at emitted IR shape.**
   IR-symbol tests catch accidental fallback and wrong symbol names. They do not
   prove pcc-compiled methods mutate Python objects correctly.

3. **Object-model invariants were implicit.**
   `FunctionType.return_type`, `Function.function_type`,
   `Module.globals`, `Block.is_terminated`, and builder insertion state are not
   optional details. They are the data model of the compiler. They need direct
   tests that run under pcc-compiled code.

4. **Error propagation was under-tested.**
   The tiny compile returned status 0 while no output file existed and a pending
   exception had been raised. That should be impossible. A compiler CLI must
   fail closed when an internal exception is pending. This is a compiler
   execution error, not a target-program runtime error.

5. **Expression tests were not side-effect-sensitive enough.**
   Chained calls such as `self._expect(...).text` need tests that prove the call
   count, not just the final syntax tree for happy paths.

6. **Module-initializer behavior was not isolated.**
   `frozenset`, `set`, tuple/list/dict literals, and constant globals need
   tests that execute module initialization under the pcc runtime, not just
   tests that import the module under CPython.


## Tests That Should Be Added

The next testing layer should stay narrow. Full-suite runs are not the right
inner loop for bootstrap bring-up.

### 1. No-libpython pcc1 link gate

Build the real entrypoint with:

```bash
pcc --ir-scaffold=on --python-libpython off --backend self pcc/__main__.py
```

Then assert:

- the binary exists
- `otool -L` / `ldd` contains no `python` or `libpython`
- `pcc1 --help` exits 0

This proves only the first half of Issue 1, but it is still required.

### 2. No-libpython pcc1 compile gate

Use the freshly built `pcc1` to compile:

```python
def main():
    return 0
```

Assert:

- compile exits 0
- output file exists
- output binary exits 0
- output binary has no `libpython` linkage

This is the first real self-host compiler gate.

### 3. pcc-compiled `llvm_capi.ir` object-model gate

Compile and run a small program under pcc that checks:

- `ir.FunctionType(ir.IntType(32), []).return_type.width == 32`
- `ir.Function(module, fty, name="main").function_type is fty`
- `module.globals.get("main") is fn`
- `builder.ret(ir.Constant(i32, 0))` makes `builder.block.is_terminated`
- `fn.append_basic_block("entry")` and `builder.append_basic_block("next")`
  mutate the expected function

These are not LLVM tests. They are self-host object-model tests.

### 4. Parser side-effect gate

Add a tiny parser-only or pcc1-compile test that proves `_expect()` and
`_advance()` are not duplicated by chained attribute access. A good source is a
function with several consecutive token-consuming constructs:

```python
def main():
    x = 1
    return x
```

The important property is not the exact AST text; it is that token position
advances exactly once per parser helper call.

### 5. Module-initializer gate

Add pcc-runtime tests for constants used by bootstrap modules:

- tuple of keywords
- string membership for single-character operator sets
- list/dict literals that contain pcc-native and cpy-bridged elements
- no set/frozenset fallback in parser-critical modules unless deliberately
  supported

### 6. Error-propagation gate

Create a test where compiled pcc code raises during compile and assert:

- CLI returns nonzero
- stderr contains the exception message
- no output file is left behind

The current "status 0, no artifact" behavior hides the real failure and makes
bootstrap debugging much more expensive. The desired behavior is that pcc1's
outermost CLI checks `py_err_occurred()` before returning, prints
`py_current_exception()` through `py_exc_print_unhandled()`, and exits nonzero.


## Recommended Debugging Workflow

For this class of bug, the useful loop is:

1. Build `pcc1` with `--python-libpython off`.
2. Verify linkage.
3. Run `pcc1 --help`.
4. Run `pcc1` on the smallest Python file.
5. If it fails silently, break on `py_raise` in `lldb`.
6. If it fails in codegen, break on the suspected pcc-compiled method and
   inspect runtime object fields directly.
7. Convert the discovered invariant into a small test before broadening.

Every probe binary must be run under a hard timeout.


## Bottom Line

The self-host route is still the right route, but the test pyramid needs a new
layer.

The repository already has many unit tests for frontend lowering and many
ratchets for CPython fallback counts. What was missing is a small set of
stage1-as-compiler tests: tests where the compiled pcc binary executes the
compiler's own parser, type inference, IR object model, and backend path without
`libpython`.

That layer should be small, deterministic, and mandatory. Without it, the code
can look clean from CPython-hosted tests while still failing as soon as pcc has
to host itself.
