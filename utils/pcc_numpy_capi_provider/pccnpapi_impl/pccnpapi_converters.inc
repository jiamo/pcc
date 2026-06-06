/* C-API scalar/shape/order/clipmode converters and low-level array helpers.
 * Included by ../pccnpapi.c; keep this file free of external linkage.
 */

static int pccnp_get_endianness(void) {
    const union {
        uint32_t i;
        unsigned char c[4];
    } marker = {0x01020304u};
    if (marker.c[0] == 1) {
        return NPY_CPU_BIG;
    }
    if (marker.c[0] == 4) {
        return NPY_CPU_LITTLE;
    }
    return NPY_CPU_UNKNOWN_ENDIAN;
}

static unsigned int pccnp_get_ndarray_c_feature_version(void) {
    return (unsigned int)NPY_API_VERSION;
}

static unsigned int pccnp_get_ndarray_c_version(void) {
    return (unsigned int)NPY_ABI_VERSION;
}

static PyObject *pccnp_int_tuple_from_intp(int len, const npy_intp *vals) {
    PyObject *tuple = PyTuple_New((Py_ssize_t)len);
    if (tuple == NULL) return NULL;
    for (int i = 0; i < len; i++) {
        PyObject *item = PyLong_FromLongLong((long long)vals[i]);
        if (item == NULL) {
            Py_DECREF(tuple);
            return NULL;
        }
        if (PyTuple_SetItem(tuple, (Py_ssize_t)i, item) != 0) {
            Py_DECREF(item);
            Py_DECREF(tuple);
            return NULL;
        }
    }
    return tuple;
}

static int pccnp_clipmode_converter(PyObject *obj, NPY_CLIPMODE *mode) {
    if (mode == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy clipmode output is NULL");
        return -1;
    }
    if (obj == NULL || obj == Py_None) {
        *mode = NPY_RAISE;
        return 0;
    }
    const char *text = NULL;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8(obj);
    } else if (PyBytes_Check(obj)) {
        text = PyBytes_AsString(obj);
    }
    if (text != NULL) {
        if (strcmp(text, "raise") == 0) {
            *mode = NPY_RAISE;
            return 0;
        }
        if (strcmp(text, "wrap") == 0) {
            *mode = NPY_WRAP;
            return 0;
        }
        if (strcmp(text, "clip") == 0) {
            *mode = NPY_CLIP;
            return 0;
        }
        PyErr_SetString(PyExc_ValueError, "pcc NumPy clipmode must be 'clip', 'raise', or 'wrap'");
        return -1;
    }
    if (PyErr_Occurred() != NULL) {
        return -1;
    }
    long value = PyLong_AsLong(obj);
    if (value == -1 && PyErr_Occurred() != NULL) {
        PyErr_SetString(PyExc_TypeError, "pcc NumPy clipmode not understood");
        return -1;
    }
    if (value == (long)NPY_RAISE || value == (long)NPY_WRAP || value == (long)NPY_CLIP) {
        *mode = (NPY_CLIPMODE)value;
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "pcc NumPy integer clipmode must be NPY_RAISE, NPY_WRAP, or NPY_CLIP");
    return -1;
}

static int pccnp_convert_clipmode_sequence(PyObject *obj, NPY_CLIPMODE *modes, int n) {
    if (n < 0) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy clipmode sequence length is negative");
        return NPY_FAIL;
    }
    if (n > 0 && modes == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy clipmode sequence output is NULL");
        return NPY_FAIL;
    }
    if (n == 0) {
        return NPY_SUCCEED;
    }

    if (obj != NULL && (PyTuple_Check(obj) || PyList_Check(obj))) {
        Py_ssize_t length = PySequence_Size(obj);
        if (length < 0) {
            return NPY_FAIL;
        }
        if (length != (Py_ssize_t)n) {
            PyErr_SetString(PyExc_ValueError, "pcc NumPy clipmode sequence has wrong length");
            return NPY_FAIL;
        }
        for (int i = 0; i < n; i++) {
            PyObject *item = PySequence_GetItem(obj, (Py_ssize_t)i);
            if (item == NULL) {
                return NPY_FAIL;
            }
            int ok = pccnp_clipmode_converter(item, &modes[i]);
            Py_DECREF(item);
            if (ok != NPY_SUCCEED) {
                return NPY_FAIL;
            }
        }
        return NPY_SUCCEED;
    }

    if (pccnp_clipmode_converter(obj, &modes[0]) != NPY_SUCCEED) {
        return NPY_FAIL;
    }
    for (int i = 1; i < n; i++) {
        modes[i] = modes[0];
    }
    return NPY_SUCCEED;
}

static int pccnp_output_converter(PyObject *obj, PyArrayObject **address) {
    if (address == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy output converter address is NULL");
        return -1;
    }
    if (obj == NULL || obj == Py_None) {
        *address = NULL;
        return 0;
    }
    if (pccnp_is_array_object(obj)) {
        *address = (PyArrayObject *)obj;
        return 0;
    }
    *address = NULL;
    PyErr_SetString(PyExc_TypeError, "output must be a pcc NumPy array");
    return -1;
}

static int pccnp_searchside_converter(PyObject *obj, void *addr) {
    if (addr == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy search side output is NULL");
        return -1;
    }
    const char *text = NULL;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8(obj);
    } else if (PyBytes_Check(obj)) {
        text = PyBytes_AsString(obj);
    } else {
        PyErr_SetString(PyExc_TypeError, "search side must be 'left' or 'right'");
        return -1;
    }
    if (text == NULL) {
        return -1;
    }
    NPY_SEARCHSIDE *side = (NPY_SEARCHSIDE *)addr;
    if (strcmp(text, "left") == 0) {
        *side = NPY_SEARCHLEFT;
        return 0;
    }
    if (strcmp(text, "right") == 0) {
        *side = NPY_SEARCHRIGHT;
        return 0;
    }
    PyErr_SetString(PyExc_ValueError, "search side must be one of 'left' or 'right'");
    return -1;
}

static int pccnp_byteorder_converter(PyObject *obj, char *endian) {
    if (endian == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy byteorder output is NULL");
        return NPY_FAIL;
    }
    const char *text = NULL;
    Py_ssize_t length = 0;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8AndSize(obj, &length);
    } else if (PyBytes_Check(obj)) {
        if (PyBytes_AsStringAndSize(obj, (char **)&text, &length) != 0) {
            return NPY_FAIL;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "byteorder must be one of 'big', 'little', 'native', 'ignore', or 'swap'");
        return NPY_FAIL;
    }
    if (text == NULL) {
        return NPY_FAIL;
    }
    if (length < 1) {
        PyErr_SetString(PyExc_ValueError, "byteorder is not recognized");
        return NPY_FAIL;
    }
    char first = text[0];
    if (first == NPY_BIG || first == NPY_LITTLE ||
        first == NPY_NATIVE || first == NPY_IGNORE) {
        *endian = first;
        return NPY_SUCCEED;
    }
    if (first == 'b' || first == 'B') {
        *endian = NPY_BIG;
        return NPY_SUCCEED;
    }
    if (first == 'l' || first == 'L') {
        *endian = NPY_LITTLE;
        return NPY_SUCCEED;
    }
    if (first == 'n' || first == 'N') {
        *endian = NPY_NATIVE;
        return NPY_SUCCEED;
    }
    if (first == 'i' || first == 'I') {
        *endian = NPY_IGNORE;
        return NPY_SUCCEED;
    }
    if (first == 's' || first == 'S') {
        *endian = NPY_SWAP;
        return NPY_SUCCEED;
    }
    PyErr_SetString(PyExc_ValueError, "byteorder is not recognized");
    return NPY_FAIL;
}

static int pccnp_sortkind_converter(PyObject *obj, NPY_SORTKIND *sortkind) {
    if (sortkind == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy sort kind output is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        return NPY_SUCCEED;
    }

    const char *text = NULL;
    Py_ssize_t length = 0;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8AndSize(obj, &length);
    } else if (PyBytes_Check(obj)) {
        if (PyBytes_AsStringAndSize(obj, (char **)&text, &length) != 0) {
            return NPY_FAIL;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "sort kind must be one of 'quick', 'heap', or 'stable'");
        return NPY_FAIL;
    }
    if (text == NULL) {
        return NPY_FAIL;
    }
    if (length < 1) {
        PyErr_SetString(PyExc_ValueError, "sort kind is not recognized");
        return NPY_FAIL;
    }

    char first = text[0];
    if (first == 'q' || first == 'Q') {
        *sortkind = NPY_QUICKSORT;
        return NPY_SUCCEED;
    }
    if (first == 'h' || first == 'H') {
        *sortkind = NPY_HEAPSORT;
        return NPY_SUCCEED;
    }
    if (first == 'm' || first == 'M') {
        *sortkind = NPY_MERGESORT;
        return NPY_SUCCEED;
    }
    if (first == 's' || first == 'S') {
        *sortkind = NPY_STABLESORT;
        return NPY_SUCCEED;
    }

    PyErr_SetString(PyExc_ValueError, "sort kind is not recognized");
    return NPY_FAIL;
}

static int pccnp_selectkind_converter(PyObject *obj, NPY_SELECTKIND *selectkind) {
    if (selectkind == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy select kind output is NULL");
        return NPY_FAIL;
    }

    const char *text = NULL;
    Py_ssize_t length = 0;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8AndSize(obj, &length);
    } else if (PyBytes_Check(obj)) {
        if (PyBytes_AsStringAndSize(obj, (char **)&text, &length) != 0) {
            return NPY_FAIL;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "select kind must be 'introselect'");
        return NPY_FAIL;
    }
    if (text == NULL) {
        return NPY_FAIL;
    }

    if (length == 11 && strcmp(text, "introselect") == 0) {
        *selectkind = NPY_INTROSELECT;
        return NPY_SUCCEED;
    }

    PyErr_SetString(PyExc_ValueError, "select kind is not recognized");
    return NPY_FAIL;
}

static int pccnp_order_converter(PyObject *obj, NPY_ORDER *order) {
    if (order == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy order output is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        return NPY_SUCCEED;
    }

    const char *text = NULL;
    Py_ssize_t length = 0;
    if (PyUnicode_Check(obj)) {
        text = PyUnicode_AsUTF8AndSize(obj, &length);
    } else if (PyBytes_Check(obj)) {
        if (PyBytes_AsStringAndSize(obj, (char **)&text, &length) != 0) {
            return NPY_FAIL;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "order must be one of 'C', 'F', 'A', or 'K'");
        return NPY_FAIL;
    }
    if (text == NULL) {
        return NPY_FAIL;
    }

    if (length == 1 && (text[0] == 'C' || text[0] == 'c')) {
        *order = NPY_CORDER;
        return NPY_SUCCEED;
    }
    if (length == 1 && (text[0] == 'F' || text[0] == 'f')) {
        *order = NPY_FORTRANORDER;
        return NPY_SUCCEED;
    }
    if (length == 1 && (text[0] == 'A' || text[0] == 'a')) {
        *order = NPY_ANYORDER;
        return NPY_SUCCEED;
    }
    if (length == 1 && (text[0] == 'K' || text[0] == 'k')) {
        *order = NPY_KEEPORDER;
        return NPY_SUCCEED;
    }
    PyErr_SetString(PyExc_ValueError, "order must be one of 'C', 'F', 'A', or 'K'");
    return NPY_FAIL;
}

static int pccnp_bool_converter(PyObject *obj, npy_bool *value) {
    if (value == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy bool output is NULL");
        return NPY_FAIL;
    }
    int bool_value = PyObject_IsTrue(obj);
    if (bool_value < 0) {
        return NPY_FAIL;
    }
    *value = bool_value ? 1 : 0;
    return NPY_SUCCEED;
}

static int pccnp_optional_bool_converter(PyObject *obj, int *value) {
    if (value == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy optional bool output is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        return NPY_SUCCEED;
    }
    int bool_value = PyObject_IsTrue(obj);
    if (bool_value < 0) {
        return NPY_FAIL;
    }
    *value = bool_value ? 1 : 0;
    return NPY_SUCCEED;
}

static int pccnp_axis_converter(PyObject *obj, int *axis) {
    if (axis == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy axis output is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        *axis = NPY_RAVEL_AXIS;
        return NPY_SUCCEED;
    }
    int parsed = pccnp_pyint_as_int(obj);
    if (parsed == -1 && PyErr_Occurred() != NULL) {
        return NPY_FAIL;
    }
    *axis = parsed;
    return NPY_SUCCEED;
}

static void *pccnp_get_ptr(PyArrayObject *arr_obj, const npy_intp *indices) {
    if (!pccnp_is_array_object((PyObject *)arr_obj)) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object(arr_obj);
    if (array == NULL) return NULL;
    if (array->data == NULL) {
        PyErr_SetString(PyExc_ValueError, "array data pointer is NULL");
        return NULL;
    }
    char *ptr = (char *)array->data;
    for (int i = 0; i < array->nd; i++) {
        ptr += array->strides[i] * indices[i];
    }
    return (void *)ptr;
}

static int pccnp_element_strides(PyObject *obj) {
    if (!pccnp_is_array_object(obj)) {
        return 0;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)obj);
    if (array == NULL || array->itemsize == 0) return 0;
    for (int i = 0; i < array->nd; i++) {
        if ((array->strides[i] % (npy_intp)array->itemsize) != 0) {
            return 0;
        }
    }
    return 1;
}

static int pccnp_valid_type(int typenum) {
    return pccnp_descr_for_type(typenum) != NULL;
}

static void pccnp_item_incref(char *data, PyArray_Descr *descr) {
    if (data == NULL || descr == NULL || descr->type_num != NPY_OBJECT) {
        return;
    }
    PyObject *item = NULL;
    memcpy(&item, data, sizeof(item));
    Py_XINCREF(item);
}

static void pccnp_item_xdecref(char *data, PyArray_Descr *descr) {
    if (data == NULL || descr == NULL || descr->type_num != NPY_OBJECT) {
        return;
    }
    PyObject *item = NULL;
    memcpy(&item, data, sizeof(item));
    Py_XDECREF(item);
}

static PyObject *pccnp_simple_new(int nd, npy_intp *dims, int typenum) {
    return pccnp_array_new_common(nd, dims, typenum, NULL, 1);
}

static PyObject *pccnp_simple_new_from_data(
    int nd,
    npy_intp *dims,
    int typenum,
    void *data
) {
    return pccnp_array_new_common(nd, dims, typenum, data, 0);
}

static PyObject *pccnp_empty(
    int nd,
    npy_intp *dims,
    PyArray_Descr *descr,
    int is_f_order
) {
    if (is_f_order) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub only supports C-order allocation"
        );
        return NULL;
    }
    if (descr == NULL) {
        PyErr_SetString(PyExc_ValueError, "array descriptor is required");
        return NULL;
    }
    return pccnp_array_new_with_itemsize(
        nd,
        dims,
        descr->type_num,
        pccnp_itemsize_from_descr(descr),
        NULL,
        1
    );
}

static PyObject *pccnp_zeros(
    int nd,
    npy_intp *dims,
    PyArray_Descr *descr,
    int is_f_order
) {
    return pccnp_empty(nd, dims, descr, is_f_order);
}

static PyObject *pccnp_new_from_descr(
    PyTypeObject *subtype,
    PyArray_Descr *descr,
    int nd,
    npy_intp *dims,
    npy_intp *strides,
    void *data,
    int flags,
    PyObject *obj
) {
    (void)subtype;
    (void)flags;
    if (descr == NULL) {
        PyErr_SetString(PyExc_ValueError, "array descriptor is required");
        return NULL;
    }
    if (strides != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy C API stub only supports C-order descriptor allocation"
        );
        return NULL;
    }
    PyObject *array_obj = pccnp_array_new_with_itemsize(
        nd,
        dims,
        descr->type_num,
        pccnp_itemsize_from_descr(descr),
        data,
        data == NULL
    );
    if (array_obj == NULL) return NULL;
    if (obj != NULL) {
        PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
        if (array == NULL) {
            Py_DECREF(array_obj);
            return NULL;
        }
        array->base = obj;
    }
    return array_obj;
}

static PyObject *pccnp_new(
    PyTypeObject *subtype,
    int nd,
    npy_intp *dims,
    int typenum,
    npy_intp *strides,
    void *data,
    int itemsize,
    int flags,
    PyObject *obj
) {
    (void)flags;
    if (subtype == NULL) {
        PyErr_SetString(PyExc_ValueError, "subtype is NULL in PyArray_New");
        return NULL;
    }
    if (subtype != &pccnp_array_type) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_New only supports the fake ndarray subtype"
        );
        return NULL;
    }
    if (strides != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_New only supports C-order allocation"
        );
        return NULL;
    }
    if (obj != NULL) {
        PyErr_SetString(
            PyExc_NotImplementedError,
            "pcc NumPy PyArray_New does not support base objects yet"
        );
        return NULL;
    }

    PyArray_Descr *descr = pccnp_descr_for_type(typenum);
    if (descr == NULL) {
        pccnp_raise_unsupported();
        return NULL;
    }
    size_t effective_itemsize = itemsize > 0
        ? (size_t)itemsize
        : pccnp_itemsize_from_descr(descr);
    if (effective_itemsize == 0) {
        PyErr_SetString(PyExc_ValueError, "data type must provide an itemsize");
        return NULL;
    }
    if (typenum != NPY_STRING) {
        size_t expected_itemsize = pccnp_itemsize_from_descr(descr);
        if (expected_itemsize == 0 || effective_itemsize != expected_itemsize) {
            PyErr_SetString(
                PyExc_ValueError,
                "itemsize does not match fixed-width dtype"
            );
            return NULL;
        }
    }
    return pccnp_array_new_with_itemsize(
        nd,
        dims,
        typenum,
        effective_itemsize,
        data,
        data == NULL
    );
}

static int pccnp_ndim(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : array->nd;
}

static npy_intp *pccnp_dims(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->dims;
}

static npy_intp *pccnp_strides(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->strides;
}

static void *pccnp_data(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->data;
}

static PyArray_Descr *pccnp_descr(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) return NULL;
    return &array->descr;
}

static PyObject *pccnp_base(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? NULL : array->base;
}

static int pccnp_set_base_object(PyArrayObject *arr, PyObject *obj) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return -1;
    }
    if (obj == NULL) {
        PyErr_SetString(PyExc_ValueError, "array base object is required");
        return -1;
    }
    if (array->base != NULL) {
        PyErr_SetString(PyExc_ValueError, "array already has a base object");
        return -1;
    }
    array->base = obj;
    return 0;
}

static PyObject *pccnp_return(PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return NULL;
    }
    if (array->nd == 0 && array->element_count == 1 && array->data != NULL) {
        PyObject *item = pccnp_getitem(arr, array->data);
        Py_DECREF((PyObject *)arr);
        return item;
    }
    return (PyObject *)arr;
}

static npy_intp pccnp_size(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : (npy_intp)array->element_count;
}

static npy_intp pccnp_size_object(PyObject *op) {
    if (op == NULL) return 0;
    if (PyObject_TypeCheck(op, &pccnp_array_type) ||
        PyCapsule_IsValid(op, PCCNP_ARRAY_OBJECT_CAPSULE)) {
        return pccnp_size((PyArrayObject *)op);
    }
    return 0;
}

static int pccnp_itemsize_api(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    return array == NULL ? -1 : (int)array->itemsize;
}

static int pccnp_array_check(PyObject *obj) {
    if (obj == NULL) return 0;
    return pccnp_is_array_object(obj);
}

static int pccnp_array_check_exact(PyObject *obj) {
    return pccnp_array_check(obj);
}

static int pccnp_base_flags(PccNpArray *array) {
    int flags = NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE;
    if (array->nd <= 1) {
        flags |= NPY_ARRAY_F_CONTIGUOUS;
    }
    if (array->owns_data) {
        flags |= NPY_ARRAY_OWNDATA;
    }
    size_t alignment = array->descr.alignment > 0 ? (size_t)array->descr.alignment : 1u;
    if (array->data == NULL || ((uintptr_t)array->data % alignment) == 0) {
        flags |= NPY_ARRAY_ALIGNED;
    }
    return flags;
}

static int pccnp_flags(const PyArrayObject *arr) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) return 0;
    int flags = pccnp_base_flags(array);
    flags |= array->enabled_flags;
    flags &= ~array->cleared_flags;
    return flags;
}

static void pccnp_enable_flags(PyArrayObject *arr, int flags) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    array->enabled_flags |= flags;
    array->cleared_flags &= ~flags;
}

static void pccnp_clear_flags(PyArrayObject *arr, int flags) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    array->cleared_flags |= flags;
    array->enabled_flags &= ~flags;
}

static void pccnp_update_flags(PyArrayObject *arr, int flags) {
    PccNpArray *array = pccnp_array_from_object(arr);
    if (array == NULL) {
        PyErr_SetString(PyExc_TypeError, "object is not a pcc NumPy array");
        return;
    }
    array->enabled_flags &= ~flags;
    array->cleared_flags &= ~flags;
}

static int pccnp_compare_lists(const npy_intp *lhs, const npy_intp *rhs, int n) {
    if (n < 0) return 0;
    if (lhs == NULL || rhs == NULL) return n == 0;
    for (int i = 0; i < n; i++) {
        if (lhs[i] != rhs[i]) return 0;
    }
    return 1;
}

static int pccnp_iter_shape_size(const npy_intp *dims, int nd, npy_intp *out) {
    if (nd < 0 || nd > NPY_MAXDIMS_LEGACY_ITERS) {
        PyErr_SetString(PyExc_ValueError, "unsupported iterator dimension count");
        return -1;
    }
    if (nd > 0 && dims == NULL) {
        PyErr_SetString(PyExc_ValueError, "iterator dimensions are required");
        return -1;
    }
    size_t size = 1;
    for (int i = 0; i < nd; i++) {
        if (dims[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "negative iterator dimension");
            return -1;
        }
        if (pccnp_mul_size(size, (size_t)dims[i], &size) != 0) {
            return -1;
        }
        if (size > (size_t)(((size_t)-1) >> 1)) {
            PyErr_SetString(PyExc_OverflowError, "iterator size overflow");
            return -1;
        }
    }
    *out = (npy_intp)size;
    return 0;
}

static int pccnp_array_is_c_contiguous(PccNpArray *array) {
    if (array == NULL) return 0;
    if (array->nd <= 1) return 1;
    npy_intp stride = (npy_intp)array->itemsize;
    for (int i = array->nd - 1; i >= 0; i--) {
        if (array->dims[i] == 0) return 1;
        if (array->strides[i] != stride) return 0;
        if (i > 0) {
            size_t next_stride = 0;
            if (pccnp_mul_size((size_t)stride, (size_t)array->dims[i], &next_stride) != 0 ||
                next_stride > (size_t)(((size_t)-1) >> 1)) {
                PyErr_Clear();
                return 0;
            }
            stride = (npy_intp)next_stride;
        }
    }
    return 1;
}

static void pccnp_iter_reset_direct(PyArrayIterObject *it) {
    it->index = 0;
    it->dataptr = it->ao == NULL ? NULL : (char *)pccnp_data(it->ao);
    if (it->nd_m1 >= 0) {
        memset(it->coordinates, 0, ((size_t)it->nd_m1 + 1u) * sizeof(npy_intp));
    }
}

static int pccnp_iter_init_shape(
    PyArrayIterObject *it,
    PyObject *array_obj,
    PccNpArray *array,
    const npy_intp *dims,
    int nd,
    const npy_intp *strides,
    int contiguous
) {
    npy_intp size = 0;
    if (pccnp_iter_shape_size(dims, nd, &size) != 0) {
        return -1;
    }
    it->nd_m1 = nd - 1;
    it->index = 0;
    it->size = size;
    memset(it->coordinates, 0, sizeof(it->coordinates));
    memset(it->dims_m1, 0, sizeof(it->dims_m1));
    memset(it->strides, 0, sizeof(it->strides));
    memset(it->backstrides, 0, sizeof(it->backstrides));
    memset(it->factors, 0, sizeof(it->factors));
    memset(it->bounds, 0, sizeof(it->bounds));
    memset(it->limits, 0, sizeof(it->limits));
    memset(it->limits_sizes, 0, sizeof(it->limits_sizes));
    it->translate = NULL;
    it->ao = (PyArrayObject *)array_obj;
    Py_INCREF(array_obj);
    it->dataptr = (char *)array->data;
    it->contiguous = contiguous ? 1 : 0;
    if (nd > 0) {
        it->factors[nd - 1] = 1;
    }
    for (int i = 0; i < nd; i++) {
        it->dims_m1[i] = dims[i] - 1;
        it->strides[i] = strides == NULL ? 0 : strides[i];
        it->backstrides[i] = it->strides[i] * it->dims_m1[i];
        if (i > 0) {
            it->factors[nd - i - 1] = it->factors[nd - i] * dims[nd - i];
        }
    }
    pccnp_iter_reset_direct(it);
    return 0;
}

static PyObject *pccnp_iter_new(PyObject *obj) {
    if (!pccnp_is_array_object(obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_IterNew requires a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)obj);
    if (array == NULL) return NULL;
    if (array->nd > NPY_MAXDIMS_LEGACY_ITERS) {
        PyErr_SetString(PyExc_RuntimeError, "iterator dimension count exceeds supported legacy limit");
        return NULL;
    }
    PyArrayIterObject *it = PyObject_New(PyArrayIterObject, &pccnp_array_iter_type);
    if (it == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    it->ao = NULL;
    it->dataptr = NULL;
    if (pccnp_iter_init_shape(
            it,
            obj,
            array,
            array->dims,
            array->nd,
            array->strides,
            pccnp_array_is_c_contiguous(array)
        ) != 0) {
        Py_DECREF((PyObject *)it);
        return NULL;
    }
    return (PyObject *)it;
}

static PyObject *pccnp_broadcast_to_shape(PyObject *obj, npy_intp *dims, int nd) {
    if (!pccnp_is_array_object(obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_BroadcastToShape requires a pcc NumPy array");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)obj);
    if (array == NULL) return NULL;
    if (nd < 0 || nd > NPY_MAXDIMS_LEGACY_ITERS || array->nd > nd ||
        (nd > 0 && dims == NULL)) {
        PyErr_SetString(PyExc_ValueError, "array is not broadcastable to correct shape");
        return NULL;
    }
    for (int i = 0; i < nd; i++) {
        if (dims[i] < 0) {
            PyErr_SetString(PyExc_ValueError, "array is not broadcastable to correct shape");
            return NULL;
        }
    }
    int diff = nd - array->nd;
    for (int i = 0; i < array->nd; i++) {
        npy_intp source_dim = array->dims[i];
        npy_intp target_dim = dims[i + diff];
        if (source_dim != 1 && source_dim != target_dim) {
            PyErr_SetString(PyExc_ValueError, "array is not broadcastable to correct shape");
            return NULL;
        }
    }

    npy_intp strides[NPY_MAXDIMS_LEGACY_ITERS];
    memset(strides, 0, sizeof(strides));
    int contiguous = pccnp_array_is_c_contiguous(array);
    for (int i = 0; i < nd; i++) {
        int source_axis = i - diff;
        if (source_axis < 0 || array->dims[source_axis] != dims[i]) {
            contiguous = 0;
            strides[i] = 0;
        } else {
            strides[i] = array->strides[source_axis];
        }
    }

    PyArrayIterObject *it = PyObject_New(PyArrayIterObject, &pccnp_array_iter_type);
    if (it == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    it->ao = NULL;
    it->dataptr = NULL;
    if (pccnp_iter_init_shape(it, obj, array, dims, nd, strides, contiguous) != 0) {
        Py_DECREF((PyObject *)it);
        return NULL;
    }
    return (PyObject *)it;
}

static int pccnp_iter_rebroadcast_shape(
    PyArrayIterObject *it,
    PccNpArray *array,
    const npy_intp *dims,
    int nd,
    const npy_intp *strides,
    int contiguous
) {
    if (it == NULL || array == NULL) {
        PyErr_SetString(PyExc_ValueError, "iterator rebroadcast requires iterator and array");
        return -1;
    }
    npy_intp size = 0;
    if (pccnp_iter_shape_size(dims, nd, &size) != 0) {
        return -1;
    }
    it->nd_m1 = nd - 1;
    it->index = 0;
    it->size = size;
    memset(it->coordinates, 0, sizeof(it->coordinates));
    memset(it->dims_m1, 0, sizeof(it->dims_m1));
    memset(it->strides, 0, sizeof(it->strides));
    memset(it->backstrides, 0, sizeof(it->backstrides));
    memset(it->factors, 0, sizeof(it->factors));
    memset(it->bounds, 0, sizeof(it->bounds));
    memset(it->limits, 0, sizeof(it->limits));
    memset(it->limits_sizes, 0, sizeof(it->limits_sizes));
    it->translate = NULL;
    it->dataptr = (char *)array->data;
    it->contiguous = contiguous ? 1 : 0;
    if (nd > 0) {
        it->factors[nd - 1] = 1;
    }
    for (int i = 0; i < nd; i++) {
        it->dims_m1[i] = dims[i] - 1;
        it->strides[i] = strides == NULL ? 0 : strides[i];
        it->backstrides[i] = it->strides[i] * it->dims_m1[i];
        if (i > 0) {
            it->factors[nd - i - 1] = it->factors[nd - i] * dims[nd - i];
        }
    }
    pccnp_iter_reset_direct(it);
    return 0;
}

static int pccnp_multi_iter_broadcast(PyArrayMultiIterObject *multi) {
    if (multi == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterNew broadcast requires a multi-iterator");
        return -1;
    }
    if (multi->numiter < 0 || multi->numiter > NPY_MAXARGS) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterNew has invalid iterator count");
        return -1;
    }

    int nd = 0;
    for (int i = 0; i < multi->numiter; i++) {
        PyArrayIterObject *it = multi->iters[i];
        if (it == NULL || it->ao == NULL ||
            !pccnp_is_array_object((PyObject *)it->ao)) {
            PyErr_SetString(PyExc_TypeError, "PyArray_MultiIterNew requires pcc NumPy arrays");
            return -1;
        }
        PccNpArray *array = pccnp_array_from_object(it->ao);
        if (array == NULL) return -1;
        if (array->nd > NPY_MAXDIMS_LEGACY_ITERS) {
            PyErr_SetString(PyExc_ValueError, "broadcast dimension count exceeds supported legacy limit");
            return -1;
        }
        if (array->nd > nd) nd = array->nd;
    }

    npy_intp dims[NPY_MAXDIMS_LEGACY_ITERS];
    for (int i = 0; i < NPY_MAXDIMS_LEGACY_ITERS; i++) {
        dims[i] = 1;
    }
    for (int i = 0; i < multi->numiter; i++) {
        PccNpArray *array = pccnp_array_from_object(multi->iters[i]->ao);
        if (array == NULL) return -1;
        int diff = nd - array->nd;
        for (int axis = 0; axis < array->nd; axis++) {
            npy_intp source_dim = array->dims[axis];
            int target_axis = axis + diff;
            if (source_dim < 0) {
                PyErr_SetString(PyExc_ValueError, "negative dimensions are not broadcastable");
                return -1;
            }
            if (source_dim != 1) {
                if (dims[target_axis] == 1) {
                    dims[target_axis] = source_dim;
                } else if (dims[target_axis] != source_dim) {
                    PyErr_SetString(PyExc_ValueError, "shape mismatch: objects cannot be broadcast to a single shape");
                    return -1;
                }
            }
        }
    }

    npy_intp size = 0;
    if (pccnp_iter_shape_size(dims, nd, &size) != 0) {
        return -1;
    }
    multi->nd = nd;
    multi->size = size;
    multi->index = 0;
    memset(multi->dimensions, 0, sizeof(multi->dimensions));
    for (int i = 0; i < nd; i++) {
        multi->dimensions[i] = dims[i];
    }

    for (int i = 0; i < multi->numiter; i++) {
        PyArrayIterObject *it = multi->iters[i];
        PccNpArray *array = pccnp_array_from_object(it->ao);
        if (array == NULL) return -1;
        npy_intp strides[NPY_MAXDIMS_LEGACY_ITERS];
        memset(strides, 0, sizeof(strides));
        int contiguous = pccnp_array_is_c_contiguous(array);
        int diff = nd - array->nd;
        for (int axis = 0; axis < nd; axis++) {
            int source_axis = axis - diff;
            if (source_axis < 0 || array->dims[source_axis] != dims[axis]) {
                contiguous = 0;
                strides[axis] = 0;
            } else {
                strides[axis] = array->strides[source_axis];
            }
        }
        if (pccnp_iter_rebroadcast_shape(it, array, dims, nd, strides, contiguous) != 0) {
            return -1;
        }
    }
    return 0;
}

static int pccnp_broadcast(PyArrayMultiIterObject *multi) {
    if (multi == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_Broadcast requires a multi-iterator");
        return NPY_FAIL;
    }
    return pccnp_multi_iter_broadcast(multi);
}

static PyObject *pccnp_multi_iter_new_impl(int n, PyObject **args) {
    if (n < 0 || n > NPY_MAXARGS || (n > 0 && args == NULL)) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterNew received invalid argument count");
        return NULL;
    }

    PyArrayMultiIterObject *multi =
        PyObject_New(PyArrayMultiIterObject, &pccnp_array_multi_iter_type);
    if (multi == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    multi->numiter = 0;
    multi->size = 1;
    multi->index = 0;
    multi->nd = 0;
    memset(multi->dimensions, 0, sizeof(multi->dimensions));
    memset(multi->iters, 0, sizeof(multi->iters));

    for (int i = 0; i < n; i++) {
        PyObject *array_obj = NULL;
        if (pccnp_is_array_object(args[i])) {
            array_obj = args[i];
            Py_INCREF(array_obj);
        } else {
            array_obj = pccnp_from_any(args[i], NULL, 0, 0, NPY_ARRAY_CARRAY, NULL);
            if (array_obj == NULL) {
                Py_DECREF((PyObject *)multi);
                return NULL;
            }
        }

        PyObject *iter_obj = pccnp_iter_new(array_obj);
        Py_DECREF(array_obj);
        if (iter_obj == NULL) {
            Py_DECREF((PyObject *)multi);
            return NULL;
        }
        multi->iters[multi->numiter] = (PyArrayIterObject *)iter_obj;
        multi->numiter += 1;
    }

    if (pccnp_multi_iter_broadcast(multi) != 0) {
        Py_DECREF((PyObject *)multi);
        return NULL;
    }
    return (PyObject *)multi;
}

static PyObject *pccnp_multi_iter_new(int n, PyObject **args) {
    if (n < 0 || n > NPY_MAXARGS) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterNew received invalid argument count");
        return NULL;
    }
    for (int i = 0; i < n; i++) {
        if (args[i] == NULL) {
            PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterNew argument is NULL");
            return NULL;
        }
    }
    return pccnp_multi_iter_new_impl(n, args);
}

static PyObject *pccnp_multi_iter_from_objects(PyObject **mps, int n, int nadd, ...) {
    if (n < 0 || nadd < 0 || n > NPY_MAXARGS || nadd > NPY_MAXARGS ||
        n > NPY_MAXARGS - nadd || (n > 0 && mps == NULL)) {
        PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterFromObjects received invalid argument count");
        return NULL;
    }

    int ntot = n + nadd;
    PyObject *items[NPY_MAXARGS];
    for (int i = 0; i < n; i++) {
        if (mps[i] == NULL) {
            PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterFromObjects argument is NULL");
            return NULL;
        }
        items[i] = mps[i];
    }

    __builtin_va_list va;
    __builtin_va_start(va, nadd);
    for (int i = n; i < ntot; i++) {
        PyObject *item = __builtin_va_arg(va, PyObject *);
        if (item == NULL) {
            __builtin_va_end(va);
            PyErr_SetString(PyExc_ValueError, "PyArray_MultiIterFromObjects extra argument is NULL");
            return NULL;
        }
        items[i] = item;
    }
    __builtin_va_end(va);

    return pccnp_multi_iter_new_impl(ntot, items);
}

static int pccnp_remove_smallest(PyArrayMultiIterObject *multi) {
    if (multi == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_RemoveSmallest requires a multi-iterator");
        return -1;
    }
    if (multi->numiter < 0 || multi->numiter > NPY_MAXARGS ||
        multi->nd < 0 || multi->nd > NPY_MAXDIMS_LEGACY_ITERS) {
        PyErr_SetString(PyExc_ValueError, "PyArray_RemoveSmallest received an invalid multi-iterator");
        return -1;
    }
    if (multi->nd == 0) {
        return -1;
    }

    npy_intp sumstrides[NPY_MAXDIMS_LEGACY_ITERS];
    for (int axis = 0; axis < multi->nd; axis++) {
        sumstrides[axis] = 0;
        for (int i = 0; i < multi->numiter; i++) {
            PyArrayIterObject *it = multi->iters[i];
            if (it == NULL) {
                PyErr_SetString(PyExc_ValueError, "PyArray_RemoveSmallest iterator is NULL");
                return -1;
            }
            sumstrides[axis] += it->strides[axis];
        }
    }

    int smallest_axis = 0;
    npy_intp smallest = sumstrides[0];
    for (int axis = 1; axis < multi->nd; axis++) {
        if (sumstrides[axis] < smallest) {
            smallest_axis = axis;
            smallest = sumstrides[axis];
        }
    }

    for (int i = 0; i < multi->numiter; i++) {
        PyArrayIterObject *it = multi->iters[i];
        it->contiguous = 0;
        if (it->size != 0) {
            npy_intp axis_len = it->dims_m1[smallest_axis] + 1;
            if (axis_len <= 0) {
                PyErr_SetString(PyExc_ValueError, "PyArray_RemoveSmallest iterator axis has invalid length");
                return -1;
            }
            it->size /= axis_len;
        }
        it->dims_m1[smallest_axis] = 0;
        it->backstrides[smallest_axis] = 0;
    }
    multi->size = multi->numiter > 0 && multi->iters[0] != NULL ? multi->iters[0]->size : 0;
    return smallest_axis;
}

static PyObject *pccnp_iter_all_but_axis(PyObject *obj, int *inaxis) {
    if (!pccnp_is_array_object(obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_IterAllButAxis requires a pcc NumPy array");
        return NULL;
    }
    if (inaxis == NULL) {
        PyErr_SetString(PyExc_ValueError, "axis pointer is required");
        return NULL;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)obj);
    if (array == NULL) return NULL;

    PyObject *iter_obj = pccnp_iter_new(obj);
    if (iter_obj == NULL) return NULL;
    if (array->nd == 0) {
        return iter_obj;
    }

    int axis = *inaxis;
    if (axis < 0) {
        int minaxis = 0;
        npy_intp minstride = 0;
        for (int i = 0; i < array->nd; i++) {
            npy_intp stride = array->strides[i];
            if (stride > 0 && (minstride == 0 || stride < minstride)) {
                minaxis = i;
                minstride = stride;
            }
        }
        axis = minaxis;
        *inaxis = axis;
    }
    if (axis < 0 || axis >= array->nd) {
        Py_DECREF(iter_obj);
        PyErr_SetString(PyExc_ValueError, "axis is out of bounds for array");
        return NULL;
    }

    PyArrayIterObject *it = (PyArrayIterObject *)iter_obj;
    it->contiguous = 0;
    if (it->size != 0) {
        npy_intp dim = array->dims[axis];
        if (dim <= 0) {
            Py_DECREF(iter_obj);
            PyErr_SetString(PyExc_ValueError, "iterator axis has invalid dimension");
            return NULL;
        }
        it->size /= dim;
    }
    it->dims_m1[axis] = 0;
    it->backstrides[axis] = 0;
    return iter_obj;
}

static npy_intp pccnp_pyint_as_intp(PyObject *obj) {
    if (obj == NULL || PyBool_Check(obj)) {
        PyErr_SetString(PyExc_TypeError, "an integer is required");
        return (npy_intp)-1;
    }

    PyObject *index_obj = NULL;
    if (PyLong_CheckExact(obj)) {
        index_obj = obj;
        Py_INCREF(index_obj);
    } else {
        index_obj = PyNumber_Index(obj);
        if (index_obj == NULL) {
            if (PyErr_ExceptionMatches(PyExc_TypeError)) {
                PyErr_SetString(PyExc_TypeError, "an integer is required");
            }
            return (npy_intp)-1;
        }
    }

    long long value = PyLong_AsLongLong(index_obj);
    Py_DECREF(index_obj);
    if (PyErr_Occurred() != NULL) {
        return (npy_intp)-1;
    }

    long long min_intp = -(long long)PY_SSIZE_T_MAX - 1LL;
    long long max_intp = (long long)PY_SSIZE_T_MAX;
    if (value < min_intp || value > max_intp) {
        PyErr_SetString(
            PyExc_OverflowError,
            "Python int too large to convert to C numpy.intp"
        );
        return (npy_intp)-1;
    }
    return (npy_intp)value;
}

static int pccnp_pyint_as_int(PyObject *obj) {
    npy_intp value = pccnp_pyint_as_intp(obj);
    if (PyErr_Occurred() != NULL) {
        return -1;
    }
    if (value < (npy_intp)INT_MIN || value > (npy_intp)INT_MAX) {
        PyErr_SetString(PyExc_ValueError, "integer won't fit into a C int");
        return -1;
    }
    return (int)value;
}

static int pccnp_python_pyint_from_int(PyObject *obj, int *value) {
    if (value == NULL) {
        PyErr_SetString(PyExc_ValueError, "PyArray_PythonPyIntFromInt output is NULL");
        return 0;
    }
    int parsed = pccnp_pyint_as_int(obj);
    if (PyErr_Occurred() != NULL) {
        return 0;
    }
    *value = parsed;
    return 1;
}

static int pccnp_intp_from_sequence(PyObject *seq, npy_intp *vals, int maxvals) {
    if (vals == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy intp output is NULL");
        return -1;
    }
    if (maxvals < 0) {
        PyErr_SetString(PyExc_ValueError, "maxvals must be non-negative");
        return -1;
    }

    PyObject *seq_obj = NULL;
    if (seq != NULL && !PyLong_CheckExact(seq) && PySequence_Check(seq)) {
        seq_obj = PySequence_Fast(
            seq,
            "expected a sequence of integers or a single integer"
        );
        if (seq_obj == NULL) {
            PyErr_Clear();
        }
    }

    if (seq_obj == NULL) {
        if (maxvals == 0) {
            PyErr_SetString(PyExc_ValueError, "maxvals is zero for scalar input");
            return -1;
        }
        vals[0] = pccnp_pyint_as_intp(seq);
        if (PyErr_Occurred() != NULL) {
            return -1;
        }
        return 1;
    }

    Py_ssize_t nd = PySequence_Fast_GET_SIZE(seq_obj);
    PyObject **items = PySequence_Fast_ITEMS(seq_obj);
    Py_ssize_t limit = nd < (Py_ssize_t)maxvals ? nd : (Py_ssize_t)maxvals;
    for (Py_ssize_t i = 0; i < limit; i++) {
        vals[i] = pccnp_pyint_as_intp(items[i]);
        if (PyErr_Occurred() != NULL) {
            Py_DECREF(seq_obj);
            return -1;
        }
    }
    Py_DECREF(seq_obj);
    if (nd > INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "sequence length does not fit into C int");
        return -1;
    }
    return (int)nd;
}

static int pccnp_intp_converter(PyObject *obj, PyArray_Dims *seq) {
    if (seq == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy dims output is NULL");
        return NPY_FAIL;
    }
    seq->ptr = NULL;
    seq->len = 0;

    if (obj == Py_None) {
        PyErr_SetString(PyExc_TypeError, "Use () not None as shape arguments");
        return NPY_FAIL;
    }

    PyObject *seq_obj = NULL;
    if (obj != NULL && !PyLong_CheckExact(obj) && PySequence_Check(obj)) {
        seq_obj = PySequence_Fast(
            obj,
            "expected a sequence of integers or a single integer"
        );
        if (seq_obj == NULL) {
            PyErr_Clear();
        }
    }

    if (seq_obj == NULL) {
        seq->ptr = PyDimMem_NEW(1);
        if (seq->ptr == NULL) {
            PyErr_NoMemory();
            return NPY_FAIL;
        }
        seq->ptr[0] = pccnp_pyint_as_intp(obj);
        if (PyErr_Occurred() != NULL) {
            PyDimMem_FREE(seq->ptr);
            seq->ptr = NULL;
            seq->len = 0;
            return NPY_FAIL;
        }
        seq->len = 1;
        return NPY_SUCCEED;
    }

    Py_ssize_t nd = PySequence_Fast_GET_SIZE(seq_obj);
    if (nd > PCCNP_MAX_SEQUENCE_ND) {
        Py_DECREF(seq_obj);
        PyErr_SetString(PyExc_ValueError, "too many dimensions for pcc fake NumPy");
        return NPY_FAIL;
    }
    if (nd > 0) {
        seq->ptr = PyDimMem_NEW((size_t)nd);
        if (seq->ptr == NULL) {
            Py_DECREF(seq_obj);
            PyErr_NoMemory();
            return NPY_FAIL;
        }
        int converted = pccnp_intp_from_sequence(seq_obj, seq->ptr, (int)nd);
        if (converted < 0 || converted != (int)nd) {
            PyDimMem_FREE(seq->ptr);
            seq->ptr = NULL;
            seq->len = 0;
            Py_DECREF(seq_obj);
            return NPY_FAIL;
        }
    }
    seq->len = (int)nd;
    Py_DECREF(seq_obj);
    return NPY_SUCCEED;
}

static int pccnp_optional_intp_converter(PyObject *obj, PyArray_Dims *seq) {
    if (seq == NULL) {
        PyErr_SetString(PyExc_ValueError, "pcc NumPy dims output is NULL");
        return NPY_FAIL;
    }
    if (obj == Py_None) {
        return NPY_SUCCEED;
    }
    return pccnp_intp_converter(obj, seq);
}

static int pccnp_array_free_api(PyObject *op, void *ptr) {
    if (!pccnp_is_array_object(op)) {
        return -1;
    }
    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)op);
    if (array == NULL || array->nd < 1 || array->nd > 3) {
        return -1;
    }
    if (array->nd >= 2) {
        PyArray_free(ptr);
    }
    Py_DECREF(op);
    return 0;
}

static int pccnp_as_carray(
    PyObject **op,
    void *ptr,
    npy_intp *dims,
    int nd,
    PyArray_Descr *typedescr
) {
    if (op == NULL || *op == NULL || ptr == NULL || dims == NULL) {
        Py_XDECREF(typedescr);
        PyErr_SetString(PyExc_ValueError, "PyArray_AsCArray requires output storage");
        return -1;
    }
    if (nd < 1 || nd > 3) {
        Py_XDECREF(typedescr);
        PyErr_SetString(PyExc_ValueError, "C arrays of only 1-3 dimensions available");
        return -1;
    }

    PyObject *array_obj = pccnp_from_any(
        *op,
        typedescr,
        nd,
        nd,
        NPY_ARRAY_CARRAY,
        NULL
    );
    Py_XDECREF(typedescr);
    if (array_obj == NULL) {
        return -1;
    }

    PccNpArray *array = pccnp_array_from_object((PyArrayObject *)array_obj);
    if (array == NULL || array->nd != nd) {
        Py_DECREF(array_obj);
        PyErr_SetString(PyExc_ValueError, "PyArray_AsCArray produced an invalid array");
        return -1;
    }

    char *data = (char *)array->data;
    if (nd == 1) {
        *((char **)ptr) = data;
    } else if (nd == 2) {
        npy_intp n = array->dims[0];
        char **ptr2 = (char **)PyArray_malloc((size_t)n * sizeof(char *));
        if (ptr2 == NULL) {
            Py_DECREF(array_obj);
            PyErr_NoMemory();
            return -1;
        }
        for (npy_intp i = 0; i < n; i++) {
            ptr2[i] = data + (i * array->strides[0]);
        }
        *((char ***)ptr) = ptr2;
    } else {
        npy_intp n = array->dims[0];
        npy_intp m = array->dims[1];
        size_t pointer_count = (size_t)n * ((size_t)m + 1);
        char ***ptr3 = (char ***)PyArray_malloc(pointer_count * sizeof(char *));
        if (ptr3 == NULL) {
            Py_DECREF(array_obj);
            PyErr_NoMemory();
            return -1;
        }
        char **row_ptrs = (char **)(ptr3 + n);
        for (npy_intp i = 0; i < n; i++) {
            ptr3[i] = row_ptrs + (i * m);
            for (npy_intp j = 0; j < m; j++) {
                ptr3[i][j] = data + (i * array->strides[0]) + (j * array->strides[1]);
            }
        }
        *((char ****)ptr) = ptr3;
    }

    for (int i = 0; i < nd; i++) {
        dims[i] = array->dims[i];
    }
    *op = array_obj;
    return 0;
}

static int pccnp_fail_unless_writeable(PyArrayObject *obj, const char *name) {
    if (!pccnp_is_array_object((PyObject *)obj)) {
        PyErr_SetString(PyExc_TypeError, "PyArray_FailUnlessWriteable expects a pcc NumPy array");
        return -1;
    }
    if ((pccnp_flags(obj) & NPY_ARRAY_WRITEABLE) == 0) {
        PyErr_Format(PyExc_ValueError, "%s is read-only", name == NULL ? "array" : name);
        return -1;
    }
    return 0;
}

static int pccnp_checked_add_intp(npy_intp lhs, npy_intp rhs, npy_intp *out) {
    if (rhs > 0 && lhs > PY_SSIZE_T_MAX - rhs) return -1;
    if (rhs < 0 && lhs < (npy_intp)(-PY_SSIZE_T_MAX - 1) - rhs) return -1;
    *out = lhs + rhs;
    return 0;
}

static int pccnp_checked_mul_intp(npy_intp lhs, npy_intp rhs, npy_intp *out) {
    if (lhs == 0 || rhs == 0) {
        *out = 0;
        return 0;
    }
    if (lhs == (npy_intp)(-PY_SSIZE_T_MAX - 1) && rhs == (npy_intp)-1) return -1;
    if (rhs == (npy_intp)(-PY_SSIZE_T_MAX - 1) && lhs == (npy_intp)-1) return -1;
    npy_intp result = lhs * rhs;
    if (result / rhs != lhs) return -1;
    *out = result;
    return 0;
}

