/* Native text-file helpers for pcc-Python's open/read/write fast path. */

#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    PyObjectHeader h;
    FILE *fp;
    int closed;
} PyFileObject;

static PyObject *coerce_str(PyObject *o, PyObject **owned) {
    *owned = NULL;
    if (o == NULL) return NULL;
    if (py_type_of(o) == PY_TYPE_STR) return o;
    *owned = py_obj_str(o);
    return *owned;
}

PyObject *py_file_open(PyObject *path, PyObject *mode) {
    PyObject *path_owned = NULL;
    PyObject *mode_owned = NULL;
    PyObject *path_s = coerce_str(path, &path_owned);
    PyObject *mode_s = NULL;
    if (mode == NULL || mode == py_None) {
        mode_s = py_str_new("r", 1);
        mode_owned = mode_s;
    } else {
        mode_s = coerce_str(mode, &mode_owned);
    }
    if (path_s == NULL || mode_s == NULL) {
        py_decref(path_owned);
        py_decref(mode_owned);
        return NULL;
    }

    const char *path_c = py_str_utf8(path_s);
    const char *mode_c = py_str_utf8(mode_s);
    FILE *fp = fopen(path_c, mode_c);
    py_decref(path_owned);
    py_decref(mode_owned);
    if (fp == NULL) return NULL;

    PyFileObject *out = (PyFileObject *)malloc(sizeof(PyFileObject));
    if (out == NULL) {
        fclose(fp);
        return NULL;
    }
    out->h.refcount = 1;
    out->h.type_tag = PY_TYPE_FILE;
    out->h.flags = 0;
    out->fp = fp;
    out->closed = 0;
    return (PyObject *)out;
}

PyObject *py_file_read_all(PyObject *file) {
    if (file == NULL || py_type_of(file) != PY_TYPE_FILE) return NULL;
    PyFileObject *f = (PyFileObject *)file;
    if (f->closed || f->fp == NULL) return NULL;

    char *buf = NULL;
    size_t len = 0;
    size_t cap = 0;
    char tmp[4096];
    for (;;) {
        size_t n = fread(tmp, 1, sizeof(tmp), f->fp);
        if (n > 0) {
            if (len + n + 1 > cap) {
                size_t new_cap = cap ? cap : 4096;
                while (new_cap < len + n + 1) new_cap *= 2;
                char *grown = (char *)realloc(buf, new_cap);
                if (grown == NULL) {
                    free(buf);
                    return NULL;
                }
                buf = grown;
                cap = new_cap;
            }
            memcpy(buf + len, tmp, n);
            len += n;
        }
        if (n < sizeof(tmp)) {
            if (ferror(f->fp)) {
                free(buf);
                return NULL;
            }
            break;
        }
    }
    PyObject *out = py_str_new(buf ? buf : "", (int64_t)len);
    free(buf);
    return out;
}

PyObject *py_file_write(PyObject *file, PyObject *text) {
    if (file == NULL || py_type_of(file) != PY_TYPE_FILE) return NULL;
    PyFileObject *f = (PyFileObject *)file;
    if (f->closed || f->fp == NULL) return NULL;

    PyObject *owned = NULL;
    PyObject *s = coerce_str(text, &owned);
    if (s == NULL) {
        py_decref(owned);
        return NULL;
    }
    int64_t n = py_str_byte_len(s);
    const char *data = py_str_utf8(s);
    size_t wrote = 0;
    if (n > 0) {
        wrote = fwrite(data, 1, (size_t)n, f->fp);
    }
    py_decref(owned);
    return py_int_from_i64((int64_t)wrote);
}

void py_file_close(PyObject *file) {
    if (file == NULL || py_type_of(file) != PY_TYPE_FILE) return;
    PyFileObject *f = (PyFileObject *)file;
    if (!f->closed && f->fp != NULL) {
        fclose(f->fp);
        f->fp = NULL;
        f->closed = 1;
    }
}
