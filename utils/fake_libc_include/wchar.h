#ifndef _FAKE_WCHAR_H
#define _FAKE_WCHAR_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

int mbtowc(wchar_t *, const char *, size_t);
int wctomb(char *, wchar_t);
size_t mbrlen(const char *, size_t, mbstate_t *);
size_t mbrtowc(wchar_t *, const char *, size_t, mbstate_t *);
size_t mbsrtowcs(wchar_t *, const char **, size_t, mbstate_t *);
size_t mbstowcs(wchar_t *, const char *, size_t);
size_t wcrtomb(char *, wchar_t, mbstate_t *);
size_t wcsrtombs(char *, const wchar_t **, size_t, mbstate_t *);
size_t wcstombs(char *, const wchar_t *, size_t);
int wcwidth(wchar_t);
int wcswidth(const wchar_t *, size_t);
wchar_t *wmemchr(const wchar_t *, wchar_t, size_t);
wchar_t *wmemcpy(wchar_t *, const wchar_t *, size_t);
wchar_t *wmemmove(wchar_t *, const wchar_t *, size_t);
int wmemcmp(const wchar_t *, const wchar_t *, size_t);

#endif
