/* pcc/py_runtime/src/py_os_native.c
 *
 * Pure-C os.* primitives that BOTH the host-cc archive and the
 * pcc-Python no-libpython archive link. Mirrors the
 * py_gc_index_table.c slot: a .py port would have to re-implement
 * platform-specific syscalls (sysconf constant, struct iteration over
 * PyStr internals) that aren't worth the bootstrap risk for one
 * function. Keeping these in a non-replaced .c file means both
 * archives pick up the same definition without a parallel pcc-Python
 * port.
 */

#include "py_internal.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/utsname.h>
#include <unistd.h>

#ifndef PCC_USE_FREESTANDING_PLATFORM_SYSTEM
/* `os.uname()` — five strings in CPython's sequence order. The compiler also
 * recognizes direct `.sysname` / `.machine` access on the result. Keeping the
 * syscall in this non-replaced C-kernel module gives the cc and pcc-Python
 * runtime archives one implementation and avoids baking build-host values
 * into deployable artifacts. */
PyObject *py_os_uname(void) {
    struct utsname raw;
    if (uname(&raw) != 0) {
        py_raise(py_exc_new(PY_EXC_OSERROR, "os.uname() failed"));
        return NULL;
    }

    const char *fields[5] = {
        raw.sysname,
        raw.nodename,
        raw.release,
        raw.version,
        raw.machine,
    };
    PyObject *out = py_tuple_new(5);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < 5; i++) {
        PyObject *field = py_str_new(fields[i], (int64_t)strlen(fields[i]));
        if (field == NULL) {
            py_decref(out);
            return NULL;
        }
        py_tuple_set_item(out, i, field);
        py_decref(field);
    }
    return out;
}

/* `os.cpu_count()` — number of CPUs the current process can use.
 * CPython returns None when undeterminable; pcc returns a tagged-int
 * 0 so callers that wrote `cpu_count() or 1` keep working (zero is
 * falsy, the `or` arm fires). */
PyObject *py_os_cpu_count(void) {
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    if (n <= 0) return py_int_from_i64(0);
    return py_int_from_i64((int64_t)n);
}
#endif

static PyObject *py_os_native_coerce_path_str(
    PyObject *o, PyObject **owned
) {
    *owned = NULL;
    if (o == NULL) return NULL;
    if (py_type_of(o) == PY_TYPE_STR) return o;
    *owned = py_obj_str(o);
    return *owned;
}

static int64_t py_os_native_seq_len(PyObject *parts) {
    if (parts == NULL || PY_IS_TAGGED_INT(parts)) return -1;
    int32_t tag = py_type_of(parts);
    if (tag == PY_TYPE_LIST) return ((PyListObject *)parts)->length;
    if (tag == PY_TYPE_TUPLE) return ((PyTupleObject *)parts)->len;
    return -1;
}

static PyObject *py_os_native_seq_borrow(PyObject *parts, int64_t i) {
    if (parts == NULL || PY_IS_TAGGED_INT(parts)) return NULL;
    int32_t tag = py_type_of(parts);
    if (tag == PY_TYPE_LIST) {
        return pcc_gc_load_ptr(
            parts,
            &((PyListObject *)parts)->items[i]
        );
    }
    if (tag == PY_TYPE_TUPLE) {
        return pcc_gc_load_ptr(
            parts,
            &((PyTupleObject *)parts)->items[i]
        );
    }
    return NULL;
}

/* `os.path.commonpath(paths)` — longest path prefix shared by every
 * entry, split at '/' boundaries. POSIX-only (pcc targets). Returns
 * "" on empty / NULL input; does NOT raise CPython's ValueError on
 * mixed absolute/relative input — pipeline.py only feeds it
 * normalized paths so the simpler contract is sufficient. */
PyObject *py_os_path_commonpath(PyObject *paths) {
    int64_t n = py_os_native_seq_len(paths);
    if (n <= 0) return py_str_new("", 0);

    PyObject **owned_arr = (PyObject **)calloc(
        (size_t)n, sizeof(PyObject *)
    );
    PyObject **items = (PyObject **)calloc((size_t)n, sizeof(PyObject *));
    if (owned_arr == NULL || items == NULL) {
        free(owned_arr);
        free(items);
        return NULL;
    }
    for (int64_t i = 0; i < n; i++) {
        PyObject *raw = py_os_native_seq_borrow(paths, i);
        items[i] = py_os_native_coerce_path_str(raw, &owned_arr[i]);
        if (items[i] == NULL) {
            for (int64_t j = 0; j <= i; j++) py_decref(owned_arr[j]);
            free(owned_arr);
            free(items);
            return NULL;
        }
    }

    PyStrObject *first = (PyStrObject *)items[0];
    int64_t prefix_len = first->byte_len;
    for (int64_t i = 1; i < n; i++) {
        PyStrObject *cur = (PyStrObject *)items[i];
        int64_t lim = first->byte_len < cur->byte_len
            ? first->byte_len : cur->byte_len;
        if (lim < prefix_len) prefix_len = lim;
        int64_t j = 0;
        while (j < prefix_len && first->data[j] == cur->data[j]) j++;
        prefix_len = j;
        if (prefix_len == 0) break;
    }

    if (prefix_len > 0) {
        int diverged = 0;
        for (int64_t i = 1; i < n; i++) {
            PyStrObject *cur = (PyStrObject *)items[i];
            if (cur->byte_len > prefix_len
                && cur->data[prefix_len] != '/') {
                diverged = 1;
                break;
            }
        }
        if (!diverged && prefix_len < first->byte_len
            && first->data[prefix_len] != '/') {
            diverged = 1;
        }
        if (diverged) {
            while (prefix_len > 0 && first->data[prefix_len - 1] != '/') {
                prefix_len--;
            }
        }
        while (prefix_len > 1 && first->data[prefix_len - 1] == '/') {
            prefix_len--;
        }
    }

    PyObject *out = py_str_new(first->data, prefix_len);
    for (int64_t i = 0; i < n; i++) py_decref(owned_arr[i]);
    free(owned_arr);
    free(items);
    return out;
}

/* `os.path.expandvars(path)` — POSIX env-var expansion: replace `$name` and
 * `${name}` with `os.environ[name]`; an unset or malformed var reference is
 * left verbatim (CPython posixpath.expandvars; `\w` == [A-Za-z0-9_], `$$` is
 * not special on POSIX). C-only helper (the char-scan + getenv loop is awkward
 * to mirror in the pcc-Python port; this .c is linked by both archives, like
 * py_os_path_commonpath above). */
PyObject *py_os_path_expandvars(PyObject *path) {
    PyObject *owned = NULL;
    PyObject *item = py_os_native_coerce_path_str(path, &owned);
    if (item == NULL) {
        py_decref(owned);
        return NULL;
    }
    PyStrObject *s = (PyStrObject *)item;
    int64_t n = s->byte_len;
    const char *d = s->data;

    int has_dollar = 0;
    for (int64_t i = 0; i < n; i++) {
        if (d[i] == '$') { has_dollar = 1; break; }
    }
    if (!has_dollar) {
        PyObject *out = py_str_new(d, n);
        py_decref(owned);
        return out;
    }

    size_t cap = (size_t)n + 16;
    char *buf = (char *)malloc(cap);
    if (buf == NULL) { py_decref(owned); return NULL; }
    size_t out_len = 0;
    int64_t i = 0;
    int oom = 0;

    while (i < n) {
        const char *seg = NULL;   /* bytes to append for this step */
        size_t seg_len = 0;
        int64_t advance = 1;
        char c = d[i];
        if (c != '$' || i + 1 >= n) {
            seg = &d[i];
            seg_len = 1;
            advance = 1;
        } else {
            char nx = d[i + 1];
            int is_name_char = (nx == '_'
                || (nx >= 'A' && nx <= 'Z')
                || (nx >= 'a' && nx <= 'z')
                || (nx >= '0' && nx <= '9'));
            if (nx == '{') {
                int64_t j = i + 2;
                while (j < n && d[j] != '}') j++;
                if (j < n) {
                    int64_t name_len = j - (i + 2);
                    char *name = (char *)malloc((size_t)name_len + 1);
                    if (name == NULL) { oom = 1; break; }
                    memcpy(name, &d[i + 2], (size_t)name_len);
                    name[name_len] = '\0';
                    const char *val = pcc_runtime_getenv(name);
                    free(name);
                    if (val != NULL) {
                        seg = val;
                        seg_len = strlen(val);
                    } else {
                        seg = &d[i];          /* unset -> verbatim ${name} */
                        seg_len = (size_t)(j - i + 1);
                    }
                    advance = (j + 1) - i;
                } else {
                    seg = &d[i];              /* no closing brace -> verbatim $ */
                    seg_len = 1;
                    advance = 1;
                }
            } else if (is_name_char) {
                int64_t j = i + 1;
                while (j < n
                       && (d[j] == '_'
                           || (d[j] >= 'A' && d[j] <= 'Z')
                           || (d[j] >= 'a' && d[j] <= 'z')
                           || (d[j] >= '0' && d[j] <= '9'))) {
                    j++;
                }
                int64_t name_len = j - (i + 1);
                char *name = (char *)malloc((size_t)name_len + 1);
                if (name == NULL) { oom = 1; break; }
                memcpy(name, &d[i + 1], (size_t)name_len);
                name[name_len] = '\0';
                const char *val = pcc_runtime_getenv(name);
                free(name);
                if (val != NULL) {
                    seg = val;
                    seg_len = strlen(val);
                } else {
                    seg = &d[i];              /* unset -> verbatim $name */
                    seg_len = (size_t)(j - i);
                }
                advance = j - i;
            } else {
                seg = &d[i];                  /* $ + non-name char -> literal $ */
                seg_len = 1;
                advance = 1;
            }
        }
        while (out_len + seg_len + 1 > cap) {
            cap *= 2;
            char *nb = (char *)realloc(buf, cap);
            if (nb == NULL) { oom = 1; break; }
            buf = nb;
        }
        if (oom) break;
        memcpy(buf + out_len, seg, seg_len);
        out_len += seg_len;
        i += advance;
    }

    if (oom) {
        free(buf);
        py_decref(owned);
        return NULL;
    }
    PyObject *out = py_str_new(buf, (int64_t)out_len);
    free(buf);
    py_decref(owned);
    return out;
}

/* Collect non-empty '/'-separated component spans of `d[0..n)` into
 * (offs[k], lens[k]), NORMALISING in place: a "." component is skipped and a
 * ".." component pops the previous one (dropped at the root, matching
 * posixpath.normpath on an ABSOLUTE path). Returns the component count.
 * `offs`/`lens` must hold at least `n` entries (an upper bound).
 *
 * This makes py_os_path_relpath robust even though the native
 * py_os_path_abspath that the dispatch wraps the args in only cwd-prefixes and
 * does NOT itself resolve '.'/'..' — so `relpath('.')` / `relpath('a/./b/../c')`
 * still match CPython. */
static int64_t py_os_native_split_components(
    const char *d, int64_t n, int64_t *offs, int64_t *lens
) {
    int64_t count = 0;
    int64_t i = 0;
    while (i < n) {
        while (i < n && d[i] == '/') i++;
        if (i >= n) break;
        int64_t start = i;
        while (i < n && d[i] != '/') i++;
        int64_t len = i - start;
        if (len == 1 && d[start] == '.') {
            continue;                          /* "." -> skip */
        }
        if (len == 2 && d[start] == '.' && d[start + 1] == '.') {
            if (count > 0) count--;            /* ".." -> pop (root: drop) */
            continue;
        }
        offs[count] = start;
        lens[count] = len;
        count++;
    }
    return count;
}

/* `os.path.relpath(path, start)` — relative path from `start` to `path`. The
 * caller (native_os.py dispatch) wraps BOTH arguments in `os.path.abspath`, so
 * here both are already absolute + normpath'd (no '.'/'..' components); this is
 * the pure component-diff tail of CPython posixpath.relpath. C-only helper
 * (component logic, like py_os_path_commonpath above; both archives link it). */
PyObject *py_os_path_relpath(PyObject *path, PyObject *start) {
    PyObject *po = NULL, *so = NULL;
    PyObject *pi = py_os_native_coerce_path_str(path, &po);
    PyObject *si = py_os_native_coerce_path_str(start, &so);
    if (pi == NULL || si == NULL) {
        py_decref(po);
        py_decref(so);
        return NULL;
    }
    PyStrObject *ps = (PyStrObject *)pi;
    PyStrObject *ss = (PyStrObject *)si;
    const char *pd = ps->data;
    int64_t pn = ps->byte_len;
    const char *sd = ss->data;
    int64_t sn = ss->byte_len;

    int64_t *poff = (int64_t *)malloc((size_t)(pn + 1) * sizeof(int64_t));
    int64_t *plen = (int64_t *)malloc((size_t)(pn + 1) * sizeof(int64_t));
    int64_t *soff = (int64_t *)malloc((size_t)(sn + 1) * sizeof(int64_t));
    int64_t *slen = (int64_t *)malloc((size_t)(sn + 1) * sizeof(int64_t));
    if (poff == NULL || plen == NULL || soff == NULL || slen == NULL) {
        free(poff); free(plen); free(soff); free(slen);
        py_decref(po); py_decref(so);
        return NULL;
    }
    int64_t pcount = py_os_native_split_components(pd, pn, poff, plen);
    int64_t scount = py_os_native_split_components(sd, sn, soff, slen);

    int64_t i = 0;
    while (i < pcount && i < scount
           && plen[i] == slen[i]
           && memcmp(pd + poff[i], sd + soff[i], (size_t)plen[i]) == 0) {
        i++;
    }

    int64_t up = scount - i;                 /* ".." per extra start component */
    int64_t tail = pcount - i;               /* remaining path components */
    int64_t total = up + tail;
    if (total == 0) {
        free(poff); free(plen); free(soff); free(slen);
        py_decref(po); py_decref(so);
        return py_str_new(".", 1);
    }

    int64_t buf_len = 2 * up;                 /* ".." == 2 bytes each */
    for (int64_t j = i; j < pcount; j++) buf_len += plen[j];
    buf_len += (total - 1);                   /* '/' separators */

    PyObject *out = py_str_new(NULL, buf_len);
    if (out == NULL) {
        free(poff); free(plen); free(soff); free(slen);
        py_decref(po); py_decref(so);
        return NULL;
    }
    PyStrObject *outs = (PyStrObject *)out;
    int64_t pos = 0;
    int64_t written = 0;
    for (int64_t k = 0; k < up; k++) {
        if (written > 0) outs->data[pos++] = '/';
        outs->data[pos++] = '.';
        outs->data[pos++] = '.';
        written++;
    }
    for (int64_t j = i; j < pcount; j++) {
        if (written > 0) outs->data[pos++] = '/';
        memcpy(outs->data + pos, pd + poff[j], (size_t)plen[j]);
        pos += plen[j];
        written++;
    }

    free(poff); free(plen); free(soff); free(slen);
    py_decref(po); py_decref(so);
    return out;
}
