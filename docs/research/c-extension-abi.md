# pcc native C-extension ABI memo

Goal item: T5 native C-extension ABI memo. This is a design memo only; it does
not implement a dynamic extension loader.

## Scope

The first ABI target is a narrow CPython-compatible shim sufficient for small
extensions that expose functions and opaque capsules. pcc should not promise
full CPython ABI parity before object layout, GIL/threading, and exception
state are stabilized.

## Minimum public surface

```c
typedef struct PyObject PyObject;
typedef struct PyTypeObject PyTypeObject;

void Py_INCREF(PyObject *);
void Py_DECREF(PyObject *);

PyObject *PyLong_FromLongLong(long long);
long long PyLong_AsLongLong(PyObject *);
PyObject *PyUnicode_FromString(const char *);
const char *PyUnicode_AsUTF8(PyObject *);

PyObject *PyCapsule_New(void *ptr, const char *name, void (*destructor)(PyObject *));
void *PyCapsule_GetPointer(PyObject *, const char *name);

PyObject *PyErr_Format(PyObject *type, const char *fmt, ...);
int PyErr_Occurred(void);
void PyErr_Clear(void);
```

## Ownership

`Py_INCREF` / `Py_DECREF` map to `pcc_gc_retain` / `pcc_gc_release`. Extensions
must not assume raw refcount fields are visible or stable.

## Exception state

Extension calls participate in pcc's return-code exception model: C returns
`NULL`, pcc TLS stores the exception, and codegen checks after calls.

## Loader plan

Phase 1: static link only. Generated glue registers extension modules through a
compile-time table.

Phase 2: `dlopen` / `dlsym` behind `PCC_ENABLE_NATIVE_EXTENSIONS=1`.

Phase 3: packaging/wheel integration.

## Required tests before implementation claim

- static extension exports `add(a, b)`;
- extension raises ValueError and pcc catches it;
- capsule creates/destroys native pointer;
- refcount/GC stress under backends 0..4;
- import failure reports diagnostic code and phase.
