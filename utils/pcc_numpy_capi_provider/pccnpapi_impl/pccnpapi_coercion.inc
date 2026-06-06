/* sequence, scalar, dtype coercion, string/buffer/from-iter helpers.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static int pccnp_validate_depth(int nd, int min_depth, int max_depth) {
    if (min_depth > nd) {
        PyErr_SetString(PyExc_ValueError, "array has too few dimensions");
        return -1;
    }
    if (max_depth > 0 && nd > max_depth) {
        PyErr_SetString(PyExc_ValueError, "array has too many dimensions");
        return -1;
    }
    return 0;
}

static int pccnp_should_descend_sequence(PyObject *obj, int typenum) {
    if (typenum == NPY_OBJECT) {
        return PyTuple_Check(obj) || PyList_Check(obj);
    }
    if (typenum == NPY_STRING && (PyBytes_Check(obj) || PyUnicode_Check(obj))) {
        return 0;
    }
    return PySequence_Check(obj);
}

static int pccnp_string_bytes_view(
    PyObject *obj,
    const char **bytes,
    Py_ssize_t *length
) {
    if (PyBytes_Check(obj)) {
        char *raw = NULL;
        Py_ssize_t raw_length = 0;
        if (PyBytes_AsStringAndSize(obj, &raw, &raw_length) != 0) return -1;
        *bytes = raw;
        *length = raw_length;
        return 0;
    }
    if (PyUnicode_Check(obj)) {
        const char *raw = PyUnicode_AsUTF8AndSize(obj, length);
        if (raw == NULL) return -1;
        *bytes = raw;
        return 0;
    }
    PyErr_SetString(PyExc_TypeError, "expected str or bytes for string dtype");
    return -1;
}

static int pccnp_scalar_string_length(PyObject *obj, size_t *length) {
    const char *bytes = NULL;
    Py_ssize_t raw_length = 0;
    if (pccnp_string_bytes_view(obj, &bytes, &raw_length) != 0) return -1;
    (void)bytes;
    if (raw_length < 0) {
        PyErr_SetString(PyExc_ValueError, "negative string length");
        return -1;
    }
    *length = (size_t)raw_length;
    return 0;
}

static int pccnp_promote_inferred_type(int *typenum, int item_type) {
    if (*typenum == NPY_NOTYPE) {
        *typenum = item_type;
        return 0;
    }
    if (*typenum == NPY_OBJECT || item_type == NPY_OBJECT) {
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == PCCNP_INFER_STRING || item_type == PCCNP_INFER_STRING) {
        if (*typenum == PCCNP_INFER_STRING && item_type == PCCNP_INFER_STRING) {
            return 0;
        }
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == PCCNP_INFER_CDOUBLE || item_type == PCCNP_INFER_CDOUBLE) {
        *typenum = PCCNP_INFER_CDOUBLE;
        return 0;
    }
    if ((*typenum == NPY_ULONG && item_type == PCCNP_INFER_NEGATIVE_LONG) ||
        (*typenum == PCCNP_INFER_NEGATIVE_LONG && item_type == NPY_ULONG)) {
        *typenum = NPY_OBJECT;
        return 0;
    }
    if (*typenum == NPY_DOUBLE || item_type == NPY_DOUBLE) {
        *typenum = NPY_DOUBLE;
        return 0;
    }
    if (*typenum == NPY_ULONG || item_type == NPY_ULONG) {
        *typenum = NPY_ULONG;
        return 0;
    }
    if (*typenum == PCCNP_INFER_NEGATIVE_LONG ||
        item_type == PCCNP_INFER_NEGATIVE_LONG) {
        *typenum = PCCNP_INFER_NEGATIVE_LONG;
        return 0;
    }
    if (*typenum == NPY_LONG || item_type == NPY_LONG) {
        *typenum = NPY_LONG;
        return 0;
    }
    *typenum = NPY_BOOL;
    return 0;
}

static int pccnp_scalar_inferred_type(PyObject *obj) {
    if (PyBool_Check(obj)) return NPY_BOOL;
    if (PyLong_Check(obj)) {
        long signed_value = PyLong_AsLong(obj);
        if (PyErr_Occurred() == NULL) {
            return signed_value < 0 ? PCCNP_INFER_NEGATIVE_LONG : NPY_LONG;
        }
        PyErr_Clear();
        (void)PyLong_AsUnsignedLong(obj);
        if (PyErr_Occurred() == NULL) return NPY_ULONG;
        PyErr_Clear();
        return NPY_OBJECT;
    }
    if (PyFloat_Check(obj)) return NPY_DOUBLE;
    if (PyComplex_Check(obj)) return PCCNP_INFER_CDOUBLE;
    if (PyBytes_Check(obj) || PyUnicode_Check(obj)) return PCCNP_INFER_STRING;
    return NPY_OBJECT;
}

static int pccnp_public_inferred_type(int typenum) {
    if (typenum == PCCNP_INFER_NEGATIVE_LONG) return NPY_LONG;
    if (typenum == PCCNP_INFER_CDOUBLE) return NPY_CDOUBLE;
    if (typenum == PCCNP_INFER_STRING) return NPY_STRING;
    return typenum;
}

static int pccnp_infer_sequence_dtype(
    PyObject *seq,
    int depth,
    int *typenum,
    size_t *string_itemsize
) {
    if (depth >= PCCNP_MAX_SEQUENCE_ND) {
        pccnp_raise_unsupported();
        return -1;
    }
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int rc = 0;
        if (PyTuple_Check(item) || PyList_Check(item)) {
            rc = pccnp_infer_sequence_dtype(
                item,
                depth + 1,
                typenum,
                string_itemsize
            );
        } else {
            int item_type = pccnp_scalar_inferred_type(item);
            if (item_type == PCCNP_INFER_STRING) {
                size_t length = 0;
                if (pccnp_scalar_string_length(item, &length) != 0) {
                    Py_DECREF(item);
                    return -1;
                }
                if (length > *string_itemsize) *string_itemsize = length;
            }
            rc = pccnp_promote_inferred_type(
                typenum,
                item_type
            );
        }
        Py_DECREF(item);
        if (rc != 0) return -1;
    }
    return 0;
}

static int pccnp_object_type(PyObject *op, int minimum_type) {
    if (op == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ObjectType requires an object");
        return NPY_NOTYPE;
    }
    if (minimum_type != NPY_NOTYPE && pccnp_descr_for_type(minimum_type) == NULL) {
        pccnp_raise_unsupported();
        return NPY_NOTYPE;
    }

    int typenum = NPY_OBJECT;
    if (pccnp_is_array_object(op)) {
        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)op);
        if (array == NULL) return NPY_NOTYPE;
        typenum = array->typenum;
    } else if (!PyBytes_Check(op) && !PyUnicode_Check(op) && PySequence_Check(op)) {
        int inferred_type = NPY_NOTYPE;
        size_t inferred_string_itemsize = 0;
        if (pccnp_infer_sequence_dtype(
                op,
                0,
                &inferred_type,
                &inferred_string_itemsize
            ) != 0) {
            return NPY_NOTYPE;
        }
        (void)inferred_string_itemsize;
        typenum = inferred_type == NPY_NOTYPE ? NPY_DOUBLE : inferred_type;
    } else {
        typenum = pccnp_scalar_inferred_type(op);
    }

    if (minimum_type != NPY_NOTYPE) {
        int promoted_type = minimum_type;
        if (pccnp_promote_inferred_type(&promoted_type, typenum) != 0) {
            return NPY_NOTYPE;
        }
        typenum = promoted_type;
    }

    typenum = pccnp_public_inferred_type(typenum);
    if (pccnp_descr_for_type(typenum) == NULL) {
        pccnp_raise_unsupported();
        return NPY_NOTYPE;
    }
    return typenum;
}

static PyArray_Descr *pccnp_descr_from_object(
    PyObject *op,
    PyArray_Descr *mintype
) {
    int minimum_type = mintype == NULL ? NPY_NOTYPE : mintype->type_num;
    int typenum = pccnp_object_type(op, minimum_type);
    if (typenum == NPY_NOTYPE) return NULL;
    return pccnp_descr_from_type(typenum);
}

static int pccnp_descr_align_converter(PyObject *obj, PyArray_Descr **at) {
    if (at == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrAlignConverter output pointer is NULL");
        return NPY_FAIL;
    }
    *at = pccnp_descr_from_object(obj, NULL);
    return *at != NULL ? NPY_SUCCEED : NPY_FAIL;
}

static int pccnp_descr_converter(PyObject *obj, PyArray_Descr **at) {
    if (at == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrConverter output pointer is NULL");
        return NPY_FAIL;
    }
    *at = pccnp_descr_from_object(obj, NULL);
    return *at != NULL ? NPY_SUCCEED : NPY_FAIL;
}

static int pccnp_descr_converter2(PyObject *obj, PyArray_Descr **at) {
    if (at == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrConverter2 output pointer is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        *at = NULL;
        return NPY_SUCCEED;
    }
    return pccnp_descr_converter(obj, at);
}

static int pccnp_descr_align_converter2(PyObject *obj, PyArray_Descr **at) {
    if (at == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrAlignConverter2 output pointer is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        *at = NULL;
        return NPY_SUCCEED;
    }
    return pccnp_descr_align_converter(obj, at);
}

static PyArray_Descr *pccnp_descr_from_scalar(PyObject *scalar) {
    if (scalar == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrFromScalar requires a scalar");
        return NULL;
    }
    int typenum = pccnp_public_inferred_type(pccnp_scalar_inferred_type(scalar));
    if (pccnp_descr_for_type(typenum) == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    return pccnp_descr_from_type(typenum);
}

static PyArray_Descr *pccnp_descr_from_type_object(PyObject *type) {
    if (type == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_DescrFromTypeObject requires a type object");
        return NULL;
    }
    int typenum = NPY_NOTYPE;
    if (type == (PyObject *)&PyBool_Type) {
        typenum = NPY_BOOL;
    } else if (type == (PyObject *)&PyLong_Type) {
        typenum = NPY_LONG;
    } else if (type == (PyObject *)&PyFloat_Type) {
        typenum = NPY_DOUBLE;
    } else if (type == (PyObject *)&PyComplex_Type) {
        typenum = NPY_CDOUBLE;
    } else if (type == (PyObject *)&PyBytes_Type ||
               type == (PyObject *)&PyUnicode_Type) {
        typenum = NPY_STRING;
    } else if (type == (PyObject *)&PyBaseObject_Type) {
        typenum = NPY_OBJECT;
    } else {
        pccnp_raise_unsupported();
        return NULL;
    }
    return pccnp_descr_from_type(typenum);
}

static void pccnp_scalar_as_ctype(PyObject *scalar, void *ctypeptr) {
    if (scalar == NULL || ctypeptr == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ScalarAsCtype requires scalar and output buffer");
        return;
    }
    int typenum = pccnp_public_inferred_type(pccnp_scalar_inferred_type(scalar));
    switch (typenum) {
        case NPY_BOOL:
            *(npy_bool *)ctypeptr = PyLong_AsLong(scalar) != 0;
            return;
        case NPY_LONG:
            *(long *)ctypeptr = PyLong_AsLong(scalar);
            return;
        case NPY_ULONG:
            *(unsigned long *)ctypeptr = PyLong_AsUnsignedLong(scalar);
            return;
        case NPY_DOUBLE:
            *(double *)ctypeptr = PyFloat_AsDouble(scalar);
            return;
        case NPY_CDOUBLE: {
            Py_complex value = PyComplex_AsCComplex(scalar);
            if (PyErr_Occurred() != NULL) return;
            *(npy_cdouble *)ctypeptr = value;
            return;
        }
        case NPY_STRING: {
            const char *bytes = NULL;
            Py_ssize_t length = 0;
            if (pccnp_string_bytes_view(scalar, &bytes, &length) != 0) return;
            (void)length;
            *(char **)ctypeptr = (char *)bytes;
            return;
        }
        case NPY_OBJECT:
            *(PyObject **)ctypeptr = scalar;
            return;
        default:
            pccnp_raise_unsupported();
            return;
    }
}

static PyObject *pccnp_from_scalar(PyObject *scalar, PyArray_Descr *outcode) {
    if (scalar == NULL) {
        Py_XDECREF((PyObject *)outcode);
        PyErr_SetString(PyExc_ValueError, "PyArray_FromScalar requires a scalar");
        return NULL;
    }
    PyArray_Descr *descr = outcode;
    PyArray_Descr *owned_descr = NULL;
    if (descr == NULL) {
        owned_descr = pccnp_descr_from_scalar(scalar);
        if (owned_descr == NULL) return NULL;
        descr = owned_descr;
    }
    size_t itemsize = pccnp_itemsize_from_descr(descr);
    if (itemsize == 0) {
        Py_XDECREF((PyObject *)owned_descr);
        Py_XDECREF((PyObject *)outcode);
        pccnp_raise_unsupported();
        return NULL;
    }
    PyObject *array_obj = pccnp_array_new_with_itemsize(
        0,
        NULL,
        descr->type_num,
        itemsize,
        NULL,
        1
    );
    Py_XDECREF((PyObject *)owned_descr);
    Py_XDECREF((PyObject *)outcode);
    if (array_obj == NULL) return NULL;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL || pccnp_setitem((PyArrayObject *)array_obj, array->data, scalar) != 0) {
        Py_DECREF(array_obj);
        return NULL;
    }
    return array_obj;
}

static PyObject *pccnp_scalar(void *data, PyArray_Descr *descr, PyObject *base) {
    (void)base;
    if (descr == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Scalar requires a descriptor");
        return NULL;
    }
    if (pccnp_reject_null_data(data) != 0) return NULL;
    if (descr->type_num == NPY_OBJECT) {
        PyObject *item = *(PyObject **)data;
        if (item == NULL) Py_RETURN_NONE;
        Py_INCREF(item);
        return item;
    }
    size_t itemsize = pccnp_itemsize_from_descr(descr);
    if (itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }
    PyObject *array_obj = pccnp_array_new_with_itemsize(
        0,
        NULL,
        descr->type_num,
        itemsize,
        NULL,
        1
    );
    if (array_obj == NULL) return NULL;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL) {
        Py_DECREF(array_obj);
        return NULL;
    }
    memcpy(array->data, data, itemsize);
    PyObject *scalar = pccnp_getitem((PyArrayObject *)array_obj, array->data);
    Py_DECREF(array_obj);
    return scalar;
}

static int pccnp_cast_scalar_to_ctype(
    PyObject *scalar,
    void *ctypeptr,
    PyArray_Descr *outcode
) {
    if (scalar == NULL || ctypeptr == NULL || outcode == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_CastScalarToCtype requires scalar, output buffer, and descriptor");
        return -1;
    }
    size_t itemsize = pccnp_itemsize_from_descr(outcode);
    if (itemsize == 0) {
        pccnp_raise_unsupported();
        return -1;
    }
    PyObject *array_obj = pccnp_array_new_with_itemsize(
        0,
        NULL,
        outcode->type_num,
        itemsize,
        NULL,
        1
    );
    if (array_obj == NULL) return -1;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL ||
        pccnp_setitem((PyArrayObject *)array_obj, array->data, scalar) != 0) {
        Py_DECREF(array_obj);
        return -1;
    }
    memcpy(ctypeptr, array->data, itemsize);
    Py_DECREF(array_obj);
    return 0;
}

static int pccnp_pack(PyArray_Descr *descr, void *item, PyObject *value) {
    if (descr == NULL || item == NULL || value == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Pack requires descriptor, output item buffer, and value");
        return -1;
    }
    if (pccnp_is_array_object(value)) {
        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)value);
        if (array == NULL) return -1;
        if (array->nd != 0 || array->element_count != 1) {
            pccnp_raise_unsupported();
            return -1;
        }
        PyObject *scalar = pccnp_getitem((PyArrayObject *)value, array->data);
        if (scalar == NULL) return -1;
        int rc = pccnp_pack(descr, item, scalar);
        Py_DECREF(scalar);
        return rc;
    }
    if (descr->type_num == NPY_OBJECT) {
        PyObject **slot = (PyObject **)item;
        Py_XINCREF(value);
        Py_XDECREF(*slot);
        *slot = value;
        return 0;
    }
    size_t itemsize = pccnp_itemsize_from_descr(descr);
    if (itemsize == 0) {
        pccnp_raise_unsupported();
        return -1;
    }
    PyObject *array_obj = pccnp_array_new_with_itemsize(
        0,
        NULL,
        descr->type_num,
        itemsize,
        NULL,
        1
    );
    if (array_obj == NULL) return -1;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL ||
        pccnp_setitem((PyArrayObject *)array_obj, array->data, value) != 0) {
        Py_DECREF(array_obj);
        return -1;
    }
    memcpy(item, array->data, itemsize);
    Py_DECREF(array_obj);
    return 0;
}

static int pccnp_cast_scalar_direct(
    PyObject *scalar,
    PyArray_Descr *indescr,
    void *ctypeptr,
    int outtype
) {
    if (scalar == NULL || indescr == NULL || ctypeptr == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_CastScalarDirect requires scalar, input descriptor, and output buffer");
        return -1;
    }
    if (pccnp_itemsize_from_descr(indescr) == 0) {
        pccnp_raise_unsupported();
        return -1;
    }
    PyArray_Descr *out_descr = pccnp_descr_for_type(outtype);
    if (out_descr == NULL) {
        pccnp_raise_unsupported();
        return -1;
    }
    return pccnp_cast_scalar_to_ctype(scalar, ctypeptr, out_descr);
}

static int pccnp_infer_sequence_shape(
    PyObject *seq,
    int typenum,
    int depth,
    int *nd,
    int *leaf_depth,
    npy_intp *dims
) {
    if (depth >= PCCNP_MAX_SEQUENCE_ND) {
        pccnp_raise_unsupported();
        return -1;
    }
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    if (*nd <= depth) {
        *nd = depth + 1;
        dims[depth] = (npy_intp)length;
    } else if (dims[depth] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
        return -1;
    }

    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        if (pccnp_should_descend_sequence(item, typenum)) {
            int rc = pccnp_infer_sequence_shape(
                item,
                typenum,
                depth + 1,
                nd,
                leaf_depth,
                dims
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
        } else {
            int item_leaf_depth = depth + 1;
            if (*leaf_depth < 0) {
                *leaf_depth = item_leaf_depth;
            } else if (*leaf_depth != item_leaf_depth) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            Py_DECREF(item);
        }
    }
    return 0;
}

static int pccnp_fill_sequence_values(
    PyObject *array_obj,
    PyObject *seq,
    int depth,
    int nd,
    const npy_intp *dims,
    size_t *offset
) {
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    if (dims[depth] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL) return -1;
    char *data = (char *)array->data;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int descend = pccnp_should_descend_sequence(item, array->typenum);
        if (depth + 1 == nd) {
            if (descend) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            int rc = pccnp_setitem(
                (PyArrayObject *)array_obj,
                data + ((*offset) * array->itemsize),
                item
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
            *offset += 1;
        } else {
            if (!descend) {
                Py_DECREF(item);
                PyErr_SetString(PyExc_ValueError, "ragged nested sequences are unsupported");
                return -1;
            }
            int rc = pccnp_fill_sequence_values(
                array_obj,
                item,
                depth + 1,
                nd,
                dims,
                offset
            );
            Py_DECREF(item);
            if (rc != 0) return -1;
        }
    }
    return 0;
}

static int pccnp_fill_object_top_level_values(PyObject *array_obj, PyObject *seq) {
    Py_ssize_t length = PySequence_Size(seq);
    if (length < 0) return -1;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL) return -1;
    if (array->typenum != NPY_OBJECT || array->nd != 1 || array->dims == NULL ||
        array->dims[0] != (npy_intp)length) {
        PyErr_SetString(PyExc_ValueError, "object array top-level shape mismatch");
        return -1;
    }
    char *data = (char *)array->data;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(seq, i);
        if (item == NULL) return -1;
        int rc = pccnp_setitem(
            (PyArrayObject *)array_obj,
            data + ((size_t)i * array->itemsize),
            item
        );
        Py_DECREF(item);
        if (rc != 0) return -1;
    }
    return 0;
}

static PyObject *pccnp_from_sequence(
    PyObject *op,
    PyArray_Descr *descr,
    int min_depth,
    int max_depth
) {
    if (!PySequence_Check(op)) {
        pccnp_raise_unsupported();
        return NULL;
    }
    PyArray_Descr *effective_descr = descr;
    size_t effective_itemsize = 0;
    if (effective_descr == NULL) {
        int inferred_type = NPY_NOTYPE;
        size_t inferred_string_itemsize = 0;
        if (pccnp_infer_sequence_dtype(
                op,
                0,
                &inferred_type,
                &inferred_string_itemsize
            ) != 0) {
            return NULL;
        }
        if (inferred_type == NPY_NOTYPE) inferred_type = NPY_DOUBLE;
        if (inferred_type == PCCNP_INFER_NEGATIVE_LONG) inferred_type = NPY_LONG;
        if (inferred_type == PCCNP_INFER_CDOUBLE) inferred_type = NPY_CDOUBLE;
        if (inferred_type == PCCNP_INFER_STRING) {
            inferred_type = NPY_STRING;
            effective_itemsize = inferred_string_itemsize == 0
                ? 1
                : inferred_string_itemsize;
        }
        effective_descr = pccnp_descr_for_type(inferred_type);
    }
    if (effective_itemsize == 0) {
        effective_itemsize = pccnp_itemsize_from_descr(effective_descr);
    }
    if (effective_descr == NULL || effective_itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }

    Py_ssize_t length = PySequence_Size(op);
    if (length < 0) return NULL;

    int nd = 0;
    int leaf_depth = -1;
    int object_top_level = 0;
    npy_intp dims[PCCNP_MAX_SEQUENCE_ND] = {0};
    if (pccnp_infer_sequence_shape(
            op,
            effective_descr->type_num,
            0,
            &nd,
            &leaf_depth,
            dims
        ) != 0) {
        if (effective_descr->type_num != NPY_OBJECT ||
            !PyErr_ExceptionMatches(PyExc_ValueError)) {
            return NULL;
        }
        PyErr_Clear();
        nd = 1;
        leaf_depth = 1;
        dims[0] = (npy_intp)length;
        object_top_level = 1;
    }
    if (pccnp_validate_depth(nd, min_depth, max_depth) != 0) {
        return NULL;
    }

    PyObject *array_obj = pccnp_array_new_with_itemsize(
        nd,
        dims,
        effective_descr->type_num,
        effective_itemsize,
        NULL,
        1
    );
    if (array_obj == NULL) return NULL;
    size_t offset = 0;
    int fill_rc = object_top_level
        ? pccnp_fill_object_top_level_values(array_obj, op)
        : pccnp_fill_sequence_values(array_obj, op, 0, nd, dims, &offset);
    if (fill_rc != 0) {
        Py_DECREF(array_obj);
        return NULL;
    }
    return array_obj;
}

static PyObject *pccnp_cast_array(
    PyObject *op,
    PccNpArray *array,
    PyArray_Descr *descr
) {
    size_t itemsize = pccnp_itemsize_from_descr(descr);
    if (descr == NULL || itemsize == 0) {
        pccnp_raise_unsupported();
        return NULL;
    }
    PyObject *cast_obj = pccnp_array_new_with_itemsize(
        array->nd,
        array->dims,
        descr->type_num,
        itemsize,
        NULL,
        1
    );
    if (cast_obj == NULL) return NULL;
    PccNpArray *cast_array = pccnp_array_from_object((PyArrayObject *)cast_obj);
    if (cast_array == NULL) {
        Py_DECREF(cast_obj);
        return NULL;
    }
    char *src = (char *)array->data;
    char *dst = (char *)cast_array->data;
    for (size_t i = 0; i < array->element_count; i++) {
        PyObject *item = pccnp_getitem(
            (PyArrayObject *)op,
            src + (i * array->itemsize)
        );
        if (item == NULL) {
            Py_DECREF(cast_obj);
            return NULL;
        }
        int rc = pccnp_setitem(
            (PyArrayObject *)cast_obj,
            dst + (i * cast_array->itemsize),
            item
        );
        Py_DECREF(item);
        if (rc != 0) {
            Py_DECREF(cast_obj);
            return NULL;
        }
    }
    return cast_obj;
}

static PyObject *pccnp_from_any(
    PyObject *op,
    PyArray_Descr *descr,
    int min_depth,
    int max_depth,
    int requirements,
    PyObject *context
) {
    (void)requirements;
    (void)context;
    if (!pccnp_is_array_object(op)) {
        return pccnp_from_sequence(op, descr, min_depth, max_depth);
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)op);
    if (array == NULL) return NULL;
    if (min_depth > array->nd) {
        PyErr_SetString(PyExc_ValueError, "array has too few dimensions");
        return NULL;
    }
    if (max_depth > 0 && array->nd > max_depth) {
        PyErr_SetString(PyExc_ValueError, "array has too many dimensions");
        return NULL;
    }
    if (descr != NULL) {
        size_t descr_itemsize = pccnp_itemsize_from_descr(descr);
        if (descr->type_num != array->typenum || descr_itemsize != array->itemsize) {
            return pccnp_cast_array(op, array, descr);
        }
    }
    Py_INCREF(op);
    return op;
}

static PyObject *pccnp_check_from_any(
    PyObject *op,
    PyArray_Descr *descr,
    int min_depth,
    int max_depth,
    int requirements,
    PyObject *context
) {
    PyObject *result = pccnp_from_any(
        op, descr, min_depth, max_depth, requirements, context
    );
    Py_XDECREF(descr);
    return result;
}

static PyArrayObject **pccnp_convert_to_common_type(PyObject *op, int *retn) {
    if (retn == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ConvertToCommonType retn is NULL");
        return NULL;
    }
    *retn = 0;
    if (op == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ConvertToCommonType requires a sequence");
        return NULL;
    }

    Py_ssize_t length = PySequence_Length(op);
    if (length < 0) return NULL;
    if (length == 0) {
        PyErr_SetString(PyExc_ValueError, "0-length sequence.");
        return NULL;
    }
    if (length > INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "sequence is too large");
        return NULL;
    }

    PyArrayObject **mps = (PyArrayObject **)PyMem_RawMalloc(
        (size_t)length * sizeof(PyArrayObject *)
    );
    if (mps == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    for (Py_ssize_t i = 0; i < length; i++) {
        mps[i] = NULL;
    }

    int common_type = NPY_NOTYPE;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(op, i);
        if (item == NULL) goto fail;
        PyObject *array_obj = pccnp_from_any(
            item,
            NULL,
            0,
            0,
            NPY_ARRAY_CARRAY,
            NULL
        );
        Py_DECREF(item);
        if (array_obj == NULL) goto fail;
        mps[i] = (PyArrayObject *)array_obj;

        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
        if (array == NULL) goto fail;
        if (array->typenum == NPY_STRING) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy ConvertToCommonType does not support string arrays yet"
            );
            goto fail;
        }
        if (pccnp_promote_inferred_type(&common_type, array->typenum) != 0) {
            goto fail;
        }
    }

    common_type = pccnp_public_inferred_type(common_type);
    PyArray_Descr *common_descr = pccnp_descr_for_type(common_type);
    if (common_descr == NULL || pccnp_itemsize_from_descr(common_descr) == 0) {
        pccnp_raise_unsupported();
        goto fail;
    }

    for (Py_ssize_t i = 0; i < length; i++) {
        PccNpArray *array = pccnp_array_from_object(mps[i]);
        if (array == NULL) goto fail;
        if (array->typenum == common_type &&
            array->itemsize == pccnp_itemsize_from_descr(common_descr)) {
            continue;
        }

        PyObject *cast_obj = pccnp_cast_array((PyObject *)mps[i], array, common_descr);
        if (cast_obj == NULL) goto fail;
        Py_DECREF((PyObject *)mps[i]);
        mps[i] = (PyArrayObject *)cast_obj;
    }

    *retn = (int)length;
    return mps;

fail:
    for (Py_ssize_t i = 0; i < length; i++) {
        Py_XDECREF((PyObject *)mps[i]);
    }
    PyMem_RawFree(mps);
    *retn = 0;
    return NULL;
}

static int pccnp_converter(PyObject *object, PyObject **address) {
    if (address == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy converter output is NULL");
        return NPY_FAIL;
    }
    *address = NULL;
    if (object == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is NULL");
        return NPY_FAIL;
    }
    if (pccnp_is_array_object(object)) {
        Py_INCREF(object);
        *address = object;
        return NPY_SUCCEED;
    }
    PyObject *array = pccnp_from_any(
        object,
        NULL,
        0,
        0,
        NPY_ARRAY_CARRAY,
        NULL
    );
    if (array == NULL) {
        return NPY_FAIL;
    }
    *address = array;
    return NPY_SUCCEED;
}

static PyObject *pccnp_copy(PyArrayObject *src_obj);

static PyObject *pccnp_from_array(
    PyArrayObject *arr_obj,
    PyArray_Descr *newtype,
    int flags
) {
    PyObject *arr_py = (PyObject *)arr_obj;
    PyObject *result = NULL;
    if (!pccnp_is_array_object(arr_py)) {
        Py_XDECREF(newtype);
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        Py_XDECREF(newtype);
        return NULL;
    }

    if (newtype == NULL ||
        (newtype->type_num == array->typenum &&
         pccnp_itemsize_from_descr(newtype) == array->itemsize)) {
        if (flags & NPY_ARRAY_ENSURECOPY) {
            result = pccnp_copy(arr_obj);
        } else {
            Py_INCREF(arr_py);
            result = arr_py;
        }
    } else {
        result = pccnp_cast_array(arr_py, array, newtype);
    }
    Py_XDECREF(newtype);
    return result;
}

static PyObject *pccnp_cast_to_type(
    PyArrayObject *arr_obj,
    PyArray_Descr *dtype,
    int is_f_order
) {
    (void)is_f_order;
    if (dtype == NULL) {
        PyErr_SetString(PyExc_ValueError, "dtype is NULL in PyArray_CastToType");
        return NULL;
    }
    PyObject *arr_py = (PyObject *)arr_obj;
    if (!pccnp_is_array_object(arr_py)) {
        Py_DECREF((PyObject *)dtype);
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        Py_DECREF((PyObject *)dtype);
        return NULL;
    }
    PyObject *result = pccnp_cast_array(arr_py, array, dtype);
    Py_DECREF((PyObject *)dtype);
    return result;
}

static int pccnp_fill_with_scalar(PyArrayObject *arr_obj, PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FillWithScalar requires a scalar");
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }
    char *data = (char *)array->data;
    for (size_t i = 0; i < array->element_count; i++) {
        if (pccnp_setitem(arr_obj, data + (i * array->itemsize), obj) != 0) {
            return -1;
        }
    }
    return 0;
}

static PyObject *pccnp_to_list_recursive(
    PyArrayObject *arr_obj,
    PccNpArray *array,
    char *dataptr,
    int startdim
) {
    if (startdim >= array->nd) {
        return pccnp_getitem(arr_obj, dataptr);
    }
    npy_intp n = array->dims[startdim];
    npy_intp stride = array->strides[startdim];
    PyObject *list = PyList_New((Py_ssize_t)n);
    if (list == NULL) return NULL;
    for (npy_intp i = 0; i < n; i++) {
        PyObject *item = pccnp_to_list_recursive(
            arr_obj,
            array,
            dataptr + (i * stride),
            startdim + 1
        );
        if (item == NULL) {
            Py_DECREF(list);
            return NULL;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)i, item);
    }
    return list;
}

static PyObject *pccnp_to_list(PyArrayObject *arr_obj) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    return pccnp_to_list_recursive(arr_obj, array, (char *)array->data, 0);
}

static int pccnp_has_c_order_strides(PccNpArray *array) {
    if (array->nd == 0) return 1;
    if (array->dims == NULL || array->strides == NULL) return 0;
    size_t stride = array->itemsize;
    for (int i = array->nd - 1; i >= 0; i--) {
        if (array->strides[i] != (npy_intp)stride) {
            return 0;
        }
        if (i > 0 && pccnp_mul_size(stride, (size_t)array->dims[i], &stride) != 0) {
            return 0;
        }
    }
    return 1;
}

static PyObject *pccnp_to_string(PyArrayObject *arr_obj, NPY_ORDER order) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (order == NPY_ANYORDER || order == NPY_KEEPORDER) {
        order = NPY_CORDER;
    }
    if (order == NPY_FORTRANORDER && array->nd <= 1) {
        order = NPY_CORDER;
    }
    if (order != NPY_CORDER) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub only supports C-order PyArray_ToString"
        );
        return NULL;
    }
    if (array->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub does not stringify object arrays"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        if (PyErr_Occurred() == NULL) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy C API stub does not reorder non-contiguous arrays"
            );
        }
        return NULL;
    }
    size_t byte_count = 0;
    if (pccnp_mul_size(array->element_count, array->itemsize, &byte_count) != 0) {
        return NULL;
    }
    if (byte_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array byte size exceeds Py_ssize_t");
        return NULL;
    }
    if (array->data == NULL && byte_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    const char *data = byte_count == 0 ? "" : (const char *)array->data;
    return PyBytes_FromStringAndSize(data, (Py_ssize_t)byte_count);
}

static void pccnp_byteswap_items(char *data, size_t itemsize, size_t count) {
    if (itemsize <= 1) return;
    for (size_t i = 0; i < count; i++) {
        char *item = data + (i * itemsize);
        for (size_t j = 0; j < itemsize / 2; j++) {
            char tmp = item[j];
            item[j] = item[itemsize - 1 - j];
            item[itemsize - 1 - j] = tmp;
        }
    }
}

static PyObject *pccnp_byteswap(PyArrayObject *arr_obj, npy_bool inplace) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub does not byteswap object arrays"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        if (PyErr_Occurred() == NULL) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy C API stub does not byteswap non-contiguous arrays"
            );
        }
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }

    if (inplace) {
        pccnp_byteswap_items((char *)array->data, array->itemsize, array->element_count);
        Py_INCREF((PyObject *)arr_obj);
        return (PyObject *)arr_obj;
    }

    PyObject *copy_obj = pccnp_copy(arr_obj);
    if (copy_obj == NULL) return NULL;
    PccNpArray *copy = pccnp_array_from_object((PyArrayObject *)copy_obj);
    if (copy == NULL) {
        Py_DECREF(copy_obj);
        return NULL;
    }
    pccnp_byteswap_items((char *)copy->data, copy->itemsize, copy->element_count);
    return copy_obj;
}

static PyObject *pccnp_from_string(
    char *data,
    npy_intp slen,
    PyArray_Descr *dtype,
    npy_intp num,
    char *sep
) {
    PyObject *result = NULL;

    if (dtype == NULL) {
        dtype = pccnp_descr_new_from_type(NPY_DOUBLE);
        if (dtype == NULL) {
            return NULL;
        }
    }

    if (dtype->type_num == NPY_OBJECT) {
        PyErr_SetString(PyExc_ValueError, "Cannot create an object array from a string");
        goto done;
    }

    size_t itemsize = pccnp_itemsize_from_descr(dtype);
    if (itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "zero-valued itemsize");
        goto done;
    }

    if (sep != NULL && sep[0] != '\0') {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_FromString text parsing is not implemented"
        );
        goto done;
    }

    if (slen < 0) {
        PyErr_SetString(PyExc_ValueError, "binary string length must be non-negative");
        goto done;
    }

    size_t raw_len = (size_t)slen;
    npy_intp count = 0;
    if (num < 0) {
        if (raw_len % itemsize != 0) {
            PyErr_SetString(
                PyExc_ValueError,
                "string size must be a multiple of element size"
            );
            goto done;
        }
        size_t inferred_count = raw_len / itemsize;
        if (inferred_count > (size_t)PY_SSIZE_T_MAX) {
            PyErr_SetString(PyExc_OverflowError, "array size exceeds Py_ssize_t");
            goto done;
        }
        count = (npy_intp)inferred_count;
    } else {
        count = num;
        size_t requested = 0;
        if (pccnp_mul_size((size_t)count, itemsize, &requested) != 0) {
            goto done;
        }
        if (raw_len < requested) {
            PyErr_SetString(PyExc_ValueError, "string is smaller than requested size");
            goto done;
        }
    }

    size_t byte_count = 0;
    if (pccnp_mul_size((size_t)count, itemsize, &byte_count) != 0) {
        goto done;
    }
    if (data == NULL && byte_count != 0) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromString data pointer is required");
        goto done;
    }

    npy_intp dims[1] = {count};
    result = pccnp_array_new_with_itemsize(
        1,
        dims,
        dtype->type_num,
        itemsize,
        NULL,
        1
    );
    if (result != NULL && byte_count != 0) {
        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)result);
        if (array == NULL) {
            Py_DECREF(result);
            result = NULL;
            goto done;
        }
        memcpy(array->data, data, byte_count);
    }

done:
    Py_DECREF((PyObject *)dtype);
    return result;
}

static PyObject *pccnp_from_buffer(
    PyObject *buf,
    PyArray_Descr *type,
    npy_intp count,
    npy_intp offset
) {
    PyObject *result = NULL;
    PyObject *base = NULL;
    Py_buffer view;
    memset(&view, 0, sizeof(view));
    int have_view = 0;
    int writeable = 1;

    if (buf == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromBuffer requires a buffer object");
        goto done;
    }
    if (type == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromBuffer requires a descriptor");
        goto done;
    }
    if (type->type_num == NPY_OBJECT) {
        PyErr_SetString(PyExc_ValueError, "cannot create an OBJECT array from memory buffer");
        goto done;
    }
    size_t itemsize = pccnp_itemsize_from_descr(type);
    if (itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "itemsize cannot be zero in type");
        goto done;
    }

    if (PyObject_GetBuffer(buf, &view, PyBUF_WRITABLE | PyBUF_SIMPLE) != 0) {
        writeable = 0;
        PyErr_Clear();
        if (PyObject_GetBuffer(buf, &view, PyBUF_SIMPLE) != 0) {
            goto done;
        }
    }
    have_view = 1;

    if (offset < 0 || offset > (npy_intp)view.len) {
        PyErr_SetString(
            PyExc_ValueError,
            "offset must be non-negative and no greater than buffer length"
        );
        goto done;
    }

    size_t remaining = (size_t)((npy_intp)view.len - offset);
    npy_intp n = count;
    if (n < 0) {
        if (remaining % itemsize != 0) {
            PyErr_SetString(
                PyExc_ValueError,
                "buffer size must be a multiple of element size"
            );
            goto done;
        }
        size_t inferred = remaining / itemsize;
        if (inferred > (size_t)PY_SSIZE_T_MAX) {
            PyErr_SetString(PyExc_OverflowError, "array size exceeds Py_ssize_t");
            goto done;
        }
        n = (npy_intp)inferred;
    } else {
        size_t requested = 0;
        if (pccnp_mul_size((size_t)n, itemsize, &requested) != 0) {
            goto done;
        }
        if (remaining < requested) {
            PyErr_SetString(PyExc_ValueError, "buffer is smaller than requested size");
            goto done;
        }
    }

    char *data = (char *)view.buf + offset;
    npy_intp dims[1] = {n};
    result = pccnp_array_new_with_itemsize(
        1,
        dims,
        type->type_num,
        itemsize,
        data,
        0
    );
    if (result == NULL) {
        goto done;
    }

    Py_INCREF(buf);
    base = buf;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)result);
    if (array == NULL) {
        Py_DECREF(result);
        result = NULL;
        goto done;
    }
    array->base = base;
    base = NULL;
    if (!writeable) {
        array->cleared_flags |= NPY_ARRAY_WRITEABLE;
    }

done:
    if (have_view) {
        PyBuffer_Release(&view);
    }
    Py_XDECREF(base);
    Py_XDECREF((PyObject *)type);
    return result;
}

static int pccnp_buffer_converter(PyObject *obj, PyArray_Chunk *buf) {
    if (buf == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_BufferConverter requires an output chunk");
        return NPY_FAIL;
    }

    buf->ptr = NULL;
    buf->base = NULL;
    buf->len = 0;
    buf->flags = NPY_ARRAY_BEHAVED;
    if (obj == Py_None) {
        return NPY_SUCCEED;
    }
    if (obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_BufferConverter requires a buffer object");
        return NPY_FAIL;
    }

    Py_buffer view;
    memset(&view, 0, sizeof(view));
    int writeable = 1;
    if (PyObject_GetBuffer(obj, &view, PyBUF_ANY_CONTIGUOUS | PyBUF_WRITABLE | PyBUF_SIMPLE) != 0) {
        PyErr_Clear();
        writeable = 0;
        if (PyObject_GetBuffer(obj, &view, PyBUF_ANY_CONTIGUOUS | PyBUF_SIMPLE) != 0) {
            return NPY_FAIL;
        }
    }

    buf->ptr = view.buf;
    buf->len = (npy_intp)view.len;
    buf->base = obj;
    if (!writeable) {
        buf->flags &= ~NPY_ARRAY_WRITEABLE;
    }
    PyBuffer_Release(&view);
    return NPY_SUCCEED;
}

static int pccnp_from_iter_grow(PccNpArray *array, size_t new_capacity) {
    if (array == NULL || !array->owns_data || array->nd != 1 || array->itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromIter requires a growable 1D array");
        return -1;
    }
    if (new_capacity <= array->element_count) {
        return 0;
    }
    size_t old_bytes = 0;
    size_t new_bytes = 0;
    if (pccnp_mul_size(array->element_count, array->itemsize, &old_bytes) != 0 ||
        pccnp_mul_size(new_capacity, array->itemsize, &new_bytes) != 0) {
        return -1;
    }
    void *new_data = PyMem_Realloc(array->data, new_bytes == 0 ? 1 : new_bytes);
    if (new_data == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    memset((char *)new_data + old_bytes, 0, new_bytes - old_bytes);
    array->data = new_data;
    array->dims[0] = (npy_intp)new_capacity;
    array->element_count = new_capacity;
    return 0;
}

static PyObject *pccnp_from_iter(PyObject *obj, PyArray_Descr *dtype, npy_intp count) {
    PyObject *result = NULL;
    PyObject *iter = NULL;
    PccNpArray *array = NULL;
    size_t filled = 0;

    if (obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromIter requires an iterable");
        goto done;
    }
    if (dtype == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_FromIter requires a descriptor");
        goto done;
    }
    size_t itemsize = pccnp_itemsize_from_descr(dtype);
    if (itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "Must specify length when using variable-size data-type");
        goto done;
    }

    iter = PyObject_GetIter(obj);
    if (iter == NULL) {
        goto done;
    }

    size_t capacity = 0;
    int explicit_count = count >= 0;
    if (explicit_count) {
        capacity = (size_t)count;
    } else {
        Py_ssize_t hint = PyObject_LengthHint(obj, 0);
        if (hint < 0) {
            goto done;
        }
        capacity = (size_t)hint;
    }

    npy_intp dims[1] = {(npy_intp)capacity};
    result = pccnp_array_new_with_itemsize(
        1,
        dims,
        dtype->type_num,
        itemsize,
        NULL,
        1
    );
    if (result == NULL) {
        goto done;
    }
    array = pccnp_array_from_object((PyArrayObject *)result);
    if (array == NULL) {
        Py_DECREF(result);
        result = NULL;
        goto done;
    }

    while (!explicit_count || filled < capacity) {
        PyObject *value = PyIter_Next(iter);
        if (value == NULL) {
            if (PyErr_Occurred() != NULL) {
                goto done;
            }
            break;
        }
        if (filled >= capacity) {
            size_t next_capacity = capacity < 4 ? 4 : capacity + capacity / 2 + 1;
            if (next_capacity <= filled) {
                next_capacity = filled + 1;
            }
            if (pccnp_from_iter_grow(array, next_capacity) != 0) {
                Py_DECREF(value);
                goto done;
            }
            capacity = next_capacity;
        }
        char *slot = (char *)array->data + filled * itemsize;
        if (pccnp_pack(dtype, slot, value) != 0) {
            Py_DECREF(value);
            goto done;
        }
        Py_DECREF(value);
        filled++;
    }

    if (explicit_count && filled < capacity) {
        PyErr_SetString(PyExc_ValueError, "iterator too short");
        goto done;
    }
    if (filled > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array size exceeds Py_ssize_t");
        goto done;
    }
    array->dims[0] = (npy_intp)filled;
    array->element_count = filled;

done:
    Py_XDECREF(iter);
    Py_XDECREF((PyObject *)dtype);
    if (PyErr_Occurred() != NULL) {
        Py_XDECREF(result);
        return NULL;
    }
    return result;
}

static npy_intp pccnp_multiply_list(const npy_intp *items, int n) {
    npy_intp result = 1;
    while (n-- > 0) {
        result *= *items++;
    }
    return result;
}

static int pccnp_multiply_int_list(const int *items, int n) {
    int result = 1;
    while (n-- > 0) {
        result *= *items++;
    }
    return result;
}

static npy_intp pccnp_overflow_multiply_list(const npy_intp *items, int n) {
    if (n <= 0) {
        return 1;
    }
    if (items == NULL) {
        return -1;
    }
    npy_intp result = 1;
    npy_intp max_intp = (npy_intp)(((size_t)-1) >> 1);
    for (int i = 0; i < n; i++) {
        npy_intp dim = items[i];
        if (dim == 0) {
            return 0;
        }
        if (dim < 0 || result > max_intp / dim) {
            return -1;
        }
        result *= dim;
    }
    return result;
}

