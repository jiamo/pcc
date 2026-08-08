# LINK-P1-MACHO-OBJ-SWITCH — the encoder it needs is ~78 mnemonics, not an ISA

Mode: host measurement over real self-backend output and the emitter source.
No encoder was written; this sizes the work.

## Why this row needs an encoder at all

The self path today emits *textual* arm64 asm and shells out to as(1).
Direct object emission means pcc must encode its own instructions to bytes —
the Mach-O container side is already done (macho_obj writes multi-section
objects with 7 proven relocation types, all differentially verified).

## The vocabulary is bounded and small

Sampled from two real programs compiled `--backend self --python-libpython=off`
(fib/loops/list/dict/str, then classes/exceptions/generators/floats/tuples):

```text
probe 1: 30 distinct mnemonics, 2,138 asm lines
probe 2: +8 more (asr, eor, fadd, fmov, fmul, ldrb, mul, smulh), 4,449 lines
union:   38 mnemonics, 8 directives
```

Source-derived upper bound across `self_backend_aarch64_darwin_*.py` (what
the emitter can produce at all, not just what these samples hit):

```text
~78 mnemonics: the 69 core ops (add/adds/addv/adrp/and/asr/asrv/b/b.cc/bl/
blr/brk/cbnz/cbz/clz/cneg/cnt/cmp/csel/cset/csinv/eor/f* float family/ldp/
ldr/lsl/lslv/lsr/lsrv/mov/movk/movz/msub/mul/neg/orr/rbit/rev/rev16/scvtf/
sdiv/smulh/smull/stp/str/sub/subs/sxtb/sxth/sxtw/tst/ucvtf/udiv/umov/umulh/
umull/uxtw) plus the unscaled/byte loads-stores (ldur/stur/ldurb/sturb/ldrb/
strb) and branch protection (paciasp/autiasp) and ret
8 directives: .byte .globl .long .p2align .quad .section .space
              .subsections_via_symbols
```

Two structural facts that make the switch tractable:

- **All 8 directives are already covered** by the macho_obj section model:
  `.section`/`.globl`/`.p2align` map to Section/TextSymbol fields, `.quad`/
  `.long`/`.byte`/`.space` are data bytes, `.subsections_via_symbols` is a
  header flag the writer always sets.
- The A64 fixed-width encoding groups these ~78 into about a dozen encoding
  families (data-processing immediate/register, load/store unsigned-offset /
  unscaled / pair, move-wide, branches, compare-and-branch, conditional
  select, floating data-processing, conversions). An encoder for the
  emitter's own vocabulary is a bounded project with an obvious differential
  oracle: byte-compare against as(1) output instruction by instruction on
  the existing self-backend test corpus.

## Suggested slice order (recorded, not started)

1. Encode the probe-1 30-mnemonic subset; differential = as(1) on pinned asm.
2. Extend to the float/conversion family (probe-2 set).
3. Sweep the remaining source-derived ops with generated operand-shape cases.
4. Route `_emit_compiled_units_self_backend`'s emit-obj path through
   macho_obj + encoder behind a flag, ld kept as the working default
   (that flag flip is the row's actual switch, gated on the bootstrap matrix).

## Update, same day: slice 1 landed — the core encoder, byte-identical to as(1)

`pcc/backend/arm64_encode.py`: a two-pass assembler for the self backend's
own asm dialect. 30+ mnemonics across the measured operand shapes — add/sub
(register, imm12, sp forms, @PAGEOFF), cmp, and/orr/eor (register + bitmask
immediates with a real N:immr:imms encoder), mov aliases (orr vs add-sp),
movz/movk with shifts, asrv/lslv/lsrv, adrp (@PAGE/@GOTPAGE), ldur/stur/
ldurb/sturb (unscaled), ldr/str/ldrb/strb (unsigned offset + @GOTPAGEOFF),
ldp/stp (pre/post/signed), b/b.cc/cbz/cbnz (local labels, both directions),
bl (local or extern with BRANCH26), csel/cset, paciasp/autiasp/ret.

`tests/python/test_arm64_encode.py` (4 passed; 32 across the six LINK-track
suites):

- **Byte differential**: a 67-instruction corpus covering every shape is
  assembled by as(1) and by the encoder; every instruction word matches
  exactly, with per-line diagnostics on divergence.
- **Relocation differential**: the extern-referencing instructions produce
  the same relocation table as as(1) (BRANCH26, PAGE21, PAGEOFF12,
  GOT_LOAD pair), with the same zero-filled fixup fields.
- **End-to-end**: encoder output feeds `macho_obj.emit_object` directly
  (its Relocation type IS the writer's), the system linker links it against
  a cc-built main, and the binary runs correctly — the first machine code
  both encoded and containerized by pcc with no assembler in the path.
- **Fail-closed**: unknown mnemonics, immediate overflows (imm12/imm16/
  imm9/bitmask), extern `b`, directives, duplicate/unknown labels all raise
  EncodeError instead of mis-encoding.

Remaining for the switch: the float/conversion family and the source-derived
tail (~40 more mnemonics, same method), a directive-level driver that maps a
full self-backend .s (sections, .quad/.byte/.space data, .globl) onto
Section lists, and then the flag-gated emit-obj route with ld as oracle,
gated on the bootstrap matrix.

## Update, same day: slice 2 — the FULL emitter vocabulary is encoded

Slice 2 extends the encoder to everything the sizing measured. New families,
each differentially proven byte-for-byte against as(1) in the same corpus:

- integer multiply/divide: mul (madd alias), msub, sdiv/udiv, smulh/umulh,
  smull/umull, neg (sub-zr alias)
- immediate shifts as sbfm/ubfm aliases: asr/lsr/lsl #imm, both widths
- flag-setting adds/subs, tst (ands-zr alias)
- extends and bit ops: sxtb/sxth/sxtw, uxtw (ubfm), clz/rbit/rev/rev16
- conditional: cneg (csneg alias), csinv; blr, brk
- double-precision floats: fadd/fsub/fmul/fdiv, fneg/fabs/fsqrt, fmov
  (d-d / d-x / x-d), fcvt (s-d both directions), scvtf/ucvtf (x and w),
  fcvtzs/fcvtzu, fcmp (register and #0.0), fcsel
- the popcount vector triple exactly as the emitter writes it:
  cnt vN.8b / addv bN / umov wN,vM.b[0]

Coverage check: of the 77 source-derived mnemonics, every one now appears in
the differential corpus (~128 instructions), and every instruction word
matches as(1) exactly. The encoder's vocabulary IS the emitter's vocabulary.

What remains for the switch is no longer encoding: a directive-level driver
that maps a full self-backend .s (sections, .quad/.long/.byte/.space data,
.globl) onto macho_obj Section lists — all eight directives already have
Section-model equivalents — and then the flag-gated emit-obj route with ld
as oracle, gated on the bootstrap matrix.

## Update, same day: slice 3 — the directive driver, proven on 2,138 lines of real output

`pcc/backend/arm64_asm_driver.py` parses the emitter's *file* dialect —
interleaved re-declared sections, `.p2align` padding, `.globl`,
`.quad/.long/.byte/.space` data items (symbol-valued `.quad` ± offset becomes
an UNSIGNED relocation with the addend in the pointer bytes), labels — and
produces the Section list `macho_obj.emit_object` takes.

Two as(1) semantics were discovered by the differential and are now encoded
and pinned, not guessed:

- **Section ordering is by segment** (`__TEXT` before `__DATA`) regardless of
  where the `.section` directives appear; within a segment, first appearance
  wins. Load-bearing: section order decides vmaddr layout and every symbol's
  n_sect/n_value.
- **The atom rule for same-file calls**: with `.subsections_via_symbols`,
  a `bl` to a symbol in a *different* atom gets a BRANCH26 relocation with a
  zero-filled field (the linker may reorder subsections), but a `bl` whose
  target is the *same* atom — the recursive-call case — is resolved inline
  with no relocation, because intra-atom offsets survive any reordering.
  Found when a real fib's recursive call diverged; verified in isolation on
  both shapes.

The full-scale proof: 2,138 lines of genuine self-backend output (the
fib/list/dict/str probe program) through `assemble_file` + `emit_object`
versus as(1) on the same file —

```text
__TEXT,__text   payload IDENTICAL (7,452 bytes)   relocs IDENTICAL (299)
__DATA,__const  payload IDENTICAL (109 bytes)     relocs IDENTICAL
__DATA,__data   payload IDENTICAL (332 bytes)     relocs IDENTICAL
symbols         IDENTICAL (66)
```

`tests/python/test_arm64_asm_driver.py` pins a miniaturized real-dialect
shape (including the recursive same-atom call and a `.quad sym+offset`
table) with payload/reloc/symbol equality plus link-and-run equality;
37 passed across the seven LINK-track suites.

What remains for the row is exactly one step: route
`_emit_compiled_units_self_backend`'s emit-obj path through
assemble_file + emit_object behind a flag, ld as oracle, gated on the
bootstrap matrix. Everything before the flag now exists and is proven.

## Update, same day: slice 4 — the opt-in route is wired end to end

`PCC_SELF_OBJ=pcc` now routes `--backend self --emit-obj` through
`assemble_file` + `emit_object` — no as(1) in the path. Default behavior is
byte-for-byte unchanged (the flag defaults off; flipping it is the switch and
stays gated on the bootstrap matrix). The routed path fails closed: once
explicitly requested there is no silent fallback to as(1), per the S-track
no-silent-fallback rule.

`tests/python/test_self_obj_pcc_route.py` (2 passed) proves it on a real
compile — a recursive fib + cstring + loop program through the actual
`pcc --backend self --emit-obj` CLI, once via as(1) and once via the pcc
route: identical section payloads, relocation tables, and symbols; and the
pcc-routed object links against a cc main and computes the right checksum at
runtime. Default-path smoke stays green.

Everything up to the default flip now exists, is wired, and is proven. The
flip itself is one environment-variable default plus the five-backend
bootstrap matrix as evidence.
