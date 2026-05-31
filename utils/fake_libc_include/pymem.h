#ifndef PCC_FAKE_PYMEM_H
#define PCC_FAKE_PYMEM_H

/* CPython pymem.h surface (allocator API). Implementations come from the pcc
 * C-API runtime; identical re-declaration is harmless if Python.h also has it. */
#include <Python.h>

void *PyMem_Malloc(size_t size);
void *PyMem_Calloc(size_t nelem, size_t elsize);
void *PyMem_Realloc(void *ptr, size_t new_size);
void PyMem_Free(void *ptr);
void *PyMem_RawMalloc(size_t size);
void *PyMem_RawCalloc(size_t nelem, size_t elsize);
void *PyMem_RawRealloc(void *ptr, size_t new_size);
void PyMem_RawFree(void *ptr);

#define PyMem_New(type, n) ((type *) PyMem_Malloc((n) * sizeof(type)))
#define PyMem_Resize(p, type, n) ((p) = (type *) PyMem_Realloc((p), (n) * sizeof(type)))
#define PyMem_Del PyMem_Free
/* legacy upper-case spellings still used by some extensions */
#define PyMem_MALLOC PyMem_Malloc
#define PyMem_REALLOC PyMem_Realloc
#define PyMem_FREE PyMem_Free

#endif /* PCC_FAKE_PYMEM_H */
