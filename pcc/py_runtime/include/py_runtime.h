/* pcc/py_runtime/include/py_runtime.h */
#ifndef PY_RUNTIME_H
#define PY_RUNTIME_H

#include <stdint.h>
#include <stddef.h>

/* Opaque PyObject; concrete definition lives in py_obj.c */
typedef struct PyObject PyObject;

/* Type tag values — used in PyObject header and tagged int */
enum {
    PY_TYPE_NONE    = 0,
    PY_TYPE_BOOL    = 1,
    PY_TYPE_INT     = 2,    /* bignum; non-tagged form */
    PY_TYPE_FLOAT   = 3,
    PY_TYPE_STR     = 4,
    PY_TYPE_LIST    = 5,
    PY_TYPE_DICT    = 6,
    PY_TYPE_TUPLE   = 7,
    PY_TYPE_SET     = 8,
    PY_TYPE_FUNC    = 9,
    PY_TYPE_CLASS   = 10,
    PY_TYPE_INSTANCE= 11,
    PY_TYPE_EXC     = 12,
    PY_TYPE_USER    = 100   /* user-defined classes >= this */
};

/* Every PyObject has this header prefix. */
typedef struct {
    int64_t refcount;
    int32_t  type_tag;
    int32_t  flags;        /* bit 0 = immortal, bit 1 = gc-tracked, ... */
} PyObjectHeader;

/* ---- INCREF/DECREF ----------------------------------------------------- */
void py_incref(PyObject *o);
void py_decref(PyObject *o);

/* ---- None -------------------------------------------------------------- */
extern PyObject *const py_None;

/* ---- Bool -------------------------------------------------------------- */
extern PyObject *const py_True;
extern PyObject *const py_False;
PyObject *py_bool_from_bit(int b);           /* b: 0 or 1 */

/* ---- Tagged int (fast path) + bignum (slow path) ---------------------- */
/* Tagged: low bit = 1 means tagged int; real value is (val >> 1).
 * Non-tagged: regular PyObject* with PY_TYPE_INT header. */
PyObject *py_int_from_i64(int64_t v);
int64_t   py_int_to_i64(PyObject *o, int *overflow);   /* returns 0 on overflow */
PyObject *py_int_add(PyObject *a, PyObject *b);
PyObject *py_int_sub(PyObject *a, PyObject *b);
PyObject *py_int_mul(PyObject *a, PyObject *b);
PyObject *py_int_floordiv(PyObject *a, PyObject *b);   /* Python floor semantics */
PyObject *py_int_truediv(PyObject *a, PyObject *b);    /* returns float */
PyObject *py_int_mod(PyObject *a, PyObject *b);        /* Python sign semantics */
PyObject *py_int_pow(PyObject *a, PyObject *b);
PyObject *py_int_neg(PyObject *a);
PyObject *py_int_and(PyObject *a, PyObject *b);
PyObject *py_int_or(PyObject *a, PyObject *b);
PyObject *py_int_xor(PyObject *a, PyObject *b);
PyObject *py_int_shl(PyObject *a, PyObject *b);
PyObject *py_int_shr(PyObject *a, PyObject *b);
int       py_int_cmp(PyObject *a, PyObject *b);        /* -1, 0, 1 */
/* ``int(str)`` / ``int(str, base)`` — returns a tagged or heap int
 * matching strtoll(); on parse error, returns NULL. Base 0 auto-
 * detects 0x / 0o / 0b prefixes. Use base 10 for Python's default. */
PyObject *py_int_from_cstr(const char *s, int base);

/* ---- Float ------------------------------------------------------------- */
PyObject *py_float_from_f64(double v);
double    py_float_to_f64(PyObject *o);
PyObject *py_float_add(PyObject *a, PyObject *b);
/* ... sub, mul, div, mod, pow, neg, cmp ... */

/* ---- Str --------------------------------------------------------------- */
PyObject *py_str_new(const char *utf8, int64_t byte_len);
int64_t   py_str_len(PyObject *s);             /* in codepoints */
int64_t   py_str_byte_len(PyObject *s);        /* in UTF-8 bytes */
const char *py_str_utf8(PyObject *s);          /* borrowed, NUL-terminated */
PyObject *py_str_concat(PyObject *a, PyObject *b);
PyObject *py_str_repeat(PyObject *s, PyObject *n);
PyObject *py_str_slice(PyObject *s, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_str_index(PyObject *s, PyObject *i);    /* returns single-char str */
int       py_str_eq(PyObject *a, PyObject *b);
int       py_str_contains(PyObject *s, PyObject *sub);
int64_t   py_str_find(PyObject *s, PyObject *sub);   /* -1 if not found */
PyObject *py_str_upper(PyObject *s);
PyObject *py_str_lower(PyObject *s);
PyObject *py_str_strip(PyObject *s);
PyObject *py_str_split(PyObject *s, PyObject *sep);  /* returns list */
PyObject *py_str_join(PyObject *sep, PyObject *list);
PyObject *py_str_replace(PyObject *s, PyObject *old, PyObject *new);
int       py_str_startswith(PyObject *s, PyObject *prefix);
int       py_str_endswith(PyObject *s, PyObject *suffix);

/* ---- List -------------------------------------------------------------- */
PyObject *py_list_new(int64_t initial_capacity);
void      py_list_append(PyObject *lst, PyObject *item);
PyObject *py_list_get(PyObject *lst, int64_t i);     /* new ref */
void      py_list_set(PyObject *lst, int64_t i, PyObject *item);
int64_t   py_list_len(PyObject *lst);
PyObject *py_list_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_list_concat(PyObject *a, PyObject *b);
void      py_list_extend(PyObject *a, PyObject *b);
void      py_list_insert(PyObject *lst, int64_t i, PyObject *item);
PyObject *py_list_pop(PyObject *lst, int64_t i);
void      py_list_remove(PyObject *lst, PyObject *item);
int       py_list_contains(PyObject *lst, PyObject *item);
int64_t   py_list_index(PyObject *lst, PyObject *item);

/* ---- Dict -------------------------------------------------------------- */
PyObject *py_dict_new(void);
void      py_dict_set(PyObject *d, PyObject *k, PyObject *v);
PyObject *py_dict_get(PyObject *d, PyObject *k);     /* NULL if missing */
PyObject *py_dict_get_default(PyObject *d, PyObject *k, PyObject *def);
int       py_dict_contains(PyObject *d, PyObject *k);
int       py_dict_del(PyObject *d, PyObject *k);     /* returns -1 on missing */
int64_t   py_dict_len(PyObject *d);
PyObject *py_dict_keys(PyObject *d);                 /* list */
PyObject *py_dict_values(PyObject *d);               /* list */
PyObject *py_dict_items(PyObject *d);                /* list of tuples */

/* ---- Tuple ------------------------------------------------------------- */
PyObject *py_tuple_new(int64_t n);
void      py_tuple_set_item(PyObject *t, int64_t i, PyObject *item); /* during construction only */
PyObject *py_tuple_get(PyObject *t, int64_t i);
int64_t   py_tuple_len(PyObject *t);

/* ---- Set --------------------------------------------------------------- */
PyObject *py_set_new(void);
void      py_set_add(PyObject *s, PyObject *item);
int       py_set_contains(PyObject *s, PyObject *item);
int       py_set_remove(PyObject *s, PyObject *item);
int64_t   py_set_len(PyObject *s);

/* ---- Generic object ops ----------------------------------------------- */
PyObject *py_obj_call(PyObject *callable, PyObject *args_tuple, PyObject *kwargs_dict);
PyObject *py_obj_getattr(PyObject *o, const char *name);
int       py_obj_setattr(PyObject *o, const char *name, PyObject *v);
PyObject *py_obj_getitem(PyObject *o, PyObject *k);
int       py_obj_setitem(PyObject *o, PyObject *k, PyObject *v);
int64_t   py_obj_len(PyObject *o);
int       py_obj_contains(PyObject *container, PyObject *item);
PyObject *py_str_splitlines(PyObject *s);
PyObject *py_str_splitlines_keepends(PyObject *s, int keepends);
PyObject *py_str_lstrip(PyObject *s);
PyObject *py_str_rstrip(PyObject *s);
PyObject *py_str_strip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_lstrip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_rstrip_chars(PyObject *s, PyObject *chars);
int64_t   py_str_count(PyObject *s, PyObject *sub);
int       py_str_isdigit(PyObject *s);
int       py_str_isalpha(PyObject *s);
int       py_str_isspace(PyObject *s);
int       py_str_isalnum(PyObject *s);
/* ``sorted(x)`` — returns a new list with elements of ``x`` in
 * py_obj_eq / py_int_cmp order. ``x`` must be any py_obj_len /
 * py_obj_getitem-friendly container. Only numeric / string
 * element types order correctly; mixed types fall back to
 * py_obj_hash order (stable but not Python-equivalent). */
PyObject *py_obj_sorted(PyObject *x);
int       py_obj_truthy(PyObject *o);                /* 0 or 1 */
int       py_obj_eq(PyObject *a, PyObject *b);
int64_t   py_obj_hash(PyObject *o);
PyObject *py_obj_repr(PyObject *o);
PyObject *py_obj_str(PyObject *o);
int       py_obj_isinstance(PyObject *o, PyObject *cls);

/* ---- Printing ---------------------------------------------------------- */
void py_print(PyObject *o);                 /* writes repr + "\n" to stdout */
void py_print_many(PyObject *args_tuple, PyObject *sep, PyObject *end);

/* ---- Exceptions (Phase 3) --------------------------------------------- */

/* Throw `exc` via the Itanium C++ ABI `__cxa_throw` under the hood.
 * Before the throw we install exc as the thread-local current_exception
 * so landingpads (and `py_current_exception`) can retrieve it once
 * they've caught the C++ exception. Does not return — marked noreturn
 * in the runtime; callers in codegen should emit `unreachable` after
 * the call. */
void py_raise(PyObject *exc) __attribute__((noreturn));

/* Return the active exception (borrowed), or NULL if none is set. Used
 * by landingpads the instant after a catch to bind `as` variables. */
PyObject *py_current_exception(void);

/* Drop the thread-local current-exception slot (decref + NULL). */
void py_clear_exception(void);

/* Allocate a new builtin exception with the given PY_EXC_* tag and
 * message. Returns a new owned reference; tag outside
 * [0, PY_EXC_N_BUILTIN) falls back to Exception. */
PyObject *py_exc_new(int32_t type_tag, const char *msg);

/* Allocate a user-defined exception using a pre-existing class object.
 * `cls` must be a PyClassObject*; `msg` may be NULL. Returns a new
 * owned reference. */
PyObject *py_exc_new_with_class(PyObject *cls, const char *msg);

/* `raise X from Y` chaining: set `exc.__cause__ = cause`. `exc` and
 * `cause` are both borrowed (ref is acquired on cause). Safe to call
 * with cause = NULL (clears existing cause). */
void py_exc_set_cause(PyObject *exc, PyObject *cause);

/* Implicit context chain — used by codegen when `raise Y` fires inside
 * an active `except` clause. `exc.__context__ = context`. */
void py_exc_set_context(PyObject *exc, PyObject *context);

/* Borrowed reference to the message PyStrObject stashed on an
 * exception by py_exc_new. Used by py_obj_str to implement ``str(e)``
 * on exception instances. Returns NULL if exc has no message. */
PyObject *py_exc_get_message(PyObject *exc);

/* Walk `exc`'s class MRO and test whether `type` appears. Either arg
 * may be an exception instance (we auto-project to the class) or a
 * PyClassObject*. Returns 1 on match, 0 otherwise. */
int py_exc_matches(PyObject *exc, PyObject *type);

/* Append a PyFrameRecord to the exception's traceback. `func_name` and
 * `filename` are borrowed — the caller must guarantee they outlive the
 * exception (typically static rodata strings emitted by the compiler). */
void py_exc_append_frame(PyObject *exc,
                         const char *func_name,
                         const char *filename,
                         int32_t line);

/* Format exception traceback-style text and write to stdout. Used by
 * the unhandled-exception handler at program top level. */
void py_exc_print_unhandled(PyObject *exc);

/* ---- GC ---------------------------------------------------------------- */
void py_gc_init(void);
void py_gc_collect(void);
void py_gc_track(PyObject *o);
void py_gc_untrack(PyObject *o);

/* ---- Phase 4: CPython C-API fallback ----------------------------------- */
/* Opaque CPython ``PyObject *`` type — distinct from pcc's own PyObject*
 * and exposed as ``void *`` at the codegen ABI boundary. All CPython
 * pointers returned from these helpers own a reference that the caller
 * must release via :c:func:`py_cpy_decref` (the codegen emits the
 * decref when a dyn-typed value falls out of scope). */
void  py_cpy_ensure_init(void);
void *py_cpy_import(const char *name);
void *py_cpy_getattr(void *obj, const char *name);
void *py_cpy_call_noargs(void *callable);
void *py_cpy_call1(void *callable, void *a);
void *py_cpy_call2(void *callable, void *a, void *b);
void *py_cpy_call3(void *callable, void *a, void *b, void *c);
/* Arbitrary-arity call. ``argv[0..n)`` must each own a reference; the
 * callee steals each reference (via PyTuple_SetItem) whether the call
 * succeeds or fails. Returns a new CPython reference, or NULL. */
void *py_cpy_call_argv(void *callable, int64_t n, void **argv);
int64_t py_cpy_len(void *obj);
void   *py_cpy_getitem(void *obj, void *key);
int     py_cpy_setitem(void *obj, void *key, void *value);
int     py_cpy_truthy(void *obj);
void   *py_cpy_iter(void *obj);
void   *py_cpy_iter_next(void *it);
PyObject *py_cpy_to_pcc_str(void *cpy_obj);
void  py_cpy_decref(void *obj);
/* pcc <-> CPython scalar marshalling. */
void   *py_cpy_from_i64(int64_t value);
int64_t py_cpy_to_i64(void *obj);
void   *py_cpy_from_f64(double value);
double  py_cpy_to_f64(void *obj);
void   *py_cpy_from_pccstr(PyObject *s);
/* Universal pcc PyObject → CPython PyObject* converter. Rebuilds the
 * object by recursing on lists / tuples / dicts so CPython APIs called
 * from pcc-emitted code receive real CPython containers, not pcc-
 * internal ones. Returns NULL on error. Caller owns the new ref. */
void   *py_cpy_from_pcc_obj(PyObject *o);

/* Positional + keyword call. ``argv[0..n_pos)`` refs are stolen into
 * the positional tuple (caller must not decref). ``kw_vals`` are
 * borrowed by PyDict_SetItemString so the caller retains ownership.
 * ``kw_names`` are NUL-terminated C strings (static lifetime).
 * Returns a new owned ref or NULL on error. */
void   *py_cpy_call_kw(void *callable,
                       int64_t n_pos, void **argv,
                       int64_t n_kw, const char **kw_names, void **kw_vals);

#endif /* PY_RUNTIME_H */
