#include "py_internal.h"

/* G-P3-LONGRUN fragmentation surface for the malloc-backed backends
 * (0-3): allocator-level heap statistics. C-only helper (no pcc-Python
 * mirror); long-run binaries poll these through the runtime ABI.
 *
 * Definitions (recorded in docs/plans/gc-longrun-benchmark-plan.md):
 *   in_use   = bytes currently handed out to the program by malloc
 *   capacity = bytes the allocator holds from the OS for the heap
 *   fragmentation/overhead proxy = capacity - in_use
 * Backend 4 keeps its richer zpage capacity/allocated metrics; these
 * helpers make the same axis observable on backends 0-3.
 *
 * Returns -1 when the platform query fails; the Linux branch is
 * UNTESTED until S-P2-LINUX provides a gated host. */

#if defined(__APPLE__)
#include <malloc/malloc.h>

int64_t pcc_os_heap_in_use_bytes(void) {
    malloc_statistics_t stats;
    /* NULL zone aggregates statistics across all malloc zones. */
    malloc_zone_statistics(NULL, &stats);
    return (int64_t)stats.size_in_use;
}

int64_t pcc_os_heap_capacity_bytes(void) {
    malloc_statistics_t stats;
    malloc_zone_statistics(NULL, &stats);
    return (int64_t)stats.size_allocated;
}

#elif defined(__linux__)
#include <malloc.h>

int64_t pcc_os_heap_in_use_bytes(void) {
#if defined(__GLIBC__) && (__GLIBC__ > 2 || (__GLIBC__ == 2 && __GLIBC_MINOR__ >= 33))
    struct mallinfo2 mi = mallinfo2();
    return (int64_t)mi.uordblks;
#else
    return -1;
#endif
}

int64_t pcc_os_heap_capacity_bytes(void) {
#if defined(__GLIBC__) && (__GLIBC__ > 2 || (__GLIBC__ == 2 && __GLIBC_MINOR__ >= 33))
    struct mallinfo2 mi = mallinfo2();
    return (int64_t)(mi.arena + mi.hblkhd);
#else
    return -1;
#endif
}

#else

int64_t pcc_os_heap_in_use_bytes(void) { return -1; }
int64_t pcc_os_heap_capacity_bytes(void) { return -1; }

#endif
