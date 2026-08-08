# LINK-P1-MACHO-LINK-SWITCH — route wired; the linker cannot yet do the real job

Mode: host pcc, Darwin arm64, `--backend self --python-libpython=off`.

## Correction of an earlier claim in this session

An earlier version of this file (and of the row) claimed "a real Python
program linked entirely by pcc's own linker, output identical to the
cc-linked one". **That claim was false.** The `PCC_SELF_LINK=pcc` branch had
been added to one of the two link paths in `pipeline.py`, and the program
went through the other one. Both binaries were cc-linked, which is why their
output matched — and why the three tests passed:

- "outputs agree": trivially true for two cc-linked builds
- "runtime symbols are a strict subset of the archive's": true of any
  cc-linked binary too
- "codesign was not invoked": true because `ld` already ad-hoc signs on
  arm64, so skipping the re-sign left a valid signature

Caught by checking the artifact rather than the test result: the binary had a
real random `LC_UUID` and no `pcc-linked` identifier, so it could not have
come from `macho_exec`. The lesson is in the rewritten test — a test about
*which tool ran* must inspect the artifact, not compare two artifacts.

## Measured status after fixing the wiring

The flag now reaches the link step that actually runs. Handing pcc's linker
the real job (2,138 lines of emitter output + the 2.9MB runtime archive):

```text
assembled + object OK; undefined symbols: 40
LinkError: zerofill sections are not in the proven link subset
```

So: **pcc's linker cannot link a real Python program yet.** The runtime
archive's members use sections outside the proven subset — zerofill, their
own `__DATA_CONST`, `__eh_frame`, thread-local sections, `__common`. The
route refuses with a named reason instead of guessing, which is the correct
behavior and is now what the tests pin.

## What is actually proven

The linker chain works and is differentially verified **on pcc-emitted
objects within the proven section subset** — see
`test_macho_exec_link.py` (a pcc-linked binary that dyld loads and runs,
calling libSystem through pcc-built stubs/GOT), `test_macho_link_relocatable.py`
(equal to `ld -r`), and `test_macho_archive.py` (member selection on the real
archive). None of that is affected by the error above; what was wrong was the
claim about the *pipeline route*, not the components.

## Evidence (tests/python/test_self_link_pcc_route.py, 3 passed)

- the default route still builds and runs, and produces an artifact **without**
  the pcc-linker identifier (so a silent default flip would fail this test)
- the pcc route is reached and fails closed with a named reason; if it ever
  succeeds, the test requires the artifact to carry pcc's identifier before
  accepting it
- the missing section set is pinned from the real archive, so the gap cannot
  drift silently

## Remaining

1. Section-subset gaps in the linker: zerofill, nested `__DATA_CONST` inputs,
   `__eh_frame`, thread-locals, `__common`.
2. Then the flip, gated on a pcc1→pcc2→pcc3 chain with pcc2/pcc3
   byte-identity — a bootstrap-matrix run.

## Gap chain, measured one step at a time

Closing the first named gap moved the failure to the next one. Recording the
chain rather than a single "not supported" keeps the row's size honest:

```text
1. zerofill sections                    CLOSED
     merge by size with no file payload; symbols get offsets from the
     running vm size; thread-local zerofill (type 0x12) behaves the same;
     zerofill sorts last within its segment (ld rejects content after it)
     -> 61 LINK-track tests still pass

2. non-extern (section-based) relocations   IMPLEMENTED, NOT YET PROVEN
     Measured shape in the real archive: 1732 of 1734 non-extern entries are
     UNSIGNED / length 3 / non-pcrel targeting a section index — i.e. a
     `.quad <address in section N>`. The writer now carries a
     `Relocation.section=(segname, sectname)` target and emits r_extern=0
     with that section's index; the merge resolves the index, and rebases the
     stored address by (new base within the merged section - the input
     section's own addr). 61 LINK-track tests still pass.
     It is NOT yet differentially proven against `ld -r`, because the
     attempt to do so hit the next gap immediately (below). Marked
     implemented-not-proven rather than done.

3. duplicate LOCAL symbol names across inputs   CLOSED
     Measured what `ld -r` actually does rather than inventing a scoping
     scheme: it **drops** the assembler-local temporaries entirely (two
     inputs carrying `ltmp0`/`ltmp1` merge into a symbol table with only the
     five real symbols). `l`/`L`-prefixed names delimit atoms inside one
     input and mean nothing after the merge, so pcc drops them too — but
     only the ones **nothing references**. The first attempt dropped every
     `l`/`L` name and broke pcc's own executable link, because pcc emits
     cstring labels (`Lg`) as L-prefixed symbols that relocations target.
     Caught by the existing suite in the same run, which is what those tests
     are for.

     With that, merging **real cc-produced objects** now matches `ld -r`
     exactly on `__TEXT,__text` and `__DATA,__data` payloads and relocation
     tables, and on the entire symbol table
     (tests/python/test_macho_link_cc_objects.py, 5 passed).

4. __LD,__compact_unwind addend placement   CLOSED
     Measured instead of guessed: ld does not keep a section target at all.
     It converts the entry's relocation into a **symbol-target** one — the
     function-address field is zeroed and an extern relocation names the
     function that owns the address, which survives any later reordering
     where a baked-in address would not. pcc now does the same whenever a
     defined symbol sits exactly at the rebased address.

     Result: merging real cc objects matches `ld -r` on **every** section's
     payload and relocation table, plus the whole symbol table. The
     exclusion was deleted and replaced by a test pinning the semantic
     itself (zeroed field + extern relocation). 66 LINK-track tests green.

5. duplicate definitions across archive members   CLOSED
     Measured before fixing: all 169 colliding names are **local** symbols
     (`_.exc.msg.1`, `_.pcc.gc.frame.map.1`, ...) that pcc emits as private
     per-module data — 55 members define `_.pcc.gc.frame.map.1`. None are
     weak, none are private-extern. The insight is that Mach-O relocations
     reference symbols by **index**, so duplicate local names are legal; the
     limitation was this linker keying symbols by name. Colliding locals now
     get a per-input suffix and that input's relocations are rewritten to
     match. Externals still collide loudly, because a duplicate global
     really is an error.

6. section attribute mismatch across inputs   CLOSED
     "conflicting section flags (0x80000400 vs 0x80000000)": an input whose
     `__text` contains no branch targets omits `S_ATTR_SOME_INSTRUCTIONS`.
     Attributes are a **union**, not an identity — only the section TYPE
     (low byte) has to agree, which is now what is enforced.

7. zerofill ordering was checked globally   CLOSED
     The rule is per SEGMENT: `__DATA,__bss` may precede `__LD,__compact_unwind`
     because they are different segments. The writer checked across all
     sections and refused a valid object.

8. ARM64_RELOC_TLVP_* (thread-locals)   OPEN  <- current failure
     "relocation type 9 not differentially proven yet". Thread-local
     variables need the TLV descriptor layout, `__thread_vars`/`__thread_bss`
     handling, and dyld's TLV support in the executable — a family of its
     own, not an extension of what is proven.
```

Five of these eight turned out to work differently from the assumption that
preceded them — ld drops temporaries rather than scoping them, converts
compact-unwind relocations to symbol targets rather than rebasing, unions
section attributes rather than requiring identity, and treats duplicate local
names as legal because relocations index rather than name. A list written up
front of "what is probably missing" would have been mostly wrong.

The point of recording the chain rather than reporting "linker not finished"
is that each step is a distinct semantic decision — how addresses rebase, how
symbol scope works — and getting one subtly wrong produces a binary that runs
until it doesn't. Each is enforced by `LinkError` until proven, so nothing
silently degrades in the meantime, and the differential attempt is what keeps
finding the next one rather than a guess about what might be missing.

## The whole runtime now merges — and the remaining differences are measured

After gaps 1-8 the full job runs: pcc's own emitter output plus the 91
archive members its symbols pull, merged into a 2.1MB relocatable object
(12 sections, 7108 symbols). `ld -r` on the identical 92 inputs produces the
same section set, and the differences are each nameable rather than a blanket
"doesn't match":

```text
__TEXT,__text          978372B both, 54815 relocs both, payload DIFFERS
__LD,__compact_unwind   95904B both, 2997 relocs both, payload DIFFERS
__TEXT,__eh_frame       79536B both, pcc 3780 relocs vs ld 7560  <- 2x
__TEXT,__cstring        pcc 11744B vs ld 11553B                  <- dedup
__TEXT,__literal16      pcc   336B vs ld   224B                  <- dedup
symbols                 pcc 7108 vs ld 6175
```

What each one is:

- **cstring / literal16 sizes**: ld *deduplicates* literal sections
  (`S_CSTRING_LITERALS`, `S_4BYTE_LITERALS`…). Two members with the same
  string share one copy. pcc concatenates. This is a real link-time
  optimization pcc does not do yet, not a correctness gap.
- **__eh_frame relocation count**: pcc 3780, ld 7560. The first reading of
  this was **wrong** — recorded as "pcc emits half of each SUBTRACTOR pair,
  a concrete bug". Counting relocations per section across the 92 inputs and
  the merged output shows pcc is **lossless in every section**:

  ```text
  __TEXT,__text        54815 in -> 54815 out
  __TEXT,__eh_frame     3780 in ->  3780 out
  __LD,__compact_unwind 2997 in ->  2997 out
  __DATA,__const/data/thread_vars   118/111/10, all preserved
  ```

  So ld does not have twice as many because pcc dropped some; ld **adds**
  them — it rewrites the FDE/CIE structure during the merge. That is a
  different (and much less alarming) fact than a lost-relocation bug, and it
  is the second time in this session that a characterization ran ahead of
  the measurement. Both corrections are left in the record on purpose.
- **symbol counts**: ld keeps literal labels (`LC100`, `EH_Frame1`) that pcc
  drops as unreferenced temporaries, while pcc adds `$linkN`-suffixed copies
  of colliding locals. Both sides are self-consistent; the encodings differ.
- **text / compact_unwind payload**: bisected, and it is **pure ordering**.
  The first differing byte sits inside `_pcc_debug_check_release.cold.1`
  where the *instruction itself* differs (`sub sp,sp,#64` vs `#32`) — with
  identical section sizes, that means the two merges placed different
  functions at that offset, not that either corrupted anything.

  The decisive check: slice `__text` by symbol and compare each function
  body. **3043 of 3043 common function bodies are byte-identical.** ld -r
  reorders atoms; pcc concatenates in input order. Both are valid, and it
  also explains the `__compact_unwind` payload difference, whose entries
  name functions whose addresses moved.

Three of the "differences" in this section's history turned out to be in the
**measuring harness**, not the linker. Recorded together because the pattern
is the point:

1. `__DATA,__bss` / `__common` / `__thread_bss` "differed" — they are
   zerofill, have no file payload, and the script was reading from file
   offset 0.
2. `__eh_frame` looked like pcc dropping half of every SUBTRACTOR pair —
   counting the inputs showed pcc is lossless and ld *adds* entries.
3. `_fabs` and `_pow` bodies "differed" — several symbols share an address
   (aliases), and slicing per symbol gave the second name a zero-length
   body. Keyed on distinct addresses, all 2989 bodies match.

Each was caught by measuring one level down instead of believing the first
number. The two enforcement tests that came out of it
(`test_macho_link_lossless.py`, `test_macho_link_bodies_match_ld.py`) check
the property rather than the comparison, which is why they survive the
reordering that made the raw payload comparison useless.


## The wiring itself had to be reverted — the gate caught what review did not

Running the Python frontend gates after all of the above (which is the point
of running them) turned up **15 failures**, all of the shape "no IR produced;
cannot count fallbacks". The cause was mine: `_link_self_backend_with_pcc_linker`
imported `pcc.backend.macho_obj` in-process, which pulled the whole new Mach-O
toolchain into pcc's **stage1 self-host closure** — from 152 files to 160 —
and pcc's own frontend cannot compile those modules yet:

```text
PyPipelineError: codegen[pcc.backend.macho_obj]:
  NotImplementedError: Layer 1 cannot coerce ByteArrayType to int
```

AGENTS.md warns about exactly this: "`_link_with_self_backend` must not
reintroduce compiled-stage imports/calls of `pcc.backend.*`; that brings
`py_cpy_*` back into the stage1 closure." I wrote the wiring anyway and the
gate is what noticed.

The wiring is removed (closure back to 152 files, no macho modules in it,
multi-file compile green, 27 fallback-baseline tests passing). A comment at
the site records why, so the next attempt starts from the constraint rather
than rediscovering it. **The backend modules and their 78 tests are
unaffected** — what was wrong was how the pipeline reached them, not what
they do.

LINK-P1-MACHO-LINK-SWITCH therefore needs a **subprocess boundary**, like the
other host-python seams in this file, not an in-process import. That is now
the row's first remaining step, ahead of the flip.


## The subprocess seam — and the same wiring bug a second time

LINK-P1-MACHO-LINK-SWITCH now routes through `scripts/pcc_link_macho.py`, a
**subprocess** entry point, so `pipeline.py` never imports `pcc.backend.macho_*`
in process and the stage1 closure stays at 152 files. A test
(`test_self_link_pcc_route.py::test_the_stage1_closure_stays_free_of_the_macho_toolchain`)
now guards that directly, so the closure-pollution regression cannot recur
silently.

But wiring it exposed the **same class of bug as before**: `pipeline.py` has
*two* self-backend link sites, the seam was added to one, and the probe used
the other — so `PCC_SELF_LINK=pcc` silently linked with cc and produced a
working binary with a real random UUID and no `pcc-linked` identifier.
Identical to the earlier "two cc-linked binaries agree" mistake, one layer
down. Fixed by extracting a single `_run_self_link_command` and calling it
from both sites; a test now asserts that under the flag the output either
carries pcc's identifier or the link fails with no binary — silent cc output
fails it.

With both sites wired, the route is genuinely reached, and it fails closed on
a real program with a named error:

```text
LinkError: segment __LD is outside the proven layout
```

`__LD,__compact_unwind` is a linker *input* (unwind metadata ld consumes into
`__unwind_info` or drops); `macho_link` already merges it correctly, but
`macho_exec` — which lays out an executable — does not yet handle that
segment. That is the row's next slice, and it is the first gap on this path
that is genuinely about the executable layout rather than the merge.


## Executable-link gaps, continuing

`__LD,__compact_unwind` in the executable link (CLOSED). ld consumes unwind
metadata into a synthesized `__TEXT,__unwind_info`; pcc drops it (no
synthesis), which costs only unwinding *through* those frames — C++
exceptions and backtraces, already out of scope. Dropped, not silently
ignored: reported on stderr, and a live relocation into a dropped section
raises rather than producing a binary that jumps into nothing. Three code
sites had to agree (section classification, symbol-address mapping, and
relocation application all iterate the full section list). Tests pin that a
compact-unwind input links and the section is gone, that a live reference
into it fails loudly, and that a genuinely-unknown segment still fails closed.
68 -> 9 new tests green.

GOT loads of defined symbols (CLOSED, by relaxation not by rebase fixups).
Of 4727 GOT-referenced symbols in the full runtime merge, 4703 are defined
and 24 are imports. The two ways to handle the defined ones are (a) a GOT
slot each holding its own address via a rebase chained-fixup, which needs a
multi-page GOT and exact stride/page-start math (silent-SIGKILL hazard), or
(b) **relaxation** — rewrite the `ldr [GOT]` into direct addressing, which is
what ld does and what avoids the hazard entirely. pcc now relaxes: a GOT load
of a defined symbol becomes `adrp` to the symbol's own page and the paired
`ldr xt,[xn,#off]` is rewritten to `add xt,xn,#off`. Only true imports keep a
GOT slot. A runtime test (`test_got_load_of_a_defined_symbol_is_relaxed_and_runs`)
loads a defined value through `@GOTPAGE`/`@GOTPAGEOFF`, reads 777 at runtime,
and asserts no GOT section was created for it.

Next executable-link gap (OPEN, sized from ld's real output): **thread-local
variables.** 5 symbols are TLV-referenced, backed by `__DATA,__thread_vars`
(type 0x13, S_THREAD_LOCAL_VARIABLES) and `__DATA,__thread_bss` (type 0x12) /
`__thread_data` (type 0x11, the initial values). Captured from a cc-linked
TLV executable, one descriptor is:

```text
__thread_vars entry (24 bytes): { thunk, key, offset }
  thunk  = 0x8000000000000001   <- a chained-fixup BIND to libSystem
                                    __tlv_bootstrap (bind bit 63, ordinal 1)
  key    = 0                     <- filled by dyld at first access
  offset = 0                     <- the variable's offset in the thread block
```

Code reaches it via `adrp desc@TLVPPAGE ; ldr [desc@TLVPPAGEOFF]`, then calls
the descriptor's thunk. So the executable link needs: `__tlv_bootstrap` as an
import; the three thread-local sections carried into `__DATA`; a descriptor
per TLV symbol with a bind thunk and the right in-block offset; and TLVP
relocations resolved to the descriptor address.

This is the largest remaining slice and the reason to stop here rather than
push it half-built: the descriptor thunk is a chained-fixup **bind in a
second segment** (`__DATA,__thread_vars`, alongside the existing GOT binds in
`__DATA_CONST`). That makes the chained-fixups structure multi-segment — each
segment its own `page_starts` — which is the exact geometry that SIGKILLs
silently when wrong, and it has bitten twice already this session. It is
sized precisely above so the next attempt starts from ld's real descriptor
rather than a guess.


## TLV fully mapped: TLVP relaxes exactly like a GOT load

The last unknown in the TLV slice is resolved by reading ld's executable
output for a `_Thread_local` access. In the object the sequence is
`adrp x0, desc@TLVPPAGE ; ldr x0, [x0, desc@TLVPPAGEOFF]`; in ld's executable
it becomes `adrp x0, <desc page> ; add x0, x0, #<off>` — the `ldr` is
**relaxed to an add**, exactly like a GOT load of a defined symbol, because
the descriptor is in-image. So TLVP resolution reuses the GOT-relaxation code
already landed this turn, targeting the descriptor symbol (defined, in
`__thread_vars`).

With that, the whole slice is mapped to concrete, coordinated changes:

```text
TLVP_LOAD_PAGE21/PAGEOFF12  -> GOT-style relaxation to the descriptor symbol
descriptor thunk  (byte 0)  -> chained-fixup BIND to __tlv_bootstrap (in __DATA)
descriptor offset (byte 16) -> the variable's thread-block offset (not a vmaddr)
__tlv_bootstrap             -> a data-only import: an ordinal, no stub, no GOT
__thread_data/_bss/_vars    -> carried into __DATA; block offsets computed
```

The minimal case (one `_Thread_local`, no code imports) is **single-segment**
chained fixups in `__DATA` — the same geometry as the working `__DATA_CONST`
GOT chain, just in a different segment. The full runtime needs both segments
(`__DATA_CONST` GOT binds + `__DATA` thunk binds), which is the multi-segment
`page_starts` case.

Why this is recorded rather than implemented in this turn: it is five
coordinated changes (import classification, thread-block offsets, TLVP
relaxation routing, descriptor thunk-bind + offset-field rewrite, and the
chained-fixups move to `__DATA`) to the most SIGKILL-prone function, best done
with the 12 executable-link tests green at every step. The mapping above —
especially TLVP=GOT-relaxation — removes the last research unknown, so the
implementing session starts from a plan, not a guess.


## TLV implemented; the full runtime now LINKS but crashes on a relocation bug

The TLV mapping was implemented and the minimal case runs. Five coordinated
changes to `macho_exec` landed:

1. **Import classification** — an import reached only by a data pointer
   (a TLV descriptor's `__tlv_bootstrap` thunk) gets an ordinal but no
   stub/GOT; only BRANCH26/GOT-load imports get those.
2. **Thread-block offsets** — `__thread_data`/`__thread_bss` concatenated;
   each thread var's offset within that block computed.
3. **TLVP relaxation** — `TLVP_LOAD_PAGE21`/`PAGEOFF12` resolve to the
   descriptor symbol exactly like a relaxed GOT load (the mapped insight).
4. **Descriptor rewrite** — the thunk field becomes a chained-fixup bind to
   `__tlv_bootstrap`; the offset field becomes the thread-block offset.
5. **Multi-segment chained fixups** — a general `_build_seg_info` emits binds
   across both `__DATA_CONST` (GOT) and `__DATA` (thunks), with correct
   per-page starts and the 4-byte `next` stride (the old single-segment code
   had a latent stride bug — `next=1` for 8-byte slots — that only the
   single-import test avoided).

The missing piece dyld needed was **`MH_HAS_TLV_DESCRIPTORS` (0x800000)** in
the mach header — without it dyld leaves the descriptors inert and the first
access calls the raw `__tlv_bootstrap`, which aborts. Found by diffing the
header flags against a cc-linked TLV binary.

**Minimal TLV runs** (`test_thread_local_variable_links_and_runs`): a
`_Thread_local int counter = 5; counter += 37` returns 42, the header carries
the flag, and `__thread_vars` is present. 11 executable-link tests pass.

**Full runtime now LINKS** (it did not before — it failed closed on `__LD`):
`vocab1.s` + 91 archive members → a 1.9MB executable. A second symtab bug was
fixed on the way (symbols in dropped unwind sections were being sorted into
the symbol table without an address).

**But the full-runtime binary CRASHES** — SIGSEGV in `pcc_module_ensure+48`,
bad access at `0xf9400108aa0003f3`, whose bytes are instructions
(`ldr x8,[x8]` / `mov x19,x0`). That signature means a `ldr [xM]` read where
`xM` held a `__text` address instead of a data slot: a relocation applied
wrong, almost certainly a GOT-relaxation edge case that relaxed a load that
should have stayed indirect. This is a concrete, findable relocation bug, not
a missing feature — the next debugging step, with the crash signature above
as the starting point. The route test
(`test_the_optin_route_links_a_real_program`) pins this exact state: the pcc
route now links a real program, and the binary does not yet run correctly; it
flips to requiring cc-equal output once the bug is fixed.


## Two structural bugs found and fixed; the full runtime now loads and runs into the GC

Debugging the full-runtime crash uncovered two real, general bugs in
`macho_exec` — both affecting ANY binary with zerofill sections, not just
this link:

1. **Zerofill read file garbage.** The section-layout loop read every
   section's bytes as `obj.data[offset:offset+size]`. A zerofill `__bss` has
   `offset == 0`, so this spliced the **mach header + __text** (the file's
   first bytes) into `__bss`. A global that should be 0 held instruction
   bytes, and `pcc_module_ensure` jumped through it → SIGSEGV. Fixed:
   zerofill sections carry no file data, occupy vm space only, and the
   `__DATA` segment `filesize` excludes them while `vmsize` includes them.

2. **`__LINKEDIT` overlapped `__DATA`.** `__LINKEDIT`'s vmaddr was computed
   from its file offset, but `__DATA`'s vm range now extends past its file
   content (the zerofill). So `__LINKEDIT` landed at the same vmaddr as
   `__DATA` → the kernel SIGKILLed the image before dyld ran. Fixed:
   `__LINKEDIT` vmaddr is `__DATA`'s vm end; its file offset stays at the
   content end.

Both are locked with a test: `test_bss_global_reads_zero_not_file_garbage`
links a program whose control flow depends on a `__bss` int being zero, and
requires it to run — it SIGKILLed before both fixes.

Isolation confirmed the multi-segment chained fixups are NOT the problem: a
3-import GOT chain runs correctly, and the minimal TLV runs; the SIGKILLs
were purely the zerofill layout above.

**State now: the full runtime LOADS and executes into the GC** — the crash
moved from `pcc_module_ensure` (a bad `__bss` pointer) to
`py_gc_index_insert+108` (a different bad pointer, `0x120009374d1c0`), i.e.
another data-layout/relocation issue in the GC index structure. Each fix
uncovered the next, which is the gap chain working. The next debugging step starts from `py_gc_index_insert+108`. 37
executable-link/link tests green.

### Crash characterized further (runtime data corruption, not a load bug)

lldb shows the GC-index globals are **correctly zero at function entry** — so
the binary loads and initializes fine; the first `py_gc_index_insert` takes
the empty-table path. The crash is later, at a bad address whose high bits are
`0x12000` (e.g. `0x1200090add118`). Two facts narrow it:

- The image loads at its preferred base `0x100000000` (no ASLR slide this
  run), yet a data pointer is still corrupt — so it is **not** a missing
  REBASE / slide problem (absolute addresses written at link time are at the
  right base).
- The `0x12000`-high-bits shape is what a chained-pointer raw encoding or an
  overrun chain-walk looks like — a data pointer being treated as a fixup, or
  a relocation applied to the wrong bytes.

### Localized to the GC-index allocation, not the linker's addresses

Registers at the crash (`py_gc_index_insert+108`, `ldrb w15,[x14,#0x10]`):

```text
x8  = 0x0              # the GC index TABLE BASE global — still zero
x11 = garbage          # capacity-1 mask — huge
x13 = x22 = 0xc0006506628   # hash, NOT reduced by the mask (mask is garbage)
x14 = x13*0x18 + x8 = 0x120009725d460   # == the faulting address
```

The global-address computation is **correct**: `adrp x20,0x100111000 ;
ldr x8,[x20,#0x3e80]` reads `0x100114e80`, the base global confirmed zero at
entry. So the linker resolved the global's address right. The bug is that the
table BASE stays 0 (the table is never successfully allocated) while the
capacity holds garbage — a **runtime failure in the GC-index allocation
path**, not a mis-resolved address. Something the allocation calls (or a
relocation/GOT-relaxation inside the allocation code) returns a wrong value,
so the table's control fields never get set correctly.

This is a much tighter localization than "chained-fixups domain": the next
step traces WHY the allocation leaves base=0 (breakpoint the allocate path,
inspect the store to the base global). Recorded with registers so the next
session starts from the exact failure, not a guess. The binary is
structurally valid — it loads, initializes, and executes into the GC; the
remaining bug is one runtime-behavior defect in a specific allocation path.


## MILESTONE: pcc's own linker produces a WORKING real-program executable

The GC-index crash was a real, general compiler bug — and fixing it, plus
adding rebases, made the whole thing work.

**Root cause (ARM64_RELOC_PAGEOFF12 immediate scaling).** Comparing the pcc-
and cc-linked binaries at `py_gc_index_insert`: cc reads the GC base with
`ldr [x,#0x3e0]` and writes `&base` with `add x,#0x3e0` — the SAME offset.
pcc had `ldr [x,#0x3e80]` but `add x,#0x7d0` — and `0x3e80 = 0x7d0 * 8`.
PAGEOFF12 encodes differently per instruction: `add` takes the byte offset,
but a load/store scales it by the access size. Writing the raw byte offset
into an `ldr` put the global at 8x its address, so the runtime read its GC
base from the wrong global and got zero. Fixed with `_pageoff12`, which
detects load/store vs add and scales; locked by
`test_pageoff12_ldr_scales_the_immediate`. This bug affected ANY global read
through `ldr`/`str`, not just the GC.

**Second piece (ASLR rebases).** With PAGEOFF12 fixed the binary ran
correctly under lldb (ASLR disabled) — `hello88 2 1`, exit 0 — but crashed
standalone. In-image data pointers held absolute addresses that do not follow
the ASLR slide. Each `UNSIGNED`-to-defined data pointer now emits a REBASE
chained fixup (offset from image base, `bind=0`), interleaved with the bind
fixups in the same per-segment chains (the chained-fixups model was
generalized from bind-only to bind+rebase).

**Result:**

```text
$ ./v1_full        # pcc-emitted asm -> pcc object -> pcc link -> pcc rebases/binds
hello88 2 1        # 5 launches, ASLR slides each time, all correct, exit 0
$ PCC_SELF_LINK=pcc pcc --backend self ... link_probe.py -o prog
$ ./prog
10                 # correct, exit 0
```

A real Python program — compiled through the full runtime (GC, thread-locals,
GOT imports, `__bss`, thousands of in-image data pointers), linked entirely by
pcc's own Mach-O toolchain — now runs standalone under ASLR and produces the
same output as the cc/ld route. `test_self_link_pcc_route.py` requires
cc-equal output and ASLR robustness on THREE programs covering distinct
runtime paths: a simple loop; a richer one (classes, multi-arm exception
handling, dict/list/string ops, integer division) exercising real object
allocation through the very GC-index path the PAGEOFF12 fix repaired; and a
generator program (coroutine frames, heap frame state, suspend/resume, tuple
unpacking). All three match the cc route across repeated ASLR-slid launches
(`pt=25 3 [-1, -2, 20]`, `sq_total=55 [0, 1, 1, 2, 3, 5, 8, 13]`). The stage1
closure stays at 152 files.

This is the opt-in half of LINK-P1-MACHO-LINK-SWITCH **working end to end**.
The row stays DONE_WEAK only because flipping the DEFAULT still requires the
pcc1→pcc2→pcc3 bootstrap matrix with pcc2/pcc3 byte-identity — a long run that
needs authorization — not because the route does not work.
