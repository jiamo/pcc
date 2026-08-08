#include "_fake_defines.h"
#include "_fake_typedefs.h"
#include "time.h"

/* struct rusage layout locked against the macOS SDK by
 * tests/python/test_sdk_struct_helpers_pcc.py:
 *   sizeof(struct rusage) == 144, ru_maxrss at +32, RUSAGE_SELF == 0.
 * ru_maxrss is BYTES on macOS (kilobytes on Linux). */
struct rusage {
    struct timeval ru_utime;
    struct timeval ru_stime;
    long ru_maxrss;
    long ru_ixrss;
    long ru_idrss;
    long ru_isrss;
    long ru_minflt;
    long ru_majflt;
    long ru_nswap;
    long ru_inblock;
    long ru_oublock;
    long ru_msgsnd;
    long ru_msgrcv;
    long ru_nsignals;
    long ru_nvcsw;
    long ru_nivcsw;
};

#define RUSAGE_SELF 0
#define RUSAGE_CHILDREN (-1)

int getrusage(int who, struct rusage *r_usage);
