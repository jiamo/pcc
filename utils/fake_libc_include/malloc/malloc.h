#ifndef _FAKE_MALLOC_MALLOC_H
#define _FAKE_MALLOC_MALLOC_H

#include "../_fake_defines.h"
#include "../_fake_typedefs.h"

/* Minimal malloc-zone statistics surface for pcc-compiled runtime sources
 * (py_os_heap.c). Layout locked against the macOS SDK by
 * tests/python/test_sdk_struct_helpers_pcc.py:
 *   sizeof(malloc_statistics_t) == 32
 *   size_in_use +8, max_size_in_use +16, size_allocated +24
 */

typedef struct malloc_statistics_t {
    unsigned int blocks_in_use;
    size_t size_in_use;
    size_t max_size_in_use;
    size_t size_allocated;
} malloc_statistics_t;

typedef struct _malloc_zone_t malloc_zone_t;

void malloc_zone_statistics(malloc_zone_t *zone, malloc_statistics_t *stats);

#endif
