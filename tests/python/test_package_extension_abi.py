from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

from pcc.capi_surface import capi_header_manifest, extension_abi_plan

REPO = Path(__file__).resolve().parents[2]

GENERIC_REFCNT_MACRO_SYMBOLS = [
    "Py_REFCNT",
    "Py_SET_REFCNT",
]

GENERIC_MEMORY_OS_SYMBOLS = [
    "PyMem_RawMalloc",
    "PyMem_RawCalloc",
    "PyMem_RawRealloc",
    "PyMem_RawFree",
    "PyMem_FREE",
    "PyObject_Malloc",
    "PyObject_Calloc",
    "PyObject_Realloc",
    "PyObject_Free",
    "PyObject_MALLOC",
    "PyObject_REALLOC",
    "PyObject_FREE",
    "PyObject_Del",
    "PyObject_DEL",
    "PyOS_snprintf",
    "PyOS_vsnprintf",
]

GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS = [
    "PyLong_FromSsize_t",
    "PyLong_FromSize_t",
    "PyLong_FromInt32",
    "PyLong_FromInt64",
    "PyLong_FromUInt32",
    "PyLong_FromUInt64",
    "PyLong_FromVoidPtr",
    "PyLong_AsInt",
    "PyLong_FromDouble",
    "PyLong_AsInt32",
    "PyLong_AsInt64",
    "PyLong_AsUInt32",
    "PyLong_AsUInt64",
    "PyLong_AsVoidPtr",
    "PyLong_AsLongAndOverflow",
    "PyLong_AsUnsignedLong",
    "PyLong_AsUnsignedLongLong",
    "PyLong_AsUnsignedLongLongMask",
    "PyLong_AsSsize_t",
    "PyLong_AsSize_t",
    "PyLong_AsDouble",
    "PyLong_Check",
    "PyLong_CheckExact",
    "PyObject_GetItem",
    "PyObject_SetItem",
    "PyObject_DelItem",
    "PyDict_GetItemWithError",
    "PyDict_GetItemRef",
    "PyDict_GetItemStringRef",
    "PyDict_SetDefaultRef",
    "PyDict_Pop",
    "PyDict_PopString",
    "PyDict_Size",
    "PyDict_Contains",
    "PyDict_ContainsString",
    "PyDict_Next",
    "PyDict_Keys",
    "PyDict_Values",
    "PyDict_Items",
    "PySet_New",
    "PySet_Add",
    "PySet_Contains",
    "PySet_Discard",
    "PySet_Size",
    "PySet_GET_SIZE",
    "PySet_Check",
    "PySet_CheckExact",
    "PyAnySet_Check",
    "PyAnySet_CheckExact",
    "PyObject_LengthHint",
    "PyObject_Size",
    "PyObject_Length",
    "PyMapping_Check",
    "PyMapping_Size",
    "PyMapping_Length",
    "PyMapping_GetItemString",
    "PyMapping_SetItemString",
    "PyMapping_HasKey",
    "PyMapping_HasKeyString",
    "PyMapping_GetOptionalItem",
    "PyMapping_GetOptionalItemString",
    "PyMapping_HasKeyWithError",
    "PyMapping_HasKeyStringWithError",
    "PyMapping_Keys",
    "PyMapping_Values",
    "PyMapping_Items",
    "PyErr_SetNone",
    "PyErr_SetObject",
    "PyErr_FormatV",
    "PyErr_BadInternalCall",
    "PyErr_SetFromErrno",
    "PyErr_SetFromErrnoWithFilenameObject",
    "PyErr_GivenExceptionMatches",
    "PyErr_ExceptionMatches",
    "PyErr_Fetch",
    "PyErr_Restore",
    "PyExc_BaseException",
    "PyExc_Exception",
    "PyExc_ValueError",
    "PyExc_TypeError",
    "PyExc_RuntimeError",
    "PyExc_KeyError",
    "PyExc_IndexError",
    "PyExc_AttributeError",
    "PyExc_MemoryError",
    "PyExc_OverflowError",
    "PyExc_SystemError",
    "PyExc_NameError",
    "PyExc_NotImplementedError",
    "PyExc_ArithmeticError",
    "PyExc_LookupError",
    "PyExc_OSError",
    "PyExc_IOError",
    "PyExc_AssertionError",
    "PyExc_StopIteration",
    "PyExc_StopAsyncIteration",
    "PyExc_ZeroDivisionError",
    "PyExc_ReferenceError",
    "PyExc_BufferError",
    "PyExc_ImportError",
    "PyExc_ImportWarning",
    "PyExc_FloatingPointError",
    "PyExc_RecursionError",
    "PyExc_UnicodeDecodeError",
    "PyErr_WarnEx",
    "PyErr_WarnFormat",
    "PyErr_WriteUnraisable",
    "PyErr_Print",
    "PyErr_CheckSignals",
    "PyExc_Warning",
    "PyExc_UserWarning",
    "PyExc_RuntimeWarning",
    "PyExc_DeprecationWarning",
    "PyExc_FutureWarning",
]

GENERIC_CALL_TYPE_FORMAT_SYMBOLS = [
    "PyUnicode_FromFormat",
    "PyUnicode_FromFormatV",
    "PyUnicode_FromKindAndData",
    "PyUnicode_FromOrdinal",
    "PyUnicode_AsUCS4",
    "PyUnicode_AsUCS4Copy",
    "PyUnicode_FromEncodedObject",
    "PyUnicode_GetLength",
    "PyUnicode_AsUTF8AndSize",
    "PyUnicode_AsEncodedString",
    "PyUnicode_AsUTF8String",
    "PyUnicode_AsASCIIString",
    "PyUnicode_Tailmatch",
    "PyUnicode_Find",
    "PyUnicode_ReadChar",
    "PyUnicode_FindChar",
    "PyUnicode_Count",
    "PyUnicode_Replace",
    "PyUnicode_Substring",
    "PyUnicode_Contains",
    "PyUnicode_Concat",
    "PyUnicode_EqualToUTF8",
    "PyUnicode_EqualToUTF8AndSize",
    "PyBytes_AS_STRING",
    "PyBytes_GET_SIZE",
    "PyObject_CallNoArgs",
    "PyObject_CallOneArg",
    "PyObject_Vectorcall",
    "PyObject_VectorcallMethod",
    "PyObject_CallFunction",
    "PyObject_CallMethod",
    "PyObject_CallMethodNoArgs",
    "PyObject_CallMethodOneArg",
    "PyObject_Bytes",
    "PyObject_Format",
    "Py_PRINT_RAW",
    "PyObject_Print",
    "PyObject_Type",
    "PyObject_IsInstance",
    "PyModule_Add",
]

GENERIC_UNICODE_MACRO_SYMBOLS = [
    "Py_UNUSED",
    "Py_UCS1",
    "Py_UCS2",
    "Py_UCS4",
    "PyUnicode_1BYTE_KIND",
    "PyUnicode_2BYTE_KIND",
    "PyUnicode_4BYTE_KIND",
    "PyUnicode_GET_LENGTH",
    "Py_UNICODE_ISSPACE",
    "Py_UNICODE_ISDIGIT",
    "Py_UNICODE_ISDECIMAL",
    "Py_UNICODE_ISNUMERIC",
    "Py_UNICODE_ISLOWER",
    "Py_UNICODE_ISUPPER",
    "Py_UNICODE_ISTITLE",
    "Py_UNICODE_ISALPHA",
    "Py_UNICODE_ISALNUM",
]

GENERIC_SCALAR_COMPLEX_SYMBOLS = [
    "PyBool_Check",
    "PyFloat_Check",
    "PyFloat_CheckExact",
    "PyFloat_AS_DOUBLE",
    "Py_complex",
    "PyComplex_FromDoubles",
    "PyComplex_FromCComplex",
    "PyComplex_AsCComplex",
    "PyComplex_RealAsDouble",
    "PyComplex_ImagAsDouble",
    "PyComplex_Check",
    "PyComplex_CheckExact",
]

GENERIC_GIL_STATE_SYMBOLS = [
    "Py_IsInitialized",
    "PyGILState_Ensure",
    "PyGILState_Release",
    "PyGILState_Check",
]

GENERIC_NUMBER_PROTOCOL_SYMBOLS = [
    "PyObject_Not",
    "PyNumber_Add",
    "PyNumber_Subtract",
    "PyNumber_Multiply",
    "PyNumber_TrueDivide",
    "PyNumber_FloorDivide",
    "PyNumber_Remainder",
    "PyNumber_Power",
    "PyNumber_Negative",
    "PyNumber_Positive",
    "PyNumber_Absolute",
    "PyNumber_Check",
    "PyNumber_Long",
    "PyNumber_Float",
    "PyNumber_And",
    "PyNumber_Or",
    "PyNumber_Xor",
    "PyNumber_Invert",
    "PyNumber_Lshift",
    "PyNumber_Rshift",
    "PyNumber_Index",
    "PyNumber_AsSsize_t",
    "PyIndex_Check",
]

GENERIC_ABSTRACT_PROTOCOL_SYMBOLS = [
    "PyObject_SelfIter",
    "PyObject_GetIter",
    "PyIter_Next",
    "PyIter_NextItem",
    "PyIter_Check",
    "PySequence_Contains",
    "PySequence_SetItem",
    "PySequence_Concat",
    "PySequence_Repeat",
    "PySequence_InPlaceConcat",
    "PySequence_InPlaceRepeat",
]

GENERIC_CONTAINER_MACRO_SYMBOLS = [
    "PyTuple_GET_ITEM",
    "PyTuple_GET_SIZE",
    "PyTuple_SET_ITEM",
    "PyList_GET_ITEM",
    "PyList_GET_SIZE",
    "PyList_SET_ITEM",
    "PyList_GetItemRef",
    "PyList_AsTuple",
    "PySequence_Fast_GET_ITEM",
]

GENERIC_CAPSULE_SYMBOLS = [
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

GENERIC_MEMORYVIEW_SYMBOLS = [
    "PyMemoryView_FromObject",
    "PyMemoryView_FromMemory",
    "PyMemoryView_Check",
    "PyMemoryView_GET_BUFFER",
    "PyMemoryView_GET_BASE",
]

GENERIC_SINGLETON_RETURN_SYMBOLS = [
    "Py_None",
    "Py_True",
    "Py_False",
    "Py_NotImplemented",
    "Py_Is",
    "Py_IsNone",
    "Py_IsTrue",
    "Py_IsFalse",
    "Py_RETURN_NONE",
    "Py_RETURN_TRUE",
    "Py_RETURN_FALSE",
    "Py_RETURN_NOTIMPLEMENTED",
]


def _find_current_pcc1() -> Path | None:
    return find_current_pcc1(REPO)


def test_capi_header_manifest_tracks_headers_and_unknown_symbols():
    manifest = capi_header_manifest(
        [
            "Py_Initialize",
            "Py_INCREF",
            "Py_DECREF",
            "Py_XINCREF",
            "Py_XDECREF",
            "Py_NewRef",
            "Py_XNewRef",
            "Py_CLEAR",
            "Py_SETREF",
            "Py_XSETREF",
            *GENERIC_REFCNT_MACRO_SYMBOLS,
            "PyMem_Malloc",
            "PyMem_Calloc",
            "PyMem_Realloc",
            "PyMem_Free",
            *GENERIC_MEMORY_OS_SYMBOLS,
            *GENERIC_SINGLETON_RETURN_SYMBOLS,
            *GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS,
            *GENERIC_CALL_TYPE_FORMAT_SYMBOLS,
            *GENERIC_UNICODE_MACRO_SYMBOLS,
            *GENERIC_SCALAR_COMPLEX_SYMBOLS,
            *GENERIC_GIL_STATE_SYMBOLS,
            *GENERIC_NUMBER_PROTOCOL_SYMBOLS,
            *GENERIC_ABSTRACT_PROTOCOL_SYMBOLS,
            "PyObject_Call",
            "PyObject_CallObject",
            "PyObject_CallFunctionObjArgs",
            "Py_BuildValue",
            "PyArg_ParseTupleAndKeywords",
            "PyObject_CheckBuffer",
            "PyTuple_New",
            *GENERIC_CONTAINER_MACRO_SYMBOLS,
            "PyDict_SetItemString",
            "PyObject_GetAttrString",
            *GENERIC_CAPSULE_SYMBOLS,
            "PyObject_GetBuffer",
            "Missing_Symbol",
        ],
        include_dir="/pcc/include",
    )
    assert manifest["include_dir"] == "/pcc/include"
    assert manifest["headers"] == [
        "Python.h",
        "object.h",
        "pymem.h",
        "longobject.h",
        "dictobject.h",
        "setobject.h",
        "abstract.h",
        "pyerrors.h",
        "unicodeobject.h",
        "bytesobject.h",
        "moduleobject.h",
        "boolobject.h",
        "floatobject.h",
        "complexobject.h",
        "pylifecycle.h",
        "pystate.h",
        "modsupport.h",
        "tupleobject.h",
        "listobject.h",
        "pycapsule.h",
    ]
    assert manifest["unknown_symbols"] == ["Missing_Symbol"]


def test_extension_abi_plan_reports_capsule_buffer_memoryview_and_version_gaps():
    plan = extension_abi_plan(
        [
            "Py_Initialize",
            "Py_INCREF",
            "Py_DECREF",
            "Py_XINCREF",
            "Py_XDECREF",
            "Py_NewRef",
            "Py_XNewRef",
            "Py_CLEAR",
            "Py_SETREF",
            "Py_XSETREF",
            *GENERIC_REFCNT_MACRO_SYMBOLS,
            "PyMem_Malloc",
            "PyMem_Calloc",
            "PyMem_Realloc",
            "PyMem_Free",
            *GENERIC_MEMORY_OS_SYMBOLS,
            *GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS,
            *GENERIC_CALL_TYPE_FORMAT_SYMBOLS,
            *GENERIC_UNICODE_MACRO_SYMBOLS,
            *GENERIC_SCALAR_COMPLEX_SYMBOLS,
            *GENERIC_NUMBER_PROTOCOL_SYMBOLS,
            *GENERIC_ABSTRACT_PROTOCOL_SYMBOLS,
            "PyObject_Call",
            "PyObject_CallObject",
            "PyObject_CallFunctionObjArgs",
            "Py_BuildValue",
            "PyArg_ParseTupleAndKeywords",
            "PyTuple_New",
            *GENERIC_CONTAINER_MACRO_SYMBOLS,
            "PyTuple_SetItem",
            "PyTuple_Pack",
            "PyDict_New",
            "PyDict_SetItemString",
            "PyDict_DelItem",
            "PyDict_DelItemString",
            "PyDict_Size",
            "PyDict_Contains",
            "PyDict_ContainsString",
            "PyObject_GetAttrString",
            "PyObject_GetAttr",
            "PyObject_GetOptionalAttr",
            "PyObject_GetOptionalAttrString",
            "PyObject_SetAttrString",
            "PyObject_SetAttr",
            "PyObject_HasAttr",
            "PyObject_HasAttrString",
            "PyObject_HasAttrWithError",
            "PyObject_HasAttrStringWithError",
            "PyObject_Hash",
            "PyCallable_Check",
            "PyObject_Str",
            "PyObject_Repr",
            "PyObject_RichCompare",
            "PyObject_RichCompareBool",
            "PyModule_AddObjectRef",
            "PyModule_AddIntConstant",
            "PyModule_GetDict",
            "PyList_New",
            "PyList_Append",
            "PyList_Check",
            "PyTuple_Check",
            "PyDict_Check",
            "PyBytes_Check",
            "PyLong_FromUnsignedLong",
            "PyLong_FromUnsignedLongLong",
            "PyUnicode_Check",
            "PyUnicode_InternFromString",
            "PyUnicode_CompareWithASCIIString",
            "PySequence_Fast",
            "PySequence_Fast_ITEMS",
            "PySequence_Size",
            "PySequence_GetItem",
            "PyUnicode_FromStringAndSize",
            "PyBytes_FromString",
            "PyBytes_FromStringAndSize",
            "PyBytes_AsStringAndSize",
            "PyFloat_FromDouble",
            "PyBool_FromLong",
            "PyObject_IsTrue",
            "PyErr_Format",
            "PyErr_NoMemory",
            "PyErr_NewException",
            "PyImport_ImportModule",
            "Missing_Symbol",
        ],
        provider="array-api",
        expected_abi=3,
        actual_abi=2,
        require_capsule=True,
        require_buffer=True,
        require_memoryview=True,
    )
    assert plan["ok"] is False
    assert "PyCapsule_New" not in plan["missing_symbols"]
    assert "Py_INCREF" not in plan["missing_symbols"]
    assert "Py_DECREF" not in plan["missing_symbols"]
    assert "Py_XINCREF" not in plan["missing_symbols"]
    assert "Py_XDECREF" not in plan["missing_symbols"]
    assert "Py_NewRef" not in plan["missing_symbols"]
    assert "Py_XNewRef" not in plan["missing_symbols"]
    assert "Py_CLEAR" not in plan["missing_symbols"]
    assert "Py_SETREF" not in plan["missing_symbols"]
    assert "Py_XSETREF" not in plan["missing_symbols"]
    for symbol in GENERIC_REFCNT_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert "PyMem_Malloc" not in plan["missing_symbols"]
    assert "PyMem_Calloc" not in plan["missing_symbols"]
    assert "PyMem_Realloc" not in plan["missing_symbols"]
    assert "PyMem_Free" not in plan["missing_symbols"]
    for symbol in GENERIC_MEMORY_OS_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_SINGLETON_RETURN_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CALL_TYPE_FORMAT_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_UNICODE_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_SCALAR_COMPLEX_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_GIL_STATE_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_NUMBER_PROTOCOL_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_ABSTRACT_PROTOCOL_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CONTAINER_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CAPSULE_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert "PyObject_Call" not in plan["missing_symbols"]
    assert "PyObject_CallObject" not in plan["missing_symbols"]
    assert "PyObject_CallFunctionObjArgs" not in plan["missing_symbols"]
    assert "Py_BuildValue" not in plan["missing_symbols"]
    assert "PyArg_ParseTupleAndKeywords" not in plan["missing_symbols"]
    assert "PyObject_CheckBuffer" not in plan["missing_symbols"]
    assert "PyTuple_New" not in plan["missing_symbols"]
    assert "PyTuple_SetItem" not in plan["missing_symbols"]
    assert "PyTuple_Pack" not in plan["missing_symbols"]
    assert "PyDict_New" not in plan["missing_symbols"]
    assert "PyDict_SetItemString" not in plan["missing_symbols"]
    assert "PyDict_DelItem" not in plan["missing_symbols"]
    assert "PyDict_DelItemString" not in plan["missing_symbols"]
    assert "PyDict_Size" not in plan["missing_symbols"]
    assert "PyDict_Contains" not in plan["missing_symbols"]
    assert "PyDict_ContainsString" not in plan["missing_symbols"]
    assert "PyObject_GetAttrString" not in plan["missing_symbols"]
    assert "PyObject_GetAttr" not in plan["missing_symbols"]
    assert "PyObject_GetOptionalAttr" not in plan["missing_symbols"]
    assert "PyObject_GetOptionalAttrString" not in plan["missing_symbols"]
    assert "PyObject_SetAttrString" not in plan["missing_symbols"]
    assert "PyObject_SetAttr" not in plan["missing_symbols"]
    assert "PyObject_HasAttr" not in plan["missing_symbols"]
    assert "PyObject_HasAttrString" not in plan["missing_symbols"]
    assert "PyObject_HasAttrWithError" not in plan["missing_symbols"]
    assert "PyObject_HasAttrStringWithError" not in plan["missing_symbols"]
    assert "PyObject_Hash" not in plan["missing_symbols"]
    assert "PyCallable_Check" not in plan["missing_symbols"]
    assert "PyObject_Str" not in plan["missing_symbols"]
    assert "PyObject_Repr" not in plan["missing_symbols"]
    assert "PyObject_RichCompare" not in plan["missing_symbols"]
    assert "PyObject_RichCompareBool" not in plan["missing_symbols"]
    assert "PyModule_AddObjectRef" not in plan["missing_symbols"]
    assert "PyModule_AddIntConstant" not in plan["missing_symbols"]
    assert "PyModule_GetDict" not in plan["missing_symbols"]
    assert "PyList_New" not in plan["missing_symbols"]
    assert "PyList_Append" not in plan["missing_symbols"]
    assert "PyList_Check" not in plan["missing_symbols"]
    assert "PyTuple_Check" not in plan["missing_symbols"]
    assert "PyDict_Check" not in plan["missing_symbols"]
    assert "PyBytes_Check" not in plan["missing_symbols"]
    assert "PyLong_FromUnsignedLong" not in plan["missing_symbols"]
    assert "PyLong_FromUnsignedLongLong" not in plan["missing_symbols"]
    assert "PyUnicode_Check" not in plan["missing_symbols"]
    assert "PyUnicode_InternFromString" not in plan["missing_symbols"]
    assert "PyUnicode_CompareWithASCIIString" not in plan["missing_symbols"]
    assert "PySequence_Fast" not in plan["missing_symbols"]
    assert "PySequence_Fast_ITEMS" not in plan["missing_symbols"]
    assert "PySequence_Size" not in plan["missing_symbols"]
    assert "PySequence_GetItem" not in plan["missing_symbols"]
    assert "PyUnicode_FromStringAndSize" not in plan["missing_symbols"]
    assert "PyBytes_FromString" not in plan["missing_symbols"]
    assert "PyBytes_FromStringAndSize" not in plan["missing_symbols"]
    assert "PyBytes_AsStringAndSize" not in plan["missing_symbols"]
    assert "PyFloat_FromDouble" not in plan["missing_symbols"]
    assert "PyBool_FromLong" not in plan["missing_symbols"]
    assert "PyObject_IsTrue" not in plan["missing_symbols"]
    assert "PyErr_Format" not in plan["missing_symbols"]
    assert "PyErr_NoMemory" not in plan["missing_symbols"]
    assert "PyErr_NewException" not in plan["missing_symbols"]
    assert "PyImport_ImportModule" not in plan["missing_symbols"]
    assert "PyObject_GetBuffer" not in plan["missing_symbols"]
    assert "PyObject_CheckBuffer" not in plan["missing_symbols"]
    assert "PyBuffer_Release" not in plan["missing_symbols"]
    for symbol in GENERIC_MEMORYVIEW_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert plan["unknown_symbols"] == ["Missing_Symbol"]
    assert {diag["code"] for diag in plan["diagnostics"]} == {
        "PCC-EXT-UNKNOWN-CAPI-SYMBOL",
        "PCC-EXT-ABI-VERSION-MISMATCH",
    }


def test_extension_abi_plan_keeps_numpy_capi_as_explicit_missing_bucket():
    plan = extension_abi_plan(
        ["Py_Initialize"],
        provider="numpy-capi",
        require_numpy_capi=True,
    )
    assert plan["ok"] is False
    assert "PyArray_API" in plan["required_symbols"]
    assert "PyArray_API" in plan["missing_symbols"]
    assert "PyArray_Type" in plan["missing_symbols"]
    assert "PyArrayDescr_Type" in plan["missing_symbols"]
    assert "PyArray_SimpleNew" in plan["missing_symbols"]
    assert "PyArray_GETITEM" in plan["missing_symbols"]
    assert "PyArray_SIZE" in plan["missing_symbols"]
    assert "PyArray_Check" in plan["missing_symbols"]
    assert "PyArray_DIM" in plan["missing_symbols"]
    assert "PyUFunc_API" in plan["missing_symbols"]
    assert "PyUFunc_FromFuncAndData" in plan["missing_symbols"]
    assert plan["unknown_symbols"] == []
    assert "numpy/arrayobject.h" in plan["header_manifest"]["headers"]
    assert "numpy/ufuncobject.h" in plan["header_manifest"]["headers"]
    assert {diag["code"] for diag in plan["diagnostics"]} == {
        "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL",
    }
    numpy_diags = {
        diag["symbol"]: diag
        for diag in plan["diagnostics"]
        if diag["code"] == "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL"
    }
    assert numpy_diags["PyArray_API"]["capability"] == "numpy_capi"
    assert numpy_diags["PyArray_API"]["provider_shape"] == "array_api"
    assert numpy_diags["PyArray_API"]["failure_mode"] == "missing_capsule_provider"
    assert numpy_diags["PyArray_API"]["table"] == "_ARRAY_API"
    assert numpy_diags["PyArray_API"]["slot"] is None
    assert numpy_diags["PyArray_NDIM"]["slot"] == 6
    assert numpy_diags["PyArray_NDIM"]["failure_mode"] == "unsupported_stub"
    assert numpy_diags["PyArray_SIZE"]["slot"] == 13
    assert numpy_diags["PyArray_ITEMSIZE"]["slot"] == 14
    assert numpy_diags["PyArray_Check"]["slot"] == 15
    assert numpy_diags["PyArray_CheckExact"]["slot"] == 16
    assert numpy_diags["PyArray_DIM"]["slot"] == 7
    assert numpy_diags["PyArray_BYTES"]["slot"] == 9
    assert numpy_diags["PyUFunc_API"]["provider_shape"] == "ufunc_api"
    assert numpy_diags["PyUFunc_FromFuncAndData"]["table"] == "_UFUNC_API"
    assert numpy_diags["PyUFunc_FromFuncAndData"]["slot"] == 0
    status = {row["symbol"]: row for row in plan["numpy_capi_status"]}
    assert status["PyArray_Type"]["slot"] == 0
    assert status["PyArrayDescr_Type"]["slot"] == 1
    assert status["PyArray_SIZE"]["slot"] == 13
    assert status["PyArray_GETITEM"]["failure_mode"] == "unsupported_stub"


def test_extension_abi_plan_sees_fake_numpy_headers_without_claiming_capi():
    fake_include = Path("utils/fake_libc_include").resolve()
    assert (fake_include / "numpy/arrayobject.h").exists()
    assert (fake_include / "numpy/ufuncobject.h").exists()
    plan = extension_abi_plan(
        [],
        provider="numpy-capi",
        include_dir=str(fake_include),
        require_numpy_capi=True,
    )
    assert plan["ok"] is False
    assert "PyArray_API" in plan["missing_symbols"]
    assert "PyUFunc_API" in plan["missing_symbols"]
    assert "numpy/arrayobject.h" in plan["header_manifest"]["provided_headers"]
    assert "numpy/ufuncobject.h" in plan["header_manifest"]["provided_headers"]
    assert "numpy/arrayobject.h" not in plan["header_manifest"]["missing_headers"]
    assert "numpy/ufuncobject.h" not in plan["header_manifest"]["missing_headers"]


def test_extension_abi_plan_scans_package_supplied_headers(tmp_path):
    include = tmp_path / "include"
    include.mkdir()
    (include / "Python.h").write_text("int Py_Initialize(void);\n", encoding="utf-8")
    (include / "abstract.h").write_text(
        "int PyObject_GetBuffer(void);\n", encoding="utf-8"
    )

    ok_plan = extension_abi_plan(["Py_Initialize"], include_dir=str(include))
    assert ok_plan["ok"] is True
    assert ok_plan["header_manifest"]["provided_headers"] == ["Python.h"]
    assert ok_plan["header_manifest"]["missing_headers"] == []
    assert ok_plan["header_manifest"]["symbols"][0]["provided_by_package"] is True

    missing_header = extension_abi_plan(
        ["Py_Initialize"],
        include_dir=str(tmp_path / "missing"),
    )
    assert missing_header["ok"] is False
    assert missing_header["header_manifest"]["missing_headers"] == ["Python.h"]
    assert missing_header["diagnostics"][0]["code"] == "PCC-EXT-MISSING-CAPI-HEADER"


def test_pcc_package_ext_abi_cli_is_json_ready():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--require-buffer",
            "--provider",
            "array-api",
            "--expected-abi",
            "1",
            "--actual-abi",
            "2",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 2
    plan = json.loads(proc.stdout)
    assert plan["provider"] == "array-api"
    assert plan["abi_version"]["code"] == "PCC-EXT-ABI-VERSION-MISMATCH"
    assert "abstract.h" in plan["header_manifest"]["headers"]
    assert "PyObject_GetBuffer" not in plan["missing_symbols"]
    assert "PyObject_CheckBuffer" not in plan["missing_symbols"]
    assert "PyBuffer_Release" not in plan["missing_symbols"]


def test_pcc_package_ext_abi_cli_reports_numpy_capi_bucket():
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "ext-abi",
            "--require-numpy-capi",
            "--provider",
            "numpy-capi",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 2
    plan = json.loads(proc.stdout)
    assert plan["provider"] == "numpy-capi"
    assert "PyArray_API" in plan["missing_symbols"]
    assert "PyArrayDescr_Type" in plan["missing_symbols"]
    assert "PyArray_GETITEM" in plan["missing_symbols"]
    assert "PyArray_SIZE" in plan["missing_symbols"]
    assert "PyArray_CheckExact" in plan["missing_symbols"]
    assert "PyUFunc_API" in plan["missing_symbols"]
    assert "PyUFunc_FromFuncAndData" in plan["missing_symbols"]
    assert plan["unknown_symbols"] == []
    assert {diag["code"] for diag in plan["diagnostics"]} == {
        "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL"
    }
    numpy_diags = {
        diag["symbol"]: diag
        for diag in plan["diagnostics"]
        if diag["code"] == "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL"
    }
    assert numpy_diags["PyArray_API"]["provider_shape"] == "array_api"
    assert numpy_diags["PyArray_NDIM"]["slot"] == 6
    assert numpy_diags["PyArray_SIZE"]["slot"] == 13
    assert numpy_diags["PyArray_Check"]["slot"] == 15
    assert numpy_diags["PyArray_NDIM"]["failure_mode"] == "unsupported_stub"
    assert numpy_diags["PyUFunc_API"]["provider_shape"] == "ufunc_api"
    status = {row["symbol"]: row for row in plan["numpy_capi_status"]}
    assert status["PyArray_DATA"]["table"] == "_ARRAY_API"
    assert status["PyArray_BYTES"]["slot"] == 9
    assert status["PyUFunc_FromFuncAndData"]["slot"] == 0


def test_pcc_package_ext_abi_cli_reports_supplied_headers(tmp_path):
    include = tmp_path / "include"
    include.mkdir()
    (include / "Python.h").write_text("int Py_Initialize(void);\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--include-dir",
            str(include),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["header_manifest"]["provided_headers"] == ["Python.h"]


def test_pcc1_ext_abi_does_not_need_host_python():
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native ext-abi shim")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--symbol",
            "Py_INCREF",
            "--symbol",
            "Py_DECREF",
            "--symbol",
            "Py_XINCREF",
            "--symbol",
            "Py_XDECREF",
            "--symbol",
            "Py_NewRef",
            "--symbol",
            "Py_XNewRef",
            "--symbol",
            "Py_CLEAR",
            "--symbol",
            "Py_SETREF",
            "--symbol",
            "Py_XSETREF",
            *[
                part
                for symbol in GENERIC_REFCNT_MACRO_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            "--symbol",
            "PyMem_Malloc",
            "--symbol",
            "PyMem_Calloc",
            "--symbol",
            "PyMem_Realloc",
            "--symbol",
            "PyMem_Free",
            *[
                part
                for symbol in GENERIC_MEMORY_OS_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_SINGLETON_RETURN_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_CALL_TYPE_FORMAT_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_UNICODE_MACRO_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_SCALAR_COMPLEX_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_GIL_STATE_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_NUMBER_PROTOCOL_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_ABSTRACT_PROTOCOL_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_CONTAINER_MACRO_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            *[
                part
                for symbol in GENERIC_CAPSULE_SYMBOLS
                for part in ("--symbol", symbol)
            ],
            "--symbol",
            "PyObject_Call",
            "--symbol",
            "PyObject_CallObject",
            "--symbol",
            "PyObject_CallFunctionObjArgs",
            "--require-capsule",
            "--symbol",
            "Py_BuildValue",
            "--symbol",
            "PyArg_ParseTupleAndKeywords",
            "--symbol",
            "PyTuple_New",
            "--symbol",
            "PyTuple_SetItem",
            "--symbol",
            "PyTuple_Pack",
            "--symbol",
            "PyDict_SetItemString",
            "--symbol",
            "PyDict_DelItem",
            "--symbol",
            "PyDict_DelItemString",
            "--symbol",
            "PyDict_Size",
            "--symbol",
            "PyDict_Contains",
            "--symbol",
            "PyDict_ContainsString",
            "--symbol",
            "PyObject_GetAttrString",
            "--symbol",
            "PyObject_GetAttr",
            "--symbol",
            "PyObject_GetOptionalAttr",
            "--symbol",
            "PyObject_GetOptionalAttrString",
            "--symbol",
            "PyObject_SetAttrString",
            "--symbol",
            "PyObject_SetAttr",
            "--symbol",
            "PyObject_HasAttr",
            "--symbol",
            "PyObject_HasAttrString",
            "--symbol",
            "PyObject_HasAttrWithError",
            "--symbol",
            "PyObject_HasAttrStringWithError",
            "--symbol",
            "PyObject_Hash",
            "--symbol",
            "PyCallable_Check",
            "--symbol",
            "PyObject_Str",
            "--symbol",
            "PyObject_Repr",
            "--symbol",
            "PyObject_RichCompare",
            "--symbol",
            "PyObject_RichCompareBool",
            "--symbol",
            "PyModule_AddObjectRef",
            "--symbol",
            "PyModule_AddIntConstant",
            "--symbol",
            "PyModule_GetDict",
            "--symbol",
            "PyList_New",
            "--symbol",
            "PyList_Append",
            "--symbol",
            "PyList_Check",
            "--symbol",
            "PyTuple_Check",
            "--symbol",
            "PyDict_Check",
            "--symbol",
            "PyBytes_Check",
            "--symbol",
            "PyLong_FromUnsignedLong",
            "--symbol",
            "PyLong_FromUnsignedLongLong",
            "--symbol",
            "PyUnicode_Check",
            "--symbol",
            "PyUnicode_InternFromString",
            "--symbol",
            "PyUnicode_CompareWithASCIIString",
            "--symbol",
            "PySequence_Fast",
            "--symbol",
            "PySequence_Fast_ITEMS",
            "--symbol",
            "PySequence_Size",
            "--symbol",
            "PySequence_GetItem",
            "--symbol",
            "PyUnicode_FromStringAndSize",
            "--symbol",
            "PyBytes_FromString",
            "--symbol",
            "PyBytes_FromStringAndSize",
            "--symbol",
            "PyBytes_AsStringAndSize",
            "--symbol",
            "PyFloat_FromDouble",
            "--symbol",
            "PyBool_FromLong",
            "--symbol",
            "PyObject_IsTrue",
            "--symbol",
            "PyErr_Format",
            "--symbol",
            "PyErr_NoMemory",
            "--symbol",
            "PyErr_NewException",
            "--symbol",
            "PyImport_ImportModule",
            "--require-buffer",
            "--require-memoryview",
            "--require-numpy-capi",
            "--provider",
            "array-api",
            "--expected-abi",
            "3",
            "--actual-abi",
            "2",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    plan = json.loads(proc.stdout)
    assert plan["provider"] == "array-api"
    assert "Py_INCREF" not in plan["missing_symbols"]
    assert "Py_DECREF" not in plan["missing_symbols"]
    assert "Py_XINCREF" not in plan["missing_symbols"]
    assert "Py_XDECREF" not in plan["missing_symbols"]
    assert "Py_NewRef" not in plan["missing_symbols"]
    assert "Py_XNewRef" not in plan["missing_symbols"]
    assert "Py_CLEAR" not in plan["missing_symbols"]
    assert "Py_SETREF" not in plan["missing_symbols"]
    assert "Py_XSETREF" not in plan["missing_symbols"]
    for symbol in GENERIC_REFCNT_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert "PyMem_Malloc" not in plan["missing_symbols"]
    assert "PyMem_Calloc" not in plan["missing_symbols"]
    assert "PyMem_Realloc" not in plan["missing_symbols"]
    assert "PyMem_Free" not in plan["missing_symbols"]
    for symbol in GENERIC_MEMORY_OS_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_SINGLETON_RETURN_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_MAPPING_LONG_EXCEPTION_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CALL_TYPE_FORMAT_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_UNICODE_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_SCALAR_COMPLEX_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_GIL_STATE_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_NUMBER_PROTOCOL_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_ABSTRACT_PROTOCOL_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CONTAINER_MACRO_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    for symbol in GENERIC_CAPSULE_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert "PyObject_Call" not in plan["missing_symbols"]
    assert "PyObject_CallObject" not in plan["missing_symbols"]
    assert "PyObject_CallFunctionObjArgs" not in plan["missing_symbols"]
    assert "Py_BuildValue" not in plan["missing_symbols"]
    assert "PyArg_ParseTupleAndKeywords" not in plan["missing_symbols"]
    assert "PyTuple_New" not in plan["missing_symbols"]
    assert "PyTuple_SetItem" not in plan["missing_symbols"]
    assert "PyTuple_Pack" not in plan["missing_symbols"]
    assert "PyDict_SetItemString" not in plan["missing_symbols"]
    assert "PyDict_DelItem" not in plan["missing_symbols"]
    assert "PyDict_DelItemString" not in plan["missing_symbols"]
    assert "PyDict_Size" not in plan["missing_symbols"]
    assert "PyDict_Contains" not in plan["missing_symbols"]
    assert "PyDict_ContainsString" not in plan["missing_symbols"]
    assert "PyObject_GetAttrString" not in plan["missing_symbols"]
    assert "PyObject_GetAttr" not in plan["missing_symbols"]
    assert "PyObject_GetOptionalAttr" not in plan["missing_symbols"]
    assert "PyObject_GetOptionalAttrString" not in plan["missing_symbols"]
    assert "PyObject_SetAttrString" not in plan["missing_symbols"]
    assert "PyObject_SetAttr" not in plan["missing_symbols"]
    assert "PyObject_HasAttr" not in plan["missing_symbols"]
    assert "PyObject_HasAttrString" not in plan["missing_symbols"]
    assert "PyObject_HasAttrWithError" not in plan["missing_symbols"]
    assert "PyObject_HasAttrStringWithError" not in plan["missing_symbols"]
    assert "PyObject_Hash" not in plan["missing_symbols"]
    assert "PyCallable_Check" not in plan["missing_symbols"]
    assert "PyObject_Str" not in plan["missing_symbols"]
    assert "PyObject_Repr" not in plan["missing_symbols"]
    assert "PyObject_RichCompare" not in plan["missing_symbols"]
    assert "PyObject_RichCompareBool" not in plan["missing_symbols"]
    assert "PyModule_AddObjectRef" not in plan["missing_symbols"]
    assert "PyModule_AddIntConstant" not in plan["missing_symbols"]
    assert "PyModule_GetDict" not in plan["missing_symbols"]
    assert "PyList_New" not in plan["missing_symbols"]
    assert "PyList_Append" not in plan["missing_symbols"]
    assert "PyList_Check" not in plan["missing_symbols"]
    assert "PyTuple_Check" not in plan["missing_symbols"]
    assert "PyDict_Check" not in plan["missing_symbols"]
    assert "PyBytes_Check" not in plan["missing_symbols"]
    assert "PyLong_FromUnsignedLong" not in plan["missing_symbols"]
    assert "PyLong_FromUnsignedLongLong" not in plan["missing_symbols"]
    assert "PyUnicode_Check" not in plan["missing_symbols"]
    assert "PyUnicode_InternFromString" not in plan["missing_symbols"]
    assert "PyUnicode_CompareWithASCIIString" not in plan["missing_symbols"]
    assert "PySequence_Fast" not in plan["missing_symbols"]
    assert "PySequence_Fast_ITEMS" not in plan["missing_symbols"]
    assert "PySequence_Size" not in plan["missing_symbols"]
    assert "PySequence_GetItem" not in plan["missing_symbols"]
    assert "PyUnicode_FromStringAndSize" not in plan["missing_symbols"]
    assert "PyBytes_FromString" not in plan["missing_symbols"]
    assert "PyBytes_FromStringAndSize" not in plan["missing_symbols"]
    assert "PyBytes_AsStringAndSize" not in plan["missing_symbols"]
    assert "PyFloat_FromDouble" not in plan["missing_symbols"]
    assert "PyBool_FromLong" not in plan["missing_symbols"]
    assert "PyObject_IsTrue" not in plan["missing_symbols"]
    assert "PyErr_Format" not in plan["missing_symbols"]
    assert "PyErr_NoMemory" not in plan["missing_symbols"]
    assert "PyErr_NewException" not in plan["missing_symbols"]
    assert "PyImport_ImportModule" not in plan["missing_symbols"]
    assert "PyObject_GetBuffer" not in plan["missing_symbols"]
    assert "PyObject_CheckBuffer" not in plan["missing_symbols"]
    assert "PyBuffer_Release" not in plan["missing_symbols"]
    for symbol in GENERIC_MEMORYVIEW_SYMBOLS:
        assert symbol not in plan["missing_symbols"]
    assert "PyArray_API" in plan["missing_symbols"]
    assert "PyArrayDescr_Type" in plan["missing_symbols"]
    assert "PyArray_GETITEM" in plan["missing_symbols"]
    assert "PyArray_SIZE" in plan["missing_symbols"]
    assert "PyArray_Check" in plan["missing_symbols"]
    assert "PyUFunc_API" in plan["missing_symbols"]
    assert "PyUFunc_FromFuncAndData" in plan["missing_symbols"]
    assert plan["unknown_symbols"] == []
    assert {diag["code"] for diag in plan["diagnostics"]} >= {
        "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL",
    }
    numpy_diags = {
        diag["symbol"]: diag
        for diag in plan["diagnostics"]
        if diag["code"] == "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL"
    }
    assert numpy_diags["PyArray_API"]["provider_shape"] == "array_api"
    assert numpy_diags["PyArray_API"]["failure_mode"] == "missing_capsule_provider"
    assert numpy_diags["PyArray_NDIM"]["slot"] == 6
    assert numpy_diags["PyArray_SIZE"]["slot"] == 13
    assert numpy_diags["PyArray_CheckExact"]["slot"] == 16
    assert numpy_diags["PyUFunc_API"]["provider_shape"] == "ufunc_api"
    status = {row["symbol"]: row for row in plan["numpy_capi_status"]}
    assert status["PyArray_SETITEM"]["slot"] == 12
    assert status["PyArray_ITEMSIZE"]["slot"] == 14
    assert status["PyUFunc_FromFuncAndData"]["failure_mode"] == "unsupported_stub"
    assert plan["abi_version"]["code"] == "PCC-EXT-ABI-VERSION-MISMATCH"


def test_pcc1_ext_abi_scans_include_dir_without_host_python(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native ext-abi shim")
    include = tmp_path / "include"
    include.mkdir()
    (include / "Python.h").write_text("int Py_Initialize(void);\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--include-dir",
            str(include),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["header_manifest"]["provided_headers"] == ["Python.h"]


def test_pcc1_ext_abi_no_host_reports_generic_plan_ok():
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native ext-abi shim")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "ext-abi",
            "--symbol",
            "Py_Initialize",
            "--symbol",
            "PyObject_GetAttrString",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    plan = json.loads(proc.stdout)
    assert plan["ok"] is True
    assert plan["missing_symbols"] == []
    assert plan["unknown_symbols"] == []
    assert plan["provider"] == "extension"


def test_pcc1_ext_abi_no_host_requires_numpy_capi_diagnostics():
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native ext-abi shim")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "ext-abi",
            "--require-numpy-capi",
            "--provider",
            "numpy-capi",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    plan = json.loads(proc.stdout)
    assert plan["provider"] == "numpy-capi"
    assert "PyArray_API" in plan["missing_symbols"]
    assert "PyArrayDescr_Type" in plan["missing_symbols"]
    assert "PyArray_CheckExact" in plan["missing_symbols"]
    assert "PyUFunc_FromFuncAndData" in plan["missing_symbols"]
    assert {diag["code"] for diag in plan["diagnostics"]} == {
        "PCC-EXT-MISSING-NUMPY-CAPI-SYMBOL",
    }
