/* Module attribute storage for no-libpython compiled modules.
 *
 * This is not a full module object model yet.  It is a stable side table keyed
 * by module name, so codegen can lower:
 *
 *   module.attr = value
 *   module.attr
 *
 * without using CPython module objects.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>

typedef struct PccModuleAttrsNode {
    char *name;
    PyObject *attrs;
    struct PccModuleAttrsNode *next;
} PccModuleAttrsNode;

static PccModuleAttrsNode *pcc_module_attrs_head = NULL;

/* Shared storage for the native function ``code`` metadata class.  The C and
 * pcc-Python py_func implementations both use this kernel-level root slot so
 * they construct the same runtime object shape without emitting one class per
 * compiled function. */
PyObject *py_func_code_class_cache = NULL;

static char *pcc_strdup(const char *s) {
    if (s == NULL) s = "";
    size_t n = strlen(s);
    char *out = (char *)malloc(n + 1);
    if (out == NULL) return NULL;
    memcpy(out, s, n + 1);
    return out;
}

static PccModuleAttrsNode *pcc_module_find(const char *module_name) {
    if (module_name == NULL) module_name = "";
    for (PccModuleAttrsNode *n = pcc_module_attrs_head; n != NULL; n = n->next) {
        if (strcmp(n->name, module_name) == 0) return n;
    }
    return NULL;
}

static PccModuleAttrsNode *pcc_module_ensure(const char *module_name) {
    PccModuleAttrsNode *existing = pcc_module_find(module_name);
    if (existing != NULL) return existing;

    PyObject *attrs = py_dict_new();
    if (attrs == NULL) return NULL;
    pcc_gc_pin(attrs);

    PccModuleAttrsNode *n = (
        PccModuleAttrsNode *
    )calloc(1, sizeof(PccModuleAttrsNode));
    if (n == NULL) {
        pcc_gc_unpin(attrs);
        py_decref(attrs);
        return NULL;
    }
    n->name = pcc_strdup(module_name);
    if (n->name == NULL) {
        pcc_gc_unpin(attrs);
        py_decref(attrs);
        free(n);
        return NULL;
    }
    n->attrs = attrs;
    n->next = pcc_module_attrs_head;
    pcc_module_attrs_head = n;
    return n;
}

PyObject *py_module_attrs_dict(const char *module_name, int64_t create) {
    PccModuleAttrsNode *n = create ? pcc_module_ensure(module_name)
                                   : pcc_module_find(module_name);
    if (n == NULL) return NULL;
    return n->attrs;
}

int64_t py_module_attr_set(const char *module_name,
                           const char *attr_name,
                           PyObject *value) {
    if (attr_name == NULL || value == NULL) return -1;
    PccModuleAttrsNode *n = pcc_module_ensure(module_name);
    if (n == NULL) return -1;
    PyObject *key = py_str_new(attr_name, (int64_t)strlen(attr_name));
    if (key == NULL) return -1;
    py_dict_set(n->attrs, key, value);
    py_decref(key);
    return 0;
}

PyObject *py_module_attr_get(const char *module_name, const char *attr_name) {
    if (attr_name == NULL) return NULL;
    PccModuleAttrsNode *n = pcc_module_find(module_name);
    if (n == NULL) return NULL;
    PyObject *key = py_str_new(attr_name, (int64_t)strlen(attr_name));
    if (key == NULL) return NULL;
    PyObject *value = py_dict_get(n->attrs, key);
    py_decref(key);
    return value;
}

static int pcc_module_import_star_name(PyObject *dest,
                                       PyObject *source_module,
                                       PyObject *source_attrs,
                                       PyObject *name) {
    if (name == NULL || PY_IS_TAGGED_INT(name) || py_type_of(name) != PY_TYPE_STR) {
        py_raise_owned(py_exc_new(
            PY_EXC_TYPEERROR,
            "module __all__ must contain only strings"
        ));
        return -1;
    }
    PyObject *value = py_dict_get(source_attrs, name);
    if (value == NULL) {
        const char *attr_name = py_str_utf8(name);
        if (attr_name != NULL) value = py_obj_getattr(source_module, attr_name);
    }
    if (value == NULL) return -1;
    py_dict_set(dest, name, value);
    py_decref(value);
    return 0;
}

int64_t py_module_import_star(const char *module_name, PyObject *source_module) {
    if (module_name == NULL || source_module == NULL) return -1;
    PyObject *source_attrs = py_obj_getattr(source_module, "__dict__");
    if (source_attrs == NULL) return -1;
    PyObject *dest = py_module_attrs_dict(module_name, 1);
    if (dest == NULL) {
        py_decref(source_attrs);
        return -1;
    }

    PyObject *all_key = py_str_new("__all__", 7);
    if (all_key == NULL) {
        py_decref(source_attrs);
        return -1;
    }
    PyObject *all_names = py_dict_get(source_attrs, all_key);
    py_decref(all_key);
    if (all_names != NULL) {
        int64_t count = py_obj_len(all_names);
        if (count < 0) {
            py_decref(all_names);
            py_decref(source_attrs);
            return -1;
        }
        for (int64_t i = 0; i < count; i++) {
            PyObject *name = py_obj_getitem_i64(all_names, i);
            if (name == NULL) {
                py_decref(all_names);
                py_decref(source_attrs);
                return -1;
            }
            int rc = pcc_module_import_star_name(
                dest, source_module, source_attrs, name
            );
            py_decref(name);
            if (rc != 0) {
                py_decref(all_names);
                py_decref(source_attrs);
                return -1;
            }
        }
        py_decref(all_names);
        py_decref(source_attrs);
        return 0;
    }

    int64_t entries = py_dict_entries_used(source_attrs);
    for (int64_t i = 0; i < entries; i++) {
        PyObject *name = py_dict_entry_key_at(source_attrs, i);
        if (name == NULL) continue;
        if (!PY_IS_TAGGED_INT(name) && py_type_of(name) == PY_TYPE_STR) {
            const char *text = py_str_utf8(name);
            if (text != NULL && text[0] != '_') {
                if (pcc_module_import_star_name(
                        dest, source_module, source_attrs, name
                    ) != 0) {
                    py_decref(name);
                    py_decref(source_attrs);
                    return -1;
                }
            }
        }
        py_decref(name);
    }
    py_decref(source_attrs);
    return 0;
}

PyObject *py_module_attr_value_or_default(PyObject **slot,
                                          PyObject *default_value) {
    if (slot == NULL || *slot == NULL) return default_value;
    if (default_value != NULL) py_decref(default_value);
    return pcc_gc_load_ptr(NULL, slot);
}

int64_t py_module_attr_del(const char *module_name, const char *attr_name) {
    if (attr_name == NULL) return -1;
    PccModuleAttrsNode *n = pcc_module_find(module_name);
    if (n == NULL) return -1;
    PyObject *key = py_str_new(attr_name, (int64_t)strlen(attr_name));
    if (key == NULL) return -1;
    int64_t rc = py_dict_del(n->attrs, key);
    py_decref(key);
    return rc;
}

int64_t py_module_attr_len(const char *module_name) {
    PccModuleAttrsNode *n = pcc_module_find(module_name);
    if (n == NULL) return 0;
    return py_dict_len(n->attrs);
}
