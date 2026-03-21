#ifndef _FAKE_STDIO_H
#define _FAKE_STDIO_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

int getc_unlocked(FILE *stream);
void flockfile(FILE *stream);
void funlockfile(FILE *stream);
int fileno(FILE *stream);
int fseeko(FILE *stream, off_t offset, int whence);
off_t ftello(FILE *stream);
FILE *popen(const char *command, const char *mode);
int pclose(FILE *stream);

#endif
