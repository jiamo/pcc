# GC write-barrier audit — non-container instance/class surface classification

Date: 2026-07-09

Task: `AUD-P0-GC-BARRIER-WRITE-AUDIT` (extends the container classification in
docs/goal/evidence/2026-07-08-gc-barrier-list-extend.md into the non-container
owned-pointer-slot surface). READ-ONLY analysis — no code changed, no build.

Scope of this slice: the instance-object and class-metadata pointer-slot store
surface (`pcc/py_runtime/src/py_class.c`, `py_class_attrs.c`) — the highest-value
non-container area (AGENTS.md "When this bites you" §4 / the freed-class
n_fields-corruption bug lives here).

Classification (every owned-pointer-slot store site):
- ALREADY-BARRIERED (mutating a possibly-old/escaped object):
  - Instance field setattr: py_class.c:646 `pcc_gc_store_ptr(inst, &inst->fields[idx], value)`.
  - Instance dynamic-attr slot: py_class.c:893, 1037 `pcc_gc_store_ptr(..dyn_slot..)`.
  - Instance copy fields: py_class.c:1101, 1109 `pcc_gc_store_ptr(..)`.
  - Class attrs setattr: py_class.c:189 `pcc_gc_note_slot_write_barrier(cls, slot, value)`.
  - Class attrs dict: py_class_attrs.c:703, 713 `pcc_gc_store_ptr(cls, &cls->attrs, ..)`.
  - classmethod/property func slots: py_class_attrs.c:520, 536, 539, 542 `pcc_gc_store_ptr(..)`.
  - Class BORROWED metadata (metaclass / del_method / method func): raw store
    IMMEDIATELY followed by `class_note_borrowed_metadata_slot_store(cls, &slot, val)`
    at py_class.c:464->465, 542->543, 536-540. These slots are traced-but-not-owned
    (borrowed metadata); the note helper is their barrier/relocation-tracking.
- PROVEN-SAFE-NON-OMISSION (raw store into a FRESH not-yet-escaped object):
  - py_class.c:601 `inst->cls = cls`, :618 `box->cls = cls`, :439
    `c->del_method = ...` — all inside constructors on the just-allocated
    inst/box/c (young, no old->young edge possible; same reasoning as the list
    concat/copy/repeat builders). `->cls` is a borrowed class pointer.
  - py_class_attrs.c:725-726 `n->cls = cls; n->attrs = attrs` — fresh cache node.
- OUT-OF-SCOPE / clears: py_class.c:1284 `inst->fields[i]=NULL; py_decref(v)`
  (dealloc clear with explicit decref), py_class_attrs.c:1017 `cls->attrs=NULL`
  (detach clear). Not owned-pointer value stores.

Finding: NO suspected omissions in the instance/class core. Every store that
mutates a possibly-old object is barriered (via pcc_gc_store_ptr or the borrowed-
metadata note helper); every raw store is into a fresh object or is a clear. The
highest-risk non-container area is sound.

Result: contributes to AUD-P0-GC-BARRIER-WRITE-AUDIT (stays DONE_WEAK).

## Update 2026-07-09 — tuple / exception / generator / coroutine also sound

Extended the read-only classification to the remaining major object families;
all barriered where they mutate escaped objects, no suspected omissions:
- Tuple build: py_tuple.c:99 `pcc_gc_store_ptr(tuple, &t->items[i], item)` — BARRIERED.
- Exceptions: py_exc_objects.c exc_class/message/cause/context all `pcc_gc_store_ptr` — BARRIERED.
- Generator: py_gen.c frame/send_value all `pcc_gc_store_ptr` — BARRIERED.
- Coroutine/task: py_coroutine.c captures/args/result/task result/waiter/shadow
  slots and stack_chunk->slots[i] all `pcc_gc_store_ptr`/`pcc_gc_store_root` — BARRIERED.
  Raw stores confirmed OUT-OF-SCOPE/SAFE: `c->name` (fresh pcc_gc_alloc'd coroutine,
  constructor), `c->resume_pc`/`resume_abi` (scalars), `c->stack_chunk` (a
  PyContinuationStackChunk* runtime C struct, not a PyObject — its PyObject slots
  are barriered at :291 and it is traced via zpage payload-span registration).

Finding: the MAJOR object-family owned-pointer-slot surface (list/dict/set +
instance/class + tuple/exception/generator/coroutine) is now fully classified
with NO suspected omissions beyond the 3 real ones fixed this session
(py_list_extend, py_set_rehash, py_list_reverse[prior]). The runtime's GC
store-discipline is consistent: escaped-object mutations barrier, fresh/scalar/
runtime-struct/NULL stores do not need one.

Open boundary (now narrow): the classification covered the MAJOR object families
but not exhaustively every one of the ~70 pcc/py_runtime/src/*.c files. Remaining
for a COMPLETE "exact pointer-slot audit": a confirming sweep of the peripheral
runtime files (py_func.c closures, py_module_attrs.c, py_context.c, py_weakref.c,
py_iter/enumerate, etc.) for any owned-pointer-slot store, plus the container
work already done + behavior-tested. DONE_STRONG is gated on that peripheral sweep
(or a task-owner decision that the audit scope is the major object families /
container mutation surface, which is now proven sound).
