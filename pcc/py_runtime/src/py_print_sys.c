/* pcc/py_runtime/src/py_print_sys.c
 *
 * sys.stdout.write / sys.stderr.write helpers, split out of
 * py_print.c so the pcc-Python port (py_print_sys.py) can replace
 * just these two symbols.
 */

#include "py_internal.h"
#include <unistd.h>

static PyObject *py_sys_write_fd(int fd, PyObject *text) {
    PyObject *owned = NULL;
    PyObject *item = text;
    if (item == NULL) item = py_None;
    if (item != NULL && py_type_of(item) != PY_TYPE_STR) {
        owned = py_obj_str(item);
        item = owned;
    }
    if (item == NULL) {
        py_decref(owned);
        return py_int_from_i64(0);
    }
    const char *raw = py_str_utf8(item);
    int64_t n = py_str_byte_len(item);
    int64_t wrote = 0;
    if (raw != NULL && n > 0) {
        ssize_t rc = write(fd, raw, (size_t)n);
        wrote = rc > 0 ? (int64_t)rc : 0;
    }
    py_decref(owned);
    return py_int_from_i64(wrote);
}

PyObject *py_sys_stdout_write(PyObject *text) {
    return py_sys_write_fd(1, text);
}

PyObject *py_sys_stderr_write(PyObject *text) {
    return py_sys_write_fd(2, text);
}
