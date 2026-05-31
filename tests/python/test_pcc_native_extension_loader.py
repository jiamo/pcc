from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if not (REPO / "utils" / "fake_libc_include" / "Python.h").exists():
    REPO = REPO / "pcc"


def _compile_extension(tmp_path: Path, module_name: str, source: str) -> Path:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    src = tmp_path / f"{module_name}.c"
    src.write_text(source, encoding="utf-8")
    out = site / f"{module_name}.so"
    cmd = [
        os.environ.get("CC", "cc"),
        "-shared",
        "-fPIC",
        "-I",
        str(REPO / "utils" / "fake_libc_include"),
        "-I",
        str(REPO / "pcc" / "py_runtime" / "include"),
        str(src),
        "-o",
        str(out),
    ]
    if sys.platform == "darwin":
        cmd.extend(["-undefined", "dynamic_lookup"])
    subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=30)
    return site


def _compile_demo_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *demo_add(PyObject *self, PyObject *args) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "ll", &a, &b)) return NULL;\n'
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyMethodDef DemoMethods[] = {\n"
        '    {"add", demo_add, METH_VARARGS, "add two ints"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef demomodule = {\n"
        '    PyModuleDef_HEAD_INIT, "demo", NULL, -1, DemoMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_demo(void) {\n"
        "    return PyModule_Create(&demomodule);\n"
        "}\n",
    )


def _compile_typedemo_extension(tmp_path: Path) -> Path:
    # A minimal custom-type extension exercising the PyType_Ready bridge: the
    # static PyTypeObject is registered via PyType_Ready, exposed on the module,
    # and instantiated through tp_new=PyType_GenericNew. This is the reduced
    # numpy type-registration pattern; it must import + instantiate under strict
    # no-libpython (--python-libpython=off --backend self).
    return _compile_extension(
        tmp_path,
        "typedemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "typedef struct { PyObject_HEAD long val; } FooObject;\n"
        "\n"
        "static PyTypeObject FooType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "typedemo.Foo",\n'
        "    .tp_basicsize = sizeof(FooObject),\n"
        "    .tp_flags = Py_TPFLAGS_DEFAULT,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "\n"
        "static PyModuleDef typemodule = {\n"
        '    PyModuleDef_HEAD_INIT, "typedemo", NULL, -1, NULL,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_typedemo(void) {\n"
        "    if (PyType_Ready(&FooType) < 0) return NULL;\n"
        "    PyObject *m = PyModule_Create(&typemodule);\n"
        "    if (m == NULL) return NULL;\n"
        "    Py_INCREF((PyObject *)&FooType);\n"
        '    PyModule_AddObject(m, "Foo", (PyObject *)&FooType);\n'
        "    return m;\n"
        "}\n",
    )


def _compile_typecheck_extension(tmp_path: Path) -> Path:
    # Exercises the builtin-tag Py_TYPE mapping: Py_TYPE of pcc builtin objects
    # must resolve to the matching &PyXxx_Type token the shim provides (numpy
    # does this constantly). Returns a bitset so a partial mapping is visible.
    return _compile_extension(
        tmp_path,
        "tcdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *tc_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *i = PyLong_FromLong(5);\n"
        '    PyObject *s = PyUnicode_FromString("x");\n'
        "    long ok = (Py_TYPE(i) == &PyLong_Type) ? 1 : 0;\n"
        "    ok += (Py_TYPE(s) == &PyUnicode_Type) ? 2 : 0;\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", tc_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "tcdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_tcdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_subtype_extension(tmp_path: Path) -> Path:
    # Exercises PyObject_TypeCheck across a tp_base inheritance chain (numpy's
    # scalar/array type hierarchy relies on subtype checks). check() returns 7
    # when: derived is-a base, derived is-a derived, base is-NOT-a derived.
    return _compile_extension(
        tmp_path,
        "stdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long v; } BaseObj;\n"
        "typedef struct { BaseObj base; long w; } DerivedObj;\n"
        "static PyTypeObject BaseType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo.Base", .tp_basicsize = sizeof(BaseObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject DerivedType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo.Derived", .tp_basicsize = sizeof(DerivedObj),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_base = &BaseType,\n"
        "    .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *st_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *d = PyType_GenericNew(&DerivedType, NULL, NULL);\n"
        "    PyObject *b = PyType_GenericNew(&BaseType, NULL, NULL);\n"
        "    long r = 0;\n"
        "    if (PyObject_TypeCheck(d, &BaseType)) r += 1;\n"
        "    if (PyObject_TypeCheck(d, &DerivedType)) r += 2;\n"
        "    if (!PyObject_TypeCheck(b, &DerivedType)) r += 4;\n"
        "    if (PyType_IsSubtype(&DerivedType, &BaseType)) r += 8;\n"
        "    if (!PyType_IsSubtype(&BaseType, &DerivedType)) r += 16;\n"
        "    BaseObj *n = PyObject_New(BaseObj, &BaseType);\n"
        "    if (n != NULL) r += 32;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", st_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "stdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_stdemo(void) {\n"
        "    if (PyType_Ready(&BaseType) < 0) return NULL;\n"
        "    if (PyType_Ready(&DerivedType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_linksym_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the batch 7-9 link symbols added to
    # py_capi_shim.c for numpy's no-libpython link surface. These were
    # link-validated (the shim compiles + ext-abi gate) but not exercised at
    # runtime; this locks their BEHAVIOR under strict no-libpython. check()
    # returns a bitset; 31 = all five correct:
    #   1  PyTuple_GetSlice((10,20,30,40)[1:3]) -> (20,30)
    #   2  PyObject_AsFileDescriptor(PyLong(7)) -> 7
    #   4  Py_GenericAlias(origin=PyLong(99), NULL) -> origin (value 99 preserved)
    #   8  PyUnicode_AsLatin1String("Ab") -> b"Ab"
    #   16 PyErr_NormalizeException(NULL triple) -> no-op, error state stays clear
    return _compile_extension(
        tmp_path,
        "lsdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *ls_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *t = PyTuple_New(4);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyTuple_SetItem(t, 2, PyLong_FromLong(30));\n"
        "    PyTuple_SetItem(t, 3, PyLong_FromLong(40));\n"
        "    PyObject *sl = PyTuple_GetSlice(t, 1, 3);\n"
        "    if (sl != NULL && PyTuple_Size(sl) == 2 &&\n"
        "        PyLong_AsLong(PyTuple_GetItem(sl, 0)) == 20 &&\n"
        "        PyLong_AsLong(PyTuple_GetItem(sl, 1)) == 30) r += 1;\n"
        "    PyObject *fd = PyLong_FromLong(7);\n"
        "    if (PyObject_AsFileDescriptor(fd) == 7) r += 2;\n"
        "    PyObject *origin = PyLong_FromLong(99);\n"
        "    PyObject *ga = Py_GenericAlias(origin, Py_None);\n"
        "    if (ga != NULL && PyLong_AsLong(ga) == 99) r += 4;\n"
        '    PyObject *u = PyUnicode_FromString("Ab");\n'
        "    PyObject *b = PyUnicode_AsLatin1String(u);\n"
        "    if (b != NULL && PyBytes_Size(b) == 2) {\n"
        "        const char *bs = PyBytes_AsString(b);\n"
        "        if (bs != NULL && bs[0] == 'A' && bs[1] == 'b') r += 8;\n"
        "    }\n"
        "    PyObject *e = NULL, *v = NULL, *tb = NULL;\n"
        "    PyErr_NormalizeException(&e, &v, &tb);\n"
        "    if (!PyErr_Occurred()) r += 16;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", ls_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "lsdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_lsdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_contextvar_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the batch 10 single-context contextvar
    # (PyContextVar_New + PyContextVar_Get) added to py_capi_shim.c. numpy.errstate
    # creates a ContextVar AT import, so this is a real (not stub) object; check()
    # verifies correct CPython single-context Get semantics:
    #   1  Get(cv, NULL, &v) on an unset var -> the var's own default (5)
    #   2  Get(cv, PyLong(9), &v) on an unset var -> the explicit default arg (9)
    #   4  after Set(cv, 7) (batch 14): Get(cv, NULL, &v) -> the set value (7),
    #      and Set returns a non-NULL token (numpy set-and-discards it).
    # 7 = all correct.
    return _compile_extension(
        tmp_path,
        "cvdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *cv_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *cv = PyContextVar_New("x", PyLong_FromLong(5));\n'
        "    if (cv == NULL) return PyLong_FromLong(-1);\n"
        "    PyObject *v = NULL;\n"
        "    if (PyContextVar_Get(cv, NULL, &v) == 0 && v != NULL &&\n"
        "        PyLong_AsLong(v) == 5) r += 1;\n"
        "    PyObject *v2 = NULL;\n"
        "    if (PyContextVar_Get(cv, PyLong_FromLong(9), &v2) == 0 && v2 != NULL &&\n"
        "        PyLong_AsLong(v2) == 9) r += 2;\n"
        "    PyObject *tok = PyContextVar_Set(cv, PyLong_FromLong(7));\n"
        "    PyObject *v3 = NULL;\n"
        "    if (tok != NULL && PyContextVar_Get(cv, NULL, &v3) == 0 && v3 != NULL &&\n"
        "        PyLong_AsLong(v3) == 7) r += 4;\n"
        "    if (tok != NULL) Py_DECREF(tok);\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", cv_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "cvdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_cvdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_vaparse_extension(tmp_path: Path) -> Path:
    # Behavioral regression for the PyArg_ParseTupleAndKeywords -> (new)
    # PyArg_VaParseTupleAndKeywords va_list-core delegation (the canonical CPython
    # refactor; PyArg_VaParseTupleAndKeywords is the symbol numpy's C core
    # references). The extension does NOT call va_start itself (the curated
    # fake-libc dir's stdarg shim is build-only); instead it calls the `...`
    # wrapper, which now routes through the va_list core. kwargs=NULL because the
    # no-libpython loader dispatches METH_VARARGS (positional). check(10, 20)
    # parses "ll" via the core and returns a*100+b = 1020.
    return _compile_extension(
        tmp_path,
        "vademo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *va_check(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    long a = 0, b = 0;\n"
        '    char *kwlist[] = {"a", "b", NULL};\n'
        '    if (!PyArg_ParseTupleAndKeywords(args, NULL, "ll", kwlist, &a, &b))\n'
        "        return PyLong_FromLong(-1);\n"
        "    return PyLong_FromLong(a * 100 + b);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", va_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "vademo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_vademo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_getbuiltins_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyEval_GetBuiltins (numpy's sole consumer is
    # npy_PyFile_OpenFile in npy_3kcompat.h: PyDict_GetItemString(
    # PyEval_GetBuiltins(), "open"), which returns NULL gracefully if absent).
    # check() returns 7:
    #   1  PyEval_GetBuiltins() is non-NULL (a real dict, not NULL/fake)
    #   2  numpy's pattern: an absent key ("open") -> NULL, no crash (import-safe)
    #   4  it is a real mutable dict AND a persistent singleton (a second call
    #      returns the same pointer; set/get roundtrips) — i.e. borrowed-ref
    #      semantics, not a throwaway.
    return _compile_extension(
        tmp_path,
        "ebdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *eb_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *b = PyEval_GetBuiltins();\n"
        "    if (b != NULL) r += 1;\n"
        '    if (PyDict_GetItemString(b, "open") == NULL) r += 2;\n'
        '    PyDict_SetItemString(b, "answer", PyLong_FromLong(42));\n'
        "    PyObject *b2 = PyEval_GetBuiltins();\n"
        '    PyObject *v = PyDict_GetItemString(b2, "answer");\n'
        "    if (b2 == b && v != NULL && PyLong_AsLong(v) == 42) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", eb_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "ebdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_ebdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_uniqueref_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyUnstable_Object_IsUniquelyReferenced (referenced
    # by the full numpy _core for safe in-place mutation). check() returns 3:
    #   1  a freshly-created object (refcount 1) is uniquely referenced
    #   2  after Py_INCREF (refcount 2) it is NOT uniquely referenced
    return _compile_extension(
        tmp_path,
        "urdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *ur_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *o = PyTuple_New(1);\n"
        "    PyTuple_SetItem(o, 0, PyLong_FromLong(0));\n"
        "    if (PyUnstable_Object_IsUniquelyReferenced(o)) r += 1;\n"
        "    Py_INCREF(o);\n"
        "    if (!PyUnstable_Object_IsUniquelyReferenced(o)) r += 2;\n"
        "    Py_DECREF(o);\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", ur_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "urdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_urdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_import_vectorcall_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyImport_Import (-> PyImport_ImportModule) and
    # PyVectorcall_Call (-> PyObject_Call), both batch 14. The caller imports the
    # sibling `demo` extension by name and calls demo.add(2, 3) via the vectorcall
    # entry. Compiled into the SAME site as demo (so it is importable). check()
    # returns 7:
    #   1  PyImport_Import("demo") is non-NULL
    #   2  demo.add is reachable
    #   4  PyVectorcall_Call(add, (2,3), NULL) == 5
    return _compile_extension(
        tmp_path,
        "imdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *im_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *m = PyImport_Import(PyUnicode_FromString("demo"));\n'
        "    if (m == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    PyObject *add = PyObject_GetAttrString(m, "add");\n'
        "    if (add == NULL) return PyLong_FromLong(r);\n"
        "    r += 2;\n"
        "    PyObject *t = PyTuple_New(2);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(2));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(3));\n"
        "    PyObject *res = PyVectorcall_Call(add, t, NULL);\n"
        "    if (res != NULL && PyLong_AsLong(res) == 5) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", im_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "imdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_imdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_sysflags_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PySys_GetObject (batch 15). Mirrors numpy's exact
    # import-time usage (npy_static_data.c:222): PySys_GetObject("flags") then
    # PyObject_GetAttrString(flags, "optimize"). check() returns 7:
    #   1  PySys_GetObject("flags") is non-NULL (a real namespace, not NULL)
    #   2  flags.optimize == 0 (accurate for pcc's no-O compile)
    #   4  an unprovided sys attr returns NULL (honest, not a fake object)
    return _compile_extension(
        tmp_path,
        "sfdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *sf_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *flags = PySys_GetObject("flags");\n'
        "    if (flags == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    PyObject *opt = PyObject_GetAttrString(flags, "optimize");\n'
        "    if (opt != NULL && PyLong_AsLong(opt) == 0) r += 2;\n"
        '    if (PySys_GetObject("nonexistent_attr_xyz") == NULL) r += 4;\n'
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", sf_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "sfdemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_sfdemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_genericgetdict_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyObject_GenericGetDict (batch 16). A module-level
    # METH_VARARGS function receives the module as `self`; its __dict__ is the
    # module namespace, which holds the function "check". check() returns 3:
    #   1  PyObject_GenericGetDict(self, NULL) is a non-NULL dict
    #   2  that dict contains this module's own "check" entry
    return _compile_extension(
        tmp_path,
        "gddemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *gd_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *d = PyObject_GenericGetDict(self, NULL);\n"
        "    if (d == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        '    if (PyDict_GetItemString(d, "check") != NULL) r += 2;\n'
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", gd_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "gddemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_gddemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_seqiter_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PySeqIter_New (batch 17). Build a real sequence
    # iterator over a tuple and drive it through the C-API PyIter_Next path.
    # check() returns 1 when iterating (10,20,30) yields exactly those 3 values
    # (sum 60), then NULL.
    return _compile_extension(
        tmp_path,
        "sidemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *si_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    PyObject *t = PyTuple_New(3);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyTuple_SetItem(t, 2, PyLong_FromLong(30));\n"
        "    PyObject *it = PySeqIter_New(t);\n"
        "    if (it == NULL) return PyLong_FromLong(-1);\n"
        "    long sum = 0; int count = 0; PyObject *item;\n"
        "    while ((item = PyIter_Next(it)) != NULL) {\n"
        "        sum += PyLong_AsLong(item);\n"
        "        if (++count > 10) break;\n"
        "    }\n"
        "    return PyLong_FromLong((sum == 60 && count == 3) ? 1 : 0);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", si_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "sidemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_sidemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_pymethod_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PyMethod_New (batch 17). A module-level function
    # gets the module as self; PyMethod_New binds the module's own "helper"
    # function to self and the result must be a real callable. check() returns 7:
    #   1  the func object is reachable
    #   2  PyMethod_New(func, self) is non-NULL
    #   4  the bound method is callable
    return _compile_extension(
        tmp_path,
        "mndemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *mn_helper(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args; return PyLong_FromLong(42);\n"
        "}\n"
        "static PyObject *mn_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *h = PyObject_GetAttrString(self, "helper");\n'
        "    if (h == NULL) return PyLong_FromLong(r);\n"
        "    r += 1;\n"
        "    PyObject *bm = PyMethod_New(h, self);\n"
        "    if (bm != NULL) r += 2;\n"
        "    if (bm != NULL && PyCallable_Check(bm)) r += 4;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"helper", mn_helper, METH_VARARGS, ""},\n'
        '    {"check", mn_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "mndemo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_mndemo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch18_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 18 host symbols (PyDict_Copy, PyDict_Merge,
    # PyUnicode_Format, PyObject_GenericGetAttr/SetAttr). check() returns 15:
    #   1  PyDict_Copy is a distinct dict carrying the original's key
    #   2  PyDict_Merge(override=1) adds the other dict's key
    #   4  PyUnicode_Format("%d", (42,)) == "42"
    #   8  PyObject_GenericSetAttr then GenericGetAttr round-trips on self
    return _compile_extension(
        tmp_path,
        "b18demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b18_check(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *d = PyDict_New();\n"
        '    PyDict_SetItemString(d, "a", PyLong_FromLong(1));\n'
        "    PyObject *c = PyDict_Copy(d);\n"
        '    if (c != NULL && c != d && PyDict_GetItemString(c, "a") != NULL) r += 1;\n'
        "    PyObject *e = PyDict_New();\n"
        '    PyDict_SetItemString(e, "b", PyLong_FromLong(2));\n'
        '    if (PyDict_Merge(d, e, 1) == 0 && PyDict_GetItemString(d, "b") != NULL) r += 2;\n'
        '    PyObject *fmt = PyUnicode_FromString("%d");\n'
        "    PyObject *ta = PyTuple_New(1);\n"
        "    PyTuple_SetItem(ta, 0, PyLong_FromLong(42));\n"
        "    PyObject *s = PyUnicode_Format(fmt, ta);\n"
        "    if (s != NULL) {\n"
        "        const char *cs = PyUnicode_AsUTF8(s);\n"
        "        if (cs != NULL && cs[0]=='4' && cs[1]=='2' && cs[2]=='\\0') r += 4;\n"
        "    }\n"
        '    PyObject *nm = PyUnicode_FromString("zz");\n'
        "    if (PyObject_GenericSetAttr(self, nm, PyLong_FromLong(9)) == 0) {\n"
        "        PyObject *got = PyObject_GenericGetAttr(self, nm);\n"
        "        if (got != NULL && PyLong_AsLong(got) == 9) r += 8;\n"
        "    }\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b18_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b18demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b18demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch19_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 19 host symbols (PySlice_New,
    # PySlice_GetIndicesEx, PyArg_UnpackTuple, PyDictProxy_New, PyObject_Init).
    # check() returns 31:
    #   1  slice(1,8,2) over len 10 -> start1 stop8 step2 slicelen4
    #   2  slice(None,None,None) over len 5 -> 0,5,1,5
    #   4  PyArg_UnpackTuple((10,20)) -> 10,20
    #   8  PyDictProxy_New(d) is a readable mapping
    #  16  PyObject_Init returns its op (passthrough on a fresh alloc)
    return _compile_extension(
        tmp_path,
        "b19demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b19_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    Py_ssize_t st, sp, stp, sln;\n"
        "    PyObject *sl = PySlice_New(PyLong_FromLong(1), PyLong_FromLong(8), PyLong_FromLong(2));\n"
        "    if (sl != NULL && PySlice_GetIndicesEx(sl, 10, &st, &sp, &stp, &sln) == 0\n"
        "        && st==1 && sp==8 && stp==2 && sln==4) r += 1;\n"
        "    PyObject *sl2 = PySlice_New(NULL, NULL, NULL);\n"
        "    if (sl2 != NULL && PySlice_GetIndicesEx(sl2, 5, &st, &sp, &stp, &sln) == 0\n"
        "        && st==0 && sp==5 && stp==1 && sln==5) r += 2;\n"
        "    PyObject *t = PyTuple_New(2);\n"
        "    PyTuple_SetItem(t, 0, PyLong_FromLong(10));\n"
        "    PyTuple_SetItem(t, 1, PyLong_FromLong(20));\n"
        "    PyObject *a = NULL, *b = NULL;\n"
        '    if (PyArg_UnpackTuple(t, "x", 2, 2, &a, &b) && a && b\n'
        "        && PyLong_AsLong(a)==10 && PyLong_AsLong(b)==20) r += 4;\n"
        "    PyObject *d = PyDict_New();\n"
        '    PyDict_SetItemString(d, "k", PyLong_FromLong(99));\n'
        "    PyObject *proxy = PyDictProxy_New(d);\n"
        '    if (proxy != NULL && PyDict_GetItemString(proxy, "k") != NULL) r += 8;\n'
        "    PyObject *fresh = _PyObject_New(&PyType_Type);\n"
        "    if (fresh != NULL && PyObject_Init(fresh, &PyType_Type) == fresh) r += 16;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b19_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b19demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b19demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch2021_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 20/21 host surface (PyUnicode_KIND,
    # PyUnicode_READ_CHAR, PyUnicode_1BYTE_DATA, PyNumber_Divmod, _Py_HashDouble) —
    # the symbols that let all 98 numpy _core files compile + link. check()==15:
    #   1  KIND==1 and READ_CHAR reads the right ASCII codepoints
    #   2  1BYTE_DATA is the UTF-8 byte buffer
    #   4  PyNumber_Divmod(17,5) == (3,2)
    #   8  _Py_HashDouble(NULL,2.0)==2 (CPython congruence: hash(2.0)==hash(2))
    return _compile_extension(
        tmp_path,
        "b21demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b21_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *s = PyUnicode_FromString("Abc");\n'
        "    if (PyUnicode_KIND(s)==1 && PyUnicode_READ_CHAR(s,0)=='A'\n"
        "        && PyUnicode_READ_CHAR(s,2)=='c') r += 1;\n"
        "    const char *data = (const char *)PyUnicode_1BYTE_DATA(s);\n"
        "    if (data != NULL && data[0]=='A' && data[1]=='b') r += 2;\n"
        "    PyObject *dm = PyNumber_Divmod(PyLong_FromLong(17), PyLong_FromLong(5));\n"
        "    if (dm != NULL && PyLong_AsLong(PyTuple_GetItem(dm,0))==3\n"
        "        && PyLong_AsLong(PyTuple_GetItem(dm,1))==2) r += 4;\n"
        "    if (_Py_HashDouble(NULL, 2.0) == 2) r += 8;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b21_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b21demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b21demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_batch22_extension(tmp_path: Path) -> Path:
    # Behavioral regression for batch 22 host symbols (PyLong_FromUnicodeObject,
    # PyFloat_FromString, PyLong_AsLongLongAndOverflow, PySlice_AdjustIndices) —
    # introduced by numpy's now-compiling C++ umath layer. check()==15.
    return _compile_extension(
        tmp_path,
        "b22demo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static PyObject *b22_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        '    PyObject *li = PyLong_FromUnicodeObject(PyUnicode_FromString("42"), 10);\n'
        "    if (li != NULL && PyLong_AsLong(li) == 42) r += 1;\n"
        '    PyObject *fl = PyFloat_FromString(PyUnicode_FromString("3.5"));\n'
        "    if (fl != NULL && PyFloat_AsDouble(fl) == 3.5) r += 2;\n"
        "    int ov = 5;\n"
        "    long long v = PyLong_AsLongLongAndOverflow(PyLong_FromLong(7), &ov);\n"
        "    if (v == 7 && ov == 0) r += 4;\n"
        "    Py_ssize_t st = 2, sp = 8;\n"
        "    Py_ssize_t sln = PySlice_AdjustIndices(10, &st, &sp, 2);\n"
        "    if (sln == 3 && st == 2 && sp == 8) r += 8;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", b22_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "b22demo", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_b22demo(void) { return PyModule_Create(&mod); }\n",
    )


def _compile_settype_extension(tmp_path: Path) -> Path:
    # Behavioral regression for Py_SET_TYPE -> pcc_capi_set_type (batch 23) — the
    # sole symbol that was undefined when link-testing numpy's full _core.
    # check() returns 3: a fresh Foo instance reports type Foo, then after
    # Py_SET_TYPE(o, &BarType) reports type Bar.
    return _compile_extension(
        tmp_path,
        "stdemo2",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "typedef struct { PyObject_HEAD long v; } FooObject;\n"
        "static PyTypeObject FooType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo2.Foo", .tp_basicsize = sizeof(FooObject),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyTypeObject BarType = {\n"
        "    PyVarObject_HEAD_INIT(NULL, 0)\n"
        '    .tp_name = "stdemo2.Bar", .tp_basicsize = sizeof(FooObject),\n'
        "    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,\n"
        "};\n"
        "static PyObject *st2_check(PyObject *self, PyObject *args) {\n"
        "    (void)self; (void)args;\n"
        "    long r = 0;\n"
        "    PyObject *o = PyType_GenericNew(&FooType, NULL, NULL);\n"
        "    if (o == NULL) return PyLong_FromLong(-1);\n"
        "    if (Py_TYPE(o) == &FooType) r += 1;\n"
        "    Py_SET_TYPE(o, &BarType);\n"
        "    if (Py_TYPE(o) == &BarType) r += 2;\n"
        "    return PyLong_FromLong(r);\n"
        "}\n"
        "static PyMethodDef M[] = {\n"
        '    {"check", st2_check, METH_VARARGS, ""}, {NULL, NULL, 0, NULL}\n'
        "};\n"
        "static PyModuleDef mod = {\n"
        '    PyModuleDef_HEAD_INIT, "stdemo2", NULL, -1, M,\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_stdemo2(void) {\n"
        "    if (PyType_Ready(&FooType) < 0) return NULL;\n"
        "    if (PyType_Ready(&BarType) < 0) return NULL;\n"
        "    return PyModule_Create(&mod);\n"
        "}\n",
    )


def _compile_multiphase_extension(tmp_path: Path) -> Path:
    # Behavioral regression for PEP 489 multi-phase init: PyInit returns
    # PyModuleDef_Init(&def) with a Py_mod_exec slot; the loader must build the
    # module + run the exec slot (where numpy's _multiarray_umath registers its
    # types). The exec slot here registers `answer` = 42. main asserts
    # mpdemo.answer == 42, proving the slot ran.
    return _compile_extension(
        tmp_path,
        "mpdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "static int mp_exec(PyObject *m) {\n"
        '    PyModule_AddObject(m, "answer", PyLong_FromLong(42));\n'
        "    return 0;\n"
        "}\n"
        "static PyModuleDef_Slot mp_slots[] = {\n"
        "    {Py_mod_exec, (void *)mp_exec}, {0, NULL}\n"
        "};\n"
        "static PyModuleDef mpmod = {\n"
        '    PyModuleDef_HEAD_INIT, "mpdemo", NULL, 0, NULL, mp_slots, NULL, NULL, NULL\n'
        "};\n"
        "PyMODINIT_FUNC PyInit_mpdemo(void) { return PyModuleDef_Init(&mpmod); }\n",
    )


def _compile_capsule_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "capsdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long secret = 37;\n"
        "static long context_secret = 11;\n"
        "static long destructor_secret = 13;\n"
        "static long destructor_hits = 0;\n"
        "\n"
        "static void capsdemo_destructor(PyObject *capsule) {\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.destruct");\n'
        "    if (ptr != NULL) destructor_hits += *ptr;\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_value(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capsdemo.secret", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (!PyCapsule_IsValid(capsule, "capsdemo.secret")) {\n'
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.secret");\n'
        "    Py_DECREF(capsule);\n"
        "    if (ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(*ptr);\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_context_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capsdemo.secret", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        "    if (!PyCapsule_CheckExact(capsule)) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "capsule check failed");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyCapsule_GetContext(capsule) != NULL || PyErr_Occurred()) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "unexpected initial capsule context");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyCapsule_SetContext(capsule, &context_secret) != 0) {\n"
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        "    long *ctx = (long *)PyCapsule_GetContext(capsule);\n"
        "    if (ctx == NULL || *ctx != 11) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "capsule context mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        '    if (PyCapsule_SetName(capsule, "capsdemo.renamed") != 0) {\n'
        "        Py_DECREF(capsule);\n"
        "        return NULL;\n"
        "    }\n"
        '    if (PyCapsule_IsValid(capsule, "capsdemo.secret")) {\n'
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "old capsule name still valid");\n'
        "        return NULL;\n"
        "    }\n"
        '    long *ptr = (long *)PyCapsule_GetPointer(capsule, "capsdemo.renamed");\n'
        "    if (ptr == NULL || *ptr != 37) {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "renamed capsule pointer mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        "    const char *name = PyCapsule_GetName(capsule);\n"
        "    if (name == NULL || name[9] != 'r') {\n"
        "        Py_DECREF(capsule);\n"
        '        PyErr_SetString(PyExc_RuntimeError, "renamed capsule name mismatch");\n'
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(capsule);\n"
        "    return PyLong_FromLong(*ptr + *ctx);\n"
        "}\n"
        "\n"
        "static PyObject *capsdemo_destructor_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    destructor_hits = 0;\n"
        '    PyObject *capsule = PyCapsule_New(&destructor_secret, "capsdemo.destruct", capsdemo_destructor);\n'
        "    if (capsule == NULL) return NULL;\n"
        "    Py_DECREF(capsule);\n"
        "    return PyLong_FromLong(destructor_hits);\n"
        "}\n"
        "\n"
        "static PyMethodDef CapsDemoMethods[] = {\n"
        '    {"value", capsdemo_value, METH_VARARGS, "read through a capsule"},\n'
        '    {"context_score", capsdemo_context_score, METH_VARARGS, "read capsule context"},\n'
        '    {"destructor_score", capsdemo_destructor_score, METH_VARARGS, "run capsule destructor"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef capsmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capsdemo", NULL, -1, CapsDemoMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capsdemo(void) {\n"
        "    return PyModule_Create(&capsmodule);\n"
        "}\n",
    )


def _compile_capsule_import_extensions(tmp_path: Path) -> Path:
    site = _compile_extension(
        tmp_path,
        "capiprov",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long secret = 73;\n"
        "static long capiprov_add_bias(long value) { return value + 78; }\n"
        "static void *api_table[] = {(void *)capiprov_add_bias, &secret};\n"
        "\n"
        "static PyMethodDef ProviderMethods[] = {\n"
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef providermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capiprov", NULL, -1, ProviderMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capiprov(void) {\n"
        "    PyObject *module = PyModule_Create(&providermodule);\n"
        "    if (module == NULL) return NULL;\n"
        '    PyObject *capsule = PyCapsule_New(&secret, "capiprov._API", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (PyModule_AddObject(module, "_API", capsule) != 0) return NULL;\n'
        '    capsule = PyCapsule_New(api_table, "capiprov._TABLE", NULL);\n'
        "    if (capsule == NULL) return NULL;\n"
        '    if (PyModule_AddObject(module, "_TABLE", capsule) != 0) return NULL;\n'
        "    return module;\n"
        "}\n",
    )
    _compile_extension(
        tmp_path,
        "capicons",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *capicons_value(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    long *ptr = (long *)PyCapsule_Import("capiprov._API", 0);\n'
        "    if (ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(*ptr);\n"
        "}\n"
        "\n"
        "static PyObject *capicons_table_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    void **api = (void **)PyCapsule_Import("capiprov._TABLE", 0);\n'
        "    if (api == NULL) return NULL;\n"
        "    long (*add_bias)(long) = (long (*)(long))api[0];\n"
        "    long *ptr = (long *)api[1];\n"
        "    if (add_bias == NULL || ptr == NULL) return NULL;\n"
        "    return PyLong_FromLong(add_bias(*ptr));\n"
        "}\n"
        "\n"
        "static PyMethodDef ConsumerMethods[] = {\n"
        '    {"value", capicons_value, METH_VARARGS, "read provider API capsule"},\n'
        '    {"table_score", capicons_table_score, METH_VARARGS, "read provider API table"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef consumermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "capicons", NULL, -1, ConsumerMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_capicons(void) {\n"
        "    return PyModule_Create(&consumermodule);\n"
        "}\n",
    )
    return site


def _compile_numpy_capi_provider_extensions(tmp_path: Path) -> Path:
    provider_source = (
        REPO / "utils" / "pcc_numpy_capi_provider" / "pccnpapi.c"
    ).read_text(encoding="utf-8")
    site = _compile_extension(
        tmp_path,
        "pccnpapi",
        provider_source,
    )
    _compile_extension(
        tmp_path,
        "pccnpcons",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <numpy/arrayobject.h>\n"
        "#include <numpy/ufuncobject.h>\n"
        "\n"
        "static PyObject *pccnpcons_table_shape_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    if (PyArray_API == NULL || PyUFunc_API == NULL) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "NumPy C API tables were not imported");\n'
        "        return NULL;\n"
        "    }\n"
        "    long count = 0;\n"
        "    for (int i = 0; i < 17; i++) {\n"
        "        if (PyArray_API[i] != NULL) count++;\n"
        "    }\n"
        "    if (PyUFunc_API[0] != NULL) count += 100;\n"
        "    return PyLong_FromLong(count);\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_unsupported_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    PyArray_Descr *descr = PyArray_DescrFromType(NPY_VOID);\n"
        "    if (descr != NULL) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "PyArray_DescrFromType unexpectedly succeeded");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 1;\n"
        "    PyErr_Clear();\n"
        '    PyObject *result = PyUFunc_FromFuncAndData(NULL, NULL, NULL, 0, 1, 1, 0, "stub", "stub", 0);\n'
        "    if (result != NULL) return result;\n"
        "    if (PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 100;\n"
        "    PyErr_Clear();\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static void pccnpcons_add_double_loop(char **args, const npy_intp *dimensions, const npy_intp *steps, void *data) {\n"
        "    (void)data;\n"
        "    char *lhs = args[0];\n"
        "    char *rhs = args[1];\n"
        "    char *out = args[2];\n"
        "    for (npy_intp i = 0; i < dimensions[0]; i++) {\n"
        "        *(double *)out = *(double *)lhs + *(double *)rhs;\n"
        "        lhs += steps[0];\n"
        "        rhs += steps[1];\n"
        "        out += steps[2];\n"
        "    }\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_ufunc_registration_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyUFuncGenericFunction funcs[1] = {pccnpcons_add_double_loop};\n"
        "    void *loop_data[1] = {NULL};\n"
        "    char types[3] = {NPY_DOUBLE, NPY_DOUBLE, NPY_DOUBLE};\n"
        '    PyObject *ufunc = PyUFunc_FromFuncAndData(funcs, loop_data, types, 1, 2, 1, 0, "pccadd", "doc", 0);\n'
        "    if (ufunc == NULL) return NULL;\n"
        "    long score = 0;\n"
        "    if (!PyCapsule_CheckExact(ufunc)) score += 1;\n"
        "    npy_intp dims[1] = {3};\n"
        "    PyObject *lhs_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);\n"
        "    PyObject *rhs_obj = PyArray_SimpleNew(1, dims, NPY_DOUBLE);\n"
        "    if (lhs_obj == NULL || rhs_obj == NULL) {\n"
        "        Py_XDECREF(lhs_obj);\n"
        "        Py_XDECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    double *lhs = (double *)PyArray_DATA((PyArrayObject *)lhs_obj);\n"
        "    double *rhs = (double *)PyArray_DATA((PyArrayObject *)rhs_obj);\n"
        "    if (lhs != NULL && rhs != NULL) {\n"
        "        lhs[0] = 1.5;\n"
        "        lhs[1] = 2.5;\n"
        "        lhs[2] = 3.5;\n"
        "        rhs[0] = 10.0;\n"
        "        rhs[1] = 20.0;\n"
        "        rhs[2] = 30.0;\n"
        "        score += 10;\n"
        "    }\n"
        "    PyObject *out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, rhs_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyArray_Check(out_obj) && PyArray_SIZE((PyArrayObject *)out_obj) == 3) score += 100;\n"
        "    PyArray_Descr *out_descr = PyArray_DESCR((PyArrayObject *)out_obj);\n"
        "    if (out_descr != NULL && out_descr->type_num == NPY_DOUBLE) score += 1000;\n"
        "    double *out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 11.5 && out[1] == 22.5 && out[2] == 33.5) score += 10000;\n"
        "    Py_DECREF(out_obj);\n"
        "    PyObject *scalar = PyFloat_FromDouble(7.25);\n"
        "    if (scalar == NULL) {\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, scalar, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 8.75 && out[1] == 9.75 && out[2] == 10.75) score += 100000;\n"
        "    Py_DECREF(out_obj);\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, scalar, rhs_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 17.25 && out[1] == 27.25 && out[2] == 37.25) score += 1000000;\n"
        "    Py_DECREF(out_obj);\n"
        "    PyObject *scalar2 = PyFloat_FromDouble(0.75);\n"
        "    if (scalar2 == NULL) {\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *scalar_result = PyObject_CallFunctionObjArgs(ufunc, scalar, scalar2, NULL);\n"
        "    if (scalar_result == NULL) {\n"
        "        Py_DECREF(scalar2);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyFloat_Check(scalar_result) && PyFloat_AsDouble(scalar_result) == 8.0 && PyErr_Occurred() == NULL) score += 10000000;\n"
        "    Py_DECREF(scalar_result);\n"
        "    Py_DECREF(scalar2);\n"
        "    PyObject *int_scalar = PyLong_FromLong(4);\n"
        "    if (int_scalar == NULL) {\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, int_scalar, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 5.5 && out[1] == 6.5 && out[2] == 7.5) score += 100000000;\n"
        "    Py_DECREF(out_obj);\n"
        "    scalar_result = PyObject_CallFunctionObjArgs(ufunc, scalar, int_scalar, NULL);\n"
        "    if (scalar_result == NULL) {\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyFloat_Check(scalar_result) && PyFloat_AsDouble(scalar_result) == 11.25 && PyErr_Occurred() == NULL) score += 1000000000;\n"
        "    Py_DECREF(scalar_result);\n"
        "    PyObject *int_arr_obj = PyArray_SimpleNew(1, dims, NPY_INT);\n"
        "    if (int_arr_obj == NULL) {\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    int *int_arr = (int *)PyArray_DATA((PyArrayObject *)int_arr_obj);\n"
        "    if (int_arr != NULL) {\n"
        "        int_arr[0] = 4;\n"
        "        int_arr[1] = 5;\n"
        "        int_arr[2] = 6;\n"
        "    }\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, int_arr_obj, rhs_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 14.0 && out[1] == 25.0 && out[2] == 36.0) score += 10000000000L;\n"
        "    Py_DECREF(out_obj);\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, int_arr_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 5.5 && out[1] == 7.5 && out[2] == 9.5) score += 100000000000L;\n"
        "    Py_DECREF(out_obj);\n"
        "    npy_intp one_dim[1] = {1};\n"
        "    PyObject *one_obj = PyArray_SimpleNew(1, one_dim, NPY_DOUBLE);\n"
        "    if (one_obj == NULL) {\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    double *one = (double *)PyArray_DATA((PyArrayObject *)one_obj);\n"
        "    if (one != NULL) one[0] = 100.0;\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, one_obj, rhs_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(one_obj);\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 110.0 && out[1] == 120.0 && out[2] == 130.0) score += 1000000000000L;\n"
        "    Py_DECREF(out_obj);\n"
        "    out_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, one_obj, NULL);\n"
        "    if (out_obj == NULL) {\n"
        "        Py_DECREF(one_obj);\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    out = (double *)PyArray_DATA((PyArrayObject *)out_obj);\n"
        "    if (out != NULL && out[0] == 101.5 && out[1] == 102.5 && out[2] == 103.5) score += 10000000000000L;\n"
        "    Py_DECREF(out_obj);\n"
        "    npy_intp two_dim[1] = {2};\n"
        "    PyObject *two_obj = PyArray_SimpleNew(1, two_dim, NPY_DOUBLE);\n"
        "    if (two_obj == NULL) {\n"
        "        Py_DECREF(one_obj);\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *bad_obj = PyObject_CallFunctionObjArgs(ufunc, lhs_obj, two_obj, NULL);\n"
        "    if (bad_obj == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {\n"
        "        PyErr_Clear();\n"
        "        score += 100000000000000L;\n"
        "    } else {\n"
        "        Py_XDECREF(bad_obj);\n"
        "        Py_DECREF(two_obj);\n"
        "        Py_DECREF(one_obj);\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        Py_DECREF(int_scalar);\n"
        "        Py_DECREF(scalar);\n"
        "        Py_DECREF(lhs_obj);\n"
        "        Py_DECREF(rhs_obj);\n"
        "        Py_DECREF(ufunc);\n"
        '        PyErr_SetString(PyExc_AssertionError, "incompatible ufunc shapes should fail");\n'
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(two_obj);\n"
        "    Py_DECREF(one_obj);\n"
        "    Py_DECREF(int_arr_obj);\n"
        "    Py_DECREF(int_scalar);\n"
        "    Py_DECREF(scalar);\n"
        "    Py_DECREF(lhs_obj);\n"
        "    Py_DECREF(rhs_obj);\n"
        "    Py_DECREF(ufunc);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_inferred_dtype_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    PyObject *i0 = PyLong_FromLong(14);\n"
        "    PyObject *i1 = PyLong_FromLong(15);\n"
        "    PyObject *i2 = PyLong_FromLong(16);\n"
        "    if (i0 == NULL || i1 == NULL || i2 == NULL) {\n"
        "        Py_XDECREF(i0);\n"
        "        Py_XDECREF(i1);\n"
        "        Py_XDECREF(i2);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *int_seq = PyTuple_Pack(3, i0, i1, i2);\n"
        "    Py_DECREF(i0);\n"
        "    Py_DECREF(i1);\n"
        "    Py_DECREF(i2);\n"
        "    if (int_seq == NULL) return NULL;\n"
        "    PyObject *int_arr_obj = PyArray_FromAny(int_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(int_seq);\n"
        "    if (int_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *int_arr = (PyArrayObject *)int_arr_obj;\n"
        "    npy_intp *int_dims = PyArray_DIMS(int_arr);\n"
        "    PyArray_Descr *int_descr = PyArray_DESCR(int_arr);\n"
        "    long *int_data = (long *)PyArray_DATA(int_arr);\n"
        "    if (PyArray_NDIM(int_arr) == 1 && int_dims != NULL && int_dims[0] == 3) score += 1;\n"
        "    if (int_descr != NULL && int_descr->type_num == NPY_LONG && int_descr->elsize == (int)sizeof(long)) score += 10;\n"
        "    if (int_data != NULL && int_data[0] == 14 && int_data[2] == 16) score += 100;\n"
        "    PyObject *int_item = int_data == NULL ? NULL : PyArray_GETITEM(int_arr, &int_data[2]);\n"
        "    if (int_item == NULL) {\n"
        "        Py_DECREF(int_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(int_item) == 16 && PyErr_Occurred() == NULL) score += 1000;\n"
        "    Py_DECREF(int_item);\n"
        "    Py_DECREF(int_arr_obj);\n"
        "    unsigned long max_ulong = (unsigned long)-1;\n"
        "    PyObject *u0 = PyLong_FromUnsignedLong(max_ulong);\n"
        "    PyObject *u1 = PyLong_FromUnsignedLong(7UL);\n"
        "    if (u0 == NULL || u1 == NULL) {\n"
        "        Py_XDECREF(u0);\n"
        "        Py_XDECREF(u1);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *ulong_seq = PyTuple_Pack(2, u0, u1);\n"
        "    Py_DECREF(u0);\n"
        "    Py_DECREF(u1);\n"
        "    if (ulong_seq == NULL) return NULL;\n"
        "    PyObject *ulong_arr_obj = PyArray_FromAny(ulong_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(ulong_seq);\n"
        "    if (ulong_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *ulong_arr = (PyArrayObject *)ulong_arr_obj;\n"
        "    PyArray_Descr *ulong_descr = PyArray_DESCR(ulong_arr);\n"
        "    unsigned long *ulong_data = (unsigned long *)PyArray_DATA(ulong_arr);\n"
        "    if (ulong_descr != NULL && ulong_descr->type_num == NPY_ULONG && ulong_descr->elsize == (int)sizeof(unsigned long)) score += 100000000L;\n"
        "    if (ulong_data != NULL && ulong_data[0] == max_ulong && ulong_data[1] == 7UL) score += 1000000000L;\n"
        "    PyObject *ulong_item = ulong_data == NULL ? NULL : PyArray_GETITEM(ulong_arr, &ulong_data[0]);\n"
        "    if (ulong_item == NULL) {\n"
        "        Py_DECREF(ulong_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsUnsignedLong(ulong_item) == max_ulong && PyErr_Occurred() == NULL) score += 10000000000L;\n"
        "    Py_DECREF(ulong_item);\n"
        "    Py_DECREF(ulong_arr_obj);\n"
        "    PyObject *n0 = PyLong_FromLong(-1);\n"
        "    PyObject *n1 = PyLong_FromUnsignedLong(max_ulong);\n"
        "    if (n0 == NULL || n1 == NULL) {\n"
        "        Py_XDECREF(n0);\n"
        "        Py_XDECREF(n1);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *mixed_int_seq = PyTuple_Pack(2, n0, n1);\n"
        "    Py_DECREF(n0);\n"
        "    Py_DECREF(n1);\n"
        "    if (mixed_int_seq == NULL) return NULL;\n"
        "    PyObject *mixed_int_arr_obj = PyArray_FromAny(mixed_int_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(mixed_int_seq);\n"
        "    if (mixed_int_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *mixed_int_arr = (PyArrayObject *)mixed_int_arr_obj;\n"
        "    PyArray_Descr *mixed_int_descr = PyArray_DESCR(mixed_int_arr);\n"
        "    PyObject **mixed_int_data = (PyObject **)PyArray_DATA(mixed_int_arr);\n"
        "    if (mixed_int_descr != NULL && mixed_int_descr->type_num == NPY_OBJECT) score += 100000000000L;\n"
        "    PyObject *mixed_neg_item = mixed_int_data == NULL ? NULL : PyArray_GETITEM(mixed_int_arr, &mixed_int_data[0]);\n"
        "    if (mixed_neg_item == NULL) {\n"
        "        Py_DECREF(mixed_int_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(mixed_neg_item) == -1 && PyErr_Occurred() == NULL) score += 1000000000000L;\n"
        "    Py_DECREF(mixed_neg_item);\n"
        "    PyObject *mixed_uint_item = mixed_int_data == NULL ? NULL : PyArray_GETITEM(mixed_int_arr, &mixed_int_data[1]);\n"
        "    if (mixed_uint_item == NULL) {\n"
        "        Py_DECREF(mixed_int_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsUnsignedLong(mixed_uint_item) == max_ulong && PyErr_Occurred() == NULL) score += 10000000000000L;\n"
        "    Py_DECREF(mixed_uint_item);\n"
        "    Py_DECREF(mixed_int_arr_obj);\n"
        "    PyObject *z0 = PyComplex_FromDoubles(1.5, 2.5);\n"
        "    PyObject *z1 = PyLong_FromLong(3);\n"
        "    if (z0 == NULL || z1 == NULL) {\n"
        "        Py_XDECREF(z0);\n"
        "        Py_XDECREF(z1);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *complex_seq = PyTuple_Pack(2, z0, z1);\n"
        "    Py_DECREF(z0);\n"
        "    Py_DECREF(z1);\n"
        "    if (complex_seq == NULL) return NULL;\n"
        "    PyObject *complex_arr_obj = PyArray_FromAny(complex_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(complex_seq);\n"
        "    if (complex_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *complex_arr = (PyArrayObject *)complex_arr_obj;\n"
        "    PyArray_Descr *complex_descr = PyArray_DESCR(complex_arr);\n"
        "    Py_complex *complex_data = (Py_complex *)PyArray_DATA(complex_arr);\n"
        "    if (complex_descr != NULL && complex_descr->type_num == NPY_CDOUBLE && complex_descr->elsize == (int)sizeof(Py_complex)) score += 100000000000000L;\n"
        "    if (complex_data != NULL && complex_data[0].real == 1.5 && complex_data[0].imag == 2.5 && complex_data[1].real == 3.0 && complex_data[1].imag == 0.0) score += 1000000000000000L;\n"
        "    PyObject *complex_item = complex_data == NULL ? NULL : PyArray_GETITEM(complex_arr, &complex_data[0]);\n"
        "    if (complex_item == NULL) {\n"
        "        Py_DECREF(complex_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyComplex_RealAsDouble(complex_item) == 1.5 && PyComplex_ImagAsDouble(complex_item) == 2.5 && PyErr_Occurred() == NULL) score += 10000000000000000L;\n"
        "    Py_DECREF(complex_item);\n"
        "    Py_DECREF(complex_arr_obj);\n"
        "    PyObject *m0 = PyLong_FromLong(2);\n"
        "    PyObject *m1 = PyFloat_FromDouble(2.5);\n"
        "    if (m0 == NULL || m1 == NULL) {\n"
        "        Py_XDECREF(m0);\n"
        "        Py_XDECREF(m1);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *mixed_seq = PyTuple_Pack(2, m0, m1);\n"
        "    Py_DECREF(m0);\n"
        "    Py_DECREF(m1);\n"
        "    if (mixed_seq == NULL) return NULL;\n"
        "    PyObject *mixed_arr_obj = PyArray_FromAny(mixed_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(mixed_seq);\n"
        "    if (mixed_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *mixed_arr = (PyArrayObject *)mixed_arr_obj;\n"
        "    PyArray_Descr *mixed_descr = PyArray_DESCR(mixed_arr);\n"
        "    double *mixed_data = (double *)PyArray_DATA(mixed_arr);\n"
        "    if (mixed_descr != NULL && mixed_descr->type_num == NPY_DOUBLE) score += 10000;\n"
        "    if (mixed_data != NULL && mixed_data[0] == 2.0 && mixed_data[1] == 2.5) score += 100000;\n"
        "    Py_DECREF(mixed_arr_obj);\n"
        '    PyObject *s0 = PyUnicode_FromString("auto");\n'
        '    PyObject *s1 = PyBytes_FromString("bytes");\n'
        "    if (s0 == NULL || s1 == NULL) {\n"
        "        Py_XDECREF(s0);\n"
        "        Py_XDECREF(s1);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *str_seq = PyTuple_Pack(2, s0, s1);\n"
        "    Py_DECREF(s0);\n"
        "    Py_DECREF(s1);\n"
        "    if (str_seq == NULL) return NULL;\n"
        "    PyObject *str_arr_obj = PyArray_FromAny(str_seq, NULL, 1, 1, 0, NULL);\n"
        "    Py_DECREF(str_seq);\n"
        "    if (str_arr_obj == NULL) return NULL;\n"
        "    PyArrayObject *str_arr = (PyArrayObject *)str_arr_obj;\n"
        "    PyArray_Descr *str_descr = PyArray_DESCR(str_arr);\n"
        "    char *str_data = (char *)PyArray_DATA(str_arr);\n"
        "    if (str_descr != NULL && str_descr->type_num == NPY_STRING && str_descr->elsize == 5 && PyArray_ITEMSIZE(str_arr) == 5) score += 1000000;\n"
        "    PyObject *str_item = str_data == NULL ? NULL : PyArray_GETITEM(str_arr, &str_data[0]);\n"
        "    if (str_item == NULL) {\n"
        "        Py_DECREF(str_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    char *str_raw = NULL;\n"
        "    Py_ssize_t str_len = 0;\n"
        "    if (PyBytes_AsStringAndSize(str_item, &str_raw, &str_len) == 0 && str_len == 4 && str_raw[0] == 'a' && str_raw[1] == 'u' && str_raw[2] == 't' && str_raw[3] == 'o' && str_data[5] == 'b' && str_data[9] == 's') score += 10000000;\n"
        "    Py_DECREF(str_item);\n"
        "    Py_DECREF(str_arr_obj);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_descr_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    PyArray_Descr *descr = PyArray_DescrFromType(NPY_INT);\n"
        "    if (descr != NULL && descr->type_num == NPY_INT) score += 1;\n"
        "    if (descr != NULL && descr->elsize == (int)sizeof(int)) score += 10;\n"
        "    if (descr != NULL && descr->kind == 'i') score += 100;\n"
        "    if (descr != NULL && descr->type == 'i') score += 1000;\n"
        "    npy_intp dims[1] = {1};\n"
        "    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_DOUBLE);\n"
        "    if (arr == NULL) return NULL;\n"
        "    PyArray_Descr *arr_descr = PyArray_DESCR((PyArrayObject *)arr);\n"
        "    if (arr_descr != NULL && arr_descr->type_num == NPY_DOUBLE) score += 10000;\n"
        "    if (arr_descr != NULL && arr_descr->elsize == (int)sizeof(double)) score += 100000;\n"
        "    if (arr_descr != NULL && arr_descr->kind == 'f' && arr_descr->type == 'd') score += 1000000;\n"
        "    Py_DECREF(arr);\n"
        "    PyArray_Descr *obj_descr = PyArray_DescrFromType(NPY_OBJECT);\n"
        "    if (obj_descr != NULL && obj_descr->type_num == NPY_OBJECT && obj_descr->kind == 'O' && obj_descr->type == 'O' && obj_descr->elsize == (int)sizeof(PyObject *)) score += 10000000;\n"
        "    PyArray_Descr *cfloat_descr = PyArray_DescrFromType(NPY_CFLOAT);\n"
        "    if (cfloat_descr != NULL && cfloat_descr->type_num == NPY_CFLOAT && cfloat_descr->kind == 'c' && cfloat_descr->type == 'F' && cfloat_descr->elsize == (int)sizeof(npy_cfloat)) score += 100000000L;\n"
        "    PyArray_Descr *clongdouble_descr = PyArray_DescrFromType(NPY_CLONGDOUBLE);\n"
        "    if (clongdouble_descr != NULL && clongdouble_descr->type_num == NPY_CLONGDOUBLE && clongdouble_descr->kind == 'c' && clongdouble_descr->type == 'G' && clongdouble_descr->elsize == (int)sizeof(npy_clongdouble)) score += 1000000000L;\n"
        "    npy_intp complex_dims[1] = {1};\n"
        "    PyObject *cfloat_arr_obj = PyArray_SimpleNew(1, complex_dims, NPY_CFLOAT);\n"
        "    PyObject *clongdouble_arr_obj = PyArray_SimpleNew(1, complex_dims, NPY_CLONGDOUBLE);\n"
        "    if (cfloat_arr_obj == NULL || clongdouble_arr_obj == NULL) {\n"
        "        Py_XDECREF(cfloat_arr_obj);\n"
        "        Py_XDECREF(clongdouble_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    npy_cfloat *cfloat_data = (npy_cfloat *)PyArray_DATA((PyArrayObject *)cfloat_arr_obj);\n"
        "    npy_clongdouble *clongdouble_data = (npy_clongdouble *)PyArray_DATA((PyArrayObject *)clongdouble_arr_obj);\n"
        "    PyObject *cfloat_value = PyComplex_FromDoubles(1.25, -2.5);\n"
        "    PyObject *clongdouble_value = PyComplex_FromDoubles(4.5, -5.5);\n"
        "    if (cfloat_data == NULL || clongdouble_data == NULL || cfloat_value == NULL || clongdouble_value == NULL) {\n"
        "        Py_XDECREF(cfloat_value);\n"
        "        Py_XDECREF(clongdouble_value);\n"
        "        Py_DECREF(cfloat_arr_obj);\n"
        "        Py_DECREF(clongdouble_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyArray_SETITEM((PyArrayObject *)cfloat_arr_obj, cfloat_data, cfloat_value) == 0 && cfloat_data[0].real == 1.25f && cfloat_data[0].imag == -2.5f) score += 10000000000L;\n"
        "    if (PyArray_SETITEM((PyArrayObject *)clongdouble_arr_obj, clongdouble_data, clongdouble_value) == 0 && clongdouble_data[0].real == (long double)4.5 && clongdouble_data[0].imag == (long double)-5.5) score += 100000000000L;\n"
        "    Py_DECREF(cfloat_value);\n"
        "    Py_DECREF(clongdouble_value);\n"
        "    PyObject *cfloat_item = PyArray_GETITEM((PyArrayObject *)cfloat_arr_obj, cfloat_data);\n"
        "    PyObject *clongdouble_item = PyArray_GETITEM((PyArrayObject *)clongdouble_arr_obj, clongdouble_data);\n"
        "    if (cfloat_item == NULL || clongdouble_item == NULL) {\n"
        "        Py_XDECREF(cfloat_item);\n"
        "        Py_XDECREF(clongdouble_item);\n"
        "        Py_DECREF(cfloat_arr_obj);\n"
        "        Py_DECREF(clongdouble_arr_obj);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyComplex_RealAsDouble(cfloat_item) == 1.25 && PyComplex_ImagAsDouble(cfloat_item) == -2.5 && PyErr_Occurred() == NULL) score += 1000000000000L;\n"
        "    if (PyComplex_RealAsDouble(clongdouble_item) == 4.5 && PyComplex_ImagAsDouble(clongdouble_item) == -5.5 && PyErr_Occurred() == NULL) score += 10000000000000L;\n"
        "    Py_DECREF(cfloat_item);\n"
        "    Py_DECREF(clongdouble_item);\n"
        "    Py_DECREF(cfloat_arr_obj);\n"
        "    Py_DECREF(clongdouble_arr_obj);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_fromany_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    npy_intp dims[2] = {2, 2};\n"
        "    PyObject *arr = PyArray_SimpleNew(2, dims, NPY_INT);\n"
        "    if (arr == NULL) return NULL;\n"
        "    PyArray_Descr *int_descr = PyArray_DescrFromType(NPY_INT);\n"
        "    PyArray_Descr *double_descr = PyArray_DescrFromType(NPY_DOUBLE);\n"
        "    PyArray_Descr *object_descr = PyArray_DescrFromType(NPY_OBJECT);\n"
        "    PyObject *same = PyArray_FromAny(arr, int_descr, 1, 2, 0, NULL);\n"
        "    if (same == arr) score += 1;\n"
        "    Py_XDECREF(same);\n"
        "    same = PyArray_FromAny(arr, NULL, 0, 0, 0, NULL);\n"
        "    if (same == arr) score += 10;\n"
        "    Py_XDECREF(same);\n"
        "    int *arr_data = (int *)PyArray_DATA((PyArrayObject *)arr);\n"
        "    if (arr_data == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    arr_data[3] = 33;\n"
        "    PyObject *casted = PyArray_FromAny(arr, double_descr, 0, 0, 0, NULL);\n"
        "    if (casted == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *casted_arr = (PyArrayObject *)casted;\n"
        "    npy_intp *casted_dims = PyArray_DIMS(casted_arr);\n"
        "    double *casted_data = (double *)PyArray_DATA(casted_arr);\n"
        "    if (casted != arr && PyArray_NDIM(casted_arr) == 2 && casted_dims != NULL && casted_dims[0] == 2 && casted_dims[1] == 2 && casted_data != NULL && casted_data[3] == 33.0) score += 100;\n"
        "    Py_DECREF(casted);\n"
        "    if (PyArray_FromAny(arr, int_descr, 3, 0, 0, NULL) == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) score += 1000;\n"
        "    PyErr_Clear();\n"
        "    if (PyArray_FromAny(Py_None, int_descr, 0, 0, 0, NULL) == NULL && PyErr_ExceptionMatches(PyExc_NotImplementedError)) score += 10000;\n"
        "    PyErr_Clear();\n"
        "    PyObject *v0 = PyLong_FromLong(4);\n"
        "    PyObject *v1 = PyLong_FromLong(5);\n"
        "    PyObject *v2 = PyLong_FromLong(6);\n"
        "    if (v0 == NULL || v1 == NULL || v2 == NULL) {\n"
        "        Py_XDECREF(v0);\n"
        "        Py_XDECREF(v1);\n"
        "        Py_XDECREF(v2);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *seq = PyTuple_Pack(3, v0, v1, v2);\n"
        "    Py_DECREF(v0);\n"
        "    Py_DECREF(v1);\n"
        "    Py_DECREF(v2);\n"
        "    if (seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *coerced = PyArray_FromAny(seq, int_descr, 1, 1, 0, NULL);\n"
        "    Py_DECREF(seq);\n"
        "    if (coerced == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *coerced_arr = (PyArrayObject *)coerced;\n"
        "    npy_intp *coerced_dims = PyArray_DIMS(coerced_arr);\n"
        "    int *coerced_data = (int *)PyArray_DATA(coerced_arr);\n"
        "    if (PyArray_NDIM(coerced_arr) == 1 && coerced_dims != NULL && coerced_dims[0] == 3) score += 100000;\n"
        "    if (coerced_data == NULL) {\n"
        "        Py_DECREF(coerced);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (coerced_data != NULL && coerced_data[0] == 4 && coerced_data[1] == 5 && coerced_data[2] == 6) score += 1000000;\n"
        "    PyObject *coerced_item = PyArray_GETITEM(coerced_arr, &coerced_data[1]);\n"
        "    if (coerced_item == NULL) {\n"
        "        Py_DECREF(coerced);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(coerced_item) == 5 && PyErr_Occurred() == NULL) score += 10000000;\n"
        "    Py_DECREF(coerced_item);\n"
        "    Py_DECREF(coerced);\n"
        "    PyObject *m00 = PyLong_FromLong(11);\n"
        "    PyObject *m01 = PyLong_FromLong(12);\n"
        "    PyObject *m10 = PyLong_FromLong(21);\n"
        "    PyObject *m11 = PyLong_FromLong(22);\n"
        "    if (m00 == NULL || m01 == NULL || m10 == NULL || m11 == NULL) {\n"
        "        Py_XDECREF(m00);\n"
        "        Py_XDECREF(m01);\n"
        "        Py_XDECREF(m10);\n"
        "        Py_XDECREF(m11);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *row0 = PyTuple_Pack(2, m00, m01);\n"
        "    PyObject *row1 = PyTuple_Pack(2, m10, m11);\n"
        "    Py_DECREF(m00);\n"
        "    Py_DECREF(m01);\n"
        "    Py_DECREF(m10);\n"
        "    Py_DECREF(m11);\n"
        "    if (row0 == NULL || row1 == NULL) {\n"
        "        Py_XDECREF(row0);\n"
        "        Py_XDECREF(row1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *matrix_seq = PyTuple_Pack(2, row0, row1);\n"
        "    Py_DECREF(row0);\n"
        "    Py_DECREF(row1);\n"
        "    if (matrix_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *matrix = PyArray_FromAny(matrix_seq, int_descr, 2, 2, 0, NULL);\n"
        "    Py_DECREF(matrix_seq);\n"
        "    if (matrix == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *matrix_arr = (PyArrayObject *)matrix;\n"
        "    npy_intp *matrix_dims = PyArray_DIMS(matrix_arr);\n"
        "    int *matrix_data = (int *)PyArray_DATA(matrix_arr);\n"
        "    if (matrix_data == NULL) {\n"
        "        Py_DECREF(matrix);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyArray_NDIM(matrix_arr) == 2 && matrix_dims != NULL && matrix_dims[0] == 2 && matrix_dims[1] == 2 && matrix_data[0] == 11 && matrix_data[3] == 22) score += 100000000;\n"
        "    PyObject *matrix_item = PyArray_GETITEM(matrix_arr, &matrix_data[3]);\n"
        "    if (matrix_item == NULL) {\n"
        "        Py_DECREF(matrix);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(matrix_item) == 22 && PyErr_Occurred() == NULL) score += 1000000000;\n"
        "    Py_DECREF(matrix_item);\n"
        "    Py_DECREF(matrix);\n"
        "    PyObject *c0 = PyLong_FromLong(31);\n"
        "    PyObject *c1 = PyLong_FromLong(32);\n"
        "    PyObject *c2 = PyLong_FromLong(41);\n"
        "    PyObject *c3 = PyLong_FromLong(42);\n"
        "    if (c0 == NULL || c1 == NULL || c2 == NULL || c3 == NULL) {\n"
        "        Py_XDECREF(c0);\n"
        "        Py_XDECREF(c1);\n"
        "        Py_XDECREF(c2);\n"
        "        Py_XDECREF(c3);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *cube_row0 = PyTuple_Pack(2, c0, c1);\n"
        "    PyObject *cube_row1 = PyTuple_Pack(2, c2, c3);\n"
        "    Py_DECREF(c0);\n"
        "    Py_DECREF(c1);\n"
        "    Py_DECREF(c2);\n"
        "    Py_DECREF(c3);\n"
        "    if (cube_row0 == NULL || cube_row1 == NULL) {\n"
        "        Py_XDECREF(cube_row0);\n"
        "        Py_XDECREF(cube_row1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *cube_plane0 = PyTuple_Pack(1, cube_row0);\n"
        "    PyObject *cube_plane1 = PyTuple_Pack(1, cube_row1);\n"
        "    Py_DECREF(cube_row0);\n"
        "    Py_DECREF(cube_row1);\n"
        "    if (cube_plane0 == NULL || cube_plane1 == NULL) {\n"
        "        Py_XDECREF(cube_plane0);\n"
        "        Py_XDECREF(cube_plane1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *cube_seq = PyTuple_Pack(2, cube_plane0, cube_plane1);\n"
        "    Py_DECREF(cube_plane0);\n"
        "    Py_DECREF(cube_plane1);\n"
        "    if (cube_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *cube = PyArray_FromAny(cube_seq, int_descr, 3, 3, 0, NULL);\n"
        "    Py_DECREF(cube_seq);\n"
        "    if (cube == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *cube_arr = (PyArrayObject *)cube;\n"
        "    npy_intp *cube_dims = PyArray_DIMS(cube_arr);\n"
        "    int *cube_data = (int *)PyArray_DATA(cube_arr);\n"
        "    if (cube_data == NULL) {\n"
        "        Py_DECREF(cube);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *cube_item = PyArray_GETITEM(cube_arr, &cube_data[3]);\n"
        "    if (cube_item == NULL) {\n"
        "        Py_DECREF(cube);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyArray_NDIM(cube_arr) == 3 && cube_dims != NULL && cube_dims[0] == 2 && cube_dims[1] == 1 && cube_dims[2] == 2 && cube_data[0] == 31 && cube_data[3] == 42 && PyLong_AsLong(cube_item) == 42 && PyErr_Occurred() == NULL) score += 10000000000L;\n"
        "    Py_DECREF(cube_item);\n"
        "    Py_DECREF(cube);\n"
        "    PyObject *obj0 = PyLong_FromLong(123);\n"
        "    PyObject *obj1 = PyLong_FromLong(456);\n"
        "    if (obj0 == NULL || obj1 == NULL) {\n"
        "        Py_XDECREF(obj0);\n"
        "        Py_XDECREF(obj1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *obj_seq = PyTuple_Pack(2, obj0, obj1);\n"
        "    Py_DECREF(obj0);\n"
        "    Py_DECREF(obj1);\n"
        "    if (obj_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *obj_arr_obj = PyArray_FromAny(obj_seq, object_descr, 1, 1, 0, NULL);\n"
        "    Py_DECREF(obj_seq);\n"
        "    if (obj_arr_obj == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *obj_arr = (PyArrayObject *)obj_arr_obj;\n"
        "    npy_intp *obj_dims = PyArray_DIMS(obj_arr);\n"
        "    PyObject **obj_data = (PyObject **)PyArray_DATA(obj_arr);\n"
        "    if (PyArray_NDIM(obj_arr) == 1 && obj_dims != NULL && obj_dims[0] == 2 && obj_data != NULL && obj_data[0] != NULL && obj_data[1] != NULL) score += 100000000000L;\n"
        "    PyObject *obj_item = obj_data == NULL ? NULL : PyArray_GETITEM(obj_arr, &obj_data[1]);\n"
        "    if (obj_item == NULL) {\n"
        "        Py_DECREF(obj_arr_obj);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(obj_item) == 456 && PyErr_Occurred() == NULL) score += 1000000000000L;\n"
        "    Py_DECREF(obj_item);\n"
        "    Py_DECREF(obj_arr_obj);\n"
        '    PyObject *str0 = PyUnicode_FromString("left");\n'
        '    PyObject *str1 = PyUnicode_FromString("right");\n'
        "    if (str0 == NULL || str1 == NULL) {\n"
        "        Py_XDECREF(str0);\n"
        "        Py_XDECREF(str1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *str_seq = PyTuple_Pack(2, str0, str1);\n"
        "    Py_DECREF(str0);\n"
        "    Py_DECREF(str1);\n"
        "    if (str_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *str_arr_obj = PyArray_FromAny(str_seq, object_descr, 1, 1, 0, NULL);\n"
        "    Py_DECREF(str_seq);\n"
        "    if (str_arr_obj == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *str_arr = (PyArrayObject *)str_arr_obj;\n"
        "    npy_intp *str_dims = PyArray_DIMS(str_arr);\n"
        "    PyObject **str_data = (PyObject **)PyArray_DATA(str_arr);\n"
        "    if (PyArray_NDIM(str_arr) == 1 && str_dims != NULL && str_dims[0] == 2 && str_data != NULL && str_data[0] != NULL && str_data[1] != NULL) score += 10000000000000L;\n"
        "    PyObject *str_item = str_data == NULL ? NULL : PyArray_GETITEM(str_arr, &str_data[1]);\n"
        "    if (str_item == NULL) {\n"
        "        Py_DECREF(str_arr_obj);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    const char *str_raw = PyUnicode_AsUTF8(str_item);\n"
        "    if (str_raw != NULL && str_raw[0] == 'r' && str_raw[1] == 'i' && str_raw[2] == 'g' && str_raw[3] == 'h' && str_raw[4] == 't' && str_raw[5] == '\\0') score += 100000000000000L;\n"
        "    Py_DECREF(str_item);\n"
        "    Py_DECREF(str_arr_obj);\n"
        "    PyObject *rg0 = PyLong_FromLong(7);\n"
        "    PyObject *rg1 = PyLong_FromLong(8);\n"
        "    PyObject *rg2 = PyLong_FromLong(9);\n"
        "    if (rg0 == NULL || rg1 == NULL || rg2 == NULL) {\n"
        "        Py_XDECREF(rg0);\n"
        "        Py_XDECREF(rg1);\n"
        "        Py_XDECREF(rg2);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *rag_row0 = PyTuple_Pack(2, rg0, rg1);\n"
        "    PyObject *rag_row1 = PyTuple_Pack(1, rg2);\n"
        "    Py_DECREF(rg0);\n"
        "    Py_DECREF(rg1);\n"
        "    Py_DECREF(rg2);\n"
        "    if (rag_row0 == NULL || rag_row1 == NULL) {\n"
        "        Py_XDECREF(rag_row0);\n"
        "        Py_XDECREF(rag_row1);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *rag_seq = PyTuple_Pack(2, rag_row0, rag_row1);\n"
        "    Py_DECREF(rag_row0);\n"
        "    Py_DECREF(rag_row1);\n"
        "    if (rag_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *rag_arr_obj = PyArray_FromAny(rag_seq, object_descr, 1, 1, 0, NULL);\n"
        "    Py_DECREF(rag_seq);\n"
        "    if (rag_arr_obj == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *rag_arr = (PyArrayObject *)rag_arr_obj;\n"
        "    npy_intp *rag_dims = PyArray_DIMS(rag_arr);\n"
        "    PyObject **rag_data = (PyObject **)PyArray_DATA(rag_arr);\n"
        "    if (PyArray_NDIM(rag_arr) == 1 && rag_dims != NULL && rag_dims[0] == 2 && rag_data != NULL && rag_data[0] != NULL && rag_data[1] != NULL) score += 1000000000000000L;\n"
        "    PyObject *rag_item = rag_data == NULL ? NULL : PyArray_GETITEM(rag_arr, &rag_data[1]);\n"
        "    if (rag_item == NULL) {\n"
        "        Py_DECREF(rag_arr_obj);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PySequence_Size(rag_item) == 1 && PyErr_Occurred() == NULL) score += 10000000000000000L;\n"
        "    Py_DECREF(rag_item);\n"
        "    Py_DECREF(rag_arr_obj);\n"
        "    static char mv_raw[3] = {1, 2, 3};\n"
        "    PyObject *mv = PyMemoryView_FromMemory(mv_raw, 3, PyBUF_READ);\n"
        "    if (mv == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *mv_seq = PyTuple_Pack(1, mv);\n"
        "    Py_DECREF(mv);\n"
        "    if (mv_seq == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyObject *mv_arr_obj = PyArray_FromAny(mv_seq, object_descr, 1, 1, 0, NULL);\n"
        "    Py_DECREF(mv_seq);\n"
        "    if (mv_arr_obj == NULL) {\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *mv_arr = (PyArrayObject *)mv_arr_obj;\n"
        "    npy_intp *mv_dims = PyArray_DIMS(mv_arr);\n"
        "    PyObject **mv_data = (PyObject **)PyArray_DATA(mv_arr);\n"
        "    if (PyArray_NDIM(mv_arr) == 1 && mv_dims != NULL && mv_dims[0] == 1 && mv_data != NULL && mv_data[0] != NULL) score += 100000000000000000L;\n"
        "    PyObject *mv_item = mv_data == NULL ? NULL : PyArray_GETITEM(mv_arr, &mv_data[0]);\n"
        "    if (mv_item == NULL) {\n"
        "        Py_DECREF(mv_arr_obj);\n"
        "        Py_DECREF(arr);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyMemoryView_Check(mv_item) && PySequence_Size(mv_item) == 3 && PyErr_Occurred() == NULL) score += 1000000000000000000L;\n"
        "    Py_DECREF(mv_item);\n"
        "    Py_DECREF(mv_arr_obj);\n"
        "    Py_DECREF(arr);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *pccnpcons_array_metadata_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    npy_intp dims[2] = {2, 3};\n"
        "    PyObject *owned = PyArray_SimpleNew(2, dims, NPY_INT);\n"
        "    if (owned == NULL) return NULL;\n"
        "    PyObject *value = NULL;\n"
        "    PyArrayObject *arr = (PyArrayObject *)owned;\n"
        "    long score = 0;\n"
        "    npy_intp *owned_dims = PyArray_DIMS(arr);\n"
        "    npy_intp *owned_strides = PyArray_STRIDES(arr);\n"
        "    int *owned_data = (int *)PyArray_DATA(arr);\n"
        "    if (PyArray_NDIM(arr) == 2) score += 1;\n"
        "    if (owned_dims != NULL && owned_dims[0] == 2 && owned_dims[1] == 3) score += 10;\n"
        "    if (PyArray_DIM(arr, 1) == 3) score += 10000000000L;\n"
        "    if (owned_strides != NULL && owned_strides[0] == 3 * (npy_intp)sizeof(int) && owned_strides[1] == (npy_intp)sizeof(int)) score += 100;\n"
        "    if (PyArray_SIZE(arr) == 6) score += 100000000000L;\n"
        "    if (PyArray_ITEMSIZE(arr) == (int)sizeof(int)) score += 1000000000000L;\n"
        "    if (PyArray_Check(owned) && PyArray_CheckExact(owned) && !PyArray_Check(Py_None)) score += 10000000000000L;\n"
        "    if (owned_data != NULL) {\n"
        "        owned_data[5] = 41;\n"
        "        if (((int *)PyArray_DATA(arr))[5] == 41) score += 1000;\n"
        "        if (PyArray_BYTES(arr) == owned_data) score += 100000000000000L;\n"
        "        value = PyArray_GETITEM(arr, &owned_data[5]);\n"
        "        if (value == NULL) {\n"
        "            Py_DECREF(owned);\n"
        "            return NULL;\n"
        "        }\n"
        "        if (PyLong_AsLong(value) == 41) score += 1000000;\n"
        "        Py_DECREF(value);\n"
        "        value = PyLong_FromLong(77);\n"
        "        if (value == NULL) {\n"
        "            Py_DECREF(owned);\n"
        "            return NULL;\n"
        "        }\n"
        "        if (PyArray_SETITEM(arr, &owned_data[0], value) == 0 && owned_data[0] == 77) score += 10000000;\n"
        "        Py_DECREF(value);\n"
        "        if (PyErr_Occurred() != NULL) {\n"
        "            Py_DECREF(owned);\n"
        "            return NULL;\n"
        "        }\n"
        "    }\n"
        "    int raw[6] = {1, 2, 3, 4, 5, 6};\n"
        "    PyObject *borrowed = PyArray_SimpleNewFromData(2, dims, NPY_INT, raw);\n"
        "    if (borrowed == NULL) {\n"
        "        Py_DECREF(owned);\n"
        "        return NULL;\n"
        "    }\n"
        "    PyArrayObject *borrowed_arr = (PyArrayObject *)borrowed;\n"
        "    if (PyArray_DATA(borrowed_arr) == raw) score += 10000;\n"
        "    if (((int *)PyArray_DATA(borrowed_arr))[4] == 5) score += 100000;\n"
        "    value = PyLong_FromLong(99);\n"
        "    if (value == NULL) {\n"
        "        Py_DECREF(borrowed);\n"
        "        Py_DECREF(owned);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyArray_SETITEM(borrowed_arr, &raw[2], value) == 0 && raw[2] == 99) score += 100000000;\n"
        "    Py_DECREF(value);\n"
        "    if (PyErr_Occurred() != NULL) {\n"
        "        Py_DECREF(borrowed);\n"
        "        Py_DECREF(owned);\n"
        "        return NULL;\n"
        "    }\n"
        "    value = PyArray_GETITEM(borrowed_arr, &raw[2]);\n"
        "    if (value == NULL) {\n"
        "        Py_DECREF(borrowed);\n"
        "        Py_DECREF(owned);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyLong_AsLong(value) == 99) score += 1000000000;\n"
        "    Py_DECREF(value);\n"
        "    Py_DECREF(borrowed);\n"
        "    Py_DECREF(owned);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef ConsumerMethods[] = {\n"
        '    {"table_shape_score", pccnpcons_table_shape_score, METH_VARARGS, "import pcc NumPy C API stub capsules"},\n'
        '    {"unsupported_score", pccnpcons_unsupported_score, METH_VARARGS, "unsupported stubs fail visibly"},\n'
        '    {"ufunc_registration_score", pccnpcons_ufunc_registration_score, METH_VARARGS, "minimal pcc NumPy ufunc registration"},\n'
        '    {"inferred_dtype_score", pccnpcons_inferred_dtype_score, METH_VARARGS, "minimal pcc NumPy descriptor-less FromAny inference"},\n'
        '    {"descr_score", pccnpcons_descr_score, METH_VARARGS, "minimal pcc NumPy descriptor metadata"},\n'
        '    {"fromany_score", pccnpcons_fromany_score, METH_VARARGS, "minimal pcc NumPy FromAny coercion"},\n'
        '    {"array_metadata_score", pccnpcons_array_metadata_score, METH_VARARGS, "minimal pcc NumPy array metadata"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef consumermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "pccnpcons", NULL, -1, ConsumerMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_pccnpcons(void) {\n"
        "    import_array();\n"
        "    import_umath();\n"
        "    return PyModule_Create(&consumermodule);\n"
        "}\n",
    )
    return site


def _compile_feature_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "featuredemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static long init_count = 0;\n"
        "\n"
        "static PyObject *feature_count(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    return PyLong_FromLong(init_count);\n"
        "}\n"
        "\n"
        "static PyObject *feature_echo(PyObject *self, PyObject *args) {\n"
        "    const char *value = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "s", &value)) return NULL;\n'
        "    return PyUnicode_FromString(value);\n"
        "}\n"
        "\n"
        "static PyObject *feature_add(PyObject *self, PyObject *args) {\n"
        "    long a = 0;\n"
        "    long b = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "ll", &a, &b)) return NULL;\n'
        "    return PyLong_FromLong(a + b);\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_none(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_NONE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_true(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_TRUE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_false(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_FALSE;\n"
        "}\n"
        "\n"
        "static PyObject *feature_return_notimplemented(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    Py_RETURN_NOTIMPLEMENTED;\n"
        "}\n"
        "\n"
        "static PyObject *feature_identity_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    score += Py_IsNone(Py_None) ? 1 : 0;\n"
        "    score += Py_IsTrue(Py_True) ? 10 : 0;\n"
        "    score += Py_IsFalse(Py_False) ? 100 : 0;\n"
        "    score += Py_Is(Py_NotImplemented, Py_NotImplemented) ? 1000 : 0;\n"
        "    score += !Py_Is(Py_None, Py_False) ? 10000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *feature_fail(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyErr_SetString(PyExc_RuntimeError, "feature method failed");\n'
        "    return NULL;\n"
        "}\n"
        "\n"
        "static PyMethodDef FeatureMethods[] = {\n"
        '    {"count", feature_count, METH_VARARGS, "return init count"},\n'
        '    {"echo", feature_echo, METH_VARARGS, "echo a string"},\n'
        '    {"add", feature_add, METH_VARARGS, "add two ints"},\n'
        '    {"return_none", feature_return_none, METH_VARARGS, "return None"},\n'
        '    {"return_true", feature_return_true, METH_VARARGS, "return True"},\n'
        '    {"return_false", feature_return_false, METH_VARARGS, "return False"},\n'
        '    {"return_notimplemented", feature_return_notimplemented, METH_VARARGS, "return NotImplemented"},\n'
        '    {"identity_score", feature_identity_score, METH_VARARGS, "Py_Is macro smoke"},\n'
        '    {"fail", feature_fail, METH_VARARGS, "raise a RuntimeError"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef featuremodule = {\n"
        '    PyModuleDef_HEAD_INIT, "featuredemo", NULL, -1, FeatureMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_featuredemo(void) {\n"
        "    init_count += 1;\n"
        "    return PyModule_Create(&featuremodule);\n"
        "}\n",
    )


def _compile_buffer_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "bufdemo",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "\n"
        "static PyObject *buffer_sum(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) != 0) return NULL;\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_writable(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    int ok = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_WRITABLE) != 0) {\n"
        "        PyErr_Clear();\n"
        "        return PyLong_FromLong(0);\n"
        "    }\n"
        "    ok = view.readonly == 0;\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_metadata_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    Py_buffer view;\n"
        "    long score = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    if (PyObject_GetBuffer(obj, &view, PyBUF_STRIDES | PyBUF_FORMAT) != 0) return NULL;\n"
        "    score += view.ndim == 1 ? 1 : 0;\n"
        "    score += view.itemsize == 1 ? 10 : 0;\n"
        "    score += view.shape != NULL ? view.shape[0] * 100 : 0;\n"
        "    score += view.strides != NULL ? view.strides[0] * 1000 : 0;\n"
        "    score += view.format != NULL && view.format[0] == 'B' ? 10000 : 0;\n"
        "    score += view.readonly ? 100000 : 200000;\n"
        "    score += view.len * 1000000;\n"
        "    PyBuffer_Release(&view);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_view_sum(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    mv = PyMemoryView_FromObject(obj);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_SIMPLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_from_memory_sum(PyObject *self, PyObject *args) {\n"
        "    static char raw[] = {7, 8, 9};\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    mv = PyMemoryView_FromMemory(raw, 3, PyBUF_READ);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_SIMPLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_from_memory_write_sum(PyObject *self, PyObject *args) {\n"
        "    static char raw[] = {1, 2, 3};\n"
        "    PyObject *mv = NULL;\n"
        "    Py_buffer view;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    mv = PyMemoryView_FromMemory(raw, 3, PyBUF_WRITE);\n"
        "    if (mv == NULL) return NULL;\n"
        "    if (PyObject_GetBuffer(mv, &view, PyBUF_WRITABLE) != 0) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view.buf;\n"
        "    data[0] = 10;\n"
        "    for (Py_ssize_t i = 0; i < view.len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    PyBuffer_Release(&view);\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_memoryview_inspect_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    PyObject *mv = NULL;\n"
        "    PyObject *base = NULL;\n"
        "    Py_buffer *view = NULL;\n"
        "    long score = 0;\n"
        "    long total = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    mv = PyMemoryView_FromObject(obj);\n"
        "    if (mv == NULL) return NULL;\n"
        "    score += PyMemoryView_Check(mv) ? 1 : 0;\n"
        "    score += !PyMemoryView_Check(obj) ? 10 : 0;\n"
        "    base = PyMemoryView_GET_BASE(mv);\n"
        "    if (base == NULL) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    score += base == obj ? 100 : 0;\n"
        "    view = PyMemoryView_GET_BUFFER(mv);\n"
        "    if (view == NULL) {\n"
        "        Py_DECREF(mv);\n"
        "        return NULL;\n"
        "    }\n"
        "    unsigned char *data = (unsigned char *)view->buf;\n"
        "    for (Py_ssize_t i = 0; i < view->len; i++) {\n"
        "        total += data[i];\n"
        "    }\n"
        "    score += view->len * 1000;\n"
        "    score += view->ndim == 1 ? 10000 : 0;\n"
        "    score += view->shape != NULL && view->shape[0] == view->len ? 100000 : 0;\n"
        "    score += view->strides != NULL && view->strides[0] == 1 ? 1000000 : 0;\n"
        "    score += view->format != NULL && view->format[0] == 'B' ? 10000000 : 0;\n"
        "    score += view->readonly == 0 ? 100000000 : 0;\n"
        "    score += total * 100000000;\n"
        "    Py_DECREF(mv);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *buffer_check_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *bytes_obj = NULL;\n"
        "    PyObject *bytearray_obj = NULL;\n"
        "    PyObject *memoryview_obj = NULL;\n"
        "    PyObject *other = NULL;\n"
        "    long score = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "OOOO", &bytes_obj, &bytearray_obj, &memoryview_obj, &other)) return NULL;\n'
        "    score += PyObject_CheckBuffer(bytes_obj) ? 1 : 0;\n"
        "    score += PyObject_CheckBuffer(bytearray_obj) ? 10 : 0;\n"
        "    score += PyObject_CheckBuffer(memoryview_obj) ? 100 : 0;\n"
        "    score += !PyObject_CheckBuffer(other) ? 1000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef BufferMethods[] = {\n"
        '    {"sum", buffer_sum, METH_VARARGS, "sum bytes through the buffer API"},\n'
        '    {"writable", buffer_writable, METH_VARARGS, "request a writable buffer"},\n'
        '    {"metadata_score", buffer_metadata_score, METH_VARARGS, "buffer metadata smoke"},\n'
        '    {"view_sum", buffer_view_sum, METH_VARARGS, "sum through PyMemoryView_FromObject"},\n'
        '    {"from_memory_sum", buffer_from_memory_sum, METH_VARARGS, "sum PyMemoryView_FromMemory"},\n'
        '    {"from_memory_write_sum", buffer_from_memory_write_sum, METH_VARARGS, "writable PyMemoryView_FromMemory"},\n'
        '    {"memoryview_inspect_score", buffer_memoryview_inspect_score, METH_VARARGS, "memoryview inspect macro smoke"},\n'
        '    {"check_score", buffer_check_score, METH_VARARGS, "PyObject_CheckBuffer smoke"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef buffermodule = {\n"
        '    PyModuleDef_HEAD_INIT, "bufdemo", NULL, -1, BufferMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_bufdemo(void) {\n"
        "    return PyModule_Create(&buffermodule);\n"
        "}\n",
    )


def _compile_call_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "calldemo",
        "#include <Python.h>\n"
        "\n"
        "static PyObject *call_noargs(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    return PyObject_Call(callable, NULL, NULL);\n"
        "}\n"
        "\n"
        "static PyObject *call_object_noargs(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    return PyObject_CallObject(callable, NULL);\n"
        "}\n"
        "\n"
        "static PyObject *call_one_arg(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *arg = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    arg = PyLong_FromLong(8);\n"
        "    if (arg == NULL) return NULL;\n"
        "    result = PyObject_CallFunctionObjArgs(callable, arg, NULL);\n"
        "    Py_DECREF(arg);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *call_bad_args(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *bad_args = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    int ok = 0;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &callable)) return NULL;\n'
        "    bad_args = PyLong_FromLong(1);\n"
        "    result = PyObject_Call(callable, bad_args, NULL);\n"
        "    if (result != NULL) {\n"
        "        Py_DECREF(result);\n"
        "        Py_DECREF(bad_args);\n"
        "        return PyLong_FromLong(0);\n"
        "    }\n"
        "    ok = PyErr_Occurred() != NULL;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(bad_args);\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "\n"
        "static PyMethodDef CallMethods[] = {\n"
        '    {"call0", call_noargs, METH_VARARGS, "call with no args"},\n'
        '    {"call_object0", call_object_noargs, METH_VARARGS, "call object with no args"},\n'
        '    {"call_one", call_one_arg, METH_VARARGS, "call with one object arg"},\n'
        '    {"bad_args", call_bad_args, METH_VARARGS, "call with invalid args"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef callmodule = {\n"
        '    PyModuleDef_HEAD_INIT, "calldemo", NULL, -1, CallMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_calldemo(void) {\n"
        "    return PyModule_Create(&callmodule);\n"
        "}\n",
    )


def _compile_object_api_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "objectapi",
        "#include <Python.h>\n"
        "extern FILE *tmpfile(void);\n"
        "extern int fclose(FILE *stream);\n"
        "extern int fflush(FILE *stream);\n"
        "extern int fseek(FILE *stream, long offset, int whence);\n"
        "extern size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream);\n"
        "\n"
        "static PyObject *objectapi_tuple_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *tuple = PyTuple_New(2);\n"
        "    if (tuple == NULL) return NULL;\n"
        "    PyTuple_SET_ITEM(tuple, 0, PyLong_FromLong(11));\n"
        "    PyTuple_SET_ITEM(tuple, 1, PyLong_FromLong(31));\n"
        "    if (PyTuple_GET_SIZE(tuple) != 2) {\n"
        "        Py_DECREF(tuple);\n"
        "        return PyLong_FromLong(-1);\n"
        "    }\n"
        "    PyObject *a = PyTuple_GET_ITEM(tuple, 0);\n"
        "    PyObject *b = PyTuple_GetItem(tuple, 1);\n"
        "    long total = PyLong_AsLong(a) + PyLong_AsLong(b);\n"
        "    Py_DECREF(tuple);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_pack_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *a = PyLong_FromLong(5);\n"
        "    PyObject *b = PyLong_FromLong(9);\n"
        "    PyObject *tuple = PyTuple_Pack(2, a, b);\n"
        "    Py_DECREF(a);\n"
        "    Py_DECREF(b);\n"
        "    if (tuple == NULL) return NULL;\n"
        "    long total = PyLong_AsLong(PyTuple_GET_ITEM(tuple, 0))\n"
        "        + PyLong_AsLong(PyTuple_GET_ITEM(tuple, 1));\n"
        "    Py_DECREF(tuple);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_dict_answer(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *dict = PyDict_New();\n"
        "    PyObject *value = PyLong_FromLong(41);\n"
        "    if (dict == NULL || value == NULL) return NULL;\n"
        '    if (PyDict_SetItemString(dict, "answer", value) != 0) return NULL;\n'
        "    Py_DECREF(value);\n"
        '    PyObject *found = PyDict_GetItemString(dict, "answer");\n'
        "    if (found == NULL) return NULL;\n"
        "    long answer = PyLong_AsLong(found) + 1;\n"
        "    Py_DECREF(dict);\n"
        "    return PyLong_FromLong(answer);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_list_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *list = PyList_New(2);\n"
        "    if (list == NULL) return NULL;\n"
        "    PyList_SET_ITEM(list, 0, PyLong_FromLong(3));\n"
        "    PyList_SET_ITEM(list, 1, PyLong_FromLong(4));\n"
        "    PyObject *extra = PyLong_FromLong(5);\n"
        "    if (extra == NULL) return NULL;\n"
        "    if (PyList_Append(list, extra) != 0) return NULL;\n"
        "    Py_DECREF(extra);\n"
        "    if (PyList_GET_SIZE(list) != 3) {\n"
        "        Py_DECREF(list);\n"
        "        return PyLong_FromLong(-1);\n"
        "    }\n"
        "    long total = PyLong_AsLong(PyList_GET_ITEM(list, 0))\n"
        "        + PyLong_AsLong(PyList_GetItem(list, 1))\n"
        "        + PyLong_AsLong(PyList_GetItem(list, 2));\n"
        "    Py_DECREF(list);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_bytes_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *bytes = PyBytes_FromString("ABC");\n'
        "    if (bytes == NULL) return NULL;\n"
        "    char *data = PyBytes_AsString(bytes);\n"
        "    Py_ssize_t n = PyBytes_Size(bytes);\n"
        "    if (data == NULL || n < 0) return NULL;\n"
        "    long total = 0;\n"
        "    for (Py_ssize_t i = 0; i < n; i++) total += (unsigned char)data[i];\n"
        "    Py_DECREF(bytes);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_bytes_size_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *bytes = PyBytes_FromStringAndSize("DEF", 3);\n'
        "    if (bytes == NULL) return NULL;\n"
        "    char *data = NULL;\n"
        "    Py_ssize_t n = 0;\n"
        "    if (PyBytes_AsStringAndSize(bytes, &data, &n) != 0) return NULL;\n"
        "    long total = n;\n"
        "    for (Py_ssize_t i = 0; i < n; i++) total += (unsigned char)data[i];\n"
        "    total += PyBytes_GET_SIZE(bytes) == 3 ? 1000 : 0;\n"
        "    total += PyBytes_AS_STRING(bytes)[0] == 'D' ? 2000 : 0;\n"
        "    Py_DECREF(bytes);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_prefix(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    return PyUnicode_FromStringAndSize("abcdef", 3);\n'
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_utf8_size_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    const char payload[] = {'a', 'b', '\\0', 'c', 'd'};\n"
        "    PyObject *text = PyUnicode_FromStringAndSize(payload, 5);\n"
        "    Py_ssize_t n = 0;\n"
        "    long score = 0;\n"
        "    if (text == NULL) return NULL;\n"
        "    const char *raw = PyUnicode_AsUTF8AndSize(text, &n);\n"
        "    if (raw == NULL) return NULL;\n"
        "    score += n == 5 ? 100 : 0;\n"
        "    score += raw[0] == 'a' ? 1 : 0;\n"
        "    score += raw[2] == '\\0' ? 2 : 0;\n"
        "    score += raw[4] == 'd' ? 4 : 0;\n"
        "    Py_DECREF(text);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_decode_length_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *bytes = PyBytes_FromStringAndSize("hello", 5);\n'
        "    PyObject *text = NULL;\n"
        "    PyObject *same = NULL;\n"
        "    PyObject *bad = NULL;\n"
        "    const char *raw = NULL;\n"
        "    long score = 0;\n"
        "    if (bytes == NULL) return NULL;\n"
        '    text = PyUnicode_FromEncodedObject(bytes, "utf-8", NULL);\n'
        "    Py_DECREF(bytes);\n"
        "    if (text == NULL) return NULL;\n"
        "    score += PyUnicode_GetLength(text) == 5 ? 1 : 0;\n"
        "    raw = PyUnicode_AsUTF8(text);\n"
        "    if (raw == NULL) return NULL;\n"
        "    score += raw[0] == 'h' && raw[4] == 'o' ? 10 : 0;\n"
        "    same = PyUnicode_FromEncodedObject(text, NULL, NULL);\n"
        "    if (same == NULL) return NULL;\n"
        "    score += same == text ? 100 : 0;\n"
        "    Py_DECREF(same);\n"
        "    bad = PyLong_FromLong(1);\n"
        "    if (bad == NULL) return NULL;\n"
        "    score += PyUnicode_FromEncodedObject(bad, NULL, NULL) == NULL && PyErr_ExceptionMatches(PyExc_TypeError) ? 1000 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(bad);\n"
        "    Py_DECREF(text);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_encode_concat_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *left = PyUnicode_FromString("ab");\n'
        '    PyObject *right = PyUnicode_FromString("CD");\n'
        "    PyObject *joined = NULL;\n"
        "    PyObject *utf8 = NULL;\n"
        "    PyObject *ascii = NULL;\n"
        "    PyObject *encoded_utf8 = NULL;\n"
        "    PyObject *latin1 = NULL;\n"
        "    PyObject *non_ascii = NULL;\n"
        "    PyObject *replacement = NULL;\n"
        "    PyObject *replaced = NULL;\n"
        "    PyObject *substring = NULL;\n"
        "    PyObject *bad_ascii = NULL;\n"
        "    PyObject *bad_encoded_ascii = NULL;\n"
        "    char *raw = NULL;\n"
        "    Py_ssize_t n = 0;\n"
        "    long score = 0;\n"
        "    if (left == NULL || right == NULL) return NULL;\n"
        "    joined = PyUnicode_Concat(left, right);\n"
        "    if (joined == NULL) return NULL;\n"
        "    score += PyUnicode_GetLength(joined) == 4 ? 1 : 0;\n"
        "    const char *joined_raw = PyUnicode_AsUTF8(joined);\n"
        "    score += joined_raw != NULL && joined_raw[0] == 'a' && joined_raw[2] == 'C' ? 10 : 0;\n"
        "    utf8 = PyUnicode_AsUTF8String(joined);\n"
        "    ascii = PyUnicode_AsASCIIString(joined);\n"
        "    if (utf8 == NULL || ascii == NULL) return NULL;\n"
        "    if (PyBytes_AsStringAndSize(utf8, &raw, &n) != 0) return NULL;\n"
        "    score += n == 4 ? 100 : 0;\n"
        "    score += raw[2] == 'C' ? 1000 : 0;\n"
        "    if (PyBytes_AsStringAndSize(ascii, &raw, &n) != 0) return NULL;\n"
        "    score += n == 4 ? 10000 : 0;\n"
        "    score += raw[3] == 'D' ? 100000 : 0;\n"
        '    encoded_utf8 = PyUnicode_AsEncodedString(joined, "utf-8", NULL);\n'
        "    if (encoded_utf8 == NULL) return NULL;\n"
        "    if (PyBytes_AsStringAndSize(encoded_utf8, &raw, &n) != 0) return NULL;\n"
        "    score += n == 4 && raw[1] == 'b' ? 2000000 : 0;\n"
        "    score += PyUnicode_Tailmatch(joined, right, 0, -1, -1) == 1 && PyUnicode_Tailmatch(joined, left, 0, -1, 1) == 1 ? 32000000 : 0;\n"
        '    replacement = PyUnicode_FromString("xy");\n'
        "    if (replacement == NULL) return NULL;\n"
        "    replaced = PyUnicode_Replace(joined, right, replacement, 1);\n"
        "    if (replaced == NULL) return NULL;\n"
        "    const char *replaced_raw = PyUnicode_AsUTF8(replaced);\n"
        "    score += replaced_raw != NULL && replaced_raw[2] == 'x' && PyUnicode_GetLength(replaced) == 4 ? 64000000 : 0;\n"
        "    substring = PyUnicode_Substring(replaced, 1, 3);\n"
        "    if (substring == NULL) return NULL;\n"
        "    const char *substring_raw = PyUnicode_AsUTF8(substring);\n"
        "    score += substring_raw != NULL && substring_raw[0] == 'b' && substring_raw[1] == 'x' ? 128000000 : 0;\n"
        "    score += PyUnicode_Contains(joined, right) == 1 ? 256000000 : 0;\n"
        "    const char nonascii_raw[] = {'x', (char)0xc3, (char)0xa9};\n"
        "    non_ascii = PyUnicode_FromStringAndSize(nonascii_raw, 3);\n"
        "    if (non_ascii == NULL) return NULL;\n"
        '    latin1 = PyUnicode_AsEncodedString(non_ascii, "latin1", NULL);\n'
        "    if (latin1 == NULL) return NULL;\n"
        "    if (PyBytes_AsStringAndSize(latin1, &raw, &n) != 0) return NULL;\n"
        "    score += n == 2 ? 4000000 : 0;\n"
        "    score += (unsigned char)raw[1] == 0xe9 ? 8000000 : 0;\n"
        "    bad_ascii = PyUnicode_AsASCIIString(non_ascii);\n"
        "    score += bad_ascii == NULL && PyErr_Occurred() != NULL ? 1000000 : 0;\n"
        "    PyErr_Clear();\n"
        '    bad_encoded_ascii = PyUnicode_AsEncodedString(non_ascii, "ascii", NULL);\n'
        "    score += bad_encoded_ascii == NULL && PyErr_Occurred() != NULL ? 16000000 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_XDECREF(bad_encoded_ascii);\n"
        "    Py_XDECREF(bad_ascii);\n"
        "    Py_DECREF(non_ascii);\n"
        "    Py_DECREF(substring);\n"
        "    Py_DECREF(replaced);\n"
        "    Py_DECREF(replacement);\n"
        "    Py_DECREF(latin1);\n"
        "    Py_DECREF(encoded_utf8);\n"
        "    Py_DECREF(ascii);\n"
        "    Py_DECREF(utf8);\n"
        "    Py_DECREF(joined);\n"
        "    Py_DECREF(right);\n"
        "    Py_DECREF(left);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_macro_score(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {\n"
        "    Py_UCS4 space = ' ';\n"
        "    Py_UCS4 digit = '7';\n"
        "    Py_UCS4 lower = 'a';\n"
        "    Py_UCS4 upper = 'A';\n"
        "    Py_UCS4 other = '?';\n"
        '    PyObject *text = PyUnicode_FromString("abcd");\n'
        "    long score = 0;\n"
        "    if (text == NULL) return NULL;\n"
        "    score += PyUnicode_GET_LENGTH(text) == 4 ? 1 : 0;\n"
        "    score += Py_UNICODE_ISSPACE(space) ? 10 : 0;\n"
        "    score += Py_UNICODE_ISDIGIT(digit) ? 100 : 0;\n"
        "    score += Py_UNICODE_ISDECIMAL(digit) ? 1000 : 0;\n"
        "    score += Py_UNICODE_ISNUMERIC(digit) ? 10000 : 0;\n"
        "    score += Py_UNICODE_ISLOWER(lower) ? 100000 : 0;\n"
        "    score += Py_UNICODE_ISUPPER(upper) ? 1000000 : 0;\n"
        "    score += Py_UNICODE_ISTITLE(upper) ? 10000000 : 0;\n"
        "    score += Py_UNICODE_ISALPHA(lower) && Py_UNICODE_ISALPHA(upper) ? 100000000 : 0;\n"
        "    score += Py_UNICODE_ISALNUM(lower) && Py_UNICODE_ISALNUM(digit) && !Py_UNICODE_ISALNUM(other) ? 1000000000 : 0;\n"
        "    Py_DECREF(text);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_kind_score(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {\n"
        "    Py_UCS1 ucs1[] = {'A', 0xe9};\n"
        "    Py_UCS2 ucs2[] = {0x03a9, 'b'};\n"
        "    Py_UCS4 ucs4[] = {'A', 0x1f600};\n"
        "    PyObject *text1 = NULL;\n"
        "    PyObject *text2 = NULL;\n"
        "    PyObject *text4 = NULL;\n"
        "    PyObject *find_text = NULL;\n"
        "    PyObject *find_needle = NULL;\n"
        "    PyObject *empty = NULL;\n"
        "    PyObject *ordinal = NULL;\n"
        "    PyObject *bad = NULL;\n"
        "    Py_UCS4 stack_buf[3] = {0, 0, 0};\n"
        "    Py_UCS4 tiny_buf[1] = {0};\n"
        "    Py_UCS4 *copy = NULL;\n"
        "    const char *raw = NULL;\n"
        "    Py_ssize_t n = 0;\n"
        "    long score = 0;\n"
        "    score += sizeof(Py_UCS1) == 1 && sizeof(Py_UCS2) == 2 && sizeof(Py_UCS4) == 4 && PyUnicode_1BYTE_KIND == 1 && PyUnicode_2BYTE_KIND == 2 && PyUnicode_4BYTE_KIND == 4 ? 1 : 0;\n"
        "    text1 = PyUnicode_FromKindAndData(PyUnicode_1BYTE_KIND, ucs1, 2);\n"
        "    if (text1 == NULL) return NULL;\n"
        "    raw = PyUnicode_AsUTF8AndSize(text1, &n);\n"
        "    score += raw != NULL && n == 3 && PyUnicode_GetLength(text1) == 2 && (unsigned char)raw[1] == 0xc3 && (unsigned char)raw[2] == 0xa9 ? 10 : 0;\n"
        "    text2 = PyUnicode_FromKindAndData(PyUnicode_2BYTE_KIND, ucs2, 2);\n"
        "    if (text2 == NULL) return NULL;\n"
        "    raw = PyUnicode_AsUTF8AndSize(text2, &n);\n"
        "    score += raw != NULL && n == 3 && PyUnicode_GetLength(text2) == 2 && (unsigned char)raw[0] == 0xce && (unsigned char)raw[1] == 0xa9 && raw[2] == 'b' ? 100 : 0;\n"
        "    text4 = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, ucs4, 2);\n"
        "    if (text4 == NULL) return NULL;\n"
        "    raw = PyUnicode_AsUTF8AndSize(text4, &n);\n"
        "    score += raw != NULL && n == 5 && PyUnicode_GetLength(text4) == 2 && raw[0] == 'A' && (unsigned char)raw[1] == 0xf0 && (unsigned char)raw[4] == 0x80 ? 1000 : 0;\n"
        "    score += PyUnicode_AsUCS4(text4, stack_buf, 3, 1) == stack_buf && stack_buf[0] == 'A' && stack_buf[1] == 0x1f600 && stack_buf[2] == 0 ? 10000 : 0;\n"
        "    copy = PyUnicode_AsUCS4Copy(text2);\n"
        "    if (copy == NULL) return NULL;\n"
        "    score += copy[0] == 0x03a9 && copy[1] == 'b' && copy[2] == 0 ? 100000 : 0;\n"
        "    PyMem_Free(copy);\n"
        "    copy = NULL;\n"
        "    score += PyUnicode_AsUCS4(text4, tiny_buf, 1, 1) == NULL && PyErr_ExceptionMatches(PyExc_SystemError) ? 1000000 : 0;\n"
        "    PyErr_Clear();\n"
        "    ordinal = PyUnicode_FromOrdinal(0x2665);\n"
        "    if (ordinal == NULL) return NULL;\n"
        "    score += PyUnicode_GetLength(ordinal) == 1 && PyUnicode_ReadChar(ordinal, 0) == 0x2665 ? 100000000 : 0;\n"
        "    score += PyUnicode_ReadChar(text4, 1) == 0x1f600 && PyUnicode_ReadChar(text2, 0) == 0x03a9 ? 1000000000 : 0;\n"
        '    find_text = PyUnicode_FromString("aba");\n'
        '    find_needle = PyUnicode_FromString("a");\n'
        '    empty = PyUnicode_FromString("");\n'
        "    if (find_text == NULL || find_needle == NULL || empty == NULL) return NULL;\n"
        "    score += PyUnicode_FindChar(text4, 'A', 0, 10, 1) == 0 && PyUnicode_FindChar(text4, 0x1f600, 0, 10, 1) == 1 && PyUnicode_FindChar(find_text, 'a', 0, 3, -1) == 2 && PyUnicode_FindChar(text4, 'Z', 0, 10, 1) == -1 ? 10000000000L : 0;\n"
        "    score += PyUnicode_Find(find_text, find_needle, 0, 3, 1) == 0 && PyUnicode_Find(find_text, find_needle, 0, 3, -1) == 2 && PyUnicode_Find(find_text, text2, 0, 3, 1) == -1 ? 1000000000000L : 0;\n"
        "    score += PyUnicode_Count(find_text, find_needle, 0, 3) == 2 && PyUnicode_Count(find_text, empty, 0, 3) == 4 ? 10000000000000L : 0;\n"
        "    score += PyUnicode_ReadChar(text4, 2) == (Py_UCS4)-1 && PyErr_ExceptionMatches(PyExc_IndexError) ? 100000000000L : 0;\n"
        "    PyErr_Clear();\n"
        "    bad = PyUnicode_FromKindAndData(3, ucs1, 1);\n"
        "    score += bad == NULL && PyErr_ExceptionMatches(PyExc_ValueError) ? 10000000 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_XDECREF(copy);\n"
        "    Py_XDECREF(bad);\n"
        "    Py_DECREF(ordinal);\n"
        "    Py_DECREF(empty);\n"
        "    Py_DECREF(find_needle);\n"
        "    Py_DECREF(find_text);\n"
        "    Py_DECREF(text4);\n"
        "    Py_DECREF(text2);\n"
        "    Py_DECREF(text1);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_float_bool_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *flt = PyFloat_FromDouble(3.5);\n"
        "    PyObject *truth = PyBool_FromLong(1);\n"
        "    PyObject *falsehood = PyBool_FromLong(0);\n"
        "    if (flt == NULL || truth == NULL || falsehood == NULL) return NULL;\n"
        "    long score = (long)(PyFloat_AsDouble(flt) * 2.0);\n"
        "    score += PyObject_IsTrue(truth) * 10;\n"
        "    score += PyObject_IsTrue(falsehood);\n"
        "    Py_DECREF(flt);\n"
        "    Py_DECREF(truth);\n"
        "    Py_DECREF(falsehood);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_scalar_complex_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *flt = PyFloat_FromDouble(2.5);\n"
        "    PyObject *truth = PyBool_FromLong(1);\n"
        "    PyObject *z = PyComplex_FromDoubles(3.0, 4.0);\n"
        "    Py_complex raw;\n"
        "    Py_complex made;\n"
        "    Py_complex bad;\n"
        "    PyObject *from_c = NULL;\n"
        "    PyObject *text = NULL;\n"
        "    long score = 0;\n"
        "    if (flt == NULL || truth == NULL || z == NULL) return NULL;\n"
        "    score += PyFloat_Check(flt) && PyFloat_CheckExact(flt) && !PyFloat_Check(truth) ? 1 : 0;\n"
        "    score += PyBool_Check(truth) && !PyBool_Check(flt) ? 10 : 0;\n"
        "    score += PyComplex_Check(z) && PyComplex_CheckExact(z) && !PyComplex_Check(flt) ? 100 : 0;\n"
        "    score += PyComplex_RealAsDouble(z) == 3.0 ? 1000 : 0;\n"
        "    score += PyComplex_ImagAsDouble(z) == 4.0 ? 10000 : 0;\n"
        "    raw = PyComplex_AsCComplex(z);\n"
        "    score += raw.real == 3.0 && raw.imag == 4.0 ? 100000 : 0;\n"
        "    score += PyComplex_RealAsDouble(flt) == 2.5 && PyComplex_ImagAsDouble(flt) == 0.0 ? 1000000 : 0;\n"
        "    made.real = 5.0;\n"
        "    made.imag = 6.0;\n"
        "    from_c = PyComplex_FromCComplex(made);\n"
        "    if (from_c == NULL) return NULL;\n"
        "    score += PyComplex_RealAsDouble(from_c) == 5.0 && PyComplex_ImagAsDouble(from_c) == 6.0 ? 10000000 : 0;\n"
        '    text = PyUnicode_FromString("x");\n'
        "    if (text == NULL) return NULL;\n"
        "    bad = PyComplex_AsCComplex(text);\n"
        "    score += bad.real == -1.0 && bad.imag == 0.0 && PyErr_ExceptionMatches(PyExc_TypeError) ? 100000000 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(text);\n"
        "    Py_DECREF(from_c);\n"
        "    Py_DECREF(z);\n"
        "    Py_DECREF(truth);\n"
        "    Py_DECREF(flt);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_set_attr(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;\n'
        "    PyObject *value = PyLong_FromLong(64);\n"
        "    if (value == NULL) return NULL;\n"
        '    if (PyObject_SetAttrString(obj, "answer", value) != 0) {\n'
        "        Py_DECREF(value);\n"
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(value);\n"
        '    return PyObject_GetAttrString(obj, "answer");\n'
        "}\n"
        "\n"
        "static PyObject *objectapi_has_attr(PyObject *self, PyObject *args) {\n"
        "    PyObject *obj = NULL;\n"
        "    const char *name = NULL;\n"
        "    (void)self;\n"
        '    if (!PyArg_ParseTuple(args, "Os", &obj, &name)) return NULL;\n'
        "    return PyLong_FromLong(PyObject_HasAttrString(obj, name));\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_format_error(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *name = PyUnicode_FromString("thing");\n'
        "    if (name == NULL) return NULL;\n"
        '    PyErr_Format(PyExc_ValueError, "bad %s %ld %.5R", "value", 7L, name);\n'
        "    Py_DECREF(name);\n"
        "    int ok = PyErr_Occurred() != NULL;\n"
        "    PyErr_Clear();\n"
        "    return PyLong_FromLong(ok);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_err_format_v(PyObject *type, const char *format, ...) {\n"
        "    __builtin_va_list ap;\n"
        "    __builtin_va_start(ap, format);\n"
        "    PyObject *result = PyErr_FormatV(type, format, ap);\n"
        "    __builtin_va_end(ap);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_warning_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        '    score += PyErr_WarnEx(PyExc_UserWarning, "heads up", 1) == 0 ? 100 : 0;\n'
        '    score += PyErr_WarnFormat(PyExc_RuntimeWarning, 1, "warn %s %ld", "value", 7L) == 0 ? 10000 : 0;\n'
        "    score += PyErr_Occurred() == NULL ? 10 : 0;\n"
        '    PyErr_SetString(PyExc_RuntimeError, "ignored");\n'
        "    PyErr_WriteUnraisable(Py_None);\n"
        "    score += PyErr_Occurred() == NULL ? 1 : 0;\n"
        "    score += PyExc_Warning != NULL && PyExc_RuntimeWarning != NULL && PyExc_DeprecationWarning != NULL && PyExc_FutureWarning != NULL ? 1000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_gil_state_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    score += Py_IsInitialized() ? 1 : 0;\n"
        "    PyGILState_STATE state = PyGILState_Ensure();\n"
        "    score += PyGILState_Check() ? 10 : 0;\n"
        "    PyGILState_Release(state);\n"
        "    score += PyGILState_Check() ? 100 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_error_helper_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    PyErr_SetNone(PyExc_MemoryError);\n"
        "    score += PyErr_ExceptionMatches(PyExc_MemoryError) ? 10 : 0;\n"
        "    PyErr_Clear();\n"
        "    score += PyErr_Occurred() == NULL ? 1 : 0;\n"
        "    PyErr_BadInternalCall();\n"
        "    score += PyErr_ExceptionMatches(PyExc_SystemError) ? 100 : 0;\n"
        "    PyErr_Clear();\n"
        "    score += PyErr_Occurred() == NULL ? 1000 : 0;\n"
        "    score += PyErr_SetFromErrno(PyExc_OSError) == NULL && PyErr_ExceptionMatches(PyExc_OSError) ? 10000 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyObject *filename = PyUnicode_FromString("missing.dat");\n'
        "    if (filename == NULL) return NULL;\n"
        "    score += PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, filename) == NULL && PyErr_ExceptionMatches(PyExc_OSError) ? 100000 : 0;\n"
        "    Py_DECREF(filename);\n"
        "    PyErr_Clear();\n"
        '    score += objectapi_err_format_v(PyExc_ValueError, "bad %s %ld", "value", 9L) == NULL && PyErr_ExceptionMatches(PyExc_ValueError) ? 1000000 : 0;\n'
        "    PyErr_Clear();\n"
        "    score += PyErr_CheckSignals() == 0 ? 10000000 : 0;\n"
        '    PyErr_SetString(PyExc_RuntimeError, "printed error");\n'
        "    PyErr_Print();\n"
        "    score += PyErr_Occurred() == NULL ? 100000000 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_exception_global_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    score += PyExc_BaseException != NULL && PyExc_Exception != NULL && PyExc_ArithmeticError != NULL && PyExc_LookupError != NULL && PyExc_OSError != NULL && PyExc_IOError != NULL && PyExc_AssertionError != NULL && PyExc_StopIteration != NULL && PyExc_StopAsyncIteration != NULL && PyExc_ZeroDivisionError != NULL && PyExc_ReferenceError != NULL && PyExc_BufferError != NULL && PyExc_ImportError != NULL && PyExc_ImportWarning != NULL && PyExc_FloatingPointError != NULL && PyExc_RecursionError != NULL && PyExc_UnicodeDecodeError != NULL ? 1 : 0;\n"
        "    score += PyExc_OSError == PyExc_IOError ? 2 : 0;\n"
        '    PyErr_SetString(PyExc_OSError, "os");\n'
        "    score += PyErr_ExceptionMatches(PyExc_OSError) ? 4 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_IOError, "io");\n'
        "    score += PyErr_ExceptionMatches(PyExc_OSError) && PyErr_ExceptionMatches(PyExc_IOError) ? 8 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_ZeroDivisionError, "zero");\n'
        "    score += PyErr_ExceptionMatches(PyExc_ArithmeticError) && PyErr_ExceptionMatches(PyExc_ZeroDivisionError) ? 16 : 0;\n"
        "    PyErr_Clear();\n"
        "    PyErr_SetNone(PyExc_StopIteration);\n"
        "    score += PyErr_ExceptionMatches(PyExc_Exception) && PyErr_ExceptionMatches(PyExc_StopIteration) ? 32 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_AssertionError, "assert");\n'
        "    score += PyErr_ExceptionMatches(PyExc_AssertionError) ? 64 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_ReferenceError, "ref");\n'
        "    score += PyErr_ExceptionMatches(PyExc_ReferenceError) ? 128 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_ImportError, "import");\n'
        "    score += PyErr_Occurred() != NULL ? 256 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_BufferError, "buffer");\n'
        "    score += PyErr_Occurred() != NULL ? 512 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_RecursionError, "recursion");\n'
        "    score += PyErr_ExceptionMatches(PyExc_RuntimeError) ? 1024 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_UnicodeDecodeError, "unicode");\n'
        "    score += PyErr_ExceptionMatches(PyExc_ValueError) ? 2048 : 0;\n"
        "    PyErr_Clear();\n"
        '    PyErr_SetString(PyExc_FloatingPointError, "float");\n'
        "    score += PyErr_ExceptionMatches(PyExc_ArithmeticError) ? 4096 : 0;\n"
        "    PyErr_Clear();\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_module_dict_magic(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    PyObject *dict = PyModule_GetDict(self);\n"
        "    if (dict == NULL) return NULL;\n"
        '    PyObject *magic = PyDict_GetItemString(dict, "MAGIC");\n'
        "    if (magic == NULL) return NULL;\n"
        "    return PyLong_FromLong(PyLong_AsLong(magic) + 1);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_bump_module_global(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    PyObject *dict = PyModule_GetDict(self);\n"
        "    if (dict == NULL) return NULL;\n"
        '    PyObject *current = PyDict_GetItemString(dict, "SEEN");\n'
        "    long next = current == NULL ? 1 : PyLong_AsLong(current) + 1;\n"
        "    PyObject *value = PyLong_FromLong(next);\n"
        "    if (value == NULL) return NULL;\n"
        '    if (PyDict_SetItemString(dict, "SEEN", value) != 0) {\n'
        "        Py_DECREF(value);\n"
        "        return NULL;\n"
        "    }\n"
        "    Py_DECREF(value);\n"
        "    return PyLong_FromLong(next);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_delete_module_global(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    PyObject *dict = PyModule_GetDict(self);\n"
        "    if (dict == NULL) return NULL;\n"
        '    if (PyDict_DelItemString(dict, "SEEN") != 0) return NULL;\n'
        '    return PyLong_FromLong(PyDict_GetItemString(dict, "SEEN") == NULL ? 1 : 0);\n'
        "}\n"
        "\n"
        "static PyObject *objectapi_str_repr_compare(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *text = PyUnicode_FromString("abc");\n'
        "    PyObject *as_str = NULL;\n"
        "    PyObject *as_repr = NULL;\n"
        "    PyObject *raw = NULL;\n"
        "    PyObject *as_bytes = NULL;\n"
        "    PyObject *format_spec = NULL;\n"
        "    PyObject *formatted = NULL;\n"
        "    PyObject *rich = NULL;\n"
        "    long score = 0;\n"
        "    if (text == NULL) return NULL;\n"
        "    as_str = PyObject_Str(text);\n"
        "    as_repr = PyObject_Repr(text);\n"
        '    raw = PyBytes_FromStringAndSize("xy", 2);\n'
        '    format_spec = PyUnicode_FromString(">5");\n'
        "    if (as_str == NULL || as_repr == NULL || raw == NULL || format_spec == NULL) return NULL;\n"
        "    as_bytes = PyObject_Bytes(raw);\n"
        "    if (as_bytes == NULL) return NULL;\n"
        "    formatted = PyObject_Format(text, format_spec);\n"
        "    if (formatted == NULL) return NULL;\n"
        "    score += PyObject_RichCompareBool(text, as_str, Py_EQ) * 10;\n"
        "    rich = PyObject_RichCompare(text, as_str, Py_EQ);\n"
        "    if (rich == NULL) return NULL;\n"
        "    score += PyObject_IsTrue(rich) * 100;\n"
        "    const char *repr_raw = PyUnicode_AsUTF8(as_repr);\n"
        "    if (repr_raw != NULL && repr_raw[0] == '\\'') score += 1;\n"
        "    char *bytes_raw = PyBytes_AsString(as_bytes);\n"
        "    score += PyBytes_Size(as_bytes) == 2 ? 1000 : 0;\n"
        "    score += bytes_raw != NULL && bytes_raw[0] == 'x' && bytes_raw[1] == 'y' ? 10000 : 0;\n"
        "    const char *formatted_raw = PyUnicode_AsUTF8(formatted);\n"
        "    score += formatted_raw != NULL && formatted_raw[0] == ' ' && formatted_raw[1] == ' ' && formatted_raw[2] == 'a' ? 100000 : 0;\n"
        "    Py_DECREF(rich);\n"
        "    Py_DECREF(formatted);\n"
        "    Py_DECREF(format_spec);\n"
        "    Py_DECREF(as_bytes);\n"
        "    Py_DECREF(raw);\n"
        "    Py_DECREF(as_repr);\n"
        "    Py_DECREF(as_str);\n"
        "    Py_DECREF(text);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_print_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *text = PyUnicode_FromString("abc");\n'
        "    FILE *fp = NULL;\n"
        "    char buf[16];\n"
        "    size_t n = 0;\n"
        "    long score = 0;\n"
        "    if (text == NULL) return NULL;\n"
        "    fp = tmpfile();\n"
        "    if (fp == NULL) {\n"
        "        Py_DECREF(text);\n"
        '        PyErr_SetString(PyExc_OSError, "tmpfile failed");\n'
        "        return NULL;\n"
        "    }\n"
        "    if (PyObject_Print(text, fp, Py_PRINT_RAW) != 0) {\n"
        "        fclose(fp);\n"
        "        Py_DECREF(text);\n"
        "        return NULL;\n"
        "    }\n"
        "    if (PyObject_Print(text, fp, 0) != 0) {\n"
        "        fclose(fp);\n"
        "        Py_DECREF(text);\n"
        "        return NULL;\n"
        "    }\n"
        "    fflush(fp);\n"
        "    fseek(fp, 0, SEEK_SET);\n"
        "    n = fread(buf, 1, sizeof(buf), fp);\n"
        "    fclose(fp);\n"
        "    Py_DECREF(text);\n"
        "    score += n == 8 ? 1 : 0;\n"
        "    score += n >= 3 && buf[0] == 'a' && buf[2] == 'c' ? 10 : 0;\n"
        "    score += n >= 8 && buf[3] == '\\'' && buf[7] == '\\'' ? 100 : 0;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unsigned_sum(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *a = PyLong_FromUnsignedLong(5UL);\n"
        "    PyObject *b = PyLong_FromUnsignedLongLong(6ULL);\n"
        "    if (a == NULL || b == NULL) return NULL;\n"
        "    long total = PyLong_AsLong(a) + PyLong_AsLong(b);\n"
        "    Py_DECREF(a);\n"
        "    Py_DECREF(b);\n"
        "    return PyLong_FromLong(total);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_import_self_magic(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *module = PyImport_ImportModule("objectapi");\n'
        "    if (module == NULL) return NULL;\n"
        '    PyObject *magic = PyObject_GetAttrString(module, "MAGIC");\n'
        "    Py_DECREF(module);\n"
        "    if (magic == NULL) return NULL;\n"
        "    long value = PyLong_AsLong(magic);\n"
        "    Py_DECREF(magic);\n"
        "    return PyLong_FromLong(value);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_sequence_score(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    PyObject *a = PyLong_FromLong(3);\n"
        "    PyObject *b = PyLong_FromLong(4);\n"
        "    PyObject *tuple = PyTuple_Pack(2, a, b);\n"
        "    PyObject *list = NULL;\n"
        "    PyObject *tuple2 = NULL;\n"
        "    PyObject *fast = NULL;\n"
        "    PyObject *item = NULL;\n"
        "    PyObject *replacement = NULL;\n"
        "    PyObject *concat = NULL;\n"
        "    PyObject *repeat = NULL;\n"
        "    PyObject *inplace_concat = NULL;\n"
        "    PyObject *inplace_repeat = NULL;\n"
        "    PyObject *bytes = NULL;\n"
        "    PyObject *interned = NULL;\n"
        "    long score = 0;\n"
        "    Py_DECREF(a);\n"
        "    Py_DECREF(b);\n"
        "    if (tuple == NULL) return NULL;\n"
        "    list = PySequence_List(tuple);\n"
        "    tuple2 = PySequence_Tuple(list);\n"
        '    fast = PySequence_Fast(list, "expected sequence");\n'
        '    bytes = PyBytes_FromStringAndSize("x", 1);\n'
        '    interned = PyUnicode_InternFromString("name");\n'
        "    if (list == NULL || tuple2 == NULL || fast == NULL || bytes == NULL || interned == NULL) return NULL;\n"
        "    PyObject *dict = PyModule_GetDict(self);\n"
        "    if (dict == NULL) return NULL;\n"
        "    score += PyTuple_Check(tuple) + PyTuple_CheckExact(tuple);\n"
        "    score += PyList_Check(list) + PyList_CheckExact(list);\n"
        "    score += PyDict_Check(dict) + PyDict_CheckExact(dict);\n"
        "    score += PyBytes_Check(bytes) + PyBytes_CheckExact(bytes);\n"
        "    score += PyUnicode_Check(interned) + PyUnicode_CheckExact(interned);\n"
        "    score += PySequence_Check(list) + PySequence_Check(tuple2);\n"
        "    score += PySequence_Size(list);\n"
        "    score += PySequence_Length(tuple2);\n"
        "    item = PySequence_GetItem(tuple2, 1);\n"
        "    if (item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item);\n"
        "    PyObject **items = PySequence_Fast_ITEMS(fast);\n"
        "    if (items == NULL) return NULL;\n"
        "    score += PyLong_AsLong(items[0]);\n"
        "    score += PySequence_Fast_GET_SIZE(fast);\n"
        "    score += PyTuple_Check(tuple2);\n"
        "    Py_DECREF(item);\n"
        "    item = NULL;\n"
        "    replacement = PyLong_FromLong(9);\n"
        "    if (replacement == NULL) return NULL;\n"
        "    if (PySequence_SetItem(list, 0, replacement) != 0) return NULL;\n"
        "    Py_DECREF(replacement);\n"
        "    item = PySequence_GetItem(list, 0);\n"
        "    if (item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 9 ? 100 : 0;\n"
        "    Py_DECREF(item);\n"
        "    concat = PySequence_Concat(tuple, tuple2);\n"
        "    if (concat == NULL) return NULL;\n"
        "    score += PyTuple_Check(concat) && PySequence_Size(concat) == 4 ? 1000 : 0;\n"
        "    Py_DECREF(concat);\n"
        "    repeat = PySequence_Repeat(tuple, 2);\n"
        "    if (repeat == NULL) return NULL;\n"
        "    score += PyTuple_Check(repeat) && PySequence_Size(repeat) == 4 ? 10000 : 0;\n"
        "    Py_DECREF(repeat);\n"
        "    inplace_concat = PySequence_InPlaceConcat(tuple, tuple2);\n"
        "    if (inplace_concat == NULL) return NULL;\n"
        "    score += PyTuple_Check(inplace_concat) && PySequence_Size(inplace_concat) == 4 ? 20000 : 0;\n"
        "    Py_DECREF(inplace_concat);\n"
        "    inplace_repeat = PySequence_InPlaceRepeat(bytes, 3);\n"
        "    if (inplace_repeat == NULL) return NULL;\n"
        "    score += PyBytes_Check(inplace_repeat) && PyBytes_Size(inplace_repeat) == 3 ? 40000 : 0;\n"
        "    Py_DECREF(inplace_repeat);\n"
        "    Py_DECREF(interned);\n"
        "    Py_DECREF(bytes);\n"
        "    Py_DECREF(fast);\n"
        "    Py_DECREF(tuple2);\n"
        "    Py_DECREF(list);\n"
        "    Py_DECREF(tuple);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_iter_contains_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *list = PyList_New(2);\n"
        "    PyObject *dict = PyDict_New();\n"
        '    PyObject *key = PyUnicode_FromString("k");\n'
        "    PyObject *needle = PyLong_FromLong(4);\n"
        "    PyObject *missing = PyLong_FromLong(5);\n"
        "    PyObject *iter = NULL;\n"
        "    PyObject *item = NULL;\n"
        "    PyObject *self_iter = NULL;\n"
        "    int status = 0;\n"
        "    long score = 0;\n"
        "    if (list == NULL || dict == NULL || key == NULL || needle == NULL || missing == NULL) return NULL;\n"
        "    if (PyList_SetItem(list, 0, PyLong_FromLong(3)) != 0) return NULL;\n"
        "    if (PyList_SetItem(list, 1, PyLong_FromLong(4)) != 0) return NULL;\n"
        "    iter = PyObject_GetIter(list);\n"
        "    if (iter == NULL) return NULL;\n"
        "    score += PyIter_Check(iter) ? 1 : 0;\n"
        "    score += !PyIter_Check(list) ? 10 : 0;\n"
        "    item = PyIter_Next(iter);\n"
        "    if (item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 3 ? 100 : 0;\n"
        "    Py_DECREF(item);\n"
        "    item = PyIter_Next(iter);\n"
        "    if (item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 4 ? 1000 : 0;\n"
        "    Py_DECREF(item);\n"
        "    item = PyIter_Next(iter);\n"
        "    score += item == NULL && PyErr_Occurred() == NULL ? 10000 : 0;\n"
        "    Py_DECREF(iter);\n"
        "    iter = PyObject_GetIter(list);\n"
        "    if (iter == NULL) return NULL;\n"
        "    status = PyIter_NextItem(iter, &item);\n"
        "    if (status != 1 || item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 3 ? 100000000 : 0;\n"
        "    Py_DECREF(item);\n"
        "    status = PyIter_NextItem(iter, &item);\n"
        "    if (status != 1 || item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 4 ? 200000000 : 0;\n"
        "    Py_DECREF(item);\n"
        "    item = needle;\n"
        "    status = PyIter_NextItem(iter, &item);\n"
        "    score += status == 0 && item == NULL && PyErr_Occurred() == NULL ? 400000000 : 0;\n"
        "    self_iter = PyObject_SelfIter(iter);\n"
        "    if (self_iter == NULL) return NULL;\n"
        "    score += self_iter == iter ? 20000000 : 0;\n"
        "    Py_DECREF(self_iter);\n"
        "    item = needle;\n"
        "    status = PyIter_NextItem(list, &item);\n"
        "    score += status == -1 && item == NULL && PyErr_Occurred() != NULL ? 800000000 : 0;\n"
        "    PyErr_Clear();\n"
        "    score += PySequence_Contains(list, needle) == 1 ? 100000 : 0;\n"
        "    score += PySequence_Contains(list, missing) == 0 ? 1000000 : 0;\n"
        "    if (PyDict_SetItem(dict, key, needle) != 0) return NULL;\n"
        "    score += PySequence_Contains(dict, key) == 1 ? 10000000 : 0;\n"
        "    Py_DECREF(iter);\n"
        "    Py_DECREF(missing);\n"
        "    Py_DECREF(needle);\n"
        "    Py_DECREF(key);\n"
        "    Py_DECREF(dict);\n"
        "    Py_DECREF(list);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_collection_views_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *dict = PyDict_New();\n"
        '    PyObject *key1 = PyUnicode_FromString("a");\n'
        '    PyObject *key2 = PyUnicode_FromString("b");\n'
        "    PyObject *value1 = PyLong_FromLong(11);\n"
        "    PyObject *value2 = PyLong_FromLong(22);\n"
        "    PyObject *keys = NULL;\n"
        "    PyObject *values = NULL;\n"
        "    PyObject *items = NULL;\n"
        "    PyObject *map_keys = NULL;\n"
        "    PyObject *map_values = NULL;\n"
        "    PyObject *map_items = NULL;\n"
        "    PyObject *key_tuple = NULL;\n"
        "    PyObject *item0 = NULL;\n"
        "    PyObject *item0_key = NULL;\n"
        "    PyObject *item0_value = NULL;\n"
        "    PyObject *dict_value = NULL;\n"
        "    long score = 0;\n"
        "    if (dict == NULL || key1 == NULL || key2 == NULL || value1 == NULL || value2 == NULL) return NULL;\n"
        "    if (PyDict_SetItem(dict, key1, value1) != 0) return NULL;\n"
        "    if (PyDict_SetItem(dict, key2, value2) != 0) return NULL;\n"
        "    keys = PyDict_Keys(dict);\n"
        "    values = PyDict_Values(dict);\n"
        "    items = PyDict_Items(dict);\n"
        "    map_keys = PyMapping_Keys(dict);\n"
        "    map_values = PyMapping_Values(dict);\n"
        "    map_items = PyMapping_Items(dict);\n"
        "    if (keys == NULL || values == NULL || items == NULL || map_keys == NULL || map_values == NULL || map_items == NULL) return NULL;\n"
        "    key_tuple = PyList_AsTuple(keys);\n"
        "    if (key_tuple == NULL) return NULL;\n"
        "    score += PyList_Check(keys) ? 1 : 0;\n"
        "    score += PyList_Size(keys) == 2 ? 10 : 0;\n"
        "    score += PyList_Size(values) == 2 ? 100 : 0;\n"
        "    score += PyList_Size(items) == 2 ? 1000 : 0;\n"
        "    score += PyTuple_Check(key_tuple) ? 10000 : 0;\n"
        "    score += PyTuple_Size(key_tuple) == 2 ? 100000 : 0;\n"
        "    score += PySequence_Contains(key_tuple, key1) == 1 ? 1000000 : 0;\n"
        "    score += PyList_Size(map_keys) == 2 ? 2 : 0;\n"
        "    score += PyList_Size(map_values) == 2 ? 20 : 0;\n"
        "    score += PyList_Size(map_items) == 2 ? 200 : 0;\n"
        "    score += PyObject_LengthHint(values, 99) == 2 ? 300 : 0;\n"
        "    score += PyObject_LengthHint(value1, 77) == 77 ? 400 : 0;\n"
        "    item0 = PyList_GetItem(items, 0);\n"
        "    if (item0 == NULL) return NULL;\n"
        "    score += PyTuple_Check(item0) && PyTuple_Size(item0) == 2 ? 10000000 : 0;\n"
        "    item0_key = PyTuple_GetItem(item0, 0);\n"
        "    item0_value = PyTuple_GetItem(item0, 1);\n"
        "    if (item0_key == NULL || item0_value == NULL) return NULL;\n"
        "    dict_value = PyDict_GetItem(dict, item0_key);\n"
        "    score += dict_value == item0_value ? 100000000 : 0;\n"
        "    score += PySequence_Contains(values, value2) == 1 ? 1000000000 : 0;\n"
        "    Py_DECREF(map_items);\n"
        "    Py_DECREF(map_values);\n"
        "    Py_DECREF(map_keys);\n"
        "    Py_DECREF(key_tuple);\n"
        "    Py_DECREF(items);\n"
        "    Py_DECREF(values);\n"
        "    Py_DECREF(keys);\n"
        "    Py_DECREF(value2);\n"
        "    Py_DECREF(value1);\n"
        "    Py_DECREF(key2);\n"
        "    Py_DECREF(key1);\n"
        "    Py_DECREF(dict);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_set_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *one = PyLong_FromLong(1);\n"
        "    PyObject *two = PyLong_FromLong(2);\n"
        "    PyObject *three = PyLong_FromLong(3);\n"
        "    PyObject *missing = PyLong_FromLong(99);\n"
        "    PyObject *tuple = NULL;\n"
        "    PyObject *set = NULL;\n"
        "    long score = 0;\n"
        "    if (one == NULL || two == NULL || three == NULL || missing == NULL) return NULL;\n"
        "    tuple = PyTuple_Pack(3, one, two, two);\n"
        "    if (tuple == NULL) return NULL;\n"
        "    set = PySet_New(tuple);\n"
        "    if (set == NULL) return NULL;\n"
        "    score += PySet_Check(set) ? 1 : 0;\n"
        "    score += PySet_CheckExact(set) ? 10 : 0;\n"
        "    score += PyAnySet_Check(set) ? 100 : 0;\n"
        "    score += PyAnySet_CheckExact(set) ? 1000 : 0;\n"
        "    score += PySet_Size(set) == 2 ? 10000 : 0;\n"
        "    score += PySet_GET_SIZE(set) == 2 ? 100000 : 0;\n"
        "    score += PySet_Contains(set, two) == 1 ? 1000000 : 0;\n"
        "    score += (PySet_Add(set, three) == 0 && PySet_Contains(set, three) == 1 && PySet_Size(set) == 3) ? 10000000 : 0;\n"
        "    score += (PySet_Discard(set, two) == 1 && PySet_Contains(set, two) == 0 && PySet_Size(set) == 2) ? 100000000 : 0;\n"
        "    score += PySet_Discard(set, missing) == 0 ? 1000000000 : 0;\n"
        "    Py_DECREF(set);\n"
        "    Py_DECREF(tuple);\n"
        "    Py_DECREF(missing);\n"
        "    Py_DECREF(three);\n"
        "    Py_DECREF(two);\n"
        "    Py_DECREF(one);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_attr_hash_score(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        '    PyObject *magic_name = PyUnicode_FromString("MAGIC");\n'
        "    PyObject *magic = PyObject_GetAttr(self, magic_name);\n"
        '    PyObject *attr_name = PyUnicode_FromString("LABEL2");\n'
        '    PyObject *attr_value = PyUnicode_FromString("set");\n'
        '    PyObject *missing_name = PyUnicode_FromString("MISSING_ATTR");\n'
        "    PyObject *got = NULL;\n"
        "    PyObject *method = NULL;\n"
        "    PyObject *optional = NULL;\n"
        "    long score = 0;\n"
        "    if (magic_name == NULL || magic == NULL || attr_name == NULL || attr_value == NULL || missing_name == NULL) return NULL;\n"
        "    score += PyLong_AsLong(magic);\n"
        "    if (PyObject_SetAttr(self, attr_name, attr_value) != 0) return NULL;\n"
        "    got = PyObject_GetAttr(self, attr_name);\n"
        '    method = PyObject_GetAttrString(self, "tuple_sum");\n'
        "    if (got == NULL || method == NULL) return NULL;\n"
        '    score += (PyUnicode_CompareWithASCIIString(got, "set") == 0) ? 1 : 0;\n'
        "    score += (PyUnicode_Compare(got, attr_value) == 0) ? 10 : 0;\n"
        "    score += (PyObject_Hash(got) != -1) ? 100 : 0;\n"
        "    score += PyCallable_Check(method) ? 1000 : 0;\n"
        "    score += PyCallable_Check(self) ? 0 : 10000;\n"
        "    score += PyObject_HasAttr(self, magic_name) ? 100000 : 0;\n"
        "    score += PyObject_HasAttrWithError(self, attr_name) == 1 ? 200000 : 0;\n"
        '    score += PyObject_HasAttrStringWithError(self, "LABEL2") == 1 ? 300000 : 0;\n'
        "    score += PyObject_HasAttrWithError(self, missing_name) == 0 ? 400000 : 0;\n"
        '    score += PyObject_HasAttrStringWithError(self, "MISSING_ATTR") == 0 ? 500000 : 0;\n'
        '    score += PyObject_HasAttrStringWithError(NULL, "x") == -1 && PyErr_ExceptionMatches(PyExc_TypeError) ? 600000 : 0;\n'
        "    PyErr_Clear();\n"
        "    score += PyObject_HasAttr(self, missing_name) == 0 ? 700000 : 0;\n"
        "    score += PyObject_GetOptionalAttr(self, attr_name, &optional) == 1 && optional != NULL ? 800000 : 0;\n"
        "    Py_XDECREF(optional);\n"
        "    optional = NULL;\n"
        '    score += PyObject_GetOptionalAttrString(self, "LABEL2", &optional) == 1 && optional != NULL ? 900000 : 0;\n'
        "    Py_XDECREF(optional);\n"
        "    optional = NULL;\n"
        "    score += PyObject_GetOptionalAttr(self, missing_name, &optional) == 0 && optional == NULL ? 1000000 : 0;\n"
        '    score += PyObject_GetOptionalAttrString(NULL, "x", &optional) == -1 && PyErr_ExceptionMatches(PyExc_TypeError) ? 1100000 : 0;\n'
        "    PyErr_Clear();\n"
        "    Py_DECREF(method);\n"
        "    Py_DECREF(got);\n"
        "    Py_DECREF(missing_name);\n"
        "    Py_DECREF(attr_value);\n"
        "    Py_DECREF(attr_name);\n"
        "    Py_DECREF(magic);\n"
        "    Py_DECREF(magic_name);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_build_mem_kw_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    char *buf = (char *)PyMem_Malloc(3);\n"
        "    char *zero = NULL;\n"
        "    PyObject *built = NULL;\n"
        "    PyObject *kw_args = NULL;\n"
        "    PyObject *kwargs = NULL;\n"
        "    PyObject *name = NULL;\n"
        "    PyObject *value_obj = NULL;\n"
        "    PyObject *owned = NULL;\n"
        "    PyObject *again = NULL;\n"
        "    PyObject *slot = NULL;\n"
        "    PyObject *xslot = NULL;\n"
        "    const char *built_text = NULL;\n"
        '    char *kwlist[] = {"value", "name", NULL};\n'
        "    long value = 0;\n"
        '    const char *text = "unset";\n'
        "    char *raw = NULL;\n"
        "    Py_ssize_t raw_len = 0;\n"
        "    if (buf == NULL) return PyErr_NoMemory();\n"
        "    buf[0] = 'o'; buf[1] = 'k'; buf[2] = '\\0';\n"
        "    buf = (char *)PyMem_Realloc(buf, 4);\n"
        "    if (buf == NULL) return PyErr_NoMemory();\n"
        "    buf[2] = '!'; buf[3] = '\\0';\n"
        "    zero = (char *)PyMem_Calloc(2, 1);\n"
        "    if (zero == NULL) return PyErr_NoMemory();\n"
        '    built = Py_BuildValue("(is#y#)", 7, buf, (Py_ssize_t)3, "AB", (Py_ssize_t)2);\n'
        "    PyMem_Free(buf);\n"
        "    if (built == NULL) return NULL;\n"
        "    score += PyTuple_Check(built) ? 1000 : 0;\n"
        "    score += PyLong_AsLong(PyTuple_GET_ITEM(built, 0));\n"
        "    built_text = PyUnicode_AsUTF8(PyTuple_GET_ITEM(built, 1));\n"
        "    score += built_text[0] == 'o' && built_text[1] == 'k' && built_text[2] == '!' && built_text[3] == '\\0' ? 100 : 0;\n"
        "    if (PyBytes_AsStringAndSize(PyTuple_GET_ITEM(built, 2), &raw, &raw_len) != 0) return NULL;\n"
        "    score += raw_len == 2 && raw[0] == 'A' && raw[1] == 'B' ? 200 : 0;\n"
        "    score += zero[0] == '\\0' && zero[1] == '\\0' ? 1 : 0;\n"
        "    PyMem_Free(zero);\n"
        "    value_obj = PyLong_FromLong(10);\n"
        "    kw_args = PyTuple_Pack(1, value_obj);\n"
        "    Py_DECREF(value_obj);\n"
        "    kwargs = PyDict_New();\n"
        '    name = PyUnicode_FromString("kw");\n'
        "    if (kw_args == NULL || kwargs == NULL || name == NULL) return NULL;\n"
        '    if (PyDict_SetItemString(kwargs, "name", name) != 0) return NULL;\n'
        "    Py_DECREF(name);\n"
        '    if (!PyArg_ParseTupleAndKeywords(kw_args, kwargs, "l|s", kwlist, &value, &text)) return NULL;\n'
        "    score += value;\n"
        "    score += text[0] == 'k' && text[1] == 'w' && text[2] == '\\0' ? 20 : 0;\n"
        "    Py_DECREF(kwargs);\n"
        "    Py_DECREF(kw_args);\n"
        "    owned = PyLong_FromLong(5);\n"
        "    again = Py_NewRef(owned);\n"
        "    score += PyLong_AsLong(again);\n"
        "    Py_DECREF(again);\n"
        "    Py_CLEAR(owned);\n"
        "    score += owned == NULL ? 30 : 0;\n"
        "    score += Py_XNewRef(NULL) == NULL ? 40 : 0;\n"
        "    Py_XDECREF(NULL);\n"
        "    slot = PyLong_FromLong(1);\n"
        "    Py_SETREF(slot, PyLong_FromLong(2));\n"
        "    score += PyLong_AsLong(slot);\n"
        "    Py_DECREF(slot);\n"
        "    xslot = NULL;\n"
        "    Py_XSETREF(xslot, PyLong_FromLong(3));\n"
        "    score += PyLong_AsLong(xslot);\n"
        "    Py_DECREF(xslot);\n"
        "    PyErr_NoMemory();\n"
        "    score += PyErr_Occurred() != NULL ? 50 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(built);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_refcnt_macro_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        '    PyObject *obj = PyUnicode_FromString("rc");\n'
        "    if (obj == NULL) return NULL;\n"
        "    Py_ssize_t refcnt = Py_REFCNT(obj);\n"
        "    long score = 0;\n"
        "    Py_INCREF(obj);\n"
        "    score += Py_REFCNT(obj) == refcnt + 1 ? 1 : 0;\n"
        "    Py_DECREF(obj);\n"
        "    Py_SET_REFCNT(obj, Py_REFCNT(obj));\n"
        "    score += Py_REFCNT(obj) == refcnt ? 10 : 0;\n"
        "    Py_DECREF(obj);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static int objectapi_vsnprintf_probe(char *buffer, size_t size, const char *format, ...) {\n"
        "    __builtin_va_list ap;\n"
        "    __builtin_va_start(ap, format);\n"
        "    int result = PyOS_vsnprintf(buffer, size, format, ap);\n"
        "    __builtin_va_end(ap);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_memory_os_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    char formatted[32];\n"
        "    char formatted_v[32];\n"
        "    char *raw = (char *)PyMem_RawMalloc(3);\n"
        "    char *zero = NULL;\n"
        "    char *obj = NULL;\n"
        "    char *obj_zero = NULL;\n"
        "    char *macro_obj = NULL;\n"
        "    char *del_obj = NULL;\n"
        "    char *DEL_obj = NULL;\n"
        "    char *mem_macro = NULL;\n"
        '    int n = PyOS_snprintf(formatted, sizeof(formatted), "%s-%d", "np", 24);\n'
        '    int nv = objectapi_vsnprintf_probe(formatted_v, sizeof(formatted_v), "%s-%d", "os", 7);\n'
        "    score += n == 5 && formatted[0] == 'n' && formatted[4] == '4' ? 1 : 0;\n"
        "    score += nv == 4 && formatted_v[0] == 'o' && formatted_v[3] == '7' ? 10 : 0;\n"
        "    if (raw == NULL) return PyErr_NoMemory();\n"
        "    raw[0] = 'a'; raw[1] = 'b'; raw[2] = '\\0';\n"
        "    raw = (char *)PyMem_RawRealloc(raw, 4);\n"
        "    if (raw == NULL) return PyErr_NoMemory();\n"
        "    raw[2] = 'c'; raw[3] = '\\0';\n"
        "    score += raw[0] == 'a' && raw[2] == 'c' ? 100 : 0;\n"
        "    PyMem_RawFree(raw);\n"
        "    zero = (char *)PyMem_RawCalloc(2, 1);\n"
        "    if (zero == NULL) return PyErr_NoMemory();\n"
        "    score += zero[0] == '\\0' && zero[1] == '\\0' ? 1000 : 0;\n"
        "    PyMem_RawFree(zero);\n"
        "    obj = (char *)PyObject_Malloc(2);\n"
        "    if (obj == NULL) return PyErr_NoMemory();\n"
        "    obj[0] = 'o'; obj[1] = '\\0';\n"
        "    obj = (char *)PyObject_Realloc(obj, 3);\n"
        "    if (obj == NULL) return PyErr_NoMemory();\n"
        "    obj[1] = 'k'; obj[2] = '\\0';\n"
        "    score += obj[0] == 'o' && obj[1] == 'k' ? 10000 : 0;\n"
        "    PyObject_Free(obj);\n"
        "    obj_zero = (char *)PyObject_Calloc(2, 1);\n"
        "    if (obj_zero == NULL) return PyErr_NoMemory();\n"
        "    score += obj_zero[0] == '\\0' && obj_zero[1] == '\\0' ? 100000 : 0;\n"
        "    PyObject_Free(obj_zero);\n"
        "    macro_obj = (char *)PyObject_MALLOC(2);\n"
        "    if (macro_obj == NULL) return PyErr_NoMemory();\n"
        "    macro_obj[0] = 'x'; macro_obj[1] = '\\0';\n"
        "    macro_obj = (char *)PyObject_REALLOC(macro_obj, 3);\n"
        "    if (macro_obj == NULL) return PyErr_NoMemory();\n"
        "    macro_obj[1] = 'y'; macro_obj[2] = '\\0';\n"
        "    score += macro_obj[0] == 'x' && macro_obj[1] == 'y' ? 1000000 : 0;\n"
        "    PyObject_FREE(macro_obj);\n"
        "    del_obj = (char *)PyObject_Malloc(1);\n"
        "    DEL_obj = (char *)PyObject_Malloc(1);\n"
        "    mem_macro = (char *)PyMem_Malloc(1);\n"
        "    if (del_obj == NULL || DEL_obj == NULL || mem_macro == NULL) return PyErr_NoMemory();\n"
        "    PyObject_Del(del_obj);\n"
        "    PyObject_DEL(DEL_obj);\n"
        "    PyMem_FREE(mem_macro);\n"
        "    score += 10000000;\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_pythoncapi_compat_score(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        '    PyObject *abc = PyUnicode_FromString("abc");\n'
        '    PyObject *abc0def = PyUnicode_FromStringAndSize("abc\\0def", 7);\n'
        "    PyObject *list = PyList_New(2);\n"
        "    PyObject *dict = PyDict_New();\n"
        '    PyObject *key = PyUnicode_FromString("key");\n'
        '    PyObject *value = PyUnicode_FromString("default");\n'
        '    PyObject *other = PyUnicode_FromString("other");\n'
        '    PyObject *null_key = PyUnicode_FromString("null-result");\n'
        "    PyObject *result = NULL;\n"
        "    PyObject *item = NULL;\n"
        "    PyObject *added = NULL;\n"
        "    if (abc == NULL || abc0def == NULL || list == NULL || dict == NULL || key == NULL || value == NULL || other == NULL || null_key == NULL) return NULL;\n"
        '    score += PyUnicode_EqualToUTF8(abc, "abc") ? 1 : 0;\n'
        '    score += !PyUnicode_EqualToUTF8(abc, "ab") ? 10 : 0;\n'
        '    score += PyUnicode_EqualToUTF8AndSize(abc0def, "abc\\0def", 7) ? 100 : 0;\n'
        '    score += !PyUnicode_EqualToUTF8(abc0def, "abc\\0def") ? 1000 : 0;\n'
        "    if (PyList_SetItem(list, 0, PyLong_FromLong(41)) != 0) return NULL;\n"
        "    if (PyList_SetItem(list, 1, PyLong_FromLong(42)) != 0) return NULL;\n"
        "    item = PyList_GetItemRef(list, 1);\n"
        "    if (item == NULL) return NULL;\n"
        "    score += PyLong_AsLong(item) == 42 ? 10000 : 0;\n"
        "    Py_DECREF(item);\n"
        "    if (PyDict_SetDefaultRef(dict, key, value, &result) != 0 || result != value) return NULL;\n"
        "    score += 100000;\n"
        "    Py_DECREF(result);\n"
        "    result = NULL;\n"
        "    if (PyDict_SetDefaultRef(dict, key, other, &result) != 1 || result != value) return NULL;\n"
        "    score += 1000000;\n"
        "    Py_DECREF(result);\n"
        "    result = NULL;\n"
        "    if (PyDict_SetDefaultRef(dict, null_key, other, NULL) != 0) return NULL;\n"
        "    if (PyDict_SetDefaultRef(dict, key, other, NULL) != 1) return NULL;\n"
        "    score += 10000000;\n"
        '    if (PyModule_Add(self, "ADDED_BY_MODULE_ADD", PyUnicode_FromString("module-added")) != 0) return NULL;\n'
        '    added = PyObject_GetAttrString(self, "ADDED_BY_MODULE_ADD");\n'
        "    if (added == NULL) return NULL;\n"
        '    score += PyUnicode_EqualToUTF8(added, "module-added") ? 100000000 : 0;\n'
        "    Py_DECREF(added);\n"
        "    Py_DECREF(other);\n"
        "    Py_DECREF(null_key);\n"
        "    Py_DECREF(value);\n"
        "    Py_DECREF(key);\n"
        "    Py_DECREF(dict);\n"
        "    Py_DECREF(list);\n"
        "    Py_DECREF(abc0def);\n"
        "    Py_DECREF(abc);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_mapping_long_exc_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    long score = 0;\n"
        "    PyObject *dict = PyDict_New();\n"
        '    PyObject *key = PyUnicode_FromString("a");\n'
        "    PyObject *value = PyLong_FromLong(33);\n"
        "    PyObject *got = NULL;\n"
        "    PyObject *bval = NULL;\n"
        "    PyObject *missing = NULL;\n"
        "    PyObject *ss = NULL;\n"
        "    PyObject *ull = NULL;\n"
        "    PyObject *i32o = NULL;\n"
        "    PyObject *i64o = NULL;\n"
        "    PyObject *u32o = NULL;\n"
        "    PyObject *u64o = NULL;\n"
        "    PyObject *ptro = NULL;\n"
        "    PyObject *neg = NULL;\n"
        "    PyObject *truth = NULL;\n"
        "    PyObject *ptype = NULL;\n"
        "    PyObject *pvalue = NULL;\n"
        "    PyObject *ptb = NULL;\n"
        "    PyObject *next_key = NULL;\n"
        "    PyObject *next_value = NULL;\n"
        "    PyObject *ref = NULL;\n"
        "    PyObject *missing_key = NULL;\n"
        "    PyObject *ckey = NULL;\n"
        "    PyObject *cval = NULL;\n"
        "    Py_ssize_t pos = 0;\n"
        "    int32_t i32 = 0;\n"
        "    int64_t i64 = 0;\n"
        "    uint32_t u32 = 0;\n"
        "    uint64_t u64 = 0;\n"
        "    char ptr_marker = 0;\n"
        "    int overflow = 99;\n"
        "    int optional_rc = 0;\n"
        "    if (dict == NULL || key == NULL || value == NULL) return NULL;\n"
        "    if (PyObject_SetItem(dict, key, value) != 0) return NULL;\n"
        "    got = PyObject_GetItem(dict, key);\n"
        "    if (got == NULL) return NULL;\n"
        "    score += PyLong_AsLong(got);\n"
        "    Py_DECREF(got);\n"
        "    score += PyObject_Size(dict) * 10;\n"
        "    score += PyMapping_Size(dict) == 1 ? 37000 : 0;\n"
        "    bval = PyLong_FromLong(4);\n"
        '    if (PyMapping_SetItemString(dict, "b", bval) != 0) return NULL;\n'
        "    Py_DECREF(bval);\n"
        '    got = PyMapping_GetItemString(dict, "b");\n'
        "    if (got == NULL) return NULL;\n"
        "    score += PyLong_AsLong(got);\n"
        "    Py_DECREF(got);\n"
        "    score += PyMapping_Length(dict) == 2 ? 38000 : 0;\n"
        "    score += PyDict_Size(dict) == 2 ? 11000 : 0;\n"
        "    score += PyDict_Contains(dict, key) == 1 ? 12000 : 0;\n"
        '    score += PyDict_ContainsString(dict, "b") == 1 ? 13000 : 0;\n'
        '    score += PyDict_ContainsString(dict, "missing") == 0 ? 14000 : 0;\n'
        "    score += PyLong_AsLong(PyDict_GetItemWithError(dict, key)) == 33 ? 25000 : 0;\n"
        "    if (PyDict_GetItemRef(dict, key, &ref) != 1 || ref == NULL) return NULL;\n"
        "    score += PyLong_AsLong(ref) == 33 ? 26000 : 0;\n"
        "    Py_DECREF(ref);\n"
        "    ref = NULL;\n"
        '    missing_key = PyUnicode_FromString("missing");\n'
        "    if (missing_key == NULL) return NULL;\n"
        "    score += PyDict_GetItemRef(dict, missing_key, &ref) == 0 && ref == NULL ? 27000 : 0;\n"
        "    Py_DECREF(missing_key);\n"
        "    missing_key = NULL;\n"
        '    if (PyDict_GetItemStringRef(dict, "b", &ref) != 1 || ref == NULL) return NULL;\n'
        "    score += PyLong_AsLong(ref) == 4 ? 28000 : 0;\n"
        "    Py_DECREF(ref);\n"
        "    ref = NULL;\n"
        '    ckey = PyUnicode_FromString("c");\n'
        "    cval = PyLong_FromLong(6);\n"
        "    if (ckey == NULL || cval == NULL) return NULL;\n"
        "    if (PyDict_SetItem(dict, ckey, cval) != 0) return NULL;\n"
        "    Py_DECREF(cval);\n"
        "    cval = NULL;\n"
        "    if (PyDict_Pop(dict, ckey, &ref) != 1 || ref == NULL) return NULL;\n"
        "    score += PyLong_AsLong(ref) == 6 ? 29000 : 0;\n"
        "    Py_DECREF(ref);\n"
        "    ref = NULL;\n"
        "    score += PyDict_Contains(dict, ckey) == 0 ? 30000 : 0;\n"
        "    score += PyDict_Pop(dict, ckey, &ref) == 0 && ref == NULL ? 31000 : 0;\n"
        "    cval = PyLong_FromLong(8);\n"
        "    if (cval == NULL) return NULL;\n"
        "    if (PyDict_SetItem(dict, ckey, cval) != 0) return NULL;\n"
        "    Py_DECREF(cval);\n"
        "    cval = NULL;\n"
        "    score += PyDict_Pop(dict, ckey, NULL) == 1 ? 33000 : 0;\n"
        "    score += PyDict_Pop(dict, ckey, NULL) == 0 ? 34000 : 0;\n"
        "    Py_DECREF(ckey);\n"
        "    ckey = NULL;\n"
        "    cval = PyLong_FromLong(7);\n"
        "    if (cval == NULL) return NULL;\n"
        '    if (PyMapping_SetItemString(dict, "d", cval) != 0) return NULL;\n'
        "    Py_DECREF(cval);\n"
        "    cval = NULL;\n"
        '    score += PyDict_PopString(dict, "d", NULL) == 1 ? 35000 : 0;\n'
        '    score += PyDict_PopString(dict, "d", &ref) == 0 && ref == NULL ? 36000 : 0;\n'
        "    score += PyDict_GetItemWithError(NULL, key) == NULL && PyErr_ExceptionMatches(PyExc_TypeError) ? 32000 : 0;\n"
        "    PyErr_Clear();\n"
        "    score += PyMapping_Check(dict) ? 100 : 0;\n"
        '    score += PyMapping_HasKeyString(dict, "b") ? 1000 : 0;\n'
        '    optional_rc = PyMapping_GetOptionalItemString(dict, "missing", &missing);\n'
        "    score += optional_rc == 0 && missing == NULL ? 2000 : 0;\n"
        '    score += PyMapping_HasKeyStringWithError(dict, "b") == 1 ? 3000 : 0;\n'
        "    if (PyDict_DelItem(dict, key) != 0) return NULL;\n"
        "    score += PyDict_Contains(dict, key) == 0 ? 15000 : 0;\n"
        "    score += PyDict_Size(dict) == 1 ? 16000 : 0;\n"
        "    score += PyMapping_HasKey(dict, key) == 0 ? 4000 : 0;\n"
        "    if (PyDict_SetItem(dict, key, value) != 0) return NULL;\n"
        "    if (PyObject_DelItem(dict, key) != 0) return NULL;\n"
        "    score += PyMapping_HasKey(dict, key) == 0 ? 50000 : 0;\n"
        "    if (!PyDict_Next(dict, &pos, &next_key, &next_value)) return NULL;\n"
        '    score += PyUnicode_CompareWithASCIIString(next_key, "b") == 0 && PyLong_AsLong(next_value) == 4 ? 23000 : 0;\n'
        "    score += !PyDict_Next(dict, &pos, &next_key, &next_value) && pos > 0 ? 24000 : 0;\n"
        "    got = PyLong_FromSize_t(12);\n"
        "    if (got == NULL) return NULL;\n"
        "    score += (long)PyLong_AsSize_t(got);\n"
        "    score += PyLong_AsInt(got) == 12 ? 17000 : 0;\n"
        "    Py_DECREF(got);\n"
        "    i32o = PyLong_FromInt32((int32_t)-1234);\n"
        "    if (i32o == NULL || PyLong_AsInt32(i32o, &i32) != 0) return NULL;\n"
        "    score += i32 == (int32_t)-1234 ? 18000 : 0;\n"
        "    Py_DECREF(i32o);\n"
        "    u32o = PyLong_FromUInt32((uint32_t)4000000000U);\n"
        "    if (u32o == NULL || PyLong_AsUInt32(u32o, &u32) != 0) return NULL;\n"
        "    score += u32 == (uint32_t)4000000000U ? 19000 : 0;\n"
        "    Py_DECREF(u32o);\n"
        "    i64o = PyLong_FromInt64((int64_t)-1234567890123LL);\n"
        "    if (i64o == NULL || PyLong_AsInt64(i64o, &i64) != 0) return NULL;\n"
        "    score += i64 == (int64_t)-1234567890123LL ? 20000 : 0;\n"
        "    Py_DECREF(i64o);\n"
        "    u64o = PyLong_FromUInt64((uint64_t)9223372036854775813ULL);\n"
        "    if (u64o == NULL || PyLong_AsUInt64(u64o, &u64) != 0) return NULL;\n"
        "    score += u64 == (uint64_t)9223372036854775813ULL ? 21000 : 0;\n"
        "    Py_DECREF(u64o);\n"
        "    ptro = PyLong_FromVoidPtr((void *)&ptr_marker);\n"
        "    if (ptro == NULL) return NULL;\n"
        "    score += PyLong_AsVoidPtr(ptro) == (void *)&ptr_marker ? 22000 : 0;\n"
        "    Py_DECREF(ptro);\n"
        "    ss = PyLong_FromSsize_t((Py_ssize_t)-3);\n"
        "    if (ss == NULL) return NULL;\n"
        "    score += PyLong_AsLongAndOverflow(ss, &overflow) == -3 && overflow == 0 ? 5000 : 0;\n"
        "    Py_DECREF(ss);\n"
        "    ull = PyLong_FromUnsignedLongLong(42ULL);\n"
        "    if (ull == NULL) return NULL;\n"
        "    score += (long)PyLong_AsUnsignedLongLong(ull);\n"
        "    score += (long)PyLong_AsUnsignedLong(ull);\n"
        "    neg = PyLong_FromLong(-1);\n"
        "    if (neg == NULL) return NULL;\n"
        "    score += PyLong_AsUnsignedLongLongMask(neg) == (unsigned long long)-1 ? 6000 : 0;\n"
        "    score += PyLong_Check(ull) ? 7000 : 0;\n"
        "    truth = PyBool_FromLong(1);\n"
        "    if (truth == NULL) return NULL;\n"
        "    score += PyLong_Check(truth) ? 8000 : 0;\n"
        "    score += !PyLong_CheckExact(truth) ? 9000 : 0;\n"
        "    Py_DECREF(truth);\n"
        "    Py_DECREF(neg);\n"
        "    Py_DECREF(ull);\n"
        "    PyErr_SetObject(PyExc_KeyError, key);\n"
        "    score += PyErr_ExceptionMatches(PyExc_KeyError) ? 10000 : 0;\n"
        "    PyErr_Fetch(&ptype, &pvalue, &ptb);\n"
        "    score += ptype != NULL && pvalue != NULL ? 20000 : 0;\n"
        "    PyErr_Restore(ptype, pvalue, ptb);\n"
        "    score += PyErr_GivenExceptionMatches(PyErr_Occurred(), PyExc_KeyError) ? 30000 : 0;\n"
        "    PyErr_Clear();\n"
        "    Py_DECREF(value);\n"
        "    Py_DECREF(key);\n"
        "    Py_DECREF(dict);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_unicode_from_format_v(const char *format, ...) {\n"
        "    __builtin_va_list ap;\n"
        "    __builtin_va_start(ap, format);\n"
        "    PyObject *result = PyUnicode_FromFormatV(format, ap);\n"
        "    __builtin_va_end(ap);\n"
        "    return result;\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_call_type_format_score(PyObject *self, PyObject *args) {\n"
        "    (void)args;\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *called = NULL;\n"
        "    PyObject *method0 = NULL;\n"
        "    PyObject *method1 = NULL;\n"
        "    PyObject *method_noargs = NULL;\n"
        "    PyObject *method_name = NULL;\n"
        "    PyObject *formatted = NULL;\n"
        "    PyObject *formatted_v = NULL;\n"
        "    PyObject *type_obj = NULL;\n"
        "    PyObject *label = NULL;\n"
        "    long score = 0;\n"
        '    callable = PyObject_GetAttrString(self, "tuple_sum");\n'
        "    if (callable == NULL) return NULL;\n"
        "    called = PyObject_CallFunction(callable, NULL);\n"
        "    Py_DECREF(callable);\n"
        "    if (called == NULL) return NULL;\n"
        "    score += PyLong_AsLong(called);\n"
        "    Py_DECREF(called);\n"
        '    method0 = PyObject_CallMethod(self, "tuple_sum", NULL);\n'
        "    if (method0 == NULL) return NULL;\n"
        "    score += PyLong_AsLong(method0) * 10;\n"
        "    Py_DECREF(method0);\n"
        '    method1 = PyObject_CallMethod(self, "has_attr", "Os", self, "MAGIC");\n'
        "    if (method1 == NULL) return NULL;\n"
        "    score += PyLong_AsLong(method1) * 100;\n"
        "    Py_DECREF(method1);\n"
        '    method_name = PyUnicode_FromString("tuple_sum");\n'
        "    if (method_name == NULL) return NULL;\n"
        "    method_noargs = PyObject_CallMethodNoArgs(self, method_name);\n"
        "    Py_DECREF(method_name);\n"
        "    if (method_noargs == NULL) return NULL;\n"
        "    score += PyLong_AsLong(method_noargs) * 1000;\n"
        "    Py_DECREF(method_noargs);\n"
        '    label = PyUnicode_FromString("ok");\n'
        "    if (label == NULL) return NULL;\n"
        '    formatted = PyUnicode_FromFormat("v=%ld/%S", 3L, label);\n'
        "    if (formatted == NULL) return NULL;\n"
        '    score += PyUnicode_CompareWithASCIIString(formatted, "v=3/ok") == 0 ? 10000 : 0;\n'
        "    Py_DECREF(formatted);\n"
        '    formatted_v = objectapi_unicode_from_format_v("vv=%ld/%S", 4L, label);\n'
        "    if (formatted_v == NULL) return NULL;\n"
        '    score += PyUnicode_CompareWithASCIIString(formatted_v, "vv=4/ok") == 0 ? 100000 : 0;\n'
        "    Py_DECREF(formatted_v);\n"
        "    Py_DECREF(label);\n"
        "    type_obj = PyObject_Type(self);\n"
        "    if (type_obj == NULL) return NULL;\n"
        "    score += PyObject_IsInstance(self, type_obj) == 1 ? 20000 : 0;\n"
        "    Py_DECREF(type_obj);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_call_helper_score(PyObject *self, PyObject *args) {\n"
        "    PyObject *callable = NULL;\n"
        "    PyObject *method_name = NULL;\n"
        "    PyObject *result = NULL;\n"
        "    PyObject *vc_args[1];\n"
        "    PyObject *vc_method_args[2];\n"
        "    long score = 0;\n"
        "    (void)args;\n"
        '    callable = PyObject_GetAttrString(self, "tuple_sum");\n'
        "    if (callable == NULL) return NULL;\n"
        "    result = PyObject_CallNoArgs(callable);\n"
        "    Py_DECREF(callable);\n"
        "    if (result == NULL) return NULL;\n"
        "    score += PyLong_AsLong(result);\n"
        "    Py_DECREF(result);\n"
        '    callable = PyObject_GetAttrString(self, "set_attr");\n'
        "    if (callable == NULL) return NULL;\n"
        "    result = PyObject_CallOneArg(callable, self);\n"
        "    Py_DECREF(callable);\n"
        "    if (result == NULL) return NULL;\n"
        "    score += PyLong_AsLong(result) * 10;\n"
        "    Py_DECREF(result);\n"
        '    method_name = PyUnicode_FromString("set_attr");\n'
        "    if (method_name == NULL) return NULL;\n"
        "    result = PyObject_CallMethodOneArg(self, method_name, self);\n"
        "    Py_DECREF(method_name);\n"
        "    if (result == NULL) return NULL;\n"
        "    score += PyLong_AsLong(result) * 100;\n"
        "    Py_DECREF(result);\n"
        '    callable = PyObject_GetAttrString(self, "set_attr");\n'
        "    if (callable == NULL) return NULL;\n"
        "    vc_args[0] = self;\n"
        "    result = PyObject_Vectorcall(callable, vc_args, 1, NULL);\n"
        "    Py_DECREF(callable);\n"
        "    if (result == NULL) return NULL;\n"
        "    score += PyLong_AsLong(result) * 1000;\n"
        "    Py_DECREF(result);\n"
        '    method_name = PyUnicode_FromString("set_attr");\n'
        "    if (method_name == NULL) return NULL;\n"
        "    vc_method_args[0] = self;\n"
        "    vc_method_args[1] = self;\n"
        "    result = PyObject_VectorcallMethod(method_name, vc_method_args, 2, NULL);\n"
        "    Py_DECREF(method_name);\n"
        "    if (result == NULL) return NULL;\n"
        "    score += PyLong_AsLong(result) * 10000;\n"
        "    Py_DECREF(result);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_number_protocol_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *a = PyLong_FromLong(9);\n"
        "    PyObject *b = PyLong_FromLong(4);\n"
        "    PyObject *two = PyLong_FromLong(2);\n"
        '    PyObject *text = PyUnicode_FromString("x");\n'
        "    PyObject *add = NULL;\n"
        "    PyObject *sub = NULL;\n"
        "    PyObject *mul = NULL;\n"
        "    PyObject *div = NULL;\n"
        "    PyObject *floor_div = NULL;\n"
        "    PyObject *rem = NULL;\n"
        "    PyObject *pow_obj = NULL;\n"
        "    PyObject *neg = NULL;\n"
        "    PyObject *pos = NULL;\n"
        "    PyObject *abs_obj = NULL;\n"
        "    PyObject *index_true = NULL;\n"
        "    PyObject *repeat = NULL;\n"
        "    long score = 0;\n"
        "    if (a == NULL || b == NULL || two == NULL || text == NULL) return NULL;\n"
        "    add = PyNumber_Add(a, b);\n"
        "    sub = PyNumber_Subtract(a, b);\n"
        "    mul = PyNumber_Multiply(a, b);\n"
        "    div = PyNumber_TrueDivide(a, b);\n"
        "    floor_div = PyNumber_FloorDivide(a, b);\n"
        "    rem = PyNumber_Remainder(a, b);\n"
        "    pow_obj = PyNumber_Power(b, two, Py_None);\n"
        "    neg = PyNumber_Negative(b);\n"
        "    pos = PyNumber_Positive(b);\n"
        "    abs_obj = PyNumber_Absolute(neg);\n"
        "    index_true = PyNumber_Index(Py_True);\n"
        "    repeat = PyNumber_Multiply(text, two);\n"
        "    if (add == NULL || sub == NULL || mul == NULL || div == NULL || floor_div == NULL || rem == NULL || pow_obj == NULL || neg == NULL || pos == NULL || abs_obj == NULL || index_true == NULL || repeat == NULL) return NULL;\n"
        "    score += PyLong_AsLong(add);\n"
        "    score += PyLong_AsLong(sub) * 10;\n"
        "    score += PyLong_AsLong(mul) * 100;\n"
        "    score += (long)(PyFloat_AsDouble(div) * 100.0);\n"
        "    score += PyLong_AsLong(floor_div) * 1000;\n"
        "    score += PyLong_AsLong(rem) * 10000;\n"
        "    score += PyLong_AsLong(pos) * 100000;\n"
        "    score += PyLong_AsLong(pow_obj) * 100000;\n"
        "    score += PyLong_AsLong(abs_obj) * 1000000;\n"
        "    score += (long)PyNumber_AsSsize_t(b, PyExc_OverflowError) * 10000000;\n"
        "    score += PyObject_Not(Py_False) * 100000000;\n"
        "    score += PyIndex_Check(a) * 200000000;\n"
        "    score += PyLong_AsLong(index_true) * 300000000;\n"
        '    score += PyUnicode_CompareWithASCIIString(repeat, "xx") == 0 ? 400000000 : 0;\n'
        "    Py_DECREF(repeat);\n"
        "    Py_DECREF(index_true);\n"
        "    Py_DECREF(abs_obj);\n"
        "    Py_DECREF(pos);\n"
        "    Py_DECREF(neg);\n"
        "    Py_DECREF(pow_obj);\n"
        "    Py_DECREF(rem);\n"
        "    Py_DECREF(floor_div);\n"
        "    Py_DECREF(div);\n"
        "    Py_DECREF(mul);\n"
        "    Py_DECREF(sub);\n"
        "    Py_DECREF(add);\n"
        "    Py_DECREF(text);\n"
        "    Py_DECREF(two);\n"
        "    Py_DECREF(b);\n"
        "    Py_DECREF(a);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyObject *objectapi_number_bitwise_score(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    PyObject *one = PyLong_FromLong(1);\n"
        "    PyObject *two = PyLong_FromLong(2);\n"
        "    PyObject *three = PyLong_FromLong(3);\n"
        "    PyObject *four = PyLong_FromLong(4);\n"
        "    PyObject *six = PyLong_FromLong(6);\n"
        "    PyObject *seven = PyLong_FromLong(7);\n"
        "    PyObject *eight = PyLong_FromLong(8);\n"
        "    PyObject *float_obj = PyFloat_FromDouble(6.5);\n"
        '    PyObject *text = PyUnicode_FromString("x");\n'
        "    PyObject *as_long = NULL;\n"
        "    PyObject *as_float = NULL;\n"
        "    PyObject *and_obj = NULL;\n"
        "    PyObject *or_obj = NULL;\n"
        "    PyObject *xor_obj = NULL;\n"
        "    PyObject *lshift_obj = NULL;\n"
        "    PyObject *rshift_obj = NULL;\n"
        "    PyObject *invert_obj = NULL;\n"
        "    PyObject *from_double = NULL;\n"
        "    long score = 0;\n"
        "    if (one == NULL || two == NULL || three == NULL || four == NULL || six == NULL || seven == NULL || eight == NULL || float_obj == NULL || text == NULL) return NULL;\n"
        "    as_long = PyNumber_Long(float_obj);\n"
        "    from_double = PyLong_FromDouble(-3.75);\n"
        "    as_float = PyNumber_Float(seven);\n"
        "    and_obj = PyNumber_And(six, three);\n"
        "    or_obj = PyNumber_Or(four, one);\n"
        "    xor_obj = PyNumber_Xor(seven, three);\n"
        "    lshift_obj = PyNumber_Lshift(one, three);\n"
        "    rshift_obj = PyNumber_Rshift(eight, two);\n"
        "    invert_obj = PyNumber_Invert(two);\n"
        "    if (as_long == NULL || from_double == NULL || as_float == NULL || and_obj == NULL || or_obj == NULL || xor_obj == NULL || lshift_obj == NULL || rshift_obj == NULL || invert_obj == NULL) return NULL;\n"
        "    score += PyNumber_Check(seven) ? 1 : 0;\n"
        "    score += !PyNumber_Check(text) ? 2 : 0;\n"
        "    score += PyLong_AsLong(as_long) == 6 ? 4 : 0;\n"
        "    score += PyFloat_AS_DOUBLE(as_float) == 7.0 ? 8 : 0;\n"
        "    score += PyLong_AsDouble(seven) == 7.0 ? 16 : 0;\n"
        "    score += PyFloat_AS_DOUBLE(float_obj) == 6.5 ? 32 : 0;\n"
        "    score += PyLong_AsLong(and_obj) == 2 ? 64 : 0;\n"
        "    score += PyLong_AsLong(or_obj) == 5 ? 128 : 0;\n"
        "    score += PyLong_AsLong(xor_obj) == 4 ? 256 : 0;\n"
        "    score += PyLong_AsLong(lshift_obj) == 8 ? 512 : 0;\n"
        "    score += PyLong_AsLong(rshift_obj) == 2 ? 1024 : 0;\n"
        "    score += PyLong_AsLong(invert_obj) == -3 ? 2048 : 0;\n"
        "    score += PyLong_AsLong(from_double) == -3 ? 4096 : 0;\n"
        "    Py_DECREF(invert_obj);\n"
        "    Py_DECREF(rshift_obj);\n"
        "    Py_DECREF(lshift_obj);\n"
        "    Py_DECREF(xor_obj);\n"
        "    Py_DECREF(or_obj);\n"
        "    Py_DECREF(and_obj);\n"
        "    Py_DECREF(as_float);\n"
        "    Py_DECREF(from_double);\n"
        "    Py_DECREF(as_long);\n"
        "    Py_DECREF(text);\n"
        "    Py_DECREF(float_obj);\n"
        "    Py_DECREF(eight);\n"
        "    Py_DECREF(seven);\n"
        "    Py_DECREF(six);\n"
        "    Py_DECREF(four);\n"
        "    Py_DECREF(three);\n"
        "    Py_DECREF(two);\n"
        "    Py_DECREF(one);\n"
        "    return PyLong_FromLong(score);\n"
        "}\n"
        "\n"
        "static PyMethodDef ObjectApiMethods[] = {\n"
        '    {"tuple_sum", objectapi_tuple_sum, METH_VARARGS, "tuple C-API smoke"},\n'
        '    {"pack_sum", objectapi_pack_sum, METH_VARARGS, "PyTuple_Pack smoke"},\n'
        '    {"dict_answer", objectapi_dict_answer, METH_VARARGS, "dict C-API smoke"},\n'
        '    {"list_sum", objectapi_list_sum, METH_VARARGS, "list C-API smoke"},\n'
        '    {"bytes_sum", objectapi_bytes_sum, METH_VARARGS, "bytes C-API smoke"},\n'
        '    {"bytes_size_sum", objectapi_bytes_size_sum, METH_VARARGS, "PyBytes_AsStringAndSize smoke"},\n'
        '    {"unicode_prefix", objectapi_unicode_prefix, METH_VARARGS, "PyUnicode_FromStringAndSize smoke"},\n'
        '    {"unicode_utf8_size_score", objectapi_unicode_utf8_size_score, METH_VARARGS, "PyUnicode_AsUTF8AndSize smoke"},\n'
        '    {"unicode_decode_length_score", objectapi_unicode_decode_length_score, METH_VARARGS, "Unicode decode/length smoke"},\n'
        '    {"unicode_encode_concat_score", objectapi_unicode_encode_concat_score, METH_VARARGS, "Unicode encode/concat C-API smoke"},\n'
        '    {"unicode_macro_score", objectapi_unicode_macro_score, METH_VARARGS, "Unicode macro C-API smoke"},\n'
        '    {"unicode_kind_score", objectapi_unicode_kind_score, METH_VARARGS, "Unicode kind/data C-API smoke"},\n'
        '    {"float_bool_score", objectapi_float_bool_score, METH_VARARGS, "float/bool C-API smoke"},\n'
        '    {"scalar_complex_score", objectapi_scalar_complex_score, METH_VARARGS, "scalar/complex C-API smoke"},\n'
        '    {"set_attr", objectapi_set_attr, METH_VARARGS, "attr string smoke"},\n'
        '    {"has_attr", objectapi_has_attr, METH_VARARGS, "has attr string smoke"},\n'
        '    {"format_error", objectapi_format_error, METH_VARARGS, "PyErr_Format smoke"},\n'
        '    {"warning_score", objectapi_warning_score, METH_VARARGS, "warning C-API smoke"},\n'
        '    {"gil_state_score", objectapi_gil_state_score, METH_VARARGS, "GIL state C-API smoke"},\n'
        '    {"error_helper_score", objectapi_error_helper_score, METH_VARARGS, "error helper C-API smoke"},\n'
        '    {"exception_global_score", objectapi_exception_global_score, METH_VARARGS, "exception global C-API smoke"},\n'
        '    {"module_dict_magic", objectapi_module_dict_magic, METH_VARARGS, "PyModule_GetDict smoke"},\n'
        '    {"bump_module_global", objectapi_bump_module_global, METH_VARARGS, "mutate module globals"},\n'
        '    {"delete_module_global", objectapi_delete_module_global, METH_VARARGS, "delete module global"},\n'
        '    {"str_repr_compare", objectapi_str_repr_compare, METH_VARARGS, "str/repr/richcompare smoke"},\n'
        '    {"print_score", objectapi_print_score, METH_VARARGS, "PyObject_Print smoke"},\n'
        '    {"unsigned_sum", objectapi_unsigned_sum, METH_VARARGS, "unsigned long constructors smoke"},\n'
        '    {"import_self_magic", objectapi_import_self_magic, METH_VARARGS, "PyImport_ImportModule smoke"},\n'
        '    {"sequence_score", objectapi_sequence_score, METH_VARARGS, "sequence/type-check C-API smoke"},\n'
        '    {"iter_contains_score", objectapi_iter_contains_score, METH_VARARGS, "iteration/containment C-API smoke"},\n'
        '    {"collection_views_score", objectapi_collection_views_score, METH_VARARGS, "list/dict collection-view C-API smoke"},\n'
        '    {"set_score", objectapi_set_score, METH_VARARGS, "set C-API smoke"},\n'
        '    {"attr_hash_score", objectapi_attr_hash_score, METH_VARARGS, "object attr/hash C-API smoke"},\n'
        '    {"build_mem_kw_score", objectapi_build_mem_kw_score, METH_VARARGS, "build/memory/keyword C-API smoke"},\n'
        '    {"refcnt_macro_score", objectapi_refcnt_macro_score, METH_VARARGS, "refcount macro C-API smoke"},\n'
        '    {"memory_os_score", objectapi_memory_os_score, METH_VARARGS, "raw/object memory and PyOS smoke"},\n'
        '    {"pythoncapi_compat_score", objectapi_pythoncapi_compat_score, METH_VARARGS, "pythoncapi-compat C-API smoke"},\n'
        '    {"mapping_long_exc_score", objectapi_mapping_long_exc_score, METH_VARARGS, "mapping/long/exception C-API smoke"},\n'
        '    {"call_type_format_score", objectapi_call_type_format_score, METH_VARARGS, "call/type/format C-API smoke"},\n'
        '    {"call_helper_score", objectapi_call_helper_score, METH_VARARGS, "call helper C-API smoke"},\n'
        '    {"number_protocol_score", objectapi_number_protocol_score, METH_VARARGS, "abstract number protocol C-API smoke"},\n'
        '    {"number_bitwise_score", objectapi_number_bitwise_score, METH_VARARGS, "number conversion/bitwise C-API smoke"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef objectapimodule = {\n"
        '    PyModuleDef_HEAD_INIT, "objectapi", NULL, -1, ObjectApiMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_objectapi(void) {\n"
        "    PyObject *module = PyModule_Create(&objectapimodule);\n"
        "    if (module == NULL) return NULL;\n"
        '    if (PyModule_AddIntConstant(module, "MAGIC", 12) != 0) return NULL;\n'
        '    if (PyModule_AddStringConstant(module, "LABEL", "ok") != 0) return NULL;\n'
        "    PyObject *added_ref = PyLong_FromLong(77);\n"
        "    if (added_ref == NULL) return NULL;\n"
        '    if (PyModule_AddObjectRef(module, "ADDED_REF", added_ref) != 0) return NULL;\n'
        "    PyObject *dict = PyModule_GetDict(module);\n"
        '    PyObject *exc = PyErr_NewException("objectapi.CustomError", NULL, NULL);\n'
        "    if (dict == NULL || exc == NULL) return NULL;\n"
        '    if (PyDict_SetItemString(dict, "CustomError", exc) != 0) return NULL;\n'
        "    Py_DECREF(exc);\n"
        "    Py_DECREF(added_ref);\n"
        "    return module;\n"
        "}\n",
    )


def _compile_missing_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "badinit",
        "#include <Python.h>\n" "int not_the_init_symbol(void) { return 0; }\n",
    )


def _compile_null_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "nullinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "PyMODINIT_FUNC PyInit_nullinit(void) {\n"
        '    PyErr_SetString(PyExc_RuntimeError, "init failed intentionally");\n'
        "    return NULL;\n"
        "}\n",
    )


def _compile_retry_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "retryinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "#include <stdlib.h>\n"
        "\n"
        "#ifdef _WIN32\n"
        "extern int _putenv(const char *);\n"
        "#else\n"
        "extern int unsetenv(const char *);\n"
        "#endif\n"
        "\n"
        "static long successful_inits = 0;\n"
        "\n"
        "static PyObject *retryinit_count(PyObject *self, PyObject *args) {\n"
        "    (void)self;\n"
        "    (void)args;\n"
        "    return PyLong_FromLong(successful_inits);\n"
        "}\n"
        "\n"
        "static PyMethodDef RetryMethods[] = {\n"
        '    {"count", retryinit_count, METH_VARARGS, "return successful init count"},\n'
        "    {NULL, NULL, 0, NULL},\n"
        "};\n"
        "\n"
        "static PyModuleDef retrymodule = {\n"
        '    PyModuleDef_HEAD_INIT, "retryinit", NULL, -1, RetryMethods,\n'
        "};\n"
        "\n"
        "PyMODINIT_FUNC PyInit_retryinit(void) {\n"
        '    const char *fail_once = getenv("PCC_RETRYINIT_FAIL_ONCE");\n'
        "    if (fail_once != NULL && fail_once[0] != '\\0') {\n"
        "#ifdef _WIN32\n"
        '        _putenv("PCC_RETRYINIT_FAIL_ONCE=");\n'
        "#else\n"
        '        unsetenv("PCC_RETRYINIT_FAIL_ONCE");\n'
        "#endif\n"
        '        PyErr_SetString(PyExc_RuntimeError, "retry init failed once");\n'
        "        return NULL;\n"
        "    }\n"
        "    successful_inits += 1;\n"
        "    return PyModule_Create(&retrymodule);\n"
        "}\n",
    )


def _compile_silent_null_init_extension(tmp_path: Path) -> Path:
    return _compile_extension(
        tmp_path,
        "silentnullinit",
        "#define PY_SSIZE_T_CLEAN\n"
        "#include <Python.h>\n"
        "PyMODINIT_FUNC PyInit_silentnullinit(void) {\n"
        "    return NULL;\n"
        "}\n",
    )


def _compile_main(
    site: Path, main: Path, exe: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    return subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )


def _run_main(
    site: Path, exe: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    if extra_env is not None:
        env.update(extra_env)
    return subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )


def test_pcc_native_extension_import_runs_under_self_backend_no_libpython(tmp_path):
    site = _compile_demo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import demo\n" "print(demo.add(2, 3))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "5"


def test_pcc_native_custom_type_pytype_ready_under_self_backend_no_libpython(tmp_path):
    """A C extension that registers a custom type via PyType_Ready and
    instantiates it must import + instantiate under strict no-libpython. This
    exercises the type bridge in py_capi_shim.c (PyType_Ready + the dynamic
    type_tag registry + PyType_GenericNew/Alloc). Reduced numpy
    type-registration pattern."""
    site = _compile_typedemo_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import typedemo\n"
        'print("has", hasattr(typedemo, "Foo"))\n'
        'print("notnone", typedemo.Foo is not None)\n'
        "x = typedemo.Foo()\n"
        'print("inst", x is not None)\n',
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert "has True" in run.stdout, run.stdout
    assert "notnone True" in run.stdout, run.stdout
    assert "inst True" in run.stdout, run.stdout


def test_pcc_native_builtin_py_type_mapping_under_self_backend_no_libpython(tmp_path):
    """Py_TYPE on pcc builtin objects must resolve to the shim's &PyXxx_Type
    tokens (numpy compares Py_TYPE(o) against builtin type objects). Validates
    the builtin-tag mapping in py_capi_shim.c, including immediate (tagged) ints.
    `check()` returns 3 when both int and str map correctly."""
    site = _compile_typecheck_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import tcdemo\nprint('tc', tcdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "tc 3" in run.stdout, run.stdout


def test_pcc_native_subtype_check_under_self_backend_no_libpython(tmp_path):
    """Type-bridge inheritance + alloc surface under strict no-libpython.
    check() returns 63 = PyObject_TypeCheck(derived,&Base) + (derived,&Derived) +
    !(base,&Derived) + PyType_IsSubtype(&Derived,&Base) + !(&Base,&Derived) +
    PyObject_New(&Base) non-NULL — i.e. subtype walk (both PyObject_TypeCheck and
    PyType_IsSubtype) and _PyObject_New all work."""
    site = _compile_subtype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import stdemo\nprint('st', stdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "st 63" in run.stdout, run.stdout


def test_pcc_native_link_symbols_behave_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for the batch 7-9 link symbols added to
    py_capi_shim.c for numpy's no-libpython link surface (PyTuple_GetSlice,
    PyObject_AsFileDescriptor, Py_GenericAlias, PyUnicode_AsLatin1String,
    PyErr_NormalizeException). They were link-validated only; this exercises
    them at runtime under strict --python-libpython=off --backend self.
    check() returns 31 when all five behave correctly."""
    site = _compile_linksym_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import lsdemo\nprint('ls', lsdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "ls 31" in run.stdout, run.stdout


def test_pcc_native_contextvar_get_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for the single-context contextvar (PyContextVar_New
    + PyContextVar_Get from batch 10, PyContextVar_Set from batch 14) in
    py_capi_shim.c — a real object, not a stub, since numpy.errstate creates a
    ContextVar at import and numpy alloc.c calls PyContextVar_Set. check() returns
    7: Get returns the var's own default (5) for a NULL default arg, the explicit
    default arg (9) when unset, and the set value (7) after Set — correct CPython
    single-context semantics under strict --python-libpython=off."""
    site = _compile_contextvar_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import cvdemo\nprint('cv', cvdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "cv 7" in run.stdout, run.stdout


def test_pcc_native_vaparse_tuple_and_keywords_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for the PyArg_ParseTupleAndKeywords -> (new)
    PyArg_VaParseTupleAndKeywords va_list-core delegation (canonical CPython
    refactor; PyArg_VaParseTupleAndKeywords is the symbol numpy's C core
    references). check(10, 20) parses "ll" through the core and returns 1020
    under strict --python-libpython=off --backend self."""
    site = _compile_vaparse_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import vademo\nprint('va', vademo.check(10, 20))\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "va 1020" in run.stdout, run.stdout


def test_pcc_native_eval_getbuiltins_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyEval_GetBuiltins — numpy's sole consumer
    (npy_PyFile_OpenFile) does PyDict_GetItemString(PyEval_GetBuiltins(), "open")
    and returns NULL gracefully if absent, so an empty real dict is import-safe.
    check() returns 7: GetBuiltins is a non-NULL real dict, an absent key yields
    NULL without crashing, and it is a mutable persistent singleton (borrowed-ref
    semantics) under strict --python-libpython=off --backend self."""
    site = _compile_getbuiltins_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import ebdemo\nprint('eb', ebdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "eb 7" in run.stdout, run.stdout


def test_pcc_native_uniquely_referenced_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyUnstable_Object_IsUniquelyReferenced (full
    numpy _core references it for safe in-place mutation). check() returns 3 when
    a freshly-created object (refcount 1) is uniquely referenced and, after
    Py_INCREF (refcount 2), it is not — under strict --python-libpython=off."""
    site = _compile_uniqueref_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import urdemo\nprint('ur', urdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "ur 3" in run.stdout, run.stdout


def test_pcc_native_import_and_vectorcall_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyImport_Import (-> PyImport_ImportModule) and
    PyVectorcall_Call (-> PyObject_Call), batch 14, both referenced by the full
    numpy _core. The caller extension imports the sibling `demo` extension by name
    and invokes demo.add(2, 3) through the vectorcall entry. check() returns 7
    (import non-NULL, attr reachable, call == 5) under strict
    --python-libpython=off --backend self."""
    site = _compile_demo_extension(tmp_path)
    _compile_import_vectorcall_extension(tmp_path)  # same site as demo
    main = tmp_path / "main.py"
    main.write_text(
        "import imdemo\nprint('im', imdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "im 7" in run.stdout, run.stdout


def test_pcc_native_sys_getobject_flags_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PySys_GetObject (batch 15) — numpy's
    npy_static_data init reads sys.flags.optimize at IMPORT and fails on NULL.
    check() returns 7: PySys_GetObject("flags") is a non-NULL real namespace,
    flags.optimize == 0 (accurate for pcc's no-O compile, matching numpy's exact
    PyObject_GetAttrString pattern), and an unprovided sys attr returns NULL —
    under strict --python-libpython=off --backend self."""
    site = _compile_sysflags_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import sfdemo\nprint('sf', sfdemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "sf 7" in run.stdout, run.stdout


def test_pcc_native_generic_getdict_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyObject_GenericGetDict (batch 16), which numpy
    installs as a `__dict__` getset. A module-level function gets the module as
    self; check() returns 3 when PyObject_GenericGetDict(self, NULL) is a non-NULL
    dict that contains the module's own "check" entry — under strict
    --python-libpython=off --backend self."""
    site = _compile_genericgetdict_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import gddemo\nprint('gd', gddemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "gd 3" in run.stdout, run.stdout


def test_pcc_native_seqiter_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PySeqIter_New (batch 17) — a real sequence
    iterator driven through the C-API PyIter_Next path. check() returns 1 when
    iterating the tuple (10,20,30) yields exactly those 3 values (sum 60) then
    NULL, under strict --python-libpython=off --backend self."""
    site = _compile_seqiter_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import sidemo\nprint('si', sidemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "si 1" in run.stdout, run.stdout


def test_pcc_native_pymethod_new_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PyMethod_New (batch 17) — binds a function to a
    self via the runtime's instance-method machinery. check() returns 7 when the
    func is reachable, PyMethod_New(func, self) is non-NULL, and the result is a
    real callable, under strict --python-libpython=off --backend self."""
    site = _compile_pymethod_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import mndemo\nprint('mn', mndemo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "mn 7" in run.stdout, run.stdout


def test_pcc_native_batch18_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 18 full-module host symbols (PyDict_Copy,
    PyDict_Merge, PyUnicode_Format, PyObject_GenericGetAttr/SetAttr), all routed
    to existing pcc primitives. check() returns 15 when copy/merge/format and the
    generic getattr/setattr round-trip all behave, under strict
    --python-libpython=off --backend self."""
    site = _compile_batch18_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import b18demo\nprint('b18', b18demo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b18 15" in run.stdout, run.stdout


def test_pcc_native_batch19_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 19 full-module host symbols (PySlice_New +
    PySlice_GetIndicesEx, PyArg_UnpackTuple, PyDictProxy_New, PyObject_Init).
    check() returns 31 when slice index computation (both an explicit and an
    all-None slice), tuple unpacking, the dict proxy read, and PyObject_Init
    passthrough all behave, under strict --python-libpython=off --backend self."""
    site = _compile_batch19_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import b19demo\nprint('b19', b19demo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b19 31" in run.stdout, run.stdout


def test_pcc_native_batch2021_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 20/21 host surface (PyUnicode_KIND,
    PyUnicode_READ_CHAR, PyUnicode_1BYTE_DATA, PyNumber_Divmod, _Py_HashDouble) —
    the symbols that let all 98 numpy _core files compile and link. check()
    returns 15 when KIND/READ_CHAR/1BYTE_DATA read the UTF-8 buffer correctly,
    PyNumber_Divmod(17,5)==(3,2), and _Py_HashDouble(NULL,2.0)==2, under strict
    --python-libpython=off --backend self."""
    site = _compile_batch2021_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import b21demo\nprint('b21', b21demo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b21 15" in run.stdout, run.stdout


def test_pcc_native_batch22_host_symbols_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for batch 22 host symbols (PyLong_FromUnicodeObject,
    PyFloat_FromString, PyLong_AsLongLongAndOverflow, PySlice_AdjustIndices) from
    numpy's C++ umath layer. check() returns 15 when str->int, str->float,
    long-long-with-overflow, and slice-index adjustment all behave, under strict
    --python-libpython=off --backend self."""
    site = _compile_batch22_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import b22demo\nprint('b22', b22demo.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "b22 15" in run.stdout, run.stdout


def test_pcc_native_set_type_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for Py_SET_TYPE -> pcc_capi_set_type (batch 23), the
    sole symbol undefined when link-testing numpy's full _core. check() returns 3
    when a fresh Foo instance reports type Foo and, after Py_SET_TYPE(o, &BarType),
    reports type Bar, under strict --python-libpython=off --backend self."""
    site = _compile_settype_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import stdemo2\nprint('st2', stdemo2.check())\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "st2 3" in run.stdout, run.stdout


def test_pcc_native_multiphase_init_under_self_backend_no_libpython(tmp_path):
    """Behavioral regression for PEP 489 multi-phase C-extension init (numpy's
    _multiarray_umath uses it): PyInit returns PyModuleDef_Init(&def) with a
    Py_mod_exec slot, and the no-libpython loader must build the module + run the
    exec slot. The slot registers answer=42; this asserts mpdemo.answer == 42,
    proving the loader executes Py_mod_exec under --python-libpython=off."""
    site = _compile_multiphase_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import mpdemo\nprint('mp', mpdemo.answer)\n", encoding="utf-8"
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(main), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert "mp 42" in run.stdout, run.stdout


def test_pcc_native_extension_capsule_roundtrip_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_capsule_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import capsdemo\n"
        "print(capsdemo.value())\n"
        "print(capsdemo.context_score())\n"
        "print(capsdemo.destructor_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["37", "48", "13"]


def test_pcc_native_extension_capsule_import_loads_provider_under_self_backend_no_libpython(
    tmp_path,
):
    site = _compile_capsule_import_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import capicons\n"
        "print(capicons.value())\n"
        "print(capicons.table_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_RUNTIME_CC"] = "cc"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["73", "151"]


def test_pcc_native_extension_numpy_capi_provider_minimal_array_metadata(tmp_path):
    site = _compile_numpy_capi_provider_extensions(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import pccnpcons\n"
        "print(pccnpcons.table_shape_score())\n"
        "print(pccnpcons.unsupported_score())\n"
        "print(pccnpcons.ufunc_registration_score())\n"
        "print(pccnpcons.inferred_dtype_score())\n"
        "print(pccnpcons.descr_score())\n"
        "print(pccnpcons.fromany_score())\n"
        "print(pccnpcons.array_metadata_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "117",
        "101",
        "111111111111111",
        "11111111111111111",
        "11111111111111",
        "1111111111111111111",
        "111111111111111",
    ]


def test_pcc_native_extension_cache_string_args_and_multiple_methods(tmp_path):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "import featuredemo as again\n"
        "print(featuredemo.count())\n"
        "print(again.count())\n"
        "print(featuredemo.echo('hello'))\n"
        "print(featuredemo.add(4, 5))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1", "hello", "9"]


def test_pcc_native_extension_singleton_return_macros(tmp_path):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "print(1 if featuredemo.return_none() is None else 0)\n"
        "print(1 if featuredemo.return_true() else 0)\n"
        "print(1 if not featuredemo.return_false() else 0)\n"
        "print(1 if featuredemo.return_notimplemented() is NotImplemented else 0)\n"
        "print(featuredemo.identity_score())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1", "1", "1", "1", "11111"]


def test_pcc_native_extension_method_exception_propagates_and_module_still_works(
    tmp_path,
):
    site = _compile_feature_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import featuredemo\n"
        "try:\n"
        "    featuredemo.fail()\n"
        "    print('unexpected-success')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "print(featuredemo.add(6, 7))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["feature method failed", "13"]


def test_pcc_native_extension_buffer_protocol_for_bytes_bytearray_memoryview(tmp_path):
    site = _compile_buffer_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import bufdemo\n"
        "b = b'ABC'\n"
        "ba = bytearray(b'\\x01\\x02\\x03')\n"
        "mv = memoryview(b'\\x04\\x05\\x06')\n"
        "print(bufdemo.sum(b))\n"
        "print(bufdemo.sum(ba))\n"
        "print(bufdemo.sum(mv))\n"
        "print(bufdemo.writable(b))\n"
        "print(bufdemo.metadata_score(b))\n"
        "print(bufdemo.metadata_score(ba))\n"
        "print(bufdemo.view_sum(ba))\n"
        "print(bufdemo.writable(ba))\n"
        "print(bufdemo.from_memory_sum())\n"
        "print(bufdemo.from_memory_write_sum())\n"
        "print(bufdemo.memoryview_inspect_score(ba))\n"
        "print(bufdemo.check_score(b, ba, mv, 123))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "198",
        "6",
        "15",
        "0",
        "3111311",
        "3211311",
        "6",
        "1",
        "24",
        "15",
        "711113111",
        "1111",
    ]


def test_pcc_native_extension_pyobject_call_class_constructor(tmp_path):
    site = _compile_call_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import calldemo\n"
        "class Box:\n"
        "    x: int\n"
        "obj = calldemo.call0(Box)\n"
        "print(isinstance(obj, Box))\n"
        "print(calldemo.bad_args(Box))\n"
        "def answer():\n"
        "    return 42\n"
        "fn = [answer][0]\n"
        "print(calldemo.call0(fn))\n"
        "print(calldemo.call_object0(fn))\n"
        "print(isinstance(calldemo.call_one(Box), Box))\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["True", "1", "42", "42", "True"]


def test_pcc_native_extension_tuple_dict_attr_helpers(tmp_path):
    site = _compile_object_api_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "import objectapi\n"
        "class Box:\n"
        "    answer: int\n"
        "box = Box()\n"
        "print(objectapi.tuple_sum())\n"
        "print(objectapi.pack_sum())\n"
        "print(objectapi.dict_answer())\n"
        "print(objectapi.list_sum())\n"
        "print(objectapi.bytes_sum())\n"
        "print(objectapi.bytes_size_sum())\n"
        "print(objectapi.unicode_prefix())\n"
        "print(objectapi.unicode_utf8_size_score())\n"
        "print(objectapi.unicode_decode_length_score())\n"
        "print(objectapi.unicode_encode_concat_score())\n"
        "print(objectapi.unicode_macro_score())\n"
        "print(objectapi.unicode_kind_score())\n"
        "print(objectapi.float_bool_score())\n"
        "print(objectapi.scalar_complex_score())\n"
        "print(objectapi.set_attr(box))\n"
        "print(objectapi.has_attr(box, 'answer'))\n"
        "print(objectapi.has_attr(box, 'missing'))\n"
        "print(objectapi.format_error())\n"
        "print(objectapi.warning_score())\n"
        "print(objectapi.gil_state_score())\n"
        "print(objectapi.error_helper_score())\n"
        "print(objectapi.exception_global_score())\n"
        "print(objectapi.module_dict_magic())\n"
        "print(objectapi.bump_module_global())\n"
        "print(objectapi.SEEN)\n"
        "print(objectapi.bump_module_global())\n"
        "print(objectapi.SEEN)\n"
        "print(objectapi.delete_module_global())\n"
        "print(objectapi.has_attr(objectapi, 'SEEN'))\n"
        "print(objectapi.str_repr_compare())\n"
        "print(objectapi.print_score())\n"
        "print(objectapi.unsigned_sum())\n"
        "print(objectapi.import_self_magic())\n"
        "print(objectapi.sequence_score())\n"
        "print(objectapi.iter_contains_score())\n"
        "print(objectapi.collection_views_score())\n"
        "print(objectapi.set_score())\n"
        "print(objectapi.attr_hash_score())\n"
        "print(objectapi.build_mem_kw_score())\n"
        "print(objectapi.refcnt_macro_score())\n"
        "print(objectapi.memory_os_score())\n"
        "print(objectapi.pythoncapi_compat_score())\n"
        "print(objectapi.mapping_long_exc_score())\n"
        "print(objectapi.call_type_format_score())\n"
        "print(objectapi.call_helper_score())\n"
        "print(objectapi.number_protocol_score())\n"
        "print(objectapi.number_bitwise_score())\n"
        "print(objectapi.has_attr(objectapi, 'CustomError'))\n"
        "print(objectapi.ADDED_REF)\n"
        "print(objectapi.MAGIC)\n"
        "print(objectapi.LABEL)\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "42",
        "14",
        "42",
        "12",
        "198",
        "3210",
        "abc",
        "107",
        "1111",
        "511111111",
        "1111111111",
        "11111111111111",
        "17",
        "111111111",
        "64",
        "1",
        "0",
        "1",
        "11111",
        "111",
        "111111111",
        "8191",
        "13",
        "1",
        "1",
        "2",
        "2",
        "1",
        "0",
        "111111",
        "111",
        "11",
        "12",
        "71126",
        "1531111111",
        "1111112033",
        "1111111111",
        "6611123",
        "1468",
        "11",
        "11111111",
        "111111111",
        "841243",
        "172562",
        "711082",
        "1046015888",
        "8191",
        "1",
        "77",
        "12",
        "ok",
    ]


def test_pcc_native_extension_missing_pyinit_symbol_fails_at_runtime(tmp_path):
    site = _compile_missing_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import badinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "dlsym failed" in run.stderr
    assert "PyInit_badinit" in run.stderr


def test_pcc_native_extension_init_returning_null_fails_at_runtime(tmp_path):
    site = _compile_null_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import nullinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "init failed intentionally" in run.stderr


def test_pcc_native_extension_failed_import_is_not_cached(tmp_path):
    site = _compile_retry_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text(
        "try:\n"
        "    import retryinit\n"
        "    print('unexpected-success')\n"
        "except Exception as exc:\n"
        "    print(str(exc))\n"
        "import retryinit\n"
        "print(retryinit.count())\n",
        encoding="utf-8",
    )
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe, {"PCC_RETRYINIT_FAIL_ONCE": "1"})
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["retry init failed once", "1"]


def test_pcc_native_extension_init_returning_null_without_error_reports_import_failure(
    tmp_path,
):
    site = _compile_silent_null_init_extension(tmp_path)
    main = tmp_path / "main.py"
    main.write_text("import silentnullinit\n", encoding="utf-8")
    exe = tmp_path / "main_bin"
    proc = _compile_main(site, main, exe)
    assert proc.returncode == 0, proc.stderr
    run = _run_main(site, exe)
    assert run.returncode != 0
    assert "native extension init failed" in run.stderr
    assert "silentnullinit" in run.stderr
