# Exact-container successful-result ownership — DONE_STRONG

Date: 2026-08-21

Task: `PY-P0-EXACT-CONTAINER-DYN-SUBSCRIPT-OWNERSHIP`

## Claim

This evidence closes only the successful-result ownership boundary for exact
built-in `tuple`, `list`, and `dict` subscripts:

- a public exact-container getitem result is a NEW reference;
- Dyn and object projections transfer that one owner through an updateable
  result root into an owned local, or through a return root for boxed `int`;
- native `int`, `float`, and `bool` projections root and reload the result,
  perform the synchronous coercion, reload again, clear and leave the root,
  then release the original NEW owner exactly once;
- the path uses no temporary `pcc_gc_pin`/`pcc_gc_unpin` mutation and adds no
  libpython or dynamic-call fallback.

This is a bounded correctness result, not a performance claim.

## Frozen identities and mode

- Candidate pcc1:
  `build/stage2-stackprep-ownership-candidate-stage1-v2/pcc1`
  (`ac3c1399d29d72b78f3382143aef1057c36d80c058b34a6ff42e502daa8b3a92`)
- Candidate build receipt:
  `build/stage2-stackprep-ownership-candidate-stage1-v2/build-receipt.json`
  (`ef0ea863c597960ba36bc05bea821e82fd21dc668961890164acfa6e2cfc5ea1`)
- Source-manifest identity:
  `0f67554b51e4fc74a283fd5ddcb7c8f754f420758f887827c71f17ee7dc3f038`
- Runtime archive:
  `a439c5d5520576cbe2451ed1d423c15ab3025c29da1ad4ae2cf5d04ff8d1ebea`
- Mode: host CPython 3.13.2 produced a Darwin arm64 pcc1 with
  `--backend self --python-libpython off --ir-scaffold=on`, pcc-Python runtime,
  GC0 for the build, frontend jobs 10, self-backend jobs 8, caches and Python
  IR passes off. The compiler and produced probe link only libSystem, not
  libpython or LLVM.
- Task source hashes:
  - `ownership_lowering.py`: `cb9dafdeb4087877b86c9d6eb5d83163a52addbf31108ee534b9bc7442ec045d`
  - `subscript_lowering.py`: `7377ce5176a97ab14eb9eabd861892f8dffbcc0e6df0258040be928237401861`
  - `exact_int_lowering.py`: `7eed46f029ecf6942aae8ab537265f62a5cf4507d25d98a157438b779c78bff4`
  - focused test: `296608ffe357b7521404b3524c9f42788e390c5d44b15ba7a93e5393a829f6aa`

## Focused host IR regression

Command:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_raw_scaffold_exact_container_subscript_ownership.py
```

Result: `19 passed in 0.40s`.

The parameterized checks cover tuple/list/dict Dyn inference and owner flags;
tuple/list/dict native `int`/`float`/`bool` result consumption; tuple/list/dict
object transfer; receiver cleanup ordering; the C-ABI/raw-scaffold ordering;
and boxed exact-int return transfer. The lifecycle assertions bind the same
result SSA, initialized root slot, LIFO frame entry, relocation-aware reloads,
root clear/leave, owned flag, and permitted final release rather than accepting
unrelated calls elsewhere in the IR.

## Candidate-pcc1 native IR gate

Frozen inputs:

- `build/exact-container-ownership-ir-gate-v1/input/pcc/subscript_ownership_probe.py`
  (`76182c0e7813700b0faa9dc98e673da2bde632aedef5f2548e26fe690f331b01`)
- `build/exact-container-ownership-ir-gate-v1/input/user/subscript_ownership_probe.py`
  (`199188039795bda53fbb34b24c417877777f0aa73c991cc3fd795c2180d9c78e`)

Frozen outputs:

- `build/exact-container-ownership-ir-gate-v1/output/pcc.ll`
  (`136bfd08e9fe5287c2b86e6d99ae9e1a68b49a241992e0adb82db16619e179fa`)
- `build/exact-container-ownership-ir-gate-v1/output/user.ll`
  (`baa4d5e4c65a8d4ebde6ce7047c872ca4714f8888274597f432019ef2edb10b5`)

The candidate pcc1 compiled ten functions: tuple/list/dict Dyn, tuple `int`,
list `float`, dict `bool`, tuple/list/dict object, and boxed `int` return. The
gate passed `10/10` and established in the emitted function bodies:

- Dyn/object: getitem NEW SSA -> initialized LIFO result root -> updated reload
  -> clear/leave -> resolved owned-local store, with no direct premature release;
- scalar: NEW SSA -> result root -> first reload/coercion -> second reload ->
  clear/leave -> exactly one release of the second reload, with no pin/unpin;
- boxed `int`: shared result root -> return root -> reloaded pointer return, with
  no native-width coercion or premature release;
- no `py_obj_call`, `py_func_call_kwargs`, or libpython fallback call occurs in
  any probe function. Unused runtime declarations in the module are not counted
  as executed fallback edges.

## Five-backend focused gate

Command:

```text
gtimeout 360s env -u LC_ALL PCC_CURRENT_PCC1=build/stage2-stackprep-ownership-candidate-stage1-v2/pcc1 PCC1_BINARY=build/stage2-stackprep-ownership-candidate-stage1-v2/pcc1 uv run pytest -q -x -n0 tests/python/test_pcc1_gc_backend_matrix.py::test_pcc1_self_backend_compile_smoke_under_gc_backend
```

Durable log:
`build/stage2-stackprep-ownership-candidate-stage1-v2/candidate-gc-matrix-v2.log`
(`41b679bb695610ef66a5f10515eeaf38fde3f5f981a0bdee5c4a57f087b0d86a`).

Result: `5 passed in 206.93s`, covering GC0 through GC4. The older
`candidate-gc-matrix.log` has no final pytest summary and is not evidence.

## Explicitly unclaimed boundaries

This result does **not** close receiver/index/key ownership across evaluation or
hash/dunder calls, getitem error cleanup, late ordinary-owned-local error-exit
registration, bool truthiness exceptions, exact-int print consumers,
pcc-Python dict missing-key parity, arbitrary-precision `int` projection,
full five-GC bootstrap, pcc2/pcc3 fixed point, or any Stage2 performance claim.
Those remain separate actionable task-board rows.

## Verdict

`DONE_STRONG` for the bounded successful-result ownership rule. Every listed
exit criterion is covered by source-level IR regression, frozen candidate-pcc1
IR, and the focused GC0..4 native compiler matrix, with the claim exclusions
above kept explicit.
