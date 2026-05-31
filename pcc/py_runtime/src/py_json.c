/* pcc/py_runtime/src/py_json.c
 *
 * Minimal native JSON helpers for builtin json.loads/json.dumps dispatch.
 */

#include "py_internal.h"
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
    int64_t start = *pos;
    while (*pos < n && s[*pos] != '"') {
        if (s[*pos] == '\\') {
            /* TODO: handle escapes properly if needed. For now just skip. */
            (*pos)++;
        }
        (*pos)++;
    }
    if (*pos >= n) return NULL;
    PyObject *out = py_str_new(s + start, *pos - start);
    (*pos)++;
    return out;
}

static PyObject *json_parse_int(const char *s, int64_t n, int64_t *pos) {
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
    return json_parse_int(s, n, pos);
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

static int json_dump_value(JsonBuf *b, PyObject *obj) {
    if (obj == NULL || obj == py_None) return json_buf_append_cstr(b, "null");
    int32_t tag = py_type_of(obj);
    if (tag == PY_TYPE_INT) {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "%lld", (long long)py_int_value_i64(obj));
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
            if (json_dump_value(b, json_list_item(obj, l, i)) != 0) return -1;
        }
        return json_buf_append_char(b, ']');
    }
    if (tag == PY_TYPE_DICT) {
        PyDictObject *d = (PyDictObject *)obj;
        if (json_buf_append_char(b, '{') != 0) return -1;
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
            if (json_dump_value(b, value) != 0) return -1;
        }
        return json_buf_append_char(b, '}');
    }
    return json_buf_append_cstr(b, "null");
}

PyObject *py_json_dumps(PyObject *obj) {
    JsonBuf b;
    b.data = NULL;
    b.len = 0;
    b.cap = 0;
    if (json_dump_value(&b, obj) != 0) {
        free(b.data);
        return NULL;
    }
    PyObject *out = py_str_new(b.data, b.len);
    free(b.data);
    return out;
}
