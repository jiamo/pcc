/* pcc/py_runtime/src/py_re.c
 *
 * Tiny no-libpython regex helper for the Python frontend's native
 * ``re.match`` lowering. This is intentionally a subset, not a full
 * ``re.Match`` implementation: a successful match returns a truthy
 * sentinel object and a failed match returns None. The parser supports
 * the common atoms needed by bootstrap probes: literals, '.', anchors,
 * '*', '+', '?', the ASCII classes \d, \w, \s plus their uppercase
 * negations, and the re.I / re.S flags.
 */

#include "py_internal.h"

#include <ctype.h>
#include <string.h>

typedef struct {
    const char *p;
    int len;
    int kind;
    char literal;
} ReAtom;

enum {
    RE_ATOM_LITERAL = 0,
    RE_ATOM_DOT = 1,
    RE_ATOM_DIGIT = 2,
    RE_ATOM_NOT_DIGIT = 3,
    RE_ATOM_WORD = 4,
    RE_ATOM_NOT_WORD = 5,
    RE_ATOM_SPACE = 6,
    RE_ATOM_NOT_SPACE = 7,
};

static int re_is_word(unsigned char c) {
    return isalnum((int)c) || c == '_';
}

static ReAtom re_parse_atom(const char *p) {
    ReAtom a;
    a.p = p;
    a.len = 1;
    a.kind = RE_ATOM_LITERAL;
    a.literal = p[0];
    if (p[0] == '.') {
        a.kind = RE_ATOM_DOT;
        return a;
    }
    if (p[0] == '\\' && p[1] != '\0') {
        a.len = 2;
        a.literal = p[1];
        if (p[1] == 'd') a.kind = RE_ATOM_DIGIT;
        else if (p[1] == 'D') a.kind = RE_ATOM_NOT_DIGIT;
        else if (p[1] == 'w') a.kind = RE_ATOM_WORD;
        else if (p[1] == 'W') a.kind = RE_ATOM_NOT_WORD;
        else if (p[1] == 's') a.kind = RE_ATOM_SPACE;
        else if (p[1] == 'S') a.kind = RE_ATOM_NOT_SPACE;
        return a;
    }
    return a;
}

static int re_lower_ascii(int c) {
    if (c >= 'A' && c <= 'Z') return c + ('a' - 'A');
    return c;
}

static int re_literal_eq(unsigned char a, unsigned char b, int ignore_case) {
    if (!ignore_case) return a == b;
    return re_lower_ascii((int)a) == re_lower_ascii((int)b);
}

static int re_atom_matches(ReAtom a, const char *t, int ignore_case, int dot_all) {
    unsigned char c;
    if (t == NULL || t[0] == '\0') return 0;
    c = (unsigned char)t[0];
    switch (a.kind) {
        case RE_ATOM_DOT:
            return dot_all || c != '\n';
        case RE_ATOM_DIGIT:
            return isdigit((int)c) != 0;
        case RE_ATOM_NOT_DIGIT:
            return isdigit((int)c) == 0;
        case RE_ATOM_WORD:
            return re_is_word(c);
        case RE_ATOM_NOT_WORD:
            return !re_is_word(c);
        case RE_ATOM_SPACE:
            return isspace((int)c) != 0;
        case RE_ATOM_NOT_SPACE:
            return isspace((int)c) == 0;
        default:
            return re_literal_eq(c, (unsigned char)a.literal, ignore_case);
    }
}

static int re_match_here_flags(const char *p, const char *t, int ignore_case, int dot_all);

static int re_match_star(ReAtom a, const char *rest, const char *t, int ignore_case, int dot_all) {
    const char *end = t;
    while (re_atom_matches(a, end, ignore_case, dot_all)) {
        end++;
    }
    for (;;) {
        if (re_match_here_flags(rest, end, ignore_case, dot_all)) return 1;
        if (end == t) break;
        end--;
    }
    return 0;
}

static int re_match_plus(ReAtom a, const char *rest, const char *t, int ignore_case, int dot_all) {
    if (!re_atom_matches(a, t, ignore_case, dot_all)) return 0;
    return re_match_star(a, rest, t + 1, ignore_case, dot_all);
}

static int re_match_here_flags(const char *p, const char *t, int ignore_case, int dot_all) {
    ReAtom atom;
    const char *rest;
    char q;
    if (p == NULL || t == NULL) return 0;
    if (p[0] == '\0') return 1;
    if (p[0] == '$' && p[1] == '\0') return t[0] == '\0';
    atom = re_parse_atom(p);
    rest = p + atom.len;
    q = rest[0];
    if (q == '*') return re_match_star(atom, rest + 1, t, ignore_case, dot_all);
    if (q == '+') return re_match_plus(atom, rest + 1, t, ignore_case, dot_all);
    if (q == '?') {
        if (
            re_atom_matches(atom, t, ignore_case, dot_all) &&
            re_match_here_flags(rest + 1, t + 1, ignore_case, dot_all)
        ) {
            return 1;
        }
        return re_match_here_flags(rest + 1, t, ignore_case, dot_all);
    }
    if (re_atom_matches(atom, t, ignore_case, dot_all)) {
        return re_match_here_flags(rest, t + 1, ignore_case, dot_all);
    }
    return 0;
}

/* E1a/E4: faithful-engine bridge (py_re_engine_obj.c) */
PyObject *py_re_engine_truth_flags(PyObject *pattern, PyObject *text,
                                   int64_t flags, int64_t search);
PyObject *py_re_engine_truth_flags_from(PyObject *pattern, PyObject *text,
                                        int64_t flags, int64_t search,
                                        int64_t start, int64_t endpos);
PyObject *py_re_engine_fullmatch_flags(PyObject *pattern, PyObject *text,
                                       int64_t flags);
#define PCC_RE_OK_FLAGS (2 | 8 | 16) /* re.I | re.M | re.S */

static PyObject *py_re_match_impl(PyObject *pattern, PyObject *text, int64_t flags, int search) {
    const char *p;
    const char *t;
    int ignore_case;
    int dot_all;
    if (pattern == NULL || text == NULL) return py_None;
    if (py_type_of(pattern) != PY_TYPE_STR || py_type_of(text) != PY_TYPE_STR) {
        return py_None;
    }
    if ((flags & ~(int64_t)PCC_RE_OK_FLAGS) == 0) {
        /* Subset patterns (incl. re.I/M/S since E4) run on the faithful
         * engine; outside-subset patterns raise (NULL) instead of silently
         * mismatching the way the legacy literal matcher below would. */
        return py_re_engine_truth_flags(pattern, text, flags, search);
    }
    py_raise(py_exc_new(
        PY_EXC_NOTIMPLEMENTEDERROR,
        "pcc re: flags outside the native regex subset (no-libpython)"
    ));
    return NULL;
    /* legacy literal matcher below is retained only for reference; the
     * mask routing above never falls through to it for match/search. */
    if (0) {
    p = py_str_utf8(pattern);
    t = py_str_utf8(text);
    if (p == NULL || t == NULL) return py_None;
    ignore_case = (flags & 2) != 0;
    dot_all = (flags & 16) != 0;
    if (p[0] == '^') {
        return re_match_here_flags(p + 1, t, ignore_case, dot_all) ? py_True : py_None;
    }
    if (!search) {
        return re_match_here_flags(p, t, ignore_case, dot_all) ? py_True : py_None;
    }
    for (;;) {
        if (re_match_here_flags(p, t, ignore_case, dot_all)) return py_True;
        if (t[0] == '\0') break;
        t++;
    }
    return py_None;
    }
}

PyObject *py_re_match(PyObject *pattern, PyObject *text) {
    return py_re_match_impl(pattern, text, 0, 0);
}

PyObject *py_re_match_flags(PyObject *pattern, PyObject *text, int64_t flags) {
    return py_re_match_impl(pattern, text, flags, 0);
}

PyObject *py_re_fullmatch(PyObject *pattern, PyObject *text) {
    return py_re_fullmatch_flags(pattern, text, 0);
}

PyObject *py_re_fullmatch_flags(PyObject *pattern, PyObject *text, int64_t flags) {
    if (pattern == NULL || text == NULL) return py_None;
    if (py_type_of(pattern) != PY_TYPE_STR || py_type_of(text) != PY_TYPE_STR) {
        return py_None;
    }
    if ((flags & ~(int64_t)PCC_RE_OK_FLAGS) == 0) {
        return py_re_engine_fullmatch_flags(pattern, text, flags);
    }
    py_raise(py_exc_new(
        PY_EXC_NOTIMPLEMENTEDERROR,
        "pcc re: flags outside the native regex subset (no-libpython)"
    ));
    return NULL;
}

PyObject *py_re_search(PyObject *pattern, PyObject *text) {
    return py_re_match_impl(pattern, text, 0, 1);
}

PyObject *py_re_search_flags(PyObject *pattern, PyObject *text, int64_t flags) {
    return py_re_match_impl(pattern, text, flags, 1);
}

static int re_cstr_eq(const char *a, const char *b) {
    if (a == NULL || b == NULL) return 0;
    return strcmp(a, b) == 0;
}

static int re_word_body(unsigned char c) {
    return isalnum((int)c) != 0 || c == '_' || c == '$';
}

static void re_findall_append_slice(PyObject *out, PyObject *text, int64_t lo, int64_t hi) {
    PyObject *part = py_str_byte_slice_i64(text, lo, hi);
    if (part == NULL) return;
    py_list_append(out, part);
    py_decref(part);
}

static PyObject *re_findall_ident_words(PyObject *text) {
    const char *t = py_str_utf8(text);
    PyObject *out;
    int64_t i = 0;
    if (t == NULL) return py_list_new(0);
    out = py_list_new(0);
    if (out == NULL) return NULL;
    while (t[i] != '\0') {
        unsigned char c = (unsigned char)t[i];
        int prev_word = i > 0 && re_is_word((unsigned char)t[i - 1]);
        if (!prev_word && isalpha((int)c)) {
            int64_t start = i;
            int64_t end;
            i++;
            while (t[i] != '\0' && re_word_body((unsigned char)t[i])) i++;
            end = i;
            while (end > start && t[end - 1] == '$') end--;
            re_findall_append_slice(out, text, start, end);
            continue;
        }
        i++;
    }
    return out;
}

static PyObject *re_findall_parenthesized(PyObject *text) {
    const char *t = py_str_utf8(text);
    PyObject *out;
    int64_t i = 0;
    if (t == NULL) return py_list_new(0);
    out = py_list_new(0);
    if (out == NULL) return NULL;
    while (t[i] != '\0') {
        if (t[i] == '(') {
            int64_t start = i;
            i++;
            while (t[i] != '\0' && t[i] != ')') i++;
            if (t[i] == ')') {
                re_findall_append_slice(out, text, start, i + 1);
                i++;
                continue;
            }
            break;
        }
        i++;
    }
    return out;
}

/* E3/E4: faithful-engine findall (py_re_engine_obj.c) */
PyObject *py_re_engine_findall(PyObject *pattern, PyObject *text,
                               int64_t flags);

PyObject *py_re_findall_flags(PyObject *pattern, PyObject *text, int64_t flags) {
    const char *p;
    (void)flags;
    if (pattern == NULL || text == NULL) return py_list_new(0);
    if (py_type_of(pattern) != PY_TYPE_STR || py_type_of(text) != PY_TYPE_STR) {
        return py_list_new(0);
    }
    if ((flags & ~(int64_t)PCC_RE_OK_FLAGS) == 0) {
        return py_re_engine_findall(pattern, text, flags);
    }
    py_raise(py_exc_new(
        PY_EXC_NOTIMPLEMENTEDERROR,
        "pcc re: flags outside the native regex subset (no-libpython)"
    ));
    return NULL;
    p = py_str_utf8(pattern);
    if (re_cstr_eq(p, "\\b[a-z][\\w$]*\\b")) {
        return re_findall_ident_words(text);
    }
    if (re_cstr_eq(p, "\\(.*?\\)")) {
        return re_findall_parenthesized(text);
    }
    return py_list_new(0);
}

static PyObject *py_re_bound_method_call(PyObject *captures, PyObject *args) {
    PyObject *pattern;
    PyObject *flags_obj;
    PyObject *method_obj;
    PyObject *text;
    PyObject *result;
    int overflow = 0;
    int64_t flags;
    int64_t method_kind;
    int64_t start = 0;
    int64_t endpos = -1;
    PyObject *start_obj = NULL;
    PyObject *endpos_obj = NULL;
    if (captures == NULL || args == NULL) return py_None;
    if (py_tuple_len(captures) < 3 || py_tuple_len(args) < 1) return py_None;
    pattern = py_tuple_get(captures, 0);
    flags_obj = py_tuple_get(captures, 1);
    method_obj = py_tuple_get(captures, 2);
    text = py_tuple_get(args, 0);
    if (pattern == NULL || flags_obj == NULL || method_obj == NULL || text == NULL) {
        if (pattern != NULL) py_decref(pattern);
        if (flags_obj != NULL) py_decref(flags_obj);
        if (method_obj != NULL) py_decref(method_obj);
        if (text != NULL) py_decref(text);
        return py_None;
    }
    flags = py_int_to_i64(flags_obj, &overflow);
    if (overflow) flags = 0;
    overflow = 0;
    method_kind = py_int_to_i64(method_obj, &overflow);
    if (overflow) method_kind = 0;
    if (py_tuple_len(args) >= 2) {
        start_obj = py_tuple_get(args, 1);
        if (start_obj != NULL) {
            overflow = 0;
            start = py_int_to_i64(start_obj, &overflow);
            if (overflow) start = 0;
        }
    }
    if (py_tuple_len(args) >= 3) {
        endpos_obj = py_tuple_get(args, 2);
        if (endpos_obj != NULL) {
            overflow = 0;
            endpos = py_int_to_i64(endpos_obj, &overflow);
            if (overflow) endpos = -1;
        }
    }
    if (method_kind == 2) {
        result = py_re_findall_flags(pattern, text, flags);
    } else {
        result = py_re_engine_truth_flags_from(
            pattern, text, flags, method_kind == 1, start, endpos
        );
    }
    py_decref(pattern);
    py_decref(flags_obj);
    py_decref(method_obj);
    py_decref(text);
    if (start_obj != NULL) py_decref(start_obj);
    if (endpos_obj != NULL) py_decref(endpos_obj);
    return result;
}

PyObject *py_re_compile_method(PyObject *pattern, int64_t flags, int64_t method_kind) {
    PyObject *captures = py_tuple_new(3);
    PyObject *flags_obj;
    PyObject *method_obj;
    PyObject *fn;
    if (captures == NULL) return NULL;
    flags_obj = py_int_from_i64(flags);
    method_obj = py_int_from_i64(method_kind);
    if (flags_obj == NULL || method_obj == NULL) {
        if (flags_obj != NULL) py_decref(flags_obj);
        if (method_obj != NULL) py_decref(method_obj);
        py_decref(captures);
        return NULL;
    }
    py_tuple_set_item(captures, 0, pattern);
    py_tuple_set_item(captures, 1, flags_obj);
    py_tuple_set_item(captures, 2, method_obj);
    py_decref(flags_obj);
    py_decref(method_obj);
    fn = py_func_new_named(
        (void *)py_re_bound_method_call,
        captures,
        method_kind == 2 ? "re.Pattern.findall" :
        (method_kind == 1 ? "re.Pattern.search" : "re.Pattern.match")
    );
    py_decref(captures);
    return fn;
}
