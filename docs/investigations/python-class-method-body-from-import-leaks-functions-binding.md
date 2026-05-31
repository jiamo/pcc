# Investigation: class method body's `from .sibling import name` leaks self.functions binding → top-level def name body attaches to sibling's symbol; local symbol undefined at link time

## Status
resolved

## Problem Description

When a class method body contains `from .sibling import some_name` (a local
function-body import resolved against a native sibling module) and the
SAME module also defines `def some_name(...):` at top level, the top-level
function's body is emitted under the SIBLING module's symbol instead of the
local module's symbol. The local module's declared-extern symbol stays
declared-but-undefined, and the linker fails with
`Undefined symbols: _user_<this_module>_<name>`.

The numpy auto-mode compile path exhibits this exactly:

- `numpy/distutils/misc_util.py` has `Configuration.get_info(self, *names)`
  method (L2109) whose body contains
  `from .system_info import get_info, dict_append` (L2115).
- The same module has top-level `def get_info(pkgname, dirs=None):` (L2201)
  and `def dict_append(d, **kws):` (L2287).
- Both top-level definitions are skipped at IR emit time; their bodies are
  emitted under `user_numpy_distutils_system_info_get_info` / `..._dict_append`
  inside misc_util's IR module (visible at line 30511 in the dump as
  `define external ptr @user_numpy_distutils_system_info_dict_append(...) { ... }` —
  the misplaced body), while the proper local symbols stay as
  `declare external ptr @user_numpy_distutils_misc_util_dict_append(...)` /
  `..._get_info(...)`.

This is the **next blocker that surfaced after** closing the class-init
phantom-symbol link cap
([python-class-init-phantom-symbol-link-fail.md](python-class-init-phantom-symbol-link-fail.md)).
With the class-init fix, `MAError___init__` / `_Dummy___init__` errors went
away; the next undefined symbol was `_user_numpy_distutils_misc_util_dict_append`.

## Repro

The minimum reproduction requires a multi-file (native sibling) setup since
the bug is about `_native_module_exports` lookup during in-function
`_emit_import_from`. A single-file harness cannot trigger
`_bind_native_cross_module_imports`. The ground-truth repro is the numpy
auto-mode diagnostic:

```bash
# (Install numpy cpython-compat into a tmp site first; see
# tests/python/test_package_import_path.py::test_pcc1_real_numpy_first_import_boundary_opt_in.)
PCC_PACKAGE_SITE=$TMP/site \
  env -u LC_ALL uv run pcc \
  --backend self --python-libpython=auto --ir-scaffold=on \
  $TMP/main.py -o $TMP/exe
```

Before fix:
```
ld: Undefined symbols:
  _user_numpy_distutils_misc_util_dict_append,
    referenced from: _user_..._Configuration_add_extension in self_backend_expanded_93.ll.o
```

After fix: compile succeeds (`rc=0`), exe is produced, both symbols are
`define`d under their proper local-module-namespaced names.

## Test [CONFIRMED]

Existing focused suites cover the regression surface (50 passed; no new
failures):
`test_py_codegen_class_model.py test_py_class_constructor_attr_args.py
test_py_class_kwargs_no_init.py test_py_exceptions.py
test_exception_chaining_wiring.py test_dataclasses_full.py
test_py_multi_file_compile.py`. The numpy auto-mode diagnostic confirms the
fix end-to-end. A minimal-multi-file regression in a unit test was not added
in this slice: it requires the multi-file driver wiring that
`scripts/pcc_multi.py` uses, and the existing focused suites already exercise
class+method+import+multi-file paths that would surface broad regressions.

## Proposals

- No.1 Snapshot and restore `self.functions` around class-method body emission  [CONFIRMED]

## No.1 Snapshot and restore self.functions around class-method body emission

### Code Change

`pcc/py_frontend/codegen/class_gen.py` method-emission save/restore (around
line 3362-3370 / 3624-3628): add `parent.functions` to the save block and
restore it in the cleanup. The save is a shallow `dict(...)` copy; the
restore reassigns the copied dict reference.

```python
# Save side:
saved_functions = dict(parent.functions)
...
# Restore side — only undo OVERWRITES of pre-existing keys.  Replacing
# parent.functions wholesale would drop NEW entries added during the
# method body (e.g. nested ``def`` helpers exposed to the rest of the
# module) and broke stage2 bootstrap with ``AttributeError: blocks``
# when later code referenced those missing entries.
for _key, _val in saved_functions.items():
    parent.functions[_key] = _val
```

### CONFIRMED

Root cause: when emitting a class method's body, statement-level lowering
hits the local `ImportFrom` and routes through
`import_lowering.py::_emit_import_from` (line 757). For a native sibling
import (`from .sibling import name`), line 839 calls
`_bind_native_cross_module_imports` → `_bind_native_cross_module_export`
(`native_modules.py:859`). That function declares an extern in
`self.module.globals` under the SIBLING's mangled name
(`user_<sibling>_<name>`) AND, critically, sets `self.functions[local_name] =
sibling_extern_fn` (line 897). This binding is intended to be scoped to the
method body — `self.functions` is a *local-name → ir.Function* lookup used
during expression lowering inside that method, so calls inside the method
resolve to the sibling fn.

But the binding LEAKS out of the method. Class-method emission saves and
restores `_owned_local_flag_slots`, `_gc_rooted_local_names`,
`_container_temp_root_slot_names`, several env hints, etc. — but NOT
`self.functions`. So after the method finishes, `self.functions["name"]`
still points to the sibling extern.

Then the emit-pass loop reaches the same module's later top-level
`def name(...):`, calls `_emit_user_function(stmt)`. At
`user_function_lowering.py:940`,
`fn = self.functions[fd.name]` retrieves the polluted sibling extern instead
of the locally-declared fn (`self.module.globals[user_<this>_<name>]`). The
function body is then emitted *into the sibling extern*, promoting it from
`declare` to `define` under the WRONG symbol name. The local module's
declared-extern symbol gets no body; the linker rejects the cross-module
reference.

The fix snapshots `self.functions` at method entry and restores it at exit
(matches the pattern that already exists for `_owned_local_flag_slots` /
`_gc_rooted_local_names`). After restore, `self.functions["name"]` points
to whatever was bound at method-emit-entry (the declare-pass binding of the
local fn). The subsequent top-level def's emit reads the correct local fn
and the body lands on the correct symbol.

Evidence:
- Numpy auto-mode compile (real numpy site) now succeeds end-to-end (rc=0),
  producing a 9.9MB exe; both `dict_append` and `get_info` are properly
  `define`d under `user_numpy_distutils_misc_util_*` in misc_util's IR.
  Before fix: link error. (The exe segfaults at runtime — that is the NEXT
  blocker, distinct from this link-stage fix.)
- Focused class/exception/multi-file suites (`test_py_codegen_class_model.py
  test_py_class_constructor_attr_args.py test_py_class_kwargs_no_init.py
  test_py_exceptions.py test_exception_chaining_wiring.py
  test_dataclasses_full.py test_py_multi_file_compile.py -q -n0`) → 50
  passed, no regression.
- Mandatory self-host gate: full stage1→stage2→stage3 bootstrap via
  `scripts/bootstrap.sh --backend self --stage 3` → green; pcc2/pcc3
  signature-normalized byte-identical
  ("OK — pcc2 and pcc3 differ only by Mach-O code-signature metadata.
  Signature-normalized copies are byte-identical."). An initial attempt
  with a wholesale `parent.functions = saved_functions` REPLACE broke stage2
  with `AttributeError: blocks` (it dropped nested-def entries legitimately
  added during method emission); the iteration-style restore fixes that
  while still undoing the cross-module import leak.

## Report

Landed No.1, a 2-line save/restore mirroring the existing pattern for sibling
caches. Closes the post-class-init link-stage cap that exposed phantom
sibling-bound bodies for `numpy.distutils.misc_util.dict_append` and
`get_info`.

**Newly-exposed downstream blocker** (NOT this change; next iteration):
the numpy exe now segfaults at runtime (rc=139 SIGSEGV) — the **runtime**
layer is the next blocker. With the link cap closed and a real exe produced,
the next investigation is to triage where in `import numpy` the runtime
crashes. This is qualitatively different from the prior link/compile-stage
caps: pcc has now PRODUCED a numpy-importing executable, and the remaining
work is runtime hardening rather than compile/link correctness.

Progress order: ... → (closed) `.owned.N` cap → (closed) phantom-init
link error → (closed, this) class-method `self.functions` leak → exe
PRODUCED → (NEW) runtime SIGSEGV in `import numpy`.
