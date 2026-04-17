/* pcc/py_runtime/src/py_int_bigint_convert.c
 *
 * Bignum-to-int64 conversion split from py_int.c for Phase 4c.
 */

#include "py_internal.h"
#include <stdint.h>

int64_t py_bigint_to_i64(const PyIntObject *b, int *overflow) {
    if (overflow) *overflow = 0;
    if (b->sign == 0) return 0;

    if (b->ndigits > 2) {
        if (overflow) *overflow = 1;
        return 0;
    }
    uint64_t u = (uint64_t)b->digits[0];
    if (b->ndigits == 2) u |= ((uint64_t)b->digits[1]) << 32;

    if (b->sign > 0) {
        if (u > (uint64_t)INT64_MAX) {
            if (overflow) *overflow = 1;
            return 0;
        }
        return (int64_t)u;
    } else {
        if (u > (uint64_t)INT64_MAX + 1u) {
            if (overflow) *overflow = 1;
            return 0;
        }
        if (u == (uint64_t)INT64_MAX + 1u) return INT64_MIN;
        return -(int64_t)u;
    }
}
