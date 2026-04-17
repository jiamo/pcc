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

extern int py_runtime_program_argc;
extern const char **py_runtime_program_argv;
extern void (*py_runtime_program_args_hook)(void);

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
extern int PyObject_SetAttrString(CPyObject *o, const char *attr, CPyObject *v);
extern CPyObject *PyObject_CallNoArgs(CPyObject *callable);
extern CPyObject *PyObject_CallOneArg(CPyObject *callable, CPyObject *arg);
extern CPyObject *PyObject_CallFunctionObjArgs(
    CPyObject *callable, ...);
extern CPyObject *PyObject_Call(CPyObject *callable,
                                CPyObject *args, CPyObject *kwargs);
extern int PyRun_SimpleString(const char *command);
extern CPyObject *PyTuple_New(long size);
extern int PyTuple_SetItem(CPyObject *tup, long index, CPyObject *item);
extern CPyObject *PyList_New(long size);
extern int PyList_SetItem(CPyObject *lst, long i, CPyObject *item);
extern long PyObject_Length(CPyObject *o);
extern CPyObject *PyObject_GetItem(CPyObject *o, CPyObject *key);
extern int PyObject_SetItem(CPyObject *o, CPyObject *key, CPyObject *value);
extern int PyObject_IsTrue(CPyObject *o);
extern CPyObject *PyObject_GetIter(CPyObject *o);
extern CPyObject *PyIter_Next(CPyObject *it);
extern CPyObject *PyObject_Str(CPyObject *o);
extern int PyObject_IsInstance(CPyObject *inst, CPyObject *cls);
extern CPyObject *PyErr_Occurred(void);
extern void PyErr_Fetch(CPyObject **ptype, CPyObject **pvalue, CPyObject **ptraceback);
extern void PyErr_NormalizeException(CPyObject **ptype, CPyObject **pvalue, CPyObject **ptraceback);
extern void PyErr_Restore(CPyObject *type, CPyObject *value, CPyObject *traceback);
extern void PyErr_Clear(void);
extern void PyErr_Print(void);
extern int PyErr_GivenExceptionMatches(CPyObject *given, CPyObject *exc);
extern CPyObject *PyLong_FromLongLong(long long value);
extern long long  PyLong_AsLongLong(CPyObject *o);
extern CPyObject *PyFloat_FromDouble(double value);
extern double     PyFloat_AsDouble(CPyObject *o);
extern CPyObject *PyUnicode_FromStringAndSize(const char *u, long len);
extern const char *PyUnicode_AsUTF8(CPyObject *unicode);
extern const char *PyUnicode_AsUTF8AndSize(CPyObject *unicode, long *size);
extern long PyList_Size(CPyObject *lst);
extern CPyObject *PyList_GetItem(CPyObject *lst, long i);
extern long PyTuple_Size(CPyObject *tup);
extern CPyObject *PyTuple_GetItem(CPyObject *tup, long i);
extern int PyDict_Next(CPyObject *d, long *pos, CPyObject **key, CPyObject **value);
extern void Py_DecRef(CPyObject *o);
extern void Py_IncRef(CPyObject *o);
extern CPyObject _Py_NoneStruct;
extern CPyObject PyBool_Type;
extern CPyObject PyLong_Type;
extern CPyObject PyFloat_Type;
extern CPyObject PyUnicode_Type;
extern CPyObject PyList_Type;
extern CPyObject PyTuple_Type;
extern CPyObject PyDict_Type;
extern CPyObject PySet_Type;

/* PyCapsule + PyCFunction shim decls (libpython-enabled path). Used to
 * wrap a pcc user function (with CPython-tagged DynType ABI) as a
 * CPython callable for passing to ``sorted`` / ``list.sort`` / etc. */
extern CPyObject *PyCapsule_New(void *pointer, const char *name,
                                void *destructor);
extern void *PyCapsule_GetPointer(CPyObject *capsule, const char *name);
typedef struct _pcc_PyMethodDef {
    const char *ml_name;
    void *ml_meth;           /* CPyObject *(*)(CPyObject *, CPyObject *) */
    int ml_flags;
    const char *ml_doc;
} PccPyMethodDef;
extern CPyObject *PyCFunction_NewEx(PccPyMethodDef *ml, CPyObject *self,
                                    CPyObject *module);
extern int PyArg_UnpackTuple(CPyObject *args, const char *name,
                              long min, long max, ...);
extern CPyObject *PyExc_SystemExit;

static atomic_int g_initialized = 0;

static int py_cpy_debug_errors_enabled(void) {
    const char *flag = getenv("PCC_CPY_DEBUG_ERRORS");
    return flag != NULL && flag[0] != '\0' && flag[0] != '0';
}

static void py_cpy_debug_current_error(const char *where) {
    if (!py_cpy_debug_errors_enabled() || PyErr_Occurred() == NULL) {
        return;
    }
    CPyObject *etype = NULL;
    CPyObject *evalue = NULL;
    CPyObject *etb = NULL;
    PyErr_Fetch(&etype, &evalue, &etb);
    PyErr_NormalizeException(&etype, &evalue, &etb);
    const char *type_utf8 = "<null>";
    const char *value_utf8 = "<null>";
    CPyObject *type_str = etype != NULL ? PyObject_Str(etype) : NULL;
    CPyObject *value_str = evalue != NULL ? PyObject_Str(evalue) : NULL;
    if (type_str != NULL) {
        const char *s = PyUnicode_AsUTF8(type_str);
        if (s != NULL) type_utf8 = s;
    }
    if (value_str != NULL) {
        const char *s = PyUnicode_AsUTF8(value_str);
        if (s != NULL) value_utf8 = s;
    }
    fprintf(stderr, "pcc cpy error in %s: %s: %s\n", where, type_utf8, value_utf8);
    if (type_str != NULL) Py_DecRef(type_str);
    if (value_str != NULL) Py_DecRef(value_str);
    PyErr_Restore(etype, evalue, etb);
}

static void py_cpy_debug_result_state(const char *where, CPyObject *res) {
    if (res == NULL || PyErr_Occurred() != NULL) {
        py_cpy_debug_current_error(where);
    }
}

static void py_libpython_atexit(void) {
    if (atomic_load(&g_initialized) && Py_IsInitialized()) {
        Py_Finalize();
    }
}

static int py_cpy_system_exit_code(CPyObject *exc_value) {
    int code = 1;
    if (exc_value == NULL) {
        return code;
    }

    CPyObject *code_obj = PyObject_GetAttrString(exc_value, "code");
    if (code_obj == NULL) {
        PyErr_Clear();
        return code;
    }

    extern CPyObject _Py_NoneStruct;
    if (code_obj == &_Py_NoneStruct) {
        Py_DecRef(code_obj);
        return 0;
    }

    long long ll = PyLong_AsLongLong(code_obj);
    if (PyErr_Occurred() == NULL) {
        Py_DecRef(code_obj);
        return (int)ll;
    }

    PyErr_Clear();
    CPyObject *text = PyObject_Str(code_obj);
    if (text != NULL) {
        const char *utf8 = PyUnicode_AsUTF8(text);
        if (utf8 != NULL && utf8[0] != '\0') {
            fprintf(stderr, "%s\n", utf8);
        }
        Py_DecRef(text);
    } else {
        PyErr_Clear();
    }
    Py_DecRef(code_obj);
    return code;
}

int py_cpy_main_exitcode(void) {
    if (!atomic_load(&g_initialized) || !Py_IsInitialized()) {
        return 0;
    }
    if (PyErr_Occurred() == NULL) {
        return 0;
    }

    CPyObject *etype = NULL;
    CPyObject *evalue = NULL;
    CPyObject *etb = NULL;
    PyErr_Fetch(&etype, &evalue, &etb);
    PyErr_NormalizeException(&etype, &evalue, &etb);

    CPyObject *given = evalue != NULL ? evalue : etype;
    if (given != NULL && PyErr_GivenExceptionMatches(given, PyExc_SystemExit)) {
        int code = py_cpy_system_exit_code(evalue);
        if (etype != NULL) Py_DecRef(etype);
        if (evalue != NULL) Py_DecRef(evalue);
        if (etb != NULL) Py_DecRef(etb);
        return code;
    }

    PyErr_Restore(etype, evalue, etb);
    PyErr_Print();
    return 1;
}

static void py_cpy_sync_sys_argv(void) {
    int argc = py_runtime_program_argc > 0 ? py_runtime_program_argc : 1;
    CPyObject *sys_mod = PyImport_ImportModule("sys");
    if (sys_mod == NULL) return;
    CPyObject *argv_list = PyList_New((long)argc);
    if (argv_list == NULL) {
        Py_DecRef(sys_mod);
        return;
    }
    for (int i = 0; i < argc; i++) {
        const char *arg = "";
        if (
            py_runtime_program_argv != NULL
            && i < py_runtime_program_argc
            && py_runtime_program_argv[i] != NULL
        ) {
            arg = py_runtime_program_argv[i];
        }
        size_t n = 0;
        while (arg[n] != '\0') n++;
        CPyObject *arg_obj = PyUnicode_FromStringAndSize(arg, (long)n);
        if (arg_obj == NULL) {
            Py_DecRef(argv_list);
            Py_DecRef(sys_mod);
            return;
        }
        if (PyList_SetItem(argv_list, (long)i, arg_obj) != 0) {
            Py_DecRef(arg_obj);
            Py_DecRef(argv_list);
            Py_DecRef(sys_mod);
            return;
        }
    }
    (void)PyObject_SetAttrString(sys_mod, "argv", argv_list);
    Py_DecRef(argv_list);
    Py_DecRef(sys_mod);
}

static void py_cpy_seed_sys_path(void) {
    (void)PyRun_SimpleString(
        "import glob, os, sys\n"
        "def _pcc_add_path(path):\n"
        "    if path and path not in sys.path:\n"
        "        sys.path.insert(0, path)\n"
        "def _pcc_seed_root(root):\n"
        "    if not root:\n"
        "        return\n"
        "    _pcc_add_path(root)\n"
        "    venv_lib = os.path.join(root, '.venv', 'lib')\n"
        "    if os.path.isdir(venv_lib):\n"
        "        for site in glob.glob(os.path.join(venv_lib, 'python*', 'site-packages')):\n"
        "            _pcc_add_path(site)\n"
        "cwd = os.getcwd()\n"
        "if cwd:\n"
        "    _pcc_seed_root(cwd)\n"
        "if sys.argv:\n"
        "    argv0_dir = os.path.dirname(os.path.abspath(sys.argv[0]))\n"
        "    _pcc_add_path(argv0_dir)\n"
        "    _pcc_seed_root(os.path.dirname(argv0_dir))\n"
        "    _pcc_seed_root(os.path.dirname(os.path.dirname(argv0_dir)))\n"
    );
}

void py_cpy_ensure_init(void) {
    int expected = 0;
    if (atomic_compare_exchange_strong(&g_initialized, &expected, 1)) {
        Py_Initialize();
        py_runtime_program_args_hook = py_cpy_sync_sys_argv;
        py_cpy_sync_sys_argv();
        py_cpy_seed_sys_path();
        atexit(py_libpython_atexit);
    }
}

void *py_cpy_import(const char *name) {
    py_cpy_ensure_init();
    CPyObject *res = PyImport_ImportModule(name);
    if (py_cpy_debug_errors_enabled() && (res == NULL || PyErr_Occurred() != NULL)) {
        fprintf(
            stderr, "pcc cpy import target: %s\n",
            name != NULL ? name : "<null>"
        );
    }
    py_cpy_debug_result_state("py_cpy_import", res);
    return (void *)res;
}

void *py_cpy_getattr(void *obj, const char *name) {
    if (obj == NULL) return NULL;
    CPyObject *res = PyObject_GetAttrString((CPyObject *)obj, name);
    py_cpy_debug_result_state("py_cpy_getattr", res);
    return (void *)res;
}

int py_cpy_setattr(void *obj, const char *name, void *value) {
    if (obj == NULL) return -1;
    int rc = PyObject_SetAttrString(
        (CPyObject *)obj, name, (CPyObject *)value
    );
    if (rc != 0) py_cpy_debug_current_error("py_cpy_setattr");
    return rc;
}

void *py_cpy_call_noargs(void *callable) {
    if (callable == NULL) return NULL;
    CPyObject *res = PyObject_CallNoArgs((CPyObject *)callable);
    py_cpy_debug_result_state("py_cpy_call_noargs", res);
    return (void *)res;
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

static int py_cpy_is_instance(CPyObject *obj, CPyObject *type_obj) {
    int rc = PyObject_IsInstance(obj, type_obj);
    if (rc < 0) {
        PyErr_Clear();
        return 0;
    }
    return rc != 0;
}

PyObject *py_cpy_to_pcc_obj(void *cpy_obj) {
    if (cpy_obj == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *obj = (CPyObject *)cpy_obj;

    if (obj == &_Py_NoneStruct) {
        py_incref(py_None);
        return py_None;
    }
    if (py_cpy_is_instance(obj, &PyBool_Type)) {
        int truth = PyObject_IsTrue(obj);
        if (truth < 0) return NULL;
        return py_bool_from_bit(truth != 0);
    }
    if (py_cpy_is_instance(obj, &PyLong_Type)) {
        long long value = PyLong_AsLongLong(obj);
        if (PyErr_Occurred() != NULL) {
            PyErr_Clear();
            return py_cpy_to_pcc_str(obj);
        }
        return py_int_from_i64((int64_t)value);
    }
    if (py_cpy_is_instance(obj, &PyFloat_Type)) {
        double value = PyFloat_AsDouble(obj);
        if (PyErr_Occurred() != NULL) return NULL;
        return py_float_from_f64(value);
    }
    if (py_cpy_is_instance(obj, &PyUnicode_Type)) {
        long n = 0;
        const char *utf8 = PyUnicode_AsUTF8AndSize(obj, &n);
        if (utf8 == NULL) return NULL;
        return py_str_new(utf8, (int64_t)n);
    }
    if (py_cpy_is_instance(obj, &PyList_Type)) {
        long n = PyList_Size(obj);
        if (n < 0) return NULL;
        PyObject *out = py_list_new((int64_t)n);
        if (out == NULL) return NULL;
        for (long i = 0; i < n; i++) {
            CPyObject *item = PyList_GetItem(obj, i);  /* borrowed */
            PyObject *pcc_item = py_cpy_to_pcc_obj(item);
            if (pcc_item == NULL) {
                py_decref(out);
                return NULL;
            }
            py_list_append(out, pcc_item);
            py_decref(pcc_item);
        }
        return out;
    }
    if (py_cpy_is_instance(obj, &PyTuple_Type)) {
        long n = PyTuple_Size(obj);
        if (n < 0) return NULL;
        PyObject *out = py_tuple_new((int64_t)n);
        if (out == NULL) return NULL;
        for (long i = 0; i < n; i++) {
            CPyObject *item = PyTuple_GetItem(obj, i);  /* borrowed */
            PyObject *pcc_item = py_cpy_to_pcc_obj(item);
            if (pcc_item == NULL) {
                py_decref(out);
                return NULL;
            }
            py_tuple_set_item(out, (int64_t)i, pcc_item);
            py_decref(pcc_item);
        }
        return out;
    }
    if (py_cpy_is_instance(obj, &PyDict_Type)) {
        PyObject *out = py_dict_new();
        if (out == NULL) return NULL;
        long pos = 0;
        CPyObject *key = NULL;
        CPyObject *value = NULL;
        while (PyDict_Next(obj, &pos, &key, &value)) {
            PyObject *pcc_key = py_cpy_to_pcc_obj(key);
            PyObject *pcc_value = py_cpy_to_pcc_obj(value);
            if (pcc_key == NULL || pcc_value == NULL) {
                py_decref(pcc_key);
                py_decref(pcc_value);
                py_decref(out);
                return NULL;
            }
            py_dict_set(out, pcc_key, pcc_value);
            py_decref(pcc_key);
            py_decref(pcc_value);
        }
        return out;
    }
    if (py_cpy_is_instance(obj, &PySet_Type)) {
        CPyObject *it = PyObject_GetIter(obj);
        if (it == NULL) return NULL;
        PyObject *out = py_set_new();
        if (out == NULL) {
            Py_DecRef(it);
            return NULL;
        }
        for (;;) {
            CPyObject *item = PyIter_Next(it);
            if (item == NULL) break;
            PyObject *pcc_item = py_cpy_to_pcc_obj(item);
            Py_DecRef(item);
            if (pcc_item == NULL) {
                Py_DecRef(it);
                py_decref(out);
                return NULL;
            }
            py_set_add(out, pcc_item);
            py_decref(pcc_item);
        }
        Py_DecRef(it);
        if (PyErr_Occurred() != NULL) {
            py_decref(out);
            return NULL;
        }
        return out;
    }

    return py_cpy_to_pcc_str(obj);
}

void py_cpy_decref(void *obj) {
    if (obj != NULL) Py_DecRef((CPyObject *)obj);
}

void py_cpy_incref(void *obj) {
    if (obj != NULL) Py_IncRef((CPyObject *)obj);
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
extern CPyObject *PyDict_Copy(CPyObject *d);
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
    CPyObject *res = PyObject_CallOneArg((CPyObject *)callable, (CPyObject *)a);
    py_cpy_debug_result_state("py_cpy_call1", res);
    return (void *)res;
}

void *py_cpy_call2(void *callable, void *a, void *b) {
    if (callable == NULL) return NULL;
    CPyObject *res = PyObject_CallFunctionObjArgs(
        (CPyObject *)callable, (CPyObject *)a, (CPyObject *)b, (CPyObject *)NULL
    );
    py_cpy_debug_result_state("py_cpy_call2", res);
    return (void *)res;
}

void *py_cpy_call3(void *callable, void *a, void *b, void *c) {
    if (callable == NULL) return NULL;
    CPyObject *res = PyObject_CallFunctionObjArgs(
        (CPyObject *)callable,
        (CPyObject *)a, (CPyObject *)b, (CPyObject *)c,
        (CPyObject *)NULL
    );
    py_cpy_debug_result_state("py_cpy_call3", res);
    return (void *)res;
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
    py_cpy_debug_result_state("py_cpy_call_argv", result);
    return (void *)result;
}

/* Tuple + dict call for positional + keyword arguments.
 *
 * Positional argv[0..n_pos) is stolen into a PyTuple.
 * Keyword kw_vals[0..n_kw) is borrowed by PyDict_SetItem (dict
 * increfs). The caller still owns each kw_vals entry and must decref
 * after this returns. */
extern int PyDict_SetItemString(CPyObject *d, const char *key, CPyObject *val);
/* Dispatch ``fn(*args)`` where ``args`` is a pcc list/tuple. Convert
 * the pcc container to a CPython tuple (PyObject_Call requires the
 * positional-args container to be a tuple; a list or other sequence
 * would trip ``_PyFunction_Vectorcall`` in CPython 3.11+) and dispatch
 * via ``PyObject_Call``. Returns a new owned ref or NULL on error. */
extern CPyObject *PySequence_Tuple(CPyObject *iterable);
void *py_cpy_call_list(void *callable, PyObject *args) {
    if (callable == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *seq = (CPyObject *)py_cpy_from_pcc_obj(args);
    if (seq == NULL) return NULL;
    CPyObject *tup = PySequence_Tuple(seq);
    Py_DecRef(seq);
    if (tup == NULL) return NULL;
    CPyObject *result = PyObject_Call(
        (CPyObject *)callable, tup, (CPyObject *)NULL
    );
    Py_DecRef(tup);
    py_cpy_debug_result_state("py_cpy_call_list", result);
    return (void *)result;
}

void *py_cpy_call_list_kwdict(void *callable, PyObject *args, void *kwargs_dict) {
    if (callable == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *seq = (CPyObject *)py_cpy_from_pcc_obj(args);
    if (seq == NULL) return NULL;
    CPyObject *tup = PySequence_Tuple(seq);
    Py_DecRef(seq);
    if (tup == NULL) return NULL;
    CPyObject *result = PyObject_Call(
        (CPyObject *)callable, tup, (CPyObject *)kwargs_dict
    );
    Py_DecRef(tup);
    py_cpy_debug_result_state("py_cpy_call_list_kwdict", result);
    return (void *)result;
}

/* ---- Lambda wrapping: pcc FuncDef → CPython callable ---------------- */

/* Typed function pointer shape for a pcc FuncDef with ABI
 * ``CPyObject *(CPyObject *)`` — a single DynType-in DynType-out.
 * pcc's codegen lowers this signature as ``ptr(ptr)`` (PyObject* /
 * opaque), matching CPython's ``PyObject *`` layout exactly. */
typedef CPyObject *(*_pcc_1arg_fn_t)(CPyObject *);

/* PyCFunction trampoline: PyCFunction signature is
 * ``PyObject *(*)(PyObject *self, PyObject *args)`` where ``self`` is
 * the ``m_self`` we passed to ``PyCFunction_NewEx`` (a PyCapsule
 * holding the pcc function pointer). ``args`` is a positional tuple —
 * use ``PyArg_UnpackTuple`` to get the single positional arg. */
static CPyObject *_pcc_1arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *arg;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 1, 1, &arg)) return NULL;
    /* Forward the arg to the pcc function. The pcc emit for this
     * body tags the incoming PyObject* as a CPython value so attr /
     * method ops route through ``py_cpy_getattr`` rather than the
     * pcc-native ``py_obj_getattr``. */
    return ((_pcc_1arg_fn_t)fn_ptr)(arg);
}

static PccPyMethodDef _pcc_1arg_methdef = {
    .ml_name = "pcc_lambda",
    .ml_meth = (void *)_pcc_1arg_trampoline,
    .ml_flags = 0x1,   /* METH_VARARGS */
    .ml_doc = NULL,
};

/* Wrap a pcc FuncDef function pointer (signature CPyObject* <- CPyObject*)
 * as a CPython PyCFunction. The caller uses the returned value the same
 * as any CPython callable (``PyObject_Call`` / ``PyObject_CallOneArg``
 * etc.). Returns NULL on failure. Caller owns the returned ref. */
void *py_cpy_wrap_pcc_1arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_1arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);  /* PyCFunction holds its own ref via m_self. */
    return (void *)callable;
}

/* 0-arg variant. Signature: ``CPyObject *(void)``. */
typedef CPyObject *(*_pcc_0arg_fn_t)(void);
static CPyObject *_pcc_0arg_trampoline(CPyObject *self, CPyObject *args) {
    (void)args;
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    return ((_pcc_0arg_fn_t)fn_ptr)();
}
static PccPyMethodDef _pcc_0arg_methdef = {
    .ml_name = "pcc_lambda_0",
    .ml_meth = (void *)_pcc_0arg_trampoline,
    .ml_flags = 0x4,   /* METH_NOARGS */
    .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_0arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_0arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 2-arg variant. Signature: ``CPyObject *(CPyObject *, CPyObject *)``. */
typedef CPyObject *(*_pcc_2arg_fn_t)(CPyObject *, CPyObject *);
static CPyObject *_pcc_2arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 2, 2, &a1, &a2)) return NULL;
    return ((_pcc_2arg_fn_t)fn_ptr)(a1, a2);
}
static PccPyMethodDef _pcc_2arg_methdef = {
    .ml_name = "pcc_lambda_2",
    .ml_meth = (void *)_pcc_2arg_trampoline,
    .ml_flags = 0x1,
    .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_2arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_2arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 3-arg variant. Signature: ``CPyObject *(CPyObject *, CPyObject *, CPyObject *)``. */
typedef CPyObject *(*_pcc_3arg_fn_t)(CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_3arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 3, 3, &a1, &a2, &a3)) return NULL;
    return ((_pcc_3arg_fn_t)fn_ptr)(a1, a2, a3);
}
static PccPyMethodDef _pcc_3arg_methdef = {
    .ml_name = "pcc_lambda_3",
    .ml_meth = (void *)_pcc_3arg_trampoline,
    .ml_flags = 0x1,
    .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_3arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_3arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 4-arg variant. */
typedef CPyObject *(*_pcc_4arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_4arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 4, 4, &a1, &a2, &a3, &a4)) return NULL;
    return ((_pcc_4arg_fn_t)fn_ptr)(a1, a2, a3, a4);
}
static PccPyMethodDef _pcc_4arg_methdef = {
    .ml_name = "pcc_lambda_4",
    .ml_meth = (void *)_pcc_4arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_4arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_4arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 5-arg variant. */
typedef CPyObject *(*_pcc_5arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_5arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4, *a5;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 5, 5, &a1, &a2, &a3, &a4, &a5)) return NULL;
    return ((_pcc_5arg_fn_t)fn_ptr)(a1, a2, a3, a4, a5);
}
static PccPyMethodDef _pcc_5arg_methdef = {
    .ml_name = "pcc_lambda_5",
    .ml_meth = (void *)_pcc_5arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_5arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_5arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 6-arg variant. */
typedef CPyObject *(*_pcc_6arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_6arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4, *a5, *a6;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 6, 6, &a1, &a2, &a3, &a4, &a5, &a6)) return NULL;
    return ((_pcc_6arg_fn_t)fn_ptr)(a1, a2, a3, a4, a5, a6);
}
static PccPyMethodDef _pcc_6arg_methdef = {
    .ml_name = "pcc_lambda_6",
    .ml_meth = (void *)_pcc_6arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_6arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_6arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 7-arg variant. */
typedef CPyObject *(*_pcc_7arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_7arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4, *a5, *a6, *a7;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 7, 7, &a1, &a2, &a3, &a4, &a5, &a6, &a7)) return NULL;
    return ((_pcc_7arg_fn_t)fn_ptr)(a1, a2, a3, a4, a5, a6, a7);
}
static PccPyMethodDef _pcc_7arg_methdef = {
    .ml_name = "pcc_lambda_7",
    .ml_meth = (void *)_pcc_7arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_7arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_7arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 8-arg variant. */
typedef CPyObject *(*_pcc_8arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_8arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4, *a5, *a6, *a7, *a8;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 8, 8, &a1, &a2, &a3, &a4, &a5, &a6, &a7, &a8)) return NULL;
    return ((_pcc_8arg_fn_t)fn_ptr)(a1, a2, a3, a4, a5, a6, a7, a8);
}
static PccPyMethodDef _pcc_8arg_methdef = {
    .ml_name = "pcc_lambda_8",
    .ml_meth = (void *)_pcc_8arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_8arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_8arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

/* 9-arg variant. */
typedef CPyObject *(*_pcc_9arg_fn_t)(CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *, CPyObject *);
static CPyObject *_pcc_9arg_trampoline(CPyObject *self, CPyObject *args) {
    void *fn_ptr = PyCapsule_GetPointer(self, NULL);
    if (fn_ptr == NULL) return NULL;
    CPyObject *a1, *a2, *a3, *a4, *a5, *a6, *a7, *a8, *a9;
    if (!PyArg_UnpackTuple(args, "pcc_lambda", 9, 9, &a1, &a2, &a3, &a4, &a5, &a6, &a7, &a8, &a9)) return NULL;
    return ((_pcc_9arg_fn_t)fn_ptr)(a1, a2, a3, a4, a5, a6, a7, a8, a9);
}
static PccPyMethodDef _pcc_9arg_methdef = {
    .ml_name = "pcc_lambda_9",
    .ml_meth = (void *)_pcc_9arg_trampoline,
    .ml_flags = 0x1, .ml_doc = NULL,
};
void *py_cpy_wrap_pcc_9arg(void *fn_ptr) {
    if (fn_ptr == NULL) return NULL;
    py_cpy_ensure_init();
    CPyObject *cap = PyCapsule_New(fn_ptr, NULL, NULL);
    if (cap == NULL) return NULL;
    CPyObject *callable = PyCFunction_NewEx(
        &_pcc_9arg_methdef, cap, (CPyObject *)NULL
    );
    Py_DecRef(cap);
    return (void *)callable;
}

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
    py_cpy_debug_result_state("py_cpy_call_kw", result);
    return (void *)result;
}

void *py_cpy_call_kwdict(void *callable,
                         int64_t n_pos, void **argv,
                         void *kwargs_dict) {
    if (callable == NULL) return NULL;
    CPyObject *tup = PyTuple_New((long)n_pos);
    if (tup == NULL) return NULL;
    for (int64_t i = 0; i < n_pos; i++) {
        PyTuple_SetItem(tup, (long)i, (CPyObject *)argv[i]);  /* steals */
    }
    CPyObject *result = PyObject_Call(
        (CPyObject *)callable, tup, (CPyObject *)kwargs_dict
    );
    Py_DecRef(tup);
    py_cpy_debug_result_state("py_cpy_call_kwdict", result);
    return (void *)result;
}

void *py_cpy_call_kwdict_plus(void *callable,
                              int64_t n_pos, void **argv,
                              int64_t n_kw,
                              const char **kw_names, void **kw_vals,
                              void *kwargs_dict) {
    if (callable == NULL) return NULL;
    CPyObject *tup = PyTuple_New((long)n_pos);
    if (tup == NULL) return NULL;
    for (int64_t i = 0; i < n_pos; i++) {
        PyTuple_SetItem(tup, (long)i, (CPyObject *)argv[i]);  /* steals */
    }
    CPyObject *kwargs = kwargs_dict != NULL
        ? PyDict_Copy((CPyObject *)kwargs_dict)
        : PyDict_New();
    if (kwargs == NULL) {
        Py_DecRef(tup);
        return NULL;
    }
    for (int64_t i = 0; i < n_kw; i++) {
        PyDict_SetItemString(kwargs, kw_names[i], (CPyObject *)kw_vals[i]);
    }
    CPyObject *result = PyObject_Call((CPyObject *)callable, tup, kwargs);
    Py_DecRef(tup);
    Py_DecRef(kwargs);
    py_cpy_debug_result_state("py_cpy_call_kwdict_plus", result);
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

int py_cpy_setattr(void *obj, const char *name, void *value) {
    (void)obj; (void)name; (void)value;
    py_cpy_ensure_init();
    return -1;
}

int py_cpy_main_exitcode(void) {
    return 0;
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

PyObject *py_cpy_to_pcc_obj(void *cpy_obj) {
    (void)cpy_obj;
    py_cpy_ensure_init();
    return NULL;
}

void py_cpy_decref(void *obj) {
    (void)obj;
    py_cpy_ensure_init();
}

void py_cpy_incref(void *obj) {
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
void *py_cpy_call_list(void *c, PyObject *args) {
    (void)c; (void)args; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_call_list_kwdict(void *c, PyObject *args, void *kwargs_dict) {
    (void)c; (void)args; (void)kwargs_dict;
    py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_0arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_1arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_2arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_3arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_4arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_5arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_6arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_7arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_8arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
}
void *py_cpy_wrap_pcc_9arg(void *fn_ptr) {
    (void)fn_ptr; py_cpy_ensure_init(); return NULL;
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

void *py_cpy_call_kwdict(void *c, int64_t n_pos, void **argv, void *kwargs_dict) {
    (void)c; (void)n_pos; (void)argv; (void)kwargs_dict;
    py_cpy_ensure_init();
    return NULL;
}

void *py_cpy_call_kwdict_plus(void *c, int64_t n_pos, void **argv,
                              int64_t n_kw,
                              const char **kw_names, void **kw_vals,
                              void *kwargs_dict) {
    (void)c; (void)n_pos; (void)argv;
    (void)n_kw; (void)kw_names; (void)kw_vals; (void)kwargs_dict;
    py_cpy_ensure_init();
    return NULL;
}

#endif /* PCC_WITH_LIBPYTHON */
