/* pcc/py_runtime/src/py_os_path.c
 *
 * os.path helpers split out of py_os.c. Holds path_join /
 * path_basename / path_exists; the env helpers live in
 * py_os_env.c so they can be independently replaced by the
 * pcc-Python port.
 */

#include "py_internal.h"

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static PyObject *coerce_path_str(PyObject *o, PyObject **owned) {
    *owned = NULL;
    if (o == NULL) return NULL;
    if (py_type_of(o) == PY_TYPE_STR) return o;
    *owned = py_obj_str(o);
    return *owned;
}

static int64_t path_seq_len(PyObject *parts) {
    if (parts == NULL || PY_IS_TAGGED_INT(parts)) return -1;
    switch (py_type_of(parts)) {
        case PY_TYPE_LIST:
            return ((PyListObject *)parts)->length;
        case PY_TYPE_TUPLE:
            return ((PyTupleObject *)parts)->len;
        default:
            return -1;
    }
}

static PyObject *path_seq_borrow(PyObject *parts, int64_t i) {
    if (parts == NULL || PY_IS_TAGGED_INT(parts)) return NULL;
    switch (py_type_of(parts)) {
        case PY_TYPE_LIST:
            return ((PyListObject *)parts)->items[i];
        case PY_TYPE_TUPLE:
            return ((PyTupleObject *)parts)->items[i];
        default:
            return NULL;
    }
}

static int buf_reserve(char **buf, int64_t *cap, int64_t want) {
    if (want <= *cap) return 0;
    int64_t new_cap = (*cap > 0) ? *cap : 32;
    while (new_cap < want) {
        new_cap *= 2;
    }
    char *grown = (char *)realloc(*buf, (size_t)new_cap);
    if (grown == NULL) return -1;
    *buf = grown;
    *cap = new_cap;
    return 0;
}

static int buf_append(
    char **buf, int64_t *len, int64_t *cap, const char *src, int64_t src_len
) {
    if (src_len <= 0) return 0;
    if (buf_reserve(buf, cap, *len + src_len + 1) != 0) return -1;
    memcpy(*buf + *len, src, (size_t)src_len);
    *len += src_len;
    (*buf)[*len] = '\0';
    return 0;
}

PyObject *py_os_path_join(PyObject *parts) {
    int64_t n = path_seq_len(parts);
    if (n < 0) return NULL;
    if (n == 0) return py_str_new("", 0);

    char *buf = NULL;
    int64_t len = 0;
    int64_t cap = 0;
    for (int64_t i = 0; i < n; i++) {
        PyObject *owned = NULL;
        PyObject *item = coerce_path_str(path_seq_borrow(parts, i), &owned);
        if (item == NULL) {
            py_decref(owned);
            free(buf);
            return NULL;
        }
        PyStrObject *s = (PyStrObject *)item;
        const char *part = s->data;
        int64_t part_len = s->byte_len;

        if (i == 0) {
            if (buf_append(&buf, &len, &cap, part, part_len) != 0) {
                py_decref(owned);
                free(buf);
                return NULL;
            }
            py_decref(owned);
            continue;
        }

        if (part_len > 0 && part[0] == '/') {
            len = 0;
            if (buf_append(&buf, &len, &cap, part, part_len) != 0) {
                py_decref(owned);
                free(buf);
                return NULL;
            }
            py_decref(owned);
            continue;
        }

        if (len > 0 && buf[len - 1] != '/') {
            if (buf_append(&buf, &len, &cap, "/", 1) != 0) {
                py_decref(owned);
                free(buf);
                return NULL;
            }
        }
        if (buf_append(&buf, &len, &cap, part, part_len) != 0) {
            py_decref(owned);
            free(buf);
            return NULL;
        }
        py_decref(owned);
    }

    PyObject *out = py_str_new(buf != NULL ? buf : "", len);
    free(buf);
    return out;
}

PyObject *py_os_path_dirname(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return NULL;
    }
    PyStrObject *s = (PyStrObject *)item;
    int64_t n = s->byte_len;

    int64_t last = -1;
    for (int64_t i = 0; i < n; i++) {
        if (s->data[i] == '/') last = i;
    }

    int64_t head_len = last + 1;
    if (head_len == 0) {
        py_decref(owned);
        return py_str_new("", 0);
    }

    int all_slash = 1;
    for (int64_t j = 0; j < head_len; j++) {
        if (s->data[j] != '/') { all_slash = 0; break; }
    }

    int64_t out_len = head_len;
    if (!all_slash) {
        while (out_len > 0 && s->data[out_len - 1] == '/') {
            out_len--;
        }
    }

    PyObject *out = py_str_new(s->data, out_len);
    py_decref(owned);
    return out;
}

PyObject *py_os_path_basename(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return NULL;
    }
    PyStrObject *s = (PyStrObject *)item;
    int64_t end = s->byte_len;
    while (end > 0 && s->data[end - 1] == '/') {
        end--;
    }
    if (end == 0) {
        py_decref(owned);
        return py_str_new("", 0);
    }
    int64_t start = end;
    while (start > 0 && s->data[start - 1] != '/') {
        start--;
    }
    PyObject *out = py_str_new(s->data + start, end - start);
    py_decref(owned);
    return out;
}

int py_os_path_isfile(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return 0;
    }
    const char *raw = py_str_utf8(item);
    int ok = (py_path_stat_kind(raw) == 1) ? 1 : 0;
    py_decref(owned);
    return ok;
}

int py_os_path_isdir(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return 0;
    }
    const char *raw = py_str_utf8(item);
    int ok = (py_path_stat_kind(raw) == 2) ? 1 : 0;
    py_decref(owned);
    return ok;
}

/* Simplified abspath: if `path` is already absolute, return a copy
 * unchanged; otherwise prepend cwd + '/'. Does not normalize . / ..
 * — pipeline.py callers don't feed unnormalized inputs, and the
 * full normpath algorithm is left for a follow-up if regressions
 * surface. */
PyObject *py_os_path_abspath(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return NULL;
    }
    PyStrObject *s = (PyStrObject *)item;
    int64_t in_len = s->byte_len;

    if (in_len > 0 && s->data[0] == '/') {
        PyObject *out = py_str_new(s->data, in_len);
        py_decref(owned);
        return out;
    }

    const char *cwd = py_path_getcwd();
    if (cwd == NULL) {
        py_decref(owned);
        return NULL;
    }
    int64_t cwd_len = (int64_t)strlen(cwd);
    if (in_len == 0) {
        py_decref(owned);
        return py_str_new(cwd, cwd_len);
    }
    int64_t total = cwd_len + 1 + in_len;
    PyObject *out = py_str_new(NULL, total);
    if (out == NULL) {
        py_decref(owned);
        return NULL;
    }
    PyStrObject *outs = (PyStrObject *)out;
    memcpy(outs->data, cwd, (size_t)cwd_len);
    outs->data[cwd_len] = '/';
    memcpy(outs->data + cwd_len + 1, s->data, (size_t)in_len);
    py_decref(owned);
    return out;
}

PyObject *py_os_path_getmtime(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return NULL;
    }
    const char *raw = py_str_utf8(item);
    double t = py_path_stat_mtime(raw);
    py_decref(owned);
    return py_float_from_f64(t);
}

int py_os_path_exists(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return 0;
    }
    const char *raw = py_str_utf8(item);
    int ok = (raw != NULL && access(raw, F_OK) == 0) ? 1 : 0;
    py_decref(owned);
    return ok;
}
