#ifndef _FAKE_STRINGS_H
#define _FAKE_STRINGS_H

#include "string.h"

int bcmp(const void *s1, const void *s2, size_t n);
void bcopy(const void *src, void *dst, size_t n);
void bzero(void *s, size_t n);
int strcasecmp(const char *s1, const char *s2);
int strncasecmp(const char *s1, const char *s2, size_t n);

#endif
