# GC4 A3b locked-log routing: gate repair and armed seam ownership

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite repair boundary confirmed; parent remains `IN_PROGRESS`.

Predecessor: `docs/goal/evidence/2026-08-23-gc4-a3b-locked-log-site-routing.md`

## Claim boundary

Four corrections on top of the committed A3b routing slice. Nothing about
quiescence, A3c, phase contracts or performance changes here.

1. `tests/python/test_runtime_tripwires.py` described an earlier design at
   three assertions, so the gate was RED on committed source. The named
   boundary list still required the zombie-retention message that the slice
   deliberately deleted as unfireable; the locked-site loop still required
   `PCC_GC_DEFER_TRIPWIRE(` at the side-table commit site that the slice
   deliberately routed through `PCC_GC_MIXED_TRIPWIRE(` for public-ABI
   reasons; and the cpy-handle assertion required a helper
   `pcc_cpy_handle_violation_locked(` that exists nowhere in the repository.
   The assertions now state the implemented contract: DEFER sites and
   public-ABI MIXED sites are separate tuples with separate expected macros,
   and the removed check carries an inline reason so its absence cannot be
   mistaken for a lost boundary.

2. `pcc_cpy_handle_move_owned_ref` routed only its validity check through the
   bailing macro; both content checks called the seam directly, discarded the
   result and fell through to the move. On the "destination owns a different
   foreign reference" path that overwrites `to_box->cpy_ref` and drops an
   owned foreign reference — the exact state the check exists to reject —
   before the deferred report fires at outer unlock. All three checks now use
   `PCC_GC_OWNER_TRIPWIRE`, so a lock owner records and returns with both
   boxes intact, an unlocked caller still aborts immediately, and unarmed
   builds compile the checks out without the two `#ifdef` blocks.

3. The cross-TU seam `pcc_gc_tripwire_defer_or_fail` was defined only in
   `py_gc_backend.c`, whose object the production archive replaces with the
   strict port (`Makefile` `PY_MODULES`), while `py_cpy_handle.c` has no port
   and is compiled into that same archive with `$(CPPFLAGS)`. An armed
   production build therefore referenced a symbol no archive member defined;
   unarmed builds were unaffected because the call site compiles out, and no
   gate covered the combination (the armed fixture builds the cc oracle
   `libpy_runtime.a`, and the link-map allowlist only describes the
   `pcc_py_gc_*` surface). The strict substrate now owns a same-named,
   same-signature export, so the seam resolves in both archives.

4. Both substrate exports now pin exact ABI widths through
   `c_abi_typed_export`, and the port contract test checks the pinned widths
   against the C declaration in `py_internal.h` rather than a decorator
   spelling.

An allowlist entry added for the seam in
`tests/python/test_freestanding_gc_production_link_map.py` was wrong and was
reverted: that set describes only `pcc_py_gc_*`-prefixed substrate symbols,
while the seam must keep its C ABI name. The test caught it. Ownership of the
seam is already covered by
`test_all_production_collector_symbols_are_pcc_python_owned`, which requires
every `pcc_gc_*` archive symbol to come from a member with a pcc-Python
source.

## RED

The committed slice at `573cd9cd` was red on its own gate:

```text
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 --tb=line \
  tests/python/test_runtime_tripwires.py

FAILED test_tripwire_source_covers_named_runtime_boundaries
FAILED test_remaining_locked_fatal_log_sites_route_through_deferred_slot
FAILED test_cpy_handle_move_checks_defer_under_graph_lock
3 failed, 8 passed in 17.55s
```

The armed seam gap was shown directly rather than by inspection: an armed
`py_cpy_handle.o` reports the undefined reference, and the production archive
did not define it.

```text
cc -c -DPCC_RUNTIME_TRIPWIRES -Ipcc/py_runtime/include -Ipcc/py_runtime/src \
   pcc/py_runtime/src/py_cpy_handle.c -o cpy_armed.o
nm -u cpy_armed.o | grep tripwire
  _pcc_gc_tripwire_defer_or_fail
  _pcc_runtime_tripwire_fail

nm -g libpy_runtime_pcc_py.a | grep tripwire_defer_or_fail
  (no output)
```

## Gates

```text
gtimeout 900s env -u LC_ALL uv run pytest -x -n0 -q --tb=short \
  tests/python/test_runtime_tripwires.py \
  tests/python/test_freestanding_gc_production_link_map.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_generational_oldification.py

44 passed in 172.81s (0:02:52)
```

`py_cpy_handle.c` compiles under `cc -fsyntax-only` in all four
tripwires/threads configurations. The strict substrate passes the
no-libpython closure emit and produces exactly the intended C ABI shapes:

```text
define external void @pcc_py_gc_defer_tripwire(ptr %msg, ptr %file, i32 %line)
define external void @pcc_py_gc_finish_deferred_tripwire()
define external i32 @pcc_gc_tripwire_defer_or_fail(ptr %msg, ptr %file, i32 %line)
```

Symbol resolution inside the production archive the gates built:

```text
freestanding_runtime_high_substrate.o: T _pcc_gc_tripwire_defer_or_fail
freestanding_runtime_high_substrate.o: T _pcc_py_gc_defer_tripwire
freestanding_runtime_high_substrate.o: T _pcc_py_gc_finish_deferred_tripwire
py_gc_backend.o:                       U _pcc_py_gc_defer_tripwire
freestanding_gc_relocation_payload.o:  U _pcc_py_gc_defer_tripwire
freestanding_gc_forwarding_retirement.o: U _pcc_py_gc_defer_tripwire
```

## Frozen identities

```text
2a034e9b745d3093315db0c57f72a61e38fcaa2452dce44b3e23279653b07725  pcc/py_runtime/src/py_cpy_handle.c
cb6beec1a3dc9ee3ae1d78abc160dc8f38757223908834aad6c0a3f6b9df973a  pcc/py_runtime/py/freestanding_runtime_high_substrate.py
7898f003e540cc4f94e7af67c31eb11c107414cd8c8f268a63168aeb6d2ed3d4  pcc/py_frontend/codegen/runtime_abi.py
3712fcc194167c9e173f7acfbcc94a5b2a653e6d36f59dcf6542bc6be0a8951d  tests/python/test_runtime_tripwires.py
f99d283f0a6b4f1081b42e8623a89082b1b4b14c3ed35e964e1804d812775c9b  pcc/py_runtime/src/py_gc_backend.c
af5750561a2cdb8c7a2e2e7c7bda8ab7c753dec96945b3bd27119b8dbaa8a62c  pcc/py_runtime/src/py_internal.h
```

The predecessor receipt's frozen identities for `py_gc_backend.c`,
`py_cpy_handle.c` and `py_internal.h` no longer match the tree; the hashes
above are the current ones for those three files.

## Nonclaims

No end-to-end armed executable link was performed — the seam claim is symbol
resolution inside the archive, not a linked binary. No claim about A3c, the
owner-referent worklist, quiescence, STW phases, pause/throughput, the fixed
point, or five-GC parity. No tripwire-clean claim for unarmed builds. The
remaining locked-context inventory outside `py_gc_backend.c` and
`py_cpy_handle.c` is unchanged by this repair.
