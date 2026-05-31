/* pcc fake CPython <datetime.h>: the capsule-based datetime C-API surface that
 * numpy's datetime.c references (datetime64 <-> Python datetime interop). The
 * struct field NAMES match CPython so numpy compiles; PyDateTimeAPI is NULL on
 * the no-libpython path (the import-time PyDateTime_IMPORT is a no-op), so the
 * interop is DEGRADED at runtime — numpy's own datetime64 dtype is independent. */
#ifndef PYDATETIME_H
#define PYDATETIME_H
#include <Python.h>

typedef struct {
    PyTypeObject *DateType;
    PyTypeObject *DateTimeType;
    PyTypeObject *TimeType;
    PyTypeObject *DeltaType;
    PyTypeObject *TZInfoType;
    PyObject *TimeZone_UTC;
    PyObject *(*Date_FromDate)(int, int, int, PyTypeObject *);
    PyObject *(*DateTime_FromDateAndTime)(int, int, int, int, int, int, int,
                                          PyObject *, PyTypeObject *);
    PyObject *(*Time_FromTime)(int, int, int, int, PyObject *, PyTypeObject *);
    PyObject *(*Delta_FromDelta)(int, int, int, int, PyTypeObject *);
    PyObject *(*TimeZone_FromTimeZone)(PyObject *offset, PyObject *name);
    PyObject *(*DateTime_FromTimestamp)(PyObject *, PyObject *, PyObject *);
    PyObject *(*Date_FromTimestamp)(PyObject *, PyObject *);
    PyObject *(*DateTime_FromDateAndTimeAndFold)(int, int, int, int, int, int,
                                                 int, PyObject *, int,
                                                 PyTypeObject *);
    PyObject *(*Time_FromTimeAndFold)(int, int, int, int, PyObject *, int,
                                      PyTypeObject *);
} PyDateTime_CAPI;

extern PyDateTime_CAPI *PyDateTimeAPI;

#define PyDateTime_IMPORT ((void)0)
#define PyDateTime_TimeZone_UTC (PyDateTimeAPI->TimeZone_UTC)

#define PyDate_Check(op) PyObject_TypeCheck((op), PyDateTimeAPI->DateType)
#define PyDateTime_Check(op) PyObject_TypeCheck((op), PyDateTimeAPI->DateTimeType)
#define PyTime_Check(op) PyObject_TypeCheck((op), PyDateTimeAPI->TimeType)
#define PyDelta_Check(op) PyObject_TypeCheck((op), PyDateTimeAPI->DeltaType)
#define PyTZInfo_Check(op) PyObject_TypeCheck((op), PyDateTimeAPI->TZInfoType)

#define PyDate_FromDate(y, m, d) \
    PyDateTimeAPI->Date_FromDate((y), (m), (d), PyDateTimeAPI->DateType)
#define PyDateTime_FromDateAndTime(y, mo, d, h, mi, s, us)               \
    PyDateTimeAPI->DateTime_FromDateAndTime((y), (mo), (d), (h), (mi),   \
        (s), (us), Py_None, PyDateTimeAPI->DateTimeType)
#define PyDelta_FromDSU(d, s, us)                                        \
    PyDateTimeAPI->Delta_FromDelta((d), (s), (us), 1, PyDateTimeAPI->DeltaType)
#define PyDelta_FromDelta(d, s, us, normalize)                           \
    PyDateTimeAPI->Delta_FromDelta((d), (s), (us), (normalize),          \
        PyDateTimeAPI->DeltaType)

#endif /* PYDATETIME_H */
