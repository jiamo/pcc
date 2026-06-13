#include "py_internal.h"

/* J2' (docs/investigations/generator-cpython-iteration-dominance.md):
 * an owned handle to a foreign CPython object. Generator frames may
 * only hold pcc objects (frame saves go through the py_list store
 * barrier and frame dealloc dereferences pcc headers), so cpy locals
 * are boxed into one of these at their store points and unboxed at
 * the central name-load helper. The handle has NO pcc pointer slots —
 * `cpy_ref` is a foreign pointer the GC never interprets — and the
 * dealloc hook releases the foreign reference, which makes dropping a
 * suspended generator release its live cpy iterator/items structurally.
 *
 * C-only helper (no pcc-Python port mirror): the port reaches these
 * through the runtime ABI like any other extern. */

typedef struct PyCpyHandleObject {
    PyObjectHeader h;
    void *cpy_ref;   /* owned foreign reference; NOT a pcc slot */
} PyCpyHandleObject;

/* Release hook: py_cpy_handle.c lives in the MAIN runtime archive,
 * while py_cpy_decref lives in the separate libpython archive (real
 * impl or stub) that plain `cc` links of libpy_runtime.a do not pull
 * in. The libpython bridge registers the release function when it
 * initializes; a process that never initializes the bridge can never
 * have produced a foreign reference, so a NULL hook is safe. */
static void (*py_cpy_handle_release_fn)(void *) = NULL;

void py_cpy_handle_set_release_fn(void (*fn)(void *)) {
    py_cpy_handle_release_fn = fn;
}

/* Box an OWNED foreign reference (takes ownership; the caller must not
 * decref it afterwards). Returns a new pcc object reference. */
PyObject *py_cpy_handle_new(void *cpy_ref) {
    PCC_RT_TRIPWIRE(
        cpy_ref != NULL,
        "py_cpy_handle_new: cannot own a NULL foreign reference"
    );
    PyCpyHandleObject *box = (PyCpyHandleObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyCpyHandleObject), PY_TYPE_CPY_HANDLE, 0);
    if (box == NULL) return NULL;
    box->cpy_ref = cpy_ref;
    return (PyObject *)box;
}

/* Borrow the foreign reference (no refcount transfer; NULL when the
 * object is not a handle or already cleared). */
void *py_cpy_handle_get(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return NULL;
    if (py_type_of(o) != PY_TYPE_CPY_HANDLE) return NULL;
    return ((PyCpyHandleObject *)o)->cpy_ref;
}

void py_dealloc_cpy_handle(PyObject *o) {
    PyCpyHandleObject *box = (PyCpyHandleObject *)o;
    PCC_RT_TRIPWIRE(
        o != NULL && !PY_IS_TAGGED_INT(o)
            && py_type_of(o) == PY_TYPE_CPY_HANDLE,
        "py_dealloc_cpy_handle: invalid native-handle object"
    );
    PCC_RT_TRIPWIRE(
        box->cpy_ref == NULL || py_cpy_handle_release_fn != NULL,
        "py_dealloc_cpy_handle: owned foreign reference has no release hook"
    );
    if (box->cpy_ref != NULL && py_cpy_handle_release_fn != NULL) {
        py_cpy_handle_release_fn(box->cpy_ref);
    }
    box->cpy_ref = NULL;
    pcc_gc_free_object_memory(o);
}
