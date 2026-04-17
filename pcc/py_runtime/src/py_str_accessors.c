/* pcc/py_runtime/src/py_str_accessors.c
 *
 * PyStrObject accessors and small string helpers split out of py_str.c
 * so they can be independently replaced by py_str_accessors.py. Most
 * functions operate on raw bytes; py_str_len and py_str_find preserve
 * Python's codepoint-visible length/offset semantics.
 *
 * py_str.c now keeps only py_str_new; this file owns the rest of the
 * string ABI so it can be replaced by the pcc-Python port as one unit.
 */

#include "py_internal.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <stdint.h>

static PyStrObject *str_alloc_local(int64_t byte_len);

static int64_t utf8_codepoint_count(const char *bytes, int64_t byte_len) {
    int64_t count = 0;
    for (int64_t i = 0; i < byte_len; i++) {
        unsigned char b = (unsigned char)bytes[i];
        if ((b & 0xC0) != 0x80) count++;
    }
    return count;
}

static int64_t byte_find(const char *hay, int64_t hay_len,
                          const char *need, int64_t need_len) {
    if (need_len == 0) return 0;
    if (need_len > hay_len) return -1;
    int64_t last = hay_len - need_len;
    for (int64_t i = 0; i <= last; i++) {
        if (hay[i] == need[0] && memcmp(hay + i, need, (size_t)need_len) == 0) {
            return i;
        }
    }
    return -1;
}

static int64_t byte_offset_to_cp_offset(PyStrObject *s, int64_t byte_off) {
    if (byte_off <= 0) return 0;
    if (byte_off >= s->byte_len) byte_off = s->byte_len;
    return utf8_codepoint_count(s->data, byte_off);
}

static int64_t str_cp_len(PyStrObject *s) {
    if (s->cp_len < 0) {
        s->cp_len = utf8_codepoint_count(s->data, s->byte_len);
    }
    return s->cp_len;
}

static int64_t utf8_byte_offset_for_codepoint(PyStrObject *s, int64_t cp_idx) {
    if (cp_idx <= 0) return 0;
    int64_t seen = 0;
    for (int64_t i = 0; i < s->byte_len; i++) {
        unsigned char b = (unsigned char)s->data[i];
        if ((b & 0xC0) != 0x80) {
            if (seen == cp_idx) return i;
            seen++;
        }
    }
    return s->byte_len;
}

static int64_t utf8_codepoint_byte_len(const PyStrObject *s, int64_t byte_off) {
    if (byte_off < 0 || byte_off >= s->byte_len) return 0;
    unsigned char b = (unsigned char)s->data[byte_off];
    if ((b & 0x80) == 0x00) return 1;
    if ((b & 0xE0) == 0xC0) return 2;
    if ((b & 0xF0) == 0xE0) return 3;
    if ((b & 0xF8) == 0xF0) return 4;
    return 1;
}

static int64_t clamp_slice_index(int64_t i, int64_t cp_len) {
    if (i < 0) {
        i += cp_len;
        if (i < 0) i = 0;
    } else if (i > cp_len) {
        i = cp_len;
    }
    return i;
}

static int64_t normalise_index(int64_t i, int64_t cp_len) {
    if (i < 0) i += cp_len;
    if (i < 0 || i >= cp_len) return -1;
    return i;
}

static int64_t int_or_default(PyObject *o, int64_t defval) {
    if (o == NULL || o == py_None) return defval;
    if (PY_IS_TAGGED_INT(o)) return py_untag_int(o);
    if (py_type_of(o) == PY_TYPE_INT) return py_int_value_i64(o);
    return defval;
}

static PyObject *str_from_range(const char *bytes, int64_t len) {
    PyStrObject *s = str_alloc_local(len);
    if (s == NULL) return NULL;
    if (len > 0) memcpy(s->data, bytes, (size_t)len);
    return (PyObject *)s;
}

int64_t py_str_byte_len(PyObject *s) {
    if (s == NULL) return 0;
    return ((PyStrObject *)s)->byte_len;
}

const char *py_str_utf8(PyObject *s) {
    if (s == NULL) return "";
    return ((PyStrObject *)s)->data;
}

int64_t py_str_len(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->cp_len < 0) {
        ss->cp_len = utf8_codepoint_count(ss->data, ss->byte_len);
    }
    return ss->cp_len;
}

int64_t py_str_ord(PyObject *s) {
    if (s == NULL) return -1;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->byte_len <= 0) return -1;
    const unsigned char *p = (const unsigned char *)ss->data;
    unsigned char b0 = p[0];
    if (b0 < 0x80) return (int64_t)b0;
    if ((b0 & 0xE0) == 0xC0) {
        if (ss->byte_len < 2) return -1;
        return (int64_t)(((b0 & 0x1F) << 6) | (p[1] & 0x3F));
    }
    if ((b0 & 0xF0) == 0xE0) {
        if (ss->byte_len < 3) return -1;
        return (int64_t)(
            ((b0 & 0x0F) << 12)
            | ((p[1] & 0x3F) << 6)
            | (p[2] & 0x3F)
        );
    }
    if ((b0 & 0xF8) == 0xF0) {
        if (ss->byte_len < 4) return -1;
        return (int64_t)(
            ((b0 & 0x07) << 18)
            | ((p[1] & 0x3F) << 12)
            | ((p[2] & 0x3F) << 6)
            | (p[3] & 0x3F)
        );
    }
    return -1;
}

PyObject *py_chr_from_i64(int64_t codepoint) {
    unsigned char buf[4];
    int64_t len = 0;
    if (codepoint < 0 || codepoint > 0x10FFFF) return NULL;
    if (codepoint >= 0xD800 && codepoint <= 0xDFFF) return NULL;
    if (codepoint <= 0x7F) {
        buf[0] = (unsigned char)codepoint;
        len = 1;
    } else if (codepoint <= 0x7FF) {
        buf[0] = (unsigned char)(0xC0 | (codepoint >> 6));
        buf[1] = (unsigned char)(0x80 | (codepoint & 0x3F));
        len = 2;
    } else if (codepoint <= 0xFFFF) {
        buf[0] = (unsigned char)(0xE0 | (codepoint >> 12));
        buf[1] = (unsigned char)(0x80 | ((codepoint >> 6) & 0x3F));
        buf[2] = (unsigned char)(0x80 | (codepoint & 0x3F));
        len = 3;
    } else {
        buf[0] = (unsigned char)(0xF0 | (codepoint >> 18));
        buf[1] = (unsigned char)(0x80 | ((codepoint >> 12) & 0x3F));
        buf[2] = (unsigned char)(0x80 | ((codepoint >> 6) & 0x3F));
        buf[3] = (unsigned char)(0x80 | (codepoint & 0x3F));
        len = 4;
    }
    return str_from_range((const char *)buf, len);
}

int64_t py_str_eq(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (a == NULL || b == NULL) return 0;
    PyStrObject *sa = (PyStrObject *)a;
    PyStrObject *sb = (PyStrObject *)b;
    if (sa->byte_len != sb->byte_len) return 0;
    if (sa->byte_len == 0) return 1;
    return memcmp(sa->data, sb->data, (size_t)sa->byte_len) == 0;
}

int64_t py_str_contains(PyObject *s, PyObject *sub) {
    if (s == NULL || sub == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *sn = (PyStrObject *)sub;
    return byte_find(ss->data, ss->byte_len, sn->data, sn->byte_len) != -1;
}

int64_t py_str_find(PyObject *s, PyObject *sub) {
    if (s == NULL || sub == NULL) return -1;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *sn = (PyStrObject *)sub;
    int64_t bo = byte_find(ss->data, ss->byte_len, sn->data, sn->byte_len);
    if (bo < 0) return -1;
    return byte_offset_to_cp_offset(ss, bo);
}

int64_t py_str_startswith(PyObject *s, PyObject *prefix) {
    if (s == NULL || prefix == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *sp = (PyStrObject *)prefix;
    if (sp->byte_len > ss->byte_len) return 0;
    if (sp->byte_len == 0) return 1;
    return memcmp(ss->data, sp->data, (size_t)sp->byte_len) == 0;
}

int64_t py_str_endswith(PyObject *s, PyObject *suffix) {
    if (s == NULL || suffix == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *sf = (PyStrObject *)suffix;
    if (sf->byte_len > ss->byte_len) return 0;
    if (sf->byte_len == 0) return 1;
    return memcmp(ss->data + (ss->byte_len - sf->byte_len),
                  sf->data, (size_t)sf->byte_len) == 0;
}

int64_t py_str_isdigit(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->byte_len == 0) return 0;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        if (c < '0' || c > '9') return 0;
    }
    return 1;
}

int64_t py_str_isalpha(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->byte_len == 0) return 0;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        int ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
        if (!ok) return 0;
    }
    return 1;
}

int64_t py_str_isspace(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->byte_len == 0) return 0;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        int ws = (c == ' ' || c == '\t' || c == '\n' || c == '\r' ||
                  c == '\v' || c == '\f');
        if (!ws) return 0;
    }
    return 1;
}

int64_t py_str_isalnum(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->byte_len == 0) return 0;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        int ok = (c >= '0' && c <= '9')
                 || (c >= 'a' && c <= 'z')
                 || (c >= 'A' && c <= 'Z');
        if (!ok) return 0;
    }
    return 1;
}

/* ---- ASCII whitespace strip variants (byte-level) ---- */

static int strip_is_ascii_ws(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n'
        || c == '\r' || c == '\v' || c == '\f';
}

PyObject *py_str_strip(PyObject *s) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (lo < hi && strip_is_ascii_ws((unsigned char)ss->data[lo])) lo++;
    while (hi > lo && strip_is_ascii_ws((unsigned char)ss->data[hi - 1])) hi--;
    return py_str_new(ss->data + lo, hi - lo);
}

PyObject *py_str_lstrip(PyObject *s) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (lo < hi && strip_is_ascii_ws((unsigned char)ss->data[lo])) lo++;
    return py_str_new(ss->data + lo, hi - lo);
}

PyObject *py_str_rstrip(PyObject *s) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (hi > lo && strip_is_ascii_ws((unsigned char)ss->data[hi - 1])) hi--;
    return py_str_new(ss->data + lo, hi - lo);
}

/* ---- chars-aware strip variants ---- */

static int strip_is_in_chars(unsigned char c, const unsigned char *chars, int64_t n) {
    for (int64_t k = 0; k < n; k++) {
        if (chars[k] == c) return 1;
    }
    return 0;
}

PyObject *py_str_strip_chars(PyObject *s, PyObject *chars) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *cs = (PyStrObject *)chars;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (lo < hi && strip_is_in_chars(
        (unsigned char)ss->data[lo],
        (const unsigned char *)cs->data, cs->byte_len)) lo++;
    while (hi > lo && strip_is_in_chars(
        (unsigned char)ss->data[hi - 1],
        (const unsigned char *)cs->data, cs->byte_len)) hi--;
    return py_str_new(ss->data + lo, hi - lo);
}

PyObject *py_str_lstrip_chars(PyObject *s, PyObject *chars) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *cs = (PyStrObject *)chars;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (lo < hi && strip_is_in_chars(
        (unsigned char)ss->data[lo],
        (const unsigned char *)cs->data, cs->byte_len)) lo++;
    return py_str_new(ss->data + lo, hi - lo);
}

PyObject *py_str_rstrip_chars(PyObject *s, PyObject *chars) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *cs = (PyStrObject *)chars;
    int64_t lo = 0;
    int64_t hi = ss->byte_len;
    while (hi > lo && strip_is_in_chars(
        (unsigned char)ss->data[hi - 1],
        (const unsigned char *)cs->data, cs->byte_len)) hi--;
    return py_str_new(ss->data + lo, hi - lo);
}

/* ---- concat / repeat (byte-level alloc + copy) ---- */

/* Local replica of py_str_alloc (static in py_str.c). Allocate a
 * fresh PyStrObject sized for byte_len + NUL terminator. */
static PyStrObject *str_alloc_local(int64_t byte_len) {
    if (byte_len < 0) return NULL;
    size_t total = sizeof(PyStrObject) + (size_t)byte_len + 1u;
    PyStrObject *s = (PyStrObject *)malloc(total);
    if (s == NULL) return NULL;
    s->h.refcount = 1;
    s->h.type_tag = PY_TYPE_STR;
    s->h.flags    = 0;
    s->byte_len   = byte_len;
    s->cp_len     = -1;
    s->hash       = -1;
    s->data[byte_len] = '\0';
    return s;
}

PyObject *py_str_upper(PyObject *s) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *out = str_alloc_local(ss->byte_len);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        if (c >= 'a' && c <= 'z') c = (unsigned char)(c - ('a' - 'A'));
        out->data[i] = (char)c;
    }
    out->cp_len = ss->cp_len;
    return (PyObject *)out;
}

PyObject *py_str_lower(PyObject *s) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *out = str_alloc_local(ss->byte_len);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        unsigned char c = (unsigned char)ss->data[i];
        if (c >= 'A' && c <= 'Z') c = (unsigned char)(c + ('a' - 'A'));
        out->data[i] = (char)c;
    }
    out->cp_len = ss->cp_len;
    return (PyObject *)out;
}

PyObject *py_str_concat(PyObject *a, PyObject *b) {
    if (a == NULL || b == NULL) return NULL;
    PyStrObject *sa = (PyStrObject *)a;
    PyStrObject *sb = (PyStrObject *)b;
    int64_t total = sa->byte_len + sb->byte_len;
    PyStrObject *out = str_alloc_local(total);
    if (out == NULL) return NULL;
    if (sa->byte_len > 0) memcpy(out->data, sa->data, (size_t)sa->byte_len);
    if (sb->byte_len > 0) memcpy(out->data + sa->byte_len, sb->data, (size_t)sb->byte_len);
    if (sa->cp_len >= 0 && sb->cp_len >= 0) {
        out->cp_len = sa->cp_len + sb->cp_len;
    }
    return (PyObject *)out;
}

PyObject *py_str_repeat(PyObject *s, PyObject *n) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t count = 0;
    if (n != NULL && n != py_None && py_type_of(n) == PY_TYPE_INT) {
        count = py_int_value_i64(n);
    }
    if (count <= 0 || ss->byte_len == 0) return py_str_new("", 0);
    if (count > INT64_MAX / ss->byte_len) return NULL;
    int64_t total = count * ss->byte_len;
    PyStrObject *out = str_alloc_local(total);
    if (out == NULL) return NULL;
    for (int64_t i = 0; i < count; i++) {
        memcpy(out->data + i * ss->byte_len, ss->data, (size_t)ss->byte_len);
    }
    if (ss->cp_len >= 0) {
        out->cp_len = ss->cp_len * count;
    }
    return (PyObject *)out;
}

PyObject *py_str_slice(PyObject *s, PyObject *lo, PyObject *hi, PyObject *step) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t cp_len = str_cp_len(ss);
    int64_t step_v = (step == NULL || step == py_None) ? 1 : int_or_default(step, 1);
    if (step_v == 0) return NULL;

    int64_t lo_v, hi_v;
    if (step_v > 0) {
        lo_v = (lo == NULL || lo == py_None) ? 0      : int_or_default(lo, 0);
        hi_v = (hi == NULL || hi == py_None) ? cp_len : int_or_default(hi, cp_len);
        lo_v = clamp_slice_index(lo_v, cp_len);
        hi_v = clamp_slice_index(hi_v, cp_len);
        if (lo_v >= hi_v) return py_str_new("", 0);

        if (step_v == 1) {
            int64_t bo_lo = utf8_byte_offset_for_codepoint(ss, lo_v);
            int64_t bo_hi = utf8_byte_offset_for_codepoint(ss, hi_v);
            return str_from_range(ss->data + bo_lo, bo_hi - bo_lo);
        }

        int64_t bo_lo = utf8_byte_offset_for_codepoint(ss, lo_v);
        int64_t bo_hi = utf8_byte_offset_for_codepoint(ss, hi_v);
        int64_t cap = bo_hi - bo_lo;
        PyStrObject *out = str_alloc_local(cap);
        if (out == NULL) return NULL;
        int64_t out_bytes = 0;
        int64_t out_cps   = 0;
        int64_t cp_index  = lo_v;
        int64_t b = bo_lo;
        int64_t next_target = lo_v;
        while (b < bo_hi) {
            int64_t w = utf8_codepoint_byte_len(ss, b);
            if (cp_index == next_target) {
                memcpy(out->data + out_bytes, ss->data + b, (size_t)w);
                out_bytes += w;
                out_cps++;
                next_target += step_v;
            }
            b += w;
            cp_index++;
        }
        out->byte_len = out_bytes;
        out->data[out_bytes] = '\0';
        out->cp_len = out_cps;
        return (PyObject *)out;
    }

    int64_t default_lo = cp_len - 1;
    int64_t default_hi = -1;
    lo_v = (lo == NULL || lo == py_None) ? default_lo : int_or_default(lo, default_lo);
    hi_v = (hi == NULL || hi == py_None) ? default_hi : int_or_default(hi, default_hi);

    if (lo_v < 0) lo_v += cp_len;
    if (lo_v >= cp_len) lo_v = cp_len - 1;
    if (lo_v < 0) return py_str_new("", 0);

    if (hi != NULL && hi != py_None) {
        if (hi_v < 0) hi_v += cp_len;
        if (hi_v < -1) hi_v = -1;
        if (hi_v > cp_len) hi_v = cp_len;
    }

    if (lo_v <= hi_v) return py_str_new("", 0);

    int64_t span = lo_v - hi_v;
    int64_t pos_step = -step_v;
    int64_t out_n = (span + pos_step - 1) / pos_step;

    int64_t *cp_off = (int64_t *)malloc(sizeof(int64_t) * (size_t)(cp_len + 1));
    if (cp_off == NULL) return NULL;
    {
        int64_t cp = 0;
        for (int64_t i = 0; i < ss->byte_len; i++) {
            unsigned char b = (unsigned char)ss->data[i];
            if ((b & 0xC0) != 0x80) {
                cp_off[cp++] = i;
            }
        }
        cp_off[cp_len] = ss->byte_len;
    }

    PyStrObject *out = str_alloc_local(ss->byte_len);
    if (out == NULL) { free(cp_off); return NULL; }
    int64_t out_bytes = 0;
    for (int64_t k = 0; k < out_n; k++) {
        int64_t cp = lo_v + step_v * k;
        int64_t start = cp_off[cp];
        int64_t end   = cp_off[cp + 1];
        int64_t w = end - start;
        memcpy(out->data + out_bytes, ss->data + start, (size_t)w);
        out_bytes += w;
    }
    out->byte_len = out_bytes;
    out->data[out_bytes] = '\0';
    out->cp_len = out_n;
    free(cp_off);
    return (PyObject *)out;
}

PyObject *py_str_index(PyObject *s, PyObject *i) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    int64_t idx = int_or_default(i, 0);
    int64_t cp_len = str_cp_len(ss);
    int64_t real = normalise_index(idx, cp_len);
    if (real < 0) return NULL;
    int64_t bo = utf8_byte_offset_for_codepoint(ss, real);
    int64_t w = utf8_codepoint_byte_len(ss, bo);
    PyObject *out = str_from_range(ss->data + bo, w);
    if (out != NULL) {
        ((PyStrObject *)out)->cp_len = 1;
    }
    return out;
}

/* ---- count ---- */

int64_t py_str_count(PyObject *s, PyObject *sub) {
    if (s == NULL || sub == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *ps = (PyStrObject *)sub;
    if (ps->byte_len == 0) return ss->byte_len + 1;
    int64_t count = 0;
    int64_t i = 0;
    while (i + ps->byte_len <= ss->byte_len) {
        if (ss->data[i] == ps->data[0]
            && memcmp(ss->data + i, ps->data, (size_t)ps->byte_len) == 0) {
            count++;
            i += ps->byte_len;
        } else {
            i++;
        }
    }
    return count;
}

int64_t py_str_hash(PyObject *s) {
    if (s == NULL) return 0;
    PyStrObject *ss = (PyStrObject *)s;
    if (ss->hash != -1) return ss->hash;

    uint64_t h = 0xcbf29ce484222325ull;
    const uint64_t prime = 0x100000001b3ull;
    for (int64_t i = 0; i < ss->byte_len; i++) {
        h ^= (unsigned char)ss->data[i];
        h *= prime;
    }

    int64_t result = (int64_t)h;
    if (result == -1) result = -2;
    ss->hash = result;
    return result;
}

static PyObject *py_str_splitlines_impl(PyObject *s, int keepends) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyObject *list = py_list_new(4);
    if (list == NULL) return NULL;
    int64_t start = 0;
    int64_t i = 0;
    while (i < ss->byte_len) {
        unsigned char c = (unsigned char)ss->data[i];
        if (c == '\r' || c == '\n') {
            int64_t end = i;
            int64_t after;
            if (c == '\r' && i + 1 < ss->byte_len && ss->data[i + 1] == '\n') {
                after = i + 2;
            } else {
                after = i + 1;
            }
            int64_t frag_end = keepends ? after : end;
            PyObject *part = str_from_range(ss->data + start, frag_end - start);
            if (part == NULL) { py_decref(list); return NULL; }
            py_list_append(list, part);
            py_decref(part);
            i = after;
            start = after;
        } else {
            i += 1;
        }
    }
    if (start < ss->byte_len) {
        PyObject *tail = str_from_range(ss->data + start, ss->byte_len - start);
        if (tail == NULL) { py_decref(list); return NULL; }
        py_list_append(list, tail);
        py_decref(tail);
    }
    return list;
}

PyObject *py_str_splitlines_keepends(PyObject *s, int keepends) {
    return py_str_splitlines_impl(s, keepends ? 1 : 0);
}

PyObject *py_str_splitlines(PyObject *s) {
    return py_str_splitlines_impl(s, 0);
}

static PyObject *py_str_split_whitespace(PyStrObject *ss) {
    PyObject *list = py_list_new(4);
    if (list == NULL) return NULL;
    int64_t i = 0;
    while (i < ss->byte_len) {
        while (i < ss->byte_len && strip_is_ascii_ws((unsigned char)ss->data[i])) i++;
        if (i >= ss->byte_len) break;
        int64_t start = i;
        while (i < ss->byte_len && !strip_is_ascii_ws((unsigned char)ss->data[i])) i++;
        PyObject *part = str_from_range(ss->data + start, i - start);
        if (part == NULL) { py_decref(list); return NULL; }
        py_list_append(list, part);
        py_decref(part);
    }
    return list;
}

PyObject *py_str_split(PyObject *s, PyObject *sep) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    if (sep == NULL || sep == py_None) {
        return py_str_split_whitespace(ss);
    }

    PyStrObject *sp = (PyStrObject *)sep;
    if (sp->byte_len == 0) return py_list_new(0);

    PyObject *list = py_list_new(4);
    if (list == NULL) return NULL;

    int64_t start = 0;
    int64_t i = 0;
    while (i + sp->byte_len <= ss->byte_len) {
        if (ss->data[i] == sp->data[0]
            && memcmp(ss->data + i, sp->data, (size_t)sp->byte_len) == 0)
        {
            PyObject *part = str_from_range(ss->data + start, i - start);
            if (part == NULL) { py_decref(list); return NULL; }
            py_list_append(list, part);
            py_decref(part);
            i += sp->byte_len;
            start = i;
        } else {
            i++;
        }
    }

    PyObject *tail = str_from_range(ss->data + start, ss->byte_len - start);
    if (tail == NULL) { py_decref(list); return NULL; }
    py_list_append(list, tail);
    py_decref(tail);
    return list;
}

static PyObject *py_str_split_whitespace_maxsplit(
    PyStrObject *ss, int64_t maxsplit
) {
    if (maxsplit < 0) return py_str_split_whitespace(ss);
    PyObject *list = py_list_new(4);
    if (list == NULL) return NULL;
    int64_t i = 0;
    int64_t splits = 0;
    while (i < ss->byte_len) {
        while (
            i < ss->byte_len
            && strip_is_ascii_ws((unsigned char)ss->data[i])
        ) {
            i++;
        }
        if (i >= ss->byte_len) break;
        if (splits >= maxsplit) {
            PyObject *tail = str_from_range(
                ss->data + i, ss->byte_len - i
            );
            if (tail == NULL) { py_decref(list); return NULL; }
            py_list_append(list, tail);
            py_decref(tail);
            return list;
        }
        int64_t start = i;
        while (
            i < ss->byte_len
            && !strip_is_ascii_ws((unsigned char)ss->data[i])
        ) {
            i++;
        }
        PyObject *part = str_from_range(ss->data + start, i - start);
        if (part == NULL) { py_decref(list); return NULL; }
        py_list_append(list, part);
        py_decref(part);
        splits++;
    }
    return list;
}

PyObject *py_str_split_maxsplit(
    PyObject *s, PyObject *sep, int64_t maxsplit
) {
    if (s == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    if (maxsplit < 0) return py_str_split(s, sep);
    if (sep == NULL || sep == py_None) {
        return py_str_split_whitespace_maxsplit(ss, maxsplit);
    }

    PyStrObject *sp = (PyStrObject *)sep;
    if (sp->byte_len == 0) return py_list_new(0);

    PyObject *list = py_list_new(4);
    if (list == NULL) return NULL;

    int64_t start = 0;
    int64_t i = 0;
    int64_t splits = 0;
    while (i + sp->byte_len <= ss->byte_len && splits < maxsplit) {
        if (ss->data[i] == sp->data[0]
            && memcmp(ss->data + i, sp->data, (size_t)sp->byte_len) == 0)
        {
            PyObject *part = str_from_range(ss->data + start, i - start);
            if (part == NULL) { py_decref(list); return NULL; }
            py_list_append(list, part);
            py_decref(part);
            i += sp->byte_len;
            start = i;
            splits++;
        } else {
            i++;
        }
    }

    PyObject *tail = str_from_range(ss->data + start, ss->byte_len - start);
    if (tail == NULL) { py_decref(list); return NULL; }
    py_list_append(list, tail);
    py_decref(tail);
    return list;
}

PyObject *py_str_join(PyObject *sep, PyObject *list) {
    if (sep == NULL || list == NULL) return NULL;
    PyStrObject *sp = (PyStrObject *)sep;
    PyListObject *l = (PyListObject *)list;

    if (l->length == 0) return py_str_new("", 0);

    int64_t total = 0;
    for (int64_t i = 0; i < l->length; i++) {
        PyObject *e = l->items[i];
        if (e == NULL || py_type_of(e) != PY_TYPE_STR) return NULL;
        if (i > 0) total += sp->byte_len;
        total += ((PyStrObject *)e)->byte_len;
    }

    PyStrObject *out = str_alloc_local(total);
    if (out == NULL) return NULL;

    int64_t off = 0;
    for (int64_t i = 0; i < l->length; i++) {
        PyStrObject *e = (PyStrObject *)l->items[i];
        if (i > 0 && sp->byte_len > 0) {
            memcpy(out->data + off, sp->data, (size_t)sp->byte_len);
            off += sp->byte_len;
        }
        if (e->byte_len > 0) {
            memcpy(out->data + off, e->data, (size_t)e->byte_len);
            off += e->byte_len;
        }
    }
    return (PyObject *)out;
}

static PyObject *py_str_replace_impl(
    PyObject *s, PyObject *old, PyObject *new_, int64_t maxreplace
) {
    if (s == NULL || old == NULL || new_ == NULL) return NULL;
    PyStrObject *ss = (PyStrObject *)s;
    PyStrObject *so = (PyStrObject *)old;
    PyStrObject *sn = (PyStrObject *)new_;

    if (so->byte_len == 0) {
        return py_str_new(ss->data, ss->byte_len);
    }

    if (maxreplace == 0) {
        return py_str_new(ss->data, ss->byte_len);
    }

    int64_t matches = 0;
    {
        int64_t i = 0;
        while (i + so->byte_len <= ss->byte_len) {
            if (ss->data[i] == so->data[0]
                && memcmp(ss->data + i, so->data, (size_t)so->byte_len) == 0)
            {
                matches++;
                if (maxreplace > 0 && matches >= maxreplace) break;
                i += so->byte_len;
            } else {
                i++;
            }
        }
    }
    if (matches == 0) {
        return py_str_new(ss->data, ss->byte_len);
    }

    int64_t delta = (sn->byte_len - so->byte_len) * matches;
    int64_t total = ss->byte_len + delta;
    if (total < 0) return NULL;

    PyStrObject *out = str_alloc_local(total);
    if (out == NULL) return NULL;

    int64_t read = 0;
    int64_t write = 0;
    int64_t replaced = 0;
    while (read + so->byte_len <= ss->byte_len) {
        if (ss->data[read] == so->data[0]
            && memcmp(ss->data + read, so->data, (size_t)so->byte_len) == 0)
        {
            if (maxreplace < 0 || replaced < maxreplace) {
                if (sn->byte_len > 0) {
                    memcpy(out->data + write, sn->data, (size_t)sn->byte_len);
                    write += sn->byte_len;
                }
                read += so->byte_len;
                replaced++;
                continue;
            }
            if (so->byte_len > 0) {
                memcpy(out->data + write, so->data, (size_t)so->byte_len);
                write += so->byte_len;
            }
            read += so->byte_len;
        } else {
            out->data[write++] = ss->data[read++];
        }
    }
    while (read < ss->byte_len) {
        out->data[write++] = ss->data[read++];
    }
    return (PyObject *)out;
}

PyObject *py_str_replace(PyObject *s, PyObject *old, PyObject *new_) {
    return py_str_replace_impl(s, old, new_, -1);
}

PyObject *py_str_replace_count(
    PyObject *s, PyObject *old, PyObject *new_, int64_t maxreplace
) {
    return py_str_replace_impl(s, old, new_, maxreplace);
}
