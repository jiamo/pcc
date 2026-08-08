/* Host-C oracle for py/py_pickle_copy_runtime.py.
 *
 * Native copy/pickle subset for no-libpython compiled Python.
 *
 * This is intentionally a small runtime helper, not a CPython-compatible
 * pickle implementation.  It covers the data-model hooks that the compiler's
 * strict mode needs without routing through py_cpy_*.
 */

#include "py_internal.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    PyObject **keys;
    PyObject **values;
    int64_t len;
    int64_t cap;
} PccCopyMemo;

typedef struct PccPickleEntry {
    int64_t id;
    PyObject *payload;
    struct PccPickleEntry *next;
} PccPickleEntry;

static PccPickleEntry *pcc_pickle_entries = NULL;
static int64_t pcc_pickle_next_id = 1;

static int copy_ptr_can_have_header(void *ptr) {
    return pcc_gc_pointer_is_managed((PyObject *)ptr) != 0;
}

static int copy_is_heap_obj(PyObject *o) {
    return o != NULL && !PY_IS_TAGGED_INT(o) && copy_ptr_can_have_header(o);
}

static int copy_is_instance(PyObject *o) {
    if (!copy_is_heap_obj(o)) return 0;
    int32_t tag = py_type_of(o);
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START;
}

static PyClassObject *copy_instance_class(PyObject *o) {
    if (!copy_is_instance(o)) return NULL;
    PyInstanceObject *inst = (PyInstanceObject *)o;
    return (PyClassObject *)pcc_gc_load_ptr(o, (PyObject **)&inst->cls);
}

static PyObject **copy_dynamic_attr_slot(PyInstanceObject *inst) {
    if (inst == NULL) return NULL;
    PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
        (PyObject *)inst,
        (PyObject **)&inst->cls
    );
    if (cls == NULL) return NULL;
    int32_t n_fields = cls->n_fields;
    if (n_fields < 0) n_fields = 0;
    return &inst->fields[n_fields];
}

static PyObject *copy_lookup_method(PyObject *o, const char *name) {
    PyClassObject *cls = copy_instance_class(o);
    if (cls == NULL) return NULL;
    return py_class_lookup(cls, name);
}

static PyObject *copy_require_result(
    PyObject *result,
    const char *helper_name,
    const char *message
) {
    if (result == NULL) {
        py_runtime_error_if_unset(helper_name, message);
    }
    return result;
}

static PyObject *copy_call_method_with_args(
    PyObject *method,
    PyObject *self,
    PyObject *args
) {
    if (method == NULL) return NULL;
    int64_t n = args == NULL ? 0 : py_tuple_len(args);
    if (
        copy_is_heap_obj(method)
        && py_type_of(method) == PY_TYPE_FUNC
    ) {
        PyObject *full_args = py_tuple_new(n + 1);
        if (full_args == NULL) {
            return copy_require_result(
                NULL,
                "py_tuple_new",
                "copy callback argument tuple allocation failed"
            );
        }
        py_tuple_set_item(full_args, 0, self);
        for (int64_t i = 0; i < n; i++) {
            PyObject *item = py_tuple_get(args, i);
            if (item == NULL) {
                copy_require_result(
                    NULL,
                    "py_tuple_get",
                    "copy callback argument lookup failed"
                );
                py_decref(full_args);
                return NULL;
            }
            py_tuple_set_item(full_args, i + 1, item);
            py_decref(item);
        }
        PyObject *out = py_func_call(method, full_args);
        copy_require_result(
            out,
            "copy_call_method_with_args",
            "copy callback returned NULL without setting an exception"
        );
        py_decref(full_args);
        return out;
    }
    if (n == 0) {
        typedef PyObject *(*M0)(PyObject *);
        PyObject *out = ((M0)(uintptr_t)method)(self);
        return copy_require_result(
            out,
            "copy_call_method_with_args",
            "copy callback returned NULL without setting an exception"
        );
    }
    if (n == 1) {
        PyObject *a0 = py_tuple_get(args, 0);
        if (a0 == NULL) {
            return copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
        }
        typedef PyObject *(*M1)(PyObject *, PyObject *);
        PyObject *out = ((M1)(uintptr_t)method)(self, a0);
        copy_require_result(
            out,
            "copy_call_method_with_args",
            "copy callback returned NULL without setting an exception"
        );
        py_decref(a0);
        return out;
    }
    if (n == 2) {
        PyObject *a0 = py_tuple_get(args, 0);
        if (a0 == NULL) {
            return copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
        }
        PyObject *a1 = py_tuple_get(args, 1);
        if (a1 == NULL) {
            copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
            py_decref(a0);
            return NULL;
        }
        typedef PyObject *(*M2)(PyObject *, PyObject *, PyObject *);
        PyObject *out = ((M2)(uintptr_t)method)(self, a0, a1);
        copy_require_result(
            out,
            "copy_call_method_with_args",
            "copy callback returned NULL without setting an exception"
        );
        py_decref(a0);
        py_decref(a1);
        return out;
    }
    if (n == 3) {
        PyObject *a0 = py_tuple_get(args, 0);
        if (a0 == NULL) {
            return copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
        }
        PyObject *a1 = py_tuple_get(args, 1);
        if (a1 == NULL) {
            copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
            py_decref(a0);
            return NULL;
        }
        PyObject *a2 = py_tuple_get(args, 2);
        if (a2 == NULL) {
            copy_require_result(
                NULL,
                "py_tuple_get",
                "copy callback argument lookup failed"
            );
            py_decref(a0);
            py_decref(a1);
            return NULL;
        }
        typedef PyObject *(*M3)(
            PyObject *,
            PyObject *,
            PyObject *,
            PyObject *
        );
        PyObject *out = ((M3)(uintptr_t)method)(self, a0, a1, a2);
        copy_require_result(
            out,
            "copy_call_method_with_args",
            "copy callback returned NULL without setting an exception"
        );
        py_decref(a0);
        py_decref(a1);
        py_decref(a2);
        return out;
    }
    py_raise(py_exc_new(PY_EXC_TYPEERROR, "too many native method args"));
    return NULL;
}

static PyObject *copy_call_unary(PyObject *method, PyObject *self) {
    return copy_call_method_with_args(method, self, NULL);
}

static PyObject *copy_call_binary(PyObject *method, PyObject *self, PyObject *arg) {
    PyObject *args = py_tuple_new(1);
    if (args == NULL) {
        return copy_require_result(
            NULL,
            "py_tuple_new",
            "copy callback argument tuple allocation failed"
        );
    }
    py_tuple_set_item(args, 0, arg);
    PyObject *out = copy_call_method_with_args(method, self, args);
    py_decref(args);
    return out;
}

static void copy_memo_dispose(PccCopyMemo *memo) {
    if (memo == NULL) return;
    free(memo->keys);
    free(memo->values);
    memo->keys = NULL;
    memo->values = NULL;
    memo->len = 0;
    memo->cap = 0;
}

static PyObject *copy_memo_get(PccCopyMemo *memo, PyObject *key) {
    if (memo == NULL || key == NULL) return NULL;
    for (int64_t i = 0; i < memo->len; i++) {
        if (memo->keys[i] == key) {
            PyObject *value = memo->values[i];
            py_incref(value);
            return value;
        }
    }
    return NULL;
}

static int copy_memo_set(PccCopyMemo *memo, PyObject *key, PyObject *value) {
    if (memo == NULL || key == NULL || value == NULL) return -1;
    for (int64_t i = 0; i < memo->len; i++) {
        if (memo->keys[i] == key) {
            memo->values[i] = value;
            return 0;
        }
    }
    if (memo->len >= memo->cap) {
        int64_t new_cap = memo->cap > 0 ? memo->cap * 2 : 16;
        PyObject **new_keys = (PyObject **)realloc(
            memo->keys,
            (size_t)new_cap * sizeof(PyObject *)
        );
        if (new_keys == NULL) return -1;
        PyObject **new_values = (PyObject **)realloc(
            memo->values,
            (size_t)new_cap * sizeof(PyObject *)
        );
        if (new_values == NULL) {
            memo->keys = new_keys;
            return -1;
        }
        memo->keys = new_keys;
        memo->values = new_values;
        memo->cap = new_cap;
    }
    memo->keys[memo->len] = key;
    memo->values[memo->len] = value;
    memo->len++;
    return 0;
}

static PyObject *copy_object_deep(PyObject *o, PccCopyMemo *memo);

static PyObject *copy_shallow_list(PyObject *o) {
    PyListObject *src = (PyListObject *)o;
    PyObject *out = py_list_new(src->length);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < src->length; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &src->items[i]);
        py_list_append(out, item);
    }
    return out;
}

static PyObject *copy_deep_list(PyObject *o, PccCopyMemo *memo) {
    PyListObject *src = (PyListObject *)o;
    PyObject *out = py_list_new(src->length);
    if (out == NULL) return NULL;
    if (copy_memo_set(memo, o, out) != 0) {
        py_decref(out);
        return NULL;
    }
    for (int64_t i = 0; i < src->length; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &src->items[i]);
        PyObject *item_copy = copy_object_deep(item, memo);
        if (item_copy == NULL) {
            py_decref(out);
            return NULL;
        }
        py_list_append(out, item_copy);
        py_decref(item_copy);
    }
    return out;
}

static PyObject *copy_shallow_tuple(PyObject *o) {
    PyTupleObject *src = (PyTupleObject *)o;
    PyObject *out = py_tuple_new(src->len);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < src->len; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &src->items[i]);
        py_tuple_set_item(out, i, item);
    }
    return out;
}

static PyObject *copy_deep_tuple(PyObject *o, PccCopyMemo *memo) {
    PyTupleObject *src = (PyTupleObject *)o;
    PyObject *out = py_tuple_new(src->len);
    if (out == NULL) return NULL;
    if (copy_memo_set(memo, o, out) != 0) {
        py_decref(out);
        return NULL;
    }
    for (int64_t i = 0; i < src->len; i++) {
        PyObject *item = pcc_gc_load_ptr(o, &src->items[i]);
        PyObject *item_copy = copy_object_deep(item, memo);
        if (item_copy == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(out, i, item_copy);
        py_decref(item_copy);
    }
    return out;
}

static PyObject *copy_shallow_dict(PyObject *o) {
    PyDictObject *src = (PyDictObject *)o;
    PyObject *out = py_dict_new();
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < src->entries_used; i++) {
        DictEntry *entry = &src->entries[i];
        if (entry->key == NULL) continue;
        PyObject *key = pcc_gc_load_ptr(o, &entry->key);
        PyObject *value = pcc_gc_load_ptr(o, &entry->value);
        py_dict_set(out, key, value);
    }
    return out;
}

static PyObject *copy_deep_dict(PyObject *o, PccCopyMemo *memo) {
    PyDictObject *src = (PyDictObject *)o;
    PyObject *out = py_dict_new();
    if (out == NULL) return NULL;
    if (copy_memo_set(memo, o, out) != 0) {
        py_decref(out);
        return NULL;
    }
    for (int64_t i = 0; i < src->entries_used; i++) {
        DictEntry *entry = &src->entries[i];
        if (entry->key == NULL) continue;
        PyObject *key = pcc_gc_load_ptr(o, &entry->key);
        PyObject *value = pcc_gc_load_ptr(o, &entry->value);
        PyObject *key_copy = copy_object_deep(key, memo);
        PyObject *value_copy = copy_object_deep(value, memo);
        if (key_copy == NULL || value_copy == NULL) {
            if (key_copy != NULL) py_decref(key_copy);
            if (value_copy != NULL) py_decref(value_copy);
            py_decref(out);
            return NULL;
        }
        py_dict_set(out, key_copy, value_copy);
        py_decref(key_copy);
        py_decref(value_copy);
    }
    return out;
}

static PyObject *copy_shallow_set(PyObject *o) {
    PySetObject *src = (PySetObject *)o;
    PyObject *out = py_set_new();
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < src->capacity; i++) {
        PyObject *key = src->entries[i].key;
        if (key == NULL || key == py_set_dummy) continue;
        key = pcc_gc_load_ptr(o, &src->entries[i].key);
        py_set_add(out, key);
    }
    return out;
}

static PyObject *copy_deep_set(PyObject *o, PccCopyMemo *memo) {
    PySetObject *src = (PySetObject *)o;
    PyObject *out = py_set_new();
    if (out == NULL) return NULL;
    if (copy_memo_set(memo, o, out) != 0) {
        py_decref(out);
        return NULL;
    }
    for (int64_t i = 0; i < src->capacity; i++) {
        PyObject *key = src->entries[i].key;
        if (key == NULL || key == py_set_dummy) continue;
        key = pcc_gc_load_ptr(o, &src->entries[i].key);
        PyObject *key_copy = copy_object_deep(key, memo);
        if (key_copy == NULL) {
            py_decref(out);
            return NULL;
        }
        py_set_add(out, key_copy);
        py_decref(key_copy);
    }
    return out;
}

static PyObject *copy_instance_object(
    PyObject *o,
    PccCopyMemo *memo,
    int deep
) {
    PyClassObject *cls = copy_instance_class(o);
    if (cls == NULL) return NULL;

    if (!deep) {
        PyObject *copy_method = copy_lookup_method(o, "__copy__");
        if (copy_method != NULL) return copy_call_unary(copy_method, o);
    } else {
        PyObject *deepcopy_method = copy_lookup_method(o, "__deepcopy__");
        if (deepcopy_method != NULL) {
            PyObject *memo_obj = py_None;
            return copy_call_binary(deepcopy_method, o, memo_obj);
        }
    }

    PyObject *out = py_instance_new(cls);
    if (out == NULL) return NULL;
    if (deep && copy_memo_set(memo, o, out) != 0) {
        py_decref(out);
        return NULL;
    }

    PyObject *getstate_method = copy_lookup_method(o, "__getstate__");
    PyObject *setstate_method = copy_lookup_method(out, "__setstate__");
    if (!deep && getstate_method != NULL && setstate_method != NULL) {
        PyObject *state = copy_call_unary(getstate_method, o);
        if (state == NULL) {
            py_decref(out);
            return NULL;
        }
        PyObject *result = copy_call_binary(setstate_method, out, state);
        py_decref(state);
        if (result == NULL && py_err_occurred()) {
            py_decref(out);
            return NULL;
        }
        if (result != NULL) py_decref(result);
        return out;
    }

    PyInstanceObject *src_inst = (PyInstanceObject *)o;
    PyInstanceObject *dst_inst = (PyInstanceObject *)out;
    for (int32_t i = 0; i < cls->n_fields; i++) {
        PyObject *value = pcc_gc_load_ptr(o, &src_inst->fields[i]);
        if (value == NULL) continue;
        if (deep) {
            PyObject *value_copy = copy_object_deep(value, memo);
            if (value_copy == NULL) {
                py_decref(out);
                return NULL;
            }
            py_instance_set_field(dst_inst, i, value_copy);
            py_decref(value_copy);
        } else {
            py_instance_set_field(dst_inst, i, value);
        }
    }

    PyObject **src_dyn_slot = copy_dynamic_attr_slot(src_inst);
    PyObject **dst_dyn_slot = copy_dynamic_attr_slot(dst_inst);
    PyObject *src_dyn = src_dyn_slot ? pcc_gc_load_ptr(o, src_dyn_slot) : NULL;
    if (src_dyn != NULL && dst_dyn_slot != NULL) {
        PyObject *dyn_copy = deep
            ? copy_object_deep(src_dyn, memo)
            : copy_shallow_dict(src_dyn);
        if (dyn_copy == NULL) {
            py_decref(out);
            return NULL;
        }
        pcc_gc_store_ptr(out, dst_dyn_slot, dyn_copy);
        py_decref(dyn_copy);
    }
    return out;
}

static PyObject *copy_object_shallow(PyObject *o) {
    if (o == NULL) return NULL;
    if (!copy_is_heap_obj(o)) {
        py_incref(o);
        return o;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_LIST) return copy_shallow_list(o);
    if (tag == PY_TYPE_TUPLE) return copy_shallow_tuple(o);
    if (tag == PY_TYPE_DICT) return copy_shallow_dict(o);
    if (tag == PY_TYPE_SET) return copy_shallow_set(o);
    if (copy_is_instance(o)) return copy_instance_object(o, NULL, 0);
    py_incref(o);
    return o;
}

static PyObject *copy_object_deep(PyObject *o, PccCopyMemo *memo) {
    if (o == NULL) return NULL;
    if (!copy_is_heap_obj(o)) {
        py_incref(o);
        return o;
    }
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_NONE:
        case PY_TYPE_BOOL:
        case PY_TYPE_INT:
        case PY_TYPE_FLOAT:
        case PY_TYPE_STR:
        case PY_TYPE_BYTES:
            py_incref(o);
            return o;
        default:
            break;
    }
    PyObject *memo_value = copy_memo_get(memo, o);
    if (memo_value != NULL) return memo_value;
    if (tag == PY_TYPE_LIST) return copy_deep_list(o, memo);
    if (tag == PY_TYPE_TUPLE) return copy_deep_tuple(o, memo);
    if (tag == PY_TYPE_DICT) return copy_deep_dict(o, memo);
    if (tag == PY_TYPE_SET) return copy_deep_set(o, memo);
    if (copy_is_instance(o)) return copy_instance_object(o, memo, 1);
    py_incref(o);
    return o;
}

PyObject *py_copy_copy(PyObject *o) {
    return copy_object_shallow(o);
}

PyObject *py_copy_deepcopy(PyObject *o) {
    PccCopyMemo memo;
    memo.keys = NULL;
    memo.values = NULL;
    memo.len = 0;
    memo.cap = 0;
    PyObject *out = copy_object_deep(o, &memo);
    copy_memo_dispose(&memo);
    return out;
}

static PyObject *pickle_call_class(PyObject *callable, PyObject *args) {
    if (
        !copy_is_heap_obj(callable)
        || py_type_of(callable) != PY_TYPE_CLASS
    ) {
        return py_obj_call(callable, args, py_None);
    }
    PyClassObject *cls = (PyClassObject *)callable;
    PyObject *out = py_instance_new(cls);
    if (out == NULL) return NULL;
    PyObject *init_method = py_class_lookup(cls, "__init__");
    if (init_method != NULL) {
        PyObject *result = copy_call_method_with_args(init_method, out, args);
        if (result == NULL && py_err_occurred()) {
            py_decref(out);
            return NULL;
        }
        if (result != NULL) py_decref(result);
    }
    return out;
}

static PyObject *pickle_clone_from_reduce(PyObject *o) {
    PyObject *reduce_method = copy_lookup_method(o, "__reduce__");
    if (reduce_method == NULL) return NULL;
    PyObject *reduced = copy_call_unary(reduce_method, o);
    if (reduced == NULL) return NULL;
    if (
        !copy_is_heap_obj(reduced)
        || py_type_of(reduced) != PY_TYPE_TUPLE
        || py_tuple_len(reduced) < 2
    ) {
        py_decref(reduced);
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "invalid __reduce__ result"));
        return NULL;
    }
    PyObject *callable = py_tuple_get(reduced, 0);
    PyObject *args = py_tuple_get(reduced, 1);
    if (
        args == NULL
        || !copy_is_heap_obj(args)
        || py_type_of(args) != PY_TYPE_TUPLE
    ) {
        if (callable != NULL) py_decref(callable);
        if (args != NULL) py_decref(args);
        py_decref(reduced);
        py_raise(py_exc_new(PY_EXC_TYPEERROR, "invalid __reduce__ args"));
        return NULL;
    }
    PyObject *out = pickle_call_class(callable, args);
    py_decref(callable);
    py_decref(args);
    py_decref(reduced);
    return out;
}

static PyObject *pickle_clone_payload(PyObject *o) {
    PyObject *reduced = pickle_clone_from_reduce(o);
    if (reduced != NULL || py_err_occurred()) return reduced;
    return py_copy_deepcopy(o);
}

static int64_t pickle_store_payload(PyObject *payload) {
    if (payload == NULL) return 0;
    PccPickleEntry *entry = (PccPickleEntry *)malloc(sizeof(PccPickleEntry));
    if (entry == NULL) return 0;
    entry->id = pcc_pickle_next_id++;
    entry->payload = payload;
    py_incref(payload);
    entry->next = pcc_pickle_entries;
    pcc_pickle_entries = entry;
    return entry->id;
}

static PccPickleEntry *pickle_find_payload(int64_t id) {
    PccPickleEntry *entry = pcc_pickle_entries;
    while (entry != NULL) {
        if (entry->id == id) return entry;
        entry = entry->next;
    }
    return NULL;
}

static int pickle_write_decimal(char *buf, int64_t value) {
    char rev[32];
    int n = 0;
    if (value <= 0) {
        buf[0] = '0';
        return 1;
    }
    while (value > 0 && n < (int)sizeof(rev)) {
        int digit = (int)(value % 10);
        rev[n++] = (char)('0' + digit);
        value /= 10;
    }
    for (int i = 0; i < n; i++) {
        buf[i] = rev[n - 1 - i];
    }
    return n;
}

PyObject *py_pickle_dumps(PyObject *o, PyObject *protocol) {
    (void)protocol;
    PyObject *payload = pickle_clone_payload(o);
    if (payload == NULL) return NULL;
    int64_t id = pickle_store_payload(payload);
    py_decref(payload);
    if (id == 0) {
        py_raise(py_exc_new(PY_EXC_RUNTIMEERROR, "pickle registry allocation failed"));
        return NULL;
    }
    const char *prefix = "PCCPICKLE:";
    char buf[80];
    int prefix_len = 10;
    memcpy(buf, prefix, (size_t)prefix_len);
    int n = pickle_write_decimal(buf + prefix_len, id);
    return py_bytes_new(buf, prefix_len + n);
}

static int pickle_parse_id(PyObject *data, int64_t *id_out) {
    if (data == NULL || id_out == NULL) return -1;
    if (!copy_is_heap_obj(data)) return -1;
    int32_t tag = py_type_of(data);
    const char *text = NULL;
    int64_t len = 0;
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)data;
        text = b->data;
        len = b->byte_len;
    } else if (tag == PY_TYPE_STR) {
        PyStrObject *s = (PyStrObject *)data;
        text = s->data;
        len = s->byte_len;
    } else {
        return -1;
    }
    const char *prefix = "PCCPICKLE:";
    int64_t prefix_len = 10;
    if (len <= prefix_len) return -1;
    if (memcmp(text, prefix, (size_t)prefix_len) != 0) return -1;
    int64_t id = 0;
    for (int64_t i = prefix_len; i < len; i++) {
        char ch = text[i];
        if (ch < '0' || ch > '9') return -1;
        id = id * 10 + (int64_t)(ch - '0');
    }
    *id_out = id;
    return 0;
}

PyObject *py_pickle_loads(PyObject *data) {
    int64_t id = 0;
    if (pickle_parse_id(data, &id) != 0) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "unsupported pickle payload"));
        return NULL;
    }
    PccPickleEntry *entry = pickle_find_payload(id);
    if (entry == NULL || entry->payload == NULL) {
        py_raise(py_exc_new(PY_EXC_VALUEERROR, "unknown pickle payload"));
        return NULL;
    }
    return py_copy_deepcopy(entry->payload);
}
