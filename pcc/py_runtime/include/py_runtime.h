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
    PY_TYPE_FILE    = 13,
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
int64_t   py_str_ord(PyObject *s);             /* first codepoint, -1 on empty/invalid */
PyObject *py_str_concat(PyObject *a, PyObject *b);
PyObject *py_str_repeat(PyObject *s, PyObject *n);
PyObject *py_str_slice(PyObject *s, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_str_index(PyObject *s, PyObject *i);    /* returns single-char str */
int64_t   py_str_eq(PyObject *a, PyObject *b);
int64_t   py_str_contains(PyObject *s, PyObject *sub);
int64_t   py_str_find(PyObject *s, PyObject *sub);   /* -1 if not found */
PyObject *py_str_upper(PyObject *s);
PyObject *py_str_lower(PyObject *s);
PyObject *py_str_strip(PyObject *s);
PyObject *py_str_split(PyObject *s, PyObject *sep);  /* returns list */
PyObject *py_str_split_maxsplit(PyObject *s, PyObject *sep, int64_t maxsplit);
PyObject *py_str_join(PyObject *sep, PyObject *list);
PyObject *py_str_replace(PyObject *s, PyObject *old, PyObject *new);
PyObject *py_str_replace_count(PyObject *s, PyObject *old, PyObject *new, int64_t maxreplace);
int64_t   py_str_startswith(PyObject *s, PyObject *prefix);
int64_t   py_str_endswith(PyObject *s, PyObject *suffix);
PyObject *py_chr_from_i64(int64_t codepoint);

/* ---- List -------------------------------------------------------------- */
PyObject *py_list_new(int64_t initial_capacity);
void      py_list_append(PyObject *lst, PyObject *item);
PyObject *py_list_get(PyObject *lst, int64_t i);     /* new ref */
void      py_list_set(PyObject *lst, int64_t i, PyObject *item);
int64_t   py_list_len(PyObject *lst);
PyObject *py_list_slice(PyObject *lst, PyObject *lo, PyObject *hi, PyObject *step);
PyObject *py_list_concat(PyObject *a, PyObject *b);
PyObject *py_list_repeat(PyObject *src, int64_t count);
void      py_list_extend(PyObject *a, PyObject *b);
void      py_list_insert(PyObject *lst, int64_t i, PyObject *item);
PyObject *py_list_pop(PyObject *lst, int64_t i);
void      py_list_remove(PyObject *lst, PyObject *item);
int64_t   py_list_contains(PyObject *lst, PyObject *item);
int64_t   py_list_index(PyObject *lst, PyObject *item);

/* ---- Dict -------------------------------------------------------------- */
PyObject *py_dict_new(void);
void      py_dict_set(PyObject *d, PyObject *k, PyObject *v);
PyObject *py_dict_get(PyObject *d, PyObject *k);     /* NULL if missing */
PyObject *py_dict_get_default(PyObject *d, PyObject *k, PyObject *def);
/* Returns 1 if k is in d, 0 otherwise. int64_t for pcc-Python ABI parity. */
int64_t   py_dict_contains(PyObject *d, PyObject *k);
/* Returns 0 on success, -1 if missing. int64_t for pcc-Python ABI parity. */
int64_t   py_dict_del(PyObject *d, PyObject *k);
int64_t   py_dict_len(PyObject *d);
PyObject *py_dict_keys(PyObject *d);                 /* list */
PyObject *py_dict_values(PyObject *d);               /* list */
PyObject *py_dict_items(PyObject *d);                /* list of tuples */

/* ---- Tuple ------------------------------------------------------------- */
PyObject *py_tuple_new(int64_t n);
void      py_tuple_set_item(PyObject *t, int64_t i, PyObject *item); /* during construction only */
PyObject *py_tuple_get(PyObject *t, int64_t i);
int64_t   py_tuple_len(PyObject *t);
PyObject *py_tuple_concat(PyObject *a, PyObject *b);
PyObject *py_tuple_slice(PyObject *t, PyObject *lo, PyObject *hi, PyObject *step);

/* ---- Set --------------------------------------------------------------- */
PyObject *py_set_new(void);
void      py_set_add(PyObject *s, PyObject *item);
/* Returns 1 if item is in the set, 0 otherwise. Returns int64_t so the
 * pcc-Python port (py_set.py) emits under pcc's default `int` lowering
 * without a type mismatch. */
int64_t   py_set_contains(PyObject *s, PyObject *item);
/* Removes item; returns 0 on success, -1 if item not present. */
int64_t   py_set_remove(PyObject *s, PyObject *item);
int64_t   py_set_len(PyObject *s);

/* ---- Generic object ops ----------------------------------------------- */
PyObject *py_obj_call(PyObject *callable, PyObject *args_tuple, PyObject *kwargs_dict);
PyObject *py_obj_getattr(PyObject *o, const char *name);
int64_t   py_obj_setattr(PyObject *o, const char *name, PyObject *v);
PyObject *py_obj_getitem(PyObject *o, PyObject *k);
int64_t   py_obj_setitem(PyObject *o, PyObject *k, PyObject *v);
int64_t   py_obj_delitem(PyObject *o, PyObject *k);
int64_t   py_obj_len(PyObject *o);
int64_t   py_obj_contains(PyObject *container, PyObject *item);
PyObject *py_str_splitlines(PyObject *s);
PyObject *py_str_splitlines_keepends(PyObject *s, int keepends);
PyObject *py_str_lstrip(PyObject *s);
PyObject *py_str_rstrip(PyObject *s);
PyObject *py_str_strip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_lstrip_chars(PyObject *s, PyObject *chars);
PyObject *py_str_rstrip_chars(PyObject *s, PyObject *chars);
int64_t   py_str_count(PyObject *s, PyObject *sub);
int64_t   py_str_isdigit(PyObject *s);
int64_t   py_str_isalpha(PyObject *s);
int64_t   py_str_isspace(PyObject *s);
int64_t   py_str_isalnum(PyObject *s);
/* ``sorted(x)`` — returns a new list with elements of ``x`` in
 * py_obj_eq / py_int_cmp order. ``x`` must be any py_obj_len /
 * py_obj_getitem-friendly container. Only numeric / string
 * element types order correctly; mixed types fall back to
 * py_obj_hash order (stable but not Python-equivalent). */
PyObject *py_obj_sorted(PyObject *x);
/* int64_t returns for pcc-Python ABI parity (default-int lowering). */
int64_t   py_obj_truthy(PyObject *o);                /* 0 or 1 */
int64_t   py_obj_type_tag(PyObject *o);
int64_t   py_obj_eq(PyObject *a, PyObject *b);
int64_t   py_obj_lt(PyObject *a, PyObject *b);
int64_t   py_obj_le(PyObject *a, PyObject *b);
int64_t   py_obj_gt(PyObject *a, PyObject *b);
int64_t   py_obj_ge(PyObject *a, PyObject *b);
int64_t   py_obj_hash(PyObject *o);
PyObject *py_obj_repr(PyObject *o);
PyObject *py_obj_str(PyObject *o);
int64_t   py_obj_isinstance(PyObject *o, PyObject *cls);

/* ---- File I/O ---------------------------------------------------------- */
PyObject *py_file_open(PyObject *path, PyObject *mode);
PyObject *py_file_read_all(PyObject *file);
PyObject *py_file_write(PyObject *file, PyObject *text);
void      py_file_close(PyObject *file);

/* ---- Printing ---------------------------------------------------------- */
void py_print(PyObject *o);                 /* writes repr + "\n" to stdout */
void py_print_many(PyObject *args_tuple, PyObject *sep, PyObject *end);
PyObject *py_sys_stdout_write(PyObject *text);
PyObject *py_sys_stderr_write(PyObject *text);

/* ---- Process startup --------------------------------------------------- */
/* Borrow the host process argc/argv so compiled Python programs can
 * observe their command-line arguments (directly or through CPython
 * fallback modules such as argparse). */
void py_set_program_args(int argc, const char **argv);
int64_t py_program_argc(void);
const char *py_program_argv(int64_t index);
void py_process_exit(int64_t code);
PyObject *py_sys_executable_str(void);
PyObject *py_subprocess_check_output(PyObject *argv);
int64_t py_subprocess_run(PyObject *argv, int32_t capture_output);
PyObject *py_sysconfig_get_config_var(PyObject *name);
PyObject *py_os_listdir(PyObject *path);
PyObject *py_shlex_split(PyObject *text);
PyObject *py_shutil_which(PyObject *name);
PyObject *py_tempdir_new(PyObject *prefix);
void py_tempdir_cleanup(PyObject *path);

/* ---- Narrow os.path subset --------------------------------------------- */
/* Native helpers used by the Python frontend for the no-libpython subset of
 * ``os.path``. ``join`` expects a list/tuple of path components and returns
 * a pcc string; ``basename`` returns the last path component; ``exists``
 * returns 0/1. */
PyObject *py_os_getenv(PyObject *key, PyObject *default_value);
PyObject *py_os_putenv(PyObject *key, PyObject *value);
PyObject *py_os_unsetenv(PyObject *key);
PyObject *py_os_path_join(PyObject *parts);
PyObject *py_os_path_basename(PyObject *path);
PyObject *py_os_path_dirname(PyObject *path);
int       py_os_path_exists(PyObject *path);
int       py_os_path_isfile(PyObject *path);
int       py_os_path_isdir(PyObject *path);
PyObject *py_os_path_getmtime(PyObject *path);
PyObject *py_os_path_abspath(PyObject *path);
/* Low-level platform-portable stat classifier — returns 0=missing,
 * 1=regular file, 2=directory, 3=other. The pcc-Python port of
 * py_os_path uses this to keep stat-buffer layout out of the
 * pcc-Python source. */
int32_t   py_path_stat_kind(const char *path);
/* Last-modification time as IEEE-754 seconds-since-epoch double; NaN
 * if stat() fails. Hides struct timespec layout from the pcc-Python
 * port. */
double      py_path_stat_mtime(const char *path);
/* Current working directory as a NUL-terminated cstring. Pointer is
 * borrowed (thread-local static buffer); copy before the next call. */
const char *py_path_getcwd(void);
/* Boxed `sys.platform` value — same value Python's sys.platform
 * exposes (e.g. "darwin", "linux"). Picked at C compile time, no
 * libpython dependency. */
PyObject   *py_sys_platform_str(void);
/* Boxed `platform.machine()` value, e.g. "arm64" or "x86_64". */
PyObject   *py_platform_machine_str(void);
/* Boxed `platform.release()` value from uname(2). */
PyObject   *py_platform_release_str(void);
/* Boxed `os.getcwd()` value. NULL if getcwd() fails. */
PyObject   *py_os_getcwd_str(void);
/* `os.access(path, mode)` — returns 1 (accessible) / 0 (not). */
int32_t     py_os_access(PyObject *path, int32_t mode);

/* ---- Exceptions (Phase 3) --------------------------------------------- */

/* Install `exc` as the thread-local current exception. Return-code
 * exception model: py_raise returns normally; callers (codegen-emitted
 * code) must check py_err_occurred() after each call that could raise
 * and branch to an error-handler / function epilogue. */
void py_raise(PyObject *exc);

/* Return the active exception (borrowed), or NULL if none is set. */
PyObject *py_current_exception(void);

/* 1 if an exception is currently pending in the TLS slot, else 0.
 * Used by codegen-emitted post-call checks in the return-code model.
 * Returns int64_t so the pcc-Python port (py_exc_tls.py) can emit it
 * under pcc's default `int` lowering without a type mismatch. */
int64_t py_err_occurred(void);

/* Drop the thread-local current-exception slot (decref + NULL). */
void py_clear_exception(void);

/* Allocate a new builtin exception with the given PY_EXC_* tag and
 * message. Returns a new owned reference; tag outside
 * [0, PY_EXC_N_BUILTIN) falls back to Exception. */
PyObject *py_exc_new(int64_t type_tag, const char *msg);

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
int64_t py_exc_matches(PyObject *exc, PyObject *type);

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
int   py_cpy_setattr(void *obj, const char *name, void *value);
/* Consume any pending unhandled CPython exception at program exit and
 * return the corresponding process status.
 *
 * - no pending exception: returns 0
 * - SystemExit(None) / SystemExit(0): returns 0 and clears it
 * - SystemExit(n): returns n and clears it
 * - other exceptions: prints via CPython's traceback printer, clears
 *   the error indicator, and returns 1
 */
int   py_cpy_main_exitcode(void);
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
/* Best-effort CPython PyObject* -> pcc PyObject* converter. Handles
 * None/bool/int/float/str/list/tuple/dict/set recursively; unsupported
 * foreign objects fall back to str(obj). Returns a new pcc-owned ref. */
PyObject *py_cpy_to_pcc_obj(void *cpy_obj);
void  py_cpy_decref(void *obj);
void  py_cpy_incref(void *obj);
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

/* Call ``callable(*args, **kwargs_dict)`` where ``kwargs_dict`` is
 * already a CPython mapping object. Positional refs are stolen into the
 * tuple; ``kwargs_dict`` is borrowed. Returns a new owned ref or NULL
 * on error. */
void   *py_cpy_call_kwdict(void *callable,
                           int64_t n_pos, void **argv,
                           void *kwargs_dict);
void   *py_cpy_call_kwdict_plus(void *callable,
                                int64_t n_pos, void **argv,
                                int64_t n_kw,
                                const char **kw_names, void **kw_vals,
                                void *kwargs_dict);
void   *py_cpy_call_list_kwdict(void *callable,
                                PyObject *args,
                                void *kwargs_dict);

/* Dynamic slice dispatch for pcc-native objects whose static type is not
 * specific enough at compile time. */
PyObject *py_obj_slice(PyObject *obj, PyObject *lo, PyObject *hi, PyObject *step);

/* Call ``callable(*args)`` where ``args`` is a pcc list / tuple. The
 * helper converts the container to a CPython tuple via
 * ``py_cpy_from_pcc_obj`` and dispatches through ``PyObject_Call``.
 * Returns a new owned ref or NULL on error. */
void   *py_cpy_call_list(void *callable, PyObject *args);

/* Wrap a pcc user FuncDef's function pointer as a CPython callable so
 * it can be passed to ``sorted(..., key=<fn>)`` / ``re.sub(pat, <fn>,
 * text)`` / any other CPython API that consumes a ``PyObject *``
 * callable. ``fn_ptr`` must target a pcc function with signature
 * ``CPyObject *(CPyObject *, ...)`` — arity-specific variants
 * dispatch via per-arity trampoline + PyMethodDef. */
void   *py_cpy_wrap_pcc_0arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_1arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_2arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_3arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_4arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_5arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_6arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_7arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_8arg(void *fn_ptr);
void   *py_cpy_wrap_pcc_9arg(void *fn_ptr);

/* ---- Substrate primitives (Phase 4a) ---------------------------------- */
/*
 * Low-level memory-access helpers used by pcc-Python ports of runtime
 * modules. Each helper is a one-liner; cc inlines them, pcc emits them
 * directly. They give pcc-Python C-struct-equivalent authoring
 * (malloc, free, offset-based load/store) without requiring native
 * raw-pointer syntax in the Python subset.
 */
void   *py_mem_alloc(size_t bytes);
void    py_mem_free(void *p);
void   *py_mem_zero(void *p, size_t bytes);
void   *py_mem_copy(void *dst, const void *src, size_t bytes);
int64_t py_mem_load_i64(const void *p, int64_t offset);
int32_t py_mem_load_i32(const void *p, int64_t offset);
int8_t  py_mem_load_i8(const void *p, int64_t offset);
void   *py_mem_load_ptr(const void *p, int64_t offset);
void    py_mem_store_i64(void *p, int64_t offset, int64_t v);
void    py_mem_store_i32(void *p, int64_t offset, int32_t v);
void    py_mem_store_i8(void *p, int64_t offset, int8_t v);
void    py_mem_store_ptr(void *p, int64_t offset, void *v);
void   *py_mem_ptr_add(void *p, int64_t offset);
int32_t py_mem_ptr_is_tagged_int(const void *p);
void   *py_mem_null_ptr(void);
int32_t py_mem_ptr_is_null(const void *p);
int32_t py_mem_ptr_eq(const void *a, const void *b);

/* Raw TLS-slot accessors for the exception runtime. Lives in
 * py_substrate.c so the cc-compiled C helpers (py_exc_tls.c) and the
 * pcc-Python port (py_exc_tls.py) can both reach it via extern. */
void   *py_tls_exc_get(void);
void    py_tls_exc_set(void *exc);

/* Function-call accessors for the three immortal singletons. These are
 * retained for the C runtime path; pcc-Python ports use pcc.unsafe
 * global intrinsics to read the exported globals directly. */
void   *py_subs_none(void);
void   *py_subs_true(void);
void   *py_subs_false(void);

/* Legacy function-style accessors for the builtin exception tables. */
const char *py_subs_exc_name(int32_t tag);
int32_t     py_subs_exc_parent(int32_t tag);
int32_t     py_subs_exc_n_builtin(void);

/* Legacy function-style accessors for the builtin exception cache. */
void       *py_subs_exc_cache_get(int32_t tag);
void        py_subs_exc_cache_set(int32_t tag, void *cls);

/* py_set_dummy tombstone sentinel accessor (value of the global
 * const pointer). Lives in substrate so py_set.c can be replaced. */
void       *py_subs_set_dummy(void);

/* OS substrate primitives for py_os.py. Thin wrappers around libc so
 * the pcc-Python port does not need native getenv/setenv/access
 * syntax. */
const char *py_subs_getenv(const char *name);
int32_t     py_subs_setenv(const char *name, const char *value);
int32_t     py_subs_unsetenv(const char *name);
int32_t     py_subs_path_exists(const char *path);
int64_t     py_subs_cstr_len(const char *s);
int8_t      py_subs_cstr_at(const char *s, int64_t i);
void       *py_subs_realloc(void *p, size_t bytes);

/* stdio substrate primitives for py_print.py. Thin write() wrapper
 * returns the number of bytes actually written. */
int64_t     py_subs_write_fd(int32_t fd, const void *buf, int64_t n);

/* String substrate for py_class.py method/field name lookup. */
int32_t     py_subs_strcmp(const char *a, const char *b);

/* Type-tag allocator counter for user-defined classes. Substrate hosts
 * it so the counter survives a swap of py_class.c for py_class.py. */
int32_t     py_subs_alloc_user_tag(void);

/* Lazily-bootstrapped root "object" class used as the universal MRO
 * tail. Hosted in substrate so a swap doesn't lose the once-only
 * static-storage object. Returns a PyClassObject*. */
void       *py_subs_object_root(void);

#endif /* PY_RUNTIME_H */
