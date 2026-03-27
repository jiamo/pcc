#include "_fake_defines.h"
#include "_fake_typedefs.h"

struct utimbuf {
    time_t actime;
    time_t modtime;
};

int utime(const char *path, const struct utimbuf *times);
