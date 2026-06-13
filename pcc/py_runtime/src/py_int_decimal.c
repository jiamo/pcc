/* pcc/py_runtime/src/py_int_decimal.c
 *
 * Decimal string conversion split from py_int.c so the pcc-Python
 * runtime can replace it independently from core bignum arithmetic.
 */

#include "py_internal.h"
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

static void py_bigint_normalize(PyIntObject *b) {
    while (b->ndigits > 0 && b->digits[b->ndigits - 1] == 0) {
        b->ndigits--;
    }
    if (b->ndigits == 0) b->sign = 0;
    else if (b->sign == 0) b->sign = 1;
}

static PyIntObject *bigint_copy(const PyIntObject *a) {
    PyIntObject *r = py_bigint_alloc(a->ndigits);
    if (r == NULL) return NULL;
    r->sign = a->sign;
    for (int32_t i = 0; i < a->ndigits; i++) r->digits[i] = a->digits[i];
    return r;
}

static uint32_t bigint_divmod_small_inplace(PyIntObject *a, uint32_t divisor) {
    uint64_t rem = 0;
    for (int32_t i = a->ndigits - 1; i >= 0; i--) {
        uint64_t cur = (rem << 32) | (uint64_t)a->digits[i];
        a->digits[i] = (uint32_t)(cur / divisor);
        rem = cur % divisor;
    }
    py_bigint_normalize(a);
    return (uint32_t)rem;
}

char *py_bigint_to_cstr(const PyIntObject *b) {
    if (b->sign == 0) {
        char *s = (char *)malloc(2);
        if (!s) return NULL;
        s[0] = '0'; s[1] = '\0';
        return s;
    }
    /* Upper bound: 10 decimal digits per 32-bit digit + sign + NUL. */
    size_t bufsz = (size_t)b->ndigits * 10 + 2;
    char *buf = (char *)malloc(bufsz);
    if (buf == NULL) return NULL;

    /* Work on a mutable copy. */
    PyIntObject *tmp = bigint_copy(b);
    if (tmp == NULL) { free(buf); return NULL; }
    tmp->sign = 1;  /* we know original is nonzero */

    size_t pos = bufsz;
    buf[--pos] = '\0';

    /* Repeatedly divmod by 10^9 to extract 9 decimal digits per iter. */
    const uint32_t CHUNK = 1000000000u;
    while (tmp->ndigits > 0) {
        uint32_t rem = bigint_divmod_small_inplace(tmp, CHUNK);
        bool more = tmp->ndigits > 0;
        /* Write exactly 9 digits if more chunks remain (zero-pad); else the
         * minimal representation. */
        int count = 0;
        if (!more) {
            /* strip leading zeros for the highest chunk */
            if (rem == 0) {
                buf[--pos] = '0';
                count = 1;
            } else {
                while (rem > 0) {
                    buf[--pos] = (char)('0' + (rem % 10));
                    rem /= 10;
                    count++;
                }
            }
        } else {
            for (int k = 0; k < 9; k++) {
                buf[--pos] = (char)('0' + (rem % 10));
                rem /= 10;
            }
            count = 9;
        }
        (void)count;
    }
    free(tmp);

    if (b->sign < 0) buf[--pos] = '-';
    /* Shift the string down so it starts at buf[0]. */
    size_t len = bufsz - pos;
    memmove(buf, buf + pos, len);
    /* Shrink allocation? Leave it — caller frees it regardless. */
    return buf;
}

/* Full base-{2,8,16} string for a bignum: "[-]0<prefix_ch><digits>" (lowercase
 * a-f for hex), e.g. py_bigint_to_base_cstr(B, 16, 'x') -> "0x10000...".
 * Mirrors py_bigint_to_cstr but divides by the (small) base, one digit per
 * iteration. Caller frees the returned malloc'd, NUL-terminated string. */
char *py_bigint_to_base_cstr(const PyIntObject *b, unsigned base, char prefix_ch) {
    int neg = b->sign < 0;
    /* base 2 is the widest: up to 32 binary digits per 32-bit limb, plus
     * sign + "0x" + NUL. */
    size_t bufsz = (size_t)b->ndigits * 32 + 8;
    char *buf = (char *)malloc(bufsz);
    if (buf == NULL) return NULL;
    PyIntObject *tmp = bigint_copy(b);
    if (tmp == NULL) { free(buf); return NULL; }
    tmp->sign = 1;  /* work on the magnitude (b is nonzero here) */
    size_t pos = bufsz;
    buf[--pos] = '\0';
    do {
        uint32_t rem = bigint_divmod_small_inplace(tmp, base);
        buf[--pos] = (rem < 10) ? (char)('0' + rem)
                                : (char)('a' + (rem - 10));
    } while (tmp->ndigits > 0);
    free(tmp);
    buf[--pos] = prefix_ch;
    buf[--pos] = '0';
    if (neg) buf[--pos] = '-';
    size_t len = bufsz - pos;  /* includes the NUL */
    memmove(buf, buf + pos, len);
    return buf;
}

/* Parse a decimal string (optional leading sign, then digits). Returns a
 * new bignum (never a tagged form) or NULL on error. */
PyIntObject *py_bigint_from_cstr(const char *s) {
    if (s == NULL) return NULL;
    const char *p = s;
    int sign = 1;
    if (*p == '+') p++;
    else if (*p == '-') { sign = -1; p++; }
    if (*p == '\0') return NULL;
    /* Accumulate by 10^9 chunks. */
    PyIntObject *acc = py_bigint_alloc(0);
    if (acc == NULL) return NULL;

    while (*p) {
        /* Read up to 9 decimal digits. */
        uint32_t chunk = 0;
        uint32_t mul = 1;
        int count = 0;
        while (*p && count < 9) {
            if (*p < '0' || *p > '9') { free(acc); return NULL; }
            chunk = chunk * 10 + (uint32_t)(*p - '0');
            mul *= 10;
            count++;
            p++;
        }
        if (count == 0) { free(acc); return NULL; }

        /* acc = acc * mul + chunk */
        /* Multiply in-place: allocate result with one extra digit. */
        int32_t la = acc->ndigits;
        PyIntObject *next = py_bigint_alloc(la + 1);
        if (next == NULL) { free(acc); return NULL; }
        uint64_t carry = 0;
        for (int32_t i = 0; i < la; i++) {
            uint64_t cur = (uint64_t)acc->digits[i] * (uint64_t)mul + carry;
            next->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        next->digits[la] = (uint32_t)carry;
        /* Add chunk. */
        carry = chunk;
        for (int32_t i = 0; i < next->ndigits && carry; i++) {
            uint64_t cur = (uint64_t)next->digits[i] + carry;
            next->digits[i] = (uint32_t)(cur & 0xFFFFFFFFu);
            carry = cur >> 32;
        }
        next->sign = 1;
        py_bigint_normalize(next);
        free(acc);
        acc = next;
    }
    if (acc->ndigits == 0) {
        acc->sign = 0;
    } else {
        acc->sign = sign;
    }
    return acc;
}
