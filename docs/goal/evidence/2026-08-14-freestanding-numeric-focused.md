# Freestanding numeric focused evidence — 2026-08-14

Mode: host LLVM execution, host/self object emission and native C consumer;
no production runtime archive or pcc1 build.

All five cases in `test_freestanding_libc_numeric.py` passed individually:
math symbol closure, C consumer link, deterministic/random math execution,
full decimal/hex `strtod` oracle, and LLVM/self errno/fenv differential.

The first self-object run exposed parser constant-branch folding that left a
removed CFG edge in a phi. The parser now prunes incoming edges against the
canonical terminator, with an exact regression. The first execution run then
exposed unary float negation as `0.0 - value`, which loses negative zero;
normal and low-IR lowering now use IEEE `fneg`, with an emit-only regression.
After both fixes, the complete self-backend plus verifier suite passed 304/304.

Linux static zero-libc ownership, GC0..4 and sequential fixed point remain
open. Bounded-ULP functions are not claimed correctly rounded.
