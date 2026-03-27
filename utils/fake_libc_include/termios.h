#ifndef _FAKE_TERMIOS_H
#define _FAKE_TERMIOS_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

typedef unsigned int tcflag_t;
typedef unsigned char cc_t;
typedef unsigned long speed_t;

#ifndef NCCS
#define NCCS 20
#endif

struct termios {
    tcflag_t c_iflag;
    tcflag_t c_oflag;
    tcflag_t c_cflag;
    tcflag_t c_lflag;
    cc_t c_cc[NCCS];
    speed_t c_ispeed;
    speed_t c_ospeed;
};

#define VINTR 0
#define VQUIT 1
#define VERASE 2
#define VKILL 3
#define VEOF 4
#define VEOL 5
#define VEOL2 6
#define VWERASE 7
#define VREPRINT 8
#define VSUSP 9
#define VDSUSP 10
#define VSTART 11
#define VSTOP 12
#define VLNEXT 13
#define VDISCARD 14
#define VSTATUS 15
#define VMIN 16
#define VTIME 17

#ifndef _POSIX_VDISABLE
#define _POSIX_VDISABLE 0xff
#endif

#define TCSANOW 0
#define TCSADRAIN 1
#define TCOOFF 0
#define TCOON 1

int tcdrain(int fd);
int tcflow(int fd, int action);
int tcgetattr(int fd, struct termios *termios_p);
int tcsetattr(int fd, int optional_actions, const struct termios *termios_p);

#endif
