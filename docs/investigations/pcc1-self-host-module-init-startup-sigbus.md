# Investigation: self-hosted pcc1 SIGBUSes at startup in the compiled-module init registry

## Status
resolved 2026-08-15 — root cause was pcc's own A64 assembler mis-encoding
`sub sp, sp, xN` (see `## Update 2026-08-15`).  The three original proposals
are all DENIED; the "garbage init_fn" evidence they rest on was a misread
dump.

## Problem Description

A freshly self-hosted `pcc0 -> pcc1` build (963 MB image, ~850 MB `__pcc_stackmaps`,
53M managed locations) produces a signed executable whose stage publish barrier
fails: the first `pcc1 --help` exits with SIGBUS (rc 138, `EXC_BAD_ACCESS
(code=2)` fetching `0x4105760018`).  The crash is inside the compiled-module
registry machinery (`py_compiled_module_runtime._run_compiled_module_init`) —
an indirect call through a garbage function pointer whose value is a real
image address with bit 38 (`0x4000000000`) OR-ed in.

This is NOT caused by the 2026-08-15 link-layer speedup changes (raw-byte
stack-map merge, structural scan, GC disable, limit raises, `__DATA` filesize
fix): a binary linked by the pre-raw-merge path crashed identically (measure7
of the speedup session, exit 138).  The runtime works for small single/multi
file programs (the `main()` trailing-call convention is required — see
`self-backend-entry-main-call-dropped-exitcode-regression.md`), so the bug is
specific to the large 500+ module closure startup path.

Symptom class matches `self-backend-large-frame-pointer-bit42-spill.md`
(GC-flag / value corruption writing a stray high bit into a pointer).  Bit 38
here, bit 42 there; both manifest as "real address | stray high bits" used as
an indirect-call target or index.

## Repro

```bash
gtimeout 900s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
gtimeout 30s ./build/bootstrap/pcc1 --help > /dev/null 2>&1   # rc 138 (SIGBUS)
```

LLDB (no ASLR) at the crash:

```
stop reason = EXC_BAD_ACCESS (code=2, address=0x4105760018)
pc = lr = 0x4105760018            # = 0x105760018 (run_compiled_module_init call site) | 0x4000000000
x19 = user_py_compiled_module_runtime__cstr_equal
x20 = pcc_compiled_modules        # both unslid (lldb disables ASLR), consistent with slide 0
frame #2..N: run_compiled_module_init + 132 (repeated -> recursion or corrupted stack)
```

## Test [CONFIRMED]

- `gtimeout 30s ./build/bootstrap/pcc1 --help` reproduces rc 138 on every
  current-source stage1 build (measured on 2026-08-15, multiple rebuilds).
- Instrumentation (raw `write(2)` dump from `py_compiled_module_runtime`,
  temporary, removed after) proves: 210 `py_compiled_module_register_init`
  calls receive **garbage `init_fn` values**, all shaped
  `0xXXXXXXXX00000001` (low 32 bits == 1, random high 32) — while the
  disassembly of `_main`'s first registration computes the correct
  `0x10561cc24` (`adrp x9, 22044; add x9, x9, #0xc24`).  The corruption
  happens between `_main`'s `stur` of the init_fn and the `register_init`
  reading it back.
- The same instrumentation produced NO dump bytes when the crash was the
  SIGBUS (crash precedes the first registration call), and shifted the crash
  to `snprintf` (SIGSEGV) when the runtime was rebuilt with a variadic
  `snprintf` extern — i.e. the binary layout changes move the failure site,
  consistent with an address/value corruption bug rather than a fixed code bug.

## Proposals

- No.1 Trace the init_fn corruption between `_main`'s `stur` and
  `py_compiled_module_register_init` [DENIED — the init_fn values were never
  corrupted; the dump was read misaligned by 4 bytes]
- No.2 Check whether the module-top function-address materialization
  (`sib_fn` in the sibling-init table, relaxed GOT) is wrong for specific
  modules of the pcc closure [DENIED — materialization is correct]
- No.3 Verify the `0xXXXXXXXX00000001` pattern against the GC flag/value
  corruption class of the bit-42 investigation [DENIED as a cause — the
  `0x40 << 32` bit IS a real `pcc_gc_pin` flags write, but pinning a garbage
  pointer is the *symptom*; the pointer was garbage because the frame was
  never allocated]
- No.4 Fix `arm64_encode` to use the ADD/SUB extended-register form when an
  operand is SP [CONFIRMED]

## No.1 Trace init_fn corruption between _main and register_init

### Code Change
None yet.  The next step is a second instrumentation round with dumps at
three points: (a) `_main` right after `stur x10, [x29,#-0x20]`, (b) the
`ldur x1` before the `bl`, (c) the `register_init` entry — to locate the
first point where the value diverges from `0x10561cc24`.  Use raw `write(2)`
(no variadic externs; a fixed-arity `snprintf` extern miscompiles the call and
shifts the failure).

### Pending
Open question: which early-startup write corrupts the value, and is it a
stack-frame overlap, a register clobber by a miscompiled call, or a
GC-flag/value write into buffer memory (bit-42 class)?

## No.2 Sibling-init function-address materialization

### Pending
The `_main` sibling-init loop materializes each `sib_fn` with `adrp+add`
(relaxed GOT).  The first entry is correct in the disassembly; the dump shows
all entries garbage.  Not yet ruled in/out whether the corruption is upstream
of the materialization (the table itself) or downstream (the store).

## No.3 GC flag/value corruption class (bit-42 reference)

### Pending
The `0xXXXXXXXX00000001` pattern (low 32 == 1) does not match the bit-42
`0x40 << 32` GC-pin flag pattern, but both are "stray high/stray low bits
OR-ed into a pointer".  Compare against `py_header_flags_or` writes and any
module-global raw-pointer store in the startup path.

## Update 2026-08-15 — root cause CONFIRMED

### Correction: the "garbage init_fn" evidence was a misread dump [DENIED]

The earlier round's headline finding — "210 `register_init` calls receive
garbage `init_fn`, all shaped `0xXXXXXXXX00000001`" — is **wrong**, and every
proposal built on it is void.  The instrumented binary's stderr is a stream of
16-byte records (`u64 tag = 0x10000000a`, `u64 value`).  Decoding all 21,990
records shows **every** value is a valid image address (`0x100000000 <= v <
0x140000000`), e.g. `0x107b98c24`.  Reading the same buffer at a 4-byte offset
reproduces the reported pattern exactly (`0x07b98c24_00000001`).  The dump was
read misaligned; the init_fn table was never corrupted.

Method note: the binary that produced that dump also no longer matched the
tree (instrumentation had been removed).  A clean rebuild reproduces the crash
with **zero** output, which is the honest baseline.

### Root cause [CONFIRMED]

`pcc`'s own A64 assembler mis-encodes `add`/`sub` when an operand is `SP`.

`pcc/backend/arm64_encode.py` encoded every register-operand `add`/`sub` with
the **shifted-register** form (`_enc_addsub_reg`, base `0x0B000000`).  In that
form register number 31 decodes as `XZR`, not `SP`.  Only the
**extended-register** form (base `0x0B200000`, `option=UXTX`) reads 31 as SP.
So the emitted, perfectly valid assembly text

```asm
  mov x15, #6896
  sub sp, sp, x15        ; -> encoded as 0xcb0f03ff = sub xzr, xzr, x15
```

assembled into a **no-op**: `sp` was never decremented, so the function ran
with **no stack frame allocated**.

`emit_add_offset` (`self_backend_aarch64_darwin_regs.py`) uses the immediate
form for `|offset| <= 4095` and only falls back to the register form above
that.  Hence the exact threshold: **every function whose frame exceeds 4095
bytes got no frame**.  Small single/multi-file programs were unaffected; the
500+ module pcc closure, whose module-top functions have large frames
(`_pcc_py_module_top_pcc_backend_self_backend_parse` needs 6896 bytes), was
guaranteed to break.  This is also why the failure only appeared after the
owned Mach-O/assembler switch — `as(1)` had always encoded it correctly.

Consequence chain observed in the debugger (no ASLR):

```text
frame never allocated -> locals live BELOW sp (x29 == sp == 0x16fdfae40)
   -> every call overwrites them with the callee's own frame
   -> a local reloaded after a call held 0x16fdfae40 (this frame's own x29,
      stored there by a callee's `stp x29, x30`)
   -> codegen's cleanup called pcc_gc_pin(that value)
   -> pin does *(int32*)(obj+12) |= PY_FLAG_GC_PINNED(0x40)
   -> obj+12 aliases the high half of the saved LR at obj+8
   -> saved LR becomes 0x105760018 | 0x4000000000 = 0x4105760018
   -> `ret` fetches that address -> SIGBUS
```

The pin/unpin pairs around the same slot explain why only the *last* pin was
fatal: an earlier `pcc_gc_pin`/`pcc_gc_unpin` pair set and cleared bit 38
again, and the final unpaired pin left it set.

Evidence commands used: `memory find` located the corrupted word at
`0x16fdfae48`; a value-conditional watchpoint caught the writing instruction
(`str w21, [x22, x19]` at `pcc_gc_pin+164`); a breakpoint conditioned on
`$x0 == 0x16fdfae40` named the caller
(`_pcc_py_module_top_pcc_backend_self_backend_parse+8284`); disassembling that
function's prologue showed `neg xzr, x15` where `sub sp, sp, x15` was intended.

### Fix

`pcc/backend/arm64_encode.py`: added `_enc_addsub_ext` (extended-register
form) and `_is_sp_token`, and dispatch to it from the `add`/`sub`/`adds`/`subs`
and `cmp` paths whenever an operand token is `sp`/`wsp`.  Dispatch is on the
**token**, not the register number, because `_reg()` maps both `sp` and `xzr`
to 31 — genuine `xzr` operands must keep the shifted form.

Regression: `tests/python/test_arm64_encode.py` — the missing shapes
(`sub sp, sp, x15`, `add sp, sp, x15`, `add x12, sp, x15`, `cmp sp, x9`) are
now in `CORPUS`, so all three encoders (as(1), pinned LLVM MC, pcc) must agree
byte-for-byte.  The corpus IS the proven subset; this shape was simply absent
from it, which is why a silent mis-encode shipped.

### Verification

- Before the fix, with the shapes added: `as 0xcb2f63ff, pcc 0xcb0f03ff`
  (and two more) — RED against as(1).
- After the fix: `tests/python/test_arm64_encode.py` 11 passed; assembler +
  Mach-O link gates 72 passed, 2 deselected.
- Stage1 rebuild + `pcc1 --help`: see `## Report`.

## Report

`pcc`'s owned A64 assembler encoded `sub sp, sp, xN` with the
shifted-register form, in which register 31 is `XZR` rather than `SP`.  The
instruction therefore assembled to `sub xzr, xzr, xN` — a no-op — and **every
function needing a frame larger than 4095 bytes ran with no stack frame at
all**, its locals aliasing memory that each call it made would overwrite.  The
SIGBUS was several steps downstream: a clobbered local reloaded as a stack
address, handed to `pcc_gc_pin`, whose `flags |= 0x40` write at `obj+12` landed
in the high half of the saved LR at `obj+8`.

The fix is in the assembler, not in the GC, the module-init registry, or the
frontend: dispatch `add`/`sub`/`adds`/`subs`/`cmp` with an `sp`/`wsp` operand
to the extended-register form.  No runtime, GC or codegen semantics were
weakened, and no package or stage was special-cased.

Verified 2026-08-15 on current source (`b655bee7` + this change):

```text
stage1 rebuild                    EXIT=0, 328.8 s
./build/bootstrap/pcc1 --help     rc=0, 1905 bytes of help, empty stderr
   (was rc=138 SIGBUS, zero output, on every backend)
PCC_GC_BACKEND=0..4 --help        rc=0 on all five (was 138/138/138/139/138)
pcc1 compiling a real program     rc=0; the artifact prints fib 144 / total 5
tests/python/test_arm64_encode.py            11 passed
assembler + Mach-O link gates                72 passed, 2 deselected
tests/c/test_self_backend*.py + stackmap ABI 354 passed
tests/python/test_bootstrap_gate_baseline.py 2 passed, 2 deselected
```

Not claimed here: the `pcc1 -> pcc2 -> pcc3` fixed point and the five-GC
bootstrap matrix have not been run for this change.  Startup and a real
compile are proven for all five backends; stage2/stage3 byte identity is a
separate gate and must be run before any fixed-point claim.

Why it escaped: `tests/python/test_arm64_encode.py` is a three-way byte
differential (as(1), pinned LLVM MC, pcc) whose corpus "IS the proven subset",
but the corpus contained only `sub sp, sp, #48` (immediate) and
`sub x10, x9, x11` (register with ordinary registers).  The one shape that
mixes both — a register operand with SP — was absent, so the encoder silently
diverged from both oracles.  The corpus now covers `sub sp, sp, x15`,
`add sp, sp, x15`, `add x12, sp, x15`, and `cmp sp, x9`.

Second-order finding: the same mis-encode also affected
`emit_add_offset(<reg>, "sp", offset)` for offsets above 4095, i.e. large
outgoing-argument areas computed a wrong address (`x12 = offset` instead of
`sp + offset`) rather than merely losing the frame.  Both are fixed by the
single dispatch change.

Compile-speed context (resolved separately, see the stage1 speedup
investigation): cold stage1 went from >25 min / never completing to ~5.1 min
with the 12-file link/frontend speedup; the SIGBUS blocks the
pcc1->pcc2->pcc3 five-GC bootstrap matrix until resolved.
