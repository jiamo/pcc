/* pcc/py_runtime/src/py_libpython.c
 *
 * Phase 4 CPython C-API fallback shim.
 *
 * Strategy: compiled code that imports arbitrary third-party packages
 * (numpy, pandas, requests, ...) trampolines through libpython's
 * ``PyImport_ImportModule`` / ``PyObject_CallObject`` / ``PyObject_GetAttr``
 * instead of our own runtime. The wrappers in this file hide the
 * lifecycle details (Py_Initialize, GIL) so the pcc-emitted IR only
 * needs to see a small, stable set of symbols.
 *
 * Design decisions:
 *
 *   - The CPython ``PyObject *`` type is DISTINCT from pcc's own
 *     ``PyObject *`` (the small-tagged-int + user-class layout defined
 *     in ``py_internal.h``). We expose the CPython type to codegen as
 *     opaque ``void *``; the two pointer namespaces never alias.
 *
 *   - Py_Initialize is called lazily on first import. Exit cleanup
 *     registers ``Py_Finalize`` via ``atexit``.
 *
 *   - All CPython API calls happen with the GIL held (the embedded
 *     interpreter created by Py_Initialize starts in the main thread
 *     holding the GIL; we never release it).
 */

#include "py_runtime.h"
#include "py_internal.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef PCC_WITH_LIBPYTHON

/* Forward declarations from libpython. We intentionally do NOT
 * ``#include <Python.h>`` here because the runtime build deliberately
 * avoids depending on CPython headers when the libpython fallback is
 * disabled at build time (see Makefile). */
typedef struct _object CPyObject;
extern void Py_Initialize(void);
extern void Py_Finalize(void);
extern int  Py_IsInitialized(void);
extern CPyObject *PyImport_ImportModule(const char *name);
extern CPyObject *PyObject_GetAttrString(CPyObject *o, const char *attr);
extern CPyObject *PyObject_CallNoArgs(CPyObject *callable);
extern CPyObject *PyObject_CallOneArg(CPyObject *callable, CPyObject *arg);
extern CPyObject *PyObject_CallFunctionObjArgs(
    CPyObject *callable, ...);
extern CPyObject *PyObject_Call(CPyObject *callable,
                                CPyObject *args, CPyObject *kwargs);
extern CPyObject *PyTuple_New(long size);
extern int PyTuple_SetItem(CPyObject *tup, long index, CPyObject *item);
extern long PyObject_Length(CPyObject *o);
extern CPyObject *PyObject_GetItem(CPyObject *o, CPyObject *key);
extern int PyObject_SetItem(CPyObject *o, CPyObject *key, CPyObject *value);
extern int PyObject_IsTrue(CPyObject *o);
extern CPyObject *PyObject_GetIter(CPyObject *o);
extern CPyObject *PyIter_Next(CPyObject *it);
extern CPyObject *PyObject_Str(CPyObject *o);
extern CPyObject *PyLong_FromLongLong(long long value);
extern long long  PyLong_AsLongLong(CPyObject *o);
extern CPyObject *PyFloat_FromDouble(double value);
extern double     PyFloat_AsDouble(CPyObject *o);
extern CPyObject *PyUnicode_FromStringAndSize(const char *u, long len);
extern const char *PyUnicode_AsUTF8(CPyObject *unicode);
extern void Py_DecRef(CPyObject *o);
extern void Py_IncRef(CPyObject *o);

static atomic_int g_initialized = 0;

static void py_libpython_atexit(void) {
    if (atomic_load(&g_initialized) && Py_IsInitialized()) {
        Py_Finalize();
    }
}

void py_cpy_ensure_init(void) {
    int expected = 0;
    if (atomic_compare_exchange_strong(&g_initialized, &expected, 1)) {
        Py_Initialize();
        atexit(py_libpython_atexit);
    }
}

void *py_cpy_import(const char *name) {
    py_cpy_ensure_init();
    return (void *)PyImport_ImportModule(name);
}

void *py_cpy_getattr(void *obj, const char *name) {
    if (obj == NULL) return NULL;
    return (void *)PyObject_GetAttrString((CPyObject *)obj, name);
}

void *py_cpy_call_noargs(void *callable) {
    if (callable == NULL) return NULL;
    return (void *)PyObject_CallNoArgs((CPyObject *)callable);
}

/* Dup the CPython object's str() into a freshly allocated pcc
 * PyStrObject so callers can interop with our native str routines
 * (py_print, py_str_concat, etc.). Returns NULL on failure; the
 * CPython error indicator is left set for the caller to inspect via
 * py_cpy_error_check. */
PyObject *py_cpy_to_pcc_str(void *cpy_obj) {
    if (cpy_obj == NULL) return NULL;
    CPyObject *s = PyObject_Str((CPyObject *)cpy_obj);
    if (s == NULL) return NULL;
    const char *utf8 = PyUnicode_AsUTF8(s);
    if (utf8 == NULL) {
        Py_DecRef(s);
        return NULL;
    }
    /* py_str_new takes a ptr+len and copies. */
    size_t n = 0;
    while (utf8[n] != '\0') n++;
    PyObject *out = py_str_new((const char *)utf8, (int64_t)n);
    Py_DecRef(s);
    return out;
}

void py_cpy_decref(void *obj) {
    if (obj != NULL) Py_DecRef((CPyObject *)obj);
}

void *py_cpy_from_i64(int64_t value) {
    py_cpy_ensure_init();
    return (void *)PyLong_FromLongLong((long long)value);
}

int64_t py_cpy_to_i64(void *obj) {
    if (obj == NULL) return 0;
    return (int64_t)PyLong_AsLongLong((CPyObject *)obj);
}

void *py_cpy_from_f64(double value) {
    py_cpy_ensure_init();
    return (void *)PyFloat_FromDouble(value);
}

double py_cpy_to_f64(void *obj) {
    if (obj == NULL) return 0.0;
    return PyFloat_AsDouble((CPyObject *)obj);
}

/* Convert a pcc PyStrObject* to a CPython unicode object. The caller
 * retains ownership of the pcc string; this function returns a new
 * owned CPython reference. */
void *py_cpy_from_pccstr(PyObject *s) {
    if (s == NULL) return NULL;
    py_cpy_ensure_init();
    /* Our py_str API exposes data + length via accessors in py_str.c. */
    extern const char *py_str_utf8(PyObject *s);
    extern int64_t     py_str_byte_len(PyObject *s);
    const char *data = py_str_utf8(s);
    int64_t len = py_str_byte_len(s);
    if (data == NULL || len < 0) return NULL;
    return (void *)PyUnicode_FromStringAndSize(data, (long)len);
}

/* Universal pcc → CPython converter. Dispatches on the pcc type tag
 * and rebuilds the object using CPython C API. Recurses through list
 * / tuple / dict / set. Returns a new CPython owned ref (caller must
 * ``py_cpy_decref``). NULL input → NULL. */
extern CPyObject *PyList_New(long size);
extern int        PyList_SetItem(CPyObject *lst, long i, CPyObject *item);
extern CPyObject *PyDict_New(void);
extern int        PyDict_SetItem(CPyObject *d, CPyObject *k, CPyObject *v);
extern CPyObject *PySet_New(CPyObject *iterable);
extern int        PySet_Add(CPyObject *s, CPyObject *item);
extern CPyObject *Py_None_Ref;  /* referenced as &_Py_NoneStruct */
void *py_cpy_from_pcc_obj(PyObject *o) {
    if (o == NULL) return NULL;
    py_cpy_ensure_init();
    int32_t tag = py_type_of(o);
    switch (tag) {
    case PY_TYPE_NONE: {
        extern CPyObject _Py_NoneStruct;
        Py_IncRef(&_Py_NoneStruct);
        return (void *)&_Py_NoneStruct;
    }
    case PY_TYPE_BOOL: {
        /* Tagged-int path; re-use bool conversion via int→bool in CPython. */
        int64_t v = py_int_to_i64(o, NULL);
        extern CPyObject *PyBool_FromLong(long);
        return (void *)PyBool_FromLong((long)v);
    }
    case PY_TYPE_INT: {
        int64_t v = py_int_to_i64(o, NULL);
        return (void *)PyLong_FromLongLong((long long)v);
    }
    case PY_TYPE_FLOAT: {
        double v = py_float_to_f64(o);
        return (void *)PyFloat_FromDouble(v);
    }
    case PY_TYPE_STR:
        return py_cpy_from_pccstr(o);
    case PY_TYPE_LIST: {
        int64_t n = py_list_len(o);
        CPyObject *lst = PyList_New((long)n);
        if (lst == NULL) return NULL;
        for (int64_t i = 0; i < n; i++) {
            PyObject *elem = py_list_get(o, i);
            CPyObject *c = (CPyObject *)py_cpy_from_pcc_obj(elem);
            py_decref(elem);  /* py_list_get returns new ref */
            PyList_SetItem(lst, (long)i, c);  /* steals ref */
        }
        return (void *)lst;
    }
    case PY_TYPE_TUPLE: {
        int64_t n = py_tuple_len(o);
        CPyObject *tup = PyTuple_New((long)n);
        if (tup == NULL) return NULL;
        for (int64_t i = 0; i < n; i++) {
            PyObject *elem = py_tuple_get(o, i);
            CPyObject *c = (CPyObject *)py_cpy_from_pcc_obj(elem);
            /* py_tuple_get returns borrowed; no decref. */
            PyTuple_SetItem(tup, (long)i, c);  /* steals ref */
        }
        return (void *)tup;
    }
    case PY_TYPE_DICT: {
        CPyObject *d = PyDict_New();
        if (d == NULL) return NULL;
        PyObject *keys = py_dict_keys(o);  /* new list ref */
        int64_t n = py_list_len(keys);
        for (int64_t i = 0; i < n; i++) {
            PyObject *k = py_list_get(keys, i);
            PyObject *v = py_dict_get(o, k);
            CPyObject *ck = (CPyObject *)py_cpy_from_pcc_obj(k);
            CPyObject *cv = (CPyObject *)py_cpy_from_pcc_obj(v);
            PyDict_SetItem(d, ck, cv);
            Py_DecRef(ck);
            Py_DecRef(cv);
            py_decref(k);
        }
        py_decref(keys);
        return (void *)d;
    }
    default: {
        /* Unknown tag — best effort: str(o) → CPython unicode. Prevents a
         * hard crash when passing a class instance or similar. */
        extern PyObject *py_obj_repr(PyObject *o);
        PyObject *r = py_obj_repr(o);
        if (r == NULL) return NULL;
        void *res = py_cpy_from_pccstr(r);
        py_decref(r);
        return res;
    }
    }
}

void *py_cpy_call1(void *callable, void *a) {
    if (callable == NULL) return NULL;
    return (void *)PyObject_CallOneArg((CPyObject *)callable, (CPyObject *)a);
}

void *py_cpy_call2(void *callable, void *a, void *b) {
    if (callable == NULL) return NULL;
    return (void *)PyObject_CallFunctionObjArgs(
        (CPyObject *)callable, (CPyObject *)a, (CPyObject *)b, (CPyObject *)NULL
    );
}

void *py_cpy_call3(void *callable, void *a, void *b, void *c) {
    if (callable == NULL) return NULL;
    return (void *)PyObject_CallFunctionObjArgs(
        (CPyObject *)callable,
        (CPyObject *)a, (CPyObject *)b, (CPyObject *)c,
        (CPyObject *)NULL
    );
}

int64_t py_cpy_len(void *obj) {
    if (obj == NULL) return 0;
    return (int64_t)PyObject_Length((CPyObject *)obj);
}

void *py_cpy_getitem(void *obj, void *key) {
    if (obj == NULL || key == NULL) return NULL;
    return (void *)PyObject_GetItem((CPyObject *)obj, (CPyObject *)key);
}

int py_cpy_setitem(void *obj, void *key, void *val) {
    if (obj == NULL || key == NULL) return -1;
    return PyObject_SetItem((CPyObject *)obj, (CPyObject *)key, (CPyObject *)val);
}

int py_cpy_truthy(void *obj) {
    if (obj == NULL) return 0;
    return PyObject_IsTrue((CPyObject *)obj);
}

void *py_cpy_iter(void *obj) {
    if (obj == NULL) return NULL;
    return (void *)PyObject_GetIter((CPyObject *)obj);
}

/* Return the next item (new ref) or NULL on end-of-iteration. */
void *py_cpy_iter_next(void *it) {
    if (it == NULL) return NULL;
    return (void *)PyIter_Next((CPyObject *)it);
}

/* Tuple-based call for arbitrary arity. Each arg in the flat argv
 * array is handed off to PyTuple_SetItem which STEALS the ref, so
 * the caller must not decref its argv entries after this returns. */
void *py_cpy_call_argv(void *callable, int64_t n, void **argv) {
    if (callable == NULL) return NULL;
    CPyObject *tup = PyTuple_New((long)n);
    if (tup == NULL) return NULL;
    for (int64_t i = 0; i < n; i++) {
        /* PyTuple_SetItem steals a reference — the caller must have
         * owned each ``argv[i]``. */
        PyTuple_SetItem(tup, (long)i, (CPyObject *)argv[i]);
    }
    CPyObject *result = PyObject_Call(
        (CPyObject *)callable, tup, (CPyObject *)NULL
    );
    Py_DecRef(tup);
    return (void *)result;
}

/* Tuple + dict call for positional + keyword arguments.
 *
 * Positional argv[0..n_pos) is stolen into a PyTuple.
 * Keyword kw_vals[0..n_kw) is borrowed by PyDict_SetItem (dict
 * increfs). The caller still owns each kw_vals entry and must decref
 * after this returns. */
extern int PyDict_SetItemString(CPyObject *d, const char *key, CPyObject *val);
void *py_cpy_call_kw(void *callable,
                     int64_t n_pos, void **argv,
                     int64_t n_kw, const char **kw_names, void **kw_vals) {
    if (callable == NULL) return NULL;
    CPyObject *tup = PyTuple_New((long)n_pos);
    if (tup == NULL) return NULL;
    for (int64_t i = 0; i < n_pos; i++) {
        PyTuple_SetItem(tup, (long)i, (CPyObject *)argv[i]);  /* steals */
    }
    CPyObject *kwargs = NULL;
    if (n_kw > 0) {
        kwargs = PyDict_New();
        if (kwargs == NULL) {
            Py_DecRef(tup);
            return NULL;
        }
        for (int64_t i = 0; i < n_kw; i++) {
            PyDict_SetItemString(kwargs, kw_names[i], (CPyObject *)kw_vals[i]);
        }
    }
    CPyObject *result = PyObject_Call((CPyObject *)callable, tup, kwargs);
    Py_DecRef(tup);
    if (kwargs != NULL) Py_DecRef(kwargs);
    return (void *)result;
}

#else /* !PCC_WITH_LIBPYTHON */

/* Build variant that does not link libpython. All CPython fallback
 * entry points abort on use so mis-compiled programs fail loudly
 * rather than silently returning NULL. */

void py_cpy_ensure_init(void) {
    fprintf(stderr, "pcc: import fell through to the CPython fallback "
                    "but the runtime was built without libpython support "
                    "(rebuild with PCC_WITH_LIBPYTHON=1)\n");
    abort();
}

void *py_cpy_import(const char *name) {
    (void)name;
    py_cpy_ensure_init();
    return NULL;
}

void *py_cpy_getattr(void *obj, const char *name) {
    (void)obj; (void)name;
    py_cpy_ensure_init();
    return NULL;
}

void *py_cpy_call_noargs(void *callable) {
    (void)callable;
    py_cpy_ensure_init();
    return NULL;
}

PyObject *py_cpy_to_pcc_str(void *cpy_obj) {
    (void)cpy_obj;
    py_cpy_ensure_init();
    return NULL;
}

void py_cpy_decref(void *obj) {
    (void)obj;
    py_cpy_ensure_init();
}

void *py_cpy_from_i64(int64_t v) {
    (void)v; py_cpy_ensure_init(); return NULL;
}
int64_t py_cpy_to_i64(void *o) {
    (void)o; py_cpy_ensure_init(); return 0;
}
void *py_cpy_from_f64(double v) {
    (void)v; py_cpy_ensure_init(); return NULL;
}
double py_cpy_to_f64(void *o) {
    (void)o; py_cpy_ensure_init(); return 0.0;
}
void *py_cpy_from_pccstr(PyObject *s) {
    (void)s; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call1(void *c, void *a) {
    (void)c; (void)a; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call2(void *c, void *a, void *b) {
    (void)c; (void)a; (void)b; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call3(void *c, void *a, void *b, void *d) {
    (void)c; (void)a; (void)b; (void)d; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call_argv(void *c, int64_t n, void **argv) {
    (void)c; (void)n; (void)argv; py_cpy_ensure_init(); return NULL;
}
int64_t py_cpy_len(void *o) {
    (void)o; py_cpy_ensure_init(); return 0;
}
void *py_cpy_getitem(void *o, void *k) {
    (void)o; (void)k; py_cpy_ensure_init(); return NULL;
}
int py_cpy_setitem(void *o, void *k, void *v) {
    (void)o; (void)k; (void)v; py_cpy_ensure_init(); return -1;
}
int py_cpy_truthy(void *o) {
    (void)o; py_cpy_ensure_init(); return 0;
}
void *py_cpy_iter(void *o) {
    (void)o; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_iter_next(void *i) {
    (void)i; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_from_pcc_obj(PyObject *o) {
    (void)o; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call_kw(void *c, int64_t n_pos, void **argv,
                     int64_t n_kw, const char **kw_names, void **kw_vals) {
    (void)c; (void)n_pos; (void)argv;
    (void)n_kw; (void)kw_names; (void)kw_vals;
    py_cpy_ensure_init();
    return NULL;
}

#endif /* PCC_WITH_LIBPYTHON */
