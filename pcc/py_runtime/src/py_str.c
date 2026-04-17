/* pcc/py_runtime/src/py_str.c
 *
 * Phase 2 UTF-8 string runtime for pcc's Python frontend.
 *
 * PyStrObject stores raw UTF-8 bytes inline (flexible array member) with
 * a NUL terminator one past the last byte, so both `const char*` access
 * and CPython-style length-prefixed access are O(1). Codepoint length
 * and the object hash are computed lazily and cached.
 *
 * Semantics:
 *   - Indexing and slicing are codepoint-aware: user-visible indices
 *     are codepoint indices, which we translate to byte offsets by
 *     scanning UTF-8 lead bytes (bytes where (b & 0xC0) != 0x80).
 *   - Equality, find, startswith, endswith, contains, split, replace
 *     all operate on bytes. For valid UTF-8 input this is equivalent to
 *     codepoint-level comparison.
 *   - `find` returns a codepoint offset (Python compatibility) while
 *     still scanning bytes internally.
 *   - upper/lower are ASCII-only in Phase 2. Full Unicode case mapping
 *     requires case-folding tables and is left as a TODO.
 *
 * All functions here (except private helpers) implement the ABI laid
 * out in pcc/py_runtime/include/py_runtime.h section 3.
 */

#include "py_internal.h"

#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdint.h>
#include <ctype.h>

/* ======================================================================
 * Internal helpers
 * ====================================================================== */

/* Allocate a new PyStrObject sized for `byte_len` payload bytes plus a
 * NUL terminator. Fields other than the data tail are initialised here
 * but the caller is responsible for copying bytes into ->data and for
 * writing the terminating NUL. Returns NULL on allocation failure.
 *
 * Safe to call with byte_len == 0; the returned string is the empty
 * string "" (still NUL terminated). */
static PyStrObject *py_str_alloc(int64_t byte_len) {
    if (byte_len < 0) return NULL;
    /* One extra byte for NUL. */
    size_t total = sizeof(PyStrObject) + (size_t)byte_len + 1u;
    PyStrObject *s = (PyStrObject *)malloc(total);
    if (s == NULL) return NULL;
    s->h.refcount = 1;
    s->h.type_tag = PY_TYPE_STR;
    s->h.flags    = 0;
    s->byte_len   = byte_len;
    s->cp_len     = -1;
    s->hash       = -1;
    s->data[byte_len] = '\0';   /* early NUL; caller overwrites payload */
    return s;
}

/* ======================================================================
 * Construction & basic accessors
 * ====================================================================== */

PyObject *py_str_new(const char *utf8, int64_t byte_len) {
    if (byte_len < 0) byte_len = 0;
    /* NULL input with zero length is legal: gives back the empty str. */
    PyStrObject *s = py_str_alloc(byte_len);
    if (s == NULL) return NULL;
    if (utf8 != NULL && byte_len > 0) {
        memcpy(s->data, utf8, (size_t)byte_len);
    }
    /* cp_len and hash stay -1 (lazy) per contract. */
    return (PyObject *)s;
}

/* py_chr_from_i64 and py_str_len moved to py_str_accessors.c. */

/* py_str_byte_len and py_str_utf8 moved to py_str_accessors.c so the
 * pcc-Python port (py_str_accessors.py) can replace them independently. */

/* ======================================================================
 * Concatenation, repetition, slicing, indexing
 * ====================================================================== */

/* py_str_concat, py_str_repeat, py_str_slice, and py_str_index moved to
 * py_str_accessors.c. */

/* ======================================================================
 * Comparisons & lookup
 * ====================================================================== */

/* py_str_eq moved to py_str_accessors.c */

/* py_str_contains and py_str_find moved to py_str_accessors.c. */

/* py_str_startswith / py_str_endswith moved to py_str_accessors.c */

/* ======================================================================
 * ASCII case mapping (Phase 2)
 * ====================================================================== */
/* Full Unicode case mapping requires case-folding tables (e.g. from
 * UCD's CaseFolding.txt). Phase 2 implements ASCII-only conversion:
 * bytes 0x41..0x5A flip with 0x61..0x7A; all other bytes pass through.
 * This preserves byte_len (one byte changes to another byte, never
 * widening) which keeps the cp_len count valid. */

/* py_str_upper / py_str_lower moved to py_str_accessors.c */

/* ======================================================================
 * Whitespace stripping
 * ====================================================================== */

/* py_str_strip / lstrip / rstrip moved to py_str_accessors.c */

/* py_str_strip_chars / lstrip_chars / rstrip_chars / count moved to
 * py_str_accessors.c. py_str_isdigit / isalpha / isspace / isalnum too. */

/* ======================================================================
 * split / join / replace
 * ====================================================================== */

/* py_str_split, py_str_join, py_str_replace, py_str_hash, and
 * py_str_splitlines* moved to py_str_accessors.c. */
