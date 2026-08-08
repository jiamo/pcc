# Freestanding Backend 4 relocation-selector evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4 candidate scoring,
large-object/page policy, object-budget relocation selection, and page-budget
selection used by the normal GC step.

This slice does not claim evacuation drain, ZPage lifecycle, or forwarding
retirement are migrated.  `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_relocation_selector.py` exports two selectors and eight
finite helper ABIs.  The migration preserves:

- small/medium acceptance and large-page fragmentation-only reconsideration;
- pinned, already-selected, unsupported, and live-thread rejection;
- fragmentation, dirty-card, remembered-slot, and old-generation score inputs;
- one-time large-object defer flags and byte/count telemetry;
- same-page grouping with seed-first two-pass order;
- one safepoint per sixteen positive selections without exception machinery;
- object-budget public selection and page object-count budget selection;
- existing graph-lock boundaries and evacuation-page handoff.

## Focused gates

```text
strict source absent
  1 failed in 0.11s (FileNotFoundError)

strict LLVM/self object closure and selector contracts
  4 passed, 1 deselected in 1.56s

migrated telemetry/source owner gate
  1 passed in 0.26s

strict production archive ownership
  5 passed in 56.38s

pcc-Python phase-reset and normal-step relocation probes
  2 passed in 56.02s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-relocation-selector-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-relocation-selector-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=71551 \
  output=build/freestanding-gc-relocation-selector-stage1/pcc1
```

The profile records 70.179 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict relocation-selector module with
`--ir-scaffold=on --backend self --python-libpython=off --python-library` in
0.632 seconds.  Clang accepted the emitted LLVM IR; all ten exports are
definitions and the undefined set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
f0bc36529c1f9afec8ecc66b5fcb227998d8c78958412e51fcd4a5d790e919dd  pcc/py_runtime/py/freestanding_gc_relocation_selector.py
3bbf31b97d7f9bcc67fff8b4d9dc97b0644a88b2cce3ec60195e05af5d360b05  pcc/py_runtime/py/py_gc_backend.py
000112b55fe3c930dc2714fb49c0df15a41e2870442e109770d5c94ea01510d9  pcc/py_frontend/codegen/runtime_abi.py
4b068ef6c57ed4d97c3bc2f8a815826c57155618542782b43baef53e15612dd4  pcc/py_runtime/Makefile
d3789a926b811732d8207f36bf6b2076fc37b92955e5d0b37b4b8598cd825414  tests/python/test_freestanding_gc_relocation_selector.py
13a5aba01382ee0bec6d591cba9ea0bf18064634b4991be0e8597e0693ee228d  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move GC4 evacuation drain and ZPage lifecycle/handoff into finite strict
objects, then one-epoch forwarding retirement and the shared
write-barrier/dispatcher policy.  Final closure still requires proof that no
production C GC object is linked, the one-shot five-GC semantic/fixed-point
matrix, and long-running RSS/fragmentation/pause/throughput deltas.
