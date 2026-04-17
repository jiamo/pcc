/* pcc/py_runtime/src/py_obj_gc.c
 *
 * GC stubs (Phase 2/3). The ABI requires these symbols to exist;
 * they're no-ops until the tricolor cycle collector lands.
 *
 * Split out of py_obj.c so the GC surface can be independently
 * ported to pcc-Python (Phase 4c) without touching the allocator /
 * refcount / singleton code that everything else depends on.
 */
#include "py_internal.h"


void py_gc_init(void) {
    /* TODO(phase2+): init tri-color lists */
}

void py_gc_collect(void) {
    /* TODO(phase2+): run a collection */
}

void py_gc_track(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header(o)->flags |= PY_FLAG_GC_TRACKED;
}

void py_gc_untrack(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    py_header(o)->flags &= ~PY_FLAG_GC_TRACKED;
}
