# Freestanding primitives: five-GC self-host fixed point

Date: 2026-08-03

Task: `LIBC-P1-PRIMITIVES`

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree; bootstrap source identity
`fb33b5e2869fbb6d88c3a56f9b35ca499c6b760308a449a6a9e698dafad1c93d`.

## Claim

The freestanding module discipline and syscall/atomic primitive surface pass
their focused LLVM/self-backend gates and one current-source five-GC
`pcc1 -> pcc2 -> pcc3` acceptance matrix.  Every backend success manifest
records `backend=self`, `python_libpython=off`, no libpython linkage, successful
publish barriers, and normalized pcc2/pcc3 equality.

This closes only `LIBC-P1-PRIMITIVES`.  It does not claim that the downstream
freestanding allocator, libc, or GC migrations are complete.

## Focused gates

```text
7 passed in 1.17s
  tests/python/test_atomic_mirror_gap.py

19 passed in 2.18s
  tests/python/test_unsafe_atomics.py
  tests/python/test_arm64_encode.py

28 passed in 2.85s
  tests/python/test_freestanding_module.py
```

These cover the absence of the deleted C atomic helpers, ordering-explicit
atomic semantics, AArch64 encoding, LLVM/self execution, freestanding
whole-body validation, exact raw exports, and zero-undefined focused objects.

## Five-GC fixed-point gate

```text
gtimeout 1800s env -u LC_ALL uv run pytest -q -m integration \
  tests/python/gc/test_pcc_bootstrap_full_gc*.py

5 passed in 778.18s (0:12:58)
```

The harness builds one backend-independent no-libpython/self pcc1, runs the
real pcc1-to-pcc2 and pcc2-to-pcc3 chain under each `PCC_GC_BACKEND=0..4`,
checks every stage is executable and does not link libpython, and compares
pcc2/pcc3 after the repository's normalization contract.  All five current
backend manifests were written during this run with the source identity above.

```text
GC   stage2 wall   stage3 wall   normalized pcc2/pcc3   libpython
0    416.906s      119.640s      equal                  no
1     47.408s       46.083s      equal                  no
2     46.827s       43.251s      equal                  no
3     39.506s       36.702s      equal                  no
4     66.216s       62.436s      equal                  no
```

Artifact identities are the same across the five runtime selections:

```text
pcc1  86c0e29a0a6c39ba19857a28dd3d4e3baf18ccc013c29867a553ae5cf1c8a606
pcc2  35905bbaa54c0cff0dd95f242153e5610bbae50198bc47de9ecc6d6ca54d6dbc
pcc3  7a7045f08450345ec83244c4ae0d8638d266c78e9324be60ad62f744ec4c68c6
```

The raw pcc2 and pcc3 hashes differ because normalization intentionally
removes accepted build metadata.  The authoritative fixed-point assertion is
the harness's normalized byte comparison, which is true in every manifest;
this evidence does not mislabel raw byte inequality as equality.

## Boundary disposition

The implementation evidence in
`2026-08-02-freestanding-pcc-python-atomic-closure.md` plus the focused and
five-GC gates above exhaust the task's open boundary.  The task may therefore
move from `DONE_WEAK` to `DONE_STRONG` with an empty `open_boundary`.
