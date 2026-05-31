#ifndef PCC_FAKE_NUMPY_ARRAYOBJECT_H
#define PCC_FAKE_NUMPY_ARRAYOBJECT_H

#include <Python.h>

typedef Py_ssize_t npy_intp;
/* PyTypeObject is now provided by <Python.h> (canonical tag struct _typeobject);
 * keep an identical typedef here for older include orders (C11 allows identical
 * typedef redefinition). Must match Python.h's tag to avoid a conflict. */
typedef struct _typeobject PyTypeObject;
typedef struct PyArrayObject PyArrayObject;

typedef struct PyArray_Descr {
    char kind;
    char type;
    char byteorder;
    char flags;
    int type_num;
    int elsize;
    int alignment;
} PyArray_Descr;

typedef struct {
    float real;
    float imag;
} npy_cfloat;

typedef Py_complex npy_cdouble;

typedef struct {
    long double real;
    long double imag;
} npy_clongdouble;

enum NPY_TYPES {
    NPY_BOOL = 0,
    NPY_BYTE,
    NPY_UBYTE,
    NPY_SHORT,
    NPY_USHORT,
    NPY_INT,
    NPY_UINT,
    NPY_LONG,
    NPY_ULONG,
    NPY_LONGLONG,
    NPY_ULONGLONG,
    NPY_FLOAT,
    NPY_DOUBLE,
    NPY_LONGDOUBLE,
    NPY_CFLOAT,
    NPY_CDOUBLE,
    NPY_CLONGDOUBLE,
    NPY_OBJECT = 17,
    NPY_STRING,
    NPY_UNICODE,
    NPY_VOID,
    NPY_NOTYPE = 25,
};

#ifndef PCC_NUMPY_ARRAY_API_CAPSULE
#define PCC_NUMPY_ARRAY_API_CAPSULE "pccnpapi._ARRAY_API"
#endif

static void **PyArray_API = NULL;

static inline int
_import_array(void)
{
    if (PyArray_API != NULL) {
        return 0;
    }
    PyArray_API = (void **)PyCapsule_Import(PCC_NUMPY_ARRAY_API_CAPSULE, 0);
    return PyArray_API == NULL ? -1 : 0;
}

#define import_array() \
    do { \
        if (_import_array() < 0) return NULL; \
    } while (0)

#define import_array1(ret) \
    do { \
        if (_import_array() < 0) return (ret); \
    } while (0)

#define import_array2(msg, ret) \
    do { \
        if (_import_array() < 0) { \
            PyErr_SetString(PyExc_ImportError, (msg)); \
            return (ret); \
        } \
    } while (0)

#define PyArray_Type (*(PyTypeObject *)PyArray_API[0])
#define PyArrayDescr_Type (*(PyTypeObject *)PyArray_API[1])
#define PyArray_DescrFromType \
    (*(PyArray_Descr *(*)(int))PyArray_API[2])
#define PyArray_FromAny \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *, int, int, int, PyObject *))PyArray_API[3])
#define PyArray_SimpleNew(nd, dims, typenum) \
    (*(PyObject *(*)(int, npy_intp *, int))PyArray_API[4])((nd), (dims), (typenum))
#define PyArray_SimpleNewFromData(nd, dims, typenum, data) \
    (*(PyObject *(*)(int, npy_intp *, int, void *))PyArray_API[5])((nd), (dims), (typenum), (data))
#define PyArray_NDIM(arr) \
    (*(int (*)(const PyArrayObject *))PyArray_API[6])((const PyArrayObject *)(arr))
#define PyArray_DIMS(arr) \
    (*(npy_intp *(*)(const PyArrayObject *))PyArray_API[7])((const PyArrayObject *)(arr))
#define PyArray_STRIDES(arr) \
    (*(npy_intp *(*)(const PyArrayObject *))PyArray_API[8])((const PyArrayObject *)(arr))
#define PyArray_DATA(arr) \
    (*(void *(*)(const PyArrayObject *))PyArray_API[9])((const PyArrayObject *)(arr))
#define PyArray_DESCR(arr) \
    (*(PyArray_Descr *(*)(const PyArrayObject *))PyArray_API[10])((const PyArrayObject *)(arr))
#define PyArray_GETITEM(arr, data) \
    (*(PyObject *(*)(PyArrayObject *, void *))PyArray_API[11])((PyArrayObject *)(arr), (data))
#define PyArray_SETITEM(arr, data, item) \
    (*(int (*)(PyArrayObject *, void *, PyObject *))PyArray_API[12])((PyArrayObject *)(arr), (data), (item))
#define PyArray_SIZE(arr) \
    (*(npy_intp (*)(const PyArrayObject *))PyArray_API[13])((const PyArrayObject *)(arr))
#define PyArray_ITEMSIZE(arr) \
    (*(int (*)(const PyArrayObject *))PyArray_API[14])((const PyArrayObject *)(arr))
#define PyArray_Check(op) \
    (*(int (*)(PyObject *))PyArray_API[15])((PyObject *)(op))
#define PyArray_CheckExact(op) \
    (*(int (*)(PyObject *))PyArray_API[16])((PyObject *)(op))
#define PyArray_DIM(arr, n) (PyArray_DIMS(arr)[n])
#define PyArray_BYTES(arr) PyArray_DATA(arr)

#endif
