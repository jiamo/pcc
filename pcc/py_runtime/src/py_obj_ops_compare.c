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
#include <math.h>
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
    if (__atomic_load_n(&pcc_gc_read_barrier_enabled, __ATOMIC_ACQUIRE) == 0) {
        return t->items[i];
    }
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

PyObject *py_obj_abs(PyObject *o) {
    if (o == NULL) {
        py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "bad operand type for abs()"));
        return NULL;
    }
    if (PY_IS_TAGGED_INT(o)) {
        int64_t value = py_untag_int(o);
        if (value < 0) return py_int_neg(o);
        return py_int_from_i64(value);
    }
    int32_t tag = py_type_of(o);
    if (tag == PY_TYPE_BOOL) {
        return py_int_from_i64(o == py_True ? 1 : 0);
    }
    if (tag == PY_TYPE_INT) {
        PyIntObject *big = (PyIntObject *)o;
        if (big->sign < 0) return py_int_neg(o);
        py_incref(o);
        return o;
    }
    if (tag == PY_TYPE_FLOAT) {
        return py_float_from_f64(fabs(py_float_to_f64(o)));
    }
    if (pcc_capi_is_cext_type_tag(tag)) {
        return pcc_capi_cext_absolute(o);
    }
    if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
        /* abs(obj) on a user instance dispatches __abs__. NULL+no-exception
         * means no __abs__ -> fall through to the TypeError below. */
        PyObject *r = py_user_abs_dispatch(o);
        if (r != NULL || py_err_occurred()) return r;
    }
    py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "bad operand type for abs()"));
    return NULL;
}

int64_t py_obj_eq(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;

    int32_t ta = py_type_of(a);
    int32_t tb = py_type_of(b);

    /* numpy / C-extension scalar ==: drive its tp_richcompare (Py_EQ=2), same
     * as py_obj_lt/le/gt/ge. Without this a[i] == 3 fell through to the default
     * not-equal and returned False even when the values matched. */
    if (pcc_capi_is_cext_type_tag(ta) || pcc_capi_is_cext_type_tag(tb)) {
        return pcc_capi_cext_richcompare_bool(a, b, 2) > 0 ? 1 : 0;
    }

    if (ta == PY_TYPE_BOOL && tb == PY_TYPE_BOOL) return 0;

    if (ta == PY_TYPE_STR && tb == PY_TYPE_STR) {
        return py_str_eq(a, b);
    }

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
            PyObject *ea = cmp_tuple_item(a, ta_o, i);
            PyObject *eb = cmp_tuple_item(b, tb_o, i);
            if (ea == eb) continue;
            if (PY_IS_TAGGED_INT(ea) && PY_IS_TAGGED_INT(eb)) return 0;
            if (!PY_IS_TAGGED_INT(ea)
                && !PY_IS_TAGGED_INT(eb)
                && ea != NULL
                && eb != NULL
                && py_header(ea)->type_tag == PY_TYPE_STR
                && py_header(eb)->type_tag == PY_TYPE_STR) {
                if (!py_str_eq(ea, eb)) return 0;
                continue;
            }
            if (!py_obj_eq(ea, eb)) return 0;
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

    /* User instances: honor __eq__ for container key lookup and ``==``.
     * Tri-state: -1 = no user __eq__ (or NotImplemented) -> keep the
     * identity fallback this fallthrough used to end with. */
    if (ta == PY_TYPE_INSTANCE || ta >= PY_TYPE_USER_CLASS_START
        || tb == PY_TYPE_INSTANCE || tb >= PY_TYPE_USER_CLASS_START) {
        int64_t dispatched = py_user_eq_dispatch(a, b);
        if (dispatched >= 0) return dispatched;
    }

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
        if (py_err_occurred()) return -1;
        h = (h * 31 + (field_hash % 1000003)) % 1000000007;
    }
    return (h == -1) ? -2 : h;
}

/* CPython's numeric hash (long_hash / _Py_HashDouble): reduce modulo the
 * Mersenne prime P = 2**61 - 1 so x == y implies hash(x) == hash(y) across
 * int, bool and float.  Mirrors _hash_i64 / _hash_f64_bits in the
 * py_obj_ops_compare.py port. */
#define PCC_HASH_MODULUS ((int64_t)2305843009213693951LL)

static int64_t pcc_hash_i64(int64_t v) {
    if (v == INT64_MIN) return -4;  /* 2**63 mod P == 4 */
    int64_t mag = v < 0 ? -v : v;
    int64_t x = mag % PCC_HASH_MODULUS;
    if (v < 0) x = -x;
    return (x == -1) ? -2 : x;
}

static int64_t pcc_hash_double_bits(uint64_t bits, const void *o) {
    int64_t sign = (bits >> 63) ? -1 : 1;
    int64_t exp = (int64_t)((bits >> 52) & 2047u);
    uint64_t frac = bits & 0xFFFFFFFFFFFFFull;
    if (exp == 2047) {
        if (frac == 0) return 314159 * sign;
        uint64_t p = (uint64_t)(uintptr_t)o;
        int64_t h = (int64_t)((p >> 4) | (p << 60));
        return (h == -1) ? -2 : h;
    }
    uint64_t mant = frac;
    int64_t e;
    if (exp == 0) {
        if (frac == 0) return 0;
        e = -1074;
    } else {
        mant = frac | (1ull << 52);
        e = exp - 1075;
    }
    int64_t k = ((e % 61) + 61) % 61;
    uint64_t x = mant;
    if (k != 0) {
        x = ((x << k) & (uint64_t)PCC_HASH_MODULUS) | (x >> (61 - k));
    }
    int64_t out = (int64_t)x * sign;
    return (out == -1) ? -2 : out;
}

static int py_obj_hash_leaf_fast(PyObject *o, int64_t *out) {
    if (o == NULL) {
        *out = 0;
        return 1;
    }
    if (PY_IS_TAGGED_INT(o)) {
        *out = pcc_hash_i64(py_untag_int(o));
        return 1;
    }
    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_NONE:
            *out = 0;
            return 1;
        case PY_TYPE_BOOL:
            *out = (o == py_True) ? 1 : 0;
            return 1;
        case PY_TYPE_INT: {
            *out = pcc_hash_i64(py_int_value_i64(o));
            return 1;
        }
        case PY_TYPE_STR: {
            PyStrObject *s = (PyStrObject *)o;
            if (s->hash != -1) {
                *out = s->hash;
                return 1;
            }
            int64_t h = fnv1a((const unsigned char *)s->data, (size_t)s->byte_len);
            s->hash = h;
            *out = h;
            return 1;
        }
        default:
            return 0;
    }
}

int64_t py_obj_hash(PyObject *o) {
    if (o == NULL) return 0;
    if (PY_IS_TAGGED_INT(o)) {
        return pcc_hash_i64(py_untag_int(o));
    }
    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_NONE:
            return 0;
        case PY_TYPE_BOOL:
            return (o == py_True) ? 1 : 0;
        case PY_TYPE_INT:
            return pcc_hash_i64(py_int_value_i64(o));
        case PY_TYPE_FLOAT: {
            double d = ((PyFloatObject *)o)->value;
            uint64_t bits;
            memcpy(&bits, &d, sizeof bits);
            return pcc_hash_double_bits(bits, o);
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
        case PY_TYPE_LIST:
            py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "unhashable type: 'list'"));
            return -1;
        case PY_TYPE_DICT:
            py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "unhashable type: 'dict'"));
            return -1;
        case PY_TYPE_SET:
            py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "unhashable type: 'set'"));
            return -1;
        case PY_TYPE_BYTEARRAY:
            py_raise_owned(py_exc_new(PY_EXC_TYPEERROR, "unhashable type: 'bytearray'"));
            return -1;
        case PY_TYPE_TUPLE: {
            PyTupleObject *t = (PyTupleObject *)o;
            uint64_t h = 3527539u;
            uint64_t mult = 1000003u;
            for (int64_t i = 0; i < t->len; i++) {
                PyObject *item = cmp_tuple_item(o, t, i);
                int64_t item_hash = 0;
                if (!py_obj_hash_leaf_fast(item, &item_hash)) {
                    item_hash = py_obj_hash(item);
                    if (py_err_occurred()) return -1;
                }
                h = (h ^ (uint64_t)item_hash) * mult;
                h += 82520u + (uint64_t)i + (uint64_t)i;
                h &= 0x7fffffffffffffffull;
                mult += 82520u + (uint64_t)i + (uint64_t)i;
            }
            int64_t out = (int64_t)(h + 97531u);
            return (out == -1) ? -2 : out;
        }
        default:
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
                int64_t handled = 0;
                int64_t user_hash = py_user_hash_dispatch(o, &handled);
                if (handled) {
                    return user_hash;
                }
            }
            return 0;
    }
}

/* Sets order by subset/superset (a PARTIAL order), not the total 3-way
 * compare: ``a <= b`` is a.issubset(b), ``a < b`` is proper subset, etc.
 * Two disjoint/overlapping sets are incomparable (all four return 0). */
static int both_sets(PyObject *a, PyObject *b) {
    return py_type_of(a) == PY_TYPE_SET && py_type_of(b) == PY_TYPE_SET;
}
int64_t py_obj_lt(PyObject *a, PyObject *b) {
    if (
        pcc_capi_is_cext_type_tag(py_type_of(a))
        || pcc_capi_is_cext_type_tag(py_type_of(b))
    ) {
        return pcc_capi_cext_richcompare_bool(a, b, 0) > 0 ? 1 : 0;
    }
    if (both_sets(a, b)) {
        return py_set_issubset(a, b) && py_set_len(a) < py_set_len(b);
    }
    return py_obj_cmp_threeway(a, b) < 0;
}
int64_t py_obj_le(PyObject *a, PyObject *b) {
    if (
        pcc_capi_is_cext_type_tag(py_type_of(a))
        || pcc_capi_is_cext_type_tag(py_type_of(b))
    ) {
        return pcc_capi_cext_richcompare_bool(a, b, 1) > 0 ? 1 : 0;
    }
    if (both_sets(a, b)) return py_set_issubset(a, b);
    return py_obj_cmp_threeway(a, b) <= 0;
}
int64_t py_obj_gt(PyObject *a, PyObject *b) {
    if (
        pcc_capi_is_cext_type_tag(py_type_of(a))
        || pcc_capi_is_cext_type_tag(py_type_of(b))
    ) {
        return pcc_capi_cext_richcompare_bool(a, b, 4) > 0 ? 1 : 0;
    }
    if (both_sets(a, b)) {
        return py_set_issuperset(a, b) && py_set_len(a) > py_set_len(b);
    }
    return py_obj_cmp_threeway(a, b) > 0;
}
int64_t py_obj_ge(PyObject *a, PyObject *b) {
    if (
        pcc_capi_is_cext_type_tag(py_type_of(a))
        || pcc_capi_is_cext_type_tag(py_type_of(b))
    ) {
        return pcc_capi_cext_richcompare_bool(a, b, 5) > 0 ? 1 : 0;
    }
    if (both_sets(a, b)) return py_set_issuperset(a, b);
    return py_obj_cmp_threeway(a, b) >= 0;
}

PyObject *py_obj_sorted(PyObject *x) {
    if (x == NULL) return NULL;
    PyObject *x_root = x;
    void *x_handle = NULL;
    int64_t moving_backend = pcc_gc_backend();
    if (
        moving_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
        || moving_backend == PCC_GC_KIND_COLORED_RELOCATING
    ) {
        x_handle = pcc_gc_scheduler_root_register_handle(&x_root);
        if (x_handle == NULL) return NULL;
        x = pcc_gc_load_ptr(NULL, &x_root);
    }
    int64_t n = py_obj_len(x);
    if (x_handle != NULL) x = pcc_gc_load_ptr(NULL, &x_root);
    /* py_obj_len is only a sizing hint here. A custom iterator (user class
     * with __iter__/__next__ but no __len__) raises from py_obj_len; left
     * pending, that aborts the iterator loop below and yields []. Clear it —
     * the iterator-protocol branch handles length-less sources. */
    if (py_err_occurred()) {
        py_clear_exception();
        n = 0;
    }
    PyObject *out = py_list_new(n);
    if (out == NULL) {
        if (x_handle != NULL) {
            pcc_gc_scheduler_root_unregister_handle(x_handle);
        }
        return NULL;
    }
    pcc_gc_pin(out);
    if (py_type_of(x) == PY_TYPE_SET) {
        for (int64_t i = 0;; i++) {
            if (x_handle != NULL) x = pcc_gc_load_ptr(NULL, &x_root);
            PySetObject *s = (PySetObject *)x;
            if (i >= s->capacity) break;
            PyObject *key = cmp_set_key(s, &s->entries[i]);
            if (key == NULL || key == py_set_dummy) continue;
            py_list_append(out, key);
        }
    } else {
        /* All other iterables — list, tuple, dict (-> keys), generator, range,
         * etc. — use the iterator protocol. py_obj_next returns an OWNED ref so
         * the py_decref(el) below is balanced. (The previous LIST/TUPLE
         * getitem fill branch treated the BORROWED element ref as owned and
         * decref'd it, under-counting heap elements -> double-free on clear;
         * the pcc-Python port has no such branch and routes list/tuple here.) */
        PyObject *it = py_obj_iter(x);
        if (it != NULL) {
            PyObject *it_root = it;
            void *it_handle = NULL;
            if (
                moving_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR
                || moving_backend == PCC_GC_KIND_COLORED_RELOCATING
            ) {
                it_handle = pcc_gc_scheduler_root_register_handle(&it_root);
                if (it_handle == NULL) {
                    py_decref(it);
                    pcc_gc_unpin(out);
                    py_decref(out);
                    if (x_handle != NULL) {
                        pcc_gc_scheduler_root_unregister_handle(x_handle);
                    }
                    return NULL;
                }
                it = pcc_gc_load_ptr(NULL, &it_root);
            }
            for (;;) {
                if (it_handle != NULL) it = pcc_gc_load_ptr(NULL, &it_root);
                PyObject *el = py_obj_next(it);
                if (it_handle != NULL) it = pcc_gc_load_ptr(NULL, &it_root);
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
            if (it_handle != NULL) {
                it = pcc_gc_load_ptr(NULL, &it_root);
                pcc_gc_scheduler_root_unregister_handle(it_handle);
            }
            py_decref(it);
        }
    }
    int64_t m = py_list_len(out);
    if (m > 1) {
        /* Bottom-up stable merge sort (was insertion sort — O(n^2)
         * comparisons dominated codegen-worker profiles via sorted
         * symbol lists). Ping-pong between `out` and a scratch py_list:
         * every element stays visible to the GC in at least one list
         * slot (all slot writes go through py_list_append's store
         * barrier), elements are MOVED borrowed (C list contract:
         * append does not incref, raw loads do not incref), and the
         * scratch is emptied by resetting `length` before release so
         * its aliasing slots never trigger element decrefs. Stability:
         * the right run only wins when strictly smaller. */
        PyObject *scratch = py_list_new(m);
        if (scratch != NULL) {
            pcc_gc_pin(scratch);
            PyObject *src_list = out;
            PyObject *dst_list = scratch;
            for (int64_t width = 1; width < m; width *= 2) {
                PyListObject *src = (PyListObject *)src_list;
                PyListObject *dst = (PyListObject *)dst_list;
                /* Reset dst through the BALANCED slot store: py_list
                 * stores go through pcc_gc_store_ptr (incref new /
                 * decref old), so a bare `length = 0` would leak one
                 * reference per element per pass. Clearing keeps every
                 * element alive via its other list's slot. */
                for (int64_t ci = 0; ci < dst->length; ci++) {
                    pcc_gc_store_ptr(dst_list, &dst->items[ci], NULL);
                }
                dst->length = 0;
                for (int64_t lo = 0; lo < m; lo += 2 * width) {
                    int64_t mid = lo + width;
                    if (mid > m) mid = m;
                    int64_t hi = mid + width;
                    if (hi > m) hi = m;
                    int64_t i = lo;
                    int64_t j = mid;
                    while (i < mid && j < hi) {
                        PyObject *ea =
                            pcc_gc_load_ptr(src_list, &src->items[i]);
                        PyObject *eb =
                            pcc_gc_load_ptr(src_list, &src->items[j]);
                        if (py_obj_cmp_threeway(eb, ea) < 0) {
                            eb = pcc_gc_load_ptr(
                                src_list, &src->items[j]
                            );
                            py_list_append(dst_list, eb);
                            j++;
                        } else {
                            ea = pcc_gc_load_ptr(
                                src_list, &src->items[i]
                            );
                            py_list_append(dst_list, ea);
                            i++;
                        }
                    }
                    while (i < mid) {
                        py_list_append(
                            dst_list,
                            pcc_gc_load_ptr(src_list, &src->items[i]));
                        i++;
                    }
                    while (j < hi) {
                        py_list_append(
                            dst_list,
                            pcc_gc_load_ptr(src_list, &src->items[j]));
                        j++;
                    }
                }
                PyObject *tmp = src_list;
                src_list = dst_list;
                dst_list = tmp;
            }
            if (src_list != out) {
                /* Final ordering ended in the scratch: move it back
                 * (balanced: clear out's stale aliases, then append —
                 * each element stays held by the scratch slot). */
                PyListObject *src = (PyListObject *)src_list;
                PyListObject *ao = (PyListObject *)out;
                for (int64_t ci = 0; ci < ao->length; ci++) {
                    pcc_gc_store_ptr(out, &ao->items[ci], NULL);
                }
                ao->length = 0;
                for (int64_t i = 0; i < m; i++) {
                    py_list_append(
                        out, pcc_gc_load_ptr(src_list, &src->items[i]));
                }
            }
            /* Release the scratch's element references (balanced) and
             * the scratch itself; out's slots keep the elements. */
            {
                PyListObject *so = (PyListObject *)scratch;
                for (int64_t ci = 0; ci < so->length; ci++) {
                    pcc_gc_store_ptr(scratch, &so->items[ci], NULL);
                }
                so->length = 0;
            }
            pcc_gc_unpin(scratch);
            py_decref(scratch);
            pcc_gc_unpin(out);
            if (x_handle != NULL) {
                pcc_gc_scheduler_root_unregister_handle(x_handle);
            }
            return out;
        }
        /* malloc-failure fallback: original insertion sort. */
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
    }
    pcc_gc_unpin(out);
    if (x_handle != NULL) {
        pcc_gc_scheduler_root_unregister_handle(x_handle);
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
                int equal = py_obj_eq(el, item) != 0;
                py_decref(el);
                if (py_err_occurred()) return 0;
                if (equal) return 1;
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
            if (tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER_CLASS_START) {
                int64_t handled = 0;
                int64_t result = py_user_contains_dispatch(container, item, &handled);
                if (handled) return result;
            }
            return 0;
    }
}
