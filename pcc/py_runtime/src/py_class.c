/* pcc/py_runtime/src/py_class.c
 *
 * Class + instance runtime for Phase 3.
 *
 * Layouts live in py_internal.h. This file implements:
 *   - py_class_new / py_class_add_method / py_class_lookup
 *   - py_instance_new / py_instance_get_field / py_instance_set_field
 *   - py_instance_getattr / py_instance_setattr
 *   - py_isinstance
 *   - py_super_lookup
 *   - c3_linearize (PEP 3119 / CPython typeobject.c:mro_implementation)
 *   - py_class_dealloc / py_instance_dealloc
 *
 * Design notes:
 *
 *   * A class is itself a PyObject carrying PY_TYPE_CLASS. Refcount is
 *     1 (it is expected to live for the process lifetime because the
 *     codegen emits it as a module-init side effect and stores a pointer
 *     in a global). Freeing a class is still supported for completeness.
 *
 *   * Instances carry either PY_TYPE_INSTANCE or a class-specific
 *     PY_TYPE_USER + N tag. The codegen can choose either; py_instance_*
 *     and py_obj_ops functions check for both tags.
 *
 *   * Method dispatch is linear over (methods[] of mro[0..n_mro-1]).
 *     Classes have small method tables so this is faster than a dict
 *     in the common case. A future phase can swap to a hashmap.
 *
 *   * C3 linearization follows the exact "merge" algorithm from PEP 3119.
 *     The implementation allocates scratch buffers with malloc and frees
 *     them on the way out; no global state. A bad MRO returns -1; the
 *     caller is expected to surface that as a Python TypeError.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- Forward decls (for dispatch from py_obj.c) ----------------------- */

/* py_obj.c already dispatches on type_tag via a switch; we hook
 * PY_TYPE_CLASS / PY_TYPE_INSTANCE / PY_TYPE_USER+ through the
 * "generic" path. Exposing dedicated deallocators here lets us centralize
 * the cleanup. */

static PyClassObject *object_root(void);

/* ---- Helpers ---------------------------------------------------------- */

/* Safe names[] duplicator. We don't strdup — we keep a shallow copy of
 * the input pointer array. Field names are usually string literals in
 * the emitted module's read-only segment, so we can borrow. */
static const char **copy_name_array(const char **src, int32_t n) {
    if (n <= 0) return NULL;
    const char **out = (const char **)malloc(sizeof(char *) * (size_t)n);
    if (!out) return NULL;
    for (int32_t i = 0; i < n; i++) out[i] = src[i];
    return out;
}

static PyClassObject **copy_class_array(PyClassObject **src, int32_t n) {
    if (n <= 0) return NULL;
    PyClassObject **out = (PyClassObject **)malloc(sizeof(PyClassObject *) * (size_t)n);
    if (!out) return NULL;
    for (int32_t i = 0; i < n; i++) out[i] = src[i];
    return out;
}

/* ---- C3 linearization ------------------------------------------------- */

/* A "candidate head" algorithm implementation:
 *
 *   L[C] = C + merge(L[B1], L[B2], ..., L[Bn], [B1, B2, ..., Bn])
 *
 * where merge() iteratively pulls the head of the first sequence whose
 * head does not appear in the tail of any other sequence, and removes
 * that head from every sequence. Failure is detected when no candidate
 * is eligible.
 *
 * Our inputs are pre-existing MROs (each base's mro[]); we combine them
 * into a single owned MRO array including the "self" placeholder.
 * py_class_new is the sole caller and prepends the new class on top of
 * what c3_linearize returns — so this function only returns the merged
 * tail.
 *
 * Returns 0 on success; writes an owned malloc'd array to *out_mro and
 * the length to *out_n. Returns -1 on merge failure; in that case
 * *out_mro is NULL and *out_n = -1. Caller frees *out_mro. */

/* Internal mutable-sequence state: one per input list. */
typedef struct {
    PyClassObject **items;   /* NOT owned */
    int32_t         head;    /* index of next unconsumed element */
    int32_t         len;
} MergeSeq;

/* Is `cand` present in seq[head+1 .. len-1]? */
static int in_tail(const MergeSeq *seq, PyClassObject *cand) {
    for (int32_t i = seq->head + 1; i < seq->len; i++) {
        if (seq->items[i] == cand) return 1;
    }
    return 0;
}

/* Consume the head of every sequence that starts with `cand`. */
static void consume_head(MergeSeq *seqs, int32_t nseqs, PyClassObject *cand) {
    for (int32_t i = 0; i < nseqs; i++) {
        if (seqs[i].head < seqs[i].len && seqs[i].items[seqs[i].head] == cand) {
            seqs[i].head++;
        }
    }
}

/* Pick the first eligible candidate (a head not found in any tail). */
static PyClassObject *pick_candidate(MergeSeq *seqs, int32_t nseqs) {
    for (int32_t i = 0; i < nseqs; i++) {
        if (seqs[i].head >= seqs[i].len) continue;  /* exhausted */
        PyClassObject *cand = seqs[i].items[seqs[i].head];
        int ok = 1;
        for (int32_t j = 0; j < nseqs; j++) {
            if (i == j) continue;
            if (in_tail(&seqs[j], cand)) { ok = 0; break; }
        }
        if (ok) return cand;
    }
    return NULL;   /* no candidate — MRO inconsistency */
}

int c3_linearize(PyClassObject **bases, int32_t n_bases,
                 PyClassObject ***out_mro, int32_t *out_n) {
    *out_mro = NULL;
    *out_n   = -1;

    /* Each base contributes its own mro[] PLUS the `bases` tail contributes
     * one more sequence (just the list of direct bases in order). So the
     * total sequence count is (n_bases + 1). Special case: zero bases ⇒
     * the resulting tail is empty (py_class_new will add the object
     * root). */
    if (n_bases == 0) {
        *out_mro = NULL;
        *out_n = 0;
        return 0;
    }

    int32_t nseqs = n_bases + 1;
    MergeSeq *seqs = (MergeSeq *)calloc((size_t)nseqs, sizeof(MergeSeq));
    if (!seqs) return -1;

    for (int32_t i = 0; i < n_bases; i++) {
        PyClassObject *b = bases[i];
        seqs[i].items = b->mro;
        seqs[i].head  = 0;
        seqs[i].len   = b->n_mro;
    }
    /* Last sequence: the bases themselves. */
    seqs[n_bases].items = bases;
    seqs[n_bases].head  = 0;
    seqs[n_bases].len   = n_bases;

    /* Upper bound on output length — sum of all input lengths. */
    int32_t cap = 0;
    for (int32_t i = 0; i < nseqs; i++) cap += seqs[i].len;

    PyClassObject **acc = (PyClassObject **)malloc(sizeof(PyClassObject *) * (size_t)cap);
    if (!acc) { free(seqs); return -1; }
    int32_t acc_len = 0;

    for (;;) {
        /* Are all sequences exhausted? */
        int any_remaining = 0;
        for (int32_t i = 0; i < nseqs; i++) {
            if (seqs[i].head < seqs[i].len) { any_remaining = 1; break; }
        }
        if (!any_remaining) break;

        PyClassObject *cand = pick_candidate(seqs, nseqs);
        if (cand == NULL) {
            free(acc);
            free(seqs);
            return -1;          /* inconsistent MRO */
        }
        acc[acc_len++] = cand;
        consume_head(seqs, nseqs, cand);
    }

    free(seqs);
    *out_mro = acc;
    *out_n = acc_len;
    return 0;
}

/* ---- py_class_new ----------------------------------------------------- */

/* Type-tag allocator. Starts at PY_TYPE_USER and monotonically increments.
 * Using a single allocator keeps tags unique across modules. In a future
 * phase this can be per-module. */
static int32_t g_next_user_tag = PY_TYPE_USER;

PyClassObject *py_class_new(const char *name,
                            PyClassObject **bases, int32_t n_bases,
                            const char **field_names, int32_t n_fields) {
    PyClassObject *c = (PyClassObject *)calloc(1, sizeof(PyClassObject));
    if (!c) return NULL;

    c->h.refcount = 1;
    c->h.type_tag = PY_TYPE_CLASS;
    c->h.flags    = 0;
    c->name       = name;
    c->n_bases    = n_bases;
    c->bases      = copy_class_array(bases, n_bases);
    c->n_fields   = n_fields;
    c->field_names = copy_name_array(field_names, n_fields);
    c->n_methods  = 0;
    c->methods    = NULL;
    c->type_tag_alloc = g_next_user_tag++;

    /* Instance size = header + class pointer + n_fields slots.
     *
     * We compute using the concrete struct layout so we don't rely on
     * implementation-defined flexible-array sizeof behavior. */
    size_t inst_size = sizeof(PyInstanceObject) + sizeof(PyObject *) * (size_t)(n_fields > 0 ? n_fields : 0);
    /* Cap at INT32_MAX; nobody should need more, and this keeps the
     * field fit in int32_t. */
    if (inst_size > (size_t)INT32_MAX) inst_size = INT32_MAX;
    c->instance_size = (int32_t)inst_size;

    /* MRO: self + c3(linearize(bases)). Fallback when there are no bases
     * and this class is not the root itself: tail is [object_root]. */
    PyClassObject **tail = NULL;
    int32_t tail_len = 0;
    if (c3_linearize(bases, n_bases, &tail, &tail_len) != 0) {
        /* Inconsistent MRO. Clean up and return NULL. */
        free(c->bases);
        free((void *)c->field_names);
        free(c);
        return NULL;
    }

    /* Decide whether to append a root "object" class. Root is appended
     * only when the class has zero bases AND is not itself the root. */
    PyClassObject *root = object_root();
    int append_root = (n_bases == 0 && c != root);

    int32_t mro_len = 1 + tail_len + (append_root ? 1 : 0);
    c->mro = (PyClassObject **)malloc(sizeof(PyClassObject *) * (size_t)mro_len);
    if (!c->mro) {
        free(tail);
        free(c->bases);
        free((void *)c->field_names);
        free(c);
        return NULL;
    }
    c->mro[0] = c;
    for (int32_t i = 0; i < tail_len; i++) c->mro[1 + i] = tail[i];
    if (append_root) c->mro[mro_len - 1] = root;
    c->n_mro = mro_len;

    free(tail);
    return c;
}

/* Lazily constructed root "object" class. It has no bases, no fields, no
 * methods. We only use it to anchor isinstance(x, object) and to appear
 * at the end of every class's MRO. Refcount is immortal-ish. */
static PyClassObject *object_root(void) {
    static PyClassObject *root = NULL;
    if (root != NULL) return root;
    PyClassObject *r = (PyClassObject *)calloc(1, sizeof(PyClassObject));
    if (!r) return NULL;
    r->h.refcount = 1;
    r->h.type_tag = PY_TYPE_CLASS;
    r->h.flags    = PY_FLAG_IMMORTAL;
    r->name       = "object";
    r->n_bases    = 0;
    r->bases      = NULL;
    r->n_mro      = 1;
    r->mro        = (PyClassObject **)malloc(sizeof(PyClassObject *));
    if (!r->mro) { free(r); return NULL; }
    r->mro[0]     = r;
    r->n_fields   = 0;
    r->field_names = NULL;
    r->n_methods  = 0;
    r->methods    = NULL;
    r->instance_size = sizeof(PyInstanceObject);
    r->type_tag_alloc = PY_TYPE_INSTANCE;
    root = r;
    return root;
}

/* ---- Method table ----------------------------------------------------- */

void py_class_add_method(PyClassObject *cls, const char *name, PyObject *func) {
    if (!cls || !name) return;
    int32_t new_n = cls->n_methods + 1;
    PyClassMethod *newarr = (PyClassMethod *)realloc(
        cls->methods, sizeof(PyClassMethod) * (size_t)new_n);
    if (!newarr) return;   /* best-effort: drop on OOM */
    newarr[cls->n_methods].name = name;
    newarr[cls->n_methods].func = func;
    cls->methods = newarr;
    cls->n_methods = new_n;
}

/* Walk MRO and return the first method with the matching name. */
PyObject *py_class_lookup(PyClassObject *cls, const char *name) {
    if (!cls || !name) return NULL;
    if (strcmp(name, "__name__") == 0) {
        const char *cls_name = cls->name ? cls->name : "";
        return py_str_new(cls_name, (int64_t)strlen(cls_name));
    }
    if (strcmp(name, "__mro__") == 0) {
        PyObject *t = py_tuple_new(cls->n_mro);
        if (!t) return NULL;
        for (int32_t i = 0; i < cls->n_mro; i++) {
            py_tuple_set_item(t, i, (PyObject *)cls->mro[i]);
        }
        return t;
    }
    for (int32_t i = 0; i < cls->n_mro; i++) {
        PyClassObject *m = cls->mro[i];
        if (!m) continue;
        for (int32_t j = 0; j < m->n_methods; j++) {
            if (m->methods[j].name && strcmp(m->methods[j].name, name) == 0) {
                return m->methods[j].func;
            }
        }
    }
    return NULL;
}

/* ---- Instance allocation --------------------------------------------- */

PyObject *py_instance_new(PyClassObject *cls) {
    if (!cls) return NULL;
    /* Allocate header + cls pointer + n_fields owned slots. We pay for
     * exactly n_fields slots even if fields are inherited via MRO — the
     * codegen allocates slots on the MOST DERIVED class for every field
     * it can statically see. */
    size_t n_slots = (size_t)(cls->n_fields > 0 ? cls->n_fields : 0);
    size_t size = sizeof(PyInstanceObject) + sizeof(PyObject *) * n_slots;
    PyInstanceObject *inst = (PyInstanceObject *)calloc(1, size);
    if (!inst) return NULL;
    inst->h.refcount = 1;
    /* Prefer the per-class unique tag so dispatch stays branchless. We
     * keep PY_TYPE_INSTANCE as a generic fallback for classes that opted
     * out. */
    inst->h.type_tag = cls->type_tag_alloc;
    inst->h.flags    = 0;
    inst->cls        = cls;
    /* field slots are zero-initialized by calloc — which is the all-NULL
     * sentinel we need. */
    return (PyObject *)inst;
}

PyObject *py_instance_get_field(PyInstanceObject *inst, int32_t idx) {
    if (!inst || idx < 0) return NULL;
    if (idx >= inst->cls->n_fields) return NULL;
    PyObject *v = inst->fields[idx];
    if (v) py_incref(v);
    return v;
}

void py_instance_set_field(PyInstanceObject *inst, int32_t idx, PyObject *value) {
    if (!inst || idx < 0) return;
    if (idx >= inst->cls->n_fields) return;
    PyObject *old = inst->fields[idx];
    if (value) py_incref(value);
    inst->fields[idx] = value;
    if (old) py_decref(old);
}

/* Look up a field index by name on the instance's class (including
 * inherited fields — we walk MRO field_names to support simple
 * single-inheritance). Returns -1 if not found. */
static int32_t lookup_field_index(PyClassObject *cls, const char *name) {
    if (!cls || !name) return -1;
    /* Field slot indices are per-class (the codegen allocates slots on
     * the most-derived class). In Phase 3 we only consult the leaf
     * class's field_names; inherited fields with identical names can
     * still live in the leaf's own slot table because the codegen
     * merges the declaration sets. */
    for (int32_t i = 0; i < cls->n_fields; i++) {
        if (cls->field_names[i] && strcmp(cls->field_names[i], name) == 0) {
            return i;
        }
    }
    return -1;
}

PyObject *py_instance_getattr(PyInstanceObject *inst, const char *name) {
    if (!inst || !name) return NULL;
    if (strcmp(name, "__class__") == 0) {
        PyObject *cls = (PyObject *)inst->cls;
        py_incref(cls);
        return cls;
    }
    int32_t idx = lookup_field_index(inst->cls, name);
    if (idx >= 0) {
        /* Return a new reference so callers can uniformly py_decref. */
        PyObject *v = inst->fields[idx];
        if (v) py_incref(v);
        return v;
    }
    /* Method fallthrough — returns a borrowed ref. The codegen is aware
     * that class-lookup results don't need refcounting. */
    return py_class_lookup(inst->cls, name);
}

int64_t py_instance_setattr(PyInstanceObject *inst, const char *name, PyObject *value) {
    if (!inst || !name) return -1;
    int32_t idx = lookup_field_index(inst->cls, name);
    if (idx < 0) return -1;
    py_instance_set_field(inst, idx, value);
    return 0;
}

static PyInstanceObject *dataclass_copy_instance(PyObject *obj,
                                                 PyClassObject **cls_out) {
    if (!obj || PY_IS_TAGGED_INT(obj)) return NULL;
    int32_t tag = py_header(obj)->type_tag;
    if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) return NULL;

    PyInstanceObject *src = (PyInstanceObject *)obj;
    PyClassObject *cls = src->cls;
    if (!cls) return NULL;
    if (cls_out) *cls_out = cls;

    PyInstanceObject *dst = (PyInstanceObject *)py_instance_new(cls);
    if (!dst) return NULL;

    for (int32_t i = 0; i < cls->n_fields; i++) {
        PyObject *v = src->fields[i];
        if (!v) continue;
        py_incref(v);
        dst->fields[i] = v;
    }
    return dst;
}

PyObject *py_dataclass_replace(PyObject *obj, int64_t n_overrides,
                               const char **names, PyObject **values) {
    PyClassObject *cls = NULL;
    PyInstanceObject *dst = dataclass_copy_instance(obj, &cls);
    if (!dst || !cls) return NULL;

    for (int64_t i = 0; i < n_overrides; i++) {
        const char *name = names ? names[i] : NULL;
        PyObject *value = values ? values[i] : NULL;
        int32_t idx = lookup_field_index(cls, name);
        if (idx < 0) {
            py_decref((PyObject *)dst);
            return NULL;
        }
        py_instance_set_field(dst, idx, value);
    }

    return (PyObject *)dst;
}

PyObject *py_dataclass_replace_from_dict(PyObject *obj, PyObject *overrides) {
    PyClassObject *cls = NULL;
    PyInstanceObject *dst = dataclass_copy_instance(obj, &cls);
    if (!dst || !cls) return NULL;

    if (!overrides || PY_IS_TAGGED_INT(overrides) ||
        py_header(overrides)->type_tag != PY_TYPE_DICT) {
        py_decref((PyObject *)dst);
        return NULL;
    }

    PyDictObject *d = (PyDictObject *)overrides;
    for (int64_t i = 0; i < d->entries_used; i++) {
        DictEntry *e = &d->entries[i];
        if (!e->key) continue;
        const char *name = py_str_utf8(e->key);
        int32_t idx = lookup_field_index(cls, name);
        if (idx < 0) {
            py_decref((PyObject *)dst);
            return NULL;
        }
        py_instance_set_field(dst, idx, e->value);
    }

    return (PyObject *)dst;
}

/* ---- isinstance ------------------------------------------------------- */

int64_t py_isinstance(PyObject *obj, PyClassObject *cls) {
    if (!obj || !cls) return 0;
    if (PY_IS_TAGGED_INT(obj)) return 0;
    int32_t tag = py_header(obj)->type_tag;
    if (tag != PY_TYPE_INSTANCE && tag < PY_TYPE_USER) return 0;
    PyInstanceObject *inst = (PyInstanceObject *)obj;
    PyClassObject *c = inst->cls;
    if (!c) return 0;
    for (int32_t i = 0; i < c->n_mro; i++) {
        if (c->mro[i] == cls) return 1;
    }
    return 0;
}

/* ---- super() lookup --------------------------------------------------- */

/* Find `from_cls` in `start_cls->mro`, then look for `name` in every
 * method table AFTER that entry. This is the standard Python super()
 * behavior per PEP 3135. */
PyObject *py_super_lookup(PyClassObject *start_cls,
                          PyClassObject *from_cls,
                          const char *name) {
    if (!start_cls || !from_cls || !name) return NULL;

    int32_t start = -1;
    for (int32_t i = 0; i < start_cls->n_mro; i++) {
        if (start_cls->mro[i] == from_cls) { start = i; break; }
    }
    if (start < 0) {
        /* from_cls not in start_cls's MRO — degenerate super() call.
         * Fall back to searching the whole MRO to match CPython's
         * "best effort" semantics. */
        start = -1;
    }

    for (int32_t i = start + 1; i < start_cls->n_mro; i++) {
        PyClassObject *m = start_cls->mro[i];
        if (!m) continue;
        for (int32_t j = 0; j < m->n_methods; j++) {
            if (m->methods[j].name && strcmp(m->methods[j].name, name) == 0) {
                return m->methods[j].func;
            }
        }
    }
    return NULL;
}

/* ---- Deallocators ---------------------------------------------------- */

void py_class_dealloc(PyObject *o) {
    if (!o) return;
    PyClassObject *c = (PyClassObject *)o;
    free(c->bases);
    free(c->mro);
    free(c->methods);
    free((void *)c->field_names);
    free(c);
}

void py_instance_dealloc(PyObject *o) {
    if (!o) return;
    PyInstanceObject *inst = (PyInstanceObject *)o;
    if (inst->cls) {
        for (int32_t i = 0; i < inst->cls->n_fields; i++) {
            PyObject *v = inst->fields[i];
            if (v) py_decref(v);
        }
    }
    free(inst);
}
