/* pcc fake CPython <pyerrors.h>: the real Python.h is self-contained, so this just
 * pulls it in (numpy includes the sub-header directly in some files). */
#ifndef Py_pyerrors_H
#define Py_pyerrors_H
#include <Python.h>
#endif
