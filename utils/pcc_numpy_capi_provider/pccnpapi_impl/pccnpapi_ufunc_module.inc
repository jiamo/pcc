/* ufunc call adapter, API tables, and module initialization.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static PyObject *pccnp_ensure_array(PyObject *op) {
    if (op == NULL) {
        return NULL;
    }
    if (pccnp_is_array_object(op)) {
        Py_INCREF(op);
        Py_DECREF(op);
        return op;
    }
    PyObject *array_obj = pccnp_from_any(
        op,
        NULL,
        0,
        0,
        NPY_ARRAY_ENSUREARRAY,
        NULL
    );
    Py_DECREF(op);
    return array_obj;
}

static PyObject *pccnp_ensure_any_array(PyObject *op) {
    if (op != NULL && pccnp_is_array_object(op)) {
        return op;
    }
    return pccnp_ensure_array(op);
}

static int pccnp_compute_broadcast_shape(
    PccNpArray *arrays[2],
    int is_array[2],
    int *out_nd,
    npy_intp **out_dims
) {
    int max_nd = 0;
    *out_nd = 0;
    *out_dims = NULL;
    for (int i = 0; i < 2; i++) {
        if (is_array[i] && arrays[i] != NULL && arrays[i]->nd > max_nd) {
            max_nd = arrays[i]->nd;
        }
    }
    if (max_nd == 0) return 0;
    npy_intp *dims = (npy_intp *)PyMem_Calloc((size_t)max_nd, sizeof(npy_intp));
    if (dims == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    for (int axis = max_nd - 1; axis >= 0; axis--) {
        npy_intp chosen = 1;
        for (int i = 0; i < 2; i++) {
            npy_intp dim = 1;
            if (is_array[i] && arrays[i] != NULL) {
                int source_axis = axis - (max_nd - arrays[i]->nd);
                if (source_axis >= 0) dim = arrays[i]->dims[source_axis];
            }
            if (dim < 0) {
                PyMem_Free(dims);
                PyErr_SetString(PyExc_ValueError, "negative array dimension");
                return -1;
            }
            if (chosen == 1) {
                chosen = dim;
            } else if (dim != 1 && dim != chosen) {
                PyMem_Free(dims);
                PyErr_SetString(PyExc_ValueError, "minimal ufunc operand shapes are not broadcast-compatible");
                return -1;
            }
        }
        dims[axis] = chosen;
    }
    *out_nd = max_nd;
    *out_dims = dims;
    return 0;
}

static char *pccnp_broadcast_data_ptr(
    PccNpArray *array,
    int is_array,
    const npy_intp *out_dims,
    int out_nd,
    size_t flat_index
) {
    if (array == NULL) return NULL;
    if (!is_array || out_nd == 0) return (char *)array->data;
    size_t remaining = flat_index;
    size_t offset = 0;
    for (int axis = out_nd - 1; axis >= 0; axis--) {
        npy_intp out_dim = out_dims[axis];
        npy_intp coord = 0;
        if (out_dim > 0) {
            coord = (npy_intp)(remaining % (size_t)out_dim);
            remaining /= (size_t)out_dim;
        }
        int source_axis = axis - (out_nd - array->nd);
        if (source_axis >= 0 && array->dims[source_axis] != 1) {
            offset += (size_t)coord * (size_t)array->strides[source_axis];
        }
    }
    return (char *)array->data + offset;
}

static int pccnp_type_is_integer(int typenum) {
    switch (typenum) {
        case NPY_BOOL:
        case NPY_BYTE:
        case NPY_UBYTE:
        case NPY_SHORT:
        case NPY_USHORT:
        case NPY_INT:
        case NPY_UINT:
        case NPY_LONG:
        case NPY_ULONG:
        case NPY_LONGLONG:
        case NPY_ULONGLONG:
            return 1;
        default:
            return 0;
    }
}

static int pccnp_type_is_float(int typenum) {
    return typenum == NPY_FLOAT || typenum == NPY_DOUBLE ||
        typenum == NPY_LONGDOUBLE;
}

static int pccnp_type_is_complex(int typenum) {
    return typenum == NPY_CFLOAT || typenum == NPY_CDOUBLE ||
        typenum == NPY_CLONGDOUBLE;
}

static int pccnp_array_matches_type(PccNpArray *array, int typenum) {
    if (array == NULL) return 0;
    if (array->typenum == typenum) return 1;
    if (pccnp_itemsize(typenum) == 0) return 0;
    if (pccnp_type_is_integer(array->typenum)) {
        return pccnp_type_is_float(typenum) || pccnp_type_is_complex(typenum);
    }
    if (pccnp_type_is_float(array->typenum)) {
        return pccnp_type_is_float(typenum) || pccnp_type_is_complex(typenum);
    }
    return 0;
}

static int pccnp_scalar_matches_type(PyObject *obj, int typenum) {
    int inferred = pccnp_scalar_inferred_type(obj);
    if (inferred == PCCNP_INFER_NEGATIVE_LONG) return typenum == NPY_LONG;
    if (inferred == PCCNP_INFER_CDOUBLE) return typenum == NPY_CDOUBLE;
    if (inferred == PCCNP_INFER_STRING) return typenum == NPY_STRING;
    if (inferred == typenum) return 1;
    if (PyBool_Check(obj) || PyLong_Check(obj)) {
        return typenum == NPY_FLOAT || typenum == NPY_DOUBLE ||
            typenum == NPY_LONGDOUBLE || typenum == NPY_CFLOAT ||
            typenum == NPY_CDOUBLE || typenum == NPY_CLONGDOUBLE;
    }
    if (PyFloat_Check(obj)) {
        return typenum == NPY_CFLOAT || typenum == NPY_CDOUBLE ||
            typenum == NPY_CLONGDOUBLE;
    }
    return 0;
}

static int pccnp_reject_null_data(void *data) {
    if (data != NULL) return 0;
    PyErr_SetString(PyExc_ValueError, "array item data pointer is NULL");
    return -1;
}

static PyObject *pccnp_getitem(PyArrayObject *arr, void *data) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL || pccnp_reject_null_data(data) != 0) return NULL;
    switch (array->typenum) {
        case NPY_BOOL:
            return PyLong_FromLong(*(unsigned char *)data != 0);
        case NPY_BYTE:
            return PyLong_FromLong(*(signed char *)data);
        case NPY_UBYTE:
            return PyLong_FromUnsignedLong(*(unsigned char *)data);
        case NPY_SHORT:
            return PyLong_FromLong(*(short *)data);
        case NPY_USHORT:
            return PyLong_FromUnsignedLong(*(unsigned short *)data);
        case NPY_INT:
            return PyLong_FromLong(*(int *)data);
        case NPY_UINT:
            return PyLong_FromUnsignedLong(*(unsigned int *)data);
        case NPY_LONG:
            return PyLong_FromLong(*(long *)data);
        case NPY_ULONG:
            return PyLong_FromUnsignedLong(*(unsigned long *)data);
        case NPY_LONGLONG:
            return PyLong_FromLongLong(*(long long *)data);
        case NPY_ULONGLONG:
            return PyLong_FromUnsignedLongLong(*(unsigned long long *)data);
        case NPY_FLOAT:
            return PyFloat_FromDouble((double)*(float *)data);
        case NPY_DOUBLE:
            return PyFloat_FromDouble(*(double *)data);
        case NPY_LONGDOUBLE:
            return PyFloat_FromDouble((double)*(long double *)data);
        case NPY_CFLOAT: {
            npy_cfloat value = *(npy_cfloat *)data;
            return PyComplex_FromDoubles((double)value.real, (double)value.imag);
        }
        case NPY_CDOUBLE: {
            npy_cdouble value = *(npy_cdouble *)data;
            return PyComplex_FromDoubles(value.real, value.imag);
        }
        case NPY_CLONGDOUBLE: {
            npy_clongdouble value = *(npy_clongdouble *)data;
            return PyComplex_FromDoubles((double)value.real, (double)value.imag);
        }
        case NPY_STRING: {
            char *bytes = (char *)data;
            size_t length = array->itemsize;
            while (length > 0 && bytes[length - 1] == '\0') {
                length--;
            }
            return PyBytes_FromStringAndSize(bytes, (Py_ssize_t)length);
        }
        case NPY_OBJECT: {
            PyObject *item = *(PyObject **)data;
            if (item == NULL) Py_RETURN_NONE;
            Py_INCREF(item);
            return item;
        }
        default:
            pccnp_raise_unsupported();
            return NULL;
    }
}

static int pccnp_setitem(PyArrayObject *arr, void *data, PyObject *item) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL || pccnp_reject_null_data(data) != 0) return -1;
    switch (array->typenum) {
        case NPY_BOOL:
            *(unsigned char *)data = PyLong_AsLong(item) != 0;
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_BYTE:
            *(signed char *)data = (signed char)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_UBYTE:
            *(unsigned char *)data = (unsigned char)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_SHORT:
            *(short *)data = (short)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_USHORT:
            *(unsigned short *)data = (unsigned short)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_INT:
            *(int *)data = (int)PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_UINT:
            *(unsigned int *)data = (unsigned int)PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONG:
            *(long *)data = PyLong_AsLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_ULONG:
            *(unsigned long *)data = PyLong_AsUnsignedLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONGLONG:
            *(long long *)data = PyLong_AsLongLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_ULONGLONG:
            *(unsigned long long *)data = PyLong_AsUnsignedLongLong(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_FLOAT:
            *(float *)data = (float)PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_DOUBLE:
            *(double *)data = PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_LONGDOUBLE:
            *(long double *)data = (long double)PyFloat_AsDouble(item);
            return PyErr_Occurred() == NULL ? 0 : -1;
        case NPY_CFLOAT: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            ((npy_cfloat *)data)->real = (float)value.real;
            ((npy_cfloat *)data)->imag = (float)value.imag;
            return 0;
        }
        case NPY_CDOUBLE: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            *(npy_cdouble *)data = value;
            return 0;
        }
        case NPY_CLONGDOUBLE: {
            Py_complex value = PyComplex_AsCComplex(item);
            if (PyErr_Occurred() != NULL) return -1;
            ((npy_clongdouble *)data)->real = (long double)value.real;
            ((npy_clongdouble *)data)->imag = (long double)value.imag;
            return 0;
        }
        case NPY_STRING: {
            const char *bytes = NULL;
            Py_ssize_t length = 0;
            if (pccnp_string_bytes_view(item, &bytes, &length) != 0) return -1;
            if (length < 0) {
                PyErr_SetString(PyExc_ValueError, "negative string length");
                return -1;
            }
            size_t copy_length = (size_t)length;
            if (copy_length > array->itemsize) copy_length = array->itemsize;
            char *target = (char *)data;
            for (size_t i = 0; i < array->itemsize; i++) {
                target[i] = '\0';
            }
            for (size_t i = 0; i < copy_length; i++) {
                target[i] = bytes[i];
            }
            return 0;
        }
        case NPY_OBJECT: {
            PyObject **slot = (PyObject **)data;
            Py_XINCREF(item);
            Py_XDECREF(*slot);
            *slot = item;
            return 0;
        }
        default:
            pccnp_raise_unsupported();
            return -1;
    }
}

static PyObject *pccnp_to_scalar(void *data, PyArrayObject *arr) {
    return pccnp_getitem(arr, data);
}

static PyObject *pccnp_ufunc_callable_entry(PyObject *captures, PyObject *args) {
    PyObject *capsule = PyTuple_GetItem(captures, 0);
    if (capsule == NULL) return NULL;
    PccNpUFunc *ufunc = pccnp_ufunc_from_capsule(capsule);
    if (ufunc == NULL) return NULL;
    if (ufunc->nin != 2 || ufunc->nout != 1) {
        pccnp_raise_unsupported();
        return NULL;
    }
    if (PyTuple_Size(args) != 2) {
        PyErr_SetString(PyExc_TypeError, "minimal ufunc expects two operands");
        return NULL;
    }

    PyObject *operands[2] = {PyTuple_GetItem(args, 0), PyTuple_GetItem(args, 1)};
    if (operands[0] == NULL || operands[1] == NULL) return NULL;
    PccNpArray *arrays[2] = {NULL, NULL};
    int is_array[2] = {
        pccnp_is_array_object(operands[0]),
        pccnp_is_array_object(operands[1]),
    };
    int has_array_operand = is_array[0] || is_array[1];
    for (int i = 0; i < 2; i++) {
        if (!is_array[i]) continue;
        arrays[i] = pccnp_array_from_object((PyArrayObject *)operands[i]);
        if (arrays[i] == NULL) return NULL;
    }
    int out_nd = 0;
    npy_intp *out_dims = NULL;
    if (pccnp_compute_broadcast_shape(arrays, is_array, &out_nd, &out_dims) != 0) {
        return NULL;
    }
    int signature = -1;
    int out_type = NPY_NOTYPE;
    int width = ufunc->nin + ufunc->nout;
    for (int i = 0; i < ufunc->ntypes; i++) {
        int base = i * width;
        int lhs_type = (unsigned char)ufunc->types[base];
        int rhs_type = (unsigned char)ufunc->types[base + 1];
        int candidate_out = (unsigned char)ufunc->types[base + 2];
        int lhs_matches = is_array[0]
            ? pccnp_array_matches_type(arrays[0], lhs_type)
            : pccnp_scalar_matches_type(operands[0], lhs_type);
        int rhs_matches = is_array[1]
            ? pccnp_array_matches_type(arrays[1], rhs_type)
            : pccnp_scalar_matches_type(operands[1], rhs_type);
        if (lhs_matches && rhs_matches && pccnp_itemsize(candidate_out) != 0) {
            signature = i;
            out_type = candidate_out;
            break;
        }
    }
    if (signature < 0) {
        PyMem_Free(out_dims);
        pccnp_raise_unsupported();
        return NULL;
    }

    PyObject *out_obj = pccnp_array_new_common(
        out_nd,
        out_dims,
        out_type,
        NULL,
        1
    );
    PyMem_Free(out_dims);
    if (out_obj == NULL) return NULL;
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)out_obj);
    if (out == NULL) {
        Py_DECREF(out_obj);
        return NULL;
    }

    PyObject *operand_temps[2] = {NULL, NULL};
    PccNpArray *loop_arrays[2] = {arrays[0], arrays[1]};
    for (int i = 0; i < 2; i++) {
        int scalar_type = (unsigned char)ufunc->types[(signature * width) + i];
        if (is_array[i]) {
            if (arrays[i]->typenum == scalar_type) continue;
            PyArray_Descr *cast_descr = pccnp_descr_for_type(scalar_type);
            operand_temps[i] = pccnp_cast_array(operands[i], arrays[i], cast_descr);
            if (operand_temps[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            loop_arrays[i] = pccnp_array_from_object(
                (PyArrayObject *)operand_temps[i]
            );
            if (loop_arrays[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
        } else {
            operand_temps[i] = pccnp_array_new_common(0, NULL, scalar_type, NULL, 1);
            if (operand_temps[i] == NULL) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            PccNpArray *scalar_array = pccnp_array_from_object(
                (PyArrayObject *)operand_temps[i]
            );
            if (scalar_array == NULL ||
                pccnp_setitem(
                    (PyArrayObject *)operand_temps[i],
                    scalar_array->data,
                    operands[i]
                ) != 0) {
                Py_XDECREF(operand_temps[0]);
                Py_XDECREF(operand_temps[1]);
                Py_DECREF(out_obj);
                return NULL;
            }
            loop_arrays[i] = scalar_array;
        }
    }

    npy_intp dimensions[1] = {1};
    npy_intp steps[3] = {0, 0, (npy_intp)out->itemsize};
    char *out_data = (char *)out->data;
    for (size_t i = 0; i < out->element_count; i++) {
        char *loop_args[3] = {
            pccnp_broadcast_data_ptr(loop_arrays[0], is_array[0], out->dims, out->nd, i),
            pccnp_broadcast_data_ptr(loop_arrays[1], is_array[1], out->dims, out->nd, i),
            out_data + (i * out->itemsize),
        };
        ufunc->functions[signature](
            loop_args,
            dimensions,
            steps,
            ufunc->data[signature]
        );
        if (PyErr_Occurred() != NULL) break;
    }
    if (PyErr_Occurred() != NULL) {
        Py_XDECREF(operand_temps[0]);
        Py_XDECREF(operand_temps[1]);
        Py_DECREF(out_obj);
        return NULL;
    }
    if (!has_array_operand) {
        PyObject *scalar_result = pccnp_getitem((PyArrayObject *)out_obj, out->data);
        Py_XDECREF(operand_temps[0]);
        Py_XDECREF(operand_temps[1]);
        Py_DECREF(out_obj);
        return scalar_result;
    }
    Py_XDECREF(operand_temps[0]);
    Py_XDECREF(operand_temps[1]);
    return out_obj;
}

static PyObject *pccnp_ufunc_from_func_and_data(
    PyUFuncGenericFunction *func,
    void **data,
    char *types,
    int ntypes,
    int nin,
    int nout,
    int identity,
    const char *name,
    const char *doc,
    int check_return
) {
    if (func == NULL || types == NULL || ntypes <= 0 || nin < 0 || nout <= 0 ||
        name == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    size_t type_count = 0;
    if (pccnp_mul_size((size_t)ntypes, (size_t)(nin + nout), &type_count) != 0) {
        return NULL;
    }
    PccNpUFunc *ufunc = (PccNpUFunc *)PyMem_Calloc(1, sizeof(PccNpUFunc));
    if (ufunc == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    ufunc->ntypes = ntypes;
    ufunc->nin = nin;
    ufunc->nout = nout;
    ufunc->identity = identity;
    ufunc->check_return = check_return;
    ufunc->functions = (PyUFuncGenericFunction *)PyMem_Calloc(
        (size_t)ntypes,
        sizeof(PyUFuncGenericFunction)
    );
    ufunc->data = (void **)PyMem_Calloc((size_t)ntypes, sizeof(void *));
    ufunc->types = (char *)PyMem_Calloc(type_count == 0 ? 1 : type_count, 1);
    ufunc->name = pccnp_copy_cstr(name);
    if (doc != NULL) {
        ufunc->doc = pccnp_copy_cstr(doc);
    }
    if (ufunc->functions == NULL || ufunc->data == NULL || ufunc->types == NULL ||
        ufunc->name == NULL || (doc != NULL && ufunc->doc == NULL)) {
        if (PyErr_Occurred() == NULL) PyErr_NoMemory();
        pccnp_ufunc_free(ufunc);
        return NULL;
    }
    for (int i = 0; i < ntypes; i++) {
        ufunc->functions[i] = func[i];
        ufunc->data[i] = data == NULL ? NULL : data[i];
    }
    for (size_t i = 0; i < type_count; i++) {
        ufunc->types[i] = types[i];
    }

    PyObject *capsule = PyCapsule_New(
        ufunc,
        PCCNP_UFUNC_OBJECT_CAPSULE,
        pccnp_ufunc_capsule_destructor
    );
    if (capsule == NULL) {
        pccnp_ufunc_free(ufunc);
        return NULL;
    }
    if (PyCapsule_SetContext(capsule, ufunc->name) != 0) {
        Py_DECREF(capsule);
        return NULL;
    }
    PyObject *captures = PyTuple_Pack(1, capsule);
    Py_DECREF(capsule);
    if (captures == NULL) return NULL;
    PyObject *callable = py_func_new((void *)pccnp_ufunc_callable_entry, captures);
    Py_DECREF(captures);
    return callable;
}

static void *array_api[] = {
    &pccnp_array_type,
    &pccnp_array_descr_type,
    (void *)pccnp_descr_from_type,
    (void *)pccnp_from_any,
    (void *)pccnp_simple_new,
    (void *)pccnp_simple_new_from_data,
    (void *)pccnp_ndim,
    (void *)pccnp_dims,
    (void *)pccnp_strides,
    (void *)pccnp_data,
    (void *)pccnp_descr,
    (void *)pccnp_getitem,
    (void *)pccnp_setitem,
    (void *)pccnp_size,
    (void *)pccnp_itemsize_api,
    (void *)pccnp_array_check,
    (void *)pccnp_array_check_exact,
    (void *)pccnp_flags,
    (void *)pccnp_compare_lists,
    (void *)pccnp_empty,
    (void *)pccnp_zeros,
    (void *)pccnp_equiv_types,
    (void *)pccnp_new_from_descr,
    (void *)pccnp_base,
    (void *)pccnp_set_base_object,
    (void *)pccnp_return,
    (void *)pccnp_enable_flags,
    (void *)pccnp_clear_flags,
    (void *)pccnp_update_flags,
    (void *)pccnp_copy_into,
    (void *)pccnp_copy_into,
    (void *)pccnp_to_scalar,
    (void *)pccnp_copy,
    (void *)pccnp_ensure_array,
    (void *)pccnp_ensure_any_array,
    (void *)pccnp_descr_new_from_type,
    (void *)pccnp_descr_new,
    (void *)pccnp_descr_new_byteorder,
    (void *)pccnp_can_cast_safely,
    (void *)pccnp_object_type,
    (void *)pccnp_check_from_any,
    (void *)pccnp_from_array,
    (void *)pccnp_multiply_list,
    (void *)pccnp_multiply_int_list,
    (void *)pccnp_get_ptr,
    (void *)pccnp_element_strides,
    (void *)pccnp_valid_type,
    (void *)pccnp_item_incref,
    (void *)pccnp_item_xdecref,
    (void *)pccnp_new_copy,
    (void *)pccnp_array_incref,
    (void *)pccnp_array_xdecref,
    (void *)pccnp_can_cast_to,
    (void *)pccnp_zero,
    (void *)pccnp_one,
    (void *)pccnp_type_object_from_type,
    (void *)pccnp_descr_from_object,
    (void *)pccnp_size_object,
    (void *)pccnp_descr_from_scalar,
    (void *)pccnp_descr_from_type_object,
    (void *)pccnp_scalar_as_ctype,
    (void *)pccnp_from_scalar,
    (void *)pccnp_cast_scalar_to_ctype,
    (void *)pccnp_pack,
    (void *)pccnp_cast_scalar_direct,
    (void *)pccnp_cast_to_type,
    (void *)pccnp_fill_with_scalar,
    (void *)pccnp_to_list,
    (void *)pccnp_to_string,
    (void *)pccnp_byteswap,
    (void *)pccnp_from_string,
    (void *)pccnp_from_buffer,
    (void *)pccnp_from_iter,
    (void *)pccnp_copy_object,
    (void *)pccnp_resize,
    (void *)pccnp_new_like_array,
    (void *)pccnp_view,
    (void *)pccnp_squeeze,
    (void *)pccnp_transpose,
    (void *)pccnp_ravel,
    (void *)pccnp_flatten,
    (void *)pccnp_take_from,
    (void *)pccnp_put_to,
    (void *)pccnp_put_mask,
    (void *)pccnp_repeat,
    (void *)pccnp_choose,
    (void *)pccnp_sort,
    (void *)pccnp_arg_sort,
    (void *)pccnp_search_sorted,
    (void *)pccnp_nonzero,
    (void *)pccnp_where,
    (void *)pccnp_compress,
    (void *)pccnp_diagonal,
    (void *)pccnp_trace,
    (void *)pccnp_clip,
    (void *)pccnp_conjugate,
    (void *)pccnp_sum,
    (void *)pccnp_prod,
    (void *)pccnp_max,
    (void *)pccnp_min,
    (void *)pccnp_arg_max,
    (void *)pccnp_arg_min,
    (void *)pccnp_reshape,
    (void *)pccnp_newshape,
    (void *)pccnp_swap_axes,
    (void *)pccnp_ptp,
    (void *)pccnp_mean,
    (void *)pccnp_any,
    (void *)pccnp_all,
    (void *)pccnp_cumsum,
    (void *)pccnp_cumprod,
    (void *)pccnp_std,
    (void *)pccnp_round,
    (void *)pccnp_equiv_typenums,
    (void *)pccnp_can_coerce_scalar,
    NULL,
    (void *)pccnp_can_cast_scalar,
    (void *)pccnp_int_tuple_from_intp,
    (void *)pccnp_clipmode_converter,
    (void *)pccnp_output_converter,
    (void *)pccnp_searchside_converter,
    (void *)pccnp_overflow_multiply_list,
    (void *)pccnp_get_endianness,
    (void *)pccnp_get_ndarray_c_feature_version,
    (void *)pccnp_check_axis,
    (void *)pccnp_descr_align_converter,
    (void *)pccnp_descr_align_converter2,
    (void *)pccnp_iter_new,
    (void *)pccnp_broadcast_to_shape,
    (void *)pccnp_iter_all_but_axis,
    (void *)pccnp_pyint_as_int,
    (void *)pccnp_pyint_as_intp,
    (void *)pccnp_check_strides,
    (void *)pccnp_get_priority,
    (void *)pccnp_order_converter,
    (void *)pccnp_bool_converter,
    (void *)pccnp_axis_converter,
    (void *)pccnp_get_ndarray_c_version,
    (void *)pccnp_byteorder_converter,
    (void *)pccnp_sortkind_converter,
    (void *)pccnp_selectkind_converter,
    (void *)pccnp_convert_clipmode_sequence,
    (void *)pccnp_optional_bool_converter,
    (void *)pccnp_intp_from_sequence,
    (void *)pccnp_converter,
    (void *)pccnp_intp_converter,
    (void *)pccnp_optional_intp_converter,
    (void *)pccnp_array_free_api,
    (void *)pccnp_as_carray,
    (void *)pccnp_fail_unless_writeable,
    (void *)pccnp_descr_converter,
    (void *)pccnp_descr_converter2,
    (void *)pccnp_set_update_if_copy_base,
    (void *)pccnp_set_writeback_if_copy_base,
    (void *)pccnp_resolve_writeback_if_copy,
    (void *)pccnp_discard_writeback_if_copy,
    (void *)pccnp_datamem_new,
    (void *)pccnp_datamem_free,
    (void *)pccnp_datamem_renew,
    (void *)pccnp_datamem_new_zeroed,
    (void *)pccnp_datamem_get_handler,
    (void *)pccnp_datamem_user_new,
    (void *)pccnp_datamem_user_free,
    (void *)pccnp_datamem_user_renew,
    (void *)pccnp_datamem_user_new_zeroed,
    (void *)pccnp_can_cast_type_to,
    (void *)pccnp_can_cast_array_to,
    (void *)pccnp_python_pyint_from_int,
    (void *)pccnp_casting_converter,
    (void *)pccnp_scalar,
    (void *)pccnp_scalar_kind,
    (void *)pccnp_convert_to_common_type,
    (void *)pccnp_new,
    (void *)pccnp_multi_iter_new,
    (void *)pccnp_promote_types,
    (void *)pccnp_result_type,
    (void *)pccnp_broadcast,
    (void *)pccnp_multi_iter_from_objects,
    (void *)pccnp_remove_smallest,
    (void *)pccnp_buffer_converter,
    (void *)pccnp_concatenate,
    (void *)pccnp_arange,
    (void *)pccnp_arange_obj,
    (void *)pccnp_lex_sort,
    (void *)pccnp_inner_product,
    (void *)pccnp_matrix_product,
    (void *)pccnp_correlate,
    (void *)pccnp_correlate2,
    (void *)pccnp_matrix_product2,
    (void *)pccnp_count_nonzero,
    (void *)pccnp_min_scalar_type,
    (void *)pccnp_create_sorted_stride_perm,
    (void *)pccnp_remove_axes_in_place,
    (void *)pccnp_debug_print,
    (void *)pccnp_einstein_sum,
    (void *)pccnp_partition,
    (void *)pccnp_arg_partition,
    (void *)pccnp_check_any_scalar_exact,
};

static void *ufunc_api[] = {
    (void *)pccnp_ufunc_from_func_and_data,
};

static PyMethodDef ProviderMethods[] = {
    {NULL, NULL, 0, NULL},
};

static PyModuleDef providermodule = {
    PyModuleDef_HEAD_INIT, "pccnpapi", NULL, -1, ProviderMethods,
};

PyMODINIT_FUNC PyInit_pccnpapi(void) {
    if (PyType_Ready(&pccnp_array_type) < 0) return NULL;
    if (PyType_Ready(&pccnp_array_descr_type) < 0) return NULL;
    if (PyType_Ready(&pccnp_array_iter_type) < 0) return NULL;
    if (PyType_Ready(&pccnp_array_multi_iter_type) < 0) return NULL;
    pccnp_ready_all_descriptors();
    PyObject *module = PyModule_Create(&providermodule);
    if (module == NULL) return NULL;
    PyObject *capsule = PyCapsule_New(array_api, PCC_NUMPY_ARRAY_API_CAPSULE, NULL);
    if (capsule == NULL) return NULL;
    if (PyModule_AddObject(module, "_ARRAY_API", capsule) != 0) return NULL;
    capsule = PyCapsule_New(ufunc_api, PCC_NUMPY_UFUNC_API_CAPSULE, NULL);
    if (capsule == NULL) return NULL;
    if (PyModule_AddObject(module, "_UFUNC_API", capsule) != 0) return NULL;
    return module;
}
