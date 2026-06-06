#ifndef PCC_FAKE_NUMPY_ARRAYOBJECT_H
#define PCC_FAKE_NUMPY_ARRAYOBJECT_H

#include <Python.h>
#include <string.h>

typedef Py_ssize_t npy_intp;
typedef size_t npy_uintp;
typedef unsigned char npy_bool;
typedef struct {
    npy_intp *ptr;
    int len;
} PyArray_Dims;
typedef struct {
    PyObject_HEAD
    PyObject *base;
    void *ptr;
    npy_intp len;
    int flags;
} PyArray_Chunk;
typedef struct {
    npy_intp perm;
    npy_intp stride;
} npy_stride_sort_item;
#define PyArray_malloc PyMem_RawMalloc
#define PyArray_free PyMem_RawFree
#define PyArray_realloc PyMem_RawRealloc
#define PyDimMem_NEW(size) ((npy_intp *)PyArray_malloc((size) * sizeof(npy_intp)))
#define PyDimMem_FREE(ptr) PyArray_free(ptr)
#define PyDimMem_RENEW(ptr, size) \
    ((npy_intp *)PyArray_realloc((ptr), (size) * sizeof(npy_intp)))
/* PyTypeObject is now provided by <Python.h> (canonical tag struct _typeobject);
 * keep an identical typedef here for older include orders (C11 allows identical
 * typedef redefinition). Must match Python.h's tag to avoid a conflict. */
typedef struct _typeobject PyTypeObject;
typedef struct PyArrayObject PyArrayObject;

#define NPY_FAIL -1
#define NPY_SUCCEED 0
#define NPY_MAXARGS 32
#define NPY_MAXDIMS_LEGACY_ITERS 32

typedef struct PyArrayIterObject_tag PyArrayIterObject;
typedef char *(*npy_iter_get_dataptr_t)(PyArrayIterObject *iter, const npy_intp *);

struct PyArrayIterObject_tag {
    PyObject_HEAD
    int nd_m1;
    npy_intp index;
    npy_intp size;
    npy_intp coordinates[NPY_MAXDIMS_LEGACY_ITERS];
    npy_intp dims_m1[NPY_MAXDIMS_LEGACY_ITERS];
    npy_intp strides[NPY_MAXDIMS_LEGACY_ITERS];
    npy_intp backstrides[NPY_MAXDIMS_LEGACY_ITERS];
    npy_intp factors[NPY_MAXDIMS_LEGACY_ITERS];
    PyArrayObject *ao;
    char *dataptr;
    npy_bool contiguous;
    npy_intp bounds[NPY_MAXDIMS_LEGACY_ITERS][2];
    npy_intp limits[NPY_MAXDIMS_LEGACY_ITERS][2];
    npy_intp limits_sizes[NPY_MAXDIMS_LEGACY_ITERS];
    npy_iter_get_dataptr_t translate;
};

typedef struct {
    PyObject_HEAD
    int numiter;
    npy_intp size;
    npy_intp index;
    int nd;
    npy_intp dimensions[NPY_MAXDIMS_LEGACY_ITERS];
    PyArrayIterObject *iters[NPY_MAXARGS];
} PyArrayMultiIterObject;

typedef struct PyArray_Descr {
    PyObject_HEAD
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

typedef enum {
    NPY_ANYORDER = -1,
    NPY_CORDER = 0,
    NPY_FORTRANORDER = 1,
    NPY_KEEPORDER = 2,
} NPY_ORDER;

typedef enum {
    NPY_RAISE = 0,
    NPY_WRAP = 1,
    NPY_CLIP = 2,
} NPY_CLIPMODE;

typedef enum {
    NPY_VALID = 0,
    NPY_SAME = 1,
    NPY_FULL = 2,
} NPY_CORRELATEMODE;

typedef enum {
    NPY_NO_CASTING = 0,
    NPY_EQUIV_CASTING = 1,
    NPY_SAFE_CASTING = 2,
    NPY_SAME_KIND_CASTING = 3,
    NPY_UNSAFE_CASTING = 4,
    NPY_SAME_VALUE_CASTING = NPY_UNSAFE_CASTING | 64,
} NPY_CASTING;

typedef enum {
    _NPY_SORT_UNDEFINED = -1,
    NPY_QUICKSORT = 0,
    NPY_HEAPSORT = 1,
    NPY_MERGESORT = 2,
    NPY_STABLESORT = 2,
    _NPY_SORT_HEAPSORT = 1,
    NPY_SORT_DEFAULT = 0,
    NPY_SORT_STABLE = 2,
    NPY_SORT_DESCENDING = 4,
} NPY_SORTKIND;

typedef enum {
    NPY_INTROSELECT = 0,
} NPY_SELECTKIND;

typedef enum {
    NPY_SEARCHLEFT = 0,
    NPY_SEARCHRIGHT = 1,
} NPY_SEARCHSIDE;

typedef enum {
    NPY_NOSCALAR = -1,
    NPY_BOOL_SCALAR = 0,
    NPY_INTPOS_SCALAR = 1,
    NPY_INTNEG_SCALAR = 2,
    NPY_FLOAT_SCALAR = 3,
    NPY_COMPLEX_SCALAR = 4,
    NPY_OBJECT_SCALAR = 5,
} NPY_SCALARKIND;

enum {
    NPY_CPU_UNKNOWN_ENDIAN = 0,
    NPY_CPU_LITTLE = 1,
    NPY_CPU_BIG = 2,
};

/* pcc fake-provider header target: version bookkeeping only, not full NumPy API coverage. */
#define NPY_1_7_API_VERSION 0x00000007
#define NPY_1_8_API_VERSION 0x00000008
#define NPY_1_9_API_VERSION 0x00000009
#define NPY_1_10_API_VERSION 0x0000000a
#define NPY_1_11_API_VERSION 0x0000000a
#define NPY_1_12_API_VERSION 0x0000000a
#define NPY_1_13_API_VERSION 0x0000000b
#define NPY_1_14_API_VERSION 0x0000000c
#define NPY_1_15_API_VERSION 0x0000000c
#define NPY_1_16_API_VERSION 0x0000000d
#define NPY_1_17_API_VERSION 0x0000000d
#define NPY_1_18_API_VERSION 0x0000000d
#define NPY_1_19_API_VERSION 0x0000000d
#define NPY_1_20_API_VERSION 0x0000000e
#define NPY_1_21_API_VERSION 0x0000000e
#define NPY_1_22_API_VERSION 0x0000000f
#define NPY_1_23_API_VERSION 0x00000010
#define NPY_1_24_API_VERSION 0x00000010
#define NPY_1_25_API_VERSION 0x00000011
#define NPY_2_0_API_VERSION 0x00000012
#define NPY_2_1_API_VERSION 0x00000013
#define NPY_2_2_API_VERSION 0x00000013
#define NPY_2_3_API_VERSION 0x00000014
#define NPY_2_4_API_VERSION 0x00000015

#ifndef NPY_API_VERSION
#define NPY_API_VERSION NPY_2_4_API_VERSION
#endif

#ifndef NPY_ABI_VERSION
#define NPY_ABI_VERSION 0x02000000
#endif

#ifndef NPY_VERSION
#define NPY_VERSION NPY_ABI_VERSION
#endif

#ifndef NPY_FEATURE_VERSION
#define NPY_FEATURE_VERSION NPY_API_VERSION
#endif

#define PyTypeNum_ISBOOL(type) ((type) == NPY_BOOL)
#define PyTypeNum_ISUNSIGNED(type) \
    (((type) == NPY_UBYTE) || ((type) == NPY_USHORT) || ((type) == NPY_UINT) || \
     ((type) == NPY_ULONG) || ((type) == NPY_ULONGLONG))
#define PyTypeNum_ISSIGNED(type) \
    (((type) == NPY_BYTE) || ((type) == NPY_SHORT) || ((type) == NPY_INT) || \
     ((type) == NPY_LONG) || ((type) == NPY_LONGLONG))
#define PyTypeNum_ISINTEGER(type) (((type) >= NPY_BYTE) && ((type) <= NPY_ULONGLONG))
#define PyTypeNum_ISFLOAT(type) (((type) >= NPY_FLOAT) && ((type) <= NPY_LONGDOUBLE))
#define PyTypeNum_ISNUMBER(type) (((type) >= NPY_BOOL) && ((type) <= NPY_CLONGDOUBLE))
#define PyTypeNum_ISSTRING(type) (((type) == NPY_STRING) || ((type) == NPY_UNICODE))
#define PyTypeNum_ISCOMPLEX(type) (((type) >= NPY_CFLOAT) && ((type) <= NPY_CLONGDOUBLE))
#define PyTypeNum_ISFLEXIBLE(type) (((type) >= NPY_STRING) && ((type) <= NPY_VOID))
#define PyTypeNum_ISOBJECT(type) ((type) == NPY_OBJECT)

#define PyDataType_ISBOOL(obj) PyTypeNum_ISBOOL(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISUNSIGNED(obj) PyTypeNum_ISUNSIGNED(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISSIGNED(obj) PyTypeNum_ISSIGNED(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISINTEGER(obj) PyTypeNum_ISINTEGER(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISFLOAT(obj) PyTypeNum_ISFLOAT(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISNUMBER(obj) PyTypeNum_ISNUMBER(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISSTRING(obj) PyTypeNum_ISSTRING(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISCOMPLEX(obj) PyTypeNum_ISCOMPLEX(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISFLEXIBLE(obj) PyTypeNum_ISFLEXIBLE(((PyArray_Descr *)(obj))->type_num)
#define PyDataType_ISOBJECT(obj) PyTypeNum_ISOBJECT(((PyArray_Descr *)(obj))->type_num)

#define NPY_ARRAY_C_CONTIGUOUS 0x0001
#define NPY_ARRAY_F_CONTIGUOUS 0x0002
#define NPY_ARRAY_OWNDATA 0x0004
#define NPY_ARRAY_ALIGNED 0x0100
#define NPY_ARRAY_ENSURECOPY 0x0020
#define NPY_ARRAY_ENSUREARRAY 0x0040
#define NPY_ARRAY_WRITEABLE 0x0400
#define NPY_ARRAY_WRITEBACKIFCOPY 0x2000
#define NPY_ARRAY_NOTSWAPPED 0x0200
#define NPY_ARRAY_BEHAVED \
    (NPY_ARRAY_ALIGNED | NPY_ARRAY_WRITEABLE)
#define NPY_ARRAY_BEHAVED_NS \
    (NPY_ARRAY_ALIGNED | NPY_ARRAY_WRITEABLE | NPY_ARRAY_NOTSWAPPED)
#define NPY_ARRAY_CARRAY \
    (NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_BEHAVED)
#define NPY_ARRAY_CARRAY_RO \
    (NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED)
#define NPY_ARRAY_FARRAY \
    (NPY_ARRAY_F_CONTIGUOUS | NPY_ARRAY_BEHAVED)
#define NPY_ARRAY_FARRAY_RO \
    (NPY_ARRAY_F_CONTIGUOUS | NPY_ARRAY_ALIGNED)
#define NPY_ARRAY_DEFAULT NPY_ARRAY_CARRAY

#define NPY_LITTLE '<'
#define NPY_BIG '>'
#define NPY_NATIVE '='
#define NPY_SWAP 's'
#define NPY_IGNORE '|'
#define NPY_RAVEL_AXIS (-2147483647 - 1)
#if defined(__BYTE_ORDER__) && defined(__ORDER_BIG_ENDIAN__) && \
    (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
#define NPY_NATBYTE NPY_BIG
#define NPY_OPPBYTE NPY_LITTLE
#else
#define NPY_NATBYTE NPY_LITTLE
#define NPY_OPPBYTE NPY_BIG
#endif

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
#define PyArray_DescrCheck(op) PyObject_TypeCheck((op), &PyArrayDescr_Type)
#define PyArray_DescrFromType \
    (*(PyArray_Descr *(*)(int))PyArray_API[2])
#define PyArray_DescrNewFromType \
    (*(PyArray_Descr *(*)(int))PyArray_API[35])
#define PyArray_DescrNew \
    (*(PyArray_Descr *(*)(PyArray_Descr *))PyArray_API[36])
#define PyArray_DescrNewByteorder \
    (*(PyArray_Descr *(*)(PyArray_Descr *, char))PyArray_API[37])
#define PyArray_CanCastSafely \
    (*(int (*)(int, int))PyArray_API[38])
#define PyArray_ObjectType \
    (*(int (*)(PyObject *, int))PyArray_API[39])
#define PyArray_CheckFromAny \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *, int, int, int, PyObject *))PyArray_API[40])
#define PyArray_FromArray \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Descr *, int))PyArray_API[41])
#define PyArray_MultiplyList \
    (*(npy_intp (*)(const npy_intp *, int))PyArray_API[42])
#define PyArray_MultiplyIntList \
    (*(int (*)(const int *, int))PyArray_API[43])
#define PyArray_GetPtr \
    (*(void *(*)(PyArrayObject *, const npy_intp *))PyArray_API[44])
#define PyArray_ElementStrides \
    (*(int (*)(PyObject *))PyArray_API[45])
#define PyArray_ValidType \
    (*(int (*)(int))PyArray_API[46])
#define PyArray_Item_INCREF \
    (*(void (*)(char *, PyArray_Descr *))PyArray_API[47])
#define PyArray_Item_XDECREF \
    (*(void (*)(char *, PyArray_Descr *))PyArray_API[48])
#define PyArray_NewCopy \
    (*(PyObject *(*)(PyArrayObject *, NPY_ORDER))PyArray_API[49])
#define PyArray_INCREF \
    (*(int (*)(PyArrayObject *))PyArray_API[50])
#define PyArray_XDECREF \
    (*(int (*)(PyArrayObject *))PyArray_API[51])
#define PyArray_CanCastTo \
    (*(npy_bool (*)(PyArray_Descr *, PyArray_Descr *))PyArray_API[52])
#define PyArray_Zero \
    (*(char *(*)(PyArrayObject *))PyArray_API[53])
#define PyArray_One \
    (*(char *(*)(PyArrayObject *))PyArray_API[54])
#define PyArray_TypeObjectFromType \
    (*(PyObject *(*)(int))PyArray_API[55])
#define PyArray_DescrFromObject \
    (*(PyArray_Descr *(*)(PyObject *, PyArray_Descr *))PyArray_API[56])
#define PyArray_Size \
    (*(npy_intp (*)(PyObject *))PyArray_API[57])
#define PyArray_DescrFromScalar \
    (*(PyArray_Descr *(*)(PyObject *))PyArray_API[58])
#define PyArray_DescrFromTypeObject \
    (*(PyArray_Descr *(*)(PyObject *))PyArray_API[59])
#define PyArray_Scalar \
    (*(PyObject *(*)(void *, PyArray_Descr *, PyObject *))PyArray_API[169])
#define PyArray_ScalarAsCtype \
    (*(void (*)(PyObject *, void *))PyArray_API[60])
#define PyArray_FromScalar \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *))PyArray_API[61])
#define PyArray_CastScalarToCtype \
    (*(int (*)(PyObject *, void *, PyArray_Descr *))PyArray_API[62])
#define PyArray_Pack \
    (*(int (*)(PyArray_Descr *, void *, PyObject *))PyArray_API[63])
#define PyArray_CastScalarDirect \
    (*(int (*)(PyObject *, PyArray_Descr *, void *, int))PyArray_API[64])
#define PyArray_CastToType \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Descr *, int))PyArray_API[65])
#define PyArray_FillWithScalar \
    (*(int (*)(PyArrayObject *, PyObject *))PyArray_API[66])
#define PyArray_ToList \
    (*(PyObject *(*)(PyArrayObject *))PyArray_API[67])
#define PyArray_ToString \
    (*(PyObject *(*)(PyArrayObject *, NPY_ORDER))PyArray_API[68])
#define PyArray_Byteswap \
    (*(PyObject *(*)(PyArrayObject *, npy_bool))PyArray_API[69])
#define PyArray_FromString \
    (*(PyObject *(*)(char *, npy_intp, PyArray_Descr *, npy_intp, char *))PyArray_API[70])
#define PyArray_FromBuffer \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *, npy_intp, npy_intp))PyArray_API[71])
#define PyArray_FromIter \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *, npy_intp))PyArray_API[72])
#define PyArray_CopyObject \
    (*(int (*)(PyArrayObject *, PyObject *))PyArray_API[73])
#define PyArray_Resize \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Dims *, int, NPY_ORDER))PyArray_API[74])
#define PyArray_NewLikeArray \
    (*(PyObject *(*)(PyArrayObject *, NPY_ORDER, PyArray_Descr *, int))PyArray_API[75])
#define PyArray_View \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Descr *, PyTypeObject *))PyArray_API[76])
#define PyArray_Squeeze \
    (*(PyObject *(*)(PyArrayObject *))PyArray_API[77])
#define PyArray_Transpose \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Dims *))PyArray_API[78])
#define PyArray_Ravel \
    (*(PyObject *(*)(PyArrayObject *, NPY_ORDER))PyArray_API[79])
#define PyArray_Flatten \
    (*(PyObject *(*)(PyArrayObject *, NPY_ORDER))PyArray_API[80])
#define PyArray_TakeFrom \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, int, PyArrayObject *, NPY_CLIPMODE))PyArray_API[81])
#define PyArray_PutTo \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, PyObject *, NPY_CLIPMODE))PyArray_API[82])
#define PyArray_PutMask \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, PyObject *))PyArray_API[83])
#define PyArray_Repeat \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, int))PyArray_API[84])
#define PyArray_Choose \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, PyArrayObject *, NPY_CLIPMODE))PyArray_API[85])
#define PyArray_Sort \
    (*(int (*)(PyArrayObject *, int, NPY_SORTKIND))PyArray_API[86])
#define PyArray_ArgSort \
    (*(PyObject *(*)(PyArrayObject *, int, NPY_SORTKIND))PyArray_API[87])
#define PyArray_SearchSorted \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, NPY_SEARCHSIDE, PyObject *))PyArray_API[88])
#define PyArray_Nonzero \
    (*(PyObject *(*)(PyArrayObject *))PyArray_API[89])
#define PyArray_Where \
    (*(PyObject *(*)(PyObject *, PyObject *, PyObject *))PyArray_API[90])
#define PyArray_Compress \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, int, PyArrayObject *))PyArray_API[91])
#define PyArray_Diagonal \
    (*(PyObject *(*)(PyArrayObject *, int, int, int))PyArray_API[92])
#define PyArray_Trace \
    (*(PyObject *(*)(PyArrayObject *, int, int, int, int, PyArrayObject *))PyArray_API[93])
#define PyArray_Clip \
    (*(PyObject *(*)(PyArrayObject *, PyObject *, PyObject *, PyArrayObject *))PyArray_API[94])
#define PyArray_Conjugate \
    (*(PyObject *(*)(PyArrayObject *, PyArrayObject *))PyArray_API[95])
#define PyArray_Sum \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *))PyArray_API[96])
#define PyArray_Prod \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *))PyArray_API[97])
#define PyArray_Max \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[98])
#define PyArray_Min \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[99])
#define PyArray_ArgMax \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[100])
#define PyArray_ArgMin \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[101])
#define PyArray_Reshape \
    (*(PyObject *(*)(PyArrayObject *, PyObject *))PyArray_API[102])
#define PyArray_Newshape \
    (*(PyObject *(*)(PyArrayObject *, PyArray_Dims *, NPY_ORDER))PyArray_API[103])
#define PyArray_SwapAxes \
    (*(PyObject *(*)(PyArrayObject *, int, int))PyArray_API[104])
#define PyArray_Ptp \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[105])
#define PyArray_Mean \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *))PyArray_API[106])
#define PyArray_Any \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[107])
#define PyArray_All \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[108])
#define PyArray_CumSum \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *))PyArray_API[109])
#define PyArray_CumProd \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *))PyArray_API[110])
#define PyArray_Std \
    (*(PyObject *(*)(PyArrayObject *, int, int, PyArrayObject *, int))PyArray_API[111])
#define PyArray_Round \
    (*(PyObject *(*)(PyArrayObject *, int, PyArrayObject *))PyArray_API[112])
#define PyArray_EquivTypenums \
    (*(int (*)(int, int))PyArray_API[113])
#define PyArray_ScalarKind \
    (*(NPY_SCALARKIND (*)(int, PyArrayObject **))PyArray_API[170])
#define PyArray_CanCoerceScalar \
    (*(int (*)(int, int, NPY_SCALARKIND))PyArray_API[114])
#define PyArray_CanCastScalar \
    (*(npy_bool (*)(PyTypeObject *, PyTypeObject *))PyArray_API[116])
#define PyArray_PromoteTypes \
    (*(PyArray_Descr *(*)(PyArray_Descr *, PyArray_Descr *))PyArray_API[174])
#define PyArray_ResultType \
    (*(PyArray_Descr *(*)(int, PyArrayObject **, int, PyArray_Descr **))PyArray_API[175])
#define PyArray_ConvertToCommonType \
    (*(PyArrayObject **(*)(PyObject *, int *))PyArray_API[171])
#define PyArray_IntTupleFromIntp \
    (*(PyObject *(*)(int, const npy_intp *))PyArray_API[117])
#define PyArray_ClipmodeConverter \
    (*(int (*)(PyObject *, NPY_CLIPMODE *))PyArray_API[118])
#define PyArray_ConvertClipmodeSequence \
    (*(int (*)(PyObject *, NPY_CLIPMODE *, int))PyArray_API[141])
#define PyArray_OutputConverter \
    (*(int (*)(PyObject *, PyArrayObject **))PyArray_API[119])
#define PyArray_SearchsideConverter \
    (*(int (*)(PyObject *, void *))PyArray_API[120])
#define PyArray_OverflowMultiplyList \
    (*(npy_intp (*)(const npy_intp *, int))PyArray_API[121])
#define PyArray_GetEndianness \
    (*(int (*)(void))PyArray_API[122])
#define PyArray_GetNDArrayCFeatureVersion \
    (*(unsigned int (*)(void))PyArray_API[123])
#define PyArray_CheckAxis \
    (*(PyObject *(*)(PyArrayObject *, int *, int))PyArray_API[124])
#define PyArray_DescrAlignConverter \
    (*(int (*)(PyObject *, PyArray_Descr **))PyArray_API[125])
#define PyArray_DescrAlignConverter2 \
    (*(int (*)(PyObject *, PyArray_Descr **))PyArray_API[126])
#define PyArray_DescrConverter \
    (*(int (*)(PyObject *, PyArray_Descr **))PyArray_API[150])
#define PyArray_DescrConverter2 \
    (*(int (*)(PyObject *, PyArray_Descr **))PyArray_API[151])
#define PyArray_FromAny \
    (*(PyObject *(*)(PyObject *, PyArray_Descr *, int, int, int, PyObject *))PyArray_API[3])
#define PyArray_Converter \
    (*(int (*)(PyObject *, PyObject **))PyArray_API[144])
#define PyArray_SimpleNew(nd, dims, typenum) \
    (*(PyObject *(*)(int, npy_intp *, int))PyArray_API[4])((nd), (dims), (typenum))
#define PyArray_SimpleNewFromData(nd, dims, typenum, data) \
    (*(PyObject *(*)(int, npy_intp *, int, void *))PyArray_API[5])((nd), (dims), (typenum), (data))
#define PyArray_NDIM(arr) \
    (*(int (*)(const PyArrayObject *))PyArray_API[6])((const PyArrayObject *)(arr))
#define PyArray_DIMS(arr) \
    (*(npy_intp *(*)(const PyArrayObject *))PyArray_API[7])((const PyArrayObject *)(arr))
#define PyArray_SHAPE(arr) PyArray_DIMS(arr)
#define PyArray_STRIDES(arr) \
    (*(npy_intp *(*)(const PyArrayObject *))PyArray_API[8])((const PyArrayObject *)(arr))
#define PyArray_DATA(arr) \
    (*(void *(*)(const PyArrayObject *))PyArray_API[9])((const PyArrayObject *)(arr))
#define PyArray_DESCR(arr) \
    (*(PyArray_Descr *(*)(const PyArrayObject *))PyArray_API[10])((const PyArrayObject *)(arr))
#define PyArray_DTYPE(arr) PyArray_DESCR(arr)
#define PyArray_TYPE(arr) (PyArray_DESCR(arr)->type_num)
#define PyArray_Cast(mp, type_num) \
    PyArray_CastToType((mp), PyArray_DescrFromType(type_num), 0)
#define PyDataType_TYPE(descr) ((descr)->type_num)
#define PyDataType_KIND(descr) ((descr)->kind)
#define PyDataType_ELSIZE(descr) ((descr)->elsize)
#define PyDataType_ALIGNMENT(descr) ((descr)->alignment)
#define PyArray_GETITEM(arr, data) \
    (*(PyObject *(*)(PyArrayObject *, void *))PyArray_API[11])((PyArrayObject *)(arr), (data))
#define PyArray_SETITEM(arr, data, item) \
    (*(int (*)(PyArrayObject *, void *, PyObject *))PyArray_API[12])((PyArrayObject *)(arr), (data), (item))
#define PyArray_SIZE(arr) \
    (*(npy_intp (*)(const PyArrayObject *))PyArray_API[13])((const PyArrayObject *)(arr))
#define PyArray_ITEMSIZE(arr) \
    (*(int (*)(const PyArrayObject *))PyArray_API[14])((const PyArrayObject *)(arr))
#define PyArray_NBYTES(arr) (PyArray_SIZE(arr) * (npy_intp)PyArray_ITEMSIZE(arr))
#define PyArray_FILLWBYTE(arr, value) \
    memset(PyArray_DATA(arr), (value), PyArray_NBYTES(arr))
#define PyArray_EquivByteorders(left, right) \
    (((left) == (right)) || (PyArray_ISNBO(left) == PyArray_ISNBO(right)))
#define PyArray_FROM_O(obj) PyArray_FromAny((obj), NULL, 0, 0, 0, NULL)
#define PyArray_FROM_OF(obj, flags) \
    PyArray_CheckFromAny((obj), NULL, 0, 0, (flags), NULL)
#define PyArray_FROM_OT(obj, type) \
    PyArray_FromAny((obj), PyArray_DescrFromType(type), 0, 0, 0, NULL)
#define PyArray_FROM_OTF(obj, type, flags) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        0, \
        0, \
        (((flags) & NPY_ARRAY_ENSURECOPY) ? ((flags) | NPY_ARRAY_DEFAULT) : (flags)), \
        NULL \
    )
#define PyArray_FROMANY(obj, type, min_depth, max_depth, flags) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        (min_depth), \
        (max_depth), \
        (((flags) & NPY_ARRAY_ENSURECOPY) ? ((flags) | NPY_ARRAY_DEFAULT) : (flags)), \
        NULL \
    )
#define PyArray_ContiguousFromAny(obj, type, min_depth, max_depth) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        (min_depth), \
        (max_depth), \
        NPY_ARRAY_DEFAULT, \
        NULL \
    )
#define PyArray_FromObject(obj, type, min_depth, max_depth) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        (min_depth), \
        (max_depth), \
        NPY_ARRAY_BEHAVED | NPY_ARRAY_ENSUREARRAY, \
        NULL \
    )
#define PyArray_ContiguousFromObject(obj, type, min_depth, max_depth) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        (min_depth), \
        (max_depth), \
        NPY_ARRAY_DEFAULT | NPY_ARRAY_ENSUREARRAY, \
        NULL \
    )
#define PyArray_CopyFromObject(obj, type, min_depth, max_depth) \
    PyArray_FromAny( \
        (obj), \
        PyArray_DescrFromType(type), \
        (min_depth), \
        (max_depth), \
        NPY_ARRAY_ENSURECOPY | NPY_ARRAY_DEFAULT | NPY_ARRAY_ENSUREARRAY, \
        NULL \
    )
#define PyArray_Check(op) \
    (*(int (*)(PyObject *))PyArray_API[15])((PyObject *)(op))
#define PyArray_CheckExact(op) \
    (*(int (*)(PyObject *))PyArray_API[16])((PyObject *)(op))
#define PyArray_FLAGS(arr) \
    (*(int (*)(const PyArrayObject *))PyArray_API[17])((const PyArrayObject *)(arr))
#define PyArray_CompareLists \
    (*(int (*)(const npy_intp *, const npy_intp *, int))PyArray_API[18])
#define PyArray_Empty \
    (*(PyObject *(*)(int, npy_intp *, PyArray_Descr *, int))PyArray_API[19])
#define PyArray_Zeros \
    (*(PyObject *(*)(int, npy_intp *, PyArray_Descr *, int))PyArray_API[20])
#define PyArray_EMPTY(nd, dims, typenum, is_f_order) \
    PyArray_Empty((nd), (dims), PyArray_DescrFromType(typenum), (is_f_order))
#define PyArray_ZEROS(nd, dims, typenum, is_f_order) \
    PyArray_Zeros((nd), (dims), PyArray_DescrFromType(typenum), (is_f_order))
#define PyArray_EquivTypes \
    (*(int (*)(PyArray_Descr *, PyArray_Descr *))PyArray_API[21])
#define PyArray_EquivArrTypes(a, b) \
    PyArray_EquivTypes(PyArray_DESCR(a), PyArray_DESCR(b))
#define PyArray_NewFromDescr \
    (*(PyObject *(*)(PyTypeObject *, PyArray_Descr *, int, npy_intp *, npy_intp *, void *, int, PyObject *))PyArray_API[22])
#define PyArray_New \
    (*(PyObject *(*)(PyTypeObject *, int, npy_intp *, int, npy_intp *, void *, int, int, PyObject *))PyArray_API[172])
#define PyArray_SimpleNewFromDescr(nd, dims, descr) \
    PyArray_NewFromDescr((PyTypeObject *)PyArray_API[0], (descr), (nd), (dims), NULL, NULL, 0, NULL)
#define PyArray_BASE(arr) \
    (*(PyObject *(*)(const PyArrayObject *))PyArray_API[23])((const PyArrayObject *)(arr))
#define PyArray_SetBaseObject \
    (*(int (*)(PyArrayObject *, PyObject *))PyArray_API[24])
#define PyArray_SetUpdateIfCopyBase \
    (*(int (*)(PyArrayObject *, PyArrayObject *))PyArray_API[152])
#define PyArray_SetWritebackIfCopyBase \
    (*(int (*)(PyArrayObject *, PyArrayObject *))PyArray_API[153])
#define PyArray_ResolveWritebackIfCopy \
    (*(int (*)(PyArrayObject *))PyArray_API[154])
#define PyArray_DiscardWritebackIfCopy(arr) \
    (*(void (*)(PyArrayObject *))PyArray_API[155])((PyArrayObject *)(arr))
#define PyDataMem_NEW \
    (*(void *(*)(size_t))PyArray_API[156])
#define PyDataMem_FREE \
    (*(void (*)(void *))PyArray_API[157])
#define PyDataMem_RENEW \
    (*(void *(*)(void *, size_t))PyArray_API[158])
#define PyDataMem_NEW_ZEROED \
    (*(void *(*)(size_t, size_t))PyArray_API[159])
#define PyDataMem_GetHandler \
    (*(PyObject *(*)(void))PyArray_API[160])
#define PyDataMem_UserNEW \
    (*(void *(*)(npy_uintp, PyObject *))PyArray_API[161])
#define PyDataMem_UserFREE \
    (*(void (*)(void *, npy_uintp, PyObject *))PyArray_API[162])
#define PyDataMem_UserRENEW \
    (*(void *(*)(void *, size_t, PyObject *))PyArray_API[163])
#define PyDataMem_UserNEW_ZEROED \
    (*(void *(*)(size_t, size_t, PyObject *))PyArray_API[164])
#define PyArray_CanCastTypeTo \
    (*(npy_bool (*)(PyArray_Descr *, PyArray_Descr *, NPY_CASTING))PyArray_API[165])
#define PyArray_CanCastArrayTo \
    (*(npy_bool (*)(PyArrayObject *, PyArray_Descr *, NPY_CASTING))PyArray_API[166])
#define PyArray_Return \
    (*(PyObject *(*)(PyArrayObject *))PyArray_API[25])
#define PyArray_ENABLEFLAGS \
    (*(void (*)(PyArrayObject *, int))PyArray_API[26])
#define PyArray_CLEARFLAGS \
    (*(void (*)(PyArrayObject *, int))PyArray_API[27])
#define PyArray_UpdateFlags \
    (*(void (*)(PyArrayObject *, int))PyArray_API[28])
#define PyArray_CopyInto \
    (*(int (*)(PyArrayObject *, PyArrayObject *))PyArray_API[29])
#define PyArray_CopyAnyInto \
    (*(int (*)(PyArrayObject *, PyArrayObject *))PyArray_API[30])
#define PyArray_ToScalar \
    (*(PyObject *(*)(void *, PyArrayObject *))PyArray_API[31])
#define PyArray_Copy \
    (*(PyObject *(*)(PyArrayObject *))PyArray_API[32])
#define PyArray_EnsureArray \
    (*(PyObject *(*)(PyObject *))PyArray_API[33])
#define PyArray_EnsureAnyArray \
    (*(PyObject *(*)(PyObject *))PyArray_API[34])
#define PyArray_IterNew \
    (*(PyObject *(*)(PyObject *))PyArray_API[127])
#define PyArray_BroadcastToShape \
    (*(PyObject *(*)(PyObject *, npy_intp *, int))PyArray_API[128])
#define PyArray_Broadcast \
    (*(int (*)(PyArrayMultiIterObject *))PyArray_API[176])
#define PyArray_MultiIterFromObjects \
    (*(PyObject *(*)(PyObject **, int, int, ...))PyArray_API[177])
#define PyArray_RemoveSmallest \
    (*(int (*)(PyArrayMultiIterObject *))PyArray_API[178])
#define _PyArray_MultiIterNew \
    (*(PyObject *(*)(int, PyObject **))PyArray_API[173])
#define PyArray_MultiIterNew(n, ...) \
    _PyArray_MultiIterNew((n), (PyObject *[]){__VA_ARGS__})
#define PyArray_IterAllButAxis \
    (*(PyObject *(*)(PyObject *, int *))PyArray_API[129])
#define PyArray_PyIntAsInt \
    (*(int (*)(PyObject *))PyArray_API[130])
#define PyArray_PyIntAsIntp \
    (*(npy_intp (*)(PyObject *))PyArray_API[131])
#define PyArray_PythonPyIntFromInt \
    (*(int (*)(PyObject *, int *))PyArray_API[167])
#define PyArray_CastingConverter \
    (*(int (*)(PyObject *, NPY_CASTING *))PyArray_API[168])
#define PyArray_IntpFromSequence \
    (*(int (*)(PyObject *, npy_intp *, int))PyArray_API[143])
#define PyArray_IntpConverter \
    (*(int (*)(PyObject *, PyArray_Dims *))PyArray_API[145])
#define PyArray_BufferConverter \
    (*(int (*)(PyObject *, PyArray_Chunk *))PyArray_API[179])
#define PyArray_Concatenate \
    (*(PyObject *(*)(PyObject *, int))PyArray_API[180])
#define PyArray_Arange \
    (*(PyObject *(*)(double, double, double, int))PyArray_API[181])
#define PyArray_ArangeObj \
    (*(PyObject *(*)(PyObject *, PyObject *, PyObject *, PyArray_Descr *))PyArray_API[182])
#define PyArray_LexSort \
    (*(PyObject *(*)(PyObject *, int))PyArray_API[183])
#define PyArray_InnerProduct \
    (*(PyObject *(*)(PyObject *, PyObject *))PyArray_API[184])
#define PyArray_MatrixProduct \
    (*(PyObject *(*)(PyObject *, PyObject *))PyArray_API[185])
#define PyArray_MatrixProduct2 \
    (*(PyObject *(*)(PyObject *, PyObject *, PyArrayObject *))PyArray_API[188])
#define PyArray_CountNonzero \
    (*(npy_intp (*)(PyArrayObject *))PyArray_API[189])
#define PyArray_MinScalarType \
    (*(PyArray_Descr *(*)(PyArrayObject *))PyArray_API[190])
#define PyArray_CreateSortedStridePerm \
    (*(void (*)(int, const npy_intp *, npy_stride_sort_item *))PyArray_API[191])
#define PyArray_RemoveAxesInPlace \
    (*(void (*)(PyArrayObject *, const npy_bool *))PyArray_API[192])
#define PyArray_DebugPrint \
    (*(void (*)(PyArrayObject *))PyArray_API[193])
#define PyArray_EinsteinSum \
    (*(PyArrayObject *(*)(char *, npy_intp, PyArrayObject **, PyArray_Descr *, NPY_ORDER, NPY_CASTING, PyArrayObject *))PyArray_API[194])
#define PyArray_Partition \
    (*(int (*)(PyArrayObject *, PyArrayObject *, int, NPY_SELECTKIND))PyArray_API[195])
#define PyArray_ArgPartition \
    (*(PyObject *(*)(PyArrayObject *, PyArrayObject *, int, NPY_SELECTKIND))PyArray_API[196])
#define PyArray_CheckAnyScalarExact \
    (*(int (*)(PyObject *))PyArray_API[197])
#define PyArray_Correlate \
    (*(PyObject *(*)(PyObject *, PyObject *, int))PyArray_API[186])
#define PyArray_Correlate2 \
    (*(PyObject *(*)(PyObject *, PyObject *, int))PyArray_API[187])
#define PyArray_OptionalIntpConverter \
    (*(int (*)(PyObject *, PyArray_Dims *))PyArray_API[146])
#define PyArray_Free \
    (*(int (*)(PyObject *, void *))PyArray_API[147])
#define PyArray_AsCArray \
    (*(int (*)(PyObject **, void *, npy_intp *, int, PyArray_Descr *))PyArray_API[148])
#define PyArray_FailUnlessWriteable \
    (*(int (*)(PyArrayObject *, const char *))PyArray_API[149])
#define PyArray_CheckStrides \
    (*(npy_bool (*)(int, int, npy_intp, npy_intp, npy_intp const *, npy_intp const *))PyArray_API[132])
#define PyArray_GetPriority \
    (*(double (*)(PyObject *, double))PyArray_API[133])
#define PyArray_OrderConverter \
    (*(int (*)(PyObject *, NPY_ORDER *))PyArray_API[134])
#define PyArray_BoolConverter \
    (*(int (*)(PyObject *, npy_bool *))PyArray_API[135])
#define PyArray_OptionalBoolConverter \
    (*(int (*)(PyObject *, int *))PyArray_API[142])
#define PyArray_AxisConverter \
    (*(int (*)(PyObject *, int *))PyArray_API[136])
#define PyArray_GetNDArrayCVersion \
    (*(unsigned int (*)(void))PyArray_API[137])
#define PyArray_ByteorderConverter \
    (*(int (*)(PyObject *, char *))PyArray_API[138])
#define PyArray_SortkindConverter \
    (*(int (*)(PyObject *, NPY_SORTKIND *))PyArray_API[139])
#define PyArray_SelectkindConverter \
    (*(int (*)(PyObject *, NPY_SELECTKIND *))PyArray_API[140])
#define _PyAIT(it) ((PyArrayIterObject *)(it))
#define PyArray_ITER_RESET(it) do { \
        _PyAIT(it)->index = 0; \
        _PyAIT(it)->dataptr = (char *)PyArray_BYTES(_PyAIT(it)->ao); \
        if (_PyAIT(it)->nd_m1 >= 0) { \
            memset(_PyAIT(it)->coordinates, 0, \
                   ((size_t)_PyAIT(it)->nd_m1 + 1u) * sizeof(npy_intp)); \
        } \
    } while (0)
#define _PyArray_ITER_NEXT1(it) do { \
        (it)->dataptr += _PyAIT(it)->strides[0]; \
        (it)->coordinates[0]++; \
    } while (0)
#define _PyArray_ITER_NEXT2(it) do { \
        if ((it)->coordinates[1] < (it)->dims_m1[1]) { \
            (it)->coordinates[1]++; \
            (it)->dataptr += (it)->strides[1]; \
        } else { \
            (it)->coordinates[1] = 0; \
            (it)->coordinates[0]++; \
            (it)->dataptr += (it)->strides[0] - (it)->backstrides[1]; \
        } \
    } while (0)
#define PyArray_ITER_NEXT(it) do { \
        _PyAIT(it)->index++; \
        if (_PyAIT(it)->nd_m1 == 0) { \
            _PyArray_ITER_NEXT1(_PyAIT(it)); \
        } else if (_PyAIT(it)->contiguous) { \
            _PyAIT(it)->dataptr += PyArray_ITEMSIZE(_PyAIT(it)->ao); \
        } else if (_PyAIT(it)->nd_m1 == 1) { \
            _PyArray_ITER_NEXT2(_PyAIT(it)); \
        } else { \
            int __npy_i; \
            for (__npy_i = _PyAIT(it)->nd_m1; __npy_i >= 0; __npy_i--) { \
                if (_PyAIT(it)->coordinates[__npy_i] < _PyAIT(it)->dims_m1[__npy_i]) { \
                    _PyAIT(it)->coordinates[__npy_i]++; \
                    _PyAIT(it)->dataptr += _PyAIT(it)->strides[__npy_i]; \
                    break; \
                } \
                _PyAIT(it)->coordinates[__npy_i] = 0; \
                _PyAIT(it)->dataptr -= _PyAIT(it)->backstrides[__npy_i]; \
            } \
        } \
    } while (0)
#define PyArray_ITER_DATA(it) ((void *)(_PyAIT(it)->dataptr))
#define PyArray_ITER_NOTDONE(it) (_PyAIT(it)->index < _PyAIT(it)->size)
#define PyArray_ITER_GOTO1D(it, ind) do { \
        npy_intp __npy_ind = (npy_intp)(ind); \
        int __npy_axis; \
        _PyAIT(it)->index = __npy_ind; \
        _PyAIT(it)->dataptr = (char *)PyArray_BYTES(_PyAIT(it)->ao); \
        for (__npy_axis = 0; __npy_axis <= _PyAIT(it)->nd_m1; __npy_axis++) { \
            npy_intp __npy_dim = _PyAIT(it)->dims_m1[__npy_axis] + 1; \
            npy_intp __npy_coord = 0; \
            if (__npy_dim > 0 && _PyAIT(it)->factors[__npy_axis] > 0) { \
                __npy_coord = (__npy_ind / _PyAIT(it)->factors[__npy_axis]) % __npy_dim; \
            } \
            _PyAIT(it)->coordinates[__npy_axis] = __npy_coord; \
            _PyAIT(it)->dataptr += __npy_coord * _PyAIT(it)->strides[__npy_axis]; \
        } \
    } while (0)
#define PyArray_ITER_GOTO(it, dest) PyArray_ITER_GOTO1D((it), (dest))
#define _PyMIT(m) ((PyArrayMultiIterObject *)(m))
#define PyArray_MultiIter_RESET(multi) do { \
        int __npy_mi; \
        _PyMIT(multi)->index = 0; \
        for (__npy_mi = 0; __npy_mi < _PyMIT(multi)->numiter; __npy_mi++) { \
            PyArray_ITER_RESET(_PyMIT(multi)->iters[__npy_mi]); \
        } \
    } while (0)
#define PyArray_MultiIter_NEXT(multi) do { \
        int __npy_mi; \
        _PyMIT(multi)->index++; \
        for (__npy_mi = 0; __npy_mi < _PyMIT(multi)->numiter; __npy_mi++) { \
            PyArray_ITER_NEXT(_PyMIT(multi)->iters[__npy_mi]); \
        } \
    } while (0)
#define PyArray_MultiIter_GOTO1D(multi, ind) do { \
        int __npy_mi; \
        for (__npy_mi = 0; __npy_mi < _PyMIT(multi)->numiter; __npy_mi++) { \
            PyArray_ITER_GOTO1D(_PyMIT(multi)->iters[__npy_mi], (ind)); \
        } \
        _PyMIT(multi)->index = (npy_intp)(ind); \
    } while (0)
#define PyArray_MultiIter_GOTO(multi, dest) PyArray_MultiIter_GOTO1D((multi), (dest))
#define PyArray_MultiIter_DATA(multi, i) ((void *)(_PyMIT(multi)->iters[i]->dataptr))
#define PyArray_MultiIter_NEXTi(multi, i) PyArray_ITER_NEXT(_PyMIT(multi)->iters[i])
#define PyArray_MultiIter_NOTDONE(multi) (_PyMIT(multi)->index < _PyMIT(multi)->size)
#define PyArray_MultiIter_NUMITER(multi) (_PyMIT(multi)->numiter)
#define PyArray_MultiIter_SIZE(multi) (_PyMIT(multi)->size)
#define PyArray_MultiIter_INDEX(multi) (_PyMIT(multi)->index)
#define PyArray_MultiIter_NDIM(multi) (_PyMIT(multi)->nd)
#define PyArray_MultiIter_DIMS(multi) (_PyMIT(multi)->dimensions)
#define PyArray_MultiIter_ITERS(multi) ((void **)_PyMIT(multi)->iters)
#define PyArray_SAMESHAPE(a, b) \
    ((PyArray_NDIM(a) == PyArray_NDIM(b)) && \
     PyArray_CompareLists(PyArray_DIMS(a), PyArray_DIMS(b), PyArray_NDIM(a)))
#define PyArray_DIM(arr, n) (PyArray_DIMS(arr)[n])
#define PyArray_BYTES(arr) PyArray_DATA(arr)
#define PyArray_STRIDE(arr, n) (PyArray_STRIDES(arr)[n])
#define PyArray_GETPTR1(arr, i) \
    ((void *)((char *)PyArray_BYTES(arr) + (i) * PyArray_ITEMSIZE(arr)))
#define PyArray_GETPTR2(arr, i, j) \
    ((void *)((char *)PyArray_BYTES(arr) + (i) * PyArray_STRIDE((arr), 0) + \
              (j) * PyArray_STRIDE((arr), 1)))
#define PyArray_GETPTR3(arr, i, j, k) \
    ((void *)((char *)PyArray_BYTES(arr) + (i) * PyArray_STRIDE((arr), 0) + \
              (j) * PyArray_STRIDE((arr), 1) + (k) * PyArray_STRIDE((arr), 2)))
#define PyArray_GETPTR4(arr, i, j, k, l) \
    ((void *)((char *)PyArray_BYTES(arr) + (i) * PyArray_STRIDE((arr), 0) + \
              (j) * PyArray_STRIDE((arr), 1) + (k) * PyArray_STRIDE((arr), 2) + \
              (l) * PyArray_STRIDE((arr), 3)))
#define PyArray_CHKFLAGS(arr, flags) \
    ((PyArray_FLAGS(arr) & (flags)) == (flags))
#define PyArray_ISCONTIGUOUS(arr) \
    PyArray_CHKFLAGS((arr), NPY_ARRAY_C_CONTIGUOUS)
#define PyArray_IS_C_CONTIGUOUS(arr) PyArray_ISCONTIGUOUS(arr)
#define PyArray_IS_F_CONTIGUOUS(arr) \
    PyArray_CHKFLAGS((arr), NPY_ARRAY_F_CONTIGUOUS)
#define PyArray_ISALIGNED(arr) PyArray_CHKFLAGS((arr), NPY_ARRAY_ALIGNED)
#define PyArray_ISWRITEABLE(arr) PyArray_CHKFLAGS((arr), NPY_ARRAY_WRITEABLE)
#define PyArray_ISONESEGMENT(arr) \
    (PyArray_CHKFLAGS((arr), NPY_ARRAY_C_CONTIGUOUS) || \
     PyArray_CHKFLAGS((arr), NPY_ARRAY_F_CONTIGUOUS))
#define PyArray_ISFORTRAN(arr) \
    (PyArray_CHKFLAGS((arr), NPY_ARRAY_F_CONTIGUOUS) && \
     !PyArray_CHKFLAGS((arr), NPY_ARRAY_C_CONTIGUOUS))
#define PyArray_FORTRAN_IF(arr) \
    (PyArray_CHKFLAGS((arr), NPY_ARRAY_F_CONTIGUOUS) ? \
         NPY_ARRAY_F_CONTIGUOUS : \
         0)
#define PyArray_ISNBO(arg) ((arg) != NPY_OPPBYTE)
#define PyArray_IsNativeByteOrder PyArray_ISNBO
#define PyArray_ISNOTSWAPPED(arr) PyArray_ISNBO(PyArray_DESCR(arr)->byteorder)
#define PyArray_ISBYTESWAPPED(arr) (!PyArray_ISNOTSWAPPED(arr))
#define PyArray_FLAGSWAP(arr, flags) \
    (PyArray_CHKFLAGS((arr), (flags)) && PyArray_ISNOTSWAPPED(arr))
#define PyArray_ISCARRAY(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_CARRAY)
#define PyArray_ISCARRAY_RO(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_CARRAY_RO)
#define PyArray_ISFARRAY(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_FARRAY)
#define PyArray_ISFARRAY_RO(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_FARRAY_RO)
#define PyArray_ISBEHAVED(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_BEHAVED)
#define PyArray_ISBEHAVED_RO(arr) PyArray_FLAGSWAP((arr), NPY_ARRAY_ALIGNED)
#define PyDataType_ISNOTSWAPPED(descr) PyArray_ISNBO(((PyArray_Descr *)(descr))->byteorder)
#define PyDataType_ISBYTESWAPPED(descr) (!PyDataType_ISNOTSWAPPED(descr))
#define PyArray_ISBOOL(obj) PyTypeNum_ISBOOL(PyArray_TYPE(obj))
#define PyArray_ISUNSIGNED(obj) PyTypeNum_ISUNSIGNED(PyArray_TYPE(obj))
#define PyArray_ISSIGNED(obj) PyTypeNum_ISSIGNED(PyArray_TYPE(obj))
#define PyArray_ISINTEGER(obj) PyTypeNum_ISINTEGER(PyArray_TYPE(obj))
#define PyArray_ISFLOAT(obj) PyTypeNum_ISFLOAT(PyArray_TYPE(obj))
#define PyArray_ISNUMBER(obj) PyTypeNum_ISNUMBER(PyArray_TYPE(obj))
#define PyArray_ISSTRING(obj) PyTypeNum_ISSTRING(PyArray_TYPE(obj))
#define PyArray_ISCOMPLEX(obj) PyTypeNum_ISCOMPLEX(PyArray_TYPE(obj))
#define PyArray_ISFLEXIBLE(obj) PyTypeNum_ISFLEXIBLE(PyArray_TYPE(obj))
#define PyArray_ISOBJECT(obj) PyTypeNum_ISOBJECT(PyArray_TYPE(obj))
#define PyArray_ISVARIABLE(obj) PyTypeNum_ISFLEXIBLE(PyArray_TYPE(obj))
#define PyArray_SAFEALIGNEDCOPY(obj) \
    (PyArray_ISALIGNED(obj) && !PyArray_ISVARIABLE(obj))

#endif
