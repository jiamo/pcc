#ifndef _FAKE_SYS_SELECT_H
#define _FAKE_SYS_SELECT_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"
#include <sys/time.h>
#include <time.h>

#ifndef FD_SETSIZE
#define FD_SETSIZE 32
#endif

typedef struct fd_set {
    int fds_bits[1];
} fd_set;

#define FD_ZERO(fdsetp) ((void)((fdsetp)->fds_bits[0] = 0))
#define FD_SET(fd, fdsetp) ((void)((fdsetp)->fds_bits[0] |= (1 << (fd))))
#define FD_CLR(fd, fdsetp) ((void)((fdsetp)->fds_bits[0] &= ~(1 << (fd))))
#define FD_ISSET(fd, fdsetp) (((fdsetp)->fds_bits[0] & (1 << (fd))) != 0)

int select(int nfds, fd_set *readfds, fd_set *writefds, fd_set *exceptfds, struct timeval *timeout);
int pselect(
    int nfds,
    fd_set *readfds,
    fd_set *writefds,
    fd_set *exceptfds,
    const struct timespec *timeout,
    const sigset_t *sigmask
);

#endif
