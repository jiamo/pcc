#ifndef PCC_FAKE_NUMPY_UFUNCOBJECT_H
#define PCC_FAKE_NUMPY_UFUNCOBJECT_H

#include <numpy/arrayobject.h>

typedef void (*PyUFuncGenericFunction)(
    char **args,
    const npy_intp *dimensions,
    const npy_intp *steps,
    void *data
);

#ifndef PCC_NUMPY_UFUNC_API_CAPSULE
#define PCC_NUMPY_UFUNC_API_CAPSULE "pccnpapi._UFUNC_API"
#endif

static void **PyUFunc_API = NULL;

static inline int
_import_umath(void)
{
    if (PyUFunc_API != NULL) {
        return 0;
    }
    PyUFunc_API = (void **)PyCapsule_Import(PCC_NUMPY_UFUNC_API_CAPSULE, 0);
    return PyUFunc_API == NULL ? -1 : 0;
}

#define import_umath() \
    do { \
        if (_import_umath() < 0) return NULL; \
    } while (0)

#define import_umath1(ret) \
    do { \
        if (_import_umath() < 0) return (ret); \
    } while (0)

#define PyUFunc_FromFuncAndData \
    (*(PyObject *(*)(PyUFuncGenericFunction *, void **, char *, int, int, int, int, const char *, const char *, int))PyUFunc_API[0])

#endif
