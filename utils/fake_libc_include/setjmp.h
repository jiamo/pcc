#ifndef _FAKE_SETJMP_H
#define _FAKE_SETJMP_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

/*
 * Keep jmp_buf as an opaque storage buffer instead of collapsing it to
 * a scalar. We only need enough size/alignment for codegen and runtime
 * interop with the host setjmp/longjmp implementation.
 */
/* Match platform jmp_buf size for correct struct layouts */
#if defined(__aarch64__) || defined(__arm64__)
/* macOS arm64: int[(14+8+2)*2] = int[48] = 192 bytes */
typedef int jmp_buf[48];
typedef int sigjmp_buf[49];
#else
/* x86_64 / generic: conservative estimate */
typedef long jmp_buf[25];
typedef long sigjmp_buf[26];
#endif

int setjmp(jmp_buf env);
void longjmp(jmp_buf env, int val);
int _setjmp(jmp_buf env);
void _longjmp(jmp_buf env, int val);

#endif
