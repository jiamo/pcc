# Freestanding pcc-Python registered-root scan ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
29093fe8...  pcc/py_runtime/py/freestanding_gc_mapped_roots.py
b2dd52da...  pcc/py_runtime/py/py_gc_backend.py
ca0dd7ab...  pcc/py_frontend/codegen/runtime_abi.py
7f42b874...  tests/python/test_freestanding_gc_mapped_roots.py
1328991c...  tests/python/test_gc_backend_generational.py
b6bfd30a...  tests/python/test_gc_abstraction_surface.py
8e204f70...  tests/python/test_gc_update_referents.py
```

## Claim boundary

The strict mapped-root object now uniquely owns
`pcc_gc_visit_registered_root_slots`. It walks frame, continuation, scheduler
and builtin-exception-cache roots and dispatches the existing one-slot visitor
for gray, promotion or rewrite. Promotion alone passes the frame/continuation
stable-value arrays; gray and rewrite preserve their prior null-stable
contract.

The managed collector no longer duplicates three registry traversal loops.
Its gray seeding, generational promotion and backend-4 remap phases call the
one strict scanner, while their object-list, referent, promotion and epoch
retirement semantics remain in the existing providers. No graph rule or
oldification logic was copied.

## Object and semantic proof

LLVM, self and fresh-pcc1 compilation define exactly the original eight
mapped-root symbols plus the new scanner. The exact raw undefined closure is
12 symbols: backend/config registry heads, graph lock/unlock, root-map decode,
the three collector providers and the builtin-exception-cache slot provider.
The new raw-only signature remains absent from global `RUNTIME_SIGNATURES`.

The production archive link map proves the nine symbols have one owner in
`freestanding_gc_mapped_roots.o`. A direct runtime probe registers two frame
slots, one continuation slot and one scheduler slot; together with 22 builtin
exception cache slots, the scanner reports exactly 26. Existing GC0..4 C
differentials, backend-4 relocation rewrite and threaded registry mutation
remain green.

## Focused and downstream results

```text
3 passed in 1.49s    # source owner plus exact LLVM/self object closure
1 passed in 56.84s   # archive owner, GC0..4, relocation and direct count=26
1 passed in 56.94s   # PCC_WITH_THREADS=1 registry mutation, GC0..4
183 passed in 80.40s # frame/mapped/collector/referent/relocation downstream
```

## Fresh pcc1 proof

The current-source self/no-libpython stage1 completed its publish and exec
smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=33205 \
  output=build/libc-gc-registered-root-scan-stage1/pcc1
link_self_native_object_cache_hits=321
link_self_native_object_cache_misses=4
link_self_emit_objects_native=3.648s
```

That pcc1 compiled the real strict mapped-root module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library` in 0.27s.
Clang and `nm` confirmed the same nine definitions and 12 raw imports.

## Not proven

Object-list root seeding, the mark/promotion/relocation providers, referent
traversal, weakref/finalizer/resurrection, full collector ownership, long-run
GC metrics and the final pcc1->pcc2->pcc3 five-GC matrix remain open.
