#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

static int64_t as_index(PyObject *k) {
    if (k == NULL || py_type_of(k) != PY_TYPE_INT) return -1;
    return py_int_value_i64(k);
}

PyObject *py_bytes_new(const char *data, int64_t byte_len) {
    if (byte_len < 0) byte_len = 0;
    size_t total = sizeof(PyBytesObject) + (size_t)byte_len + 1;
    PyBytesObject *b = (PyBytesObject *)pcc_gc_alloc(
        (int64_t)total, PY_TYPE_BYTES, 0);
    if (b == NULL) return NULL;
    b->byte_len = byte_len;
    if (byte_len > 0 && data != NULL) {
        memcpy(b->data, data, (size_t)byte_len);
    }
    b->data[byte_len] = '\0';
    return (PyObject *)b;
}

static PyObject *bytearray_new_raw(const char *data, int64_t byte_len) {
    if (byte_len < 0) byte_len = 0;
    size_t total = sizeof(PyByteArrayObject) + (size_t)byte_len + 1;
    PyByteArrayObject *b = (PyByteArrayObject *)pcc_gc_alloc(
        (int64_t)total, PY_TYPE_BYTEARRAY, 0);
    if (b == NULL) return NULL;
    b->byte_len = byte_len;
    if (byte_len > 0 && data != NULL) {
        memcpy(b->data, data, (size_t)byte_len);
    }
    b->data[byte_len] = '\0';
    return (PyObject *)b;
}

static const char *bytes_data(PyObject *o, int64_t *n) {
    if (o == NULL) {
        *n = 0;
        return NULL;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *b = (PyByteArrayObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *m = (PyMemoryViewObject *)o;
        PyObject *base = pcc_gc_load_ptr(o, &m->base);
        return bytes_data(base, n);
    }
    *n = 0;
    return NULL;
}

static int byte_from_obj(PyObject *o, int64_t *out) {
    if (o == NULL) {
        return -1;
    }
    if (PY_IS_TAGGED_INT(o)) {
        *out = py_untag_int(o);
        return 0;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BOOL) {
        if (o == py_True) {
            *out = 1;
            return 0;
        }
        if (o == py_False) {
            *out = 0;
            return 0;
        }
        return -1;
    }
    if (tag == PY_TYPE_INT) {
        int overflow = 0;
        *out = py_int_to_i64(o, &overflow);
        return overflow ? -1 : 0;
    }
    return -1;
}

static PyObject *bytes_from_int_sequence(PyObject *o, int as_bytearray) {
    int64_t n = 0;
    char *tmp = NULL;
    PyObject *out = NULL;
    int is_list = py_type_of(o) == PY_TYPE_LIST;
    int is_tuple = py_type_of(o) == PY_TYPE_TUPLE;

    if (is_list) {
        n = py_list_len(o);
    } else if (is_tuple) {
        n = py_tuple_len(o);
    } else {
        return NULL;
    }

    if (n <= 0) {
        return as_bytearray ? bytearray_new_raw(NULL, 0) : py_bytes_new(NULL, 0);
    }
    tmp = (char *)malloc((size_t)n);
    if (tmp == NULL) {
        return NULL;
    }
    for (int64_t i = 0; i < n; i++) {
        PyObject *item = is_list ? py_list_get(o, i) : py_tuple_get(o, i);
        if (item == NULL) {
            free(tmp);
            return NULL;
        }
        int64_t byte = 0;
        if (byte_from_obj(item, &byte) != 0 || byte < 0 || byte > 255) {
            py_decref(item);
            free(tmp);
            return NULL;
        }
        tmp[i] = (char)(unsigned char)byte;
        py_decref(item);
    }
    out = as_bytearray ? bytearray_new_raw(tmp, n) : py_bytes_new(tmp, n);
    free(tmp);
    return out;
}

static int bytes_is_none_or_null(PyObject *o) {
    return o == NULL || o == py_None;
}

static int bytes_normalize_slice(PyObject *lo, PyObject *hi, PyObject *step,
                                 int64_t len, int64_t *lo_out,
                                 int64_t *hi_out, int64_t *step_out) {
    int64_t step_v = 1;
    if (!bytes_is_none_or_null(step)) {
        step_v = py_int_value_i64(step);
        if (step_v == 0) return -1;
    }

    int64_t lo_v, hi_v;
    if (step_v > 0) {
        lo_v = bytes_is_none_or_null(lo) ? 0   : py_int_value_i64(lo);
        hi_v = bytes_is_none_or_null(hi) ? len : py_int_value_i64(hi);
    } else {
        lo_v = bytes_is_none_or_null(lo) ? len - 1 : py_int_value_i64(lo);
        hi_v = bytes_is_none_or_null(hi) ? -1      : py_int_value_i64(hi);
    }

    if (step_v > 0) {
        if (lo_v < 0) {
            lo_v += len;
            if (lo_v < 0) lo_v = 0;
        }
        if (lo_v > len) lo_v = len;
        if (hi_v < 0) {
            hi_v += len;
            if (hi_v < 0) hi_v = 0;
        }
        if (hi_v > len) hi_v = len;
    } else {
        if (lo_v < 0) {
            lo_v += len;
            if (lo_v < 0) lo_v = -1;
        }
        if (lo_v >= len) lo_v = len - 1;
        if (hi_v < 0) {
            if (bytes_is_none_or_null(hi)) {
                hi_v = -1;
            } else {
                hi_v += len;
                if (hi_v < 0) hi_v = -1;
            }
        }
        if (hi_v >= len) hi_v = len - 1;
    }

    *lo_out = lo_v;
    *hi_out = hi_v;
    *step_out = step_v;
    return 0;
}

static int64_t bytes_slice_count(int64_t lo, int64_t hi, int64_t step) {
    int64_t count = 0;
    if (step > 0) {
        for (int64_t i = lo; i < hi; i += step) count++;
    } else {
        for (int64_t i = lo; i > hi; i += step) count++;
    }
    return count;
}

static PyObject *bytes_new_same_family(PyObject *src, const char *data, int64_t n) {
    if (src != NULL && py_type_of(src) == PY_TYPE_BYTEARRAY) {
        return bytearray_new_raw(data, n);
    }
    return py_bytes_new(data, n);
}

static char *bytes_mutable_data(PyObject *o, int64_t *n) {
    if (o == NULL) {
        *n = 0;
        return NULL;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *b = (PyByteArrayObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    *n = 0;
    return NULL;
}

static int bytes_concat_operand(PyObject *o) {
    int32_t tag = py_type_of(o);
    return tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY;
}

PyObject *py_bytearray_from_obj(PyObject *o) {
    if (o == NULL) {
        return bytearray_new_raw(NULL, 0);
    }
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_LIST || tag == PY_TYPE_TUPLE) {
        return bytes_from_int_sequence(o, 1);
    }
    return bytearray_new_raw(data, n);
}

PyObject *py_bytes_from_obj(PyObject *o) {
    if (o == NULL) {
        return py_bytes_new(NULL, 0);
    }
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_LIST || tag == PY_TYPE_TUPLE) {
        return bytes_from_int_sequence(o, 0);
    }
    return py_bytes_new(data, n);
}

PyObject *py_str_utf8_encode(PyObject *s) {
    const char *in = py_str_utf8(s);
    int64_t n = py_str_byte_len(s);
    if (in == NULL || n <= 0) {
        return py_bytes_new(NULL, 0);
    }
    return py_bytes_new(in, n);
}

PyObject *py_str_latin1_encode(PyObject *s) {
    const unsigned char *in = (const unsigned char *)py_str_utf8(s);
    int64_t n = py_str_byte_len(s);
    if (in == NULL || n <= 0) {
        return py_bytes_new(NULL, 0);
    }

    unsigned char *out = (unsigned char *)malloc((size_t)n);
    if (out == NULL) return NULL;
    int64_t i = 0;
    int64_t j = 0;
    while (i < n) {
        uint32_t cp;
        unsigned char c = in[i];
        if (c < 0x80u) {
            cp = c;
            i += 1;
        } else if ((c & 0xE0u) == 0xC0u && i + 1 < n) {
            cp = ((uint32_t)(c & 0x1Fu) << 6)
                | (uint32_t)(in[i + 1] & 0x3Fu);
            i += 2;
        } else if ((c & 0xF0u) == 0xE0u && i + 2 < n) {
            cp = ((uint32_t)(c & 0x0Fu) << 12)
                | ((uint32_t)(in[i + 1] & 0x3Fu) << 6)
                | (uint32_t)(in[i + 2] & 0x3Fu);
            i += 3;
        } else if ((c & 0xF8u) == 0xF0u && i + 3 < n) {
            cp = ((uint32_t)(c & 0x07u) << 18)
                | ((uint32_t)(in[i + 1] & 0x3Fu) << 12)
                | ((uint32_t)(in[i + 2] & 0x3Fu) << 6)
                | (uint32_t)(in[i + 3] & 0x3Fu);
            i += 4;
        } else {
            free(out);
            return NULL;
        }
        if (cp > 255u) {
            free(out);
            return NULL;
        }
        out[j++] = (unsigned char)cp;
    }
    PyObject *bytes = py_bytes_new((const char *)out, j);
    free(out);
    return bytes;
}

PyObject *py_memoryview_new(PyObject *o) {
    PyMemoryViewObject *m = (PyMemoryViewObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyMemoryViewObject), PY_TYPE_MEMORYVIEW, 0);
    if (m == NULL) return NULL;
    m->base = NULL;
    pcc_gc_store_ptr((PyObject *)m, &m->base, o);
    return (PyObject *)m;
}

void py_dealloc_memoryview(PyObject *o) {
    if (o == NULL) return;
    PyMemoryViewObject *m = (PyMemoryViewObject *)o;
    PyObject *base = pcc_gc_load_ptr(o, &m->base);
    if (base != NULL) py_decref(base);
    pcc_gc_free_object_memory(o);
}

PyObject *py_bytes_decode(PyObject *o) {
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    return py_str_new(data, n);
}

/* bytes.hex(): lowercase two-hex-digits-per-byte string (no separator). */
PyObject *py_bytes_hex(PyObject *o) {
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    if (data == NULL) return py_str_new("", 0);
    char *buf = (char *)malloc((size_t)(n * 2 + 1));
    if (buf == NULL) return NULL;
    static const char hx[] = "0123456789abcdef";
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)data[i];
        buf[i * 2] = hx[(c >> 4) & 0xF];
        buf[i * 2 + 1] = hx[c & 0xF];
    }
    PyObject *out = py_str_new(buf, n * 2);
    free(buf);
    return out;
}

PyObject *py_bytes_getitem(PyObject *o, PyObject *k) {
    int64_t i = as_index(k);
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    if (data == NULL || i < 0 || i >= n) return NULL;
    return py_int_from_i64((unsigned char)data[i]);
}

PyObject *py_bytes_slice(PyObject *o, PyObject *lo, PyObject *hi, PyObject *step) {
    int64_t n = 0;
    const char *data = bytes_data(o, &n);
    if (data == NULL) return NULL;

    int64_t lo_v, hi_v, step_v;
    if (bytes_normalize_slice(lo, hi, step, n, &lo_v, &hi_v, &step_v) != 0) {
        return NULL;
    }
    int64_t count = bytes_slice_count(lo_v, hi_v, step_v);
    if (count <= 0) {
        return bytes_new_same_family(o, NULL, 0);
    }
    if (step_v == 1) {
        return bytes_new_same_family(o, data + lo_v, count);
    }

    char *tmp = (char *)malloc((size_t)count);
    if (tmp == NULL) return NULL;
    int64_t j = 0;
    if (step_v > 0) {
        for (int64_t i = lo_v; i < hi_v; i += step_v) {
            tmp[j++] = data[i];
        }
    } else {
        for (int64_t i = lo_v; i > hi_v; i += step_v) {
            if (i < 0 || i >= n) break;
            tmp[j++] = data[i];
        }
    }
    PyObject *out = bytes_new_same_family(o, tmp, j);
    free(tmp);
    return out;
}

PyObject *py_bytes_concat(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return NULL;
    if (!bytes_concat_operand(a) || !bytes_concat_operand(b)) return NULL;

    int64_t an = 0;
    int64_t bn = 0;
    const char *ad = bytes_data(a, &an);
    const char *bd = bytes_data(b, &bn);
    if (ad == NULL || bd == NULL || an < 0 || bn < 0) return NULL;
    if (an > INT64_MAX - bn) return NULL;

    int64_t total = an + bn;
    PyObject *out = bytes_new_same_family(a, NULL, total);
    if (out == NULL) return NULL;
    int64_t out_n = 0;
    char *dst = bytes_mutable_data(out, &out_n);
    if (dst == NULL || out_n != total) return NULL;
    if (an > 0) memcpy(dst, ad, (size_t)an);
    if (bn > 0) memcpy(dst + an, bd, (size_t)bn);
    dst[total] = '\0';
    return out;
}

PyObject *py_bytes_repeat(PyObject *src, int64_t count) {
    if (src == NULL) return NULL;
    int32_t tag = py_type_of(src);
    if (tag != PY_TYPE_BYTES && tag != PY_TYPE_BYTEARRAY) return NULL;
    int64_t n = 0;
    const char *data = bytes_data(src, &n);
    if (data == NULL || n < 0) return NULL;
    if (count <= 0 || n == 0) {
        return bytes_new_same_family(src, NULL, 0);
    }
    if (count > INT64_MAX / n) return NULL;
    int64_t total = count * n;
    PyObject *out = bytes_new_same_family(src, NULL, total);
    if (out == NULL) return NULL;
    int64_t out_n = 0;
    char *dst = bytes_mutable_data(out, &out_n);
    if (dst == NULL || out_n != total) return NULL;
    for (int64_t i = 0; i < count; i++) {
        memcpy(dst + i * n, data, (size_t)n);
    }
    dst[total] = '\0';
    return out;
}

int64_t py_bytes_len(PyObject *o) {
    int64_t n = 0;
    (void)bytes_data(o, &n);
    return n;
}

int64_t py_bytearray_setitem(PyObject *o, PyObject *k, PyObject *v) {
    if (py_type_of(o) != PY_TYPE_BYTEARRAY) return -1;
    int64_t i = as_index(k);
    int64_t byte = py_int_value_i64(v);
    PyByteArrayObject *b = (PyByteArrayObject *)o;
    if (i < 0 || i >= b->byte_len || byte < 0 || byte > 255) return -1;
    b->data[i] = (char)(unsigned char)byte;
    return 0;
}
