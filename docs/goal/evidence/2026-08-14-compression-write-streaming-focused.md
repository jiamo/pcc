# Compression write streaming — focused source evidence

Date: 2026-08-14

Mode proved: host-Python source checks plus host-pcc recursive-stdlib LLVM-IR
emission, `--python-libpython=off --ir-scaffold=on --backend=self`. This is not
current-pcc1 or five-GC runtime evidence.

Implemented one persistent native codec state per incremental zlib, bzip2 and
XZ encoder. Each call exposes a fixed 64 KiB native output fragment, clears
managed input/output pointers before returning, and owns an idempotent abort or
finalize path. Writable gzip, bz2 and lzma binary streams share one destination
policy that forwards every encoded result immediately, distinguishes owned
paths from borrowed file objects, finalizes on close, and rejects operations
after close. Gzip sync flush remains non-final and the RFC 1952 header, CRC and
input-size trailer are maintained incrementally.

Focused gates:

- `python -m py_compile` over the five codec modules and two focused tests:
  PASS.
- `git diff --check` over the slice: PASS.
- `pytest -q -x -n0 -m 'not integration'` over
  `test_py_stdlib_compression_streaming.py` and
  `test_py_stdlib_compression_closure.py`: **5 passed, 3 deselected**.
- Recursive-stdlib closure emission of a zlib/gzip/bz2/lzma writer probe:
  PASS; 4,808,048-byte IR and zero `call ... @py_cpy_*` instructions.

The first light pytest attempt revealed that a `pcc_gate` marker triggers
stage1 provisioning during collection even when the node is deselected. It was
interrupted after about five seconds, leftover processes were checked (none),
and the integration node was left as an ordinary integration node whose
current-pcc1 lookup occurs only when executed. No result from that interrupted
collection is counted as evidence.

Open: execute and fix the current-source pcc1 strict self/no-libpython writer
interoperability node across GC0..4 after the externally owned HARNESS/compiler
source is stable. That gate must prove CPython cross-decoding, native resource
cleanup and post-close behavior; the present evidence does not claim those
runtime properties.
