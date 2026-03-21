#ifndef _FAKE_UNISTD_H
#define _FAKE_UNISTD_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

int isatty(int fd);
int mkstemp(char *template);

#endif
