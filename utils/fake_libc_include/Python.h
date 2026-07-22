#ifndef PCC_FAKE_PYTHON_H
#define PCC_FAKE_PYTHON_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>     /* numpy npy_math uses log2/atan2/hypot/... */
#include <complex.h>  /* numpy uses C99 creal/cimag/... */
#include <assert.h>   /* numpy npy_atomic.h / many sources use assert() */
#include <stdlib.h>   /* numpy uses getenv/malloc/... (CPython Python.h pulls this) */
#include <string.h>
#include <errno.h>    /* numpy uses ERANGE etc. */
#include <limits.h>
#include <ctype.h>    /* numpy uses isspace/isdigit/tolower (CPython pulls this) */

/* CPython gives every C-API symbol C linkage; numpy's C++ TUs (umath dispatch)
 * include this header and must see/link the same unmangled names, and numpy's
 * vendored pythoncapi_compat re-declares some symbols with C linkage. Wrap the
 * runtime header + all declarations below in extern "C" for C++. (System headers
 * above are already included, so py_runtime.h's guarded re-includes are no-ops
 * and are not parsed inside the block.) */
#ifdef __cplusplus
extern "C" {
#endif
#include "py_runtime.h"

#ifndef PY_SSIZE_T_CLEAN
#define PY_SSIZE_T_CLEAN 1
#endif

#ifndef PyDoc_STRVAR
#define PyDoc_STRVAR(name, str) static const char name[] = str
#endif

/* CPython's master include guard. numpy headers gate on it
 * (`#ifndef Py_PYTHON_H #error "Python headers needed..."`). */
#ifndef Py_PYTHON_H
#define Py_PYTHON_H
#endif

/* Complete the opaque `struct PyObject` from py_runtime.h with pcc's object
 * header so `sizeof(PyObject)` (numpy uses it for tp_basicsize) is concrete and
 * equals pcc's real 16-byte header. Only affects extension compiles (Python.h
 * consumers); the runtime keeps its own definition in py_obj.c. Field access
 * still goes through the Py_REFCNT/Py_TYPE shims (pcc names differ from CPython
 * ob_refcnt/ob_type). */
struct PyObject {
    PyObjectHeader _pcc_ob_base;
    struct _typeobject *ob_type;  /* CPython-compat slot; set by PyType_GenericAlloc */
};

/* numpy (npy_common.h) hard-#errors without this; pcc's unicode surface is
 * UCS4-backed, so unicode is "enabled" for C-extension build purposes. */
#ifndef Py_USING_UNICODE
#define Py_USING_UNICODE 1
#endif

/* Report a recent CPython version so version-gated compat shims (e.g. numpy's
 * bundled pythoncapi-compat/pythoncapi_compat.h) SKIP their static-inline
 * back-fills of functions pcc already provides — otherwise they static-redefine
 * pcc's non-static decls and poke fields of types pcc keeps opaque. 3.14.0 final
 * => PY_VERSION_HEX 0x030e00f0, above every back-fill gate. */
#ifndef PY_MAJOR_VERSION
#define PY_MAJOR_VERSION 3
#define PY_MINOR_VERSION 14
#define PY_MICRO_VERSION 0
#define PY_RELEASE_LEVEL 0xF
#define PY_RELEASE_SERIAL 0
#endif
#ifndef PY_VERSION_HEX
#define PY_VERSION_HEX ((PY_MAJOR_VERSION << 24) | (PY_MINOR_VERSION << 16) | \
                        (PY_MICRO_VERSION << 8) | (PY_RELEASE_LEVEL << 4) | \
                        PY_RELEASE_SERIAL)
#endif

/* CPython export-decoration macros. numpy and its bundled pythoncapi-compat
 * shim wrap every prototype in PyAPI_FUNC()/PyAPI_DATA(); when these are not
 * defined as macros the prototype `PyAPI_FUNC(void) _Py_SetImmortal(...)` parses
 * as a call to a function named PyAPI_FUNC, so the real symbol never gets
 * declared. Plain pass-through is the no-DLL-export host form. */
#ifndef PyAPI_FUNC
#define PyAPI_FUNC(RTYPE) RTYPE
#endif

/* CPython's narrowing cast (release form): cast VALUE to NARROW; WIDE is only
 * used by the debug-mode range assert. numpy's string_fastsearch.h uses it. */
#ifndef Py_SAFE_DOWNCAST
#define Py_SAFE_DOWNCAST(VALUE, WIDE, NARROW) ((NARROW)(VALUE))
#endif
#ifndef Py_IS_FINITE
#define Py_IS_FINITE(X) isfinite(X)
#endif
#ifndef PyAPI_DATA
#define PyAPI_DATA(RTYPE) extern RTYPE
#endif

/* GCC/clang attribute wrapper. numpy's compat shim trails prototypes with
 * Py_GCC_ATTRIBUTE((format(printf,...))); undefined it leaves stray tokens
 * after the declarator ("expected function body"). clang accepts __attribute__. */
#ifndef Py_GCC_ATTRIBUTE
#if defined(__GNUC__) || defined(__clang__)
#define Py_GCC_ATTRIBUTE(x) __attribute__(x)
#else
#define Py_GCC_ATTRIBUTE(x)
#endif
#endif

typedef long Py_ssize_t;
typedef long Py_hash_t;

typedef intptr_t Py_intptr_t;
typedef uintptr_t Py_uintptr_t;
#ifndef PY_LONG_LONG
#define PY_LONG_LONG long long
#endif
#ifndef PY_LLONG_MAX
#define PY_LLONG_MAX 0x7fffffffffffffffLL
#define PY_LLONG_MIN (-PY_LLONG_MAX - 1)
#define PY_ULLONG_MAX 0xffffffffffffffffULL
#endif
/* numpy's unicode scalar embeds PyUnicodeObject BY VALUE, so it needs a
 * complete type. A minimal header-prefixed struct suffices for the compile;
 * the real unicode layout is a runtime concern. */
typedef struct {
    PyObjectHeader ob_base;
    Py_ssize_t length;
    Py_hash_t hash;
    void *data;
} PyUnicodeObject;
typedef uint8_t Py_UCS1;
typedef uint16_t Py_UCS2;
typedef uint32_t Py_UCS4;
typedef PyObject *(*PyCFunction)(PyObject *, PyObject *);
typedef PyObject *(*PyCFunctionWithKeywords)(PyObject *, PyObject *, PyObject *);
typedef void (*PyCapsule_Destructor)(PyObject *);
typedef int PyGILState_STATE;

typedef struct {
    double real;
    double imag;
} Py_complex;

#ifndef Py_UNUSED
#  if defined(__GNUC__)
#    define Py_UNUSED(name) _unused_ ## name __attribute__((unused))
#  else
#    define Py_UNUSED(name) _unused_ ## name
#  endif
#endif

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

/* CPython object-header prefix macros, mapped to pcc's PyObjectHeader
 * (refcount/type_tag/flags from py_runtime.h). Extensions (e.g. numpy's
 * arrayscalars.h / PyArrayObject) embed the header at the struct start via
 * PyObject_HEAD / PyObject_VAR_HEAD; field access must go through the
 * Py_REFCNT / Py_TYPE / Py_SIZE shim macros (pcc's header field names differ
 * from CPython's ob_refcnt/ob_type). The *_HEAD_INIT type argument is ignored
 * here (static-type linkage to pcc's type system is a runtime/L3.5 concern);
 * the initializer only needs to be a valid PyObjectHeader brace-init for the
 * compile step. */
/* CPython-compat object layout: ob_type follows the pcc header. Every extension
 * object carries an ob_type slot at offset sizeof(PyObjectHeader); GenericAlloc
 * sets it. PyTypeObject embeds PyVarObject ob_base, so tp_* shift one pointer —
 * the PyTypeObject layout mirror in py_capi_shim.c is kept in sync. Validated:
 * import-critical 10/10, hand-written 98%, type bridge 25 passed, self-host
 * bootstrap fixpoint (see investigation). */
#ifndef PyObject_HEAD
#define PyObject_HEAD PyObjectHeader ob_base; struct _typeobject *ob_type;
#endif
typedef struct {
    PyObjectHeader ob_base;
    struct _typeobject *ob_type;
    Py_ssize_t ob_size;
} PyVarObject;
#ifndef PyObject_VAR_HEAD
#define PyObject_VAR_HEAD PyVarObject ob_base;
#endif
#ifndef PyObject_HEAD_INIT
#define PyObject_HEAD_INIT(type) { 1, 0, 0 }, (type),
#endif
#ifndef PyVarObject_HEAD_INIT
#define PyVarObject_HEAD_INIT(type, size) { { 1, 0, 0 }, (type), (size) },
#endif

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

#define PyModuleDef_HEAD_INIT {0, 0, 0, 0}

typedef struct PyModuleDef_Slot {
    int slot;
    void *value;
} PyModuleDef_Slot;

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

/* ---- Type-object subsystem (compile-level surface) -------------------------
 * Standard CPython type-object layout, expressed over pcc's PyObject/
 * PyVarObject. This is the HEADER part of the type-object milestone; the
 * runtime side (PyType_Ready, tp_* slot dispatch, and linkage of these static
 * PyTypeObjects to pcc's type_tag type system) is a separate runtime task.
 * numpy declares PyArray_Type, all scalar types, and dtype types through this
 * surface, so it must parse before numpy's C core can compile. */
typedef struct _typeobject PyTypeObject;

typedef PyObject *(*unaryfunc)(PyObject *);
typedef PyObject *(*binaryfunc)(PyObject *, PyObject *);
typedef PyObject *(*ternaryfunc)(PyObject *, PyObject *, PyObject *);
typedef int (*inquiry)(PyObject *);
typedef Py_ssize_t (*lenfunc)(PyObject *);
typedef PyObject *(*ssizeargfunc)(PyObject *, Py_ssize_t);
typedef PyObject *(*ssizessizeargfunc)(PyObject *, Py_ssize_t, Py_ssize_t);
typedef int (*ssizeobjargproc)(PyObject *, Py_ssize_t, PyObject *);
typedef int (*ssizessizeobjargproc)(PyObject *, Py_ssize_t, Py_ssize_t, PyObject *);
typedef int (*objobjargproc)(PyObject *, PyObject *, PyObject *);
typedef int (*objobjproc)(PyObject *, PyObject *);
typedef int (*visitproc)(PyObject *, void *);
typedef int (*traverseproc)(PyObject *, visitproc, void *);
typedef void (*freefunc)(void *);
typedef void (*destructor)(PyObject *);
typedef PyObject *(*getattrfunc)(PyObject *, char *);
typedef PyObject *(*getattrofunc)(PyObject *, PyObject *);
typedef int (*setattrfunc)(PyObject *, char *, PyObject *);
typedef int (*setattrofunc)(PyObject *, PyObject *, PyObject *);
typedef PyObject *(*reprfunc)(PyObject *);
typedef Py_hash_t (*hashfunc)(PyObject *);
typedef PyObject *(*richcmpfunc)(PyObject *, PyObject *, int);
typedef PyObject *(*getiterfunc)(PyObject *);
typedef PyObject *(*iternextfunc)(PyObject *);
typedef PyObject *(*descrgetfunc)(PyObject *, PyObject *, PyObject *);
typedef int (*descrsetfunc)(PyObject *, PyObject *, PyObject *);
typedef int (*initproc)(PyObject *, PyObject *, PyObject *);
typedef PyObject *(*newfunc)(PyTypeObject *, PyObject *, PyObject *);
typedef PyObject *(*allocfunc)(PyTypeObject *, Py_ssize_t);
typedef PyObject *(*getter)(PyObject *, void *);
typedef int (*setter)(PyObject *, PyObject *, void *);
typedef PyObject *(*vectorcallfunc)(PyObject *, PyObject *const *, size_t, PyObject *);

/* The C-function object (numpy's compiled_base.c casts to it as
 * `PyCFunctionObject *new = (PyCFunctionObject *)obj` and reads new->m_ml->...;
 * leaving it undeclared cascades into "undeclared identifier 'new'"). Layout
 * matches CPython's prefix through m_ml/m_self so field reads resolve. */
typedef struct {
    PyObjectHeader ob_base;
    PyMethodDef *m_ml;
    PyObject *m_self;
    PyObject *m_module;
    PyObject *m_weakreflist;
    vectorcallfunc vectorcall;
} PyCFunctionObject;

typedef int (*getbufferproc)(PyObject *, Py_buffer *, int);
typedef void (*releasebufferproc)(PyObject *, Py_buffer *);
typedef PyObject *(*sendfunc)(PyObject *, PyObject *, PyObject **);

typedef struct PyMemberDef {
    const char *name;
    int type;
    Py_ssize_t offset;
    int flags;
    const char *doc;
} PyMemberDef;

typedef struct PyGetSetDef {
    const char *name;
    getter get;
    setter set;
    const char *doc;
    void *closure;
} PyGetSetDef;

typedef struct PyNumberMethods {
    binaryfunc nb_add, nb_subtract, nb_multiply, nb_remainder, nb_divmod;
    ternaryfunc nb_power;
    unaryfunc nb_negative, nb_positive, nb_absolute;
    inquiry nb_bool;
    unaryfunc nb_invert;
    binaryfunc nb_lshift, nb_rshift, nb_and, nb_xor, nb_or;
    unaryfunc nb_int, nb_reserved, nb_float;
    binaryfunc nb_inplace_add, nb_inplace_subtract, nb_inplace_multiply,
        nb_inplace_remainder;
    ternaryfunc nb_inplace_power;
    binaryfunc nb_inplace_lshift, nb_inplace_rshift, nb_inplace_and,
        nb_inplace_xor, nb_inplace_or;
    binaryfunc nb_floor_divide, nb_true_divide,
        nb_inplace_floor_divide, nb_inplace_true_divide;
    unaryfunc nb_index;
    binaryfunc nb_matrix_multiply, nb_inplace_matrix_multiply;
} PyNumberMethods;

typedef struct PySequenceMethods {
    lenfunc sq_length;
    binaryfunc sq_concat;
    ssizeargfunc sq_repeat, sq_item;
    void *was_sq_slice;
    ssizeobjargproc sq_ass_item;
    void *was_sq_ass_slice;
    objobjproc sq_contains;
    binaryfunc sq_inplace_concat;
    ssizeargfunc sq_inplace_repeat;
} PySequenceMethods;

typedef struct PyMappingMethods {
    lenfunc mp_length;
    binaryfunc mp_subscript;
    objobjargproc mp_ass_subscript;
} PyMappingMethods;

typedef struct PyAsyncMethods {
    unaryfunc am_await, am_aiter, am_anext;
    sendfunc am_send;
} PyAsyncMethods;

typedef struct PyBufferProcs {
    getbufferproc bf_getbuffer;
    releasebufferproc bf_releasebuffer;
} PyBufferProcs;

struct _typeobject {
    PyVarObject ob_base;
    const char *tp_name;
    Py_ssize_t tp_basicsize, tp_itemsize;
    destructor tp_dealloc;
    Py_ssize_t tp_vectorcall_offset;
    getattrfunc tp_getattr;
    setattrfunc tp_setattr;
    PyAsyncMethods *tp_as_async;
    reprfunc tp_repr;
    PyNumberMethods *tp_as_number;
    PySequenceMethods *tp_as_sequence;
    PyMappingMethods *tp_as_mapping;
    hashfunc tp_hash;
    ternaryfunc tp_call;
    reprfunc tp_str;
    getattrofunc tp_getattro;
    setattrofunc tp_setattro;
    PyBufferProcs *tp_as_buffer;
    unsigned long tp_flags;
    const char *tp_doc;
    traverseproc tp_traverse;
    inquiry tp_clear;
    richcmpfunc tp_richcompare;
    Py_ssize_t tp_weaklistoffset;
    getiterfunc tp_iter;
    iternextfunc tp_iternext;
    PyMethodDef *tp_methods;
    PyMemberDef *tp_members;
    PyGetSetDef *tp_getset;
    PyTypeObject *tp_base;
    PyObject *tp_dict;
    descrgetfunc tp_descr_get;
    descrsetfunc tp_descr_set;
    Py_ssize_t tp_dictoffset;
    initproc tp_init;
    allocfunc tp_alloc;
    newfunc tp_new;
    freefunc tp_free;
    inquiry tp_is_gc;
    PyObject *tp_bases;
    PyObject *tp_mro;
    PyObject *tp_cache;
    void *tp_subclasses;
    PyObject *tp_weaklist;
    destructor tp_del;
    unsigned int tp_version_tag;
    destructor tp_finalize;
    vectorcallfunc tp_vectorcall;
};

typedef struct _heaptypeobject {
    PyTypeObject ht_type;
    PyAsyncMethods as_async;
    PyNumberMethods as_number;
    PyMappingMethods as_mapping;
    PySequenceMethods as_sequence;
    PyBufferProcs as_buffer;
    PyObject *ht_name, *ht_slots, *ht_qualname;
    void *ht_cached_keys;
    PyObject *ht_module;
    char *_ht_tpname;
    void *_spec_cache_getitem;
} PyHeapTypeObject;

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

#define METH_VARARGS 0x0001
/* PyInit must stay EXPORTED so the no-libpython loader's dlsym finds it. numpy
 * compiles with -fvisibility=hidden, which would otherwise hide PyInit; mirror
 * CPython by forcing default visibility (+ extern "C" for C++ modules so the
 * symbol name is not mangled). */
#ifndef PyMODINIT_FUNC
#ifdef __cplusplus
#define PyMODINIT_FUNC extern "C" __attribute__((visibility("default"))) PyObject *
#else
#define PyMODINIT_FUNC __attribute__((visibility("default"))) PyObject *
#endif
#endif
#define PYTHON_API_VERSION 1013

#define PyBUF_SIMPLE 0
#define PyBUF_WRITABLE 0x0001
#define PyBUF_FORMAT 0x0004
#define PyBUF_ND 0x0008
#define PyBUF_STRIDES (0x0010 | PyBUF_ND)
#define PyBUF_C_CONTIGUOUS (0x0020 | PyBUF_STRIDES)
#define PyBUF_F_CONTIGUOUS (0x0040 | PyBUF_STRIDES)
#define PyBUF_ANY_CONTIGUOUS (0x0080 | PyBUF_STRIDES)
#define PyBUF_INDIRECT (0x0100 | PyBUF_STRIDES)
#define PyBUF_CONTIG (PyBUF_ND | PyBUF_WRITABLE)
#define PyBUF_CONTIG_RO PyBUF_ND
#define PyBUF_STRIDED (PyBUF_STRIDES | PyBUF_WRITABLE)
#define PyBUF_STRIDED_RO PyBUF_STRIDES
#define PyBUF_RECORDS (PyBUF_STRIDES | PyBUF_WRITABLE | PyBUF_FORMAT)
#define PyBUF_RECORDS_RO (PyBUF_STRIDES | PyBUF_FORMAT)
#define PyBUF_FULL (PyBUF_INDIRECT | PyBUF_WRITABLE | PyBUF_FORMAT)
#define PyBUF_FULL_RO (PyBUF_INDIRECT | PyBUF_FORMAT)
#define PyBUF_READ 0x0100
#define PyBUF_WRITE 0x0200
#define PY_VECTORCALL_ARGUMENTS_OFFSET (((size_t)1) << (8 * sizeof(size_t) - 1))

#define Py_LT 0
#define Py_LE 1
#define Py_EQ 2
#define Py_NE 3
#define Py_GT 4
#define Py_GE 5

/* PyThread lock surface (numpy npy_import.h uses it for one-time init locks). */
typedef void *PyThread_type_lock;
#define WAIT_LOCK 1
#define NOWAIT_LOCK 0
typedef enum PyLockStatus { PY_LOCK_FAILURE = 0, PY_LOCK_ACQUIRED = 1, PY_LOCK_INTR } PyLockStatus;
PyThread_type_lock PyThread_allocate_lock(void);
void PyThread_free_lock(PyThread_type_lock lock);
int PyThread_acquire_lock(PyThread_type_lock lock, int waitflag);
void PyThread_release_lock(PyThread_type_lock lock);

/* Object access macros, mapped to pcc shim functions (pcc's header uses
 * refcount/type_tag, not CPython's ob_refcnt/ob_type; the shim runtime maps
 * type_tag -> PyTypeObject). Declarations suffice for the C-extension compile;
 * the runtime impls are the type-object-model linkage (L3.5). */
PyTypeObject *pcc_capi_type(PyObject *o);
PyTypeObject **pcc_capi_type_addr(PyObject *o);
void pcc_capi_set_type(PyObject *o, PyTypeObject *t);
int pcc_capi_typecheck(PyObject *o, PyTypeObject *t);
Py_ssize_t pcc_capi_size(PyObject *o);
void pcc_capi_set_size(PyObject *o, Py_ssize_t size);
#ifndef Py_TYPE
#define Py_TYPE(o) (*pcc_capi_type_addr((PyObject *)(o)))
#endif
#ifndef Py_SIZE
#define Py_SIZE(o) pcc_capi_size((PyObject *)(o))
#endif
#ifndef Py_SET_TYPE
#define Py_SET_TYPE(o, t) pcc_capi_set_type((PyObject *)(o), (t))
#endif
#ifndef Py_SET_SIZE
#define Py_SET_SIZE(o, s) pcc_capi_set_size((PyObject *)(o), (s))
#endif
#ifndef Py_IS_TYPE
#define Py_IS_TYPE(o, t) (Py_TYPE(o) == (t))
#endif
#ifndef PyObject_TypeCheck
#define PyObject_TypeCheck(o, t) pcc_capi_typecheck((PyObject *)(o), (t))
#endif

/* type/slice identity checks (numpy uses these widely). */
#ifndef PyType_Check
#define PyType_Check(op) PyObject_TypeCheck((op), &PyType_Type)
#define PyType_CheckExact(op) (Py_TYPE(op) == &PyType_Type)
#endif
#ifndef PySlice_Check
#define PySlice_Check(op) (Py_TYPE(op) == &PySlice_Type)
#endif
#ifndef PyCFunction_Check
#define PyCFunction_Check(op) (Py_TYPE(op) == &PyCFunction_Type)
#define PyCFunction_GET_FUNCTION(op) (((PyCFunctionObject *)(op))->m_ml->ml_meth)
#define PyCFunction_GET_SELF(op) (((PyCFunctionObject *)(op))->m_self)
#define PyCFunction_GET_FLAGS(op) (((PyCFunctionObject *)(op))->m_ml->ml_flags)
#endif

/* Free-threading critical sections: no-ops on the single-interpreter
 * no-libpython path (CPython makes them no-ops without the GIL-free build too). */
#ifndef Py_BEGIN_CRITICAL_SECTION
#define Py_BEGIN_CRITICAL_SECTION(op) {
#define Py_END_CRITICAL_SECTION() }
#define Py_BEGIN_CRITICAL_SECTION2(a, b) {
#define Py_END_CRITICAL_SECTION2() }
#endif

/* Recursion guard: pcc does not track a C recursion depth here, so enter is a
 * no-op success (0) and leave is empty. */
#ifndef Py_EnterRecursiveCall
#define Py_EnterRecursiveCall(where) (0)
#define Py_LeaveRecursiveCall() ((void)0)
#endif

/* Builtin type objects (extern; provided by the pcc C-API runtime). */
extern PyTypeObject PyType_Type, PyBaseObject_Type, PyTuple_Type, PyList_Type,
    PyDict_Type, PyUnicode_Type, PyLong_Type, PyFloat_Type, PyBool_Type,
    PyBytes_Type, PyByteArray_Type, PySet_Type, PyFrozenSet_Type, PySlice_Type,
    PyComplex_Type, PyModule_Type, PyFunction_Type, PyCFunction_Type,
    PyMemberDescr_Type, PyGetSetDescr_Type, PyMethodDescr_Type,
    PyDictProxy_Type, PyMemoryView_Type;

/* --- numpy full-core long-tail C-API decls (B-P0-PKG, 2026-05-29). */
PyObject *_PyDict_GetItem_KnownHash(PyObject *mp, PyObject *key, Py_hash_t hash);
int PyDict_Merge(PyObject *a, PyObject *b, int override);
PyObject *PyDict_Copy(PyObject *mp);
PyObject *PyDictProxy_New(PyObject *mapping);
PyObject *PySlice_New(PyObject *start, PyObject *stop, PyObject *step);
int PySlice_GetIndices(PyObject *r, Py_ssize_t length,
                       Py_ssize_t *start, Py_ssize_t *stop, Py_ssize_t *step);
int PySlice_GetIndicesEx(PyObject *r, Py_ssize_t length, Py_ssize_t *start,
                         Py_ssize_t *stop, Py_ssize_t *step, Py_ssize_t *slicelen);
PyObject *PyUnicode_Format(PyObject *format, PyObject *args);
PyObject *PyUnicode_AsLatin1String(PyObject *unicode);
PyObject *PyContextVar_New(const char *name, PyObject *def);
PyObject *PyModuleDef_Init(PyModuleDef *def);
double PyOS_string_to_double(const char *s, char **endptr, PyObject *overflow_exc);
unsigned long PyType_GetFlags(PyTypeObject *type);
void *PyType_GetSlot(PyTypeObject *type, int slot);
int PyArg_UnpackTuple(PyObject *args, const char *name, Py_ssize_t min,
                       Py_ssize_t max, ...);
PyObject *_PyObject_GC_New(PyTypeObject *type);
void PyObject_GC_Track(void *op);
void PyObject_GC_UnTrack(void *op);
void PyObject_GC_Del(void *op);
#ifndef PyObject_GC_New
#define PyObject_GC_New(type, typeobj) ((type *)_PyObject_GC_New(typeobj))
#endif

/* descriptor / tuple object layouts numpy reads (compile-surface; pcc's runtime
 * objects differ — field access at runtime must route through pcc accessors). */
typedef struct {
    PyObjectHeader ob_base;
    PyTypeObject *d_type;
    PyObject *d_name;
    PyObject *d_qualname;
} PyDescrObject;
typedef struct { PyDescrObject d_common; PyMethodDef *d_method; } PyMethodDescrObject;
typedef struct { PyDescrObject d_common; PyMemberDef *d_member; } PyMemberDescrObject;
typedef struct { PyDescrObject d_common; PyGetSetDef *d_getset; } PyGetSetDescrObject;
typedef struct {
    PyObjectHeader ob_base;
    Py_ssize_t ob_size;
    PyObject *ob_item[1];
} PyTupleObject;

/* multi-phase module init slot ids + values (CPython values). */
#ifndef Py_mod_create
#define Py_mod_create 1
#define Py_mod_exec 2
#define Py_mod_multiple_interpreters 3
#define Py_mod_gil 4
#endif
#ifndef Py_MOD_GIL_USED
#define Py_MOD_GIL_USED ((void *)0)
#define Py_MOD_GIL_NOT_USED ((void *)1)
#define Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED ((void *)0)
#define Py_MOD_MULTIPLE_INTERPRETERS_SUPPORTED ((void *)1)
#define Py_MOD_PER_INTERPRETER_GIL_SUPPORTED ((void *)2)
#endif
#ifndef _Py_TPFLAGS_HAVE_VECTORCALL
#define _Py_TPFLAGS_HAVE_VECTORCALL (1UL << 11)
#endif
#ifndef PyObject_INIT
#define PyObject_INIT(op, typeobj) (Py_SET_TYPE((PyObject *)(op), (typeobj)), (op))
#endif

/* C-function object accessors (numpy reads m_ml/m_self off PyCFunctionObject). */
PyCFunction PyCFunction_GetFunction(PyObject *op);
PyObject *PyCFunction_GetSelf(PyObject *op);
int PyCFunction_GetFlags(PyObject *op);
PyObject *PyCFunction_Call(PyObject *callable, PyObject *args, PyObject *kwds);

/* Thread-state surface (numpy uses Py_BEGIN/END_ALLOW_THREADS for slow ops). */
typedef struct PyThreadState PyThreadState;
PyThreadState *PyEval_SaveThread(void);
void PyEval_RestoreThread(PyThreadState *tstate);
#define Py_BEGIN_ALLOW_THREADS { PyThreadState *_save; _save = PyEval_SaveThread();
#define Py_END_ALLOW_THREADS PyEval_RestoreThread(_save); }
#define Py_BLOCK_THREADS PyEval_RestoreThread(_save);
#define Py_UNBLOCK_THREADS _save = PyEval_SaveThread();

PyObject *PyImport_Import(PyObject *name);
/* 'O&' arg-parse converter return code requesting cleanup (CPython value). */
#ifndef Py_CLEANUP_SUPPORTED
#define Py_CLEANUP_SUPPORTED 0x20000
#endif

/* exception/eval helpers used by numpy core */
void PyErr_NormalizeException(PyObject **exc, PyObject **val, PyObject **tb);
PyObject *PyEval_GetBuiltins(void);
void PyException_SetCause(PyObject *self, PyObject *cause);
void PyException_SetContext(PyObject *self, PyObject *context);
int PyException_SetTraceback(PyObject *self, PyObject *tb);
int PyObject_AsFileDescriptor(PyObject *o);
int PyType_Ready(PyTypeObject *type);
void PyType_Modified(PyTypeObject *type);
PyObject *PyType_FromSpec(PyType_Spec *spec);
PyObject *PyType_FromModuleAndSpec(
    PyObject *module,
    PyType_Spec *spec,
    PyObject *bases
);
PyObject *PyType_GetModule(PyTypeObject *type);
PyObject *PyType_GetModuleByDef(PyTypeObject *type, PyModuleDef *def);
PyObject *PyType_GenericNew(PyTypeObject *type, PyObject *args, PyObject *kwds);
PyObject *PyType_GenericAlloc(PyTypeObject *type, Py_ssize_t nitems);

/* tp_flags bits (CPython values) */
#define Py_TPFLAGS_DEFAULT 0UL
#define Py_TPFLAGS_SEQUENCE (1UL << 5)
#define Py_TPFLAGS_HEAPTYPE (1UL << 9)
#define Py_TPFLAGS_BASETYPE (1UL << 10)
#define Py_TPFLAGS_READY (1UL << 12)
#define Py_TPFLAGS_READYING (1UL << 13)
#define Py_TPFLAGS_HAVE_GC (1UL << 14)
#define Py_TPFLAGS_HAVE_VERSION_TAG (1UL << 18)
#define Py_TPFLAGS_IS_ABSTRACT (1UL << 20)
#define Py_TPFLAGS_LONG_SUBCLASS (1UL << 24)
/* pcc-private: tp_dealloc only releases extension payload fields; pcc frees
 * the object body through pcc_gc_free_object_memory after the hook returns. */
#define PCC_TPFLAGS_MANAGED_DEALLOC (1UL << 62)
#define Py_TPFLAGS_LIST_SUBCLASS (1UL << 25)
#define Py_TPFLAGS_TUPLE_SUBCLASS (1UL << 26)
#define Py_TPFLAGS_BYTES_SUBCLASS (1UL << 27)
#define Py_TPFLAGS_UNICODE_SUBCLASS (1UL << 28)
#define Py_TPFLAGS_DICT_SUBCLASS (1UL << 29)
#define Py_TPFLAGS_BASE_EXC_SUBCLASS (1UL << 30)
#define Py_TPFLAGS_TYPE_SUBCLASS (1UL << 31)

/* PyMethodDef ml_flags bits */
#define METH_VARARGS 0x0001
#define METH_KEYWORDS 0x0002
#define METH_NOARGS 0x0004
#define METH_O 0x0008
#define METH_CLASS 0x0010
#define METH_STATIC 0x0020
#define METH_COEXIST 0x0040
#define METH_FASTCALL 0x0080
#define METH_METHOD 0x0200

/* buffer-protocol request flags */
#define PyBUF_SIMPLE 0
#define PyBUF_WRITABLE 0x0001
#define PyBUF_WRITEABLE PyBUF_WRITABLE
#define PyBUF_FORMAT 0x0004
#define PyBUF_ND 0x0008
#define PyBUF_STRIDES (0x0010 | PyBUF_ND)
#define PyBUF_C_CONTIGUOUS (0x0020 | PyBUF_STRIDES)
#define PyBUF_F_CONTIGUOUS (0x0040 | PyBUF_STRIDES)
#define PyBUF_ANY_CONTIGUOUS (0x0080 | PyBUF_STRIDES)
#define PyBUF_INDIRECT (0x0100 | PyBUF_STRIDES)
#define PyBUF_FULL (PyBUF_INDIRECT | PyBUF_WRITABLE | PyBUF_FORMAT)
#define PyBUF_FULL_RO (PyBUF_INDIRECT | PyBUF_FORMAT)

#ifndef PY_SSIZE_T_MAX
#define PY_SSIZE_T_MAX ((Py_ssize_t)(((size_t)-1) >> 1))
#endif

/* additional opaque object types referenced by numpy */
typedef struct PyCodeObject PyCodeObject;
typedef struct _is PyInterpreterState;
PyInterpreterState *PyInterpreterState_Main(void);
/* Complete PyThreadState enough for numpy's subinterpreter guard
 * (`PyThreadState_Get()->interp != PyInterpreterState_Main()`); only the interp
 * field is read. Extension-compile only. */
struct PyThreadState {
    PyInterpreterState *interp;
};
int PyUnstable_Object_IsUniqueReferencedTemporary(PyObject *op);
/* Complete layout so numpy can take sizeof(PyBytesObject) (scalartypes.c). Mirrors
 * CPython's struct: var-head + cached hash + inline char data. */
typedef struct PyBytesObject {
    PyObject_VAR_HEAD
    Py_hash_t ob_shash;
    char ob_sval[1];
} PyBytesObject;

/* Frame / free-threading / immortal surface. With PY_VERSION_HEX reported as
 * 3.14 the bundled pythoncapi-compat shim SKIPS its static-inline back-fills of
 * these (they would otherwise poke CPython-internal frame fields pcc keeps
 * opaque) and expects Python.h to declare them directly. All return opaque
 * pointers or scalars — none expose pcc's object layout. */
#ifndef PyFrameObject_DEFINED
#define PyFrameObject_DEFINED
typedef struct _frame PyFrameObject;
#endif
PyAPI_FUNC(PyCodeObject *) PyFrame_GetCode(PyFrameObject *frame);
PyAPI_FUNC(PyFrameObject *) PyFrame_GetBack(PyFrameObject *frame);
PyAPI_FUNC(PyFrameObject *) PyThreadState_GetFrame(PyThreadState *tstate);
PyAPI_FUNC(int) PyUnstable_Object_IsUniquelyReferenced(PyObject *obj);
PyAPI_FUNC(void) _Py_SetImmortal(PyObject *op);
PyAPI_FUNC(Py_ssize_t) PyVectorcall_NARGS(size_t n);

/* 3.13 free-threading mutex (PyMutex is a 1-byte lock state in CPython). */
typedef struct { unsigned char _bits; } PyMutex;
PyAPI_FUNC(void) PyMutex_Lock(PyMutex *m);
PyAPI_FUNC(void) PyMutex_Unlock(PyMutex *m);

/* Compact unicode header; the compat shim casts to it for ((PyASCIIObject*)op)
 * ->hash. Compile-surface only — pcc strings are not laid out this way, so the
 * runtime hash path must route through pcc's own str object, not this cast. */
typedef struct {
    PyObjectHeader ob_base;
    Py_ssize_t length;
    Py_hash_t hash;
    unsigned int state;
    void *wstr;
} PyASCIIObject;

/* additional C-API functions used by numpy core */
PyObject *_PyObject_New(PyTypeObject *type);
PyVarObject *_PyObject_NewVar(PyTypeObject *type, Py_ssize_t nitems);
#define PyObject_New(type, typeobj) ((type *)_PyObject_New(typeobj))
#define PyObject_NewVar(type, typeobj, n) ((type *)_PyObject_NewVar((typeobj), (n)))
PyObject *PyObject_Init(PyObject *op, PyTypeObject *type);
PyVarObject *PyObject_InitVar(PyVarObject *op, PyTypeObject *type, Py_ssize_t size);
PyObject *PyObject_GenericGetAttr(PyObject *o, PyObject *name);
int PyObject_GenericSetAttr(PyObject *o, PyObject *name, PyObject *value);
PyObject *PyObject_GenericGetDict(PyObject *o, void *context);
int PyObject_IsSubclass(PyObject *derived, PyObject *cls);
int PyObject_IS_GC(PyObject *o);
void PyObject_ClearWeakRefs(PyObject *o);
PyObject *PyImport_AddModule(const char *name);
int PyList_SetSlice(PyObject *list, Py_ssize_t low, Py_ssize_t high, PyObject *items);
PyObject *PyNumber_Divmod(PyObject *o1, PyObject *o2);
PyObject *PyMethod_New(PyObject *func, PyObject *self);
PyObject *PySeqIter_New(PyObject *seq);
int PyContextVar_Get(PyObject *var, PyObject *default_value, PyObject **value);
PyObject *PyContextVar_Set(PyObject *var, PyObject *value);
PyObject *PyBytes_FromFormatV(const char *format, va_list vargs);
long PyOS_strtol(const char *str, char **ptr, int base);
unsigned long PyOS_strtoul(const char *str, char **ptr, int base);
PyObject *PyVectorcall_Call(PyObject *callable, PyObject *tuple, PyObject *dict);
PyObject *Py_GenericAlias(PyObject *origin, PyObject *args);
Py_ssize_t PyDict_Size(PyObject *mp);
#define PyDict_GET_SIZE(d) PyDict_Size(d)
extern PyObject *Py_Ellipsis;

/* Python2-era names some extensions/compat shims still reference (bytes-backed) */
int PyString_Check(PyObject *o);
char *PyString_AsString(PyObject *o);
#define PyString_AS_STRING(o) PyString_AsString(o)
PyObject *PyString_FromString(const char *str);
Py_ssize_t PyString_GET_SIZE(PyObject *op);

/* deeper C-API surface (numpy umath / scalartypes / dtype meta) */
int PyType_IsSubtype(PyTypeObject *a, PyTypeObject *b);
int PyWeakref_Check(PyObject *ob);
PyObject *PyWeakref_GetObject(PyObject *ref);
int _PyFloat_Pack4(double x, unsigned char *p, int le);
int _PyFloat_Pack8(double x, unsigned char *p, int le);
double _PyFloat_Unpack4(const unsigned char *p, int le);
double _PyFloat_Unpack8(const unsigned char *p, int le);
int _PyLong_AsInt(PyObject *obj);
int _PyLong_Sign(PyObject *v);
PyObject **_PyObject_GetDictPtr(PyObject *obj);
PyObject *PySys_GetObject(const char *name);
PyThreadState *PyThreadState_Get(void);
#define PyThreadState_GET() PyThreadState_Get()
int PyTraceMalloc_Track(unsigned int domain, uintptr_t ptr, size_t size);
int PyTraceMalloc_Untrack(unsigned int domain, uintptr_t ptr);
#define PYMEM_DOMAIN_RAW 0
PyObject *PyTuple_GetSlice(PyObject *tuple, Py_ssize_t low, Py_ssize_t high);
int PyUnicode_KIND(PyObject *op);
void Py_FatalError(const char *message);
PyObject *_PyBytes_Join(PyObject *sep, PyObject *x);
int _PyBytes_Resize(PyObject **bytes, Py_ssize_t newsize);
PyObject *PyDict_GetItemWithError(PyObject *dp, PyObject *key);
PyObject *_PyDict_GetItemWithError(PyObject *dp, PyObject *key);
int _PyObject_GC_IS_TRACKED(PyObject *op);
int pcc_capi_visit_slot(PyObject **slot, visitproc visit, void *arg);
#define Py_CHARMASK(c) ((unsigned char)((c) & 0xff))
#define Py_VISIT(op) do { if (op) { int _vret = pcc_capi_visit_slot((PyObject **)&(op), visit, arg); if (_vret) return _vret; } } while (0)
#define Py_TPFLAGS_HAVE_VECTORCALL (1UL << 11)
#define Py_TPFLAGS_METHOD_DESCRIPTOR (1UL << 17)
#define _PyObject_VAR_SIZE(typeobj, nitems) \
    ((size_t)((typeobj)->tp_basicsize + (nitems) * (typeobj)->tp_itemsize))
Py_hash_t _Py_HashDouble(PyObject *inst, double v);
Py_hash_t _Py_HashPointer(const void *p);

extern PyObject *PyExc_ValueError;
extern PyObject *PyExc_TypeError;
extern PyObject *PyExc_RuntimeError;
extern PyObject *PyExc_KeyError;
extern PyObject *PyExc_IndexError;
extern PyObject *PyExc_AttributeError;
extern PyObject *PyExc_MemoryError;
extern PyObject *PyExc_OverflowError;
extern PyObject *PyExc_SystemError;
extern PyObject *PyExc_NameError;
extern PyObject *PyExc_NotImplementedError;
extern PyObject *PyExc_BaseException;
extern PyObject *PyExc_Exception;
extern PyObject *PyExc_ArithmeticError;
extern PyObject *PyExc_LookupError;
extern PyObject *PyExc_OSError;
extern PyObject *PyExc_IOError;
extern PyObject *PyExc_AssertionError;
extern PyObject *PyExc_StopIteration;
extern PyObject *PyExc_StopAsyncIteration;
extern PyObject *PyExc_ZeroDivisionError;
extern PyObject *PyExc_ReferenceError;
extern PyObject *PyExc_BufferError;
extern PyObject *PyExc_ImportError;
extern PyObject *PyExc_ModuleNotFoundError;
extern PyObject *PyExc_ModuleNotFoundError;
extern PyObject *PyExc_ImportWarning;
extern PyObject *PyExc_FloatingPointError;
extern PyObject *PyExc_RecursionError;
extern PyObject *PyExc_UnicodeDecodeError;
extern PyObject *PyExc_Warning;
extern PyObject *PyExc_UserWarning;
extern PyObject *PyExc_RuntimeWarning;
extern PyObject *PyExc_DeprecationWarning;
extern PyObject *PyExc_FutureWarning;
extern PyObject *PyExc_UnicodeEncodeError;
extern PyObject *PyExc_UnicodeError;

/* --- C++ umath layer compat (numpy _core/*.cpp dispatch loops). */
typedef size_t Py_uhash_t;
#ifndef Py_MAX
#define Py_MAX(a, b) ((a) > (b) ? (a) : (b))
#endif
#ifndef Py_MIN
#define Py_MIN(a, b) ((a) < (b) ? (a) : (b))
#endif
#ifndef SIZEOF_VOID_P
#define SIZEOF_VOID_P 8
#endif
#define PyExceptionInstance_Class(x) ((PyObject *)Py_TYPE(x))
PyObject *PyLong_FromUnicodeObject(PyObject *u, int base);
Py_ssize_t PySlice_AdjustIndices(Py_ssize_t length, Py_ssize_t *start, Py_ssize_t *stop, Py_ssize_t step);
PyObject *PyFloat_FromString(PyObject *str);
long long PyLong_AsLongLongAndOverflow(PyObject *obj, int *overflow);

#define Py_True py_True
#define Py_False py_False
#define Py_NotImplemented py_NotImplemented
#define Py_Is(x, y) ((x) == (y))
#define Py_IsNone(x) Py_Is((x), Py_None)
#define Py_IsTrue(x) Py_Is((x), Py_True)
#define Py_IsFalse(x) Py_Is((x), Py_False)

void Py_INCREF(PyObject *obj);
void Py_DECREF(PyObject *obj);
/* Cast the argument to PyObject* like CPython, so numpy's C++ code can pass
 * derived pointers (PyArrayObject* etc.) — C++ has no implicit pointer
 * conversion. The self-reference rule stops the inner name from re-expanding. */
#define Py_INCREF(obj) Py_INCREF((PyObject *)(obj))
#define Py_DECREF(obj) Py_DECREF((PyObject *)(obj))
Py_ssize_t pcc_capi_refcnt(PyObject *obj);
void pcc_capi_set_refcnt(PyObject *obj, Py_ssize_t refcnt);
#define Py_XINCREF(obj) do { if ((obj) != NULL) Py_INCREF((PyObject *)(obj)); } while (0)
#define Py_XDECREF(obj) do { if ((obj) != NULL) Py_DECREF((PyObject *)(obj)); } while (0)
#define Py_REFCNT(obj) pcc_capi_refcnt((PyObject *)(obj))
#define Py_SET_REFCNT(obj, refcnt) pcc_capi_set_refcnt((PyObject *)(obj), (Py_ssize_t)(refcnt))
#define Py_NewRef(obj) (Py_INCREF((PyObject *)(obj)), (obj))
#define Py_XNewRef(obj) ((obj) == NULL ? NULL : Py_NewRef(obj))
#define Py_CLEAR(obj) do { PyObject *_py_tmp = (PyObject *)(obj); (obj) = NULL; Py_XDECREF(_py_tmp); } while (0)
#define Py_SETREF(obj, value) do { PyObject *_py_tmp = (PyObject *)(obj); (obj) = (value); Py_DECREF(_py_tmp); } while (0)
#define Py_XSETREF(obj, value) do { PyObject *_py_tmp = (PyObject *)(obj); (obj) = (value); Py_XDECREF(_py_tmp); } while (0)
#define Py_None py_None
#define Py_RETURN_NONE do { Py_INCREF(Py_None); return Py_None; } while (0)
#define Py_RETURN_TRUE do { return PyBool_FromLong(1); } while (0)
#define Py_RETURN_FALSE do { return PyBool_FromLong(0); } while (0)
#define Py_RETURN_NOTIMPLEMENTED do { Py_INCREF(Py_NotImplemented); return Py_NotImplemented; } while (0)

void *PyMem_Malloc(size_t size);
void *PyMem_Calloc(size_t nelem, size_t elsize);
void *PyMem_Realloc(void *ptr, size_t new_size);
void PyMem_Free(void *ptr);
void *PyMem_RawMalloc(size_t size);
void *PyMem_RawCalloc(size_t nelem, size_t elsize);
void *PyMem_RawRealloc(void *ptr, size_t new_size);
void PyMem_RawFree(void *ptr);
void *PyObject_Malloc(size_t size);
void *PyObject_Calloc(size_t nelem, size_t elsize);
void *PyObject_Realloc(void *ptr, size_t new_size);
void PyObject_Free(void *ptr);
#define PyMem_FREE(ptr) PyMem_Free((ptr))
#define PyObject_MALLOC(size) PyObject_Malloc((size))
#define PyObject_REALLOC(ptr, size) PyObject_Realloc((ptr), (size))
#define PyObject_FREE(ptr) PyObject_Free((ptr))
#define PyObject_Del(ptr) PyObject_Free((ptr))
#define PyObject_DEL(ptr) PyObject_Free((ptr))
int PyOS_snprintf(char *str, size_t size, const char *format, ...);
int PyOS_vsnprintf(char *str, size_t size, const char *format, va_list va);

PyObject *PyLong_FromLong(long value);
PyObject *PyLong_FromUnsignedLong(unsigned long value);
PyObject *PyLong_FromLongLong(long long value);
PyObject *PyLong_FromUnsignedLongLong(unsigned long long value);
PyObject *PyLong_FromInt32(int32_t value);
PyObject *PyLong_FromInt64(int64_t value);
PyObject *PyLong_FromUInt32(uint32_t value);
PyObject *PyLong_FromUInt64(uint64_t value);
PyObject *PyLong_FromVoidPtr(void *value);
PyObject *PyLong_FromSsize_t(Py_ssize_t value);
PyObject *PyLong_FromSize_t(size_t value);
PyObject *PyLong_FromDouble(double value);
long PyLong_AsLong(PyObject *obj);
int PyLong_AsInt(PyObject *obj);
int PyLong_AsInt32(PyObject *obj, int32_t *pvalue);
int PyLong_AsInt64(PyObject *obj, int64_t *pvalue);
int PyLong_AsUInt32(PyObject *obj, uint32_t *pvalue);
int PyLong_AsUInt64(PyObject *obj, uint64_t *pvalue);
void *PyLong_AsVoidPtr(PyObject *obj);
int PyLong_AsLongAndOverflow(PyObject *obj, int *overflow);
long long PyLong_AsLongLong(PyObject *obj);
double PyLong_AsDouble(PyObject *obj);
unsigned long PyLong_AsUnsignedLong(PyObject *obj);
unsigned long long PyLong_AsUnsignedLongLong(PyObject *obj);
unsigned long long PyLong_AsUnsignedLongLongMask(PyObject *obj);
Py_ssize_t PyLong_AsSsize_t(PyObject *obj);
size_t PyLong_AsSize_t(PyObject *obj);
int PyLong_Check(PyObject *obj);
int PyLong_CheckExact(PyObject *obj);
int PyLong_IsZero(PyObject *obj);
PyObject *PyBool_FromLong(long value);
int PyBool_Check(PyObject *obj);
PyObject *PyFloat_FromDouble(double value);
double PyFloat_AsDouble(PyObject *obj);
#define PyFloat_AS_DOUBLE(obj) PyFloat_AsDouble(obj)
int PyFloat_Check(PyObject *obj);
int PyFloat_CheckExact(PyObject *obj);
PyObject *PyComplex_FromDoubles(double real, double imag);
PyObject *PyComplex_FromCComplex(Py_complex value);
Py_complex PyComplex_AsCComplex(PyObject *obj);
double PyComplex_RealAsDouble(PyObject *obj);
double PyComplex_ImagAsDouble(PyObject *obj);
int PyComplex_Check(PyObject *obj);
int PyComplex_CheckExact(PyObject *obj);

PyObject *PyUnicode_FromString(const char *value);
PyObject *PyUnicode_FromStringAndSize(const char *value, Py_ssize_t len);
PyObject *PyUnicode_FromObject(PyObject *obj);
PyObject *PyUnicode_New(Py_ssize_t size, Py_UCS4 maxchar);
PyObject *PyUnicode_FromFormat(const char *format, ...);
PyObject *PyUnicode_FromFormatV(const char *format, va_list vargs);
PyObject *PyUnicode_InternFromString(const char *value);
PyObject *PyUnicode_FromKindAndData(int kind, const void *buffer, Py_ssize_t size);
PyObject *PyUnicode_FromOrdinal(int ordinal);
Py_UCS4 *PyUnicode_AsUCS4(PyObject *unicode, Py_UCS4 *buffer, Py_ssize_t buflen, int copy_null);
Py_UCS4 *PyUnicode_AsUCS4Copy(PyObject *unicode);
PyObject *PyUnicode_FromEncodedObject(PyObject *obj, const char *encoding, const char *errors);
PyObject *PyUnicode_DecodeUTF8(
    const char *str,
    Py_ssize_t size,
    const char *errors
);
PyObject *PyUnicode_Decode(
    const char *str,
    Py_ssize_t size,
    const char *encoding,
    const char *errors
);
PyObject *PyUnicode_AsEncodedString(PyObject *obj, const char *encoding, const char *errors);
const char *PyUnicode_AsUTF8(PyObject *obj);
const char *PyUnicode_AsUTF8AndSize(PyObject *obj, Py_ssize_t *size);
PyObject *PyUnicode_AsUTF8String(PyObject *obj);
PyObject *PyUnicode_AsASCIIString(PyObject *obj);
int PyUnicode_Check(PyObject *obj);
int PyUnicode_CheckExact(PyObject *obj);
Py_ssize_t PyUnicode_GetLength(PyObject *obj);
#define PyUnicode_GET_LENGTH(obj) PyUnicode_GetLength((obj))
int PyUnicode_Compare(PyObject *left, PyObject *right);
int PyUnicode_CompareWithASCIIString(PyObject *left, const char *right);
Py_ssize_t PyUnicode_Tailmatch(PyObject *str, PyObject *substr, Py_ssize_t start, Py_ssize_t end, int direction);
Py_ssize_t PyUnicode_Find(PyObject *str, PyObject *substr, Py_ssize_t start, Py_ssize_t end, int direction);
Py_UCS4 PyUnicode_ReadChar(PyObject *unicode, Py_ssize_t index);
Py_ssize_t PyUnicode_FindChar(PyObject *str, Py_UCS4 ch, Py_ssize_t start, Py_ssize_t end, int direction);
Py_ssize_t PyUnicode_Count(PyObject *str, PyObject *substr, Py_ssize_t start, Py_ssize_t end);
PyObject *PyUnicode_Replace(PyObject *str, PyObject *substr, PyObject *replstr, Py_ssize_t maxcount);
PyObject *PyUnicode_Substring(PyObject *str, Py_ssize_t start, Py_ssize_t end);
int PyUnicode_Contains(PyObject *container, PyObject *element);
PyObject *PyUnicode_Concat(PyObject *left, PyObject *right);
int PyUnicode_EqualToUTF8AndSize(PyObject *unicode, const char *str, Py_ssize_t str_len);
int PyUnicode_EqualToUTF8(PyObject *unicode, const char *str);
typedef struct PyUnicodeWriter PyUnicodeWriter;
PyUnicodeWriter *PyUnicodeWriter_Create(Py_ssize_t length);
PyObject *PyUnicodeWriter_Finish(PyUnicodeWriter *writer);
void PyUnicodeWriter_Discard(PyUnicodeWriter *writer);
int PyUnicodeWriter_WriteChar(PyUnicodeWriter *writer, Py_UCS4 ch);
int PyUnicodeWriter_WriteUTF8(PyUnicodeWriter *writer, const char *str, Py_ssize_t size);
int PyUnicodeWriter_WriteStr(PyUnicodeWriter *writer, PyObject *obj);
int PyUnicodeWriter_WriteSubstring(
    PyUnicodeWriter *writer,
    PyObject *str,
    Py_ssize_t start,
    Py_ssize_t end
);
#define PyUnicode_1BYTE_KIND 1
#define PyUnicode_2BYTE_KIND 2
#define PyUnicode_4BYTE_KIND 4
/* pcc strings use immutable UTF-8 storage, so PyUnicode_KIND() is always 1 and
 * PyUnicode_DATA() exposes the UTF-8 bytes. 2/4BYTE_DATA never apply (KIND==1)
 * but must be defined so files that branch on KIND still compile. READ and
 * READ_CHAR route through a helper that decodes the requested codepoint, rather
 * than treating a UTF-8 byte offset as a character index. */
extern const char *py_str_utf8(PyObject *s);
#define PyUnicode_1BYTE_DATA(op) ((Py_UCS1 *)py_str_utf8(op))
#define PyUnicode_2BYTE_DATA(op) ((Py_UCS2 *)py_str_utf8(op))
#define PyUnicode_4BYTE_DATA(op) ((Py_UCS4 *)py_str_utf8(op))
#define PyUnicode_DATA(op) ((void *)py_str_utf8(op))
Py_UCS4 pcc_capi_unicode_read(int kind, const void *data, Py_ssize_t index);
#define PyUnicode_READ(kind, data, index) pcc_capi_unicode_read((kind), (data), (index))
#define PyUnicode_READ_CHAR(op, i) \
    pcc_capi_unicode_read(PyUnicode_KIND(op), PyUnicode_DATA(op), (i))
#define Py_UNICODE_ISSPACE(ch) ((ch) == ' ' || (ch) == '\t' || (ch) == '\n' || (ch) == '\r' || (ch) == '\f' || (ch) == '\v')
#define Py_UNICODE_ISDIGIT(ch) ((ch) >= '0' && (ch) <= '9')
#define Py_UNICODE_ISDECIMAL(ch) Py_UNICODE_ISDIGIT((ch))
#define Py_UNICODE_ISNUMERIC(ch) Py_UNICODE_ISDIGIT((ch))
#define Py_UNICODE_ISLOWER(ch) ((ch) >= 'a' && (ch) <= 'z')
#define Py_UNICODE_ISUPPER(ch) ((ch) >= 'A' && (ch) <= 'Z')
#define Py_UNICODE_ISTITLE(ch) Py_UNICODE_ISUPPER((ch))
#define Py_UNICODE_ISALPHA(ch) (Py_UNICODE_ISLOWER((ch)) || Py_UNICODE_ISUPPER((ch)))
#define Py_UNICODE_ISALNUM(ch) (Py_UNICODE_ISALPHA((ch)) || Py_UNICODE_ISDIGIT((ch)))

PyObject *PyObject_GetAttr(PyObject *obj, PyObject *attr);
PyObject *PyObject_GetAttrString(PyObject *obj, const char *attr);
int PyObject_GetOptionalAttr(PyObject *obj, PyObject *attr, PyObject **result);
int PyObject_GetOptionalAttrString(PyObject *obj, const char *attr, PyObject **result);
int PyObject_SetAttr(PyObject *obj, PyObject *attr, PyObject *value);
int PyObject_SetAttrString(PyObject *obj, const char *attr, PyObject *value);
int PyObject_HasAttr(PyObject *obj, PyObject *attr);
int PyObject_HasAttrString(PyObject *obj, const char *attr);
int PyObject_HasAttrWithError(PyObject *obj, PyObject *attr);
int PyObject_HasAttrStringWithError(PyObject *obj, const char *attr);
int PyObject_IsTrue(PyObject *obj);
int PyObject_Not(PyObject *obj);
Py_hash_t PyObject_Hash(PyObject *obj);
int PyCallable_Check(PyObject *obj);
PyObject *PyObject_Str(PyObject *obj);
PyObject *PyObject_Repr(PyObject *obj);
PyObject *PyObject_Bytes(PyObject *obj);
PyObject *PyObject_Format(PyObject *obj, PyObject *format_spec);
#define Py_PRINT_RAW 1
int PyObject_Print(PyObject *obj, FILE *fp, int flags);
PyObject *PyObject_Type(PyObject *obj);
int PyObject_IsInstance(PyObject *obj, PyObject *cls);
PyObject *PyObject_RichCompare(PyObject *left, PyObject *right, int opid);
int PyObject_RichCompareBool(PyObject *left, PyObject *right, int opid);
PyObject *PyObject_GetItem(PyObject *obj, PyObject *key);
int PyObject_SetItem(PyObject *obj, PyObject *key, PyObject *value);
int PyObject_DelItem(PyObject *obj, PyObject *key);
Py_ssize_t PyObject_Size(PyObject *obj);
Py_ssize_t PyObject_Length(PyObject *obj);
Py_ssize_t PyObject_LengthHint(PyObject *obj, Py_ssize_t default_value);
PyObject *PyObject_SelfIter(PyObject *obj);
PyObject *PyObject_GetIter(PyObject *obj);
PyObject *PyIter_Next(PyObject *obj);
int PyIter_NextItem(PyObject *iter, PyObject **item);
int PyIter_Check(PyObject *obj);

PyObject *PyNumber_Add(PyObject *left, PyObject *right);
PyObject *PyNumber_Subtract(PyObject *left, PyObject *right);
PyObject *PyNumber_Multiply(PyObject *left, PyObject *right);
PyObject *PyNumber_TrueDivide(PyObject *left, PyObject *right);
PyObject *PyNumber_FloorDivide(PyObject *left, PyObject *right);
PyObject *PyNumber_Remainder(PyObject *left, PyObject *right);
PyObject *PyNumber_Power(PyObject *left, PyObject *right, PyObject *mod);
PyObject *PyNumber_Negative(PyObject *obj);
PyObject *PyNumber_Positive(PyObject *obj);
PyObject *PyNumber_Absolute(PyObject *obj);
int PyNumber_Check(PyObject *obj);
PyObject *PyNumber_Long(PyObject *obj);
PyObject *PyNumber_Float(PyObject *obj);
PyObject *PyNumber_And(PyObject *left, PyObject *right);
PyObject *PyNumber_Or(PyObject *left, PyObject *right);
PyObject *PyNumber_Xor(PyObject *left, PyObject *right);
PyObject *PyNumber_Invert(PyObject *obj);
PyObject *PyNumber_Lshift(PyObject *left, PyObject *right);
PyObject *PyNumber_Rshift(PyObject *left, PyObject *right);
PyObject *PyNumber_Index(PyObject *obj);
Py_ssize_t PyNumber_AsSsize_t(PyObject *obj, PyObject *exc);
int PyIndex_Check(PyObject *obj);

int PyMapping_Check(PyObject *obj);
Py_ssize_t PyMapping_Size(PyObject *obj);
Py_ssize_t PyMapping_Length(PyObject *obj);
PyObject *PyMapping_GetItemString(PyObject *obj, const char *key);
int PyMapping_SetItemString(PyObject *obj, const char *key, PyObject *value);
int PyMapping_HasKey(PyObject *obj, PyObject *key);
int PyMapping_HasKeyString(PyObject *obj, const char *key);
int PyMapping_GetOptionalItem(PyObject *obj, PyObject *key, PyObject **result);
int PyMapping_GetOptionalItemString(PyObject *obj, const char *key, PyObject **result);
int PyMapping_HasKeyWithError(PyObject *obj, PyObject *key);
int PyMapping_HasKeyStringWithError(PyObject *obj, const char *key);
PyObject *PyMapping_Keys(PyObject *obj);
PyObject *PyMapping_Values(PyObject *obj);
PyObject *PyMapping_Items(PyObject *obj);

PyObject *PyCapsule_New(void *pointer, const char *name, PyCapsule_Destructor destructor);
void *PyCapsule_GetPointer(PyObject *capsule, const char *name);
const char *PyCapsule_GetName(PyObject *capsule);
void *PyCapsule_GetContext(PyObject *capsule);
PyCapsule_Destructor PyCapsule_GetDestructor(PyObject *capsule);
int PyCapsule_IsValid(PyObject *capsule, const char *name);
int PyCapsule_CheckExact(PyObject *capsule);
int PyCapsule_SetContext(PyObject *capsule, void *context);
int PyCapsule_SetName(PyObject *capsule, const char *name);
int PyCapsule_SetPointer(PyObject *capsule, void *pointer);
int PyCapsule_SetDestructor(PyObject *capsule, PyCapsule_Destructor destructor);
void *PyCapsule_Import(const char *name, int no_block);

PyObject *PyTuple_New(Py_ssize_t size);
int PyTuple_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value);
Py_ssize_t PyTuple_Size(PyObject *obj);
PyObject *PyTuple_GetItem(PyObject *obj, Py_ssize_t index);
PyObject *PyTuple_Pack(Py_ssize_t size, ...);
int PyTuple_Check(PyObject *obj);
int PyTuple_CheckExact(PyObject *obj);

PyObject *PyList_New(Py_ssize_t size);
int PyList_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value);
PyObject *PyList_GetItem(PyObject *obj, Py_ssize_t index);
PyObject *PyList_GetItemRef(PyObject *obj, Py_ssize_t index);
Py_ssize_t PyList_Size(PyObject *obj);
int PyList_Append(PyObject *obj, PyObject *value);
PyObject *PyList_AsTuple(PyObject *obj);
int PyList_Check(PyObject *obj);
int PyList_CheckExact(PyObject *obj);

PyObject *PyDict_New(void);
void PyDict_Clear(PyObject *dict);
int PyDict_SetItem(PyObject *dict, PyObject *key, PyObject *value);
int PyDict_SetItemString(PyObject *dict, const char *key, PyObject *value);
PyObject *PyDict_GetItem(PyObject *dict, PyObject *key);
PyObject *PyDict_GetItemString(PyObject *dict, const char *key);
PyObject *PyDict_GetItemWithError(PyObject *dict, PyObject *key);
int PyDict_GetItemRef(PyObject *dict, PyObject *key, PyObject **result);
int PyDict_GetItemStringRef(PyObject *dict, const char *key, PyObject **result);
int PyDict_SetDefaultRef(PyObject *dict, PyObject *key, PyObject *default_value, PyObject **result);
int PyDict_Pop(PyObject *dict, PyObject *key, PyObject **result);
int PyDict_PopString(PyObject *dict, const char *key, PyObject **result);
int PyDict_DelItem(PyObject *dict, PyObject *key);
int PyDict_DelItemString(PyObject *dict, const char *key);
Py_ssize_t PyDict_Size(PyObject *dict);
int PyDict_Contains(PyObject *dict, PyObject *key);
int PyDict_ContainsString(PyObject *dict, const char *key);
int PyDict_Next(PyObject *dict, Py_ssize_t *pos, PyObject **key, PyObject **value);
PyObject *PyDict_Keys(PyObject *dict);
PyObject *PyDict_Values(PyObject *dict);
PyObject *PyDict_Items(PyObject *dict);
int PyDict_Check(PyObject *obj);
int PyDict_CheckExact(PyObject *obj);

PyObject *PySet_New(PyObject *iterable);
int PySet_Add(PyObject *set, PyObject *key);
int PySet_Contains(PyObject *set, PyObject *key);
int PySet_Discard(PyObject *set, PyObject *key);
Py_ssize_t PySet_Size(PyObject *set);
int PySet_Check(PyObject *obj);
int PySet_CheckExact(PyObject *obj);
int PyAnySet_Check(PyObject *obj);
int PyAnySet_CheckExact(PyObject *obj);
#define PySet_GET_SIZE(obj) PySet_Size((obj))

PyObject *PyBytes_FromString(const char *value);
PyObject *PyBytes_FromStringAndSize(const char *value, Py_ssize_t len);
char *PyBytes_AsString(PyObject *obj);
int PyBytes_AsStringAndSize(PyObject *obj, char **buffer, Py_ssize_t *length);
Py_ssize_t PyBytes_Size(PyObject *obj);
int PyBytes_Check(PyObject *obj);
int PyBytes_CheckExact(PyObject *obj);
#define PyBytes_AS_STRING(obj) PyBytes_AsString((obj))
#define PyBytes_GET_SIZE(obj) PyBytes_Size((obj))

int PyArg_ParseTuple(PyObject *args, const char *format, ...);
int PyArg_ParseTupleAndKeywords(
    PyObject *args,
    PyObject *kwargs,
    const char *format,
    char **kwlist,
    ...
);
int PyArg_VaParseTupleAndKeywords(PyObject *args, PyObject *kwargs,
                                  const char *format, char **kwlist, va_list va);
PyObject *Py_BuildValue(const char *format, ...);

void PyErr_SetString(PyObject *type, const char *message);
void PyErr_SetNone(PyObject *type);
void PyErr_SetObject(PyObject *type, PyObject *value);
PyObject *PyErr_Format(PyObject *type, const char *format, ...);
PyObject *PyErr_FormatV(PyObject *type, const char *format, va_list vargs);
PyObject *PyErr_NoMemory(void);
PyObject *PyErr_SetFromErrno(PyObject *type);
PyObject *PyErr_SetFromErrnoWithFilenameObject(PyObject *type, PyObject *filenameObject);
PyObject *PyErr_NewException(const char *name, PyObject *base, PyObject *dict);
void PyErr_BadInternalCall(void);
int PyErr_WarnEx(PyObject *category, const char *message, Py_ssize_t stack_level);
int PyErr_WarnFormat(PyObject *category, Py_ssize_t stack_level, const char *format, ...);
void PyErr_WriteUnraisable(PyObject *obj);
void PyErr_Print(void);
int PyErr_CheckSignals(void);
PyObject *PyErr_Occurred(void);
void PyErr_Clear(void);
int PyErr_GivenExceptionMatches(PyObject *given, PyObject *exc);
int PyErr_ExceptionMatches(PyObject *exc);
void PyErr_Fetch(PyObject **ptype, PyObject **pvalue, PyObject **ptraceback);
void PyErr_Restore(PyObject *type, PyObject *value, PyObject *traceback);

int PyObject_CheckBuffer(PyObject *obj);
int PyObject_GetBuffer(PyObject *obj, Py_buffer *view, int flags);
void PyBuffer_Release(Py_buffer *view);
int PyMemoryView_Check(PyObject *obj);
PyObject *PyMemoryView_FromObject(PyObject *obj);
PyObject *PyMemoryView_FromMemory(char *mem, Py_ssize_t size, int flags);
Py_buffer *pcc_PyMemoryView_GET_BUFFER(PyObject *obj);
PyObject *pcc_PyMemoryView_GET_BASE(PyObject *obj);
#define PyMemoryView_GET_BUFFER(obj) pcc_PyMemoryView_GET_BUFFER((PyObject *)(obj))
#define PyMemoryView_GET_BASE(obj) pcc_PyMemoryView_GET_BASE((PyObject *)(obj))
PyObject *PyObject_Call(PyObject *callable, PyObject *args, PyObject *kwargs);
PyObject *PyObject_CallObject(PyObject *callable, PyObject *args);
PyObject *PyObject_CallNoArgs(PyObject *callable);
PyObject *PyObject_CallOneArg(PyObject *callable, PyObject *arg);
PyObject *PyObject_Vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames);
PyObject *PyObject_VectorcallMethod(PyObject *name, PyObject *const *args, size_t nargsf, PyObject *kwnames);
PyObject *PyObject_CallFunction(PyObject *callable, const char *format, ...);
PyObject *PyObject_CallMethod(PyObject *obj, const char *name, const char *format, ...);
PyObject *PyObject_CallMethodNoArgs(PyObject *obj, PyObject *name);
PyObject *PyObject_CallMethodOneArg(PyObject *obj, PyObject *name, PyObject *arg);
PyObject *PyObject_CallFunctionObjArgs(PyObject *callable, ...);
PyObject *PyObject_CallMethodObjArgs(PyObject *obj, PyObject *name, ...);
int Py_IsInitialized(void);
PyGILState_STATE PyGILState_Ensure(void);
void PyGILState_Release(PyGILState_STATE state);
int PyGILState_Check(void);

int PySequence_Check(PyObject *obj);
Py_ssize_t PySequence_Size(PyObject *obj);
Py_ssize_t PySequence_Length(PyObject *obj);
PyObject *PySequence_GetItem(PyObject *obj, Py_ssize_t index);
int PySequence_SetItem(PyObject *obj, Py_ssize_t index, PyObject *value);
int PySequence_Contains(PyObject *obj, PyObject *value);
PyObject *PySequence_Concat(PyObject *left, PyObject *right);
PyObject *PySequence_Repeat(PyObject *obj, Py_ssize_t count);
PyObject *PySequence_InPlaceConcat(PyObject *left, PyObject *right);
PyObject *PySequence_InPlaceRepeat(PyObject *obj, Py_ssize_t count);
PyObject *PySequence_Fast(PyObject *obj, const char *message);
Py_ssize_t PySequence_Fast_GET_SIZE(PyObject *obj);
PyObject **PySequence_Fast_ITEMS(PyObject *obj);
PyObject *PySequence_List(PyObject *obj);
PyObject *PySequence_Tuple(PyObject *obj);

PyObject *PyModule_Create2(PyModuleDef *def, int api_version);
PyObject *PyModule_GetDict(PyObject *module);
void *PyModule_GetState(PyObject *module);
int PyModule_AddObject(PyObject *module, const char *name, PyObject *value);
int PyModule_AddObjectRef(PyObject *module, const char *name, PyObject *value);
int PyModule_Add(PyObject *module, const char *name, PyObject *value);
int PyModule_AddIntConstant(PyObject *module, const char *name, long value);
int PyModule_AddStringConstant(PyObject *module, const char *name, const char *value);
#define PyModule_Create(def) PyModule_Create2((def), PYTHON_API_VERSION)

PyObject *PyImport_ImportModule(const char *name);

#define PyTuple_GET_ITEM(obj, index) PyTuple_GetItem((obj), (index))
#define PyTuple_GET_SIZE(obj) PyTuple_Size((obj))
#define PyTuple_SET_ITEM(obj, index, value) ((void)PyTuple_SetItem((obj), (index), (value)))
#define PyList_GET_ITEM(obj, index) PyList_GetItem((obj), (index))
#define PyList_GET_SIZE(obj) PyList_Size((obj))
#define PyList_SET_ITEM(obj, index, value) ((void)PyList_SetItem((obj), (index), (value)))
#define PySequence_Fast_GET_ITEM(obj, index) (PySequence_Fast_ITEMS(obj)[index])

#ifdef __cplusplus
}
#endif

#endif
