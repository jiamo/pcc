/* pcc/py_runtime/src/py_int_convert.c
 *
 * Public int conversion helper split from py_int.c for the Phase 4c
 * pcc-Python replacement path.
 */

#include "py_internal.h"

int64_t py_int_to_i64(PyObject *o, int *overflow) {
    if (overflow) *overflow = 0;
    if (o == NULL) {
        if (overflow) *overflow = 1;
        return 0;
    }
    if (PY_IS_TAGGED_INT(o)) {
        return py_untag_int(o);
    }
    if (py_header(o)->type_tag != PY_TYPE_INT) {
        if (overflow) *overflow = 1;
        return 0;
    }
    return py_bigint_to_i64((const PyIntObject *)o, overflow);
}
