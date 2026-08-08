# Freestanding pcc-Python mem/str production closure

Date: 2026-08-03

Task: `LIBC-P1-FREESTANDING-MEM-STR`

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
freestanding_mem_str.py       76e0f9f4d7f0028eb49370678f32f9d2ce6a741668b609d69ef4c8e899dfa758
test_freestanding_mem_str.py  a254adb0c95b09cd9a60e8293a564d879d828729d7f26c5cc5bbd6f0cd4af8b3
```

## Claim

The production pcc-Python runtime archive resolves its supported 15-symbol
memory/string substrate from the strict freestanding pcc-Python object. The
former musl C objects remain only in the explicitly labeled C oracle archive.
LLVM/self closed-object tests, host-libc differential behavior, a PCC C
frontend direct-consumer link, archive ownership, and the current-source
five-GC no-libpython/self fixed point are green.

This does not claim automatic standalone C-frontend selection of the complete
freestanding libc collection; that separate link-route boundary remains owned
by `LIBC-P2-C-FRONTEND-FREESTANDING-LIBC`. Darwin remains libSystem-bounded.

## Current focused proof

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_mem_str.py

8 passed in 4.58s
```

The test file exercises the exact 15 exported C ABI symbols, LLVM and
self-backend zero-undefined objects, portable behavior against host libc,
sizes 0..96, alignment and overlap matrices, bounded strings and pointer
identity, secure-clear IR shape, a self-backend PCC C-frontend consumer, and
the production archive member policy.

The current archive audit reports exactly `freestanding_mem_str.o` for this
family and no vendored musl string member.

## Current five-GC acceptance

The same current source was accepted immediately beforehand by:

```text
gtimeout 1800s env -u LC_ALL uv run pytest -q -m integration \
  tests/python/gc/test_pcc_bootstrap_full_gc*.py

5 passed in 778.18s (0:12:58)
```

All GC0..4 manifests record the bootstrap source identity
`fb33b5e2869fbb6d88c3a56f9b35ca499c6b760308a449a6a9e698dafad1c93d`,
no libpython linkage, successful stage publish barriers, and normalized
pcc2/pcc3 equality. Detailed artifact hashes and per-stage times are in
`2026-08-03-freestanding-primitives-five-gc-fixed-point.md`.

## Boundary disposition

The implementation/differential/link evidence in
`2026-08-02-freestanding-pcc-python-mem-str.md`, the current focused rerun, and
the current five-GC fixed point exhaust this task's exit criteria. The task's
open boundary is empty.
