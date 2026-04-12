/* pcc/py_runtime/src/py_exc.c
 *
 * Phase 3 exception machinery for pcc's Python runtime.
 *
 * Strategy: piggy-back on the Itanium C++ ABI personality
 * (__gxx_personality_v0) and __cxa_throw. The rationale is pragmatic —
 * clang/gcc ship the ABI + libunwind + libc++abi / libsupc++ on every
 * platform pcc targets (macOS / Linux), so we get correct DWARF unwind
 * tables, personality dispatch, landing-pad filtering, and stack-frame
 * cleanup for free.
 *
 * Wire protocol between this module and codegen/layer1.py:
 *   - codegen emits landingpads as
 *       landingpad { ptr, i32 } catch ptr @py_exception_typeinfo
 *     Because Itanium matches on the typeinfo pointer, landingpads
 *     always select the single `py_exception_typeinfo` object defined
 *     below. Subclass discrimination (e.g. `except ValueError`) happens
 *     AFTER the catch by calling `py_exc_matches` on the instance.
 *   - codegen sets every user function's `personality` field to
 *     `@__gxx_personality_v0`.
 *   - `py_raise(exc)` stashes `exc` in a thread-local slot and calls
 *     `__cxa_throw(exc, &py_exception_typeinfo, NULL)`. Declared
 *     `noreturn` — codegen emits `unreachable` after the `call`.
 *   - Inside a landingpad, codegen calls `__cxa_begin_catch` on the
 *     unwind exception pointer (that's the first element of the
 *     landingpad struct), then reads `py_current_exception()` to rebind
 *     `as` variables, executes the handler, and calls `__cxa_end_catch`
 *     before leaving the catch block.
 *
 * Upstream references:
 *   - https://llvm.org/docs/ExceptionHandling.html (Itanium unwind)
 *   - Itanium C++ ABI §15 (exception handling) — specifically the
 *     __cxa_throw / __cxa_begin_catch / __cxa_end_catch trio and the
 *     pad_offset/action-table encoding used by the personality fn.
 *   - /private/tmp/cpython-3.13.x/Python/errors.c (traceback growth
 *     model, chained __cause__/__context__).
 */

#include "py_internal.h"
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --------------------------------------------------------------------- */
/* ABI glue: __cxa_throw + typeinfo                                      */
/* --------------------------------------------------------------------- */

/* Declared noreturn to let callers (py_raise) emit `unreachable`. */
extern void __cxa_throw(void *thrown_exc, void *tinfo, void (*dest)(void *))
    __attribute__((noreturn));

/* The typeinfo sentinel. At the ABI level, __cxa_throw stores this
 * pointer (by value) into the unwinder's exception record; the
 * personality function compares it to the typeinfo referenced by each
 * landingpad's `catch` clause. We define it as a zero-initialised
 * `void*`-sized object so its address is a stable symbol. The exact
 * byte contents are never inspected — only the pointer identity. */
static const void *const py_exception_typeinfo_storage = NULL;
const void *const py_exception_typeinfo = &py_exception_typeinfo_storage;

/* --------------------------------------------------------------------- */
/* Thread-local current-exception slot                                   */
/* --------------------------------------------------------------------- */
/* Itanium's __cxa_get_globals returns per-thread storage including the
 * in-flight exception, but its layout is private — the public ABI is
 * __cxa_begin_catch/end_catch only. For pcc we keep a parallel TLS slot
 * so the runtime (and handler bodies) can retrieve the exception
 * without ABI-private trickery.
 *
 * The slot holds an owned reference — py_raise acquires, py_clear and
 * py_clear_exception release. A non-NULL slot transiting py_raise into
 * the thrown state is intentional: landingpads use
 * py_current_exception() to bind `as` vars without needing to unpack
 * the unwind header. */
static _Thread_local PyObject *g_current_exc = NULL;

/* --------------------------------------------------------------------- */
/* Built-in exception class table                                        */
/* --------------------------------------------------------------------- */

/* Display names in declaration order. Keep in sync with the PY_EXC_*
 * enum in py_internal.h. */
static const char *const PY_EXC_BUILTIN_NAMES[PY_EXC_N_BUILTIN] = {
    "BaseException",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "RuntimeError",
    "StopIteration",
    "ZeroDivisionError",
    "NameError",
    "NotImplementedError",
    "ArithmeticError",
    "LookupError",
    "OSError",
    "OverflowError",
    "AssertionError",
};

/* Parent-chain table. Each entry is the PY_EXC_* tag of the builtin
 * class's direct base. A value of -1 means no base (i.e. this is
 * BaseException). The resulting MRO — computed on bootstrap by the C3
 * linearizer already present in py_class.c — matches CPython's order
 * for the subset we expose. */
static const int32_t PY_EXC_PARENT[PY_EXC_N_BUILTIN] = {
    [PY_EXC_BASE]              = -1,
    [PY_EXC_EXCEPTION]         = PY_EXC_BASE,
    [PY_EXC_VALUEERROR]        = PY_EXC_EXCEPTION,
    [PY_EXC_TYPEERROR]         = PY_EXC_EXCEPTION,
    [PY_EXC_LOOKUPERROR]       = PY_EXC_EXCEPTION,
    [PY_EXC_KEYERROR]          = PY_EXC_LOOKUPERROR,
    [PY_EXC_INDEXERROR]        = PY_EXC_LOOKUPERROR,
    [PY_EXC_ATTRIBUTEERROR]    = PY_EXC_EXCEPTION,
    [PY_EXC_RUNTIMEERROR]      = PY_EXC_EXCEPTION,
    [PY_EXC_STOPITERATION]     = PY_EXC_EXCEPTION,
    [PY_EXC_ARITHMETICERROR]   = PY_EXC_EXCEPTION,
    [PY_EXC_ZERODIVISIONERROR] = PY_EXC_ARITHMETICERROR,
    [PY_EXC_OVERFLOWERROR]     = PY_EXC_ARITHMETICERROR,
    [PY_EXC_NAMEERROR]         = PY_EXC_EXCEPTION,
    [PY_EXC_NOTIMPLEMENTEDERROR] = PY_EXC_RUNTIMEERROR,
    [PY_EXC_OSERROR]           = PY_EXC_EXCEPTION,
    [PY_EXC_ASSERTIONERROR]    = PY_EXC_EXCEPTION,
};

/* Cache slot per tag. Populated lazily on first access; never freed —
 * the runtime keeps a permanent reference on each. */
static PyClassObject *g_exc_classes[PY_EXC_N_BUILTIN] = {0};

/* The P3-a agent exposes py_class_new but Phase 3 may not yet have
 * landed py_class.c. To keep py_exc.c linkable standalone, we fall
 * through to a minimal "class stub" when py_class_new is unavailable.
 * At link time libpy_runtime.a will resolve py_class_new from
 * py_class.c; until then the weak symbol keeps a sane default.
 *
 * NOTE: the weak-reference trick is a compile-time convenience. Once
 * py_class.c lands, the strong definition wins and this stub is
 * effectively dead. */
__attribute__((weak))
PyClassObject *py_class_new(const char *name,
                            PyClassObject **bases, int32_t n_bases,
                            const char **field_names, int32_t n_fields) {
    (void)bases; (void)n_bases; (void)field_names; (void)n_fields;
    PyClassObject *cls = (PyClassObject *)calloc(1, sizeof(PyClassObject));
    if (cls == NULL) return NULL;
    cls->h.refcount = 1;
    cls->h.type_tag = PY_TYPE_CLASS;
    cls->h.flags    = 0;
    cls->name = name;
    cls->n_bases = 0;
    cls->bases   = NULL;
    cls->n_mro   = 1;
    cls->mro     = (struct PyClassObject **)calloc(1, sizeof(PyClassObject *));
    if (cls->mro) cls->mro[0] = cls;
    cls->n_methods = 0;
    cls->methods   = NULL;
    cls->n_fields  = 0;
    cls->field_names = NULL;
    cls->instance_size = (int32_t)sizeof(PyExceptionObject);
    cls->type_tag_alloc = PY_TYPE_EXC;
    return cls;
}

/* Build or return the cached builtin class for `tag`. Returns a
 * borrowed reference — the runtime pins every builtin permanently. */
PyClassObject *py_exc_builtin_class(int32_t tag) {
    if (tag < 0 || tag >= PY_EXC_N_BUILTIN) {
        tag = PY_EXC_EXCEPTION;
    }
    if (g_exc_classes[tag] != NULL) {
        return g_exc_classes[tag];
    }
    /* Recursively materialise the parent chain so MRO is correct. */
    int32_t parent = PY_EXC_PARENT[tag];
    PyClassObject *base = NULL;
    if (parent >= 0) {
        base = py_exc_builtin_class(parent);
    }
    PyClassObject *bases_arr[1];
    int32_t n_bases = 0;
    if (base != NULL) {
        bases_arr[0] = base;
        n_bases = 1;
    }
    PyClassObject *cls = py_class_new(
        PY_EXC_BUILTIN_NAMES[tag],
        n_bases ? bases_arr : NULL, n_bases,
        /*field_names=*/NULL, /*n_fields=*/0
    );
    if (cls != NULL) {
        /* Pin permanently — the runtime never releases builtin classes. */
        cls->h.flags |= PY_FLAG_IMMORTAL;
        g_exc_classes[tag] = cls;
    }
    return cls;
}

/* --------------------------------------------------------------------- */
/* Exception-object construction                                          */
/* --------------------------------------------------------------------- */

PyExceptionObject *py_exc_alloc(PyClassObject *cls, const char *msg) {
    PyExceptionObject *e = (PyExceptionObject *)calloc(
        1, sizeof(PyExceptionObject));
    if (e == NULL) return NULL;
    e->h.refcount = 1;
    e->h.type_tag = PY_TYPE_EXC;
    e->h.flags    = 0;
    if (cls == NULL) {
        cls = py_exc_builtin_class(PY_EXC_EXCEPTION);
    }
    /* Classes are usually immortal (builtins) or strongly referenced by
     * the module's class-decl globals; we still py_incref to be
     * defensive against user-defined exceptions that might be gc'd. */
    py_incref((PyObject *)cls);
    e->exc_class = cls;
    if (msg != NULL) {
        PyObject *s = py_str_new(msg, (int64_t)strlen(msg));
        e->message = s;   /* owned ref from py_str_new */
    } else {
        py_incref(py_None);
        e->message = py_None;
    }
    e->cause      = NULL;
    e->context    = NULL;
    e->traceback  = NULL;
    e->n_frames   = 0;
    e->cap_frames = 0;
    return e;
}

PyObject *py_exc_new(int32_t type_tag, const char *msg) {
    PyClassObject *cls = py_exc_builtin_class(type_tag);
    PyExceptionObject *e = py_exc_alloc(cls, msg);
    return (PyObject *)e;
}

PyObject *py_exc_new_with_class(PyObject *cls, const char *msg) {
    if (cls == NULL || py_type_of(cls) != PY_TYPE_CLASS) {
        return py_exc_new(PY_EXC_EXCEPTION, msg);
    }
    PyExceptionObject *e = py_exc_alloc((PyClassObject *)cls, msg);
    return (PyObject *)e;
}

/* --------------------------------------------------------------------- */
/* Chaining / matching                                                    */
/* --------------------------------------------------------------------- */

void py_exc_set_cause(PyObject *exc, PyObject *cause) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *old = e->cause;
    if (cause != NULL) py_incref(cause);
    e->cause = cause;
    if (old != NULL) py_decref(old);
}

void py_exc_set_context(PyObject *exc, PyObject *context) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    PyObject *old = e->context;
    if (context != NULL) py_incref(context);
    e->context = context;
    if (old != NULL) py_decref(old);
}

/* Project either an exception instance or a class object down to a
 * PyClassObject*. Returns NULL when the input is not usable. */
static PyClassObject *exc_to_class(PyObject *o) {
    if (o == NULL) return NULL;
    if (PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_CLASS) {
        return (PyClassObject *)o;
    }
    if (tag == PY_TYPE_EXC) {
        return ((PyExceptionObject *)o)->exc_class;
    }
    return NULL;
}

int py_exc_matches(PyObject *exc, PyObject *type) {
    PyClassObject *ecls = exc_to_class(exc);
    PyClassObject *tcls = exc_to_class(type);
    if (ecls == NULL || tcls == NULL) return 0;
    /* Walk MRO (produced by C3 in py_class_new). */
    if (ecls->mro == NULL) {
        /* Defensive: MRO not yet computed; direct identity only. */
        return ecls == tcls;
    }
    for (int32_t i = 0; i < ecls->n_mro; i++) {
        if (ecls->mro[i] == tcls) return 1;
    }
    return 0;
}

/* --------------------------------------------------------------------- */
/* Traceback growth                                                       */
/* --------------------------------------------------------------------- */

void py_exc_append_frame(PyObject *exc,
                         const char *func_name,
                         const char *filename,
                         int32_t line) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) return;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    if (e->n_frames == e->cap_frames) {
        int32_t new_cap = e->cap_frames ? e->cap_frames * 2 : 8;
        PyFrameRecord *newbuf = (PyFrameRecord *)realloc(
            e->traceback, (size_t)new_cap * sizeof(PyFrameRecord));
        if (newbuf == NULL) return;  /* silently drop — out of memory */
        e->traceback  = newbuf;
        e->cap_frames = new_cap;
    }
    PyFrameRecord *fr = &e->traceback[e->n_frames++];
    fr->func_name = func_name;
    fr->filename  = filename;
    fr->line      = line;
    fr->_pad      = 0;
}

/* --------------------------------------------------------------------- */
/* Raise / current / clear                                                */
/* --------------------------------------------------------------------- */

void py_raise(PyObject *exc) {
    /* Auto-chain context: if a prior exception is still active (we're
     * inside an except block), stash it as __context__ on the new one.
     * Matches CPython's implicit chaining behaviour. */
    if (g_current_exc != NULL && exc != NULL &&
        py_type_of(exc) == PY_TYPE_EXC) {
        PyExceptionObject *new_exc = (PyExceptionObject *)exc;
        if (new_exc->context == NULL) {
            py_incref(g_current_exc);
            new_exc->context = g_current_exc;
        }
    }
    if (g_current_exc != NULL) py_decref(g_current_exc);
    if (exc != NULL) py_incref(exc);
    g_current_exc = exc;

    /* __cxa_throw hands off to the Itanium personality which walks
     * frames using DWARF CFI. Caller is `noreturn` so codegen emits
     * `unreachable` immediately after — no cleanup here. */
    if (exc == NULL) {
        /* `raise` with nothing active: abort rather than unwind. */
        fprintf(stderr, "py_raise: no active exception to reraise\n");
        abort();
    }
    /* The Itanium ABI's personality function compares typeinfo
     * pointers by address. The landingpads emitted by pcc codegen
     * reference ``@py_exception_typeinfo`` (the GLOBAL's address,
     * not its stored value), so __cxa_throw must be given that same
     * address to make the comparison succeed. */
    __cxa_throw((void *)exc, (void *)&py_exception_typeinfo, NULL);
}

PyObject *py_current_exception(void) {
    return g_current_exc;   /* borrowed */
}

/* str(exc) — return the exception's message PyObject (borrowed). If the
 * exception has no message, return an empty string object. */
PyObject *py_exc_get_message(PyObject *exc) {
    if (exc == NULL) return NULL;
    if (py_type_of(exc) != PY_TYPE_EXC) return NULL;
    PyExceptionObject *e = (PyExceptionObject *)exc;
    return e->message;  /* borrowed */
}

void py_clear_exception(void) {
    if (g_current_exc != NULL) {
        py_decref(g_current_exc);
        g_current_exc = NULL;
    }
}

/* --------------------------------------------------------------------- */
/* Deallocation                                                           */
/* --------------------------------------------------------------------- */

void py_dealloc_exc(PyObject *o) {
    PyExceptionObject *e = (PyExceptionObject *)o;
    if (e->exc_class) py_decref((PyObject *)e->exc_class);
    if (e->message)   py_decref(e->message);
    if (e->cause)     py_decref(e->cause);
    if (e->context)   py_decref(e->context);
    if (e->traceback) free(e->traceback);
    free(e);
}

/* --------------------------------------------------------------------- */
/* Pretty-printing for unhandled exceptions                               */
/* --------------------------------------------------------------------- */

static void print_exc_heading(PyExceptionObject *e) {
    const char *cls_name = (e->exc_class && e->exc_class->name)
        ? e->exc_class->name : "Exception";
    if (e->message != NULL && e->message != py_None &&
        py_type_of(e->message) == PY_TYPE_STR) {
        const char *msg = py_str_utf8(e->message);
        fprintf(stderr, "%s: %s\n", cls_name, msg ? msg : "");
    } else {
        fprintf(stderr, "%s\n", cls_name);
    }
}

void py_exc_print_unhandled(PyObject *exc) {
    if (exc == NULL || py_type_of(exc) != PY_TYPE_EXC) {
        fprintf(stderr, "Unhandled non-exception object\n");
        return;
    }
    PyExceptionObject *e = (PyExceptionObject *)exc;

    /* Emit chained causes oldest-first, CPython-style. */
    if (e->cause != NULL && py_type_of(e->cause) == PY_TYPE_EXC) {
        py_exc_print_unhandled(e->cause);
        fprintf(stderr,
                "\nThe above exception was the direct cause of the "
                "following exception:\n\n");
    } else if (e->context != NULL && py_type_of(e->context) == PY_TYPE_EXC) {
        py_exc_print_unhandled(e->context);
        fprintf(stderr,
                "\nDuring handling of the above exception, another "
                "exception occurred:\n\n");
    }

    fprintf(stderr, "Traceback (most recent call last):\n");
    for (int32_t i = 0; i < e->n_frames; i++) {
        PyFrameRecord *fr = &e->traceback[i];
        fprintf(stderr, "  File \"%s\", line %d, in %s\n",
                fr->filename ? fr->filename : "<unknown>",
                fr->line,
                fr->func_name ? fr->func_name : "<module>");
    }
    print_exc_heading(e);
}
