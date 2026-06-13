/*
 * py_re_engine.c — E0 faithful regex engine subset (steps 1+2).
 *
 * Standalone, pure-C, byte/ASCII regex engine intended to replace the toy
 * matcher in py_re.c as the backend for native re support. NOT wired into
 * the runtime build yet (not listed in the Makefile); exercised directly by
 * tests/python/test_re_engine_differential.py via a cc -dynamiclib build.
 *
 * Subset: literals, '.', character classes [...] with ranges / negation /
 * class escapes, perl escapes \d \D \w \W \s \S, word boundaries \b \B,
 * anchors ^ $, quantifiers * + ? with lazy variants, counted repeats
 * {m} {m,} {m,n} {,n} (greedy + lazy, bounded), alternation |, capturing
 * ( ) and non-capturing (?: ) groups, and CPython's empty-iteration rule
 * for quantified can-match-empty bodies such as (a?)* (one trailing empty
 * iteration participates, then the loop stops).
 *
 * Strict-parser contract: anything outside the subset returns
 * PCC_RE_UNSUPPORTED — the engine must never guess. Deliberately rejected:
 * backreferences, named groups, lookaround, inline flags (?i...),
 * \A \Z \z, octal/unicode escapes, double quantifiers, counted repeats over
 * pure assertions (^{2}), and counts above RE_MAX_COUNT. A malformed
 * brace such as a{x} or a{2 is treated as a literal '{', matching CPython.
 *
 * Faithfulness boundary: byte semantics equal CPython str semantics only
 * for ASCII text, so any text byte >= 0x80 is declined with
 * PCC_RE_NONASCII rather than risking a silent divergence ('.', classes,
 * and \w operate per-codepoint in CPython).
 */

#include <stdint.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#define PCC_RE_MATCH 1
#define PCC_RE_NOMATCH 0
#define PCC_RE_UNSUPPORTED (-1)
#define PCC_RE_LIMIT (-2)
#define PCC_RE_BADARGS (-3)
#define PCC_RE_NONASCII (-4)

#define RE_MAX_OPS 4096
#define RE_MAX_GROUPS 32
#define RE_MAX_GUARDS 16
#define RE_MAX_DEPTH 8192
#define RE_MAX_COUNT 64
#define RE_MAX_NAME 32

#define RE_FLAG_I 2
#define RE_FLAG_M 8
#define RE_FLAG_S 16
#define PCC_RE_OK_FLAGS (RE_FLAG_I | RE_FLAG_M | RE_FLAG_S)

enum {
    OP_CHAR = 1,   /* a = byte */
    OP_ANY,        /* '.' (not newline) */
    OP_CLASS,      /* cls bitmap */
    OP_BOL,
    OP_EOL,
    OP_WB,
    OP_NWB,
    OP_SPLIT,      /* try a (preferred) then b */
    OP_JMP,        /* goto a */
    OP_SAVE,       /* a = capture slot, then next */
    OP_MATCH,
    OP_STAR1,      /* iterative star over single-byte atom; lazy flag */
    OP_PLUS1,
    OP_QUES1,
    OP_GENTER,     /* a = guard slot: record pos for the empty-iteration rule */
    OP_GCHECK,     /* a = guard slot: advanced -> b (loop), empty -> next (stop) */
    OP_BOS,        /* \A: absolute start of text (ignores re.M) */
    OP_EOS,        /* \Z: absolute end of text (no trailing-newline rule) */
};

typedef struct {
    unsigned char op;
    unsigned char lazy;
    unsigned char atom_op;            /* for STAR1/PLUS1/QUES1 */
    unsigned char atom_c;
    unsigned char cls[32];
    int32_t a;
    int32_t b;
    int32_t next;
} ReOp;

typedef struct {
    ReOp ops[RE_MAX_OPS];
    int32_t nops;
    int32_t ngroups;                  /* capturing groups, excluding group 0 */
    int32_t nguards;
    int64_t flags;
    char group_names[RE_MAX_GROUPS][RE_MAX_NAME];
} ReProg;

/*
 * Module-level ``re.compile`` patterns are long-lived and are commonly
 * matched once per parsed line.  Rebuilding the fixed-size ReProg for every
 * match made native callers spend most of their time in the parser.  Keep a
 * bounded, process-wide cache of immutable compiled programs instead.
 *
 * Entries are append-only: once published they are never replaced, so a
 * matcher may safely retain the returned pointer after releasing the cache
 * lock.  When the cache is full (or allocation fails), callers use their
 * stack scratch program and preserve the old behavior.  The spin lock only
 * covers first-use lookup/compilation; matching never holds it.
 */
#define RE_CACHE_CAPACITY 64

typedef struct {
    char *pattern;
    int64_t flags;
    int start_pc;
    ReProg *prog;
} ReCacheEntry;

static ReCacheEntry re_cache[RE_CACHE_CAPACITY];
static int re_cache_count = 0;
static atomic_flag re_cache_lock = ATOMIC_FLAG_INIT;
static _Atomic(int64_t) re_compile_count = 0;

static void re_cache_acquire(void) {
    while (atomic_flag_test_and_set_explicit(&re_cache_lock,
                                              memory_order_acquire)) {
    }
}

static void re_cache_release(void) {
    atomic_flag_clear_explicit(&re_cache_lock, memory_order_release);
}

/* ---------------- parser ---------------- */

typedef struct {
    const char *p;
    ReProg *prog;
    int64_t flags;
    int err;                          /* 0 ok, PCC_RE_UNSUPPORTED on reject */
} ReParser;

static int re_emit(ReParser *ps, unsigned char op) {
    ReProg *pg = ps->prog;
    if (pg->nops >= RE_MAX_OPS) {
        ps->err = PCC_RE_UNSUPPORTED;
        return 0;
    }
    memset(&pg->ops[pg->nops], 0, sizeof(ReOp));
    pg->ops[pg->nops].op = op;
    pg->ops[pg->nops].a = -1;
    pg->ops[pg->nops].b = -1;
    pg->ops[pg->nops].next = -1;
    return pg->nops++;
}

static void re_cls_set(unsigned char *cls, unsigned char c) {
    cls[c >> 3] |= (unsigned char)(1u << (c & 7));
}

static int re_cls_has(const unsigned char *cls, unsigned char c) {
    return (cls[c >> 3] >> (c & 7)) & 1u;
}

static void re_cls_perl(unsigned char *cls, char kind) {
    int c;
    switch (kind) {
    case 'd':
        for (c = '0'; c <= '9'; c++) re_cls_set(cls, (unsigned char)c);
        break;
    case 'w':
        for (c = '0'; c <= '9'; c++) re_cls_set(cls, (unsigned char)c);
        for (c = 'a'; c <= 'z'; c++) re_cls_set(cls, (unsigned char)c);
        for (c = 'A'; c <= 'Z'; c++) re_cls_set(cls, (unsigned char)c);
        re_cls_set(cls, '_');
        break;
    case 's':
        re_cls_set(cls, ' ');
        re_cls_set(cls, '\t');
        re_cls_set(cls, '\n');
        re_cls_set(cls, '\r');
        re_cls_set(cls, '\f');
        re_cls_set(cls, '\v');
        break;
    default:
        break;
    }
}

static void re_cls_fold_case(unsigned char *cls) {
    int c;
    for (c = 'a'; c <= 'z'; c++) {
        if (re_cls_has(cls, (unsigned char)c)) re_cls_set(cls, (unsigned char)(c - 32));
    }
    for (c = 'A'; c <= 'Z'; c++) {
        if (re_cls_has(cls, (unsigned char)c)) re_cls_set(cls, (unsigned char)(c + 32));
    }
}

static void re_cls_negate(unsigned char *cls) {
    int i;
    for (i = 0; i < 32; i++) cls[i] = (unsigned char)~cls[i];
}

/* literal escapes the subset accepts outside/inside classes */
static int re_literal_escape(char e, unsigned char *out) {
    switch (e) {
    case 'n': *out = '\n'; return 1;
    case 't': *out = '\t'; return 1;
    case 'r': *out = '\r'; return 1;
    case 'f': *out = '\f'; return 1;
    case 'v': *out = '\v'; return 1;
    case '\\': case '.': case '*': case '+': case '?': case '(': case ')':
    case '[': case ']': case '{': case '}': case '|': case '^': case '$':
    case '-': case '/': case '\'': case '"': case ' ': case ',': case ':':
    case ';': case '=': case '<': case '>': case '#': case '!': case '&':
    case '~': case '@': case '%':
        *out = (unsigned char)e;
        return 1;
    default:
        return 0;
    }
}

static int re_hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int re_hex_byte(const char *p, unsigned char *out) {
    int hi = re_hex_digit(p[0]);
    int lo = re_hex_digit(p[1]);
    if (hi < 0 || lo < 0) return 0;
    *out = (unsigned char)((hi << 4) | lo);
    return 1;
}

/* fragment: start op index + patch list of op fields pointing past the end.
 * Patch entries encode (op_index << 2) | field, field: 0 = next, 1 = a, 2 = b. */
typedef struct {
    int32_t start;
    int32_t patch[RE_MAX_OPS];
    int32_t npatch;
} ReFrag;

static void re_frag_init(ReFrag *f) {
    f->start = -1;
    f->npatch = 0;
}

static void re_frag_add_patch(ReParser *ps, ReFrag *f, int32_t op_index, int field) {
    if (f->npatch >= RE_MAX_OPS) {
        ps->err = PCC_RE_UNSUPPORTED;
        return;
    }
    f->patch[f->npatch++] = (op_index << 2) | field;
}

static void re_frag_patch_to(ReParser *ps, ReFrag *f, int32_t target) {
    int i;
    ReProg *pg = ps->prog;
    (void)ps;
    for (i = 0; i < f->npatch; i++) {
        int32_t op_index = f->patch[i] >> 2;
        int field = f->patch[i] & 3;
        if (field == 0) pg->ops[op_index].next = target;
        else if (field == 1) pg->ops[op_index].a = target;
        else pg->ops[op_index].b = target;
    }
    f->npatch = 0;
}

static void re_frag_merge(ReParser *ps, ReFrag *dst, const ReFrag *src) {
    int i;
    for (i = 0; i < src->npatch; i++) {
        if (dst->npatch >= RE_MAX_OPS) {
            ps->err = PCC_RE_UNSUPPORTED;
            return;
        }
        dst->patch[dst->npatch++] = src->patch[i];
    }
}

/* sequential concatenation: acc = acc then piece */
static void re_frag_cat(ReParser *ps, ReFrag *acc, ReFrag *piece) {
    if (piece->start < 0) return;
    if (acc->start < 0) {
        *acc = *piece;
        return;
    }
    re_frag_patch_to(ps, acc, piece->start);
    re_frag_merge(ps, acc, piece);
}

static int re_parse_alt(ReParser *ps, ReFrag *out, int *nullable);
static int re_parse_counts(ReParser *ps, int *m_out, int *n_out, int *inf_out);

/* returns 1 and fills the single-byte atom payload when the fragment is one
 * CHAR/ANY/CLASS op (enables the iterative quantifier fast ops). */
static int re_frag_single_atom(ReParser *ps, const ReFrag *f, unsigned char *atom_op,
                               unsigned char *atom_c, unsigned char *cls) {
    const ReOp *op;
    if (f->start < 0) return 0;
    op = &ps->prog->ops[f->start];
    if (op->op != OP_CHAR && op->op != OP_ANY && op->op != OP_CLASS) return 0;
    if (f->npatch != 1) return 0;
    if ((f->patch[0] >> 2) != f->start) return 0;
    *atom_op = op->op;
    *atom_c = (unsigned char)op->a;
    memcpy(cls, op->cls, 32);
    return 1;
}

static int re_parse_class(ReParser *ps, ReFrag *out) {
    unsigned char cls[32];
    int negate = 0;
    int first = 1;
    int idx;
    memset(cls, 0, sizeof(cls));
    ps->p++; /* consume '[' */
    if (*ps->p == '^') {
        negate = 1;
        ps->p++;
    }
    for (;;) {
        unsigned char lo;
        if (*ps->p == '\0') {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        if (*ps->p == ']' && !first) break;
        first = 0;
        if (*ps->p == '\\') {
            char e = ps->p[1];
            ps->p += 2;
            if (e == 'd' || e == 'w' || e == 's') {
                re_cls_perl(cls, e);
                continue;
            }
            if (e == 'D' || e == 'W' || e == 'S') {
                unsigned char tmp[32];
                memset(tmp, 0, sizeof(tmp));
                re_cls_perl(tmp, (char)(e + 32));
                re_cls_negate(tmp);
                { int i; for (i = 0; i < 32; i++) cls[i] |= tmp[i]; }
                continue;
            }
            if (e == 'x') {
                if (!re_hex_byte(ps->p, &lo)) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
                ps->p += 2;
            } else if (e == 'b') { /* \b inside a class is backspace in CPython */
                lo = 0x08;
            } else if (!re_literal_escape(e, &lo)) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
        } else {
            lo = (unsigned char)*ps->p;
            if (lo >= 0x80) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
            ps->p++;
        }
        if (*ps->p == '-' && ps->p[1] != ']' && ps->p[1] != '\0') {
            unsigned char hi;
            ps->p++;
            if (*ps->p == '\\') {
                char e = ps->p[1];
                ps->p += 2;
                if (e == 'x') {
                    if (!re_hex_byte(ps->p, &hi)) {
                        ps->err = PCC_RE_UNSUPPORTED;
                        return 0;
                    }
                    ps->p += 2;
                } else if (!re_literal_escape(e, &hi)) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
            } else {
                hi = (unsigned char)*ps->p;
                if (hi >= 0x80) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
                ps->p++;
            }
            if (hi < lo) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
            { unsigned int c; for (c = lo; c <= hi; c++) re_cls_set(cls, (unsigned char)c); }
        } else {
            re_cls_set(cls, lo);
        }
    }
    ps->p++; /* consume ']' */
    if (ps->flags & RE_FLAG_I) re_cls_fold_case(cls); /* fold BEFORE negation */
    if (negate) re_cls_negate(cls);
    idx = re_emit(ps, OP_CLASS);
    if (ps->err) return 0;
    memcpy(ps->prog->ops[idx].cls, cls, 32);
    re_frag_init(out);
    out->start = idx;
    re_frag_add_patch(ps, out, idx, 0);
    return 1;
}

static int re_parse_atom(ReParser *ps, ReFrag *out, int *nullable) {
    int idx;
    *nullable = 0;
    re_frag_init(out);
    switch (*ps->p) {
    case '(': {
        int gidx = -1;
        ReFrag body;
        int body_nullable = 0;
        int save_open = -1, save_close = -1;
        ps->p++;
        if (*ps->p == '?') {
            if (ps->p[1] == ':') {
                ps->p += 2;
            } else if (ps->p[1] == 'P' && ps->p[2] == '<') {
                /* (?P<name>...) named capturing group */
                char name[RE_MAX_NAME];
                int ni = 0;
                const char *q = ps->p + 3;
                int32_t g;
                if (!((*q >= 'A' && *q <= 'Z') || (*q >= 'a' && *q <= 'z') ||
                      *q == '_')) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
                while (*q != '\0' && *q != '>') {
                    char c2 = *q;
                    if (!((c2 >= 'A' && c2 <= 'Z') || (c2 >= 'a' && c2 <= 'z') ||
                          (c2 >= '0' && c2 <= '9') || c2 == '_') ||
                        ni >= RE_MAX_NAME - 1) {
                        ps->err = PCC_RE_UNSUPPORTED;
                        return 0;
                    }
                    name[ni++] = c2;
                    q++;
                }
                if (*q != '>' || ni == 0) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
                name[ni] = '\0';
                if (ps->prog->ngroups >= RE_MAX_GROUPS - 1) {
                    ps->err = PCC_RE_UNSUPPORTED;
                    return 0;
                }
                for (g = 1; g <= ps->prog->ngroups; g++) {
                    if (strcmp(ps->prog->group_names[g], name) == 0) {
                        ps->err = PCC_RE_UNSUPPORTED; /* duplicate name */
                        return 0;
                    }
                }
                gidx = ++ps->prog->ngroups;
                memcpy(ps->prog->group_names[gidx], name, (size_t)ni + 1);
                ps->p = q + 1;
            } else {
                ps->err = PCC_RE_UNSUPPORTED; /* (?P=, (?=, (?!, (?i.. */
                return 0;
            }
        } else {
            if (ps->prog->ngroups >= RE_MAX_GROUPS - 1) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
            gidx = ++ps->prog->ngroups;
        }
        if (gidx >= 0) {
            save_open = re_emit(ps, OP_SAVE);
            if (ps->err) return 0;
            ps->prog->ops[save_open].a = gidx * 2;
        }
        if (!re_parse_alt(ps, &body, &body_nullable)) return 0;
        if (*ps->p != ')') {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        ps->p++;
        *nullable = body_nullable;
        if (gidx >= 0) {
            save_close = re_emit(ps, OP_SAVE);
            if (ps->err) return 0;
            ps->prog->ops[save_close].a = gidx * 2 + 1;
            ps->prog->ops[save_open].next = body.start >= 0 ? body.start : save_close;
            re_frag_patch_to(ps, &body, save_close);
            re_frag_init(out);
            out->start = save_open;
            re_frag_add_patch(ps, out, save_close, 0);
        } else {
            if (body.start < 0) {
                /* empty (?:) — matches empty */
                idx = re_emit(ps, OP_JMP);
                if (ps->err) return 0;
                out->start = idx;
                re_frag_add_patch(ps, out, idx, 1);
                *nullable = 1;
                return 1;
            }
            out->start = body.start;
            re_frag_merge(ps, out, &body);
        }
        return !ps->err;
    }
    case '[':
        return re_parse_class(ps, out);
    case '.':
        ps->p++;
        idx = re_emit(ps, OP_ANY);
        if (ps->err) return 0;
        out->start = idx;
        re_frag_add_patch(ps, out, idx, 0);
        return 1;
    case '^':
        ps->p++;
        idx = re_emit(ps, OP_BOL);
        if (ps->err) return 0;
        out->start = idx;
        re_frag_add_patch(ps, out, idx, 0);
        *nullable = 1;
        return 1;
    case '$':
        ps->p++;
        idx = re_emit(ps, OP_EOL);
        if (ps->err) return 0;
        out->start = idx;
        re_frag_add_patch(ps, out, idx, 0);
        *nullable = 1;
        return 1;
    case '\\': {
        char e = ps->p[1];
        unsigned char lit;
        ps->p += 2;
        if (e == 'd' || e == 'D' || e == 'w' || e == 'W' || e == 's' || e == 'S') {
            unsigned char cls[32];
            memset(cls, 0, sizeof(cls));
            re_cls_perl(cls, (char)(e | 32));
            if (e >= 'A' && e <= 'Z') re_cls_negate(cls);
            idx = re_emit(ps, OP_CLASS);
            if (ps->err) return 0;
            memcpy(ps->prog->ops[idx].cls, cls, 32);
            out->start = idx;
            re_frag_add_patch(ps, out, idx, 0);
            return 1;
        }
        if (e == 'b' || e == 'B') {
            idx = re_emit(ps, e == 'b' ? OP_WB : OP_NWB);
            if (ps->err) return 0;
            out->start = idx;
            re_frag_add_patch(ps, out, idx, 0);
            *nullable = 1;
            return 1;
        }
        if (e == 'A' || e == 'Z') {
            idx = re_emit(ps, e == 'A' ? OP_BOS : OP_EOS);
            if (ps->err) return 0;
            out->start = idx;
            re_frag_add_patch(ps, out, idx, 0);
            *nullable = 1;
            return 1;
        }
        if (e >= '1' && e <= '9') { /* backreference */
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        if (e == 'x') {
            if (!re_hex_byte(ps->p, &lit)) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
            ps->p += 2;
        } else if (e == 'z' || e == 'u' || e == 'N' || e == '0') {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        } else if (!re_literal_escape(e, &lit)) {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        idx = re_emit(ps, OP_CHAR);
        if (ps->err) return 0;
        ps->prog->ops[idx].a = lit;
        out->start = idx;
        re_frag_add_patch(ps, out, idx, 0);
        return 1;
    }
    case '\0': case ')': case '|': case '*': case '+': case '?':
        ps->err = PCC_RE_UNSUPPORTED;
        return 0;
    default: {
        unsigned char c = (unsigned char)*ps->p;
        if (c >= 0x80) {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        if (c == '{') {
            /* a VALID counted repeat with nothing to repeat is a CPython
             * re.error ("nothing to repeat"); reject instead of matching
             * it literally. A malformed brace stays a literal. */
            ReParser tmp = *ps;
            int m, n, inf;
            if (re_parse_counts(&tmp, &m, &n, &inf)) {
                ps->err = PCC_RE_UNSUPPORTED;
                return 0;
            }
        }
        ps->p++;
        idx = re_emit(ps, OP_CHAR);
        if (ps->err) return 0;
        ps->prog->ops[idx].a = c;
        out->start = idx;
        re_frag_add_patch(ps, out, idx, 0);
        return 1;
    }
    }
}

/* ---------------- quantifier builders ---------------- */

static void re_build_ques(ReParser *ps, ReFrag *atom, int lazy, ReFrag *out) {
    int sp = re_emit(ps, OP_SPLIT);
    if (ps->err) return;
    re_frag_init(out);
    out->start = sp;
    if (lazy) {
        re_frag_add_patch(ps, out, sp, 1); /* prefer skipping */
        ps->prog->ops[sp].b = atom->start;
    } else {
        ps->prog->ops[sp].a = atom->start;
        re_frag_add_patch(ps, out, sp, 2);
    }
    re_frag_merge(ps, out, atom);
}

/* guarded loop body: GENTER -> atom -> GCHECK; GCHECK loops to loop_target
 * when the iteration advanced, else exits via its dangling next. */
static int re_build_guarded_body(ReParser *ps, ReFrag *atom, int *genter_out,
                                 int *gcheck_out) {
    int g, ge, gc;
    if (ps->prog->nguards >= RE_MAX_GUARDS) {
        ps->err = PCC_RE_UNSUPPORTED;
        return 0;
    }
    g = ps->prog->nguards++;
    ge = re_emit(ps, OP_GENTER);
    gc = re_emit(ps, OP_GCHECK);
    if (ps->err) return 0;
    ps->prog->ops[ge].a = g;
    ps->prog->ops[gc].a = g;
    ps->prog->ops[ge].next = atom->start;
    re_frag_patch_to(ps, atom, gc);
    *genter_out = ge;
    *gcheck_out = gc;
    return 1;
}

static void re_build_star(ReParser *ps, ReFrag *atom, int atom_nullable,
                          int lazy, ReFrag *out) {
    if (atom->start < 0) { /* star of empty fragment matches empty */
        int j = re_emit(ps, OP_JMP);
        if (ps->err) return;
        re_frag_init(out);
        out->start = j;
        re_frag_add_patch(ps, out, j, 1);
        return;
    }
    if (!atom_nullable) {
        int sp = re_emit(ps, OP_SPLIT);
        if (ps->err) return;
        if (lazy) ps->prog->ops[sp].b = atom->start;
        else ps->prog->ops[sp].a = atom->start;
        re_frag_patch_to(ps, atom, sp);
        re_frag_init(out);
        out->start = sp;
        re_frag_add_patch(ps, out, sp, lazy ? 1 : 2);
        return;
    }
    {
        int ge = -1, gc = -1, sp;
        if (!re_build_guarded_body(ps, atom, &ge, &gc)) return;
        sp = re_emit(ps, OP_SPLIT);
        if (ps->err) return;
        ps->prog->ops[gc].b = sp; /* advanced: try to loop again */
        if (lazy) ps->prog->ops[sp].b = ge;
        else ps->prog->ops[sp].a = ge;
        re_frag_init(out);
        out->start = sp;
        re_frag_add_patch(ps, out, sp, lazy ? 1 : 2);
        re_frag_add_patch(ps, out, gc, 0); /* empty iteration: stop, succeed */
    }
}

static void re_build_plus(ReParser *ps, ReFrag *atom, int atom_nullable,
                          int lazy, ReFrag *out) {
    if (atom->start < 0) {
        int j = re_emit(ps, OP_JMP);
        if (ps->err) return;
        re_frag_init(out);
        out->start = j;
        re_frag_add_patch(ps, out, j, 1);
        return;
    }
    if (!atom_nullable) {
        int sp = re_emit(ps, OP_SPLIT);
        if (ps->err) return;
        if (lazy) ps->prog->ops[sp].b = atom->start;
        else ps->prog->ops[sp].a = atom->start;
        re_frag_patch_to(ps, atom, sp);
        re_frag_init(out);
        out->start = atom->start;
        re_frag_add_patch(ps, out, sp, lazy ? 1 : 2);
        return;
    }
    {
        int ge = -1, gc = -1, sp;
        if (!re_build_guarded_body(ps, atom, &ge, &gc)) return;
        sp = re_emit(ps, OP_SPLIT);
        if (ps->err) return;
        ps->prog->ops[gc].b = sp;
        if (lazy) ps->prog->ops[sp].b = ge;
        else ps->prog->ops[sp].a = ge;
        re_frag_init(out);
        out->start = ge; /* first iteration is mandatory (may match empty once) */
        re_frag_add_patch(ps, out, sp, lazy ? 1 : 2);
        re_frag_add_patch(ps, out, gc, 0);
    }
}

/* try the single-byte-atom iterative fast ops; returns 1 when emitted */
static int re_build_fast_quant(ReParser *ps, ReFrag *atom, char q, int lazy,
                               ReFrag *out) {
    unsigned char a_op = 0, a_c = 0, a_cls[32];
    int idx;
    if (!re_frag_single_atom(ps, atom, &a_op, &a_c, a_cls)) return 0;
    idx = re_emit(ps, q == '*' ? OP_STAR1 : (q == '+' ? OP_PLUS1 : OP_QUES1));
    if (ps->err) return 1;
    ps->prog->ops[idx].lazy = (unsigned char)lazy;
    ps->prog->ops[idx].atom_op = a_op;
    ps->prog->ops[idx].atom_c = a_c;
    memcpy(ps->prog->ops[idx].cls, a_cls, 32);
    re_frag_init(out);
    out->start = idx;
    re_frag_add_patch(ps, out, idx, 0);
    return 1;
}

/* parse "{m}", "{m,}", "{m,n}", "{,n}" at ps->p (starting at '{').
 * Returns 1 and consumes through '}' on success; returns 0 WITHOUT consuming
 * when malformed (caller treats '{' as a literal, like CPython). */
static int re_parse_counts(ReParser *ps, int *m_out, int *n_out, int *inf_out) {
    const char *q = ps->p + 1;
    int m = -1, n = -1, inf = 0;
    if (*q >= '0' && *q <= '9') {
        m = 0;
        while (*q >= '0' && *q <= '9') {
            m = m * 10 + (*q - '0');
            if (m > 9999) return 0;
            q++;
        }
    }
    if (*q == ',') {
        q++;
        if (*q >= '0' && *q <= '9') {
            n = 0;
            while (*q >= '0' && *q <= '9') {
                n = n * 10 + (*q - '0');
                if (n > 9999) return 0;
                q++;
            }
        } else {
            inf = 1;
        }
    } else {
        n = m;
    }
    if (*q != '}') return 0;
    if (m < 0 && n < 0 && !inf) return 0; /* bare {,} or {} */
    if (m < 0) m = 0; /* {,n} == {0,n} */
    if (!inf && n < m) return 0;
    ps->p = q + 1;
    *m_out = m;
    *n_out = n;
    *inf_out = inf;
    return 1;
}

static int re_parse_rep(ReParser *ps, ReFrag *out, int *nullable) {
    ReFrag atom;
    int atom_nullable = 0;
    char q;
    int lazy = 0;
    if (!re_parse_atom(ps, &atom, &atom_nullable)) return 0;
    q = *ps->p;
    if (q != '*' && q != '+' && q != '?' && q != '{') {
        *out = atom;
        *nullable = atom_nullable;
        return 1;
    }
    if (q == '{') {
        int m, n, inf;
        unsigned char a_op = 0, a_c = 0, a_cls[32];
        if (!re_parse_counts(ps, &m, &n, &inf)) {
            /* malformed counted repeat: '{' is a literal (CPython) */
            *out = atom;
            *nullable = atom_nullable;
            return 1;
        }
        if (*ps->p == '?') {
            lazy = 1;
            ps->p++;
        }
        if (*ps->p == '*' || *ps->p == '+' || *ps->p == '?' || *ps->p == '{') {
            ps->err = PCC_RE_UNSUPPORTED; /* double quantifier */
            return 0;
        }
        if (m > RE_MAX_COUNT || (!inf && n > RE_MAX_COUNT)) {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        /* Counted repeats are supported ONLY over single-byte atoms
         * (CHAR/CLASS/ANY): the QUES1/STAR1 expansion is provably
         * equivalent to sre's REPEAT_ONE enumeration order. Group or
         * multi-op bodies are REJECTED: CPython's MIN/MAX_UNTIL backtracks
         * only the deepest iteration's inner choices, which diverges from
         * full preference-order DFS on shapes like (.{,3}){,3}?[a] (fuzz
         * minimized 2026-06-10) — never guess. */
        if (!re_frag_single_atom(ps, &atom, &a_op, &a_c, a_cls)) {
            ps->err = PCC_RE_UNSUPPORTED;
            return 0;
        }
        {
            ReFrag acc;
            int i;
            re_frag_init(&acc);
            acc = atom; /* first copy (unlinked when m == 0 and n == 0) */
            if (m == 0 && !inf && n == 0) {
                /* x{0}: matches empty; atom op stays emitted but unlinked */
                int j = re_emit(ps, OP_JMP);
                if (ps->err) return 0;
                re_frag_init(out);
                out->start = j;
                re_frag_add_patch(ps, out, j, 1);
                *nullable = 1;
                return 1;
            }
            if (m == 0) {
                /* first copy becomes optional (or the star tail below) */
                if (!inf) {
                    ReFrag piece;
                    if (!re_build_fast_quant(ps, &acc, '?', lazy, &piece)) {
                        ps->err = PCC_RE_UNSUPPORTED;
                        return 0;
                    }
                    if (ps->err) return 0;
                    acc = piece;
                } else {
                    ReFrag piece;
                    if (!re_build_fast_quant(ps, &acc, '*', lazy, &piece)) {
                        ps->err = PCC_RE_UNSUPPORTED;
                        return 0;
                    }
                    if (ps->err) return 0;
                    *out = piece;
                    *nullable = 1;
                    return 1;
                }
            }
            /* remaining mandatory copies (we already used one above when
             * m == 0; for m > 0 the first parsed atom is copy 1) */
            for (i = 1; i < m; i++) {
                int idx = re_emit(ps, a_op);
                ReFrag copy;
                if (ps->err) return 0;
                ps->prog->ops[idx].a = a_c;
                ps->prog->ops[idx].atom_c = a_c;
                memcpy(ps->prog->ops[idx].cls, a_cls, 32);
                re_frag_init(&copy);
                copy.start = idx;
                re_frag_add_patch(ps, &copy, idx, 0);
                re_frag_cat(ps, &acc, &copy);
                if (ps->err) return 0;
            }
            if (inf) {
                int idx = re_emit(ps, OP_STAR1);
                ReFrag tail;
                if (ps->err) return 0;
                ps->prog->ops[idx].lazy = (unsigned char)lazy;
                ps->prog->ops[idx].atom_op = a_op;
                ps->prog->ops[idx].atom_c = a_c;
                memcpy(ps->prog->ops[idx].cls, a_cls, 32);
                re_frag_init(&tail);
                tail.start = idx;
                re_frag_add_patch(ps, &tail, idx, 0);
                re_frag_cat(ps, &acc, &tail);
            } else {
                int extras = n - (m > 0 ? m : 1);
                for (i = 0; i < extras; i++) {
                    int idx = re_emit(ps, OP_QUES1);
                    ReFrag piece;
                    if (ps->err) return 0;
                    ps->prog->ops[idx].lazy = (unsigned char)lazy;
                    ps->prog->ops[idx].atom_op = a_op;
                    ps->prog->ops[idx].atom_c = a_c;
                    memcpy(ps->prog->ops[idx].cls, a_cls, 32);
                    re_frag_init(&piece);
                    piece.start = idx;
                    re_frag_add_patch(ps, &piece, idx, 0);
                    re_frag_cat(ps, &acc, &piece);
                    if (ps->err) return 0;
                }
            }
            *out = acc;
            *nullable = (m == 0);
            return 1;
        }
    }
    ps->p++;
    if (*ps->p == '?') {
        lazy = 1;
        ps->p++;
    }
    if (*ps->p == '*' || *ps->p == '+' || *ps->p == '?' || *ps->p == '{') {
        ps->err = PCC_RE_UNSUPPORTED; /* double quantifier */
        return 0;
    }
    if (!atom_nullable && re_build_fast_quant(ps, &atom, q, lazy, out)) {
        if (ps->err) return 0;
        *nullable = (q != '+');
        return 1;
    }
    if (q == '?') {
        re_build_ques(ps, &atom, lazy, out);
        *nullable = 1;
        return !ps->err;
    }
    if (q == '*') {
        re_build_star(ps, &atom, atom_nullable, lazy, out);
        *nullable = 1;
        return !ps->err;
    }
    re_build_plus(ps, &atom, atom_nullable, lazy, out);
    *nullable = atom_nullable;
    return !ps->err;
}

static int re_parse_cat(ReParser *ps, ReFrag *out, int *nullable) {
    ReFrag acc;
    int acc_nullable = 1;
    re_frag_init(&acc);
    for (;;) {
        char c = *ps->p;
        ReFrag piece;
        int piece_nullable;
        if (c == '\0' || c == '|' || c == ')') break;
        if (!re_parse_rep(ps, &piece, &piece_nullable)) return 0;
        re_frag_cat(ps, &acc, &piece);
        acc_nullable = acc_nullable && piece_nullable;
        if (ps->err) return 0;
    }
    *out = acc;
    *nullable = acc.start < 0 ? 1 : acc_nullable;
    return 1;
}

static int re_parse_alt(ReParser *ps, ReFrag *out, int *nullable) {
    ReFrag left;
    int left_nullable;
    if (!re_parse_cat(ps, &left, &left_nullable)) return 0;
    if (*ps->p != '|') {
        *out = left;
        *nullable = left_nullable;
        return 1;
    }
    {
        ReFrag rest;
        int rest_nullable;
        int sp;
        ps->p++; /* consume '|' */
        sp = re_emit(ps, OP_SPLIT);
        if (ps->err) return 0;
        if (!re_parse_alt(ps, &rest, &rest_nullable)) return 0;
        re_frag_init(out);
        out->start = sp;
        if (left.start >= 0) {
            ps->prog->ops[sp].a = left.start;
            re_frag_merge(ps, out, &left);
        } else {
            re_frag_add_patch(ps, out, sp, 1);
        }
        if (rest.start >= 0) {
            ps->prog->ops[sp].b = rest.start;
            re_frag_merge(ps, out, &rest);
        } else {
            re_frag_add_patch(ps, out, sp, 2);
        }
        *nullable = left_nullable || rest_nullable;
        return !ps->err;
    }
}

static int re_compile(const char *pattern, int64_t flags, ReProg *prog) {
    ReParser ps;
    ReFrag top;
    int nullable = 0;
    int match_idx;
    atomic_fetch_add_explicit(&re_compile_count, 1, memory_order_relaxed);
    ps.p = pattern;
    ps.prog = prog;
    ps.flags = flags;
    ps.err = 0;
    prog->nops = 0;
    prog->ngroups = 0;
    prog->nguards = 0;
    prog->flags = flags;
    memset(prog->group_names, 0, sizeof(prog->group_names));
    if (!re_parse_alt(&ps, &top, &nullable) || ps.err) return PCC_RE_UNSUPPORTED;
    if (*ps.p != '\0') return PCC_RE_UNSUPPORTED;
    match_idx = re_emit(&ps, OP_MATCH);
    if (ps.err) return PCC_RE_UNSUPPORTED;
    if (top.start < 0) {
        top.start = match_idx;
    } else {
        re_frag_patch_to(&ps, &top, match_idx);
    }
    return top.start;
}

static int re_compiled_program(const char *pattern, int64_t flags,
                               ReProg *scratch, const ReProg **prog_out) {
    int i;
    int start_pc;
    ReProg *cached_prog = NULL;
    char *cached_pattern = NULL;
    size_t pattern_len;
    if (pattern == NULL || scratch == NULL || prog_out == NULL) {
        return PCC_RE_BADARGS;
    }
    *prog_out = NULL;
    re_cache_acquire();
    for (i = 0; i < re_cache_count; i++) {
        if (re_cache[i].flags == flags &&
            strcmp(re_cache[i].pattern, pattern) == 0) {
            start_pc = re_cache[i].start_pc;
            *prog_out = re_cache[i].prog;
            re_cache_release();
            return start_pc;
        }
    }
    if (re_cache_count < RE_CACHE_CAPACITY) {
        pattern_len = strlen(pattern);
        cached_pattern = (char *)malloc(pattern_len + 1);
        cached_prog = (ReProg *)malloc(sizeof(ReProg));
        if (cached_pattern != NULL && cached_prog != NULL) {
            memcpy(cached_pattern, pattern, pattern_len + 1);
            start_pc = re_compile(pattern, flags, cached_prog);
            if (start_pc >= 0) {
                ReCacheEntry *entry = &re_cache[re_cache_count++];
                entry->pattern = cached_pattern;
                entry->flags = flags;
                entry->start_pc = start_pc;
                entry->prog = cached_prog;
                *prog_out = cached_prog;
                re_cache_release();
                return start_pc;
            }
            free(cached_pattern);
            free(cached_prog);
            re_cache_release();
            return start_pc;
        }
        free(cached_pattern);
        free(cached_prog);
    }
    re_cache_release();
    start_pc = re_compile(pattern, flags, scratch);
    if (start_pc >= 0) *prog_out = scratch;
    return start_pc;
}

/* ---------------- matcher ---------------- */

typedef struct {
    const ReProg *pg;
    const char *t;
    int64_t n;
    int64_t *caps;     /* 2 * (ngroups + 1) slots */
    int ncaps;
    int64_t guards[RE_MAX_GUARDS];
    int depth;
    int limit_hit;
} ReCtx;

static unsigned char re_fold_byte(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return (unsigned char)(c + 32);
    return c;
}

static int re_is_word_byte(unsigned char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') ||
           (c >= 'A' && c <= 'Z') || c == '_';
}

static int re_atom_ok(const ReOp *op, unsigned char op_kind, const char *t,
                      int64_t n, int64_t pos, int64_t flags) {
    unsigned char c;
    if (pos >= n) return 0;
    c = (unsigned char)t[pos];
    switch (op_kind) {
    case OP_CHAR:
        if (flags & RE_FLAG_I) {
            return re_fold_byte(c) == re_fold_byte(op->atom_c);
        }
        return c == op->atom_c;
    case OP_ANY:
        return c != '\n' || (flags & RE_FLAG_S) != 0;
    case OP_CLASS:
        return re_cls_has(op->cls, c);
    default:
        return 0;
    }
}

static int re_m(ReCtx *cx, int32_t pc, int64_t pos);

static int re_m_star1(ReCtx *cx, const ReOp *op, int64_t pos, int64_t minrun) {
    int64_t run = 0;
    while (re_atom_ok(op, op->atom_op, cx->t, cx->n, pos + run, cx->pg->flags)) run++;
    if (run < minrun) return PCC_RE_NOMATCH;
    if (op->lazy) {
        int64_t k;
        for (k = minrun; k <= run; k++) {
            int r = re_m(cx, op->next, pos + k);
            if (r != PCC_RE_NOMATCH) return r;
        }
        return PCC_RE_NOMATCH;
    }
    {
        int64_t k;
        for (k = run; k >= minrun; k--) {
            int r = re_m(cx, op->next, pos + k);
            if (r != PCC_RE_NOMATCH) return r;
        }
    }
    return PCC_RE_NOMATCH;
}

static int re_m(ReCtx *cx, int32_t pc, int64_t pos) {
    if (++cx->depth > RE_MAX_DEPTH) {
        cx->limit_hit = 1;
        cx->depth--;
        return PCC_RE_NOMATCH;
    }
    for (;;) {
        const ReOp *op;
        if (pc < 0 || pc >= cx->pg->nops) {
            cx->depth--;
            return PCC_RE_NOMATCH;
        }
        op = &cx->pg->ops[pc];
        switch (op->op) {
        case OP_CHAR:
            if (pos < cx->n &&
                ((cx->pg->flags & RE_FLAG_I)
                     ? re_fold_byte((unsigned char)cx->t[pos]) ==
                           re_fold_byte((unsigned char)op->a)
                     : (unsigned char)cx->t[pos] == (unsigned char)op->a)) {
                pos++;
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_ANY:
            if (pos < cx->n &&
                (cx->t[pos] != '\n' || (cx->pg->flags & RE_FLAG_S) != 0)) {
                pos++;
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_CLASS:
            if (pos < cx->n && re_cls_has(op->cls, (unsigned char)cx->t[pos])) {
                pos++;
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_BOL:
            if (pos == 0 ||
                ((cx->pg->flags & RE_FLAG_M) != 0 && pos > 0 &&
                 cx->t[pos - 1] == '\n')) {
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_EOL:
            /* CPython '$': end of text, before a trailing '\n', or (with
             * re.M) before ANY '\n'. */
            if (pos == cx->n ||
                ((cx->pg->flags & RE_FLAG_M) != 0
                     ? cx->t[pos] == '\n'
                     : (pos == cx->n - 1 && cx->t[pos] == '\n'))) {
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_BOS:
            if (pos == 0) {
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_EOS:
            if (pos == cx->n) {
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        case OP_WB:
        case OP_NWB: {
            int before = pos > 0 && re_is_word_byte((unsigned char)cx->t[pos - 1]);
            int after = pos < cx->n && re_is_word_byte((unsigned char)cx->t[pos]);
            int at_b = before != after;
            if ((op->op == OP_WB) ? at_b : !at_b) {
                pc = op->next;
                continue;
            }
            cx->depth--;
            return PCC_RE_NOMATCH;
        }
        case OP_JMP:
            pc = op->a;
            continue;
        case OP_SPLIT: {
            int r = re_m(cx, op->a, pos);
            if (r != PCC_RE_NOMATCH) {
                cx->depth--;
                return r;
            }
            pc = op->b;
            continue;
        }
        case OP_SAVE: {
            int slot = op->a;
            int64_t old;
            int r;
            if (slot < 0 || slot >= cx->ncaps) {
                cx->depth--;
                return PCC_RE_NOMATCH;
            }
            old = cx->caps[slot];
            cx->caps[slot] = pos;
            r = re_m(cx, op->next, pos);
            if (r == PCC_RE_NOMATCH) cx->caps[slot] = old;
            cx->depth--;
            return r;
        }
        case OP_GENTER: {
            int slot = op->a;
            int64_t old;
            int r;
            if (slot < 0 || slot >= RE_MAX_GUARDS) {
                cx->depth--;
                return PCC_RE_NOMATCH;
            }
            old = cx->guards[slot];
            cx->guards[slot] = pos;
            r = re_m(cx, op->next, pos);
            if (r == PCC_RE_NOMATCH) cx->guards[slot] = old;
            cx->depth--;
            return r;
        }
        case OP_GCHECK: {
            int slot = op->a;
            if (slot >= 0 && slot < RE_MAX_GUARDS && pos != cx->guards[slot]) {
                pc = op->b; /* iteration advanced: try to loop */
                continue;
            }
            pc = op->next; /* empty iteration: stop looping, succeed onward */
            continue;
        }
        case OP_STAR1: {
            int r = re_m_star1(cx, op, pos, 0);
            cx->depth--;
            return r;
        }
        case OP_PLUS1: {
            int r = re_m_star1(cx, op, pos, 1);
            cx->depth--;
            return r;
        }
        case OP_QUES1: {
            if (op->lazy) {
                int r = re_m(cx, op->next, pos);
                if (r != PCC_RE_NOMATCH) {
                    cx->depth--;
                    return r;
                }
                if (re_atom_ok(op, op->atom_op, cx->t, cx->n, pos, cx->pg->flags)) {
                    pos++;
                    pc = op->next;
                    continue;
                }
                cx->depth--;
                return PCC_RE_NOMATCH;
            }
            if (re_atom_ok(op, op->atom_op, cx->t, cx->n, pos, cx->pg->flags)) {
                int r = re_m(cx, op->next, pos + 1);
                if (r != PCC_RE_NOMATCH) {
                    cx->depth--;
                    return r;
                }
            }
            pc = op->next;
            continue;
        }
        case OP_MATCH:
            cx->caps[1] = pos; /* group 0 end */
            cx->depth--;
            return PCC_RE_MATCH;
        default:
            cx->depth--;
            return PCC_RE_NOMATCH;
        }
    }
}

/* ---------------- public entry points ---------------- */

int pcc_re_engine_supported(const char *pattern) {
    ReProg scratch;
    const ReProg *prog;
    if (pattern == NULL) return 0;
    return re_compiled_program(pattern, 0, &scratch, &prog) >= 0 ? 1 : 0;
}

int pcc_re_engine_supported_flags(const char *pattern, int64_t flags) {
    ReProg scratch;
    const ReProg *prog;
    if (pattern == NULL || (flags & ~(int64_t)PCC_RE_OK_FLAGS) != 0) return 0;
    return re_compiled_program(pattern, flags, &scratch, &prog) >= 0 ? 1 : 0;
}

int64_t pcc_re_engine_compile_count(void) {
    return atomic_load_explicit(&re_compile_count, memory_order_relaxed);
}

/*
 * Run the engine.
 *   is_search = 0: anchored at position 0 (re.match)
 *   is_search = 1: try successive start positions (re.search)
 * caps receives 2*(ngroups+1) byte offsets (-1 = unset); caps_len is the
 * caller-provided capacity in int64 slots. ngroups_out receives the number
 * of capturing groups in the pattern.
 * Returns PCC_RE_MATCH / PCC_RE_NOMATCH / PCC_RE_UNSUPPORTED /
 * PCC_RE_LIMIT / PCC_RE_BADARGS / PCC_RE_NONASCII.
 */
int pcc_re_engine_run_flags(const char *pattern, int64_t flags,
                            const char *text, int64_t text_len, int64_t start,
                            int is_search, int64_t *caps, int caps_len,
                            int64_t *ngroups_out) {
    ReProg scratch;
    const ReProg *prog;
    ReCtx cx;
    int start_pc;
    int need;
    int64_t s;
    int64_t i;
    if (pattern == NULL || text == NULL || caps == NULL || ngroups_out == NULL) {
        return PCC_RE_BADARGS;
    }
    if (start < 0) start = 0;
    for (i = 0; i < text_len; i++) {
        if ((unsigned char)text[i] >= 0x80) return PCC_RE_NONASCII;
    }
    if ((flags & ~(int64_t)PCC_RE_OK_FLAGS) != 0) return PCC_RE_UNSUPPORTED;
    start_pc = re_compiled_program(pattern, flags, &scratch, &prog);
    if (start_pc < 0) return PCC_RE_UNSUPPORTED;
    *ngroups_out = prog->ngroups;
    need = 2 * (prog->ngroups + 1);
    if (caps_len < need) return PCC_RE_BADARGS;
    cx.pg = prog;
    cx.t = text;
    cx.n = text_len;
    cx.caps = caps;
    cx.ncaps = need;
    for (s = start; s <= (is_search ? text_len : start); s++) {
        int r;
        for (i = 0; i < need; i++) caps[i] = -1;
        for (i = 0; i < RE_MAX_GUARDS; i++) cx.guards[i] = -1;
        cx.depth = 0;
        cx.limit_hit = 0;
        caps[0] = s; /* group 0 start */
        r = re_m(&cx, start_pc, s);
        if (cx.limit_hit) return PCC_RE_LIMIT;
        if (r == PCC_RE_MATCH) return PCC_RE_MATCH;
    }
    for (i = 0; i < need; i++) caps[i] = -1;
    return PCC_RE_NOMATCH;
}

int pcc_re_engine_run_from(const char *pattern, const char *text,
                           int64_t text_len, int64_t start, int is_search,
                           int64_t *caps, int caps_len, int64_t *ngroups_out) {
    return pcc_re_engine_run_flags(pattern, 0, text, text_len, start,
                                   is_search, caps, caps_len, ngroups_out);
}

int pcc_re_engine_run(const char *pattern, const char *text, int64_t text_len,
                      int is_search, int64_t *caps, int caps_len,
                      int64_t *ngroups_out) {
    return pcc_re_engine_run_flags(pattern, 0, text, text_len, 0, is_search,
                                   caps, caps_len, ngroups_out);
}

/*
 * Write the capturing-group names for groups 1..N as consecutive
 * NUL-terminated strings into out (empty string for unnamed groups).
 * Returns the group count, or PCC_RE_UNSUPPORTED / PCC_RE_BADARGS.
 */
int pcc_re_engine_group_names_flags(const char *pattern, int64_t flags,
                                    char *out, int out_len) {
    ReProg scratch;
    const ReProg *prog;
    int g;
    int off = 0;
    if (pattern == NULL || out == NULL) return PCC_RE_BADARGS;
    if ((flags & ~(int64_t)PCC_RE_OK_FLAGS) != 0) return PCC_RE_UNSUPPORTED;
    if (re_compiled_program(pattern, flags, &scratch, &prog) < 0) {
        return PCC_RE_UNSUPPORTED;
    }
    for (g = 1; g <= prog->ngroups; g++) {
        int len = (int)strlen(prog->group_names[g]);
        if (off + len + 1 > out_len) return PCC_RE_BADARGS;
        memcpy(out + off, prog->group_names[g], (size_t)len + 1);
        off += len + 1;
    }
    return prog->ngroups;
}

int pcc_re_engine_group_names(const char *pattern, char *out, int out_len) {
    return pcc_re_engine_group_names_flags(pattern, 0, out, out_len);
}
