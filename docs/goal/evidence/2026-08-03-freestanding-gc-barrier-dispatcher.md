# Freestanding shared GC barrier/dispatcher evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the public five-backend GC
step dispatcher and shared slot/write barriers. This closes the known GC code
migration list, but it does not prove the parent task's final cross-backend,
root, link-map, or long-running claims. `LIBC-P2-FREESTANDING-GC` remains
`DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_barrier_dispatcher.py` uniquely exports six ABIs:

- public `pcc_gc_step`, `pcc_gc_note_slot_write_barrier`, and
  `pcc_gc_note_write_barrier`;
- strict internal selected-backend, pointer-header eligibility, and pending
  tracing-work helpers.

The dispatcher delegates Backend 1/2 work to the strict
incremental/concurrent scheduler, Backend 3 work to the strict generational
scheduler plus tracing, and Backend 4 work through remembered roots, aging,
page evacuation, relocation selection/drain, tracing, and idle
remap/retirement. The barrier preserves null/tagged filtering, tri-color
shading, old-to-young ownership checks before taking the graph lock, Backend 3
remembered owners, and Backend 4 slot/value snapshots.

The three Backend 4 raw provider ABIs consumed by this object remain authored
in pcc-Python `py_gc_backend.py`; no C implementation was introduced.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and policy contracts
  4 passed, 1 deselected in 1.06s

production archive ownership
  5 passed in 57.03s (cold archive rebuild)

Backend 3/4 old-to-young C-oracle differential
  2 passed in 0.98s
  backend 3: 1,0,0
  backend 4: 1,1,1

adjacent dispatcher, forwarding-retirement, relocation-drain,
incremental/concurrent, and Backend 3 barrier gates
  28 passed in 7.55s

five updated source-owner/config/safepoint ratchets
  5 passed in 0.37s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-barrier-dispatcher-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-barrier-dispatcher-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=72250 \
  output=build/freestanding-gc-barrier-dispatcher-stage1/pcc1
```

The profile records 70.699 seconds, 321 self-object cache hits, four misses,
4.119 seconds in native object emission, and 41.423 seconds constructing the
changed runtime archive. `file` reports arm64 Mach-O and `otool -L` reports
only `/usr/lib/libSystem.B.dylib`, not libpython.

That pcc1 compiled the strict module with `--ir-scaffold=on --backend self
--python-libpython=off --python-library` in 0.483 seconds. Clang accepted the
emitted LLVM IR; exactly six exports are definitions and the undefined set
contains no `py_cpy_*` symbol.

## Scoped hashes

```text
09c7e4f4599bdf722438e78686d0fa8d92c51e4dc0045c4d5b232787882e2b0c  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
384e10325fd6b24c380a5443c8cfa3f6f89a8074a5d4f8ff9be4d18a053fe733  pcc/py_runtime/py/py_gc_backend.py
b6b9a2256e0befb5a9518a321c50b2c845380fc96a8117e851e3baa05d22cc8d  pcc/py_frontend/codegen/runtime_abi.py
9fe543d05011453ce78eeff0d6d4e2ec69dd301009c4f17b4992fed959260738  pcc/py_runtime/Makefile
579b008e3fbe3ce578c1f1a7d0f186d5a1a5397d88ea740cccd5d3eb5e21dfdd  tests/python/test_freestanding_gc_barrier_dispatcher.py
fab53a3c7f162b270911f6c50e8ee3f2f633a6baab7c14b70cf9e4f3648427b5  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
075bd08e73a67765e03c129278f478169189f8633984e5d1486decf38350cf6c  tests/python/test_gc_backend_generational.py
929a3fd9151d9cc0526af07f91dd2130205161fc2b3a871b479ac1c03c30986e  tests/python/test_gc_backend4_production.py
7338820ec81d985afe285b61ba5369496a18f23d45dffbaa61a7429742fbeea7  tests/python/test_gc_threading_substrate.py
a3017e175619a5b07e70a55395c72174288594afc617dd133f25a7ed1a8fd15a  tests/python/gc/test_gc_backend_config_fastpath.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Prove suspended-frame/scheduler/C-extension roots, relocation, and
synchronization across the final production object set; prove no production
GC C object remains; run the five-GC semantic/fixed-point matrix once; and
record long-running RSS, fragmentation, pause, and throughput deltas.
