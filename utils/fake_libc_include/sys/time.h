#include "_fake_defines.h"
#include "_fake_typedefs.h"

struct timeval {
    time_t tv_sec;
    suseconds_t tv_usec;
};

int gettimeofday(struct timeval *restrict tp, void *restrict tzp);
int settimeofday(const struct timeval *tp, const void *tzp);
int utimes(const char *path, const struct timeval times[2]);
int futimes(int fd, const struct timeval times[2]);
