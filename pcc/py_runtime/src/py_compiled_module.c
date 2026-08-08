/* pcc-owned compiled-module registry.
 *
 * This is runtime infrastructure, not part of the CPython C-API shim.  It is
 * linked into both pcc-native/no-libpython and cpython-compat/libpython
 * archives so generated module init/import calls never depend on whether the
 * extension-facing shim is present.
 */

#include "py_internal.h"

#include <stdlib.h>
#include <string.h>


PyClassObject *pcc_runtime_module_class(void) {
    static PyClassObject *cls = NULL;
    if (cls != NULL) return cls;
    cls = py_class_new("module", NULL, 0, NULL, 0);
    if (cls != NULL) pcc_gc_pin((PyObject *)cls);
    return cls;
}

typedef struct PccCompiledModuleNode {
    char *name;
    PyObject *module;
    struct PccCompiledModuleNode *next;
} PccCompiledModuleNode;

static PccCompiledModuleNode *pcc_compiled_modules = NULL;

typedef void (*PccCompiledModuleInitFn)(void);

typedef struct PccCompiledModuleInitNode {
    char *name;
    PccCompiledModuleInitFn init;
    int state; /* 0 = not started, 1 = initializing, 2 = invoked */
    struct PccCompiledModuleInitNode *next;
} PccCompiledModuleInitNode;

static PccCompiledModuleInitNode *pcc_compiled_module_inits = NULL;

static void pcc_compiled_module_no_memory(void) {
    PyObject *exc = py_exc_new(PY_EXC_MEMORYERROR, "out of memory");
    if (exc != NULL) py_raise_owned(exc);
}

int64_t py_compiled_module_register_init(const char *name, void *init_fn) {
    if (name == NULL || name[0] == '\0' || init_fn == NULL) return -1;
    for (
        PccCompiledModuleInitNode *n = pcc_compiled_module_inits;
        n != NULL;
        n = n->next
    ) {
        if (strcmp(n->name, name) == 0) {
            n->init = (PccCompiledModuleInitFn)init_fn;
            return 0;
        }
    }
    PccCompiledModuleInitNode *node = (
        PccCompiledModuleInitNode *
    )calloc(1, sizeof(PccCompiledModuleInitNode));
    if (node == NULL) return -1;
    size_t name_len = strlen(name);
    node->name = (char *)malloc(name_len + 1);
    if (node->name == NULL) {
        free(node);
        return -1;
    }
    memcpy(node->name, name, name_len + 1);
    node->init = (PccCompiledModuleInitFn)init_fn;
    node->next = pcc_compiled_module_inits;
    pcc_compiled_module_inits = node;
    return 0;
}

static int pcc_run_compiled_module_init(const char *name) {
    for (
        PccCompiledModuleInitNode *n = pcc_compiled_module_inits;
        n != NULL;
        n = n->next
    ) {
        if (strcmp(n->name, name) != 0) continue;
        if (n->state != 0) return 0;
        n->state = 1;
        n->init();
        if (py_err_occurred()) {
            n->state = 0;
            return -1;
        }
        n->state = 2;
        return 0;
    }
    return 0;
}

static int pcc_compiled_module_has_init(const char *name) {
    if (name == NULL || name[0] == '\0') return 0;
    for (
        PccCompiledModuleInitNode *n = pcc_compiled_module_inits;
        n != NULL;
        n = n->next
    ) {
        if (strcmp(n->name, name) == 0) return 1;
    }
    return 0;
}

/* Importing a.b.c initializes a, then a.b, before a.b.c itself.  In-progress
 * parents return early, preserving partial-module behavior through cycles. */
int py_compiled_module_ensure_parent_packages(const char *module_name) {
    if (module_name == NULL) return 0;
    const char *dot = strchr(module_name, '.');
    while (dot != NULL) {
        size_t len = (size_t)(dot - module_name);
        char *parent = (char *)malloc(len + 1);
        if (parent == NULL) {
            pcc_compiled_module_no_memory();
            return -1;
        }
        memcpy(parent, module_name, len);
        parent[len] = '\0';
        int rc = pcc_run_compiled_module_init(parent);
        free(parent);
        if (rc != 0) return -1;
        dot = strchr(dot + 1, '.');
    }
    return 0;
}

static int pcc_run_compiled_module_init_with_parents(const char *name) {
    if (py_compiled_module_ensure_parent_packages(name) != 0) return -1;
    return pcc_run_compiled_module_init(name);
}

PyObject *py_compiled_module_import_by_name(const char *name) {
    if (name == NULL || name[0] == '\0') return NULL;
    for (
        PccCompiledModuleNode *n = pcc_compiled_modules;
        n != NULL;
        n = n->next
    ) {
        if (strcmp(n->name, name) == 0) {
            py_incref(n->module);
            return n->module;
        }
    }

    /* The module attributes side table is intentionally create-on-write.
     * Do not let that implementation detail turn an unknown import into a
     * successful empty module: only linked modules with a registered guarded
     * initializer belong to this registry. */
    if (!pcc_compiled_module_has_init(name)) return NULL;

    if (pcc_run_compiled_module_init_with_parents(name) != 0) return NULL;

    PyObject *attrs = py_module_attrs_dict(name, 0);
    if (attrs == NULL) return NULL;
    PyClassObject *cls = pcc_runtime_module_class();
    if (cls == NULL) return NULL;
    PyObject *module = py_instance_new(cls);
    if (module == NULL) return NULL;

    /* Share the compiled module's live side-table dictionary rather than a
     * copied namespace snapshot. */
    PyInstanceObject *inst = (PyInstanceObject *)module;
    pcc_gc_store_ptr(module, &inst->fields[0], attrs);
    PyObject *name_obj = py_str_new(name, (int64_t)strlen(name));
    if (name_obj == NULL) {
        py_decref(module);
        return NULL;
    }
    int64_t name_rc = py_instance_setattr(inst, "__name__", name_obj);
    py_decref(name_obj);
    if (name_rc != 0) {
        py_decref(module);
        return NULL;
    }

    PccCompiledModuleNode *node = (
        PccCompiledModuleNode *
    )calloc(1, sizeof(PccCompiledModuleNode));
    if (node == NULL) {
        py_decref(module);
        return NULL;
    }
    size_t name_len = strlen(name);
    node->name = (char *)malloc(name_len + 1);
    if (node->name == NULL) {
        free(node);
        py_decref(module);
        return NULL;
    }
    memcpy(node->name, name, name_len + 1);
    node->module = module;
    pcc_gc_pin(module);
    node->next = pcc_compiled_modules;
    pcc_compiled_modules = node;
    py_incref(module);
    return module;
}
