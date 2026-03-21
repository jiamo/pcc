#ifndef _FAKE_SIGNAL_H
#define _FAKE_SIGNAL_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

struct sigaction {
    void (*sa_handler)(int);
    sigset_t sa_mask;
    int sa_flags;
};

int sigemptyset(sigset_t *set);
int sigaction(int signum, const struct sigaction *act, struct sigaction *oldact);

#endif
