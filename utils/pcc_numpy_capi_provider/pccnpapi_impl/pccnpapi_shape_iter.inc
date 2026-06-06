/* stride validation, reshape/view, broadcasting, iterators, and flattening.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static npy_bool pccnp_check_strides(
    int elsize,
    int nd,
    npy_intp numbytes,
    npy_intp offset,
    npy_intp const *dims,
    npy_intp const *newstrides
) {
    if (elsize <= 0 || nd < 0 || offset < 0) return 0;
    if (nd > 0 && (dims == NULL || newstrides == NULL)) return 0;

    int has_empty_axis = 0;
    npy_intp inferred_numbytes = (npy_intp)elsize;
    for (int i = 0; i < nd; i++) {
        if (dims[i] < 0) return 0;
        if (dims[i] == 0) {
            has_empty_axis = 1;
            break;
        }
        if (numbytes == 0 &&
            pccnp_checked_mul_intp(inferred_numbytes, dims[i], &inferred_numbytes) != 0) {
            return 0;
        }
    }

    if (has_empty_axis) return offset <= numbytes;
    if (numbytes == 0) numbytes = inferred_numbytes;
    if (numbytes <= 0) return 0;

    npy_intp low = offset;
    npy_intp high = offset;
    npy_intp item_last = (npy_intp)elsize - 1;
    if (pccnp_checked_add_intp(high, item_last, &high) != 0) return 0;

    for (int i = 0; i < nd; i++) {
        npy_intp axis_span = 0;
        if (dims[i] > 1 &&
            pccnp_checked_mul_intp(dims[i] - 1, newstrides[i], &axis_span) != 0) {
            return 0;
        }
        if (axis_span < 0) {
            if (pccnp_checked_add_intp(low, axis_span, &low) != 0) return 0;
        } else {
            if (pccnp_checked_add_intp(high, axis_span, &high) != 0) return 0;
        }
    }

    return low >= 0 && high < numbytes;
}

static double pccnp_get_priority(PyObject *obj, double default_value) {
    if (obj == NULL) {
        return default_value;
    }
    if (pccnp_is_array_object(obj)) {
        return 0.0;
    }

    PyObject *priority_obj = PyObject_GetAttrString(obj, "__array_priority__");
    if (priority_obj == NULL) {
        PyErr_Clear();
        return default_value;
    }

    double priority = PyFloat_AsDouble(priority_obj);
    Py_DECREF(priority_obj);
    if (PyErr_Occurred() != NULL) {
        PyErr_Clear();
        return default_value;
    }
    return priority;
}

static int pccnp_equiv_types(PyArray_Descr *lhs, PyArray_Descr *rhs) {
    if (lhs == NULL || rhs == NULL) return 0;
    if (lhs->type_num != rhs->type_num) return 0;
    if (lhs->elsize != rhs->elsize) return 0;
    return lhs->kind == rhs->kind;
}

static int pccnp_equiv_typenums(int lhs_typenum, int rhs_typenum) {
    if (lhs_typenum == rhs_typenum) return 1;
    PyArray_Descr *lhs = pccnp_descr_for_type(lhs_typenum);
    PyArray_Descr *rhs = pccnp_descr_for_type(rhs_typenum);
    return pccnp_equiv_types(lhs, rhs);
}

static PyArray_Descr *pccnp_promote_types(PyArray_Descr *type1, PyArray_Descr *type2) {
    if (type1 == NULL || type2 == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_PromoteTypes requires two descriptors");
        return NULL;
    }
    int typenum = pccnp_dtype_promotion_type(type1->type_num, type2->type_num);
    if (typenum == NPY_NOTYPE) {
        pccnp_raise_unsupported();
        return NULL;
    }
    return pccnp_descr_new_from_type(typenum);
}

static PyArray_Descr *pccnp_result_type(
    int narrs,
    PyArrayObject **arrs,
    int ndtypes,
    PyArray_Descr **descrs
) {
    if (narrs < 0 || ndtypes < 0) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ResultType received a negative input count");
        return NULL;
    }
    if (narrs == 0 && ndtypes == 0) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ResultType requires at least one input");
        return NULL;
    }
    if (narrs > 0 && arrs == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ResultType array inputs are NULL");
        return NULL;
    }
    if (ndtypes > 0 && descrs == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_ResultType descriptor inputs are NULL");
        return NULL;
    }

    int typenum = NPY_NOTYPE;
    for (int i = 0; i < narrs; i++) {
        if (arrs[i] == NULL || !pccnp_is_array_object((PyObject *)arrs[i])) {
            PyErr_SetString(PyExc_TypeError, "PyArray_ResultType requires pcc NumPy array inputs");
            return NULL;
        }
        PccNpArray *array = pccnp_array_from_object(arrs[i]);
        if (array == NULL) return NULL;
        typenum = typenum == NPY_NOTYPE
            ? array->typenum
            : pccnp_dtype_promotion_type(typenum, array->typenum);
        if (typenum == NPY_NOTYPE) {
            pccnp_raise_unsupported();
            return NULL;
        }
    }

    for (int i = 0; i < ndtypes; i++) {
        if (descrs[i] == NULL) {
            PyErr_SetString(PyExc_ValueError, "PyArray_ResultType descriptor input is NULL");
            return NULL;
        }
        typenum = typenum == NPY_NOTYPE
            ? descrs[i]->type_num
            : pccnp_dtype_promotion_type(typenum, descrs[i]->type_num);
        if (typenum == NPY_NOTYPE) {
            pccnp_raise_unsupported();
            return NULL;
        }
    }

    return pccnp_descr_new_from_type(typenum);
}

static int pccnp_signed_first_item_is_negative(int typenum, PyArrayObject **arr) {
    if (arr == NULL || *arr == NULL || !pccnp_is_array_object((PyObject *)*arr)) {
        return 0;
    }
    PccNpArray *array = pccnp_array_from_object(*arr);
    if (array == NULL || array->data == NULL || array->element_count == 0) {
        PyErr_Clear();
        return 0;
    }
    switch (typenum) {
        case NPY_BYTE:
            return *(signed char *)array->data < 0;
        case NPY_SHORT:
            return *(short *)array->data < 0;
        case NPY_INT:
            return *(int *)array->data < 0;
        case NPY_LONG:
            return *(long *)array->data < 0;
        case NPY_LONGLONG:
            return *(long long *)array->data < 0;
        default:
            return 0;
    }
}

static NPY_SCALARKIND pccnp_scalar_kind(int typenum, PyArrayObject **arr) {
    if (typenum == NPY_BOOL) return NPY_BOOL_SCALAR;
    if (PyTypeNum_ISUNSIGNED(typenum)) return NPY_INTPOS_SCALAR;
    if (PyTypeNum_ISSIGNED(typenum)) {
        return pccnp_signed_first_item_is_negative(typenum, arr)
            ? NPY_INTNEG_SCALAR
            : NPY_INTPOS_SCALAR;
    }
    if (PyTypeNum_ISFLOAT(typenum)) return NPY_FLOAT_SCALAR;
    if (PyTypeNum_ISCOMPLEX(typenum)) return NPY_COMPLEX_SCALAR;
    if (typenum == NPY_OBJECT || PyTypeNum_ISFLEXIBLE(typenum)) {
        return NPY_OBJECT_SCALAR;
    }
    return NPY_NOSCALAR;
}

static NPY_SCALARKIND pccnp_scalar_kind_table_value(int typenum) {
    if (typenum == NPY_BOOL) return NPY_BOOL_SCALAR;
    if (PyTypeNum_ISUNSIGNED(typenum)) return NPY_INTPOS_SCALAR;
    if (PyTypeNum_ISSIGNED(typenum)) return NPY_INTNEG_SCALAR;
    if (PyTypeNum_ISFLOAT(typenum)) return NPY_FLOAT_SCALAR;
    if (PyTypeNum_ISCOMPLEX(typenum)) return NPY_COMPLEX_SCALAR;
    if (typenum == NPY_OBJECT || PyTypeNum_ISFLEXIBLE(typenum)) {
        return NPY_OBJECT_SCALAR;
    }
    return NPY_NOSCALAR;
}

static int pccnp_check_any_scalar_exact(PyObject *obj) {
    if (obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "obj is NULL in PyArray_CheckAnyScalarExact");
        return 0;
    }
    return 0;
}

static int pccnp_can_coerce_scalar(
    int thistype,
    int neededtype,
    NPY_SCALARKIND scalar
) {
    if (scalar == NPY_NOSCALAR) {
        return pccnp_can_cast_safely(thistype, neededtype);
    }
    if (scalar == NPY_OBJECT_SCALAR) {
        return pccnp_can_cast_safely(thistype, neededtype);
    }
    NPY_SCALARKIND neededscalar = pccnp_scalar_kind_table_value(neededtype);
    if (neededscalar == NPY_NOSCALAR) return 0;
    return neededscalar >= scalar;
}

static int pccnp_typenum_from_type_object_silent(PyTypeObject *type) {
    if (type == &PyBool_Type) return NPY_BOOL;
    if (type == &PyLong_Type) return NPY_LONG;
    if (type == &PyFloat_Type) return NPY_DOUBLE;
    if (type == &PyComplex_Type) return NPY_CDOUBLE;
    if (type == &PyBytes_Type || type == &PyUnicode_Type) return NPY_STRING;
    if (type == &PyBaseObject_Type) return NPY_OBJECT;
    return NPY_NOTYPE;
}

static npy_bool pccnp_can_cast_scalar(PyTypeObject *from, PyTypeObject *to) {
    int fromtype = pccnp_typenum_from_type_object_silent(from);
    int totype = pccnp_typenum_from_type_object_silent(to);
    if (fromtype == NPY_NOTYPE || totype == NPY_NOTYPE) return 0;
    return (npy_bool)pccnp_can_cast_safely(fromtype, totype);
}

static int pccnp_copy_into(PyArrayObject *dst_obj, PyArrayObject *src_obj) {
    PccNpArray *dst = pccnp_array_from_object(dst_obj);
    PccNpArray *src = pccnp_array_from_object(src_obj);
    if (dst == NULL || src == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (dst->typenum != src->typenum || dst->itemsize != src->itemsize) {
        PyErr_SetString(PyExc_ValueError, "array dtype mismatch");
        return -1;
    }
    if (dst->nd != src->nd ||
        !pccnp_compare_lists(dst->dims, src->dims, dst->nd)) {
        PyErr_SetString(PyExc_ValueError, "array shape mismatch");
        return -1;
    }
    if (dst->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub does not copy object arrays"
        );
        return -1;
    }
    if (dst->data == NULL || src->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }
    size_t byte_count = 0;
    if (pccnp_mul_size(dst->element_count, dst->itemsize, &byte_count) != 0) {
        return -1;
    }
    memcpy(dst->data, src->data, byte_count);
    return 0;
}

static int pccnp_set_update_if_copy_base(PyArrayObject *arr, PyArrayObject *base) {
    (void)arr;
    (void)base;
    PyErr_SetString(
        PyExc_RuntimeError,
        "PyArray_SetUpdateIfCopyBase is disabled; use PyArray_SetWritebackIfCopyBase"
    );
    return -1;
}

static int pccnp_set_writeback_if_copy_base(PyArrayObject *arr, PyArrayObject *base) {
    if (base == NULL) {
        PyErr_SetString(PyExc_ValueError, "Cannot WRITEBACKIFCOPY to NULL array");
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object(arr);
    PccNpArray *base_array = pccnp_array_from_object(base);
    if (array == NULL || base_array == NULL) {
        PyErr_SetString(PyExc_TypeError, "WRITEBACKIFCOPY requires pcc NumPy arrays");
        Py_DECREF((PyObject *)base);
        return -1;
    }
    if (array->base != NULL) {
        PyErr_SetString(PyExc_ValueError, "Cannot set array with existing base to WRITEBACKIFCOPY");
        Py_DECREF((PyObject *)base);
        return -1;
    }
    if (pccnp_fail_unless_writeable(base, "WRITEBACKIFCOPY base") != 0) {
        Py_DECREF((PyObject *)base);
        return -1;
    }
    array->base = (PyObject *)base;
    pccnp_enable_flags(arr, NPY_ARRAY_WRITEBACKIFCOPY);
    pccnp_clear_flags(base, NPY_ARRAY_WRITEABLE);
    return 0;
}

static int pccnp_resolve_writeback_if_copy(PyArrayObject *self) {
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (array->base == NULL ||
        (pccnp_flags(self) & NPY_ARRAY_WRITEBACKIFCOPY) == 0) {
        return 0;
    }
    PyObject *base_obj = array->base;
    if (!pccnp_is_array_object(base_obj)) {
        PyErr_SetString(PyExc_TypeError, "WRITEBACKIFCOPY base is not a pcc NumPy array");
        return -1;
    }
    pccnp_enable_flags((PyArrayObject *)base_obj, NPY_ARRAY_WRITEABLE);
    pccnp_clear_flags(self, NPY_ARRAY_WRITEBACKIFCOPY);
    int rc = pccnp_copy_into((PyArrayObject *)base_obj, self);
    array->base = NULL;
    Py_DECREF(base_obj);
    return rc == 0 ? 1 : rc;
}

static void *pccnp_datamem_new(size_t size) {
    return PyMem_RawMalloc(size);
}

static void pccnp_datamem_free(void *ptr) {
    PyMem_RawFree(ptr);
}

static void *pccnp_datamem_renew(void *ptr, size_t size) {
    return PyMem_RawRealloc(ptr, size);
}

static void *pccnp_datamem_new_zeroed(size_t nelem, size_t elsize) {
    return PyMem_RawCalloc(nelem, elsize);
}

static PyObject *pccnp_datamem_get_handler(void) {
    Py_INCREF(Py_None);
    return Py_None;
}

static void *pccnp_datamem_user_new(npy_uintp size, PyObject *handler) {
    (void)handler;
    return PyMem_RawMalloc((size_t)size);
}

static void pccnp_datamem_user_free(void *ptr, npy_uintp size, PyObject *handler) {
    (void)size;
    (void)handler;
    PyMem_RawFree(ptr);
}

static void *pccnp_datamem_user_renew(void *ptr, size_t size, PyObject *handler) {
    (void)handler;
    return PyMem_RawRealloc(ptr, size);
}

static void *pccnp_datamem_user_new_zeroed(
    size_t nelem,
    size_t elsize,
    PyObject *handler
) {
    (void)handler;
    return PyMem_RawCalloc(nelem, elsize);
}

static void pccnp_discard_writeback_if_copy(PyArrayObject *self) {
    if (self == NULL) {
        return;
    }
    if (!pccnp_is_array_object((PyObject *)self)) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL ||
        array->base == NULL ||
        (pccnp_flags(self) & NPY_ARRAY_WRITEBACKIFCOPY) == 0) {
        return;
    }
    PyObject *base_obj = array->base;
    if (pccnp_is_array_object(base_obj)) {
        pccnp_enable_flags((PyArrayObject *)base_obj, NPY_ARRAY_WRITEABLE);
    }
    pccnp_clear_flags(self, NPY_ARRAY_WRITEBACKIFCOPY);
    array->base = NULL;
    Py_DECREF(base_obj);
}

static void pccnp_dims_clear(PyArray_Dims *dims) {
    if (dims == NULL) return;
    PyMem_Free(dims->ptr);
    dims->ptr = NULL;
    dims->len = 0;
}

static int pccnp_shape_from_object(PyObject *shape, PyArray_Dims *out) {
    if (out == NULL) {
        PyErr_SetString(PyExc_ValueError, "shape output is required");
        return -1;
    }
    out->ptr = NULL;
    out->len = 0;
    if (shape == NULL) {
        PyErr_SetString(PyExc_ValueError, "shape object is required");
        return -1;
    }

    if (PyLong_Check(shape)) {
        long value = PyLong_AsLong(shape);
        if (PyErr_Occurred() != NULL) return -1;
        if (value < 0) {
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            return -1;
        }
        out->ptr = (npy_intp *)PyMem_Calloc(1, sizeof(npy_intp));
        if (out->ptr == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        out->ptr[0] = (npy_intp)value;
        out->len = 1;
        return 0;
    }

    Py_ssize_t len = -1;
    int is_tuple = PyTuple_Check(shape);
    int is_list = PyList_Check(shape);
    if (is_tuple) {
        len = PyTuple_Size(shape);
    } else if (is_list) {
        len = PyList_Size(shape);
    } else {
        PyErr_SetString(PyExc_TypeError, "shape must be an int, tuple, or list");
        return -1;
    }
    if (len < 0) return -1;
    if (len > INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "shape dimension count is too large");
        return -1;
    }
    if (len == 0) {
        out->len = 0;
        return 0;
    }

    out->ptr = (npy_intp *)PyMem_Calloc((size_t)len, sizeof(npy_intp));
    if (out->ptr == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    out->len = (int)len;
    for (Py_ssize_t i = 0; i < len; i++) {
        PyObject *item = is_tuple ? PyTuple_GetItem(shape, i) : PyList_GetItem(shape, i);
        if (item == NULL) {
            pccnp_dims_clear(out);
            return -1;
        }
        if (!PyLong_Check(item)) {
            pccnp_dims_clear(out);
            PyErr_SetString(PyExc_TypeError, "shape dimensions must be integers");
            return -1;
        }
        long value = PyLong_AsLong(item);
        if (PyErr_Occurred() != NULL) {
            pccnp_dims_clear(out);
            return -1;
        }
        if (value < 0) {
            pccnp_dims_clear(out);
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            return -1;
        }
        out->ptr[i] = (npy_intp)value;
    }
    return 0;
}

static int pccnp_copy_object(PyArrayObject *dest_obj, PyObject *src_object) {
    if (dest_obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "dest is NULL in PyArray_CopyObject");
        return -1;
    }
    if (src_object == NULL) {
        PyErr_SetString(PyExc_ValueError, "src_object is NULL in PyArray_CopyObject");
        return -1;
    }
    PccNpArray *dest = pccnp_array_from_object(dest_obj);
    if (dest == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (dest->data == NULL && dest->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return -1;
    }

    if (pccnp_is_array_object(src_object) ||
        (PySequence_Check(src_object) &&
         !PyBytes_Check(src_object) &&
         !PyUnicode_Check(src_object))) {
        PyObject *src_array = pccnp_from_any(
            src_object,
            &dest->descr,
            dest->nd,
            dest->nd,
            0,
            NULL
        );
        if (src_array == NULL) {
            return -1;
        }
        int rc = pccnp_copy_into(dest_obj, (PyArrayObject *)src_array);
        Py_DECREF(src_array);
        return rc;
    }

    char *data = (char *)dest->data;
    for (size_t i = 0; i < dest->element_count; i++) {
        if (pccnp_setitem(
                dest_obj,
                data + (i * dest->itemsize),
                src_object
            ) != 0) {
            return -1;
        }
    }
    return 0;
}

static PyObject *pccnp_resize(
    PyArrayObject *arr_obj,
    PyArray_Dims *newshape,
    int refcheck,
    NPY_ORDER order
) {
    (void)refcheck;
    if (arr_obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "self is NULL in PyArray_Resize");
        return NULL;
    }
    if (newshape == NULL || (newshape->len > 0 && newshape->ptr == NULL)) {
        PyErr_SetString(PyExc_ValueError, "newshape is invalid in PyArray_Resize");
        return NULL;
    }
    if (newshape->len < 0) {
        PyErr_SetString(PyExc_ValueError, "negative dimension count in PyArray_Resize");
        return NULL;
    }
    if (order == NPY_FORTRANORDER) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Resize supports C-order arrays only"
        );
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (!array->owns_data || array->base != NULL) {
        PyErr_SetString(
            PyExc_ValueError,
            "cannot resize this pcc NumPy array because it does not own its data"
        );
        return NULL;
    }
    if (array->typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Resize does not resize object arrays"
        );
        return NULL;
    }

    size_t new_count = 1;
    for (int i = 0; i < newshape->len; i++) {
        if (newshape->ptr[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            return NULL;
        }
        if (pccnp_mul_size(new_count, (size_t)newshape->ptr[i], &new_count) != 0) {
            return NULL;
        }
    }
    size_t old_bytes = 0;
    size_t new_bytes = 0;
    if (pccnp_mul_size(array->element_count, array->itemsize, &old_bytes) != 0 ||
        pccnp_mul_size(new_count, array->itemsize, &new_bytes) != 0) {
        return NULL;
    }

    npy_intp *new_dims = NULL;
    npy_intp *new_strides = NULL;
    if (newshape->len > 0) {
        new_dims = (npy_intp *)PyMem_Calloc((size_t)newshape->len, sizeof(npy_intp));
        new_strides = (npy_intp *)PyMem_Calloc((size_t)newshape->len, sizeof(npy_intp));
        if (new_dims == NULL || new_strides == NULL) {
            PyMem_Free(new_dims);
            PyMem_Free(new_strides);
            PyErr_NoMemory();
            return NULL;
        }
        for (int i = 0; i < newshape->len; i++) {
            new_dims[i] = newshape->ptr[i];
        }
        size_t stride = array->itemsize;
        for (int i = newshape->len - 1; i >= 0; i--) {
            new_strides[i] = (npy_intp)stride;
            if (i > 0 && pccnp_mul_size(stride, (size_t)new_dims[i], &stride) != 0) {
                PyMem_Free(new_dims);
                PyMem_Free(new_strides);
                return NULL;
            }
        }
    }

    size_t allocation = new_bytes == 0 ? 1 : new_bytes;
    void *new_data = PyMem_Realloc(array->data, allocation);
    if (new_data == NULL) {
        PyMem_Free(new_dims);
        PyMem_Free(new_strides);
        PyErr_NoMemory();
        return NULL;
    }
    if (new_bytes > old_bytes) {
        memset((char *)new_data + old_bytes, 0, new_bytes - old_bytes);
    }

    PyMem_Free(array->dims);
    PyMem_Free(array->strides);
    array->dims = new_dims;
    array->strides = new_strides;
    array->data = new_data;
    array->nd = newshape->len;
    array->element_count = new_count;
    Py_RETURN_NONE;
}

static PyObject *pccnp_new_like_array(
    PyArrayObject *prototype,
    NPY_ORDER order,
    PyArray_Descr *dtype,
    int subok
) {
    (void)subok;
    PyObject *result = NULL;
    if (prototype == NULL) {
        PyErr_SetString(PyExc_ValueError, "prototype is NULL in PyArray_NewLikeArray");
        goto done;
    }

    PccNpArray *array = pccnp_array_from_object(prototype);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "prototype is not a pcc NumPy array");
        goto done;
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
            "pcc NumPy PyArray_NewLikeArray supports C-order arrays only"
        );
        goto done;
    }

    int typenum = array->typenum;
    size_t itemsize = array->itemsize;
    if (dtype != NULL) {
        typenum = dtype->type_num;
        itemsize = pccnp_itemsize_from_descr(dtype);
    }
    if (typenum == NPY_OBJECT) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_NewLikeArray does not allocate object arrays"
        );
        goto done;
    }
    if (itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "itemsize cannot be zero in descriptor");
        goto done;
    }

    result = pccnp_array_new_with_itemsize(
        array->nd,
        array->dims,
        typenum,
        itemsize,
        NULL,
        1
    );

done:
    Py_XDECREF((PyObject *)dtype);
    return result;
}

static PyObject *pccnp_newshape_impl(
    PyArrayObject *self,
    PyArray_Dims *newshape,
    NPY_ORDER order,
    const char *api_name
) {
    if (self == NULL) {
        PyErr_Format(PyExc_ValueError, "self is NULL in %s", api_name);
        return NULL;
    }
    if (!pccnp_is_array_object((PyObject *)self)) {
        PyErr_Format(PyExc_TypeError, "%s expects a pcc NumPy array", api_name);
        return NULL;
    }
    if (newshape == NULL || (newshape->len > 0 && newshape->ptr == NULL)) {
        PyErr_Format(PyExc_ValueError, "newshape is invalid in %s", api_name);
        return NULL;
    }
    if (newshape->len < 0) {
        PyErr_SetString(PyExc_ValueError, "negative dimension count");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_Format(PyExc_TypeError, "%s expects a pcc NumPy array", api_name);
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (order == NPY_ANYORDER || order == NPY_KEEPORDER) {
        order = NPY_CORDER;
    }
    if (order == NPY_FORTRANORDER && array->nd <= 1 && newshape->len <= 1) {
        order = NPY_CORDER;
    }
    if (order != NPY_CORDER) {
        PyErr_Format(
            PyExc_NotImplementedError,
            "pcc NumPy %s supports C-order arrays only",
            api_name
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_Format(
            PyExc_NotImplementedError,
            "pcc NumPy %s supports C-order arrays only",
            api_name
        );
        return NULL;
    }

    size_t new_count = 1;
    for (int i = 0; i < newshape->len; i++) {
        if (newshape->ptr[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "negative array dimension");
            return NULL;
        }
        if (pccnp_mul_size(new_count, (size_t)newshape->ptr[i], &new_count) != 0) {
            return NULL;
        }
    }
    if (new_count != array->element_count) {
        PyErr_SetString(PyExc_ValueError, "cannot reshape array to requested size");
        return NULL;
    }

    PyObject *result = pccnp_array_new_with_itemsize(
        newshape->len,
        newshape->ptr,
        array->typenum,
        array->itemsize,
        array->data,
        0
    );
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *view = pccnp_array_from_object((PyArrayObject *)result);
    if (view == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    Py_INCREF((PyObject *)self);
    view->base = (PyObject *)self;
    return result;
}

static PyObject *pccnp_newshape(
    PyArrayObject *self,
    PyArray_Dims *newshape,
    NPY_ORDER order
) {
    return pccnp_newshape_impl(self, newshape, order, "PyArray_Newshape");
}

static PyObject *pccnp_reshape(PyArrayObject *self, PyObject *shape) {
    PyArray_Dims newshape = {NULL, 0};
    if (pccnp_shape_from_object(shape, &newshape) != 0) {
        return NULL;
    }
    PyObject *result = pccnp_newshape_impl(
        self,
        &newshape,
        NPY_CORDER,
        "PyArray_Reshape"
    );
    pccnp_dims_clear(&newshape);
    return result;
}

static PyObject *pccnp_view(
    PyArrayObject *self,
    PyArray_Descr *type,
    PyTypeObject *pytype
) {
    PyObject *result = NULL;
    if (self == NULL) {
        PyErr_SetString(PyExc_ValueError, "self is NULL in PyArray_View");
        goto done;
    }
    if (pytype != NULL && pytype != &pccnp_array_type) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_View does not support array subtypes"
        );
        goto done;
    }
    if (type != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_View does not support dtype reinterpretation"
        );
        goto done;
    }

    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        goto done;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        goto done;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_View supports C-order arrays only"
        );
        goto done;
    }

    result = pccnp_array_new_with_itemsize(
        array->nd,
        array->dims,
        array->typenum,
        array->itemsize,
        array->data,
        0
    );
    if (result == NULL) {
        goto done;
    }
    PccNpArray *view = pccnp_array_from_object((PyArrayObject *)result);
    if (view == NULL) {
        Py_DECREF(result);
        result = NULL;
        goto done;
    }
    Py_INCREF((PyObject *)self);
    view->base = (PyObject *)self;

done:
    Py_XDECREF((PyObject *)type);
    return result;
}

static PyObject *pccnp_squeeze(PyArrayObject *self) {
    if (self == NULL) {
        PyErr_SetString(PyExc_ValueError, "self is NULL in PyArray_Squeeze");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Squeeze supports C-order arrays only"
        );
        return NULL;
    }

    int kept_nd = 0;
    int has_unit = 0;
    for (int i = 0; i < array->nd; i++) {
        if (array->dims[i] == 1) {
            has_unit = 1;
        } else {
            kept_nd++;
        }
    }
    if (!has_unit) {
        Py_INCREF((PyObject *)self);
        return (PyObject *)self;
    }

    npy_intp *dims = NULL;
    if (kept_nd > 0) {
        dims = (npy_intp *)PyMem_Calloc((size_t)kept_nd, sizeof(npy_intp));
        if (dims == NULL) {
            PyErr_NoMemory();
            return NULL;
        }
    }
    int out_i = 0;
    for (int i = 0; i < array->nd; i++) {
        if (array->dims[i] != 1) {
            dims[out_i++] = array->dims[i];
        }
    }

    PyObject *result = pccnp_array_new_with_itemsize(
        kept_nd,
        dims,
        array->typenum,
        array->itemsize,
        array->data,
        0
    );
    PyMem_Free(dims);
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *squeezed = pccnp_array_from_object((PyArrayObject *)result);
    if (squeezed == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    Py_INCREF((PyObject *)self);
    squeezed->base = (PyObject *)self;
    return result;
}

static PyObject *pccnp_transpose(PyArrayObject *self, PyArray_Dims *permute) {
    if (self == NULL) {
        PyErr_SetString(PyExc_ValueError, "self is NULL in PyArray_Transpose");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Transpose supports C-order arrays only"
        );
        return NULL;
    }

    int nd = array->nd;
    npy_intp *dims = NULL;
    npy_intp *strides = NULL;
    unsigned char *seen = NULL;
    PyObject *result = NULL;
    if (nd > 0) {
        dims = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
        strides = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
        seen = (unsigned char *)PyMem_Calloc((size_t)nd, sizeof(unsigned char));
        if (dims == NULL || strides == NULL || seen == NULL) {
            PyErr_NoMemory();
            goto done;
        }
    }

    if (permute != NULL && permute->len != nd) {
        PyErr_SetString(PyExc_ValueError, "transpose permutation length mismatch");
        goto done;
    }
    if (permute != NULL && nd > 0 && permute->ptr == NULL) {
        PyErr_SetString(PyExc_ValueError, "transpose permutation axes are NULL");
        goto done;
    }

    for (int i = 0; i < nd; i++) {
        npy_intp axis = permute == NULL ? (npy_intp)(nd - 1 - i) : permute->ptr[i];
        if (axis < 0 || axis >= nd) {
            PyErr_SetString(PyExc_ValueError, "transpose permutation axis out of range");
            goto done;
        }
        if (seen[axis]) {
            PyErr_SetString(PyExc_ValueError, "transpose permutation axis repeated");
            goto done;
        }
        seen[axis] = 1;
        dims[i] = array->dims[axis];
        strides[i] = array->strides[axis];
    }

    result = pccnp_array_new_with_itemsize(
        nd,
        dims,
        array->typenum,
        array->itemsize,
        array->data,
        0
    );
    if (result == NULL) {
        goto done;
    }
    PccNpArray *view = pccnp_array_from_object((PyArrayObject *)result);
    if (view == NULL) {
        Py_DECREF(result);
        result = NULL;
        goto done;
    }
    for (int i = 0; i < nd; i++) {
        view->strides[i] = strides[i];
    }
    Py_INCREF((PyObject *)self);
    view->base = (PyObject *)self;

done:
    PyMem_Free(dims);
    PyMem_Free(strides);
    PyMem_Free(seen);
    return result;
}

static PyObject *pccnp_swap_axes(PyArrayObject *self, int axis1, int axis2) {
    if (self == NULL) {
        PyErr_SetString(PyExc_ValueError, "self is NULL in PyArray_SwapAxes");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(self);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    int nd = array->nd;
    if (axis1 < 0) axis1 += nd;
    if (axis2 < 0) axis2 += nd;
    if (axis1 < 0 || axis1 >= nd || axis2 < 0 || axis2 >= nd) {
        PyErr_SetString(PyExc_ValueError, "swapaxes axis out of range");
        return NULL;
    }

    npy_intp *axes = NULL;
    if (nd > 0) {
        axes = (npy_intp *)PyMem_Calloc((size_t)nd, sizeof(npy_intp));
        if (axes == NULL) {
            PyErr_NoMemory();
            return NULL;
        }
    }
    for (int i = 0; i < nd; i++) {
        axes[i] = (npy_intp)i;
    }
    axes[axis1] = (npy_intp)axis2;
    axes[axis2] = (npy_intp)axis1;
    PyArray_Dims permute = {axes, nd};
    PyObject *result = pccnp_transpose(self, &permute);
    PyMem_Free(axes);
    return result;
}

static PyObject *pccnp_ravel(PyArrayObject *arr_obj, NPY_ORDER order) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (
        order != NPY_CORDER &&
        order != NPY_ANYORDER &&
        order != NPY_KEEPORDER &&
        order != NPY_FORTRANORDER
    ) {
        PyErr_SetString(PyExc_NotImplementedError, "unsupported order in PyArray_Ravel");
        return NULL;
    }
    if (order == NPY_FORTRANORDER && array->nd > 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Ravel does not support multidimensional Fortran order"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Ravel supports C-order arrays only"
        );
        return NULL;
    }
    if (array->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "flattened array is too large");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        array->typenum,
        array->itemsize,
        array->data,
        0
    );
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *flat = pccnp_array_from_object((PyArrayObject *)result);
    if (flat == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    Py_INCREF((PyObject *)arr_obj);
    flat->base = (PyObject *)arr_obj;
    return result;
}

static int pccnp_adjust_axis(int *axis, int ndim) {
    if (axis == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_CheckAxis axis pointer is NULL");
        return -1;
    }
    if (*axis < 0) {
        *axis += ndim;
    }
    if (*axis < 0 || *axis >= ndim) {
        PyErr_SetString(PyExc_ValueError, "axis is out of bounds for pcc NumPy array");
        return -1;
    }
    return 0;
}

static int pccnp_check_axis_flags(PyArrayObject *arr_obj, int flags) {
    if (flags == 0) {
        return 0;
    }
    int supported = NPY_ARRAY_CARRAY | NPY_ARRAY_CARRAY_RO |
        NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED | NPY_ARRAY_WRITEABLE;
    if ((flags & ~supported) != 0) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CheckAxis supports C-array flag checks only"
        );
        return -1;
    }

    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CheckAxis expects a pcc NumPy array");
        return -1;
    }
    if ((flags & NPY_ARRAY_C_CONTIGUOUS) != 0 && !pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CheckAxis does not copy non-C-order arrays for flag coercion"
        );
        return -1;
    }

    int actual = pccnp_flags(arr_obj);
    int non_contig_flags = flags & ~NPY_ARRAY_C_CONTIGUOUS;
    if ((actual & non_contig_flags) != non_contig_flags) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_CheckAxis cannot satisfy requested array flags"
        );
        return -1;
    }
    return 0;
}

static PyObject *pccnp_check_axis(PyArrayObject *arr_obj, int *axis, int flags) {
    if (axis == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_CheckAxis axis pointer is NULL");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "PyArray_CheckAxis expects a pcc NumPy array");
        return NULL;
    }

    PyObject *candidate = NULL;
    int original_axis = *axis;
    if (*axis == NPY_RAVEL_AXIS || array->nd == 0) {
        if (array->nd != 1) {
            candidate = pccnp_ravel(arr_obj, NPY_CORDER);
            if (candidate == NULL) {
                *axis = 0;
                return NULL;
            }
            if (original_axis == NPY_RAVEL_AXIS) {
                PccNpArray *flat = pccnp_array_from_object((PyArrayObject *)candidate);
                if (flat == NULL) {
                    Py_DECREF(candidate);
                    PyErr_SetString(PyExc_TypeError, "PyArray_CheckAxis expected ravel to return a pcc NumPy array");
                    return NULL;
                }
                *axis = flat->nd - 1;
            }
        } else {
            candidate = (PyObject *)arr_obj;
            Py_INCREF(candidate);
            *axis = 0;
        }
        if (flags == 0 && *axis == 0) {
            return candidate;
        }
    } else {
        candidate = (PyObject *)arr_obj;
        Py_INCREF(candidate);
    }

    if (pccnp_check_axis_flags((PyArrayObject *)candidate, flags) != 0) {
        Py_DECREF(candidate);
        return NULL;
    }

    PccNpArray *candidate_array = pccnp_array_from_object((PyArrayObject *)candidate);
    if (candidate_array == NULL) {
        Py_DECREF(candidate);
        PyErr_SetString(PyExc_TypeError, "PyArray_CheckAxis expected a pcc NumPy array");
        return NULL;
    }
    if (pccnp_adjust_axis(axis, candidate_array->nd) != 0) {
        Py_DECREF(candidate);
        return NULL;
    }
    return candidate;
}

static PyObject *pccnp_flatten(PyArrayObject *arr_obj, NPY_ORDER order) {
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->data == NULL && array->element_count != 0) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    if (
        order != NPY_CORDER &&
        order != NPY_ANYORDER &&
        order != NPY_KEEPORDER &&
        order != NPY_FORTRANORDER
    ) {
        PyErr_SetString(PyExc_NotImplementedError, "unsupported order in PyArray_Flatten");
        return NULL;
    }
    if (order == NPY_FORTRANORDER && array->nd > 1) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Flatten does not support multidimensional Fortran order"
        );
        return NULL;
    }
    if (!pccnp_has_c_order_strides(array)) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_Flatten supports C-order arrays only"
        );
        return NULL;
    }
    if (array->element_count > (size_t)PY_SSIZE_T_MAX) {
        PyErr_SetString(PyExc_OverflowError, "flattened array is too large");
        return NULL;
    }

    npy_intp dims[1] = {(npy_intp)array->element_count};
    PyObject *result = pccnp_array_new_with_itemsize(
        1,
        dims,
        array->typenum,
        array->itemsize,
        NULL,
        1
    );
    if (result == NULL) {
        return NULL;
    }
    PccNpArray *flat = pccnp_array_from_object((PyArrayObject *)result);
    if (flat == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    size_t byte_count = 0;
    if (pccnp_mul_size(array->element_count, array->itemsize, &byte_count) != 0) {
        Py_DECREF(result);
        return NULL;
    }
    if (byte_count != 0) {
        memcpy(flat->data, array->data, byte_count);
    }
    return result;
}

