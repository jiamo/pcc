# Frontend GC bug: module-global store of a RAW pointer pins it as a GC object, corrupting the buffer (bit-42 wild pointer)

## Status
FIXED 2026-08-08 (root cause confirmed via watchpoint + source; fix + regression landed). Not a self-backend/spill bug (an
earlier revision of this file guessed spill-slot overlap — WRONG, corrected
below). The bug is in the Python frontend:
`pcc/py_frontend/codegen/module_global_lowering.py` treats EVERY pointer-typed
value assigned to a module-global as a GC-managed `PyObject` and emits
`pcc_gc_pin(value)`, which writes `PY_FLAG_GC_PINNED (0x40)` into the flags word
at `value+12`. When the module-global holds a RAW `pcc.unsafe` pointer
(`stack_alloc`/`calloc`/`ptr_add`/extern), that write lands in ordinary buffer
memory and corrupts it.

## Symptom
`--backend self`, large program: a value that should be a small int/index reads
back as `(0x40 << 32) | value` (e.g. `2` → `0x0000004000000002`). Used as an
array index it scales to bit 42 (`0x40000000000`); the derived address faults:
`ldr x10, [x9]`, `x9 = good | 0x40000000000` → EXC_BAD_ACCESS (rc 139).
The stray value is exactly `PY_FLAG_GC_PINNED = 0x40` (py_internal.h:66).

## Confirmed causal chain
1. In `projects/mac_diff_app/app.py` the module-level render loop did
   `opp = ptr_add(ops, oi * 24)`. Because it is assigned at MODULE scope, `opp`
   is a module global (`@.modvar.app.opp`); `ops = stack_alloc(6144)` is a raw
   stack buffer, so `opp` holds a RAW interior pointer.
2. `module_global_lowering._store_module_global` (approx lines 637-646):
   ```python
   if is_cpy_value or not isinstance(value.type, ir.PointerType):
       self.builder.store(value, gv); ...; return          # non-pointer: plain store
   # ELSE (ANY pointer type) -> treated as a GC object:
   old = load(gv)
   call pcc_gc_unpin(old)
   call pcc_gc_pin(value)        # <-- pins the raw pointer
   call pcc_gc_store_root(&gv, value)
   ```
   The only guard is "is it a pointer at all"; it does NOT distinguish a real
   GC `PyObject*` from a raw unsafe/extern pointer.
3. `pcc_gc_pin(o)` (py_obj.c:645) = `py_header_flags_or(py_header(o),
   PY_FLAG_GC_PINNED)` — a read-modify-write OR of `0x40` into the flags field.
   Header layout (py_runtime.h / CLAUDE.md): refcount@0 (8B), type_tag@8 (i32),
   **flags@12 (i32)**. So it writes `o+12`.
4. `o = opp = ops + 2*24 = &op[2]`; `o+12 = op[2].left(+8) high 32 bits`.
   `op[2].left` was `2`; after the OR it is `0x0000004000000002`.
5. `_calc_red` reads `ll = op.left = 0x4000000002`, computes
   `LINES_L + ll*16` (= `... | 0x40000000020`) and dereferences → SIGSEGV.

## Ground-truth evidence (lldb, current-source pcc1 binary)
- Fault `user_app__calc_red +292: ldr x10,[x9]`, `x9 = 0x40100180050`.
- op record on the stack: `opp+0 = 3 (type, clean)`, `opp+8 = 0x0000004000000002
  (left, HIGH-32 = 0x40)`, `opp+16 = 2 (right, clean)`.
- Hardware watchpoint on `opp+12` (the clobbered high word) fires inside
  **`pcc_gc_pin + 48`**, called directly from `main` (the module-level render
  loop). New value low word = `0x40`.
- `PY_FLAG_GC_PINNED == 0x40`. Everything matches.

## Why it is config/scale dependent (and why minimal repros failed)
It has nothing to do with function size or spilling. It fires iff a module-scope
variable is assigned a raw pointer. The crash appeared only when the render loop
introduced `opp = ptr_add(ops, ...)` as a module var, and vanished when that
line was inlined (`ptr_add(ops, oi*24)` passed directly, no module-global
store). Small standalone repros used function-LOCAL variables (no module-global
store path) so they never pinned.

## Broader impact
This mis-pins EVERY module-global that holds a raw `pcc.unsafe`/extern pointer.
In mac_diff_app that includes `IDS_L/IDS_R/BUF_L/BUF_R/LINES_L/LINES_R/ops/...`
— each has `0x40` OR'd into byte offset +12 of its buffer at store time (and
`pcc_gc_store_root` wrongly registers a raw buffer as a GC root, and
`pcc_gc_unpin(old)` runs on the previous raw pointer). Most were silently
non-fatal (they corrupt a high word of element 1 / a length field that was not
used as a pointer); the `opp`-as-index path is what turned it into a crash. Any
pcc-Python program that stores unsafe/extern pointers in module globals is
affected on all backends (the write is runtime, not backend-specific; it just
surfaced under --backend self here).

## Fix direction
`_store_module_global` must only run the pin/unpin/store_root GC path for values
that are genuinely GC-managed `PyObject` references. Distinguish raw pointers
(results of `pcc.unsafe.*`, `extern`, `c_ptr`, `calloc`, `stack_alloc`,
`ptr_add`, `int_to_ptr`, …) from GC object references — e.g. gate on the
declared/semantic type being a GC object type, or on a "is-gc-ref" provenance
flag carried by the value, instead of the raw `isinstance(value.type,
ir.PointerType)` check. Raw pointers must take the plain `store` path (no pin,
no unpin, no store_root). Add a regression: a module-global assigned a
`stack_alloc`/`ptr_add` pointer, then read back a 64-bit field, must be
bit-exact (no 0x40 in the flags-offset word) on llvm AND self backends.

## Reproduction
See `scripts/bootstrap.sh --backend self --stage 1 --out-dir build/bootstrap-self`
then compile mac_diff_app (inline-red config with `opp = ptr_add(...)` as a
module var) and run — rc 139. Watchpoint recipe: break `user_app__repair_ops`
(arg0 x0 = ops base), `watchpoint set -s4` on `x0+60`, condition
`== 0x40`, continue → stops in `pcc_gc_pin`.

## Related
- `docs/investigations/self-backend-torture-phi-swap-and-minmax-zero-fold.md`


## Fix (landed 2026-08-08)
Three changes:
1. `pcc/py_frontend/codegen/module_global_lowering.py::_store_module_global_root_value`
   gained a `raw_pointer: bool` param; when true it takes the plain `store`
   path (no pcc_gc_unpin/pcc_gc_pin/pcc_gc_store_root).
2. `pcc/py_frontend/codegen/assignment_statement_lowering.py` passes
   `raw_pointer=self._expr_returns_unsafe_raw_pointer(stmt.value)` for
   module-global stores.
3. `pcc/py_frontend/codegen/ownership_lowering.py::_UNSAFE_RAW_POINTER_RETURNS`
   gained `stack_alloc` (it returns a raw pointer like calloc/ptr_add).

Validation: pre-fix the minimal repro read a stored `2` back as
`274877906946` (0x40_00000002); post-fix it reads `2`. Regression
`tests/python/test_native_module_global_raw_pointer.py` passes on self AND llvm.
Stage1 self-host bootstrap (`scripts/bootstrap.sh --backend self --stage 1`)
still succeeds with the fix. REMAINING (heavier, run before commit):
`tests/python/test_bootstrap_gate_baseline.py`,
`tests/python/test_fallback_baseline.py`,
`tests/python/test_ir_py_fallback_baseline.py`.

## Follow-up: the "theme buffer clobber" was the SAME bug (2026-08-08)
While fixing mac_diff_app colors, a second symptom appeared: `theme_get(k)`
returned garbage (th2=-1, th4=42, th5=9) at runtime though it stored correctly
at setup; a hardware watchpoint showed the theme `calloc(512)` buffer being
overwritten by `py_dict__rehash`/`py_module_attr_set` (i.e. freed and reused).
This was initially suspected to be a separate "raw calloc freed as owned" bug.
It is NOT a separate bug: with the module-global GC-pin fix above, `theme_get`
returns correct values (0xFFFBE3E4 / 0xFFD8D8D8) and two faithful standalone
repros (`ptr_to_int(calloc(...))` in a global + heavy dict churn) keep the
buffer intact. The pin bug OR'd PY_FLAG_GC_PINNED into buffer headers broadly;
a spurious flag on the theme buffer let the GC treat it as collectable and free
it. Fixing the pin removed this downstream corruption too. No separate fix
needed.
