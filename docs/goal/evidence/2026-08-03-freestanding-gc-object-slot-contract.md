# Freestanding pcc-Python GC object-slot contract

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
16de8afd...  pcc/py_runtime/py/freestanding_gc_object_slots.py
7388f270...  pcc/py_runtime/py/py_gc_backend.py
e4b57aab...  pcc/py_runtime/py/py_obj_gc.py
b2fb801f...  pcc/py_frontend/codegen/runtime_abi.py
a7e10ec3...  pcc/py_frontend/pipeline.py
29916a1e...  pcc/py_frontend/codegen/unsafe_lowering.py
de6a5b7b...  pcc/llvm_capi/ir.py
f9755994...  tests/python/test_freestanding_gc_object_slots.py
4de56454...  tests/python/test_unsafe_runtime_boundaries.py
```

## Claim boundary

`freestanding_gc_object_slots.py` is now the single production pcc-Python
owner of runtime type-layout to pointer-slot/role enumeration.  It exports
`pcc_gc_visit_object_slots(obj, visitor, context)` and covers lists, tuples,
dicts, sets, fixed owner layouts, weakrefs, continuations, classes,
C-extension traversal, instances/value boxes/user tags and the finite
pointer-free families.

Both the tracing/generational/relocating consumer in `py_gc_backend.py` and
backend 0 in `py_obj_gc.py` call this ABI with their own action callbacks.
They no longer copy any object geometry.  The callback is invoked through the
verified `call_void_ptr_i64_ptr` intrinsic.

## Fail-closed and bootstrap findings

The new unsafe boundary test exposed that strict validation admitted every
registered `pcc_gc_* () -> i64` extern as a read-only query.  Admission is now
an explicit finite set.  All strict runtime modules use
`gc_backend_current()` instead of binding `pcc_gc_backend` directly; the
telemetry allowlist exactly matches its 81 current raw queries (`missing=[]`,
`extra=[]`).  The arbitrary direct-binding escape is rejected while the
registered fragmentation query remains accepted.

The first stage1 failed at the self link after 100.791 seconds because the
three-parameter callback signature exposed a missing
`FunctionType___init__3` scaffold bridge.  Repository AST audit found three is
the maximum current literal `FunctionType` arity.  Adding the bridge plus
scaffold/parity regressions made the second stage1 green.

## Object, semantic and production proof

LLVM, self and fresh-pcc1 objects define exactly the nine exported slot
contract symbols. Their undefined closure is exactly:

```text
pcc_capi_is_cext_type_tag
pcc_capi_visit_cext_object_slots_i64
py_set_dummy
```

Compiled C-ABI probes prove slot addresses and roles for all layout families,
including weakref target role 3, class method and `del_method` update-only
metadata, C-extension callback preservation and pointer-free classification.
The production archive assigns every symbol uniquely to
`freestanding_gc_object_slots.o`.

Focused results on the final source:

```text
7 passed                 # object contract LLVM/self/production closure + behavior
31 passed                # C/pcc-Python slot and referent parity ratchets
30 passed                # strict-module and unsafe boundary validation
4 passed                 # telemetry LLVM/self differential + production archive
6 passed, 74 deselected  # generational layout/action source consumers
1 passed, 127 deselected # backend4 class metadata consumer
5 passed, 98 deselected  # production list/task/instance relocation + list/class oldify
1 passed                 # root-registry pcc-Python vs C oracle, GC0..4
4 passed + 1 passed      # FunctionType scaffold and direct LLVM-CAPI bridge
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-object-slots-stage1-v2-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-object-slots-stage1-v2 --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=80916 \
  output=build/libc-gc-object-slots-stage1-v2/pcc1
```

The fresh pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off` and `--python-library` in 0.84
seconds.  Clang and nm confirmed the same nine definitions and three imports.

## Not proven

Managed action providers still own promotion/oldification, relocation policy,
mark/sweep/refcount algorithms, weakref/finalizer/resurrection sequencing and
other collector state machines.  No claim is made that all production GC C or
managed GC symbols have moved.  Long-running GC0..4 metrics and the final
pcc1->pcc2->pcc3 five-GC matrix remain intentionally deferred until the last
GC migration slice.
