/* core types, descriptors, ownership, allocation helpers, and forward declarations.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

#define PY_SSIZE_T_CLEAN
#include <limits.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <numpy/arrayobject.h>
#include <numpy/ufuncobject.h>

#define PCCNP_ARRAY_OBJECT_CAPSULE "pccnpapi.ndarray"
#define PCCNP_UFUNC_OBJECT_CAPSULE "pccnpapi.ufunc"
#define PCCNP_MAX_SEQUENCE_ND 8
#define PCCNP_INFER_NEGATIVE_LONG (-1001)
#define PCCNP_INFER_CDOUBLE (-1002)
#define PCCNP_INFER_STRING (-1003)
#define PCCNP_PY_FLAG_IMMORTAL 0x1
#define PCCNP_FLT_MAX 3.40282346638528859812e+38L
#define PCCNP_DBL_MAX 1.79769313486231570815e+308L

typedef struct {
    int nd;
    int typenum;
    size_t itemsize;
    size_t element_count;
    int owns_data;
    int enabled_flags;
    int cleared_flags;
    npy_intp *dims;
    npy_intp *strides;
    void *data;
    PyObject *base;
    PyArray_Descr descr;
} PccNpArray;

typedef struct {
    PyObject_HEAD
    PccNpArray array;
} PccNpArrayObject;

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

static void pccnp_array_object_dealloc(PyObject *self);
static void pccnp_array_iter_object_dealloc(PyObject *self);
static void pccnp_array_multi_iter_object_dealloc(PyObject *self);
static int pccnp_reject_null_data(void *data);

static PyTypeObject pccnp_array_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "numpy.ndarray",
    .tp_basicsize = sizeof(PccNpArrayObject),
    .tp_dealloc = pccnp_array_object_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | PCC_TPFLAGS_MANAGED_DEALLOC,
    .tp_base = &PyBaseObject_Type,
};

static PyTypeObject pccnp_array_descr_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "numpy.dtype",
    .tp_basicsize = sizeof(PyArray_Descr),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_base = &PyBaseObject_Type,
};

static PyTypeObject pccnp_array_iter_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "numpy.flatiter",
    .tp_basicsize = sizeof(PyArrayIterObject),
    .tp_dealloc = pccnp_array_iter_object_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | PCC_TPFLAGS_MANAGED_DEALLOC,
    .tp_base = &PyBaseObject_Type,
};

static PyTypeObject pccnp_array_multi_iter_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "numpy.broadcast",
    .tp_basicsize = sizeof(PyArrayMultiIterObject),
    .tp_dealloc = pccnp_array_multi_iter_object_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | PCC_TPFLAGS_MANAGED_DEALLOC,
    .tp_base = &PyBaseObject_Type,
};

#define PCCNP_DESCR_INIT(kind, type, byteorder, flags, typenum, elsize, alignment) \
    { PyObject_HEAD_INIT(NULL) kind, type, byteorder, flags, typenum, elsize, alignment }

static PyArray_Descr pccnp_bool_descr =
    PCCNP_DESCR_INIT('b', '?', '=', 0, NPY_BOOL, 1, 1);
static PyArray_Descr pccnp_byte_descr =
    PCCNP_DESCR_INIT('i', 'b', '=', 0, NPY_BYTE, 1, 1);
static PyArray_Descr pccnp_ubyte_descr =
    PCCNP_DESCR_INIT('u', 'B', '=', 0, NPY_UBYTE, 1, 1);
static PyArray_Descr pccnp_short_descr = {
    PyObject_HEAD_INIT(NULL)
    'i', 'h', '=', 0, NPY_SHORT, sizeof(short), sizeof(short)
};
static PyArray_Descr pccnp_ushort_descr = {
    PyObject_HEAD_INIT(NULL)
    'u', 'H', '=', 0, NPY_USHORT, sizeof(unsigned short), sizeof(unsigned short)
};
static PyArray_Descr pccnp_int_descr = {
    PyObject_HEAD_INIT(NULL)
    'i', 'i', '=', 0, NPY_INT, sizeof(int), sizeof(int)
};
static PyArray_Descr pccnp_uint_descr = {
    PyObject_HEAD_INIT(NULL)
    'u', 'I', '=', 0, NPY_UINT, sizeof(unsigned int), sizeof(unsigned int)
};
static PyArray_Descr pccnp_long_descr = {
    PyObject_HEAD_INIT(NULL)
    'i', 'l', '=', 0, NPY_LONG, sizeof(long), sizeof(long)
};
static PyArray_Descr pccnp_ulong_descr = {
    PyObject_HEAD_INIT(NULL)
    'u', 'L', '=', 0, NPY_ULONG, sizeof(unsigned long), sizeof(unsigned long)
};
static PyArray_Descr pccnp_longlong_descr = {
    PyObject_HEAD_INIT(NULL)
    'i', 'q', '=', 0, NPY_LONGLONG, sizeof(long long), sizeof(long long)
};
static PyArray_Descr pccnp_ulonglong_descr = {
    PyObject_HEAD_INIT(NULL)
    'u', 'Q', '=', 0, NPY_ULONGLONG, sizeof(unsigned long long), sizeof(unsigned long long)
};
static PyArray_Descr pccnp_float_descr = {
    PyObject_HEAD_INIT(NULL)
    'f', 'f', '=', 0, NPY_FLOAT, sizeof(float), sizeof(float)
};
static PyArray_Descr pccnp_double_descr = {
    PyObject_HEAD_INIT(NULL)
    'f', 'd', '=', 0, NPY_DOUBLE, sizeof(double), sizeof(double)
};
static PyArray_Descr pccnp_longdouble_descr = {
    PyObject_HEAD_INIT(NULL)
    'f', 'g', '=', 0, NPY_LONGDOUBLE, sizeof(long double), sizeof(long double)
};
static PyArray_Descr pccnp_cfloat_descr = {
    PyObject_HEAD_INIT(NULL)
    'c', 'F', '=', 0, NPY_CFLOAT, sizeof(npy_cfloat), sizeof(float)
};
static PyArray_Descr pccnp_cdouble_descr = {
    PyObject_HEAD_INIT(NULL)
    'c', 'D', '=', 0, NPY_CDOUBLE, sizeof(npy_cdouble), sizeof(double)
};
static PyArray_Descr pccnp_clongdouble_descr = {
    PyObject_HEAD_INIT(NULL)
    'c', 'G', '=', 0, NPY_CLONGDOUBLE, sizeof(npy_clongdouble), sizeof(long double)
};
static PyArray_Descr pccnp_object_descr = {
    PyObject_HEAD_INIT(NULL)
    'O', 'O', '=', 0, NPY_OBJECT, sizeof(PyObject *), sizeof(PyObject *)
};
static PyArray_Descr pccnp_string_descr =
    PCCNP_DESCR_INIT('S', 'S', '|', 0, NPY_STRING, 0, 1);

#undef PCCNP_DESCR_INIT

static void pccnp_ready_descr(PyArray_Descr *descr) {
    PyObject_Init((PyObject *)descr, &pccnp_array_descr_type);
    descr->ob_base.flags |= PCCNP_PY_FLAG_IMMORTAL;
}

static void pccnp_ready_all_descriptors(void) {
    pccnp_ready_descr(&pccnp_bool_descr);
    pccnp_ready_descr(&pccnp_byte_descr);
    pccnp_ready_descr(&pccnp_ubyte_descr);
    pccnp_ready_descr(&pccnp_short_descr);
    pccnp_ready_descr(&pccnp_ushort_descr);
    pccnp_ready_descr(&pccnp_int_descr);
    pccnp_ready_descr(&pccnp_uint_descr);
    pccnp_ready_descr(&pccnp_long_descr);
    pccnp_ready_descr(&pccnp_ulong_descr);
    pccnp_ready_descr(&pccnp_longlong_descr);
    pccnp_ready_descr(&pccnp_ulonglong_descr);
    pccnp_ready_descr(&pccnp_float_descr);
    pccnp_ready_descr(&pccnp_double_descr);
    pccnp_ready_descr(&pccnp_longdouble_descr);
    pccnp_ready_descr(&pccnp_cfloat_descr);
    pccnp_ready_descr(&pccnp_cdouble_descr);
    pccnp_ready_descr(&pccnp_clongdouble_descr);
    pccnp_ready_descr(&pccnp_object_descr);
    pccnp_ready_descr(&pccnp_string_descr);
}

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

static const char *pccnp_typenum_name(int typenum) {
    switch (typenum) {
        case NPY_BOOL: return "NPY_BOOL";
        case NPY_BYTE: return "NPY_BYTE";
        case NPY_UBYTE: return "NPY_UBYTE";
        case NPY_SHORT: return "NPY_SHORT";
        case NPY_USHORT: return "NPY_USHORT";
        case NPY_INT: return "NPY_INT";
        case NPY_UINT: return "NPY_UINT";
        case NPY_LONG: return "NPY_LONG";
        case NPY_ULONG: return "NPY_ULONG";
        case NPY_LONGLONG: return "NPY_LONGLONG";
        case NPY_ULONGLONG: return "NPY_ULONGLONG";
        case NPY_FLOAT: return "NPY_FLOAT";
        case NPY_DOUBLE: return "NPY_DOUBLE";
        case NPY_LONGDOUBLE: return "NPY_LONGDOUBLE";
        case NPY_CFLOAT: return "NPY_CFLOAT";
        case NPY_CDOUBLE: return "NPY_CDOUBLE";
        case NPY_CLONGDOUBLE: return "NPY_CLONGDOUBLE";
        case NPY_OBJECT: return "NPY_OBJECT";
        case NPY_STRING: return "NPY_STRING";
        default: return "NPY_UNKNOWN";
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

static int pccnp_integer_bits(int typenum) {
    if (typenum == NPY_BOOL) return 1;
    size_t itemsize = pccnp_itemsize(typenum);
    if (itemsize == 0) return 0;
    return (int)(itemsize * CHAR_BIT);
}

static int pccnp_float_mantissa_bits(int typenum) {
    switch (typenum) {
        case NPY_FLOAT:
            return 24;
        case NPY_DOUBLE:
            return 53;
        case NPY_LONGDOUBLE:
            return 64;
        default:
            return 0;
    }
}

static int pccnp_float_rank(int typenum) {
    switch (typenum) {
        case NPY_FLOAT:
            return 1;
        case NPY_DOUBLE:
            return 2;
        case NPY_LONGDOUBLE:
            return 3;
        default:
            return 0;
    }
}

static int pccnp_complex_rank(int typenum) {
    switch (typenum) {
        case NPY_CFLOAT:
            return 1;
        case NPY_CDOUBLE:
            return 2;
        case NPY_CLONGDOUBLE:
            return 3;
        default:
            return 0;
    }
}

static int pccnp_complex_real_type(int typenum) {
    switch (typenum) {
        case NPY_CFLOAT:
            return NPY_FLOAT;
        case NPY_CDOUBLE:
            return NPY_DOUBLE;
        case NPY_CLONGDOUBLE:
            return NPY_LONGDOUBLE;
        default:
            return NPY_NOTYPE;
    }
}

static int pccnp_can_cast_safely(int fromtype, int totype) {
    if (fromtype == totype) return pccnp_descr_for_type(fromtype) != NULL;
    if (pccnp_descr_for_type(fromtype) == NULL || pccnp_descr_for_type(totype) == NULL) {
        return 0;
    }
    if (totype == NPY_OBJECT) return 1;
    if (fromtype == NPY_OBJECT) return 0;
    if (fromtype == NPY_BOOL && PyTypeNum_ISNUMBER(totype)) return 1;

    if (PyTypeNum_ISINTEGER(fromtype) && PyTypeNum_ISINTEGER(totype)) {
        int from_bits = pccnp_integer_bits(fromtype);
        int to_bits = pccnp_integer_bits(totype);
        if (from_bits == 0 || to_bits == 0) return 0;
        if (PyTypeNum_ISSIGNED(fromtype)) {
            return PyTypeNum_ISSIGNED(totype) && to_bits >= from_bits;
        }
        if (PyTypeNum_ISUNSIGNED(totype)) return to_bits >= from_bits;
        if (PyTypeNum_ISSIGNED(totype)) return to_bits > from_bits;
        return 0;
    }

    if (PyTypeNum_ISINTEGER(fromtype) && PyTypeNum_ISFLOAT(totype)) {
        return pccnp_float_mantissa_bits(totype) >= pccnp_integer_bits(fromtype);
    }

    if (PyTypeNum_ISINTEGER(fromtype) && PyTypeNum_ISCOMPLEX(totype)) {
        return pccnp_can_cast_safely(fromtype, pccnp_complex_real_type(totype));
    }

    if (PyTypeNum_ISFLOAT(fromtype) && PyTypeNum_ISFLOAT(totype)) {
        return pccnp_float_rank(totype) >= pccnp_float_rank(fromtype);
    }

    if (PyTypeNum_ISFLOAT(fromtype) && PyTypeNum_ISCOMPLEX(totype)) {
        return pccnp_complex_rank(totype) >= pccnp_float_rank(fromtype);
    }

    if (PyTypeNum_ISCOMPLEX(fromtype) && PyTypeNum_ISCOMPLEX(totype)) {
        return pccnp_complex_rank(totype) >= pccnp_complex_rank(fromtype);
    }

    return 0;
}

static int pccnp_signed_integer_type_for_bits(int bits) {
    if (bits <= (int)(sizeof(signed char) * CHAR_BIT)) return NPY_BYTE;
    if (bits <= (int)(sizeof(short) * CHAR_BIT)) return NPY_SHORT;
    if (bits <= (int)(sizeof(int) * CHAR_BIT)) return NPY_INT;
    if (bits <= (int)(sizeof(long) * CHAR_BIT)) return NPY_LONG;
    if (bits <= (int)(sizeof(long long) * CHAR_BIT)) return NPY_LONGLONG;
    return NPY_NOTYPE;
}

static int pccnp_unsigned_integer_type_for_bits(int bits) {
    if (bits <= (int)(sizeof(unsigned char) * CHAR_BIT)) return NPY_UBYTE;
    if (bits <= (int)(sizeof(unsigned short) * CHAR_BIT)) return NPY_USHORT;
    if (bits <= (int)(sizeof(unsigned int) * CHAR_BIT)) return NPY_UINT;
    if (bits <= (int)(sizeof(unsigned long) * CHAR_BIT)) return NPY_ULONG;
    if (bits <= (int)(sizeof(unsigned long long) * CHAR_BIT)) return NPY_ULONGLONG;
    return NPY_NOTYPE;
}

static int pccnp_float_type_for_rank(int rank) {
    if (rank <= 1) return NPY_FLOAT;
    if (rank == 2) return NPY_DOUBLE;
    return NPY_LONGDOUBLE;
}

static int pccnp_complex_type_for_rank(int rank) {
    if (rank <= 1) return NPY_CFLOAT;
    if (rank == 2) return NPY_CDOUBLE;
    return NPY_CLONGDOUBLE;
}

static int pccnp_min_unsigned_integer_type_for_value(unsigned long long value) {
    if (value <= (unsigned long long)UCHAR_MAX) return NPY_UBYTE;
    if (value <= (unsigned long long)USHRT_MAX) return NPY_USHORT;
    if (value <= (unsigned long long)UINT_MAX) return NPY_UINT;
    if (value <= (unsigned long long)ULONG_MAX) return NPY_ULONG;
    return NPY_ULONGLONG;
}

static int pccnp_min_signed_integer_type_for_value(long long value) {
    if (value >= 0) {
        return pccnp_min_unsigned_integer_type_for_value((unsigned long long)value);
    }
    if (value >= (long long)SCHAR_MIN && value <= (long long)SCHAR_MAX) {
        return NPY_BYTE;
    }
    if (value >= (long long)SHRT_MIN && value <= (long long)SHRT_MAX) {
        return NPY_SHORT;
    }
    if (value >= (long long)INT_MIN && value <= (long long)INT_MAX) {
        return NPY_INT;
    }
    if (value >= (long long)LONG_MIN && value <= (long long)LONG_MAX) {
        return NPY_LONG;
    }
    return NPY_LONGLONG;
}

static int pccnp_min_float_type_for_value(long double value, int fallback) {
    if (value >= -PCCNP_FLT_MAX && value <= PCCNP_FLT_MAX) {
        return NPY_FLOAT;
    }
    if (value >= -PCCNP_DBL_MAX && value <= PCCNP_DBL_MAX) {
        return NPY_DOUBLE;
    }
    return fallback;
}

static int pccnp_min_scalar_typenum(PccNpArray *array) {
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NPY_NOTYPE;
    }
    switch (array->typenum) {
        case NPY_BOOL:
            return NPY_BOOL;
        case NPY_BYTE:
            return pccnp_min_signed_integer_type_for_value(
                (long long)*(signed char *)array->data
            );
        case NPY_SHORT:
            return pccnp_min_signed_integer_type_for_value(
                (long long)*(short *)array->data
            );
        case NPY_INT:
            return pccnp_min_signed_integer_type_for_value(
                (long long)*(int *)array->data
            );
        case NPY_LONG:
            return pccnp_min_signed_integer_type_for_value(
                (long long)*(long *)array->data
            );
        case NPY_LONGLONG:
            return pccnp_min_signed_integer_type_for_value(
                (long long)*(long long *)array->data
            );
        case NPY_UBYTE:
            return pccnp_min_unsigned_integer_type_for_value(
                (unsigned long long)*(unsigned char *)array->data
            );
        case NPY_USHORT:
            return pccnp_min_unsigned_integer_type_for_value(
                (unsigned long long)*(unsigned short *)array->data
            );
        case NPY_UINT:
            return pccnp_min_unsigned_integer_type_for_value(
                (unsigned long long)*(unsigned int *)array->data
            );
        case NPY_ULONG:
            return pccnp_min_unsigned_integer_type_for_value(
                (unsigned long long)*(unsigned long *)array->data
            );
        case NPY_ULONGLONG:
            return pccnp_min_unsigned_integer_type_for_value(
                (unsigned long long)*(unsigned long long *)array->data
            );
        case NPY_FLOAT:
            return NPY_FLOAT;
        case NPY_DOUBLE:
            return pccnp_min_float_type_for_value(
                (long double)*(double *)array->data,
                NPY_DOUBLE
            );
        case NPY_LONGDOUBLE:
            return pccnp_min_float_type_for_value(
                *(long double *)array->data,
                NPY_LONGDOUBLE
            );
        default:
            return array->typenum;
    }
}

static int pccnp_dtype_promotion_type(int lhs, int rhs) {
    if (lhs == rhs && pccnp_descr_for_type(lhs) != NULL) return lhs;
    if (pccnp_descr_for_type(lhs) == NULL || pccnp_descr_for_type(rhs) == NULL) {
        return NPY_NOTYPE;
    }
    if (lhs == NPY_OBJECT || rhs == NPY_OBJECT) return NPY_OBJECT;
    if (lhs == NPY_STRING || rhs == NPY_STRING) {
        return lhs == rhs ? NPY_STRING : NPY_OBJECT;
    }
    if (lhs == NPY_BOOL && PyTypeNum_ISNUMBER(rhs)) return rhs;
    if (rhs == NPY_BOOL && PyTypeNum_ISNUMBER(lhs)) return lhs;
    if (PyTypeNum_ISCOMPLEX(lhs) || PyTypeNum_ISCOMPLEX(rhs)) {
        int lhs_rank = PyTypeNum_ISCOMPLEX(lhs)
            ? pccnp_complex_rank(lhs)
            : pccnp_float_rank(lhs);
        int rhs_rank = PyTypeNum_ISCOMPLEX(rhs)
            ? pccnp_complex_rank(rhs)
            : pccnp_float_rank(rhs);
        if (lhs_rank == 0 && PyTypeNum_ISINTEGER(lhs)) lhs_rank = 2;
        if (rhs_rank == 0 && PyTypeNum_ISINTEGER(rhs)) rhs_rank = 2;
        int rank = lhs_rank > rhs_rank ? lhs_rank : rhs_rank;
        return pccnp_complex_type_for_rank(rank);
    }
    if (PyTypeNum_ISFLOAT(lhs) || PyTypeNum_ISFLOAT(rhs)) {
        int lhs_rank = pccnp_float_rank(lhs);
        int rhs_rank = pccnp_float_rank(rhs);
        if (lhs_rank == 0 && PyTypeNum_ISINTEGER(lhs)) lhs_rank = 2;
        if (rhs_rank == 0 && PyTypeNum_ISINTEGER(rhs)) rhs_rank = 2;
        int rank = lhs_rank > rhs_rank ? lhs_rank : rhs_rank;
        return pccnp_float_type_for_rank(rank);
    }
    if (PyTypeNum_ISINTEGER(lhs) && PyTypeNum_ISINTEGER(rhs)) {
        if (lhs == NPY_BOOL) return rhs;
        if (rhs == NPY_BOOL) return lhs;
        int lhs_bits = pccnp_integer_bits(lhs);
        int rhs_bits = pccnp_integer_bits(rhs);
        int bits = lhs_bits > rhs_bits ? lhs_bits : rhs_bits;
        if (PyTypeNum_ISSIGNED(lhs) && PyTypeNum_ISSIGNED(rhs)) {
            return pccnp_signed_integer_type_for_bits(bits);
        }
        if (PyTypeNum_ISUNSIGNED(lhs) && PyTypeNum_ISUNSIGNED(rhs)) {
            return pccnp_unsigned_integer_type_for_bits(bits);
        }
        int signed_bits = PyTypeNum_ISSIGNED(lhs) ? lhs_bits : rhs_bits;
        int unsigned_bits = PyTypeNum_ISUNSIGNED(lhs) ? lhs_bits : rhs_bits;
        if (signed_bits > unsigned_bits) {
            return pccnp_signed_integer_type_for_bits(signed_bits);
        }
        if (unsigned_bits < (int)(sizeof(long long) * CHAR_BIT)) {
            return pccnp_signed_integer_type_for_bits(unsigned_bits + 1);
        }
        return NPY_OBJECT;
    }
    return NPY_OBJECT;
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

static void pccnp_array_release_fields(PccNpArray *array) {
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
    Py_XDECREF(array->base);
    array->dims = NULL;
    array->strides = NULL;
    array->data = NULL;
    array->base = NULL;
    array->element_count = 0;
    array->owns_data = 0;
}

static void pccnp_array_free(PccNpArray *array) {
    if (array == NULL) return;
    pccnp_array_release_fields(array);
    PyMem_Free(array);
}

static void pccnp_array_object_dealloc(PyObject *self) {
    if (self == NULL) return;
    PccNpArrayObject *obj = (PccNpArrayObject *)self;
    pccnp_array_release_fields(&obj->array);
}

static void pccnp_array_iter_object_dealloc(PyObject *self) {
    if (self == NULL) return;
    PyArrayIterObject *it = (PyArrayIterObject *)self;
    Py_XDECREF((PyObject *)it->ao);
    it->ao = NULL;
    it->dataptr = NULL;
}

static void pccnp_array_multi_iter_object_dealloc(PyObject *self) {
    if (self == NULL) return;
    PyArrayMultiIterObject *multi = (PyArrayMultiIterObject *)self;
    int numiter = multi->numiter;
    if (numiter < 0) numiter = 0;
    if (numiter > NPY_MAXARGS) numiter = NPY_MAXARGS;
    for (int i = 0; i < numiter; i++) {
        Py_XDECREF((PyObject *)multi->iters[i]);
        multi->iters[i] = NULL;
    }
    multi->numiter = 0;
    multi->size = 0;
    multi->index = 0;
    multi->nd = 0;
    memset(multi->dimensions, 0, sizeof(multi->dimensions));
    memset(multi->iters, 0, sizeof(multi->iters));
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
    PyObject *obj = (PyObject *)arr;
    if (obj != NULL && PyObject_TypeCheck(obj, &pccnp_array_type)) {
        return &((PccNpArrayObject *)obj)->array;
    }
    return (PccNpArray *)PyCapsule_GetPointer(obj, PCCNP_ARRAY_OBJECT_CAPSULE);
}

static int pccnp_is_array_object(PyObject *obj) {
    return (obj != NULL && PyObject_TypeCheck(obj, &pccnp_array_type)) ||
        PyCapsule_IsValid(obj, PCCNP_ARRAY_OBJECT_CAPSULE);
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

    PccNpArrayObject *obj = PyObject_New(PccNpArrayObject, &pccnp_array_type);
    if (obj == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    PccNpArray *array = &obj->array;
    memset(array, 0, sizeof(*array));
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
            Py_DECREF((PyObject *)obj);
            return NULL;
        }
    }

    size_t element_count = 1;
    for (int i = 0; i < nd; i++) {
        if (dims[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            Py_DECREF((PyObject *)obj);
            return NULL;
        }
        array->dims[i] = dims[i];
        if (pccnp_mul_size(element_count, (size_t)dims[i], &element_count) != 0) {
            Py_DECREF((PyObject *)obj);
            return NULL;
        }
    }
    array->element_count = element_count;

    size_t byte_count = 0;
    if (pccnp_mul_size(element_count, itemsize, &byte_count) != 0) {
        Py_DECREF((PyObject *)obj);
        return NULL;
    }
    if (nd > 0) {
        size_t stride = itemsize;
        for (int i = nd - 1; i >= 0; i--) {
            array->strides[i] = (npy_intp)stride;
            if (i > 0 && pccnp_mul_size(stride, (size_t)array->dims[i], &stride) != 0) {
                Py_DECREF((PyObject *)obj);
                return NULL;
            }
        }
    }

    if (owns_data) {
        size_t allocation = byte_count == 0 ? 1 : byte_count;
        array->data = PyMem_Calloc(allocation, 1);
        if (array->data == NULL) {
            PyErr_NoMemory();
            Py_DECREF((PyObject *)obj);
            return NULL;
        }
    } else {
        if (data == NULL && byte_count != 0) {
            PyErr_SetString(PyExc_ValueError, "array data pointer is required");
            Py_DECREF((PyObject *)obj);
            return NULL;
        }
        array->data = data;
    }

    return (PyObject *)obj;
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

static PyArray_Descr *pccnp_descr_copy(PyArray_Descr *base) {
    PyArray_Descr *copy = PyObject_New(PyArray_Descr, &pccnp_array_descr_type);
    if (copy == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    copy->kind = base->kind;
    copy->type = base->type;
    copy->byteorder = base->byteorder;
    copy->flags = base->flags;
    copy->type_num = base->type_num;
    copy->elsize = base->elsize;
    copy->alignment = base->alignment;
    return copy;
}

static PyArray_Descr *pccnp_descr_new_from_type(int typenum) {
    PyArray_Descr *base = pccnp_descr_for_type(typenum);
    if (base == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    return pccnp_descr_copy(base);
}

static PyArray_Descr *pccnp_descr_new(PyArray_Descr *base) {
    if (base == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrNew requires a descriptor");
        return NULL;
    }
    return pccnp_descr_copy(base);
}

static PyArray_Descr *pccnp_descr_new_byteorder(PyArray_Descr *base, char newendian) {
    if (base == NULL) {
        PyErr_SetString(
            PyExc_ValueError,
            "PyArray_DescrNewByteorder requires a descriptor"
        );
        return NULL;
    }
    PyArray_Descr *copy = pccnp_descr_copy(base);
    if (copy == NULL) return NULL;
    if (copy->byteorder != NPY_IGNORE) {
        if (newendian == NPY_SWAP) {
            copy->byteorder = PyArray_ISNBO(copy->byteorder) ? NPY_OPPBYTE : NPY_NATBYTE;
        } else if (newendian != NPY_IGNORE) {
            copy->byteorder = newendian;
        }
    }
    return copy;
}

static PyObject *pccnp_getitem(PyArrayObject *arr, void *data);
static int pccnp_setitem(PyArrayObject *arr, void *data, PyObject *item);
static PyObject *pccnp_ufunc_callable_entry(PyObject *captures, PyObject *args);
static PyObject *pccnp_copy(PyArrayObject *src_obj);
static int pccnp_pyint_as_int(PyObject *obj);

