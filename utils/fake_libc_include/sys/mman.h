#include "_fake_defines.h"
#include "_fake_typedefs.h"

#ifndef PROT_NONE
#define PROT_NONE 0x0
#endif
#ifndef PROT_READ
#define PROT_READ 0x1
#endif
#ifndef PROT_WRITE
#define PROT_WRITE 0x2
#endif
#ifndef PROT_EXEC
#define PROT_EXEC 0x4
#endif

#ifndef MAP_SHARED
#define MAP_SHARED 0x0001
#endif
#ifndef MAP_PRIVATE
#define MAP_PRIVATE 0x0002
#endif
#ifndef MAP_FAILED
#define MAP_FAILED ((void *)-1)
#endif
#ifndef MAP_ANONYMOUS
#if defined(__APPLE__)
#define MAP_ANONYMOUS 0x1000
#else
#define MAP_ANONYMOUS 0x0020
#endif
#endif
#ifndef MAP_ANON
#define MAP_ANON MAP_ANONYMOUS
#endif

void *mmap(void *addr, size_t len, int prot, int flags, int fd, off_t offset);
int munmap(void *addr, size_t len);
int mlock(const void *addr, size_t len);
int munlock(const void *addr, size_t len);
void *mremap(void *addr, size_t old_size, size_t new_size, int flags, ...);
