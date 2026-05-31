"""Set-slice dispatch split out from py_obj_ops_dispatch.

Keep ``py_obj_set_slice`` in its own archive member so generic truthy/add/getitem
dispatch does not force list set-slice support into ordinary executables.
"""

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import is_tagged_int, load_i32, ptr_is_null


py_list_set_slice = extern(
    "py_list_set_slice", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return 2
    return load_i32(o, 8)


@c_abi_export("py_obj_set_slice")
def py_obj_set_slice(o, lo, hi, step, replacement) -> int:
    if ptr_is_null(o) != 0:
        return -1
    tag: int = _type_of(o)
    if tag == 5:
        return py_list_set_slice(o, lo, hi, step, replacement)
    return -1
