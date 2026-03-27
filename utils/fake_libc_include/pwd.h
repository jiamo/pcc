#ifndef _PCC_FAKE_PWD_H
#define _PCC_FAKE_PWD_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

struct passwd {
    char *pw_name;
    char *pw_passwd;
    uid_t pw_uid;
    gid_t pw_gid;
    char *pw_gecos;
    char *pw_dir;
    char *pw_shell;
};

struct passwd *getpwent(void);
struct passwd *getpwnam(const char *);
struct passwd *getpwuid(uid_t);
void setpwent(void);
void endpwent(void);

#endif
