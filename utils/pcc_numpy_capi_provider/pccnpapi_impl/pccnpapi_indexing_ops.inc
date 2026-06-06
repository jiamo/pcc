/* indexing, sorting, search, where, linear algebra, and nonzero helpers.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static int pccnp_read_signed_index(PccNpArray *indices, size_t i, npy_intp *out) {
    char *data = (char *)indices->data + (i * indices->itemsize);
    switch (indices->typenum) {
        case NPY_BYTE:
            *out = (npy_intp)*(signed char *)data;
            return 0;
        case NPY_SHORT:
            *out = (npy_intp)*(short *)data;
            return 0;
        case NPY_INT:
            *out = (npy_intp)*(int *)data;
            return 0;
        case NPY_LONG:
            *out = (npy_intp)*(long *)data;
            return 0;
        case NPY_LONGLONG:
            *out = (npy_intp)*(long long *)data;
            return 0;
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy indexed helpers support signed integer index arrays only"
            );
            return -1;
    }
}

static PyObject *pccnp_take_from(
    PyArrayObject *self_obj,
    PyObject *indices_obj,
    int axis,
    PyArrayObject *out,
    NPY_CLIPMODE clipmode
) {
    if (out != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom does not support out arrays"
        );
        return NULL;
    }
    if (clipmode != NPY_RAISE) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom supports NPY_RAISE only"
        );
        return NULL;
    }

    PccNpArray *source = pccnp_array_from_object(self_obj);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_is_array_object(indices_obj)) {
        PyErr_SetString(PyExc_TypeError, "indices object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)indices_obj);
    if (indices == NULL) {
        PyErr_SetString(PyExc_TypeError, "indices object is not a pcc NumPy array");
        return NULL;
    }
    if (source->nd != 1 || indices->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom supports 1D source and 1D indices only"
        );
        return NULL;
    }
    if (axis < 0) axis += source->nd;
    if (axis != 0) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom supports axis 0 only"
        );
        return NULL;
    }
    if ((source->data == NULL && source->element_count != 0) ||
        (indices->data == NULL && indices->element_count != 0)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(source) || !pccnp_has_c_order_strides(indices)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom supports C-order arrays only"
        );
        return NULL;
    }
    if (source->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_TakeFrom does not support object arrays"
        );
        return NULL;
    }
    if (source->element_count > (size_t)PY_SSIZE_T_MAX ||
        indices->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array is too large");
        return NULL;
    }
    if (source->element_count == 0 && indices->element_count != 0) {
        PyErr_SetString(PyExc_IndexError, "cannot take from an empty array");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)indices->element_count};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        source->typenum,
        source->itemsize,
        NULL,
        1
    );
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *taken = pccnp_array_from_object((PyArrayObject *)result);
    if (taken == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    char *src = (char *)source->data;
    char *dst = (char *)taken->data;
    for (size_t i = 0; i < indices->element_count; i++) {
        npy_intp index = 0;
        if (pccnp_read_signed_index(indices, i, &index) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        if (index < 0) {
            index += (npy_intp)source->element_count;
        }
        if (index < 0 || index >= (npy_intp)source->element_count) {
            Py_DECREF(result);
            PyErr_SetString(PyExc_IndexError, "take index out of range");
            return NULL;
        }
        memcpy(
            dst + (i * taken->itemsize),
            src + ((size_t)index * source->itemsize),
            source->itemsize
        );
    }
    return result;
}

static PyObject *pccnp_put_to(
    PyArrayObject *self_obj,
    PyObject *values_obj,
    PyObject *indices_obj,
    NPY_CLIPMODE clipmode
) {
    if (clipmode != NPY_RAISE) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutTo supports NPY_RAISE only"
        );
        return NULL;
    }

    PccNpArray *dest = pccnp_array_from_object(self_obj);
    if (dest == NULL) {
        PyErr_SetString(PyExc_TypeError, "put: first argument must be a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_is_array_object(indices_obj)) {
        PyErr_SetString(PyExc_TypeError, "indices object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)indices_obj);
    if (indices == NULL) {
        PyErr_SetString(PyExc_TypeError, "indices object is not a pcc NumPy array");
        return NULL;
    }
    if (dest->nd != 1 || indices->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutTo supports 1D destination and 1D indices only"
        );
        return NULL;
    }
    if ((dest->data == NULL && dest->element_count != 0) ||
        (indices->data == NULL && indices->element_count != 0)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(dest) || !pccnp_has_c_order_strides(indices)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutTo supports C-order arrays only"
        );
        return NULL;
    }
    if (dest->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutTo does not support object arrays"
        );
        return NULL;
    }
    if (dest->element_count > (size_t)PY_SSIZE_T_MAX ||
        indices->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array is too large");
        return NULL;
    }
    if (dest->element_count == 0 && indices->element_count != 0) {
        PyErr_SetString(PyExc_IndexError, "cannot replace elements of an empty array");
        return NULL;
    }

    PccNpArray *values = NULL;
    if (pccnp_is_array_object(values_obj)) {
        values = pccnp_array_from_object((PyArrayObject *)values_obj);
        if (values == NULL) {
            PyErr_SetString(PyExc_TypeError, "values object is not a pcc NumPy array");
            return NULL;
        }
        if (values->nd != 1) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutTo supports 1D value arrays only"
            );
            return NULL;
        }
        if (values->data == NULL && values->element_count != 0) {
            PyErr_SetString(PyExc_ValueError, "values array data pointer is NULL");
            return NULL;
        }
        if (!pccnp_has_c_order_strides(values)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutTo supports C-order value arrays only"
            );
            return NULL;
        }
        if (values->typenum == NPY_OBJECT) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutTo does not support object value arrays"
            );
            return NULL;
        }
        if (values->element_count == 0) {
            Py_RETURN_NONE;
        }
    }

    char *dest_data = (char *)dest->data;
    char *values_data = values == NULL ? NULL : (char *)values->data;
    for (size_t i = 0; i < indices->element_count; i++) {
        npy_intp index = 0;
        if (pccnp_read_signed_index(indices, i, &index) != 0) {
            return NULL;
        }
        if (index < 0) {
            index += (npy_intp)dest->element_count;
        }
        if (index < 0 || index >= (npy_intp)dest->element_count) {
            PyErr_SetString(PyExc_IndexError, "put index out of range");
            return NULL;
        }
        char *dest_slot = dest_data + ((size_t)index * dest->itemsize);
        if (values != NULL) {
            size_t value_index = i % values->element_count;
            PyObject *value = pccnp_getitem(
                (PyArrayObject *)values_obj,
                values_data + (value_index * values->itemsize)
            );
            if (value == NULL) {
                return NULL;
            }
            int rc = pccnp_setitem(self_obj, dest_slot, value);
            Py_DECREF(value);
            if (rc != 0) {
                return NULL;
            }
        } else if (pccnp_setitem(self_obj, dest_slot, values_obj) != 0) {
            return NULL;
        }
    }

    Py_RETURN_NONE;
}

static PyObject *pccnp_put_mask(
    PyArrayObject *self_obj,
    PyObject *values_obj,
    PyObject *mask_obj
) {
    PccNpArray *dest = pccnp_array_from_object(self_obj);
    if (dest == NULL) {
        PyErr_SetString(PyExc_TypeError, "putmask: first argument must be a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_is_array_object(mask_obj)) {
        PyErr_SetString(PyExc_TypeError, "mask object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *mask = pccnp_array_from_object((PyArrayObject *)mask_obj);
    if (mask == NULL) {
        PyErr_SetString(PyExc_TypeError, "mask object is not a pcc NumPy array");
        return NULL;
    }
    if (dest->nd != 1 || mask->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutMask supports 1D destination and 1D mask arrays only"
        );
        return NULL;
    }
    if (dest->element_count != mask->element_count) {
        PyErr_SetString(
            PyExc_ValueError,
            "putmask: mask and data must be the same size"
        );
        return NULL;
    }
    if ((dest->data == NULL && dest->element_count != 0) ||
        (mask->data == NULL && mask->element_count != 0)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(dest) || !pccnp_has_c_order_strides(mask)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutMask supports C-order arrays only"
        );
        return NULL;
    }
    if (mask->typenum != NPY_BOOL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutMask supports boolean mask arrays only"
        );
        return NULL;
    }
    if (dest->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_PutMask does not support object arrays"
        );
        return NULL;
    }
    if (dest->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array is too large");
        return NULL;
    }

    PccNpArray *values = NULL;
    if (pccnp_is_array_object(values_obj)) {
        values = pccnp_array_from_object((PyArrayObject *)values_obj);
        if (values == NULL) {
            PyErr_SetString(PyExc_TypeError, "values object is not a pcc NumPy array");
            return NULL;
        }
        if (values->nd != 1) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutMask supports 1D value arrays only"
            );
            return NULL;
        }
        if (values->data == NULL && values->element_count != 0) {
            PyErr_SetString(PyExc_ValueError, "values array data pointer is NULL");
            return NULL;
        }
        if (!pccnp_has_c_order_strides(values)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutMask supports C-order value arrays only"
            );
            return NULL;
        }
        if (values->typenum == NPY_OBJECT) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_PutMask does not support object value arrays"
            );
            return NULL;
        }
        if (values->element_count == 0) {
            Py_RETURN_NONE;
        }
    }

    char *dest_data = (char *)dest->data;
    npy_bool *mask_data = (npy_bool *)mask->data;
    char *values_data = values == NULL ? NULL : (char *)values->data;
    for (size_t i = 0; i < dest->element_count; i++) {
        if (!mask_data[i]) {
            continue;
        }
        char *dest_slot = dest_data + (i * dest->itemsize);
        if (values != NULL) {
            size_t value_index = i % values->element_count;
            PyObject *value = pccnp_getitem(
                (PyArrayObject *)values_obj,
                values_data + (value_index * values->itemsize)
            );
            if (value == NULL) {
                return NULL;
            }
            int rc = pccnp_setitem(self_obj, dest_slot, value);
            Py_DECREF(value);
            if (rc != 0) {
                return NULL;
            }
        } else if (pccnp_setitem(self_obj, dest_slot, values_obj) != 0) {
            return NULL;
        }
    }

    Py_RETURN_NONE;
}

static PyObject *pccnp_repeat(PyArrayObject *src_obj, PyObject *repeats_obj, int axis) {
    PccNpArray *src = pccnp_array_from_object(src_obj);
    if (src == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (src->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Repeat supports 1D source arrays only"
        );
        return NULL;
    }
    if (axis < 0) axis += src->nd;
    if (axis != 0) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Repeat supports axis 0 only"
        );
        return NULL;
    }
    if (src->data == NULL && src->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(src)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Repeat supports C-order arrays only"
        );
        return NULL;
    }
    if (src->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Repeat does not support object arrays"
        );
        return NULL;
    }
    if (src->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array is too large");
        return NULL;
    }

    int broadcast = 0;
    npy_intp scalar_count = 0;
    PccNpArray *counts = NULL;
    if (pccnp_is_array_object(repeats_obj)) {
        counts = pccnp_array_from_object((PyArrayObject *)repeats_obj);
        if (counts == NULL) {
            PyErr_SetString(PyExc_TypeError, "repeats object is not a pcc NumPy array");
            return NULL;
        }
        if (counts->nd > 1) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Repeat supports scalar or 1D repeats only"
            );
            return NULL;
        }
        if (counts->data == NULL && counts->element_count != 0) {
            PyErr_SetString(PyExc_ValueError, "repeats array data pointer is NULL");
            return NULL;
        }
        if (!pccnp_has_c_order_strides(counts)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Repeat supports C-order repeats only"
            );
            return NULL;
        }
        if (counts->element_count == 1) {
            broadcast = 1;
            if (pccnp_read_signed_index(counts, 0, &scalar_count) != 0) {
                return NULL;
            }
        } else if (counts->element_count != src->element_count) {
            PyErr_SetString(
                PyExc_ValueError,
                "repeat counts do not match source length"
            );
            return NULL;
        }
    } else {
        long value = PyLong_AsLong(repeats_obj);
        if (PyErr_Occurred() != NULL) {
            return NULL;
        }
        scalar_count = (npy_intp)value;
        broadcast = 1;
    }

    size_t total = 0;
    if (broadcast) {
        if (scalar_count < 0) {
            PyErr_SetString(PyExc_ValueError, "repeats may not contain negative values.");
            return NULL;
        }
        if (scalar_count == 0) {
            total = 0;
        } else if ((size_t)scalar_count > (size_t)PY_SSIZE_T_MAX ||
            src->element_count > (size_t)PY_SSIZE_T_MAX / (size_t)scalar_count) {
            PyErr_SetString(PyExc_OverflowError, "repeated array is too large");
            return NULL;
        } else {
            total = src->element_count * (size_t)scalar_count;
        }
    } else {
        for (size_t i = 0; i < src->element_count; i++) {
            npy_intp count = 0;
            if (pccnp_read_signed_index(counts, i, &count) != 0) {
                return NULL;
            }
            if (count < 0) {
                PyErr_SetString(PyExc_ValueError, "repeats may not contain negative values.");
                return NULL;
            }
            if ((size_t)count > (size_t)PY_SSIZE_T_MAX ||
                total > (size_t)PY_SSIZE_T_MAX - (size_t)count) {
                PyErr_SetString(PyExc_OverflowError, "repeated array is too large");
                return NULL;
            }
            total += (size_t)count;
        }
    }

    npy_intp dims[1] = {(npy_intp)total};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        src->typenum,
        src->itemsize,
        NULL,
        1
    );
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *repeated = pccnp_array_from_object((PyArrayObject *)result);
    if (repeated == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    char *src_data = (char *)src->data;
    char *dst_data = (char *)repeated->data;
    size_t out_index = 0;
    for (size_t i = 0; i < src->element_count; i++) {
        npy_intp count = scalar_count;
        if (!broadcast && pccnp_read_signed_index(counts, i, &count) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        for (npy_intp j = 0; j < count; j++) {
            memcpy(
                dst_data + (out_index * repeated->itemsize),
                src_data + (i * src->itemsize),
                src->itemsize
            );
            out_index++;
        }
    }
    return result;
}

static PyObject *pccnp_choose(
    PyArrayObject *indices_obj,
    PyObject *choices_obj,
    PyArrayObject *out_obj,
    NPY_CLIPMODE clipmode
) {
    if (out_obj != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Choose does not support out arrays"
        );
        return NULL;
    }
    if (clipmode != NPY_RAISE) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Choose supports NPY_RAISE only"
        );
        return NULL;
    }

    PccNpArray *indices = pccnp_array_from_object(indices_obj);
    if (indices == NULL) {
        PyErr_SetString(PyExc_TypeError, "choice index object is not a pcc NumPy array");
        return NULL;
    }
    if (indices->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Choose supports 1D index arrays only"
        );
        return NULL;
    }
    if (indices->data == NULL && indices->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "index array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(indices)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Choose supports C-order index arrays only"
        );
        return NULL;
    }
    if (indices->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "array is too large");
        return NULL;
    }
    if (!PySequence_Check(choices_obj)) {
        PyErr_SetString(PyExc_TypeError, "choices object must be a sequence");
        return NULL;
    }
    Py_ssize_t choice_count = PySequence_Size(choices_obj);
    if (choice_count <= 0) {
        if (choice_count == 0) {
            PyErr_SetString(PyExc_ValueError, "choices sequence must not be empty");
        }
        return NULL;
    }

    PyObject **choice_refs = (PyObject **)PyMem_Calloc(
        (size_t)choice_count,
        sizeof(PyObject *)
    );
    PccNpArray **choices = (PccNpArray **)PyMem_Calloc(
        (size_t)choice_count,
        sizeof(PccNpArray *)
    );
    if (choice_refs == NULL || choices == NULL) {
        PyMem_Free(choice_refs);
        PyMem_Free(choices);
        PyErr_NoMemory();
        return NULL;
    }

    int typenum = -1;
    size_t itemsize = 0;
    for (Py_ssize_t i = 0; i < choice_count; i++) {
        PyObject *choice_obj = PySequence_GetItem(choices_obj, i);
        if (choice_obj == NULL) {
            goto fail;
        }
        choice_refs[i] = choice_obj;
        if (!pccnp_is_array_object(choice_obj)) {
            PyErr_SetString(PyExc_TypeError, "choice item is not a pcc NumPy array");
            goto fail;
        }
        PccNpArray *choice = pccnp_array_from_object((PyArrayObject *)choice_obj);
        if (choice == NULL) {
            PyErr_SetString(PyExc_TypeError, "choice item is not a pcc NumPy array");
            goto fail;
        }
        if (choice->nd != 1 || choice->element_count != indices->element_count) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Choose supports same-length 1D choices only"
            );
            goto fail;
        }
        if (choice->data == NULL && choice->element_count != 0) {
            PyErr_SetString(PyExc_ValueError, "choice array data pointer is NULL");
            goto fail;
        }
        if (!pccnp_has_c_order_strides(choice)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Choose supports C-order choices only"
            );
            goto fail;
        }
        if (choice->typenum == NPY_OBJECT) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Choose does not support object arrays"
            );
            goto fail;
        }
        if (i == 0) {
            typenum = choice->typenum;
            itemsize = choice->itemsize;
        } else if (choice->typenum != typenum || choice->itemsize != itemsize) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Choose requires matching choice dtypes"
            );
            goto fail;
        }
        choices[i] = choice;
    }

    npy_intp dims[1] = {(npy_intp)indices->element_count};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        typenum,
        itemsize,
        NULL,
        1
    );
    if (result == NULL) {
        goto fail;
    }
    PccNpArray *chosen = pccnp_array_from_object((PyArrayObject *)result);
    if (chosen == NULL) {
        Py_DECREF(result);
        goto fail;
    }

    char *dst = (char *)chosen->data;
    for (size_t i = 0; i < indices->element_count; i++) {
        npy_intp choice_index = 0;
        if (pccnp_read_signed_index(indices, i, &choice_index) != 0) {
            Py_DECREF(result);
            goto fail;
        }
        if (choice_index < 0 || choice_index >= (npy_intp)choice_count) {
            Py_DECREF(result);
            PyErr_SetString(PyExc_ValueError, "invalid entry in choice array");
            goto fail;
        }
        PccNpArray *choice = choices[choice_index];
        memcpy(
            dst + (i * chosen->itemsize),
            (char *)choice->data + (i * choice->itemsize),
            chosen->itemsize
        );
    }

    for (Py_ssize_t i = 0; i < choice_count; i++) {
        Py_XDECREF(choice_refs[i]);
    }
    PyMem_Free(choice_refs);
    PyMem_Free(choices);
    return result;

fail:
    for (Py_ssize_t i = 0; i < choice_count; i++) {
        Py_XDECREF(choice_refs[i]);
    }
    PyMem_Free(choice_refs);
    PyMem_Free(choices);
    return NULL;
}

static PyObject *pccnp_concatenate(PyObject *op, int axis) {
    if (op == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Concatenate requires a sequence");
        return NULL;
    }
    Py_ssize_t length = PySequence_Length(op);
    if (length < 0) {
        return NULL;
    }
    if (length == 0) {
        PyErr_SetString(PyExc_ValueError, "need at least one array to concatenate");
        return NULL;
    }

    PyObject **refs = (PyObject **)PyMem_Calloc((size_t)length, sizeof(PyObject *));
    PccNpArray **arrays = (PccNpArray **)PyMem_Calloc((size_t)length, sizeof(PccNpArray *));
    npy_intp *out_dims = NULL;
    PyObject *result = NULL;
    if (refs == NULL || arrays == NULL) {
        PyMem_Free(refs);
        PyMem_Free(arrays);
        PyErr_NoMemory();
        return NULL;
    }

    int nd = -1;
    int typenum = NPY_NOTYPE;
    size_t itemsize = 0;
    for (Py_ssize_t i = 0; i < length; i++) {
        PyObject *item = PySequence_GetItem(op, i);
        if (item == NULL) goto fail;
        if (pccnp_is_array_object(item)) {
            refs[i] = item;
        } else {
            refs[i] = pccnp_from_any(item, NULL, 0, 0, NPY_ARRAY_CARRAY, NULL);
            Py_DECREF(item);
            if (refs[i] == NULL) goto fail;
        }

        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)refs[i]);
        if (array == NULL) {
            PyErr_SetString(PyExc_TypeError, "concatenate item is not a pcc NumPy array");
            goto fail;
        }
        if (array->nd <= 0) {
            PyErr_SetString(
                PyExc_ValueError,
                "pcc NumPy PyArray_Concatenate requires arrays with at least one dimension"
            );
            goto fail;
        }
        if (array->data == NULL && array->element_count != 0) {
            PyErr_SetString(PyExc_ValueError, "concatenate array data pointer is NULL");
            goto fail;
        }
        if (!pccnp_has_c_order_strides(array)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Concatenate supports C-order arrays only"
            );
            goto fail;
        }
        if (array->typenum == NPY_OBJECT) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Concatenate does not support object arrays"
            );
            goto fail;
        }

        if (i == 0) {
            nd = array->nd;
            if (axis < 0) axis += nd;
            if (axis < 0 || axis >= nd) {
                PyErr_SetString(PyExc_ValueError, "concatenate axis is out of bounds");
                goto fail;
            }
            typenum = array->typenum;
            itemsize = array->itemsize;
            out_dims = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
            if (out_dims == NULL) {
                PyErr_NoMemory();
                goto fail;
            }
            for (int dim = 0; dim < nd; dim++) {
                out_dims[dim] = array->dims[dim];
            }
            out_dims[axis] = 0;
        } else if (array->nd != nd) {
            PyErr_SetString(
                PyExc_ValueError,
                "all the input arrays must have same number of dimensions"
            );
            goto fail;
        } else if (array->typenum != typenum || array->itemsize != itemsize) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Concatenate requires matching dtypes"
            );
            goto fail;
        }

        for (int dim = 0; dim < nd; dim++) {
            if (dim == axis) continue;
            if (array->dims[dim] != out_dims[dim]) {
                PyErr_SetString(
                    PyExc_ValueError,
                    "all the input array dimensions except for the concatenation axis must match"
                );
                goto fail;
            }
        }
        if (array->dims[axis] < 0 ||
            out_dims[axis] > (npy_intp)(PY_SSIZE_T_MAX - array->dims[axis])) {
            PyErr_SetString(PyExc_OverflowError, "concatenated array is too large");
            goto fail;
        }
        out_dims[axis] += array->dims[axis];
        arrays[i] = array;
    }

    result = pccnp_array_new_with_itemsize(nd, out_dims, typenum, itemsize, NULL, 1);
    if (result == NULL) goto fail;
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL) goto fail;

    size_t inner = 1;
    for (int dim = axis + 1; dim < nd; dim++) {
        if (pccnp_mul_size(inner, (size_t)out_dims[dim], &inner) != 0) goto fail;
    }
    size_t outer = 1;
    for (int dim = 0; dim < axis; dim++) {
        if (pccnp_mul_size(outer, (size_t)out_dims[dim], &outer) != 0) goto fail;
    }
    size_t inner_bytes = 0;
    if (pccnp_mul_size(inner, itemsize, &inner_bytes) != 0) goto fail;
    size_t out_axis_bytes = 0;
    if (pccnp_mul_size((size_t)out_dims[axis], inner_bytes, &out_axis_bytes) != 0) {
        goto fail;
    }

    char *dst = (char *)out->data;
    for (size_t outer_index = 0; outer_index < outer; outer_index++) {
        size_t dst_axis_offset = 0;
        for (Py_ssize_t i = 0; i < length; i++) {
            PccNpArray *array = arrays[i];
            size_t src_axis_bytes = 0;
            if (pccnp_mul_size((size_t)array->dims[axis], inner_bytes, &src_axis_bytes) != 0) {
                goto fail;
            }
            memcpy(
                dst + (outer_index * out_axis_bytes) + dst_axis_offset,
                ((char *)array->data) + (outer_index * src_axis_bytes),
                src_axis_bytes
            );
            dst_axis_offset += src_axis_bytes;
        }
    }

    for (Py_ssize_t i = 0; i < length; i++) {
        Py_XDECREF(refs[i]);
    }
    PyMem_Free(out_dims);
    PyMem_Free(arrays);
    PyMem_Free(refs);
    return result;

fail:
    Py_XDECREF(result);
    for (Py_ssize_t i = 0; i < length; i++) {
        Py_XDECREF(refs[i]);
    }
    PyMem_Free(out_dims);
    PyMem_Free(arrays);
    PyMem_Free(refs);
    return NULL;
}

static int pccnp_store_real_from_double(PccNpArray *array, size_t index, double value) {
    if (array == NULL || array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }
    char *slot = ((char *)array->data) + (index * array->itemsize);
    switch (array->typenum) {
        case NPY_BOOL:
            *(unsigned char *)slot = value != 0.0;
            return 0;
        case NPY_BYTE:
            *(signed char *)slot = (signed char)value;
            return 0;
        case NPY_UBYTE:
            *(unsigned char *)slot = (unsigned char)value;
            return 0;
        case NPY_SHORT:
            *(short *)slot = (short)value;
            return 0;
        case NPY_USHORT:
            *(unsigned short *)slot = (unsigned short)value;
            return 0;
        case NPY_INT:
            *(int *)slot = (int)value;
            return 0;
        case NPY_UINT:
            *(unsigned int *)slot = (unsigned int)value;
            return 0;
        case NPY_LONG:
            *(long *)slot = (long)value;
            return 0;
        case NPY_ULONG:
            *(unsigned long *)slot = (unsigned long)value;
            return 0;
        case NPY_LONGLONG:
            *(long long *)slot = (long long)value;
            return 0;
        case NPY_ULONGLONG:
            *(unsigned long long *)slot = (unsigned long long)value;
            return 0;
        case NPY_FLOAT:
            *(float *)slot = (float)value;
            return 0;
        case NPY_DOUBLE:
            *(double *)slot = value;
            return 0;
        case NPY_LONGDOUBLE:
            *(long double *)slot = (long double)value;
            return 0;
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Arange supports real built-in dtypes only"
            );
            return -1;
    }
}

static PyObject *pccnp_arange(double start, double stop, double step, int typenum) {
    if (step == 0.0) {
        PyErr_SetString(PyExc_ValueError, "arange: step must not be zero");
        return NULL;
    }
    if (typenum == NPY_OBJECT || typenum == NPY_STRING ||
        typenum == NPY_CFLOAT || typenum == NPY_CDOUBLE ||
        typenum == NPY_CLONGDOUBLE || pccnp_itemsize(typenum) == 0) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Arange supports real built-in dtypes only"
        );
        return NULL;
    }

    size_t count = 0;
    double value = start;
    if (step > 0.0) {
        while (value < stop) {
            if (count > (size_t)PY_SSIZE_T_MAX) {
                PyErr_SetString(PyExc_OverflowError, "arange result is too large");
                return NULL;
            }
            double next = value + step;
            if (next <= value) {
                PyErr_SetString(PyExc_OverflowError, "arange step does not advance");
                return NULL;
            }
            count++;
            value = next;
        }
    } else {
        while (value > stop) {
            if (count > (size_t)PY_SSIZE_T_MAX) {
                PyErr_SetString(PyExc_OverflowError, "arange result is too large");
                return NULL;
            }
            double next = value + step;
            if (next >= value) {
                PyErr_SetString(PyExc_OverflowError, "arange step does not advance");
                return NULL;
            }
            count++;
            value = next;
        }
    }

    npy_intp dims[1] = {(npy_intp)count};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        typenum,
        pccnp_itemsize(typenum),
        NULL,
        1
    );
    if (result == NULL) return NULL;
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)result);
    if (array == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    value = start;
    for (size_t i = 0; i < count; i++) {
        if (pccnp_store_real_from_double(array, i, value) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        value += step;
    }
    return result;
}

static int pccnp_scalar_to_double(PyObject *obj, const char *name, double *out) {
    if (obj == NULL || out == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ArangeObj requires scalar bounds");
        return -1;
    }
    if (PyBool_Check(obj) || PyLong_Check(obj)) {
        long long value = PyLong_AsLongLong(obj);
        if (PyErr_Occurred() != NULL) return -1;
        *out = (double)value;
        return 0;
    }
    if (PyFloat_Check(obj)) {
        double value = PyFloat_AsDouble(obj);
        if (PyErr_Occurred() != NULL) return -1;
        *out = value;
        return 0;
    }
    PyErr_Format(
        PyExc_TypeError,
        "PyArray_ArangeObj %s must be an int or float scalar",
        name == NULL ? "argument" : name
    );
    return -1;
}

static int pccnp_arange_obj_inferred_type(
    PyObject *start,
    PyObject *stop,
    PyObject *step,
    PyArray_Descr *dtype
) {
    if (dtype != NULL) {
        if (pccnp_descr_for_type(dtype->type_num) == NULL) {
            pccnp_raise_unsupported();
            return NPY_NOTYPE;
        }
        return dtype->type_num;
    }
    if (PyFloat_Check(start) || PyFloat_Check(stop) ||
        (step != NULL && PyFloat_Check(step))) {
        return NPY_DOUBLE;
    }
    return NPY_LONG;
}

static PyObject *pccnp_arange_obj(
    PyObject *start,
    PyObject *stop,
    PyObject *step,
    PyArray_Descr *dtype
) {
    if (start == NULL || stop == NULL) {
        PyErr_SetString(
            PyExc_ValueError,
            "PyArray_ArangeObj requires start and stop scalars"
        );
        return NULL;
    }
    int typenum = pccnp_arange_obj_inferred_type(start, stop, step, dtype);
    if (typenum == NPY_NOTYPE) {
        return NULL;
    }
    double start_value = 0.0;
    double stop_value = 0.0;
    double step_value = 1.0;
    if (pccnp_scalar_to_double(start, "start", &start_value) != 0 ||
        pccnp_scalar_to_double(stop, "stop", &stop_value) != 0) {
        return NULL;
    }
    if (step != NULL &&
        pccnp_scalar_to_double(step, "step", &step_value) != 0) {
        return NULL;
    }
    return pccnp_arange(start_value, stop_value, step_value, typenum);
}

static int pccnp_compare_elements(
    PccNpArray *array,
    const unsigned char *lhs,
    const unsigned char *rhs,
    int *out
) {
#define PCCNP_COMPARE_TYPED(type) \
    do { \
        type left = *(const type *)lhs; \
        type right = *(const type *)rhs; \
        *out = (left > right) - (left < right); \
        return 0; \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
        case NPY_UBYTE:
            PCCNP_COMPARE_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_COMPARE_TYPED(signed char);
        case NPY_SHORT:
            PCCNP_COMPARE_TYPED(short);
        case NPY_USHORT:
            PCCNP_COMPARE_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_COMPARE_TYPED(int);
        case NPY_UINT:
            PCCNP_COMPARE_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_COMPARE_TYPED(long);
        case NPY_ULONG:
            PCCNP_COMPARE_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_COMPARE_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_COMPARE_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_COMPARE_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_COMPARE_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_COMPARE_TYPED(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Sort supports real numeric arrays only"
            );
            return -1;
    }

#undef PCCNP_COMPARE_TYPED
}

static int pccnp_element_is_nonzero(
    PccNpArray *array,
    const unsigned char *value,
    int *out
) {
#define PCCNP_NONZERO_TYPED(type) \
    do { \
        *out = (*(const type *)value) != (type)0; \
        return 0; \
    } while (0)

    switch (array->typenum) {
        case NPY_BOOL:
        case NPY_UBYTE:
            PCCNP_NONZERO_TYPED(unsigned char);
        case NPY_BYTE:
            PCCNP_NONZERO_TYPED(signed char);
        case NPY_SHORT:
            PCCNP_NONZERO_TYPED(short);
        case NPY_USHORT:
            PCCNP_NONZERO_TYPED(unsigned short);
        case NPY_INT:
            PCCNP_NONZERO_TYPED(int);
        case NPY_UINT:
            PCCNP_NONZERO_TYPED(unsigned int);
        case NPY_LONG:
            PCCNP_NONZERO_TYPED(long);
        case NPY_ULONG:
            PCCNP_NONZERO_TYPED(unsigned long);
        case NPY_LONGLONG:
            PCCNP_NONZERO_TYPED(long long);
        case NPY_ULONGLONG:
            PCCNP_NONZERO_TYPED(unsigned long long);
        case NPY_FLOAT:
            PCCNP_NONZERO_TYPED(float);
        case NPY_DOUBLE:
            PCCNP_NONZERO_TYPED(double);
        case NPY_LONGDOUBLE:
            PCCNP_NONZERO_TYPED(long double);
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_Nonzero supports real numeric arrays only"
            );
            return -1;
    }

#undef PCCNP_NONZERO_TYPED
}

static int pccnp_sort(PyArrayObject *op, int axis, NPY_SORTKIND flags) {
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Sort supports axis 0 only");
        return -1;
    }
    if (flags != NPY_QUICKSORT && flags != NPY_STABLESORT && flags != NPY_SORT_DEFAULT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Sort supports default/stable sort only"
        );
        return -1;
    }

    PccNpArray *array = pccnp_array_from_object(op);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Sort supports 1D arrays only");
        return -1;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Sort supports C-order arrays only");
        return -1;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Sort does not support object or complex arrays"
        );
        return -1;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return -1;
    }
    if (array->element_count <= 1) {
        return 0;
    }

    unsigned char *scratch = (unsigned char *)PyMem_Malloc(array->itemsize);
    if (scratch == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    unsigned char *data = (unsigned char *)array->data;
    for (size_t i = 1; i < array->element_count; i++) {
        unsigned char *key_slot = data + (i * array->itemsize);
        memcpy(scratch, key_slot, array->itemsize);
        size_t j = i;
        while (j > 0) {
            unsigned char *prev = data + ((j - 1) * array->itemsize);
            int cmp = 0;
            if (pccnp_compare_elements(array, prev, scratch, &cmp) != 0) {
                PyMem_Free(scratch);
                return -1;
            }
            if (cmp <= 0) break;
            for (size_t b = array->itemsize; b > 0; b--) {
                prev[array->itemsize + b - 1] = prev[b - 1];
            }
            j--;
        }
        memcpy(data + (j * array->itemsize), scratch, array->itemsize);
    }
    PyMem_Free(scratch);
    return 0;
}

static PyObject *pccnp_arg_sort(PyArrayObject *op, int axis, NPY_SORTKIND flags) {
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgSort supports axis 0 only");
        return NULL;
    }
    if (flags != NPY_QUICKSORT && flags != NPY_STABLESORT && flags != NPY_SORT_DEFAULT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_ArgSort supports default/stable sort only"
        );
        return NULL;
    }

    PccNpArray *array = pccnp_array_from_object(op);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgSort supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_ArgSort supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_ArgSort does not support object or complex arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "argsort index array is too large");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, NPY_LONG, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)result);
    if (indices == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    long *idx_data = (long *)indices->data;
    for (size_t i = 0; i < array->element_count; i++) {
        idx_data[i] = (long)i;
    }

    unsigned char *data = (unsigned char *)array->data;
    for (size_t i = 1; i < array->element_count; i++) {
        long key = idx_data[i];
        size_t j = i;
        while (j > 0) {
            int cmp = 0;
            if (pccnp_compare_elements(
                    array,
                    data + ((size_t)idx_data[j - 1] * array->itemsize),
                    data + ((size_t)key * array->itemsize),
                    &cmp
                ) != 0) {
                Py_DECREF(result);
                return NULL;
            }
            if (cmp <= 0) break;
            idx_data[j] = idx_data[j - 1];
            j--;
        }
        idx_data[j] = key;
    }
    return result;
}

static int pccnp_validate_partition_kth(PccNpArray *source, PyArrayObject *ktharray) {
    PccNpArray *kth = pccnp_array_from_object(ktharray);
    if (kth == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Partition kth must be a pcc NumPy array");
        return -1;
    }
    if (kth->nd != 0 && kth->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy partition supports 0D or 1D kth arrays only");
        return -1;
    }
    if (!pccnp_has_c_order_strides(kth)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy partition supports C-order kth arrays only");
        return -1;
    }
    if (kth->element_count != 0 && kth->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "kth data pointer is required");
        return -1;
    }
    if (source->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "partition source array is too large");
        return -1;
    }
    npy_intp source_len = (npy_intp)source->element_count;
    size_t kth_count = kth->nd == 0 ? 1 : kth->element_count;
    if (kth->nd == 0 && kth->element_count != 1) {
        PyErr_SetString(PyExc_ValueError, "0D kth array must contain exactly one element");
        return -1;
    }
    for (size_t i = 0; i < kth_count; i++) {
        npy_intp raw_kth = 0;
        if (pccnp_read_signed_index(kth, i, &raw_kth) != 0) {
            return -1;
        }
        npy_intp normalized = raw_kth;
        if (normalized < 0) {
            normalized += source_len;
        }
        if (normalized < 0 || normalized >= source_len) {
            PyErr_SetString(PyExc_ValueError, "kth out of bounds");
            return -1;
        }
    }
    return 0;
}

static int pccnp_partition(PyArrayObject *op, PyArrayObject *ktharray, int axis, NPY_SELECTKIND which) {
    if (which != NPY_INTROSELECT) {
        PyErr_SetString(PyExc_ValueError, "not a valid partition kind");
        return -1;
    }
    PccNpArray *source = pccnp_array_from_object(op);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (pccnp_validate_partition_kth(source, ktharray) != 0) {
        return -1;
    }
    return pccnp_sort(op, axis, NPY_QUICKSORT);
}

static PyObject *pccnp_arg_partition(
    PyArrayObject *op,
    PyArrayObject *ktharray,
    int axis,
    NPY_SELECTKIND which
) {
    if (which != NPY_INTROSELECT) {
        PyErr_SetString(PyExc_ValueError, "not a valid partition kind");
        return NULL;
    }
    PccNpArray *source = pccnp_array_from_object(op);
    if (source == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (pccnp_validate_partition_kth(source, ktharray) != 0) {
        return NULL;
    }
    return pccnp_arg_sort(op, axis, NPY_QUICKSORT);
}

static int pccnp_lexsort_compare_indices(
    PccNpArray **keys,
    Py_ssize_t key_count,
    long lhs,
    long rhs,
    int *out
) {
    for (Py_ssize_t pos = key_count; pos > 0; pos--) {
        PccNpArray *key = keys[pos - 1];
        unsigned char *data = (unsigned char *)key->data;
        int cmp = 0;
        if (pccnp_compare_elements(
                key,
                data + ((size_t)lhs * key->itemsize),
                data + ((size_t)rhs * key->itemsize),
                &cmp
            ) != 0) {
            return -1;
        }
        if (cmp != 0) {
            *out = cmp;
            return 0;
        }
    }
    *out = 0;
    return 0;
}

static PyObject *pccnp_lex_sort(PyObject *sort_keys, int axis) {
    if (axis != 0 && axis != -1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_LexSort supports axis 0 only");
        return NULL;
    }

    PyObject *seq = PySequence_Fast(sort_keys, "PyArray_LexSort expects a sequence of keys");
    if (seq == NULL) {
        return NULL;
    }
    Py_ssize_t key_count = PySequence_Fast_GET_SIZE(seq);
    if (key_count <= 0) {
        Py_DECREF(seq);
        PyErr_SetString(PyExc_ValueError, "PyArray_LexSort requires at least one key");
        return NULL;
    }
    PccNpArray **keys = (PccNpArray **)PyMem_Malloc((size_t)key_count * sizeof(PccNpArray *));
    if (keys == NULL) {
        Py_DECREF(seq);
        PyErr_NoMemory();
        return NULL;
    }

    size_t element_count = 0;
    int have_element_count = 0;
    for (Py_ssize_t i = 0; i < key_count; i++) {
        PyObject *item = PySequence_Fast_GET_ITEM(seq, i);
        if (!pccnp_is_array_object(item)) {
            PyErr_SetString(PyExc_TypeError, "PyArray_LexSort expects pcc NumPy array keys");
            goto fail;
        }
        PccNpArray *key = pccnp_array_from_object((PyArrayObject *)item);
        if (key == NULL) {
            PyErr_SetString(PyExc_TypeError, "PyArray_LexSort expects pcc NumPy array keys");
            goto fail;
        }
        if (key->nd != 1) {
            PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_LexSort supports 1D keys only");
            goto fail;
        }
        if (!pccnp_has_c_order_strides(key)) {
            PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_LexSort supports C-order keys only");
            goto fail;
        }
        if (key->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(key->typenum)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_LexSort does not support object or complex keys"
            );
            goto fail;
        }
        if (key->element_count != 0 && key->data == NULL) {
            PyErr_SetString(PyExc_ValueError, "array data pointer is required");
            goto fail;
        }
        if (key->element_count > (size_t)LONG_MAX) {
            PyErr_SetString(PyExc_OverflowError, "lexsort index array is too large");
            goto fail;
        }
        if (!have_element_count) {
            element_count = key->element_count;
            have_element_count = 1;
        } else if (key->element_count != element_count) {
            PyErr_SetString(PyExc_ValueError, "PyArray_LexSort key length mismatch");
            goto fail;
        }
        keys[i] = key;
    }

    npy_intp dims[1] = {(npy_intp)element_count};
    PyObject *result = pccnp_array_new_common(1, dims, NPY_LONG, NULL, 1);
    if (result == NULL) {
        goto fail;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)result);
    if (indices == NULL) {
        Py_DECREF(result);
        goto fail;
    }
    long *idx_data = (long *)indices->data;
    for (size_t i = 0; i < element_count; i++) {
        idx_data[i] = (long)i;
    }

    for (size_t i = 1; i < element_count; i++) {
        long key = idx_data[i];
        size_t j = i;
        while (j > 0) {
            int cmp = 0;
            if (pccnp_lexsort_compare_indices(
                    keys,
                    key_count,
                    idx_data[j - 1],
                    key,
                    &cmp
                ) != 0) {
                Py_DECREF(result);
                goto fail;
            }
            if (cmp <= 0) break;
            idx_data[j] = idx_data[j - 1];
            j--;
        }
        idx_data[j] = key;
    }

    PyMem_Free(keys);
    Py_DECREF(seq);
    return result;

fail:
    PyMem_Free(keys);
    Py_DECREF(seq);
    return NULL;
}

static int pccnp_element_as_long_double(
    PccNpArray *array,
    size_t index,
    long double *out
) {
    unsigned char *slot = (unsigned char *)array->data + (index * array->itemsize);
    switch (array->typenum) {
        case NPY_BOOL:
        case NPY_UBYTE:
            *out = (long double)*(unsigned char *)slot;
            return 0;
        case NPY_BYTE:
            *out = (long double)*(signed char *)slot;
            return 0;
        case NPY_SHORT:
            *out = (long double)*(short *)slot;
            return 0;
        case NPY_USHORT:
            *out = (long double)*(unsigned short *)slot;
            return 0;
        case NPY_INT:
            *out = (long double)*(int *)slot;
            return 0;
        case NPY_UINT:
            *out = (long double)*(unsigned int *)slot;
            return 0;
        case NPY_LONG:
            *out = (long double)*(long *)slot;
            return 0;
        case NPY_ULONG:
            *out = (long double)*(unsigned long *)slot;
            return 0;
        case NPY_LONGLONG:
            *out = (long double)*(long long *)slot;
            return 0;
        case NPY_ULONGLONG:
            *out = (long double)*(unsigned long long *)slot;
            return 0;
        case NPY_FLOAT:
            *out = (long double)*(float *)slot;
            return 0;
        case NPY_DOUBLE:
            *out = (long double)*(double *)slot;
            return 0;
        case NPY_LONGDOUBLE:
            *out = *(long double *)slot;
            return 0;
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_InnerProduct supports real numeric arrays only"
            );
            return -1;
    }
}

static int pccnp_typenum_is_unsigned_or_bool(int typenum) {
    return typenum == NPY_BOOL || PyTypeNum_ISUNSIGNED(typenum);
}

static int pccnp_store_long_double(
    PccNpArray *array,
    size_t index,
    long double value
) {
    unsigned char *slot = (unsigned char *)array->data + (index * array->itemsize);
    switch (array->typenum) {
        case NPY_FLOAT:
            *(float *)slot = (float)value;
            return 0;
        case NPY_DOUBLE:
            *(double *)slot = (double)value;
            return 0;
        case NPY_LONGDOUBLE:
            *(long double *)slot = value;
            return 0;
        default:
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy floating rtype output requires a floating result dtype"
            );
            return -1;
    }
}

static int pccnp_typenum_is_real_numeric(int typenum) {
    return typenum != NPY_OBJECT &&
        !PyTypeNum_ISCOMPLEX(typenum) &&
        PyTypeNum_ISNUMBER(typenum);
}

static PyObject *pccnp_reduction_scalar_or_out(
    PyObject *value,
    PyArrayObject *out_obj,
    const char *api_name
) {
    if (value == NULL) return NULL;
    if (out_obj == NULL) return value;

    PyObject *out_py = (PyObject *)out_obj;
    if (!pccnp_is_array_object(out_py)) {
        Py_DECREF(value);
        PyErr_Format(PyExc_TypeError, "%s expects a pcc NumPy out array", api_name);
        return NULL;
    }

    PccNpArray *out = pccnp_array_from_object(out_obj);
    if (out == NULL) {
        Py_DECREF(value);
        PyErr_Format(PyExc_TypeError, "%s expects a pcc NumPy out array", api_name);
        return NULL;
    }
    if (out->element_count != 1) {
        Py_DECREF(value);
        PyErr_Format(
            PyExc_NotImplementedError,
            "pcc NumPy %s supports scalar one-element out arrays only",
            api_name
        );
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(out->typenum)) {
        Py_DECREF(value);
        PyErr_Format(
            PyExc_NotImplementedError,
            "pcc NumPy %s supports real numeric out arrays only",
            api_name
        );
        return NULL;
    }
    if (out->data == NULL) {
        Py_DECREF(value);
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (pccnp_setitem(out_obj, out->data, value) != 0) {
        Py_DECREF(value);
        return NULL;
    }
    Py_DECREF(value);
    Py_INCREF(out_py);
    return out_py;
}

static PyObject *pccnp_array_result_or_out(
    PyObject *result,
    PyArrayObject *out_obj,
    const char *api_name
) {
    if (result == NULL) return NULL;
    if (out_obj == NULL) return result;

    if (!pccnp_is_array_object(result)) {
        Py_DECREF(result);
        PyErr_Format(PyExc_TypeError, "%s expected a pcc NumPy result array", api_name);
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)out_obj)) {
        Py_DECREF(result);
        PyErr_Format(PyExc_TypeError, "%s expects a pcc NumPy out array", api_name);
        return NULL;
    }
    if (pccnp_copy_into(out_obj, (PyArrayObject *)result) != 0) {
        Py_DECREF(result);
        return NULL;
    }
    Py_DECREF(result);
    Py_INCREF((PyObject *)out_obj);
    return (PyObject *)out_obj;
}

static PyObject *pccnp_inner_product(PyObject *op1, PyObject *op2) {
    if (!pccnp_is_array_object(op1) || !pccnp_is_array_object(op2)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_InnerProduct expects pcc NumPy arrays");
        return NULL;
    }
    PccNpArray *lhs = pccnp_array_from_object((PyArrayObject *)op1);
    PccNpArray *rhs = pccnp_array_from_object((PyArrayObject *)op2);
    if (lhs == NULL || rhs == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_InnerProduct expects pcc NumPy arrays");
        return NULL;
    }
    if (lhs->nd != 1 || rhs->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_InnerProduct supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(lhs) || !pccnp_has_c_order_strides(rhs)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_InnerProduct supports C-order arrays only");
        return NULL;
    }
    if (lhs->element_count != rhs->element_count) {
        PyErr_SetString(PyExc_ValueError, "PyArray_InnerProduct shape mismatch");
        return NULL;
    }
    if (lhs->element_count != 0 && (lhs->data == NULL || rhs->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(lhs->typenum) ||
        !pccnp_typenum_is_real_numeric(rhs->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_InnerProduct supports real numeric arrays only"
        );
        return NULL;
    }

    long double total = 0.0;
    for (size_t i = 0; i < lhs->element_count; i++) {
        long double left = 0.0;
        long double right = 0.0;
        if (pccnp_element_as_long_double(lhs, i, &left) != 0 ||
            pccnp_element_as_long_double(rhs, i, &right) != 0) {
            return NULL;
        }
        total += left * right;
    }

    if (PyTypeNum_ISFLOAT(lhs->typenum) || PyTypeNum_ISFLOAT(rhs->typenum)) {
        return PyFloat_FromDouble((double)total);
    }
    if (pccnp_typenum_is_unsigned_or_bool(lhs->typenum) &&
        pccnp_typenum_is_unsigned_or_bool(rhs->typenum)) {
        if (total > (long double)ULLONG_MAX || total < 0.0) {
            PyErr_SetString(PyExc_OverflowError, "inner product result is too large");
            return NULL;
        }
        return PyLong_FromUnsignedLongLong((unsigned long long)total);
    }
    if (total > (long double)LLONG_MAX || total < (long double)LLONG_MIN) {
        PyErr_SetString(PyExc_OverflowError, "inner product result is too large");
        return NULL;
    }
    return PyLong_FromLongLong((long long)total);
}

static PyObject *pccnp_matrix_product(PyObject *op1, PyObject *op2) {
    if (!pccnp_is_array_object(op1) || !pccnp_is_array_object(op2)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_MatrixProduct expects pcc NumPy arrays");
        return NULL;
    }
    PccNpArray *lhs = pccnp_array_from_object((PyArrayObject *)op1);
    PccNpArray *rhs = pccnp_array_from_object((PyArrayObject *)op2);
    if (lhs == NULL || rhs == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_MatrixProduct expects pcc NumPy arrays");
        return NULL;
    }
    if (lhs->nd == 1 && rhs->nd == 1) {
        return pccnp_inner_product(op1, op2);
    }
    if (!((lhs->nd == 1 || lhs->nd == 2) && (rhs->nd == 1 || rhs->nd == 2))) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_MatrixProduct supports 1D/2D matrix products only"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(lhs) || !pccnp_has_c_order_strides(rhs)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_MatrixProduct supports C-order arrays only");
        return NULL;
    }
    if (lhs->dims[0] < 0 || rhs->dims[0] < 0 ||
        (lhs->nd == 2 && lhs->dims[1] < 0) ||
        (rhs->nd == 2 && rhs->dims[1] < 0)) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MatrixProduct dimensions must be non-negative");
        return NULL;
    }
    npy_intp lhs_rows = lhs->nd == 1 ? 1 : lhs->dims[0];
    npy_intp lhs_cols = lhs->nd == 1 ? lhs->dims[0] : lhs->dims[1];
    npy_intp rhs_rows = rhs->dims[0];
    npy_intp rhs_cols = rhs->nd == 1 ? 1 : rhs->dims[1];
    if (lhs_cols != rhs_rows) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MatrixProduct shape mismatch");
        return NULL;
    }
    if ((lhs->element_count != 0 && lhs->data == NULL) ||
        (rhs->element_count != 0 && rhs->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(lhs->typenum) ||
        !pccnp_typenum_is_real_numeric(rhs->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_MatrixProduct supports real numeric arrays only"
        );
        return NULL;
    }

    int result_nd = (lhs->nd == 2 && rhs->nd == 2) ? 2 : 1;
    npy_intp result_dims[2] = {0, 0};
    if (result_nd == 2) {
        result_dims[0] = lhs_rows;
        result_dims[1] = rhs_cols;
    } else {
        result_dims[0] = lhs->nd == 1 ? rhs_cols : lhs_rows;
    }
    int result_typenum = (PyTypeNum_ISFLOAT(lhs->typenum) || PyTypeNum_ISFLOAT(rhs->typenum))
        ? NPY_DOUBLE
        : NPY_LONG;
    PyObject *result_obj = pccnp_array_new_common(result_nd, result_dims, result_typenum, NULL, 1);
    if (result_obj == NULL) {
        return NULL;
    }
    PccNpArray *result = pccnp_array_from_object((PyArrayObject *)result_obj);
    if (result == NULL || result->data == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "PyArray_MatrixProduct failed to allocate result data");
        Py_DECREF(result_obj);
        return NULL;
    }

    for (npy_intp row = 0; row < lhs_rows; row++) {
        for (npy_intp col = 0; col < rhs_cols; col++) {
            long double total = 0.0;
            for (npy_intp k = 0; k < lhs_cols; k++) {
                long double left = 0.0;
                long double right = 0.0;
                size_t lhs_index = lhs->nd == 1
                    ? (size_t)k
                    : (size_t)(row * lhs_cols + k);
                size_t rhs_index = rhs->nd == 1
                    ? (size_t)k
                    : (size_t)(k * rhs_cols + col);
                if (pccnp_element_as_long_double(lhs, lhs_index, &left) != 0 ||
                    pccnp_element_as_long_double(rhs, rhs_index, &right) != 0) {
                    Py_DECREF(result_obj);
                    return NULL;
                }
                total += left * right;
            }
            size_t result_index = result_nd == 2
                ? (size_t)(row * rhs_cols + col)
                : (size_t)(lhs->nd == 1 ? col : row);
            if (result_typenum == NPY_DOUBLE) {
                ((double *)result->data)[result_index] = (double)total;
            } else {
                if (total > (long double)LONG_MAX || total < (long double)LONG_MIN) {
                    PyErr_SetString(PyExc_OverflowError, "matrix product result is too large");
                    Py_DECREF(result_obj);
                    return NULL;
                }
                ((long *)result->data)[result_index] = (long)total;
            }
        }
    }
    return result_obj;
}

static PyObject *pccnp_matrix_product2(PyObject *op1, PyObject *op2, PyArrayObject *out_obj) {
    if (out_obj != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_MatrixProduct2 does not support out arrays");
        return NULL;
    }
    return pccnp_matrix_product(op1, op2);
}

static PyArrayObject *pccnp_einstein_sum(
    char *subscripts,
    npy_intp nop,
    PyArrayObject **op_in,
    PyArray_Descr *dtype,
    NPY_ORDER order,
    NPY_CASTING casting,
    PyArrayObject *out
) {
    (void)order;
    (void)casting;
    if (subscripts == NULL) {
        PyErr_SetString(PyExc_ValueError, "einsum subscripts are required");
        return NULL;
    }
    if (op_in == NULL) {
        PyErr_SetString(PyExc_ValueError, "einsum operands are required");
        return NULL;
    }
    if (dtype != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_EinsteinSum does not support forced dtype");
        return NULL;
    }
    if (out != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_EinsteinSum does not support out arrays");
        return NULL;
    }
    if (nop != 2) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_EinsteinSum supports two operands only");
        return NULL;
    }
    if (op_in[0] == NULL || op_in[1] == NULL) {
        PyErr_SetString(PyExc_ValueError, "einsum operands must not be NULL");
        return NULL;
    }

    if (strcmp(subscripts, "i,i->") == 0 || strcmp(subscripts, "i,i") == 0) {
        return (PyArrayObject *)pccnp_inner_product((PyObject *)op_in[0], (PyObject *)op_in[1]);
    }
    if (strcmp(subscripts, "ij,jk->ik") == 0 || strcmp(subscripts, "ij,jk") == 0) {
        return (PyArrayObject *)pccnp_matrix_product((PyObject *)op_in[0], (PyObject *)op_in[1]);
    }
    if (strcmp(subscripts, "i,ij->j") == 0 || strcmp(subscripts, "i,ij") == 0 ||
        strcmp(subscripts, "ij,j->i") == 0 || strcmp(subscripts, "ij,j") == 0) {
        return (PyArrayObject *)pccnp_matrix_product((PyObject *)op_in[0], (PyObject *)op_in[1]);
    }

    PyErr_SetString(
        PyExc_NotImplementedError,
        "pcc NumPy PyArray_EinsteinSum supports only reduced dot and 1D/2D matrix-product forms"
    );
    return NULL;
}

static PyObject *pccnp_correlate(PyObject *op1, PyObject *op2, int mode) {
    if (!pccnp_is_array_object(op1) || !pccnp_is_array_object(op2)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Correlate expects pcc NumPy arrays");
        return NULL;
    }
    PccNpArray *lhs = pccnp_array_from_object((PyArrayObject *)op1);
    PccNpArray *rhs = pccnp_array_from_object((PyArrayObject *)op2);
    if (lhs == NULL || rhs == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Correlate expects pcc NumPy arrays");
        return NULL;
    }
    if (lhs->nd != 1 || rhs->nd != 1) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Correlate supports 1D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(lhs) || !pccnp_has_c_order_strides(rhs)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Correlate supports C-order arrays only");
        return NULL;
    }
    if (lhs->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "first array argument cannot be empty");
        return NULL;
    }
    if (rhs->element_count == 0) {
        PyErr_SetString(PyExc_ValueError, "second array argument cannot be empty");
        return NULL;
    }
    if ((lhs->element_count != 0 && lhs->data == NULL) ||
        (rhs->element_count != 0 && rhs->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (!pccnp_typenum_is_real_numeric(lhs->typenum) ||
        !pccnp_typenum_is_real_numeric(rhs->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Correlate supports real numeric arrays only"
        );
        return NULL;
    }

    PccNpArray *ap1 = lhs;
    PccNpArray *ap2 = rhs;
    if (ap1->element_count < ap2->element_count) {
        ap1 = rhs;
        ap2 = lhs;
    }
    size_t n1 = ap1->element_count;
    size_t n2 = ap2->element_count;
    size_t output_len = 0;
    size_t full_start = 0;
    if (mode == NPY_VALID) {
        output_len = n1 - n2 + 1;
        full_start = n2 - 1;
    } else if (mode == NPY_SAME) {
        size_t n_left = n2 / 2;
        output_len = n1;
        full_start = (n2 - 1) - n_left;
    } else if (mode == NPY_FULL) {
        if (n1 > (size_t)PY_SSIZE_T_MAX - n2 + 1) {
            PyErr_SetString(PyExc_OverflowError, "correlate result is too large");
            return NULL;
        }
        output_len = n1 + n2 - 1;
        full_start = 0;
    } else {
        PyErr_SetString(PyExc_ValueError, "mode must be 0, 1, or 2");
        return NULL;
    }
    if (output_len > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "correlate result is too large");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)output_len};
    int result_typenum = (PyTypeNum_ISFLOAT(ap1->typenum) || PyTypeNum_ISFLOAT(ap2->typenum))
        ? NPY_DOUBLE
        : NPY_LONG;
    PyObject *result_obj = pccnp_array_new_common(1, dims, result_typenum, NULL, 1);
    if (result_obj == NULL) {
        return NULL;
    }
    PccNpArray *result = pccnp_array_from_object((PyArrayObject *)result_obj);
    if (result == NULL || result->data == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "PyArray_Correlate failed to allocate result data");
        Py_DECREF(result_obj);
        return NULL;
    }

    for (size_t out_i = 0; out_i < output_len; out_i++) {
        size_t full_pos = full_start + out_i;
        long double total = 0.0;
        for (size_t k = 0; k < n2; k++) {
            size_t shifted = full_pos + k;
            if (shifted < n2 - 1) {
                continue;
            }
            size_t ap1_index = shifted - (n2 - 1);
            if (ap1_index >= n1) {
                continue;
            }
            long double left = 0.0;
            long double right = 0.0;
            if (pccnp_element_as_long_double(ap1, ap1_index, &left) != 0 ||
                pccnp_element_as_long_double(ap2, k, &right) != 0) {
                Py_DECREF(result_obj);
                return NULL;
            }
            total += left * right;
        }
        if (result_typenum == NPY_DOUBLE) {
            ((double *)result->data)[out_i] = (double)total;
        } else {
            if (total > (long double)LONG_MAX || total < (long double)LONG_MIN) {
                PyErr_SetString(PyExc_OverflowError, "correlate result is too large");
                Py_DECREF(result_obj);
                return NULL;
            }
            ((long *)result->data)[out_i] = (long)total;
        }
    }
    return result_obj;
}

static PyObject *pccnp_correlate2(PyObject *op1, PyObject *op2, int mode) {
    return pccnp_correlate(op1, op2, mode);
}

static PyObject *pccnp_search_sorted(
    PyArrayObject *op1,
    PyObject *op2,
    NPY_SEARCHSIDE side,
    PyObject *perm
) {
    if (side != NPY_SEARCHLEFT && side != NPY_SEARCHRIGHT) {
        PyErr_SetString(
            PyExc_ValueError,
            "pcc NumPy PyArray_SearchSorted received an invalid side"
        );
        return NULL;
    }

    PccNpArray *haystack = pccnp_array_from_object(op1);
    if (haystack == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (!pccnp_is_array_object(op2)) {
        PyErr_SetString(PyExc_TypeError, "keys object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *keys = pccnp_array_from_object((PyArrayObject *)op2);
    if (keys == NULL) {
        PyErr_SetString(PyExc_TypeError, "keys object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *sorter = NULL;
    if (perm != NULL) {
        if (!pccnp_is_array_object(perm)) {
            PyErr_SetString(PyExc_TypeError, "sorter object is not a pcc NumPy array");
            return NULL;
        }
        sorter = pccnp_array_from_object((PyArrayObject *)perm);
        if (sorter == NULL) {
            PyErr_SetString(PyExc_TypeError, "sorter object is not a pcc NumPy array");
            return NULL;
        }
    }
    if (haystack->nd != 1 || keys->nd != 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_SearchSorted supports 1D arrays only"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(haystack) || !pccnp_has_c_order_strides(keys)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_SearchSorted supports C-order arrays only"
        );
        return NULL;
    }
    if (haystack->typenum != keys->typenum || haystack->itemsize != keys->itemsize) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_SearchSorted requires matching fake dtypes"
        );
        return NULL;
    }
    if (haystack->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(haystack->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_SearchSorted does not support object or complex arrays"
        );
        return NULL;
    }
    if ((haystack->element_count != 0 && haystack->data == NULL) ||
        (keys->element_count != 0 && keys->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (sorter != NULL) {
        if (sorter->nd != 1) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_SearchSorted supports 1D sorter arrays only"
            );
            return NULL;
        }
        if (!pccnp_has_c_order_strides(sorter)) {
            PyErr_SetString(
                PyExc_NotImplementedError,
                "pcc NumPy PyArray_SearchSorted supports C-order sorter arrays only"
            );
            return NULL;
        }
        if (sorter->element_count != haystack->element_count) {
            PyErr_SetString(PyExc_ValueError, "sorter length must match sorted array length");
            return NULL;
        }
        if (sorter->element_count != 0 && sorter->data == NULL) {
            PyErr_SetString(PyExc_ValueError, "sorter data pointer is required");
            return NULL;
        }
        for (size_t i = 0; i < sorter->element_count; i++) {
            npy_intp raw_index = 0;
            if (pccnp_read_signed_index(sorter, i, &raw_index) != 0) {
                return NULL;
            }
            if (raw_index < 0 || (size_t)raw_index >= haystack->element_count) {
                PyErr_SetString(PyExc_ValueError, "sorter index is out of bounds");
                return NULL;
            }
        }
    }
    if (haystack->element_count > (size_t)LONG_MAX ||
        keys->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "searchsorted index array is too large");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)keys->element_count};
    PyObject *result = pccnp_array_new_common(1, dims, NPY_LONG, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *indices = pccnp_array_from_object((PyArrayObject *)result);
    if (indices == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    unsigned char *hay_data = (unsigned char *)haystack->data;
    unsigned char *key_data = (unsigned char *)keys->data;
    long *idx_data = (long *)indices->data;
    for (size_t i = 0; i < keys->element_count; i++) {
        size_t lo = 0;
        size_t hi = haystack->element_count;
        unsigned char *key = key_data + (i * keys->itemsize);
        while (lo < hi) {
            size_t mid = lo + ((hi - lo) / 2);
            size_t hay_index = mid;
            if (sorter != NULL) {
                npy_intp raw_index = 0;
                if (pccnp_read_signed_index(sorter, mid, &raw_index) != 0) {
                    Py_DECREF(result);
                    return NULL;
                }
                hay_index = (size_t)raw_index;
            }
            int cmp = 0;
            if (pccnp_compare_elements(
                    haystack,
                    hay_data + (hay_index * haystack->itemsize),
                    key,
                    &cmp
                ) != 0) {
                Py_DECREF(result);
                return NULL;
            }
            if (cmp < 0 || (side == NPY_SEARCHRIGHT && cmp == 0)) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        idx_data[i] = (long)lo;
    }
    return result;
}

static PyObject *pccnp_nonzero(PyArrayObject *op) {
    PccNpArray *array = pccnp_array_from_object(op);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->nd != 1 && array->nd != 2) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Nonzero supports 1D/2D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Nonzero supports C-order arrays only");
        return NULL;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Nonzero does not support object, complex, or non-numeric arrays"
        );
        return NULL;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }
    if (array->element_count > (size_t)LONG_MAX) {
        PyErr_SetString(PyExc_OverflowError, "nonzero index array is too large");
        return NULL;
    }
    for (int axis = 0; axis < array->nd; axis++) {
        if (array->dims[axis] < 0) {
            PyErr_SetString(PyExc_ValueError, "nonzero dimensions must be non-negative");
            return NULL;
        }
    }

    unsigned char *data = (unsigned char *)array->data;
    size_t count = 0;
    for (size_t i = 0; i < array->element_count; i++) {
        int truthy = 0;
        if (pccnp_element_is_nonzero(array, data + (i * array->itemsize), &truthy) != 0) {
            return NULL;
        }
        if (truthy) {
            count++;
        }
    }

    npy_intp dims[1] = {(npy_intp)count};
    PyObject *tuple = PyTuple_New((Py_ssize_t)array->nd);
    if (tuple == NULL) {
        return NULL;
    }
    PccNpArray *index_arrays[2] = {NULL, NULL};
    for (int axis = 0; axis < array->nd; axis++) {
        PyObject *indices = pccnp_array_new_common(1, dims, NPY_LONG, NULL, 1);
        if (indices == NULL) {
            Py_DECREF(tuple);
            return NULL;
        }
        index_arrays[axis] = pccnp_array_from_object((PyArrayObject *)indices);
        if (index_arrays[axis] == NULL || (count != 0 && index_arrays[axis]->data == NULL)) {
            Py_DECREF(indices);
            Py_DECREF(tuple);
            return NULL;
        }
        if (PyTuple_SetItem(tuple, (Py_ssize_t)axis, indices) != 0) {
            Py_DECREF(indices);
            Py_DECREF(tuple);
            return NULL;
        }
    }

    long *idx_data0 = (long *)index_arrays[0]->data;
    long *idx_data1 = array->nd == 2 ? (long *)index_arrays[1]->data : NULL;
    size_t out_pos = 0;
    for (size_t i = 0; i < array->element_count; i++) {
        int truthy = 0;
        if (pccnp_element_is_nonzero(array, data + (i * array->itemsize), &truthy) != 0) {
            Py_DECREF(tuple);
            return NULL;
        }
        if (truthy) {
            if (array->nd == 1) {
                idx_data0[out_pos] = (long)i;
            } else {
                npy_intp cols = array->dims[1];
                idx_data0[out_pos] = cols == 0 ? 0 : (long)((npy_intp)i / cols);
                idx_data1[out_pos] = cols == 0 ? 0 : (long)((npy_intp)i % cols);
            }
            out_pos++;
        }
    }

    return tuple;
}

static npy_intp pccnp_count_nonzero(PyArrayObject *op) {
    if (!pccnp_is_array_object((PyObject *)op)) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object(op);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CountNonzero supports C-order arrays only"
        );
        return -1;
    }
    if (array->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(array->typenum) ||
        !PyTypeNum_ISNUMBER(array->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CountNonzero does not support object, complex, or non-numeric arrays"
        );
        return -1;
    }
    if (array->element_count != 0 && array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return -1;
    }
    if (array->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "nonzero count is too large");
        return -1;
    }

    unsigned char *data = (unsigned char *)array->data;
    npy_intp count = 0;
    for (size_t i = 0; i < array->element_count; i++) {
        int truthy = 0;
        if (pccnp_element_is_nonzero(array, data + (i * array->itemsize), &truthy) != 0) {
            return -1;
        }
        if (truthy) {
            count++;
        }
    }
    return count;
}

static PyArray_Descr *pccnp_min_scalar_type(PyArrayObject *op) {
    if (!pccnp_is_array_object((PyObject *)op)) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(op);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (pccnp_descr_for_type(array->typenum) == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    if (array->nd > 0 || !PyTypeNum_ISNUMBER(array->typenum) ||
        PyTypeNum_ISCOMPLEX(array->typenum)) {
        return pccnp_descr_new_from_type(array->typenum);
    }
    int typenum = pccnp_min_scalar_typenum(array);
    if (typenum == NPY_NOTYPE) return NULL;
    return pccnp_descr_new_from_type(typenum);
}

static npy_uintp pccnp_abs_intp_as_uint(npy_intp value) {
    if (value >= 0) return (npy_uintp)value;
    return (npy_uintp)(-(value + 1)) + 1u;
}

static int pccnp_stride_sort_item_before(
    const npy_stride_sort_item *lhs,
    const npy_stride_sort_item *rhs
) {
    npy_uintp lhs_abs = pccnp_abs_intp_as_uint(lhs->stride);
    npy_uintp rhs_abs = pccnp_abs_intp_as_uint(rhs->stride);
    if (lhs_abs != rhs_abs) return lhs_abs > rhs_abs;
    return lhs->perm < rhs->perm;
}

static void pccnp_create_sorted_stride_perm(
    int ndim,
    const npy_intp *strides,
    npy_stride_sort_item *out_strideperm
) {
    if (ndim <= 0) return;
    if (strides == NULL || out_strideperm == NULL) {
        PyErr_SetString(PyExc_ValueError, "stride permutation inputs are required");
        return;
    }

    for (int i = 0; i < ndim; i++) {
        out_strideperm[i].perm = (npy_intp)i;
        out_strideperm[i].stride = strides[i];
    }

    for (int i = 1; i < ndim; i++) {
        npy_stride_sort_item item = out_strideperm[i];
        int j = i - 1;
        while (j >= 0 && pccnp_stride_sort_item_before(&item, &out_strideperm[j])) {
            out_strideperm[j + 1] = out_strideperm[j];
            j--;
        }
        out_strideperm[j + 1] = item;
    }
}

static void pccnp_remove_axes_in_place(PyArrayObject *arr, const npy_bool *flags) {
    if (!pccnp_is_array_object((PyObject *)arr)) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    if (array->nd > 0 && flags == NULL) {
        PyErr_SetString(PyExc_ValueError, "axis-removal flags are required");
        return;
    }

    int out_nd = 0;
    for (int i = 0; i < array->nd; i++) {
        if (!flags[i]) {
            if (out_nd != i) {
                array->dims[out_nd] = array->dims[i];
                array->strides[out_nd] = array->strides[i];
            }
            out_nd++;
        }
    }
    array->nd = out_nd;
    pccnp_update_flags(arr, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_F_CONTIGUOUS);
}

static void pccnp_debug_print(PyArrayObject *obj) {
    printf("-------------------------------------------------------\n");
    printf(" Dump of pcc NumPy ndarray at address %p\n", (void *)obj);
    if (obj == NULL) {
        printf(" It's NULL!\n");
        printf("-------------------------------------------------------\n");
        fflush(stdout);
        return;
    }
    if (!pccnp_is_array_object((PyObject *)obj)) {
        printf(" not a pcc NumPy array\n");
        printf("-------------------------------------------------------\n");
        fflush(stdout);
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }

    PccNpArray *array = pccnp_array_from_object(obj);
    if (array == NULL) {
        printf(" not a pcc NumPy array\n");
        printf("-------------------------------------------------------\n");
        fflush(stdout);
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    printf(" ndim   : %d\n", array->nd);
    printf(" shape  :");
    for (int i = 0; i < array->nd; i++) {
        printf(" %ld", (long)array->dims[i]);
    }
    printf("\n");
    printf(" dtype  : %s\n", pccnp_typenum_name(array->typenum));
    printf(" itemsize : %zu\n", array->itemsize);
    printf(" data   : %p\n", array->data);
    printf(" strides:");
    for (int i = 0; i < array->nd; i++) {
        printf(" %ld", (long)array->strides[i]);
    }
    printf("\n");
    printf(" base   : %p\n", (void *)array->base);
    printf(" flags  : %d\n", pccnp_flags(obj));
    printf("-------------------------------------------------------\n");
    fflush(stdout);
}

static PyObject *pccnp_where(
    PyObject *condition_obj,
    PyObject *x_obj,
    PyObject *y_obj
) {
    if (x_obj == NULL && y_obj == NULL) {
        return pccnp_nonzero((PyArrayObject *)condition_obj);
    }
    if (x_obj == NULL || y_obj == NULL) {
        PyErr_SetString(
            PyExc_ValueError,
            "pcc NumPy PyArray_Where requires both x and y or neither"
        );
        return NULL;
    }
    if (!pccnp_is_array_object(condition_obj) ||
        !pccnp_is_array_object(x_obj) ||
        !pccnp_is_array_object(y_obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Where expects pcc NumPy arrays");
        return NULL;
    }

    PccNpArray *condition = pccnp_array_from_object((PyArrayObject *)condition_obj);
    PccNpArray *x = pccnp_array_from_object((PyArrayObject *)x_obj);
    PccNpArray *y = pccnp_array_from_object((PyArrayObject *)y_obj);
    if (condition == NULL || x == NULL || y == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_Where expects pcc NumPy arrays");
        return NULL;
    }
    if (condition->nd < 1 || condition->nd > 2 ||
        x->nd < 1 || x->nd > 2 ||
        y->nd < 1 || y->nd > 2) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Where supports 1D/2D arrays only");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(condition) ||
        !pccnp_has_c_order_strides(x) ||
        !pccnp_has_c_order_strides(y)) {
        PyErr_SetString(PyExc_NotImplementedError, "pcc NumPy PyArray_Where supports C-order arrays only");
        return NULL;
    }
    if (condition->nd != x->nd || condition->nd != y->nd) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Where shape mismatch");
        return NULL;
    }
    for (int dim = 0; dim < x->nd; dim++) {
        if (condition->dims[dim] != x->dims[dim] ||
            condition->dims[dim] != y->dims[dim]) {
            PyErr_SetString(PyExc_ValueError, "PyArray_Where shape mismatch");
            return NULL;
        }
    }
    if (condition->element_count != x->element_count ||
        condition->element_count != y->element_count) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Where shape mismatch");
        return NULL;
    }
    if (x->typenum != y->typenum || x->itemsize != y->itemsize) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Where requires matching x/y fake dtypes"
        );
        return NULL;
    }
    if (x->typenum == NPY_OBJECT || y->typenum == NPY_OBJECT ||
        PyTypeNum_ISCOMPLEX(x->typenum) || PyTypeNum_ISCOMPLEX(y->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Where does not support object or complex result arrays"
        );
        return NULL;
    }
    if (condition->typenum == NPY_OBJECT || PyTypeNum_ISCOMPLEX(condition->typenum) ||
        !PyTypeNum_ISNUMBER(condition->typenum)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Where does not support object, complex, or non-numeric condition arrays"
        );
        return NULL;
    }
    if ((condition->element_count != 0 && condition->data == NULL) ||
        (x->element_count != 0 && x->data == NULL) ||
        (y->element_count != 0 && y->data == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is required");
        return NULL;
    }

    npy_intp dims[2] = {0, 0};
    for (int dim = 0; dim < x->nd; dim++) {
        dims[dim] = x->dims[dim];
    }
    PyObject *result = pccnp_array_new_common(x->nd, dims, x->typenum, NULL, 1);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *out = pccnp_array_from_object((PyArrayObject *)result);
    if (out == NULL) {
        Py_DECREF(result);
        return NULL;
    }

    unsigned char *cond_data = (unsigned char *)condition->data;
    unsigned char *x_data = (unsigned char *)x->data;
    unsigned char *y_data = (unsigned char *)y->data;
    unsigned char *out_data = (unsigned char *)out->data;
    for (size_t i = 0; i < x->element_count; i++) {
        int truthy = 0;
        if (pccnp_element_is_nonzero(
                condition,
                cond_data + (i * condition->itemsize),
                &truthy
            ) != 0) {
            Py_DECREF(result);
            return NULL;
        }
        unsigned char *src = truthy ?
            x_data + (i * x->itemsize) :
            y_data + (i * y->itemsize);
        memcpy(out_data + (i * out->itemsize), src, out->itemsize);
    }
    return result;
}

