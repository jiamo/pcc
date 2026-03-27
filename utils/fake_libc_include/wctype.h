#ifndef _FAKE_WCTYPE_H
#define _FAKE_WCTYPE_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

int iswalnum(wint_t);
int iswalpha(wint_t);
int iswcntrl(wint_t);
int iswctype(wint_t, wctype_t);
int iswgraph(wint_t);
int iswlower(wint_t);
int iswprint(wint_t);
int iswspace(wint_t);
int iswupper(wint_t);
wint_t towlower(wint_t);
wint_t towupper(wint_t);
wctype_t wctype(const char *);

#endif
