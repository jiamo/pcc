# Investigation: cold self-host stage1 build >25 min / never completes — link + frontend speedup to ~5.1 min

## Status
resolved (2026-08-15) — cold `pcc0 -> pcc1` self-backend build measured
311 s (~5.1 min) end-to-end, pcc1 produced and code-signed.  A separate
startup SIGBUS (see `pcc1-self-host-module-init-startup-sigbus.md`) still
blocks the pcc1->pcc2->pcc3 five-GC bootstrap matrix.

## Problem Description

User-reported: "第一步编译 pcc1 就很慢" — a cold `scripts/bootstrap.sh
--stage 1` self-backend build took >25 min and never completed (repeated
SIGKILLs at the link phase, exit -9).  Target: < 5 min.  Reference: mature
linkers (mold) and self-compiling compilers (LLVM/Go/Java/CPython) compile
their own closures quickly; pcc's single god-function
(`_emit_unsafe_intrinsic_call`, 4498 source lines → 266k IR instructions,
13.6M GC stack-map locations, 217 MB stack-map payload) made one module's
assembly 1.1 GB of text and the link quadratic in object construction.

## Repro

```bash
gtimeout 1800s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
# before: >25 min, killed at the link (semantic phase 12+ min, GC-thrash bound)
# after:  ~5.1 min (311 s), pcc1 produced
```

## Test [CONFIRMED]

- Before: measure2-6 (2026-08-15) each killed by the 25-30 min watchdog; the
  semantic link phase alone was 12+ min with `sample` showing ~84% CPU in
  CPython `gc_collect_main` (cycle collector tracing millions of short-lived
  dataclasses).
- After: measure8 — 311 s total; profile split: frontend codegen ~38 s +
  emit ~35 s + link driver ~3 min.  Link driver went from 729 s (profile of
  measure7) to ~180 s (direct full 522-file link with all fixes).
- 91 focused link/stackmap tests pass (precise stackmap ABI, macho link
  relocatable/exec/parallel/incremental, native object, arm64 asm driver,
  bootstrap gate baseline).

## Proposals

- No.1 Restore bootstrap frontend codegen parallelism (8-10 workers) [CONFIRMED]
- No.2 Pack self-backend stack-map text emission (96.7M lines -> 3.4M) [CONFIRMED]
- No.3 Batch stack-map decode / cursor-walk address offsets / inline location validation [CONFIRMED]
- No.4 Raw-byte stack-map merge in the link (no 30M object materialization) [CONFIRMED]
- No.5 Structural stack-map scan in object validation (no 3x full decode) [CONFIRMED]
- No.6 Disable CPython cycle collector for the batch link + assemble workers [CONFIRMED]
- No.7 Parallelize the pcc-Python runtime archive rebuild (make -j8) [CONFIRMED]
- No.8 Raise merged-closure limits (MAX_LOCATIONS 16M->128M, _MAX_COUNT 1M->8M) [CONFIRMED]
- No.9 `__DATA` segment filesize must end at the file payload (not the
  page-aligned end), so `__bss`/`__common` globals are zero-filled by dyld
  instead of backed by file padding bytes [CONFIRMED]

## No.1 Bootstrap frontend codegen parallelism

### Code Change
`scripts/bootstrap.sh`: `PCC_PY_FRONTEND_JOBS` default changed from `auto` to
numeric `min(ncpu, 10)`.  The `auto` source-lane policy added later
(`SOURCE_WORKER_AUTO_SAFE_JOBS=2` + oversized-serial in
`pipeline_frontend_workers.py`) caps codegen at 1-2 workers; the numeric
override takes the authoritative full-parallelism path (documented in the
repo as "Ten isolated native frontend workers are useful for the compiler
bootstrap").

### CONFIRMED
Frontend codegen phase: serial 1-2 workers for 13+ min -> 10 workers, ~38 s
wall (profile counter `multi_frontend_codegen_parallel`), ~92 s aggregate CPU.

## No.2 Pack self-backend stack-map text emission

### Code Change
`pcc/backend/self_backend_precise_stackmaps.py`: each 16-byte GC location
record was emitted as 7 asm lines (`.byte`/`.short`/`.long` one value per
line).  Packed into four little-endian `.long` words per record, 8 records
per line.  The assembler already parses comma-separated values; bytes are
identical (verified by `test_aarch64_emitter_finalizes_stackmap_after_machine_peepholes`).

### CONFIRMED
One module's `.s`: 96,783,254 lines / 1.1 GB -> 3,412,758 lines / 458 MB.
Whole-closure asm text ~4.7 GB -> ~2.3 GB.

## No.3 Batch stack-map decode / cursor-walk / inline validation

### Code Change
`pcc/backend/precise_stackmap.py`:
- `decode_stack_map` location loop: per-record `struct.unpack_from` ->
  `_LOCATION.iter_unpack` over the location byte range.
- `function_address_offsets`: cursor-walk without a second full decode
  (fixed a bug where `_take` advanced past the header and the returned
  offset was wrong).
- `validate_stack_map` location loop: inlined `_validate_location` checks
  (same checks/errors, no per-location function call + dataclass attr reads).

### CONFIRMED
Per-giant-file worker chain (assemble + from_sections + encode): >320 s ->
73 s with inline validation; ~20 s with the structural scan (No.5).

## No.4 Raw-byte stack-map merge

### Code Change
`pcc/backend/macho_link.py` + `precise_stackmap.merge_stack_map_payloads`:
the old decode -> merge -> encode path materialized ~30M `StackMapLocation`
objects several times per link.  The raw merge structurally scans each input
(cursor-walk) and concatenates the per-function byte ranges sorted by
function id, zeroing the function-address fields (relocations bind them).
Byte-identical to the old path on a two-function test; 62 link contract
tests pass.

### CONFIRMED
Cache-hit min link (1 giant + 5 small): ~100 s semantic -> 38 s total.
Full 522-file link: 12+ min -> ~3 min.

## No.5 Structural stack-map scan in object validation

### Code Change
`pcc/backend/macho_obj.py` `_validate_section`: the `__pcc_stackmaps` guard
called `decode_stack_map` (full 30M-location materialization) once per object
construction, and `from_sections` + `__post_init__` + `encode` each ran it
(3x per giant file).  Replaced with a structural scan (magic/counts/bounds/
trailing) via `_scan_stack_map_payload`; the full semantic decode+validation
remains the final executable-boundary gate.

### CONFIRMED
Per-giant worker chain 73 s -> ~24 s (assemble ~20 s + from_sections/encode
~4 s with no stack-map decode).

## No.6 Disable CPython cycle collector for batch link/assemble

### Code Change
`scripts/pcc_link_macho.py` and `pcc/backend/macho_assemble_worker.py`:
`gc.freeze()` + `gc.disable()` for the batch process (the objects are
acyclic — relocations name symbols by string — so refcounting suffices);
`gc.enable()` + `gc.collect()` before publish in the driver.

### CONFIRMED
`sample` of the semantic link showed ~84% CPU in `gc_collect_main` /
`deduce_unreachable` / `visit_reachable` before; the semantic phase dropped
from ~12 min to ~3 min after this plus the raw merge.

## No.7 Parallelize the pcc-Python runtime archive rebuild

### Code Change
`pcc/py_frontend/pipeline_runtime_archive.py`: the cold `make -B` runtime
rebuild (~180 pcc-Python modules through the pcc frontend, serial) now runs
with `-j min(8, ncpu)`.  The module rules are independent and the single make
process holds the build lock.

### CONFIRMED
Cold runtime rebuild ~20-30 min serial -> ~4-6 min with -j8 (one-time per
emit-identity change; steady state is provenance-hit).

## No.8 Raise merged-closure limits

### Code Change
`precise_stackmap.MAX_LOCATIONS` 16M -> 128M; `native_object._MAX_COUNT`
1M -> 8M.  The merged pcc closure legitimately exceeds the old bounds (53M
locations, >1M merged `__TEXT` relocations); the bounds remain corruption
guards.

### CONFIRMED
The full link previously failed with "too many stack-map locations" / "native
section has too many relocations"; both links now complete.

## No.9 __DATA filesize ends at the file payload

### Code Change
`pcc/backend/macho_exec.py`: the `__DATA` load command's `filesize` was
`data_file_end - data_off` (page-aligned), which backed the alignment padding
(and thus the `__bss`/`__common` globals) with file bytes instead of dyld
zero-fill.  Changed to `data_content_end - data_off` (the last file-backed
byte).  Test `test_large_mixed_data_and_bss_do_not_alias_linkedit_pages`
updated: LINKEDIT's file offset may exceed the `__DATA` content end, and the
fixup page_count is the rounded-up file span.

### CONFIRMED
Pre-fix the pcc1's `__bss`/`__common` globals read the file's padding bytes;
post-fix the layout is `vmsize > filesize` with the bss beyond the file
(zero-filled).  This was applied while chasing the startup SIGBUS (see the
SIGBUS investigation); it is a correct fix but was not the SIGBUS root cause.

## Report

Landed changes span 12 files (+2 test files): `scripts/bootstrap.sh`,
`scripts/pcc_link_macho.py`, `pcc/backend/{macho_assemble_worker,macho_exec,
macho_link,macho_obj,native_object,precise_stackmap,
self_backend_precise_stackmaps}.py`, `pcc/py_frontend/
pipeline_runtime_archive.py`, `tests/python/test_macho_exec_link.py`,
`tests/python/test_precise_stackmap_abi.py` (assertions only where noted).
Measured cold stage1: 311 s (~5.1 min).  Follow-up: the self-hosted pcc1
startup SIGBUS (separate investigation) must be resolved before the
pcc1->pcc2->pcc3 five-GC bootstrap matrix can run.
