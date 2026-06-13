/* pcc/py_runtime/src/py_os_env.c
 *
 * Environment-variable helpers split out of py_os.c so the pcc-
 * Python port (py_os_env.py) can replace just these three symbols
 * in the libpy_runtime_pcc_py.a archive without touching the path
 * helpers that still live in py_os_path.c.
 */

#include "py_internal.h"

#include <stdlib.h>
#include <string.h>

static PyObject *coerce_path_str(PyObject *o, PyObject **owned) {
    *owned = NULL;
    if (o == NULL) return NULL;
    if (py_type_of(o) == PY_TYPE_STR) return o;
    *owned = py_obj_str(o);
    return *owned;
}

PyObject *py_os_getenv(PyObject *key, PyObject *default_value) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(key, &owned);
    if (item == NULL) {
        py_decref(owned);
        return default_value;
    }
    const char *name = py_str_utf8(item);
    if (name == NULL) {
        py_decref(owned);
        return default_value;
    }
    const char *raw = getenv(name);
    py_decref(owned);
    if (raw == NULL) return default_value;
    return py_str_new(raw, (int64_t)strlen(raw));
}

PyObject *py_os_putenv(PyObject *key, PyObject *value) {
    PyObject *owned_key = NULL;
    PyObject *owned_value = NULL;
    PyObject *key_obj = coerce_path_str(key, &owned_key);
    PyObject *value_obj = coerce_path_str(value, &owned_value);
    if (key_obj == NULL || value_obj == NULL) {
        py_decref(owned_key);
        py_decref(owned_value);
        return py_None;
    }
    const char *raw_key = py_str_utf8(key_obj);
    const char *raw_value = py_str_utf8(value_obj);
    if (raw_key != NULL && raw_value != NULL) {
        (void)setenv(raw_key, raw_value, 1);
    }
    py_decref(owned_key);
    py_decref(owned_value);
    return py_None;
}

/* os.environ[key]: CPython mapping semantics — the key must be a str
 * (TypeError otherwise, like CPython's encodekey()) and a missing
 * variable raises KeyError carrying the key. py_os_getenv stays
 * non-raising for os.getenv() / os.environ.get(). */
PyObject *py_os_environ_getitem(PyObject *key) {
    if (key == NULL || py_type_of(key) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "str expected"));
        return NULL;
    }
    const char *name = py_str_utf8(key);
    if (name == NULL) {
        py_raise_owned(py_exc_new_with_value(PY_EXC_KEYERROR, key));
        return NULL;
    }
    const char *raw = getenv(name);
    if (raw == NULL) {
        py_raise_owned(py_exc_new_with_value(PY_EXC_KEYERROR, key));
        return NULL;
    }
    return py_str_new(raw, (int64_t)strlen(raw));
}

int32_t py_os_environ_contains(PyObject *key) {
    if (key == NULL || py_type_of(key) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "str expected"));
        return -1;
    }
    const char *name = py_str_utf8(key);
    if (name == NULL) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "str expected"));
        return -1;
    }
    return getenv(name) != NULL ? 1 : 0;
}

/* os.environ[key] = value: CPython mapping semantics — both key and
 * value must be str (TypeError otherwise); the store is visible to the
 * process environment (setenv), matching CPython's putenv-backed
 * __setitem__. py_os_putenv stays coercing/non-raising. */
PyObject *py_os_environ_setitem(PyObject *key, PyObject *value) {
    if (key == NULL || py_type_of(key) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "str expected"));
        return NULL;
    }
    if (value == NULL || py_type_of(value) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "str expected"));
        return NULL;
    }
    const char *raw_key = py_str_utf8(key);
    const char *raw_value = py_str_utf8(value);
    if (raw_key != NULL && raw_value != NULL) {
        (void)setenv(raw_key, raw_value, 1);
    }
    return py_None;
}

PyObject *py_os_unsetenv(PyObject *key) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(key, &owned);
    if (item == NULL) {
        py_decref(owned);
        return py_None;
    }
    const char *raw = py_str_utf8(item);
    if (raw != NULL) {
        (void)unsetenv(raw);
    }
    py_decref(owned);
    return py_None;
}
