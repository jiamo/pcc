#ifndef _FAKE_DLFCN_H
#define _FAKE_DLFCN_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef RTLD_NOW
#define RTLD_NOW 2
#endif
#ifndef RTLD_LOCAL
#define RTLD_LOCAL 4
#endif
#ifndef RTLD_GLOBAL
#define RTLD_GLOBAL 8
#endif

void *dlopen(const char *path, int mode);
int dlclose(void *handle);
void *dlsym(void *handle, const char *symbol);
char *dlerror(void);

#endif
