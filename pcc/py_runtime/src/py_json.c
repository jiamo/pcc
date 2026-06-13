/* pcc/py_runtime/src/py_json.c
 *
 * Minimal native JSON helpers for builtin json.loads/json.dumps dispatch.
 */

#include "py_internal.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    char *data;
    int64_t len;
    int64_t cap;
} JsonBuf;

static void json_skip_ws(const char *s, int64_t n, int64_t *pos) {
    while (*pos < n) {
        char c = s[*pos];
        if (c != ' ' && c != '\t' && c != '\n' && c != '\r') return;
        (*pos)++;
    }
}

static int json_buf_reserve(JsonBuf *b, int64_t extra) {
    int64_t want = b->len + extra + 1;
    if (want <= b->cap) return 0;
    int64_t cap = b->cap > 0 ? b->cap : 64;
    while (cap < want) cap *= 2;
    char *next = (char *)realloc(b->data, (size_t)cap);
    if (next == NULL) return -1;
    b->data = next;
    b->cap = cap;
    return 0;
}

static int json_buf_append_bytes(JsonBuf *b, const char *s, int64_t n) {
    if (json_buf_reserve(b, n) != 0) return -1;
    if (n > 0) memcpy(b->data + b->len, s, (size_t)n);
    b->len += n;
    b->data[b->len] = '\0';
    return 0;
}

static int json_buf_append_cstr(JsonBuf *b, const char *s) {
    return json_buf_append_bytes(b, s, (int64_t)strlen(s));
}

static int json_buf_append_char(JsonBuf *b, char c) {
    if (json_buf_reserve(b, 1) != 0) return -1;
    b->data[b->len++] = c;
    b->data[b->len] = '\0';
    return 0;
}

static int json_hex_digit(char c) {
    if (c >= '0' && c <= '9') return (int)(c - '0');
    if (c >= 'a' && c <= 'f') return 10 + (int)(c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (int)(c - 'A');
    return -1;
}

static int json_parse_hex4(const char *s, int64_t n, int64_t *pos, uint32_t *out) {
    if (*pos + 4 > n) return -1;
    uint32_t value = 0;
    for (int i = 0; i < 4; i++) {
        int d = json_hex_digit(s[*pos + i]);
        if (d < 0) return -1;
        value = (value << 4) | (uint32_t)d;
    }
    *pos += 4;
    *out = value;
    return 0;
}

static int json_buf_append_utf8(JsonBuf *b, uint32_t codepoint) {
    if (codepoint <= 0x7f) {
        return json_buf_append_char(b, (char)codepoint);
    }
    if (codepoint <= 0x7ff) {
        if (json_buf_reserve(b, 2) != 0) return -1;
        b->data[b->len++] = (char)(0xc0 | (codepoint >> 6));
        b->data[b->len++] = (char)(0x80 | (codepoint & 0x3f));
        b->data[b->len] = '\0';
        return 0;
    }
    if (codepoint <= 0xffff) {
        if (codepoint >= 0xd800 && codepoint <= 0xdfff) return -1;
        if (json_buf_reserve(b, 3) != 0) return -1;
        b->data[b->len++] = (char)(0xe0 | (codepoint >> 12));
        b->data[b->len++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        b->data[b->len++] = (char)(0x80 | (codepoint & 0x3f));
        b->data[b->len] = '\0';
        return 0;
    }
    if (codepoint <= 0x10ffff) {
        if (json_buf_reserve(b, 4) != 0) return -1;
        b->data[b->len++] = (char)(0xf0 | (codepoint >> 18));
        b->data[b->len++] = (char)(0x80 | ((codepoint >> 12) & 0x3f));
        b->data[b->len++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        b->data[b->len++] = (char)(0x80 | (codepoint & 0x3f));
        b->data[b->len] = '\0';
        return 0;
    }
    return -1;
}

static int json_buf_append_u00_escape(JsonBuf *b, unsigned char c) {
    char tmp[7];
    snprintf(tmp, sizeof(tmp), "\\u%04x", (unsigned int)c);
    return json_buf_append_cstr(b, tmp);
}

static int json_buf_append_quoted_str(JsonBuf *b, PyObject *obj) {
    if (obj == NULL || py_type_of(obj) != PY_TYPE_STR) return -1;
    PyStrObject *s = (PyStrObject *)obj;
    if (json_buf_append_char(b, '"') != 0) return -1;
    for (int64_t i = 0; i < s->byte_len; i++) {
        char c = s->data[i];
        if (c == '"' || c == '\\') {
            if (json_buf_append_char(b, '\\') != 0) return -1;
            if (json_buf_append_char(b, c) != 0) return -1;
        } else if (c == '\n') {
            if (json_buf_append_cstr(b, "\\n") != 0) return -1;
        } else if (c == '\r') {
            if (json_buf_append_cstr(b, "\\r") != 0) return -1;
        } else if (c == '\t') {
            if (json_buf_append_cstr(b, "\\t") != 0) return -1;
        } else if (c == '\b') {
            if (json_buf_append_cstr(b, "\\b") != 0) return -1;
        } else if (c == '\f') {
            if (json_buf_append_cstr(b, "\\f") != 0) return -1;
        } else if ((unsigned char)c < 0x20) {
            if (json_buf_append_u00_escape(b, (unsigned char)c) != 0) return -1;
        } else {
            if (json_buf_append_char(b, c) != 0) return -1;
        }
    }
    return json_buf_append_char(b, '"');
}

static PyObject *json_list_item(PyObject *owner, PyListObject *l, int64_t i) {
    return pcc_gc_load_ptr(owner, &l->items[i]);
}

static PyObject *json_dict_key(PyDictObject *d, DictEntry *e) {
    if (e->key == NULL) return NULL;
    return pcc_gc_load_ptr((PyObject *)d, &e->key);
}

static PyObject *json_dict_value(PyDictObject *d, DictEntry *e) {
    if (e->value == NULL) return NULL;
    return pcc_gc_load_ptr((PyObject *)d, &e->value);
}

static PyObject *json_parse_value(const char *s, int64_t n, int64_t *pos);

static PyObject *json_parse_string(const char *s, int64_t n, int64_t *pos) {
    if (*pos >= n || s[*pos] != '"') return NULL;
    (*pos)++;
    JsonBuf b;
    b.data = NULL;
    b.len = 0;
    b.cap = 0;
    while (*pos < n) {
        char c = s[*pos];
        if (c == '"') {
            PyObject *out = py_str_new(b.data != NULL ? b.data : "", b.len);
            free(b.data);
            (*pos)++;
            return out;
        }
        if (c == '\\') {
            (*pos)++;
            if (*pos >= n) goto fail;
            char esc = s[*pos];
            (*pos)++;
            switch (esc) {
                case '"':
                    if (json_buf_append_char(&b, '"') != 0) goto fail;
                    break;
                case '\\':
                    if (json_buf_append_char(&b, '\\') != 0) goto fail;
                    break;
                case '/':
                    if (json_buf_append_char(&b, '/') != 0) goto fail;
                    break;
                case 'b':
                    if (json_buf_append_char(&b, '\b') != 0) goto fail;
                    break;
                case 'f':
                    if (json_buf_append_char(&b, '\f') != 0) goto fail;
                    break;
                case 'n':
                    if (json_buf_append_char(&b, '\n') != 0) goto fail;
                    break;
                case 'r':
                    if (json_buf_append_char(&b, '\r') != 0) goto fail;
                    break;
                case 't':
                    if (json_buf_append_char(&b, '\t') != 0) goto fail;
                    break;
                case 'u': {
                    uint32_t codepoint = 0;
                    if (json_parse_hex4(s, n, pos, &codepoint) != 0) goto fail;
                    if (codepoint >= 0xd800 && codepoint <= 0xdbff) {
                        if (*pos + 6 > n || s[*pos] != '\\' || s[*pos + 1] != 'u') {
                            goto fail;
                        }
                        *pos += 2;
                        uint32_t low = 0;
                        if (json_parse_hex4(s, n, pos, &low) != 0) goto fail;
                        if (low < 0xdc00 || low > 0xdfff) goto fail;
                        codepoint = (
                            0x10000
                            + ((codepoint - 0xd800) << 10)
                            + (low - 0xdc00)
                        );
                    } else if (codepoint >= 0xdc00 && codepoint <= 0xdfff) {
                        goto fail;
                    }
                    if (json_buf_append_utf8(&b, codepoint) != 0) goto fail;
                    break;
                }
                default:
                    goto fail;
            }
            continue;
        }
        if ((unsigned char)c < 0x20) goto fail;
        if (json_buf_append_char(&b, c) != 0) goto fail;
        (*pos)++;
    }
fail:
    free(b.data);
    return NULL;
}

static PyObject *json_parse_number(const char *s, int64_t n, int64_t *pos) {
    int64_t start = *pos;
    int64_t sign = 1;
    if (*pos < n && s[*pos] == '-') {
        sign = -1;
        (*pos)++;
    }
    if (*pos >= n || s[*pos] < '0' || s[*pos] > '9') return NULL;
    int64_t value = 0;
    while (*pos < n && s[*pos] >= '0' && s[*pos] <= '9') {
        value = value * 10 + (int64_t)(s[*pos] - '0');
        (*pos)++;
    }
    int is_float = 0;
    if (*pos < n && s[*pos] == '.') {
        is_float = 1;
        (*pos)++;
        if (*pos >= n || s[*pos] < '0' || s[*pos] > '9') return NULL;
        while (*pos < n && s[*pos] >= '0' && s[*pos] <= '9') {
            (*pos)++;
        }
    }
    if (*pos < n && (s[*pos] == 'e' || s[*pos] == 'E')) {
        is_float = 1;
        (*pos)++;
        if (*pos < n && (s[*pos] == '+' || s[*pos] == '-')) {
            (*pos)++;
        }
        if (*pos >= n || s[*pos] < '0' || s[*pos] > '9') return NULL;
        while (*pos < n && s[*pos] >= '0' && s[*pos] <= '9') {
            (*pos)++;
        }
    }
    if (is_float) {
        int64_t len = *pos - start;
        char *tmp = (char *)malloc((size_t)len + 1);
        if (tmp == NULL) return NULL;
        memcpy(tmp, s + start, (size_t)len);
        tmp[len] = '\0';
        double f = strtod(tmp, NULL);
        free(tmp);
        return py_float_from_f64(f);
    }
    return py_int_from_i64(sign * value);
}

static PyObject *json_parse_array(const char *s, int64_t n, int64_t *pos) {
    if (*pos >= n || s[*pos] != '[') return NULL;
    (*pos)++;
    PyObject *out = py_list_new(4);
    if (out == NULL) return NULL;
    for (;;) {
        json_skip_ws(s, n, pos);
        if (*pos < n && s[*pos] == ']') {
            (*pos)++;
            return out;
        }
        PyObject *val = json_parse_value(s, n, pos);
        if (val == NULL) {
            py_decref(out);
            return NULL;
        }
        py_list_append(out, val);
        py_decref(val);
        json_skip_ws(s, n, pos);
        if (*pos < n && s[*pos] == ',') {
            (*pos)++;
            continue;
        }
        if (*pos < n && s[*pos] == ']') {
            (*pos)++;
            return out;
        }
        py_decref(out);
        return NULL;
    }
}

static PyObject *json_parse_object(const char *s, int64_t n, int64_t *pos) {
    if (*pos >= n || s[*pos] != '{') return NULL;
    (*pos)++;
    PyObject *out = py_dict_new();
    if (out == NULL) return NULL;
    for (;;) {
        json_skip_ws(s, n, pos);
        if (*pos < n && s[*pos] == '}') {
            (*pos)++;
            return out;
        }
        PyObject *key = json_parse_string(s, n, pos);
        if (key == NULL) {
            py_decref(out);
            return NULL;
        }
        json_skip_ws(s, n, pos);
        if (*pos >= n || s[*pos] != ':') {
            py_decref(key);
            py_decref(out);
            return NULL;
        }
        (*pos)++;
        json_skip_ws(s, n, pos);
        PyObject *value = json_parse_value(s, n, pos);
        if (value == NULL) {
            py_decref(key);
            py_decref(out);
            return NULL;
        }
        py_dict_set(out, key, value);
        py_decref(key);
        py_decref(value);
        json_skip_ws(s, n, pos);
        if (*pos < n && s[*pos] == ',') {
            (*pos)++;
            continue;
        }
        if (*pos < n && s[*pos] == '}') {
            (*pos)++;
            return out;
        }
        py_decref(out);
        return NULL;
    }
}

static PyObject *json_parse_value(const char *s, int64_t n, int64_t *pos) {
    json_skip_ws(s, n, pos);
    if (*pos >= n) return NULL;
    char c = s[*pos];
    if (c == '"') return json_parse_string(s, n, pos);
    if (c == '{') return json_parse_object(s, n, pos);
    if (c == '[') return json_parse_array(s, n, pos);
    if (c == 't' && *pos + 4 <= n && memcmp(s + *pos, "true", 4) == 0) {
        (*pos) += 4;
        return pcc_gc_retain(py_True);
    }
    if (c == 'f' && *pos + 5 <= n && memcmp(s + *pos, "false", 5) == 0) {
        (*pos) += 5;
        return pcc_gc_retain(py_False);
    }
    if (c == 'n' && *pos + 4 <= n && memcmp(s + *pos, "null", 4) == 0) {
        (*pos) += 4;
        return pcc_gc_retain(py_None);
    }
    if (c == 'N' && *pos + 3 <= n && memcmp(s + *pos, "NaN", 3) == 0) {
        (*pos) += 3;
        return py_float_from_f64(NAN);
    }
    if (c == 'I' && *pos + 8 <= n && memcmp(s + *pos, "Infinity", 8) == 0) {
        (*pos) += 8;
        return py_float_from_f64(INFINITY);
    }
    if (
        c == '-'
        && *pos + 9 <= n
        && memcmp(s + *pos, "-Infinity", 9) == 0
    ) {
        (*pos) += 9;
        return py_float_from_f64(-INFINITY);
    }
    return json_parse_number(s, n, pos);
}

PyObject *py_json_loads(PyObject *text) {
    if (text == NULL || py_type_of(text) != PY_TYPE_STR) return NULL;
    PyStrObject *input = (PyStrObject *)text;
    const char *s = input->data;
    int64_t n = input->byte_len;
    int64_t pos = 0;
    PyObject *res = json_parse_value(s, n, &pos);
    if (res == NULL) return NULL;
    json_skip_ws(s, n, &pos);
    /* If there's trailing garbage, we should ideally raise but here we just
     * return what we got. */
    return res;
}

/* Byte-wise comparison of two string keys. UTF-8 preserves code-point order,
 * so an unsigned byte-wise memcmp of the encoded bytes matches CPython's
 * default (code-point) ordering used by json.dumps(sort_keys=True). Returns
 * <0, 0, >0 like memcmp. Non-str keys sort as empty (json only emits str
 * keys via json_buf_append_quoted_str, which rejects non-str keys anyway). */
static int json_str_key_cmp(PyObject *a, PyObject *b) {
    const char *ad = "";
    int64_t alen = 0;
    const char *bd = "";
    int64_t blen = 0;
    if (a != NULL && py_type_of(a) == PY_TYPE_STR) {
        PyStrObject *sa = (PyStrObject *)a;
        ad = sa->data;
        alen = sa->byte_len;
    }
    if (b != NULL && py_type_of(b) == PY_TYPE_STR) {
        PyStrObject *sb = (PyStrObject *)b;
        bd = sb->data;
        blen = sb->byte_len;
    }
    int64_t n = alen < blen ? alen : blen;
    for (int64_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char)ad[i];
        unsigned char cb = (unsigned char)bd[i];
        if (ca != cb) return (int)ca - (int)cb;
    }
    if (alen < blen) return -1;
    if (alen > blen) return 1;
    return 0;
}

static int json_dump_value(JsonBuf *b, PyObject *obj, int sort_keys) {
    if (obj == NULL || obj == py_None) return json_buf_append_cstr(b, "null");
    int32_t tag = py_type_of(obj);
    if (tag == PY_TYPE_INT) {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "%lld", (long long)py_int_value_i64(obj));
        return json_buf_append_cstr(b, tmp);
    }
    if (tag == PY_TYPE_FLOAT) {
        double value = py_float_to_f64(obj);
        if (isnan(value)) return json_buf_append_cstr(b, "NaN");
        if (isinf(value)) {
            return json_buf_append_cstr(b, value < 0.0 ? "-Infinity" : "Infinity");
        }
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "%.17g", value);
        return json_buf_append_cstr(b, tmp);
    }
    if (tag == PY_TYPE_BOOL) {
        return json_buf_append_cstr(b, obj == py_True ? "true" : "false");
    }
    if (tag == PY_TYPE_STR) {
        return json_buf_append_quoted_str(b, obj);
    }
    if (tag == PY_TYPE_LIST) {
        PyListObject *l = (PyListObject *)obj;
        if (json_buf_append_char(b, '[') != 0) return -1;
        for (int64_t i = 0; i < l->length; i++) {
            if (i > 0 && json_buf_append_cstr(b, ", ") != 0) return -1;
            if (json_dump_value(b, json_list_item(obj, l, i), sort_keys) != 0) {
                return -1;
            }
        }
        return json_buf_append_char(b, ']');
    }
    if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)obj;
        if (json_buf_append_char(b, '{') != 0) return -1;
        if (sort_keys) {
            /* Collect the live entries into a pointer array, insertion-sort
             * them by key bytes, then emit in sorted order. Insertion sort is
             * stable; dicts hold no duplicate keys so ties do not arise, and
             * dicts encoded by json are typically small. The pointer array is
             * freed on every exit path below. */
            int64_t count = 0;
            for (int64_t i = 0; i < d->entries_used; i++) {
                if (d->entries[i].key != NULL) count++;
            }
            if (count > 0) {
                DictEntry **order =
                    (DictEntry **)malloc((size_t)count * sizeof(DictEntry *));
                if (order == NULL) return -1;
                int64_t k = 0;
                for (int64_t i = 0; i < d->entries_used; i++) {
                    if (d->entries[i].key != NULL) order[k++] = &d->entries[i];
                }
                for (int64_t i = 1; i < count; i++) {
                    DictEntry *cur = order[i];
                    PyObject *cur_key = json_dict_key(d, cur);
                    int64_t j = i - 1;
                    while (j >= 0
                           && json_str_key_cmp(json_dict_key(d, order[j]),
                                               cur_key) > 0) {
                        order[j + 1] = order[j];
                        j--;
                    }
                    order[j + 1] = cur;
                }
                for (int64_t i = 0; i < count; i++) {
                    DictEntry *e = order[i];
                    PyObject *key = json_dict_key(d, e);
                    PyObject *value = json_dict_value(d, e);
                    if (i > 0 && json_buf_append_cstr(b, ", ") != 0) {
                        free(order);
                        return -1;
                    }
                    if (json_buf_append_quoted_str(b, key) != 0) {
                        free(order);
                        return -1;
                    }
                    if (json_buf_append_cstr(b, ": ") != 0) {
                        free(order);
                        return -1;
                    }
                    if (json_dump_value(b, value, sort_keys) != 0) {
                        free(order);
                        return -1;
                    }
                }
                free(order);
            }
            return json_buf_append_char(b, '}');
        }
        int first = 1;
        for (int64_t i = 0; i < d->entries_used; i++) {
            DictEntry *e = &d->entries[i];
            PyObject *key = json_dict_key(d, e);
            if (key == NULL) continue;
            PyObject *value = json_dict_value(d, e);
            if (!first && json_buf_append_cstr(b, ", ") != 0) return -1;
            first = 0;
            if (json_buf_append_quoted_str(b, key) != 0) return -1;
            if (json_buf_append_cstr(b, ": ") != 0) return -1;
            if (json_dump_value(b, value, sort_keys) != 0) return -1;
        }
        return json_buf_append_char(b, '}');
    }
    return json_buf_append_cstr(b, "null");
}

PyObject *py_json_dumps_ex(PyObject *obj, int64_t sort_keys) {
    JsonBuf b;
    b.data = NULL;
    b.len = 0;
    b.cap = 0;
    if (json_dump_value(&b, obj, sort_keys != 0 ? 1 : 0) != 0) {
        free(b.data);
        return NULL;
    }
    PyObject *out = py_str_new(b.data, b.len);
    free(b.data);
    return out;
}

PyObject *py_json_dumps(PyObject *obj) {
    return py_json_dumps_ex(obj, 0);
}
