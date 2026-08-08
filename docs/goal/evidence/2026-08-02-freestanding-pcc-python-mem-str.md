# Freestanding pcc-Python mem/str object evidence — 2026-08-02

## Claim boundary

This slice implements the 15-symbol memory/string substrate in freestanding
pcc-Python and proves that LLVM-backed and self-backed objects are closed and
C-ABI callable.  It also proves a PCC C-frontend self-backend object can resolve
its memory/string imports to this same pcc-Python object.

It does **not** yet claim that every production link automatically selects this
object, that the old C/musl objects have been removed from every archive, or
that a complete executable is zero-libc.  Allocator, stdio, syscall/startup and
the final production-link switch remain separate boundaries.  Darwin test
executables retain the explicitly labeled libSystem process boundary.

## Implementation

- `pcc/py_runtime/py/freestanding_mem_str.py` exports `memcpy`, `memmove`,
  `memset`, `bzero`, `explicit_bzero`, `memcmp`, `memchr`, `memrchr`, `strlen`,
  `strnlen`, `strchrnul`, `strchr`, `strrchr`, `strcmp`, and `strncmp`.
- The implementation is Python source using only raw `pcc.unsafe` byte loads,
  stores and pointer arithmetic.  It contains no C source, extern libc call,
  managed object, allocator, exception or GC operation.
- Strict freestanding void intrinsics return an unowned raw null sentinel
  instead of materializing `py_None`; ordinary Python lowering is unchanged.

## Fast evidence

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_module.py \
  tests/python/test_freestanding_mem_str.py \
  tests/python/test_unsafe_atomics.py \
  tests/python/test_atomic_mirror_gap.py
45 passed in 54.11s
```

The focused mem/str suite additionally proves:

- LLVM and self-backend objects have no undefined symbols (`nm -u` empty).
- All 15 C ABI symbols are exported by the pcc-Python object.
- Portable behavior matches the host libc oracle.
- A deterministic matrix covers sizes 0..96, 8-by-8 source/destination
  misalignment, 49 overlapping `memmove` offsets, signed byte values, bounded
  strings, null searches and pointer-return identity.
- `memrchr`, `strchrnul`, `strnlen`, `bzero` and `explicit_bzero` pass explicit
  edge expectations; the secure-clear IR retains an explicit byte-store body.
- A PCC C-frontend self-backend object has unresolved mem/str symbols before
  linking and runs successfully after linking the pcc-Python self object; the
  final executable defines those symbols from that object.
- The default `libpy_runtime_pcc_py.a` Makefile recipe now archives
  `build_py/freestanding_mem_str.o` and filters all musl string-directory
  objects out of that production archive.  `libpy_runtime_pcc.a` retains the
  musl objects as the explicitly labeled C oracle archive.

After the archive switch:

```text
ar t pcc/py_runtime/libpy_runtime_pcc_py.a
  freestanding_mem_str.o
  # no vendor memcpy/memmove/memset/string object

tests/python/test_unsafe_atomics.py::test_atomic_intrinsics_single_thread_semantics[self]
1 passed in 0.34s

tests/python/test_libc_import_baseline.py + freestanding suites
26 passed, 2 deselected in 105.39s
```

## Remaining boundary

The normal pcc-Python runtime archive now uses the object and excludes the
replaced C/musl mem/str objects.  Remaining: make standalone C-frontend
production linking select the shared freestanding runtime collection (owned by
`LIBC-P2-C-FRONTEND-FREESTANDING-LIBC` after allocator/stdio/startup exist),
then run one final focused five-GC/bootstrap acceptance after the other
zero-libc families are no longer moving.
