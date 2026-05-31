from __future__ import annotations

from pcc.capi_surface import (
    CApiPriority,
    abi_version_diagnostic,
    default_capi_symbols,
    missing_symbols,
    symbol_report,
)


def test_capi_surface_prioritizes_extension_import_blockers():
    symbols = default_capi_symbols()
    assert any(sym.name == "PyModule_Create" for sym in symbols)
    missing = missing_symbols(CApiPriority.IMPORT_BLOCKER)
    assert [sym.name for sym in missing] == []


def test_capi_report_is_json_ready():
    report = symbol_report()
    assert "symbols" in report
    assert report["missing_import_blockers"] == []
    assert "PyObject_Call" not in report["missing_array_core"]
    assert "PyObject_GetBuffer" not in report["missing_array_core"]
    assert "PyObject_CheckBuffer" not in report["missing_array_core"]
    assert "PyBuffer_Release" not in report["missing_array_core"]
    assert "PyCapsule_New" not in report["missing_array_core"]
    assert "PyCapsule_GetPointer" not in report["missing_array_core"]
    assert "PyCapsule_GetName" not in report["missing_array_core"]
    assert "PyCapsule_GetContext" not in report["missing_array_core"]
    assert "PyCapsule_IsValid" not in report["missing_array_core"]
    assert "PyCapsule_CheckExact" not in report["missing_array_core"]
    assert "PyCapsule_SetContext" not in report["missing_array_core"]
    assert "PyCapsule_SetName" not in report["missing_array_core"]
    assert "PyCapsule_Import" not in report["missing_array_core"]
    assert "PyMemoryView_FromObject" not in report["missing_array_core"]
    assert "PyMemoryView_FromMemory" not in report["missing_array_core"]
    assert "PyMemoryView_Check" not in report["missing_array_core"]
    assert "PyMemoryView_GET_BUFFER" not in report["missing_array_core"]
    assert "PyMemoryView_GET_BASE" not in report["missing_array_core"]
    assert report["missing_by_priority"]["array_core"] == []
    assert "PyArray_API" in report["missing_numpy_capi"]
    assert "PyArray_SIZE" in report["missing_numpy_capi"]
    assert "PyArray_Check" in report["missing_numpy_capi"]
    assert "PyUFunc_API" in report["missing_numpy_capi"]
    assert "PyArray_API" in report["missing_by_priority"]["numpy_capi"]
    numpy_status = {row["symbol"]: row for row in report["numpy_capi_status"]}
    assert numpy_status["PyArray_API"]["failure_mode"] == "missing_capsule_provider"
    assert numpy_status["PyArray_Type"]["slot"] == 0
    assert numpy_status["PyArray_NDIM"]["slot"] == 6
    assert numpy_status["PyArray_SIZE"]["slot"] == 13
    assert numpy_status["PyArray_ITEMSIZE"]["slot"] == 14
    assert numpy_status["PyArray_Check"]["slot"] == 15
    assert numpy_status["PyArray_CheckExact"]["slot"] == 16
    assert numpy_status["PyArray_DIM"]["slot"] == 7
    assert numpy_status["PyArray_BYTES"]["slot"] == 9
    assert numpy_status["PyArray_GETITEM"]["failure_mode"] == "unsupported_stub"
    assert numpy_status["PyUFunc_API"]["table"] == "_UFUNC_API"
    assert numpy_status["PyUFunc_FromFuncAndData"]["slot"] == 0
    assert "Py_INCREF" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_XDECREF" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_NewRef" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_CLEAR" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_REFCNT" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_SET_REFCNT" in report["implemented_by_priority"]["runtime_core"]
    for symbol in [
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
    ]:
        assert symbol in report["implemented_by_priority"]["runtime_core"]
    assert "PyMem_Malloc" in report["implemented_by_priority"]["runtime_core"]
    assert "PyMem_Realloc" in report["implemented_by_priority"]["runtime_core"]
    for symbol in [
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
        "Py_UNUSED",
        "PyLong_FromSsize_t",
        "PyLong_FromSize_t",
        "PyLong_FromInt32",
        "PyLong_FromInt64",
        "PyLong_FromUInt32",
        "PyLong_FromUInt64",
        "PyLong_FromVoidPtr",
        "PyLong_FromDouble",
        "PyLong_AsLongAndOverflow",
        "PyLong_AsUnsignedLong",
        "PyLong_AsUnsignedLongLong",
        "PyLong_AsUnsignedLongLongMask",
        "PyLong_AsSsize_t",
        "PyLong_AsSize_t",
        "PyLong_AsInt",
        "PyLong_AsInt32",
        "PyLong_AsInt64",
        "PyLong_AsUInt32",
        "PyLong_AsUInt64",
        "PyLong_AsVoidPtr",
        "PyLong_AsDouble",
        "PyLong_Check",
        "PyLong_CheckExact",
        "PyObject_GetItem",
        "PyObject_SetItem",
        "PyObject_DelItem",
        "PyDict_GetItemWithError",
        "PyDict_GetItemRef",
        "PyDict_GetItemStringRef",
        "PyDict_Pop",
        "PyDict_PopString",
        "PyDict_DelItem",
        "PyDict_DelItemString",
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
        "PyErr_BadInternalCall",
        "PyErr_SetFromErrno",
        "PyErr_SetFromErrnoWithFilenameObject",
        "PyErr_GivenExceptionMatches",
        "PyErr_ExceptionMatches",
        "PyErr_Print",
        "PyErr_CheckSignals",
        "PyErr_Fetch",
        "PyErr_Restore",
        "PyExc_BaseException",
        "PyExc_Exception",
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
        "PyExc_Warning",
        "PyExc_UserWarning",
        "PyExc_RuntimeWarning",
        "PyExc_DeprecationWarning",
        "PyExc_FutureWarning",
        "PyUnicode_FromFormat",
        "PyUnicode_FromFormatV",
        "PyUnicode_FromKindAndData",
        "PyUnicode_FromOrdinal",
        "PyUnicode_AsUCS4",
        "PyUnicode_AsUCS4Copy",
        "PyUnicode_FromEncodedObject",
        "PyUnicode_GetLength",
        "PyUnicode_GET_LENGTH",
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
        "Py_UCS1",
        "Py_UCS2",
        "Py_UCS4",
        "PyUnicode_1BYTE_KIND",
        "PyUnicode_2BYTE_KIND",
        "PyUnicode_4BYTE_KIND",
        "Py_UNICODE_ISSPACE",
        "Py_UNICODE_ISDIGIT",
        "Py_UNICODE_ISDECIMAL",
        "Py_UNICODE_ISNUMERIC",
        "Py_UNICODE_ISLOWER",
        "Py_UNICODE_ISUPPER",
        "Py_UNICODE_ISTITLE",
        "Py_UNICODE_ISALPHA",
        "Py_UNICODE_ISALNUM",
        "PyUnicode_EqualToUTF8",
        "PyUnicode_EqualToUTF8AndSize",
        "PyErr_FormatV",
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
        "PyModule_Add",
        "PyList_GetItemRef",
        "PyList_AsTuple",
        "PyDict_SetDefaultRef",
        "PyObject_CallNoArgs",
        "PyObject_CallOneArg",
        "PyObject_Vectorcall",
        "PyObject_VectorcallMethod",
        "PyObject_CallFunction",
        "PyObject_CallMethod",
        "PyObject_CallMethodNoArgs",
        "PyObject_CallMethodOneArg",
        "PyObject_Type",
        "PyObject_IsInstance",
        "PyUnicode_AsUTF8AndSize",
        "PyBytes_AS_STRING",
        "PyBytes_GET_SIZE",
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
    ]:
        assert symbol in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Call" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_CallObject" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyObject_CallFunctionObjArgs"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "Py_BuildValue" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyArg_ParseTupleAndKeywords"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyTuple_New" in report["implemented_by_priority"]["runtime_core"]
    assert "PyTuple_SetItem" in report["implemented_by_priority"]["runtime_core"]
    assert "PyDict_SetItemString" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_GetAttrString" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_GetAttr" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyObject_GetOptionalAttr" in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyObject_GetOptionalAttrString"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyObject_SetAttr" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_HasAttr" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_HasAttrString" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyObject_HasAttrWithError" in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyObject_HasAttrStringWithError"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyObject_Hash" in report["implemented_by_priority"]["runtime_core"]
    assert "PyCallable_Check" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Str" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Repr" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Bytes" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Format" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_PRINT_RAW" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_Print" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_RichCompare" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyObject_RichCompareBool" in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyModule_AddIntConstant" in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyModule_AddObjectRef" in report["implemented_by_priority"]["runtime_core"]
    assert "PyModule_GetDict" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_New" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_Append" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_Check" in report["implemented_by_priority"]["runtime_core"]
    assert "PyTuple_Check" in report["implemented_by_priority"]["runtime_core"]
    assert "PyDict_Check" in report["implemented_by_priority"]["runtime_core"]
    assert "PyBytes_Check" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyLong_FromUnsignedLong" in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyLong_FromUnsignedLongLong"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyUnicode_Check" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyUnicode_InternFromString"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyUnicode_CompareWithASCIIString"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PySequence_Fast" in report["implemented_by_priority"]["runtime_core"]
    assert "PySequence_Fast_ITEMS" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PySequence_Fast_GET_ITEM" in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyTuple_GET_ITEM" in report["implemented_by_priority"]["runtime_core"]
    assert "PyTuple_GET_SIZE" in report["implemented_by_priority"]["runtime_core"]
    assert "PyTuple_SET_ITEM" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_GET_ITEM" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_GET_SIZE" in report["implemented_by_priority"]["runtime_core"]
    assert "PyList_SET_ITEM" in report["implemented_by_priority"]["runtime_core"]
    assert "PySequence_Size" in report["implemented_by_priority"]["runtime_core"]
    assert "PySequence_GetItem" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyUnicode_FromStringAndSize"
        in report["implemented_by_priority"]["runtime_core"]
    )
    assert (
        "PyBytes_FromStringAndSize" in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyBytes_FromString" in report["implemented_by_priority"]["runtime_core"]
    assert (
        "PyBytes_AsStringAndSize" in report["implemented_by_priority"]["runtime_core"]
    )
    assert "PyFloat_FromDouble" in report["implemented_by_priority"]["runtime_core"]
    assert "PyBool_FromLong" in report["implemented_by_priority"]["runtime_core"]
    assert "PyObject_IsTrue" in report["implemented_by_priority"]["runtime_core"]
    assert "PyErr_Format" in report["implemented_by_priority"]["runtime_core"]
    assert "PyErr_NoMemory" in report["implemented_by_priority"]["runtime_core"]
    assert "PyErr_NewException" in report["implemented_by_priority"]["runtime_core"]
    assert "Py_IsInitialized" in report["implemented_by_priority"]["runtime_core"]
    assert "PyGILState_Ensure" in report["implemented_by_priority"]["runtime_core"]
    assert "PyGILState_Release" in report["implemented_by_priority"]["runtime_core"]
    assert "PyGILState_Check" in report["implemented_by_priority"]["runtime_core"]
    for symbol in [
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
    ]:
        assert symbol in report["implemented_by_priority"]["runtime_core"]
    assert "PyImport_ImportModule" in report["implemented_by_priority"]["runtime_core"]


def test_numpy_capi_is_tracked_but_not_folded_into_array_core():
    assert missing_symbols(CApiPriority.ARRAY_CORE) == []
    missing_numpy = [sym.name for sym in missing_symbols(CApiPriority.NUMPY_CAPI)]
    assert "PyArray_API" in missing_numpy
    assert "PyArray_Type" in missing_numpy
    assert "PyUFunc_API" in missing_numpy


def test_abi_version_diagnostic_is_actionable():
    ok = abi_version_diagnostic(provider="array-api", expected=1, actual=1)
    assert ok["ok"] is True
    bad = abi_version_diagnostic(provider="array-api", expected=3, actual=2)
    assert bad["ok"] is False
    assert bad["code"] == "PCC-EXT-ABI-VERSION-MISMATCH"
    assert "expected 3" in bad["message"]
