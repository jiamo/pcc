#include "py_internal.h"

#include <string.h>

/* `int.to_bytes(length, byteorder)` / `int.from_bytes(bytes, byteorder)`
 * for the strict no-libpython subset. Unsigned-only (CPython's default
 * signed=False); negative values raise OverflowError exactly like
 * CPython, and the signed= keyword is NOT lowered (the frontend falls
 * through, so strict mode rejects it honestly instead of mis-computing).
 * Full bignum magnitude support via the canonical base-2^32 limbs.
 *
 * C-only helper (no pcc-Python mirror): linked into both runtime
 * archives via OBJ_PY_CC_HELPERS. */

static int py_int_bytes_order_is_big(PyObject *byteorder) {
    const char *s = py_str_utf8(byteorder);
    if (s == NULL) return -1;
    if (strcmp(s, "big") == 0) return 1;
    if (strcmp(s, "little") == 0) return 0;
    return -1;
}

static PyObject *py_int_bytes_like_base(PyObject *obj) {
    while (obj != NULL && !PY_IS_TAGGED_INT(obj)) {
        int32_t tag = py_type_of(obj);
        if (tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY) return obj;
        if (tag != PY_TYPE_MEMORYVIEW) return NULL;
        PyMemoryViewObject *view = (PyMemoryViewObject *)obj;
        obj = pcc_gc_load_ptr(obj, &view->base);
    }
    return NULL;
}

PyObject *py_int_to_bytes(PyObject *v, int64_t length, PyObject *byteorder) {
    int big = py_int_bytes_order_is_big(byteorder);
    if (big < 0) {
        py_raise_owned(py_exc_new(
            PY_EXC_VALUEERROR, "byteorder must be either 'little' or 'big'"
        ));
        return NULL;
    }
    if (length < 0) {
        py_raise_owned(py_exc_new(
            PY_EXC_VALUEERROR, "length argument must be non-negative"
        ));
        return NULL;
    }
    /* Magnitude limbs, little-endian base 2^32. */
    uint32_t small[2];
    const uint32_t *digits = NULL;
    int32_t ndigits = 0;
    if (PY_IS_TAGGED_INT(v)) {
        int64_t raw = py_int_value_i64(v);
        if (raw < 0) {
            py_raise_owned(py_exc_new(
                PY_EXC_OVERFLOWERROR,
                "can't convert negative int to unsigned"
            ));
            return NULL;
        }
        small[0] = (uint32_t)((uint64_t)raw & 0xffffffffu);
        small[1] = (uint32_t)((uint64_t)raw >> 32);
        digits = small;
        ndigits = small[1] != 0 ? 2 : (small[0] != 0 ? 1 : 0);
    } else if (py_type_of(v) == PY_TYPE_INT) {
        PyIntObject *b = (PyIntObject *)v;
        if (b->sign < 0) {
            py_raise_owned(py_exc_new(
                PY_EXC_OVERFLOWERROR,
                "can't convert negative int to unsigned"
            ));
            return NULL;
        }
        digits = b->digits;
        ndigits = b->ndigits;
    } else {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "to_bytes expects an int"));
        return NULL;
    }
    /* Bytes needed for the magnitude (0 needs 0 bytes). */
    int64_t needed = 0;
    if (ndigits > 0) {
        uint32_t top = digits[ndigits - 1];
        int64_t top_bytes = 4;
        while (top_bytes > 1 && (top >> ((top_bytes - 1) * 8)) == 0) {
            top_bytes--;
        }
        needed = (int64_t)(ndigits - 1) * 4 + top_bytes;
    }
    if (needed > length) {
        py_raise_owned(py_exc_new(PY_EXC_OVERFLOWERROR, "int too big to convert"));
        return NULL;
    }
    PyObject *out = py_bytes_new(NULL, length);
    if (out == NULL) return NULL;
    char *data = ((PyBytesObject *)out)->data;
    memset(data, 0, (size_t)length);
    /* Fill little-endian, then reverse for big-endian. */
    for (int64_t i = 0; i < needed; i++) {
        uint32_t limb = digits[i / 4];
        data[i] = (char)((limb >> ((i % 4) * 8)) & 0xffu);
    }
    if (big) {
        for (int64_t i = 0, j = length - 1; i < j; i++, j--) {
            char tmp = data[i];
            data[i] = data[j];
            data[j] = tmp;
        }
    }
    return out;
}

PyObject *py_int_from_bytes(PyObject *bytes_obj, PyObject *byteorder) {
    int big = py_int_bytes_order_is_big(byteorder);
    if (big < 0) {
        py_raise_owned(py_exc_new(
            PY_EXC_VALUEERROR, "byteorder must be either 'little' or 'big'"
        ));
        return NULL;
    }
    PyObject *base = py_int_bytes_like_base(bytes_obj);
    if (base == NULL) {
        py_raise_owned(py_exc_new(
            PY_EXC_TYPEERROR, "from_bytes expects a bytes object"
        ));
        return NULL;
    }
    int32_t tag = py_type_of(base);
    int64_t n;
    const char *data;
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)base;
        n = b->byte_len;
        data = b->data;
    } else {
        PyByteArrayObject *b = (PyByteArrayObject *)base;
        n = b->byte_len;
        data = b->data;
    }
    int32_t ndigits = (int32_t)((n + 3) / 4);
    if (ndigits < 1) ndigits = 1;
    PyIntObject *out = py_bigint_alloc(ndigits);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < n; i++) {
        /* index of this byte in little-endian magnitude order */
        int64_t le = big ? (n - 1 - i) : i;
        uint32_t byte = (uint32_t)(unsigned char)data[i];
        out->digits[le / 4] |= byte << ((le % 4) * 8);
    }
    int32_t used = ndigits;
    while (used > 0 && out->digits[used - 1] == 0) used--;
    out->ndigits = used;
    out->sign = used > 0 ? 1 : 0;
    return py_bigint_to_pyobject(out);
}
