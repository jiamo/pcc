#ifndef _FAKE_SIGNAL_H
#define _FAKE_SIGNAL_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef SIG_BLOCK
#define SIG_BLOCK 1
#endif

#ifndef SIG_UNBLOCK
#define SIG_UNBLOCK 2
#endif

#ifndef SIG_SETMASK
#define SIG_SETMASK 3
#endif

#ifndef SIGHUP
#define SIGHUP 1
#endif

#ifndef SIGINT
#define SIGINT 2
#endif

#ifndef SIGQUIT
#define SIGQUIT 3
#endif

#ifndef SIGFPE
#define SIGFPE 8
#endif

#ifndef SIGALRM
#define SIGALRM 14
#endif

#ifndef SIGTERM
#define SIGTERM 15
#endif

#ifndef SIGTSTP
#define SIGTSTP 18
#endif

#ifndef SIGTTIN
#define SIGTTIN 21
#endif

#ifndef SIGTTOU
#define SIGTTOU 22
#endif

#ifndef SIGWINCH
#define SIGWINCH 28
#endif

#ifndef SIG_DFL
#define SIG_DFL ((void (*)(int))0)
#endif

#ifndef SIG_IGN
#define SIG_IGN ((void (*)(int))1)
#endif

#ifndef SIG_ERR
#define SIG_ERR ((void (*)(int))-1)
#endif

struct sigaction {
    void (*sa_handler)(int);
    sigset_t sa_mask;
    int sa_flags;
};

int sigaddset(sigset_t *set, int signo);
int sigdelset(sigset_t *set, int signo);
int sigemptyset(sigset_t *set);
int sigismember(const sigset_t *set, int signo);
int sigprocmask(int how, const sigset_t *set, sigset_t *oldset);
int sigaction(int signum, const struct sigaction *act, struct sigaction *oldact);
int sigsuspend(const sigset_t *sigmask);
int kill(pid_t pid, int sig);
void (*signal(int sig, void (*func)(int)))(int);

#endif
