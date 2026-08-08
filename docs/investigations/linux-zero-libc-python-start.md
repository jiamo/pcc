# Investigation: Linux zero-libc pcc-Python process entry

## Status
resolved

## Problem Description

`LIBC-P3-LINUX-ZERO-LIBC-TRACER` required an x86_64 Linux executable whose
process entry, initial-stack decoding, output, and exit behavior are authored
in freestanding pcc-Python.  The production link could contain no hand-written
C/assembly startup object, libc, interpreter, dynamic dependency, undefined
symbol, or C runtime object.

Three independent boundaries appeared:

1. the x86 self backend treated `_start` as an ordinary SysV callee, even
   though the kernel supplies no return address or argument registers;
2. the Python CLI parsed `--target` but both its host and launcher paths failed
   to propagate that target into frontend-emitted IR;
3. a fresh pcc1 misclassified `-> None` from an independently compiled type
   module and emitted `ptr` rather than `void` C-ABI returns.

## Repro

The first Docker probe emitted `unknown-unknown-unknown` despite an explicit
`--target x86_64-unknown-linux-gnu`, and self-backend emission failed closed.
After target propagation was repaired, the static tracer ran.  A fresh pcc1
then exposed the separate return-type drift:

```text
pcc0: define external void @_start(ptr %initial_stack)
pcc1: define external ptr @_start(ptr %.1)
```

The same pcc1 emitted pointer returns for `bzero` and `explicit_bzero`, proving
the third failure was generic rather than tracer-specific.

## Test [CONFIRMED]

The focused unit suite covers the `_start(ptr)` signature, original stack
capture, stack alignment, trap-on-return behavior, malformed-signature
rejection, ordinary SysV function preservation, target propagation through
both CLI paths and multi-file emission, and the self-host `None` class-boundary
case.

The Docker gate compiles the pcc-Python source to IR, lowers it through the
x86 self backend, assembles only generated assembly, and links a single object
with `ld -static -nostdlib -e _start`.  It checks `file`, `readelf -l`,
`readelf -d`, `nm -u`, the link map, `_start`, and runtime output.

## Resolution

- `_start` is a fail-closed backend contract: global `void (ptr)` only.  Its
  prologue preserves the kernel initial stack pointer, establishes 16-byte
  call alignment, and passes that pointer to pcc-Python.  Any return traps.
- `compile_python`/`compile_python_multi`, the host CLI, and the actual launcher
  path now propagate an explicit target triple into every emitted module.
- `None` return detection uses the stable semantic type name in addition to
  local `isinstance`, so independently compiled frontend modules agree on
  `void` C-ABI returns.

A fresh self-backend/no-libpython pcc1 preserves `void` for `_start`, `bzero`,
and `explicit_bzero`.  The pcc1 CLI does not yet expose cross-target `--target`;
therefore the Linux execution claim is explicitly pcc0/host-frontend plus the
x86 self backend, while pcc1 evidence proves frontend acceptance and ABI return
stability only.
