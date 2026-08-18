# Mold design transfer: accepted packed-linker slices

Mode: host-Python owned Darwin Mach-O assembler/linker replay over the frozen
No.75 464-assembly input set and the source-matched pcc-Python runtime archive.
This is link-level evidence, not a Stage2/Stage3 fixed point.

Accepted:

- No.1 consumes fully validated indexed `NativeObject` inputs without eager
  `NativeObjectView` payload/dict expansion.  Three paired wall speedups:
  1.0336x, 1.0418x, 1.0438x; user CPU 1.0379x, 1.0405x, 1.0429x;
  instructions about -3.9%; RSS/footprint about -11.8%.
- No.2 validates `.pco` through a read-only packed codec view without input
  relocation dataclasses.  Against accepted No.1, three paired wall speedups:
  1.1882x, 1.1717x, 1.1952x; user CPU 1.1878x, 1.1926x,
  1.2001x; instructions about -15.1%; memory about -12.1%.
- All eight outputs in each accepted experiment were identical 174,301,592
  byte Mach-O executables with SHA-256
  `d4694dc75cf4495388ceffcc37341944079a02f1209c1a581853eedd3c86fe20`;
  every output ran with identical `--help`, and source/archive receipts were
  unchanged.
- Focused validation includes direct/materialized/packed parity, malformed
  framing/relocation/special-stackmap rejection, 2,000 mutation differential
  cases, 590 real cached `.pco` objects with 3,775,914 relocations, packed
  same-layout incremental reuse, external Mach-O/archive boundaries and the
  driver route.

Denied/removed:

- No.3 integer symbol IDs: 0.9936x/1.0051x/0.9916x paired wall with flat or
  regressed instructions; prototype removed.
- No.5 mmap input: no uncontaminated paired evidence and no clean warmup win;
  prototype removed.

Current blocker:

- No.6 is now accepted narrowly after explicit authorization to terminate the
  remaining probe5 process groups.  Its three paired instruction ratios are
  0.9556, 0.9575 and 0.9713 with exact outputs; wall was noisy and is not
  claimed.  It removes ordinary-section relocation tuple materialization and
  reuses validation's targeted-symbol summary.  The experimental selector was
  removed and 124 adjacent focused tests pass.
- The enhanced runner checks for competing pcc/bootstrap processes before and
  after every arm and aborts rather than publishing contaminated evidence.

Primary attribution remains honest: No.75 Stage2 is 977.866s; the final owned
assembler/linker is 118.087s (about 12.1%).  The separately recorded 583.303s
`link_self_emit_objects_native` phase is IR-to-`.s` self-backend generation,
not linker time, and remains the larger optimization target.
