# 003 — authorized capped Stage2 (v3): burst fix holds, the new owner is the small lane's in-process native-object path

Date: 2026-09-03.  Human authorization: one diagnostic capped Stage2 with the
timeout raised to 1500 s, cap unchanged at 8 GiB, "extract maximum value".

## Same-envelope receipts (source v14 = HEAD e63c5d64 + per-worker admission records)

```text
Stage1 v4  build/inline-edge-stage1-capped-v4   rc 0  wall 152.35s  user+sys 790s
           peak tree 5.01 GB  libSystem-only  canary 42  pcc1 sha 11cc5f94
Stage2 v3  build/inline-edge-stage2-capped-v3   MEMORY_LIMIT at 628s
           peak tree 9.04 GB (8.42 GiB) > 8 GiB cap; 31/224 modules done
```

Per-lane admission (new receipt `pcc2.pcc-codegen-plan.admission.json`):

```text
lane              width launched denied susp peak_live peak_charged max_wall max_peak
serial              1      1        0     0   6.06G      6.06G       70.8s    6.06G
paired_oversized    2      6        0     0   6.23G      6.23G       43.2s    4.00G
heavy               2      8        0     0   5.77G      7.71G       48.0s    4.21G
medium              3     16      451     0   4.13G      7.00G       30.6s    2.69G
small               4   (killed 10s after its first launches)
```

The burst is gone: every non-small lane ran under 6.3 GiB tree with zero
suspensions (medium ran at effective width 2, 451 denied polls).  The breaker
then fired in the SMALL lane: worker_27 = `pcc.py_frontend.codegen.
exception_lowering` (module 151, AST 1.92 MB) went 1.29 -> 2.44 -> 6.19 -> 6.82
GiB in 6 s (still growing) while a second small worker held 1.57 GiB.

## Single-variable replays of module 151 (same pcc1, same env, one knob)

```text
arm  pcc1        NATIVE_OBJECT   wall     peak tree   note
B2   v4 (pre)        0           10.7s     1.29 GiB   emits .s
A2   v4 (pre)        1           77.2s    13.64 GiB   emits .pco in-process
C2   HOST python     1            3.7s     0.21 GiB   same code, CPython
A5   v6 (fix 1)      1           28.6s     3.28 GiB   chunks+join assembler
A8   v7 (fix 1+2)    1           19.5s     3.27 GiB   + struct zero-copy
```

The small lane is the ONLY lane that runs `assemble_file` +
`NativeObject.from_sections` + `encode_native_object` inside the pcc1 worker
(`assembly_only=False`); the other four hand `.s` to the host-side link
driver.  A2's `.pco` is byte-identical to the host assembling B2's `.s`, so the
pcc1 assembler is correct; the cost is pcc1 execution, not logic.  (Host direct
emit differs from pcc1 direct emit: __text 528308 vs 471748 bytes, 24513 vs
24516 relocations -- a host-vs-pcc1 emitter difference recorded for the
fixed-point owner, not chased here.)

Profiles of the pcc1 worker (scripts/pcc_profile.py, self time):

- pre-fix blow-up window: `_py_bytes_concat` 66.8%, `_memset` 21.1%.
  Mechanism: pcc's bytearray append/extend/+= allocate a replacement buffer
  (PY-P0-BYTEARRAY-INPLACE-IDENTITY-MUTATION), so `code += word.to_bytes(4)`
  per instruction (118k) and `out.extend(...)` per relocation (24.5k) were
  O(n^2).
- after fix 1: `_py_bytes_new` 34.9% under `struct.Struct.unpack_from`
  (46% inclusive), called by the stack-map validator three times per object
  (from_sections validate, `__post_init__` round-trip validate, encode
  validate).  Mechanism: `pcc/py_stdlib/struct.py::_unpack_fields` copied the
  whole buffer (`raw = bytes(buffer)`) on every call.
- after fix 2 (A8): remaining 3.27 GiB is allocator growth driven by
  `assemble_file` 40% (assemble_text 28%, _encode_one 10%),
  `emit_aarch64_darwin_indexed_module` 17%, `_read_native_exports_wire` 17%
  (exports retained through emit), frontend codegen 9% (MallocStackLogging
  live-heap attribution).

## Fixes landed (all host-verified, closure-checked, byte-identical output)

1. `pcc/backend/arm64_encode.py`, `arm64_asm_driver.py`, `native_object.py`:
   chunks + one `b"".join` instead of `bytearray +=` / `.extend` / `.append`.
2. `bytes.join` did not exist in the pcc runtime (the Stage1 v5 canary failed
   with `AttributeError: join`; pcc/py_stdlib zlib/lzma/bz2/hashlib carry the
   same latent idiom).  Added `py_bytes_join` in both mirrors
   (`py/py_obj_stubs.py`, `src/py_bytes.c`), header, ABI table (chunk
   rebalanced: parts 11/17 were already 58/51 > 50 at HEAD), typed frontend
   branch for bytes/bytearray receivers only.  Test
   `tests/python/test_native_bytes_join.py`: port + cc + GC0..4, CPython-equal.
3. `pcc/py_stdlib/struct.py`: `unpack_from` reads an immutable bytes buffer
   in place (82 struct tests green).
4. Stage1 canary/closure for every changed file via the v6/v7 Stage1 builds
   (rc 0, canary 42, libSystem-only, sha c0ebe208 / daa0d22a).

## Found, measured, not yet fixed (codegen ownership leaks)

Micro-benchmarks, 300k iterations, host pcc, `--backend self
--python-libpython=off`, `/usr/bin/time -l` max RSS (baseline 3-36 MB):

```text
cur = cur + ch  in a loop (20k chars)          3 MB   no leak
cur += ch       in a loop (20k chars)        299 MB   LEAK: str += drops the old value
cur += "x" x19 inside a function, 300k calls  597 MB   same leak, no for-target involved
x = f(i); len(x)   f returns list              3 MB   no leak
len(f(i))          f returns list            116 MB   LEAK: owned list call result not released
len(f(i))          f returns str              36 MB   no leak
```

Both are generic pcc codegen ownership defects (the compiler's own source is
full of `s += ...`), and they are a direct component of the 5-10x pcc1 memory
amplification.  They belong to the consumer-boundary ownership rows
(`PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER`, `PY-P0-EXACT-CONTAINER-*`); the
probes above are the red-first tests for them.

## Not proven

- No capped Stage2 has run with fixes 1-3; the projected small-lane worker
  peak is now ~3.3 GiB for the largest small module (fits width 2 under the
  floor model).  The 600 s contract remains out of reach until the wall
  owners above move.
- The `_read_native_exports_wire` retention (~430 MB per worker) and the
  emitter's own ~450 MB are unattributed beyond the frame names.

## Addendum — ownership fixes landed and replayed (Stage1 v8, pcc1 sha f2a8d9a2)

```text
module 151 native-object replay   3.27 GiB / 19.5 s  ->  2.92 GiB / 19.8 s  (.pco byte-identical)
cli_bootstrap serial worker       6.06 GiB / 71.3 s  ->  6.14 GiB / 71.5 s  (noise; not its owner)
```

Projected small-lane worst case is now under 3 GiB against a 3.18 GiB floor,
so a capped Stage2 is expected to fit 8 GiB; its wall will still exceed 600 s.
The next authorized capped Stage2 (1500 s) is the proof and yields the full
224-module per-worker table.
