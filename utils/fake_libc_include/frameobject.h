#ifndef PCC_FAKE_FRAMEOBJECT_H
#define PCC_FAKE_FRAMEOBJECT_H

/* CPython frameobject.h surface (numpy uses it lightly for tracebacks/line
 * numbers). The frame object is opaque here; impls come from the pcc runtime. */
#include <Python.h>

typedef struct _frame PyFrameObject;

int PyFrame_GetLineNumber(PyFrameObject *frame);
PyObject *PyFrame_GetCode(PyFrameObject *frame);
PyObject *PyFrame_GetBack(PyFrameObject *frame);
int PyFrame_FastToLocalsWithError(PyFrameObject *frame);
void PyFrame_FastToLocals(PyFrameObject *frame);

#endif /* PCC_FAKE_FRAMEOBJECT_H */
