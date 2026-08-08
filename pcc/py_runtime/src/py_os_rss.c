#include "py_internal.h"

/* G-P3-LONGRUN slice 2 (docs/plans/gc-longrun-benchmark-plan.md):
 * process RSS sampling for the long-running GC workloads. This is the
 * readable host-C oracle; the production no-libpython archive owns the same
 * ABI in py/freestanding_platform_rss.py.
 *
 * Returns -1 when the platform query fails. */

#if defined(__APPLE__)
#include <mach/mach.h>
#include <sys/resource.h>

int64_t pcc_os_current_rss_bytes(void) {
    struct mach_task_basic_info info;
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    kern_return_t kr = task_info(
        mach_task_self(),
        MACH_TASK_BASIC_INFO,
        (task_info_t)&info,
        &count
    );
    if (kr != KERN_SUCCESS) return -1;
    return (int64_t)info.resident_size;
}

int64_t pcc_os_peak_rss_bytes(void) {
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) != 0) return -1;
    /* ru_maxrss is BYTES on macOS (kilobytes on Linux). */
    return (int64_t)ru.ru_maxrss;
}

#elif defined(__linux__)
#include <stdio.h>
#include <sys/resource.h>
#include <unistd.h>

int64_t pcc_os_current_rss_bytes(void) {
    FILE *f = fopen("/proc/self/statm", "r");
    if (f == NULL) return -1;
    long pages_total = 0;
    long pages_resident = 0;
    int n = fscanf(f, "%ld %ld", &pages_total, &pages_resident);
    fclose(f);
    if (n != 2) return -1;
    return (int64_t)pages_resident * (int64_t)sysconf(_SC_PAGESIZE);
}

int64_t pcc_os_peak_rss_bytes(void) {
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) != 0) return -1;
    /* ru_maxrss is KILOBYTES on Linux. */
    return (int64_t)ru.ru_maxrss * 1024;
}

#else

int64_t pcc_os_current_rss_bytes(void) { return -1; }
int64_t pcc_os_peak_rss_bytes(void) { return -1; }

#endif
