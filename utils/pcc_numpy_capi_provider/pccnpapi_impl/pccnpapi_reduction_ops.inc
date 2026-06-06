/* compress/diagonal, reductions, scalar-kind, casting, zeros/ones, and dtype helpers.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static PyObject *pccnp_compress(
    PyArrayObject *self_obj,
    PyObject *condition_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Compress does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj) ||
        !pccnp_is_array_object(condition_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Compress expects pcc NumPy arrays");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    PccNpArray *condition = pccnp_array_from_object((PyArrayObject *)condition_obj);
    if (source == NULL || condition == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Compress expects pcc NumPy arrays");
        return NULL;
    }
    if (source->nd < 1 || source->nd > 2 || condition->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Compress supports 1D/2D sources and 1D conditions only");
        return NULL;
    }

    int normalized_axis = axis;
    if (normalized_axis < 0) {
        normalized_axis += source->nd;
    }
    if (normalized_axis < 0 || normalized_axis >= source->nd) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Compress axis is out of range for the reduced provider");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source) || !pccnp_has_c_order_strides(condition)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Compress supports C-order arrays only");
        return NULL;
    }
    npy_intp axis_len = source->dims[normalized_axis];
    if (axis_len < 0 || condition->element_count != (size_t)axis_len) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Compress condition length mismatch");
        return NULL;
    }
    if (source->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(source->typenum) ||
        !PyTypeNum_ISNUMBER(source->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Compress does not support object, complex, or non-numeric source arrays"
        );
        return NULL;
    }
    if (condition->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(condition->typenum) ||
        !PyTypeNum_ISNUMBER(condition->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Compress does not support object, complex, or non-numeric condition arrays"
        );
        return NULL;
    }
    if ((source->element_count != 0 && source->data == NULL) ||
        (condition->element_count != 0 && condition->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *cond_data = (unsigned char *)condition->data;
    size_t count = 0;
    for (size_t i = 0; i < condition->element_count; i++) {
        int truthy = 0;
        if (pccnp_element_is_nonzero(
                condition,
                cond_data + (i * condition->itemsize),
                &truthy
            ) != 0) {
            return NULL;
        }
        if (truthy) {
            count++;
        }
    }

    npy_intp dims[2] = {0, 0};
    if (source->nd == 1) {
        dims[0] = (npy_intp)count;
    } else {
        dims[0] = source->dims[0];
        dims[1] = source->dims[1];
        dims[normalized_axis] = (npy_intp)count;
    }
    PyObject *result = pccnp_array_new_common(source->nd, dims, source->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    unsigned char *src_data = (unsigned char *)source->data;
    unsigned char *out_data = (unsigned char *)out->data;
    if (source->nd == 1) {
        size_t out_pos = 0;
        for (size_t i = 0; i < condition->element_count; i++) {
            int truthy = 0;
            if (pccnp_element_is_nonzero(
                    condition,
                    cond_data + (i * condition->itemsize),
                    &truthy
                ) != 0) {
                Py_DECREF(result);
                return NULL;
            }
            if (truthy) {
                memcpy(
                    out_data + (out_pos * out->itemsize),
                    src_data + (i * source->itemsize),
                    out->itemsize
                );
                out_pos++;
            }
        }
    } else if (normalized_axis == 0) {
        size_t rows = (size_t)source->dims[0];
        size_t cols = (size_t)source->dims[1];
        size_t row_bytes = cols * source->itemsize;
        size_t out_row = 0;
        for (size_t row = 0; row < rows; row++) {
            int truthy = 0;
            if (pccnp_element_is_nonzero(
                    condition,
                    cond_data + (row * condition->itemsize),
                    &truthy
                ) != 0) {
                Py_DECREF(result);
                return NULL;
            }
            if (truthy) {
                memcpy(out_data + (out_row * row_bytes), src_data + (row * row_bytes), row_bytes);
                out_row++;
            }
        }
    } else {
        size_t rows = (size_t)source->dims[0];
        size_t cols = (size_t)source->dims[1];
        size_t out_cols = (size_t)dims[1];
        for (size_t row = 0; row < rows; row++) {
            size_t out_col = 0;
            for (size_t col = 0; col < cols; col++) {
                int truthy = 0;
                if (pccnp_element_is_nonzero(
                        condition,
                        cond_data + (col * condition->itemsize),
                        &truthy
                    ) != 0) {
                    Py_DECREF(result);
                    return NULL;
                }
                if (truthy) {
                    memcpy(
                        out_data + (((row * out_cols) + out_col) * out->itemsize),
                        src_data + (((row * cols) + col) * source->itemsize),
                        out->itemsize
                    );
                    out_col++;
                }
            }
        }
    }
    return result;
}

static PyObject *pccnp_diagonal(PyArrayObject *self_obj, int offset, int axis1, int axis2) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Diagonal expects a pcc NumPy array");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Diagonal expects a pcc NumPy array");
        return NULL;
    }
    if (source->nd != 2) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Diagonal supports 2D arrays only");
        return NULL;
    }
    int normalized_axis1 = axis1;
    int normalized_axis2 = axis2;
    if (normalized_axis1 < 0) {
        normalized_axis1 += source->nd;
    }
    if (normalized_axis2 < 0) {
        normalized_axis2 += source->nd;
    }
    if (normalized_axis1 < 0 || normalized_axis1 >= source->nd ||
        normalized_axis2 < 0 || normalized_axis2 >= source->nd) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Diagonal axis is out of range");
        return NULL;
    }
    if (normalized_axis1 == normalized_axis2) {
        PyErr_SetString(PyExc_ValueError, "axis1 and axis2 cannot be the same");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Diagonal supports C-order arrays only");
        return NULL;
    }
    if (source->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(source->typenum) ||
        !PyTypeNum_ISNUMBER(source->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Diagonal does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (source->element_count != 0 && source->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dim1 = source->dims[normalized_axis1];
    npy_intp dim2 = source->dims[normalized_axis2];
    if (dim1 < 0 || dim2 < 0) {
        PyErr_SetString(PyExc_ValueError, "diagonal dimensions must be non-negative");
        return NULL;
    }
    npy_intp stride1 = source->strides[normalized_axis1];
    npy_intp stride2 = source->strides[normalized_axis2];
    npy_intp off = (npy_intp)offset;
    npy_intp length = 0;
    npy_intp start_offset = 0;
    if (off >= 0) {
        dim2 -= off;
        if (dim2 > 0) {
            start_offset = off * stride2;
        }
    } else {
        off = -off;
        dim1 -= off;
        if (dim1 > 0) {
            start_offset = off * stride1;
        }
    }
    length = dim1 < dim2 ? dim1 : dim2;
    if (length < 0) {
        length = 0;
        start_offset = 0;
    }

    npy_intp dims[1] = {length};
    PyObject *result = pccnp_array_new_common(1, dims, source->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    unsigned char *src_data = (unsigned char *)source->data;
    unsigned char *out_data = (unsigned char *)out->data;
    for (npy_intp i = 0; i < length; i++) {
        npy_intp src_offset = start_offset + (i * (stride1 + stride2));
        memcpy(
            out_data + ((size_t)i * out->itemsize),
            src_data + src_offset,
            out->itemsize
        );
    }
    return result;
}

static PyObject *pccnp_sum_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_SUM_SIGNED(type) \
    do { \
        long long total = 0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long long)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyLong_FromLongLong(total); \
    } while (0)

#define PCCNP_SUM_UNSIGNED(type) \
    do { \
        unsigned long long total = 0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (unsigned long long)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyLong_FromUnsignedLongLong(total); \
    } while (0)

#define PCCNP_SUM_FLOAT(type) \
    do { \
        long double total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyFloat_FromDouble((double)total); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_SUM_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_SUM_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_SUM_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_SUM_SIGNED(short);
        case NPY_USHORT:
            PCCNP_SUM_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_SUM_SIGNED(int);
        case NPY_UINT:
            PCCNP_SUM_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_SUM_SIGNED(long);
        case NPY_ULONG:
            PCCNP_SUM_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_SUM_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_SUM_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_SUM_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_SUM_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_SUM_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_SUM_SIGNED
#undef PCCNP_SUM_UNSIGNED
#undef PCCNP_SUM_FLOAT
}

static PyObject *pccnp_sum_1d_numeric_float_rtype(PccNpArray *array, int rtype) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy reduction supports C-order arrays only");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (!PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy reduction supports only floating rtype conversion"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    long double total = 0.0;
    for (size_t i = 0; i < array->element_count; i++) {
        long double item = 0.0;
        if (pccnp_element_as_long_double(array, i, &item) != 0) {
            return NULL;
        }
        total += item;
    }
    return PyFloat_FromDouble((double)total);
}

static PyObject *pccnp_prod_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy product reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy product reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy product reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_PROD_SIGNED(type) \
    do { \
        long long total = 1; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total *= (long long)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyLong_FromLongLong(total); \
    } while (0)

#define PCCNP_PROD_UNSIGNED(type) \
    do { \
        unsigned long long total = 1; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total *= (unsigned long long)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyLong_FromUnsignedLongLong(total); \
    } while (0)

#define PCCNP_PROD_FLOAT(type) \
    do { \
        long double total = 1.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total *= (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyFloat_FromDouble((double)total); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_PROD_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_PROD_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_PROD_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_PROD_SIGNED(short);
        case NPY_USHORT:
            PCCNP_PROD_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_PROD_SIGNED(int);
        case NPY_UINT:
            PCCNP_PROD_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_PROD_SIGNED(long);
        case NPY_ULONG:
            PCCNP_PROD_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_PROD_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_PROD_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_PROD_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_PROD_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_PROD_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy product reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_PROD_SIGNED
#undef PCCNP_PROD_UNSIGNED
#undef PCCNP_PROD_FLOAT
}

static PyObject *pccnp_prod_1d_numeric_float_rtype(PccNpArray *array, int rtype) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy product reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy product reduction supports C-order arrays only");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy product reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (!PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy product reduction supports only floating rtype conversion"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    long double total = 1.0;
    for (size_t i = 0; i < array->element_count; i++) {
        long double item = 0.0;
        if (pccnp_element_as_long_double(array, i, &item) != 0) {
            return NULL;
        }
        total *= item;
    }
    return PyFloat_FromDouble((double)total);
}

static PyObject *pccnp_max_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy maximum reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy maximum reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy maximum reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(
            PyExc_ValueError,
            "zero-size array to reduction operation maximum which has no identity"
        );
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_MAX_SIGNED(type) \
    do { \
        long long best = (long long)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long long value = (long long)*(type *)(data + (i * array->itemsize)); \
            if (value > best) best = value; \
        } \
        return PyLong_FromLongLong(best); \
    } while (0)

#define PCCNP_MAX_UNSIGNED(type) \
    do { \
        unsigned long long best = (unsigned long long)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            unsigned long long value = (unsigned long long)*(type *)(data + (i * array->itemsize)); \
            if (value > best) best = value; \
        } \
        return PyLong_FromUnsignedLongLong(best); \
    } while (0)

#define PCCNP_MAX_FLOAT(type) \
    do { \
        long double best = (long double)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long double value = (long double)*(type *)(data + (i * array->itemsize)); \
            if (value > best) best = value; \
        } \
        return PyFloat_FromDouble((double)best); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_MAX_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_MAX_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_MAX_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_MAX_SIGNED(short);
        case NPY_USHORT:
            PCCNP_MAX_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_MAX_SIGNED(int);
        case NPY_UINT:
            PCCNP_MAX_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_MAX_SIGNED(long);
        case NPY_ULONG:
            PCCNP_MAX_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_MAX_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_MAX_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_MAX_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_MAX_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_MAX_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy maximum reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_MAX_SIGNED
#undef PCCNP_MAX_UNSIGNED
#undef PCCNP_MAX_FLOAT
}

static PyObject *pccnp_min_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy minimum reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy minimum reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy minimum reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(
            PyExc_ValueError,
            "zero-size array to reduction operation minimum which has no identity"
        );
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_MIN_SIGNED(type) \
    do { \
        long long best = (long long)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long long value = (long long)*(type *)(data + (i * array->itemsize)); \
            if (value < best) best = value; \
        } \
        return PyLong_FromLongLong(best); \
    } while (0)

#define PCCNP_MIN_UNSIGNED(type) \
    do { \
        unsigned long long best = (unsigned long long)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            unsigned long long value = (unsigned long long)*(type *)(data + (i * array->itemsize)); \
            if (value < best) best = value; \
        } \
        return PyLong_FromUnsignedLongLong(best); \
    } while (0)

#define PCCNP_MIN_FLOAT(type) \
    do { \
        long double best = (long double)*(type *)data; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long double value = (long double)*(type *)(data + (i * array->itemsize)); \
            if (value < best) best = value; \
        } \
        return PyFloat_FromDouble((double)best); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_MIN_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_MIN_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_MIN_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_MIN_SIGNED(short);
        case NPY_USHORT:
            PCCNP_MIN_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_MIN_SIGNED(int);
        case NPY_UINT:
            PCCNP_MIN_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_MIN_SIGNED(long);
        case NPY_ULONG:
            PCCNP_MIN_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_MIN_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_MIN_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_MIN_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_MIN_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_MIN_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy minimum reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_MIN_SIGNED
#undef PCCNP_MIN_UNSIGNED
#undef PCCNP_MIN_FLOAT
}

static PyObject *pccnp_ptp_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy ptp reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy ptp reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy ptp reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(
            PyExc_ValueError,
            "zero-size array to reduction operation peak-to-peak which has no identity"
        );
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_PTP_SIGNED(type) \
    do { \
        long long low = (long long)*(type *)data; \
        long long high = low; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long long value = (long long)*(type *)(data + (i * array->itemsize)); \
            if (value < low) low = value; \
            if (value > high) high = value; \
        } \
        return PyLong_FromLongLong(high - low); \
    } while (0)

#define PCCNP_PTP_UNSIGNED(type) \
    do { \
        unsigned long long low = (unsigned long long)*(type *)data; \
        unsigned long long high = low; \
        for (size_t i = 1; i < array->element_count; i++) { \
            unsigned long long value = (unsigned long long)*(type *)(data + (i * array->itemsize)); \
            if (value < low) low = value; \
            if (value > high) high = value; \
        } \
        return PyLong_FromUnsignedLongLong(high - low); \
    } while (0)

#define PCCNP_PTP_FLOAT(type) \
    do { \
        long double low = (long double)*(type *)data; \
        long double high = low; \
        for (size_t i = 1; i < array->element_count; i++) { \
            long double value = (long double)*(type *)(data + (i * array->itemsize)); \
            if (value < low) low = value; \
            if (value > high) high = value; \
        } \
        return PyFloat_FromDouble((double)(high - low)); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_PTP_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_PTP_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_PTP_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_PTP_SIGNED(short);
        case NPY_USHORT:
            PCCNP_PTP_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_PTP_SIGNED(int);
        case NPY_UINT:
            PCCNP_PTP_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_PTP_SIGNED(long);
        case NPY_ULONG:
            PCCNP_PTP_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_PTP_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_PTP_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_PTP_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_PTP_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_PTP_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy ptp reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_PTP_SIGNED
#undef PCCNP_PTP_UNSIGNED
#undef PCCNP_PTP_FLOAT
}

static PyObject *pccnp_mean_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy mean reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy mean reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy mean reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "mean of empty pcc NumPy array is not supported");
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_MEAN_SIGNED(type) \
    do { \
        long double total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyFloat_FromDouble((double)(total / (long double)array->element_count)); \
    } while (0)

#define PCCNP_MEAN_UNSIGNED(type) \
    do { \
        long double total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyFloat_FromDouble((double)(total / (long double)array->element_count)); \
    } while (0)

#define PCCNP_MEAN_FLOAT(type) \
    do { \
        long double total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        return PyFloat_FromDouble((double)(total / (long double)array->element_count)); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_MEAN_UNSIGNED(unsigned char);
        case NPY_BYTE:
            PCCNP_MEAN_SIGNED(signed char);
        case NPY_UBYTE:
            PCCNP_MEAN_UNSIGNED(unsigned char);
        case NPY_SHORT:
            PCCNP_MEAN_SIGNED(short);
        case NPY_USHORT:
            PCCNP_MEAN_UNSIGNED(unsigned short);
        case NPY_INT:
            PCCNP_MEAN_SIGNED(int);
        case NPY_UINT:
            PCCNP_MEAN_UNSIGNED(unsigned int);
        case NPY_LONG:
            PCCNP_MEAN_SIGNED(long);
        case NPY_ULONG:
            PCCNP_MEAN_UNSIGNED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_MEAN_SIGNED(long long);
        case NPY_ULONGLONG:
            PCCNP_MEAN_UNSIGNED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_MEAN_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_MEAN_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_MEAN_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy mean reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_MEAN_SIGNED
#undef PCCNP_MEAN_UNSIGNED
#undef PCCNP_MEAN_FLOAT
}

static double pccnp_sqrt_nonnegative_double(double value) {
    if (value <= 0.0) {
        return 0.0;
    }
    double guess = value >= 1.0 ? value : 1.0;
    for (int i = 0; i < 32; i++) {
        guess = 0.5 * (guess + value / guess);
    }
    return guess;
}

static PyObject *pccnp_std_1d_numeric(PccNpArray *array, int variance) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy std reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy std reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy std reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "std of empty pcc NumPy array is not supported");
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_STD_TYPED(type) \
    do { \
        long double total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total += (long double)*(type *)(data + (i * array->itemsize)); \
        } \
        long double mean = total / (long double)array->element_count; \
        long double sq_total = 0.0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            long double value = (long double)*(type *)(data + (i * array->itemsize)); \
            long double delta = value - mean; \
            sq_total += delta * delta; \
        } \
        long double var_value = sq_total / (long double)array->element_count; \
        double out_value = variance ? (double)var_value : pccnp_sqrt_nonnegative_double((double)var_value); \
        return PyFloat_FromDouble(out_value); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_STD_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_STD_TYPED(signed char);
        case NPY_UBYTE:
            PCCNP_STD_TYPED(unsigned char);
        case NPY_SHORT:
            PCCNP_STD_TYPED(short);
        case NPY_USHORT:
            PCCNP_STD_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_STD_TYPED(int);
        case NPY_UINT:
            PCCNP_STD_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_STD_TYPED(long);
        case NPY_ULONG:
            PCCNP_STD_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_STD_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_STD_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_STD_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_STD_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_STD_TYPED(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy std reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_STD_TYPED
}

static PyObject *pccnp_cumsum_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumsum supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumsum supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumsum supports real numeric arrays only"
        );
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "cumsum result length exceeds C long range");
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, array->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL || (out->element_count != 0 && out->data == NULL)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_CumSum expected a pcc result array");
        return NULL;
    }

    unsigned char *in_data = (unsigned char *)array->data;
    unsigned char *out_data = (unsigned char *)out->data;

#define PCCNP_CUMSUM_TYPED(type) \
    do { \
        type total = (type)0; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total = (type)(total + *(type *)(in_data + (i * array->itemsize))); \
            *(type *)(out_data + (i * out->itemsize)) = total; \
        } \
        return result; \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_CUMSUM_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_CUMSUM_TYPED(signed char);
        case NPY_UBYTE:
            PCCNP_CUMSUM_TYPED(unsigned char);
        case NPY_SHORT:
            PCCNP_CUMSUM_TYPED(short);
        case NPY_USHORT:
            PCCNP_CUMSUM_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_CUMSUM_TYPED(int);
        case NPY_UINT:
            PCCNP_CUMSUM_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_CUMSUM_TYPED(long);
        case NPY_ULONG:
            PCCNP_CUMSUM_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_CUMSUM_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_CUMSUM_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_CUMSUM_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_CUMSUM_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_CUMSUM_TYPED(long double);
        default:
            Py_DECREF(result);
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy cumsum supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_CUMSUM_TYPED
}

static PyObject *pccnp_cumsum_1d_numeric_float_rtype(PccNpArray *array, int rtype) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumsum supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumsum supports C-order arrays only");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumsum supports real numeric arrays only"
        );
        return NULL;
    }
    if (!PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumsum supports only floating rtype conversion"
        );
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "cumsum result length exceeds C long range");
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, rtype, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL || (out->element_count != 0 && out->data == NULL)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_CumSum expected a pcc result array");
        return NULL;
    }

    long double total = 0.0;
    for (size_t i = 0; i < array->element_count; i++) {
        long double item = 0.0;
        if (pccnp_element_as_long_double(array, i, &item) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        total += item;
        if (pccnp_store_long_double(out, i, total) != 0) {
            Py_DECREF(result);
            return NULL;
        }
    }
    return result;
}

static PyObject *pccnp_cumprod_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumprod supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumprod supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumprod supports real numeric arrays only"
        );
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "cumprod result length exceeds C long range");
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, array->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL || (out->element_count != 0 && out->data == NULL)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_CumProd expected a pcc result array");
        return NULL;
    }

    unsigned char *in_data = (unsigned char *)array->data;
    unsigned char *out_data = (unsigned char *)out->data;

#define PCCNP_CUMPROD_TYPED(type) \
    do { \
        type total = (type)1; \
        for (size_t i = 0; i < array->element_count; i++) { \
            total = (type)(total * *(type *)(in_data + (i * array->itemsize))); \
            *(type *)(out_data + (i * out->itemsize)) = total; \
        } \
        return result; \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_CUMPROD_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_CUMPROD_TYPED(signed char);
        case NPY_UBYTE:
            PCCNP_CUMPROD_TYPED(unsigned char);
        case NPY_SHORT:
            PCCNP_CUMPROD_TYPED(short);
        case NPY_USHORT:
            PCCNP_CUMPROD_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_CUMPROD_TYPED(int);
        case NPY_UINT:
            PCCNP_CUMPROD_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_CUMPROD_TYPED(long);
        case NPY_ULONG:
            PCCNP_CUMPROD_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_CUMPROD_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_CUMPROD_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_CUMPROD_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_CUMPROD_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_CUMPROD_TYPED(long double);
        default:
            Py_DECREF(result);
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy cumprod supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_CUMPROD_TYPED
}

static PyObject *pccnp_cumprod_1d_numeric_float_rtype(PccNpArray *array, int rtype) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumprod supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy cumprod supports C-order arrays only");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumprod supports real numeric arrays only"
        );
        return NULL;
    }
    if (!PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy cumprod supports only floating rtype conversion"
        );
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "cumprod result length exceeds C long range");
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, rtype, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL || (out->element_count != 0 && out->data == NULL)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_CumProd expected a pcc result array");
        return NULL;
    }

    long double total = 1.0;
    for (size_t i = 0; i < array->element_count; i++) {
        long double item = 0.0;
        if (pccnp_element_as_long_double(array, i, &item) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        total *= item;
        if (pccnp_store_long_double(out, i, total) != 0) {
            Py_DECREF(result);
            return NULL;
        }
    }
    return result;
}

static PyObject *pccnp_any_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy any reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy any reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        (array->typenum != NPY_BOOL && !PyTypeNum_ISNUMBER(array->typenum))) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy any reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (array->element_count == 0) {
        return PyBool_FromLong(0);
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_ANY_INT(type) \
    do { \
        for (size_t i = 0; i < array->element_count; i++) { \
            if (*(type *)(data + (i * array->itemsize)) != 0) { \
                return PyBool_FromLong(1); \
            } \
        } \
        return PyBool_FromLong(0); \
    } while (0)

#define PCCNP_ANY_FLOAT(type) \
    do { \
        for (size_t i = 0; i < array->element_count; i++) { \
            if (*(type *)(data + (i * array->itemsize)) != (type)0) { \
                return PyBool_FromLong(1); \
            } \
        } \
        return PyBool_FromLong(0); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_ANY_INT(unsigned char);
        case NPY_BYTE:
            PCCNP_ANY_INT(signed char);
        case NPY_UBYTE:
            PCCNP_ANY_INT(unsigned char);
        case NPY_SHORT:
            PCCNP_ANY_INT(short);
        case NPY_USHORT:
            PCCNP_ANY_INT(unsigned short);
        case NPY_INT:
            PCCNP_ANY_INT(int);
        case NPY_UINT:
            PCCNP_ANY_INT(unsigned int);
        case NPY_LONG:
            PCCNP_ANY_INT(long);
        case NPY_ULONG:
            PCCNP_ANY_INT(unsigned long);
        case NPY_LONGLONG:
            PCCNP_ANY_INT(long long);
        case NPY_ULONGLONG:
            PCCNP_ANY_INT(unsigned long long);
        case NPY_FLOAT:
            PCCNP_ANY_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_ANY_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_ANY_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy any reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_ANY_INT
#undef PCCNP_ANY_FLOAT
}

static PyObject *pccnp_all_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy all reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy all reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        (array->typenum != NPY_BOOL && !PyTypeNum_ISNUMBER(array->typenum))) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy all reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (array->element_count == 0) {
        return PyBool_FromLong(1);
    }

    unsigned char *data = (unsigned char *)array->data;

#define PCCNP_ALL_INT(type) \
    do { \
        for (size_t i = 0; i < array->element_count; i++) { \
            if (*(type *)(data + (i * array->itemsize)) == 0) { \
                return PyBool_FromLong(0); \
            } \
        } \
        return PyBool_FromLong(1); \
    } while (0)

#define PCCNP_ALL_FLOAT(type) \
    do { \
        for (size_t i = 0; i < array->element_count; i++) { \
            if (*(type *)(data + (i * array->itemsize)) == (type)0) { \
                return PyBool_FromLong(0); \
            } \
        } \
        return PyBool_FromLong(1); \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
            PCCNP_ALL_INT(unsigned char);
        case NPY_BYTE:
            PCCNP_ALL_INT(signed char);
        case NPY_UBYTE:
            PCCNP_ALL_INT(unsigned char);
        case NPY_SHORT:
            PCCNP_ALL_INT(short);
        case NPY_USHORT:
            PCCNP_ALL_INT(unsigned short);
        case NPY_INT:
            PCCNP_ALL_INT(int);
        case NPY_UINT:
            PCCNP_ALL_INT(unsigned int);
        case NPY_LONG:
            PCCNP_ALL_INT(long);
        case NPY_ULONG:
            PCCNP_ALL_INT(unsigned long);
        case NPY_LONGLONG:
            PCCNP_ALL_INT(long long);
        case NPY_ULONGLONG:
            PCCNP_ALL_INT(unsigned long long);
        case NPY_FLOAT:
            PCCNP_ALL_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_ALL_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_ALL_FLOAT(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy all reduction supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_ALL_INT
#undef PCCNP_ALL_FLOAT
}

static PyObject *pccnp_argmax_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy argmax reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy argmax reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy argmax reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "attempt to get argmax of an empty sequence");
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "argmax index exceeds C long range");
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;
    size_t best_index = 0;
    for (size_t i = 1; i < array->element_count; i++) {
        int cmp = 0;
        if (pccnp_compare_elements(
                array,
                data + (i * array->itemsize),
                data + (best_index * array->itemsize),
                &cmp
            ) != 0) {
            return NULL;
        }
        if (cmp > 0) {
            best_index = i;
        }
    }

    PyObject *result = pccnp_array_new_common(0, NULL, NPY_LONG, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)result);
    if (indices == NULL || indices->data == NULL) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMax expected a pcc result array");
        return NULL;
    }
    *(long *)indices->data = (long)best_index;
    return result;
}

static PyObject *pccnp_argmin_1d_numeric(PccNpArray *array) {
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy argmin reduction supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy argmin reduction supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy argmin reduction does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "attempt to get argmin of an empty sequence");
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "argmin index exceeds C long range");
        return NULL;
    }
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    unsigned char *data = (unsigned char *)array->data;
    size_t best_index = 0;
    for (size_t i = 1; i < array->element_count; i++) {
        int cmp = 0;
        if (pccnp_compare_elements(
                array,
                data + (i * array->itemsize),
                data + (best_index * array->itemsize),
                &cmp
            ) != 0) {
            return NULL;
        }
        if (cmp < 0) {
            best_index = i;
        }
    }

    PyObject *result = pccnp_array_new_common(0, NULL, NPY_LONG, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)result);
    if (indices == NULL || indices->data == NULL) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMin expected a pcc result array");
        return NULL;
    }
    *(long *)indices->data = (long)best_index;
    return result;
}

static PyObject *pccnp_trace(
    PyArrayObject *self_obj,
    int offset,
    int axis1,
    int axis2,
    int rtype,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Trace does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Trace expects a pcc NumPy array");
        return NULL;
    }
    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Trace expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Trace supports only floating rtype conversion"
        );
        return NULL;
    }

    PyObject *diag_obj = pccnp_diagonal(self_obj, offset, axis1, axis2);
    if (diag_obj == NULL) {
        return NULL;
    }
    PccNpArray *diag = pccnp_array_from_object((PyArrayObject *)diag_obj);
    if (diag == NULL) {
        Py_DECREF(diag_obj);
        PyErr_SetString(PyExc_TypeError, "PyArray_Trace expected a pcc diagonal array");
        return NULL;
    }
    PyObject *sum = (rtype != NPY_NOTYPE && rtype != diag->typenum)
        ? pccnp_sum_1d_numeric_float_rtype(diag, rtype)
        : pccnp_sum_1d_numeric(diag);
    Py_DECREF(diag_obj);
    return sum;
}

static PyObject *pccnp_sum(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Sum expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Sum supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Sum expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Sum supports only floating rtype conversion"
        );
        return NULL;
    }
    PyObject *value = (rtype != NPY_NOTYPE && rtype != source->typenum)
        ? pccnp_sum_1d_numeric_float_rtype(source, rtype)
        : pccnp_sum_1d_numeric(source);
    return pccnp_reduction_scalar_or_out(value, out_obj, "PyArray_Sum");
}

static PyObject *pccnp_prod(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Prod expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Prod supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Prod expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Prod supports only floating rtype conversion"
        );
        return NULL;
    }
    PyObject *value = (rtype != NPY_NOTYPE && rtype != source->typenum)
        ? pccnp_prod_1d_numeric_float_rtype(source, rtype)
        : pccnp_prod_1d_numeric(source);
    return pccnp_reduction_scalar_or_out(value, out_obj, "PyArray_Prod");
}

static PyObject *pccnp_cumsum(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CumSum expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_CumSum supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CumSum expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CumSum supports only floating rtype conversion"
        );
        return NULL;
    }
    PyObject *result = (rtype != NPY_NOTYPE && rtype != source->typenum)
        ? pccnp_cumsum_1d_numeric_float_rtype(source, rtype)
        : pccnp_cumsum_1d_numeric(source);
    return pccnp_array_result_or_out(result, out_obj, "PyArray_CumSum");
}

static PyObject *pccnp_cumprod(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CumProd expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_CumProd supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CumProd expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CumProd supports only floating rtype conversion"
        );
        return NULL;
    }
    PyObject *result = (rtype != NPY_NOTYPE && rtype != source->typenum)
        ? pccnp_cumprod_1d_numeric_float_rtype(source, rtype)
        : pccnp_cumprod_1d_numeric(source);
    return pccnp_array_result_or_out(result, out_obj, "PyArray_CumProd");
}

static PyObject *pccnp_max(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Max expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Max supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Max expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_reduction_scalar_or_out(
        pccnp_max_1d_numeric(source),
        out_obj,
        "PyArray_Max"
    );
}

static PyObject *pccnp_min(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Min expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Min supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Min expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_reduction_scalar_or_out(
        pccnp_min_1d_numeric(source),
        out_obj,
        "PyArray_Min"
    );
}

static PyObject *pccnp_ptp(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Ptp expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Ptp supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Ptp expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_reduction_scalar_or_out(
        pccnp_ptp_1d_numeric(source),
        out_obj,
        "PyArray_Ptp"
    );
}

static PyObject *pccnp_mean(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Mean expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Mean supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Mean expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Mean supports only floating rtype conversion"
        );
        return NULL;
    }
    return pccnp_reduction_scalar_or_out(
        pccnp_mean_1d_numeric(source),
        out_obj,
        "PyArray_Mean"
    );
}

static PyObject *pccnp_std(
    PyArrayObject *self_obj,
    int axis,
    int rtype,
    PyArrayObject *out_obj,
    int variance
) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Std expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Std supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Std expects a pcc NumPy array");
        return NULL;
    }
    if (rtype != NPY_NOTYPE && rtype != source->typenum && !PyTypeNum_ISFLOAT(rtype)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Std supports only floating rtype conversion"
        );
        return NULL;
    }
    return pccnp_reduction_scalar_or_out(
        pccnp_std_1d_numeric(source, variance),
        out_obj,
        "PyArray_Std"
    );
}

static PyObject *pccnp_any(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Any does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Any expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Any supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Any expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_any_1d_numeric(source);
}

static PyObject *pccnp_all(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_All does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_All expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_All supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_All expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_all_1d_numeric(source);
}

static PyObject *pccnp_arg_max(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgMax does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMax expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgMax supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMax expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_argmax_1d_numeric(source);
}

static PyObject *pccnp_arg_min(
    PyArrayObject *self_obj,
    int axis,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgMin does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMin expects a pcc NumPy array");
        return NULL;
    }
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgMin supports axis 0 only");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_ArgMin expects a pcc NumPy array");
        return NULL;
    }
    return pccnp_argmin_1d_numeric(source);
}

static int pccnp_bound_to_long_double(
    PyObject *bound,
    int *present,
    long double *out
) {
    if (bound == NULL || bound == Py_None) {
        *present = 0;
        return 0;
    }
    *present = 1;
    if (PyBool_Check(bound) || PyLong_Check(bound)) {
        long long value = PyLong_AsLongLong(bound);
        if (PyErr_Occurred() != NULL) {
            return -1;
        }
        *out = (long double)value;
        return 0;
    }
    if (PyFloat_Check(bound)) {
        double value = PyFloat_AsDouble(bound);
        if (PyErr_Occurred() != NULL) {
            return -1;
        }
        *out = (long double)value;
        return 0;
    }
    PyErr_SetString(
        PyExc_NotImplementedError,
        "pcc NumPy PyArray_Clip supports Python numeric scalar bounds only"
    );
    return -1;
}

static PyObject *pccnp_clip(
    PyArrayObject *self_obj,
    PyObject *min_obj,
    PyObject *max_obj,
    PyArrayObject *out_obj
) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Clip does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Clip expects a pcc NumPy array");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Clip expects a pcc NumPy array");
        return NULL;
    }
    if (source->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Clip supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Clip supports C-order arrays only");
        return NULL;
    }
    if (source->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(source->typenum) ||
        !PyTypeNum_ISNUMBER(source->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Clip does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (source->element_count != 0 && source->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    int has_min = 0;
    int has_max = 0;
    long double lower = 0.0;
    long double upper = 0.0;
    if (pccnp_bound_to_long_double(min_obj, &has_min, &lower) != 0 ||
        pccnp_bound_to_long_double(max_obj, &has_max, &upper) != 0) {
        return NULL;
    }
    if (!has_min && !has_max) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Clip requires at least one bound");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)source->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, source->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    unsigned char *src_data = (unsigned char *)source->data;
    unsigned char *out_data = (unsigned char *)out->data;

#define PCCNP_CLIP_TYPED(type) \
    do { \
        for (size_t i = 0; i < source->element_count; i++) { \
            long double value = (long double)*(type *)(src_data + (i * source->itemsize)); \
            if (has_min && value < lower) value = lower; \
            if (has_max && value > upper) value = upper; \
            *(type *)(out_data + (i * out->itemsize)) = (type)value; \
        } \
        return result; \
    } while (0)

    switch (source->typenum) {
        case NPY_BOOL:
        case NPY_UBYTE:
            PCCNP_CLIP_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_CLIP_TYPED(signed char);
        case NPY_SHORT:
            PCCNP_CLIP_TYPED(short);
        case NPY_USHORT:
            PCCNP_CLIP_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_CLIP_TYPED(int);
        case NPY_UINT:
            PCCNP_CLIP_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_CLIP_TYPED(long);
        case NPY_ULONG:
            PCCNP_CLIP_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_CLIP_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_CLIP_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_CLIP_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_CLIP_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_CLIP_TYPED(long double);
        default:
            Py_DECREF(result);
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Clip supports real numeric arrays only"
            );
            return NULL;
    }

#undef PCCNP_CLIP_TYPED
}

static int pccnp_round_integral(long double value, long double *out) {
    if (value > (long double)LLONG_MAX || value < (long double)LLONG_MIN) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Round does not support values outside long long range"
        );
        return -1;
    }
    long double truncated = (long double)((long long)value);
    long double fraction = value - truncated;
    if (fraction >= 0.5) {
        truncated += 1.0;
    } else if (fraction <= -0.5) {
        truncated -= 1.0;
    }
    *out = truncated;
    return 0;
}

static PyObject *pccnp_round(PyArrayObject *self_obj, int decimals, PyArrayObject *out_obj) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Round does not support out arrays");
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Round expects a pcc NumPy array");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Round expects a pcc NumPy array");
        return NULL;
    }
    if (source->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Round supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Round supports C-order arrays only");
        return NULL;
    }
    if (source->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(source->typenum) ||
        !PyTypeNum_ISNUMBER(source->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Round supports real numeric arrays only"
        );
        return NULL;
    }
    if (source->element_count != 0 && source->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (PyTypeNum_ISINTEGER(source->typenum) || source->typenum == NPY_BOOL) {
        if (decimals < 0) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Round does not support negative decimals"
            );
            return NULL;
        }
        return pccnp_copy(self_obj);
    }
    if (decimals != 0) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Round supports decimal places only for integer arrays"
        );
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)source->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, source->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL || (out->element_count != 0 && out->data == NULL)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_TypeError, "PyArray_Round expected a pcc result array");
        return NULL;
    }

    unsigned char *src_data = (unsigned char *)source->data;
    unsigned char *out_data = (unsigned char *)out->data;

#define PCCNP_ROUND_FLOAT(type) \
    do { \
        for (size_t i = 0; i < source->element_count; i++) { \
            long double rounded = 0.0; \
            if (pccnp_round_integral( \
                    (long double)*(type *)(src_data + (i * source->itemsize)), \
                    &rounded \
                ) != 0) { \
                Py_DECREF(result); \
                return NULL; \
            } \
            *(type *)(out_data + (i * out->itemsize)) = (type)rounded; \
        } \
        return result; \
    } while (0)

    switch (source->typenum) {
        case NPY_FLOAT:
            PCCNP_ROUND_FLOAT(float);
        case NPY_DOUBLE:
            PCCNP_ROUND_FLOAT(double);
        case NPY_LONGDOUBLE:
            PCCNP_ROUND_FLOAT(long double);
        default:
            Py_DECREF(result);
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Round supports integer and real floating arrays only"
            );
            return NULL;
    }

#undef PCCNP_ROUND_FLOAT
}

static PyObject *pccnp_conjugate(PyArrayObject *self_obj, PyArrayObject *out_obj) {
    if (!pccnp_is_array_object((PyObject *)self_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Conjugate expects a pcc NumPy array");
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Conjugate expects a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Conjugate supports C-order arrays only");
        return NULL;
    }
    if (source->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(source->typenum) ||
        !PyTypeNum_ISNUMBER(source->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Conjugate supports real numeric arrays only"
        );
        return NULL;
    }
    if (source->element_count != 0 && source->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (out_obj != NULL) {
        if (pccnp_copy_into(out_obj, self_obj) != 0) {
            return NULL;
        }
        Py_INCREF((PyObject *)out_obj);
        return (PyObject *)out_obj;
    }
    Py_INCREF((PyObject *)self_obj);
    return (PyObject *)self_obj;
}

static PyObject *pccnp_copy(PyArrayObject *src_obj) {
    PccNpArray *src = pccnp_array_from_object(src_obj);
    if (src == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (src->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub does not copy object arrays"
        );
        return NULL;
    }
    PyObject *copy_obj = pccnp_array_new_with_itemsize(
        src->nd,
        src->dims,
        src->typenum,
        src->itemsize,
        NULL,
        1
    );
    if (copy_obj == NULL) return NULL;
    if (pccnp_copy_into((PyArrayObject *)copy_obj, src_obj) != 0) {
        Py_DECREF(copy_obj);
        return NULL;
    }
    return copy_obj;
}

static PyObject *pccnp_new_copy(PyArrayObject *src_obj, NPY_ORDER order) {
    (void)order;
    if (src_obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "obj is NULL in PyArray_NewCopy");
        return NULL;
    }
    return pccnp_copy(src_obj);
}

static int pccnp_array_incref(PyArrayObject *arr_obj) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (array->typenum != NPY_OBJECT) return 0;
    if (array->data == NULL) {
        if (array->element_count == 0) return 0;
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }
    PyObject **items = (PyObject **)array->data;
    for (size_t i = 0; i < array->element_count; i++) {
        Py_XINCREF(items[i]);
    }
    return 0;
}

static int pccnp_array_xdecref(PyArrayObject *arr_obj) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (array->typenum != NPY_OBJECT) return 0;
    if (array->data == NULL) {
        if (array->element_count == 0) return 0;
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }
    PyObject **items = (PyObject **)array->data;
    for (size_t i = 0; i < array->element_count; i++) {
        Py_XDECREF(items[i]);
    }
    return 0;
}

static npy_bool pccnp_can_cast_to(PyArray_Descr *from, PyArray_Descr *to) {
    if (from == NULL || to == NULL) return 0;
    return pccnp_can_cast_safely(from->type_num, to->type_num) ? 1 : 0;
}

static npy_bool pccnp_can_cast_type_to(
    PyArray_Descr *from,
    PyArray_Descr *to,
    NPY_CASTING casting
) {
    if (from == NULL || to == NULL) return 0;
    if (pccnp_descr_for_type(from->type_num) == NULL ||
        pccnp_descr_for_type(to->type_num) == NULL) {
        return 0;
    }
    switch (casting) {
        case NPY_NO_CASTING:
            return from->type_num == to->type_num ? 1 : 0;
        case NPY_EQUIV_CASTING:
            return pccnp_equiv_types(from, to) ? 1 : 0;
        case NPY_SAFE_CASTING:
        case NPY_SAME_KIND_CASTING:
        case NPY_SAME_VALUE_CASTING:
            return pccnp_can_cast_safely(from->type_num, to->type_num) ? 1 : 0;
        case NPY_UNSAFE_CASTING:
            return 1;
        default:
            return 0;
    }
}

static npy_bool pccnp_can_cast_array_to(
    PyArrayObject *arr,
    PyArray_Descr *to,
    NPY_CASTING casting
) {
    if (arr == NULL || to == NULL) return 0;
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) return 0;
    PyArray_Descr *from = pccnp_descr_for_type(array->typenum);
    return pccnp_can_cast_type_to(from, to, casting);
}

static int pccnp_casting_converter(PyObject *obj, NPY_CASTING *casting) {
    if (casting == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy casting output is NULL");
        return NPY_FAIL;
    }

    const char *text = NULL;
    Py_ssize_t length = 0;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8AndSize(obj, &length);
    } else if (PyBytes_Check(obj)) {
        if (PyBytes_AsStringAndSize(obj, (char **)&text, &length) != 0) {
            return NPY_FAIL;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "casting must be str or bytes");
        return NPY_FAIL;
    }
    if (text == NULL) {
        return NPY_FAIL;
    }

    if (length == 2 && strcmp(text, "no") == 0) {
        *casting = NPY_NO_CASTING;
        return NPY_SUCCEED;
    }
    if (length == 5 && strcmp(text, "equiv") == 0) {
        *casting = NPY_EQUIV_CASTING;
        return NPY_SUCCEED;
    }
    if (length == 4 && strcmp(text, "safe") == 0) {
        *casting = NPY_SAFE_CASTING;
        return NPY_SUCCEED;
    }
    if (length == 9 && strcmp(text, "same_kind") == 0) {
        *casting = NPY_SAME_KIND_CASTING;
        return NPY_SUCCEED;
    }
    if (length == 6 && strcmp(text, "unsafe") == 0) {
        *casting = NPY_UNSAFE_CASTING;
        return NPY_SUCCEED;
    }

    PyErr_SetString(
        PyExc_ValueError,
        "casting must be one of 'no', 'equiv', 'safe', 'same_kind', 'unsafe'"
    );
    return NPY_FAIL;
}

static int pccnp_supports_zero_one_type(int typenum) {
    switch (typenum) {
        case NPY_BOOL:
        case NPY_BYTE:
        case NPY_UBYTE:
        case NPY_SHORT:
        case NPY_USHORT:
        case NPY_INT:
        case NPY_UINT:
        case NPY_LONG:
        case NPY_ULONG:
        case NPY_LONGLONG:
        case NPY_ULONGLONG:
        case NPY_FLOAT:
        case NPY_DOUBLE:
        case NPY_LONGDOUBLE:
        case NPY_CFLOAT:
        case NPY_CDOUBLE:
        case NPY_CLONGDOUBLE:
            return 1;
        default:
            return 0;
    }
}

static char *pccnp_zero_or_one(PyArrayObject *arr_obj, int is_one) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_supports_zero_one_type(array->typenum) || array->itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }
    char *buffer = (char *)PyMem_RawMalloc(array->itemsize);
    if (buffer == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    memset(buffer, 0, array->itemsize);
    if (!is_one) return buffer;
    switch (array->typenum) {
        case NPY_BOOL:
            *(unsigned char *)buffer = 1;
            break;
        case NPY_BYTE:
            *(signed char *)buffer = 1;
            break;
        case NPY_UBYTE:
            *(unsigned char *)buffer = 1;
            break;
        case NPY_SHORT:
            *(short *)buffer = 1;
            break;
        case NPY_USHORT:
            *(unsigned short *)buffer = 1;
            break;
        case NPY_INT:
            *(int *)buffer = 1;
            break;
        case NPY_UINT:
            *(unsigned int *)buffer = 1;
            break;
        case NPY_LONG:
            *(long *)buffer = 1;
            break;
        case NPY_ULONG:
            *(unsigned long *)buffer = 1;
            break;
        case NPY_LONGLONG:
            *(long long *)buffer = 1;
            break;
        case NPY_ULONGLONG:
            *(unsigned long long *)buffer = 1;
            break;
        case NPY_FLOAT:
            *(float *)buffer = 1.0f;
            break;
        case NPY_DOUBLE:
            *(double *)buffer = 1.0;
            break;
        case NPY_LONGDOUBLE:
            *(long double *)buffer = 1.0L;
            break;
        case NPY_CFLOAT:
            ((npy_cfloat *)buffer)->real = 1.0f;
            break;
        case NPY_CDOUBLE:
            ((npy_cdouble *)buffer)->real = 1.0;
            break;
        case NPY_CLONGDOUBLE:
            ((npy_clongdouble *)buffer)->real = 1.0L;
            break;
        default:
            PyMem_RawFree(buffer);
            pccnp_raise_unsupported();
            return NULL;
    }
    return buffer;
}

static char *pccnp_zero(PyArrayObject *arr_obj) {
    return pccnp_zero_or_one(arr_obj, 0);
}

static char *pccnp_one(PyArrayObject *arr_obj) {
    return pccnp_zero_or_one(arr_obj, 1);
}

static PyObject *pccnp_type_object_from_type(int typenum) {
    PyObject *type_obj = NULL;
    switch (typenum) {
        case NPY_BOOL:
            type_obj = (PyObject *)&PyBool_Type;
            break;
        case NPY_BYTE:
        case NPY_UBYTE:
        case NPY_SHORT:
        case NPY_USHORT:
        case NPY_INT:
        case NPY_UINT:
        case NPY_LONG:
        case NPY_ULONG:
        case NPY_LONGLONG:
        case NPY_ULONGLONG:
            type_obj = (PyObject *)&PyLong_Type;
            break;
        case NPY_FLOAT:
        case NPY_DOUBLE:
        case NPY_LONGDOUBLE:
            type_obj = (PyObject *)&PyFloat_Type;
            break;
        case NPY_CFLOAT:
        case NPY_CDOUBLE:
        case NPY_CLONGDOUBLE:
            type_obj = (PyObject *)&PyComplex_Type;
            break;
        case NPY_STRING:
            type_obj = (PyObject *)&PyBytes_Type;
            break;
        case NPY_OBJECT:
            type_obj = (PyObject *)&PyBaseObject_Type;
            break;
        default:
            pccnp_raise_unsupported();
            return NULL;
    }
    Py_INCREF(type_obj);
    return type_obj;
}

