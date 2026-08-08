# Investigation: freestanding GC frame-registry production ownership

## Status
resolved

## Problem Description
`LIBC-P2-FREESTANDING-GC` still obtains native frame enter/leave, duplicate
slot-key restoration, LIFO removal and the size-bucketed frame-node pool from
the large managed `py_gc_backend.py` object. The shared mapped-root visitor is
already strict, and the frame index plus root-map decoding already have strict
pcc-Python owners. The next finite boundary is therefore registry mutation and
node storage only; collector mark/promotion/relocation stays outside this slice.

Relevant prior work:

- `freestanding-gc-mapped-root-visitor-ownership.md`
- `gc-frame-index-entry-pool-perf.md`
- `gc-backend3-frame-root-slot-rewrite.md`

## Repro
Run the ownership gate before the new strict frame module exists:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_frame_registry.py
```

Expected pre-change result: the strict module is absent and all four note-frame
symbols plus their node-pool helpers remain in `py_gc_backend.py`.

## Test [N/A]
This is an ownership migration rather than a reported crash. The gate must
prove exact LLVM/self closure, unique archive ownership, GC0..4 parity with the
C oracle for duplicate/LIFO/invalid-map/pool-reuse behavior, and threaded
mutation versus introspection before closure.

## Proposals
- No.1 Move frame registry mutation and its node pool into one strict module [accepted]

## No.1 Move frame registry mutation and its node pool into one strict module

### Code Change
Create `freestanding_gc_frame_registry.py` as the sole owner of the four
`pcc_gc_note_frame_*` entrypoints and their node create/unlink/pool helpers.
Consume the existing strict frame-index, root-map, cycle-publication and graph
lock ABIs. Keep the public `pcc_gc_frame_*` wrappers in `py_obj.py` and keep all
collector scanning providers outside this module.

### Result
`freestanding_gc_frame_registry.py` is now the unique production owner of the
four public frame mutation entrypoints and ten node/pool helpers. Exact
LLVM/self/fresh-pcc1 object closure, unique archive ownership, GC0..4 C-oracle
parity, threaded mutation/observation and 169 downstream tests are green.

The finite raw-only frame-index and cycle-publication signatures stay outside
global `RUNTIME_SIGNATURES`; the fresh stage1 profile retained 321 native
object cache hits with only four misses. Full commands and measured results are
recorded in
`docs/goal/evidence/2026-08-03-freestanding-gc-frame-registry.md`.
