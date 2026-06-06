#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef _PCC_FAKE_SYS_UTSNAME_H
#define _PCC_FAKE_SYS_UTSNAME_H

/* Match the target libc ABI closely enough for pcc-emitted callers of
 * uname(2). Darwin uses 256-byte fields; Linux and the other supported POSIX
 * targets use the traditional 65-byte UTS fields. */
#if defined(__APPLE__)
#define PCC_UTSNAME_FIELD_LEN 256
#else
#define PCC_UTSNAME_FIELD_LEN 65
#endif

struct utsname {
    char sysname[PCC_UTSNAME_FIELD_LEN];
    char nodename[PCC_UTSNAME_FIELD_LEN];
    char release[PCC_UTSNAME_FIELD_LEN];
    char version[PCC_UTSNAME_FIELD_LEN];
    char machine[PCC_UTSNAME_FIELD_LEN];
#if defined(__linux__)
    char domainname[PCC_UTSNAME_FIELD_LEN];
#endif
};

int uname(struct utsname *name);

#endif
