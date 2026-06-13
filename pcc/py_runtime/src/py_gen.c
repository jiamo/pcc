/* pcc/py_runtime/src/py_gen.c
 *
 * Native generator shell for pcc-compiled Python. The frontend owns the
 * generator state machine; the runtime only stores the resume thunk, frame
 * object, and current state.
 */

#include "py_internal.h"


PyObject *py_gen_new(void *resume, PyObject *frame) {
    if (resume == NULL || frame == NULL) return NULL;
    PyGenObject *g = (PyGenObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyGenObject), PY_TYPE_GEN, 0
    );
    if (g == NULL) return NULL;
    g->resume = (PyNativeGenResume)resume;
    g->frame = NULL;
    g->state = 0;
    g->done = 0;
    g->send_value = NULL;
    pcc_gc_store_ptr((PyObject *)g, &g->frame, frame);
    pcc_gc_store_ptr((PyObject *)g, &g->send_value, py_None);
    py_gc_track((PyObject *)g);
    return (PyObject *)g;
}


void py_dealloc_gen(PyObject *o) {
    PyGenObject *g = (PyGenObject *)o;
    PyObject *frame = pcc_gc_load_ptr(o, &g->frame);
    PyObject *send_value = pcc_gc_load_ptr(o, &g->send_value);
    if (frame != NULL) py_decref(frame);
    if (send_value != NULL) py_decref(send_value);
    pcc_gc_free_object_memory(o);
}


static PyGenObject *checked_gen(PyObject *gen) {
    if (gen == NULL || PY_IS_TAGGED_INT(gen)) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object is not a generator"));
        return NULL;
    }
    PyObjectHeader *h = py_header(gen);
    if (h->type_tag != PY_TYPE_GEN) {
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "object is not a generator"));
        return NULL;
    }
    return (PyGenObject *)gen;
}


static int py_gen_set_send_value(PyGenObject *g, PyObject *value) {
    if (value == NULL) value = py_None;
    pcc_gc_store_ptr((PyObject *)g, &g->send_value, value);
    return 0;
}


int64_t py_gen_state(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return -1;
    return g->state;
}


void py_gen_set_state(PyObject *gen, int64_t state) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return;
    g->state = state;
}


void py_gen_set_done(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return;
    g->done = 1;
}


int64_t py_gen_is_done(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return 1;
    return g->done != 0 ? 1 : 0;
}


PyObject *py_gen_finish(PyObject *gen, PyObject *value) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    g->done = 1;
    if (value == NULL) value = py_None;
    PyObject *stop = py_exc_new_with_value(PY_EXC_STOPITERATION, value);
    py_raise(stop);
    if (stop != NULL) py_decref(stop);
    return NULL;
}


PyObject *py_gen_next(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    if (g->done != 0 || g->resume == NULL) {
        py_raise(py_exc_new(PY_EXC_STOPITERATION, ""));
        return NULL;
    }
    py_gen_set_send_value(g, py_None);
    PyObject *frame = pcc_gc_load_ptr((PyObject *)g, &g->frame);
    return g->resume(gen, frame);
}


PyObject *py_gen_send(PyObject *gen, PyObject *value) {
    if (gen != NULL && !PY_IS_TAGGED_INT(gen)
        && py_type_of(gen) == PY_TYPE_COROUTINE) {
        if (value != NULL && value != py_None) {
            py_raise(py_exc_new(
                PY_EXC_TYPEERROR,
                "can't send non-None value to a just-started coroutine"
            ));
            return NULL;
        }
        return py_coroutine_run(gen);
    }
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    if (g->done != 0 || g->resume == NULL) {
        py_raise(py_exc_new(PY_EXC_STOPITERATION, ""));
        return NULL;
    }
    if (g->state == 0 && value != NULL && value != py_None) {
        py_raise(py_exc_new(
            PY_EXC_TYPEERROR,
            "can't send non-None value to a just-started generator"
        ));
        return NULL;
    }
    py_gen_set_send_value(g, value);
    PyObject *frame = pcc_gc_load_ptr((PyObject *)g, &g->frame);
    return g->resume(gen, frame);
}


PyObject *py_gen_throw(PyObject *gen, PyObject *exc) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    if (g->done != 0 || g->resume == NULL) {
        py_raise(py_exc_new(PY_EXC_STOPITERATION, ""));
        return NULL;
    }
    py_gen_set_send_value(g, py_None);
    py_raise(exc);
    PyObject *frame = pcc_gc_load_ptr((PyObject *)g, &g->frame);
    return g->resume(gen, frame);
}


PyObject *py_gen_close(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    if (g->done == 0 && g->resume != NULL) {
        PyObject *exc = py_exc_new(PY_EXC_BASE, "GeneratorExit");
        py_gen_set_send_value(g, py_None);
        py_raise(exc);
        PyObject *frame = pcc_gc_load_ptr((PyObject *)g, &g->frame);
        PyObject *result = g->resume(gen, frame);
        if (result != NULL) {
            py_decref(result);
            g->done = 1;
            py_raise(py_exc_new(
                PY_EXC_RUNTIMEERROR,
                "generator ignored GeneratorExit"
            ));
            return NULL;
        }
        PyObject *cur = py_current_exception();
        PyObject *stop_cls = (PyObject *)py_exc_builtin_class(
            PY_EXC_STOPITERATION
        );
        if (py_exc_matches(cur, stop_cls)) {
            py_clear_exception();
            g->done = 1;
        } else if (cur == exc) {
            /* Our injected GeneratorExit propagated back unhandled:
             * that IS the normal close path in CPython — swallow it.
             * (Previously it stayed pending and detonated at the next
             * err check.) Any OTHER exception raised by the generator
             * body keeps propagating. */
            py_clear_exception();
            g->done = 1;
        } else {
            return NULL;
        }
    }
    py_incref(py_None);
    return py_None;
}


PyObject *py_gen_take_send(PyObject *gen) {
    PyGenObject *g = checked_gen(gen);
    if (g == NULL) return NULL;
    PyObject *value = pcc_gc_load_ptr((PyObject *)g, &g->send_value);
    if (value == NULL) value = py_None;
    py_incref(value);
    pcc_gc_store_ptr((PyObject *)g, &g->send_value, py_None);
    return value;
}
