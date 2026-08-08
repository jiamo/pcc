# Freestanding helper inventory against compiler-rt 20.1.8

This table is the finite inventory for
`LLVMREF-P2-COMPILER-RT-BUILTINS`.  The pinned oracle root is
`~/pcc_refs/llvm-project-20.1.8-full-depth1/compiler-rt/lib/builtins`.
An entry marked **target-lowered** is owned by pcc's self backend and does not
add a runtime archive symbol.  An entry marked **absent** is not silently
claimed by the Linux zero-libc runtime.

| Required family | Required ABI / operation | pcc owner and status | Pinned compiler-rt oracle |
|---|---|---|---|
| memory copy/move | `memcpy`, `memmove` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| memory fill/zero | `memset`, `bzero`, `explicit_bzero` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| memory compare/search | `memcmp`, `memchr`, `memrchr` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| string length | `strlen`, `strnlen` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| string search | `strchrnul`, `strchr`, `strrchr` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| string compare | `strcmp`, `strncmp` | `pcc/py_runtime/py/freestanding_mem_str.py`; freestanding pcc-Python C-ABI owner | absent from `compiler-rt/lib/builtins` (libc ABI, not a compiler-rt builtin) |
| signed 64-bit division/remainder | `__divdi3`, `__moddi3`, `__divmoddi4` semantics | target-lowered by `pcc/backend/self_backend_aarch64_darwin_ops.py` (`sdiv`/`msub`) and `pcc/backend/self_backend_x86_64_linux.py` (guarded `idiv`); no archive helper | `divdi3.c`, `moddi3.c`, `divmoddi4.c`, `int_div_impl.inc` |
| unsigned 64-bit division/remainder | `__udivdi3`, `__umoddi3`, `__udivmoddi4` semantics | target-lowered by the AArch64 and x86-64 self backends (`udiv`/`div` plus remainder); no archive helper | `udivdi3.c`, `umoddi3.c`, `udivmoddi4.c`, `int_div_impl.inc` |
| signed/unsigned 128-bit division/remainder | `__divti3`, `__modti3`, `__udivti3`, `__umodti3`, `__divmodti4`, `__udivmodti4` | **absent**: neither current self target legalizes i128 division nor the production archive owns these symbols | `divti3.c`, `modti3.c`, `udivti3.c`, `umodti3.c`, `divmodti4.c`, `udivmodti4.c`, `int_div_impl.inc` |
| float/double to signed 32/64-bit integer | `__fixsfsi`, `__fixsfdi`, `__fixdfsi`, `__fixdfdi` semantics | target-lowered by `self_backend_aarch64_darwin_ops.py` and `self_backend_x86_64_linux.py`; no archive helper | `fixsfsi.c`, `fixsfdi.c`, `fixdfsi.c`, `fixdfdi.c`, `fp_fixint_impl.inc` |
| float/double to unsigned 32/64-bit integer | `__fixunssfsi`, `__fixunssfdi`, `__fixunsdfsi`, `__fixunsdfdi` semantics | target-lowered by both self targets, including the x86-64 unsigned-range fixup; no archive helper | `fixunssfsi.c`, `fixunssfdi.c`, `fixunsdfsi.c`, `fixunsdfdi.c`, `fp_fixuint_impl.inc` |
| signed 32/64-bit integer to float/double | `__floatsisf`, `__floatsidf`, `__floatdisf`, `__floatdidf` semantics | target-lowered by both self targets (`scvtf` / `cvtsi2s*`); no archive helper | `floatsisf.c`, `floatsidf.c`, `floatdisf.c`, `floatdidf.c` |
| unsigned 32/64-bit integer to float/double | `__floatunsisf`, `__floatunsidf`, `__floatundisf`, `__floatundidf` semantics | target-lowered by both self targets, including the x86-64 high-bit fixup; no archive helper | `floatunsisf.c`, `floatunsidf.c`, `floatundisf.c`, `floatundidf.c` |
| conversions involving 128-bit integers | `__fix*sfti`, `__fix*dfti`, `__fixuns*sfti`, `__fixuns*dfti`, `__floattisf`, `__floattidf`, `__floatuntisf`, `__floatuntidf` | **absent**: no current i128 scalar legalization or freestanding archive owner | the correspondingly named `compiler-rt/lib/builtins/*.c` files plus `fp_fixint_impl.inc` / `fp_fixuint_impl.inc` |

The integer differential gate intentionally treats division by zero as outside
the oracle: `int_div_impl.inc` explicitly calls that case unspecified.  It does
include `INT64_MIN / -1`; compiler-rt's unsigned-magnitude shape returns the
two's-complement pair `(INT64_MIN, 0)`.  AArch64 already has that machine
behavior.  x86-64 must guard the otherwise trapping `idiv` instruction.

