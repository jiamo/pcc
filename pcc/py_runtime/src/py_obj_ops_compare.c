/* pcc/py_runtime/src/py_obj_ops_compare.c
 *
 * Equality, hashing, three-way compare, sorted, contains. Kept C-side
 * because the FNV-1a + bignum compare + recursive container compare
 * are subtly tricky to port to pcc-Python signed-i64 arithmetic.
 *
 * Split out of py_obj_ops.c so the dispatch half (truthy / len /
 * getitem / setitem / etc.) can be replaced by py_obj_ops_dispatch.py.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static int is_bool(PyObject *o) {
    return o == py_True || o == py_False;
}

static int is_int_like(int32_t tag) {
    return tag == PY_TYPE_INT || tag == PY_TYPE_BOOL;
}

static int64_t bool_as_i64(PyObject *o) {
    return (o == py_True) ? 1 : 0;
}

static int64_t int_or_bool_as_i64(PyObject *o) {
    if (is_bool(o)) return bool_as_i64(o);
    return py_int_value_i64(o);
}

static int64_t fnv1a(const unsigned char *p, size_t n) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; i++) {
        h ^= (uint64_t)p[i];
        h *= 0x100000001b3ull;
    }
    int64_t out = (int64_t)h;
    if (out == -1) out = -2;
    return out;
}

static int is_bytes_like(int32_t tag) {
    return tag == PY_TYPE_BYTES
        || tag == PY_TYPE_BYTEARRAY
        || tag == PY_TYPE_MEMORYVIEW;
}

static const char *bytes_like_data(PyObject *o, int64_t *n) {
    if (o == NULL) {
        *n = 0;
        return NULL;
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BYTES) {
        PyBytesObject *b = (PyBytesObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_BYTEARRAY) {
        PyByteArrayObject *b = (PyByteArrayObject *)o;
        *n = b->byte_len;
        return b->data;
    }
    if (tag == PY_TYPE_MEMORYVIEW) {
        PyMemoryViewObject *m = (PyMemoryViewObject *)o;
        return bytes_like_data(pcc_gc_load_ptr(o, &m->base), n);
    }
    *n = 0;
    return NULL;
}

static PyObject *cmp_tuple_item(PyObject *owner, PyTupleObject *t, int64_t i) {
    return pcc_gc_load_ptr(owner, &t->items[i]);
}

static PyObject *cmp_list_item(PyObject *owner, PyListObject *l, int64_t i) {
    return pcc_gc_load_ptr(owner, &l->items[i]);
}

static PyObject *cmp_dict_key(PyDictObject *d, DictEntry *e) {
    if (e->key == NULL) return NULL;
    return pcc_gc_load_ptr((PyObject *)d, &e->key);
}

static PyObject *cmp_dict_value(PyDictObject *d, DictEntry *e) {
    if (e->value == NULL) return NULL;
    return pcc_gc_load_ptr((PyObject *)d, &e->value);
}

static PyObject *cmp_set_key(PySetObject *s, SetEntry *e) {
    PyObject *raw = e->key;
    if (raw == NULL || raw == py_set_dummy) return raw;
    return pcc_gc_load_ptr((PyObject *)s, &e->key);
}

int py_obj_cmp_threeway(PyObject *a, PyObject *b) {
    if (a == b) return 0;
    if (a == NULL) return (b == NULL) ? 0 : -1;
    if (b == NULL) return 1;

    int32_t ta = py_type_of(a);
    int32_t tb = py_type_of(b);
    int a_is_int = is_int_like(ta);
    int b_is_int = is_int_like(tb);

    if (a_is_int && b_is_int) {
        if (ta == PY_TYPE_INT && tb == PY_TYPE_INT) {
            return py_int_cmp(a, b);
        }
        int64_t av = int_or_bool_as_i64(a);
        int64_t bv = int_or_bool_as_i64(b);
        if (av < bv) return -1;
        if (av > bv) return 1;
        return 0;
    }

    if ((ta == PY_TYPE_FLOAT || a_is_int) && (tb == PY_TYPE_FLOAT || b_is_int)) {
        double av = (ta == PY_TYPE_FLOAT)
            ? ((PyFloatObject *)a)->value
            : (double)int_or_bool_as_i64(a);
        double bv = (tb == PY_TYPE_FLOAT)
            ? ((PyFloatObject *)b)->value
            : (double)int_or_bool_as_i64(b);
        if (av < bv) return -1;
        if (av > bv) return 1;
        return 0;
    }

    if (ta == PY_TYPE_STR && tb == PY_TYPE_STR) {
        PyStrObject *sa = (PyStrObject *)a;
        PyStrObject *sb = (PyStrObject *)b;
        int64_t n = sa->byte_len < sb->byte_len ? sa->byte_len : sb->byte_len;
        int r = memcmp(sa->data, sb->data, (size_t)n);
        if (r != 0) return r < 0 ? -1 : 1;
        if (sa->byte_len < sb->byte_len) return -1;
        if (sa->byte_len > sb->byte_len) return 1;
        return 0;
    }

    if (is_bytes_like(ta) && is_bytes_like(tb)) {
        int64_t na = 0;
        int64_t nb = 0;
        const char *da = bytes_like_data(a, &na);
        const char *db = bytes_like_data(b, &nb);
        int64_t n = na < nb ? na : nb;
        int r = 0;
        if (n > 0) r = memcmp(da, db, (size_t)n);
        if (r != 0) return r < 0 ? -1 : 1;
        if (na < nb) return -1;
        if (na > nb) return 1;
        return 0;
    }

    if (ta == PY_TYPE_TUPLE && tb == PY_TYPE_TUPLE) {
        PyTupleObject *ta_o = (PyTupleObject *)a;
        PyTupleObject *tb_o = (PyTupleObject *)b;
        int64_t n = ta_o->len < tb_o->len ? ta_o->len : tb_o->len;
        for (int64_t i = 0; i < n; i++) {
            int r = py_obj_cmp_threeway(
                cmp_tuple_item(a, ta_o, i),
                cmp_tuple_item(b, tb_o, i)
            );
            if (r != 0) return r;
        }
        if (ta_o->len < tb_o->len) return -1;
        if (ta_o->len > tb_o->len) return 1;
        return 0;
    }

    if (ta == PY_TYPE_LIST && tb == PY_TYPE_LIST) {
        PyListObject *la = (PyListObject *)a;
        PyListObject *lb = (PyListObject *)b;
        int64_t n = la->length < lb->length ? la->length : lb->length;
        for (int64_t i = 0; i < n; i++) {
            int r = py_obj_cmp_threeway(
                cmp_list_item(a, la, i),
                cmp_list_item(b, lb, i)
            );
            if (r != 0) return r;
        }
        if (la->length < lb->length) return -1;
        if (la->length > lb->length) return 1;
        return 0;
    }

    if (ta == PY_TYPE_NONE && tb == PY_TYPE_NONE) return 0;

    if ((uintptr_t)a < (uintptr_t)b) return -1;
    if ((uintptr_t)a > (uintptr_t)b) return 1;
    return 0;
}

static int64_t valuebox_cstr_eq(const char *a, const char *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;
    return strcmp(a, b) == 0;
}

static int64_t valuebox_classes_eq(PyClassObject *ca, PyClassObject *cb) {
    if (ca == cb) return ca != NULL;
    if (ca == NULL || cb == NULL) return 0;
    if (!valuebox_cstr_eq(ca->name, cb->name)) return 0;
    if (ca->n_fields != cb->n_fields) return 0;
    if (ca->n_fields < 0) return 0;
    for (int32_t i = 0; i < ca->n_fields; i++) {
        const char *fa = ca->field_names != NULL ? ca->field_names[i] : NULL;
        const char *fb = cb->field_names != NULL ? cb->field_names[i] : NULL;
        if (!valuebox_cstr_eq(fa, fb)) return 0;
    }
    return 1;
}

int64_t py_obj_eq(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;

    int32_t ta = py_type_of(a);
    int32_t tb = py_type_of(b);

    if (ta == PY_TYPE_BOOL && tb == PY_TYPE_BOOL) return 0;

    if (is_int_like(ta) && is_int_like(tb)) {
        if (ta == PY_TYPE_INT && tb == PY_TYPE_INT) {
            return py_int_cmp(a, b) == 0;
        }
        return int_or_bool_as_i64(a) == int_or_bool_as_i64(b);
    }

    if (ta == PY_TYPE_FLOAT && tb == PY_TYPE_FLOAT) {
        return ((PyFloatObject *)a)->value == ((PyFloatObject *)b)->value;
    }
    if (ta == PY_TYPE_FLOAT && is_int_like(tb)) {
        return ((PyFloatObject *)a)->value == (double)int_or_bool_as_i64(b);
    }
    if (tb == PY_TYPE_FLOAT && is_int_like(ta)) {
        return ((PyFloatObject *)b)->value == (double)int_or_bool_as_i64(a);
    }

    if (ta == PY_TYPE_STR && tb == PY_TYPE_STR) {
        return py_str_eq(a, b);
    }

    if (is_bytes_like(ta) && is_bytes_like(tb)) {
        int64_t na = 0;
        int64_t nb = 0;
        const char *da = bytes_like_data(a, &na);
        const char *db = bytes_like_data(b, &nb);
        if (na != nb) return 0;
        if (na == 0) return 1;
        return memcmp(da, db, (size_t)na) == 0;
    }

    if (ta == PY_TYPE_TUPLE && tb == PY_TYPE_TUPLE) {
        PyTupleObject *ta_o = (PyTupleObject *)a;
        PyTupleObject *tb_o = (PyTupleObject *)b;
        if (ta_o->len != tb_o->len) return 0;
        for (int64_t i = 0; i < ta_o->len; i++) {
            if (!py_obj_eq(
                    cmp_tuple_item(a, ta_o, i),
                    cmp_tuple_item(b, tb_o, i)
                )) return 0;
        }
        return 1;
    }

    if (ta == PY_TYPE_LIST && tb == PY_TYPE_LIST) {
        PyListObject *la = (PyListObject *)a;
        PyListObject *lb = (PyListObject *)b;
        if (la->length != lb->length) return 0;
        for (int64_t i = 0; i < la->length; i++) {
            if (!py_obj_eq(
                    cmp_list_item(a, la, i),
                    cmp_list_item(b, lb, i)
                )) return 0;
        }
        return 1;
    }

    if (ta == PY_TYPE_DICT && tb == PY_TYPE_DICT) {
        PyDictObject *da = (PyDictObject *)a;
        PyDictObject *db = (PyDictObject *)b;
        if (da->size != db->size) return 0;
        for (int64_t i = 0; i < da->entries_used; i++) {
            DictEntry *e = &da->entries[i];
            PyObject *key = cmp_dict_key(da, e);
            if (key == NULL) continue;
            PyObject *other = py_dict_get(b, key);
            if (other == NULL) return 0;
            int64_t eq = py_obj_eq(cmp_dict_value(da, e), other);
            py_decref(other);
            if (!eq) return 0;
        }
        return 1;
    }

    if (ta == PY_TYPE_SET && tb == PY_TYPE_SET) {
        PySetObject *sa = (PySetObject *)a;
        PySetObject *sb = (PySetObject *)b;
        if (sa->size != sb->size) return 0;
        for (int64_t i = 0; i < sa->capacity; i++) {
            PyObject *key = cmp_set_key(sa, &sa->entries[i]);
            if (key == NULL || key == py_set_dummy) continue;
            if (!py_set_contains(b, key)) return 0;
        }
        return 1;
    }

    if (ta == PY_TYPE_VALUEBOX && tb == PY_TYPE_VALUEBOX) {
        PyValueBoxObject *ba = (PyValueBoxObject *)a;
        PyValueBoxObject *bb = (PyValueBoxObject *)b;
        PyClassObject *ca = (PyClassObject *)pcc_gc_load_ptr(
            a,
            (PyObject **)&ba->cls
        );
        PyClassObject *cb = (PyClassObject *)pcc_gc_load_ptr(
            b,
            (PyObject **)&bb->cls
        );
        if (!valuebox_classes_eq(ca, cb)) return 0;
        int32_t n_fields = ca->n_fields;
        if (n_fields < 0) return 0;
        for (int32_t i = 0; i < n_fields; i++) {
            PyObject *va = pcc_gc_load_ptr(a, &ba->fields[i]);
            PyObject *vb = pcc_gc_load_ptr(b, &bb->fields[i]);
            if (va == vb) continue;
            if (va == NULL || vb == NULL) return 0;
            if (!py_obj_eq(va, vb)) return 0;
        }
        return 1;
    }

    if (ta == PY_TYPE_NONE || tb == PY_TYPE_NONE) return 0;

    return 0;
}

static int64_t py_valuebox_hash(PyValueBoxObject *box) {
    if (box == NULL) return 0;
    PyClassObject *cls = (PyClassObject *)pcc_gc_load_ptr(
        (PyObject *)box,
        (PyObject **)&box->cls
    );
    if (cls == NULL) return 0;
    int32_t n_fields = cls->n_fields;
    if (n_fields < 0) return 0;
    int64_t h = n_fields;
    for (int32_t i = 0; i < n_fields; i++) {
        PyObject *v = pcc_gc_load_ptr((PyObject *)box, &box->fields[i]);
        int64_t field_hash = (v == NULL) ? 0 : py_obj_hash(v);
        h = (h * 31 + (field_hash % 1000003)) % 1000000007;
    }
    return (h == -1) ? -2 : h;
}

int64_t py_obj_hash(PyObject *o) {
    if (o == NULL) return 0;
    if (PY_IS_TAGGED_INT(o)) {
        int64_t v = py_untag_int(o);
        return (v == -1) ? -2 : v;
    }
    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_NONE:
            return 0;
        case PY_TYPE_BOOL:
            return (o == py_True) ? 1 : 0;
        case PY_TYPE_INT: {
            int64_t v = py_int_value_i64(o);
            return (v == -1) ? -2 : v;
        }
        case PY_TYPE_FLOAT: {
            double d = ((PyFloatObject *)o)->value;
            int64_t as_i = (int64_t)d;
            if ((double)as_i == d) {
                return (as_i == -1) ? -2 : as_i;
            }
            uint64_t bits;
            memcpy(&bits, &d, sizeof bits);
            int64_t out = (int64_t)bits;
            return (out == -1) ? -2 : out;
        }
        case PY_TYPE_STR: {
            PyStrObject *s = (PyStrObject *)o;
            if (s->hash != -1) return s->hash;
            int64_t h = fnv1a((const unsigned char *)s->data, (size_t)s->byte_len);
            s->hash = h;
            return h;
        }
        case PY_TYPE_VALUEBOX:
            return py_valuebox_hash((PyValueBoxObject *)o);
        case PY_TYPE_BYTES: {
            PyBytesObject *b = (PyBytesObject *)o;
            return fnv1a((const unsigned char *)b->data, (size_t)b->byte_len);
        }
        case PY_TYPE_TUPLE: {
            PyTupleObject *t = (PyTupleObject *)o;
            int64_t h = 0;
            for (int64_t i = 0; i < t->len; i++) {
                h ^= py_obj_hash(cmp_tuple_item(o, t, i));
            }
            return (h == -1) ? -2 : h;
        }
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t user_hash = py_user_hash_dispatch(o, &handled);
                if (handled) {
                    return user_hash;
                }
            }
            return 0;
    }
}

int64_t py_obj_lt(PyObject *a, PyObject *b) { return py_obj_cmp_threeway(a, b) < 0; }
int64_t py_obj_le(PyObject *a, PyObject *b) { return py_obj_cmp_threeway(a, b) <= 0; }
int64_t py_obj_gt(PyObject *a, PyObject *b) { return py_obj_cmp_threeway(a, b) > 0; }
int64_t py_obj_ge(PyObject *a, PyObject *b) { return py_obj_cmp_threeway(a, b) >= 0; }

PyObject *py_obj_sorted(PyObject *x) {
    if (x == NULL) return NULL;
    int64_t n = py_obj_len(x);
    /* py_obj_len is only a sizing hint here. A custom iterator (user class
     * with __iter__/__next__ but no __len__) raises from py_obj_len; left
     * pending, that aborts the iterator loop below and yields []. Clear it —
     * the iterator-protocol branch handles length-less sources. */
    if (py_err_occurred()) {
        py_clear_exception();
        n = 0;
    }
    PyObject *out = py_list_new(n);
    if (out == NULL) return NULL;
    if (py_type_of(x) == PY_TYPE_SET) {
        PySetObject *s = (PySetObject *)x;
        for (int64_t i = 0; i < s->capacity; i++) {
            PyObject *key = cmp_set_key(s, &s->entries[i]);
            if (key == NULL || key == py_set_dummy) continue;
            py_list_append(out, key);
        }
    } else if (py_type_of(x) == PY_TYPE_LIST || py_type_of(x) == PY_TYPE_TUPLE) {
        for (int64_t i = 0; i < n; i++) {
            PyObject *idx_box = py_int_from_i64(i);
            PyObject *el = py_obj_getitem(x, idx_box);
            py_list_append(out, el);
            py_decref(idx_box);
        }
    } else {
        /* General iterable that is not integer-indexable: dict (-> keys),
         * generator, range, etc. Use the iterator protocol. (Previously this
         * branch used py_obj_getitem(x, i), which returns NULL for a dict
         * since 0/1/... are not keys -> sorted(dict) gave [<null>, ...].) */
        PyObject *it = py_obj_iter(x);
        if (it != NULL) {
            for (;;) {
                PyObject *el = py_obj_next(it);
                if (el == NULL) {
                    if (py_err_occurred()) {
                        PyObject *cur = py_current_exception();
                        PyObject *stop =
                            py_exc_builtin_class(PY_EXC_STOPITERATION);
                        if (py_exc_matches(cur, stop)) py_clear_exception();
                    }
                    break;
                }
                py_list_append(out, el);
                py_decref(el);
            }
            py_decref(it);
        }
    }
    int64_t m = py_list_len(out);
    for (int64_t i = 1; i < m; i++) {
        PyObject *cur = py_list_get(out, i);
        int64_t j = i;
        while (j > 0) {
            PyObject *prev = py_list_get(out, j - 1);
            if (py_obj_cmp_threeway(prev, cur) <= 0) break;
            py_list_set(out, j, prev);
            j--;
        }
        py_list_set(out, j, cur);
    }
    return out;
}

int64_t py_obj_contains(PyObject *container, PyObject *item) {
    if (container == NULL) return 0;
    int32_t tag = py_type_of(container);
    switch (tag) {
        case PY_TYPE_LIST:  return py_list_contains(container, item);
        case PY_TYPE_TUPLE: {
            int64_t n = py_tuple_len(container);
            for (int64_t i = 0; i < n; i++) {
                PyObject *el = py_tuple_get(container, i);
                if (py_obj_eq(el, item)) return 1;
            }
            return 0;
        }
        case PY_TYPE_DICT:  return py_dict_contains(container, item);
        case PY_TYPE_SET: {
            int c = py_set_contains(container, item);
            return c ? 1 : 0;
        }
        case PY_TYPE_STR:   return py_str_contains(container, item);
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER) {
                int64_t handled = 0;
                int64_t result = py_user_contains_dispatch(container, item, &handled);
                if (handled) return result;
            }
            return 0;
    }
}
