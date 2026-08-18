# Stage2 complete-path owners and the struct native-decode slice (v82)

Date: 2026-09-06. Status: two serial-phase owners profiled by caller and
leaf on pcc1; the first vertical slice (native `struct.unpack_from` for the
compiled `pcc/py_stdlib/struct.py` provider) is accepted through worker
replays and a source-frozen Stage1 -> Stage2 pair. Stage3 remains deferred
by the human; no parity or fixed-point claim.

## Complete-path profile receipts (v81 lineage, GC0, self, no libpython)

`scripts/pcc_profile.py` (self time, binary identity checked) and
`scripts/pcc_flamegraph.py cpu --folded` (callers) on the v81 pcc1 replaying
retained v80 Stage2 sidecars under the recorded Stage2 environment.
Artifacts: `build/tuple-noop-scan-profile-v81/{pyast,pyast-flame,module1-flame}/`.

PCO lane, `py_ast` (`module_83.direct.pidx`), 6,035 folded samples:

```text
family (leaf self time)          share
py-runtime other                 27.3%
refcount protocol                13.2%   incref/decref prepare+finish, release
barrier/root/frame               11.9%   pcc_gc_load_ptr, store_root, frame enter/leave
granule provenance               11.8%   pcc_gc_granule_is_object_start (9.9% alone)
gc other                          7.9%
pin / relocation notes            6.7%
provenance probe                  5.1%   pcc_gc_pointer_is_managed + no-lock chain
alloc                             3.0%
compiler (self backend) code      5.9%

nearest compiler caller (inclusive)
pcc.backend.native_object._SpanReader.unpack   18.0%  } struct.Struct.unpack_from
pcc.backend.precise_stackmap._take              7.7%  } 25.7% total
self_backend_precise_stackmaps heapsort          9.3%  (_swap/_sort_final_stack_map_records
                                                        via CompilerIntArena get/set_unchecked)
```

ASM lane, `cli_bootstrap` (`module_1.direct.pidx`), 10,621 samples: the GC
protocol family is 61% (granule provenance 15.3%, refcount 12.0%, gc other
11.1%, barrier/root 8.6%, probe 5.2%, pin 4.8%, alloc 4.0%);
`_pcc_gc_granule_is_object_start` is the top leaf at 12.5%; no struct frames.
Nearest callers: `emit_indexed_module_file` 24.1%,
`_build_function_stack_map_plan_native` 12.9%, `data_emit_typed_initializer`
11.1%, verifier 12%+.

Source reading of the leaf family: `py_incref`/`py_decref` in both mirrors
(`pcc/py_runtime/py/py_obj.py`, `src/py_obj.c`) build a 56-byte prepared
record and call `_ptr_can_have_header(o)` = `pcc_gc_pointer_is_managed(o)`,
i.e. one granule radix probe per refcount operation, before reading the
header. That probe is the cross-phase owner (about 15-20% of every pcc1
phase). It is deliberately a fail-closed provenance net and is NOT changed
by this slice; see the boundary below.

Coordinator (134.6 s) and frontend-worker (103.6 s) phases were not
sampled directly in this slice; their v82 receipts moved with the struct
change (below), which shows they also execute the provider.

## Slice: native decode in the compiled struct provider

Change: `pcc/py_stdlib/struct.py` (the module pcc1 compiles for
`import struct`; module 225 of the closure). A `Struct` now resolves its
format once into integer `(kind, width, signed, count)` rows, and when pcc
lowered `pcc.unsafe` for the module (`try: ptr_is_null(null()) except
NotImplementedError`, the repository idiom), little-endian integer fields are
read in place from the immutable `bytes` payload with `load_i64`/`load_i32`/
`load_i8` at `abi_constant("object.bytes.data_offset")`. CPython, other
buffer types (copied to `bytes` first) and big-endian layouts keep the
byte-slice decode. Importing `PYBYTESOBJECT_DATA_OFFSET` from the runtime
port module instead made the cursor a dynamic global whose i64 conversion
reached `py_cpy_from_pcc_obj` and turned the function into a
`strict.nolib.stub`; the closure check caught it before any build.

Correctness gates:

- `tests/python/test_pcc_stdlib_struct.py`: 91 host tests pass, including 12
  new CPython-differential unpack cases (all widths/signedness, unsigned
  64-bit high bit, pad/bytes/char, offsets, bytearray/memoryview, big-endian,
  truncation) and a 257-record sweep.
- New `test_host_pcc_and_current_pcc1_unpack_from_matches_cpython_without_libpython`
  passes with `PCC_CURRENT_PCC1=build/struct-native-stage1-v82/pcc1`
  (`build/struct-native-pcc1-parity-v82.log`). The pre-existing subnormal
  pack node fails on HEAD text (`struct format code 'd' is not owned yet`),
  untouched by this change; routed to `PY-P0-IEEE-SUBNORMAL-BIT-CONVERSION`.
- Strict closure emission of the module: 0 stubs, native loads present, the
  same 44 `py_cpy_*` declarations and 0 calls as the HEAD emission.
- Compiled parity program (74 lines) identical to CPython under host pcc and
  under `PCC_GC_BACKEND=0..4`.

## Receipt-bound worker replays (v81 control vs v82 candidate pcc1)

Only `pcc/py_stdlib/struct.py` differs between the frozen snapshots (plus
the regenerated `libpy_runtime_pcc_py.a.provenance.json`, not a build
input); same candidate runtime archive `658c2c95...`.

`py_ast` PCO, two alternating pairs, PCO exact `cb81f6c2...`:

```text
run          wall     user     instructions        max RSS
control-1    12.05 s  11.73 s  172,081,814,344     1,052,786,688 B
candidate-1   9.21 s   9.04 s  136,460,761,410       806,420,480 B
control-2    11.97 s  11.77 s  171,987,100,572     1,052,770,304 B
candidate-2   9.35 s   9.18 s  136,417,776,325       806,420,480 B
```

Worker CPU 1.29x, instructions -20.7%, RSS -23.4%. Nine further PCO
modules all exact, user 59.19 s -> 48.71 s (1.215x), RSS -12% to -18%.
`cli_bootstrap` ASM exact, 26.42 s -> 26.29 s (unchanged). Artifacts:
`build/struct-native-pyast-replay/`, `build/struct-native-pco-population-replay/`,
`build/struct-native-module1-asm-replay/`.

## Source-frozen Stage1 -> Stage2 (v82)

```text
                       v80        v81        v82
Stage1 wall            185.70 s   187.21 s   170.76 s
Stage1 CPU             736.87 s   730.65 s   689.41 s
Stage2 wall            566.617 s  536.959 s  474.406 s
Stage2 compile CPU     1788.9 s   1650.6 s   1437.6 s
Stage2 sys CPU         203.3 s    189.9 s    149.6 s
Stage2 tree peak       8.032 GB   8.030 GB   8.029 GB
  coordinator          132.4 s    134.6 s    124.1 s
  frontend workers     103.9 s    103.6 s     94.5 s
  ASM emit             119.9 s    119.2 s    116.5 s
  PCO emit             126.7 s     98.5 s     65.2 s
  pcc-owned link        72.7 s     70.0 s     63.5 s
Stage2 / Stage1        3.05       2.87       2.78
pcc2                   8e2f7ea6   1f6f7c2d   38863e8f   (all libSystem only)
```

Receipts: `build/struct-native-stage1-v82/`, `build/struct-native-v82-build-guard/`,
`build/struct-native-stage2-v82/`. Same recipe as v80/v81 (frontend auto,
self-backend 2, link 8, 8 GiB guard, 600 s watchdog, cache off).

## Supported claim

On the frozen v82 source, GC0, self backend, no libpython: the compiled
struct provider decodes exactly like CPython, and the whole Stage2 moved
62.5 s (11.6%) with unchanged peak memory, dominated by the PCO phase
(-33.8%) plus smaller coordinator/frontend/link movements.

## Not proven / open

Stage2 is still 2.78x Stage1. Stage3 and the fixed point are deferred by the
human. The next measured owner is the refcount provenance probe
(`_ptr_can_have_header` -> `pcc_gc_pointer_is_managed`, 15-20% of every pcc1
phase); changing it trades a fail-closed provenance net for speed and needs
an explicit design decision. The stack-map heapsort through arena
getters/setters (9.3% of the PCO worker) is the next mechanism-level slice.

## Addendum (same day): fallback ratchets and the provenance-probe ceiling

- `tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  with `-vv -x -n0 --durations=15`: 45 passed in 640.96 s
  (`build/struct-slice-fallback-ratchets-v82-vv.log`). The slowest node is
  117 s, so the earlier 600 s wrapper simply expired; that run is not
  evidence and this one is.
- Diagnostic ceiling for the cross-phase owner: a never-to-land variant
  (`_ptr_can_have_header` returning True, snapshot `pcc-provdiag-v83d`,
  runtime `b4ad215b...`, pcc1 `0dc59fdb...`, `build/provdiag-stage1-v83d/`)
  replayed against v82 on the same sidecars, exact outputs:
  `py_ast` PCO 9.14 -> 8.21 s user (instructions -11.0%), `cli_bootstrap`
  ASM 26.42 -> 23.48 s (-12.8%). Removing the provenance probe from every
  refcount op is therefore worth about 10-13% of each pcc1 phase. It trades
  the fail-closed net that turns stale/foreign pointers into silent no-ops
  for that speed; it is presented to the human as a design decision, not
  taken. Artifacts: `build/provdiag-replay/`.
- Frontend codegen worker for `py_ast` on v82 (`build/struct-native-profile-v82/frontend-pyast/`):
  GC protocol family again about 60% of self time; `struct._unpack_plan`
  remains 13.9% inclusive (now list append/tuple protocol, not decoding)
  and the stack-map heapsort about 9%.
