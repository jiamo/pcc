#ifndef _FAKE_TIME_H
#define _FAKE_TIME_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

struct tm {
    int tm_sec;
    int tm_min;
    int tm_hour;
    int tm_mday;
    int tm_mon;
    int tm_year;
    int tm_wday;
    int tm_yday;
    int tm_isdst;
    long tm_gmtoff;
    const char *tm_zone;
};

clock_t clock(void);
time_t time(time_t *timer);
double difftime(time_t end, time_t beginning);
time_t mktime(struct tm *timeptr);
struct tm *gmtime(const time_t *timer);
struct tm *localtime(const time_t *timer);
struct tm *gmtime_r(const time_t *timer, struct tm *result);
struct tm *localtime_r(const time_t *timer, struct tm *result);
size_t strftime(char *s, size_t max, const char *format, const struct tm *tm);
char *ctime(const time_t *timer);
char *asctime(const struct tm *tm);

#endif
