"""pcc-Python owner for object, numeric, and percent formatting ABIs.

The host-C oracle is ``src/py_format.c``.  This module deliberately builds
text with pcc-owned buffers and the freestanding stdio numeric formatter; it
does not replace the C helper with a call back into ``snprintf``.
"""

__pcc_runtime_port__ = True

from pcc.py_runtime.py.py_abi_constants import (
    PYCLASSOBJECT_NAME_OFFSET,
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_COMPLEX,
    PY_TYPE_DICT,
    PY_TYPE_EXC,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
    PY_TYPE_MEMORYVIEW,
    PY_TYPE_NONE,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
)

from pcc.extern import c_abi_export, c_double, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_i64_i64_ptr,
    cstr,
    define_global_ptr_null,
    f64_bits,
    f64_signbit,
    free,
    global_load_ptr,
    is_tagged_int,
    load_f64,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    stack_alloc,
    store_i8,
    store_i64,
    store_ptr,
    strlen,
    untag_int,
)


define_global_ptr_null("py_format_cpy_object_hook")

py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_get_message = extern("py_exc_get_message", (c_ptr,), c_ptr)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_obj_repr = extern("py_obj_repr", (c_ptr,), c_ptr)
py_obj_ascii = extern("py_obj_ascii", (c_ptr,), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_float_to_f64 = extern("py_float_to_f64", (c_ptr,), c_double)
py_float_from_f64 = extern("py_float_from_f64", (c_double,), c_ptr)
py_bigint_to_double = extern("py_bigint_to_double", (c_ptr,), c_double)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_int_format_decimal = extern(
    "py_int_format_decimal", (c_ptr, c_int64, c_int64, c_int64), c_ptr
)
py_int_format_hex = extern(
    "py_int_format_hex", (c_ptr, c_int64, c_int64), c_ptr
)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_bytearray_from_obj = extern("py_bytearray_from_obj", (c_ptr,), c_ptr)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_func_call = extern("py_func_call", (c_ptr, c_ptr), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_stdio_format_float_raw = extern(
    "pcc_stdio_format_float_raw",
    (c_ptr, c_double, c_int64, c_int64, c_int64, c_int64, c_int64),
    c_int64,
)
strtod_c = extern("strtod", (c_ptr, c_ptr), c_double)
pow_c = extern("pow", (c_double, c_double), c_double)
rint_c = extern("rint", (c_double,), c_double)
hypot_c = extern("hypot", (c_double, c_double), c_double)


def _type_of(obj) -> int:
    if is_tagged_int(obj) != 0:
        return PY_TYPE_INT
    if ptr_is_null(obj) != 0:
        return -1
    return load_i32(obj, 8)


def _format_require_result(result, helper_name, message):
    if ptr_is_null(result) != 0:
        py_runtime_error_if_unset(helper_name, message)
    return result


def _buffer_new(capacity: int):
    if capacity < 64:
        capacity = 64
    state = malloc(24)
    if ptr_is_null(state) != 0:
        return null()
    data = malloc(capacity + 1)
    if ptr_is_null(data) != 0:
        free(state)
        return null()
    store_ptr(state, 0, data)
    store_i64(state, 8, 0)
    store_i64(state, 16, capacity)
    store_i8(data, 0, 0)
    return state


def _buffer_free(state) -> None:
    if ptr_is_null(state) != 0:
        return
    data = load_ptr(state, 0)
    if ptr_is_null(data) == 0:
        free(data)
    free(state)


def _buffer_reserve(state, extra: int) -> int:
    if ptr_is_null(state) != 0 or extra < 0:
        return -1
    length: int = load_i64(state, 8)
    capacity: int = load_i64(state, 16)
    need: int = length + extra
    if need <= capacity:
        return 0
    next_capacity: int = capacity
    if next_capacity < 64:
        next_capacity = 64
    while next_capacity < need:
        if next_capacity > 4611686018427387903:
            return -1
        next_capacity = next_capacity * 2
    data = realloc(load_ptr(state, 0), next_capacity + 1)
    if ptr_is_null(data) != 0:
        return -1
    store_ptr(state, 0, data)
    store_i64(state, 16, next_capacity)
    return 0


def _buffer_append(state, source, count: int) -> int:
    if count <= 0:
        return 0
    if ptr_is_null(source) != 0 or _buffer_reserve(state, count) != 0:
        return -1
    data = load_ptr(state, 0)
    length: int = load_i64(state, 8)
    i: int = 0
    while i < count:
        store_i8(data, length + i, load_i8(source, i))
        i = i + 1
    length = length + count
    store_i64(state, 8, length)
    store_i8(data, length, 0)
    return 0


def _buffer_char(state, value: int) -> int:
    if _buffer_reserve(state, 1) != 0:
        return -1
    data = load_ptr(state, 0)
    length: int = load_i64(state, 8)
    store_i8(data, length, value)
    store_i64(state, 8, length + 1)
    store_i8(data, length + 1, 0)
    return 0


def _buffer_repeat(state, value: int, count: int) -> int:
    if count <= 0:
        return 0
    if _buffer_reserve(state, count) != 0:
        return -1
    data = load_ptr(state, 0)
    length: int = load_i64(state, 8)
    i: int = 0
    while i < count:
        store_i8(data, length + i, value)
        i = i + 1
    store_i64(state, 8, length + count)
    store_i8(data, length + count, 0)
    return 0


def _buffer_string(state):
    if ptr_is_null(state) != 0:
        return null()
    return py_str_new(load_ptr(state, 0), load_i64(state, 8))


def _buffer_bytes(state):
    if ptr_is_null(state) != 0:
        return null()
    return py_bytes_new(load_ptr(state, 0), load_i64(state, 8))


def _append_pystr(state, value) -> int:
    if ptr_is_null(value) != 0 or _type_of(value) != PY_TYPE_STR:
        return -1
    return _buffer_append(state, py_str_utf8(value), py_str_byte_len(value))


def _complex_real(value) -> float:
    if ptr_is_null(value) != 0:
        return 0.0
    if is_tagged_int(value) != 0:
        return float(untag_int(value))
    tag: int = load_i32(value, 8)
    if tag == PY_TYPE_COMPLEX or tag == PY_TYPE_FLOAT:
        return load_f64(value, 16)
    if tag == PY_TYPE_INT:
        return py_bigint_to_double(value)
    if tag == PY_TYPE_BOOL:
        if ptr_eq(value, global_load_ptr("py_True")) != 0:
            return 1.0
    return 0.0


def _complex_imag(value) -> float:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0.0
    if load_i32(value, 8) == PY_TYPE_COMPLEX:
        return load_f64(value, 24)
    return 0.0


@c_abi_export("py_format_try_cpy_object_into_fd")
def py_format_try_cpy_object_into_fd(fd: int, obj, tag: int) -> int:
    hook = global_load_ptr("py_format_cpy_object_hook")
    if ptr_is_null(hook) != 0 or ptr_is_null(obj) != 0:
        return 0
    if tag >= PY_TYPE_NONE and tag <= 1023:
        return 0
    return call_i64_i64_ptr(hook, fd, obj)


@c_abi_export("py_float_value_of")
def py_float_value_of(value) -> float:
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        if load_i32(value, 8) == PY_TYPE_STR:
            text = py_str_utf8(value)
            while (
                load_i8(text, 0) == 32
                or load_i8(text, 0) == 9
                or load_i8(text, 0) == 10
                or load_i8(text, 0) == 13
                or load_i8(text, 0) == 11
                or load_i8(text, 0) == 12
            ):
                text = ptr_add(text, 1)
            end_slot = stack_alloc(8)
            store_ptr(end_slot, 0, null())
            parsed: float = strtod_c(text, end_slot)
            end = load_ptr(end_slot, 0)
            if ptr_eq(end, text) != 0:
                py_raise_owned(py_exc_new(2, cstr("could not convert string to float")))
                return 0.0
            while (
                load_i8(end, 0) == 32
                or load_i8(end, 0) == 9
                or load_i8(end, 0) == 10
                or load_i8(end, 0) == 13
                or load_i8(end, 0) == 11
                or load_i8(end, 0) == 12
            ):
                end = ptr_add(end, 1)
            if load_i8(end, 0) != 0:
                py_raise_owned(py_exc_new(2, cstr("could not convert string to float")))
                return 0.0
            return parsed
    return py_float_to_f64(value)


@c_abi_export("pcc_float_round_fixed_f64")
def pcc_float_round_fixed_f64(value: float, ndigits: int) -> float:
    if value != value:
        return value
    if value != 0.0 and value == value * 2.0:
        return value
    if ndigits >= 0:
        if ndigits > 200:
            return value
        text = stack_alloc(800)
        pcc_stdio_format_float_raw(text, value, 102, ndigits, 0, 0, 0)
        return strtod_c(text, null())
    if ndigits < -308:
        if f64_signbit(value) != 0:
            return -0.0
        return 0.0
    scale: float = pow_c(10.0, float(0 - ndigits))
    if scale == 0.0 or scale != scale or scale == scale * 2.0:
        return value
    return rint_c(value / scale) * scale


@c_abi_export("py_complex_sub")
def py_complex_sub(left, right):
    return py_complex_new(
        _complex_real(left) - _complex_real(right),
        _complex_imag(left) - _complex_imag(right),
    )


@c_abi_export("py_complex_mul")
def py_complex_mul(left, right):
    ar: float = _complex_real(left)
    ai: float = _complex_imag(left)
    br: float = _complex_real(right)
    bi: float = _complex_imag(right)
    return py_complex_new(ar * br - ai * bi, ar * bi + ai * br)


@c_abi_export("py_complex_div")
def py_complex_div(left, right):
    ar: float = _complex_real(left)
    ai: float = _complex_imag(left)
    br: float = _complex_real(right)
    bi: float = _complex_imag(right)
    denominator: float = br * br + bi * bi
    if denominator == 0.0:
        error = py_exc_new(9, cstr("complex division by zero"))
        py_raise(error)
        if ptr_is_null(error) == 0:
            py_decref(error)
        return null()
    return py_complex_new(
        (ar * br + ai * bi) / denominator,
        (ai * br - ar * bi) / denominator,
    )


@c_abi_export("py_complex_neg")
def py_complex_neg(value):
    return py_complex_new(0.0 - _complex_real(value), 0.0 - _complex_imag(value))


@c_abi_export("py_complex_conjugate")
def py_complex_conjugate(value):
    return py_complex_new(_complex_real(value), 0.0 - _complex_imag(value))


@c_abi_export("py_complex_abs")
def py_complex_abs(value):
    return py_float_from_f64(hypot_c(_complex_real(value), _complex_imag(value)))


py_complex_new = extern("py_complex_new", (c_double, c_double), c_ptr)


def _find_byte(text, value: int) -> int:
    i: int = 0
    while load_i8(text, i) != 0:
        if (load_i8(text, i) & 255) == value:
            return i
        i = i + 1
    return -1


def _parse_decimal_exponent(text, start: int) -> int:
    sign: int = 1
    if load_i8(text, start) == 45:
        sign = -1
        start = start + 1
    elif load_i8(text, start) == 43:
        start = start + 1
    value: int = 0
    while load_i8(text, start) >= 48 and load_i8(text, start) <= 57:
        value = value * 10 + load_i8(text, start) - 48
        start = start + 1
    return value * sign


def _pow10_i64(exponent: int) -> int:
    """A bounded power of ten used only for at most 17 repr digits."""
    result: int = 1
    while exponent > 0:
        result = result * 10
        exponent = exponent - 1
    return result


def _write_exact_digits(output, position: int, digits: int, count: int) -> int:
    """Write exactly ``count`` decimal digits, including leading zeroes."""
    end: int = position + count
    cursor: int = end
    while cursor > position:
        cursor = cursor - 1
        store_i8(output, cursor, 48 + (digits % 10))
        digits = digits // 10
    return end


def _write_exact_exponent(output, position: int, exponent: int) -> int:
    store_i8(output, position, 101)
    position = position + 1
    if exponent < 0:
        store_i8(output, position, 45)
        exponent = 0 - exponent
    else:
        store_i8(output, position, 43)
    position = position + 1
    if exponent >= 100:
        return _write_exact_digits(output, position, exponent, 3)
    return _write_exact_digits(output, position, exponent, 2)


def _write_exact_scientific(
    output,
    negative: int,
    digits: int,
    significant: int,
    exponent: int,
) -> int:
    position: int = 0
    if negative != 0:
        store_i8(output, position, 45)
        position = position + 1
    divisor: int = _pow10_i64(significant - 1)
    store_i8(output, position, 48 + (digits // divisor))
    position = position + 1
    if significant > 1:
        store_i8(output, position, 46)
        position = position + 1
        position = _write_exact_digits(
            output,
            position,
            digits % divisor,
            significant - 1,
        )
    position = _write_exact_exponent(output, position, exponent)
    store_i8(output, position, 0)
    return position


def _write_exact_float_text(
    output,
    negative: int,
    digits: int,
    significant: int,
    exponent: int,
) -> int:
    if exponent < -4 or exponent >= 16:
        return _write_exact_scientific(
            output,
            negative,
            digits,
            significant,
            exponent,
        )

    position: int = 0
    if negative != 0:
        store_i8(output, position, 45)
        position = position + 1
    integer_count: int = exponent + 1
    if integer_count <= 0:
        store_i8(output, position, 48)
        store_i8(output, position + 1, 46)
        position = position + 2
        zeroes: int = 0 - integer_count
        while zeroes > 0:
            store_i8(output, position, 48)
            position = position + 1
            zeroes = zeroes - 1
        position = _write_exact_digits(
            output,
            position,
            digits,
            significant,
        )
    elif significant <= integer_count:
        position = _write_exact_digits(
            output,
            position,
            digits,
            significant,
        )
        zeroes = integer_count - significant
        while zeroes > 0:
            store_i8(output, position, 48)
            position = position + 1
            zeroes = zeroes - 1
        store_i8(output, position, 46)
        store_i8(output, position + 1, 48)
        position = position + 2
    else:
        fractional_count: int = significant - integer_count
        divisor = _pow10_i64(fractional_count)
        position = _write_exact_digits(
            output,
            position,
            digits // divisor,
            integer_count,
        )
        store_i8(output, position, 46)
        position = position + 1
        position = _write_exact_digits(
            output,
            position,
            digits % divisor,
            fractional_count,
        )
    store_i8(output, position, 0)
    return position


@c_abi_export("py_float_repr_shortest")
def py_float_repr_shortest(value_obj):
    value: float = py_float_to_f64(value_obj)
    if value != value:
        return py_str_new(cstr("nan"), 3)
    if value != 0.0 and value == value * 2.0:
        if value < 0.0:
            return py_str_new(cstr("-inf"), 4)
        return py_str_new(cstr("inf"), 3)
    if value == 0.0:
        if f64_signbit(value) != 0:
            return py_str_new(cstr("-0.0"), 4)
        return py_str_new(cstr("0.0"), 3)

    negative: int = 0
    absolute: float = value
    if value < 0.0:
        negative = 1
        absolute = 0.0 - value

    # Recover the exact binary rational m * 2**exp2.  ``num`` and ``den``
    # deliberately live in semantic Python-int projection: the denominator of
    # the smallest subnormal is 1 << 1074 and cannot be represented by i64.
    bits: int = f64_bits(absolute)
    exponent_bits: int = (bits >> 52) & 2047
    mantissa: int = bits & 4503599627370495
    exp2: int = -1074
    if exponent_bits != 0:
        mantissa = mantissa + 4503599627370496
        exp2 = exponent_bits - 1075
    num: int = mantissa
    den: int = 1
    if exp2 >= 0:
        num = mantissa << exp2
    else:
        shift_amount: int = 0 - exp2
        den = 1 << shift_amount

    # Exact floor(log10(value)) without floating-point normalisation.
    exponent: int = 0
    probe_num: int = num
    probe_den: int = den
    if probe_num >= probe_den:
        while probe_num >= probe_den * 10:
            probe_den = probe_den * 10
            exponent = exponent + 1
    else:
        while probe_num < probe_den:
            probe_num = probe_num * 10
            exponent = exponent - 1

    # Scale once for a one-significant-digit candidate.  Each failed
    # round-trip adds one exact decimal digit by multiplying the numerator by
    # ten.  Round-half-even matches conversion semantics; strtod is used only
    # as the independent acceptance oracle, never to generate digits.
    scaled_num: int = num
    scaled_den: int = den
    scale: int = 0 - exponent
    while scale > 0:
        scaled_num = scaled_num * 10
        scale = scale - 1
    while scale < 0:
        scaled_den = scaled_den * 10
        scale = scale + 1

    candidate = stack_alloc(96)
    significant: int = 1
    found: int = 0
    digits: int = 0
    candidate_exponent: int = exponent
    while significant <= 17 and found == 0:
        quotient: int = scaled_num // scaled_den
        remainder: int = scaled_num % scaled_den
        twice: int = remainder * 2
        if twice > scaled_den or (
            twice == scaled_den and (quotient & 1) != 0
        ):
            quotient = quotient + 1
        digits = quotient
        candidate_exponent = exponent
        digit_limit: int = _pow10_i64(significant)
        if digits >= digit_limit:
            digits = digits // 10
            candidate_exponent = candidate_exponent + 1
        _write_exact_scientific(
            candidate,
            negative,
            digits,
            significant,
            candidate_exponent,
        )
        parsed: float = strtod_c(candidate, null())
        if parsed == value:
            found = 1
        else:
            scaled_num = scaled_num * 10
            significant = significant + 1

    if significant > 17:
        significant = 17
    output = stack_alloc(800)
    length: int = _write_exact_float_text(
        output,
        negative,
        digits,
        significant,
        candidate_exponent,
    )
    return py_str_new(output, length)


def _append_complex_component(state, value: float) -> int:
    boxed = py_float_from_f64(value)
    if ptr_is_null(boxed) != 0:
        return _buffer_char(state, 48)
    rendered = py_float_repr_shortest(boxed)
    py_decref(boxed)
    if ptr_is_null(rendered) != 0:
        return _buffer_char(state, 48)
    text = py_str_utf8(rendered)
    length: int = py_str_byte_len(rendered)
    if length >= 2:
        if load_i8(text, length - 2) == 46 and load_i8(text, length - 1) == 48:
            length = length - 2
    rc: int = _buffer_append(state, text, length)
    py_decref(rendered)
    return rc


@c_abi_export("py_complex_repr")
def py_complex_repr(value):
    if ptr_is_null(value) != 0 or _type_of(value) != PY_TYPE_COMPLEX:
        return null()
    real: float = load_f64(value, 16)
    imag: float = load_f64(value, 24)
    state = _buffer_new(64)
    if ptr_is_null(state) != 0:
        return null()
    if real == 0.0 and f64_signbit(real) == 0:
        _append_complex_component(state, imag)
        _buffer_char(state, 106)
    else:
        _buffer_char(state, 40)
        _append_complex_component(state, real)
        if imag < 0.0 or (imag == 0.0 and f64_signbit(imag) != 0):
            _buffer_char(state, 45)
            imag = 0.0 - imag
        else:
            _buffer_char(state, 43)
        _append_complex_component(state, imag)
        _buffer_char(state, 106)
        _buffer_char(state, 41)
    result = _buffer_string(state)
    _buffer_free(state)
    return result


@c_abi_export("py_exc_repr")
def py_exc_repr(value):
    if ptr_is_null(value) != 0 or _type_of(value) != PY_TYPE_EXC:
        return null()
    cls = pcc_gc_load_ptr(value, ptr_add(value, 16))
    name = cstr("Exception")
    if ptr_is_null(cls) == 0:
        candidate = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(candidate) == 0:
            name = candidate
    state = _buffer_new(64)
    if ptr_is_null(state) != 0:
        return null()
    _buffer_append(state, name, strlen(name))
    _buffer_char(state, 40)
    message = py_exc_get_message(value)
    none_obj = global_load_ptr("py_None")
    empty: int = 0
    if ptr_is_null(message) != 0 or ptr_eq(message, none_obj) != 0:
        empty = 1
    elif _type_of(message) == PY_TYPE_STR and py_str_byte_len(message) == 0:
        empty = 1
    if empty == 0:
        rendered = py_obj_repr(message)
        if ptr_is_null(rendered) != 0:
            _buffer_free(state)
            return null()
        _append_pystr(state, rendered)
        py_decref(rendered)
    _buffer_char(state, 41)
    result = _buffer_string(state)
    _buffer_free(state)
    return result


def _parse_digits(text, position: int) -> int:
    value: int = 0
    while load_i8(text, position) >= 48 and load_i8(text, position) <= 57:
        value = value * 10 + load_i8(text, position) - 48
        position = position + 1
    return value


def _skip_digits(text, position: int) -> int:
    while load_i8(text, position) >= 48 and load_i8(text, position) <= 57:
        position = position + 1
    return position


def _pad_text(text, length: int, width: int, align: int, fill: int, zero: int):
    if width < length:
        width = length
    padding: int = width - length
    left: int = 0
    right: int = 0
    if align == 60:
        right = padding
    elif align == 94:
        left = padding // 2
        right = padding - left
    else:
        left = padding
    pad: int = fill
    if zero != 0:
        pad = 48
    state = _buffer_new(width + 1)
    if ptr_is_null(state) != 0:
        return null()
    _buffer_repeat(state, pad, left)
    _buffer_append(state, text, length)
    _buffer_repeat(state, pad, right)
    result = _buffer_string(state)
    _buffer_free(state)
    return result


def _pad_signed_text(text, length: int, width: int, align: int, fill: int, zero: int):
    if zero == 0 or align != 62 or length >= width:
        return _pad_text(text, length, width, align, fill, 0)
    state = _buffer_new(width + 1)
    if ptr_is_null(state) != 0:
        return null()
    start: int = 0
    if length > 0 and (load_i8(text, 0) == 45 or load_i8(text, 0) == 43 or load_i8(text, 0) == 32):
        _buffer_char(state, load_i8(text, 0))
        start = 1
    if length >= start + 2 and load_i8(text, start) == 48:
        prefix: int = load_i8(text, start + 1)
        if prefix == 120 or prefix == 88 or prefix == 111 or prefix == 98:
            _buffer_char(state, 48)
            _buffer_char(state, prefix)
            start = start + 2
    _buffer_repeat(state, 48, width - length)
    _buffer_append(state, ptr_add(text, start), length - start)
    result = _buffer_string(state)
    _buffer_free(state)
    return result


def _format_string_builtin(value, spec):
    position: int = 0
    align: int = 60
    fill: int = 32
    first: int = load_i8(spec, position)
    second: int = load_i8(spec, position + 1)
    if first != 0 and (second == 60 or second == 62 or second == 94):
        fill = first
        align = second
        position = position + 2
    elif first == 60 or first == 62 or first == 94:
        align = first
        position = position + 1
    width: int = _parse_digits(spec, position)
    position = _skip_digits(spec, position)
    precision: int = -1
    if load_i8(spec, position) == 46:
        position = position + 1
        if load_i8(spec, position) < 48 or load_i8(spec, position) > 57:
            return null()
        precision = _parse_digits(spec, position)
        position = _skip_digits(spec, position)
    if load_i8(spec, position) != 0:
        return null()
    text = py_str_utf8(value)
    length: int = py_str_byte_len(value)
    if precision >= 0 and precision < length:
        length = precision
    return _pad_text(text, length, width, align, fill, 0)


def _int_raw_base(value, conversion: int, alternate: int):
    if conversion == 100:
        return py_int_format_decimal(value, 0, 0, 0)
    if conversion == 120 or conversion == 88:
        rendered = py_int_format_hex(value, 0, 0)
        if ptr_is_null(rendered) != 0:
            return null()
        source = py_str_utf8(rendered)
        length: int = py_str_byte_len(rendered)
        state = _buffer_new(length + 4)
        negative: int = 0
        start: int = 0
        if length > 0 and load_i8(source, 0) == 45:
            negative = 1
            start = 1
            _buffer_char(state, 45)
        if alternate != 0:
            _buffer_char(state, 48)
            _buffer_char(state, 88 if conversion == 88 else 120)
        i: int = start
        while i < length:
            byte: int = load_i8(source, i)
            if conversion == 88 and byte >= 97 and byte <= 102:
                byte = byte - 32
            _buffer_char(state, byte)
            i = i + 1
        result = _buffer_string(state)
        _buffer_free(state)
        py_decref(rendered)
        return result
    integer: int = py_int_value_i64(value)
    negative: int = 0
    if integer < 0:
        negative = 1
        integer = 0 - integer
    base: int = 8
    if conversion == 98:
        base = 2
    reverse = stack_alloc(80)
    count: int = 0
    if integer == 0:
        store_i8(reverse, 0, 48)
        count = 1
    while integer > 0:
        store_i8(reverse, count, 48 + integer % base)
        integer = integer // base
        count = count + 1
    state = _buffer_new(count + 4)
    if negative != 0:
        _buffer_char(state, 45)
    if alternate != 0:
        _buffer_char(state, 48)
        _buffer_char(state, 111 if conversion == 111 else 98)
    i = count - 1
    while i >= 0:
        _buffer_char(state, load_i8(reverse, i))
        i = i - 1
    result = _buffer_string(state)
    _buffer_free(state)
    return result


def _add_grouping(rendered, separator: int):
    if separator == 0 or ptr_is_null(rendered) != 0:
        return rendered
    text = py_str_utf8(rendered)
    length: int = py_str_byte_len(rendered)
    dot: int = _find_byte(text, 46)
    exponent: int = _find_byte(text, 101)
    if exponent < 0:
        exponent = _find_byte(text, 69)
    integer_end: int = length
    if dot >= 0:
        integer_end = dot
    elif exponent >= 0:
        integer_end = exponent
    start: int = 0
    if length > 0 and (load_i8(text, 0) == 45 or load_i8(text, 0) == 43 or load_i8(text, 0) == 32):
        start = 1
    digits: int = integer_end - start
    groups: int = 0
    if digits > 3:
        groups = (digits - 1) // 3
    if groups == 0:
        return rendered
    state = _buffer_new(length + groups + 1)
    if start != 0:
        _buffer_char(state, load_i8(text, 0))
    i: int = 0
    while i < digits:
        if i > 0 and ((digits - i) % 3) == 0:
            _buffer_char(state, separator)
        _buffer_char(state, load_i8(text, start + i))
        i = i + 1
    _buffer_append(state, ptr_add(text, integer_end), length - integer_end)
    result = _buffer_string(state)
    _buffer_free(state)
    py_decref(rendered)
    return result


def _format_int_builtin(value, spec):
    position: int = 0
    align: int = 62
    fill: int = 32
    first: int = load_i8(spec, position)
    second: int = load_i8(spec, position + 1)
    if first != 0 and (second == 60 or second == 62 or second == 94):
        fill = first
        align = second
        position = position + 2
    elif first == 60 or first == 62 or first == 94:
        align = first
        position = position + 1
    plus: int = 0
    space: int = 0
    if load_i8(spec, position) == 43:
        plus = 1
        position = position + 1
    elif load_i8(spec, position) == 32:
        space = 1
        position = position + 1
    alternate: int = 0
    if load_i8(spec, position) == 35:
        alternate = 1
        position = position + 1
    zero: int = 0
    if load_i8(spec, position) == 48:
        zero = 1
        position = position + 1
    width: int = _parse_digits(spec, position)
    position = _skip_digits(spec, position)
    separator: int = 0
    if load_i8(spec, position) == 44 or load_i8(spec, position) == 95:
        separator = load_i8(spec, position)
        position = position + 1
    conversion: int = 100
    current: int = load_i8(spec, position)
    if current == 100 or current == 120 or current == 88 or current == 111 or current == 98:
        conversion = current
        position = position + 1
    if load_i8(spec, position) != 0:
        return null()
    if alternate != 0 and conversion == 100:
        return null()
    rendered = _int_raw_base(value, conversion, alternate)
    if ptr_is_null(rendered) != 0:
        return null()
    if conversion == 100:
        rendered = _add_grouping(rendered, separator)
    text = py_str_utf8(rendered)
    length: int = py_str_byte_len(rendered)
    signed = rendered
    if length > 0 and load_i8(text, 0) != 45 and (plus != 0 or space != 0):
        state = _buffer_new(length + 2)
        _buffer_char(state, 43 if plus != 0 else 32)
        _buffer_append(state, text, length)
        signed = _buffer_string(state)
        _buffer_free(state)
        py_decref(rendered)
        text = py_str_utf8(signed)
        length = py_str_byte_len(signed)
    result = _pad_signed_text(text, length, width, align, fill, zero)
    py_decref(signed)
    return result


def _format_float_builtin(value, spec):
    position: int = 0
    align: int = 62
    fill: int = 32
    first: int = load_i8(spec, position)
    second: int = load_i8(spec, position + 1)
    if first != 0 and (second == 60 or second == 62 or second == 94):
        fill = first
        align = second
        position = position + 2
    elif first == 60 or first == 62 or first == 94:
        align = first
        position = position + 1
    plus: int = 0
    space: int = 0
    if load_i8(spec, position) == 43:
        plus = 1
        position = position + 1
    elif load_i8(spec, position) == 32:
        space = 1
        position = position + 1
    zero: int = 0
    if load_i8(spec, position) == 48:
        zero = 1
        position = position + 1
    width: int = _parse_digits(spec, position)
    position = _skip_digits(spec, position)
    separator: int = 0
    if load_i8(spec, position) == 44 or load_i8(spec, position) == 95:
        separator = load_i8(spec, position)
        position = position + 1
    precision: int = 6
    has_precision: int = 0
    if load_i8(spec, position) == 46:
        position = position + 1
        if load_i8(spec, position) < 48 or load_i8(spec, position) > 57:
            return null()
        precision = _parse_digits(spec, position)
        position = _skip_digits(spec, position)
        has_precision = 1
    conversion: int = 0
    current: int = load_i8(spec, position)
    if current == 102 or current == 70 or current == 101 or current == 69 or current == 103 or current == 71:
        conversion = current
        position = position + 1
    if load_i8(spec, position) != 0:
        return null()
    rendered = null()
    if conversion == 0 and has_precision == 0:
        rendered = py_float_repr_shortest(value)
    else:
        if conversion == 0:
            conversion = 102
        output = stack_alloc(800)
        length: int = pcc_stdio_format_float_raw(
            output, py_float_to_f64(value), conversion, precision, 0, plus, space
        )
        rendered = py_str_new(output, length)
    rendered = _add_grouping(rendered, separator)
    if ptr_is_null(rendered) != 0:
        return null()
    text = py_str_utf8(rendered)
    length = py_str_byte_len(rendered)
    result = _pad_signed_text(text, length, width, align, fill, zero)
    py_decref(rendered)
    return result


def _call_format_method(method, spec):
    args = py_tuple_new(1)
    if ptr_is_null(args) != 0:
        return _format_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("format callback argument tuple allocation failed"),
        )
    actual_spec = spec
    made_spec: int = 0
    if ptr_is_null(actual_spec) != 0:
        actual_spec = py_str_new(cstr(""), 0)
        made_spec = 1
        if ptr_is_null(actual_spec) != 0:
            py_decref(args)
            return _format_require_result(
                null(),
                cstr("py_str_new"),
                cstr("format callback could not allocate an empty format spec"),
            )
    py_tuple_set_item(args, 0, actual_spec)
    if made_spec != 0:
        py_decref(actual_spec)
    result = py_obj_call(method, args, global_load_ptr("py_None"))
    _format_require_result(
        result,
        cstr("__format__"),
        cstr("format callback returned NULL without setting an exception"),
    )
    py_decref(args)
    return result


@c_abi_export("py_obj_format")
def py_obj_format(value, spec):
    if ptr_is_null(value) != 0:
        return null()
    if is_tagged_int(value) == 0:
        method = py_obj_getattr(value, cstr("__format__"))
        if ptr_is_null(method) == 0:
            result = _call_format_method(method, spec)
            py_decref(method)
            if ptr_is_null(result) == 0 or py_err_occurred() != 0:
                return result
        if py_err_occurred() != 0:
            py_clear_exception()
    text = cstr("")
    none_obj = global_load_ptr("py_None")
    if ptr_is_null(spec) == 0 and ptr_eq(spec, none_obj) == 0 and _type_of(spec) == PY_TYPE_STR:
        text = py_str_utf8(spec)
    if load_i8(text, 0) == 0:
        return py_obj_str(value)
    tag: int = _type_of(value)
    result = null()
    if tag == PY_TYPE_INT:
        result = _format_int_builtin(value, text)
    elif tag == PY_TYPE_STR:
        result = _format_string_builtin(value, text)
    elif tag == PY_TYPE_FLOAT:
        result = _format_float_builtin(value, text)
    if ptr_is_null(result) == 0:
        return result
    py_raise_owned(py_exc_new(2, cstr("unsupported format specifier")))
    return null()


def _percent_next_argument(arguments, state):
    index: int = load_i64(state, 0)
    if _type_of(arguments) == PY_TYPE_TUPLE:
        store_i64(state, 8, 1)
        length: int = py_tuple_len(arguments)
        if index >= length:
            py_raise_owned(py_exc_new(3, cstr("not enough arguments for format string")))
            return null()
        item = py_tuple_get(arguments, index)
        store_i64(state, 0, index + 1)
        store_i64(state, 16, 1)
        return item
    store_i64(state, 8, 0)
    if index != 0:
        py_raise_owned(py_exc_new(3, cstr("not enough arguments for format string")))
        return null()
    store_i64(state, 0, 1)
    store_i64(state, 16, 0)
    return arguments


def _percent_release_argument(argument, state) -> None:
    if load_i64(state, 16) != 0 and ptr_is_null(argument) == 0:
        py_decref(argument)
    store_i64(state, 16, 0)


def _scan_percent_conversion(data, position: int, length: int) -> int:
    while position < length:
        byte: int = load_i8(data, position)
        if byte == 35 or byte == 48 or byte == 45 or byte == 32 or byte == 43:
            position = position + 1
        else:
            break
    while position < length and load_i8(data, position) >= 48 and load_i8(data, position) <= 57:
        position = position + 1
    if position < length and load_i8(data, position) == 46:
        position = position + 1
        while position < length and load_i8(data, position) >= 48 and load_i8(data, position) <= 57:
            position = position + 1
    while position < length:
        byte = load_i8(data, position)
        if byte == 104 or byte == 108 or byte == 76 or byte == 122 or byte == 106 or byte == 116:
            position = position + 1
        else:
            break
    return position


def _percent_width(data, start: int, conversion_pos: int) -> int:
    position: int = start
    while position < conversion_pos:
        byte: int = load_i8(data, position)
        if byte >= 48 and byte <= 57:
            return _parse_digits(data, position)
        if byte == 46:
            return 0
        position = position + 1
    return 0


def _percent_precision(data, start: int, conversion_pos: int) -> int:
    position: int = start
    while position < conversion_pos:
        if load_i8(data, position) == 46:
            position = position + 1
            return _parse_digits(data, position)
        position = position + 1
    return -1


def _percent_has_flag(data, start: int, conversion_pos: int, flag: int) -> int:
    position: int = start
    while position < conversion_pos:
        if load_i8(data, position) == flag:
            return 1
        if load_i8(data, position) >= 48 and load_i8(data, position) <= 57:
            return 0
        if load_i8(data, position) == 46:
            return 0
        position = position + 1
    return 0


def _append_percent_text(state, rendered, data, start: int, conversion_pos: int) -> int:
    if ptr_is_null(rendered) != 0 or _type_of(rendered) != PY_TYPE_STR:
        if py_err_occurred() == 0:
            py_raise_owned(py_exc_new(3, cstr("format argument cannot be converted to string")))
        return -1
    text = py_str_utf8(rendered)
    length: int = py_str_byte_len(rendered)
    width: int = _percent_width(data, start, conversion_pos)
    precision: int = _percent_precision(data, start, conversion_pos)
    left: int = _percent_has_flag(data, start, conversion_pos, 45)
    if precision >= 0 and precision < length:
        length = precision
    padding: int = 0
    if width > length:
        padding = width - length
    if left == 0:
        _buffer_repeat(state, 32, padding)
    rc: int = _buffer_append(state, text, length)
    if left != 0:
        _buffer_repeat(state, 32, padding)
    return rc


def _i64_render(value: int, base: int, uppercase: int, alternate: int):
    negative: int = 0
    if value < 0:
        negative = 1
        value = 0 - value
    reverse = stack_alloc(80)
    count: int = 0
    if value == 0:
        store_i8(reverse, 0, 48)
        count = 1
    while value > 0:
        digit: int = value % base
        byte: int = 48 + digit
        if digit >= 10:
            byte = (65 if uppercase != 0 else 97) + digit - 10
        store_i8(reverse, count, byte)
        count = count + 1
        value = value // base
    state = _buffer_new(count + 5)
    if negative != 0:
        _buffer_char(state, 45)
    if alternate != 0 and base != 10:
        _buffer_char(state, 48)
        if base == 16:
            _buffer_char(state, 88 if uppercase != 0 else 120)
        elif base == 8:
            _buffer_char(state, 111)
        else:
            _buffer_char(state, 98)
    i: int = count - 1
    while i >= 0:
        _buffer_char(state, load_i8(reverse, i))
        i = i - 1
    result = _buffer_string(state)
    _buffer_free(state)
    return result


def _append_percent_integer(state, argument, data, start: int, conversion_pos: int, conversion: int) -> int:
    tag: int = _type_of(argument)
    if tag != PY_TYPE_BOOL and tag != PY_TYPE_INT:
        py_raise_owned(py_exc_new(3, cstr("integer format requires a number")))
        return -1
    integer: int = 0
    if tag == PY_TYPE_BOOL:
        if ptr_eq(argument, global_load_ptr("py_True")) != 0:
            integer = 1
    else:
        integer = py_int_value_i64(argument)
    base: int = 10
    uppercase: int = 0
    if conversion == 120 or conversion == 88:
        base = 16
        if conversion == 88:
            uppercase = 1
    elif conversion == 111:
        base = 8
    alternate: int = _percent_has_flag(data, start, conversion_pos, 35)
    rendered = _i64_render(integer, base, uppercase, alternate)
    if ptr_is_null(rendered) != 0:
        return -1
    text = py_str_utf8(rendered)
    length: int = py_str_byte_len(rendered)
    plus: int = _percent_has_flag(data, start, conversion_pos, 43)
    space: int = _percent_has_flag(data, start, conversion_pos, 32)
    signed = rendered
    if length > 0 and load_i8(text, 0) != 45 and (plus != 0 or space != 0):
        signed_state = _buffer_new(length + 2)
        _buffer_char(signed_state, 43 if plus != 0 else 32)
        _buffer_append(signed_state, text, length)
        signed = _buffer_string(signed_state)
        _buffer_free(signed_state)
        py_decref(rendered)
        text = py_str_utf8(signed)
        length = py_str_byte_len(signed)
    width: int = _percent_width(data, start, conversion_pos)
    precision: int = _percent_precision(data, start, conversion_pos)
    left: int = _percent_has_flag(data, start, conversion_pos, 45)
    zero: int = _percent_has_flag(data, start, conversion_pos, 48)
    if precision >= 0:
        zero = 0
    padding: int = 0
    if width > length:
        padding = width - length
    if left == 0 and zero == 0:
        _buffer_repeat(state, 32, padding)
    prefix: int = 0
    if length > 0 and (load_i8(text, 0) == 45 or load_i8(text, 0) == 43 or load_i8(text, 0) == 32):
        prefix = 1
        _buffer_char(state, load_i8(text, 0))
    if left == 0 and zero != 0:
        _buffer_repeat(state, 48, padding)
    digit_start: int = prefix
    if length >= prefix + 2 and load_i8(text, prefix) == 48:
        next_byte: int = load_i8(text, prefix + 1)
        if next_byte == 120 or next_byte == 88 or next_byte == 111 or next_byte == 98:
            _buffer_char(state, 48)
            _buffer_char(state, next_byte)
            digit_start = prefix + 2
    digits: int = length - digit_start
    if precision > digits:
        _buffer_repeat(state, 48, precision - digits)
    _buffer_append(state, ptr_add(text, digit_start), digits)
    if left != 0:
        _buffer_repeat(state, 32, padding)
    py_decref(signed)
    return 0


def _append_percent_float(state, argument, data, start: int, conversion_pos: int, conversion: int) -> int:
    precision: int = _percent_precision(data, start, conversion_pos)
    if precision < 0:
        precision = 6
    alternate: int = _percent_has_flag(data, start, conversion_pos, 35)
    plus: int = _percent_has_flag(data, start, conversion_pos, 43)
    space: int = _percent_has_flag(data, start, conversion_pos, 32)
    output = stack_alloc(800)
    length: int = pcc_stdio_format_float_raw(
        output,
        py_float_to_f64(argument),
        conversion,
        precision,
        alternate,
        plus,
        space,
    )
    width: int = _percent_width(data, start, conversion_pos)
    left: int = _percent_has_flag(data, start, conversion_pos, 45)
    zero: int = _percent_has_flag(data, start, conversion_pos, 48)
    padding: int = 0
    if width > length:
        padding = width - length
    if left == 0 and zero == 0:
        _buffer_repeat(state, 32, padding)
    source: int = 0
    if left == 0 and zero != 0:
        if length > 0 and (load_i8(output, 0) == 45 or load_i8(output, 0) == 43 or load_i8(output, 0) == 32):
            _buffer_char(state, load_i8(output, 0))
            source = 1
        _buffer_repeat(state, 48, padding)
    _buffer_append(state, ptr_add(output, source), length - source)
    if left != 0:
        _buffer_repeat(state, 32, padding)
    return 0


def _bytes_payload(value):
    tag: int = _type_of(value)
    if tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY:
        return ptr_add(value, 24)
    if tag == PY_TYPE_MEMORYVIEW:
        base = pcc_gc_load_ptr(value, ptr_add(value, 16))
        return _bytes_payload(base)
    return null()


def _bytes_payload_length(value) -> int:
    tag: int = _type_of(value)
    if tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY:
        return load_i64(value, 16)
    if tag == PY_TYPE_MEMORYVIEW:
        base = pcc_gc_load_ptr(value, ptr_add(value, 16))
        return _bytes_payload_length(base)
    return -1


def _mapping_argument(arguments, data, key_start: int, key_length: int, bytes_key: int):
    if _type_of(arguments) != PY_TYPE_DICT:
        py_raise_owned(py_exc_new(3, cstr("format requires a mapping")))
        return null()
    key = null()
    if bytes_key != 0:
        key = py_bytes_new(ptr_add(data, key_start), key_length)
    else:
        key = py_str_new(ptr_add(data, key_start), key_length)
    if ptr_is_null(key) != 0:
        return null()
    value = py_dict_get(arguments, key)
    py_decref(key)
    if ptr_is_null(value) != 0:
        py_raise_owned(py_exc_new(4, cstr("format key not found")))
    return value


def _format_percent(data, length: int, arguments, bytes_mode: int):
    output = _buffer_new(length + 64)
    if ptr_is_null(output) != 0:
        py_raise_owned(py_exc_new(7, cstr("out of memory")))
        return null()
    arg_state = stack_alloc(24)
    store_i64(arg_state, 0, 0)
    store_i64(arg_state, 8, 0)
    store_i64(arg_state, 16, 0)
    failed: int = 0
    position: int = 0
    while position < length and failed == 0:
        byte: int = load_i8(data, position)
        if byte != 37:
            if _buffer_char(output, byte) != 0:
                failed = 1
            position = position + 1
        elif position + 1 < length and load_i8(data, position + 1) == 37:
            if _buffer_char(output, 37) != 0:
                failed = 1
            position = position + 2
        else:
            spec_start: int = position + 1
            scan_start: int = spec_start
            argument = null()
            owned: int = 0
            if spec_start < length and load_i8(data, spec_start) == 40:
                key_start: int = spec_start + 1
                key_end: int = key_start
                while key_end < length and load_i8(data, key_end) != 41:
                    key_end = key_end + 1
                if key_end >= length:
                    py_raise_owned(py_exc_new(2, cstr("incomplete format key")))
                    failed = 1
                else:
                    argument = _mapping_argument(
                        arguments, data, key_start, key_end - key_start, bytes_mode
                    )
                    owned = 1
                    scan_start = key_end + 1
            conversion_pos: int = scan_start
            if failed == 0:
                conversion_pos = _scan_percent_conversion(data, scan_start, length)
                if conversion_pos >= length:
                    py_raise_owned(py_exc_new(2, cstr("incomplete format")))
                    failed = 1
            if failed == 0 and ptr_is_null(argument) != 0:
                argument = _percent_next_argument(arguments, arg_state)
                owned = load_i64(arg_state, 16)
                if ptr_is_null(argument) != 0:
                    failed = 1
            if failed == 0:
                conversion: int = load_i8(data, conversion_pos)
                if conversion == 115 or conversion == 114 or (bytes_mode != 0 and (conversion == 98 or conversion == 97)):
                    rendered = null()
                    if bytes_mode != 0 and (conversion == 115 or conversion == 98):
                        payload = _bytes_payload(argument)
                        payload_length: int = _bytes_payload_length(argument)
                        if ptr_is_null(payload) != 0:
                            py_raise_owned(py_exc_new(3, cstr("%b requires a bytes-like object")))
                            failed = 1
                        else:
                            width: int = _percent_width(data, scan_start, conversion_pos)
                            precision: int = _percent_precision(data, scan_start, conversion_pos)
                            left: int = _percent_has_flag(data, scan_start, conversion_pos, 45)
                            if precision >= 0 and precision < payload_length:
                                payload_length = precision
                            padding: int = 0
                            if width > payload_length:
                                padding = width - payload_length
                            if left == 0:
                                _buffer_repeat(output, 32, padding)
                            _buffer_append(output, payload, payload_length)
                            if left != 0:
                                _buffer_repeat(output, 32, padding)
                    else:
                        if conversion == 114:
                            rendered = py_obj_repr(argument)
                        elif bytes_mode != 0 and conversion == 97:
                            rendered = py_obj_ascii(argument)
                        else:
                            rendered = py_obj_str(argument)
                            if ptr_is_null(rendered) != 0:
                                rendered = py_obj_repr(argument)
                        if ptr_is_null(rendered) != 0:
                            failed = 1
                        elif _append_percent_text(
                            output, rendered, data, scan_start, conversion_pos
                        ) != 0:
                            failed = 1
                        if ptr_is_null(rendered) == 0:
                            py_decref(rendered)
                elif conversion == 100 or conversion == 105 or conversion == 117 or conversion == 120 or conversion == 88 or conversion == 111:
                    if _append_percent_integer(
                        output, argument, data, scan_start, conversion_pos, conversion
                    ) != 0:
                        failed = 1
                elif conversion == 101 or conversion == 69 or conversion == 102 or conversion == 70 or conversion == 103 or conversion == 71:
                    if _append_percent_float(
                        output, argument, data, scan_start, conversion_pos, conversion
                    ) != 0:
                        failed = 1
                elif conversion == 99:
                    payload = _bytes_payload(argument)
                    payload_length = _bytes_payload_length(argument)
                    if bytes_mode != 0 and ptr_is_null(payload) == 0:
                        if payload_length != 1:
                            py_raise_owned(py_exc_new(3, cstr("%c requires a single byte")))
                            failed = 1
                        else:
                            _buffer_char(output, load_i8(payload, 0))
                    elif bytes_mode == 0 and _type_of(argument) == PY_TYPE_STR:
                        if py_str_byte_len(argument) != 1:
                            py_raise_owned(py_exc_new(3, cstr("%c requires int or char")))
                            failed = 1
                        else:
                            _buffer_char(output, load_i8(py_str_utf8(argument), 0))
                    elif _type_of(argument) == PY_TYPE_BOOL or _type_of(argument) == PY_TYPE_INT:
                        integer: int = py_int_value_i64(argument)
                        if bytes_mode != 0 and (integer < 0 or integer > 255):
                            py_raise_owned(py_exc_new(2, cstr("%c arg not in range(256)")))
                            failed = 1
                        else:
                            _buffer_char(output, integer & 255)
                    else:
                        py_raise_owned(py_exc_new(3, cstr("%c requires int or char")))
                        failed = 1
                else:
                    py_raise_owned(py_exc_new(2, cstr("unsupported format character")))
                    failed = 1
                position = conversion_pos + 1
            if owned != 0 and ptr_is_null(argument) == 0:
                py_decref(argument)
            store_i64(arg_state, 16, 0)
    result = null()
    if failed == 0:
        if load_i64(arg_state, 8) != 0 and _type_of(arguments) == PY_TYPE_TUPLE:
            if load_i64(arg_state, 0) < py_tuple_len(arguments):
                py_raise_owned(py_exc_new(3, cstr("not all arguments converted during formatting")))
                failed = 1
    if failed == 0:
        if bytes_mode != 0:
            result = _buffer_bytes(output)
        else:
            result = _buffer_string(output)
    _buffer_free(output)
    return result


@c_abi_export("py_str_mod")
def py_str_mod(format_obj, arguments):
    if ptr_is_null(format_obj) != 0 or _type_of(format_obj) != PY_TYPE_STR:
        py_raise_owned(py_exc_new(3, cstr("left operand of % must be str")))
        return null()
    return _format_percent(
        py_str_utf8(format_obj), py_str_byte_len(format_obj), arguments, 0
    )


@c_abi_export("py_bytes_mod")
def py_bytes_mod(format_obj, arguments):
    format_tag: int = _type_of(format_obj)
    if format_tag != PY_TYPE_BYTES and format_tag != PY_TYPE_BYTEARRAY:
        py_raise_owned(py_exc_new(3, cstr("left operand of % must be bytes or bytearray")))
        return null()
    result = _format_percent(
        ptr_add(format_obj, 24), load_i64(format_obj, 16), arguments, 1
    )
    if ptr_is_null(result) == 0 and format_tag == PY_TYPE_BYTEARRAY:
        bytearray = py_bytearray_from_obj(result)
        py_decref(result)
        return bytearray
    return result
