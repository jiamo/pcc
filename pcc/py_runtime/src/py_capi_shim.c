/* Minimal pcc-native CPython C-API shim for extension import smoke tests.
 *
 * This is deliberately narrow: it supports PyModuleDef/PyMethodDef modules
 * exposing common PyCFunction calling conventions over pcc-native objects. It
 * does not claim CPython binary object-layout parity.
 */

#include "py_internal.h"
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef long Py_ssize_t;
typedef long Py_hash_t;
typedef uint8_t Py_UCS1;
typedef uint16_t Py_UCS2;
typedef uint32_t Py_UCS4;
typedef PyObject *(*PyCFunction)(PyObject *, PyObject *);
typedef PyObject *(*PyCFunctionWithKeywords)(PyObject *, PyObject *, PyObject *);
typedef PyObject *(*PyCFunctionFast)(
    PyObject *, PyObject *const *, Py_ssize_t
);
typedef PyObject *(*PyCFunctionFastWithKeywords)(
    PyObject *, PyObject *const *, Py_ssize_t, PyObject *
);
typedef PyObject *(*vectorcallfunc)(
    PyObject *, PyObject *const *, size_t, PyObject *
);
typedef void (*PyCapsule_Destructor)(PyObject *);
typedef int (*visitproc)(PyObject *, void *);
typedef int (*traverseproc)(PyObject *, visitproc, void *);
typedef int PyGILState_STATE;

int pcc_capi_visit_slot(PyObject **slot, visitproc visit, void *arg);

typedef struct {
    double real;
    double imag;
} Py_complex;

typedef struct bufferinfo {
    void *buf;
    PyObject *obj;
    Py_ssize_t len;
    Py_ssize_t itemsize;
    int readonly;
    int ndim;
    char *format;
    Py_ssize_t *shape;
    Py_ssize_t *strides;
    Py_ssize_t *suboffsets;
    void *internal;
} Py_buffer;

typedef struct PyMethodDef {
    const char *ml_name;
    PyCFunction ml_meth;
    int ml_flags;
    const char *ml_doc;
} PyMethodDef;

typedef struct PyModuleDef_Base {
    PyObject *ob_base;
    PyObject *m_init;
    Py_ssize_t m_index;
    PyObject *m_copy;
} PyModuleDef_Base;

typedef struct PyModuleDef_Slot {
    int slot;
    void *value;
} PyModuleDef_Slot;

/* Must match utils/fake_libc_include/Python.h's PyModuleDef exactly (the
 * extension compiles against that), so the shim can read m_slots for multi-phase
 * init (numpy's _multiarray_umath uses Py_mod_exec). */
typedef struct PyModuleDef {
    PyModuleDef_Base m_base;
    const char *m_name;
    const char *m_doc;
    Py_ssize_t m_size;
    PyMethodDef *m_methods;
    PyModuleDef_Slot *m_slots;
    void *m_traverse;
    void *m_clear;
    void *m_free;
} PyModuleDef;

#define PCC_Py_mod_create 1
#define PCC_Py_mod_exec 2

#define METH_VARARGS 0x0001
#define METH_KEYWORDS 0x0002
#define METH_NOARGS 0x0004
#define METH_O 0x0008
#define METH_FASTCALL 0x0080
#define PCC_FUNC_SIGNATURE_MAGIC "__pcc_func_signature_v1__"
#define PCC_FUNC_KIND_VARARGS 3
#define PCC_FUNC_KIND_VARKW 4
#define PyBUF_WRITABLE 0x0001
#define PyBUF_FORMAT 0x0004
#define PyBUF_ND 0x0008
#define PyBUF_STRIDES (0x0010 | PyBUF_ND)
#define PyBUF_READ 0x0100
#define PyBUF_WRITE 0x0200
#define PY_VECTORCALL_ARGUMENTS_OFFSET (((size_t)1) << (8 * sizeof(size_t) - 1))

#define Py_LT 0
#define Py_LE 1
#define Py_EQ 2
#define Py_NE 3
#define Py_GT 4
#define Py_GE 5
#define Py_PRINT_RAW 1
#define PyUnicode_1BYTE_KIND 1
#define PyUnicode_2BYTE_KIND 2
#define PyUnicode_4BYTE_KIND 4

typedef struct PccBufferMeta {
    Py_ssize_t shape;
    Py_ssize_t strides;
} PccBufferMeta;

struct PyUnicodeWriter {
    char *data;
    Py_ssize_t length;
    Py_ssize_t capacity;
};
typedef struct PyUnicodeWriter PyUnicodeWriter;

void PyErr_SetString(PyObject *type, const char *message);
PyObject *PyErr_NoMemory(void);
PyObject *PyErr_Format(PyObject *type, const char *format, ...);
int PyErr_ExceptionMatches(PyObject *exc);
void PyErr_Clear(void);
void PyErr_Fetch(PyObject **ptype, PyObject **pvalue, PyObject **ptraceback);
void PyErr_Restore(PyObject *type, PyObject *value, PyObject *traceback);
long long PyLong_AsLongLong(PyObject *obj);
long PyLong_AsLong(PyObject *obj);
unsigned long PyLong_AsUnsignedLong(PyObject *obj);
unsigned long long PyLong_AsUnsignedLongLong(PyObject *obj);
PyObject *PyUnicode_AsEncodedString(PyObject *obj, const char *encoding, const char *errors);
const char *PyUnicode_AsUTF8AndSize(PyObject *obj, Py_ssize_t *size);
PyObject *PyUnicode_Substring(PyObject *str, Py_ssize_t start, Py_ssize_t end);
Py_ssize_t PyUnicode_GetLength(PyObject *obj);
PyObject *PyUnicode_FromFormatV(const char *format, va_list vargs);
PyObject *PyObject_Str(PyObject *obj);
PyObject *PyObject_Repr(PyObject *obj);
PyObject *PyObject_GetAttrString(PyObject *obj, const char *attr);
int PyObject_SetAttrString(PyObject *obj, const char *attr, PyObject *value);
PyObject *PyObject_CallMethod(PyObject *obj, const char *name, const char *format, ...);
int PyDict_Check(PyObject *obj);
PyObject *PyDict_Keys(PyObject *dict);
PyObject *PyDict_Values(PyObject *dict);
PyObject *PyDict_Items(PyObject *dict);
PyObject *PyObject_SelfIter(PyObject *obj);
PyObject *PyObject_GetIter(PyObject *obj);
PyObject *PyIter_Next(PyObject *obj);
int PyIter_NextItem(PyObject *iter, PyObject **item);
PyObject *PySequence_Tuple(PyObject *obj);
PyObject *PyNumber_Index(PyObject *obj);
PyObject *PyErr_Occurred(void);

static int pcc_capi_value_error_sentinel;
static int pcc_capi_type_error_sentinel;
static int pcc_capi_runtime_error_sentinel;
static int pcc_capi_key_error_sentinel;
static int pcc_capi_index_error_sentinel;
static int pcc_capi_attribute_error_sentinel;
static int pcc_capi_memory_error_sentinel;
static int pcc_capi_overflow_error_sentinel;
static int pcc_capi_system_error_sentinel;
static int pcc_capi_name_error_sentinel;
static int pcc_capi_notimplemented_error_sentinel;
static int pcc_capi_base_exception_sentinel;
static int pcc_capi_exception_sentinel;
static int pcc_capi_arithmetic_error_sentinel;
static int pcc_capi_lookup_error_sentinel;
static int pcc_capi_os_error_sentinel;
static int pcc_capi_assertion_error_sentinel;
static int pcc_capi_stop_iteration_sentinel;
static int pcc_capi_stop_async_iteration_sentinel;
static int pcc_capi_zero_division_error_sentinel;
static int pcc_capi_reference_error_sentinel;
static int pcc_capi_buffer_error_sentinel;
static int pcc_capi_import_error_sentinel;
static int pcc_capi_module_not_found_error_sentinel;
static int pcc_capi_import_warning_sentinel;
static int pcc_capi_floating_point_error_sentinel;
static int pcc_capi_recursion_error_sentinel;
static int pcc_capi_unicode_decode_error_sentinel;
static int pcc_capi_unicode_encode_error_sentinel;
static int pcc_capi_unicode_error_sentinel;
static int pcc_capi_warning_sentinel;
static int pcc_capi_user_warning_sentinel;
static int pcc_capi_runtime_warning_sentinel;
static int pcc_capi_deprecation_warning_sentinel;
static int pcc_capi_future_warning_sentinel;

PyObject *PyExc_ValueError = (PyObject *)&pcc_capi_value_error_sentinel;
PyObject *PyExc_TypeError = (PyObject *)&pcc_capi_type_error_sentinel;
PyObject *PyExc_RuntimeError = (PyObject *)&pcc_capi_runtime_error_sentinel;
PyObject *PyExc_KeyError = (PyObject *)&pcc_capi_key_error_sentinel;
PyObject *PyExc_IndexError = (PyObject *)&pcc_capi_index_error_sentinel;
PyObject *PyExc_AttributeError = (PyObject *)&pcc_capi_attribute_error_sentinel;
PyObject *PyExc_MemoryError = (PyObject *)&pcc_capi_memory_error_sentinel;
PyObject *PyExc_OverflowError = (PyObject *)&pcc_capi_overflow_error_sentinel;
PyObject *PyExc_SystemError = (PyObject *)&pcc_capi_system_error_sentinel;
PyObject *PyExc_NameError = (PyObject *)&pcc_capi_name_error_sentinel;
PyObject *PyExc_NotImplementedError = (PyObject *)&pcc_capi_notimplemented_error_sentinel;
PyObject *PyExc_BaseException = (PyObject *)&pcc_capi_base_exception_sentinel;
PyObject *PyExc_Exception = (PyObject *)&pcc_capi_exception_sentinel;
PyObject *PyExc_ArithmeticError = (PyObject *)&pcc_capi_arithmetic_error_sentinel;
PyObject *PyExc_LookupError = (PyObject *)&pcc_capi_lookup_error_sentinel;
PyObject *PyExc_OSError = (PyObject *)&pcc_capi_os_error_sentinel;
PyObject *PyExc_IOError = (PyObject *)&pcc_capi_os_error_sentinel;
PyObject *PyExc_AssertionError = (PyObject *)&pcc_capi_assertion_error_sentinel;
PyObject *PyExc_StopIteration = (PyObject *)&pcc_capi_stop_iteration_sentinel;
PyObject *PyExc_StopAsyncIteration = (PyObject *)&pcc_capi_stop_async_iteration_sentinel;
PyObject *PyExc_ZeroDivisionError = (PyObject *)&pcc_capi_zero_division_error_sentinel;
PyObject *PyExc_ReferenceError = (PyObject *)&pcc_capi_reference_error_sentinel;
PyObject *PyExc_BufferError = (PyObject *)&pcc_capi_buffer_error_sentinel;
PyObject *PyExc_ImportError = (PyObject *)&pcc_capi_import_error_sentinel;
PyObject *PyExc_ModuleNotFoundError =
    (PyObject *)&pcc_capi_module_not_found_error_sentinel;
PyObject *PyExc_ImportWarning = (PyObject *)&pcc_capi_import_warning_sentinel;
PyObject *PyExc_FloatingPointError = (PyObject *)&pcc_capi_floating_point_error_sentinel;
PyObject *PyExc_RecursionError = (PyObject *)&pcc_capi_recursion_error_sentinel;
PyObject *PyExc_UnicodeDecodeError = (PyObject *)&pcc_capi_unicode_decode_error_sentinel;
PyObject *PyExc_UnicodeEncodeError = (PyObject *)&pcc_capi_unicode_encode_error_sentinel;
PyObject *PyExc_UnicodeError = (PyObject *)&pcc_capi_unicode_error_sentinel;
PyObject *PyExc_Warning = (PyObject *)&pcc_capi_warning_sentinel;
PyObject *PyExc_UserWarning = (PyObject *)&pcc_capi_user_warning_sentinel;
PyObject *PyExc_RuntimeWarning = (PyObject *)&pcc_capi_runtime_warning_sentinel;
PyObject *PyExc_DeprecationWarning = (PyObject *)&pcc_capi_deprecation_warning_sentinel;
PyObject *PyExc_FutureWarning = (PyObject *)&pcc_capi_future_warning_sentinel;

static int pcc_capi_is_exact_type(PyObject *obj, int32_t tag) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == tag;
}

static PyObject *pcc_capi_bytearray_from_memory(const char *data, Py_ssize_t len) {
    if (len < 0) len = 0;
    size_t total = sizeof(PyByteArrayObject) + (size_t)len + 1;
    PyByteArrayObject *obj = (PyByteArrayObject *)pcc_gc_alloc(
        (int64_t)total,
        PY_TYPE_BYTEARRAY,
        0
    );
    if (obj == NULL) return NULL;
    obj->byte_len = (int64_t)len;
    if (len > 0 && data != NULL) {
        memcpy(obj->data, data, (size_t)len);
    }
    obj->data[len] = '\0';
    return (PyObject *)obj;
}

void Py_INCREF(PyObject *obj) {
    py_incref(obj);
}

void Py_DECREF(PyObject *obj) {
    py_decref(obj);
}

Py_ssize_t pcc_capi_refcnt(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    return (Py_ssize_t)pcc_refcount_load(&py_header(obj)->refcount);
}

void pcc_capi_set_refcnt(PyObject *obj, Py_ssize_t refcnt) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return;
    PyObjectHeader *h = py_header(obj);
    pcc_refcount_forget(&h->refcount);
#if PCC_REFCOUNT_STRATEGY == PCC_REFCOUNT_KIND_ATOMIC
    __atomic_store_n(&h->refcount, (int64_t)refcnt, __ATOMIC_RELEASE);
#else
    h->refcount = (int64_t)refcnt;
#endif
}

/* --- numpy host-symbol gap (B-P0-PKG no-libpython runtime-core scope, 2026-05-29).
 * CPython C-API symbols numpy's _core references that the host (pcc) must
 * provide. This batch is the cleanly-correct subset (genuine implementations,
 * NOT crash-stubs): tracemalloc no-ops, libc strtol wrappers, the Ellipsis
 * singleton. The deeper host symbols (PyType_Ready, PyVectorcall_*,
 * PyContextVar_*, builtin type objects, PyObject_New/NewVar) need the
 * object-model work and are tracked in
 * docs/investigations/python-no-libpython-numpy-build-pcc-capi-include-redirect.md
 * ("Runtime-core symbol scope"). */

/* tracemalloc is not modeled by pcc's GC; track/untrack are no-ops. CPython
 * returns 0 on success (and when tracing is disabled), so do we. */
int PyTraceMalloc_Track(unsigned int domain, uintptr_t ptr, size_t size) {
    (void)domain; (void)ptr; (void)size;
    return 0;
}

int PyTraceMalloc_Untrack(unsigned int domain, uintptr_t ptr) {
    (void)domain; (void)ptr;
    return 0;
}

/* CPython's strtol/strtoul wrappers. pcc carries no separate C-locale state on
 * this path, so the libc forms are the correct implementation. */
long PyOS_strtol(const char *str, char **ptr, int base) {
    return strtol(str, ptr, base);
}

unsigned long PyOS_strtoul(const char *str, char **ptr, int base) {
    return strtoul(str, ptr, base);
}

/* The Ellipsis singleton (`...`). pcc has no Ellipsis object; numpy references
 * the symbol for slice handling. A stable non-NULL sentinel satisfies identity
 * use and linking (mirrors the PyExc_* sentinel pattern above). */
static int pcc_capi_ellipsis_sentinel;
PyObject *Py_Ellipsis = (PyObject *)&pcc_capi_ellipsis_sentinel;

/* --- C-extension type bridge (B-P0-PKG runtime core, 2026-05-29).
 * Bridges a C-extension's static `PyTypeObject` onto pcc's `type_tag` object
 * model. pcc objects carry a 16-byte `PyObjectHeader{refcount,type_tag,flags}`,
 * NOT CPython's `{ob_refcnt,ob_type}` — so an instance cannot store an
 * `ob_type` pointer. Instead each readied C-ext type is assigned a DYNAMIC
 * `type_tag` (above the ~0..27 builtin enum) recorded in a registry, the tag is
 * cached in the type's `tp_version_tag` slot, instances are allocated carrying
 * that tag, and `Py_TYPE`/`PyObject_TypeCheck` route the tag back to the
 * `PyTypeObject*`. First cut: registration + generic alloc/new + exact-type
 * Py_TYPE; slot inheritance, subtype checks, and the type-call protocol are
 * follow-on. See docs/investigations/
 * python-no-libpython-numpy-build-pcc-capi-include-redirect.md ("PyType_Ready crux"). */
/* Layout mirror of fake_libc_include/Python.h `struct _typeobject` (the CPython
 * PyTypeObject the extension is compiled against). py_internal.h does not define
 * PyTypeObject (the shim works in pcc's object model), so the shim carries its
 * own faithful layout copy to read tp_* fields off an extension's static type.
 * Function/struct-pointer slots are `void *` (same 8-byte size) since the shim
 * never calls them; scalar slots keep their real types so tp_basicsize /
 * tp_itemsize / tp_flags / tp_version_tag land at the SAME offsets as the
 * canonical struct. MUST stay in sync with that struct's prefix through
 * tp_version_tag (layout-drift class — see AGENTS.md §10). */
typedef struct _pcc_capi_typeobject {
    PyObjectHeader ob_base;        /* PyVarObject.ob_base */
    void *ob_type;                 /* PyVarObject.ob_type (CPython-compat slot) */
    Py_ssize_t ob_size;            /* PyVarObject.ob_size */
    const char *tp_name;
    Py_ssize_t tp_basicsize, tp_itemsize;
    void *tp_dealloc;
    Py_ssize_t tp_vectorcall_offset;
    void *tp_getattr, *tp_setattr, *tp_as_async, *tp_repr;
    void *tp_as_number, *tp_as_sequence, *tp_as_mapping;
    void *tp_hash, *tp_call, *tp_str, *tp_getattro, *tp_setattro, *tp_as_buffer;
    unsigned long tp_flags;
    const char *tp_doc;
    void *tp_traverse, *tp_clear, *tp_richcompare;
    Py_ssize_t tp_weaklistoffset;
    void *tp_iter, *tp_iternext;
    void *tp_methods, *tp_members, *tp_getset, *tp_base, *tp_dict;
    void *tp_descr_get, *tp_descr_set;
    Py_ssize_t tp_dictoffset;
    void *tp_init, *tp_alloc, *tp_new, *tp_free, *tp_is_gc;
    void *tp_bases, *tp_mro, *tp_cache, *tp_subclasses, *tp_weaklist, *tp_del;
    unsigned int tp_version_tag;
    void *tp_finalize;
} PyTypeObject;

/* Layout mirror of fake_libc_include/Python.h PyNumberMethods. Only the
 * conversion slots are called here, but every preceding field is retained so
 * nb_int/nb_float/nb_index land at the extension ABI's exact offsets. */
typedef struct PccCapiNumberMethods {
    void *nb_add, *nb_subtract, *nb_multiply, *nb_remainder, *nb_divmod;
    void *nb_power;
    void *nb_negative, *nb_positive, *nb_absolute;
    void *nb_bool;
    void *nb_invert;
    void *nb_lshift, *nb_rshift, *nb_and, *nb_xor, *nb_or;
    void *nb_int, *nb_reserved, *nb_float;
    void *nb_inplace_add, *nb_inplace_subtract, *nb_inplace_multiply;
    void *nb_inplace_remainder;
    void *nb_inplace_power;
    void *nb_inplace_lshift, *nb_inplace_rshift, *nb_inplace_and;
    void *nb_inplace_xor, *nb_inplace_or;
    void *nb_floor_divide, *nb_true_divide;
    void *nb_inplace_floor_divide, *nb_inplace_true_divide;
    void *nb_index;
    void *nb_matrix_multiply, *nb_inplace_matrix_multiply;
} PccCapiNumberMethods;

typedef struct PccCapiSequenceMethods {
    void *sq_length, *sq_concat, *sq_repeat, *sq_item;
    void *was_sq_slice, *sq_ass_item, *was_sq_ass_slice, *sq_contains;
    void *sq_inplace_concat, *sq_inplace_repeat;
} PccCapiSequenceMethods;

typedef struct PccCapiMappingMethods {
    void *mp_length, *mp_subscript, *mp_ass_subscript;
} PccCapiMappingMethods;

typedef struct PyType_Slot {
    int slot;
    void *pfunc;
} PyType_Slot;

typedef struct PyType_Spec {
    const char *name;
    int basicsize;
    int itemsize;
    unsigned int flags;
    PyType_Slot *slots;
} PyType_Spec;

typedef struct PccCapiMemberDef {
    const char *name;
    int type;
    Py_ssize_t offset;
    int flags;
    const char *doc;
} PccCapiMemberDef;

typedef struct PccCapiGetSetDef {
    const char *name;
    PyObject *(*get)(PyObject *, void *);
    int (*set)(PyObject *, PyObject *, void *);
    const char *doc;
    void *closure;
} PccCapiGetSetDef;

#define PCC_CAPI_T_SHORT 0
#define PCC_CAPI_T_INT 1
#define PCC_CAPI_T_LONG 2
#define PCC_CAPI_T_FLOAT 3
#define PCC_CAPI_T_DOUBLE 4
#define PCC_CAPI_T_STRING 5
#define PCC_CAPI_T_OBJECT 6
#define PCC_CAPI_T_CHAR 7
#define PCC_CAPI_T_BYTE 8
#define PCC_CAPI_T_UBYTE 9
#define PCC_CAPI_T_USHORT 10
#define PCC_CAPI_T_UINT 11
#define PCC_CAPI_T_ULONG 12
#define PCC_CAPI_T_STRING_INPLACE 13
#define PCC_CAPI_T_BOOL 14
#define PCC_CAPI_T_OBJECT_EX 16
#define PCC_CAPI_T_LONGLONG 17
#define PCC_CAPI_T_ULONGLONG 18
#define PCC_CAPI_T_PYSSIZET 19
#define PCC_CAPI_T_NONE 20

/* tp_flags READY bit (mirrors fake_libc_include/Python.h Py_TPFLAGS_READY). */
#ifndef Py_TPFLAGS_READY
#define Py_TPFLAGS_READY (1UL << 12)
#endif
#ifndef Py_TPFLAGS_HAVE_VECTORCALL
#define Py_TPFLAGS_HAVE_VECTORCALL (1UL << 11)
#endif
#ifndef Py_TPFLAGS_HAVE_GC
#define Py_TPFLAGS_HAVE_GC (1UL << 14)
#endif
#ifndef PCC_TPFLAGS_MANAGED_DEALLOC
#define PCC_TPFLAGS_MANAGED_DEALLOC (1UL << 62)
#endif

#define Py_tp_base 48
#define Py_tp_call 50
#define Py_tp_clear 51
#define Py_tp_dealloc 52
#define Py_tp_doc 56
#define Py_tp_hash 59
#define Py_tp_init 60
#define Py_tp_iter 62
#define Py_tp_iternext 63
#define Py_tp_methods 64
#define Py_tp_new 65
#define Py_tp_repr 66
#define Py_tp_richcompare 67
#define Py_tp_str 70
#define Py_tp_traverse 71
#define Py_tp_members 72
#define Py_tp_getset 73

#define PCC_CAPI_CEXT_TAG_BASE 0x10000
#define PCC_CAPI_CEXT_TAG_MAX 1024
static PyTypeObject *pcc_capi_cext_types[PCC_CAPI_CEXT_TAG_MAX];
static PyObject *pcc_capi_cext_type_modules[PCC_CAPI_CEXT_TAG_MAX];
static int32_t pcc_capi_cext_type_count;

/* Builtin type objects referenced by C extensions. pcc objects carry a
 * type_tag, not an ob_type pointer, so these are stable RECOGNITION TOKENS:
 * extensions compare `Py_TYPE(o) == &PyLong_Type` and use the addresses as
 * base/identity, and `pcc_capi_type` maps each builtin tag to the matching
 * token below. tp_name lets a later type repr print the name; tp_flags carries
 * READY. Defined here because the no-libpython runtime (unlike the libpython
 * bridge, which dlsym's them) has no other source. */
#define PCC_CAPI_TYPEOBJ(sym, nm) \
    PyTypeObject sym = {.ob_base = {1, 0, 0}, .tp_name = nm, \
                        .tp_flags = Py_TPFLAGS_READY}
PCC_CAPI_TYPEOBJ(PyType_Type, "type");
PCC_CAPI_TYPEOBJ(PyBaseObject_Type, "object");
PCC_CAPI_TYPEOBJ(PyTuple_Type, "tuple");
PCC_CAPI_TYPEOBJ(PyList_Type, "list");
PCC_CAPI_TYPEOBJ(PyDict_Type, "dict");
PCC_CAPI_TYPEOBJ(PyUnicode_Type, "str");
PCC_CAPI_TYPEOBJ(PyLong_Type, "int");
PCC_CAPI_TYPEOBJ(PyFloat_Type, "float");
PCC_CAPI_TYPEOBJ(PyBool_Type, "bool");
PCC_CAPI_TYPEOBJ(PyBytes_Type, "bytes");
PCC_CAPI_TYPEOBJ(PyByteArray_Type, "bytearray");
PCC_CAPI_TYPEOBJ(PySet_Type, "set");
PCC_CAPI_TYPEOBJ(PyFrozenSet_Type, "frozenset");
PCC_CAPI_TYPEOBJ(PySlice_Type, "slice");
PCC_CAPI_TYPEOBJ(PyComplex_Type, "complex");
PCC_CAPI_TYPEOBJ(PyModule_Type, "module");
PCC_CAPI_TYPEOBJ(PyFunction_Type, "function");
PCC_CAPI_TYPEOBJ(PyCFunction_Type, "builtin_function_or_method");
PCC_CAPI_TYPEOBJ(PyMemberDescr_Type, "member_descriptor");
PCC_CAPI_TYPEOBJ(PyGetSetDescr_Type, "getset_descriptor");
PCC_CAPI_TYPEOBJ(PyMethodDescr_Type, "method_descriptor");
PCC_CAPI_TYPEOBJ(PyDictProxy_Type, "mappingproxy");
PCC_CAPI_TYPEOBJ(PyMemoryView_Type, "memoryview");
#undef PCC_CAPI_TYPEOBJ

static PyObject *pcc_capi_builtin_type_token(PyObject *value) {
    switch (py_builtin_type_class_tag(value)) {
        case PY_TYPE_BOOL: return (PyObject *)&PyBool_Type;
        case PY_TYPE_INT: return (PyObject *)&PyLong_Type;
        case PY_TYPE_FLOAT: return (PyObject *)&PyFloat_Type;
        case PY_TYPE_STR: return (PyObject *)&PyUnicode_Type;
        case PY_TYPE_LIST: return (PyObject *)&PyList_Type;
        case PY_TYPE_DICT: return (PyObject *)&PyDict_Type;
        case PY_TYPE_TUPLE: return (PyObject *)&PyTuple_Type;
        case PY_TYPE_SET: return (PyObject *)&PySet_Type;
        case PY_TYPE_CLASS: return (PyObject *)&PyType_Type;
        case PY_TYPE_COMPLEX: return (PyObject *)&PyComplex_Type;
        case PY_TYPE_BYTES: return (PyObject *)&PyBytes_Type;
        case PY_TYPE_BYTEARRAY: return (PyObject *)&PyByteArray_Type;
        case PY_TYPE_MEMORYVIEW: return (PyObject *)&PyMemoryView_Type;
        case -1: return (PyObject *)&PyBaseObject_Type;
        default: return value;
    }
}

static PyObject *pcc_capi_prepare_call_args(PyObject *args) {
    if (args == NULL || PY_IS_TAGGED_INT(args)
        || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "C method args must be a tuple");
        return NULL;
    }
    int64_t n = py_tuple_len(args);
    int changed = 0;
    for (int64_t i = 0; i < n; i++) {
        PyObject *item = py_tuple_get(args, i);
        if (item == NULL) return NULL;
        if (pcc_capi_builtin_type_token(item) != item) changed = 1;
        py_decref(item);
    }
    if (!changed) {
        py_incref(args);
        return args;
    }
    PyObject *out = py_tuple_new(n);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < n; i++) {
        PyObject *item = py_tuple_get(args, i);
        if (item == NULL) {
            py_decref(out);
            return NULL;
        }
        PyObject *translated = pcc_capi_builtin_type_token(item);
        py_tuple_set_item(out, i, translated);
        py_decref(item);
    }
    return out;
}

/* Assign (or fetch the cached) dynamic pcc type_tag for a C-ext type. The tag
 * is stashed in tp_version_tag (0 = unassigned); the registry maps it back. */
static int32_t pcc_capi_cext_tag_for(PyTypeObject *type) {
    if (type == NULL) return PY_TYPE_NONE;
    if (type->tp_version_tag != 0) return (int32_t)type->tp_version_tag;
    if (pcc_capi_cext_type_count >= PCC_CAPI_CEXT_TAG_MAX) return PY_TYPE_NONE;
    int32_t tag = PCC_CAPI_CEXT_TAG_BASE + pcc_capi_cext_type_count;
    pcc_capi_cext_types[pcc_capi_cext_type_count] = type;
    pcc_capi_cext_type_count++;
    type->tp_version_tag = (unsigned int)tag;
    return tag;
}

PyObject *PyType_GenericAlloc(PyTypeObject *type, Py_ssize_t nitems);

int PyType_Ready(PyTypeObject *type) {
    if (type == NULL) return -1;
    if (type->tp_flags & Py_TPFLAGS_READY) return 0;
    if (type->tp_alloc == NULL) {
        PyTypeObject *base = (PyTypeObject *)type->tp_base;
        if (base != NULL && base->tp_alloc != NULL) {
            type->tp_alloc = base->tp_alloc;
        } else {
            type->tp_alloc = PyType_GenericAlloc;
        }
    }
    pcc_capi_cext_tag_for(type);
    type->tp_flags |= Py_TPFLAGS_READY;
    return 0;
}

int64_t pcc_capi_is_cext_type_tag(int64_t type_tag) {
    int64_t offset = type_tag - PCC_CAPI_CEXT_TAG_BASE;
    return offset >= 0 && offset < pcc_capi_cext_type_count;
}

static PyTypeObject *pcc_capi_cext_type_for_object(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return NULL;
    int64_t offset = (
        (int64_t)py_header(o)->type_tag - PCC_CAPI_CEXT_TAG_BASE
    );
    if (offset < 0 || offset >= pcc_capi_cext_type_count) return NULL;
    return pcc_capi_cext_types[offset];
}

static int pcc_capi_is_seqiter(PyObject *obj);
static PyObject *pcc_capi_seqiter_next(PyObject *obj);
void PyErr_SetNone(PyObject *type);

PyObject *pcc_capi_cext_object_iter(PyObject *o) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL || type->tp_iter == NULL) return NULL;
    PyObject *(*iter_slot)(PyObject *) = (
        PyObject *(*)(PyObject *)
    )type->tp_iter;
    return iter_slot(o);
}

/* Render a C-extension object (e.g. a numpy scalar returned by ndarray element
 * access) by driving its own tp_repr slot directly — mirrors
 * pcc_capi_cext_object_iter. Returns NULL when the type has no tp_repr, letting
 * the caller keep the <object tag=N> fallback. Do NOT route this through
 * PyObject_Repr, which falls back to pcc's py_obj_repr and raises "object
 * cannot be converted to repr" for a foreign cext object. */
PyObject *pcc_capi_cext_object_repr(PyObject *o) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL || type->tp_repr == NULL) return NULL;
    PyObject *(*repr_slot)(PyObject *) = (
        PyObject *(*)(PyObject *)
    )type->tp_repr;
    return repr_slot(o);
}

PyObject *pcc_capi_cext_object_next(PyObject *o) {
    PyObject *result = NULL;
    if (pcc_capi_is_seqiter(o)) {
        result = pcc_capi_seqiter_next(o);
    } else {
        PyTypeObject *type = pcc_capi_cext_type_for_object(o);
        if (type == NULL || type->tp_iternext == NULL) return NULL;
        PyObject *(*next_slot)(PyObject *) = (
            PyObject *(*)(PyObject *)
        )type->tp_iternext;
        result = next_slot(o);
    }
    if (result == NULL && !py_err_occurred()) {
        PyErr_SetNone(PyExc_StopIteration);
    }
    return result;
}

int64_t pcc_capi_cext_object_is_iterator(PyObject *o) {
    if (pcc_capi_is_seqiter(o)) return 1;
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    return type != NULL && type->tp_iternext != NULL;
}

PyObject *pcc_capi_cext_object_getitem(PyObject *o, PyObject *key) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL) return NULL;
    PccCapiMappingMethods *mapping = (
        PccCapiMappingMethods *
    )type->tp_as_mapping;
    if (mapping != NULL && mapping->mp_subscript != NULL) {
        PyObject *(*subscript)(PyObject *, PyObject *) = (
            PyObject *(*)(PyObject *, PyObject *)
        )mapping->mp_subscript;
        return subscript(o, key);
    }
    PccCapiSequenceMethods *sequence = (
        PccCapiSequenceMethods *
    )type->tp_as_sequence;
    if (sequence != NULL && sequence->sq_item != NULL) {
        Py_ssize_t index = (Py_ssize_t)PyLong_AsLong(key);
        if (py_err_occurred()) return NULL;
        PyObject *(*item)(PyObject *, Py_ssize_t) = (
            PyObject *(*)(PyObject *, Py_ssize_t)
        )sequence->sq_item;
        return item(o, index);
    }
    return NULL;
}

static int pcc_capi_cext_vectorcall_slot(
    PyObject *callable,
    vectorcallfunc *result
) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(callable);
    if (result != NULL) *result = NULL;
    if (
        type == NULL
        || (type->tp_flags & Py_TPFLAGS_HAVE_VECTORCALL) == 0
        || type->tp_vectorcall_offset <= 0
        || type->tp_vectorcall_offset
            > type->tp_basicsize - (Py_ssize_t)sizeof(vectorcallfunc)
    ) {
        return 0;
    }
    if (result != NULL) {
        memcpy(
            result,
            (char *)callable + type->tp_vectorcall_offset,
            sizeof(vectorcallfunc)
        );
    }
    return 1;
}

static PyObject **pcc_capi_object_dict_slot(
    PyObject *o,
    PyTypeObject *type
) {
    if (o == NULL || type == NULL || type->tp_dictoffset <= 0) return NULL;
    if (type->tp_dictoffset + (Py_ssize_t)sizeof(PyObject *)
        > type->tp_basicsize) {
        return NULL;
    }
    return (PyObject **)((char *)o + type->tp_dictoffset);
}

int64_t pcc_capi_cext_object_is_callable(PyObject *callable) {
    if (callable == NULL || PY_IS_TAGGED_INT(callable)) return 0;
    int64_t offset = (
        (int64_t)py_header(callable)->type_tag - PCC_CAPI_CEXT_TAG_BASE
    );
    if (offset < 0 || offset >= pcc_capi_cext_type_count) return 0;
    PyTypeObject *type = pcc_capi_cext_types[offset];
    return type != NULL && type->tp_call != NULL;
}

PyObject *pcc_capi_call_cext_object(
    PyObject *callable,
    PyObject *args,
    PyObject *kwargs
) {
    if (!pcc_capi_cext_object_is_callable(callable)) {
        PyErr_SetString(PyExc_TypeError, "C extension object is not callable");
        return NULL;
    }
    int64_t offset = (
        (int64_t)py_header(callable)->type_tag - PCC_CAPI_CEXT_TAG_BASE
    );
    PyTypeObject *type = pcc_capi_cext_types[offset];
    PyObject *(*tp_call)(PyObject *, PyObject *, PyObject *) = (
        PyObject *(*)(PyObject *, PyObject *, PyObject *)
    )type->tp_call;
    PyObject *call_kwargs = kwargs == py_None ? NULL : kwargs;
    PyObject *result = tp_call(callable, args, call_kwargs);
    if (result == NULL && !py_err_occurred()) {
        PyErr_SetString(
            PyExc_RuntimeError,
            "C extension tp_call returned NULL without setting an exception"
        );
    }
    return result;
}

int64_t pcc_capi_dealloc_cext_object(PyObject *o, int64_t type_tag) {
    int64_t offset = type_tag - PCC_CAPI_CEXT_TAG_BASE;
    if (offset < 0 || offset >= pcc_capi_cext_type_count) return 0;
    PyTypeObject *type = pcc_capi_cext_types[offset];
    if (
        type != NULL
        && (type->tp_flags & PCC_TPFLAGS_MANAGED_DEALLOC) != 0
        && type->tp_dealloc != NULL
    ) {
        void (*dealloc)(PyObject *) = (void (*)(PyObject *))type->tp_dealloc;
        dealloc(o);
    }
    pcc_gc_free_object_memory(o);
    return 1;
}

PyObject *PyType_GenericNew(PyTypeObject *type, PyObject *args, PyObject *kwds);

PyObject *PyType_FromSpec(PyType_Spec *spec) {
    if (spec == NULL || spec->name == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyType_Spec");
        return NULL;
    }
    PyTypeObject *type = (PyTypeObject *)calloc(1, sizeof(PyTypeObject));
    if (type == NULL) return PyErr_NoMemory();
    type->ob_base.refcount = 1;
    type->ob_base.type_tag = PY_TYPE_NONE;
    type->ob_type = &PyType_Type;
    type->tp_name = spec->name;
    type->tp_basicsize = (Py_ssize_t)spec->basicsize;
    type->tp_itemsize = (Py_ssize_t)spec->itemsize;
    type->tp_flags = spec->flags;

    for (PyType_Slot *slot = spec->slots; slot != NULL && slot->slot != 0; slot++) {
        switch (slot->slot) {
            case Py_tp_base: type->tp_base = slot->pfunc; break;
            case Py_tp_call: type->tp_call = slot->pfunc; break;
            case Py_tp_clear: type->tp_clear = slot->pfunc; break;
            case Py_tp_dealloc: type->tp_dealloc = slot->pfunc; break;
            case Py_tp_doc: type->tp_doc = (const char *)slot->pfunc; break;
            case Py_tp_hash: type->tp_hash = slot->pfunc; break;
            case Py_tp_init: type->tp_init = slot->pfunc; break;
            case Py_tp_iter: type->tp_iter = slot->pfunc; break;
            case Py_tp_iternext: type->tp_iternext = slot->pfunc; break;
            case Py_tp_methods: type->tp_methods = slot->pfunc; break;
            case Py_tp_new: type->tp_new = slot->pfunc; break;
            case Py_tp_repr: type->tp_repr = slot->pfunc; break;
            case Py_tp_richcompare: type->tp_richcompare = slot->pfunc; break;
            case Py_tp_str: type->tp_str = slot->pfunc; break;
            case Py_tp_traverse: type->tp_traverse = slot->pfunc; break;
            case Py_tp_members: type->tp_members = slot->pfunc; break;
            case Py_tp_getset: type->tp_getset = slot->pfunc; break;
            default: break;
        }
    }
    if (type->tp_new == NULL) type->tp_new = PyType_GenericNew;
    if (type->tp_alloc == NULL) type->tp_alloc = PyType_GenericAlloc;
    if (PyType_Ready(type) < 0) {
        free(type);
        return NULL;
    }
    return (PyObject *)type;
}

PyObject *PyType_FromModuleAndSpec(
    PyObject *module,
    PyType_Spec *spec,
    PyObject *bases
) {
    (void)bases;
    if (module == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL module for heap type");
        return NULL;
    }
    PyObject *type_obj = PyType_FromSpec(spec);
    if (type_obj == NULL) return NULL;
    PyTypeObject *type = (PyTypeObject *)type_obj;
    int32_t tag = pcc_capi_cext_tag_for(type);
    int32_t offset = tag - PCC_CAPI_CEXT_TAG_BASE;
    if (offset < 0 || offset >= PCC_CAPI_CEXT_TAG_MAX) {
        PyErr_SetString(PyExc_RuntimeError, "heap type registry exhausted");
        return NULL;
    }
    py_incref(module);
    if (
        !PY_IS_TAGGED_INT(module)
        && pcc_gc_object_is_known_no_lock(module) != 0
        && (py_header_flags_load(py_header(module)) & PY_FLAG_GC_PINNED) == 0
    ) {
        pcc_gc_pin(module);
    }
    pcc_capi_cext_type_modules[offset] = module;
    return type_obj;
}

PyObject *PyType_GetModule(PyTypeObject *type) {
    if (type == NULL || type->tp_version_tag < PCC_CAPI_CEXT_TAG_BASE) {
        PyErr_SetString(PyExc_TypeError, "type has no associated module");
        return NULL;
    }
    int32_t offset = (int32_t)type->tp_version_tag - PCC_CAPI_CEXT_TAG_BASE;
    if (
        offset < 0
        || offset >= pcc_capi_cext_type_count
        || pcc_capi_cext_type_modules[offset] == NULL
    ) {
        PyErr_SetString(PyExc_TypeError, "type has no associated module");
        return NULL;
    }
    return pcc_capi_cext_type_modules[offset];
}

PyObject *PyType_GenericAlloc(PyTypeObject *type, Py_ssize_t nitems) {
    if (type == NULL) return NULL;
    int32_t tag = pcc_capi_cext_tag_for(type);
    /* Minimum body must hold the header + the CPython-compat ob_type slot. */
    Py_ssize_t minsz = (Py_ssize_t)(sizeof(PyObjectHeader) + sizeof(void *));
    Py_ssize_t basic = type->tp_basicsize;
    if (basic < minsz) basic = minsz;
    int64_t size = (int64_t)basic + (int64_t)nitems * (int64_t)type->tp_itemsize;
    /* pcc_gc_alloc calloc's the block and sets refcount=1 + type_tag, so the
     * extension's own fields start zeroed (matches CPython tp_alloc). */
    PyObject *obj = pcc_gc_alloc(size, tag, 0);
    if (obj != NULL) {
        /* Set the ob_type slot (offset = end of PyObjectHeader) so numpy's
         * direct `obj->ob_type` reads resolve to the type. */
        *(PyTypeObject **)((char *)obj + sizeof(PyObjectHeader)) = type;
    }
    return obj;
}

PyObject *PyType_GenericNew(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    (void)args;
    (void)kwds;
    return PyType_GenericAlloc(type, 0);
}

static int pcc_capi_is_type_object(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    if (o == (PyObject *)&PyType_Type
        || o == (PyObject *)&PyBaseObject_Type
        || o == (PyObject *)&PyTuple_Type
        || o == (PyObject *)&PyList_Type
        || o == (PyObject *)&PyDict_Type
        || o == (PyObject *)&PyUnicode_Type
        || o == (PyObject *)&PyLong_Type
        || o == (PyObject *)&PyFloat_Type
        || o == (PyObject *)&PyBool_Type
        || o == (PyObject *)&PyBytes_Type
        || o == (PyObject *)&PyByteArray_Type
        || o == (PyObject *)&PySet_Type
        || o == (PyObject *)&PyFrozenSet_Type
        || o == (PyObject *)&PySlice_Type
        || o == (PyObject *)&PyComplex_Type
        || o == (PyObject *)&PyModule_Type
        || o == (PyObject *)&PyFunction_Type
        || o == (PyObject *)&PyCFunction_Type
        || o == (PyObject *)&PyMemberDescr_Type
        || o == (PyObject *)&PyGetSetDescr_Type
        || o == (PyObject *)&PyMethodDescr_Type
        || o == (PyObject *)&PyDictProxy_Type
        || o == (PyObject *)&PyMemoryView_Type) {
        return 1;
    }
    for (int32_t i = 0; i < pcc_capi_cext_type_count; i++) {
        if (o == (PyObject *)pcc_capi_cext_types[i]) return 1;
    }
    return 0;
}

int64_t pcc_capi_is_type_object_value(PyObject *value) {
    return pcc_capi_is_type_object(value);
}

static PyObject *pcc_capi_type_method_descriptor(PyMethodDef *method);
static PyObject *pcc_capi_type_getset_descriptor(PccCapiGetSetDef *getset);
static PyObject *pcc_capi_type_member_descriptor(PccCapiMemberDef *member);
static PyObject *pcc_capi_type_richcompare_descriptor(
    PyTypeObject *type,
    const char *name
);

PyObject *pcc_capi_type_object_getattr(PyObject *type_object, const char *name) {
    if (!pcc_capi_is_type_object(type_object) || name == NULL) return NULL;
    if (strcmp(name, "__name__") == 0) {
        const char *qualified = ((PyTypeObject *)type_object)->tp_name;
        if (qualified == NULL) qualified = "";
        const char *short_name = strrchr(qualified, '.');
        if (short_name != NULL) short_name++;
        else short_name = qualified;
        return py_str_new(short_name, (int64_t)strlen(short_name));
    }
    if (strcmp(name, "__module__") == 0) {
        const char *qualified = ((PyTypeObject *)type_object)->tp_name;
        if (qualified == NULL) qualified = "";
        const char *separator = strrchr(qualified, '.');
        if (separator == NULL) return py_str_new("builtins", 8);
        return py_str_new(qualified, (int64_t)(separator - qualified));
    }

    for (
        PyTypeObject *type = (PyTypeObject *)type_object;
        type != NULL;
        type = (PyTypeObject *)type->tp_base
    ) {
        PccCapiGetSetDef *getset = (PccCapiGetSetDef *)type->tp_getset;
        for (; getset != NULL && getset->name != NULL; getset++) {
            if (strcmp(name, getset->name) == 0) {
                return pcc_capi_type_getset_descriptor(getset);
            }
        }
        PccCapiMemberDef *member = (PccCapiMemberDef *)type->tp_members;
        for (; member != NULL && member->name != NULL; member++) {
            if (strcmp(name, member->name) == 0) {
                return pcc_capi_type_member_descriptor(member);
            }
        }
        PyMethodDef *methods = (PyMethodDef *)type->tp_methods;
        for (
            PyMethodDef *method = methods;
            method != NULL && method->ml_name != NULL;
            method++
        ) {
            if (strcmp(name, method->ml_name) == 0) {
                return pcc_capi_type_method_descriptor(method);
            }
        }
        if (type->tp_richcompare != NULL) {
            PyObject *descriptor = pcc_capi_type_richcompare_descriptor(
                type,
                name
            );
            if (descriptor != NULL || py_err_occurred()) return descriptor;
        }
    }
    return NULL;
}

int64_t pcc_capi_type_object_is_callable(PyObject *callable) {
    if (!pcc_capi_is_type_object(callable)) return 0;
    PyTypeObject *type = (PyTypeObject *)callable;
    return type->tp_version_tag >= PCC_CAPI_CEXT_TAG_BASE
        && type->tp_new != NULL;
}

PyObject *pcc_capi_call_type_object(
    PyObject *callable,
    PyObject *args,
    PyObject *kwargs
) {
    if (!pcc_capi_type_object_is_callable(callable)) {
        PyErr_SetString(PyExc_TypeError, "C extension type is not callable");
        return NULL;
    }
    PyTypeObject *type = (PyTypeObject *)callable;
    PyObject *call_kwargs = kwargs == py_None ? NULL : kwargs;
    PyObject *(*tp_new)(PyTypeObject *, PyObject *, PyObject *) = (
        PyObject *(*)(PyTypeObject *, PyObject *, PyObject *)
    )type->tp_new;
    PyObject *result = tp_new(type, args, call_kwargs);
    if (result == NULL) return NULL;
    if (type->tp_init != NULL) {
        int (*tp_init)(PyObject *, PyObject *, PyObject *) = (
            int (*)(PyObject *, PyObject *, PyObject *)
        )type->tp_init;
        if (tp_init(result, args, call_kwargs) != 0) {
            py_decref(result);
            return NULL;
        }
    }
    return result;
}

PyTypeObject *pcc_capi_type(PyObject *o) {
    if (o == NULL) return NULL;
    if (PY_IS_TAGGED_INT(o)) return &PyLong_Type;  /* immediate small ints */
    if (pcc_capi_is_type_object(o)) {
        /* Registered type objects may themselves have a custom metaclass.
         * Py_SET_TYPE and static PyVarObject_HEAD_INIT both populate the
         * compatibility ob_type slot; preserve it for TypeCheck instead of
         * flattening every class object to the builtin `type`. */
        PyTypeObject *type = (PyTypeObject *)o;
        return type->ob_type != NULL
            ? (PyTypeObject *)type->ob_type
            : &PyType_Type;
    }
    if (py_obj_is_slice(o)) return &PySlice_Type;
    int32_t tag = py_type_of(o);
    if (tag >= PCC_CAPI_CEXT_TAG_BASE
        && tag < PCC_CAPI_CEXT_TAG_BASE + pcc_capi_cext_type_count) {
        return pcc_capi_cext_types[tag - PCC_CAPI_CEXT_TAG_BASE];
    }
    switch (tag) {
    case PY_TYPE_BOOL: return &PyBool_Type;
    case PY_TYPE_INT: return &PyLong_Type;
    case PY_TYPE_FLOAT: return &PyFloat_Type;
    case PY_TYPE_STR: return &PyUnicode_Type;
    case PY_TYPE_LIST: return &PyList_Type;
    case PY_TYPE_DICT: return &PyDict_Type;
    case PY_TYPE_TUPLE: return &PyTuple_Type;
    case PY_TYPE_SET: return &PySet_Type;
    case PY_TYPE_COMPLEX: return &PyComplex_Type;
    case PY_TYPE_BYTES: return &PyBytes_Type;
    case PY_TYPE_BYTEARRAY: return &PyByteArray_Type;
    case PY_TYPE_FUNC:
        return ((PyFuncObject *)o)->capi_method != NULL
            ? &PyCFunction_Type
            : &PyFunction_Type;
    default: return NULL;
    }
}

PyTypeObject **pcc_capi_type_addr(PyObject *o) {
    static PyTypeObject *tagged_int_type = &PyLong_Type;
    static PyTypeObject *unknown_type = NULL;
    static PyTypeObject *builtin_types[256];
    if (o == NULL) return &unknown_type;
    if (PY_IS_TAGGED_INT(o)) return &tagged_int_type;
    if (pcc_capi_is_type_object(o)) {
        PyTypeObject *type = (PyTypeObject *)o;
        if (type->ob_type == NULL) type->ob_type = &PyType_Type;
        return (PyTypeObject **)&type->ob_type;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_FUNC) {
        static PyTypeObject *func_type = NULL;
        func_type = pcc_capi_type(o);
        return &func_type;
    }
    if (
        tag >= PCC_CAPI_CEXT_TAG_BASE
        && tag < PCC_CAPI_CEXT_TAG_BASE + pcc_capi_cext_type_count
    ) {
        return (PyTypeObject **)((char *)o + sizeof(PyObjectHeader));
    }
    if (tag >= 0 && tag < (int32_t)(sizeof(builtin_types) / sizeof(builtin_types[0]))) {
        if (builtin_types[tag] == NULL) builtin_types[tag] = pcc_capi_type(o);
        return &builtin_types[tag];
    }
    unknown_type = pcc_capi_type(o);
    return &unknown_type;
}

Py_ssize_t pcc_capi_size(PyObject *o) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST: return (Py_ssize_t)py_list_len(o);
        case PY_TYPE_TUPLE: return (Py_ssize_t)py_tuple_len(o);
        case PY_TYPE_STR: return (Py_ssize_t)py_str_len(o);
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
            return (Py_ssize_t)py_bytes_len(o);
        default:
            if (pcc_capi_is_cext_type_tag((int64_t)tag) != 0) {
                return *(Py_ssize_t *)(
                    (char *)o + sizeof(PyObjectHeader) + sizeof(void *)
                );
            }
            return 0;
    }
}

void pcc_capi_set_size(PyObject *o, Py_ssize_t size) {
    if (o == NULL || PY_IS_TAGGED_INT(o)) return;
    int32_t tag = py_type_of(o);
    if (pcc_capi_is_cext_type_tag((int64_t)tag) != 0) {
        *(Py_ssize_t *)(
            (char *)o + sizeof(PyObjectHeader) + sizeof(void *)
        ) = size;
    }
}

int pcc_capi_typecheck(PyObject *o, PyTypeObject *t) {
    if (o == NULL || t == NULL) return 0;
    /* True if o's type IS t or a subtype: walk the tp_base inheritance chain
     * (C-ext types set tp_base; builtin tokens have NULL tp_base, ending it). */
    PyTypeObject *ot = pcc_capi_type(o);
    for (int guard = 0; ot != NULL && guard < 64; guard++) {
        if (ot == t) return 1;
        ot = (PyTypeObject *)ot->tp_base;
    }
    return 0;
}

/* --- More host symbols numpy references (B-P0-PKG runtime core, 2026-05-29 batch 2).
 * Each reuses the type bridge above or is a trivial/correct primitive. */

/* a IS b or descends from b via tp_base (the same walk, on two types). */
int PyType_IsSubtype(PyTypeObject *a, PyTypeObject *b) {
    for (int guard = 0; a != NULL && guard < 64; guard++) {
        if (a == b) return 1;
        a = (PyTypeObject *)a->tp_base;
    }
    return 0;
}

int64_t pcc_capi_type_object_issubclass(PyObject *derived, PyObject *cls) {
    if (!pcc_capi_is_type_object(derived) || !pcc_capi_is_type_object(cls)) {
        return 0;
    }
    return PyType_IsSubtype((PyTypeObject *)derived, (PyTypeObject *)cls);
}

/* Vectorcall arg count: strip the high "arguments-offset" flag bit. */
#ifndef PY_VECTORCALL_ARGUMENTS_OFFSET
#define PY_VECTORCALL_ARGUMENTS_OFFSET (((size_t)1) << (8 * sizeof(size_t) - 1))
#endif
Py_ssize_t PyVectorcall_NARGS(size_t n) {
    return (Py_ssize_t)(n & ~PY_VECTORCALL_ARGUMENTS_OFFSET);
}

/* Allocate an instance of a type (the PyObject_New/NewVar macros call these). */
PyObject *_PyObject_New(PyTypeObject *type) {
    return PyType_GenericAlloc(type, 0);
}
PyObject *_PyObject_NewVar(PyTypeObject *type, Py_ssize_t nitems) {
    return PyType_GenericAlloc(type, nitems);
}

/* Stamp an already-allocated var object's type tag + size (CPython sets ob_type
 * + ob_size; pcc carries a type_tag + the PyVarObject ob_size slot). */
void *PyObject_InitVar(void *op, PyTypeObject *type, Py_ssize_t size) {
    if (op == NULL) return op;
    PyObjectHeader *h = (PyObjectHeader *)op;
    h->type_tag = pcc_capi_cext_tag_for(type);
    /* ob_size is the Py_ssize_t immediately after the 16-byte header. */
    *(Py_ssize_t *)((char *)op + sizeof(PyObjectHeader)) = size;
    return op;
}

/* Free-threading mutex: the no-libpython shim is single-interpreter on this
 * path, so lock/unlock are no-ops (no contention to guard). */
void PyMutex_Lock(void *m) { (void)m; }
void PyMutex_Unlock(void *m) { (void)m; }

/* GIL detach/reattach: no detachable thread state on the no-libpython import
 * path, so save returns NULL and restore is a no-op. */
void *PyEval_SaveThread(void) { return NULL; }
void PyEval_RestoreThread(void *ts) { (void)ts; }

/* The builtins mapping. pcc's no-libpython runtime exposes builtins as native
 * intrinsics rather than a dict object, so this is a real (initially-empty)
 * persistent singleton dict — a valid mapping, not a fake type. CPython's
 * contract returns a BORROWED reference, so it is cached for the process and
 * NOT incref'd here. numpy's only consumer is npy_PyFile_OpenFile
 * (npy_3kcompat.h), which looks up "open" and returns NULL gracefully when
 * absent — so an empty dict is import-safe (import never calls this) and the
 * file-open path degrades to NULL without crashing. Populating it with pcc
 * builtins-as-callables (e.g. a real "open") is a follow-on gated behind the
 * file-object/array runtime, far past import. */
PyObject *PyEval_GetBuiltins(void) {
    static PyObject *builtins = NULL;
    if (builtins == NULL) {
        builtins = py_dict_new();
    }
    return builtins;
}

/* --- Host symbols backed by existing pcc runtime primitives (batch 3). */

/* Invalidate weakrefs to a dying object (pcc's py_weakref_invalidate). */
void PyObject_ClearWeakRefs(PyObject *obj) {
    if (obj != NULL) py_weakref_invalidate(obj);
}

/* `raise X from Y`: set exc.__cause__ (pcc's py_exc_set_cause). */
void PyException_SetCause(PyObject *self, PyObject *cause) {
    py_exc_set_cause(self, cause);
}

/* pcc has no traceback object (no Itanium-style unwinding), so there is nothing
 * to attach; report success. */
int PyException_SetTraceback(PyObject *self, PyObject *tb) {
    (void)self;
    (void)tb;
    return 0;
}

/* issubclass for the type-object case numpy uses (walk tp_base). General
 * class-object issubclass is a follow-on. */
int PyObject_IsSubclass(PyObject *derived, PyObject *cls) {
    return PyType_IsSubtype((PyTypeObject *)derived, (PyTypeObject *)cls);
}

/* --- Link-readiness host symbols numpy references (clearly-correct, no new
 * mirrors / pcc primitives). batch 4. */

/* tp_flags read off the type (mirror struct above). */
unsigned long PyType_GetFlags(PyTypeObject *type) {
    return type != NULL ? type->tp_flags : 0UL;
}

void *PyType_GetSlot(PyTypeObject *type, int slot) {
    if (type == NULL) return NULL;
    switch (slot) {
        case Py_tp_base: return type->tp_base;
        case Py_tp_call: return type->tp_call;
        case Py_tp_dealloc: return type->tp_dealloc;
        case Py_tp_doc: return (void *)type->tp_doc;
        case Py_tp_hash: return type->tp_hash;
        case Py_tp_init: return type->tp_init;
        case Py_tp_iter: return type->tp_iter;
        case Py_tp_iternext: return type->tp_iternext;
        case Py_tp_methods: return type->tp_methods;
        case Py_tp_new: return type->tp_new;
        case Py_tp_repr: return type->tp_repr;
        case Py_tp_richcompare: return type->tp_richcompare;
        case Py_tp_str: return type->tp_str;
        case Py_tp_members: return type->tp_members;
        case Py_tp_getset: return type->tp_getset;
        default: return NULL;
    }
}

/* CPython's locale-independent string->double; libc strtod is the correct
 * implementation here (no separate C-locale on this path). overflow_exc is the
 * exception to raise on overflow — unused; callers also check errno/endptr. */
double PyOS_string_to_double(const char *s, char **endptr, PyObject *overflow_exc) {
    (void)overflow_exc;
    return strtod(s, endptr);
}

/* GC object allocation = ordinary allocation on pcc (the GC tracks via the
 * object header, not a separate gc list). */
PyObject *_PyObject_GC_New(PyTypeObject *type) {
    return PyType_GenericAlloc(type, 0);
}

/* Explicit GC track/untrack are CPython gc-list hooks; pcc's GC discovers
 * objects through the header, so these are no-ops. */
void PyObject_GC_Track(void *op) { (void)op; }
void PyObject_GC_UnTrack(void *op) { (void)op; }

/* --- batch 5: more link-readiness reusing existing shim primitives.
 * (forward decls — these are defined later in this file) */
PyObject *PyDict_GetItem(PyObject *dict, PyObject *key);
void PyObject_Free(void *ptr);

/* The precomputed hash is an optimization; pcc dicts rehash, so routing to the
 * ordinary lookup returns the same item. */
PyObject *_PyDict_GetItem_KnownHash(PyObject *mp, PyObject *key, Py_hash_t hash) {
    (void)hash;
    return PyDict_GetItem(mp, key);
}

/* Free a GC object via pcc's object allocator. */
void PyObject_GC_Del(void *op) { PyObject_Free(op); }

/* Multi-phase module init: PyInit_* returns `PyModuleDef_Init(&def)` for
 * slot-based modules (numpy's _multiarray_umath). The returned thing is the raw
 * def struct (NOT a pcc object), so we stamp a recognizable marker into
 * m_base.ob_base; the loader detects it via pcc_capi_is_moduledef and then runs
 * the Py_mod_exec slots (pcc_capi_module_exec). */
static int pcc_capi_moduledef_marker;
PyObject *PyModuleDef_Init(PyModuleDef *def) {
    if (def == NULL) return NULL;
    def->m_base.ob_base = (PyObject *)&pcc_capi_moduledef_marker;
    return (PyObject *)def;
}
int pcc_capi_is_moduledef(PyObject *o) {
    /* A real module is a pcc instance whose first 8 bytes are a refcount, which
     * will not equal the marker address; a def carries the marker we stamped. */
    if (o == NULL) return 0;
    return ((PyModuleDef *)o)->m_base.ob_base
           == (PyObject *)&pcc_capi_moduledef_marker;
}

/* --- batch 6: PyCFunction accessors. Layout mirror of the fake-libc
 * PyCFunctionObject prefix (PyObject_HEAD is the 16-byte PyObjectHeader, so m_ml
 * sits at offset 16 in both). */
typedef struct {
    PyObjectHeader ob_base;
    PyMethodDef *m_ml;
    PyObject *m_self;
} pcc_capi_cfunc;

static PyObject *pcc_capi_method_call_entry(PyObject *captures, PyObject *args);

static PyMethodDef *pcc_capi_func_method_def(PyObject *op) {
    if (op == NULL || PY_IS_TAGGED_INT(op) || py_type_of(op) != PY_TYPE_FUNC) {
        return NULL;
    }
    return (PyMethodDef *)((PyFuncObject *)op)->capi_method;
}

static PyObject *pcc_capi_fast_keyword_call(
    PyObject *self,
    PyMethodDef *method,
    PyObject *call_args,
    PyObject *kwargs
) {
    int64_t positional_count = py_tuple_len(call_args);
    int64_t keyword_count = kwargs == py_None ? 0 : py_dict_len(kwargs);
    int64_t total_count = positional_count + keyword_count;
    PyObject **vector = NULL;
    PyObject *items = NULL;
    PyObject *keyword_names = NULL;
    PyObject *result = NULL;
    int ready = 1;

    if (total_count > 0) {
        vector = calloc((size_t)total_count, sizeof(PyObject *));
        if (vector == NULL) {
            PyErr_NoMemory();
            ready = 0;
        }
    }
    for (int64_t i = 0; ready && i < positional_count; i++) {
        vector[i] = py_tuple_get(call_args, i);
        if (vector[i] == NULL) ready = 0;
    }
    if (ready && keyword_count > 0) {
        items = py_dict_items(kwargs);
        keyword_names = py_tuple_new(keyword_count);
        if (items == NULL || keyword_names == NULL
            || py_list_len(items) != keyword_count) {
            ready = 0;
        }
    }
    for (int64_t i = 0; ready && i < keyword_count; i++) {
        PyObject *pair = py_list_get(items, i);
        PyObject *key = NULL;
        PyObject *value = NULL;
        if (pair != NULL && !PY_IS_TAGGED_INT(pair)
            && py_type_of(pair) == PY_TYPE_TUPLE
            && py_tuple_len(pair) == 2) {
            key = py_tuple_get(pair, 0);
            value = py_tuple_get(pair, 1);
        }
        if (key == NULL || value == NULL || PY_IS_TAGGED_INT(key)
            || py_type_of(key) != PY_TYPE_STR) {
            PyErr_SetString(PyExc_TypeError, "keyword names must be strings");
            ready = 0;
        } else {
            py_tuple_set_item(keyword_names, i, key);
            vector[positional_count + i] = value;
            value = NULL;
        }
        py_decref(key);
        py_decref(value);
        py_decref(pair);
    }
    if (ready) {
        result = ((PyCFunctionFastWithKeywords)method->ml_meth)(
            self,
            vector,
            (Py_ssize_t)positional_count,
            keyword_names
        );
    }
    for (int64_t i = 0; i < total_count; i++) {
        py_decref(vector == NULL ? NULL : vector[i]);
    }
    free(vector);
    py_decref(items);
    py_decref(keyword_names);
    return result;
}

PyCFunction PyCFunction_GetFunction(PyObject *op) {
    if (op == NULL) return NULL;
    if (!PY_IS_TAGGED_INT(op) && py_type_of(op) == PY_TYPE_FUNC) {
        PyMethodDef *method = pcc_capi_func_method_def(op);
        if (method != NULL) return method->ml_meth;
        return (PyCFunction)((PyFuncObject *)op)->entry;
    }
    PyMethodDef *ml = ((pcc_capi_cfunc *)op)->m_ml;
    return ml ? ml->ml_meth : NULL;
}
PyObject *PyCFunction_GetSelf(PyObject *op) {
    if (op != NULL && !PY_IS_TAGGED_INT(op) && py_type_of(op) == PY_TYPE_FUNC) {
        PyMethodDef *method = pcc_capi_func_method_def(op);
        if (method != NULL) {
            return ((PyFuncObject *)op)->capi_self;
        }
        return ((PyFuncObject *)op)->self_obj;
    }
    return op ? ((pcc_capi_cfunc *)op)->m_self : NULL;
}
int PyCFunction_GetFlags(PyObject *op) {
    if (op == NULL) return -1;
    if (!PY_IS_TAGGED_INT(op) && py_type_of(op) == PY_TYPE_FUNC) {
        PyMethodDef *method = pcc_capi_func_method_def(op);
        if (method != NULL) return method->ml_flags;
        return METH_VARARGS;
    }
    PyMethodDef *ml = ((pcc_capi_cfunc *)op)->m_ml;
    return ml ? ml->ml_flags : 0;
}

static PyObject *pcc_capi_method_call_entry(PyObject *captures, PyObject *args) {
    if (captures == NULL || PY_IS_TAGGED_INT(captures)
        || py_type_of(captures) != PY_TYPE_TUPLE
        || py_tuple_len(captures) != 3) {
        PyErr_SetString(PyExc_TypeError, "invalid C method capture");
        return NULL;
    }
    if (args == NULL) {
        args = py_tuple_new(0);
        if (args == NULL) return PyErr_NoMemory();
    } else if (PY_IS_TAGGED_INT(args) || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "C method args must be a tuple");
        return NULL;
    } else {
        py_incref(args);
    }

    PyObject *self = py_tuple_get(captures, 0);
    PyObject *method_ptr_obj = py_tuple_get(captures, 1);
    PyObject *flags_obj = py_tuple_get(captures, 2);
    if (self == NULL || method_ptr_obj == NULL || flags_obj == NULL) {
        py_decref(args);
        py_decref(self);
        py_decref(method_ptr_obj);
        py_decref(flags_obj);
        PyErr_SetString(PyExc_TypeError, "invalid C method capture");
        return NULL;
    }

    PyMethodDef *method = (PyMethodDef *)(intptr_t)py_int_value_i64(method_ptr_obj);
    int flags = (int)py_int_value_i64(flags_obj);
    int64_t nargs = py_tuple_len(args);
    PyObject *result = NULL;
    int args_ready = 1;

    if (method != NULL && method->ml_meth != NULL
        && (flags & METH_KEYWORDS) == 0) {
        PyObject *prepared = pcc_capi_prepare_call_args(args);
        if (prepared == NULL) {
            args_ready = 0;
        } else {
            py_decref(args);
            args = prepared;
            nargs = py_tuple_len(args);
        }
    }

    if (!args_ready) {
        if (!py_err_occurred()) PyErr_NoMemory();
    } else if (method == NULL || method->ml_meth == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid C method");
    } else if ((flags & METH_FASTCALL) != 0
               && (flags & METH_KEYWORDS) != 0) {
        if (nargs != 2) {
            PyErr_SetString(PyExc_TypeError, "invalid C keyword method call");
        } else {
            PyObject *call_args = py_tuple_get(args, 0);
            PyObject *kwargs = py_tuple_get(args, 1);
            if (call_args == NULL || kwargs == NULL
                || PY_IS_TAGGED_INT(call_args)
                || py_type_of(call_args) != PY_TYPE_TUPLE
                || (kwargs != py_None
                    && (PY_IS_TAGGED_INT(kwargs)
                        || py_type_of(kwargs) != PY_TYPE_DICT))) {
                PyErr_SetString(PyExc_TypeError, "invalid C keyword method call");
            } else {
                result = pcc_capi_fast_keyword_call(
                    self, method, call_args, kwargs
                );
            }
            py_decref(call_args);
            py_decref(kwargs);
        }
    } else if ((flags & METH_VARARGS) != 0 && (flags & METH_KEYWORDS) != 0) {
        if (nargs != 2) {
            PyErr_SetString(PyExc_TypeError, "invalid C keyword method call");
        } else {
            PyObject *call_args = py_tuple_get(args, 0);
            PyObject *kwargs = py_tuple_get(args, 1);
            if (call_args == NULL || kwargs == NULL
                || PY_IS_TAGGED_INT(call_args)
                || py_type_of(call_args) != PY_TYPE_TUPLE
                || (kwargs != py_None
                    && (PY_IS_TAGGED_INT(kwargs)
                        || py_type_of(kwargs) != PY_TYPE_DICT))) {
                PyErr_SetString(PyExc_TypeError, "invalid C keyword method call");
            } else {
                result = ((PyCFunctionWithKeywords)method->ml_meth)(
                    self, call_args, kwargs
                );
            }
            py_decref(call_args);
            py_decref(kwargs);
        }
    } else if ((flags & METH_NOARGS) != 0) {
        if (nargs != 0) {
            PyErr_SetString(PyExc_TypeError, "method takes no arguments");
        } else {
            result = method->ml_meth(self, NULL);
        }
    } else if ((flags & METH_O) != 0) {
        if (nargs != 1) {
            PyErr_SetString(PyExc_TypeError, "method takes exactly one argument");
        } else {
            PyObject *arg = py_tuple_get(args, 0);
            result = method->ml_meth(self, arg);
            py_decref(arg);
        }
    } else if ((flags & METH_FASTCALL) != 0
               && (flags & METH_KEYWORDS) == 0) {
        PyObject **fastcall_args = NULL;
        int args_ready = 1;
        if (nargs > 0) {
            fastcall_args = calloc((size_t)nargs, sizeof(PyObject *));
            if (fastcall_args == NULL) {
                PyErr_NoMemory();
                args_ready = 0;
            }
        }
        for (int64_t i = 0; args_ready && i < nargs; i++) {
            fastcall_args[i] = py_tuple_get(args, i);
            if (fastcall_args[i] == NULL) args_ready = 0;
        }
        if (args_ready) {
            result = ((PyCFunctionFast)method->ml_meth)(
                self, fastcall_args, (Py_ssize_t)nargs
            );
        }
        for (int64_t i = 0; i < nargs; i++) {
            py_decref(fastcall_args == NULL ? NULL : fastcall_args[i]);
        }
        free(fastcall_args);
    } else if ((flags & METH_VARARGS) != 0) {
        result = method->ml_meth(self, args);
    } else {
        PyErr_SetString(PyExc_TypeError, "unsupported C method flags");
    }

    py_decref(args);
    py_decref(self);
    py_decref(method_ptr_obj);
    py_decref(flags_obj);
    return result;
}

static PyObject *pcc_capi_method_capture(PyObject *self, PyMethodDef *method) {
    PyObject *captures = py_tuple_new(3);
    if (captures == NULL) return NULL;
    PyObject *method_ptr = py_int_from_i64((int64_t)(intptr_t)method);
    PyObject *flags = py_int_from_i64((int64_t)method->ml_flags);
    if (method_ptr == NULL || flags == NULL) {
        py_decref(captures);
        py_decref(method_ptr);
        py_decref(flags);
        return NULL;
    }
    py_tuple_set_item(captures, 0, self != NULL ? self : py_None);
    py_tuple_set_item(captures, 1, method_ptr);
    py_tuple_set_item(captures, 2, flags);
    py_decref(method_ptr);
    py_decref(flags);
    return captures;
}

static PyObject *pcc_capi_method_keyword_signature(void) {
    PyObject *sig = NULL;
    PyObject *names = NULL;
    PyObject *kinds = NULL;
    PyObject *has_defaults = NULL;
    PyObject *defaults = NULL;
    PyObject *magic = NULL;
    PyObject *args_name = NULL;
    PyObject *kwargs_name = NULL;
    PyObject *varargs_kind = NULL;
    PyObject *varkw_kind = NULL;

    sig = py_tuple_new(5);
    names = py_tuple_new(2);
    kinds = py_tuple_new(2);
    has_defaults = py_tuple_new(2);
    defaults = py_tuple_new(2);
    magic = py_str_new(
        PCC_FUNC_SIGNATURE_MAGIC,
        (int64_t)strlen(PCC_FUNC_SIGNATURE_MAGIC)
    );
    args_name = py_str_new("args", 4);
    kwargs_name = py_str_new("kwargs", 6);
    varargs_kind = py_int_from_i64(PCC_FUNC_KIND_VARARGS);
    varkw_kind = py_int_from_i64(PCC_FUNC_KIND_VARKW);
    if (sig == NULL || names == NULL || kinds == NULL
        || has_defaults == NULL || defaults == NULL || magic == NULL
        || args_name == NULL || kwargs_name == NULL
        || varargs_kind == NULL || varkw_kind == NULL) {
        goto fail;
    }

    py_tuple_set_item(names, 0, args_name);
    py_tuple_set_item(names, 1, kwargs_name);
    py_tuple_set_item(kinds, 0, varargs_kind);
    py_tuple_set_item(kinds, 1, varkw_kind);
    py_tuple_set_item(has_defaults, 0, py_False);
    py_tuple_set_item(has_defaults, 1, py_False);
    py_tuple_set_item(defaults, 0, py_None);
    py_tuple_set_item(defaults, 1, py_None);
    py_tuple_set_item(sig, 0, magic);
    py_tuple_set_item(sig, 1, names);
    py_tuple_set_item(sig, 2, kinds);
    py_tuple_set_item(sig, 3, has_defaults);
    py_tuple_set_item(sig, 4, defaults);

    py_decref(names);
    py_decref(kinds);
    py_decref(has_defaults);
    py_decref(defaults);
    py_decref(magic);
    py_decref(args_name);
    py_decref(kwargs_name);
    py_decref(varargs_kind);
    py_decref(varkw_kind);
    return sig;

fail:
    py_decref(sig);
    py_decref(names);
    py_decref(kinds);
    py_decref(has_defaults);
    py_decref(defaults);
    py_decref(magic);
    py_decref(args_name);
    py_decref(kwargs_name);
    py_decref(varargs_kind);
    py_decref(varkw_kind);
    return NULL;
}

static PyObject *pcc_capi_method_keyword_capture(PyObject *self, PyMethodDef *method) {
    PyObject *inner = pcc_capi_method_capture(self, method);
    PyObject *signature = pcc_capi_method_keyword_signature();
    PyObject *wrapped = py_tuple_new(2);
    if (inner == NULL || signature == NULL || wrapped == NULL) {
        py_decref(inner);
        py_decref(signature);
        py_decref(wrapped);
        return NULL;
    }
    py_tuple_set_item(wrapped, 0, inner);
    py_tuple_set_item(wrapped, 1, signature);
    py_decref(inner);
    py_decref(signature);
    return wrapped;
}

static PyObject *pcc_capi_method_func_finish(
    PyObject *fn,
    PyObject *self,
    PyMethodDef *method
) {
    if (fn == NULL) return NULL;
    PyFuncObject *func = (PyFuncObject *)fn;
    func->capi_method = method;
    pcc_gc_store_ptr(fn, &func->capi_self, self != NULL ? self : py_None);
    return fn;
}

static PyObject *pcc_capi_method_func_new(PyObject *self, PyMethodDef *method) {
    if (method == NULL || method->ml_meth == NULL) return NULL;
    if ((method->ml_flags & METH_KEYWORDS) != 0
        && (method->ml_flags & (METH_VARARGS | METH_FASTCALL)) != 0) {
        PyObject *captures = pcc_capi_method_keyword_capture(self, method);
        if (captures == NULL) return NULL;
        PyObject *fn = py_func_new_named(
            (void *)pcc_capi_method_call_entry, captures, method->ml_name
        );
        py_decref(captures);
        return pcc_capi_method_func_finish(fn, self, method);
    }
    if ((method->ml_flags & METH_VARARGS) != 0) {
        PyObject *captures = pcc_capi_method_capture(self, method);
        if (captures == NULL) return NULL;
        PyObject *fn = py_func_new_named(
            (void *)pcc_capi_method_call_entry, captures, method->ml_name
        );
        py_decref(captures);
        return pcc_capi_method_func_finish(fn, self, method);
    }
    if ((method->ml_flags & (METH_NOARGS | METH_O | METH_FASTCALL)) != 0
        && (method->ml_flags & METH_KEYWORDS) == 0) {
        PyObject *captures = pcc_capi_method_capture(self, method);
        if (captures == NULL) return NULL;
        PyObject *fn = py_func_new_named(
            (void *)pcc_capi_method_call_entry, captures, method->ml_name
        );
        py_decref(captures);
        return pcc_capi_method_func_finish(fn, self, method);
    }
    return NULL;
}

/* A type's tp_methods entries are descriptors, not methods bound to the type
 * object.  Keep one unbound function per defining PyMethodDef so inherited
 * class lookup preserves descriptor identity (numpy uses that property to
 * recognize its no-op base __array_finalize__).  The cache owns and pins each
 * wrapper for the process lifetime; extension method tables have the same
 * lifetime. */
#define PCC_CAPI_METHOD_DESCRIPTOR_MAX 4096
typedef struct {
    PyMethodDef *method;
    PyObject *descriptor;
} PccCapiMethodDescriptor;

static PccCapiMethodDescriptor
    pcc_capi_method_descriptors[PCC_CAPI_METHOD_DESCRIPTOR_MAX];
static int32_t pcc_capi_method_descriptor_count;

static PyObject *pcc_capi_unbound_method_call_entry(
    PyObject *captures,
    PyObject *args
) {
    if (captures == NULL || PY_IS_TAGGED_INT(captures)
        || py_type_of(captures) != PY_TYPE_TUPLE
        || py_tuple_len(captures) != 2
        || args == NULL || PY_IS_TAGGED_INT(args)
        || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "invalid C method descriptor call");
        return NULL;
    }

    int64_t nargs = py_tuple_len(args);
    if (nargs < 1) {
        PyErr_SetString(
            PyExc_TypeError,
            "unbound C method requires an instance"
        );
        return NULL;
    }

    PyObject *method_ptr_obj = py_tuple_get(captures, 0);
    PyObject *flags_obj = py_tuple_get(captures, 1);
    PyObject *self = py_tuple_get(args, 0);
    if (method_ptr_obj == NULL || flags_obj == NULL || self == NULL) {
        py_decref(method_ptr_obj);
        py_decref(flags_obj);
        py_decref(self);
        PyErr_SetString(PyExc_TypeError, "invalid C method descriptor");
        return NULL;
    }

    PyMethodDef *method = (PyMethodDef *)(intptr_t)py_int_value_i64(
        method_ptr_obj
    );
    int flags = (int)py_int_value_i64(flags_obj);
    PyObject *result = NULL;
    if (method == NULL || method->ml_meth == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid C method descriptor");
    } else if ((flags & METH_NOARGS) != 0) {
        if (nargs != 1) {
            PyErr_SetString(PyExc_TypeError, "method takes no arguments");
        } else {
            result = method->ml_meth(self, NULL);
        }
    } else if ((flags & METH_O) != 0) {
        if (nargs != 2) {
            PyErr_SetString(PyExc_TypeError, "method takes exactly one argument");
        } else {
            PyObject *arg = py_tuple_get(args, 1);
            result = method->ml_meth(self, arg);
            py_decref(arg);
        }
    } else if ((flags & METH_VARARGS) != 0) {
        PyObject *call_args = py_tuple_new(nargs - 1);
        if (call_args != NULL) {
            for (int64_t i = 1; i < nargs; i++) {
                PyObject *arg = py_tuple_get(args, i);
                if (arg == NULL) {
                    py_decref(call_args);
                    call_args = NULL;
                    break;
                }
                py_tuple_set_item(call_args, i - 1, arg);
                py_decref(arg);
            }
        }
        if (call_args != NULL) {
            if ((flags & METH_KEYWORDS) != 0) {
                result = ((PyCFunctionWithKeywords)method->ml_meth)(
                    self, call_args, NULL
                );
            } else {
                result = method->ml_meth(self, call_args);
            }
            py_decref(call_args);
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "unsupported C method flags");
    }

    py_decref(method_ptr_obj);
    py_decref(flags_obj);
    py_decref(self);
    return result;
}

static PyObject *pcc_capi_type_method_descriptor(PyMethodDef *method) {
    if (method == NULL || method->ml_meth == NULL) return NULL;
    for (int32_t i = 0; i < pcc_capi_method_descriptor_count; i++) {
        if (pcc_capi_method_descriptors[i].method == method) {
            PyObject *descriptor = pcc_capi_method_descriptors[i].descriptor;
            py_incref(descriptor);
            return descriptor;
        }
    }
    if (pcc_capi_method_descriptor_count >= PCC_CAPI_METHOD_DESCRIPTOR_MAX) {
        PyErr_SetString(PyExc_RuntimeError, "C method descriptor cache exhausted");
        return NULL;
    }

    PyObject *captures = py_tuple_new(2);
    PyObject *method_ptr = py_int_from_i64((int64_t)(intptr_t)method);
    PyObject *flags = py_int_from_i64((int64_t)method->ml_flags);
    if (captures == NULL || method_ptr == NULL || flags == NULL) {
        py_decref(captures);
        py_decref(method_ptr);
        py_decref(flags);
        return NULL;
    }
    py_tuple_set_item(captures, 0, method_ptr);
    py_tuple_set_item(captures, 1, flags);
    py_decref(method_ptr);
    py_decref(flags);

    PyObject *descriptor = py_func_new_named(
        (void *)pcc_capi_unbound_method_call_entry,
        captures,
        method->ml_name
    );
    py_decref(captures);
    if (descriptor == NULL) return NULL;
    pcc_gc_pin(descriptor);
    int32_t index = pcc_capi_method_descriptor_count++;
    pcc_capi_method_descriptors[index].method = method;
    pcc_capi_method_descriptors[index].descriptor = descriptor;
    py_incref(descriptor);
    return descriptor;
}

/* Type-level lookup returns descriptor objects for tp_getset/tp_members;
 * instance lookup still invokes the actual getter/member bridge below.  pcc's
 * compact object model does not embed CPython's PyDescrObject layout, so use a
 * stable function-shaped metadata carrier just as the method-descriptor bridge
 * above does.  It preserves descriptor identity and writable __doc__, which
 * extension documentation installers such as numpy.add_docstring require. */
#define PCC_CAPI_DATA_DESCRIPTOR_MAX 4096
typedef struct {
    const void *definition;
    PyObject *descriptor;
} PccCapiDataDescriptor;

static PccCapiDataDescriptor
    pcc_capi_getset_descriptors[PCC_CAPI_DATA_DESCRIPTOR_MAX];
static int32_t pcc_capi_getset_descriptor_count;
static PccCapiDataDescriptor
    pcc_capi_member_descriptors[PCC_CAPI_DATA_DESCRIPTOR_MAX];
static int32_t pcc_capi_member_descriptor_count;

static PyObject *pcc_capi_data_descriptor_call_entry(
    PyObject *captures,
    PyObject *args
) {
    (void)captures;
    (void)args;
    PyErr_SetString(PyExc_TypeError, "attribute descriptor is not callable");
    return NULL;
}

static PyObject *pcc_capi_type_data_descriptor(
    const void *definition,
    const char *name,
    PccCapiDataDescriptor *cache,
    int32_t *count
) {
    if (definition == NULL || name == NULL) return NULL;
    for (int32_t i = 0; i < *count; i++) {
        if (cache[i].definition == definition) {
            py_incref(cache[i].descriptor);
            return cache[i].descriptor;
        }
    }
    if (*count >= PCC_CAPI_DATA_DESCRIPTOR_MAX) {
        PyErr_SetString(PyExc_RuntimeError, "C data descriptor cache exhausted");
        return NULL;
    }
    PyObject *descriptor = py_func_new_named(
        (void *)pcc_capi_data_descriptor_call_entry,
        py_None,
        name
    );
    if (descriptor == NULL) return NULL;
    pcc_gc_pin(descriptor);
    int32_t index = (*count)++;
    cache[index].definition = definition;
    cache[index].descriptor = descriptor;
    py_incref(descriptor);
    return descriptor;
}

static PyObject *pcc_capi_type_getset_descriptor(PccCapiGetSetDef *getset) {
    return pcc_capi_type_data_descriptor(
        getset,
        getset == NULL ? NULL : getset->name,
        pcc_capi_getset_descriptors,
        &pcc_capi_getset_descriptor_count
    );
}

static PyObject *pcc_capi_type_member_descriptor(PccCapiMemberDef *member) {
    return pcc_capi_type_data_descriptor(
        member,
        member == NULL ? NULL : member->name,
        pcc_capi_member_descriptors,
        &pcc_capi_member_descriptor_count
    );
}

typedef struct {
    void *richcompare;
    int op;
    PyObject *descriptor;
} PccCapiRichcompareDescriptor;

static PccCapiRichcompareDescriptor
    pcc_capi_richcompare_descriptors[PCC_CAPI_DATA_DESCRIPTOR_MAX];
static int32_t pcc_capi_richcompare_descriptor_count;

static int pcc_capi_richcompare_op(const char *name) {
    if (name == NULL) return -1;
    if (strcmp(name, "__lt__") == 0) return 0;
    if (strcmp(name, "__le__") == 0) return 1;
    if (strcmp(name, "__eq__") == 0) return 2;
    if (strcmp(name, "__ne__") == 0) return 3;
    if (strcmp(name, "__gt__") == 0) return 4;
    if (strcmp(name, "__ge__") == 0) return 5;
    return -1;
}

static PyObject *pcc_capi_richcompare_descriptor_call_entry(
    PyObject *captures,
    PyObject *args
) {
    if (captures == NULL || PY_IS_TAGGED_INT(captures)
        || py_type_of(captures) != PY_TYPE_TUPLE
        || py_tuple_len(captures) != 2
        || args == NULL || PY_IS_TAGGED_INT(args)
        || py_type_of(args) != PY_TYPE_TUPLE
        || py_tuple_len(args) != 2) {
        PyErr_SetString(PyExc_TypeError, "invalid rich comparison descriptor call");
        return NULL;
    }
    PyObject *fn_ptr_obj = py_tuple_get(captures, 0);
    PyObject *op_obj = py_tuple_get(captures, 1);
    PyObject *lhs = py_tuple_get(args, 0);
    PyObject *rhs = py_tuple_get(args, 1);
    if (fn_ptr_obj == NULL || op_obj == NULL || lhs == NULL || rhs == NULL) {
        py_decref(fn_ptr_obj);
        py_decref(op_obj);
        py_decref(lhs);
        py_decref(rhs);
        PyErr_SetString(PyExc_TypeError, "invalid rich comparison descriptor");
        return NULL;
    }
    PyObject *(*richcompare)(PyObject *, PyObject *, int) = (
        PyObject *(*)(PyObject *, PyObject *, int)
    )(intptr_t)py_int_value_i64(fn_ptr_obj);
    int op = (int)py_int_value_i64(op_obj);
    PyObject *result = richcompare == NULL ? NULL : richcompare(lhs, rhs, op);
    if (richcompare == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid rich comparison function");
    }
    py_decref(fn_ptr_obj);
    py_decref(op_obj);
    py_decref(lhs);
    py_decref(rhs);
    return result;
}

static PyObject *pcc_capi_type_richcompare_descriptor(
    PyTypeObject *type,
    const char *name
) {
    int op = pcc_capi_richcompare_op(name);
    if (type == NULL || type->tp_richcompare == NULL || op < 0) return NULL;
    for (int32_t i = 0; i < pcc_capi_richcompare_descriptor_count; i++) {
        PccCapiRichcompareDescriptor *entry = &pcc_capi_richcompare_descriptors[i];
        if (entry->richcompare == type->tp_richcompare && entry->op == op) {
            py_incref(entry->descriptor);
            return entry->descriptor;
        }
    }
    if (pcc_capi_richcompare_descriptor_count >= PCC_CAPI_DATA_DESCRIPTOR_MAX) {
        PyErr_SetString(PyExc_RuntimeError, "rich comparison descriptor cache exhausted");
        return NULL;
    }
    PyObject *captures = py_tuple_new(2);
    PyObject *fn_ptr = py_int_from_i64((int64_t)(intptr_t)type->tp_richcompare);
    PyObject *op_obj = py_int_from_i64((int64_t)op);
    if (captures == NULL || fn_ptr == NULL || op_obj == NULL) {
        py_decref(captures);
        py_decref(fn_ptr);
        py_decref(op_obj);
        return NULL;
    }
    py_tuple_set_item(captures, 0, fn_ptr);
    py_tuple_set_item(captures, 1, op_obj);
    py_decref(fn_ptr);
    py_decref(op_obj);
    PyObject *descriptor = py_func_new_named(
        (void *)pcc_capi_richcompare_descriptor_call_entry,
        captures,
        name
    );
    py_decref(captures);
    if (descriptor == NULL) return NULL;
    pcc_gc_pin(descriptor);
    int32_t index = pcc_capi_richcompare_descriptor_count++;
    pcc_capi_richcompare_descriptors[index].richcompare = type->tp_richcompare;
    pcc_capi_richcompare_descriptors[index].op = op;
    pcc_capi_richcompare_descriptors[index].descriptor = descriptor;
    py_incref(descriptor);
    return descriptor;
}

/* --- batch 7: import-critical link-gap host symbols. */

/* One main interpreter + thread state. numpy's subinterpreter guard checks
 * `PyThreadState_Get()->interp != PyInterpreterState_Main()`, so both must
 * agree (interp is the first field, offset 0). void* returns are ABI-compatible
 * with the fake-Python.h opaque PyThreadState / PyInterpreterState pointers. */
static char pcc_capi_main_interp;
static struct { void *interp; } pcc_capi_main_tstate = {&pcc_capi_main_interp};
void *PyInterpreterState_Main(void) { return &pcc_capi_main_interp; }
void *PyThreadState_Get(void) { return &pcc_capi_main_tstate; }

/* exception __context__ via the pcc primitive (mirrors PyException_SetCause). */
void PyException_SetContext(PyObject *self, PyObject *context) {
    py_exc_set_context(self, context);
}

int PyUnstable_Object_IsUniqueReferencedTemporary(PyObject *op) {
    (void)op;
    return 0;
}

/* Exactly one reference -> safe for in-place mutation. pcc is refcounted
 * (refcount at offset 0). Tagged-int immediates have no header and are
 * conceptually shared -> not uniquely referenced; immortal objects carry a
 * large refcount (!= 1) so they also return 0. */
int PyUnstable_Object_IsUniquelyReferenced(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    return ((PyObjectHeader *)obj)->refcount == 1;
}

/* datetime C-API capsule: PyDateTime_IMPORT is a no-op on the no-libpython
 * path, so the table stays NULL; the symbol just needs to exist for linking. */
void *PyDateTimeAPI = NULL;

/* --- batch 8: link symbols reusing existing shim primitives (forward-declared
 * here; defined later in this file). */
PyObject *PyTuple_New(Py_ssize_t size);
PyObject *PyTuple_GetItem(PyObject *obj, Py_ssize_t index);
int PyTuple_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value);
Py_ssize_t PyTuple_Size(PyObject *obj);
PyObject *PyUnicode_AsASCIIString(PyObject *obj);
int PyLong_Check(PyObject *obj);
long PyLong_AsLong(PyObject *obj);

/* Sub-tuple [lo, hi) — new tuple + copied (incref'd) items. */
PyObject *PyTuple_GetSlice(PyObject *tuple, Py_ssize_t lo, Py_ssize_t hi) {
    if (tuple == NULL) return NULL;
    Py_ssize_t n = PyTuple_Size(tuple);
    if (lo < 0) lo = 0;
    if (hi > n) hi = n;
    if (hi < lo) hi = lo;
    PyObject *result = PyTuple_New(hi - lo);
    if (result == NULL) return NULL;
    for (Py_ssize_t i = lo; i < hi; i++) {
        PyObject *item = PyTuple_GetItem(tuple, i);
        py_incref(item);  /* PyTuple_SetItem steals the reference */
        PyTuple_SetItem(result, i - lo, item);
    }
    return result;
}

/* latin1 encode — for numpy's ASCII dtype/field names, latin1 == ASCII, so
 * route to the ASCII encoder (full latin1 high-byte support is a follow-on). */
PyObject *PyUnicode_AsLatin1String(PyObject *unicode) {
    return PyUnicode_AsASCIIString(unicode);
}

/* fd from an object: an int IS the fd; otherwise unsupported here (numpy passes
 * integer fds on this path). */
int PyObject_AsFileDescriptor(PyObject *o) {
    if (o != NULL && PyLong_Check(o)) return (int)PyLong_AsLong(o);
    PyErr_SetString(PyExc_TypeError, "argument must be an int file descriptor");
    return -1;
}

/* --- batch 9: genuinely-correct / import-safe link symbols only. */

/* pcc raises normalized exception INSTANCES (no separate type/value/tb triple to
 * reconcile), so the triple is already normalized — nothing to do. */
void PyErr_NormalizeException(PyObject **exc, PyObject **val, PyObject **tb) {
    (void)exc;
    (void)val;
    (void)tb;
}

/* `T.__class_getitem__` is set to this at import but only CALLED when user code
 * subscripts the type (`T[X]`), not during import. Return the origin (incref'd):
 * a faithful GenericAlias object is a follow-on; this keeps `T[X] -> T` rather
 * than crashing, and the symbol links. */
PyObject *Py_GenericAlias(PyObject *origin, PyObject *args) {
    (void)args;
    if (origin != NULL) py_incref(origin);
    return origin;
}

/* --- batch 10: a genuinely-correct single-context contextvar (NOT a stub).
 * numpy.errstate creates a ContextVar AT import via PyContextVar_New, so an
 * empty stub could break import; this is a real object with correct CPython
 * single-context Get semantics. pcc has no Context objects (no per-context
 * isolation / thread-local contexts), so this models the one implicit global
 * context — correct for single-threaded import and basic get/default use.
 * Only New + Get are provided (the two symbols numpy's C core references);
 * Set/Reset are driven from Python's contextvars module, not the C API. */
typedef struct {
    PyObjectHeader header;
    void *ob_type;       /* set by PyType_GenericAlloc at offset sizeof(header) */
    const char *name;    /* borrowed; PyContextVar_New callers pass a literal */
    PyObject *def;       /* the var's own default (owned), or NULL */
    PyObject *value;     /* value set in the single context (owned), or NULL */
} pcc_capi_contextvar;

static int pcc_capi_contextvar_traverse(
    PyObject *obj,
    visitproc visit,
    void *arg
) {
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)obj;
    int result = pcc_capi_visit_slot(&cv->def, visit, arg);
    if (result != 0) return result;
    return pcc_capi_visit_slot(&cv->value, visit, arg);
}

static void pcc_capi_contextvar_dealloc(PyObject *obj) {
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)obj;
    PyObject *def = pcc_gc_load_ptr(obj, &cv->def);
    PyObject *value = pcc_gc_load_ptr(obj, &cv->value);
    cv->def = NULL;
    cv->value = NULL;
    py_decref(def);
    py_decref(value);
}

static PyObject *pcc_capi_contextvar_get_method(PyObject *self, PyObject *args);
static PyObject *pcc_capi_contextvar_set_method(PyObject *self, PyObject *value);
static PyObject *pcc_capi_contextvar_reset_method(PyObject *self, PyObject *token);

static PyMethodDef pcc_capi_contextvar_methods[] = {
    {"get", pcc_capi_contextvar_get_method, METH_VARARGS, NULL},
    {"set", pcc_capi_contextvar_set_method, METH_O, NULL},
    {"reset", pcc_capi_contextvar_reset_method, METH_O, NULL},
    {NULL, NULL, 0, NULL},
};

static PyTypeObject pcc_capi_contextvar_type = {
    .ob_base = {1, 0, 0},
    .tp_name = "ContextVar",
    .tp_flags = (
        Py_TPFLAGS_READY
        | Py_TPFLAGS_HAVE_GC
        | PCC_TPFLAGS_MANAGED_DEALLOC
    ),
    .tp_basicsize = (Py_ssize_t)sizeof(pcc_capi_contextvar),
    .tp_methods = pcc_capi_contextvar_methods,
    .tp_dealloc = pcc_capi_contextvar_dealloc,
    .tp_traverse = pcc_capi_contextvar_traverse,
};

PyObject *PyContextVar_New(const char *name, PyObject *def) {
    PyObject *obj = PyType_GenericAlloc(&pcc_capi_contextvar_type, 0);
    if (obj == NULL) return NULL;
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)obj;
    cv->name = name;
    pcc_gc_store_ptr(obj, &cv->def, def);
    /* cv->value left NULL (calloc'd) = unset in the single context. */
    return obj;
}

int PyContextVar_Get(PyObject *var, PyObject *default_value, PyObject **value) {
    if (var == NULL || value == NULL) return -1;
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)var;
    PyObject *res = pcc_gc_load_ptr(var, &cv->value);
                                            /* a set value wins, then... */
    if (res == NULL) res = default_value;  /* the explicit default arg, then... */
    if (res == NULL) res = pcc_gc_load_ptr(var, &cv->def);
                                            /* the var's own default. */
    if (res != NULL) py_incref(res);
    *value = res;                          /* may be NULL = no default anywhere */
    return 0;
}

void *PyMem_Malloc(size_t size) {
    return malloc(size == 0 ? 1 : size);
}

void *PyMem_RawMalloc(size_t size) {
    return PyMem_Malloc(size);
}

void *PyMem_Calloc(size_t nelem, size_t elsize) {
    if (nelem != 0 && elsize > ((size_t)-1) / nelem) {
        PyErr_NoMemory();
        return NULL;
    }
    return calloc(nelem == 0 ? 1 : nelem, elsize == 0 ? 1 : elsize);
}

void *PyMem_RawCalloc(size_t nelem, size_t elsize) {
    return PyMem_Calloc(nelem, elsize);
}

void *PyMem_Realloc(void *ptr, size_t new_size) {
    return realloc(ptr, new_size == 0 ? 1 : new_size);
}

void *PyMem_RawRealloc(void *ptr, size_t new_size) {
    return PyMem_Realloc(ptr, new_size);
}

void PyMem_Free(void *ptr) {
    free(ptr);
}

void PyMem_RawFree(void *ptr) {
    PyMem_Free(ptr);
}

void *PyObject_Malloc(size_t size) {
    return PyMem_Malloc(size);
}

void *PyObject_Calloc(size_t nelem, size_t elsize) {
    return PyMem_Calloc(nelem, elsize);
}

void *PyObject_Realloc(void *ptr, size_t new_size) {
    return PyMem_Realloc(ptr, new_size);
}

void PyObject_Free(void *ptr) {
    PyMem_Free(ptr);
}

int PyOS_vsnprintf(char *str, size_t size, const char *format, va_list va) {
    return vsnprintf(str, size, format, va);
}

int PyOS_snprintf(char *str, size_t size, const char *format, ...) {
    va_list va;
    va_start(va, format);
    int result = PyOS_vsnprintf(str, size, format, va);
    va_end(va);
    return result;
}

PyObject *PyLong_FromLong(long value) {
    return py_int_from_i64((int64_t)value);
}

static PyObject *pcc_capi_uint_to_pyobject(unsigned long long value) {
    if (value <= (unsigned long long)INT64_MAX) {
        return py_int_from_i64((int64_t)value);
    }
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%llu", value);
    PyIntObject *big = py_bigint_from_cstr(tmp);
    return py_bigint_to_pyobject(big);
}

PyObject *PyLong_FromUnsignedLong(unsigned long value) {
    return pcc_capi_uint_to_pyobject((unsigned long long)value);
}

PyObject *PyLong_FromLongLong(long long value) {
    return py_int_from_i64((int64_t)value);
}

PyObject *PyLong_FromUnsignedLongLong(unsigned long long value) {
    return pcc_capi_uint_to_pyobject(value);
}

PyObject *PyLong_FromInt32(int32_t value) {
    return PyLong_FromLong((long)value);
}

PyObject *PyLong_FromInt64(int64_t value) {
    return PyLong_FromLongLong((long long)value);
}

PyObject *PyLong_FromUInt32(uint32_t value) {
    return PyLong_FromUnsignedLong((unsigned long)value);
}

PyObject *PyLong_FromUInt64(uint64_t value) {
    return PyLong_FromUnsignedLongLong((unsigned long long)value);
}

PyObject *PyLong_FromVoidPtr(void *value) {
    return pcc_capi_uint_to_pyobject((unsigned long long)(uintptr_t)value);
}

PyObject *PyLong_FromSsize_t(Py_ssize_t value) {
    return py_int_from_i64((int64_t)value);
}

PyObject *PyLong_FromSize_t(size_t value) {
    return pcc_capi_uint_to_pyobject((unsigned long long)value);
}

PyObject *PyLong_FromDouble(double value) {
    if (isnan(value)) {
        PyErr_SetString(PyExc_ValueError, "cannot convert NaN to integer");
        return NULL;
    }
    if (isinf(value)) {
        PyErr_SetString(PyExc_OverflowError, "cannot convert infinity to integer");
        return NULL;
    }
    if (
        value < (-9223372036854775807.0 - 1.0)
        || value >= 9223372036854775808.0
    ) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return NULL;
    }
    return py_int_from_i64((int64_t)value);
}

PyObject *PyBool_FromLong(long value) {
    PyObject *obj = py_bool_from_bit(value != 0);
    py_incref(obj);
    return obj;
}

int PyBool_Check(PyObject *obj) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_BOOL;
}

PyObject *PyFloat_FromDouble(double value) {
    return py_float_from_f64(value);
}

double PyFloat_AsDouble(PyObject *obj) {
    if (
        obj == NULL
        || (!PY_IS_TAGGED_INT(obj)
            && py_type_of(obj) != PY_TYPE_FLOAT
            && py_type_of(obj) != PY_TYPE_INT
            && py_type_of(obj) != PY_TYPE_BOOL)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected float-compatible object");
        return -1.0;
    }
    return py_float_to_f64(obj);
}

int PyFloat_Check(PyObject *obj) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_FLOAT;
}

int PyFloat_CheckExact(PyObject *obj) {
    return PyFloat_Check(obj);
}

static int pcc_capi_is_complex(PyObject *obj) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_COMPLEX;
}

PyObject *PyComplex_FromDoubles(double real, double imag) {
    return py_complex_new(real, imag);
}

PyObject *PyComplex_FromCComplex(Py_complex value) {
    return PyComplex_FromDoubles(value.real, value.imag);
}

double PyComplex_RealAsDouble(PyObject *obj) {
    if (pcc_capi_is_complex(obj)) {
        return ((PyComplexObject *)obj)->real;
    }
    return PyFloat_AsDouble(obj);
}

double PyComplex_ImagAsDouble(PyObject *obj) {
    if (pcc_capi_is_complex(obj)) {
        return ((PyComplexObject *)obj)->imag;
    }
    if (
        obj != NULL
        && (PY_IS_TAGGED_INT(obj)
            || py_type_of(obj) == PY_TYPE_INT
            || py_type_of(obj) == PY_TYPE_BOOL
            || py_type_of(obj) == PY_TYPE_FLOAT)
    ) {
        return 0.0;
    }
    PyErr_SetString(PyExc_TypeError, "expected complex-compatible object");
    return -1.0;
}

Py_complex PyComplex_AsCComplex(PyObject *obj) {
    Py_complex value;
    if (pcc_capi_is_complex(obj)) {
        value.real = ((PyComplexObject *)obj)->real;
        value.imag = ((PyComplexObject *)obj)->imag;
        return value;
    }
    if (
        obj != NULL
        && (PY_IS_TAGGED_INT(obj)
            || py_type_of(obj) == PY_TYPE_INT
            || py_type_of(obj) == PY_TYPE_BOOL
            || py_type_of(obj) == PY_TYPE_FLOAT)
    ) {
        value.real = PyFloat_AsDouble(obj);
        value.imag = 0.0;
        return value;
    }
    PyErr_SetString(PyExc_TypeError, "expected complex-compatible object");
    value.real = -1.0;
    value.imag = 0.0;
    return value;
}

int PyComplex_Check(PyObject *obj) {
    return pcc_capi_is_complex(obj);
}

int PyComplex_CheckExact(PyObject *obj) {
    return PyComplex_Check(obj);
}

long PyLong_AsLong(PyObject *obj) {
    if (obj == py_True) return 1;
    if (obj == py_False) return 0;
    int overflow = 0;
    int64_t value = py_int_to_i64(obj, &overflow);
    if (overflow) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return -1;
    }
    if (value < (int64_t)LONG_MIN || value > (int64_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return -1;
    }
    return (long)value;
}

int PyLong_AsInt(PyObject *obj) {
    long value = PyLong_AsLong(obj);
    if (py_err_occurred()) return -1;
    if (value < INT_MIN || value > INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return -1;
    }
    return (int)value;
}

int PyLong_AsInt32(PyObject *obj, int32_t *pvalue) {
    if (pvalue == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL int32 output pointer");
        return -1;
    }
    int value = PyLong_AsInt(obj);
    if (py_err_occurred()) return -1;
    *pvalue = (int32_t)value;
    return 0;
}

int PyLong_AsInt64(PyObject *obj, int64_t *pvalue) {
    if (pvalue == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL int64 output pointer");
        return -1;
    }
    long long value = PyLong_AsLongLong(obj);
    if (py_err_occurred()) return -1;
    *pvalue = (int64_t)value;
    return 0;
}

int PyLong_AsUInt32(PyObject *obj, uint32_t *pvalue) {
    if (pvalue == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL uint32 output pointer");
        return -1;
    }
    unsigned long value = PyLong_AsUnsignedLong(obj);
    if (py_err_occurred()) return -1;
    if (value > (unsigned long)UINT32_MAX) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return -1;
    }
    *pvalue = (uint32_t)value;
    return 0;
}

int PyLong_AsUInt64(PyObject *obj, uint64_t *pvalue) {
    if (pvalue == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL uint64 output pointer");
        return -1;
    }
    unsigned long long value = PyLong_AsUnsignedLongLong(obj);
    if (py_err_occurred()) return -1;
    *pvalue = (uint64_t)value;
    return 0;
}

int PyLong_AsLongAndOverflow(PyObject *obj, int *overflow) {
    if (obj == py_True || obj == py_False) {
        if (overflow != NULL) *overflow = 0;
        return obj == py_True ? 1 : 0;
    }
    int local_overflow = 0;
    int64_t value = py_int_to_i64(obj, &local_overflow);
    int direction = 0;
    if (local_overflow) {
        direction = 1;
        if (obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_INT) {
            PyIntObject *big = (PyIntObject *)obj;
            direction = big->sign < 0 ? -1 : 1;
        }
    } else if (value < (int64_t)LONG_MIN) {
        local_overflow = 1;
        direction = -1;
    } else if (value > (int64_t)LONG_MAX) {
        local_overflow = 1;
        direction = 1;
    }
    if (overflow != NULL) *overflow = local_overflow ? direction : 0;
    return local_overflow ? -1 : (long)value;
}

long long PyLong_AsLongLong(PyObject *obj) {
    if (obj == py_True) return 1;
    if (obj == py_False) return 0;
    int overflow = 0;
    int64_t value = py_int_to_i64(obj, &overflow);
    if (overflow) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return -1;
    }
    return (long long)value;
}

double PyLong_AsDouble(PyObject *obj) {
    if (
        obj == NULL
        || (!PY_IS_TAGGED_INT(obj)
            && py_type_of(obj) != PY_TYPE_INT
            && py_type_of(obj) != PY_TYPE_BOOL)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected int-compatible object");
        return -1.0;
    }
    return py_float_to_f64(obj);
}

static int pcc_capi_unsigned_from_pyobject(
    PyObject *obj,
    unsigned long long *out,
    int mask
) {
    if (out == NULL) return 0;
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected int");
        return 0;
    }
    if (PY_IS_TAGGED_INT(obj)) {
        int64_t value = py_untag_int(obj);
        if (!mask && value < 0) {
            PyErr_SetString(PyExc_OverflowError, "can't convert negative int to unsigned");
            return 0;
        }
        *out = (unsigned long long)value;
        return 1;
    }
    if (py_type_of(obj) == PY_TYPE_BOOL) {
        *out = obj == py_True ? 1u : 0u;
        return 1;
    }
    if (py_type_of(obj) != PY_TYPE_INT) {
        PyErr_SetString(PyExc_TypeError, "expected int");
        return 0;
    }
    PyIntObject *big = (PyIntObject *)obj;
    unsigned long long raw = 0;
    int ndigits = big->ndigits;
    if (ndigits > 2 && !mask) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return 0;
    }
    if (ndigits > 0) raw |= (unsigned long long)big->digits[0];
    if (ndigits > 1) raw |= ((unsigned long long)big->digits[1]) << 32;
    if (big->sign < 0) {
        if (!mask) {
            PyErr_SetString(PyExc_OverflowError, "can't convert negative int to unsigned");
            return 0;
        }
        raw = 0u - raw;
    }
    *out = raw;
    return 1;
}

unsigned long PyLong_AsUnsignedLong(PyObject *obj) {
    unsigned long long value = 0;
    if (!pcc_capi_unsigned_from_pyobject(obj, &value, 0)) return (unsigned long)-1;
    if (value > (unsigned long long)ULONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return (unsigned long)-1;
    }
    return (unsigned long)value;
}

unsigned long long PyLong_AsUnsignedLongLong(PyObject *obj) {
    unsigned long long value = 0;
    if (!pcc_capi_unsigned_from_pyobject(obj, &value, 0)) return (unsigned long long)-1;
    return value;
}

void *PyLong_AsVoidPtr(PyObject *obj) {
    unsigned long long value = PyLong_AsUnsignedLongLong(obj);
    if (py_err_occurred()) return NULL;
    if (value > (unsigned long long)UINTPTR_MAX) {
        PyErr_SetString(PyExc_OverflowError, "integer conversion overflow");
        return NULL;
    }
    return (void *)(uintptr_t)value;
}

unsigned long long PyLong_AsUnsignedLongLongMask(PyObject *obj) {
    unsigned long long value = 0;
    if (!pcc_capi_unsigned_from_pyobject(obj, &value, 1)) return (unsigned long long)-1;
    return value;
}

Py_ssize_t PyLong_AsSsize_t(PyObject *obj) {
    long value = PyLong_AsLong(obj);
    if (py_err_occurred()) return (Py_ssize_t)-1;
    return (Py_ssize_t)value;
}

size_t PyLong_AsSize_t(PyObject *obj) {
    unsigned long value = PyLong_AsUnsignedLong(obj);
    if (py_err_occurred()) return (size_t)-1;
    return (size_t)value;
}

int PyLong_Check(PyObject *obj) {
    if (obj == NULL) return 0;
    if (PY_IS_TAGGED_INT(obj)) return 1;
    int32_t tag = py_type_of(obj);
    return tag == PY_TYPE_INT || tag == PY_TYPE_BOOL;
}

int PyLong_CheckExact(PyObject *obj) {
    if (obj == NULL) return 0;
    if (PY_IS_TAGGED_INT(obj)) return 1;
    return py_type_of(obj) == PY_TYPE_INT;
}

PyObject *PyUnicode_FromString(const char *value) {
    if (value == NULL) value = "";
    return py_str_new(value, (int64_t)strlen(value));
}

PyObject *PyUnicode_FromStringAndSize(const char *value, Py_ssize_t len) {
    if (len < 0) {
        PyErr_SetString(PyExc_ValueError, "negative unicode size");
        return NULL;
    }
    if (value == NULL && len > 0) {
        PyErr_SetString(PyExc_ValueError, "NULL unicode data with nonzero size");
        return NULL;
    }
    return py_str_new(value, (int64_t)len);
}

PyObject *PyUnicode_New(Py_ssize_t size, Py_UCS4 maxchar) {
    (void)maxchar;
    if (size < 0) {
        PyErr_SetString(PyExc_SystemError, "negative PyUnicode_New size");
        return NULL;
    }
    if (size == 0) {
        return py_str_new("", 0);
    }
    PyErr_SetString(
        PyExc_NotImplementedError,
        "nonempty writable PyUnicode_New storage is not supported"
    );
    return NULL;
}

PyObject *PyUnicode_InternFromString(const char *value) {
    return PyUnicode_FromString(value);
}

static int pcc_capi_utf8_codepoint_len(uint32_t ch) {
    if (ch <= 0x7fU) return 1;
    if (ch <= 0x7ffU) return 2;
    if (ch <= 0xffffU) return 3;
    if (ch <= 0x10ffffU) return 4;
    return -1;
}

static int pcc_capi_utf8_write(char *out, uint32_t ch) {
    if (ch <= 0x7fU) {
        out[0] = (char)ch;
        return 1;
    }
    if (ch <= 0x7ffU) {
        out[0] = (char)(0xc0U | (ch >> 6));
        out[1] = (char)(0x80U | (ch & 0x3fU));
        return 2;
    }
    if (ch <= 0xffffU) {
        out[0] = (char)(0xe0U | (ch >> 12));
        out[1] = (char)(0x80U | ((ch >> 6) & 0x3fU));
        out[2] = (char)(0x80U | (ch & 0x3fU));
        return 3;
    }
    if (ch <= 0x10ffffU) {
        out[0] = (char)(0xf0U | (ch >> 18));
        out[1] = (char)(0x80U | ((ch >> 12) & 0x3fU));
        out[2] = (char)(0x80U | ((ch >> 6) & 0x3fU));
        out[3] = (char)(0x80U | (ch & 0x3fU));
        return 4;
    }
    return -1;
}

static int pcc_capi_unicode_kind_supported(int kind) {
    return kind == PyUnicode_1BYTE_KIND
        || kind == PyUnicode_2BYTE_KIND
        || kind == PyUnicode_4BYTE_KIND;
}

static uint32_t pcc_capi_unicode_read_kind(const void *buffer, int kind, Py_ssize_t i) {
    if (kind == PyUnicode_1BYTE_KIND) {
        return (uint32_t)((const Py_UCS1 *)buffer)[i];
    }
    if (kind == PyUnicode_2BYTE_KIND) {
        return (uint32_t)((const Py_UCS2 *)buffer)[i];
    }
    return ((const Py_UCS4 *)buffer)[i];
}

PyObject *PyUnicode_FromKindAndData(int kind, const void *buffer, Py_ssize_t size) {
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative unicode size");
        return NULL;
    }
    if (size == 0) {
        return py_str_new("", 0);
    }
    if (buffer == NULL) {
        PyErr_SetString(PyExc_ValueError, "NULL unicode data with nonzero size");
        return NULL;
    }
    if (!pcc_capi_unicode_kind_supported(kind)) {
        PyErr_SetString(PyExc_ValueError, "unsupported unicode kind");
        return NULL;
    }
    Py_ssize_t byte_len = 0;
    for (Py_ssize_t i = 0; i < size; i++) {
        int n = pcc_capi_utf8_codepoint_len(
            pcc_capi_unicode_read_kind(buffer, kind, i)
        );
        if (n < 0 || byte_len > ((Py_ssize_t)LONG_MAX - n)) {
            PyErr_SetString(PyExc_ValueError, "invalid unicode codepoint");
            return NULL;
        }
        byte_len += n;
    }
    char *utf8 = (char *)malloc((size_t)byte_len);
    if (utf8 == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    Py_ssize_t pos = 0;
    for (Py_ssize_t i = 0; i < size; i++) {
        int n = pcc_capi_utf8_write(
            utf8 + pos,
            pcc_capi_unicode_read_kind(buffer, kind, i)
        );
        if (n < 0) {
            free(utf8);
            PyErr_SetString(PyExc_ValueError, "invalid unicode codepoint");
            return NULL;
        }
        pos += n;
    }
    PyObject *out = py_str_new(utf8, (int64_t)byte_len);
    free(utf8);
    if (out == NULL) PyErr_NoMemory();
    return out;
}

PyObject *PyUnicode_FromOrdinal(int ordinal) {
    if (ordinal < 0 || ordinal > 0x10ffff) {
        PyErr_SetString(PyExc_ValueError, "unicode ordinal out of range");
        return NULL;
    }
    Py_UCS4 ch = (Py_UCS4)ordinal;
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, &ch, 1);
}

static int pcc_capi_utf8_next_u4(
    const unsigned char *data,
    int64_t len,
    int64_t *pos,
    Py_UCS4 *out
) {
    int64_t i = *pos;
    if (i < 0 || i >= len) return 0;
    unsigned char b0 = data[i];
    if (b0 < 0x80U) {
        *out = (Py_UCS4)b0;
        *pos = i + 1;
        return 1;
    }
    if (b0 >= 0xc2U && b0 <= 0xdfU) {
        if (i + 1 >= len) return -1;
        unsigned char b1 = data[i + 1];
        if ((b1 & 0xc0U) != 0x80U) return -1;
        *out = (Py_UCS4)(((uint32_t)(b0 & 0x1fU) << 6) | (b1 & 0x3fU));
        *pos = i + 2;
        return 1;
    }
    if (b0 >= 0xe0U && b0 <= 0xefU) {
        if (i + 2 >= len) return -1;
        unsigned char b1 = data[i + 1];
        unsigned char b2 = data[i + 2];
        if ((b1 & 0xc0U) != 0x80U || (b2 & 0xc0U) != 0x80U) return -1;
        if (b0 == 0xe0U && b1 < 0xa0U) return -1;
        if (b0 == 0xedU && b1 >= 0xa0U) return -1;
        *out = (Py_UCS4)(
            ((uint32_t)(b0 & 0x0fU) << 12)
            | ((uint32_t)(b1 & 0x3fU) << 6)
            | (b2 & 0x3fU)
        );
        *pos = i + 3;
        return 1;
    }
    if (b0 >= 0xf0U && b0 <= 0xf4U) {
        if (i + 3 >= len) return -1;
        unsigned char b1 = data[i + 1];
        unsigned char b2 = data[i + 2];
        unsigned char b3 = data[i + 3];
        if (
            (b1 & 0xc0U) != 0x80U
            || (b2 & 0xc0U) != 0x80U
            || (b3 & 0xc0U) != 0x80U
        ) {
            return -1;
        }
        if (b0 == 0xf0U && b1 < 0x90U) return -1;
        if (b0 == 0xf4U && b1 > 0x8fU) return -1;
        *out = (Py_UCS4)(
            ((uint32_t)(b0 & 0x07U) << 18)
            | ((uint32_t)(b1 & 0x3fU) << 12)
            | ((uint32_t)(b2 & 0x3fU) << 6)
            | (b3 & 0x3fU)
        );
        *pos = i + 4;
        return 1;
    }
    return -1;
}

Py_UCS4 pcc_capi_unicode_read(int kind, const void *data, Py_ssize_t index) {
    (void)kind;
    if (data == NULL || index < 0) return (Py_UCS4)-1;
    const unsigned char *raw = (const unsigned char *)data;
    const PyStrObject *owner = (const PyStrObject *)(
        (const char *)data - offsetof(PyStrObject, data)
    );
    int64_t byte_len = (int64_t)strlen((const char *)data);
    if (py_type_of((PyObject *)owner) == PY_TYPE_STR && owner->data == data) {
        byte_len = owner->byte_len;
    }
    int64_t pos = 0;
    Py_ssize_t current = 0;
    while (pos < byte_len) {
        Py_UCS4 ch = 0;
        if (pcc_capi_utf8_next_u4(raw, byte_len, &pos, &ch) <= 0) {
            return (Py_UCS4)-1;
        }
        if (current == index) return ch;
        current++;
    }
    return (Py_UCS4)-1;
}

static int pcc_unicode_writer_reserve(
    PyUnicodeWriter *writer,
    Py_ssize_t extra
) {
    if (writer == NULL || extra < 0 || writer->length > LONG_MAX - extra) {
        PyErr_SetString(PyExc_OverflowError, "unicode writer size overflow");
        return -1;
    }
    Py_ssize_t needed = writer->length + extra;
    if (needed <= writer->capacity) return 0;
    Py_ssize_t capacity = writer->capacity > 0 ? writer->capacity : 16;
    while (capacity < needed) {
        if (capacity > LONG_MAX / 2) {
            capacity = needed;
            break;
        }
        capacity *= 2;
    }
    char *resized = (char *)PyMem_Realloc(writer->data, (size_t)capacity + 1u);
    if (resized == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    writer->data = resized;
    writer->capacity = capacity;
    writer->data[writer->length] = '\0';
    return 0;
}

static int pcc_unicode_writer_append(
    PyUnicodeWriter *writer,
    const char *data,
    Py_ssize_t size
) {
    if (writer == NULL || size < 0 || (data == NULL && size > 0)) {
        PyErr_SetString(PyExc_SystemError, "invalid unicode writer append");
        return -1;
    }
    if (pcc_unicode_writer_reserve(writer, size) < 0) return -1;
    if (size > 0) memcpy(writer->data + writer->length, data, (size_t)size);
    writer->length += size;
    writer->data[writer->length] = '\0';
    return 0;
}

PyUnicodeWriter *PyUnicodeWriter_Create(Py_ssize_t length) {
    if (length < 0) {
        PyErr_SetString(PyExc_ValueError, "negative unicode writer length");
        return NULL;
    }
    PyUnicodeWriter *writer = (PyUnicodeWriter *)PyMem_Malloc(
        sizeof(PyUnicodeWriter)
    );
    if (writer == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    writer->data = NULL;
    writer->length = 0;
    writer->capacity = 0;
    if (length > 0 && pcc_unicode_writer_reserve(writer, length) < 0) {
        PyMem_Free(writer);
        return NULL;
    }
    return writer;
}

PyObject *PyUnicodeWriter_Finish(PyUnicodeWriter *writer) {
    if (writer == NULL) {
        PyErr_SetString(PyExc_SystemError, "NULL unicode writer");
        return NULL;
    }
    PyObject *result = py_str_new(writer->data, (int64_t)writer->length);
    PyMem_Free(writer->data);
    PyMem_Free(writer);
    if (result == NULL) PyErr_NoMemory();
    return result;
}

void PyUnicodeWriter_Discard(PyUnicodeWriter *writer) {
    if (writer == NULL) return;
    PyMem_Free(writer->data);
    PyMem_Free(writer);
}

int PyUnicodeWriter_WriteChar(PyUnicodeWriter *writer, Py_UCS4 ch) {
    char encoded[4];
    int size = pcc_capi_utf8_write(encoded, ch);
    if (size < 0 || (ch >= 0xd800U && ch <= 0xdfffU)) {
        PyErr_SetString(PyExc_ValueError, "invalid unicode codepoint");
        return -1;
    }
    return pcc_unicode_writer_append(writer, encoded, (Py_ssize_t)size);
}

int PyUnicodeWriter_WriteUTF8(
    PyUnicodeWriter *writer,
    const char *str,
    Py_ssize_t size
) {
    if (str == NULL) {
        PyErr_SetString(PyExc_SystemError, "NULL UTF-8 input");
        return -1;
    }
    if (size == -1) size = (Py_ssize_t)strlen(str);
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative UTF-8 size");
        return -1;
    }
    int64_t pos = 0;
    while (pos < (int64_t)size) {
        Py_UCS4 ch = 0;
        if (
            pcc_capi_utf8_next_u4(
                (const unsigned char *)str,
                (int64_t)size,
                &pos,
                &ch
            ) <= 0
        ) {
            PyErr_SetString(PyExc_UnicodeDecodeError, "invalid UTF-8 input");
            return -1;
        }
    }
    return pcc_unicode_writer_append(writer, str, size);
}

int PyUnicodeWriter_WriteStr(PyUnicodeWriter *writer, PyObject *obj) {
    PyObject *text = PyObject_Str(obj);
    if (text == NULL) return -1;
    Py_ssize_t size = 0;
    const char *data = PyUnicode_AsUTF8AndSize(text, &size);
    int result = data == NULL ? -1 : pcc_unicode_writer_append(writer, data, size);
    py_decref(text);
    return result;
}

int PyUnicodeWriter_WriteSubstring(
    PyUnicodeWriter *writer,
    PyObject *str,
    Py_ssize_t start,
    Py_ssize_t end
) {
    PyObject *text = PyUnicode_Substring(str, start, end);
    if (text == NULL) return -1;
    Py_ssize_t size = 0;
    const char *data = PyUnicode_AsUTF8AndSize(text, &size);
    int result = data == NULL ? -1 : pcc_unicode_writer_append(writer, data, size);
    py_decref(text);
    return result;
}

static Py_ssize_t pcc_capi_unicode_ucs4_len(PyObject *unicode) {
    const unsigned char *raw = (const unsigned char *)py_str_utf8(unicode);
    int64_t byte_len = py_str_byte_len(unicode);
    int64_t pos = 0;
    Py_ssize_t count = 0;
    while (pos < byte_len) {
        Py_UCS4 ch = 0;
        int ok = pcc_capi_utf8_next_u4(raw, byte_len, &pos, &ch);
        (void)ch;
        if (ok <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid UTF-8 string data");
            return -1;
        }
        count++;
    }
    return count;
}

Py_UCS4 PyUnicode_ReadChar(PyObject *unicode, Py_ssize_t index) {
    if (!pcc_capi_is_exact_type(unicode, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return (Py_UCS4)-1;
    }
    if (index < 0) {
        PyErr_SetString(PyExc_IndexError, "string index out of range");
        return (Py_UCS4)-1;
    }
    const unsigned char *raw = (const unsigned char *)py_str_utf8(unicode);
    int64_t byte_len = py_str_byte_len(unicode);
    int64_t pos = 0;
    Py_ssize_t current = 0;
    while (pos < byte_len) {
        Py_UCS4 ch = 0;
        int ok = pcc_capi_utf8_next_u4(raw, byte_len, &pos, &ch);
        if (ok <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid UTF-8 string data");
            return (Py_UCS4)-1;
        }
        if (current == index) return ch;
        current++;
    }
    PyErr_SetString(PyExc_IndexError, "string index out of range");
    return (Py_UCS4)-1;
}

static Py_ssize_t pcc_capi_unicode_clamp_index(Py_ssize_t index, Py_ssize_t len) {
    if (index < 0) index += len;
    if (index < 0) return 0;
    if (index > len) return len;
    return index;
}

Py_ssize_t PyUnicode_FindChar(
    PyObject *str,
    Py_UCS4 ch,
    Py_ssize_t start,
    Py_ssize_t end,
    int direction
) {
    if (!pcc_capi_is_exact_type(str, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -2;
    }
    if (ch > 0x10ffffU) {
        PyErr_SetString(PyExc_ValueError, "unicode character out of range");
        return -2;
    }
    Py_ssize_t len = pcc_capi_unicode_ucs4_len(str);
    if (len < 0) return -2;
    start = pcc_capi_unicode_clamp_index(start, len);
    end = pcc_capi_unicode_clamp_index(end, len);
    if (end < start) end = start;

    const unsigned char *raw = (const unsigned char *)py_str_utf8(str);
    int64_t byte_len = py_str_byte_len(str);
    int64_t pos = 0;
    Py_ssize_t index = 0;
    Py_ssize_t found = -1;
    while (pos < byte_len) {
        Py_UCS4 current = 0;
        int ok = pcc_capi_utf8_next_u4(raw, byte_len, &pos, &current);
        if (ok <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid UTF-8 string data");
            return -2;
        }
        if (index >= start && index < end && current == ch) {
            found = index;
            if (direction >= 0) return found;
        }
        index++;
    }
    return found;
}

Py_ssize_t PyUnicode_Find(
    PyObject *str,
    PyObject *substr,
    Py_ssize_t start,
    Py_ssize_t end,
    int direction
) {
    if (
        !pcc_capi_is_exact_type(str, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(substr, PY_TYPE_STR)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -2;
    }
    Py_ssize_t len = pcc_capi_unicode_ucs4_len(str);
    if (len < 0) return -2;
    start = pcc_capi_unicode_clamp_index(start, len);
    end = pcc_capi_unicode_clamp_index(end, len);
    if (end < start) end = start;
    PyObject *window = PyUnicode_Substring(str, start, end);
    if (window == NULL) return -2;
    int64_t found = direction < 0
        ? py_str_rfind(window, substr)
        : py_str_find(window, substr);
    py_decref(window);
    if (found < 0) return -1;
    return start + (Py_ssize_t)found;
}

Py_ssize_t PyUnicode_Count(
    PyObject *str,
    PyObject *substr,
    Py_ssize_t start,
    Py_ssize_t end
) {
    if (
        !pcc_capi_is_exact_type(str, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(substr, PY_TYPE_STR)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    Py_ssize_t len = pcc_capi_unicode_ucs4_len(str);
    if (len < 0) return -1;
    start = pcc_capi_unicode_clamp_index(start, len);
    end = pcc_capi_unicode_clamp_index(end, len);
    if (end < start) end = start;
    PyObject *window = PyUnicode_Substring(str, start, end);
    if (window == NULL) return -1;
    Py_ssize_t count = py_str_byte_len(substr) == 0
        ? PyUnicode_GetLength(window) + 1
        : (Py_ssize_t)py_str_count(window, substr);
    py_decref(window);
    return count;
}

Py_UCS4 *PyUnicode_AsUCS4(
    PyObject *unicode,
    Py_UCS4 *buffer,
    Py_ssize_t buflen,
    int copy_null
) {
    if (!pcc_capi_is_exact_type(unicode, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    if (buffer == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected UCS4 buffer");
        return NULL;
    }
    if (buflen < 0) {
        PyErr_SetString(PyExc_SystemError, "negative UCS4 buffer length");
        return NULL;
    }
    Py_ssize_t len = pcc_capi_unicode_ucs4_len(unicode);
    if (len < 0) return NULL;
    Py_ssize_t required = len + (copy_null ? 1 : 0);
    if (buflen < required) {
        PyErr_SetString(PyExc_SystemError, "string is longer than the UCS4 buffer");
        return NULL;
    }
    const unsigned char *raw = (const unsigned char *)py_str_utf8(unicode);
    int64_t byte_len = py_str_byte_len(unicode);
    int64_t pos = 0;
    Py_ssize_t out = 0;
    while (pos < byte_len) {
        Py_UCS4 ch = 0;
        int ok = pcc_capi_utf8_next_u4(raw, byte_len, &pos, &ch);
        if (ok <= 0) {
            PyErr_SetString(PyExc_ValueError, "invalid UTF-8 string data");
            return NULL;
        }
        buffer[out++] = ch;
    }
    if (copy_null) buffer[out] = 0;
    return buffer;
}

Py_UCS4 *PyUnicode_AsUCS4Copy(PyObject *unicode) {
    if (!pcc_capi_is_exact_type(unicode, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    Py_ssize_t len = pcc_capi_unicode_ucs4_len(unicode);
    if (len < 0) return NULL;
    if ((size_t)len > (SIZE_MAX / sizeof(Py_UCS4)) - 1u) {
        PyErr_NoMemory();
        return NULL;
    }
    Py_UCS4 *buffer = (Py_UCS4 *)PyMem_Malloc(((size_t)len + 1u) * sizeof(Py_UCS4));
    if (buffer == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    if (PyUnicode_AsUCS4(unicode, buffer, len + 1, 1) == NULL) {
        PyMem_Free(buffer);
        return NULL;
    }
    return buffer;
}

PyObject *PyUnicode_FromEncodedObject(
    PyObject *obj,
    const char *encoding,
    const char *errors
) {
    (void)errors;
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected str or bytes");
        return NULL;
    }
    if (pcc_capi_is_exact_type(obj, PY_TYPE_STR)) {
        Py_INCREF(obj);
        return obj;
    }
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_BYTES)) {
        PyErr_SetString(PyExc_TypeError, "expected str or bytes");
        return NULL;
    }
    if (
        encoding != NULL
        && strcmp(encoding, "utf-8") != 0
        && strcmp(encoding, "UTF-8") != 0
        && strcmp(encoding, "ascii") != 0
        && strcmp(encoding, "ASCII") != 0
        && strcmp(encoding, "latin-1") != 0
        && strcmp(encoding, "latin1") != 0
    ) {
        PyErr_SetString(PyExc_ValueError, "unsupported encoding");
        return NULL;
    }
    PyBytesObject *bytes = (PyBytesObject *)obj;
    return py_str_new(bytes->data, bytes->byte_len);
}

PyObject *PyUnicode_DecodeUTF8(
    const char *str,
    Py_ssize_t size,
    const char *errors
) {
    if (str == NULL || size < 0) {
        PyErr_SetString(PyExc_TypeError, "invalid UTF-8 input");
        return NULL;
    }
    if (errors != NULL && strcmp(errors, "strict") != 0) {
        PyErr_SetString(PyExc_ValueError, "unsupported UTF-8 error handler");
        return NULL;
    }
    int64_t pos = 0;
    while (pos < (int64_t)size) {
        Py_UCS4 ch = 0;
        if (
            pcc_capi_utf8_next_u4(
                (const unsigned char *)str,
                (int64_t)size,
                &pos,
                &ch
            ) <= 0
        ) {
            PyErr_SetString(PyExc_UnicodeDecodeError, "invalid UTF-8 input");
            return NULL;
        }
    }
    return py_str_new(str, (int64_t)size);
}

PyObject *PyUnicode_Decode(
    const char *str,
    Py_ssize_t size,
    const char *encoding,
    const char *errors
) {
    if (
        encoding == NULL
        || strcmp(encoding, "utf-8") == 0
        || strcmp(encoding, "UTF-8") == 0
    ) {
        return PyUnicode_DecodeUTF8(str, size, errors);
    }
    if (str == NULL || size < 0) {
        PyErr_SetString(PyExc_TypeError, "invalid encoded input");
        return NULL;
    }
    if (errors != NULL && strcmp(errors, "strict") != 0) {
        PyErr_SetString(PyExc_ValueError, "unsupported decode error handler");
        return NULL;
    }
    if (strcmp(encoding, "ascii") == 0 || strcmp(encoding, "ASCII") == 0) {
        for (Py_ssize_t i = 0; i < size; i++) {
            if ((unsigned char)str[i] > 0x7fU) {
                PyErr_SetString(PyExc_UnicodeDecodeError, "invalid ASCII input");
                return NULL;
            }
        }
        return py_str_new(str, (int64_t)size);
    }
    if (strcmp(encoding, "latin-1") == 0 || strcmp(encoding, "latin1") == 0) {
        return PyUnicode_FromKindAndData(PyUnicode_1BYTE_KIND, str, size);
    }
    PyErr_SetString(PyExc_ValueError, "unsupported encoding");
    return NULL;
}

static int pcc_capi_encoding_is(const char *encoding, const char *a, const char *b) {
    if (encoding == NULL) return strcmp(a, "utf-8") == 0;
    return strcmp(encoding, a) == 0 || (b != NULL && strcmp(encoding, b) == 0);
}

const char *PyUnicode_AsUTF8(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || py_type_of(obj) != PY_TYPE_STR) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    return py_str_utf8(obj);
}

const char *PyUnicode_AsUTF8AndSize(PyObject *obj, Py_ssize_t *size) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || py_type_of(obj) != PY_TYPE_STR) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    if (size != NULL) {
        *size = (Py_ssize_t)py_str_byte_len(obj);
    }
    return py_str_utf8(obj);
}

PyObject *PyUnicode_AsUTF8String(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    return py_bytes_new(py_str_utf8(obj), py_str_byte_len(obj));
}

PyObject *PyUnicode_AsASCIIString(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    const char *raw = py_str_utf8(obj);
    int64_t n = py_str_byte_len(obj);
    for (int64_t i = 0; i < n; i++) {
        if (((unsigned char)raw[i]) > 0x7f) {
            PyErr_SetString(PyExc_ValueError, "non-ascii character");
            return NULL;
        }
    }
    return py_bytes_new(raw, n);
}

PyObject *PyUnicode_AsEncodedString(
    PyObject *obj,
    const char *encoding,
    const char *errors
) {
    (void)errors;
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    if (
        pcc_capi_encoding_is(encoding, "utf-8", "UTF-8")
        || pcc_capi_encoding_is(encoding, "utf8", "UTF8")
    ) {
        return PyUnicode_AsUTF8String(obj);
    }
    if (pcc_capi_encoding_is(encoding, "ascii", "ASCII")) {
        return PyUnicode_AsASCIIString(obj);
    }
    if (
        pcc_capi_encoding_is(encoding, "latin-1", "LATIN-1")
        || pcc_capi_encoding_is(encoding, "latin1", "LATIN1")
    ) {
        PyObject *out = py_str_latin1_encode(obj);
        if (out == NULL && PyErr_Occurred() == NULL) {
            PyErr_SetString(PyExc_ValueError, "cannot encode latin-1");
        }
        return out;
    }
    PyErr_SetString(PyExc_ValueError, "unsupported encoding");
    return NULL;
}

PyObject *PyUnicode_Substring(PyObject *str, Py_ssize_t start, Py_ssize_t end) {
    if (!pcc_capi_is_exact_type(str, PY_TYPE_STR)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    PyObject *lo = py_int_from_i64((int64_t)start);
    PyObject *hi = py_int_from_i64((int64_t)end);
    if (lo == NULL || hi == NULL) {
        py_decref(lo);
        py_decref(hi);
        return NULL;
    }
    PyObject *out = py_str_slice(str, lo, hi, NULL);
    py_decref(lo);
    py_decref(hi);
    return out;
}

PyObject *PyUnicode_Replace(
    PyObject *str,
    PyObject *substr,
    PyObject *replstr,
    Py_ssize_t maxcount
) {
    if (
        !pcc_capi_is_exact_type(str, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(substr, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(replstr, PY_TYPE_STR)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    return py_str_replace_count(str, substr, replstr, (int64_t)maxcount);
}

int PyUnicode_Contains(PyObject *container, PyObject *element) {
    if (
        !pcc_capi_is_exact_type(container, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(element, PY_TYPE_STR)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    return py_str_contains(container, element) ? 1 : 0;
}

Py_ssize_t PyUnicode_Tailmatch(
    PyObject *str,
    PyObject *substr,
    Py_ssize_t start,
    Py_ssize_t end,
    int direction
) {
    if (
        !pcc_capi_is_exact_type(str, PY_TYPE_STR)
        || !pcc_capi_is_exact_type(substr, PY_TYPE_STR)
    ) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    int64_t length = py_str_len(str);
    if (start < 0) start += (Py_ssize_t)length;
    if (start < 0) start = 0;
    if (end < 0 || end > (Py_ssize_t)length) end = (Py_ssize_t)length;
    if (end < start) end = start;
    PyObject *window = PyUnicode_Substring(str, start, end);
    if (window == NULL) return -1;
    int64_t matched = direction < 0
        ? py_str_endswith(window, substr)
        : py_str_startswith(window, substr);
    py_decref(window);
    return matched ? 1 : 0;
}

int PyUnicode_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_STR);
}

int PyUnicode_CheckExact(PyObject *obj) {
    return PyUnicode_Check(obj);
}

Py_ssize_t PyUnicode_GetLength(PyObject *obj) {
    if (!PyUnicode_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    return (Py_ssize_t)py_str_len(obj);
}

int PyUnicode_Compare(PyObject *left, PyObject *right) {
    if (!PyUnicode_Check(left) || !PyUnicode_Check(right)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return -1;
    }
    if (py_obj_eq(left, right)) return 0;
    return py_obj_lt(left, right) ? -1 : 1;
}

int PyUnicode_CompareWithASCIIString(PyObject *left, const char *right) {
    if (right == NULL) right = "";
    PyObject *right_obj = PyUnicode_FromString(right);
    if (right_obj == NULL) return -1;
    int result = PyUnicode_Compare(left, right_obj);
    py_decref(right_obj);
    return result;
}

PyObject *PyUnicode_Concat(PyObject *left, PyObject *right) {
    if (!PyUnicode_Check(left) || !PyUnicode_Check(right)) {
        PyErr_SetString(PyExc_TypeError, "expected str");
        return NULL;
    }
    return py_str_concat(left, right);
}

int PyUnicode_EqualToUTF8AndSize(
    PyObject *unicode,
    const char *str,
    Py_ssize_t str_len
) {
    PyObject *exc_type = NULL;
    PyObject *exc_value = NULL;
    PyObject *exc_traceback = NULL;
    PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);

    int result = 0;
    Py_ssize_t actual_len = 0;
    const char *actual = PyUnicode_AsUTF8AndSize(unicode, &actual_len);
    if (actual != NULL && str != NULL && actual_len == str_len) {
        result = memcmp(actual, str, (size_t)actual_len) == 0 ? 1 : 0;
    }

    PyErr_Restore(exc_type, exc_value, exc_traceback);
    return result;
}

int PyUnicode_EqualToUTF8(PyObject *unicode, const char *str) {
    if (str == NULL) return 0;
    return PyUnicode_EqualToUTF8AndSize(unicode, str, (Py_ssize_t)strlen(str));
}

static PyObject *pcc_capi_list_sort_key_call(PyObject *key, PyObject *item) {
    if (key == NULL || key == py_None) {
        py_incref(item);
        return item;
    }
    PyObject *args = py_tuple_new(1);
    if (args == NULL) return NULL;
    py_tuple_set_item(args, 0, item);
    PyObject *result = py_obj_call(key, args, py_None);
    py_decref(args);
    return result;
}

static PyObject *pcc_capi_list_sort_method(
    PyObject *self,
    PyObject *args,
    PyObject *kwargs
) {
    if (self == NULL || py_type_of(self) != PY_TYPE_LIST) {
        PyErr_SetString(PyExc_TypeError, "list.sort receiver must be a list");
        return NULL;
    }
    if (args != NULL && py_tuple_len(args) != 0) {
        PyErr_SetString(PyExc_TypeError, "list.sort takes no positional arguments");
        return NULL;
    }
    PyObject *key = NULL;
    if (kwargs != NULL && kwargs != py_None) {
        PyObject *key_name = py_str_new("key", 3);
        key = py_dict_get(kwargs, key_name);
        py_decref(key_name);
        if (key == NULL && py_err_occurred()) return NULL;
    }

    int64_t length = py_list_len(self);
    for (int64_t i = 1; i < length; i++) {
        PyObject *current = py_list_get(self, i);
        PyObject *current_key = pcc_capi_list_sort_key_call(key, current);
        if (current == NULL || current_key == NULL) {
            py_decref(current);
            py_decref(current_key);
            py_decref(key);
            return NULL;
        }
        int64_t j = i;
        while (j > 0) {
            PyObject *previous = py_list_get(self, j - 1);
            PyObject *previous_key = pcc_capi_list_sort_key_call(key, previous);
            if (previous == NULL || previous_key == NULL) {
                py_decref(previous);
                py_decref(previous_key);
                py_decref(current);
                py_decref(current_key);
                py_decref(key);
                return NULL;
            }
            int64_t move = py_obj_lt(current_key, previous_key);
            py_decref(previous_key);
            if (py_err_occurred()) {
                py_decref(previous);
                py_decref(current);
                py_decref(current_key);
                py_decref(key);
                return NULL;
            }
            if (!move) {
                py_decref(previous);
                break;
            }
            py_list_set(self, j, previous);
            py_decref(previous);
            j--;
        }
        py_list_set(self, j, current);
        py_decref(current);
        py_decref(current_key);
    }
    py_decref(key);
    py_incref(py_None);
    return py_None;
}

static PyMethodDef pcc_capi_list_sort_def = {
    "sort",
    (PyCFunction)pcc_capi_list_sort_method,
    METH_VARARGS | METH_KEYWORDS,
    NULL,
};

PyObject *pcc_capi_builtin_object_getattr(PyObject *o, const char *name) {
    if (o == NULL || name == NULL || PY_IS_TAGGED_INT(o)) return NULL;
    if (py_type_of(o) == PY_TYPE_LIST && strcmp(name, "sort") == 0) {
        return pcc_capi_method_func_new(o, &pcc_capi_list_sort_def);
    }
    return NULL;
}

PyObject *PyObject_GetAttr(PyObject *obj, PyObject *attr) {
    const char *name = PyUnicode_AsUTF8(attr);
    if (name == NULL) return NULL;
    return PyObject_GetAttrString(obj, name);
}

PyObject *PyObject_GetAttrString(PyObject *obj, const char *attr) {
    if (obj == NULL || attr == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_GetAttrString call");
        return NULL;
    }
    PyObject *builtin_attr = pcc_capi_builtin_object_getattr(obj, attr);
    if (builtin_attr != NULL || py_err_occurred()) return builtin_attr;
    return py_obj_getattr(obj, attr);
}

int PyObject_GetOptionalAttr(PyObject *obj, PyObject *attr, PyObject **result) {
    if (result == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL result pointer");
        return -1;
    }
    *result = PyObject_GetAttr(obj, attr);
    if (*result != NULL) return 1;
    if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
        PyErr_Clear();
        return 0;
    }
    return py_err_occurred() ? -1 : 0;
}

int PyObject_GetOptionalAttrString(
    PyObject *obj,
    const char *attr,
    PyObject **result
) {
    if (result == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL result pointer");
        return -1;
    }
    *result = PyObject_GetAttrString(obj, attr);
    if (*result != NULL) return 1;
    if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
        PyErr_Clear();
        return 0;
    }
    return py_err_occurred() ? -1 : 0;
}

int PyObject_SetAttr(PyObject *obj, PyObject *attr, PyObject *value) {
    const char *name = PyUnicode_AsUTF8(attr);
    if (name == NULL) return -1;
    return PyObject_SetAttrString(obj, name, value);
}

int PyObject_SetAttrString(PyObject *obj, const char *attr, PyObject *value) {
    if (obj == NULL || attr == NULL || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_SetAttrString call");
        return -1;
    }
    return py_obj_setattr(obj, attr, value) == 0 ? 0 : -1;
}

int PyObject_HasAttrWithError(PyObject *obj, PyObject *attr) {
    PyObject *value = PyObject_GetAttr(obj, attr);
    if (value == NULL) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            return 0;
        }
        return py_err_occurred() ? -1 : 0;
    }
    py_decref(value);
    return 1;
}

int PyObject_HasAttrStringWithError(PyObject *obj, const char *attr) {
    PyObject *value = PyObject_GetAttrString(obj, attr);
    if (value == NULL) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            return 0;
        }
        return py_err_occurred() ? -1 : 0;
    }
    py_decref(value);
    return 1;
}

int PyObject_HasAttr(PyObject *obj, PyObject *attr) {
    int rc = PyObject_HasAttrWithError(obj, attr);
    if (rc < 0) {
        PyErr_Clear();
        return 0;
    }
    return rc;
}

int PyObject_HasAttrString(PyObject *obj, const char *attr) {
    int rc = PyObject_HasAttrStringWithError(obj, attr);
    if (rc < 0) {
        PyErr_Clear();
        return 0;
    }
    return rc;
}

int PyObject_IsTrue(PyObject *obj) {
    int64_t truth = py_obj_truthy(obj);
    if (py_err_occurred()) return -1;
    return truth ? 1 : 0;
}

int PyObject_Not(PyObject *obj) {
    int truth = PyObject_IsTrue(obj);
    if (truth < 0) return -1;
    return truth ? 0 : 1;
}

static int pcc_capi_is_intlike(PyObject *obj) {
    if (obj == NULL) return 0;
    if (PY_IS_TAGGED_INT(obj)) return 1;
    int32_t tag = py_type_of(obj);
    return tag == PY_TYPE_INT || tag == PY_TYPE_BOOL;
}

static int pcc_capi_is_floatlike(PyObject *obj) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_FLOAT;
}

static int pcc_capi_is_numberlike(PyObject *obj) {
    return pcc_capi_is_intlike(obj) || pcc_capi_is_floatlike(obj);
}

static PccCapiNumberMethods *pcc_capi_number_methods(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return NULL;
    PyTypeObject *type = pcc_capi_type(obj);
    if (type == NULL) return NULL;
    return (PccCapiNumberMethods *)type->tp_as_number;
}

static PyObject *pcc_capi_numeric_error(const char *op);

static PyObject *pcc_capi_call_binary_number_slot(
    void *slot,
    PyObject *left,
    PyObject *right,
    const char *slot_name
) {
    PyObject *(*binary)(PyObject *, PyObject *) = (
        PyObject *(*)(PyObject *, PyObject *)
    )slot;
    PyObject *result = binary(left, right);
    if (result == NULL && !py_err_occurred()) {
        PyErr_Format(
            PyExc_RuntimeError,
            "%s returned NULL without setting an exception",
            slot_name
        );
    }
    return result;
}

static void *pcc_capi_binary_number_slot(
    PccCapiNumberMethods *methods,
    int64_t op
) {
    if (methods == NULL) return NULL;
    switch (op) {
        case 0: return methods->nb_add;
        case 1: return methods->nb_subtract;
        case 2: return methods->nb_multiply;
        case 3: return methods->nb_true_divide;
        default: return NULL;
    }
}

static const char *pcc_capi_binary_number_slot_name(int64_t op) {
    switch (op) {
        case 0: return "nb_add";
        case 1: return "nb_subtract";
        case 2: return "nb_multiply";
        case 3: return "nb_true_divide";
        default: return "binary number slot";
    }
}

static const char *pcc_capi_binary_number_symbol(int64_t op) {
    switch (op) {
        case 0: return "+";
        case 1: return "-";
        case 2: return "*";
        case 3: return "/";
        default: return "binary operation";
    }
}

PyObject *pcc_capi_cext_binary_number(
    PyObject *left,
    PyObject *right,
    int64_t op
) {
    if (op < 0 || op > 3) {
        PyErr_SetString(PyExc_ValueError, "invalid binary number operation");
        return NULL;
    }
    PyTypeObject *left_type = pcc_capi_cext_type_for_object(left);
    PyTypeObject *right_type = pcc_capi_cext_type_for_object(right);
    PccCapiNumberMethods *left_methods = left_type == NULL
        ? NULL
        : (PccCapiNumberMethods *)left_type->tp_as_number;
    PccCapiNumberMethods *right_methods = right_type == NULL
        ? NULL
        : (PccCapiNumberMethods *)right_type->tp_as_number;
    void *left_slot = pcc_capi_binary_number_slot(left_methods, op);
    void *right_slot = pcc_capi_binary_number_slot(right_methods, op);
    const char *slot_name = pcc_capi_binary_number_slot_name(op);

    if (left_type == right_type || right_slot == left_slot) {
        right_slot = NULL;
    }
    if (
        right_slot != NULL
        && left_type != NULL
        && PyType_IsSubtype(right_type, left_type)
    ) {
        PyObject *result = pcc_capi_call_binary_number_slot(
            right_slot, left, right, slot_name
        );
        if (result == NULL) return NULL;
        if (result != py_NotImplemented) return result;
        py_decref(result);
        right_slot = NULL;
    }
    if (left_slot != NULL) {
        PyObject *result = pcc_capi_call_binary_number_slot(
            left_slot, left, right, slot_name
        );
        if (result == NULL) return NULL;
        if (result != py_NotImplemented) return result;
        py_decref(result);
    }
    if (right_slot != NULL) {
        PyObject *result = pcc_capi_call_binary_number_slot(
            right_slot, left, right, slot_name
        );
        if (result == NULL) return NULL;
        if (result != py_NotImplemented) return result;
        py_decref(result);
    }
    return pcc_capi_numeric_error(pcc_capi_binary_number_symbol(op));
}

PyObject *pcc_capi_cext_subtract(PyObject *left, PyObject *right) {
    return pcc_capi_cext_binary_number(left, right, 1);
}

PyObject *pcc_capi_cext_absolute(PyObject *value) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(value);
    PccCapiNumberMethods *methods = type == NULL
        ? NULL
        : (PccCapiNumberMethods *)type->tp_as_number;
    void *slot = methods == NULL ? NULL : methods->nb_absolute;
    if (slot == NULL) return pcc_capi_numeric_error("abs()");

    PyObject *(*unary)(PyObject *) = (PyObject *(*)(PyObject *))slot;
    PyObject *result = unary(value);
    if (result == NULL) {
        if (!py_err_occurred()) {
            PyErr_SetString(
                PyExc_RuntimeError,
                "nb_absolute returned NULL without setting an exception"
            );
        }
        return NULL;
    }
    if (result == py_NotImplemented) {
        py_decref(result);
        return pcc_capi_numeric_error("abs()");
    }
    return result;
}

int64_t pcc_capi_cext_truthy(PyObject *value) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(value);
    PccCapiNumberMethods *methods = type == NULL
        ? NULL
        : (PccCapiNumberMethods *)type->tp_as_number;
    void *slot = methods == NULL ? NULL : methods->nb_bool;
    if (slot == NULL) return 1;

    int (*truth)(PyObject *) = (int (*)(PyObject *))slot;
    int result = truth(value);
    if (result < 0) {
        if (!py_err_occurred()) {
            PyErr_SetString(
                PyExc_RuntimeError,
                "nb_bool returned a negative result without an exception"
            );
        }
        return -1;
    }
    return result != 0 ? 1 : 0;
}

static int pcc_capi_swapped_richcompare_op(int op) {
    static const int swapped[6] = {Py_GT, Py_GE, Py_EQ, Py_NE, Py_LT, Py_LE};
    return op >= Py_LT && op <= Py_GE ? swapped[op] : -1;
}

static int64_t pcc_capi_call_richcompare_slot(
    void *slot,
    PyObject *left,
    PyObject *right,
    int op
) {
    PyObject *(*richcompare)(PyObject *, PyObject *, int) = (
        PyObject *(*)(PyObject *, PyObject *, int)
    )slot;
    PyObject *result = richcompare(left, right, op);
    if (result == NULL) {
        if (!py_err_occurred()) {
            PyErr_SetString(
                PyExc_RuntimeError,
                "tp_richcompare returned NULL without setting an exception"
            );
        }
        return -1;
    }
    if (result == py_NotImplemented) {
        py_decref(result);
        return -2;
    }
    int64_t truth = py_obj_truthy(result);
    py_decref(result);
    if (py_err_occurred()) return -1;
    return truth ? 1 : 0;
}

int64_t pcc_capi_cext_richcompare_bool(
    PyObject *left,
    PyObject *right,
    int64_t op
) {
    int swapped_op = pcc_capi_swapped_richcompare_op((int)op);
    if (swapped_op < 0) {
        PyErr_SetString(PyExc_ValueError, "invalid rich-compare operation");
        return -1;
    }
    PyTypeObject *left_type = pcc_capi_cext_type_for_object(left);
    PyTypeObject *right_type = pcc_capi_cext_type_for_object(right);
    void *left_slot = left_type == NULL ? NULL : left_type->tp_richcompare;
    void *right_slot = right_type == NULL ? NULL : right_type->tp_richcompare;
    if (left_type == right_type || right_slot == left_slot) right_slot = NULL;

    if (
        right_slot != NULL
        && left_type != NULL
        && PyType_IsSubtype(right_type, left_type)
    ) {
        int64_t result = pcc_capi_call_richcompare_slot(
            right_slot, right, left, swapped_op
        );
        if (result != -2) return result;
        right_slot = NULL;
    }
    if (left_slot != NULL) {
        int64_t result = pcc_capi_call_richcompare_slot(
            left_slot, left, right, (int)op
        );
        if (result != -2) return result;
    }
    if (right_slot != NULL) {
        int64_t result = pcc_capi_call_richcompare_slot(
            right_slot, right, left, swapped_op
        );
        if (result != -2) return result;
    }
    if (op == Py_EQ) return left == right ? 1 : 0;
    if (op == Py_NE) return left == right ? 0 : 1;
    PyErr_SetString(PyExc_TypeError, "unsupported rich comparison");
    return -1;
}

static PyObject *pcc_capi_call_int_conversion_slot(
    PyObject *obj,
    void *slot,
    const char *slot_name
) {
    if (slot == NULL) return NULL;
    PyObject *(*convert)(PyObject *) = (PyObject *(*)(PyObject *))slot;
    PyObject *result = convert(obj);
    if (result == NULL) return NULL;
    if (!pcc_capi_is_intlike(result)) {
        py_decref(result);
        PyErr_Format(PyExc_TypeError, "%s returned a non-int", slot_name);
        return NULL;
    }
    return result;
}

static PyObject *pcc_capi_int_operand(PyObject *obj) {
    if (obj != NULL && !PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_BOOL) {
        return py_int_from_i64(obj == py_True ? 1 : 0);
    }
    return obj;
}

static PyObject *pcc_capi_numeric_error(const char *op) {
    if (!py_err_occurred()) {
        PyErr_Format(PyExc_TypeError, "unsupported operand type(s) for %s", op);
    }
    return NULL;
}

static PyObject *pcc_capi_binary_int_result(
    PyObject *left,
    PyObject *right,
    PyObject *(*op)(PyObject *, PyObject *),
    const char *op_name
) {
    PyObject *a = pcc_capi_int_operand(left);
    PyObject *b = pcc_capi_int_operand(right);
    PyObject *result = op(a, b);
    if (result == NULL) return pcc_capi_numeric_error(op_name);
    return result;
}

static int pcc_capi_sequence_repeatable(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    return (
        tag == PY_TYPE_STR
        || tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY
        || tag == PY_TYPE_LIST
        || tag == PY_TYPE_TUPLE
    );
}

static PyObject *pcc_capi_repeat_sequence(PyObject *seq, PyObject *count_obj) {
    if (!pcc_capi_sequence_repeatable(seq) || !pcc_capi_is_intlike(count_obj)) {
        return NULL;
    }
    PyObject *n_obj = pcc_capi_int_operand(count_obj);
    Py_ssize_t count = PyLong_AsSsize_t(n_obj);
    if (py_err_occurred()) return NULL;
    int32_t tag = py_type_of(seq);
    if (tag == PY_TYPE_STR) {
        return py_str_repeat(seq, n_obj);
    }
    if (tag == PY_TYPE_BYTES || tag == PY_TYPE_BYTEARRAY) {
        return py_bytes_repeat(seq, (int64_t)count);
    }
    if (tag == PY_TYPE_LIST) {
        return py_list_repeat(seq, (int64_t)count);
    }
    if (tag == PY_TYPE_TUPLE) {
        return py_tuple_repeat(seq, (int64_t)count);
    }
    return NULL;
}

int PyNumber_Check(PyObject *obj) {
    if (pcc_capi_is_numberlike(obj)) return 1;
    PccCapiNumberMethods *methods = pcc_capi_number_methods(obj);
    return methods != NULL && (
        methods->nb_int != NULL
        || methods->nb_index != NULL
        || methods->nb_float != NULL
        || methods->nb_add != NULL
    );
}

PyObject *PyNumber_Long(PyObject *obj) {
    if (pcc_capi_is_intlike(obj)) {
        return PyNumber_Index(obj);
    }
    if (pcc_capi_is_floatlike(obj)) {
        double value = py_float_to_f64(obj);
        if (!isfinite(value) || value < (double)INT64_MIN || value > (double)INT64_MAX) {
            PyErr_SetString(PyExc_OverflowError, "cannot convert float to integer");
            return NULL;
        }
        return py_int_from_i64((int64_t)value);
    }
    PccCapiNumberMethods *methods = pcc_capi_number_methods(obj);
    if (methods != NULL) {
        if (methods->nb_int != NULL) {
            return pcc_capi_call_int_conversion_slot(
                obj,
                methods->nb_int,
                "__int__"
            );
        }
        if (methods->nb_index != NULL) {
            return pcc_capi_call_int_conversion_slot(
                obj,
                methods->nb_index,
                "__index__"
            );
        }
    }
    return pcc_capi_numeric_error("int()");
}

/* Unbox a C-extension number scalar (e.g. a numpy int64/bool scalar returned
 * by ndarray element access) to a native int64. Returns 0 with *overflow set
 * when the object is not a cext number or the value does not fit. Generic —
 * driven by the type's nb_int/nb_index number slots, no package special-casing.
 * py_int_to_i64 (pcc-Python port + this C baseline) calls this before giving up
 * on a non-PY_TYPE_INT object, so ``int(numpy_scalar)`` / ``int(a[i])`` unbox
 * instead of silently returning 0. */
int64_t py_cext_number_to_i64(PyObject *o, int *overflow) {
    if (overflow) *overflow = 1;
    if (o == NULL || PY_IS_TAGGED_INT(o)) return 0;
    int32_t tag = py_header(o)->type_tag;
    if (pcc_capi_is_cext_type_tag((int64_t)tag) == 0) return 0;
    PyObject *boxed = PyNumber_Long(o);
    if (boxed == NULL) {
        if (py_err_occurred()) PyErr_Clear();
        return 0;
    }
    int ov = 0;
    int64_t value = py_int_to_i64(boxed, &ov);
    py_decref(boxed);
    if (overflow) *overflow = ov;
    return value;
}

PyObject *PyNumber_Float(PyObject *obj) {
    if (pcc_capi_is_floatlike(obj)) {
        py_incref(obj);
        return obj;
    }
    if (pcc_capi_is_intlike(obj)) {
        return py_float_from_f64(py_float_to_f64(obj));
    }
    return pcc_capi_numeric_error("float()");
}

PyObject *PyNumber_And(PyObject *left, PyObject *right) {
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_and, "&");
    }
    return pcc_capi_numeric_error("&");
}

PyObject *PyNumber_Or(PyObject *left, PyObject *right) {
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_or, "|");
    }
    return pcc_capi_numeric_error("|");
}

PyObject *PyNumber_Xor(PyObject *left, PyObject *right) {
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_xor, "^");
    }
    return pcc_capi_numeric_error("^");
}

PyObject *PyNumber_Invert(PyObject *obj) {
    if (!pcc_capi_is_intlike(obj)) {
        return pcc_capi_numeric_error("~");
    }
    PyObject *operand = pcc_capi_int_operand(obj);
    PyObject *result = py_int_xor(operand, py_int_from_i64(-1));
    if (result == NULL) return pcc_capi_numeric_error("~");
    return result;
}

PyObject *PyNumber_Lshift(PyObject *left, PyObject *right) {
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_shl, "<<");
    }
    return pcc_capi_numeric_error("<<");
}

PyObject *PyNumber_Rshift(PyObject *left, PyObject *right) {
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_shr, ">>");
    }
    return pcc_capi_numeric_error(">>");
}

PyObject *PyNumber_Add(PyObject *left, PyObject *right) {
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
            return pcc_capi_numeric_error("+");
        }
        return py_float_from_f64(py_float_to_f64(left) + py_float_to_f64(right));
    }
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_add, "+");
    }
    if (
        pcc_capi_cext_type_for_object(left) != NULL
        || pcc_capi_cext_type_for_object(right) != NULL
    ) {
        return pcc_capi_cext_binary_number(left, right, 0);
    }
    PyObject *result = py_obj_add(left, right);
    if (result == NULL) return pcc_capi_numeric_error("+");
    return result;
}

PyObject *PyNumber_Subtract(PyObject *left, PyObject *right) {
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
            return pcc_capi_numeric_error("-");
        }
        return py_float_from_f64(py_float_to_f64(left) - py_float_to_f64(right));
    }
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_sub, "-");
    }
    if (
        pcc_capi_cext_type_for_object(left) != NULL
        || pcc_capi_cext_type_for_object(right) != NULL
    ) {
        return pcc_capi_cext_subtract(left, right);
    }
    return pcc_capi_numeric_error("-");
}

PyObject *PyNumber_Multiply(PyObject *left, PyObject *right) {
    PyObject *repeat = pcc_capi_repeat_sequence(left, right);
    if (repeat != NULL || py_err_occurred()) return repeat;
    repeat = pcc_capi_repeat_sequence(right, left);
    if (repeat != NULL || py_err_occurred()) return repeat;
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
            return pcc_capi_numeric_error("*");
        }
        return py_float_from_f64(py_float_to_f64(left) * py_float_to_f64(right));
    }
    if (pcc_capi_is_intlike(left) && pcc_capi_is_intlike(right)) {
        return pcc_capi_binary_int_result(left, right, py_int_mul, "*");
    }
    if (
        pcc_capi_cext_type_for_object(left) != NULL
        || pcc_capi_cext_type_for_object(right) != NULL
    ) {
        return pcc_capi_cext_binary_number(left, right, 2);
    }
    return pcc_capi_numeric_error("*");
}

PyObject *PyNumber_TrueDivide(PyObject *left, PyObject *right) {
    if (
        pcc_capi_cext_type_for_object(left) != NULL
        || pcc_capi_cext_type_for_object(right) != NULL
    ) {
        return pcc_capi_cext_binary_number(left, right, 3);
    }
    if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
        return pcc_capi_numeric_error("/");
    }
    double divisor = py_float_to_f64(right);
    if (divisor == 0.0) {
        PyErr_SetString(PyExc_RuntimeError, "division by zero");
        return NULL;
    }
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        return py_float_from_f64(py_float_to_f64(left) / divisor);
    }
    return pcc_capi_binary_int_result(left, right, py_int_truediv, "/");
}

PyObject *PyNumber_FloorDivide(PyObject *left, PyObject *right) {
    if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
        return pcc_capi_numeric_error("//");
    }
    double divisor = py_float_to_f64(right);
    if (divisor == 0.0) {
        PyErr_SetString(PyExc_RuntimeError, "division by zero");
        return NULL;
    }
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        return py_float_from_f64(floor(py_float_to_f64(left) / divisor));
    }
    return pcc_capi_binary_int_result(left, right, py_int_floordiv, "//");
}

PyObject *PyNumber_Remainder(PyObject *left, PyObject *right) {
    if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
        return pcc_capi_numeric_error("%");
    }
    double divisor = py_float_to_f64(right);
    if (divisor == 0.0) {
        PyErr_SetString(PyExc_RuntimeError, "division by zero");
        return NULL;
    }
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        return py_float_from_f64(fmod(py_float_to_f64(left), divisor));
    }
    return pcc_capi_binary_int_result(left, right, py_int_mod, "%");
}

PyObject *PyNumber_Power(PyObject *left, PyObject *right, PyObject *mod) {
    if (mod != NULL && mod != py_None) {
        PyErr_SetString(PyExc_TypeError, "modular power is not supported");
        return NULL;
    }
    if (!pcc_capi_is_numberlike(left) || !pcc_capi_is_numberlike(right)) {
        return pcc_capi_numeric_error("**");
    }
    if (pcc_capi_is_floatlike(left) || pcc_capi_is_floatlike(right)) {
        return py_float_from_f64(pow(py_float_to_f64(left), py_float_to_f64(right)));
    }
    return pcc_capi_binary_int_result(left, right, py_int_pow, "**");
}

PyObject *PyNumber_Negative(PyObject *obj) {
    if (pcc_capi_is_floatlike(obj)) {
        return py_float_from_f64(-py_float_to_f64(obj));
    }
    if (pcc_capi_is_intlike(obj)) {
        PyObject *operand = pcc_capi_int_operand(obj);
        PyObject *result = py_int_neg(operand);
        if (result == NULL) return pcc_capi_numeric_error("unary -");
        return result;
    }
    return pcc_capi_numeric_error("unary -");
}

PyObject *PyNumber_Positive(PyObject *obj) {
    if (pcc_capi_is_floatlike(obj)) {
        py_incref(obj);
        return obj;
    }
    if (pcc_capi_is_intlike(obj)) {
        return PyNumber_Index(obj);
    }
    return pcc_capi_numeric_error("unary +");
}

PyObject *PyNumber_Absolute(PyObject *obj) {
    if (pcc_capi_is_floatlike(obj)) {
        return py_float_from_f64(fabs(py_float_to_f64(obj)));
    }
    if (pcc_capi_cext_type_for_object(obj) != NULL) {
        return pcc_capi_cext_absolute(obj);
    }
    if (!pcc_capi_is_intlike(obj)) {
        return pcc_capi_numeric_error("abs()");
    }
    PyObject *operand = pcc_capi_int_operand(obj);
    if (PY_IS_TAGGED_INT(operand)) {
        int64_t value = py_untag_int(operand);
        if (value < 0) return py_int_neg(operand);
        return py_int_from_i64(value);
    }
    PyIntObject *big = (PyIntObject *)operand;
    if (big->sign < 0) return py_int_neg(operand);
    py_incref(operand);
    return operand;
}

int PyIndex_Check(PyObject *obj) {
    if (pcc_capi_is_intlike(obj)) return 1;
    PccCapiNumberMethods *methods = pcc_capi_number_methods(obj);
    return methods != NULL && methods->nb_index != NULL;
}

PyObject *PyNumber_Index(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected integer index");
        return NULL;
    }
    if (!PY_IS_TAGGED_INT(obj) && py_type_of(obj) == PY_TYPE_BOOL) {
        return py_int_from_i64(obj == py_True ? 1 : 0);
    }
    if (!pcc_capi_is_intlike(obj)) {
        PccCapiNumberMethods *methods = pcc_capi_number_methods(obj);
        if (methods != NULL && methods->nb_index != NULL) {
            return pcc_capi_call_int_conversion_slot(
                obj,
                methods->nb_index,
                "__index__"
            );
        }
        PyErr_SetString(PyExc_TypeError, "expected integer index");
        return NULL;
    }
    py_incref(obj);
    return obj;
}

Py_ssize_t PyNumber_AsSsize_t(PyObject *obj, PyObject *exc) {
    PyObject *index = PyNumber_Index(obj);
    if (index == NULL) return (Py_ssize_t)-1;
    Py_ssize_t value = PyLong_AsSsize_t(index);
    py_decref(index);
    if (py_err_occurred() && exc != NULL) {
        PyErr_SetString(exc, "cannot fit integer index into Py_ssize_t");
    }
    return value;
}

Py_hash_t PyObject_Hash(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object");
        return -1;
    }
    return (Py_hash_t)py_obj_hash(obj);
}

int PyCallable_Check(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    return tag == PY_TYPE_FUNC || tag == PY_TYPE_CLASS || tag == PY_TYPE_WEAKREF
        || pcc_capi_cext_object_is_callable(obj)
        || pcc_capi_type_object_is_callable(obj);
}

PyObject *PyObject_Str(PyObject *obj) {
    PyObject *out = py_obj_str(obj);
    if (out == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "object cannot be converted to str");
    }
    return out;
}

PyObject *PyObject_Repr(PyObject *obj) {
    PyObject *out = py_obj_repr(obj);
    if (out == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "object cannot be converted to repr");
    }
    return out;
}

PyObject *PyObject_Bytes(PyObject *obj) {
    PyObject *out = py_bytes_from_obj(obj);
    if (out == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "object cannot be converted to bytes");
    }
    return out;
}

PyObject *PyObject_Format(PyObject *obj, PyObject *format_spec) {
    PyObject *out = py_obj_format(obj, format_spec);
    if (out == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_ValueError, "object cannot be formatted");
    }
    return out;
}

int PyObject_Print(PyObject *obj, FILE *fp, int flags) {
    if (fp == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL FILE pointer");
        return -1;
    }
    PyObject *text = (flags & Py_PRINT_RAW) ? PyObject_Str(obj) : PyObject_Repr(obj);
    if (text == NULL) return -1;
    Py_ssize_t n = 0;
    const char *raw = PyUnicode_AsUTF8AndSize(text, &n);
    if (raw == NULL) {
        py_decref(text);
        return -1;
    }
    size_t written = fwrite(raw, 1, (size_t)n, fp);
    py_decref(text);
    if (written != (size_t)n) {
        PyErr_SetString(PyExc_OSError, "failed to write object");
        return -1;
    }
    fflush(fp);
    return 0;
}

PyObject *PyObject_Type(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object");
        return NULL;
    }
    return py_type_builtin(obj);
}

int PyObject_IsInstance(PyObject *obj, PyObject *cls) {
    if (obj == NULL || cls == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_IsInstance call");
        return -1;
    }
    return py_obj_isinstance(obj, cls) ? 1 : 0;
}

int PyObject_RichCompareBool(PyObject *left, PyObject *right, int opid) {
    switch (opid) {
        case Py_LT:
            return py_obj_lt(left, right) ? 1 : 0;
        case Py_LE:
            return py_obj_le(left, right) ? 1 : 0;
        case Py_EQ:
            return py_obj_eq(left, right) ? 1 : 0;
        case Py_NE:
            return py_obj_eq(left, right) ? 0 : 1;
        case Py_GT:
            return py_obj_gt(left, right) ? 1 : 0;
        case Py_GE:
            return py_obj_ge(left, right) ? 1 : 0;
        default:
            PyErr_SetString(PyExc_ValueError, "invalid rich-compare operation");
            return -1;
    }
}

PyObject *PyObject_RichCompare(PyObject *left, PyObject *right, int opid) {
    int result = PyObject_RichCompareBool(left, right, opid);
    if (result < 0) return NULL;
    return PyBool_FromLong(result);
}

PyObject *PyObject_GetItem(PyObject *obj, PyObject *key) {
    if (obj == NULL || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_GetItem call");
        return NULL;
    }
    PyObject *out = py_obj_getitem(obj, key);
    if (out == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_KeyError, "item not found");
    }
    return out;
}

int PyObject_SetItem(PyObject *obj, PyObject *key, PyObject *value) {
    if (obj == NULL || key == NULL || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_SetItem call");
        return -1;
    }
    int64_t rc = py_obj_setitem(obj, key, value);
    if (rc != 0 && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "object does not support item assignment");
    }
    return rc == 0 ? 0 : -1;
}

int PyObject_DelItem(PyObject *obj, PyObject *key) {
    if (obj == NULL || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_DelItem call");
        return -1;
    }
    int64_t rc = py_obj_delitem(obj, key);
    if (rc != 0 && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "object does not support item deletion");
    }
    return rc == 0 ? 0 : -1;
}

Py_ssize_t PyObject_Size(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object");
        return -1;
    }
    int64_t n = py_obj_len(obj);
    if (py_err_occurred()) return -1;
    return (Py_ssize_t)n;
}

Py_ssize_t PyObject_Length(PyObject *obj) {
    return PyObject_Size(obj);
}

static int pcc_capi_len_hint_value(PyObject *obj, int64_t *out) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj) || out == NULL) return 0;
    int32_t tag = py_type_of(obj);
    switch (tag) {
        case PY_TYPE_LIST:
        case PY_TYPE_TUPLE:
        case PY_TYPE_STR:
        case PY_TYPE_BYTES:
        case PY_TYPE_BYTEARRAY:
        case PY_TYPE_MEMORYVIEW:
        case PY_TYPE_DICT:
        case PY_TYPE_SET:
            *out = py_obj_len(obj);
            return 1;
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t user_len = py_user_len_dispatch(obj, &handled);
                if (handled) {
                    *out = user_len;
                    return 1;
                }
            }
            return 0;
    }
}

Py_ssize_t PyObject_LengthHint(PyObject *obj, Py_ssize_t default_value) {
    if (default_value < 0) {
        PyErr_SetString(PyExc_ValueError, "default length hint must be non-negative");
        return -1;
    }
    int64_t n = 0;
    if (!pcc_capi_len_hint_value(obj, &n)) return default_value;
    if (n < 0) {
        PyErr_SetString(PyExc_ValueError, "negative length hint");
        return -1;
    }
    return (Py_ssize_t)n;
}

int PyMapping_Check(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    return tag == PY_TYPE_DICT || tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}

Py_ssize_t PyMapping_Size(PyObject *obj) {
    if (!PyMapping_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected mapping");
        return -1;
    }
    return PyObject_Size(obj);
}

Py_ssize_t PyMapping_Length(PyObject *obj) {
    return PyMapping_Size(obj);
}

static PyObject *pcc_capi_mapping_noarg(PyObject *obj, const char *method) {
    if (!PyMapping_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected mapping");
        return NULL;
    }
    return PyObject_CallMethod(obj, method, NULL);
}

PyObject *PyMapping_Keys(PyObject *obj) {
    if (PyDict_Check(obj)) return PyDict_Keys(obj);
    return pcc_capi_mapping_noarg(obj, "keys");
}

PyObject *PyMapping_Values(PyObject *obj) {
    if (PyDict_Check(obj)) return PyDict_Values(obj);
    return pcc_capi_mapping_noarg(obj, "values");
}

PyObject *PyMapping_Items(PyObject *obj) {
    if (PyDict_Check(obj)) return PyDict_Items(obj);
    return pcc_capi_mapping_noarg(obj, "items");
}

PyObject *PyMapping_GetItemString(PyObject *obj, const char *key) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL mapping key");
        return NULL;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return NULL;
    PyObject *out = PyObject_GetItem(obj, key_obj);
    py_decref(key_obj);
    return out;
}

int PyMapping_SetItemString(PyObject *obj, const char *key, PyObject *value) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL mapping key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyObject_SetItem(obj, key_obj, value);
    py_decref(key_obj);
    return rc;
}

int PyMapping_GetOptionalItem(PyObject *obj, PyObject *key, PyObject **result) {
    if (result == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL result pointer");
        return -1;
    }
    *result = PyObject_GetItem(obj, key);
    if (*result != NULL) return 1;
    if (PyErr_ExceptionMatches(PyExc_KeyError)) {
        PyErr_Clear();
        return 0;
    }
    return py_err_occurred() ? -1 : 0;
}

int PyMapping_GetOptionalItemString(
    PyObject *obj,
    const char *key,
    PyObject **result
) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL mapping key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyMapping_GetOptionalItem(obj, key_obj, result);
    py_decref(key_obj);
    return rc;
}

int PyMapping_HasKeyWithError(PyObject *obj, PyObject *key) {
    PyObject *item = NULL;
    int rc = PyMapping_GetOptionalItem(obj, key, &item);
    if (item != NULL) py_decref(item);
    return rc;
}

int PyMapping_HasKeyStringWithError(PyObject *obj, const char *key) {
    PyObject *item = NULL;
    int rc = PyMapping_GetOptionalItemString(obj, key, &item);
    if (item != NULL) py_decref(item);
    return rc;
}

int PyMapping_HasKey(PyObject *obj, PyObject *key) {
    int rc = PyMapping_HasKeyWithError(obj, key);
    if (rc < 0) {
        PyErr_Clear();
        return 0;
    }
    return rc;
}

int PyMapping_HasKeyString(PyObject *obj, const char *key) {
    int rc = PyMapping_HasKeyStringWithError(obj, key);
    if (rc < 0) {
        PyErr_Clear();
        return 0;
    }
    return rc;
}

static void pcc_capi_capsule_del(PyObject *capsule);

static PyClassObject *pcc_capi_capsule_class(void) {
    static const char *fields[] = {
        "__pcc_capsule_pointer__",
        "__pcc_capsule_name__",
        "__pcc_capsule_context__",
        "__pcc_capsule_destructor__",
    };
    static PyClassObject *cls = NULL;
    if (cls != NULL) return cls;
    cls = py_class_new("capsule", NULL, 0, fields, 4);
    if (cls != NULL) {
        py_class_add_method(
            cls,
            "__del__",
            (PyObject *)(uintptr_t)pcc_capi_capsule_del
        );
        pcc_gc_pin((PyObject *)cls);
    }
    return cls;
}

static int pcc_capi_is_capsule_object(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) return 0;
    PyInstanceObject *inst = (PyInstanceObject *)obj;
    PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
        obj,
        (PyObject **)&inst->cls
    );
    return cls == pcc_capi_capsule_class();
}

static int pcc_capi_capsule_name_matches(PyObject *name_obj, const char *name) {
    if (name == NULL) return name_obj == NULL || name_obj == py_None;
    if (name_obj == NULL || name_obj == py_None || PY_IS_TAGGED_INT(name_obj)) {
        return 0;
    }
    if (py_type_of(name_obj) != PY_TYPE_STR) return 0;
    const char *stored = py_str_utf8(name_obj);
    return stored != NULL && strcmp(stored, name) == 0;
}

PyObject *PyCapsule_New(void *pointer, const char *name, PyCapsule_Destructor destructor) {
    if (pointer == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyCapsule_New called with NULL pointer");
        return NULL;
    }
    PyClassObject *cls = pcc_capi_capsule_class();
    if (cls == NULL) return NULL;
    PyObject *capsule = py_instance_new(cls);
    if (capsule == NULL) return NULL;

    PyObject *ptr_obj = PyLong_FromVoidPtr(pointer);
    PyObject *name_obj = name == NULL
        ? py_None
        : py_str_new(name, (int64_t)strlen(name));
    PyObject *destructor_obj = destructor == NULL
        ? py_None
        : PyLong_FromVoidPtr((void *)(uintptr_t)destructor);
    if (ptr_obj == NULL || name_obj == NULL || destructor_obj == NULL) {
        py_decref(ptr_obj);
        if (name_obj != NULL && name_obj != py_None) py_decref(name_obj);
        if (destructor_obj != NULL && destructor_obj != py_None) py_decref(destructor_obj);
        py_decref(capsule);
        return NULL;
    }
    int64_t stored_pointer = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_pointer__",
        ptr_obj
    );
    int64_t stored_name = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_name__",
        name_obj
    );
    int64_t stored_destructor = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_destructor__",
        destructor_obj
    );
    py_decref(ptr_obj);
    if (name_obj != py_None) py_decref(name_obj);
    if (destructor_obj != py_None) py_decref(destructor_obj);
    if (stored_pointer != 0 || stored_name != 0 || stored_destructor != 0) {
        py_decref(capsule);
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize capsule");
        return NULL;
    }
    return capsule;
}

static void pcc_capi_capsule_del(PyObject *capsule) {
    if (!pcc_capi_is_capsule_object(capsule)) return;
    PyObject *destructor_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_destructor__"
    );
    if (destructor_obj == NULL || destructor_obj == py_None) {
        py_decref(destructor_obj);
        return;
    }
    void *destructor_ptr = PyLong_AsVoidPtr(destructor_obj);
    py_decref(destructor_obj);
    if (destructor_ptr == NULL || py_err_occurred()) return;
    PyCapsule_Destructor destructor =
        (PyCapsule_Destructor)(uintptr_t)destructor_ptr;
    destructor(capsule);
}

int PyCapsule_CheckExact(PyObject *capsule) {
    return pcc_capi_is_capsule_object(capsule);
}

int PyCapsule_IsValid(PyObject *capsule, const char *name) {
    if (!pcc_capi_is_capsule_object(capsule)) return 0;
    PyObject *name_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_name__"
    );
    int valid = pcc_capi_capsule_name_matches(name_obj, name);
    py_decref(name_obj);
    return valid;
}

const char *PyCapsule_GetName(PyObject *capsule) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return NULL;
    }
    PyObject *name_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_name__"
    );
    const char *out = NULL;
    if (name_obj != NULL && name_obj != py_None && !PY_IS_TAGGED_INT(name_obj)
        && py_type_of(name_obj) == PY_TYPE_STR) {
        out = py_str_utf8(name_obj);
    }
    py_decref(name_obj);
    return out;
}

void *PyCapsule_GetContext(PyObject *capsule) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return NULL;
    }
    PyObject *context_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_context__"
    );
    if (context_obj == NULL || context_obj == py_None) {
        py_decref(context_obj);
        return NULL;
    }
    void *context = PyLong_AsVoidPtr(context_obj);
    py_decref(context_obj);
    return context;
}

PyCapsule_Destructor PyCapsule_GetDestructor(PyObject *capsule) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return NULL;
    }
    PyObject *destructor_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_destructor__"
    );
    if (destructor_obj == NULL || destructor_obj == py_None) {
        py_decref(destructor_obj);
        return NULL;
    }
    void *destructor_ptr = PyLong_AsVoidPtr(destructor_obj);
    py_decref(destructor_obj);
    if (destructor_ptr == NULL || py_err_occurred()) return NULL;
    return (PyCapsule_Destructor)(uintptr_t)destructor_ptr;
}

void *PyCapsule_GetPointer(PyObject *capsule, const char *name) {
    if (!PyCapsule_IsValid(capsule, name)) {
        PyErr_SetString(PyExc_ValueError, "invalid capsule or capsule name");
        return NULL;
    }
    PyObject *ptr_obj = py_instance_getattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_pointer__"
    );
    void *pointer = PyLong_AsVoidPtr(ptr_obj);
    py_decref(ptr_obj);
    return pointer;
}

int PyCapsule_SetPointer(PyObject *capsule, void *pointer) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return -1;
    }
    if (pointer == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyCapsule_SetPointer called with NULL pointer");
        return -1;
    }
    PyObject *ptr_obj = PyLong_FromVoidPtr(pointer);
    if (ptr_obj == NULL) return -1;
    int64_t stored = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_pointer__",
        ptr_obj
    );
    py_decref(ptr_obj);
    if (stored != 0) {
        PyErr_SetString(PyExc_RuntimeError, "failed to set capsule pointer");
        return -1;
    }
    return 0;
}

int PyCapsule_SetContext(PyObject *capsule, void *context) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return -1;
    }
    PyObject *context_obj = context == NULL ? NULL : PyLong_FromVoidPtr(context);
    if (context != NULL && context_obj == NULL) return -1;
    int64_t stored = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_context__",
        context_obj
    );
    py_decref(context_obj);
    if (stored != 0) {
        PyErr_SetString(PyExc_RuntimeError, "failed to set capsule context");
        return -1;
    }
    return 0;
}

int PyCapsule_SetDestructor(PyObject *capsule, PyCapsule_Destructor destructor) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return -1;
    }
    PyObject *destructor_obj = destructor == NULL
        ? py_None
        : PyLong_FromVoidPtr((void *)(uintptr_t)destructor);
    if (destructor_obj == NULL) return -1;
    int64_t stored = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_destructor__",
        destructor_obj
    );
    if (destructor_obj != py_None) py_decref(destructor_obj);
    if (stored != 0) {
        PyErr_SetString(PyExc_RuntimeError, "failed to set capsule destructor");
        return -1;
    }
    return 0;
}

int PyCapsule_SetName(PyObject *capsule, const char *name) {
    if (!pcc_capi_is_capsule_object(capsule)) {
        PyErr_SetString(PyExc_TypeError, "expected capsule");
        return -1;
    }
    PyObject *name_obj = name == NULL
        ? py_None
        : py_str_new(name, (int64_t)strlen(name));
    if (name_obj == NULL) return -1;
    int64_t stored = py_instance_setattr(
        (PyInstanceObject *)capsule,
        "__pcc_capsule_name__",
        name_obj
    );
    if (name_obj != py_None) py_decref(name_obj);
    if (stored != 0) {
        PyErr_SetString(PyExc_RuntimeError, "failed to set capsule name");
        return -1;
    }
    return 0;
}

void *PyCapsule_Import(const char *name, int no_block) {
    (void)no_block;
    if (name == NULL || name[0] == '\0') {
        PyErr_SetString(PyExc_ValueError, "empty capsule import name");
        return NULL;
    }
    const char *dot = strrchr(name, '.');
    if (dot == NULL || dot == name || dot[1] == '\0') {
        PyErr_SetString(PyExc_ValueError, "capsule import name must be module.attr");
        return NULL;
    }
    size_t module_len = (size_t)(dot - name);
    char *module_name = (char *)malloc(module_len + 1);
    if (module_name == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "out of memory importing capsule");
        return NULL;
    }
    memcpy(module_name, name, module_len);
    module_name[module_len] = '\0';

    PyObject *module = py_native_extension_import_by_name(module_name);
    free(module_name);
    if (module == NULL) {
        if (!py_err_occurred()) {
            PyErr_SetString(PyExc_RuntimeError, "capsule import module not found");
        }
        return NULL;
    }
    PyObject *capsule = py_obj_getattr(module, dot + 1);
    py_decref(module);
    if (capsule == NULL) return NULL;
    void *pointer = PyCapsule_GetPointer(capsule, name);
    py_decref(capsule);
    return pointer;
}

Py_ssize_t PyTuple_Size(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_TUPLE)) {
        PyErr_SetString(PyExc_TypeError, "expected tuple");
        return -1;
    }
    return (Py_ssize_t)py_tuple_len(obj);
}

PyObject *PyTuple_GetItem(PyObject *obj, Py_ssize_t index) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_TUPLE)) {
        PyErr_SetString(PyExc_TypeError, "expected tuple");
        return NULL;
    }
    PyObject *item = py_tuple_get(obj, (int64_t)index);
    if (item == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "tuple index out of range");
        return NULL;
    }
    /* py_tuple_get returns an owned reference; CPython PyTuple_GetItem returns
     * borrowed. Drop the temporary ownership before returning the live slot. */
    py_decref(item);
    return item;
}

PyObject *PyTuple_New(Py_ssize_t size) {
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative tuple size");
        return NULL;
    }
    return py_tuple_new((int64_t)size);
}

int PyTuple_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_TUPLE) || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyTuple_SetItem call");
        return -1;
    }
    int64_t n = py_tuple_len(obj);
    if (index < 0 || index >= (Py_ssize_t)n) {
        PyErr_SetString(PyExc_RuntimeError, "tuple index out of range");
        return -1;
    }
    py_tuple_set_item(obj, (int64_t)index, value);
    /* CPython PyTuple_SetItem steals a reference on success. */
    py_decref(value);
    return 0;
}

PyObject *PyTuple_Pack(Py_ssize_t size, ...) {
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative tuple size");
        return NULL;
    }
    PyObject *tuple = PyTuple_New(size);
    if (tuple == NULL) return NULL;
    va_list ap;
    va_start(ap, size);
    for (Py_ssize_t i = 0; i < size; i++) {
        PyObject *item = va_arg(ap, PyObject *);
        if (item == NULL) {
            va_end(ap);
            py_decref(tuple);
            PyErr_SetString(PyExc_TypeError, "NULL item in PyTuple_Pack");
            return NULL;
        }
        py_tuple_set_item(tuple, (int64_t)i, item);
    }
    va_end(ap);
    return tuple;
}

int PyTuple_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_TUPLE);
}

int PyTuple_CheckExact(PyObject *obj) {
    return PyTuple_Check(obj);
}

PyObject *PyList_New(Py_ssize_t size) {
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative list size");
        return NULL;
    }
    PyObject *list = py_list_new((int64_t)size);
    if (list == NULL) return NULL;
    PyListObject *lst = (PyListObject *)list;
    for (Py_ssize_t i = 0; i < size; i++) {
        lst->items[i] = NULL;
    }
    lst->length = (int64_t)size;
    return list;
}

int PyList_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_LIST) || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyList_SetItem call");
        return -1;
    }
    PyListObject *lst = (PyListObject *)obj;
    if (index < 0 || index >= (Py_ssize_t)lst->length) {
        PyErr_SetString(PyExc_RuntimeError, "list index out of range");
        return -1;
    }
    pcc_gc_store_ptr(obj, &lst->items[index], value);
    /* CPython PyList_SetItem steals a reference on success. */
    py_decref(value);
    return 0;
}

PyObject *PyList_GetItem(PyObject *obj, Py_ssize_t index) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_LIST)) {
        PyErr_SetString(PyExc_TypeError, "expected list");
        return NULL;
    }
    PyObject *item = py_list_get(obj, (int64_t)index);
    if (item == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "list index out of range");
        return NULL;
    }
    /* py_list_get returns owned; CPython PyList_GetItem returns borrowed. */
    py_decref(item);
    return item;
}

PyObject *PyList_GetItemRef(PyObject *obj, Py_ssize_t index) {
    PyObject *item = PyList_GetItem(obj, index);
    if (item != NULL) Py_INCREF(item);
    return item;
}

Py_ssize_t PyList_Size(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_LIST)) {
        PyErr_SetString(PyExc_TypeError, "expected list");
        return -1;
    }
    return (Py_ssize_t)py_list_len(obj);
}

int PyList_Append(PyObject *obj, PyObject *value) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_LIST) || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyList_Append call");
        return -1;
    }
    py_list_append(obj, value);
    return py_err_occurred() ? -1 : 0;
}

PyObject *PyList_AsTuple(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_LIST)) {
        PyErr_SetString(PyExc_TypeError, "expected list");
        return NULL;
    }
    int64_t n = py_list_len(obj);
    PyObject *tuple = py_tuple_new(n);
    if (tuple == NULL) return NULL;
    for (int64_t i = 0; i < n; i++) {
        PyObject *item = py_list_get(obj, i);
        if (item == NULL) {
            py_decref(tuple);
            PyErr_SetString(PyExc_RuntimeError, "list item missing");
            return NULL;
        }
        py_tuple_set_item(tuple, i, item);
        py_decref(item);
    }
    return tuple;
}

int PyList_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_LIST);
}

int PyList_CheckExact(PyObject *obj) {
    return PyList_Check(obj);
}

PyObject *PyDict_New(void) {
    return py_dict_new();
}

int PyDict_SetItem(PyObject *dict, PyObject *key, PyObject *value) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_SetItem call");
        return -1;
    }
    py_dict_set(dict, key, value);
    return py_err_occurred() ? -1 : 0;
}

int PyDict_SetItemString(PyObject *dict, const char *key, PyObject *value) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL dict key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyDict_SetItem(dict, key_obj, value);
    py_decref(key_obj);
    return rc;
}

PyObject *PyDict_GetItem(PyObject *dict, PyObject *key) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL) {
        py_clear_exception();
        return NULL;
    }
    PyObject *item = py_dict_get(dict, key);
    if (item == NULL) return NULL;
    /* py_dict_get returns owned; CPython PyDict_GetItem returns borrowed. */
    py_decref(item);
    return item;
}

PyObject *PyDict_GetItemString(PyObject *dict, const char *key) {
    if (key == NULL) return NULL;
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return NULL;
    PyObject *item = PyDict_GetItem(dict, key_obj);
    py_decref(key_obj);
    return item;
}

PyObject *PyDict_GetItemWithError(PyObject *dict, PyObject *key) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_GetItemWithError call");
        return NULL;
    }
    PyObject *item = py_dict_get(dict, key);
    if (item == NULL) return NULL;
    /* py_dict_get returns owned; CPython PyDict_GetItemWithError returns borrowed. */
    py_decref(item);
    return item;
}

int PyDict_GetItemRef(PyObject *dict, PyObject *key, PyObject **result) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL || result == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_GetItemRef call");
        return -1;
    }
    PyObject *item = py_dict_get(dict, key);
    if (item == NULL) {
        *result = NULL;
        return py_err_occurred() ? -1 : 0;
    }
    *result = item;
    return 1;
}

int PyDict_GetItemStringRef(PyObject *dict, const char *key, PyObject **result) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL dict key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyDict_GetItemRef(dict, key_obj, result);
    py_decref(key_obj);
    return rc;
}

int PyDict_SetDefaultRef(
    PyObject *dict,
    PyObject *key,
    PyObject *default_value,
    PyObject **result
) {
    if (default_value == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL default value");
        if (result != NULL) *result = NULL;
        return -1;
    }
    PyObject *item = NULL;
    int rc = PyDict_GetItemRef(dict, key, &item);
    if (rc < 0) {
        if (result != NULL) *result = NULL;
        return -1;
    }
    if (rc > 0) {
        if (result != NULL) {
            *result = item;
        } else {
            Py_DECREF(item);
        }
        return 1;
    }
    if (PyDict_SetItem(dict, key, default_value) != 0) {
        if (result != NULL) *result = NULL;
        return -1;
    }
    if (result != NULL) {
        Py_INCREF(default_value);
        *result = default_value;
    }
    return 0;
}

int PyDict_Pop(PyObject *dict, PyObject *key, PyObject **result) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_Pop call");
        return -1;
    }
    PyObject *item = py_dict_get(dict, key);
    if (item == NULL) {
        if (result != NULL) *result = NULL;
        return py_err_occurred() ? -1 : 0;
    }
    int64_t rc = py_dict_del(dict, key);
    if (rc != 0) {
        py_decref(item);
        if (result != NULL) *result = NULL;
        if (!py_err_occurred()) {
            PyErr_SetString(PyExc_KeyError, "missing dict key");
        }
        return -1;
    }
    if (result != NULL) {
        *result = item;
    } else {
        py_decref(item);
    }
    return 1;
}

int PyDict_PopString(PyObject *dict, const char *key, PyObject **result) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL dict key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyDict_Pop(dict, key_obj, result);
    py_decref(key_obj);
    return rc;
}

int PyDict_DelItem(PyObject *dict, PyObject *key) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_DelItem call");
        return -1;
    }
    int64_t rc = py_dict_del(dict, key);
    if (rc != 0 && !py_err_occurred()) {
        PyErr_SetString(PyExc_KeyError, "missing dict key");
    }
    return rc == 0 ? 0 : -1;
}

int PyDict_DelItemString(PyObject *dict, const char *key) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_DelItemString call");
        return -1;
    }
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL dict key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyDict_DelItem(dict, key_obj);
    py_decref(key_obj);
    return rc;
}

Py_ssize_t PyDict_Size(PyObject *dict) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_Size call");
        return -1;
    }
    return (Py_ssize_t)py_dict_len(dict);
}

int PyDict_Contains(PyObject *dict, PyObject *key) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_Contains call");
        return -1;
    }
    int64_t rc = py_dict_contains(dict, key);
    if (py_err_occurred()) return -1;
    return rc != 0 ? 1 : 0;
}

int PyDict_ContainsString(PyObject *dict, const char *key) {
    if (key == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL dict key");
        return -1;
    }
    PyObject *key_obj = PyUnicode_FromString(key);
    if (key_obj == NULL) return -1;
    int rc = PyDict_Contains(dict, key_obj);
    py_decref(key_obj);
    return rc;
}

int PyDict_Next(
    PyObject *dict,
    Py_ssize_t *pos,
    PyObject **key,
    PyObject **value
) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT) || pos == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyDict_Next call");
        return 0;
    }
    PyDictObject *d = (PyDictObject *)dict;
    int64_t i = *pos < 0 ? 0 : (int64_t)*pos;
    while (i < d->entries_used) {
        DictEntry *entry = &d->entries[i];
        i += 1;
        PyObject *entry_key = pcc_gc_load_ptr(dict, &entry->key);
        if (entry_key == NULL) continue;
        PyObject *entry_value = pcc_gc_load_ptr(dict, &entry->value);
        if (entry_value == NULL) continue;
        *pos = (Py_ssize_t)i;
        if (key != NULL) *key = entry_key;
        if (value != NULL) *value = entry_value;
        return 1;
    }
    return 0;
}

PyObject *PyDict_Keys(PyObject *dict) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "expected dict");
        return NULL;
    }
    return py_dict_keys(dict);
}

PyObject *PyDict_Values(PyObject *dict) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "expected dict");
        return NULL;
    }
    return py_dict_values(dict);
}

PyObject *PyDict_Items(PyObject *dict) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "expected dict");
        return NULL;
    }
    return py_dict_items(dict);
}

void PyDict_Clear(PyObject *dict) {
    if (!pcc_capi_is_exact_type(dict, PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "expected dict");
        return;
    }
    py_dict_clear(dict);
}

int PyDict_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_DICT);
}

int PyDict_CheckExact(PyObject *obj) {
    return PyDict_Check(obj);
}

PyObject *PySet_New(PyObject *iterable) {
    PyObject *set = py_set_new();
    if (set == NULL) return NULL;
    if (iterable == NULL) return set;

    PyObject *iter = PyObject_GetIter(iterable);
    if (iter == NULL) {
        py_decref(set);
        return NULL;
    }
    for (;;) {
        PyObject *item = PyIter_Next(iter);
        if (item == NULL) {
            if (PyErr_Occurred() != NULL) {
                py_decref(iter);
                py_decref(set);
                return NULL;
            }
            break;
        }
        py_set_add(set, item);
        py_decref(item);
        if (py_err_occurred()) {
            py_decref(iter);
            py_decref(set);
            return NULL;
        }
    }
    py_decref(iter);
    return set;
}

int PySet_Add(PyObject *set, PyObject *key) {
    if (!pcc_capi_is_exact_type(set, PY_TYPE_SET) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PySet_Add call");
        return -1;
    }
    py_set_add(set, key);
    return py_err_occurred() ? -1 : 0;
}

int PySet_Contains(PyObject *set, PyObject *key) {
    if (!pcc_capi_is_exact_type(set, PY_TYPE_SET) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PySet_Contains call");
        return -1;
    }
    int64_t rc = py_set_contains(set, key);
    if (py_err_occurred()) return -1;
    return rc != 0 ? 1 : 0;
}

int PySet_Discard(PyObject *set, PyObject *key) {
    if (!pcc_capi_is_exact_type(set, PY_TYPE_SET) || key == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PySet_Discard call");
        return -1;
    }
    int64_t rc = py_set_remove(set, key);
    if (py_err_occurred()) return -1;
    return rc == 0 ? 1 : 0;
}

Py_ssize_t PySet_Size(PyObject *set) {
    if (!pcc_capi_is_exact_type(set, PY_TYPE_SET)) {
        PyErr_SetString(PyExc_TypeError, "invalid PySet_Size call");
        return -1;
    }
    return (Py_ssize_t)py_set_len(set);
}

int PySet_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_SET);
}

int PySet_CheckExact(PyObject *obj) {
    return PySet_Check(obj);
}

int PyAnySet_Check(PyObject *obj) {
    return PySet_Check(obj);
}

int PyAnySet_CheckExact(PyObject *obj) {
    return PySet_Check(obj);
}

PyObject *PyBytes_FromStringAndSize(const char *value, Py_ssize_t len) {
    if (len < 0) {
        PyErr_SetString(PyExc_ValueError, "negative bytes size");
        return NULL;
    }
    return py_bytes_new(value, (int64_t)len);
}

PyObject *PyBytes_FromString(const char *value) {
    if (value == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL bytes string");
        return NULL;
    }
    return PyBytes_FromStringAndSize(value, (Py_ssize_t)strlen(value));
}

char *PyBytes_AsString(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_BYTES)) {
        PyErr_SetString(PyExc_TypeError, "expected bytes");
        return NULL;
    }
    return ((PyBytesObject *)obj)->data;
}

int PyBytes_AsStringAndSize(PyObject *obj, char **buffer, Py_ssize_t *length) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_BYTES) || buffer == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyBytes_AsStringAndSize call");
        return -1;
    }
    PyBytesObject *bytes = (PyBytesObject *)obj;
    if (length == NULL && memchr(bytes->data, '\0', (size_t)bytes->byte_len) != NULL) {
        PyErr_SetString(PyExc_ValueError, "embedded null byte");
        return -1;
    }
    *buffer = bytes->data;
    if (length != NULL) {
        *length = (Py_ssize_t)bytes->byte_len;
    }
    return 0;
}

Py_ssize_t PyBytes_Size(PyObject *obj) {
    if (!pcc_capi_is_exact_type(obj, PY_TYPE_BYTES)) {
        PyErr_SetString(PyExc_TypeError, "expected bytes");
        return -1;
    }
    return (Py_ssize_t)((PyBytesObject *)obj)->byte_len;
}

int PyBytes_Check(PyObject *obj) {
    return pcc_capi_is_exact_type(obj, PY_TYPE_BYTES);
}

int PyBytes_CheckExact(PyObject *obj) {
    return PyBytes_Check(obj);
}

static int pcc_capi_exception_tag(PyObject *type) {
    if (type == PyExc_BaseException) return PY_EXC_BASE;
    if (type == PyExc_Exception) return PY_EXC_EXCEPTION;
    if (type == PyExc_ValueError) return PY_EXC_VALUEERROR;
    if (type == PyExc_TypeError) return PY_EXC_TYPEERROR;
    if (type == PyExc_RuntimeError) return PY_EXC_RUNTIMEERROR;
    if (type == PyExc_KeyError) return PY_EXC_KEYERROR;
    if (type == PyExc_IndexError) return PY_EXC_INDEXERROR;
    if (type == PyExc_AttributeError) return PY_EXC_ATTRIBUTEERROR;
    if (type == PyExc_MemoryError) return PY_EXC_MEMORYERROR;
    if (type == PyExc_OverflowError) return PY_EXC_OVERFLOWERROR;
    if (type == PyExc_SystemError) return PY_EXC_RUNTIMEERROR;
    if (type == PyExc_NameError) return PY_EXC_NAMEERROR;
    if (type == PyExc_NotImplementedError) return PY_EXC_NOTIMPLEMENTEDERROR;
    if (type == PyExc_ArithmeticError) return PY_EXC_ARITHMETICERROR;
    if (type == PyExc_LookupError) return PY_EXC_LOOKUPERROR;
    if (type == PyExc_OSError || type == PyExc_IOError) return PY_EXC_OSERROR;
    if (type == PyExc_AssertionError) return PY_EXC_ASSERTIONERROR;
    if (type == PyExc_StopIteration) return PY_EXC_STOPITERATION;
    if (type == PyExc_StopAsyncIteration) return PY_EXC_STOPASYNCITERATION;
    if (type == PyExc_ZeroDivisionError) return PY_EXC_ZERODIVISIONERROR;
    if (type == PyExc_ReferenceError) return PY_EXC_REFERENCEERROR;
    if (type == PyExc_FloatingPointError) return PY_EXC_ARITHMETICERROR;
    if (type == PyExc_RecursionError) return PY_EXC_RUNTIMEERROR;
    if (type == PyExc_UnicodeDecodeError) return PY_EXC_VALUEERROR;
    if (type == PyExc_ImportError) return PY_EXC_IMPORTERROR;
    if (type == PyExc_ModuleNotFoundError) return PY_EXC_MODULENOTFOUNDERROR;
    return PY_EXC_EXCEPTION;
}

static PyObject *pcc_capi_exception_class(PyObject *type) {
    if (type == NULL) return NULL;
    if (!PY_IS_TAGGED_INT(type) && py_type_of(type) == PY_TYPE_CLASS) {
        return type;
    }
    return (PyObject *)py_exc_builtin_class(pcc_capi_exception_tag(type));
}

void PyErr_SetString(PyObject *type, const char *message) {
    py_raise(py_exc_new(pcc_capi_exception_tag(type), message ? message : ""));
}

void PyErr_SetNone(PyObject *type) {
    PyErr_SetString(type, "");
}

void PyErr_SetObject(PyObject *type, PyObject *value) {
    PyObject *cls = pcc_capi_exception_class(type);
    PyObject *exc = NULL;
    if (cls != NULL && !PY_IS_TAGGED_INT(cls) && py_type_of(cls) == PY_TYPE_CLASS) {
        if (type == PyExc_ValueError
            || type == PyExc_TypeError
            || type == PyExc_RuntimeError
            || type == PyExc_KeyError
            || type == PyExc_IndexError
            || type == PyExc_AttributeError
            || type == PyExc_MemoryError
            || type == PyExc_OverflowError
            || type == PyExc_SystemError
            || type == PyExc_NameError
            || type == PyExc_NotImplementedError
            || type == PyExc_BaseException
            || type == PyExc_Exception
            || type == PyExc_ArithmeticError
            || type == PyExc_LookupError
            || type == PyExc_OSError
            || type == PyExc_IOError
            || type == PyExc_AssertionError
            || type == PyExc_StopIteration
            || type == PyExc_StopAsyncIteration
            || type == PyExc_ZeroDivisionError
            || type == PyExc_ReferenceError
            || type == PyExc_FloatingPointError
            || type == PyExc_RecursionError
            || type == PyExc_UnicodeDecodeError
            || type == PyExc_ImportError
            || type == PyExc_ModuleNotFoundError) {
            exc = py_exc_new_with_value(pcc_capi_exception_tag(type), value);
        } else {
            PyObject *text = value != NULL ? py_obj_str(value) : NULL;
            const char *msg = text != NULL && !PY_IS_TAGGED_INT(text)
                && py_type_of(text) == PY_TYPE_STR
                ? py_str_utf8(text)
                : "";
            exc = py_exc_new_with_class(cls, msg);
            py_decref(text);
        }
    }
    if (exc == NULL) {
        exc = py_exc_new_with_value(PY_EXC_EXCEPTION, value);
    }
    py_raise(exc);
    py_decref(exc);
}

PyObject *PyErr_NoMemory(void) {
    PyErr_SetString(PyExc_MemoryError, "out of memory");
    return NULL;
}

void PyErr_BadInternalCall(void) {
    PyErr_SetString(PyExc_SystemError, "bad internal call");
}

static void pcc_capi_append_bytes(
    char *out,
    size_t cap,
    size_t *len,
    const char *text,
    size_t text_len
) {
    if (out == NULL || cap == 0 || len == NULL) return;
    if (text == NULL) text = "(null)";
    while (text_len > 0 && *len + 1 < cap) {
        out[*len] = *text;
        *len += 1;
        text += 1;
        text_len -= 1;
    }
    out[*len] = '\0';
}

static void pcc_capi_append_cstr(
    char *out,
    size_t cap,
    size_t *len,
    const char *text,
    int precision
) {
    if (text == NULL) text = "(null)";
    size_t n = strlen(text);
    if (precision >= 0 && (size_t)precision < n) n = (size_t)precision;
    pcc_capi_append_bytes(out, cap, len, text, n);
}

static void pcc_capi_append_object_format(
    char *out,
    size_t cap,
    size_t *len,
    PyObject *obj,
    int use_repr,
    int precision
) {
    if (obj == NULL) {
        pcc_capi_append_cstr(out, cap, len, "<NULL>", precision);
        return;
    }
    PyObject *text = use_repr ? py_obj_repr(obj) : py_obj_str(obj);
    if (text == NULL) text = py_obj_repr(obj);
    if (text == NULL || PY_IS_TAGGED_INT(text) || py_type_of(text) != PY_TYPE_STR) {
        py_decref(text);
        pcc_capi_append_cstr(out, cap, len, "<object>", precision);
        return;
    }
    const char *raw = py_str_utf8(text);
    size_t n = (size_t)py_str_byte_len(text);
    if (precision >= 0 && (size_t)precision < n) n = (size_t)precision;
    pcc_capi_append_bytes(out, cap, len, raw, n);
    py_decref(text);
}

static void pcc_capi_append_signed(
    char *out,
    size_t cap,
    size_t *len,
    long long value
) {
    char tmp[64];
    snprintf(tmp, sizeof(tmp), "%lld", value);
    pcc_capi_append_cstr(out, cap, len, tmp, -1);
}

static void pcc_capi_append_unsigned(
    char *out,
    size_t cap,
    size_t *len,
    unsigned long long value,
    char conv
) {
    char tmp[64];
    if (conv == 'x') {
        snprintf(tmp, sizeof(tmp), "%llx", value);
    } else if (conv == 'X') {
        snprintf(tmp, sizeof(tmp), "%llX", value);
    } else if (conv == 'o') {
        snprintf(tmp, sizeof(tmp), "%llo", value);
    } else {
        snprintf(tmp, sizeof(tmp), "%llu", value);
    }
    pcc_capi_append_cstr(out, cap, len, tmp, -1);
}

static void pcc_capi_format_message(
    char *message,
    size_t message_cap,
    const char *format,
    va_list *ap
) {
    size_t out_len = 0;
    if (message == NULL || message_cap == 0) return;
    message[0] = '\0';
    if (format == NULL) {
        return;
    }

    const char *p = format;
    while (*p != '\0') {
        if (*p != '%') {
            pcc_capi_append_bytes(message, message_cap, &out_len, p, 1);
            p += 1;
            continue;
        }

        p += 1;
        if (*p == '%') {
            pcc_capi_append_bytes(message, message_cap, &out_len, "%", 1);
            p += 1;
            continue;
        }

        while (*p == '#' || *p == '0' || *p == '-' || *p == ' ' || *p == '+') {
            p += 1;
        }
        if (*p == '*') {
            (void)va_arg(*ap, int);
            p += 1;
        } else {
            while (*p >= '0' && *p <= '9') p += 1;
        }

        int precision = -1;
        if (*p == '.') {
            p += 1;
            if (*p == '*') {
                precision = va_arg(*ap, int);
                p += 1;
            } else {
                precision = 0;
                while (*p >= '0' && *p <= '9') {
                    precision = precision * 10 + (*p - '0');
                    p += 1;
                }
            }
        }

        int length = 0;
        if (*p == 'l' && p[1] == 'l') {
            length = 2;
            p += 2;
        } else if (*p == 'l') {
            length = 1;
            p += 1;
        } else if (*p == 'z') {
            length = 3;
            p += 1;
        } else if (*p == 'h') {
            if (p[1] == 'h') p += 2;
            else p += 1;
        }

        char conv = *p;
        if (conv == '\0') break;
        p += 1;

        if (conv == 's') {
            const char *value = va_arg(*ap, const char *);
            pcc_capi_append_cstr(message, message_cap, &out_len, value, precision);
        } else if (conv == 'R' || conv == 'S' || conv == 'U') {
            PyObject *obj = va_arg(*ap, PyObject *);
            pcc_capi_append_object_format(
                message,
                message_cap,
                &out_len,
                obj,
                conv == 'R',
                precision
            );
        } else if (conv == 'd' || conv == 'i') {
            long long value;
            if (length == 2) value = va_arg(*ap, long long);
            else if (length == 1) value = va_arg(*ap, long);
            else if (length == 3) value = va_arg(*ap, Py_ssize_t);
            else value = va_arg(*ap, int);
            pcc_capi_append_signed(message, message_cap, &out_len, value);
        } else if (conv == 'u' || conv == 'x' || conv == 'X' || conv == 'o') {
            unsigned long long value;
            if (length == 2) value = va_arg(*ap, unsigned long long);
            else if (length == 1) value = va_arg(*ap, unsigned long);
            else if (length == 3) value = va_arg(*ap, size_t);
            else value = va_arg(*ap, unsigned int);
            pcc_capi_append_unsigned(message, message_cap, &out_len, value, conv);
        } else if (conv == 'p') {
            void *value = va_arg(*ap, void *);
            char tmp[64];
            snprintf(tmp, sizeof(tmp), "%p", value);
            pcc_capi_append_cstr(message, message_cap, &out_len, tmp, -1);
        } else if (conv == 'c') {
            int value = va_arg(*ap, int);
            char ch = (char)value;
            pcc_capi_append_bytes(message, message_cap, &out_len, &ch, 1);
        } else if (
            conv == 'f' || conv == 'F' || conv == 'e'
            || conv == 'E' || conv == 'g' || conv == 'G'
        ) {
            double value = va_arg(*ap, double);
            char tmp[128];
            char fmt[16];
            if (precision >= 0) {
                snprintf(fmt, sizeof(fmt), "%%.%d%c", precision, conv);
            } else {
                snprintf(fmt, sizeof(fmt), "%%%c", conv);
            }
            snprintf(tmp, sizeof(tmp), fmt, value);
            pcc_capi_append_cstr(message, message_cap, &out_len, tmp, -1);
        } else {
            pcc_capi_append_bytes(message, message_cap, &out_len, "%", 1);
            pcc_capi_append_bytes(message, message_cap, &out_len, &conv, 1);
        }
    }
}

PyObject *PyUnicode_FromFormat(const char *format, ...) {
    char message[2048];
    va_list ap;
    va_start(ap, format);
    pcc_capi_format_message(message, sizeof(message), format, &ap);
    va_end(ap);
    return py_str_new(message, (int64_t)strlen(message));
}

PyObject *PyUnicode_FromFormatV(const char *format, va_list vargs) {
    char message[2048];
    va_list ap;
    va_copy(ap, vargs);
    pcc_capi_format_message(message, sizeof(message), format, &ap);
    va_end(ap);
    return py_str_new(message, (int64_t)strlen(message));
}

PyObject *PyErr_Format(PyObject *type, const char *format, ...) {
    char message[2048];
    va_list ap;
    va_start(ap, format);
    pcc_capi_format_message(message, sizeof(message), format, &ap);
    va_end(ap);
    PyErr_SetString(type, message);
    return NULL;
}

PyObject *PyErr_FormatV(PyObject *type, const char *format, va_list vargs) {
    char message[2048];
    va_list ap;
    va_copy(ap, vargs);
    pcc_capi_format_message(message, sizeof(message), format, &ap);
    va_end(ap);
    PyErr_SetString(type, message);
    return NULL;
}

static const char *pcc_capi_errno_message(void) {
    const char *message = strerror(errno);
    return message != NULL ? message : "system error";
}

PyObject *PyErr_SetFromErrno(PyObject *type) {
    PyErr_SetString(type, pcc_capi_errno_message());
    return NULL;
}

PyObject *PyErr_SetFromErrnoWithFilenameObject(
    PyObject *type,
    PyObject *filenameObject
) {
    char message[2048];
    const char *err = pcc_capi_errno_message();
    const char *path = NULL;
    if (filenameObject != NULL && pcc_capi_is_exact_type(filenameObject, PY_TYPE_STR)) {
        path = py_str_utf8(filenameObject);
    }
    if (path != NULL && path[0] != '\0') {
        snprintf(message, sizeof(message), "%s: %s", err, path);
        PyErr_SetString(type, message);
    } else {
        PyErr_SetString(type, err);
    }
    return NULL;
}

PyObject *PyErr_NewException(const char *name, PyObject *base, PyObject *dict) {
    (void)dict;
    if (name == NULL || name[0] == '\0') {
        PyErr_SetString(PyExc_ValueError, "empty exception name");
        return NULL;
    }
    const char *leaf = strrchr(name, '.');
    leaf = leaf == NULL ? name : leaf + 1;
    if (leaf[0] == '\0') leaf = name;

    PyClassObject *base_cls = NULL;
    PyClassObject *bases[1];
    int32_t n_bases = 0;
    if (base != NULL && base != py_None && !PY_IS_TAGGED_INT(base)
        && py_type_of(base) == PY_TYPE_CLASS) {
        base_cls = (PyClassObject *)base;
        bases[0] = base_cls;
        n_bases = 1;
    }

    char *class_name = (char *)malloc(strlen(leaf) + 1);
    if (class_name == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "out of memory creating exception");
        return NULL;
    }
    strcpy(class_name, leaf);

    PyClassObject *cls = py_class_new(
        class_name,
        n_bases ? bases : NULL,
        n_bases,
        NULL,
        0
    );
    if (cls == NULL) {
        free(class_name);
        PyErr_SetString(PyExc_RuntimeError, "failed to create exception class");
        return NULL;
    }
    return (PyObject *)cls;
}

int PyErr_WarnEx(PyObject *category, const char *message, Py_ssize_t stack_level) {
    (void)category;
    (void)message;
    (void)stack_level;
    return 0;
}

int PyErr_WarnFormat(
    PyObject *category,
    Py_ssize_t stack_level,
    const char *format,
    ...
) {
    char message[2048];
    va_list ap;
    va_start(ap, format);
    pcc_capi_format_message(message, sizeof(message), format, &ap);
    va_end(ap);
    return PyErr_WarnEx(category, message, stack_level);
}

void PyErr_WriteUnraisable(PyObject *obj) {
    (void)obj;
    PyErr_Clear();
}

void PyErr_Print(void) {
    PyObject *cur = py_current_exception();
    if (cur == NULL) return;
    py_exc_print_unhandled(cur);
    py_clear_exception();
}

int PyErr_CheckSignals(void) {
    return 0;
}

PyObject *PyErr_Occurred(void) {
    PyObject *cur = py_current_exception();
    if (cur == NULL) return NULL;
    if (!PY_IS_TAGGED_INT(cur) && py_type_of(cur) == PY_TYPE_EXC) {
        PyExceptionObject *exc = (PyExceptionObject *)cur;
        PyObject *cls = pcc_gc_load_ptr(cur, (PyObject **)&exc->exc_class);
        if (cls != NULL) return cls;
    }
    return PyExc_RuntimeError;
}

void PyErr_Clear(void) {
    py_clear_exception();
}

int PyErr_GivenExceptionMatches(PyObject *given, PyObject *exc) {
    PyObject *cls = pcc_capi_exception_class(exc);
    if (given == NULL || cls == NULL) return 0;
    return py_exc_matches(given, cls) ? 1 : 0;
}

int PyErr_ExceptionMatches(PyObject *exc) {
    PyObject *cur = py_current_exception();
    if (cur == NULL) return 0;
    return PyErr_GivenExceptionMatches(cur, exc);
}

void PyErr_Fetch(PyObject **ptype, PyObject **pvalue, PyObject **ptraceback) {
    PyObject *cur = py_current_exception();
    PyObject *type = NULL;
    PyObject *value = NULL;
    if (cur != NULL) {
        py_incref(cur);
        value = cur;
        if (!PY_IS_TAGGED_INT(cur) && py_type_of(cur) == PY_TYPE_EXC) {
            PyExceptionObject *exc = (PyExceptionObject *)cur;
            type = pcc_gc_load_ptr(cur, (PyObject **)&exc->exc_class);
        }
        if (type == NULL) type = PyExc_RuntimeError;
        py_incref(type);
        py_clear_exception();
    }
    if (ptype != NULL) *ptype = type;
    else py_decref(type);
    if (pvalue != NULL) *pvalue = value;
    else py_decref(value);
    if (ptraceback != NULL) *ptraceback = NULL;
}

void PyErr_Restore(PyObject *type, PyObject *value, PyObject *traceback) {
    (void)traceback;
    if (value != NULL) {
        py_raise(value);
    } else if (type != NULL) {
        PyErr_SetString(type, "");
    } else {
        py_clear_exception();
    }
    py_decref(type);
    py_decref(value);
    py_decref(traceback);
}

static int pcc_capi_buffer_data(
    PyObject *obj,
    void **buf,
    Py_ssize_t *len,
    int *readonly
) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return -1;
    int32_t tag = py_type_of(obj);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *bytes = (PyBytesObject *)obj;
        *buf = (void *)bytes->data;
        *len = (Py_ssize_t)bytes->byte_len;
        *readonly = 1;
        return 0;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *bytes = (PyByteArrayObject *)obj;
        *buf = (void *)bytes->data;
        *len = (Py_ssize_t)bytes->byte_len;
        *readonly = 0;
        return 0;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *view = (PyMemoryViewObject *)obj;
        PyObject *base = pcc_gc_load_ptr(obj, &view->base);
        return pcc_capi_buffer_data(base, buf, len, readonly);
    }
    return -1;
}

int PyObject_CheckBuffer(PyObject *obj) {
    void *buf = NULL;
    Py_ssize_t len = 0;
    int readonly = 1;
    return pcc_capi_buffer_data(obj, &buf, &len, &readonly) == 0 ? 1 : 0;
}

int PyObject_GetBuffer(PyObject *obj, Py_buffer *view, int flags) {
    if (view == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "NULL Py_buffer");
        return -1;
    }
    memset(view, 0, sizeof(*view));
    void *buf = NULL;
    Py_ssize_t len = 0;
    int readonly = 1;
    if (pcc_capi_buffer_data(obj, &buf, &len, &readonly) != 0) {
        PyErr_SetString(PyExc_TypeError, "object does not support buffer protocol");
        return -1;
    }
    if ((flags & PyBUF_WRITABLE) != 0 && readonly) {
        PyErr_SetString(PyExc_TypeError, "object is not writable");
        return -1;
    }

    PccBufferMeta *meta = NULL;
    if ((flags & PyBUF_ND) != 0) {
        meta = (PccBufferMeta *)malloc(sizeof(PccBufferMeta));
        if (meta == NULL) {
            PyErr_SetString(PyExc_RuntimeError, "out of memory creating buffer view");
            return -1;
        }
        meta->shape = len;
        meta->strides = 1;
    }

    view->buf = buf;
    view->obj = obj;
    view->len = len;
    view->itemsize = 1;
    view->readonly = readonly;
    view->ndim = (flags & PyBUF_ND) != 0 ? 1 : 0;
    view->format = (flags & PyBUF_FORMAT) != 0 ? "B" : NULL;
    view->shape = meta != NULL ? &meta->shape : NULL;
    view->strides = ((flags & PyBUF_STRIDES) != 0 && meta != NULL)
        ? &meta->strides
        : NULL;
    view->suboffsets = NULL;
    view->internal = meta;
    py_incref(obj);
    return 0;
}

void PyBuffer_Release(Py_buffer *view) {
    if (view == NULL) return;
    if (view->obj != NULL) {
        py_decref(view->obj);
    }
    if (view->internal != NULL) {
        free(view->internal);
    }
    memset(view, 0, sizeof(*view));
}

int PyMemoryView_Check(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    return py_type_of(obj) == PY_TYPE_MEMORYVIEW ? 1 : 0;
}

Py_buffer *pcc_PyMemoryView_GET_BUFFER(PyObject *obj) {
    static _Thread_local Py_buffer cached_view;
    if (!PyMemoryView_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected memoryview");
        return NULL;
    }
    if (cached_view.obj != NULL) {
        PyBuffer_Release(&cached_view);
    }
    if (PyObject_GetBuffer(obj, &cached_view, PyBUF_STRIDES | PyBUF_FORMAT) != 0) {
        return NULL;
    }
    return &cached_view;
}

PyObject *pcc_PyMemoryView_GET_BASE(PyObject *obj) {
    if (!PyMemoryView_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected memoryview");
        return NULL;
    }
    PyMemoryViewObject *view = (PyMemoryViewObject *)obj;
    return pcc_gc_load_ptr(obj, &view->base);
}

PyObject *PyMemoryView_FromObject(PyObject *obj) {
    void *buf = NULL;
    Py_ssize_t len = 0;
    int readonly = 1;
    if (pcc_capi_buffer_data(obj, &buf, &len, &readonly) != 0) {
        PyErr_SetString(PyExc_TypeError, "object does not support buffer protocol");
        return NULL;
    }
    (void)buf;
    (void)len;
    (void)readonly;
    return py_memoryview_new(obj);
}

PyObject *PyMemoryView_FromMemory(char *mem, Py_ssize_t size, int flags) {
    PyObject *base = NULL;
    PyObject *view = NULL;
    if (size < 0) {
        PyErr_SetString(PyExc_ValueError, "negative memoryview size");
        return NULL;
    }
    if (mem == NULL && size > 0) {
        PyErr_SetString(PyExc_ValueError, "NULL memoryview buffer");
        return NULL;
    }
    if ((flags & PyBUF_WRITE) != 0) {
        base = pcc_capi_bytearray_from_memory(mem, size);
    } else {
        base = py_bytes_new(mem, (int64_t)size);
    }
    if (base == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "out of memory creating memoryview");
        return NULL;
    }
    view = py_memoryview_new(base);
    py_decref(base);
    return view;
}

PyObject *PyObject_Call(PyObject *callable, PyObject *args, PyObject *kwargs) {
    PyObject *call_args = args;
    PyObject *result = NULL;
    int made_args = 0;
    if (callable == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL callable");
        return NULL;
    }
    if (call_args == NULL) {
        call_args = py_tuple_new(0);
        made_args = 1;
        if (call_args == NULL) {
            PyErr_SetString(PyExc_RuntimeError, "out of memory creating call args");
            return NULL;
        }
    } else if (PY_IS_TAGGED_INT(call_args) || py_type_of(call_args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "PyObject_Call args must be tuple or NULL");
        return NULL;
    }
    if (kwargs == NULL) {
        kwargs = py_None;
    }
    result = py_obj_call(callable, call_args, kwargs);
    if (made_args) {
        py_decref(call_args);
    }
    if (result == NULL && py_err_occurred() == 0) {
        PyErr_SetString(PyExc_TypeError, "object is not callable");
    }
    return result;
}

PyObject *PyObject_CallObject(PyObject *callable, PyObject *args) {
    return PyObject_Call(callable, args, NULL);
}

static PyObject *pcc_capi_call_objargs_v(PyObject *callable, va_list *ap) {
    if (callable == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL callable");
        return NULL;
    }

    va_list count_ap;
    va_copy(count_ap, *ap);
    Py_ssize_t count = 0;
    while (va_arg(count_ap, PyObject *) != NULL) {
        count++;
    }
    va_end(count_ap);

    PyObject *args = py_tuple_new((int64_t)count);
    if (args == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "out of memory creating call args");
        return NULL;
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *item = va_arg(*ap, PyObject *);
        py_tuple_set_item(args, (int64_t)i, item);
    }
    (void)va_arg(*ap, PyObject *);

    PyObject *result = PyObject_Call(callable, args, NULL);
    py_decref(args);
    return result;
}

PyObject *PyObject_CallFunctionObjArgs(PyObject *callable, ...) {
    va_list ap;
    va_start(ap, callable);
    PyObject *result = pcc_capi_call_objargs_v(callable, &ap);
    va_end(ap);
    return result;
}

PyObject *PyObject_CallMethodObjArgs(PyObject *obj, PyObject *name, ...) {
    if (obj == NULL || name == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_CallMethodObjArgs call");
        return NULL;
    }
    PyObject *method = PyObject_GetAttr(obj, name);
    if (method == NULL) return NULL;

    va_list ap;
    va_start(ap, name);
    PyObject *result = pcc_capi_call_objargs_v(method, &ap);
    va_end(ap);
    py_decref(method);
    return result;
}

PyObject *PyObject_CallNoArgs(PyObject *callable) {
    return PyObject_CallFunctionObjArgs(callable, NULL);
}

PyObject *PyObject_CallOneArg(PyObject *callable, PyObject *arg) {
    if (arg == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL call argument");
        return NULL;
    }
    return PyObject_CallFunctionObjArgs(callable, arg, NULL);
}

PyObject *PyObject_Vectorcall(
    PyObject *callable,
    PyObject *const *args,
    size_t nargsf,
    PyObject *kwnames
) {
    vectorcallfunc vectorcall = NULL;
    if (callable == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL callable");
        return NULL;
    }
    if (pcc_capi_cext_vectorcall_slot(callable, &vectorcall)) {
        if (vectorcall == NULL) {
            PyErr_SetString(PyExc_TypeError, "vectorcall function is NULL");
            return NULL;
        }
        return vectorcall(callable, args, nargsf, kwnames);
    }
    size_t nargs = nargsf & ~PY_VECTORCALL_ARGUMENTS_OFFSET;
    Py_ssize_t nkwargs = 0;
    if (kwnames != NULL) {
        nkwargs = PyTuple_Size(kwnames);
        if (nkwargs < 0) return NULL;
    }
    if ((nargs > 0 || nkwargs > 0) && args == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL vectorcall args");
        return NULL;
    }
    PyObject *tuple = py_tuple_new((int64_t)nargs);
    if (tuple == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "out of memory creating vectorcall args");
        return NULL;
    }
    for (size_t i = 0; i < nargs; i++) {
        if (args[i] == NULL) {
            py_decref(tuple);
            PyErr_SetString(PyExc_TypeError, "NULL vectorcall argument");
            return NULL;
        }
        py_tuple_set_item(tuple, (int64_t)i, args[i]);
    }
    PyObject *kwargs = NULL;
    if (nkwargs > 0) {
        kwargs = PyDict_New();
        if (kwargs == NULL) {
            py_decref(tuple);
            return NULL;
        }
        for (Py_ssize_t i = 0; i < nkwargs; i++) {
            PyObject *key = PyTuple_GetItem(kwnames, i);
            PyObject *value = args[nargs + (size_t)i];
            if (key == NULL || value == NULL || !PyUnicode_Check(key)) {
                py_decref(kwargs);
                py_decref(tuple);
                if (key != NULL && !PyUnicode_Check(key)) {
                    PyErr_SetString(
                        PyExc_TypeError,
                        "vectorcall keyword names must be strings"
                    );
                } else if (value == NULL) {
                    PyErr_SetString(
                        PyExc_TypeError,
                        "NULL vectorcall keyword argument"
                    );
                }
                return NULL;
            }
            if (PyDict_SetItem(kwargs, key, value) != 0) {
                py_decref(kwargs);
                py_decref(tuple);
                return NULL;
            }
        }
    }
    PyObject *result = PyObject_Call(callable, tuple, kwargs);
    if (kwargs != NULL) py_decref(kwargs);
    py_decref(tuple);
    return result;
}

PyObject *PyObject_VectorcallMethod(
    PyObject *name,
    PyObject *const *args,
    size_t nargsf,
    PyObject *kwnames
) {
    size_t nargs = nargsf & ~PY_VECTORCALL_ARGUMENTS_OFFSET;
    if (name == NULL || args == NULL || nargs == 0 || args[0] == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid vectorcall method call");
        return NULL;
    }
    PyObject *method = PyObject_GetAttr(args[0], name);
    if (method == NULL) return NULL;
    size_t method_nargsf = (nargs - 1) | (
        nargsf & PY_VECTORCALL_ARGUMENTS_OFFSET
    );
    PyObject *result = PyObject_Vectorcall(
        method,
        args + 1,
        method_nargsf,
        kwnames
    );
    py_decref(method);
    return result;
}

PyObject *PyObject_CallMethodNoArgs(PyObject *obj, PyObject *name) {
    PyObject *method = PyObject_GetAttr(obj, name);
    if (method == NULL) return NULL;
    PyObject *result = PyObject_CallNoArgs(method);
    py_decref(method);
    return result;
}

PyObject *PyObject_CallMethodOneArg(PyObject *obj, PyObject *name, PyObject *arg) {
    PyObject *method = PyObject_GetAttr(obj, name);
    if (method == NULL) return NULL;
    PyObject *result = PyObject_CallOneArg(method, arg);
    py_decref(method);
    return result;
}

int Py_IsInitialized(void) {
    return 1;
}

PyGILState_STATE PyGILState_Ensure(void) {
    return 0;
}

void PyGILState_Release(PyGILState_STATE state) {
    (void)state;
}

int PyGILState_Check(void) {
    return 1;
}

int PySequence_Check(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    if (tag == PY_TYPE_TUPLE
        || tag == PY_TYPE_LIST
        || tag == PY_TYPE_STR
        || tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY
        || tag == PY_TYPE_MEMORYVIEW) {
        return 1;
    }
    /* A C-extension object is a sequence when its type exposes integer item
     * access through the sequence (sq_item) or mapping (mp_subscript) slot.
     * Without this, PySequence_GetItem raised "expected sequence", so the
     * PySeqIter_New that numpy's array tp_iter returns yielded nothing and
     * ``list(ndarray)`` / ``for x in ndarray`` produced an empty result under
     * no-libpython. Generic C-API protocol check — no package special-casing.
     * (pcc_capi_cext_object_getitem already dispatches through the same slots,
     * so any object accepted here is genuinely subscriptable by index.) */
    if (pcc_capi_is_cext_type_tag((int64_t)tag) != 0) {
        PyTypeObject *type = pcc_capi_cext_type_for_object(obj);
        if (type != NULL) {
            PccCapiSequenceMethods *seq =
                (PccCapiSequenceMethods *)type->tp_as_sequence;
            if (seq != NULL && seq->sq_item != NULL) return 1;
            PccCapiMappingMethods *map =
                (PccCapiMappingMethods *)type->tp_as_mapping;
            if (map != NULL && map->mp_subscript != NULL) return 1;
        }
    }
    return 0;
}

PyObject *PyObject_GetIter(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object is not iterable");
        return NULL;
    }
    return py_obj_iter(obj);
}

PyObject *PyObject_SelfIter(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object has no self iterator");
        return NULL;
    }
    Py_INCREF(obj);
    return obj;
}

int PyIter_Check(PyObject *obj) {
    if (obj == NULL || PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_type_of(obj);
    return tag == PY_TYPE_ITER || tag == PY_TYPE_GEN
        || pcc_capi_cext_object_is_iterator(obj);
}

/* batch 17 forward decls: the PySeqIter_New object (defined at end of file) is
 * iterated through the C-API PyIter_Next path here. */
static int pcc_capi_is_seqiter(PyObject *obj);
static PyObject *pcc_capi_seqiter_next(PyObject *obj);

PyObject *PyIter_Next(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object is not an iterator");
        return NULL;
    }
    if (pcc_capi_is_seqiter(obj)) {
        return pcc_capi_seqiter_next(obj);
    }
    PyObject *item = py_obj_next(obj);
    if (item == NULL && PyErr_ExceptionMatches(PyExc_StopIteration)) {
        PyErr_Clear();
    }
    return item;
}

int PyIter_NextItem(PyObject *iter, PyObject **item) {
    if (item == NULL) {
        PyErr_SetString(PyExc_SystemError, "NULL result pointer");
        return -1;
    }
    *item = NULL;
    if (iter == NULL || !PyIter_Check(iter)) {
        PyErr_SetString(PyExc_TypeError, "expected an iterator");
        return -1;
    }
    *item = PyIter_Next(iter);
    if (*item != NULL) return 1;
    return PyErr_Occurred() == NULL ? 0 : -1;
}

Py_ssize_t PySequence_Size(PyObject *obj) {
    if (!PySequence_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected sequence");
        return -1;
    }
    return (Py_ssize_t)py_obj_len(obj);
}

Py_ssize_t PySequence_Length(PyObject *obj) {
    return PySequence_Size(obj);
}

PyObject *PySequence_GetItem(PyObject *obj, Py_ssize_t index) {
    if (!PySequence_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected sequence");
        return NULL;
    }
    PyObject *key = py_int_from_i64((int64_t)index);
    if (key == NULL) return NULL;
    PyObject *item = py_obj_getitem(obj, key);
    py_decref(key);
    return item;
}

int PySequence_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value) {
    if (!PySequence_Check(obj) || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PySequence_SetItem call");
        return -1;
    }
    PyObject *key = py_int_from_i64((int64_t)index);
    if (key == NULL) return -1;
    int64_t rc = py_obj_setitem(obj, key, value);
    py_decref(key);
    if (rc != 0 && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "sequence does not support item assignment");
    }
    return rc == 0 ? 0 : -1;
}

int PySequence_Contains(PyObject *obj, PyObject *value) {
    if (obj == NULL || value == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PySequence_Contains call");
        return -1;
    }
    int64_t contains = py_obj_contains(obj, value);
    if (py_err_occurred()) return -1;
    return contains != 0 ? 1 : 0;
}

PyObject *PySequence_Concat(PyObject *left, PyObject *right) {
    if (!PySequence_Check(left) || !PySequence_Check(right)) {
        PyErr_SetString(PyExc_TypeError, "expected sequences");
        return NULL;
    }
    PyObject *result = py_obj_add(left, right);
    if (result == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "unsupported sequence concatenation");
    }
    return result;
}

PyObject *PySequence_Repeat(PyObject *obj, Py_ssize_t count) {
    if (!PySequence_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "expected sequence");
        return NULL;
    }
    PyObject *count_obj = py_int_from_i64((int64_t)count);
    if (count_obj == NULL) return NULL;
    PyObject *result = pcc_capi_repeat_sequence(obj, count_obj);
    py_decref(count_obj);
    if (result == NULL && !py_err_occurred()) {
        PyErr_SetString(PyExc_TypeError, "unsupported sequence repeat");
    }
    return result;
}

PyObject *PySequence_InPlaceConcat(PyObject *left, PyObject *right) {
    return PySequence_Concat(left, right);
}

PyObject *PySequence_InPlaceRepeat(PyObject *obj, Py_ssize_t count) {
    return PySequence_Repeat(obj, count);
}

PyObject *PySequence_Fast(PyObject *obj, const char *message) {
    if (PyTuple_Check(obj) || PyList_Check(obj)) {
        py_incref(obj);
        return obj;
    }
    if (!PySequence_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, message ? message : "expected sequence");
        return NULL;
    }
    return PySequence_Tuple(obj);
}

Py_ssize_t PySequence_Fast_GET_SIZE(PyObject *obj) {
    if (PyTuple_Check(obj)) return PyTuple_Size(obj);
    if (PyList_Check(obj)) return PyList_Size(obj);
    PyErr_SetString(PyExc_TypeError, "expected fast sequence");
    return -1;
}

PyObject **PySequence_Fast_ITEMS(PyObject *obj) {
    if (PyTuple_Check(obj)) {
        return ((PyTupleObject *)obj)->items;
    }
    if (PyList_Check(obj)) {
        return ((PyListObject *)obj)->items;
    }
    PyErr_SetString(PyExc_TypeError, "expected fast sequence");
    return NULL;
}

PyObject *PySequence_List(PyObject *obj) {
    Py_ssize_t n = PySequence_Size(obj);
    if (n < 0) return NULL;
    PyObject *out = PyList_New(0);
    if (out == NULL) return NULL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PySequence_GetItem(obj, i);
        if (item == NULL) {
            py_decref(out);
            return NULL;
        }
        if (PyList_Append(out, item) != 0) {
            py_decref(item);
            py_decref(out);
            return NULL;
        }
        py_decref(item);
    }
    return out;
}

PyObject *PySequence_Tuple(PyObject *obj) {
    Py_ssize_t n = PySequence_Size(obj);
    if (n < 0) return NULL;
    PyObject *out = PyTuple_New(n);
    if (out == NULL) return NULL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PySequence_GetItem(obj, i);
        if (item == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(out, (int64_t)i, item);
        py_decref(item);
    }
    return out;
}

typedef int (*PccCapiArgConverter)(PyObject *, void *);

static int pcc_capi_parse_one(
    PyObject *item,
    char code,
    char object_modifier,
    va_list *ap
) {
    if (code == 'l') {
        long *out = va_arg(*ap, long *);
        int overflow = 0;
        int64_t value = py_int_to_i64(item, &overflow);
        if (overflow) return 0;
        *out = (long)value;
        return 1;
    }
    if (code == 'i') {
        int *out = va_arg(*ap, int *);
        int overflow = 0;
        int64_t value = py_int_to_i64(item, &overflow);
        if (overflow || value < (int64_t)INT32_MIN || value > (int64_t)INT32_MAX) {
            return 0;
        }
        *out = (int)value;
        return 1;
    }
    if (code == 'n') {
        Py_ssize_t *out = va_arg(*ap, Py_ssize_t *);
        int overflow = 0;
        int64_t value = py_int_to_i64(item, &overflow);
        if (overflow) return 0;
        *out = (Py_ssize_t)value;
        return 1;
    }
    if (code == 'p') {
        int *out = va_arg(*ap, int *);
        int truth = PyObject_IsTrue(item);
        if (truth < 0) return 0;
        *out = truth;
        return 1;
    }
    if (code == 'O') {
        if (object_modifier == '!') {
            PyTypeObject *expected = va_arg(*ap, PyTypeObject *);
            PyObject **out = va_arg(*ap, PyObject **);
            if (!pcc_capi_typecheck(item, expected)) return 0;
            *out = item;
            return 1;
        }
        if (object_modifier == '&') {
            PccCapiArgConverter converter = va_arg(*ap, PccCapiArgConverter);
            void *out = va_arg(*ap, void *);
            return converter != NULL && converter(item, out);
        }
        PyObject **out = va_arg(*ap, PyObject **);
        *out = item;
        return 1;
    }
    if (code == 's') {
        const char **out = va_arg(*ap, const char **);
        if (item == NULL || PY_IS_TAGGED_INT(item) || py_type_of(item) != PY_TYPE_STR) {
            return 0;
        }
        *out = py_str_utf8(item);
        return 1;
    }
    if (code == 'y') {
        char **out = va_arg(*ap, char **);
        if (item == NULL || PY_IS_TAGGED_INT(item) || py_type_of(item) != PY_TYPE_BYTES) {
            return 0;
        }
        *out = ((PyBytesObject *)item)->data;
        return 1;
    }
    return 0;
}

static int pcc_capi_parse_one_hash(PyObject *item, char code, va_list *ap) {
    if (code == 's') {
        const char **out = va_arg(*ap, const char **);
        Py_ssize_t *len_out = va_arg(*ap, Py_ssize_t *);
        if (item == NULL || PY_IS_TAGGED_INT(item) || py_type_of(item) != PY_TYPE_STR) {
            return 0;
        }
        *out = py_str_utf8(item);
        *len_out = (Py_ssize_t)py_str_byte_len(item);
        return 1;
    }
    if (code == 'y') {
        char **out = va_arg(*ap, char **);
        Py_ssize_t *len_out = va_arg(*ap, Py_ssize_t *);
        if (item == NULL || PY_IS_TAGGED_INT(item) || py_type_of(item) != PY_TYPE_BYTES) {
            return 0;
        }
        PyBytesObject *bytes = (PyBytesObject *)item;
        *out = bytes->data;
        *len_out = (Py_ssize_t)bytes->byte_len;
        return 1;
    }
    return 0;
}

static int pcc_capi_is_parse_code(char c) {
    return c == 'l' || c == 'i' || c == 'n' || c == 'p'
        || c == 'O' || c == 's' || c == 'y';
}

static void pcc_capi_skip_parse_dest(
    char code,
    char object_modifier,
    int has_hash,
    va_list *ap
) {
    if (has_hash && (code == 's' || code == 'y')) {
        (void)va_arg(*ap, void *);
        (void)va_arg(*ap, Py_ssize_t *);
        return;
    }
    if (code == 'l') (void)va_arg(*ap, long *);
    else if (code == 'i') (void)va_arg(*ap, int *);
    else if (code == 'n') (void)va_arg(*ap, Py_ssize_t *);
    else if (code == 'p') (void)va_arg(*ap, int *);
    else if (code == 'O' && object_modifier == '!') {
        (void)va_arg(*ap, PyTypeObject *);
        (void)va_arg(*ap, PyObject **);
    }
    else if (code == 'O' && object_modifier == '&') {
        (void)va_arg(*ap, PccCapiArgConverter);
        (void)va_arg(*ap, void *);
    }
    else if (code == 'O') (void)va_arg(*ap, PyObject **);
    else if (code == 's') (void)va_arg(*ap, const char **);
    else if (code == 'y') (void)va_arg(*ap, char **);
}

static void pcc_capi_format_counts(
    const char *format,
    int *required,
    int *total
) {
    int req = 0;
    int all = 0;
    int optional = 0;
    for (const char *p = format; p != NULL && *p != '\0'; p++) {
        char c = *p;
        if (c == ':' || c == ';') break;
        if (c == '|') {
            optional = 1;
            continue;
        }
        if (pcc_capi_is_parse_code(c)) {
            all++;
            if (!optional) req++;
            if ((c == 's' || c == 'y') && p[1] == '#') p++;
            else if (c == 'O' && (p[1] == '!' || p[1] == '&')) p++;
        }
    }
    if (required != NULL) *required = req;
    if (total != NULL) *total = all;
}

static void pcc_capi_build_skip(const char **p) {
    while (
        p != NULL && *p != NULL
        && (**p == ' ' || **p == '\t' || **p == '\n' || **p == ',')
    ) {
        *p += 1;
    }
}

static PyObject *pcc_capi_build_many(
    const char **p,
    va_list *ap,
    char terminator,
    int force_tuple
);

static PyObject *pcc_capi_build_none(void) {
    py_incref(py_None);
    return py_None;
}

static PyObject *pcc_capi_build_one(const char **p, va_list *ap) {
    pcc_capi_build_skip(p);
    char code = **p;
    if (code == '\0') return pcc_capi_build_none();
    *p += 1;

    if (code == '(') {
        return pcc_capi_build_many(p, ap, ')', 1);
    }
    if (code == '[') {
        PyObject *items = pcc_capi_build_many(p, ap, ']', 1);
        if (items == NULL) return NULL;
        PyObject *list = PySequence_List(items);
        py_decref(items);
        return list;
    }
    if (code == '{') {
        PyObject *dict = PyDict_New();
        if (dict == NULL) return NULL;
        for (;;) {
            pcc_capi_build_skip(p);
            if (**p == '}') {
                *p += 1;
                return dict;
            }
            if (**p == '\0') {
                py_decref(dict);
                PyErr_SetString(
                    PyExc_ValueError,
                    "unterminated Py_BuildValue dict"
                );
                return NULL;
            }
            PyObject *key = pcc_capi_build_one(p, ap);
            if (key == NULL) {
                py_decref(dict);
                return NULL;
            }
            pcc_capi_build_skip(p);
            if (**p == ':') *p += 1;
            pcc_capi_build_skip(p);
            if (**p == '}' || **p == '\0') {
                py_decref(key);
                py_decref(dict);
                PyErr_SetString(
                    PyExc_ValueError,
                    "Py_BuildValue dict requires key/value pairs"
                );
                return NULL;
            }
            PyObject *value = pcc_capi_build_one(p, ap);
            if (value == NULL) {
                py_decref(key);
                py_decref(dict);
                return NULL;
            }
            int rc = PyDict_SetItem(dict, key, value);
            py_decref(key);
            py_decref(value);
            if (rc != 0) {
                py_decref(dict);
                return NULL;
            }
        }
    }
    if (code == 'b' || code == 'h' || code == 'i') {
        return PyLong_FromLong((long)va_arg(*ap, int));
    }
    if (code == 'l') {
        if (**p == 'l') {
            *p += 1;
            return PyLong_FromLongLong(va_arg(*ap, long long));
        }
        return PyLong_FromLong(va_arg(*ap, long));
    }
    if (code == 'L') {
        return PyLong_FromLongLong(va_arg(*ap, long long));
    }
    if (code == 'n') {
        return PyLong_FromLong((long)va_arg(*ap, Py_ssize_t));
    }
    if (code == 'k') {
        return PyLong_FromUnsignedLong(va_arg(*ap, unsigned long));
    }
    if (code == 'K') {
        return PyLong_FromUnsignedLongLong(va_arg(*ap, unsigned long long));
    }
    if (code == 'f' || code == 'd') {
        return PyFloat_FromDouble(va_arg(*ap, double));
    }
    if (code == 's') {
        const char *value = va_arg(*ap, const char *);
        if (**p == '#') {
            *p += 1;
            Py_ssize_t len = va_arg(*ap, Py_ssize_t);
            if (value == NULL) return pcc_capi_build_none();
            return PyUnicode_FromStringAndSize(value, len);
        }
        if (value == NULL) return pcc_capi_build_none();
        return PyUnicode_FromString(value);
    }
    if (code == 'y') {
        const char *value = va_arg(*ap, const char *);
        if (**p == '#') {
            *p += 1;
            Py_ssize_t len = va_arg(*ap, Py_ssize_t);
            if (value == NULL) return pcc_capi_build_none();
            return PyBytes_FromStringAndSize(value, len);
        }
        if (value == NULL) return pcc_capi_build_none();
        return PyBytes_FromStringAndSize(value, (Py_ssize_t)strlen(value));
    }
    if (code == 'O' || code == 'S' || code == 'Y' || code == 'U') {
        PyObject *obj = va_arg(*ap, PyObject *);
        if (obj == NULL) return pcc_capi_build_none();
        py_incref(obj);
        return obj;
    }
    if (code == 'N') {
        PyObject *obj = va_arg(*ap, PyObject *);
        if (obj == NULL) return PyErr_NoMemory();
        return obj;
    }

    PyErr_Format(PyExc_ValueError, "unsupported Py_BuildValue format: %c", code);
    return NULL;
}

static PyObject *pcc_capi_build_many(
    const char **p,
    va_list *ap,
    char terminator,
    int force_tuple
) {
    PyObject **items = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t cap = 0;

    while (p != NULL && *p != NULL) {
        pcc_capi_build_skip(p);
        if (**p == '\0' || (terminator != '\0' && **p == terminator)) break;
        PyObject *item = pcc_capi_build_one(p, ap);
        if (item == NULL) {
            for (Py_ssize_t i = 0; i < count; i++) py_decref(items[i]);
            free(items);
            return NULL;
        }
        if (count == cap) {
            Py_ssize_t next_cap = cap == 0 ? 4 : cap * 2;
            PyObject **next = (PyObject **)realloc(
                items,
                (size_t)next_cap * sizeof(PyObject *)
            );
            if (next == NULL) {
                py_decref(item);
                for (Py_ssize_t i = 0; i < count; i++) py_decref(items[i]);
                free(items);
                return PyErr_NoMemory();
            }
            items = next;
            cap = next_cap;
        }
        items[count++] = item;
    }

    if (terminator != '\0') {
        if (**p != terminator) {
            for (Py_ssize_t i = 0; i < count; i++) py_decref(items[i]);
            free(items);
            PyErr_SetString(PyExc_ValueError, "unterminated Py_BuildValue tuple");
            return NULL;
        }
        *p += 1;
    }

    if (!force_tuple && count == 0) {
        free(items);
        return pcc_capi_build_none();
    }
    if (!force_tuple && count == 1) {
        PyObject *only = items[0];
        free(items);
        return only;
    }

    PyObject *tuple = PyTuple_New(count);
    if (tuple == NULL) {
        for (Py_ssize_t i = 0; i < count; i++) py_decref(items[i]);
        free(items);
        return NULL;
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        if (PyTuple_SetItem(tuple, i, items[i]) != 0) {
            for (Py_ssize_t j = i; j < count; j++) py_decref(items[j]);
            py_decref(tuple);
            free(items);
            return NULL;
        }
    }
    free(items);
    return tuple;
}

PyObject *Py_BuildValue(const char *format, ...) {
    if (format == NULL) {
        PyErr_SetString(PyExc_ValueError, "NULL Py_BuildValue format");
        return NULL;
    }
    va_list ap;
    va_start(ap, format);
    const char *p = format;
    PyObject *out = pcc_capi_build_many(&p, &ap, '\0', 0);
    va_end(ap);
    return out;
}

static PyObject *pcc_capi_build_call_args(const char *format, va_list *ap) {
    if (format == NULL) {
        return PyTuple_New(0);
    }
    const char *p = format;
    return pcc_capi_build_many(&p, ap, '\0', 1);
}

PyObject *PyObject_CallFunction(PyObject *callable, const char *format, ...) {
    va_list ap;
    va_start(ap, format);
    PyObject *args = pcc_capi_build_call_args(format, &ap);
    va_end(ap);
    if (args == NULL) return NULL;
    PyObject *result = PyObject_Call(callable, args, NULL);
    py_decref(args);
    return result;
}

PyObject *PyObject_CallMethod(PyObject *obj, const char *name, const char *format, ...) {
    if (obj == NULL || name == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid PyObject_CallMethod call");
        return NULL;
    }
    PyObject *method = PyObject_GetAttrString(obj, name);
    if (method == NULL) return NULL;

    va_list ap;
    va_start(ap, format);
    PyObject *args = pcc_capi_build_call_args(format, &ap);
    va_end(ap);
    if (args == NULL) {
        py_decref(method);
        return NULL;
    }
    PyObject *result = PyObject_Call(method, args, NULL);
    py_decref(args);
    py_decref(method);
    return result;
}

int PyArg_ParseTuple(PyObject *args, const char *format, ...) {
    if (args == NULL || PY_IS_TAGGED_INT(args) || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "expected argument tuple");
        return 0;
    }
    int required = 0;
    int total = 0;
    pcc_capi_format_counts(format, &required, &total);
    int64_t nargs = py_tuple_len(args);
    if (nargs < required || nargs > total) {
        PyErr_SetString(PyExc_TypeError, "argument count mismatch");
        return 0;
    }
    va_list ap;
    va_start(ap, format);
    int index = 0;
    int ok = 1;
    for (const char *p = format; p != NULL && *p != '\0'; p++) {
        char c = *p;
        if (c == ':' || c == ';') break;
        if (c == '|') continue;
        if (!pcc_capi_is_parse_code(c)) continue;
        char object_modifier = (
            c == 'O' && (p[1] == '!' || p[1] == '&') ? p[1] : '\0'
        );
        int has_hash = (c == 's' || c == 'y') && p[1] == '#';
        if (index < nargs) {
            PyObject *item = py_tuple_get(args, (int64_t)index);
            int parsed = has_hash
                ? pcc_capi_parse_one_hash(item, c, &ap)
                : pcc_capi_parse_one(item, c, object_modifier, &ap);
            py_decref(item);
            if (!parsed) {
                ok = 0;
                break;
            }
        } else {
            pcc_capi_skip_parse_dest(c, object_modifier, has_hash, &ap);
        }
        index++;
        if (has_hash) p++;
        else if (object_modifier != '\0') p++;
    }
    va_end(ap);
    if (!ok) {
        PyErr_SetString(PyExc_TypeError, "argument type mismatch");
        return 0;
    }
    return 1;
}

/* va_list CORE (canonical CPython structure: the `...` PyArg_ParseTupleAndKeywords
 * below is a thin wrapper over this). numpy's C core references this directly
 * (e.g. METH_VARARGS|METH_KEYWORDS helpers that forward their own va_list).
 * va_copy so the caller's va_list is not consumed (it is an array type on
 * arm64/x86-64 SysV — passing it shares state with the caller otherwise). */
int PyArg_VaParseTupleAndKeywords(
    PyObject *args,
    PyObject *kwargs,
    const char *format,
    char **kwlist,
    va_list va
) {
    if (args == NULL || PY_IS_TAGGED_INT(args) || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "expected argument tuple");
        return 0;
    }
    if (kwargs != NULL && kwargs != py_None
        && (PY_IS_TAGGED_INT(kwargs) || py_type_of(kwargs) != PY_TYPE_DICT)) {
        PyErr_SetString(PyExc_TypeError, "expected keyword dict");
        return 0;
    }

    int required = 0;
    int total = 0;
    pcc_capi_format_counts(format, &required, &total);
    int64_t nargs = py_tuple_len(args);
    if (nargs > total) {
        PyErr_Format(
            PyExc_TypeError,
            "too many positional arguments (got %lld, max %d)",
            (long long)nargs,
            total
        );
        return 0;
    }

    va_list ap;
    va_copy(ap, va);
    int index = 0;
    int ok = 1;
    for (const char *p = format; p != NULL && *p != '\0'; p++) {
        char c = *p;
        if (c == ':' || c == ';') break;
        if (c == '|') continue;
        if (!pcc_capi_is_parse_code(c)) continue;
        char object_modifier = (
            c == 'O' && (p[1] == '!' || p[1] == '&') ? p[1] : '\0'
        );

        int has_hash = (c == 's' || c == 'y') && p[1] == '#';
        PyObject *owned_item = NULL;
        PyObject *item = NULL;
        if (index < nargs) {
            owned_item = py_tuple_get(args, (int64_t)index);
            item = owned_item;
        } else if (kwargs != NULL && kwargs != py_None
            && kwlist != NULL && kwlist[index] != NULL) {
            item = PyDict_GetItemString(kwargs, kwlist[index]);
        }

        if (item == NULL) {
            if (index < required) {
                ok = 0;
                if (owned_item != NULL) py_decref(owned_item);
                break;
            }
            pcc_capi_skip_parse_dest(c, object_modifier, has_hash, &ap);
        } else {
            int parsed = has_hash
                ? pcc_capi_parse_one_hash(item, c, &ap)
                : pcc_capi_parse_one(item, c, object_modifier, &ap);
            if (!parsed) ok = 0;
        }
        if (owned_item != NULL) py_decref(owned_item);
        if (!ok) break;
        index++;
        if (has_hash) p++;
        else if (object_modifier != '\0') p++;
    }
    va_end(ap);
    if (!ok) {
        PyErr_SetString(PyExc_TypeError, "argument type mismatch");
        return 0;
    }
    return 1;
}

int PyArg_ParseTupleAndKeywords(
    PyObject *args,
    PyObject *kwargs,
    const char *format,
    char **kwlist,
    ...
) {
    va_list va;
    va_start(va, kwlist);
    int r = PyArg_VaParseTupleAndKeywords(args, kwargs, format, kwlist, va);
    va_end(va);
    return r;
}

typedef struct PccCapiModuleStateNode {
    PyObject *module;
    PyModuleDef *def;
    void *state;
    struct PccCapiModuleStateNode *next;
} PccCapiModuleStateNode;

static PccCapiModuleStateNode *pcc_capi_module_states = NULL;

static PccCapiModuleStateNode *pcc_capi_find_module_state(PyObject *module) {
    for (
        PccCapiModuleStateNode *n = pcc_capi_module_states;
        n != NULL;
        n = n->next
    ) {
        if (n->module == module) return n;
    }
    return NULL;
}

PyObject *PyType_GetModuleByDef(PyTypeObject *type, PyModuleDef *def) {
    if (type == NULL || def == NULL) {
        PyErr_SetString(PyExc_TypeError, "invalid type or module definition");
        return NULL;
    }
    for (int guard = 0; type != NULL && guard < 64; guard++) {
        PyObject *module = NULL;
        if (type->tp_version_tag >= PCC_CAPI_CEXT_TAG_BASE) {
            int32_t offset = (
                (int32_t)type->tp_version_tag - PCC_CAPI_CEXT_TAG_BASE
            );
            if (offset >= 0 && offset < pcc_capi_cext_type_count) {
                module = pcc_capi_cext_type_modules[offset];
            }
        }
        PccCapiModuleStateNode *state = pcc_capi_find_module_state(module);
        if (state != NULL && state->def == def) return module;
        type = (PyTypeObject *)type->tp_base;
    }
    PyErr_SetString(PyExc_TypeError, "module definition not found in type MRO");
    return NULL;
}

static int pcc_capi_register_module_state(
    PyObject *module,
    PyModuleDef *def,
    void *state
) {
    PccCapiModuleStateNode *node = (
        PccCapiModuleStateNode *
    )calloc(1, sizeof(PccCapiModuleStateNode));
    if (node == NULL) return -1;
    node->module = module;
    node->def = def;
    node->state = state;
    node->next = pcc_capi_module_states;
    pcc_capi_module_states = node;
    if (
        module != NULL
        && !PY_IS_TAGGED_INT(module)
        && (py_header_flags_load(py_header(module)) & PY_FLAG_GC_PINNED) == 0
    ) {
        pcc_gc_pin(module);
    }
    return 0;
}

PyObject *PyModule_Create2(PyModuleDef *def, int api_version) {
    (void)api_version;
    if (def == NULL || def->m_name == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "invalid module definition");
        return NULL;
    }
    PyClassObject *cls = pcc_runtime_module_class();
    if (cls == NULL) return NULL;
    PyObject *module = py_instance_new(cls);
    if (module == NULL) return NULL;

    if (def->m_size > 0) {
        void *state = calloc(1, (size_t)def->m_size);
        if (state == NULL) {
            py_decref(module);
            return PyErr_NoMemory();
        }
        if (pcc_capi_register_module_state(module, def, state) != 0) {
            free(state);
            py_decref(module);
            return PyErr_NoMemory();
        }
    }

    PyObject *name = py_str_new(def->m_name, (int64_t)strlen(def->m_name));
    if (name != NULL) {
        py_instance_setattr((PyInstanceObject *)module, "__name__", name);
        py_decref(name);
    }

    PyMethodDef *method = def->m_methods;
    while (method != NULL && method->ml_name != NULL) {
        PyObject *fn = pcc_capi_method_func_new(module, method);
        if (fn != NULL) {
            py_instance_setattr((PyInstanceObject *)module, method->ml_name, fn);
            py_decref(fn);
        }
        method++;
    }
    return module;
}

void *PyModule_GetState(PyObject *module) {
    PccCapiModuleStateNode *node = pcc_capi_find_module_state(module);
    return node != NULL ? node->state : NULL;
}

typedef struct PccCapiObjectSlotVisitCtx {
    PyObjSlotVisitor visit;
    void *ctx;
} PccCapiObjectSlotVisitCtx;

static int pcc_capi_visit_cext_object_slot_ref(PyObject *obj, void *arg) {
    (void)obj;
    (void)arg;
    return 0;
}

int pcc_capi_visit_slot(PyObject **slot, visitproc visit, void *arg) {
    if (slot == NULL || visit == NULL) return 0;
    if (visit == pcc_capi_visit_cext_object_slot_ref) {
        PccCapiObjectSlotVisitCtx *visit_ctx = (
            PccCapiObjectSlotVisitCtx *
        )arg;
        if (visit_ctx == NULL || visit_ctx->visit == NULL) return 0;
        visit_ctx->visit(slot, PY_OBJ_SLOT_OWNED, visit_ctx->ctx);
        return 0;
    }
    PyObject *obj = pcc_gc_load_ptr(NULL, slot);
    if (obj == NULL) return 0;
    return visit(obj, arg);
}

int pcc_capi_visit_cext_object_slots(
    PyObject *o,
    PyObjSlotVisitor visit,
    void *ctx
) {
    if (o == NULL || PY_IS_TAGGED_INT(o) || visit == NULL) return 0;
    int64_t offset = (int64_t)py_header(o)->type_tag - PCC_CAPI_CEXT_TAG_BASE;
    if (offset < 0 || offset >= pcc_capi_cext_type_count) return 0;
    PyTypeObject *type = pcc_capi_cext_types[offset];
    if (type == NULL) return 1;
    if (type->tp_traverse == NULL) return 1;
    PccCapiObjectSlotVisitCtx visit_ctx = { visit, ctx };
    traverseproc traverse = (traverseproc)type->tp_traverse;
    (void)traverse(o, pcc_capi_visit_cext_object_slot_ref, &visit_ctx);
    return 1;
}

typedef struct PccCapiObjectSlotVisitI64Ctx {
    PccPyObjSlotVisitorI64 visit;
    void *ctx;
} PccCapiObjectSlotVisitI64Ctx;

static void pcc_capi_visit_cext_object_slot_i64_adapter(
    PyObject **slot,
    int32_t role,
    void *arg
) {
    PccCapiObjectSlotVisitI64Ctx *visit_ctx = (
        PccCapiObjectSlotVisitI64Ctx *
    )arg;
    if (visit_ctx == NULL || visit_ctx->visit == NULL) return;
    visit_ctx->visit(slot, (int64_t)role, visit_ctx->ctx);
}

int pcc_capi_visit_cext_object_slots_i64(
    PyObject *o,
    PccPyObjSlotVisitorI64 visit,
    void *ctx
) {
    if (visit == NULL) return 0;
    PccCapiObjectSlotVisitI64Ctx visit_ctx = { visit, ctx };
    return pcc_capi_visit_cext_object_slots(
        o,
        pcc_capi_visit_cext_object_slot_i64_adapter,
        &visit_ctx
    );
}

typedef struct PccCapiModuleStateVisitCtx {
    PccGcRootVisitor visit;
    void *ctx;
} PccCapiModuleStateVisitCtx;

static int pcc_capi_visit_module_state_ref(PyObject *obj, void *arg) {
    if (obj == NULL) return 0;
    PccCapiModuleStateVisitCtx *visit_ctx = (
        PccCapiModuleStateVisitCtx *
    )arg;
    if (visit_ctx == NULL || visit_ctx->visit == NULL) return 0;
    if (
        !PY_IS_TAGGED_INT(obj)
        && pcc_gc_object_is_known_no_lock(obj) != 0
        && (py_header_flags_load(py_header(obj)) & PY_FLAG_GC_PINNED) == 0
    ) {
        pcc_gc_pin(obj);
    }
    visit_ctx->visit(obj, visit_ctx->ctx);
    return 0;
}

void pcc_capi_visit_extension_module_state_roots(
    PccGcRootVisitor visit,
    void *ctx
) {
    if (visit == NULL) return;
    PccCapiModuleStateVisitCtx visit_ctx = {visit, ctx};
    for (
        PccCapiModuleStateNode *n = pcc_capi_module_states;
        n != NULL;
        n = n->next
    ) {
        if (n->module != NULL) visit(n->module, ctx);
        if (n->def == NULL || n->def->m_traverse == NULL) continue;
        traverseproc traverse = (traverseproc)n->def->m_traverse;
        (void)traverse(n->module, pcc_capi_visit_module_state_ref, &visit_ctx);
    }
}

/* Multi-phase init is split so the extension loader can register the module in
 * its load-once cache BETWEEN creation and exec (CPython's PEP 489 contract:
 * the module enters sys.modules before exec_module runs, so a nested import of
 * the same module during exec returns the in-progress module instead of
 * re-running PyInit — numpy's _multiarray_umath rejects a second init per
 * process). */
PyObject *pcc_capi_module_from_def(PyObject *def_as_obj) {
    PyModuleDef *def = (PyModuleDef *)def_as_obj;
    if (def == NULL) return NULL;
    return PyModule_Create2(def, 0);
}

/* Invoke each Py_mod_exec slot with the module (where numpy registers ndarray /
 * the PyArray_API capsule / static data). Returns 0, or -1 on a slot error. */
int pcc_capi_module_run_exec_slots(PyObject *def_as_obj, PyObject *module) {
    PyModuleDef *def = (PyModuleDef *)def_as_obj;
    if (def == NULL || module == NULL) return -1;
    if (def->m_slots != NULL) {
        for (PyModuleDef_Slot *s = def->m_slots; s->slot != 0; s++) {
            if (s->slot == PCC_Py_mod_exec && s->value != NULL) {
                int (*exec_fn)(PyObject *) = (int (*)(PyObject *))s->value;
                if (exec_fn(module) != 0) {
                    if (!py_err_occurred()) {
                        PyErr_SetString(PyExc_SystemError,
                                        "module exec slot failed");
                    }
                    return -1;
                }
            }
            /* PCC_Py_mod_create is rarely used by numpy; default module is fine. */
        }
    }
    return 0;
}

/* Create + exec in one step. Kept for callers that have no nested-import
 * re-entry concern. */
PyObject *pcc_capi_module_exec(PyObject *def_as_obj) {
    PyObject *module = pcc_capi_module_from_def(def_as_obj);
    if (module == NULL) return NULL;
    if (pcc_capi_module_run_exec_slots(def_as_obj, module) != 0) {
        py_decref(module);
        return NULL;
    }
    return module;
}

PyObject *PyModule_GetDict(PyObject *module) {
    if (module == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL module");
        return NULL;
    }
    PyObject *dict = py_obj_getattr(module, "__dict__");
    if (dict == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected module object");
        return NULL;
    }
    /* py_obj_getattr returns owned; CPython PyModule_GetDict returns borrowed. */
    py_decref(dict);
    return dict;
}

int PyModule_AddObject(PyObject *module, const char *name, PyObject *value) {
    if (module == NULL || name == NULL || value == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "invalid PyModule_AddObject call");
        return -1;
    }
    int64_t rc = py_obj_setattr(module, name, value);
    if (rc != 0) return -1;
    py_decref(value);
    return 0;
}

int PyModule_AddObjectRef(PyObject *module, const char *name, PyObject *value) {
    if (value == NULL) {
        if (!py_err_occurred()) {
            PyErr_SetString(
                PyExc_SystemError,
                "PyModule_AddObjectRef must be called with an exception raised if value is NULL"
            );
        }
        return -1;
    }
    py_incref(value);
    int rc = PyModule_AddObject(module, name, value);
    if (rc != 0) {
        py_decref(value);
    }
    return rc;
}

int PyModule_Add(PyObject *module, const char *name, PyObject *value) {
    int rc = PyModule_AddObjectRef(module, name, value);
    if (value != NULL) Py_DECREF(value);
    return rc;
}

int PyModule_AddIntConstant(PyObject *module, const char *name, long value) {
    PyObject *obj = PyLong_FromLong(value);
    if (obj == NULL) return -1;
    return PyModule_AddObject(module, name, obj);
}

int PyModule_AddStringConstant(PyObject *module, const char *name, const char *value) {
    PyObject *obj = PyUnicode_FromString(value == NULL ? "" : value);
    if (obj == NULL) return -1;
    return PyModule_AddObject(module, name, obj);
}

PyObject *PyImport_ImportModule(const char *name) {
    if (name == NULL || name[0] == '\0') {
        PyErr_SetString(PyExc_ValueError, "empty module name");
        return NULL;
    }
    PyObject *module = py_native_extension_import_by_name(name);
    if (module == NULL && !py_err_occurred()) {
        module = py_compiled_module_import_by_name(name);
    }
    if (module == NULL && !py_err_occurred()) {
        PyErr_Format(
            PyExc_RuntimeError,
            "PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: %s",
            name
        );
    }
    return module;
}

/* --- batch 14: full-_core host symbols routed to existing pcc primitives /
 * extending the batch-10 contextvar. All genuine, not stubs. */

/* PyImport_Import(name_obj) is importlib's entry; PyImport_ImportModule is a
 * faithful implementation for the by-name case numpy uses (it interns the name
 * then imports). */
PyObject *PyImport_Import(PyObject *name) {
    if (name == NULL) {
        PyErr_SetString(PyExc_TypeError, "import name required");
        return NULL;
    }
    const char *cname = PyUnicode_AsUTF8(name);
    if (cname == NULL) return NULL;
    return PyImport_ImportModule(cname);
}

PyObject *py_builtin_import(PyObject *name, PyObject *fromlist) {
    if (name == NULL || PY_IS_TAGGED_INT(name) || py_type_of(name) != PY_TYPE_STR) {
        PyErr_SetString(PyExc_TypeError, "module name must be a string");
        return NULL;
    }
    const char *cname = py_str_utf8(name);
    if (cname == NULL) return NULL;

    PyObject *module = PyImport_ImportModule(cname);
    if (module == NULL) return NULL;

    int64_t fromlist_count = 0;
    if (fromlist != NULL && fromlist != py_None) {
        fromlist_count = py_obj_len(fromlist);
        if (fromlist_count < 0) {
            py_decref(module);
            return NULL;
        }
    }
    if (fromlist_count > 0) return module;

    const char *dot = strchr(cname, '.');
    if (dot == NULL) return module;
    size_t top_len = (size_t)(dot - cname);
    char *top_name = (char *)malloc(top_len + 1);
    if (top_name == NULL) {
        py_decref(module);
        PyErr_SetString(PyExc_MemoryError, "out of memory importing module");
        return NULL;
    }
    memcpy(top_name, cname, top_len);
    top_name[top_len] = '\0';
    PyObject *top_module = PyImport_ImportModule(top_name);
    free(top_name);
    py_decref(module);
    return top_module;
}

/* Generic tp_call entry used by vectorcall-aware C extension types.  numpy's
 * ufunc and array-function dispatcher types store a vectorcallfunc in each
 * instance and install this function as tp_call.  Calling PyObject_Call again
 * before reading that slot would recurse through tp_call until stack overflow. */
PyObject *PyVectorcall_Call(PyObject *callable, PyObject *tuple, PyObject *dict) {
    vectorcallfunc vectorcall = NULL;
    if (!pcc_capi_cext_vectorcall_slot(callable, &vectorcall)) {
        return PyObject_Call(callable, tuple, dict);
    }
    if (vectorcall == NULL) {
        PyErr_SetString(PyExc_TypeError, "vectorcall function is NULL");
        return NULL;
    }

    Py_ssize_t nargs = PyTuple_Size(tuple);
    if (nargs < 0) return NULL;
    Py_ssize_t nkwargs = 0;
    if (dict != NULL) {
        if (!PyDict_Check(dict)) {
            PyErr_SetString(PyExc_TypeError, "vectorcall kwargs must be a dict");
            return NULL;
        }
        nkwargs = PyDict_Size(dict);
        if (nkwargs < 0) return NULL;
    }
    if (nargs > LONG_MAX - nkwargs) {
        return PyErr_NoMemory();
    }

    Py_ssize_t total = nargs + nkwargs;
    PyObject **values = NULL;
    if (total > 0) {
        values = (PyObject **)calloc((size_t)total, sizeof(PyObject *));
        if (values == NULL) return PyErr_NoMemory();
    }
    for (Py_ssize_t i = 0; i < nargs; i++) {
        values[i] = PyTuple_GetItem(tuple, i);
        if (values[i] == NULL) {
            free(values);
            return NULL;
        }
    }

    PyObject *kwnames = NULL;
    if (nkwargs > 0) {
        kwnames = PyTuple_New(nkwargs);
        if (kwnames == NULL) {
            free(values);
            return NULL;
        }
        Py_ssize_t pos = 0;
        Py_ssize_t index = 0;
        PyObject *key = NULL;
        PyObject *value = NULL;
        while (PyDict_Next(dict, &pos, &key, &value)) {
            if (index >= nkwargs) {
                PyErr_SetString(PyExc_RuntimeError, "kwargs changed during call");
                py_decref(kwnames);
                free(values);
                return NULL;
            }
            values[nargs + index] = value;
            py_incref(key);
            if (PyTuple_SetItem(kwnames, index, key) != 0) {
                py_decref(key);
                py_decref(kwnames);
                free(values);
                return NULL;
            }
            index++;
        }
        if (index != nkwargs) {
            PyErr_SetString(PyExc_RuntimeError, "kwargs changed during call");
            py_decref(kwnames);
            free(values);
            return NULL;
        }
    }

    PyObject *result = vectorcall(
        callable,
        (PyObject *const *)values,
        (size_t)nargs,
        kwnames
    );
    if (kwnames != NULL) py_decref(kwnames);
    free(values);
    return result;
}

/* Update the single-context value and return an opaque token represented as
 * (var, previously-set, previous-value).  The explicit set bit is required to
 * distinguish an unset variable from a variable whose value was actually
 * None, which makes the Python-visible reset() bridge below semantic rather
 * than a NumPy-only set-and-discard shim. */
PyObject *PyContextVar_Set(PyObject *var, PyObject *value) {
    if (var == NULL) {
        PyErr_SetString(PyExc_TypeError, "ContextVar required");
        return NULL;
    }
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)var;
    PyObject *prev = pcc_gc_load_ptr(var, &cv->value);
                                          /* owned by cv, may be NULL (unset) */
    if (value != NULL) py_incref(value);
    cv->value = value;                   /* move cv's old ownership to token */
    pcc_gc_note_slot_write_barrier(var, &cv->value, value);
    PyObject *tok = PyTuple_New(3);
    if (tok == NULL) {
        if (value != NULL) py_decref(value);
        cv->value = prev;                /* restore: keep cv's reference intact */
        pcc_gc_note_slot_write_barrier(var, &cv->value, prev);
        return NULL;
    }
    py_incref(var);
    PyTuple_SetItem(tok, 0, var);        /* steals the extra ref */
    PyObject *had_value = PyBool_FromLong(prev != NULL);
    if (had_value == NULL || PyTuple_SetItem(tok, 1, had_value) != 0) {
        if (had_value != NULL) py_decref(had_value);
        py_decref(tok);
        return NULL;
    }
    if (prev == NULL) {
        py_incref(py_None);
        PyTuple_SetItem(tok, 2, py_None);
    } else {
        PyTuple_SetItem(tok, 2, prev);   /* token takes over cv's old reference */
    }
    return tok;
}

static PyObject *pcc_capi_contextvar_get_method(PyObject *self, PyObject *args) {
    Py_ssize_t nargs = PyTuple_Size(args);
    if (nargs < 0) return NULL;
    if (nargs > 1) {
        PyErr_SetString(PyExc_TypeError, "ContextVar.get expected at most 1 argument");
        return NULL;
    }
    PyObject *default_value = nargs == 1 ? PyTuple_GetItem(args, 0) : NULL;
    PyObject *value = NULL;
    if (PyContextVar_Get(self, default_value, &value) != 0) return NULL;
    if (value == NULL) {
        PyErr_SetString(PyExc_LookupError, "ContextVar has no value");
        return NULL;
    }
    return value;
}

static PyObject *pcc_capi_contextvar_set_method(PyObject *self, PyObject *value) {
    return PyContextVar_Set(self, value);
}

static PyObject *pcc_capi_contextvar_reset_method(PyObject *self, PyObject *token) {
    if (token == NULL || PyTuple_Size(token) != 3) {
        PyErr_SetString(PyExc_TypeError, "ContextVar.reset expected a token");
        return NULL;
    }
    PyObject *token_var = PyTuple_GetItem(token, 0);
    PyObject *had_value = PyTuple_GetItem(token, 1);
    PyObject *previous = PyTuple_GetItem(token, 2);
    if (token_var != self || had_value == NULL || previous == NULL) {
        PyErr_SetString(PyExc_ValueError, "Token was created by a different ContextVar");
        return NULL;
    }
    pcc_capi_contextvar *cv = (pcc_capi_contextvar *)self;
    if (PyObject_IsTrue(had_value)) {
        pcc_gc_store_ptr(self, &cv->value, previous);
    } else {
        pcc_gc_store_ptr(self, &cv->value, NULL);
    }
    py_incref(py_None);
    return py_None;
}

/* --- batch 15: sys.flags for numpy module init. numpy npy_static_data init
 * reads sys.flags.optimize at IMPORT (a NULL return fails init with "cannot get
 * sys.flags"). pcc's no-libpython runtime has no sys object, so build a real
 * namespace (a PyClassObject supports get/setattr) carrying the flags numpy
 * reads. optimize=0 is accurate for pcc's no-`-O` compile. Other sys.* names
 * return NULL until a real consumer needs them. Returns a BORROWED reference
 * (CPython contract); the singleton is GC-pinned for the process. */
PyObject *PySys_GetObject(const char *name) {
    if (name == NULL) return NULL;
    if (strcmp(name, "flags") == 0) {
        static PyObject *flags = NULL;
        if (flags == NULL) {
            PyClassObject *cls = py_class_new("sys.flags", NULL, 0, NULL, 0);
            if (cls == NULL) return NULL;
            pcc_gc_pin((PyObject *)cls);
            PyObject *zero = PyLong_FromLong(0);
            if (zero != NULL) {
                PyObject_SetAttrString((PyObject *)cls, "optimize", zero);
                py_decref(zero);  /* py_class_setattr -> py_dict_set incref's */
            }
            flags = (PyObject *)cls;
        }
        return flags;
    }
    return NULL;
}

/* --- batch 16: object __dict__ getset. numpy installs this as a `__dict__`
 * getset function pointer (arrayfunction_override.c:752); it is called only when
 * Python accesses `obj.__dict__`. Route to the runtime's attribute machinery,
 * which returns the object's own dict (new ref) or raises AttributeError when
 * the object has none — exactly CPython's PyObject_GenericGetDict contract. */
PyObject *PyObject_GenericGetDict(PyObject *o, void *context) {
    (void)context;
    if (o == NULL) {
        PyErr_SetString(PyExc_TypeError, "NULL object has no __dict__");
        return NULL;
    }
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL) return py_obj_getattr(o, "__dict__");
    PyObject **slot = pcc_capi_object_dict_slot(o, type);
    if (slot == NULL) {
        PyErr_SetString(PyExc_AttributeError, "object has no __dict__");
        return NULL;
    }
    PyObject *dict = pcc_gc_load_ptr(o, slot);
    if (dict != NULL) {
        py_incref(dict);
        return dict;
    }
    dict = py_dict_new();
    if (dict == NULL) return NULL;
    pcc_gc_store_ptr(o, slot, dict);
    return dict;
}

/* --- batch 17: PySeqIter_New (real sequence iterator) + PyMethod_New (bound
 * method via the runtime's instance-method machinery). */

/* A sequence iterator object: holds (seq, index) and yields seq[index++] via
 * PySequence_GetItem until it runs off the end. C-API iteration through
 * PyIter_Next (wired above) is genuine; pcc's Python for-loop dispatch
 * (py_obj_next) only knows PY_TYPE_ITER/GEN, so iterating this from Python is
 * array-runtime-era. numpy returns it from array tp_iter (arrayobject.c:1222). */
typedef struct {
    PyObjectHeader header;
    void *ob_type;       /* set by PyType_GenericAlloc at offset sizeof(header) */
    PyObject *seq;       /* the underlying sequence (owned) */
    Py_ssize_t index;    /* next index to fetch */
} pcc_capi_seqiter;

static int pcc_capi_seqiter_traverse(
    PyObject *obj,
    visitproc visit,
    void *arg
) {
    pcc_capi_seqiter *it = (pcc_capi_seqiter *)obj;
    return pcc_capi_visit_slot(&it->seq, visit, arg);
}

static void pcc_capi_seqiter_dealloc(PyObject *obj) {
    pcc_capi_seqiter *it = (pcc_capi_seqiter *)obj;
    PyObject *seq = pcc_gc_load_ptr(obj, &it->seq);
    it->seq = NULL;
    py_decref(seq);
}

static PyTypeObject pcc_capi_seqiter_type = {
    .ob_base = {1, 0, 0},
    .tp_name = "iterator",
    .tp_flags = (
        Py_TPFLAGS_READY
        | Py_TPFLAGS_HAVE_GC
        | PCC_TPFLAGS_MANAGED_DEALLOC
    ),
    .tp_basicsize = (Py_ssize_t)sizeof(pcc_capi_seqiter),
    .tp_dealloc = pcc_capi_seqiter_dealloc,
    .tp_traverse = pcc_capi_seqiter_traverse,
};

static int pcc_capi_is_seqiter(PyObject *obj) {
    return obj != NULL && !PY_IS_TAGGED_INT(obj)
        && py_type_of(obj) == pcc_capi_cext_tag_for(&pcc_capi_seqiter_type);
}

static PyObject *pcc_capi_seqiter_next(PyObject *obj) {
    pcc_capi_seqiter *it = (pcc_capi_seqiter *)obj;
    PyObject *seq = pcc_gc_load_ptr(obj, &it->seq);
    PyObject *item = PySequence_GetItem(seq, it->index);
    if (item == NULL) {
        /* IndexError (or any error) ends iteration: clear and stop. */
        if (py_err_occurred()) PyErr_Clear();
        return NULL;
    }
    it->index++;
    return item;
}

PyObject *PySeqIter_New(PyObject *seq) {
    if (seq == NULL) {
        PyErr_SetString(PyExc_TypeError, "iteration over a non-sequence");
        return NULL;
    }
    PyObject *obj = PyType_GenericAlloc(&pcc_capi_seqiter_type, 0);
    if (obj == NULL) return NULL;
    pcc_capi_seqiter *it = (pcc_capi_seqiter *)obj;
    pcc_gc_store_ptr(obj, &it->seq, seq);
                            /* index left 0 (calloc'd) */
    return obj;
}

/* PyMethod_New(func, self) -> a bound method that calls func(self, *args), built
 * with the runtime's standard instance-method machinery (the same path every
 * `instance.method` uses), so the result is a real callable PY_TYPE_FUNC. numpy
 * returns it from a descriptor __get__ (arrayfunction_override.c:724). */
PyObject *PyMethod_New(PyObject *func, PyObject *self) {
    if (func == NULL || self == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyMethod_New requires func and self");
        return NULL;
    }
    return py_instance_bind_method(func, self, NULL);
}

/* --- batch 18: full-module host symbols routed to existing pcc primitives.
 * (These are referenced by the broader _core once all 95 compilable files are
 * considered; they are declared in Python.h but had no runtime impl.) */

PyObject *PyDict_Copy(PyObject *mp) {
    if (mp == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyDict_Copy requires a dict");
        return NULL;
    }
    PyObject *copy = PyDict_New();
    if (copy == NULL) return NULL;
    py_dict_update(copy, mp);   /* shallow copy = update an empty dict */
    return copy;
}

int PyDict_Merge(PyObject *a, PyObject *b, int override) {
    if (a == NULL || b == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyDict_Merge requires two dicts");
        return -1;
    }
    if (override) {
        py_dict_update(a, b);   /* b's values win */
        return 0;
    }
    /* override == 0: keep a's existing keys, only add b's missing keys. */
    Py_ssize_t pos = 0;
    PyObject *k = NULL, *v = NULL;
    while (PyDict_Next(b, &pos, &k, &v)) {
        if (PyDict_GetItem(a, k) == NULL) {
            if (PyDict_SetItem(a, k, v) != 0) return -1;
        }
    }
    return 0;
}

static PyObject *pcc_capi_member_get(
    PyObject *o,
    PccCapiMemberDef *member
) {
    char *address = (char *)o + member->offset;
    switch (member->type) {
        case PCC_CAPI_T_SHORT:
            return PyLong_FromLong((long)*(short *)address);
        case PCC_CAPI_T_INT:
            return PyLong_FromLong((long)*(int *)address);
        case PCC_CAPI_T_LONG:
            return PyLong_FromLong(*(long *)address);
        case PCC_CAPI_T_FLOAT:
            return PyFloat_FromDouble((double)*(float *)address);
        case PCC_CAPI_T_DOUBLE:
            return PyFloat_FromDouble(*(double *)address);
        case PCC_CAPI_T_STRING: {
            const char *value = *(const char **)address;
            if (value == NULL) {
                PyErr_SetString(PyExc_AttributeError, member->name);
                return NULL;
            }
            return PyUnicode_FromString(value);
        }
        case PCC_CAPI_T_OBJECT:
        case PCC_CAPI_T_OBJECT_EX: {
            PyObject *value = pcc_gc_load_ptr(o, (PyObject **)address);
            if (value == NULL && member->type == PCC_CAPI_T_OBJECT_EX) {
                PyErr_SetString(PyExc_AttributeError, member->name);
                return NULL;
            }
            if (value == NULL) value = py_None;
            py_incref(value);
            return value;
        }
        case PCC_CAPI_T_CHAR:
            return PyUnicode_FromStringAndSize(address, 1);
        case PCC_CAPI_T_BYTE:
            return PyLong_FromLong((long)*(signed char *)address);
        case PCC_CAPI_T_UBYTE:
            return PyLong_FromUnsignedLong((unsigned long)*(unsigned char *)address);
        case PCC_CAPI_T_USHORT:
            return PyLong_FromUnsignedLong((unsigned long)*(unsigned short *)address);
        case PCC_CAPI_T_UINT:
            return PyLong_FromUnsignedLong((unsigned long)*(unsigned int *)address);
        case PCC_CAPI_T_ULONG:
            return PyLong_FromUnsignedLong(*(unsigned long *)address);
        case PCC_CAPI_T_STRING_INPLACE:
            return PyUnicode_FromString(address);
        case PCC_CAPI_T_BOOL:
            return PyBool_FromLong(*(char *)address != 0);
        case PCC_CAPI_T_LONGLONG:
            return PyLong_FromLongLong(*(long long *)address);
        case PCC_CAPI_T_ULONGLONG:
            return PyLong_FromUnsignedLongLong(*(unsigned long long *)address);
        case PCC_CAPI_T_PYSSIZET:
            return PyLong_FromSsize_t(*(Py_ssize_t *)address);
        case PCC_CAPI_T_NONE:
            py_incref(py_None);
            return py_None;
        default:
            PyErr_SetString(PyExc_TypeError, "unsupported C member type");
            return NULL;
    }
}

PyObject *PyObject_GenericGetAttr(PyObject *o, PyObject *name) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (name == NULL || PY_IS_TAGGED_INT(name)
        || py_type_of(name) != PY_TYPE_STR) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a string");
        return NULL;
    }
    if (type == NULL) return PyObject_GetAttr(o, name);
    const char *attr = py_str_utf8(name);
    for (PyTypeObject *current = type; current != NULL;
         current = (PyTypeObject *)current->tp_base) {
        PccCapiGetSetDef *getset = (PccCapiGetSetDef *)current->tp_getset;
        for (; getset != NULL && getset->name != NULL; getset++) {
            if (strcmp(attr, getset->name) == 0 && getset->get != NULL) {
                return getset->get(o, getset->closure);
            }
        }
        PccCapiMemberDef *member = (PccCapiMemberDef *)current->tp_members;
        for (; member != NULL && member->name != NULL; member++) {
            if (strcmp(attr, member->name) != 0) continue;
            return pcc_capi_member_get(o, member);
        }
    }
    PyObject **dict_slot = pcc_capi_object_dict_slot(o, type);
    if (dict_slot != NULL) {
        PyObject *dict = pcc_gc_load_ptr(o, dict_slot);
        if (dict != NULL) {
            PyObject *value = py_dict_get(dict, name);
            if (value != NULL) return value;
        }
    }
    for (PyTypeObject *current = type; current != NULL;
         current = (PyTypeObject *)current->tp_base) {
        PyMethodDef *method = (PyMethodDef *)current->tp_methods;
        for (; method != NULL && method->ml_name != NULL; method++) {
            if (strcmp(attr, method->ml_name) == 0) {
                return pcc_capi_method_func_new(o, method);
            }
        }
    }
    PyErr_SetString(PyExc_AttributeError, attr);
    return NULL;
}

int PyObject_GenericSetAttr(PyObject *o, PyObject *name, PyObject *value) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (name == NULL || PY_IS_TAGGED_INT(name)
        || py_type_of(name) != PY_TYPE_STR) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a string");
        return -1;
    }
    if (type == NULL) return PyObject_SetAttr(o, name, value);
    const char *attr = py_str_utf8(name);
    for (PyTypeObject *current = type; current != NULL;
         current = (PyTypeObject *)current->tp_base) {
        PccCapiGetSetDef *getset = (PccCapiGetSetDef *)current->tp_getset;
        for (; getset != NULL && getset->name != NULL; getset++) {
            if (strcmp(attr, getset->name) != 0) continue;
            if (getset->set == NULL) {
                PyErr_SetString(PyExc_AttributeError, attr);
                return -1;
            }
            return getset->set(o, value, getset->closure);
        }
    }
    PyObject **slot = pcc_capi_object_dict_slot(o, type);
    if (slot == NULL || value == NULL) {
        PyErr_SetString(PyExc_AttributeError, attr);
        return -1;
    }
    PyObject *dict = pcc_gc_load_ptr(o, slot);
    if (dict == NULL) {
        dict = py_dict_new();
        if (dict == NULL) return -1;
        pcc_gc_store_ptr(o, slot, dict);
        py_decref(dict);
        dict = pcc_gc_load_ptr(o, slot);
    }
    py_dict_set(dict, name, value);
    return 0;
}

PyObject *pcc_capi_cext_object_getattr(PyObject *o, const char *name) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL || name == NULL) return NULL;
    PyObject *name_obj = py_str_new(name, (int64_t)strlen(name));
    if (name_obj == NULL) return NULL;
    PyObject *result;
    if (type->tp_getattro != NULL) {
        PyObject *(*getattro)(PyObject *, PyObject *) = (
            PyObject *(*)(PyObject *, PyObject *)
        )type->tp_getattro;
        result = getattro(o, name_obj);
    } else {
        result = PyObject_GenericGetAttr(o, name_obj);
    }
    py_decref(name_obj);
    return result;
}

int64_t pcc_capi_cext_object_setattr(
    PyObject *o,
    const char *name,
    PyObject *value
) {
    PyTypeObject *type = pcc_capi_cext_type_for_object(o);
    if (type == NULL || name == NULL) return -1;
    PyObject *name_obj = py_str_new(name, (int64_t)strlen(name));
    if (name_obj == NULL) return -1;
    int result;
    if (type->tp_setattro != NULL) {
        int (*setattro)(PyObject *, PyObject *, PyObject *) = (
            int (*)(PyObject *, PyObject *, PyObject *)
        )type->tp_setattro;
        result = setattro(o, name_obj, value);
    } else {
        result = PyObject_GenericSetAttr(o, name_obj, value);
    }
    py_decref(name_obj);
    return result;
}

PyObject *PyUnicode_Format(PyObject *format, PyObject *args) {
    if (format == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyUnicode_Format requires a format");
        return NULL;
    }
    return py_str_mod(format, args);   /* the `fmt % args` runtime path */
}

/* --- batch 19: the last 5 full-module host symbols. */

/* Stamp refcount + type onto an already-allocated object (CPython sets ob_refcnt
 * + ob_type; pcc carries refcount + type_tag + the ob_type slot). */
PyObject *PyObject_Init(PyObject *op, PyTypeObject *type) {
    if (op == NULL) return op;
    PyObjectHeader *h = (PyObjectHeader *)op;
    h->refcount = 1;
    h->type_tag = pcc_capi_cext_tag_for(type);
    *(PyTypeObject **)((char *)op + sizeof(PyObjectHeader)) = type;
    return op;
}

/* pcc has no separate read-only proxy type; return the mapping itself as a
 * readable view (read access is correct; read-only ENFORCEMENT is a follow-on).
 * numpy uses this to expose a type dict for reading. */
PyObject *PyDictProxy_New(PyObject *mapping) {
    if (mapping == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyDictProxy_New requires a mapping");
        return NULL;
    }
    py_incref(mapping);
    return mapping;
}

/* Unpack a tuple's items into PyObject* slots. Like CPython, the stored refs are
 * BORROWED (valid while `args` lives); optional slots beyond the arg count are
 * left untouched. */
int PyArg_UnpackTuple(PyObject *args, const char *name, Py_ssize_t min,
                      Py_ssize_t max, ...) {
    (void)name;
    if (args == NULL || PY_IS_TAGGED_INT(args)
        || py_type_of(args) != PY_TYPE_TUPLE) {
        PyErr_SetString(PyExc_TypeError, "PyArg_UnpackTuple requires a tuple");
        return 0;
    }
    Py_ssize_t n = (Py_ssize_t)py_tuple_len(args);
    if (n < min || n > max) {
        PyErr_SetString(PyExc_TypeError,
                        "PyArg_UnpackTuple: wrong number of arguments");
        return 0;
    }
    va_list ap;
    va_start(ap, max);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject **dest = va_arg(ap, PyObject **);
        PyObject *item = py_tuple_get(args, (int64_t)i);  /* owned */
        *dest = item;
        if (item != NULL) py_decref(item);  /* hand back a borrowed ref */
    }
    va_end(ap);
    return 1;
}

/* A slice object {start, stop, step}. pcc lowers Python `a[i:j]` directly to
 * py_*_slice without materializing a slice, so a slice object only exists when
 * PySlice_New (here) makes one; PySlice_GetIndicesEx below reads it back. */
typedef struct {
    PyObjectHeader header;
    void *ob_type;
    PyObject *start, *stop, *step;
} pcc_capi_slice;

static int pcc_capi_slice_traverse(
    PyObject *obj,
    visitproc visit,
    void *arg
) {
    pcc_capi_slice *slice = (pcc_capi_slice *)obj;
    int result = pcc_capi_visit_slot(&slice->start, visit, arg);
    if (result != 0) return result;
    result = pcc_capi_visit_slot(&slice->stop, visit, arg);
    if (result != 0) return result;
    return pcc_capi_visit_slot(&slice->step, visit, arg);
}

static void pcc_capi_slice_dealloc(PyObject *obj) {
    pcc_capi_slice *slice = (pcc_capi_slice *)obj;
    PyObject *start = pcc_gc_load_ptr(obj, &slice->start);
    PyObject *stop = pcc_gc_load_ptr(obj, &slice->stop);
    PyObject *step = pcc_gc_load_ptr(obj, &slice->step);
    slice->start = NULL;
    slice->stop = NULL;
    slice->step = NULL;
    py_decref(start);
    py_decref(stop);
    py_decref(step);
}

static PyTypeObject pcc_capi_slice_obj_type = {
    .ob_base = {1, 0, 0},
    .tp_name = "slice",
    .tp_flags = (
        Py_TPFLAGS_READY
        | Py_TPFLAGS_HAVE_GC
        | PCC_TPFLAGS_MANAGED_DEALLOC
    ),
    .tp_basicsize = (Py_ssize_t)sizeof(pcc_capi_slice),
    .tp_dealloc = pcc_capi_slice_dealloc,
    .tp_traverse = pcc_capi_slice_traverse,
};

PyObject *PySlice_New(PyObject *start, PyObject *stop, PyObject *step) {
    PyObject *obj = PyType_GenericAlloc(&pcc_capi_slice_obj_type, 0);
    if (obj == NULL) return NULL;
    pcc_capi_slice *s = (pcc_capi_slice *)obj;
    if (start == NULL) start = py_None;
    if (stop == NULL) stop = py_None;
    if (step == NULL) step = py_None;
    pcc_gc_store_ptr(obj, &s->start, start);
    pcc_gc_store_ptr(obj, &s->stop, stop);
    pcc_gc_store_ptr(obj, &s->step, step);
    return obj;
}

/* CPython's slice-index algorithm: clamp start/stop/step (None-aware, negative
 * indices relative to length) and compute the resulting slice length. */
int PySlice_GetIndicesEx(PyObject *r, Py_ssize_t length, Py_ssize_t *start,
                         Py_ssize_t *stop, Py_ssize_t *step,
                         Py_ssize_t *slicelen) {
    pcc_capi_slice *s = (pcc_capi_slice *)r;
    PyObject *start_obj = NULL;
    PyObject *stop_obj = NULL;
    PyObject *step_obj = NULL;
    int native_slice = py_obj_is_slice(r) != 0;
    if (native_slice) {
        start_obj = py_obj_getattr(r, "start");
        stop_obj = py_obj_getattr(r, "stop");
        step_obj = py_obj_getattr(r, "step");
        if (start_obj == NULL || stop_obj == NULL || step_obj == NULL) {
            if (start_obj != NULL) py_decref(start_obj);
            if (stop_obj != NULL) py_decref(stop_obj);
            if (step_obj != NULL) py_decref(step_obj);
            return -1;
        }
    } else {
        start_obj = pcc_gc_load_ptr(r, &s->start);
        stop_obj = pcc_gc_load_ptr(r, &s->stop);
        step_obj = pcc_gc_load_ptr(r, &s->step);
    }
    Py_ssize_t stp;
    if (step_obj == py_None) {
        stp = 1;
    } else {
        stp = (Py_ssize_t)PyLong_AsLong(step_obj);
        if (stp == 0) {
            PyErr_SetString(PyExc_ValueError, "slice step cannot be zero");
            if (native_slice) {
                py_decref(start_obj); py_decref(stop_obj); py_decref(step_obj);
            }
            return -1;
        }
    }
    int neg = stp < 0;
    Py_ssize_t lower = neg ? -1 : 0;
    Py_ssize_t upper = neg ? length - 1 : length;
    Py_ssize_t st, sp;
    if (start_obj == py_None) {
        st = neg ? upper : lower;
    } else {
        st = (Py_ssize_t)PyLong_AsLong(start_obj);
        if (st < 0) st += length;
        if (st < lower) st = lower;
        if (st > upper) st = upper;
    }
    if (stop_obj == py_None) {
        sp = neg ? lower : upper;
    } else {
        sp = (Py_ssize_t)PyLong_AsLong(stop_obj);
        if (sp < 0) sp += length;
        if (sp < lower) sp = lower;
        if (sp > upper) sp = upper;
    }
    *start = st;
    *stop = sp;
    *step = stp;
    if (neg) {
        *slicelen = (sp < st) ? (st - sp - 1) / (-stp) + 1 : 0;
    } else {
        *slicelen = (st < sp) ? (sp - st - 1) / stp + 1 : 0;
    }
    if (native_slice) {
        py_decref(start_obj); py_decref(stop_obj); py_decref(step_obj);
    }
    return 0;
}

/* --- batch 20: unicode-kind for numpy textreading. pcc strings are UTF-8
 * (1-byte storage), so the PEP-393 "kind" is always 1; the matching data buffer
 * (PyUnicode_1BYTE_DATA, defined as py_str_utf8 in Python.h) is the UTF-8 bytes. */
int PyUnicode_KIND(PyObject *op) {
    (void)op;
    return 1;
}

/* --- batch 21: divmod + double hash for numpy scalar types (the last 2 host
 * symbols referenced once all 98 _core files compile). */

PyObject *PyNumber_Divmod(PyObject *o1, PyObject *o2) {
    PyObject *q = PyNumber_FloorDivide(o1, o2);
    if (q == NULL) return NULL;
    PyObject *r = PyNumber_Remainder(o1, o2);
    if (r == NULL) {
        py_decref(q);
        return NULL;
    }
    PyObject *t = PyTuple_Pack(2, q, r);   /* packs new refs */
    py_decref(q);
    py_decref(r);
    return t;
}

/* CPython's float hash (Objects/object.c): a value congruent modulo 2^61-1 so
 * equal float/int values hash equally. Implemented directly so numpy scalar
 * hashes match CPython. */
Py_hash_t _Py_HashDouble(PyObject *inst, double v) {
    const int bits = 61;
    const uint64_t modulus = (((uint64_t)1 << bits) - 1);
    int e, sign;
    double m;
    uint64_t x, y;
    if (!isfinite(v)) {
        if (isinf(v)) return v > 0 ? 314159 : -314159;
        return inst ? (Py_hash_t)py_obj_hash(inst) : 0;   /* nan */
    }
    m = frexp(v, &e);
    sign = 1;
    if (m < 0) { sign = -1; m = -m; }
    x = 0;
    while (m) {
        x = ((x << 28) & modulus) | x >> (bits - 28);
        m *= 268435456.0;   /* 2**28 */
        e -= 28;
        y = (uint64_t)m;
        m -= y;
        x += y;
        if (x >= modulus) x -= modulus;
    }
    e = e % bits;
    if (e < 0) e += bits;
    x = ((x << e) & modulus) | x >> (bits - e);
    x = x * (uint64_t)sign;
    if (x == (uint64_t)-1) x = (uint64_t)-2;
    return (Py_hash_t)x;
}

/* --- batch 22: link symbols introduced by the now-compiling numpy _core C++
 * (umath) layer, routed to existing primitives. */

PyObject *PyLong_FromUnicodeObject(PyObject *u, int base) {
    if (u == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyLong_FromUnicodeObject requires a str");
        return NULL;
    }
    const char *s = PyUnicode_AsUTF8(u);
    if (s == NULL) return NULL;
    char *end = NULL;
    long long v = strtoll(s, &end, base == 0 ? 10 : base);
    if (end == s) {
        PyErr_SetString(PyExc_ValueError, "invalid literal for int()");
        return NULL;
    }
    return PyLong_FromLongLong(v);
}

PyObject *PyFloat_FromString(PyObject *str) {
    if (str == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyFloat_FromString requires a str");
        return NULL;
    }
    const char *s = PyUnicode_AsUTF8(str);
    if (s == NULL) return NULL;
    char *end = NULL;
    double v = PyOS_string_to_double(s, &end, NULL);   /* locale-independent */
    if (end == s) {
        PyErr_SetString(PyExc_ValueError, "could not convert string to float");
        return NULL;
    }
    return PyFloat_FromDouble(v);
}

/* pcc's PyLong_AsLongLong already handles the value range; full overflow
 * detection would need bigint comparison, so report no overflow (sufficient for
 * numpy's in-range scalar parsing). */
long long PyLong_AsLongLongAndOverflow(PyObject *obj, int *overflow) {
    if (overflow != NULL) *overflow = 0;
    return PyLong_AsLongLong(obj);
}

/* CPython's PySlice_AdjustIndices: clamp already-resolved start/stop to bounds
 * given step (!= 0) and return the resulting slice length. */
Py_ssize_t PySlice_AdjustIndices(Py_ssize_t length, Py_ssize_t *start,
                                 Py_ssize_t *stop, Py_ssize_t step) {
    if (*start < 0) {
        *start += length;
        if (*start < 0) *start = (step < 0) ? -1 : 0;
    } else if (*start >= length) {
        *start = (step < 0) ? length - 1 : length;
    }
    if (*stop < 0) {
        *stop += length;
        if (*stop < 0) *stop = (step < 0) ? -1 : 0;
    } else if (*stop >= length) {
        *stop = (step < 0) ? length - 1 : length;
    }
    if (step < 0) {
        if (*stop < *start) return (*start - *stop - 1) / (-step) + 1;
    } else {
        if (*start < *stop) return (*stop - *start - 1) / step + 1;
    }
    return 0;
}

/* --- batch 23: Py_SET_TYPE backing. numpy sets an object's type (array/scalar
 * types registered via PyType_Ready). Stamp the type_tag + the ob_type slot
 * (mirrors PyObject_Init's type half). This was the SOLE undefined symbol when
 * link-testing numpy's full _core under pcc-native. */
void pcc_capi_set_type(PyObject *o, PyTypeObject *t) {
    if (o == NULL) return;
    PyObjectHeader *h = (PyObjectHeader *)o;
    h->type_tag = pcc_capi_cext_tag_for(t);
    *(PyTypeObject **)((char *)o + sizeof(PyObjectHeader)) = t;
}
