#ifndef _FAKE_SYS_WAIT_H
#define _FAKE_SYS_WAIT_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#define WIFEXITED(status) (((status) & 0x7f) == 0)
#define WEXITSTATUS(status) (((status) >> 8) & 0xff)
#define WIFSIGNALED(status) (((status) & 0x7f) != 0 && ((status) & 0x7f) != 0x7f)
#define WTERMSIG(status) ((status) & 0x7f)

#endif
