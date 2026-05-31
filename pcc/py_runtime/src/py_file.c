/* Native text-file helpers for pcc-Python's open/read/write fast path. */

#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    PyObjectHeader h;
    FILE *fp;
    int closed;
    int binary;
} PyFileObject;

static PyObject *py_file_pin_current_vthread(const char *reason) {
    PyObject *vt = py_virtual_thread_current();
    if (vt == NULL || vt == py_None) {
        py_decref(vt);
        return NULL;
    }
    if (py_virtual_thread_pin_enter(vt, reason) < 0) {
        py_decref(vt);
        return NULL;
    }
    return vt;
}

static void py_file_unpin_current_vthread(PyObject *vt) {
    if (vt == NULL) return;
    (void)py_virtual_thread_pin_leave(vt);
    py_decref(vt);
}

static PyObject *coerce_str(PyObject *o, PyObject **owned) {
    *owned = NULL;
    if (o == NULL) return NULL;
    if (py_type_of(o) == PY_TYPE_STR) return o;
    *owned = py_obj_str(o);
    return *owned;
}

static int mode_is_binary(PyObject *mode_s) {
    if (mode_s == NULL) return 0;
    const char *mode_c = py_str_utf8(mode_s);
    int64_t n = py_str_byte_len(mode_s);
    for (int64_t i = 0; i < n; i++) {
        if (mode_c[i] == 'b') return 1;
    }
    return 0;
}

static PyObject *file_bytes_or_str(PyFileObject *f, const char *buf, int64_t n) {
    if (f != NULL && f->binary) {
        return py_bytes_new(buf, n);
    }
    return py_str_new(buf, n);
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
    int binary = mode_is_binary(mode_s);
    PyObject *vt = py_file_pin_current_vthread("file.open");
    FILE *fp = fopen(path_c, mode_c);
    py_file_unpin_current_vthread(vt);
    py_decref(path_owned);
    py_decref(mode_owned);
    if (fp == NULL) return NULL;

    PyFileObject *out = (PyFileObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyFileObject),
        PY_TYPE_FILE,
        0
    );
    if (out == NULL) {
        fclose(fp);
        return NULL;
    }
    out->fp = fp;
    out->closed = 0;
    out->binary = binary;
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
    PyObject *vt = py_file_pin_current_vthread("file.read");
    for (;;) {
        size_t n = fread(tmp, 1, sizeof(tmp), f->fp);
        if (n > 0) {
            if (len + n + 1 > cap) {
                size_t new_cap = cap ? cap : 4096;
                while (new_cap < len + n + 1) new_cap *= 2;
                char *grown = (char *)realloc(buf, new_cap);
                if (grown == NULL) {
                    py_file_unpin_current_vthread(vt);
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
                py_file_unpin_current_vthread(vt);
                free(buf);
                return NULL;
            }
            break;
        }
    }
    py_file_unpin_current_vthread(vt);
    PyObject *out = file_bytes_or_str(f, buf ? buf : "", (int64_t)len);
    free(buf);
    return out;
}

PyObject *py_file_read(PyObject *file, int64_t limit) {
    if (limit < 0) return py_file_read_all(file);
    if (file == NULL || py_type_of(file) != PY_TYPE_FILE) return NULL;
    PyFileObject *f = (PyFileObject *)file;
    if (f->closed || f->fp == NULL) return NULL;

    size_t cap = (size_t)limit;
    char *buf = NULL;
    if (cap > 0) {
        buf = (char *)malloc(cap);
        if (buf == NULL) return NULL;
    }
    size_t n = 0;
    if (cap > 0) {
        PyObject *vt = py_file_pin_current_vthread("file.read");
        n = fread(buf, 1, cap, f->fp);
        py_file_unpin_current_vthread(vt);
        if (n < cap && ferror(f->fp)) {
            free(buf);
            return NULL;
        }
    }
    PyObject *out = file_bytes_or_str(f, buf ? buf : "", (int64_t)n);
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
        PyObject *vt = py_file_pin_current_vthread("file.write");
        wrote = fwrite(data, 1, (size_t)n, f->fp);
        py_file_unpin_current_vthread(vt);
    }
    py_decref(owned);
    return py_int_from_i64((int64_t)wrote);
}

void py_file_close(PyObject *file) {
    if (file == NULL || py_type_of(file) != PY_TYPE_FILE) return;
    PyFileObject *f = (PyFileObject *)file;
    if (!f->closed && f->fp != NULL) {
        PyObject *vt = py_file_pin_current_vthread("file.close");
        fclose(f->fp);
        py_file_unpin_current_vthread(vt);
        f->fp = NULL;
        f->closed = 1;
    }
}

#define FILEINPUT_FILES 0
#define FILEINPUT_OPENHOOK 1
#define FILEINPUT_FILE_INDEX 2
#define FILEINPUT_LINES 3
#define FILEINPUT_LINE_INDEX 4
#define FILEINPUT_TOTAL_LINENO 5
#define FILEINPUT_FILENAME 6

static PyObject *fileinput_state_get(PyObject *state, int64_t index) {
    return py_list_get(state, index);
}

static int64_t fileinput_state_get_i64(PyObject *state, int64_t index) {
    PyObject *item = py_list_get(state, index);
    int64_t out = 0;
    if (item != NULL && item != py_None) {
        out = py_int_value_i64(item);
    }
    py_decref(item);
    return out;
}

static void fileinput_state_set_i64(PyObject *state, int64_t index, int64_t value) {
    PyObject *obj = py_int_from_i64(value);
    py_list_set(state, index, obj);
    py_decref(obj);
}

static int64_t fileinput_files_len(PyObject *files) {
    if (files == NULL) return 0;
    int32_t tag = py_type_of(files);
    if (tag == PY_TYPE_STR) return 1;
    if (tag == PY_TYPE_LIST) return py_list_len(files);
    if (tag == PY_TYPE_TUPLE) return py_tuple_len(files);
    return 0;
}

static PyObject *fileinput_files_get(PyObject *files, int64_t index) {
    if (files == NULL) return NULL;
    int32_t tag = py_type_of(files);
    if (tag == PY_TYPE_STR) {
        if (index != 0) return NULL;
        py_incref(files);
        return files;
    }
    if (tag == PY_TYPE_LIST) return py_list_get(files, index);
    if (tag == PY_TYPE_TUPLE) return py_tuple_get(files, index);
    return NULL;
}

static PyObject *fileinput_open_text(PyObject *filename) {
    PyObject *mode = py_str_new("r", 1);
    PyObject *file = py_file_open(filename, mode);
    py_decref(mode);
    return file;
}

static int fileinput_open_next(PyObject *state) {
    PyObject *files = fileinput_state_get(state, FILEINPUT_FILES);
    int64_t idx = fileinput_state_get_i64(state, FILEINPUT_FILE_INDEX);
    int64_t nfiles = fileinput_files_len(files);
    while (idx < nfiles) {
        PyObject *filename = fileinput_files_get(files, idx);
        fileinput_state_set_i64(state, FILEINPUT_FILE_INDEX, idx + 1);
        idx++;
        if (filename == NULL) {
            continue;
        }
        py_list_set(state, FILEINPUT_FILENAME, filename);
        PyObject *file = fileinput_open_text(filename);
        py_decref(filename);
        if (file == NULL) {
            py_decref(files);
            return 0;
        }
        PyObject *text = py_file_read_all(file);
        py_file_close(file);
        py_decref(file);
        if (text == NULL) {
            py_decref(files);
            return 0;
        }
        PyObject *lines = py_str_splitlines_keepends(text, 1);
        py_decref(text);
        if (lines == NULL) {
            py_decref(files);
            return 0;
        }
        py_list_set(state, FILEINPUT_LINES, lines);
        fileinput_state_set_i64(state, FILEINPUT_LINE_INDEX, 0);
        if (py_list_len(lines) > 0) {
            py_decref(lines);
            py_decref(files);
            return 1;
        }
        py_decref(lines);
    }
    py_decref(files);
    return 0;
}

PyObject *py_fileinput_new(PyObject *files, PyObject *openhook) {
    PyObject *state = py_list_new(7);
    if (state == NULL) return NULL;
    PyObject *zero = py_int_from_i64(0);
    PyObject *empty = py_list_new(0);
    py_list_append(state, files == NULL ? py_None : files);
    py_list_append(state, openhook == NULL ? py_None : openhook);
    py_list_append(state, zero);
    py_list_append(state, empty);
    py_list_append(state, zero);
    py_list_append(state, zero);
    py_list_append(state, py_None);
    py_decref(zero);
    py_decref(empty);
    return state;
}

PyObject *py_fileinput_readline(PyObject *state) {
    if (state == NULL) return py_str_new("", 0);
    for (;;) {
        PyObject *lines = fileinput_state_get(state, FILEINPUT_LINES);
        int64_t line_idx = fileinput_state_get_i64(state, FILEINPUT_LINE_INDEX);
        int64_t nlines = py_list_len(lines);
        if (line_idx < nlines) {
            PyObject *line = py_list_get(lines, line_idx);
            py_decref(lines);
            fileinput_state_set_i64(state, FILEINPUT_LINE_INDEX, line_idx + 1);
            fileinput_state_set_i64(
                state,
                FILEINPUT_TOTAL_LINENO,
                fileinput_state_get_i64(state, FILEINPUT_TOTAL_LINENO) + 1
            );
            return line;
        }
        py_decref(lines);
        if (!fileinput_open_next(state)) {
            return py_str_new("", 0);
        }
    }
}

PyObject *py_fileinput_filename(PyObject *state) {
    PyObject *filename = fileinput_state_get(state, FILEINPUT_FILENAME);
    if (filename == NULL) {
        py_incref(py_None);
        return py_None;
    }
    return filename;
}

PyObject *py_fileinput_lineno(PyObject *state) {
    return py_int_from_i64(fileinput_state_get_i64(state, FILEINPUT_TOTAL_LINENO));
}

PyObject *py_fileinput_filelineno(PyObject *state) {
    return py_int_from_i64(fileinput_state_get_i64(state, FILEINPUT_LINE_INDEX));
}

PyObject *py_fileinput_isfirstline(PyObject *state) {
    return py_bool_from_bit(fileinput_state_get_i64(state, FILEINPUT_LINE_INDEX) == 1);
}

PyObject *py_fileinput_close(PyObject *state) {
    (void)state;
    py_incref(py_None);
    return py_None;
}
