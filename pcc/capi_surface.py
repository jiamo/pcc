"""CPython C-API compatibility surface catalogue.

This is not an implementation of every C-API symbol.  It is the executable
priority map used by extension-loader work so gaps are explicit and tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class CApiPriority(IntEnum):
    IMPORT_BLOCKER = 0
    RUNTIME_CORE = 1
    ARRAY_CORE = 2
    NUMPY_CAPI = 3
    DOWNSTREAM_EXTENSION = 4
    ACCELERATION = 5


@dataclass(frozen=True)
class CApiSymbol:
    name: str
    header: str
    priority: CApiPriority
    implemented: bool = False
    notes: str = ""


_DEFAULT_SYMBOLS = (
    CApiSymbol("Py_Initialize", "Python.h", CApiPriority.IMPORT_BLOCKER, True),
    CApiSymbol("Py_INCREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_DECREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_XINCREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_XDECREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_REFCNT", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_SET_REFCNT", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_NewRef", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_XNewRef", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_CLEAR", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_SETREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_XSETREF", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_None", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_True", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_False", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_NotImplemented", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_Is", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_IsNone", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_IsTrue", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_IsFalse", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_RETURN_NONE", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_RETURN_TRUE", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_RETURN_FALSE", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_RETURN_NOTIMPLEMENTED", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_Malloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_Calloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_Realloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_Free", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_RawMalloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_RawCalloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_RawRealloc", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_RawFree", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMem_FREE", "pymem.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Malloc", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Calloc", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Realloc", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Free", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_MALLOC", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_REALLOC", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_FREE", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Del", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_DEL", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyOS_snprintf", "Python.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyOS_vsnprintf", "Python.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyModule_Create", "moduleobject.h", CApiPriority.IMPORT_BLOCKER, True),
    CApiSymbol("PyModule_Create2", "moduleobject.h", CApiPriority.IMPORT_BLOCKER, True),
    CApiSymbol(
        "PyModule_AddObject", "moduleobject.h", CApiPriority.IMPORT_BLOCKER, True
    ),
    CApiSymbol(
        "PyModule_AddObjectRef",
        "moduleobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyModule_Add", "moduleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyArg_ParseTuple", "modsupport.h", CApiPriority.IMPORT_BLOCKER, True),
    CApiSymbol(
        "PyArg_ParseTupleAndKeywords",
        "modsupport.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Supports pcc-native tuple/dict parsing for l/i/O/s/y and optional arguments.",
    ),
    CApiSymbol(
        "Py_BuildValue",
        "modsupport.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Builds pcc-native scalars, strings/bytes, objects, and tuples for common formats.",
    ),
    CApiSymbol("PyLong_FromLong", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyLong_FromUnsignedLong", "longobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyLong_AsLong", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromLongLong", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyLong_FromUnsignedLongLong",
        "longobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyLong_FromInt32", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromInt64", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromUInt32", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromUInt64", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromVoidPtr", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromSsize_t", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromSize_t", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_FromDouble", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsLongLong", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsDouble", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsInt", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsInt32", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsInt64", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsUInt32", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsUInt64", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsVoidPtr", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyLong_AsLongAndOverflow", "longobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyLong_AsUnsignedLong", "longobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyLong_AsUnsignedLongLong",
        "longobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyLong_AsUnsignedLongLongMask",
        "longobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyLong_AsSsize_t", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_AsSize_t", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_Check", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyLong_CheckExact", "longobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBool_FromLong", "boolobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBool_Check", "boolobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyFloat_FromDouble", "floatobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyFloat_AsDouble", "floatobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyFloat_AS_DOUBLE", "floatobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyFloat_Check", "floatobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyFloat_CheckExact", "floatobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_complex", "complexobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyComplex_FromDoubles", "complexobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyComplex_FromCComplex", "complexobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyComplex_AsCComplex", "complexobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyComplex_RealAsDouble",
        "complexobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyComplex_ImagAsDouble",
        "complexobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyComplex_Check", "complexobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyComplex_CheckExact", "complexobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("Py_UNUSED", "Python.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_UCS1", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_UCS2", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_UCS4", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_1BYTE_KIND", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_2BYTE_KIND", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_4BYTE_KIND", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_FromString", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_FromStringAndSize",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyUnicode_FromFormat", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_FromFormatV", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_InternFromString",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyUnicode_FromKindAndData",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Converts UCS1/UCS2/UCS4 buffers into pcc-native UTF-8 strings.",
    ),
    CApiSymbol(
        "PyUnicode_FromOrdinal",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Creates a one-codepoint pcc-native string from a Unicode ordinal.",
    ),
    CApiSymbol(
        "PyUnicode_AsUCS4",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Copies pcc-native strings into caller-provided UCS4 buffers.",
    ),
    CApiSymbol(
        "PyUnicode_AsUCS4Copy",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Allocates a UCS4 copy of pcc-native strings with PyMem_Malloc.",
    ),
    CApiSymbol(
        "PyUnicode_FromEncodedObject",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Narrow pcc-native decode path for str and bytes with UTF-8/ASCII/Latin-1 labels.",
    ),
    CApiSymbol(
        "PyUnicode_AsEncodedString",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Narrow pcc-native encode path for str to bytes with UTF-8/ASCII/Latin-1 labels.",
    ),
    CApiSymbol("PyUnicode_AsUTF8", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_AsUTF8AndSize",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyUnicode_AsUTF8String", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_AsASCIIString", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyUnicode_Check", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_CheckExact", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_GetLength", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_GET_LENGTH", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyUnicode_Compare", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_CompareWithASCIIString",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyUnicode_Tailmatch", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyUnicode_Find", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_ReadChar", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_FindChar", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyUnicode_Count", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyUnicode_Replace", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_Substring", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyUnicode_Contains", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyUnicode_Concat", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyUnicode_EqualToUTF8",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyUnicode_EqualToUTF8AndSize",
        "unicodeobject.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "Py_UNICODE_ISSPACE", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISDIGIT", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISDECIMAL", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISNUMERIC", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISLOWER", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISUPPER", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISTITLE", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISALPHA", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "Py_UNICODE_ISALNUM", "unicodeobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyErr_SetString", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_SetNone", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_SetObject", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyErr_Format",
        "pyerrors.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Formats common C printf and CPython %R/%S/%U exception messages.",
    ),
    CApiSymbol(
        "PyErr_FormatV",
        "pyerrors.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "va_list form of the same narrow formatter used by PyErr_Format.",
    ),
    CApiSymbol("PyErr_NoMemory", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_SetFromErrno", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyErr_SetFromErrnoWithFilenameObject",
        "pyerrors.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyErr_NewException", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_BadInternalCall", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_WarnEx", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_WarnFormat", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_WriteUnraisable", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_Print", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_CheckSignals", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_Occurred", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_Clear", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyErr_GivenExceptionMatches", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyErr_ExceptionMatches", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_Fetch", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyErr_Restore", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyObject_Call",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
        "Dispatches pcc-native callables through py_obj_call.",
    ),
    CApiSymbol("PyObject_CallObject", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_CallNoArgs", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_CallOneArg", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Vectorcall", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyObject_VectorcallMethod",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyObject_CallFunction", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_CallMethod", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyObject_CallMethodNoArgs",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyObject_CallMethodOneArg",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyObject_CallFunctionObjArgs",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("Py_IsInitialized", "pylifecycle.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyGILState_Ensure", "pystate.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyGILState_Release", "pystate.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyGILState_Check", "pystate.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_GetAttrString", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_GetAttr", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_GetOptionalAttr", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyObject_GetOptionalAttrString",
        "object.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyObject_SetAttrString", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_SetAttr", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_HasAttr", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_HasAttrString", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyObject_HasAttrWithError", "object.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyObject_HasAttrStringWithError",
        "object.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyObject_IsTrue", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Not", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Hash", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyCallable_Check", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Str", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Repr", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Bytes", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Format", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("Py_PRINT_RAW", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Print", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Type", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_IsInstance", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_RichCompare", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_RichCompareBool", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_GetItem", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_SetItem", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_DelItem", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Size", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_Length", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_LengthHint", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_SelfIter", "object.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyObject_GetIter", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyIter_Next", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyIter_NextItem", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyIter_Check", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Add", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Subtract", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Multiply", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_TrueDivide", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_FloorDivide", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Remainder", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Power", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Negative", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Positive", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Absolute", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Check", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Long", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Float", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_And", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Or", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Xor", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Invert", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Lshift", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Rshift", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_Index", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyNumber_AsSsize_t", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyIndex_Check", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_New", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_SetItem", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_GetItem", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_Size", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_GET_ITEM", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_GET_SIZE", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_SET_ITEM", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_Pack", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_Check", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyTuple_CheckExact", "tupleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_New", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_SetItem", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_GetItem", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_GetItemRef", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_Size", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_GET_ITEM", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_GET_SIZE", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_SET_ITEM", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_Append", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_AsTuple", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_Check", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyList_CheckExact", "listobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_New", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_SetItem", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_SetItemString", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_GetItem", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_GetItemString", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyDict_GetItemWithError", "dictobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyDict_GetItemRef", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyDict_GetItemStringRef", "dictobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyDict_SetDefaultRef", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Pop", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_PopString", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_DelItem", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_DelItemString", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Size", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Contains", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyDict_ContainsString", "dictobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyDict_Next", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Keys", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Values", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Items", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_Check", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyDict_CheckExact", "dictobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_New", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_Add", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_Contains", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_Discard", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_Size", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_GET_SIZE", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_Check", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySet_CheckExact", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyAnySet_Check", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyAnySet_CheckExact", "setobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBytes_FromString", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyBytes_FromStringAndSize", "bytesobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyBytes_AsString", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyBytes_AsStringAndSize", "bytesobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyBytes_AS_STRING", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBytes_Size", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBytes_GET_SIZE", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBytes_Check", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyBytes_CheckExact", "bytesobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_BaseException", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_Exception", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_ValueError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_TypeError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_RuntimeError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_KeyError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_IndexError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_AttributeError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_MemoryError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_OverflowError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_SystemError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_NameError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyExc_NotImplementedError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyExc_ArithmeticError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_LookupError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_OSError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_IOError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_AssertionError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_StopIteration", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyExc_StopAsyncIteration", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyExc_ZeroDivisionError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyExc_ReferenceError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_BufferError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_ImportError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_ImportWarning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyExc_FloatingPointError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyExc_RecursionError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyExc_UnicodeDecodeError", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyExc_Warning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_UserWarning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyExc_RuntimeWarning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyExc_DeprecationWarning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyExc_FutureWarning", "pyerrors.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyModule_AddIntConstant", "moduleobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyModule_AddStringConstant", "moduleobject.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyModule_GetDict", "moduleobject.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyImport_ImportModule", "import.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Check", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_Check", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_Size", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_Length", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyMapping_GetItemString", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyMapping_SetItemString", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PyMapping_HasKey", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_HasKeyString", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PyMapping_GetOptionalItem", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyMapping_GetOptionalItemString",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol(
        "PyMapping_HasKeyWithError", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PyMapping_HasKeyStringWithError",
        "abstract.h",
        CApiPriority.RUNTIME_CORE,
        True,
    ),
    CApiSymbol("PyMapping_Keys", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_Values", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyMapping_Items", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Size", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Length", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_GetItem", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_SetItem", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Contains", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Concat", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Repeat", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PySequence_InPlaceConcat", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol(
        "PySequence_InPlaceRepeat", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PySequence_Fast", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PySequence_Fast_GET_SIZE", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PySequence_Fast_ITEMS", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol(
        "PySequence_Fast_GET_ITEM", "abstract.h", CApiPriority.RUNTIME_CORE, True
    ),
    CApiSymbol("PySequence_List", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PySequence_Tuple", "abstract.h", CApiPriority.RUNTIME_CORE, True),
    CApiSymbol("PyCapsule_New", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_GetPointer", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_GetName", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_GetContext", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_IsValid", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_CheckExact", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_SetContext", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_SetName", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol("PyCapsule_Import", "pycapsule.h", CApiPriority.ARRAY_CORE, True),
    CApiSymbol(
        "PyMemoryView_FromObject",
        "memoryobject.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Creates pcc memoryview objects for exporters handled by PyObject_GetBuffer.",
    ),
    CApiSymbol(
        "PyMemoryView_FromMemory",
        "memoryobject.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Creates copy-backed pcc memoryview objects from raw extension memory.",
    ),
    CApiSymbol(
        "PyMemoryView_Check",
        "memoryobject.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Checks pcc memoryview objects without exposing CPython object layout.",
    ),
    CApiSymbol(
        "PyMemoryView_GET_BUFFER",
        "memoryobject.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Macro over a pcc helper returning buffer metadata for pcc memoryviews.",
    ),
    CApiSymbol(
        "PyMemoryView_GET_BASE",
        "memoryobject.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Macro over a pcc helper returning the borrowed base object.",
    ),
    CApiSymbol(
        "PyObject_GetBuffer",
        "abstract.h",
        CApiPriority.ARRAY_CORE,
        True,
        "SIMPLE/1D contiguous buffers for pcc bytes, bytearray, and memoryview.",
    ),
    CApiSymbol(
        "PyObject_CheckBuffer",
        "abstract.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Checks whether pcc-native bytes, bytearray, or memoryview export a buffer.",
    ),
    CApiSymbol(
        "PyBuffer_Release",
        "abstract.h",
        CApiPriority.ARRAY_CORE,
        True,
        "Releases pcc-native buffer views acquired by PyObject_GetBuffer.",
    ),
    CApiSymbol(
        "PyArray_API",
        "numpy/arrayobject.h",
        CApiPriority.NUMPY_CAPI,
        False,
        "NumPy capsule/API table. Required before import_array() can be a pcc-native claim.",
    ),
    CApiSymbol("PyArray_Type", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArrayDescr_Type", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_DescrFromType", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_FromAny", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_SimpleNew", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol(
        "PyArray_SimpleNewFromData", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI
    ),
    CApiSymbol("PyArray_NDIM", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_DIMS", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_STRIDES", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_DATA", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_DESCR", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_GETITEM", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_SETITEM", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_SIZE", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_ITEMSIZE", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_Check", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_CheckExact", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_DIM", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol("PyArray_BYTES", "numpy/arrayobject.h", CApiPriority.NUMPY_CAPI),
    CApiSymbol(
        "PyUFunc_API",
        "numpy/ufuncobject.h",
        CApiPriority.NUMPY_CAPI,
        False,
        "NumPy ufunc API table. Separate from generic CPython capsule/buffer support.",
    ),
    CApiSymbol(
        "PyUFunc_FromFuncAndData", "numpy/ufuncobject.h", CApiPriority.NUMPY_CAPI
    ),
)


_NUMPY_CAPI_REQUIREMENT = (
    "PyArray_API",
    "PyArray_Type",
    "PyArrayDescr_Type",
    "PyArray_DescrFromType",
    "PyArray_FromAny",
    "PyArray_SimpleNew",
    "PyArray_SimpleNewFromData",
    "PyArray_NDIM",
    "PyArray_DIMS",
    "PyArray_STRIDES",
    "PyArray_DATA",
    "PyArray_DESCR",
    "PyArray_GETITEM",
    "PyArray_SETITEM",
    "PyArray_SIZE",
    "PyArray_ITEMSIZE",
    "PyArray_Check",
    "PyArray_CheckExact",
    "PyArray_DIM",
    "PyArray_BYTES",
    "PyUFunc_API",
    "PyUFunc_FromFuncAndData",
)

_NUMPY_CAPI_TABLE_SLOTS: dict[str, dict[str, object]] = {
    "PyArray_API": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": None,
        "failure_mode": "missing_capsule_provider",
    },
    "PyArray_Type": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 0,
        "failure_mode": "missing_array_type_object",
    },
    "PyArrayDescr_Type": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 1,
        "failure_mode": "missing_dtype_type_object",
    },
    "PyArray_DescrFromType": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 2,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_FromAny": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 3,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_SimpleNew": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 4,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_SimpleNewFromData": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 5,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_NDIM": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 6,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_DIMS": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 7,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_STRIDES": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 8,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_DATA": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 9,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_DESCR": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 10,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_GETITEM": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 11,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_SETITEM": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 12,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_SIZE": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 13,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_ITEMSIZE": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 14,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_Check": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 15,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_CheckExact": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 16,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_DIM": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 7,
        "failure_mode": "unsupported_stub",
    },
    "PyArray_BYTES": {
        "provider_shape": "array_api",
        "table": "_ARRAY_API",
        "slot": 9,
        "failure_mode": "unsupported_stub",
    },
    "PyUFunc_API": {
        "provider_shape": "ufunc_api",
        "table": "_UFUNC_API",
        "slot": None,
        "failure_mode": "missing_capsule_provider",
    },
    "PyUFunc_FromFuncAndData": {
        "provider_shape": "ufunc_api",
        "table": "_UFUNC_API",
        "slot": 0,
        "failure_mode": "unsupported_stub",
    },
}


def _numpy_capi_family(name: str) -> str | None:
    if name == "PyUFunc_API" or name.startswith("PyUFunc_"):
        return "ufunc_api"
    if (
        name == "PyArray_API"
        or name == "PyArray_Type"
        or name == "PyArrayDescr_Type"
        or name.startswith("PyArray_")
    ):
        return "array_api"
    return None


def _numpy_capi_symbol_status(name: str) -> dict[str, object] | None:
    meta = _NUMPY_CAPI_TABLE_SLOTS.get(name)
    if meta is None:
        family = _numpy_capi_family(name)
        if family is None:
            return None
        meta = {
            "provider_shape": family,
            "table": "_UFUNC_API" if family == "ufunc_api" else "_ARRAY_API",
            "slot": None,
            "failure_mode": "unknown_numpy_capi_symbol",
        }
    catalogue = symbols_by_name()
    sym = catalogue.get(name)
    return {
        "symbol": name,
        "capability": "numpy_capi",
        "provider_shape": meta["provider_shape"],
        "table": meta["table"],
        "slot": meta["slot"],
        "implemented": bool(sym.implemented) if sym is not None else False,
        "failure_mode": meta["failure_mode"],
    }


def default_capi_symbols() -> tuple[CApiSymbol, ...]:
    return _DEFAULT_SYMBOLS


def missing_symbols(
    priority_at_most: CApiPriority = CApiPriority.ARRAY_CORE,
) -> list[CApiSymbol]:
    return [
        sym
        for sym in _DEFAULT_SYMBOLS
        if not sym.implemented and sym.priority <= priority_at_most
    ]


def symbols_by_name() -> dict[str, CApiSymbol]:
    return {sym.name: sym for sym in _DEFAULT_SYMBOLS}


def capi_header_manifest(
    required_symbols: tuple[str, ...] | list[str] | None = None,
    *,
    include_dir: str | None = None,
) -> dict[str, object]:
    catalogue = symbols_by_name()
    requested = (
        list(required_symbols)
        if required_symbols is not None
        else [sym.name for sym in _DEFAULT_SYMBOLS]
    )
    headers: list[str] = []
    provided_headers: list[str] = []
    missing_headers: list[str] = []
    symbol_rows: list[dict[str, object]] = []
    unknown: list[str] = []
    include_root = Path(include_dir).expanduser().resolve() if include_dir else None
    for name in requested:
        sym = catalogue.get(name)
        if sym is None:
            unknown.append(name)
            continue
        if sym.header not in headers:
            headers.append(sym.header)
            if include_root is not None:
                if (include_root / sym.header).exists():
                    provided_headers.append(sym.header)
                else:
                    missing_headers.append(sym.header)
        header_path = (
            str(include_root / sym.header) if include_root is not None else None
        )
        provided = bool(
            include_root is not None
            and header_path is not None
            and Path(header_path).exists()
        )
        symbol_rows.append(
            {
                "name": sym.name,
                "header": sym.header,
                "header_path": header_path,
                "provided_by_package": provided,
                "implemented": sym.implemented,
                "priority": int(sym.priority),
            }
        )
    return {
        "include_dir": include_dir,
        "headers": headers,
        "provided_headers": provided_headers,
        "missing_headers": missing_headers,
        "symbols": symbol_rows,
        "unknown_symbols": unknown,
    }


def extension_abi_plan(
    required_symbols: tuple[str, ...] | list[str] | None = None,
    *,
    provider: str = "extension",
    expected_abi: int | None = None,
    actual_abi: int | None = None,
    abi_mode: str = "pcc-native",
    include_dir: str | None = None,
    require_capsule: bool = False,
    require_buffer: bool = False,
    require_memoryview: bool = False,
    require_numpy_capi: bool = False,
) -> dict[str, object]:
    requested = list(required_symbols or [])
    if require_capsule:
        requested.extend(
            [
                "PyCapsule_New",
                "PyCapsule_GetPointer",
                "PyCapsule_GetName",
                "PyCapsule_GetContext",
                "PyCapsule_IsValid",
                "PyCapsule_CheckExact",
                "PyCapsule_SetContext",
                "PyCapsule_SetName",
                "PyCapsule_Import",
            ]
        )
    if require_buffer:
        requested.extend(
            ["PyObject_GetBuffer", "PyObject_CheckBuffer", "PyBuffer_Release"]
        )
    if require_memoryview:
        requested.extend(
            [
                "PyMemoryView_FromObject",
                "PyMemoryView_FromMemory",
                "PyMemoryView_Check",
                "PyMemoryView_GET_BUFFER",
                "PyMemoryView_GET_BASE",
            ]
        )
    if require_numpy_capi:
        requested.extend(_NUMPY_CAPI_REQUIREMENT)
    requested = list(dict.fromkeys(requested))

    manifest = capi_header_manifest(requested, include_dir=include_dir)
    catalogue = symbols_by_name()
    numpy_capi_status = [
        status
        for name in requested
        if (status := _numpy_capi_symbol_status(name)) is not None
    ]
    missing = [
        name
        for name in requested
        if name in catalogue and not catalogue[name].implemented
    ]
    diagnostics: list[dict[str, object]] = []
    for name in missing:
        sym = catalogue[name]
        numpy_family = (
            _numpy_capi_family(name)
            if sym.priority == CApiPriority.NUMPY_CAPI
            else None
        )
        if numpy_family is not None:
            status = _numpy_capi_symbol_status(name) or {}
            diagnostics.append(
                {
                    "code": "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL",
                    "symbol": name,
                    "capability": "numpy_capi",
                    "provider_shape": numpy_family,
                    "table": status.get("table"),
                    "slot": status.get("slot"),
                    "failure_mode": status.get("failure_mode"),
                    "message": (
                        f"{name} requires a pcc-native NumPy {numpy_family} "
                        f"provider; it is not implemented for {abi_mode}"
                    ),
                }
            )
            continue
        diagnostics.append(
            {
                "code": "PCC-EXT-MISSING-CAPI-SYMBOL",
                "symbol": name,
                "message": f"{name} is not implemented for {abi_mode}",
            }
        )
    for name in manifest["unknown_symbols"]:  # type: ignore[index]
        diagnostics.append(
            {
                "code": "PCC-EXT-UNKNOWN-CAPI-SYMBOL",
                "symbol": name,
                "message": f"{name} is not in the pcc C-API catalogue",
            }
        )
    if include_dir is not None:
        for header in manifest["missing_headers"]:  # type: ignore[index]
            diagnostics.append(
                {
                    "code": "PCC-EXT-MISSING-CAPI-HEADER",
                    "header": header,
                    "include_dir": include_dir,
                    "message": f"{header} was not provided by {include_dir}",
                }
            )
    abi_diag: dict[str, object] | None = None
    if expected_abi is not None and actual_abi is not None:
        abi_diag = abi_version_diagnostic(
            provider=provider,
            expected=expected_abi,
            actual=actual_abi,
            abi_mode=abi_mode,
        )
        if not abi_diag["ok"]:
            diagnostics.append(abi_diag)

    return {
        "ok": not diagnostics,
        "provider": provider,
        "abi_mode": abi_mode,
        "required_symbols": requested,
        "header_manifest": manifest,
        "missing_symbols": missing,
        "unknown_symbols": manifest["unknown_symbols"],
        "numpy_capi_status": numpy_capi_status,
        "abi_version": abi_diag,
        "diagnostics": diagnostics,
    }


def symbol_report() -> dict:
    missing_by_priority: dict[str, list[str]] = {}
    implemented_by_priority: dict[str, list[str]] = {}
    for priority in CApiPriority:
        missing_by_priority[priority.name.lower()] = [
            s.name
            for s in _DEFAULT_SYMBOLS
            if s.priority == priority and not s.implemented
        ]
        implemented_by_priority[priority.name.lower()] = [
            s.name for s in _DEFAULT_SYMBOLS if s.priority == priority and s.implemented
        ]
    return {
        "symbols": [
            {
                "name": s.name,
                "header": s.header,
                "priority": int(s.priority),
                "implemented": s.implemented,
                "notes": s.notes,
            }
            for s in _DEFAULT_SYMBOLS
        ],
        "missing_import_blockers": [
            s.name for s in missing_symbols(CApiPriority.IMPORT_BLOCKER)
        ],
        "missing_runtime_core": [
            s.name
            for s in _DEFAULT_SYMBOLS
            if s.priority == CApiPriority.RUNTIME_CORE and not s.implemented
        ],
        "missing_array_core": [
            s.name for s in missing_symbols(CApiPriority.ARRAY_CORE)
        ],
        "missing_numpy_capi": [
            s.name
            for s in _DEFAULT_SYMBOLS
            if s.priority == CApiPriority.NUMPY_CAPI and not s.implemented
        ],
        "numpy_capi_status": [
            status
            for s in _DEFAULT_SYMBOLS
            if s.priority == CApiPriority.NUMPY_CAPI
            if (status := _numpy_capi_symbol_status(s.name)) is not None
        ],
        "missing_downstream_extension": [
            s.name
            for s in _DEFAULT_SYMBOLS
            if s.priority == CApiPriority.DOWNSTREAM_EXTENSION and not s.implemented
        ],
        "missing_by_priority": missing_by_priority,
        "implemented_by_priority": implemented_by_priority,
    }


def abi_version_diagnostic(
    *,
    provider: str,
    expected: int,
    actual: int,
    abi_mode: str = "pcc-native",
) -> dict[str, object]:
    ok = expected == actual
    return {
        "ok": ok,
        "code": None if ok else "PCC-EXT-ABI-VERSION-MISMATCH",
        "provider": provider,
        "expected": expected,
        "actual": actual,
        "abi_mode": abi_mode,
        "message": (
            "ABI versions match"
            if ok
            else (
                f"{provider} ABI version mismatch: expected {expected}, "
                f"got {actual} under {abi_mode}"
            )
        ),
    }
