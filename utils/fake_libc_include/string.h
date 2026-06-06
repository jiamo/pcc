#include "_fake_defines.h"
#include "_fake_typedefs.h"

/* C11 Annex K bounded-clear support types. Declared locally (not in
 * _fake_typedefs.h) so string.h stays self-contained for secure-clear tests. */
#ifndef __rsize_t_defined
#define __rsize_t_defined
typedef size_t rsize_t;
#endif
#ifndef __errno_t_defined
#define __errno_t_defined
typedef int errno_t;
#endif

void *memset(void *s, int c, size_t n);
void *memcpy(void *dest, const void *src, size_t n);
int strcmp(const char *s1, const char *s2);
/* Secret-clearing / sensitive-data scrub APIs (CWE-14): the compiler lowers
 * these to volatile llvm.memset operations so dead-store elimination cannot
 * remove the fill of a buffer that is about to die. memset_s is bounded by
 * smax and reports a non-zero errno_t on n > smax. */
void explicit_bzero(void *s, size_t n);
errno_t memset_s(void *s, rsize_t smax, int c, rsize_t n);
