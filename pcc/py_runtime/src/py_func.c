/* pcc/py_runtime/src/py_func.c
 *
 * Native function values for pcc-compiled Python.
 *
 * The runtime object is intentionally small: it stores a codegen-synthesized
 * adapter plus a tuple of captured values. The adapter performs all typed ABI
 * unboxing/boxing, so this file stays independent of frontend type details.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

#define PCC_FUNC_SIGNATURE_MAGIC "__pcc_func_signature_v1__"

#define PCC_FUNC_KIND_POS 0
#define PCC_FUNC_KIND_POS_ONLY 1
#define PCC_FUNC_KIND_KW_ONLY 2
#define PCC_FUNC_KIND_VARARGS 3
#define PCC_FUNC_KIND_VARKW 4

extern PyObject *py_func_code_class_cache;

PyObject *py_func_new_bound(
    void *entry,
    PyObject *captures_tuple,
    const char *name,
    PyObject *self_obj
) {
    if (entry == NULL) return NULL;
    PyFuncObject *f = (PyFuncObject *)pcc_gc_alloc(
        (int64_t)sizeof(PyFuncObject), PY_TYPE_FUNC, 0);
    if (f == NULL) return NULL;
    f->capi_method = NULL;
    f->capi_self = NULL;
    f->capi_module = NULL;
    f->capi_weakreflist = NULL;
    f->capi_vectorcall = NULL;
    f->entry = (PyNativeFuncEntry)entry;
    f->name = name;
    f->self_obj = NULL;
    f->attrs = NULL;
    PyObject *captures = captures_tuple == NULL ? py_tuple_new(0) : captures_tuple;
    f->captures = NULL;
    pcc_gc_store_ptr((PyObject *)f, &f->captures, captures);
    if (self_obj != NULL) {
        pcc_gc_store_ptr((PyObject *)f, &f->self_obj, self_obj);
    }
    if (captures_tuple == NULL) {
        py_decref(captures);
    }
    py_gc_track((PyObject *)f);
    return (PyObject *)f;
}

PyObject *py_func_new_named(void *entry, PyObject *captures_tuple, const char *name) {
    return py_func_new_bound(entry, captures_tuple, name, NULL);
}

PyObject *py_func_new(void *entry, PyObject *captures_tuple) {
    return py_func_new_named(entry, captures_tuple, NULL);
}

static PyObject *py_func_type_error(const char *message) {
    PyObject *exc = py_exc_new(PY_EXC_TYPEERROR, message);
    py_raise(exc);
    if (exc != NULL) py_decref(exc);
    return NULL;
}

static PyObject *py_func_runtime_error_if_unset(const char *helper_name,
                                                const char *message) {
    if (py_err_occurred()) return NULL;
    return py_runtime_error_if_unset(helper_name, message);
}

static int py_func_kwargs_empty(PyObject *kwargs) {
    if (kwargs == NULL || kwargs == py_None) return 1;
    if (PY_IS_TAGGED_INT(kwargs)) return 0;
    if (py_header(kwargs)->type_tag != PY_TYPE_DICT) return 0;
    return py_dict_len(kwargs) == 0;
}

static int py_func_signature_valid(PyObject *sig) {
    if (sig == NULL || PY_IS_TAGGED_INT(sig)) return 0;
    if (py_header(sig)->type_tag != PY_TYPE_TUPLE) return 0;
    if (py_tuple_len(sig) < 5) return 0;

    PyObject *magic = py_tuple_get(sig, 0);
    if (magic == NULL) return 0;
    PyObject *expected = py_str_new(
        PCC_FUNC_SIGNATURE_MAGIC,
        (int64_t)strlen(PCC_FUNC_SIGNATURE_MAGIC)
    );
    int ok = expected != NULL && py_str_eq(magic, expected) != 0;
    py_decref(magic);
    if (expected != NULL) py_decref(expected);
    return ok;
}

static PyObject *py_func_signature_from_captures(
    PyObject *captures,
    PyObject **actual_captures
) {
    *actual_captures = captures;
    if (captures == NULL || PY_IS_TAGGED_INT(captures)) return NULL;
    if (py_header(captures)->type_tag != PY_TYPE_TUPLE) return NULL;
    if (py_tuple_len(captures) != 2) return NULL;

    PyObject *candidate = py_tuple_get(captures, 1);
    if (!py_func_signature_valid(candidate)) {
        if (candidate != NULL) py_decref(candidate);
        return NULL;
    }

    PyObject *inner = py_tuple_get(captures, 0);
    if (inner == NULL) {
        py_decref(candidate);
        py_func_runtime_error_if_unset(
            "py_func_signature_from_captures",
            "native function signature has no captures tuple"
        );
        return NULL;
    }
    *actual_captures = inner;
    return candidate;
}

static PyClassObject *py_func_code_class(void) {
    if (py_func_code_class_cache != NULL) {
        return (PyClassObject *)py_func_code_class_cache;
    }
    PyClassObject *cls = py_class_new("code", NULL, 0, NULL, 0);
    if (cls == NULL) return NULL;
    pcc_gc_pin((PyObject *)cls);
    py_func_code_class_cache = (PyObject *)cls;
    return cls;
}

static int py_func_code_set_owned_attr(
    PyObject *code,
    const char *name,
    PyObject *value
) {
    if (value == NULL) return -1;
    int64_t rc = py_obj_setattr(code, name, value);
    py_decref(value);
    return rc == 0 ? 0 : -1;
}

int64_t py_func_attach_code_metadata(
    PyObject *func,
    PyObject *signature,
    const char *name
) {
    if (
        func == NULL || PY_IS_TAGGED_INT(func)
        || py_type_of(func) != PY_TYPE_FUNC
        || !py_func_signature_valid(signature)
    ) {
        return -1;
    }

    PyObject *names = py_tuple_get(signature, 1);
    PyObject *kinds = py_tuple_get(signature, 2);
    if (names == NULL || kinds == NULL) {
        if (names != NULL) py_decref(names);
        if (kinds != NULL) py_decref(kinds);
        return -1;
    }
    int64_t count = py_tuple_len(names);
    if (py_tuple_len(kinds) != count) {
        py_decref(names);
        py_decref(kinds);
        return -1;
    }

    int64_t argcount = 0;
    int64_t posonlyargcount = 0;
    int64_t kwonlyargcount = 0;
    int64_t flags = 0;
    for (int64_t i = 0; i < count; i++) {
        PyObject *kind_obj = py_tuple_get(kinds, i);
        if (kind_obj == NULL) {
            py_decref(names);
            py_decref(kinds);
            return -1;
        }
        int64_t kind = py_int_value_i64(kind_obj);
        py_decref(kind_obj);
        if (kind == PCC_FUNC_KIND_POS || kind == PCC_FUNC_KIND_POS_ONLY) {
            argcount++;
        }
        if (kind == PCC_FUNC_KIND_POS_ONLY) posonlyargcount++;
        if (kind == PCC_FUNC_KIND_KW_ONLY) kwonlyargcount++;
        if (kind == PCC_FUNC_KIND_VARARGS) flags |= 4;
        if (kind == PCC_FUNC_KIND_VARKW) flags |= 8;
    }

    PyObject *varnames = py_tuple_new(count);
    if (varnames == NULL) {
        py_decref(names);
        py_decref(kinds);
        return -1;
    }
    int64_t out_index = 0;
    for (int pass = 0; pass < 4; pass++) {
        for (int64_t i = 0; i < count; i++) {
            PyObject *kind_obj = py_tuple_get(kinds, i);
            if (kind_obj == NULL) goto fail_varnames;
            int64_t kind = py_int_value_i64(kind_obj);
            py_decref(kind_obj);
            int matches =
                (pass == 0 && (
                    kind == PCC_FUNC_KIND_POS
                    || kind == PCC_FUNC_KIND_POS_ONLY
                ))
                || (pass == 1 && kind == PCC_FUNC_KIND_KW_ONLY)
                || (pass == 2 && kind == PCC_FUNC_KIND_VARARGS)
                || (pass == 3 && kind == PCC_FUNC_KIND_VARKW);
            if (!matches) continue;
            PyObject *arg_name = py_tuple_get(names, i);
            if (arg_name == NULL) goto fail_varnames;
            py_tuple_set_item(varnames, out_index++, arg_name);
            py_decref(arg_name);
        }
    }

    PyClassObject *code_cls = py_func_code_class();
    if (code_cls == NULL) goto fail_varnames;
    PyObject *code = py_instance_new(code_cls);
    if (code == NULL) goto fail_varnames;
    if (
        py_func_code_set_owned_attr(
            code, "co_argcount", py_int_from_i64(argcount)
        ) != 0
        || py_func_code_set_owned_attr(
            code, "co_posonlyargcount", py_int_from_i64(posonlyargcount)
        ) != 0
        || py_func_code_set_owned_attr(
            code, "co_kwonlyargcount", py_int_from_i64(kwonlyargcount)
        ) != 0
        || py_func_code_set_owned_attr(
            code, "co_flags", py_int_from_i64(flags)
        ) != 0
        || py_func_code_set_owned_attr(code, "co_varnames", varnames) != 0
        || py_func_code_set_owned_attr(
            code,
            "co_name",
            py_str_new(name == NULL ? "" : name, (int64_t)strlen(name == NULL ? "" : name))
        ) != 0
        || py_obj_setattr(func, "__code__", code) != 0
    ) {
        py_decref(code);
        py_decref(names);
        py_decref(kinds);
        return -1;
    }
    py_decref(code);
    py_decref(names);
    py_decref(kinds);
    return 0;

fail_varnames:
    py_decref(varnames);
    py_decref(names);
    py_decref(kinds);
    return -1;
}

PyObject *py_func_get_code_metadata(PyObject *func) {
    if (
        func == NULL || PY_IS_TAGGED_INT(func)
        || py_type_of(func) != PY_TYPE_FUNC
    ) {
        return NULL;
    }
    PyFuncObject *f = (PyFuncObject *)func;
    PyObject *captures = pcc_gc_load_ptr(func, &f->captures);
    PyObject *actual_captures = captures;
    PyObject *signature = py_func_signature_from_captures(
        captures, &actual_captures);
    if (signature == NULL) return NULL;
    if (actual_captures != captures) py_decref(actual_captures);

    int64_t rc = py_func_attach_code_metadata(func, signature, f->name);
    py_decref(signature);
    if (rc != 0) return NULL;

    PyObject *attrs = pcc_gc_load_ptr(func, &f->attrs);
    if (attrs == NULL) return NULL;
    PyObject *key = py_str_new("__code__", 8);
    if (key == NULL) return NULL;
    PyObject *code = py_dict_get(attrs, key);
    py_decref(key);
    return code;
}

PyObject *py_func_get_defaults_metadata(PyObject *func) {
    if (
        func == NULL || PY_IS_TAGGED_INT(func)
        || py_type_of(func) != PY_TYPE_FUNC
    ) {
        return NULL;
    }
    PyFuncObject *f = (PyFuncObject *)func;
    PyObject *captures = pcc_gc_load_ptr(func, &f->captures);
    PyObject *actual_captures = captures;
    PyObject *signature = py_func_signature_from_captures(
        captures, &actual_captures);
    if (signature == NULL) return NULL;
    if (actual_captures != captures) py_decref(actual_captures);

    PyObject *kinds = py_tuple_get(signature, 2);
    PyObject *has_defaults = py_tuple_get(signature, 3);
    PyObject *defaults = py_tuple_get(signature, 4);
    if (kinds == NULL || has_defaults == NULL || defaults == NULL) {
        if (kinds != NULL) py_decref(kinds);
        if (has_defaults != NULL) py_decref(has_defaults);
        if (defaults != NULL) py_decref(defaults);
        py_decref(signature);
        return NULL;
    }
    int64_t count = py_tuple_len(kinds);
    if (
        py_tuple_len(has_defaults) != count
        || py_tuple_len(defaults) != count
    ) {
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        py_decref(signature);
        return NULL;
    }

    int64_t default_count = 0;
    for (int64_t i = 0; i < count; i++) {
        PyObject *kind_obj = py_tuple_get(kinds, i);
        PyObject *has_default_obj = py_tuple_get(has_defaults, i);
        if (kind_obj == NULL || has_default_obj == NULL) {
            if (kind_obj != NULL) py_decref(kind_obj);
            if (has_default_obj != NULL) py_decref(has_default_obj);
            goto fail;
        }
        int64_t kind = py_int_value_i64(kind_obj);
        int64_t has_default = py_obj_truthy(has_default_obj);
        py_decref(kind_obj);
        py_decref(has_default_obj);
        if (
            (kind == PCC_FUNC_KIND_POS || kind == PCC_FUNC_KIND_POS_ONLY)
            && has_default
        ) {
            default_count++;
        }
    }

    PyObject *out = NULL;
    if (default_count == 0) {
        py_incref(py_None);
        out = py_None;
    } else {
        out = py_tuple_new(default_count);
        if (out == NULL) goto fail;
        int64_t out_index = 0;
        for (int64_t i = 0; i < count; i++) {
            PyObject *kind_obj = py_tuple_get(kinds, i);
            PyObject *has_default_obj = py_tuple_get(has_defaults, i);
            if (kind_obj == NULL || has_default_obj == NULL) {
                if (kind_obj != NULL) py_decref(kind_obj);
                if (has_default_obj != NULL) py_decref(has_default_obj);
                py_decref(out);
                goto fail;
            }
            int64_t kind = py_int_value_i64(kind_obj);
            int64_t has_default = py_obj_truthy(has_default_obj);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            if (
                (kind == PCC_FUNC_KIND_POS || kind == PCC_FUNC_KIND_POS_ONLY)
                && has_default
            ) {
                PyObject *default_obj = py_tuple_get(defaults, i);
                if (default_obj == NULL) {
                    py_decref(out);
                    goto fail;
                }
                py_tuple_set_item(out, out_index++, default_obj);
                py_decref(default_obj);
            }
        }
    }

    py_decref(kinds);
    py_decref(has_defaults);
    py_decref(defaults);
    py_decref(signature);
    if (py_obj_setattr(func, "__defaults__", out) != 0) {
        py_decref(out);
        return NULL;
    }
    return out;

fail:
    py_decref(kinds);
    py_decref(has_defaults);
    py_decref(defaults);
    py_decref(signature);
    return NULL;
}

static PyObject *py_func_copy_varargs(
    PyObject *args,
    int64_t start,
    int64_t nargs
) {
    int64_t count = nargs - start;
    if (count < 0) count = 0;
    PyObject *out = py_tuple_new(count);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < count; i++) {
        PyObject *item = py_tuple_get(args, start + i);
        if (item == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(out, i, item);
        py_decref(item);
    }
    return out;
}

static PyObject *py_func_bind_signature(
    PyObject *sig,
    PyObject *args_tuple,
    PyObject *kwargs
) {
    PyObject *names = py_tuple_get(sig, 1);
    PyObject *kinds = py_tuple_get(sig, 2);
    PyObject *has_defaults = py_tuple_get(sig, 3);
    PyObject *defaults = py_tuple_get(sig, 4);
    if (
        names == NULL || kinds == NULL ||
        has_defaults == NULL || defaults == NULL
    ) {
        if (names != NULL) py_decref(names);
        if (kinds != NULL) py_decref(kinds);
        if (has_defaults != NULL) py_decref(has_defaults);
        if (defaults != NULL) py_decref(defaults);
        return NULL;
    }

    int64_t nformals = py_tuple_len(names);
    if (
        py_tuple_len(kinds) != nformals ||
        py_tuple_len(has_defaults) != nformals ||
        py_tuple_len(defaults) != nformals
    ) {
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return py_func_type_error("invalid native function signature");
    }

    int made_args = 0;
    PyObject *args = args_tuple;
    if (args == NULL || args == py_None) {
        args = py_tuple_new(0);
        if (args == NULL) {
            py_decref(names);
            py_decref(kinds);
            py_decref(has_defaults);
            py_decref(defaults);
            return NULL;
        }
        made_args = 1;
    }
    if (PY_IS_TAGGED_INT(args) || py_header(args)->type_tag != PY_TYPE_TUPLE) {
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return py_func_type_error("native function args must be a tuple");
    }

    PyObject *remaining = py_call_merge_kwargs(NULL, kwargs);
    if (remaining == NULL) {
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return NULL;
    }

    PyObject *bound = py_tuple_new(nformals);
    if (bound == NULL) {
        py_decref(remaining);
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return NULL;
    }

    int64_t nargs = py_tuple_len(args);
    int64_t pos_index = 0;
    int saw_varkw = 0;

    for (int64_t i = 0; i < nformals; i++) {
        PyObject *name = py_tuple_get(names, i);
        PyObject *kind_obj = py_tuple_get(kinds, i);
        PyObject *has_default_obj = py_tuple_get(has_defaults, i);
        PyObject *default_obj = py_tuple_get(defaults, i);
        if (
            name == NULL || kind_obj == NULL ||
            has_default_obj == NULL || default_obj == NULL
        ) {
            if (name != NULL) py_decref(name);
            if (kind_obj != NULL) py_decref(kind_obj);
            if (has_default_obj != NULL) py_decref(has_default_obj);
            if (default_obj != NULL) py_decref(default_obj);
            py_decref(bound);
            py_decref(remaining);
            if (made_args) py_decref(args);
            py_decref(names);
            py_decref(kinds);
            py_decref(has_defaults);
            py_decref(defaults);
            return NULL;
        }

        int64_t kind = py_int_value_i64(kind_obj);
        int64_t has_default = py_obj_truthy(has_default_obj);

        if (kind == PCC_FUNC_KIND_VARARGS) {
            PyObject *varargs = py_func_copy_varargs(args, pos_index, nargs);
            if (varargs == NULL) {
                py_decref(name);
                py_decref(kind_obj);
                py_decref(has_default_obj);
                py_decref(default_obj);
                py_decref(bound);
                py_decref(remaining);
                if (made_args) py_decref(args);
                py_decref(names);
                py_decref(kinds);
                py_decref(has_defaults);
                py_decref(defaults);
                return NULL;
            }
            py_tuple_set_item(bound, i, varargs);
            py_decref(varargs);
            pos_index = nargs;
            py_decref(name);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            py_decref(default_obj);
            continue;
        }

        if (kind == PCC_FUNC_KIND_VARKW) {
            py_tuple_set_item(bound, i, remaining);
            saw_varkw = 1;
            py_decref(name);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            py_decref(default_obj);
            continue;
        }

        if (
            kind != PCC_FUNC_KIND_KW_ONLY &&
            pos_index < nargs
        ) {
            if (
                kind != PCC_FUNC_KIND_POS_ONLY &&
                py_dict_contains(remaining, name) != 0
            ) {
                py_decref(name);
                py_decref(kind_obj);
                py_decref(has_default_obj);
                py_decref(default_obj);
                py_decref(bound);
                py_decref(remaining);
                if (made_args) py_decref(args);
                py_decref(names);
                py_decref(kinds);
                py_decref(has_defaults);
                py_decref(defaults);
                return py_func_type_error(
                    "native function got multiple values for argument"
                );
            }
            PyObject *item = py_tuple_get(args, pos_index);
            pos_index++;
            if (item == NULL) {
                py_decref(name);
                py_decref(kind_obj);
                py_decref(has_default_obj);
                py_decref(default_obj);
                py_decref(bound);
                py_decref(remaining);
                if (made_args) py_decref(args);
                py_decref(names);
                py_decref(kinds);
                py_decref(has_defaults);
                py_decref(defaults);
                return NULL;
            }
            py_tuple_set_item(bound, i, item);
            py_decref(item);
            py_decref(name);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            py_decref(default_obj);
            continue;
        }

        if (
            kind != PCC_FUNC_KIND_POS_ONLY &&
            py_dict_contains(remaining, name) != 0
        ) {
            PyObject *item = py_dict_get(remaining, name);
            (void)py_dict_del(remaining, name);
            if (item == NULL) {
                py_decref(name);
                py_decref(kind_obj);
                py_decref(has_default_obj);
                py_decref(default_obj);
                py_decref(bound);
                py_decref(remaining);
                if (made_args) py_decref(args);
                py_decref(names);
                py_decref(kinds);
                py_decref(has_defaults);
                py_decref(defaults);
                return NULL;
            }
            py_tuple_set_item(bound, i, item);
            py_decref(item);
            py_decref(name);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            py_decref(default_obj);
            continue;
        }

        if (has_default != 0) {
            py_tuple_set_item(bound, i, default_obj);
            py_decref(name);
            py_decref(kind_obj);
            py_decref(has_default_obj);
            py_decref(default_obj);
            continue;
        }

        py_decref(name);
        py_decref(kind_obj);
        py_decref(has_default_obj);
        py_decref(default_obj);
        py_decref(bound);
        py_decref(remaining);
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return py_func_type_error("missing required native function argument");
    }

    if (pos_index < nargs) {
        py_decref(bound);
        py_decref(remaining);
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return py_func_type_error(
            "native function got too many positional arguments"
        );
    }

    if (!saw_varkw && py_dict_len(remaining) != 0) {
        py_decref(bound);
        py_decref(remaining);
        if (made_args) py_decref(args);
        py_decref(names);
        py_decref(kinds);
        py_decref(has_defaults);
        py_decref(defaults);
        return py_func_type_error("unexpected native function keyword argument");
    }

    py_decref(remaining);
    if (made_args) py_decref(args);
    py_decref(names);
    py_decref(kinds);
    py_decref(has_defaults);
    py_decref(defaults);
    return bound;
}

PyObject *py_func_call_kwargs(
    PyObject *callable,
    PyObject *args_tuple,
    PyObject *kwargs
) {
    if (callable == NULL) {
        return py_func_type_error("native function call received NULL callable");
    }
    if (PY_IS_TAGGED_INT(callable)) {
        return py_func_type_error("native function call requires a function object");
    }
    PyObjectHeader *h = py_header(callable);
    if (h->type_tag != PY_TYPE_FUNC) {
        return py_func_type_error("native function call requires a function object");
    }
    PyFuncObject *f = (PyFuncObject *)callable;
    if (f->entry == NULL) {
        return py_func_runtime_error_if_unset(
            "py_func_call_kwargs",
            "native function object has no entry point"
        );
    }
    PyObject *args = args_tuple == NULL ? py_tuple_new(0) : args_tuple;
    if (args == NULL) {
        return py_func_runtime_error_if_unset(
            "py_tuple_new",
            "native function could not create its argument tuple"
        );
    }
    PyObject *captures = pcc_gc_load_ptr(callable, &f->captures);
    PyObject *actual_captures = captures;
    PyObject *sig = py_func_signature_from_captures(captures, &actual_captures);
    if (sig == NULL && py_err_occurred()) {
        if (args_tuple == NULL) py_decref(args);
        return NULL;
    }
    if (sig == NULL && !py_func_kwargs_empty(kwargs)) {
        if (args_tuple == NULL) py_decref(args);
        return py_func_type_error("native function does not accept keywords");
    }

    PyObject *call_args = args;
    int bound_args = 0;
    if (sig != NULL) {
        call_args = py_func_bind_signature(sig, args, kwargs);
        if (call_args == NULL) {
            /* Enforce the callee contract before releasing temporary
             * signature state.  Cleanup can run deallocators; it must not be
             * allowed to turn a silent binder failure into an unrelated
             * exception (or leave it silent). */
            py_func_runtime_error_if_unset(
                "py_func_bind_signature",
                "native function argument binding returned NULL without exception"
            );
            py_decref(sig);
            py_decref(actual_captures);
            if (args_tuple == NULL) py_decref(args);
            return NULL;
        }
        bound_args = 1;
    }

    PyObject *result = f->entry(actual_captures, call_args);
    /* Inspect the entry result at the immediate call boundary.  In
     * particular, do this before decrefing call_args/signature captures so a
     * cleanup side effect cannot supply the exception that the compiled
     * entry itself failed to set. */
    if (result == NULL) {
        py_func_runtime_error_if_unset(
            f->name != NULL ? f->name : "<compiled native function>",
            "compiled native function returned NULL without exception"
        );
    }
    if (bound_args) py_decref(call_args);
    if (sig != NULL) {
        py_decref(sig);
        py_decref(actual_captures);
    }
    if (args_tuple == NULL) py_decref(args);
    return result;
}

PyObject *py_func_call(PyObject *callable, PyObject *args_tuple) {
    return py_func_call_kwargs(callable, args_tuple, NULL);
}

void py_dealloc_func(PyObject *o) {
    PyFuncObject *f = (PyFuncObject *)o;
    PyObject *capi_self = pcc_gc_load_ptr(o, &f->capi_self);
    PyObject *capi_module = pcc_gc_load_ptr(o, &f->capi_module);
    PyObject *capi_weakreflist = pcc_gc_load_ptr(o, &f->capi_weakreflist);
    PyObject *captures = pcc_gc_load_ptr(o, &f->captures);
    PyObject *self_obj = pcc_gc_load_ptr(o, &f->self_obj);
    PyObject *attrs = pcc_gc_load_ptr(o, &f->attrs);
    if (capi_self != NULL) py_decref(capi_self);
    if (capi_module != NULL) py_decref(capi_module);
    if (capi_weakreflist != NULL) py_decref(capi_weakreflist);
    py_decref(captures);
    if (self_obj != NULL) py_decref(self_obj);
    if (attrs != NULL) py_decref(attrs);
    pcc_gc_free_object_memory(o);
}

/* functools.partial: a callable prepending captured `bound` args to the call
 * args, then invoking `fn` via the generic call. */
static PyObject *pcc_partial_entry(PyObject *captures, PyObject *args) {
    PyObject *fn = py_tuple_get(captures, 0);
    PyObject *bound = py_tuple_get(captures, 1);
    if (fn == NULL || bound == NULL) {
        if (fn != NULL) py_decref(fn);
        if (bound != NULL) py_decref(bound);
        return NULL;
    }
    int64_t nb = py_tuple_len(bound);
    int64_t na = py_tuple_len(args);
    PyObject *full = py_tuple_new(nb + na);
    if (full == NULL) { py_decref(fn); py_decref(bound); return NULL; }
    for (int64_t i = 0; i < nb; i++) py_tuple_set_item(full, i, py_tuple_get(bound, i));
    for (int64_t i = 0; i < na; i++) py_tuple_set_item(full, nb + i, py_tuple_get(args, i));
    PyObject *out = py_obj_call(fn, full, NULL);
    py_decref(full); py_decref(fn); py_decref(bound);
    return out;
}

static PyObject *pcc_partial_kw_entry(PyObject *captures, PyObject *args) {
    PyObject *fn = py_tuple_get(captures, 0);
    PyObject *bound = py_tuple_get(captures, 1);
    PyObject *kwargs = py_tuple_get(captures, 2);
    if (fn == NULL || bound == NULL || kwargs == NULL) {
        if (fn != NULL) py_decref(fn);
        if (bound != NULL) py_decref(bound);
        if (kwargs != NULL) py_decref(kwargs);
        return NULL;
    }
    int64_t nb = py_tuple_len(bound);
    int64_t na = py_tuple_len(args);
    PyObject *full = py_tuple_new(nb + na);
    if (full == NULL) {
        py_decref(fn);
        py_decref(bound);
        py_decref(kwargs);
        return NULL;
    }
    for (int64_t i = 0; i < nb; i++) py_tuple_set_item(full, i, py_tuple_get(bound, i));
    for (int64_t i = 0; i < na; i++) py_tuple_set_item(full, nb + i, py_tuple_get(args, i));
    PyObject *out = py_obj_call(fn, full, kwargs);
    py_decref(full);
    py_decref(fn);
    py_decref(bound);
    py_decref(kwargs);
    return out;
}

PyObject *py_functools_partial(PyObject *fn, PyObject *bound_args) {
    if (fn == NULL) return NULL;
    PyObject *bound = bound_args == NULL ? py_tuple_new(0) : bound_args;
    if (bound == NULL) return NULL;
    PyObject *captures = py_tuple_new(2);
    if (captures == NULL) { if (bound_args == NULL) py_decref(bound); return NULL; }
    py_incref(fn); py_tuple_set_item(captures, 0, fn);
    py_incref(bound); py_tuple_set_item(captures, 1, bound);
    PyObject *p = py_func_new_bound((void *)pcc_partial_entry, captures, "partial", NULL);
    py_decref(captures);
    if (bound_args == NULL) py_decref(bound);
    return p;
}

PyObject *py_functools_partial_kw(PyObject *fn, PyObject *bound_args, PyObject *bound_kwargs) {
    if (fn == NULL) return NULL;
    PyObject *bound = bound_args == NULL ? py_tuple_new(0) : bound_args;
    if (bound == NULL) return NULL;
    PyObject *kwargs = bound_kwargs == NULL ? py_dict_new() : bound_kwargs;
    if (kwargs == NULL) {
        if (bound_args == NULL) py_decref(bound);
        return NULL;
    }
    PyObject *captures = py_tuple_new(3);
    if (captures == NULL) {
        if (bound_args == NULL) py_decref(bound);
        if (bound_kwargs == NULL) py_decref(kwargs);
        return NULL;
    }
    py_incref(fn); py_tuple_set_item(captures, 0, fn);
    py_incref(bound); py_tuple_set_item(captures, 1, bound);
    py_incref(kwargs); py_tuple_set_item(captures, 2, kwargs);
    PyObject *p = py_func_new_bound((void *)pcc_partial_kw_entry, captures, "partial", NULL);
    py_decref(captures);
    if (bound_args == NULL) py_decref(bound);
    if (bound_kwargs == NULL) py_decref(kwargs);
    return p;
}

static int pcc_update_wrapper_copy_attr(
    PyObject *wrapper,
    PyObject *wrapped,
    const char *name
) {
    PyObject *value = py_obj_getattr(wrapped, name);
    if (value == NULL) {
        /* functools.update_wrapper ignores an assigned attribute that the
         * wrapped object does not expose. */
        py_clear_exception();
        return 0;
    }
    int64_t status = py_obj_setattr(wrapper, name, value);
    py_decref(value);
    return status == 0 ? 0 : -1;
}

PyObject *py_functools_update_wrapper(PyObject *wrapper, PyObject *wrapped) {
    if (wrapper == NULL || wrapped == NULL) return NULL;
    static const char *assigned[] = {
        "__module__",
        "__name__",
        "__qualname__",
        "__doc__",
        "__annotations__",
        "__type_params__",
    };
    for (int64_t i = 0; i < 6; i++) {
        if (pcc_update_wrapper_copy_attr(wrapper, wrapped, assigned[i]) != 0) {
            return NULL;
        }
    }

    PyObject *wrapper_dict = py_obj_getattr(wrapper, "__dict__");
    if (wrapper_dict == NULL) py_clear_exception();
    PyObject *wrapped_dict = py_obj_getattr(wrapped, "__dict__");
    if (wrapped_dict == NULL) py_clear_exception();
    if (wrapper_dict != NULL && wrapped_dict != NULL) {
        py_dict_update(wrapper_dict, wrapped_dict);
    }
    py_decref(wrapper_dict);
    py_decref(wrapped_dict);

    if (py_obj_setattr(wrapper, "__wrapped__", wrapped) != 0) return NULL;
    py_incref(wrapper);
    return wrapper;
}
