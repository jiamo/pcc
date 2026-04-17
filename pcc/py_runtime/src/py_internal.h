/* pcc/py_runtime/src/py_internal.h
 *
 * Private layouts shared across the runtime .c files.
 * NOT part of the public ABI — only py_runtime.h is.
 */
#ifndef PY_INTERNAL_H
#define PY_INTERNAL_H

#include "../include/py_runtime.h"
#include <stdint.h>
#include <stddef.h>

/* ---- Flags ------------------------------------------------------------- */
#define PY_FLAG_IMMORTAL    0x1
#define PY_FLAG_GC_TRACKED  0x2

/* ---- Type-specific deallocators (extern so py_obj.py can dispatch) -- */
void py_dealloc_int(PyObject *o);
void py_dealloc_float(PyObject *o);
void py_dealloc_str(PyObject *o);
void py_dealloc_list(PyObject *o);
void py_dealloc_tuple(PyObject *o);
void py_dealloc_dict(PyObject *o);
void py_dealloc_set(PyObject *o);
void py_dealloc_generic(PyObject *o);

/* ---- Tagged-int helpers ------------------------------------------------ */
/* Encoding: low bit = 1 means value; shift right arithmetic to recover the
 * int63 payload. Low bit = 0 means a real PyObject* pointer. Since malloc
 * returns at least 8-byte-aligned pointers on every platform we care about,
 * bit 0 of a real pointer is always 0. */

#define PY_IS_TAGGED_INT(p)  (((uintptr_t)(p) & 1u) == 1u)

/* Encode int63 payload (already range-checked) into a tagged PyObject*. */
static inline PyObject *py_tag_int(int64_t v) {
    /* Shift left by 1, set low bit. */
    return (PyObject *)(uintptr_t)(((uint64_t)v << 1) | 1u);
}

/* Decode tagged int payload via arithmetic right shift (sign-preserving). */
static inline int64_t py_untag_int(PyObject *p) {
    /* Cast to intptr_t so >>1 is arithmetic. */
    return (int64_t)((intptr_t)(uintptr_t)p >> 1);
}

/* Tagged-int range: 63-bit signed. */
#define PY_TAGGED_INT_MIN  ((int64_t)INT64_MIN >> 1)
#define PY_TAGGED_INT_MAX  ((int64_t)INT64_MAX >> 1)

/* ---- Concrete object layouts ------------------------------------------ */

/* The public PyObject is opaque; concretely every real (heap) object
 * starts with a PyObjectHeader and is followed by type-specific fields. */

/* Heap-allocated int: bignum representation (Phase 2).
 *
 * Sign-magnitude bignum with base 2^32 digits stored little-endian
 * (digits[0] is least significant). `sign` is -1, 0, or +1. `ndigits` is the
 * number of meaningful digits; the value 0 is encoded as sign=0, ndigits=0.
 *
 * Invariants (maintained by all constructors/operations that return a
 * canonical bignum):
 *   - If sign == 0: ndigits == 0.
 *   - If sign != 0: ndigits >= 1 and digits[ndigits - 1] != 0.
 *
 * Values that fit in the tagged range (PY_TAGGED_INT_MIN .. PY_TAGGED_INT_MAX)
 * should be stored as tagged ints, not as PyIntObjects. Routines that build
 * a bignum but want the canonical PyObject form go through
 * py_bigint_to_pyobject().
 */
typedef struct {
    PyObjectHeader h;
    int32_t  sign;      /* -1, 0, +1 */
    int32_t  ndigits;   /* number of base-2^32 digits in use */
    uint32_t digits[];  /* flexible array — length == ndigits */
} PyIntObject;

typedef struct {
    PyObjectHeader h;
    double value;
} PyFloatObject;

/* PyStrObject: UTF-8 encoded string.
 *
 * Phase 2 layout uses a flexible-array-member tail for the UTF-8 payload,
 * so the bytes + NUL terminator live inline with the header. This removes
 * a second allocation and makes bounds/hash/len lookups cache-friendly.
 *
 *   sizeof(PyStrObject) = header + 3 * int64
 *   total alloc size    = sizeof(PyStrObject) + byte_len + 1
 */
typedef struct {
    PyObjectHeader h;
    int64_t byte_len;   /* UTF-8 bytes, not codepoints */
    int64_t cp_len;     /* cached codepoint count, -1 = unset (lazy) */
    int64_t hash;       /* cached FNV-1a hash, -1 if not yet computed */
    char    data[];     /* UTF-8 bytes followed by NUL terminator */
} PyStrObject;

PyObject *py_str_new(const char *data, int64_t byte_len);
int64_t py_str_byte_len(PyObject *s);
const char *py_str_utf8(PyObject *s);
PyObject *py_obj_str(PyObject *o);

typedef struct {
    PyObjectHeader h;
    int64_t length;
    int64_t capacity;
    PyObject **items;   /* owned array of owned refs */
} PyListObject;

/* Tuple: header + len + inline flexible array of owned refs.
 *
 * Phase 2 switches to a flexible array member so the header and the items
 * live in one contiguous allocation. This matches the contract exactly
 * (see docs/plans/python-frontend-interfaces.md §3) and saves one malloc
 * per tuple. Deallocation walks items[0..len-1] and frees the whole block.
 */
typedef struct {
    PyObjectHeader h;
    int64_t  len;
    PyObject *items[];   /* flexible array — owned refs */
} PyTupleObject;

/* ---- Dict -------------------------------------------------------------- */
/* Dict entry (insertion-ordered compact array). */
typedef struct {
    int64_t   hash;
    PyObject *key;      /* NULL marks a dead entry (deleted post-insert) */
    PyObject *value;
} DictEntry;

/* Dict with split index-table + insertion-ordered entries array
 * (PEP 468 / PEP 509 compact-dict style).
 *
 *   indices[]     : probe table, size = capacity (power of 2).
 *                   Each cell holds an index into entries[], or:
 *                     -1  (PY_DICT_EMPTY)     -> slot has never held a key
 *                     -2  (PY_DICT_TOMBSTONE) -> slot held a deleted key
 *   entries[]     : insertion order preserved; length == capacity so we
 *                   never overflow while honoring the 2/3 load factor.
 *   entries_used  : high-water mark into entries[] (including dead slots
 *                   created by del). Next insert lands at
 *                   entries[entries_used]. Rehash compacts the array.
 *   size          : number of live entries.
 */
typedef struct {
    PyObjectHeader h;
    int64_t     size;
    int64_t     capacity;
    int64_t    *indices;
    DictEntry  *entries;
    int64_t     entries_used;
} PyDictObject;

#define PY_DICT_EMPTY      ((int64_t)-1)
#define PY_DICT_TOMBSTONE  ((int64_t)-2)

/* ---- Set --------------------------------------------------------------- */
/* Set entry — open-addressing table with no value side.
 *
 *   key == NULL            -> empty slot (never written)
 *   key == py_set_dummy    -> tombstone (deleted)
 *   otherwise              -> live entry with cached `hash`
 */
typedef struct {
    int64_t   hash;
    PyObject *key;
} SetEntry;

typedef struct {
    PyObjectHeader h;
    int64_t   size;        /* live entries */
    int64_t   capacity;    /* power of 2 */
    int64_t   fill;        /* live + tombstones; triggers rehash */
    SetEntry *entries;
} PySetObject;

/* Sentinel pointer used as a set tombstone. Distinct from any heap object
 * (it does not carry a valid header) and from any tagged int (low bit is
 * always zero on a static-data address). py_incref/py_decref must never
 * see it — py_set code is responsible for keeping it out of their way.
 * Defined in py_set.c. */
extern PyObject *const py_set_dummy;

/* ---- Class / Instance (Phase 3) --------------------------------------- */

/* Method table entry (name -> PyObject* function). For Phase 3 the stored
 * PyObject is not a real callable PyObject yet; it is a cast of the LLVM
 * function pointer the codegen emitted. py_obj_call checks class lookups
 * and invokes the pointer through the right ABI trampoline. */
typedef struct PyClassMethod {
    const char *name;
    PyObject   *func;       /* borrowed — points at a user_* LLVM function */
} PyClassMethod;

/* Class object: describes a user-defined Python class.
 *
 * Layout matches docs/plans/python-frontend-interfaces.md §3 addendum.
 * Every field is set at module-init time by the codegen-emitted class
 * initializer function; nothing in here is mutated after init.
 *
 *   name              : interned C string; never freed.
 *   bases / n_bases   : direct bases in declaration order.
 *   mro / n_mro       : C3 linearization; mro[0] == this class,
 *                       mro[n_mro - 1] == <root object> (or NULL if we
 *                       never installed a root).
 *   methods           : array of {name, PyObject* func}. Lookup is linear;
 *                       Phase 3 doesn't bother with a hash table because
 *                       classes are small and the cost is dwarfed by call.
 *   field_names       : declared instance-field names in slot order.
 *   instance_size     : total bytes of a PyInstanceObject carrying
 *                       n_fields slots.
 *   type_tag_alloc    : the type tag allocated for this class
 *                       (PY_TYPE_USER + n). Instances carry this tag in
 *                       their header so isinstance and dispatch stay O(1)
 *                       for pointer identity checks.
 */
typedef struct PyClassObject {
    PyObjectHeader           h;
    const char              *name;
    int32_t                  n_bases;
    struct PyClassObject   **bases;
    int32_t                  n_mro;
    struct PyClassObject   **mro;
    int32_t                  n_methods;
    PyClassMethod           *methods;
    int32_t                  n_fields;
    const char             **field_names;
    int32_t                  instance_size;
    int32_t                  type_tag_alloc;
} PyClassObject;

/* Instance object: header + pointer to class + flexible field slot array.
 *
 * Each slot holds a PyObject* (owned reference; may be NULL if the field
 * has not been assigned yet). Deallocation walks fields[0..n_fields - 1]
 * and decrements each non-NULL slot before freeing the block.
 */
typedef struct PyInstanceObject {
    PyObjectHeader          h;
    PyClassObject          *cls;
    PyObject               *fields[];
} PyInstanceObject;

/* ---- Class / Instance API (py_class.c) -------------------------------- */

/* Construct a new class. Computes a C3 linearization over `bases`.
 *
 *   name        : interned; class keeps a borrowed pointer to it.
 *   bases       : array of PyClassObject* (direct bases). May be NULL if
 *                 n_bases == 0 (root class).
 *   n_bases     : number of direct bases.
 *   field_names : declared instance-field names, in slot order. Borrowed
 *                 pointers (caller keeps them alive for the class's life).
 *   n_fields    : number of declared instance fields. NOTE: this is the
 *                 count declared on THIS class only; the class inherits
 *                 its bases' fields through MRO lookup at attribute time,
 *                 not by slot duplication.
 *
 * Returns a new reference that the caller owns.
 */
PyClassObject *py_class_new(const char *name,
                            PyClassObject **bases, int32_t n_bases,
                            const char **field_names, int32_t n_fields);

/* Install a method on the class. `func` is borrowed (caller retains
 * ownership). Methods added after py_class_new are visible to subsequent
 * lookups but not to earlier instances — normal Python behavior. */
void py_class_add_method(PyClassObject *cls, const char *name, PyObject *func);

/* Walk the class's MRO and return the first method with the given name,
 * or NULL if none is found. Borrowed reference. */
PyObject *py_class_lookup(PyClassObject *cls, const char *name);

/* Allocate an instance of `cls`. All field slots start at NULL. Returns a
 * new reference. Calls py_class_new implicit logic — no __init__ is
 * invoked from here; callers (py_obj_call for class-as-callable) run
 * __init__ separately. */
PyObject *py_instance_new(PyClassObject *cls);

/* Direct field accessors by slot index. No bounds check at runtime in the
 * fast path — codegen is expected to emit a valid index. A defensive
 * check is kept (returns NULL / is a no-op on out-of-range) so malformed
 * IR cannot segfault us. */
PyObject *py_instance_get_field(PyInstanceObject *inst, int32_t idx);
void      py_instance_set_field(PyInstanceObject *inst, int32_t idx, PyObject *value);

/* Generic attribute dispatch for PY_TYPE_INSTANCE / PY_TYPE_USER tags.
 * Tries `inst->cls->field_names` first, then MRO method lookup. Returns
 * a borrowed reference (caller may py_incref if keeping). */
PyObject *py_instance_getattr(PyInstanceObject *inst, const char *name);

/* Attribute assignment. Returns 0 on success, -1 on failure (e.g. unknown
 * field and no method slot to accept). */
int64_t py_instance_setattr(PyInstanceObject *inst, const char *name, PyObject *value);

/* Shallow-copy a native instance and override named fields. Used by the
 * dataclasses.replace fast path on pcc-native class instances. */
PyObject *py_dataclass_replace(PyObject *obj, int64_t n_overrides,
                               const char **names, PyObject **values);
PyObject *py_dataclass_replace_from_dict(PyObject *obj, PyObject *overrides);

/* isinstance(obj, cls) — walks obj's class's MRO looking for cls.
 * Returns 1 if obj is an instance of cls or any subclass, 0 otherwise.
 * Non-instance objects get 0. */
int64_t py_isinstance(PyObject *obj, PyClassObject *cls);

/* super() lookup: find the first method named `name` in `start_cls`'s
 * MRO strictly AFTER `from_cls`. This is the standard super() semantic —
 * a super() call inside a method defined on `from_cls` invoked on an
 * instance whose class is `start_cls`. Returns a borrowed reference or
 * NULL if no such method exists. */
PyObject *py_super_lookup(PyClassObject *start_cls,
                          PyClassObject *from_cls,
                          const char *name);

/* Compute the C3 linearization of `bases`. The first element of the
 * returned MRO is always a freshly introduced "self" placeholder supplied
 * by py_class_new (it handles prepending); this function only linearizes
 * the bases' MROs plus the `bases` list itself. Writes an owned array to
 * *out_mro (malloc'd) and the length to *out_n. Returns 0 on success,
 * -1 on MRO inconsistency (raises nothing in Phase 3; marks out_n = -1).
 *
 * Callers retain ownership of the returned array and must free() it. */
int c3_linearize(PyClassObject **bases, int32_t n_bases,
                 PyClassObject ***out_mro, int32_t *out_n);

/* Destructor helper used by py_decref when a class's refcount drops. */
void py_class_dealloc(PyObject *o);
void py_instance_dealloc(PyObject *o);

/* ---- Descriptors (Phase 3 — property / classmethod / staticmethod) ----
 *
 * The three descriptor wrappers wear their own user-type tags so the
 * descriptor protocol in py_obj_getattr can recognise them without a
 * string match on the function's name. Tags sit just above PY_TYPE_USER
 * and must stay stable — codegen (layer1.py) and the runtime both test
 * against these constants. */
#define PY_TYPE_PROPERTY      (PY_TYPE_USER + 1)
#define PY_TYPE_CLASSMETHOD   (PY_TYPE_USER + 2)
#define PY_TYPE_STATICMETHOD  (PY_TYPE_USER + 3)

typedef struct {
    PyObjectHeader h;       /* type_tag = PY_TYPE_PROPERTY */
    PyObject *fget;         /* function or NULL */
    PyObject *fset;         /* function or NULL */
    PyObject *fdel;         /* function or NULL */
} PyPropertyObject;

typedef struct {
    PyObjectHeader h;       /* type_tag = PY_TYPE_CLASSMETHOD */
    PyObject *func;
} PyClassMethodObject;

typedef struct {
    PyObjectHeader h;       /* type_tag = PY_TYPE_STATICMETHOD */
    PyObject *func;
} PyStaticMethodObject;

/* Constructors (implemented in py_descr.c). All return new references. */
PyObject *py_property_new(PyObject *fget, PyObject *fset, PyObject *fdel);
PyObject *py_classmethod_new(PyObject *func);
PyObject *py_staticmethod_new(PyObject *func);

/* In-place setter/deleter replacement — used by the @name.setter /
 * @name.deleter decorator form where a second `def` with the same
 * attribute name updates the already-installed property's fset/fdel
 * slot instead of creating a new property object.
 *
 * Returns 0 on success, -1 if ``prop`` is not a property. Acquires
 * a new reference to ``func`` (NULL is accepted to clear the slot). */
int py_property_set_fset(PyObject *prop, PyObject *func);
int py_property_set_fdel(PyObject *prop, PyObject *func);

/* ---- Iteration + extended generic ops (Phase 3) ----------------------- */
PyObject *py_obj_iter(PyObject *o);
PyObject *py_obj_next(PyObject *it);
int64_t   py_obj_contains(PyObject *container, PyObject *item);

/* Numeric / comparison dunders. Each first tries the native fast path
 * for built-in types, then falls through to ``__op__`` on LHS and
 * ``__rop__`` on RHS. */
PyObject *py_obj_add(PyObject *a, PyObject *b);
PyObject *py_obj_sub(PyObject *a, PyObject *b);
PyObject *py_obj_mul(PyObject *a, PyObject *b);
PyObject *py_obj_truediv(PyObject *a, PyObject *b);
PyObject *py_obj_floordiv(PyObject *a, PyObject *b);
PyObject *py_obj_mod(PyObject *a, PyObject *b);
PyObject *py_obj_pow(PyObject *a, PyObject *b);
PyObject *py_obj_neg(PyObject *a);
PyObject *py_obj_pos(PyObject *a);
PyObject *py_obj_invert(PyObject *a);
int64_t py_obj_eq(PyObject *a, PyObject *b);
int64_t py_obj_lt(PyObject *a, PyObject *b);
int64_t py_obj_le(PyObject *a, PyObject *b);
int64_t py_obj_gt(PyObject *a, PyObject *b);
int64_t py_obj_ge(PyObject *a, PyObject *b);

/* ---- Exceptions (Phase 3) --------------------------------------------- */

/* Traceback frame record.
 *
 * Each entry captures a single activation: the function name, source
 * filename, and line number of the call site or throw point. Stored by
 * value inside a traceback array owned by the PyExceptionObject.
 *
 * Borrowed-pointer semantics: `func_name` and `filename` reference
 * static rodata globals emitted by the frontend. The exception object
 * never frees them. */
typedef struct PyFrameRecord {
    const char *func_name;
    const char *filename;
    int32_t     line;
    int32_t     _pad;       /* keep 16-byte alignment */
} PyFrameRecord;

/* Exception object layout.
 *
 * PY_TYPE_EXC is the header type tag for every builtin exception; user
 * subclasses carry the same tag but name a subclass in exc_class, so
 * isinstance walks stay uniform. Frontend-emitted landingpads only
 * ever peek through this view — they read exc_class for matching, then
 * the python-level handler body reads .args / .__cause__ etc. via
 * getattr.
 *
 *   exc_class : concrete class. NULL is legal transiently (py_exc_alloc
 *               sets it to py_exc_builtin_class(PY_EXC_EXCEPTION) if
 *               the caller leaves it unset), but in steady state is
 *               always non-NULL. Owns its ref.
 *   message   : args[0] — a PyStrObject* or py_None. Owns its ref.
 *   cause     : `raise X from Y` target. NULL = no explicit cause.
 *               Owns its ref.
 *   context   : implicit context captured when a new exception replaces
 *               an active one (`raise Y` inside except-block). Owns its
 *               ref.
 *   traceback : heap-allocated growable array of PyFrameRecord. NULL
 *               until a frame is appended. */
typedef struct PyExceptionObject {
    PyObjectHeader      h;
    PyClassObject      *exc_class;
    PyObject           *message;
    PyObject           *cause;
    PyObject           *context;
    PyFrameRecord      *traceback;
    int32_t             n_frames;
    int32_t             cap_frames;
} PyExceptionObject;

/* Built-in exception tags. The frontend passes one of these to
 * py_exc_new; the runtime maps each to the lazily-bootstrapped class
 * returned by py_exc_builtin_class. Keep these in sync with
 * PY_EXC_BUILTIN_NAMES in py_exc.c. */
enum {
    PY_EXC_BASE              = 0,   /* BaseException */
    PY_EXC_EXCEPTION         = 1,   /* Exception */
    PY_EXC_VALUEERROR        = 2,
    PY_EXC_TYPEERROR         = 3,
    PY_EXC_KEYERROR          = 4,
    PY_EXC_INDEXERROR        = 5,
    PY_EXC_ATTRIBUTEERROR    = 6,
    PY_EXC_RUNTIMEERROR      = 7,
    PY_EXC_STOPITERATION     = 8,
    PY_EXC_ZERODIVISIONERROR = 9,
    PY_EXC_NAMEERROR         = 10,
    PY_EXC_NOTIMPLEMENTEDERROR = 11,
    PY_EXC_ARITHMETICERROR   = 12,
    PY_EXC_LOOKUPERROR       = 13,
    PY_EXC_OSERROR           = 14,
    PY_EXC_OVERFLOWERROR     = 15,
    PY_EXC_ASSERTIONERROR    = 16,
    PY_EXC_N_BUILTIN         = 17
};

/* Lazily allocate and cache the builtin exception class for `tag`.
 * Returns a borrowed reference — the runtime holds a permanent ref on
 * every builtin class so callers need not incref. */
PyClassObject *py_exc_builtin_class(int64_t tag);

/* Allocate a PyExceptionObject wired to `cls` with `msg` as message
 * (may be NULL). Returns a new owned reference; installs header
 * type_tag = PY_TYPE_EXC. */
PyExceptionObject *py_exc_alloc(PyClassObject *cls, const char *msg);

/* Deallocation hook (called from py_decref via py_dealloc_exc). */
void py_dealloc_exc(PyObject *o);

/* Exception model is now return-code based (see py_exc.c header
 * comment). No Itanium C++ ABI symbols are exported from py_exc.c
 * anymore. */

/* ---- Helpers ----------------------------------------------------------- */

/* Return the int64 value from either a tagged int or a small heap int.
 *
 * Precondition: the object must actually fit in int64. Callers that may
 * encounter a large bignum should use py_int_to_i64 (public ABI) with the
 * overflow out-param instead. */
int64_t py_int_value_i64(PyObject *o);

/* Access typed fields. */
static inline PyObjectHeader *py_header(PyObject *o) {
    /* Only valid if !PY_IS_TAGGED_INT(o). */
    return (PyObjectHeader *)o;
}

static inline int32_t py_type_of(PyObject *o) {
    if (PY_IS_TAGGED_INT(o)) return PY_TYPE_INT;
    return py_header(o)->type_tag;
}

/* ---- Bignum helpers (Phase 2) ----------------------------------------- */

/* Allocate a raw bignum object with storage for `ndigits` base-2^32 digits.
 * Caller must fill sign + ndigits + digits[]. Does not normalize. */
PyIntObject *py_bigint_alloc(int32_t ndigits);

/* Build a bignum from an int64 value. Always returns a non-tagged
 * PyIntObject heap object — callers that want the canonical tagged form
 * should use py_int_from_i64 or py_bigint_to_pyobject. */
PyIntObject *py_bigint_from_i64(int64_t v);

/* Convert a bignum to the canonical PyObject*: a tagged int if the value
 * fits in the tagged range, else the same bignum pointer cast back to
 * PyObject*. May free `b` when collapsing to a tagged int. Always returns
 * a new reference that the caller owns. */
PyObject *py_bigint_to_pyobject(PyIntObject *b);

/* Promote any int (tagged or heap bignum) to a fresh bignum copy. Caller
 * owns the result and must py_decref it. */
PyIntObject *py_bigint_from_any(PyObject *o);

/* Schoolbook arithmetic. All return new references (or NULL on malloc fail). */
PyIntObject *py_bigint_add(const PyIntObject *a, const PyIntObject *b);
PyIntObject *py_bigint_sub(const PyIntObject *a, const PyIntObject *b);
PyIntObject *py_bigint_mul(const PyIntObject *a, const PyIntObject *b);
PyIntObject *py_bigint_neg(const PyIntObject *a);

/* Compare two bignums. Returns -1/0/+1. */
int py_bigint_cmp(const PyIntObject *a, const PyIntObject *b);

/* If the bignum fits in int64, return the value and set *overflow = 0.
 * Otherwise return 0 and set *overflow = 1. */
int64_t py_bigint_to_i64(const PyIntObject *b, int *overflow);

/* Convert to a double. Precision may be lost for very large magnitudes. */
double py_bigint_to_double(const PyIntObject *b);

/* Decimal string conversion. Returned buffer is malloc'd; caller owns and
 * must free(). Returns NULL on allocation failure. */
char *py_bigint_to_cstr(const PyIntObject *b);

/* Parse a (possibly signed) decimal string. Returns a new bignum or NULL on
 * parse / allocation failure. */
PyIntObject *py_bigint_from_cstr(const char *s);

/* Dynamic dunder helpers implemented in py_dunder.c. */
PyObject *py_int_to_str_obj(PyObject *o);
PyObject *py_user_str_dispatch(PyObject *o);

/* Bitwise ops (treat operands as two's-complement of infinite width). */
PyIntObject *py_bigint_and(const PyIntObject *a, const PyIntObject *b);
PyIntObject *py_bigint_or (const PyIntObject *a, const PyIntObject *b);
PyIntObject *py_bigint_xor(const PyIntObject *a, const PyIntObject *b);

/* Shifts by a non-negative bit count. */
PyIntObject *py_bigint_shl(const PyIntObject *a, uint64_t bits);
PyIntObject *py_bigint_shr(const PyIntObject *a, uint64_t bits);

/* base ** exp, both bignums, exp must be non-negative. Returns NULL on
 * negative exponent or alloc failure. */
PyIntObject *py_bigint_pow(const PyIntObject *base, const PyIntObject *exp);

/* Divmod — Python floor semantics: q = floor(a / b); r = a - q*b.
 * Writes new refs to *q_out and *r_out; returns 0 on success, -1 on
 * divide-by-zero or alloc failure. */
int py_bigint_divmod(const PyIntObject *a, const PyIntObject *b,
                     PyIntObject **q_out, PyIntObject **r_out);

#endif /* PY_INTERNAL_H */
