/* Host-C oracle for the production pcc-Python extension loader.
 *
 * The production no-libpython archive owns these ABIs in
 * py/py_extension_loader_runtime.py; this source remains an explicit host-C
 * and pcc-C oracle input.
 *
 * pcc-native extension loader.
 *
 * Loads package-installed extension artifacts without libpython. The loaded
 * extension must be built against pcc's narrow Python.h/C-API shim and export
 * PyInit_<leaf_module>() returning a pcc PyObject* module object.
 */

#include "py_internal.h"
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef PyObject *(*PccExtensionInitFn)(void);

typedef struct PccExtensionModuleNode {
    char *module_name;
    char *path;
    void *handle;
    PyObject *module;
    struct PccExtensionModuleNode *next;
} PccExtensionModuleNode;

static PccExtensionModuleNode *pcc_extension_modules = NULL;

static char *pcc_ext_strdup(const char *s) {
    if (s == NULL) s = "";
    size_t n = strlen(s);
    char *out = (char *)malloc(n + 1);
    if (out == NULL) return NULL;
    memcpy(out, s, n + 1);
    return out;
}

/* PCC_DEBUG_EXT_IMPORT=1: off-by-default stderr trace of extension-module
 * loads (cache hit/miss, exec begin, registration). The ordering of these
 * events against a cext's own exec-time imports is what diagnosed the numpy
 * re-init and parent-package-order bugs; see
 * docs/investigations/numpy-loader-probe-cext-reimport-load-once.md. */
static int pcc_ext_debug(void) {
    static int v = -1;
    if (v < 0) v = pcc_runtime_getenv("PCC_DEBUG_EXT_IMPORT") != NULL ? 1 : 0;
    return v;
}

static PccExtensionModuleNode *pcc_extension_find_module(const char *module_name) {
    for (PccExtensionModuleNode *n = pcc_extension_modules; n != NULL; n = n->next) {
        if (strcmp(n->module_name, module_name ? module_name : "") == 0) {
            return n;
        }
    }
    return NULL;
}

static const char *pcc_extension_leaf(const char *module_name) {
    const char *leaf = module_name ? module_name : "";
    for (const char *p = leaf; *p != '\0'; p++) {
        if (*p == '.') leaf = p + 1;
    }
    return leaf;
}

static char *pcc_extension_init_symbol(const char *module_name) {
    const char *leaf = pcc_extension_leaf(module_name);
    size_t n = strlen(leaf);
    char *symbol = (char *)malloc(n + 8);
    if (symbol == NULL) return NULL;
    memcpy(symbol, "PyInit_", 7);
    memcpy(symbol + 7, leaf, n + 1);
    return symbol;
}

/* Take ownership of the caller's ref on `module` and publish it in the
 * load-once cache. Returns the node, or NULL on allocation failure (caller
 * keeps its ref). */
static PccExtensionModuleNode *pcc_extension_register(
    const char *module_name, const char *path, void *handle, PyObject *module
) {
    PccExtensionModuleNode *node =
        (PccExtensionModuleNode *)calloc(1, sizeof(PccExtensionModuleNode));
    if (node == NULL) return NULL;
    node->module_name = pcc_ext_strdup(module_name);
    node->path = pcc_ext_strdup(path);
    if (node->module_name == NULL || node->path == NULL) {
        free(node->module_name);
        free(node->path);
        free(node);
        return NULL;
    }
    node->handle = handle;
    node->module = module;
    pcc_gc_pin(module);
    node->next = pcc_extension_modules;
    pcc_extension_modules = node;
    if (pcc_ext_debug()) {
        fprintf(stderr, "[ext-import] registered name=%s\n", module_name);
    }
    return node;
}

/* Roll a failed registration back out of the cache (exec-slot failure).
 * Releases the node's module ref; the caller still owns dlclose. */
static void pcc_extension_unregister(PccExtensionModuleNode *node) {
    PccExtensionModuleNode **link = &pcc_extension_modules;
    while (*link != NULL && *link != node) link = &(*link)->next;
    if (*link == node) *link = node->next;
    pcc_gc_unpin(node->module);
    py_decref(node->module);
    free(node->module_name);
    free(node->path);
    free(node);
}

static PyObject *pcc_extension_runtime_error(const char *prefix, const char *detail) {
    char buf[768];
    const char *p = prefix ? prefix : "native extension import failed";
    const char *d = detail ? detail : "";
    size_t pn = strlen(p);
    size_t dn = strlen(d);
    if (pn > 350) pn = 350;
    if (dn > 350) dn = 350;
    memcpy(buf, p, pn);
    buf[pn] = ':';
    buf[pn + 1] = ' ';
    memcpy(buf + pn + 2, d, dn);
    buf[pn + 2 + dn] = '\0';
    py_raise_owned(py_exc_new(PY_EXC_RUNTIMEERROR, buf));
    return NULL;
}

PyObject *py_native_extension_import(const char *module_name, const char *path) {
    if (module_name == NULL || path == NULL || module_name[0] == '\0' || path[0] == '\0') {
        return pcc_extension_runtime_error("native extension import failed", "missing module name or path");
    }

    /* Cache lookup is keyed by the fully qualified module NAME, matching
     * CPython's sys.modules: a second import of the same dotted name returns
     * the already-initialized module even when it was resolved through a
     * different filesystem path (compile-time baked path vs PCC_PACKAGE_SITE
     * search can legitimately name the same module twice). Keying on
     * (name, path) re-ran PyInit_* for the second path, which C extensions
     * with process-global init guards (numpy's _multiarray_umath) reject with
     * "cannot load module more than once per process". */
    PccExtensionModuleNode *cached = pcc_extension_find_module(module_name);
    if (pcc_ext_debug()) {
        fprintf(stderr, "[ext-import] name=%s cached=%d path=%s\n",
                module_name, cached != NULL && cached->module != NULL, path);
    }
    if (cached != NULL && cached->module != NULL) {
        py_incref(cached->module);
        return cached->module;
    }

    /* CPython initializes parent packages before loading a submodule
     * extension. Running the compiled parents here means the packages that
     * surround a C extension are in progress (or done) before its exec slots
     * run, so exec-time imports back into the package see CPython's
     * partial-module state instead of re-running whole package bodies. The
     * parent init may itself import this extension; re-check the cache. */
    if (py_compiled_module_ensure_parent_packages(module_name) != 0) return NULL;
    cached = pcc_extension_find_module(module_name);
    if (cached != NULL && cached->module != NULL) {
        py_incref(cached->module);
        return cached->module;
    }

    void *handle = dlopen(path, RTLD_NOW | RTLD_GLOBAL);
    if (handle == NULL) {
        return pcc_extension_runtime_error("dlopen failed", dlerror());
    }

    char *symbol = pcc_extension_init_symbol(module_name);
    if (symbol == NULL) {
        dlclose(handle);
        return pcc_extension_runtime_error("native extension import failed", "out of memory");
    }
    dlerror();
    PccExtensionInitFn init = (PccExtensionInitFn)dlsym(handle, symbol);
    const char *sym_error = dlerror();
    if (sym_error != NULL || init == NULL) {
        free(symbol);
        dlclose(handle);
        return pcc_extension_runtime_error("dlsym failed", sym_error);
    }
    free(symbol);

    PyObject *module = init();
    if (module == NULL) {
        dlclose(handle);
        if (!py_err_occurred()) {
            return pcc_extension_runtime_error("native extension init failed", module_name);
        }
        return NULL;
    }
    /* Multi-phase init (PEP 489): PyInit_* returned a module DEF, not a ready
     * module. Build the module, REGISTER it, then run its Py_mod_exec slots
     * (numpy registers its types / PyArray_API capsule there). Registration
     * must precede exec — CPython puts the module into sys.modules before
     * exec_module — so a nested import of the same module from inside an exec
     * slot's Python imports returns the in-progress module instead of
     * re-running PyInit (numpy's _multiarray_umath hard-fails a second init
     * per process). */
    if (pcc_capi_is_moduledef(module)) {
        PyObject *def_obj = module;
        PyObject *built = pcc_capi_module_from_def(def_obj);
        if (built == NULL) {
            dlclose(handle);
            if (!py_err_occurred()) {
                return pcc_extension_runtime_error("native extension exec failed", module_name);
            }
            return NULL;
        }
        PccExtensionModuleNode *node =
            pcc_extension_register(module_name, path, handle, built);
        if (node == NULL) {
            py_decref(built);
            dlclose(handle);
            return pcc_extension_runtime_error("native extension import failed", "out of memory");
        }
        if (pcc_ext_debug()) {
            fprintf(stderr, "[ext-import] exec-begin name=%s\n", module_name);
        }
        if (pcc_capi_module_run_exec_slots(def_obj, built) != 0) {
            pcc_extension_unregister(node);
            dlclose(handle);
            if (!py_err_occurred()) {
                return pcc_extension_runtime_error("native extension exec failed", module_name);
            }
            return NULL;
        }
        py_incref(built);
        return built;
    }

    PccExtensionModuleNode *node =
        pcc_extension_register(module_name, path, handle, module);
    if (node == NULL) {
        py_decref(module);
        dlclose(handle);
        return pcc_extension_runtime_error("native extension import failed", "out of memory");
    }
    py_incref(module);
    return module;
}

static char *pcc_extension_module_relpath(const char *module_name) {
    if (module_name == NULL || module_name[0] == '\0') return NULL;
    size_t n = strlen(module_name);
    char *rel = (char *)malloc(n + 1);
    if (rel == NULL) return NULL;
    for (size_t i = 0; i < n; i++) {
        rel[i] = module_name[i] == '.' ? '/' : module_name[i];
    }
    rel[n] = '\0';
    return rel;
}

static char *pcc_extension_candidate_path(const char *site,
                                          size_t site_len,
                                          const char *rel,
                                          const char *ext) {
    if (site == NULL || site_len == 0 || rel == NULL || ext == NULL) return NULL;
    size_t rel_len = strlen(rel);
    size_t ext_len = strlen(ext);
    int needs_slash = site[site_len - 1] != '/';
    size_t total = site_len + (needs_slash ? 1u : 0u) + rel_len + ext_len;
    char *path = (char *)malloc(total + 1);
    if (path == NULL) return NULL;
    memcpy(path, site, site_len);
    size_t pos = site_len;
    if (needs_slash) path[pos++] = '/';
    memcpy(path + pos, rel, rel_len);
    pos += rel_len;
    memcpy(path + pos, ext, ext_len);
    pos += ext_len;
    path[pos] = '\0';
    return path;
}

static int pcc_extension_path_exists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) return 0;
    fclose(f);
    return 1;
}

PyObject *py_native_extension_import_by_name(const char *module_name) {
    if (module_name == NULL || module_name[0] == '\0') return NULL;
    PccExtensionModuleNode *cached = pcc_extension_find_module(module_name);
    if (pcc_ext_debug()) {
        fprintf(stderr, "[ext-import] by-name name=%s cached=%d\n",
                module_name, cached != NULL && cached->module != NULL);
    }
    if (cached != NULL && cached->module != NULL) {
        py_incref(cached->module);
        return cached->module;
    }

    /* Parent packages first (see py_native_extension_import); the parent
     * init may import this extension, so re-check the cache afterwards. */
    if (py_compiled_module_ensure_parent_packages(module_name) != 0) return NULL;
    cached = pcc_extension_find_module(module_name);
    if (cached != NULL && cached->module != NULL) {
        py_incref(cached->module);
        return cached->module;
    }

    const char *sites = pcc_runtime_getenv("PCC_PACKAGE_SITE");
    if (sites == NULL || sites[0] == '\0') return NULL;
    char *rel = pcc_extension_module_relpath(module_name);
    if (rel == NULL) return NULL;

    static const char *exts[] = {".so", ".dylib", ".pyd", ".dll", NULL};
    const char *start = sites;
    while (*start != '\0') {
        const char *end = start;
        while (
            *end != '\0'
#ifdef _WIN32
            && *end != ';'
#else
            && *end != ':'
#endif
        ) end++;
        size_t site_len = (size_t)(end - start);
        if (site_len > 0) {
            for (int i = 0; exts[i] != NULL; i++) {
                char *path = pcc_extension_candidate_path(start, site_len, rel, exts[i]);
                if (path == NULL) continue;
                if (pcc_extension_path_exists(path)) {
                    PyObject *module = py_native_extension_import(module_name, path);
                    free(path);
                    free(rel);
                    return module;
                }
                free(path);
            }
        }
        start = *end != '\0' ? end + 1 : end;
    }

    free(rel);
    return NULL;
}
