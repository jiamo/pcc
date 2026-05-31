#define PY_SSIZE_T_CLEAN
#include <limits.h>
#include <string.h>
#include <numpy/arrayobject.h>
#include <numpy/ufuncobject.h>

#define PCCNP_ARRAY_OBJECT_CAPSULE "pccnpapi.ndarray"
#define PCCNP_UFUNC_OBJECT_CAPSULE "pccnpapi.ufunc"
#define PCCNP_MAX_SEQUENCE_ND 8
#define PCCNP_INFER_NEGATIVE_LONG (-1001)
#define PCCNP_INFER_CDOUBLE (-1002)
#define PCCNP_INFER_STRING (-1003)

typedef struct {
    int nd;
    int typenum;
    size_t itemsize;
    size_t element_count;
    int owns_data;
    npy_intp *dims;
    npy_intp *strides;
    void *data;
    PyArray_Descr descr;
} PccNpArray;

typedef struct {
    PyUFuncGenericFunction *functions;
    void **data;
    char *types;
    char *name;
    char *doc;
    int ntypes;
    int nin;
    int nout;
    int identity;
    int check_return;
} PccNpUFunc;

static int array_type_sentinel;
static int array_descr_type_sentinel;

static PyArray_Descr pccnp_bool_descr = {'b', '?', '=', 0, NPY_BOOL, 1, 1};
static PyArray_Descr pccnp_byte_descr = {'i', 'b', '=', 0, NPY_BYTE, 1, 1};
static PyArray_Descr pccnp_ubyte_descr = {'u', 'B', '=', 0, NPY_UBYTE, 1, 1};
static PyArray_Descr pccnp_short_descr = {
    'i', 'h', '=', 0, NPY_SHORT, sizeof(short), sizeof(short)
};
static PyArray_Descr pccnp_ushort_descr = {
    'u', 'H', '=', 0, NPY_USHORT, sizeof(unsigned short), sizeof(unsigned short)
};
static PyArray_Descr pccnp_int_descr = {
    'i', 'i', '=', 0, NPY_INT, sizeof(int), sizeof(int)
};
static PyArray_Descr pccnp_uint_descr = {
    'u', 'I', '=', 0, NPY_UINT, sizeof(unsigned int), sizeof(unsigned int)
};
static PyArray_Descr pccnp_long_descr = {
    'i', 'l', '=', 0, NPY_LONG, sizeof(long), sizeof(long)
};
static PyArray_Descr pccnp_ulong_descr = {
    'u', 'L', '=', 0, NPY_ULONG, sizeof(unsigned long), sizeof(unsigned long)
};
static PyArray_Descr pccnp_longlong_descr = {
    'i', 'q', '=', 0, NPY_LONGLONG, sizeof(long long), sizeof(long long)
};
static PyArray_Descr pccnp_ulonglong_descr = {
    'u', 'Q', '=', 0, NPY_ULONGLONG, sizeof(unsigned long long), sizeof(unsigned long long)
};
static PyArray_Descr pccnp_float_descr = {
    'f', 'f', '=', 0, NPY_FLOAT, sizeof(float), sizeof(float)
};
static PyArray_Descr pccnp_double_descr = {
    'f', 'd', '=', 0, NPY_DOUBLE, sizeof(double), sizeof(double)
};
static PyArray_Descr pccnp_longdouble_descr = {
    'f', 'g', '=', 0, NPY_LONGDOUBLE, sizeof(long double), sizeof(long double)
};
static PyArray_Descr pccnp_cfloat_descr = {
    'c', 'F', '=', 0, NPY_CFLOAT, sizeof(npy_cfloat), sizeof(float)
};
static PyArray_Descr pccnp_cdouble_descr = {
    'c', 'D', '=', 0, NPY_CDOUBLE, sizeof(npy_cdouble), sizeof(double)
};
static PyArray_Descr pccnp_clongdouble_descr = {
    'c', 'G', '=', 0, NPY_CLONGDOUBLE, sizeof(npy_clongdouble), sizeof(long double)
};
static PyArray_Descr pccnp_object_descr = {
    'O', 'O', '=', 0, NPY_OBJECT, sizeof(PyObject *), sizeof(PyObject *)
};
static PyArray_Descr pccnp_string_descr = {'S', 'S', '|', 0, NPY_STRING, 0, 1};

static void pccnp_raise_unsupported(void) {
    PyErr_SetString(
        PyExc_NotImplementedError,
        "pcc NumPy C API stub is not implemented"
    );
}

static size_t pccnp_itemsize(int typenum) {
    switch (typenum) {
        case NPY_BOOL:
        case NPY_BYTE:
        case NPY_UBYTE:
            return 1;
        case NPY_SHORT:
        case NPY_USHORT:
            return sizeof(short);
        case NPY_INT:
        case NPY_UINT:
            return sizeof(int);
        case NPY_LONG:
        case NPY_ULONG:
            return sizeof(long);
        case NPY_LONGLONG:
        case NPY_ULONGLONG:
            return sizeof(long long);
        case NPY_FLOAT:
            return sizeof(float);
        case NPY_DOUBLE:
            return sizeof(double);
        case NPY_LONGDOUBLE:
            return sizeof(long double);
        case NPY_CFLOAT:
            return sizeof(npy_cfloat);
        case NPY_CDOUBLE:
            return sizeof(npy_cdouble);
        case NPY_CLONGDOUBLE:
            return sizeof(npy_clongdouble);
        case NPY_OBJECT:
            return sizeof(PyObject *);
        default:
            return 0;
    }
}

static PyArray_Descr *pccnp_descr_for_type(int typenum) {
    switch (typenum) {
        case NPY_BOOL:
            return &pccnp_bool_descr;
        case NPY_BYTE:
            return &pccnp_byte_descr;
        case NPY_UBYTE:
            return &pccnp_ubyte_descr;
        case NPY_SHORT:
            return &pccnp_short_descr;
        case NPY_USHORT:
            return &pccnp_ushort_descr;
        case NPY_INT:
            return &pccnp_int_descr;
        case NPY_UINT:
            return &pccnp_uint_descr;
        case NPY_LONG:
            return &pccnp_long_descr;
        case NPY_ULONG:
            return &pccnp_ulong_descr;
        case NPY_LONGLONG:
            return &pccnp_longlong_descr;
        case NPY_ULONGLONG:
            return &pccnp_ulonglong_descr;
        case NPY_FLOAT:
            return &pccnp_float_descr;
        case NPY_DOUBLE:
            return &pccnp_double_descr;
        case NPY_LONGDOUBLE:
            return &pccnp_longdouble_descr;
        case NPY_CFLOAT:
            return &pccnp_cfloat_descr;
        case NPY_CDOUBLE:
            return &pccnp_cdouble_descr;
        case NPY_CLONGDOUBLE:
            return &pccnp_clongdouble_descr;
        case NPY_OBJECT:
            return &pccnp_object_descr;
        case NPY_STRING:
            return &pccnp_string_descr;
        default:
            return NULL;
    }
}

static size_t pccnp_itemsize_from_descr(PyArray_Descr *descr) {
    if (descr == NULL) return 0;
    size_t itemsize = pccnp_itemsize(descr->type_num);
    if (itemsize != 0) return itemsize;
    if (descr->type_num == NPY_STRING && descr->elsize > 0) {
        return (size_t)descr->elsize;
    }
    return 0;
}

static int pccnp_mul_size(size_t lhs, size_t rhs, size_t *out) {
    if (rhs != 0 && lhs > ((size_t)-1) / rhs) {
        PyErr_SetString(PyExc_OverflowError, "array size overflow");
        return -1;
    }
    *out = lhs * rhs;
    return 0;
}

static void pccnp_array_free(PccNpArray *array) {
    if (array == NULL) return;
    if (array->owns_data) {
        if (array->typenum == NPY_OBJECT && array->data != NULL) {
            PyObject **items = (PyObject **)array->data;
            for (size_t i = 0; i < array->element_count; i++) {
                Py_XDECREF(items[i]);
            }
        }
        PyMem_Free(array->data);
    }
    PyMem_Free(array->strides);
    PyMem_Free(array->dims);
    PyMem_Free(array);
}

static void pccnp_array_capsule_destructor(PyObject *capsule) {
    PccNpArray *array = (PccNpArray *)PyCapsule_GetPointer(
        capsule,
        PCCNP_ARRAY_OBJECT_CAPSULE
    );
    if (array == NULL) {
        PyErr_Clear();
        return;
    }
    pccnp_array_free(array);
}

static Py_ssize_t pccnp_cstr_len(const char *value) {
    if (value == NULL) return 0;
    Py_ssize_t len = 0;
    while (value[len] != '\0') len++;
    return len;
}

static char *pccnp_copy_cstr(const char *value) {
    if (value == NULL) return NULL;
    Py_ssize_t len = pccnp_cstr_len(value);
    char *copy = (char *)PyMem_Malloc((size_t)len + 1);
    if (copy == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    for (Py_ssize_t i = 0; i <= len; i++) {
        copy[i] = value[i];
    }
    return copy;
}

static void pccnp_ufunc_free(PccNpUFunc *ufunc) {
    if (ufunc == NULL) return;
    PyMem_Free(ufunc->functions);
    PyMem_Free(ufunc->data);
    PyMem_Free(ufunc->types);
    PyMem_Free(ufunc->name);
    PyMem_Free(ufunc->doc);
    PyMem_Free(ufunc);
}

static void pccnp_ufunc_capsule_destructor(PyObject *capsule) {
    PccNpUFunc *ufunc = (PccNpUFunc *)PyCapsule_GetPointer(
        capsule,
        PCCNP_UFUNC_OBJECT_CAPSULE
    );
    if (ufunc == NULL) {
        PyErr_Clear();
        return;
    }
    pccnp_ufunc_free(ufunc);
}

static PccNpUFunc *pccnp_ufunc_from_capsule(PyObject *capsule) {
    return (PccNpUFunc *)PyCapsule_GetPointer(
        capsule,
        PCCNP_UFUNC_OBJECT_CAPSULE
    );
}

static PccNpArray *pccnp_array_from_object(const PyArrayObject *arr) {
    return (PccNpArray *)PyCapsule_GetPointer(
        (PyObject *)arr,
        PCCNP_ARRAY_OBJECT_CAPSULE
    );
}

static int pccnp_is_array_object(PyObject *obj) {
    return PyCapsule_IsValid(obj, PCCNP_ARRAY_OBJECT_CAPSULE);
}

static PyObject *pccnp_array_new_with_itemsize(
    int nd,
    npy_intp *dims,
    int typenum,
    size_t itemsize,
    void *data,
    int owns_data
) {
    if (nd < 0) {
        PyErr_SetString(PyExc_ValueError, "negative array dimension count");
        return NULL;
    }
    if (nd > 0 && dims == NULL) {
        PyErr_SetString(PyExc_ValueError, "array dimensions are required");
        return NULL;
    }
    PyArray_Descr *base_descr = pccnp_descr_for_type(typenum);
    if (itemsize == 0 || base_descr == NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "unsupported NumPy dtype");
        return NULL;
    }
    if (itemsize > (size_t)INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "dtype itemsize overflow");
        return NULL;
    }

    PccNpArray *array = (PccNpArray *)PyMem_Calloc(1, sizeof(PccNpArray));
    if (array == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    array->nd = nd;
    array->typenum = typenum;
    array->itemsize = itemsize;
    array->owns_data = owns_data;
    array->descr = *base_descr;
    array->descr.elsize = (int)itemsize;
    if (nd > 0) {
        array->dims = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
        array->strides = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
        if (array->dims == NULL || array->strides == NULL) {
            PyErr_NoMemory();
            pccnp_array_free(array);
            return NULL;
        }
    }

    size_t element_count = 1;
    for (int i = 0; i < nd; i++) {
        if (dims[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            pccnp_array_free(array);
            return NULL;
        }
        array->dims[i] = dims[i];
        if (pccnp_mul_size(element_count, (size_t)dims[i], &element_count) != 0) {
            pccnp_array_free(array);
            return NULL;
        }
    }
    array->element_count = element_count;

    size_t byte_count = 0;
    if (pccnp_mul_size(element_count, itemsize, &byte_count) != 0) {
        pccnp_array_free(array);
        return NULL;
    }
    if (nd > 0) {
        size_t stride = itemsize;
        for (int i = nd - 1; i >= 0; i--) {
            array->strides[i] = (npy_intp)stride;
            if (i > 0 && pccnp_mul_size(stride, (size_t)array->dims[i], &stride) != 0) {
                pccnp_array_free(array);
                return NULL;
            }
        }
    }

    if (owns_data) {
        size_t allocation = byte_count == 0 ? 1 : byte_count;
        array->data = PyMem_Calloc(allocation, 1);
        if (array->data == NULL) {
            PyErr_NoMemory();
            pccnp_array_free(array);
            return NULL;
        }
    } else {
        if (data == NULL && byte_count != 0) {
            PyErr_SetString(PyExc_ValueError, "array data pointer is required");
            pccnp_array_free(array);
            return NULL;
        }
        array->data = data;
    }

    PyObject *capsule = PyCapsule_New(
        array,
        PCCNP_ARRAY_OBJECT_CAPSULE,
        pccnp_array_capsule_destructor
    );
    if (capsule == NULL) {
        pccnp_array_free(array);
        return NULL;
    }
    return capsule;
}

static PyObject *pccnp_array_new_common(
    int nd,
    npy_intp *dims,
    int typenum,
    void *data,
    int owns_data
) {
    return pccnp_array_new_with_itemsize(
        nd,
        dims,
        typenum,
        pccnp_itemsize(typenum),
        data,
        owns_data
    );
}

static PyArray_Descr *pccnp_descr_from_type(int typenum) {
    PyArray_Descr *descr = pccnp_descr_for_type(typenum);
    if (descr != NULL) return descr;
    pccnp_raise_unsupported();
    return NULL;
}

static PyObject *pccnp_getitem(PyArrayObject *arr, void *data);
static int pccnp_setitem(PyArrayObject *arr, void *data, PyObject *item);
static PyObject *pccnp_ufunc_callable_entry(PyObject *captures, PyObject *args);

static int pccnp_validate_depth(int nd, int min_depth, int max_depth) {
    if (min_depth > nd) {
        PyErr_SetString(PyExc_ValueError, "array has too few dimensions");
        return -1;
    }
    if (max_depth > 0 && nd > max_depth) {
        PyErr_SetString(PyExc_ValueError, "array has too many dimensions");
        return -1;
    }
    return 0;
}

static int pccnp_should_descend_sequence(PyObject *obj, int typenum) {
    if (typenum == NPY_OBJECT) {
        return PyTuple_Check(obj) || PyList_Check(obj);
    }
    if (typenum == NPY_STRING && (PyBytes_Check(obj) || PyUnicode_Check(obj))) {
        return 0;
    }
    return PySequence_Check(obj);
}

static int pccnp_string_bytes_view(
    PyObject *obj,
    const char **bytes,
    Py_ssize_t *length
) {
    if (PyBytes_Check(obj)) {
        char *raw = NULL;
        Py_ssize_t raw_length = 0;
        if (PyBytes_AsStringAndSize(obj, &raw, &raw_length) != 0) return -1;
        *bytes = raw;
        *length = raw_length;
        return 0;
    }
    if (PyUnicode_Check(obj)) {
        const char *raw = PyUnicode_AsUTF8AndSize(obj, length);
        if (raw == NULL) return -1;
        *bytes = raw;
        return 0;
    }
    PyErr_SetString(PyExc_TypeError, "expected str or bytes for string dtype");
    return -1;
}

static int pccnp_scalar_string_length(PyObject *obj, size_t *length) {
    const char *bytes = NULL;
    Py_ssize_t raw_length = 0;
    if (pccnp_string_bytes_view(obj, &bytes, &raw_length) != 0) return -1;
    (void)bytes;
    if (raw_length < 0) {
        PyErr_SetString(PyExc_ValueError, "negative string length");
        return -1;
    }
    *length = (size_t)raw_length;
    return 0;
}

static int pccnp_promote_inferred_type(int *typenum, int item_type) {
    if (*typenum == NPY_NOTYPE) {
        *typenum = item_type;
        return 0;
    }
    if (*typenum == NPY_OBJECT || item_type == NPY_OBJECT) {
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == PCCNP_INFER_STRING || item_type == PCCNP_INFER_STRING) {
        if (*typenum == PCCNP_INFER_STRING && item_type == PCCNP_INFER_STRING) {
            return 0;
        }
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == PCCNP_INFER_CDOUBLE || item_type == PCCNP_INFER_CDOUBLE) {
        *typenum = PCCNP_INFER_CDOUBLE;
        return 0;
    }
    if ((*typenum == NPY_ULONG && item_type == PCCNP_INFER_NEGATIVE_LONG) ||
        (*typenum == PCCNP_INFER_NEGATIVE_LONG && item_type == NPY_ULONG)) {
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == NPY_DOUBLE || item_type == NPY_DOUBLE) {
        *typenum = NPY_DOUBLE;
        return 0;
    }
    if (*typenum == NPY_ULONG || item_type == NPY_ULONG) {
        *typenum = NPY_ULONG;
        return 0;
    }
    if (*typenum == PCCNP_INFER_NEGATIVE_LONG ||
        item_type == PCCNP_INFER_NEGATIVE_LONG) {
        *typenum = PCCNP_INFER_NEGATIVE_LONG;
        return 0;
    }
    if (*typenum == NPY_LONG || item_type == NPY_LONG) {
        *typenum = NPY_LONG;
        return 0;
    }
    *typenum = NPY_BOOL;
    return 0;
}

static int pccnp_scalar_inferred_type(PyObject *obj) {
    if (PyBool_Check(obj)) return NPY_BOOL;
    if (PyLong_Check(obj)) {
        long signed_value = PyLong_AsLong(obj);
        if (PyErr_Occurred() == NULL) {
            return signed_value < 0 ? PCCNP_INFER_NEGATIVE_LONG : NPY_LONG;
        }
        PyErr_Clear();
        (void)PyLong_AsUnsignedLong(obj);
        if (PyErr_Occurred() == NULL) return NPY_ULONG;
        PyErr_Clear();
        return NPY_OBJECT;
    }
    if (PyFloat_Check(obj)) return NPY_DOUBLE;
    if (PyComplex_Check(obj)) return PCCNP_INFER_CDOUBLE;
    if (PyBytes_Check(obj) || PyUnicode_Check(obj)) return PCCNP_INFER_STRING;
    return NPY_OBJECT;
}

static int pccnp_infer_sequence_dtype(
    PyObject *seq,
    int depth,
    int *typenum,
    size_t *string_itemsize
) {
    if (depth >= PCCNP_MAX_SEQUENCE_ND) {
        pccnp_raise_unsupported();
        return -1;
    }
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int rc = 0;
        if (PyTuple_Check(item) || PyList_Check(item)) {
            rc = pccnp_infer_sequence_dtype(
                item,
                depth + 1,
                typenum,
                string_itemsize
            );
        } else {
            int item_type = pccnp_scalar_inferred_type(item);
            if (item_type == PCCNP_INFER_STRING) {
                size_t length = 0;
                if (pccnp_scalar_string_length(item, &length) != 0) {
                    Py_DECREF(item);
                    return -1;
                }
                if (length > *string_itemsize) *string_itemsize = length;
            }
            rc = pccnp_promote_inferred_type(
                typenum,
                item_type
            );
        }
        Py_DECREF(item);
        if (rc != 0) return -1;
    }
    return 0;
}

static int pccnp_infer_sequence_shape(
    PyObject *seq,
    int typenum,
    int depth,
    int *nd,
    int *leaf_depth,
    npy_intp *dims
) {
    if (depth >= PCCNP_MAX_SEQUENCE_ND) {
        pccnp_raise_unsupported();
        return -1;
    }
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    if (*nd <= depth) {
        *nd = depth + 1;
        dims[depth] = (npy_intp)length;
    } else if (dims[depth] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
        return -1;
    }

    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        if (pccnp_should_descend_sequence(item, typenum)) {
            int rc = pccnp_infer_sequence_shape(
                item,
                typenum,
                depth + 1,
                nd,
                leaf_depth,
                dims
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
        } else {
            int item_leaf_depth = depth + 1;
            if (*leaf_depth < 0) {
                *leaf_depth = item_leaf_depth;
            } else if (*leaf_depth != item_leaf_depth) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            Py_DECREF(item);
        }
    }
    return 0;
}

static int pccnp_fill_sequence_values(
    PyObject *array_obj,
    PyObject *seq,
    int depth,
    int nd,
    const npy_intp *dims,
    size_t *offset
) {
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    if (dims[depth] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL) return -1;
    char *data = (char *)array->data;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int descend = pccnp_should_descend_sequence(item, array->typenum);
        if (depth + 1 == nd) {
            if (descend) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            int rc = pccnp_setitem(
                (PyArrayObject *)array_obj,
                data + ((*offset) * array->itemsize),
                item
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
            *offset += 1;
        } else {
            if (!descend) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            int rc = pccnp_fill_sequence_values(
                array_obj,
                item,
                depth + 1,
                nd,
                dims,
                offset
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
        }
    }
    return 0;
}

static int pccnp_fill_object_top_level_values(PyObject *array_obj, PyObject *seq) {
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL) return -1;
    if (array->typenum != NPY_OBJECT || array->nd != 1 || array->dims == NULL ||
        array->dims[0] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "object array top-level shape mismatch");
        return -1;
    }
    char *data = (char *)array->data;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int rc = pccnp_setitem(
            (PyArrayObject *)array_obj,
            data + ((size_t)i * array->itemsize),
            item
        );
        Py_DECREF(item);
        if (rc != 0) return -1;
    }
    return 0;
}

static PyObject *pccnp_from_sequence(
    PyObject *op,
    PyArray_Descr *descr,
    int min_depth,
    int max_depth
) {
    if (!PySequence_Check(op)) {
        pccnp_raise_unsupported();
        return NULL;
    }
    PyArray_Descr *effective_descr = descr;
    size_t effective_itemsize = 0;
    if (effective_descr == NULL) {
        int inferred_type = NPY_NOTYPE;
        size_t inferred_string_itemsize = 0;
        if (pccnp_infer_sequence_dtype(
                op,
                0,
                &inferred_type,
                &inferred_string_itemsize
            ) != 0) {
            return NULL;
        }
        if (inferred_type == NPY_NOTYPE) inferred_type = NPY_DOUBLE;
        if (inferred_type == PCCNP_INFER_NEGATIVE_LONG) inferred_type = NPY_LONG;
        if (inferred_type == PCCNP_INFER_CDOUBLE) inferred_type = NPY_CDOUBLE;
        if (inferred_type == PCCNP_INFER_STRING) {
            inferred_type = NPY_STRING;
            effective_itemsize = inferred_string_itemsize == 0
                ? 1
                : inferred_string_itemsize;
        }
        effective_descr = pccnp_descr_for_type(inferred_type);
    }
    if (effective_itemsize == 0) {
        effective_itemsize = pccnp_itemsize_from_descr(effective_descr);
    }
    if (effective_descr == NULL || effective_itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }

    Py_ssize_t length = PySequence_Size(op);
    if (length < 0) return NULL;

    int nd = 0;
    int leaf_depth = -1;
    int object_top_level = 0;
    npy_intp dims[PCCNP_MAX_SEQUENCE_ND] = {0};
    if (pccnp_infer_sequence_shape(
            op,
            effective_descr->type_num,
            0,
            &nd,
            &leaf_depth,
            dims
        ) != 0) {
        if (effective_descr->type_num != NPY_OBJECT ||
            !PyErr_ExceptionMatches(PyExc_ValueError)) {
            return NULL;
        }
        PyErr_Clear();
        nd = 1;
        leaf_depth = 1;
        dims[0] = (npy_intp)length;
        object_top_level = 1;
    }
    if (pccnp_validate_depth(nd, min_depth, max_depth) != 0) {
        return NULL;
    }

    PyObject *array_obj = pccnp_array_new_with_itemsize(
        nd,
        dims,
        effective_descr->type_num,
        effective_itemsize,
        NULL,
        1
    );
    if (array_obj == NULL) return NULL;
    size_t offset = 0;
    int fill_rc = object_top_level
        ? pccnp_fill_object_top_level_values(array_obj, op)
        : pccnp_fill_sequence_values(array_obj, op, 0, nd, dims, &offset);
    if (fill_rc != 0) {
        Py_DECREF(array_obj);
        return NULL;
    }
    return array_obj;
}

static PyObject *pccnp_cast_array(
    PyObject *op,
    PccNpArray *array,
    PyArray_Descr *descr
) {
    size_t itemsize = pccnp_itemsize_from_descr(descr);
    if (descr == NULL || itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }
    PyObject *cast_obj = pccnp_array_new_with_itemsize(
        array->nd,
        array->dims,
        descr->type_num,
        itemsize,
        NULL,
        1
    );
    if (cast_obj == NULL) return NULL;
    PccNpArray *cast_array = pccnp_array_from_object((PyArrayObject *)cast_obj);
    if (cast_array == NULL) {
        Py_DECREF(cast_obj);
        return NULL;
    }
    char *src = (char *)array->data;
    char *dst = (char *)cast_array->data;
    for (size_t i = 0; i < array->element_count; i++) {
        PyObject *item = pccnp_getitem(
            (PyArrayObject *)op,
            src + (i * array->itemsize)
        );
        if (item == NULL) {
            Py_DECREF(cast_obj);
            return NULL;
        }
        int rc = pccnp_setitem(
            (PyArrayObject *)cast_obj,
            dst + (i * cast_array->itemsize),
            item
        );
        Py_DECREF(item);
        if (rc != 0) {
            Py_DECREF(cast_obj);
            return NULL;
        }
    }
    return cast_obj;
}

static PyObject *pccnp_from_any(
    PyObject *op,
    PyArray_Descr *descr,
    int min_depth,
    int max_depth,
    int requirements,
    PyObject *context
) {
    (void)requirements;
    (void)context;
    if (!pccnp_is_array_object(op)) {
        return pccnp_from_sequence(op, descr, min_depth, max_depth);
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)op);
    if (array == NULL) return NULL;
    if (min_depth > array->nd) {
        PyErr_SetString(PyExc_ValueError, "array has too few dimensions");
        return NULL;
    }
    if (max_depth > 0 && array->nd > max_depth) {
        PyErr_SetString(PyExc_ValueError, "array has too many dimensions");
        return NULL;
    }
    if (descr != NULL) {
        size_t descr_itemsize = pccnp_itemsize_from_descr(descr);
        if (descr->type_num != array->typenum || descr_itemsize != array->itemsize) {
            return pccnp_cast_array(op, array, descr);
        }
    }
    Py_INCREF(op);
    return op;
}

static PyObject *pccnp_simple_new(int nd, npy_intp *dims, int typenum) {
    return pccnp_array_new_common(nd, dims, typenum, NULL, 1);
}

static PyObject *pccnp_simple_new_from_data(
    int nd,
    npy_intp *dims,
    int typenum,
    void *data
) {
    return pccnp_array_new_common(nd, dims, typenum, data, 0);
}

static int pccnp_ndim(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : array->nd;
}

static npy_intp *pccnp_dims(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->dims;
}

static npy_intp *pccnp_strides(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->strides;
}

static void *pccnp_data(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->data;
}

static PyArray_Descr *pccnp_descr(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) return NULL;
    return &array->descr;
}

static npy_intp pccnp_size(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : (npy_intp)array->element_count;
}

static int pccnp_itemsize_api(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : (int)array->itemsize;
}

static int pccnp_array_check(PyObject *obj) {
    if (obj == NULL) return 0;
    return pccnp_is_array_object(obj);
}

static int pccnp_array_check_exact(PyObject *obj) {
    return pccnp_array_check(obj);
}

static int pccnp_compute_broadcast_shape(
    PccNpArray *arrays[2],
    int is_array[2],
    int *out_nd,
    npy_intp **out_dims
) {
    int max_nd = 0;
    *out_nd = 0;
    *out_dims = NULL;
    for (int i = 0; i < 2; i++) {
        if (is_array[i] && arrays[i] != NULL && arrays[i]->nd > max_nd) {
            max_nd = arrays[i]->nd;
        }
    }
    if (max_nd == 0) return 0;
    npy_intp *dims = (npy_intp *)PyMem_Calloc((size_t)max_nd, sizeof(npy_intp));
    if (dims == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    for (int axis = max_nd - 1; axis >= 0; axis--) {
        npy_intp chosen = 1;
        for (int i = 0; i < 2; i++) {
            npy_intp dim = 1;
            if (is_array[i] && arrays[i] != NULL) {
                int source_axis = axis - (max_nd - arrays[i]->nd);
                if (source_axis >= 0) dim = arrays[i]->dims[source_axis];
            }
            if (dim < 0) {
                PyMem_Free(dims);
                PyErr_SetString(PyExc_ValueError, "negative array dimension");
                return -1;
            }
            if (chosen == 1) {
                chosen = dim;
            } else if (dim != 1 && dim != chosen) {
                PyMem_Free(dims);
                PyErr_SetString(PyExc_ValueError, "minimal ufunc operand shapes are not broadcast-compatible");
                return -1;
            }
        }
        dims[axis] = chosen;
    }
    *out_nd = max_nd;
    *out_dims = dims;
    return 0;
}

static char *pccnp_broadcast_data_ptr(
    PccNpArray *array,
    int is_array,
    const npy_intp *out_dims,
    int out_nd,
    size_t flat_index
) {
    if (array == NULL) return NULL;
    if (!is_array || out_nd == 0) return (char *)array->data;
    size_t remaining = flat_index;
    size_t offset = 0;
    for (int axis = out_nd - 1; axis >= 0; axis--) {
        npy_intp out_dim = out_dims[axis];
        npy_intp coord = 0;
        if (out_dim > 0) {
            coord = (npy_intp)(remaining % (size_t)out_dim);
            remaining /= (size_t)out_dim;
        }
        int source_axis = axis - (out_nd - array->nd);
        if (source_axis >= 0 && array->dims[source_axis] != 1) {
            offset += (size_t)coord * (size_t)array->strides[source_axis];
        }
    }
    return (char *)array->data + offset;
}

static int pccnp_type_is_integer(int typenum) {
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
            return 1;
        default:
            return 0;
    }
}

static int pccnp_type_is_float(int typenum) {
    return typenum == NPY_FLOAT || typenum == NPY_DOUBLE ||
        typenum == NPY_LONGDOUBLE;
}

static int pccnp_type_is_complex(int typenum) {
    return typenum == NPY_CFLOAT || typenum == NPY_CDOUBLE ||
        typenum == NPY_CLONGDOUBLE;
}

static int pccnp_array_matches_type(PccNpArray *array, int typenum) {
    if (array == NULL) return 0;
    if (array->typenum == typenum) return 1;
    if (pccnp_itemsize(typenum) == 0) return 0;
    if (pccnp_type_is_integer(array->typenum)) {
        return pccnp_type_is_float(typenum) || pccnp_type_is_complex(typenum);
    }
    if (pccnp_type_is_float(array->typenum)) {
        return pccnp_type_is_float(typenum) || pccnp_type_is_complex(typenum);
    }
    return 0;
}

static int pccnp_scalar_matches_type(PyObject *obj, int typenum) {
    int inferred = pccnp_scalar_inferred_type(obj);
    if (inferred == PCCNP_INFER_NEGATIVE_LONG) return typenum == NPY_LONG;
    if (inferred == PCCNP_INFER_CDOUBLE) return typenum == NPY_CDOUBLE;
    if (inferred == PCCNP_INFER_STRING) return typenum == NPY_STRING;
    if (inferred == typenum) return 1;
    if (PyBool_Check(obj) || PyLong_Check(obj)) {
        return typenum == NPY_FLOAT || typenum == NPY_DOUBLE ||
            typenum == NPY_LONGDOUBLE || typenum == NPY_CFLOAT ||
            typenum == NPY_CDOUBLE || typenum == NPY_CLONGDOUBLE;
    }
    if (PyFloat_Check(obj)) {
        return typenum == NPY_CFLOAT || typenum == NPY_CDOUBLE ||
            typenum == NPY_CLONGDOUBLE;
    }
    return 0;
}

static int pccnp_reject_null_data(void *data) {
    if (data != NULL) return 0;
    PyErr_SetString(PyExc_ValueError, "array item data pointer is NULL");
    return -1;
}

static PyObject *pccnp_getitem(PyArrayObject *arr, void *data) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL || pccnp_reject_null_data(data) != 0) return NULL;
    switch (array->typenum) {
        case NPY_BOOL:
            return PyLong_FromLong(*(unsigned char *)data != 0);
        case NPY_BYTE:
            return PyLong_FromLong(*(signed char *)data);
        case NPY_UBYTE:
            return PyLong_FromUnsignedLong(*(unsigned char *)data);
        case NPY_SHORT:
            return PyLong_FromLong(*(short *)data);
        case NPY_USHORT:
            return PyLong_FromUnsignedLong(*(unsigned short *)data);
        case NPY_INT:
            return PyLong_FromLong(*(int *)data);
        case NPY_UINT:
            return PyLong_FromUnsignedLong(*(unsigned int *)data);
        case NPY_LONG:
            return PyLong_FromLong(*(long *)data);
        case NPY_ULONG:
            return PyLong_FromUnsignedLong(*(unsigned long *)data);
        case NPY_LONGLONG:
            return PyLong_FromLongLong(*(long long *)data);
        case NPY_ULONGLONG:
            return PyLong_FromUnsignedLongLong(*(unsigned long long *)data);
        case NPY_FLOAT:
            return PyFloat_FromDouble((double)*(float *)data);
        case NPY_DOUBLE:
            return PyFloat_FromDouble(*(double *)data);
        case NPY_LONGDOUBLE:
            return PyFloat_FromDouble((double)*(long double *)data);
        case NPY_CFLOAT: {
            npy_cfloat value = *(npy_cfloat *)data;
            return PyComplex_FromDoubles((double)value.real, (double)value.imag);
        }
        case NPY_CDOUBLE: {
            npy_cdouble value = *(npy_cdouble *)data;
            return PyComplex_FromDoubles(value.real, value.imag);
        }
        case NPY_CLONGDOUBLE: {
            npy_clongdouble value = *(npy_clongdouble *)data;
            return PyComplex_FromDoubles((double)value.real, (double)value.imag);
        }
        case NPY_STRING: {
            char *bytes = (char *)data;
            size_t length = array->itemsize;
            while (length > 0 && bytes[length - 1] == '\0') {
                length--;
            }
            return PyBytes_FromStringAndSize(bytes, (Py_ssize_t)length);
        }
        case NPY_OBJECT: {
            PyObject *item = *(PyObject **)data;
            if (item == NULL) Py_RETURN_NONE;
            Py_INCREF(item);
            return item;
        }
        default:
            pccnp_raise_unsupported();
            return NULL;
    }
}

static int pccnp_setitem(PyArrayObject *arr, void *data, PyObject *item) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL || pccnp_reject_null_data(data) != 0) return -1;
    switch (array->typenum) {
        case NPY_BOOL:
            *(unsigned char *)data = PyLong_AsLong(item) != 0;
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_BYTE:
            *(signed char *)data = (signed char)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_UBYTE:
            *(unsigned char *)data = (unsigned char)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_SHORT:
            *(short *)data = (short)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_USHORT:
            *(unsigned short *)data = (unsigned short)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_INT:
            *(int *)data = (int)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_UINT:
            *(unsigned int *)data = (unsigned int)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONG:
            *(long *)data = PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_ULONG:
            *(unsigned long *)data = PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONGLONG:
            *(long long *)data = PyLong_AsLongLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_ULONGLONG:
            *(unsigned long long *)data = PyLong_AsUnsignedLongLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_FLOAT:
            *(float *)data = (float)PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_DOUBLE:
            *(double *)data = PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONGDOUBLE:
            *(long double *)data = (long double)PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_CFLOAT: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            ((npy_cfloat *)data)->real = (float)value.real;
            ((npy_cfloat *)data)->imag = (float)value.imag;
            return 0;
        }
        case NPY_CDOUBLE: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            *(npy_cdouble *)data = value;
            return 0;
        }
        case NPY_CLONGDOUBLE: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            ((npy_clongdouble *)data)->real = (long double)value.real;
            ((npy_clongdouble *)data)->imag = (long double)value.imag;
            return 0;
        }
        case NPY_STRING: {
            const char *bytes = NULL;
            Py_ssize_t length = 0;
            if (pccnp_string_bytes_view(item, &bytes, &length) != 0) return -1;
            if (length < 0) {
                PyErr_SetString(PyExc_ValueError, "negative string length");
                return -1;
            }
            size_t copy_length = (size_t)length;
            if (copy_length > array->itemsize) copy_length = array->itemsize;
            char *target = (char *)data;
            for (size_t i = 0; i < array->itemsize; i++) {
                target[i] = '\0';
            }
            for (size_t i = 0; i < copy_length; i++) {
                target[i] = bytes[i];
            }
            return 0;
        }
        case NPY_OBJECT: {
            PyObject **slot = (PyObject **)data;
            Py_XINCREF(item);
            Py_XDECREF(*slot);
            *slot = item;
            return 0;
        }
        default:
            pccnp_raise_unsupported();
            return -1;
    }
}

static PyObject *pccnp_ufunc_callable_entry(PyObject *captures, PyObject *args) {
    PyObject *capsule = PyTuple_GetItem(captures, 0);
    if (capsule == NULL) return NULL;
    PccNpUFunc *ufunc = pccnp_ufunc_from_capsule(capsule);
    if (ufunc == NULL) return NULL;
    if (ufunc->nin != 2 || ufunc->nout != 1) {
        pccnp_raise_unsupported();
        return NULL;
    }
    if (PyTuple_Size(args) != 2) {
        PyErr_SetString(PyExc_TypeError, "minimal ufunc expects two operands");
        return NULL;
    }

    PyObject *operands[2] = {PyTuple_GetItem(args, 0), PyTuple_GetItem(args, 1)};
    if (operands[0] == NULL || operands[1] == NULL) return NULL;
    PccNpArray *arrays[2] = {NULL, NULL};
    int is_array[2] = {
        pccnp_is_array_object(operands[0]),
        pccnp_is_array_object(operands[1]),
    };
    int has_array_operand = is_array[0] || is_array[1];
    for (int i = 0; i < 2; i++) {
        if (!is_array[i]) continue;
        arrays[i] = pccnp_array_from_object((PyArrayObject *)operands[i]);
        if (arrays[i] == NULL) return NULL;
    }
    int out_nd = 0;
    npy_intp *out_dims = NULL;
    if (pccnp_compute_broadcast_shape(arrays, is_array, &out_nd, &out_dims) != 0) {
        return NULL;
    }
    int signature = -1;
    int out_type = NPY_NOTYPE;
    int width = ufunc->nin + ufunc->nout;
    for (int i = 0; i < ufunc->ntypes; i++) {
        int base = i * width;
        int lhs_type = (unsigned char)ufunc->types[base];
        int rhs_type = (unsigned char)ufunc->types[base + 1];
        int candidate_out = (unsigned char)ufunc->types[base + 2];
        int lhs_matches = is_array[0]
            ? pccnp_array_matches_type(arrays[0], lhs_type)
            : pccnp_scalar_matches_type(operands[0], lhs_type);
        int rhs_matches = is_array[1]
            ? pccnp_array_matches_type(arrays[1], rhs_type)
            : pccnp_scalar_matches_type(operands[1], rhs_type);
        if (lhs_matches && rhs_matches && pccnp_itemsize(candidate_out) != 0) {
            signature = i;
            out_type = candidate_out;
            break;
        }
    }
    if (signature < 0) {
        PyMem_Free(out_dims);
        pccnp_raise_unsupported();
        return NULL;
    }

    PyObject *out_obj = pccnp_array_new_common(
        out_nd,
        out_dims,
        out_type,
        NULL,
        1
    );
    PyMem_Free(out_dims);
    if (out_obj == NULL) return NULL;
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)out_obj);
    if (out == NULL) {
        Py_DECREF(out_obj);
        return NULL;
    }

    PyObject *operand_temps[2] = {NULL, NULL};
    PccNpArray *loop_arrays[2] = {arrays[0], arrays[1]};
    for (int i = 0; i < 2; i++) {
        int scalar_type = (unsigned char)ufunc->types[(signature * width) + i];
        if (is_array[i]) {
            if (arrays[i]->typenum == scalar_type) continue;
            PyArray_Descr *cast_descr = pccnp_descr_for_type(scalar_type);
            operand_temps[i] = pccnp_cast_array(operands[i], arrays[i], cast_descr);
            if (operand_temps[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            loop_arrays[i] = pccnp_array_from_object(
                (PyArrayObject *)operand_temps[i]
            );
            if (loop_arrays[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
        } else {
            operand_temps[i] = pccnp_array_new_common(0, NULL, scalar_type, NULL, 1);
            if (operand_temps[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            PccNpArray *scalar_array = pccnp_array_from_object(
                (PyArrayObject *)operand_temps[i]
            );
            if (scalar_array == NULL ||
                pccnp_setitem(
                    (PyArrayObject *)operand_temps[i],
                    scalar_array->data,
                    operands[i]
                ) != 0) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            loop_arrays[i] = scalar_array;
        }
    }

    npy_intp dimensions[1] = {1};
    npy_intp steps[3] = {0, 0, (npy_intp)out->itemsize};
    char *out_data = (char *)out->data;
    for (size_t i = 0; i < out->element_count; i++) {
        char *loop_args[3] = {
            pccnp_broadcast_data_ptr(loop_arrays[0], is_array[0], out->dims, out->nd, i),
            pccnp_broadcast_data_ptr(loop_arrays[1], is_array[1], out->dims, out->nd, i),
            out_data + (i * out->itemsize),
        };
        ufunc->functions[signature](
            loop_args,
            dimensions,
            steps,
            ufunc->data[signature]
        );
        if (PyErr_Occurred() != NULL) break;
    }
    if (PyErr_Occurred() != NULL) {
        Py_XDECREF(operand_temps[0]);
        Py_XDECREF(operand_temps[1]);
        Py_DECREF(out_obj);
        return NULL;
    }
    if (!has_array_operand) {
        PyObject *scalar_result = pccnp_getitem((PyArrayObject *)out_obj, out->data);
        Py_XDECREF(operand_temps[0]);
        Py_XDECREF(operand_temps[1]);
        Py_DECREF(out_obj);
        return scalar_result;
    }
    Py_XDECREF(operand_temps[0]);
    Py_XDECREF(operand_temps[1]);
    return out_obj;
}

static PyObject *pccnp_ufunc_from_func_and_data(
    PyUFuncGenericFunction *func,
    void **data,
    char *types,
    int ntypes,
    int nin,
    int nout,
    int identity,
    const char *name,
    const char *doc,
    int check_return
) {
    if (func == NULL || types == NULL || ntypes <= 0 || nin < 0 || nout <= 0 ||
        name == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    size_t type_count = 0;
    if (pccnp_mul_size((size_t)ntypes, (size_t)(nin + nout), &type_count) != 0) {
        return NULL;
    }
    PccNpUFunc *ufunc = (PccNpUFunc *)PyMem_Calloc(1, sizeof(PccNpUFunc));
    if (ufunc == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    ufunc->ntypes = ntypes;
    ufunc->nin = nin;
    ufunc->nout = nout;
    ufunc->identity = identity;
    ufunc->check_return = check_return;
    ufunc->functions = (PyUFuncGenericFunction *)PyMem_Calloc(
        (size_t)ntypes,
        sizeof(PyUFuncGenericFunction)
    );
    ufunc->data = (void **)PyMem_Calloc((size_t)ntypes, sizeof(void *));
    ufunc->types = (char *)PyMem_Calloc(type_count == 0 ? 1 : type_count, 1);
    ufunc->name = pccnp_copy_cstr(name);
    if (doc != NULL) {
        ufunc->doc = pccnp_copy_cstr(doc);
    }
    if (ufunc->functions == NULL || ufunc->data == NULL || ufunc->types == NULL ||
        ufunc->name == NULL || (doc != NULL && ufunc->doc == NULL)) {
        if (PyErr_Occurred() == NULL) PyErr_NoMemory();
        pccnp_ufunc_free(ufunc);
        return NULL;
    }
    for (int i = 0; i < ntypes; i++) {
        ufunc->functions[i] = func[i];
        ufunc->data[i] = data == NULL ? NULL : data[i];
    }
    for (size_t i = 0; i < type_count; i++) {
        ufunc->types[i] = types[i];
    }

    PyObject *capsule = PyCapsule_New(
        ufunc,
        PCCNP_UFUNC_OBJECT_CAPSULE,
        pccnp_ufunc_capsule_destructor
    );
    if (capsule == NULL) {
        pccnp_ufunc_free(ufunc);
        return NULL;
    }
    if (PyCapsule_SetContext(capsule, ufunc->name) != 0) {
        Py_DECREF(capsule);
        return NULL;
    }
    PyObject *captures = PyTuple_Pack(1, capsule);
    Py_DECREF(capsule);
    if (captures == NULL) return NULL;
    PyObject *callable = py_func_new((void *)pccnp_ufunc_callable_entry, captures);
    Py_DECREF(captures);
    return callable;
}

static void *array_api[] = {
    &array_type_sentinel,
    &array_descr_type_sentinel,
    (void *)pccnp_descr_from_type,
    (void *)pccnp_from_any,
    (void *)pccnp_simple_new,
    (void *)pccnp_simple_new_from_data,
    (void *)pccnp_ndim,
    (void *)pccnp_dims,
    (void *)pccnp_strides,
    (void *)pccnp_data,
    (void *)pccnp_descr,
    (void *)pccnp_getitem,
    (void *)pccnp_setitem,
    (void *)pccnp_size,
    (void *)pccnp_itemsize_api,
    (void *)pccnp_array_check,
    (void *)pccnp_array_check_exact,
};

static void *ufunc_api[] = {
    (void *)pccnp_ufunc_from_func_and_data,
};

static PyMethodDef ProviderMethods[] = {
    {NULL, NULL, 0, NULL},
};

static PyModuleDef providermodule = {
    PyModuleDef_HEAD_INIT, "pccnpapi", NULL, -1, ProviderMethods,
};

PyMODINIT_FUNC PyInit_pccnpapi(void) {
    PyObject *module = PyModule_Create(&providermodule);
    if (module == NULL) return NULL;
    PyObject *capsule = PyCapsule_New(array_api, PCC_NUMPY_ARRAY_API_CAPSULE, NULL);
    if (capsule == NULL) return NULL;
    if (PyModule_AddObject(module, "_ARRAY_API", capsule) != 0) return NULL;
    capsule = PyCapsule_New(ufunc_api, PCC_NUMPY_UFUNC_API_CAPSULE, NULL);
    if (capsule == NULL) return NULL;
    if (PyModule_AddObject(module, "_UFUNC_API", capsule) != 0) return NULL;
    return module;
}
