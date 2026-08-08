# Freestanding pcc-Python stdio subset closure

Date: 2026-08-03

Task: `LIBC-P2-STDIO-SUBSET`

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
freestanding_stdio.py       4789763fa15eeff4908a001afeb3c88906e248d9e48189e71ce0caefeadc6346
pcc_stdio_abi.h             0f76659940f002502828a05614b8f3932628b8530ab13c88523e29b6ea3bc413
gen_freestanding_stdio_abi  b59c4cbe5d6e3f9c8dede89086d059a026e0dc1a9f5745d8d0965cf7babc7810
test_freestanding_stdio.py  81d4582a8a8e96007883e09ebc616ae7a4da224d93f0db7ab438af7d2508b97c
test_freestanding_stdio_abi 33d9fa64fc19f6d599aa8f23037e9670698b225de1604c4a7e3b60a16a3d9ae7
```

## Claim

The production pcc-Python runtime owns the finite 14-symbol stdio subset used
by pcc: `remove`, `fopen`, `fclose`, `fread`, `fwrite`, `fflush`, `ferror`,
`fgetc`, `fprintf`, `snprintf`, `vsnprintf`, `__stderrp`, `popen`, and
`pclose`. The implementation is strict freestanding pcc-Python and consumes
one generated `pcc_stdio_abi.h` layout contract plus the already-owned
allocator, memory, IO, filesystem, process, and spawn ABIs.

This remains explicitly narrower than general POSIX `FILE`/stdio support.
musl and apple-libc are semantic/layout references only; no musl stdio object
is linked into the production archive.

## Current focused proof

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_stdio.py \
  tests/python/test_freestanding_stdio_abi.py \
  tests/python/test_freestanding_variadic_export.py \
  tests/c/test_c_varargs_split.py \
  tests/python/test_freestanding_module.py

52 passed in 15.99s
```

These tests cover generated ABI reproducibility, file lifecycle and buffering,
EOF/error state, partial writes, formatting width/precision/float behavior,
native variadics, flush/close order, remove, popen/pclose status, LLVM/self
objects, production archive ownership, and strict module closure.

The current Darwin import ratchet remains at 46 symbols (52 threads-on), with
the exact six-symbol pthread delta. The fourteen former stdio imports are
absent; only the lower platform operations required by the same behavior are
retained. Detailed pcc1 and archive symbol evidence is in
`2026-08-03-freestanding-pcc-python-stdio.md`.

## Fixed point and boundary disposition

No `pcc/` source changed after the current-source acceptance recorded in
`2026-08-03-freestanding-primitives-five-gc-fixed-point.md`:

```text
5 passed in 778.18s (0:12:58)
GC0..4; backend=self; python-libpython=off; normalized pcc2/pcc3 equal
```

The original differential/pcc1/archive evidence, current 52-test focused
gate, exact import ratchet, and current fixed point exhaust this task's finite
open boundary.
