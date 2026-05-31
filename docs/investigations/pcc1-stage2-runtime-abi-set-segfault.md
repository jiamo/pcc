# Investigation: pcc1 stage2 segfault in py_set._lookup_slot during runtime_abi.declare_runtime

## Context

After commit `a13bd0d3` resolved the lift_expr raw-value-leak (see
`pcc1-stage2-lift-expr-raw-value-leak.md`), pcc1 stage2 reaches the
codegen phase. With `--verbose` it completes through
`type_infer[pcc.__main__]` and `codegen pcc.__main__` (exit 0). Without
`--verbose` it segfaults silently (exit 139, no stdout/stderr) early
in codegen.

This is a **separate, distinct** memory-safety bug from the lift_expr
leak — the same single-file repro that the earlier bug used now passes
through lift cleanly across 5/5 runs.

## Repro

```bash
# Build the fix-baseline pcc1
env -u LC_ALL uv run pcc --ir-scaffold=on --python-libpython off \
    --backend self pcc/__main__.py -o /tmp/pcc1_fix

# Without --verbose: segfault
/tmp/pcc1_fix --ir-scaffold=on --python-libpython off --backend self \
    pcc/__main__.py -o /tmp/pcc2_test
echo $?   # 139

# With --verbose: succeeds
/tmp/pcc1_fix --verbose --ir-scaffold=on --python-libpython off \
    --backend self pcc/__main__.py -o /tmp/pcc2_test
echo $?   # 0
```

The crash reproduces deterministically when --verbose is off and
deterministically passes when --verbose is on.

## Backtrace (lldb)

```
Process stopped: EXC_BAD_ACCESS (code=1, address=0x600075309e838)
* frame #0: 0x00000001003f9fd4 pcc1_fix`user_py_set__lookup_slot + 124
  frame #1: 0x00000001003f9cb4 pcc1_fix`py_set_add + 92
  frame #2: 0x00000001003d5794 pcc1_fix`user_pcc_py_frontend_codegen_runtime_abi__apply_runtime_function_attrs + 484
  frame #3: 0x00000001003d551c pcc1_fix`user_pcc_py_frontend_codegen_runtime_abi_declare_runtime + 1876
  frame #4: 0x00000001000b772c pcc1_fix`user_pcc_py_frontend_codegen_layer1_L1CodeGen___init__ + 688
  frame #5: 0x000000010002bd38 pcc1_fix`user_pcc_py_frontend_pipeline_compile_python_multi + 41404
  ...
```

## Source-level call site

`pcc/py_frontend/codegen/runtime_abi.py:512`:

```python
def _apply_runtime_function_attrs(fn: ir.Function, name: str) -> None:
    attrs = RUNTIME_FUNCTION_ATTRS.get(name)
    if not attrs:
        return
    fn_attrs = getattr(fn, "attributes", None)
    if fn_attrs is None:
        return
    for attr in sorted(attrs):
        try:
            fn_attrs.add(attr)   # ← line 512, segfaults inside py_set.add
```

`fn_attrs` is a `FunctionAttributes` whose `_attrs` is initialized to
`set()` in the constructor (`pcc/llvm_capi/ir.py:836`,
`FunctionAttributes.__init__` at `ir.py:968-970`). Not a default-`None`
field; not the same shape as the lift_expr bug.

## What flips the bug on/off

Heap-layout dependent. Inserting any
`sys.stderr.write("...")` call before the failing `add()` call masks
the crash entirely — pcc1 then runs through to a clean exit. The
`--verbose` case is the same effect: log calls allocate strings and
perturb the heap enough to dodge the bug.

This is the classic UAF / nano-allocator-corruption signature:
behavior changes when the surrounding allocation pattern changes, and
the immediate crash bt points at a *load* from an integrity-checked
slot rather than at the code that caused the corruption.

## Address arithmetic

The faulting address `0x600075309e838` decomposes as
`entries (heap) + slot_off + 8` where `slot_off = j * 16` is the
hash-bucket offset inside the entries array. For
`0x600075309e000` + `0x838`, `j*16 + 8 = 0x838` → `j = 131` — a
plausible bucket index. So the fault is consistent with `entries`
being a stale (freed-and-recycled) pointer, or `capacity` being out
of sync with the actual allocation, rather than `j` being wildly
wrong.

## Hypothesis

Most likely the `FunctionAttributes._attrs` set's `entries` pointer
is dangling: the underlying capacity-array was freed (by GC sweep,
by another code path's `_resize`, or by a refcount decrement that
should not have fired) before `add()` reads it. The same set was
constructed seconds earlier and had attributes appended successfully
in some earlier function — the corruption happens between functions.

Alternative: `_attrs` is being assigned to a different (corrupt) set
between construction and `add()`. The lift_expr bug had this shape;
worth ruling out.

## Actual root cause

Disassembling the self-host binary's
``user_pcc_py_frontend_codegen_runtime_abi__apply_runtime_function_attrs``
showed a direct ``bl py_set_add`` at the ``fn_attrs.add(attr)`` call
site — pcc-py codegen had statically committed to the set fast path,
ignoring the actual receiver type.

Tracing the dispatch in ``pcc/py_frontend/codegen/layer1.py``:

```python
# layer1.py:21897
def _maybe_emit_set_method(self, expr: Call) -> Optional[ir.Value]:
    ...
    name = attr.name
    if name not in ("add", "remove", "discard", "update"):
        return None
    ...
    if name == "add":
        self.builder.call(self.runtime["py_set_add"], [recv, item])
```

The dispatch is keyed on the **method name** alone. The receiver
type check (`_is_native_set_dyn(obj_ty0)`) gates this code path at
layer1.py:19593, but the type inferrer reports
``DynType(name="set")`` for at least some receivers that are not
actually sets — including ``getattr(fn, "attributes", None)`` whose
true type is ``FunctionAttributes`` — so the gate lets it through
and ``py_set_add`` lands on a non-set object.

Symptom test: rebuilding ``FunctionAttributes.__init__`` to back
``_attrs`` with a ``list`` did not change the disassembly of
``_apply_runtime_function_attrs`` — the caller still emitted
``py_set_add`` regardless. Confirmed by inspecting
``user_pcc_llvm_capi_ir_FunctionAttributes_add`` (which now correctly
called ``py_list_append``) vs. the caller's site (still
``py_set_add``).

So this is not a memory-safety UAF; it is a codegen mis-dispatch.
The "heap-layout sensitivity" / ``--verbose`` masking comes from
``py_set_add`` happening to read random heap bytes whose value
varies with surrounding allocations, not from a freed object.

## Resolution (workaround)

Two-part source-side workaround applied in commit `057ea1e4`:

1. ``FunctionAttributes._attrs`` switches from ``set[str]`` to
   ``list[str]``; ``add()`` becomes a list-membership check + append.
2. ``_apply_runtime_function_attrs`` bypasses the ``.add()`` method
   call entirely and inlines the membership-check + append directly
   on ``fa._attrs``. This dodges the codegen mis-dispatch because the
   call site never sees ``.add()`` at all.

After this: stage2 progresses past the previous codegen segfault. With
``--verbose`` it reaches ``type_infer[pcc.cli_bootstrap]`` (the second
module after pcc.__main__), vs. previously crashing inside the first
``L1CodeGen.__init__``.

## Real fix (TODO)

The right fix is in the codegen / type_infer pair:

* **type_infer**: stop typing ``getattr(<custom-class>, "attr",
  None)`` as ``DynType("set")``. The current behavior leaks set-typing
  through unrelated attribute accesses.
* **layer1 ``_maybe_emit_set_method``**: tighten the receiver-type
  check. The current ``_is_native_set_dyn`` accepts any
  ``DynType(name="set")``; it should also verify there is no
  user-defined ``__class__`` overriding ``add``, or fall back to the
  generic call path on uncertainty. A method-name keyed fast path
  only works if the receiver type is *known* to be a primitive set.

Audit candidates: any other ``.add(...)`` / ``.remove(...)`` /
``.discard(...)`` / ``.update(...)`` call site whose receiver is
returned by ``getattr`` or by a function with no return-type
annotation may suffer the same mis-dispatch.
