/* pcc-owned _FORTIFY_SOURCE bounds-checking wrappers.
 *
 * LIBC-P2-MEM-STR: the runtime's compiled code references __memcpy_chk
 * (clang emits it for a memcpy whose destination size it knows). It is a
 * platform fortify symbol, not a musl function — musl has no _FORTIFY_SOURCE
 * layer — so this is pcc's own implementation rather than a vendored source,
 * and it lives with the runtime C kernel like any other ABI-boundary helper.
 *
 * Semantics follow the platform contract: the wrapper aborts when the copy
 * would exceed the destination's known size, otherwise it performs the copy.
 * Aborting (rather than truncating) is the point of the check — a silent
 * short copy would turn a detected overflow into corrupted data.
 */

#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#ifdef PCC_USE_FREESTANDING_PLATFORM_PROCESS
extern void pcc_platform_abort(void);
#define pcc_fortify_abort pcc_platform_abort
#else
#define pcc_fortify_abort abort
#endif

void *__memcpy_chk(void *dst, const void *src, size_t len, size_t dst_len) {
    if (len > dst_len) {
        pcc_fortify_abort();
    }
    return memcpy(dst, src, len);
}

void *__memmove_chk(void *dst, const void *src, size_t len, size_t dst_len) {
    if (len > dst_len) {
        pcc_fortify_abort();
    }
    return memmove(dst, src, len);
}

void *__memset_chk(void *dst, int fill, size_t len, size_t dst_len) {
    if (len > dst_len) {
        pcc_fortify_abort();
    }
    return memset(dst, fill, len);
}
