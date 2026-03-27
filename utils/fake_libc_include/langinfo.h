#ifndef _FAKE_LANGINFO_H
#define _FAKE_LANGINFO_H

#include "_fake_defines.h"
#include "_fake_typedefs.h"

typedef int nl_item;

#ifndef CODESET
#define CODESET 1
#endif

char *nl_langinfo(nl_item item);

#endif
