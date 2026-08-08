#define _GNU_SOURCE 1

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/resource.h>

#if defined(__APPLE__)
#include <malloc/malloc.h>
#elif defined(__linux__)
#include <malloc.h>
#endif

#ifndef PCC_ALLOCATOR
#define PCC_ALLOCATOR 0
#endif

#if PCC_ALLOCATOR
long pcc_allocator_mapped_bytes(void);
long pcc_allocator_live_requested_bytes(void);
long pcc_allocator_live_usable_bytes(void);
#endif

enum { LIVE_SLOTS = 2048 };

static uint64_t monotonic_ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + (uint64_t)value.tv_nsec;
}

static uint64_t peak_rss_bytes(void) {
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) != 0) return 0;
#if defined(__APPLE__)
    return (uint64_t)usage.ru_maxrss;
#else
    return (uint64_t)usage.ru_maxrss * UINT64_C(1024);
#endif
}

static uint64_t host_retained_capacity(void) {
#if defined(__APPLE__)
    malloc_statistics_t stats = {0};
    malloc_zone_statistics(NULL, &stats);
    return (uint64_t)stats.size_allocated;
#elif defined(__linux__)
    struct mallinfo2 stats = mallinfo2();
    return (uint64_t)stats.arena + (uint64_t)stats.hblkhd;
#else
    return 0;
#endif
}

static long parse_rounds(const char *text) {
    char *end = NULL;
    long value;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0) return -1;
    return value;
}

int main(int argc, char **argv) {
    const char *mode = PCC_ALLOCATOR ? "pcc" : "host";
    long rounds = argc > 1 ? parse_rounds(argv[1]) : 200000;
    void **slots;
    uint64_t checksum = 0;
    uint64_t start;
    uint64_t end;
    uint64_t elapsed;
    uint64_t throughput;
    uint64_t retained;
    long live_requested_before = 0;
    long live_usable_before = 0;
    long live_requested_after = 0;
    long live_usable_after = 0;
    long i;

    if (rounds <= 0) return 2;
#if PCC_ALLOCATOR
    live_requested_before = pcc_allocator_live_requested_bytes();
    live_usable_before = pcc_allocator_live_usable_bytes();
#endif

    slots = (void **)malloc((size_t)LIVE_SLOTS * sizeof(void *));
    if (slots == NULL) return 3;
    for (i = 0; i < LIVE_SLOTS; i++) {
        size_t size = (size_t)((i * 173) % 2048) + 1;
        unsigned char *ptr = (unsigned char *)malloc(size);
        if (ptr == NULL) return 4;
        ptr[0] = (unsigned char)i;
        if (size > 1) ptr[size - 1] = (unsigned char)(i ^ 0x5a);
        slots[i] = ptr;
    }

    start = monotonic_ns();
    if (start == 0) return 5;
    for (i = 0; i < rounds; i++) {
        size_t slot = (size_t)i & (LIVE_SLOTS - 1);
        size_t size = (size_t)(((uint64_t)i * UINT64_C(1315423911)) % 2048) + 1;
        unsigned char marker = (unsigned char)(i * 17 + 3);
        unsigned char *ptr;

        free(slots[slot]);
        ptr = (unsigned char *)malloc(size);
        if (ptr == NULL) return 6;
        ptr[0] = marker;
        if (size > 1) ptr[size - 1] = (unsigned char)(marker ^ 0xa5);
        if ((i & 3) == 0) {
            size_t grown = size + 257;
            ptr = (unsigned char *)realloc(ptr, grown);
            if (ptr == NULL || ptr[0] != marker) return 7;
            ptr[grown - 1] = (unsigned char)(marker ^ 0x3c);
            checksum += ptr[grown - 1];
        } else {
            checksum += ptr[size - 1];
        }
        checksum += ptr[0];
        slots[slot] = ptr;
    }
    end = monotonic_ns();
    if (end <= start) return 8;

    for (i = 0; i < LIVE_SLOTS; i++) free(slots[i]);
    free(slots);

    elapsed = end - start;
    throughput = (uint64_t)rounds * UINT64_C(1000000000) / elapsed;
#if PCC_ALLOCATOR
    retained = (uint64_t)pcc_allocator_mapped_bytes();
    live_requested_after = pcc_allocator_live_requested_bytes();
    live_usable_after = pcc_allocator_live_usable_bytes();
#else
    retained = host_retained_capacity();
#endif

    printf(
        "%s,%ld,%llu,%llu,%llu,%llu,%ld,%ld,%llu\n",
        mode,
        rounds,
        (unsigned long long)elapsed,
        (unsigned long long)throughput,
        (unsigned long long)peak_rss_bytes(),
        (unsigned long long)retained,
        live_requested_after - live_requested_before,
        live_usable_after - live_usable_before,
        (unsigned long long)checksum
    );
    return 0;
}
