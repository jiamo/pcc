# 2026-07-16 typed-array value-projection evidence

Task: `M3-TYPED-ARRAY-PROJECTION`

## Result

The finite `pcc.array[ValueClass, N]` slice now uses the existing valueclass
payload as its only typed element representation.  For the selected two-point
kernel the direct ABI is:

```text
Point                         { double, double }
pcc.array[Point, 2]           { { double, double }, { double, double } }
make_points()                 returns the nested aggregate
pick(values, index)           accepts the nested aggregate and returns Point
```

`ValueArrayType(elem, length)` is shared by annotations, constructor calls,
direct parameters/returns, and subscripts.  The accepted production surface is
deliberately fixed at lengths 1..7 and valueclass elements.  Construction
checks the exact element count/type; ordinary classes, missing/dynamic lengths,
and out-of-range lengths fail closed.  The host-Python `pcc.array` surface is
the CPython oracle for the same finite source contract.

The typed constructor writes valueclass payloads directly into the nested
aggregate.  Typed bodies contain no `py_list_new`, `py_instance_new`, or
`py_valuebox_new`.  Dynamic indexing converts a Python `int` with an explicit
overflow flag, normalizes negative indices, checks both bounds, and selects a
finite constant-index `extractvalue` path.  Overflow raises `OverflowError`;
other invalid indices raise `IndexError`.  The pending exception returns
through a zero aggregate sentinel, so LLVM and self-backend aggregate-return
functions preserve the normal TLS error protocol.

An element returned as `Any` uses the existing ValueBox object projection.  The
array itself has no object projection in this MVP: an attempted `Any`/object
escape receives the stable compile-time diagnostic `pcc.array cannot cross an
object or Any boundary; select an element first`.  A dynamically invoked
function whose signature contains the array likewise raises a native
`TypeError` instead of silently rewriting the array as a list.

The self backend also now allocates the required destination buffer for an
indirect aggregate-return call even when its SSA result is discarded.  This is
required for calls made only to observe a pending `IndexError`/`OverflowError`;
the minimized raw-IR regression proves the stack-slot rule independently of
the typed-array frontend.

## Gates

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_array_source_shape.py \
  tests/python/test_type_annotations_optional_dotted.py \
  tests/c/test_self_backend.py::test_self_backend_stackprep_materializes_discarded_indirect_aggregate_call

16 passed in 1.03s
```

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_array_projection.py

3 passed in 1.46s
```

The second gate includes CPython-host, LLVM/no-libpython, and
self/no-libpython execution of the checked-index/escape source, plus nested
aggregate signature/body assertions.

Shared-path adjacency:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py \
  tests/python/test_native_subscript_raise.py \
  tests/python/test_native_list_index_error.py \
  tests/c/test_self_backend.py \
  -k 'valueclass or subscript or index_error or sret or aggregate_call or stackprep_materializes_discarded'

56 passed, 273 deselected in 9.69s
```

The generated/static L1 host method sets are synchronized at 240 methods;
`py_compile` passed for the touched frontend/backend/test modules.  The focused
host-contract plus authoritative bootstrap-baseline nodes ended with `2 passed,
4 skipped in 0.10s`.

## Adjacent non-gate observation

The broader multi-file suite deterministically reports the pre-existing
`test_sibling_top_init_runs_before_entry` boundary (`entry\n` instead of
`lib\nentry\n`).  That source has no value array or aggregate return, and the
typed-array/self-sret minimized gates above are green.  It is outside this
task's claim and remains routed through the existing module-init-order work;
this evidence does not claim the full multi-file suite is green.

## Claim boundary

This proves the selected fixed-length, valueclass-element array shape shares
one pcc-owned aggregate projection across the frontend, LLVM, and the Darwin
AArch64 self backend, with checked index slow paths and explicit element
boxing.  It does not claim a heap-resident V4 array container, dynamic lengths,
mutation/append/slices, pointer-bearing element GC tracing, arbitrary layouts,
NumPy compatibility, GPU-buffer ownership, array object escape, x86_64 runtime
parity, or a pcc1/pcc2/pcc3 bootstrap result.
