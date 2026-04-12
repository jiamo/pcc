/* pcc/py_runtime/src/py_obj_ops.c
 *
 * Generic object ops that cut across types: equality, hashing, truthiness,
 * length, subscript dispatch, etc.
 *
 * Phase 2 scope:
 *   - py_obj_eq covers None/bool/int/float/str/tuple/list and defers to
 *     py_obj_eq for recursive containers. The simple rules:
 *       * pointer equality short-circuits (handles interned singletons).
 *       * tagged int ↔ heap int cross-representation compares via py_int_cmp.
 *       * floats compare numerically; int ↔ float compares as float.
 *       * str compares bytes via py_str_eq (defined in py_str.c or stubs).
 *       * tuple / list compare length and element-wise.
 *   - py_obj_hash implements a small, deterministic hash for each
 *     supported type. The hash matches nothing in particular outside the
 *     process — dicts/sets just need a function that's consistent with
 *     py_obj_eq. Tuple hash is cumulative XOR of element hashes (simple,
 *     fast, and satisfies the equality-implies-hash-equality property).
 *     None→0, True→1, False→0, int→the int value, float→rounded i64 if
 *     exact else FNV of its bits, str→FNV-1a of the UTF-8 bytes.
 *   - py_obj_len / py_obj_truthy dispatch by type tag and forward to the
 *     container's native len/truthy routine.
 *   - py_obj_getitem / py_obj_setitem dispatch to list/dict/tuple ops.
 *
 * The remaining generic ops (call, getattr, setattr, repr, str,
 * isinstance) stay stubbed in py_obj_stubs.c pending Phase 3.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ---- Helpers ---------------------------------------------------------- */

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

/* FNV-1a over a byte range. */
static int64_t fnv1a(const unsigned char *p, size_t n) {
    uint64_t h = 0xcbf29ce484222325ull;   /* FNV offset basis */
    for (size_t i = 0; i < n; i++) {
        h ^= (uint64_t)p[i];
        h *= 0x100000001b3ull;            /* FNV prime */
    }
    /* Avoid returning -1 since our hash API reserves no special value,
     * but several callers cache -1 to mean "not computed" — keep that
     * tradition: map -1 to -2. */
    int64_t out = (int64_t)h;
    if (out == -1) out = -2;
    return out;
}

/* ---- Equality --------------------------------------------------------- */

int py_obj_eq(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;

    int32_t ta = py_type_of(a);
    int32_t tb = py_type_of(b);

    /* bool vs bool: pointer equality already handled above; distinct
     * pointers means distinct values (there are only two singletons). */
    if (ta == PY_TYPE_BOOL && tb == PY_TYPE_BOOL) return 0;

    /* int/bool mixed: compare as int64. (In Python, True == 1 and
     * False == 0 compare equal; we respect that.) */
    if (is_int_like(ta) && is_int_like(tb)) {
        if (ta == PY_TYPE_INT && tb == PY_TYPE_INT) {
            /* Defer to py_int_cmp so bignum impls work. */
            return py_int_cmp(a, b) == 0;
        }
        return int_or_bool_as_i64(a) == int_or_bool_as_i64(b);
    }

    /* float equality — includes int/float cross-type. */
    if (ta == PY_TYPE_FLOAT && tb == PY_TYPE_FLOAT) {
        return ((PyFloatObject *)a)->value == ((PyFloatObject *)b)->value;
    }
    if (ta == PY_TYPE_FLOAT && is_int_like(tb)) {
        return ((PyFloatObject *)a)->value == (double)int_or_bool_as_i64(b);
    }
    if (tb == PY_TYPE_FLOAT && is_int_like(ta)) {
        return ((PyFloatObject *)b)->value == (double)int_or_bool_as_i64(a);
    }

    /* String equality: delegate to py_str_eq if the types match. */
    if (ta == PY_TYPE_STR && tb == PY_TYPE_STR) {
        return py_str_eq(a, b);
    }

    /* Tuple equality: same length, element-wise equal. */
    if (ta == PY_TYPE_TUPLE && tb == PY_TYPE_TUPLE) {
        PyTupleObject *ta_o = (PyTupleObject *)a;
        PyTupleObject *tb_o = (PyTupleObject *)b;
        if (ta_o->len != tb_o->len) return 0;
        for (int64_t i = 0; i < ta_o->len; i++) {
            if (!py_obj_eq(ta_o->items[i], tb_o->items[i])) return 0;
        }
        return 1;
    }

    /* List equality: same length, element-wise equal. */
    if (ta == PY_TYPE_LIST && tb == PY_TYPE_LIST) {
        PyListObject *la = (PyListObject *)a;
        PyListObject *lb = (PyListObject *)b;
        if (la->length != lb->length) return 0;
        for (int64_t i = 0; i < la->length; i++) {
            if (!py_obj_eq(la->items[i], lb->items[i])) return 0;
        }
        return 1;
    }

    /* None comparisons: only None == None (handled by pointer eq above). */
    if (ta == PY_TYPE_NONE || tb == PY_TYPE_NONE) return 0;

    /* TODO(phase3): dispatch __eq__ for user-defined classes. */
    return 0;
}

/* ---- Hashing ---------------------------------------------------------- */

int64_t py_obj_hash(PyObject *o) {
    if (o == NULL) return 0;

    /* Tagged int fast path: the int value is its own hash (matches
     * CPython's hash(n) == n for small ints). */
    if (PY_IS_TAGGED_INT(o)) {
        int64_t v = py_untag_int(o);
        /* Reserve -1 (CPython-style). */
        return (v == -1) ? -2 : v;
    }

    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_NONE:
            return 0;
        case PY_TYPE_BOOL:
            return (o == py_True) ? 1 : 0;
        case PY_TYPE_INT: {
            /* Phase-2 heap int may be a bignum; fall back to its int64
             * representation via the existing helper. Good enough for
             * hashing — any two ints with the same i64 value hash the
             * same, which is all the dict/set contract requires. */
            int64_t v = py_int_value_i64(o);
            return (v == -1) ? -2 : v;
        }
        case PY_TYPE_FLOAT: {
            double d = ((PyFloatObject *)o)->value;
            /* If d is an exact integer, hash matches the integer's hash
             * so dict keys stay interoperable across int/float. */
            int64_t iv = (int64_t)d;
            if ((double)iv == d) {
                return (iv == -1) ? -2 : iv;
            }
            /* Otherwise hash the raw bits. */
            uint64_t bits;
            memcpy(&bits, &d, sizeof(bits));
            return fnv1a((const unsigned char *)&bits, sizeof(bits));
        }
        case PY_TYPE_STR: {
            PyStrObject *s = (PyStrObject *)o;
            if (s->hash != -1) return s->hash;
            int64_t h = fnv1a((const unsigned char *)s->data, (size_t)s->byte_len);
            s->hash = h;
            return h;
        }
        case PY_TYPE_TUPLE: {
            PyTupleObject *t = (PyTupleObject *)o;
            /* Cumulative XOR with a rotating mix for element hashes;
             * matches the spec's "cumulative XOR of element hashes" and
             * picks up order sensitivity via the left-rotation. */
            uint64_t h = 0xbb40e64d;   /* arbitrary non-zero seed */
            for (int64_t i = 0; i < t->len; i++) {
                uint64_t eh = (uint64_t)py_obj_hash(t->items[i]);
                /* Rotate 7 bits so (a,b) hashes differently from (b,a). */
                h = ((h << 7) | (h >> 57)) ^ eh;
            }
            if ((int64_t)h == -1) h = (uint64_t)-2;
            return (int64_t)h;
        }
        default:
            /* list/dict/set/user types: unhashable. Return 0 for now;
             * TODO(phase3): raise TypeError via py_raise. */
            return 0;
    }
}

/* ---- Truthiness ------------------------------------------------------- */

int py_obj_truthy(PyObject *o) {
    if (o == NULL) return 0;
    if (o == py_None || o == py_False) return 0;
    if (o == py_True) return 1;
    if (PY_IS_TAGGED_INT(o)) return py_untag_int(o) != 0;
    int32_t tag = py_header(o)->type_tag;
    switch (tag) {
        case PY_TYPE_INT:
            /* Phase 2 bignum: non-zero magnitude is truthy. py_int_value_i64
             * gives us the int64 view which is zero iff the value is zero. */
            return py_int_value_i64(o) != 0;
        case PY_TYPE_FLOAT: return ((PyFloatObject *)o)->value != 0.0;
        case PY_TYPE_LIST:  return ((PyListObject *)o)->length != 0;
        case PY_TYPE_TUPLE: return ((PyTupleObject *)o)->len != 0;
        case PY_TYPE_STR:   return ((PyStrObject *)o)->byte_len != 0;
        case PY_TYPE_DICT:  return ((PyDictObject *)o)->size != 0;
        case PY_TYPE_SET:   return ((PySetObject *)o)->size != 0;
        default:
            /* TODO(phase3): __bool__ / __len__ dunder dispatch. */
            return 1;
    }
}

/* ---- Length ----------------------------------------------------------- */

int64_t py_obj_len(PyObject *o) {
    if (o == NULL) return 0;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:  return py_list_len(o);
        case PY_TYPE_TUPLE: return py_tuple_len(o);
        case PY_TYPE_STR:   return py_str_len(o);
        case PY_TYPE_DICT:  return py_dict_len(o);
        case PY_TYPE_SET:   return py_set_len(o);
        default:
            /* TODO(phase3): __len__ dunder dispatch for user types. */
            return 0;
    }
}

/* ``sorted(x)`` — create a new list, copy all elements, sort via
 * py_obj_eq / py_int_cmp / py_str_hash dispatch, return list.
 * Simple insertion sort — fine for pcc's own sort sites (small n).
 * Elements are compared via a helper that handles int<->int and
 * str<->str orderings; anything else falls back to address order
 * (stable but not Python-equivalent). */
static int sorted_cmp(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return 0;
    int32_t ta = py_type_of(a);
    int32_t tb = py_type_of(b);
    int a_is_int = (ta == PY_TYPE_INT || ta == PY_TYPE_BOOL);
    int b_is_int = (tb == PY_TYPE_INT || tb == PY_TYPE_BOOL);
    if (a_is_int && b_is_int) return py_int_cmp(a, b);
    if (ta == PY_TYPE_STR && tb == PY_TYPE_STR) {
        PyStrObject *sa = (PyStrObject *)a;
        PyStrObject *sb = (PyStrObject *)b;
        int64_t n = sa->byte_len < sb->byte_len ? sa->byte_len : sb->byte_len;
        int r = memcmp(sa->data, sb->data, (size_t)n);
        if (r != 0) return r < 0 ? -1 : 1;
        if (sa->byte_len == sb->byte_len) return 0;
        return sa->byte_len < sb->byte_len ? -1 : 1;
    }
    /* Fallback: stable address order. */
    if ((uintptr_t)a < (uintptr_t)b) return -1;
    if ((uintptr_t)a > (uintptr_t)b) return 1;
    return 0;
}

PyObject *py_obj_sorted(PyObject *x) {
    if (x == NULL) return NULL;
    int64_t n = py_obj_len(x);
    PyObject *out = py_list_new(n);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < n; i++) {
        PyObject *idx_box = py_int_from_i64(i);
        PyObject *el = py_obj_getitem(x, idx_box);
        py_list_append(out, el);
        py_decref(idx_box);
    }
    /* Insertion sort on the appended elements. */
    int64_t m = py_list_len(out);
    for (int64_t i = 1; i < m; i++) {
        PyObject *cur = py_list_get(out, i);
        int64_t j = i;
        while (j > 0) {
            PyObject *prev = py_list_get(out, j - 1);
            if (sorted_cmp(prev, cur) <= 0) break;
            /* swap */
            py_list_set(out, j, prev);
            j--;
        }
        py_list_set(out, j, cur);
    }
    return out;
}

/* Membership test: ``item in container``. Dispatches on the
 * container's type tag; returns 1 if contained, 0 otherwise. */
int py_obj_contains(PyObject *container, PyObject *item) {
    if (container == NULL) return 0;
    int32_t tag = py_type_of(container);
    switch (tag) {
        case PY_TYPE_LIST:  return py_list_contains(container, item);
        case PY_TYPE_TUPLE: {
            /* No dedicated tuple_contains: linear scan via obj_eq. */
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
            /* TODO(phase3): __contains__ dunder dispatch for user types. */
            return 0;
    }
}

/* ---- Subscript dispatch ---------------------------------------------- */

PyObject *py_obj_getitem(PyObject *o, PyObject *k) {
    if (o == NULL || k == NULL) return NULL;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            /* Keys are ints; extract the int64 index. */
            if (py_type_of(k) == PY_TYPE_INT) {
                return py_list_get(o, py_int_value_i64(k));
            }
            return NULL;
        case PY_TYPE_TUPLE:
            if (py_type_of(k) == PY_TYPE_INT) {
                return py_tuple_get(o, py_int_value_i64(k));
            }
            return NULL;
        case PY_TYPE_DICT:
            /* dict[k] returns value or NULL if missing; Phase 3 will
             * raise KeyError. */
            return py_dict_get(o, k);
        case PY_TYPE_STR:
            return py_str_index(o, k);
        default:
            /* TODO(phase3): __getitem__ dispatch. */
            return NULL;
    }
}

int py_obj_setitem(PyObject *o, PyObject *k, PyObject *v) {
    if (o == NULL || k == NULL) return -1;
    int32_t tag = py_type_of(o);
    switch (tag) {
        case PY_TYPE_LIST:
            if (py_type_of(k) == PY_TYPE_INT) {
                py_list_set(o, py_int_value_i64(k), v);
                return 0;
            }
            return -1;
        case PY_TYPE_DICT:
            py_dict_set(o, k, v);
            return 0;
        default:
            /* TODO(phase3): __setitem__ dispatch. */
            return -1;
    }
}

/* ---- Phase 3: class / instance ops ----------------------------------- */

/* Is this type tag a user-defined instance (either the generic
 * PY_TYPE_INSTANCE bucket or a per-class PY_TYPE_USER+N tag)? */
static int is_instance_tag(int32_t tag) {
    return tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER;
}

PyObject *py_obj_getattr(PyObject *o, const char *name) {
    if (!o || !name) return NULL;
    if (PY_IS_TAGGED_INT(o)) return NULL;
    int32_t tag = py_header(o)->type_tag;

    if (is_instance_tag(tag)) {
        return py_instance_getattr((PyInstanceObject *)o, name);
    }
    if (tag == PY_TYPE_CLASS) {
        /* Attribute access on a class looks up methods via MRO. Fields
         * are not class-level in Phase 3 (no class-variables yet). */
        return py_class_lookup((PyClassObject *)o, name);
    }
    /* Other types: no getattr yet (strings / lists expose methods through
     * dedicated AST dispatch at codegen time, not through this path). */
    return NULL;
}

int py_obj_setattr(PyObject *o, const char *name, PyObject *v) {
    if (!o || !name) return -1;
    if (PY_IS_TAGGED_INT(o)) return -1;
    int32_t tag = py_header(o)->type_tag;

    if (is_instance_tag(tag)) {
        return py_instance_setattr((PyInstanceObject *)o, name, v);
    }
    /* Setting attributes on classes is not permitted in Phase 3. */
    return -1;
}

PyObject *py_obj_call(PyObject *callable, PyObject *args, PyObject *kwargs) {
    if (!callable) return NULL;
    if (PY_IS_TAGGED_INT(callable)) return NULL;
    int32_t tag = py_header(callable)->type_tag;

    if (tag == PY_TYPE_CLASS) {
        /* Class-as-callable: allocate an instance, then invoke __init__
         * if the class defines one. The instance is returned regardless
         * (matching Python semantics — __init__ mutates but does not
         * replace the instance).
         *
         * Phase 3 lowers __init__ via a dedicated trampoline emitted by
         * the codegen — we don't know the ABI to call it through from
         * this generic helper. So we just allocate and return; the
         * codegen is expected to emit the __init__ call itself for the
         * cases it can see statically. For the fully dynamic path this
         * gives a valid, zero-initialized instance. */
        PyClassObject *cls = (PyClassObject *)callable;
        PyObject *inst = py_instance_new(cls);
        (void)args;
        (void)kwargs;
        return inst;
    }

    /* TODO(phase3): function objects carry an fptr + signature tag and
     * we'd trampoline through a dispatch table here. For now the codegen
     * side-steps py_obj_call for user function calls by emitting direct
     * calls when the target is statically resolvable. */
    return NULL;
}

int py_obj_isinstance(PyObject *o, PyObject *cls) {
    if (!o || !cls) return 0;
    if (PY_IS_TAGGED_INT(cls)) return 0;
    if (py_header(cls)->type_tag != PY_TYPE_CLASS) return 0;
    return py_isinstance(o, (PyClassObject *)cls);
}
