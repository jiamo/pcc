"""Finite freestanding stdio surface authored in pcc-Python.

This module grows one externally verified ABI slice at a time.  It is not a
claim of general POSIX ``FILE`` compatibility.
"""

from pcc import i64
from pcc.extern import (
    c_abi_export,
    c_abi_typed_export,
    c_abi_variadic_export,
    c_ptr,
)
from pcc.unsafe import (
    abi_constant,
    close,
    cstr,
    define_global_i64_array,
    define_global_ptr_to_global,
    free,
    f64_bits,
    f64_signbit,
    float_to_i64,
    i64_to_float,
    initial_environ,
    load_i32,
    load_i64,
    load_i8,
    load_ptr,
    logical_shift_right_i64,
    malloc,
    mul_overflow_i64,
    null,
    open_file,
    ptr_add,
    ptr_diff,
    ptr_is_null,
    read,
    seek_file,
    spawn_process_pipe,
    stack_alloc,
    store_i64,
    store_i8,
    store_ptr,
    unlinkat,
    unsigned_rem_i64,
    unsigned_div_i64,
    va_arg_f64,
    va_arg_i32,
    va_arg_i64,
    va_arg_ptr,
    va_arg_u32,
    va_cursor,
    va_end,
    va_start,
    waitpid,
    wrapping_mul_i64,
    write,
)

__pcc_freestanding__ = True


define_global_i64_array(
    "pcc_stdio_stdin_storage",
    abi_constant("stdio.file.magic"),
    0,
    abi_constant("stdio.flag.input_standard"),
    0,
    0,
    0,
    0,
    0,
)
define_global_i64_array(
    "pcc_stdio_stdout_storage",
    abi_constant("stdio.file.magic"),
    1,
    abi_constant("stdio.flag.output_standard"),
    0,
    0,
    0,
    0,
    0,
)
define_global_i64_array(
    "pcc_stdio_stderr_storage",
    abi_constant("stdio.file.magic"),
    2,
    abi_constant("stdio.flag.output_standard"),
    0,
    0,
    0,
    0,
    0,
)
define_global_ptr_to_global("__stdinp", "pcc_stdio_stdin_storage")
define_global_ptr_to_global("__stdoutp", "pcc_stdio_stdout_storage")
define_global_ptr_to_global("__stderrp", "pcc_stdio_stderr_storage")
define_global_ptr_to_global("stdin", "pcc_stdio_stdin_storage")
define_global_ptr_to_global("stdout", "pcc_stdio_stdout_storage")
define_global_ptr_to_global("stderr", "pcc_stdio_stderr_storage")


@c_abi_export("remove")
def remove(path) -> i64:
    # POSIX remove() first applies unlink semantics and, for an empty
    # directory, retries with AT_REMOVEDIR.  The unsafe boundary supplies the
    # target-specific AT_FDCWD value; this source owns only the portable rule.
    if unlinkat(path, 0) == 0:
        return 0
    if unlinkat(path, 1) == 0:
        return 0
    return -1


@c_abi_export("pcc_stdio_stream_new")
def _stream_new(fd: i64, flags: i64, aux: i64):
    stream = malloc(abi_constant("stdio.file.size"))
    if ptr_is_null(stream):
        return null()
    buffer = null()
    buffer_capacity: i64 = 0
    if (flags & abi_constant("stdio.flag.writable")) != 0:
        buffer_capacity: i64 = 4096
        buffer = malloc(buffer_capacity)
        if ptr_is_null(buffer):
            free(stream)
            return null()
    store_i64(
        stream,
        abi_constant("stdio.file.magic_offset"),
        abi_constant("stdio.file.magic"),
    )
    store_i64(stream, abi_constant("stdio.file.fd_offset"), fd)
    store_i64(stream, abi_constant("stdio.file.flags_offset"), flags)
    store_i64(stream, abi_constant("stdio.file.aux_offset"), aux)
    store_ptr(stream, abi_constant("stdio.file.buffer_offset"), buffer)
    store_i64(
        stream,
        abi_constant("stdio.file.buffer_capacity_offset"),
        buffer_capacity,
    )
    store_i64(stream, abi_constant("stdio.file.buffer_length_offset"), 0)
    store_i64(stream, abi_constant("stdio.file.buffer_position_offset"), 0)
    return stream


@c_abi_export("fopen")
def fopen(path, mode) -> c_ptr:
    if ptr_is_null(path) or ptr_is_null(mode):
        return null()
    first = load_i8(mode, 0)
    access: i64 = 0
    disposition: i64 = 0
    flags: i64 = 0
    if first == 114:
        flags = abi_constant("stdio.flag.readable")
    elif first == 119:
        access: i64 = 1
        disposition: i64 = 1
        flags = abi_constant("stdio.flag.writable")
    elif first == 97:
        access: i64 = 1
        disposition: i64 = 2
        flags = abi_constant("stdio.flag.writable")
    elif first == 120:
        access: i64 = 1
        disposition: i64 = 3
        flags = abi_constant("stdio.flag.writable")
    else:
        return null()

    offset: i64 = 1
    while load_i8(mode, offset) != 0:
        marker = load_i8(mode, offset)
        if marker == 43:
            access: i64 = 2
            flags = abi_constant("stdio.flag.readable") | abi_constant(
                "stdio.flag.writable"
            )
        elif marker != 98 and marker != 116:
            return null()
        offset = offset + 1
    if first == 97:
        flags = flags | abi_constant("stdio.flag.append")

    fd = open_file(path, access, disposition)
    if fd < 0:
        return null()
    append_position: i64 = 0
    if (flags & abi_constant("stdio.flag.append")) != 0:
        # C append streams start with their logical position at EOF.  Later
        # fseek calls may move that logical position even though the eventual
        # write is still forced to EOF by O_APPEND.
        append_position = seek_file(fd, 0, 2)
        if append_position < 0:
            close(fd)
            return null()
    stream = _stream_new(fd, flags, 0)
    if ptr_is_null(stream):
        close(fd)
        return null()
    if (flags & abi_constant("stdio.flag.append")) != 0:
        store_i64(
            stream,
            abi_constant("stdio.file.buffer_position_offset"),
            append_position,
        )
    return stream


@c_abi_typed_export("fileno", "i32", ("ptr",))
def fileno(stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    return load_i64(stream, abi_constant("stdio.file.fd_offset"))


@c_abi_export("pcc_stdio_flush_output")
def _flush_output(stream) -> i64:
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    buffer = load_ptr(stream, abi_constant("stdio.file.buffer_offset"))
    length = load_i64(
        stream, abi_constant("stdio.file.buffer_length_offset")
    )
    if ptr_is_null(buffer) or length <= 0:
        return 0
    offset: i64 = 0
    fd = load_i64(stream, abi_constant("stdio.file.fd_offset"))
    while offset < length:
        written = write(fd, ptr_add(buffer, offset), length - offset)
        while written == -4:
            written = write(fd, ptr_add(buffer, offset), length - offset)
        if written <= 0:
            remaining = length - offset
            move_index: i64 = 0
            while move_index < remaining:
                store_i8(
                    buffer,
                    move_index,
                    load_i8(buffer, offset + move_index),
                )
                move_index = move_index + 1
            store_i64(
                stream,
                abi_constant("stdio.file.buffer_length_offset"),
                remaining,
            )
            if (flags & abi_constant("stdio.flag.append")) != 0:
                # The bytes before ``offset`` were committed through O_APPEND.
                # The shifted remainder now starts at the fd's resulting
                # position, not at the EOF captured when buffering began.
                store_i64(
                    stream,
                    abi_constant("stdio.file.buffer_position_offset"),
                    seek_file(fd, 0, 1),
                )
            store_i64(
                stream,
                abi_constant("stdio.file.flags_offset"),
                flags | abi_constant("stdio.flag.error"),
            )
            return -1
        offset = offset + written
    store_i64(stream, abi_constant("stdio.file.buffer_length_offset"), 0)
    if (flags & abi_constant("stdio.flag.append")) != 0:
        # A successful O_APPEND write advances the stream's logical position
        # to the committed EOF.  The next buffered write starts from here
        # unless an intervening seek/read changes it.
        store_i64(
            stream,
            abi_constant("stdio.file.buffer_position_offset"),
            seek_file(fd, 0, 1),
        )
    return 0


@c_abi_export("pcc_stdio_stream_release_buffer")
def _stream_release_buffer(stream) -> None:
    buffer = load_ptr(stream, abi_constant("stdio.file.buffer_offset"))
    if not ptr_is_null(buffer):
        free(buffer)
        store_ptr(
            stream,
            abi_constant("stdio.file.buffer_offset"),
            null(),
        )


@c_abi_export("fwrite")
def fwrite(input_buffer, size: i64, count: i64, stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return 0
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    if (flags & abi_constant("stdio.flag.writable")) == 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return 0
    if size <= 0 or count <= 0:
        return 0
    if mul_overflow_i64(size, count):
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return 0
    total = wrapping_mul_i64(size, count)
    output_buffer = load_ptr(
        stream, abi_constant("stdio.file.buffer_offset")
    )
    capacity = load_i64(
        stream, abi_constant("stdio.file.buffer_capacity_offset")
    )
    consumed: i64 = 0
    if ptr_is_null(output_buffer) or capacity <= 0:
        fd = load_i64(stream, abi_constant("stdio.file.fd_offset"))
        while consumed < total:
            written = write(
                fd, ptr_add(input_buffer, consumed), total - consumed
            )
            while written == -4:
                written = write(
                    fd, ptr_add(input_buffer, consumed), total - consumed
                )
            if written <= 0:
                store_i64(
                    stream,
                    abi_constant("stdio.file.flags_offset"),
                    flags | abi_constant("stdio.flag.error"),
                )
                return unsigned_div_i64(consumed, size)
            consumed = consumed + written
        return count

    while consumed < total:
        buffered = load_i64(
            stream, abi_constant("stdio.file.buffer_length_offset")
        )
        if buffered >= capacity:
            if _flush_output(stream) != 0:
                return unsigned_div_i64(consumed, size)
            buffered: i64 = 0
        available = capacity - buffered
        chunk = total - consumed
        if chunk > available:
            chunk = available
        copy_index: i64 = 0
        while copy_index < chunk:
            store_i8(
                output_buffer,
                buffered + copy_index,
                load_i8(input_buffer, consumed + copy_index),
            )
            copy_index = copy_index + 1
        buffered = buffered + chunk
        consumed = consumed + chunk
        store_i64(
            stream,
            abi_constant("stdio.file.buffer_length_offset"),
            buffered,
        )
        if buffered >= capacity and _flush_output(stream) != 0:
            return unsigned_div_i64(consumed, size)
    return count


@c_abi_export("fread")
def fread(buffer, size: i64, count: i64, stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return 0
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    if (flags & abi_constant("stdio.flag.readable")) == 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return 0
    if size <= 0 or count <= 0:
        return 0
    if mul_overflow_i64(size, count):
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return 0
    total = wrapping_mul_i64(size, count)
    received = read(
        load_i64(stream, abi_constant("stdio.file.fd_offset")), buffer, total
    )
    if received < 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return 0
    if received == 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.eof"),
        )
    if (flags & abi_constant("stdio.flag.append")) != 0:
        # On an update stream a read advances the logical position used by a
        # later, standards-compliant write transition.
        store_i64(
            stream,
            abi_constant("stdio.file.buffer_position_offset"),
            seek_file(
                load_i64(stream, abi_constant("stdio.file.fd_offset")),
                0,
                1,
            ),
        )
    return unsigned_div_i64(received, size)


@c_abi_export("fgetc")
def fgetc(stream) -> i64:
    byte = stack_alloc(1)
    if fread(byte, 1, 1, stream) != 1:
        return -1
    return load_i8(byte, 0) & 255


@c_abi_typed_export("fseek", "i32", ("ptr", "i64", "i32"))
def fseek(stream, offset: i64, whence: i64) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    if whence < 0 or whence > 2:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return -1
    append_seek_position: i64 = -1
    if (
        whence == 1
        and (flags & abi_constant("stdio.flag.append")) != 0
    ):
        buffered = load_i64(
            stream, abi_constant("stdio.file.buffer_length_offset")
        )
        if buffered > 0:
            append_seek_position = load_i64(
                stream,
                abi_constant("stdio.file.buffer_position_offset"),
            ) + buffered
        else:
            append_seek_position = seek_file(
                load_i64(stream, abi_constant("stdio.file.fd_offset")),
                0,
                1,
            )
        if append_seek_position < 0:
            store_i64(
                stream,
                abi_constant("stdio.file.flags_offset"),
                flags | abi_constant("stdio.flag.error"),
            )
            return -1
    if (
        (flags & abi_constant("stdio.flag.writable")) != 0
        and _flush_output(stream) != 0
    ):
        return -1
    if append_seek_position >= 0:
        # Flushing an append stream moves the kernel descriptor to physical
        # EOF.  C stdio nevertheless defines SEEK_CUR from the stream's
        # pre-flush logical position, so preserve that position explicitly.
        position = seek_file(
            load_i64(stream, abi_constant("stdio.file.fd_offset")),
            append_seek_position + offset,
            0,
        )
    else:
        position = seek_file(
            load_i64(stream, abi_constant("stdio.file.fd_offset")),
            offset,
            whence,
        )
    if position < 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return -1
    store_i64(
        stream,
        abi_constant("stdio.file.flags_offset"),
        flags & ~abi_constant("stdio.flag.eof"),
    )
    store_i64(
        stream,
        abi_constant("stdio.file.buffer_position_offset"),
        position,
    )
    return 0


@c_abi_typed_export("ftell", "i64", ("ptr",))
def ftell(stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    buffered: i64 = 0
    if (flags & abi_constant("stdio.flag.writable")) != 0:
        buffered = load_i64(
            stream, abi_constant("stdio.file.buffer_length_offset")
        )
        if (
            buffered > 0
            and (flags & abi_constant("stdio.flag.append")) != 0
        ):
            position = load_i64(
                stream,
                abi_constant("stdio.file.buffer_position_offset"),
            )
            if position < 0:
                store_i64(
                    stream,
                    abi_constant("stdio.file.flags_offset"),
                    flags | abi_constant("stdio.flag.error"),
                )
                return -1
            return position + buffered
    position = seek_file(
        load_i64(stream, abi_constant("stdio.file.fd_offset")), 0, 1
    )
    if position < 0:
        store_i64(
            stream,
            abi_constant("stdio.file.flags_offset"),
            flags | abi_constant("stdio.flag.error"),
        )
        return -1
    if buffered > 0:
        position = position + buffered
    return position


@c_abi_export("fflush")
def fflush(stream) -> i64:
    if ptr_is_null(stream):
        return 0
    if load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    if (flags & abi_constant("stdio.flag.writable")) != 0:
        return _flush_output(stream)
    return 0


@c_abi_export("ferror")
def ferror(stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return 1
    if (
        load_i64(stream, abi_constant("stdio.file.flags_offset"))
        & abi_constant("stdio.flag.error")
    ) != 0:
        return 1
    return 0


@c_abi_export("fclose")
def fclose(stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    if (flags & abi_constant("stdio.flag.standard")) != 0:
        return fflush(stream)
    flush_result: i64 = 0
    if (flags & abi_constant("stdio.flag.writable")) != 0:
        flush_result = _flush_output(stream)
    rc = close(load_i64(stream, abi_constant("stdio.file.fd_offset")))
    _stream_release_buffer(stream)
    store_i64(stream, abi_constant("stdio.file.magic_offset"), 0)
    free(stream)
    if flush_result != 0 or rc != 0:
        return -1
    return 0


@c_abi_export("pcc_stdio_format_emit_char")
def _format_emit_char(
    output,
    capacity: i64,
    stream,
    index: i64,
    byte: i64,
) -> i64:
    if not ptr_is_null(stream):
        one = stack_alloc(1)
        store_i8(one, 0, byte)
        if fwrite(one, 1, 1, stream) != 1:
            return -1
    elif capacity > 0 and index < capacity - 1 and not ptr_is_null(output):
        store_i8(output, index, byte)
    return index + 1


@c_abi_export("pcc_stdio_format_emit_cstr")
def _format_emit_cstr(
    output,
    capacity: i64,
    stream,
    index: i64,
    value,
    limit: i64,
) -> i64:
    if ptr_is_null(value):
        value = cstr("(null)")
    offset: i64 = 0
    while load_i8(value, offset) != 0 and (limit < 0 or offset < limit):
        index = _format_emit_char(
            output, capacity, stream, index, load_i8(value, offset)
        )
        if index < 0:
            return -1
        offset = offset + 1
    return index


@c_abi_export("pcc_stdio_format_cstr_length")
def _format_cstr_length(value, limit: i64) -> i64:
    if ptr_is_null(value):
        value = cstr("(null)")
    length: i64 = 0
    while load_i8(value, length) != 0 and (limit < 0 or length < limit):
        length = length + 1
    return length


@c_abi_export("pcc_stdio_format_emit_repeat")
def _format_emit_repeat(
    output,
    capacity: i64,
    stream,
    index: i64,
    byte: i64,
    count: i64,
) -> i64:
    while count > 0:
        index = _format_emit_char(output, capacity, stream, index, byte)
        if index < 0:
            return -1
        count = count - 1
    return index


@c_abi_export("pcc_stdio_format_unsigned_digits")
def _format_unsigned_digits(value: i64, base: i64) -> i64:
    count: i64 = 1
    while unsigned_div_i64(value, base) != 0:
        count = count + 1
        value = unsigned_div_i64(value, base)
    return count


@c_abi_export("pcc_stdio_format_emit_unsigned")
def _format_emit_unsigned(
    output,
    capacity: i64,
    stream,
    index: i64,
    value: i64,
    base: i64,
    uppercase: i64,
) -> i64:
    digits = stack_alloc(66)
    count: i64 = 0
    if value == 0:
        store_i8(digits, 0, 48)
        count: i64 = 1
    else:
        while value != 0:
            digit = unsigned_rem_i64(value, base)
            if digit < 10:
                byte = 48 + digit
            elif uppercase != 0:
                byte = 55 + digit
            else:
                byte = 87 + digit
            store_i8(digits, count, byte)
            count = count + 1
            value = unsigned_div_i64(value, base)
    while count > 0:
        count = count - 1
        index = _format_emit_char(
            output, capacity, stream, index, load_i8(digits, count)
        )
        if index < 0:
            return -1
    return index


@c_abi_export("pcc_stdio_format_emit_signed")
def _format_emit_signed(
    output,
    capacity: i64,
    stream,
    index: i64,
    value: i64,
) -> i64:
    magnitude = value
    if value < 0:
        index = _format_emit_char(output, capacity, stream, index, 45)
        if index < 0:
            return -1
        magnitude = wrapping_mul_i64(value, -1)
    return _format_emit_unsigned(
        output, capacity, stream, index, magnitude, 10, 0
    )


@c_abi_export("pcc_stdio_format_emit_integer")
def _format_emit_integer(
    output,
    capacity: i64,
    stream,
    index: i64,
    value: i64,
    base: i64,
    uppercase: i64,
    signed: i64,
    width: i64,
    precision: i64,
    left: i64,
    zero_pad: i64,
    plus: i64,
    space: i64,
    prefix_kind: i64,
) -> i64:
    magnitude = value
    sign: i64 = 0
    if signed != 0:
        if value < 0:
            sign: i64 = 45
            magnitude = wrapping_mul_i64(value, -1)
        elif plus != 0:
            sign: i64 = 43
        elif space != 0:
            sign: i64 = 32

    prefix_first: i64 = 0
    prefix_second: i64 = 0
    if prefix_kind == 2:
        prefix_first: i64 = 48
        prefix_second = 88 if uppercase != 0 else 120
    elif prefix_kind == 1:
        if base == 16 and magnitude != 0:
            prefix_first: i64 = 48
            prefix_second = 88 if uppercase != 0 else 120
        elif base == 8 and (magnitude != 0 or precision == 0):
            prefix_first: i64 = 48

    digit_count = _format_unsigned_digits(magnitude, base)
    if magnitude == 0 and precision == 0:
        digit_count: i64 = 0
    zero_count: i64 = 0
    if precision > digit_count:
        zero_count = precision - digit_count
    sign_count: i64 = 1 if sign != 0 else 0
    prefix_count: i64 = 0
    if prefix_first != 0:
        prefix_count: i64 = 1
    if prefix_second != 0:
        prefix_count: i64 = 2
    if zero_pad != 0 and left == 0 and precision < 0:
        requested_zeroes = width - sign_count - prefix_count - digit_count
        if requested_zeroes > zero_count:
            zero_count = requested_zeroes
    spaces = width - sign_count - prefix_count - zero_count - digit_count
    if spaces < 0:
        spaces: i64 = 0

    if left == 0:
        index = _format_emit_repeat(
            output, capacity, stream, index, 32, spaces
        )
    if index < 0:
        return -1
    if sign != 0:
        index = _format_emit_char(output, capacity, stream, index, sign)
    if index < 0:
        return -1
    if prefix_first != 0:
        index = _format_emit_char(
            output, capacity, stream, index, prefix_first
        )
    if index < 0:
        return -1
    if prefix_second != 0:
        index = _format_emit_char(
            output, capacity, stream, index, prefix_second
        )
    if index < 0:
        return -1
    index = _format_emit_repeat(
        output, capacity, stream, index, 48, zero_count
    )
    if index < 0:
        return -1
    if digit_count > 0:
        index = _format_emit_unsigned(
            output, capacity, stream, index, magnitude, base, uppercase
        )
    if index < 0:
        return -1
    if left != 0:
        index = _format_emit_repeat(
            output, capacity, stream, index, 32, spaces
        )
    return index


@c_abi_export("pcc_stdio_format_raw_unsigned")
def _format_raw_unsigned(
    output,
    position: i64,
    value: i64,
    minimum_digits: i64,
) -> i64:
    digits = stack_alloc(32)
    count: i64 = 0
    if value == 0:
        store_i8(digits, 0, 48)
        count: i64 = 1
    else:
        while value != 0:
            store_i8(
                digits,
                count,
                48 + unsigned_rem_i64(value, 10),
            )
            count = count + 1
            value = unsigned_div_i64(value, 10)
    while count < minimum_digits:
        store_i8(output, position, 48)
        position = position + 1
        minimum_digits = minimum_digits - 1
    while count > 0:
        count = count - 1
        store_i8(output, position, load_i8(digits, count))
        position = position + 1
    return position


@c_abi_export("pcc_stdio_format_float_raw")
def _format_float_raw(
    output,
    value: float,
    conversion: i64,
    precision: i64,
    alternate: i64,
    plus: i64,
    space: i64,
) -> i64:
    position: i64 = 0
    negative = f64_signbit(value)
    if negative != 0:
        store_i8(output, position, 45)
        position = position + 1
        value = 0.0 - value
    elif plus != 0:
        store_i8(output, position, 43)
        position = position + 1
    elif space != 0:
        store_i8(output, position, 32)
        position = position + 1

    uppercase: i64 = 0
    if conversion == 70 or conversion == 69 or conversion == 71:
        uppercase: i64 = 1
    bits = f64_bits(value)
    exponent_bits = logical_shift_right_i64(bits, 52) & 2047
    mantissa_bits = bits & 4503599627370495
    if exponent_bits == 2047 and mantissa_bits != 0:
        store_i8(output, position, 78 if uppercase != 0 else 110)
        store_i8(output, position + 1, 65 if uppercase != 0 else 97)
        store_i8(output, position + 2, 78 if uppercase != 0 else 110)
        position = position + 3
        store_i8(output, position, 0)
        return position
    if exponent_bits == 2047 and mantissa_bits == 0:
        store_i8(output, position, 73 if uppercase != 0 else 105)
        store_i8(output, position + 1, 78 if uppercase != 0 else 110)
        store_i8(output, position + 2, 70 if uppercase != 0 else 102)
        position = position + 3
        store_i8(output, position, 0)
        return position

    if precision < 0:
        precision: i64 = 6
    if precision > 64:
        precision: i64 = 64
    if (conversion == 103 or conversion == 71) and precision == 0:
        precision: i64 = 1

    exponent: i64 = 0
    normalized = value
    if normalized != 0.0:
        while normalized >= 10.0:
            normalized = normalized * 0.1
            exponent = exponent + 1
        while normalized < 1.0:
            normalized = normalized * 10.0
            exponent = exponent - 1

    requested_digits = precision + 1
    if conversion == 102 or conversion == 70:
        requested_digits = exponent + precision + 1
    elif conversion == 103 or conversion == 71:
        requested_digits = precision

    digits = stack_alloc(384)
    digit_count = requested_digits
    if digit_count < 0:
        digit_count: i64 = 0
    generated: i64 = 0
    remainder = normalized
    while generated < digit_count:
        digit = float_to_i64(remainder)
        if digit < 0:
            digit: i64 = 0
        if digit > 9:
            digit: i64 = 9
        store_i8(digits, generated, digit)
        remainder = (remainder - i64_to_float(digit)) * 10.0
        generated = generated + 1

    guard: i64 = 0
    if digit_count > 0:
        guard = float_to_i64(remainder)
    elif exponent == 0 - precision - 1:
        guard = float_to_i64(normalized)
    round_up: i64 = 0
    if guard > 5:
        round_up: i64 = 1
    elif guard == 5:
        # Round halfway cases to even. ``remainder`` still contains the guard
        # digit plus its fractional tail: a value above 5 is not an exact tie;
        # at an exact tie only an odd retained digit advances. This is shared
        # by printf-style formatting and round(x, ndigits).
        if remainder > 5.0:
            round_up: i64 = 1
        elif remainder == 5.0 and digit_count > 0:
            if (load_i8(digits, digit_count - 1) & 1) != 0:
                round_up: i64 = 1
    if round_up != 0:
        if digit_count == 0:
            store_i8(digits, 0, 1)
            digit_count: i64 = 1
            exponent = 0 - precision
        else:
            carry_index = digit_count - 1
            carry: i64 = 1
            while carry_index >= 0 and carry != 0:
                rounded = load_i8(digits, carry_index) + 1
                if rounded >= 10:
                    store_i8(digits, carry_index, 0)
                else:
                    store_i8(digits, carry_index, rounded)
                    carry: i64 = 0
                carry_index = carry_index - 1
            if carry != 0:
                store_i8(digits, 0, 1)
                fill_index: i64 = 1
                while fill_index < digit_count:
                    store_i8(digits, fill_index, 0)
                    fill_index = fill_index + 1
                exponent = exponent + 1

    scientific: i64 = 0
    fractional_digits = precision
    if conversion == 101 or conversion == 69:
        scientific: i64 = 1
    elif conversion == 103 or conversion == 71:
        if exponent < -4 or exponent >= precision:
            scientific: i64 = 1
        effective_digits = digit_count
        if alternate == 0:
            while effective_digits > 1 and load_i8(
                digits, effective_digits - 1
            ) == 0:
                effective_digits = effective_digits - 1
        if scientific != 0:
            fractional_digits = effective_digits - 1
        else:
            fractional_digits = effective_digits - exponent - 1
            if fractional_digits < 0:
                fractional_digits: i64 = 0

    if scientific != 0:
        leading: i64 = 0
        if digit_count > 0:
            leading = load_i8(digits, 0)
        store_i8(output, position, 48 + leading)
        position = position + 1
        if fractional_digits > 0 or alternate != 0:
            store_i8(output, position, 46)
            position = position + 1
        fraction_index: i64 = 0
        while fraction_index < fractional_digits:
            source_index = fraction_index + 1
            digit: i64 = 0
            if source_index < digit_count:
                digit = load_i8(digits, source_index)
            store_i8(output, position, 48 + digit)
            position = position + 1
            fraction_index = fraction_index + 1
        store_i8(output, position, 69 if uppercase != 0 else 101)
        position = position + 1
        exponent_value = exponent
        if exponent_value < 0:
            store_i8(output, position, 45)
            exponent_value = wrapping_mul_i64(exponent_value, -1)
        else:
            store_i8(output, position, 43)
        position = position + 1
        position = _format_raw_unsigned(
            output, position, exponent_value, 2
        )
    else:
        if exponent < 0:
            store_i8(output, position, 48)
            position = position + 1
        else:
            integer_position = exponent
            while integer_position >= 0:
                source_index = exponent - integer_position
                digit: i64 = 0
                if source_index >= 0 and source_index < digit_count:
                    digit = load_i8(digits, source_index)
                store_i8(output, position, 48 + digit)
                position = position + 1
                integer_position = integer_position - 1
        if fractional_digits > 0 or alternate != 0:
            store_i8(output, position, 46)
            position = position + 1
        fraction_index: i64 = 1
        while fraction_index <= fractional_digits:
            source_index = exponent + fraction_index
            digit: i64 = 0
            if source_index >= 0 and source_index < digit_count:
                digit = load_i8(digits, source_index)
            store_i8(output, position, 48 + digit)
            position = position + 1
            fraction_index = fraction_index + 1

    store_i8(output, position, 0)
    return position


@c_abi_export("pcc_stdio_format_core")
def _format_core(output, capacity: i64, stream, format, cursor) -> i64:
    if ptr_is_null(format):
        if capacity > 0 and not ptr_is_null(output):
            store_i8(output, 0, 0)
        return -1
    index: i64 = 0
    offset: i64 = 0
    while load_i8(format, offset) != 0:
        marker = load_i8(format, offset)
        if marker != 37:
            index = _format_emit_char(output, capacity, stream, index, marker)
            if index < 0:
                return -1
            offset = offset + 1
            continue

        offset = offset + 1
        conversion = load_i8(format, offset)
        if conversion == 37:
            index = _format_emit_char(output, capacity, stream, index, 37)
            if index < 0:
                return -1
            offset = offset + 1
            continue

        alternate: i64 = 0
        zero_pad: i64 = 0
        left: i64 = 0
        space: i64 = 0
        plus: i64 = 0
        while (
            conversion == 35
            or conversion == 48
            or conversion == 45
            or conversion == 32
            or conversion == 43
        ):
            if conversion == 35:
                alternate: i64 = 1
            elif conversion == 48:
                zero_pad: i64 = 1
            elif conversion == 45:
                left: i64 = 1
            elif conversion == 32:
                space: i64 = 1
            elif conversion == 43:
                plus: i64 = 1
            offset = offset + 1
            conversion = load_i8(format, offset)

        width: i64 = 0
        if conversion == 42:
            width = va_arg_i32(cursor)
            if width < 0:
                width = wrapping_mul_i64(width, -1)
                left: i64 = 1
            offset = offset + 1
            conversion = load_i8(format, offset)
        else:
            while conversion >= 48 and conversion <= 57:
                width = width * 10 + conversion - 48
                offset = offset + 1
                conversion = load_i8(format, offset)

        precision: i64 = -1
        if conversion == 46:
            precision: i64 = 0
            offset = offset + 1
            conversion = load_i8(format, offset)
            if conversion == 42:
                precision = va_arg_i32(cursor)
                if precision < 0:
                    precision: i64 = -1
                offset = offset + 1
                conversion = load_i8(format, offset)
            else:
                while conversion >= 48 and conversion <= 57:
                    precision = precision * 10 + conversion - 48
                    offset = offset + 1
                    conversion = load_i8(format, offset)

        wide: i64 = 0
        if conversion == 108:
            wide: i64 = 1
            offset = offset + 1
            conversion = load_i8(format, offset)
            if conversion == 108:
                wide: i64 = 1
                offset = offset + 1
                conversion = load_i8(format, offset)
        elif conversion == 122:
            wide: i64 = 1
            offset = offset + 1
            conversion = load_i8(format, offset)
        elif conversion == 104:
            offset = offset + 1
            conversion = load_i8(format, offset)
            if conversion == 104:
                offset = offset + 1
                conversion = load_i8(format, offset)

        if conversion == 0:
            break
        if conversion == 115:
            string_value = va_arg_ptr(cursor)
            string_length = _format_cstr_length(string_value, precision)
            padding = width - string_length
            if padding < 0:
                padding: i64 = 0
            if left == 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
            if index < 0:
                return -1
            index = _format_emit_cstr(
                output,
                capacity,
                stream,
                index,
                string_value,
                precision,
            )
            if index >= 0 and left != 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
        elif conversion == 99:
            padding = width - 1
            if padding < 0:
                padding: i64 = 0
            if left == 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
            if index < 0:
                return -1
            index = _format_emit_char(
                output, capacity, stream, index, va_arg_i32(cursor) & 255
            )
            if index >= 0 and left != 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
        elif conversion == 100 or conversion == 105:
            if wide != 0:
                signed_value = va_arg_i64(cursor)
            else:
                signed_value = va_arg_i32(cursor)
            index = _format_emit_integer(
                output,
                capacity,
                stream,
                index,
                signed_value,
                10,
                0,
                1,
                width,
                precision,
                left,
                zero_pad,
                plus,
                space,
                0,
            )
        elif (
            conversion == 117
            or conversion == 120
            or conversion == 88
            or conversion == 111
        ):
            if wide != 0:
                unsigned_value = va_arg_i64(cursor)
            else:
                unsigned_value = va_arg_u32(cursor)
            base: i64 = 10
            uppercase: i64 = 0
            if conversion == 120 or conversion == 88:
                base: i64 = 16
                if conversion == 88:
                    uppercase: i64 = 1
            elif conversion == 111:
                base: i64 = 8
            index = _format_emit_integer(
                output,
                capacity,
                stream,
                index,
                unsigned_value,
                base,
                uppercase,
                0,
                width,
                precision,
                left,
                zero_pad,
                0,
                0,
                alternate,
            )
        elif conversion == 112:
            pointer_value = va_arg_ptr(cursor)
            index = _format_emit_integer(
                output,
                capacity,
                stream,
                index,
                ptr_diff(pointer_value, null()),
                16,
                0,
                0,
                width,
                precision,
                left,
                zero_pad,
                0,
                0,
                2,
            )
        elif (
            conversion == 102
            or conversion == 70
            or conversion == 101
            or conversion == 69
            or conversion == 103
            or conversion == 71
        ):
            raw_float = stack_alloc(512)
            raw_length = _format_float_raw(
                raw_float,
                va_arg_f64(cursor),
                conversion,
                precision,
                alternate,
                plus,
                space,
            )
            padding = width - raw_length
            if padding < 0:
                padding: i64 = 0
            raw_offset: i64 = 0
            if left == 0 and zero_pad == 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
            elif left == 0 and zero_pad != 0:
                first = load_i8(raw_float, 0)
                if first == 45 or first == 43 or first == 32:
                    index = _format_emit_char(
                        output, capacity, stream, index, first
                    )
                    raw_offset: i64 = 1
                if index >= 0:
                    index = _format_emit_repeat(
                        output, capacity, stream, index, 48, padding
                    )
            if index >= 0:
                index = _format_emit_cstr(
                    output,
                    capacity,
                    stream,
                    index,
                    ptr_add(raw_float, raw_offset),
                    -1,
                )
            if index >= 0 and left != 0:
                index = _format_emit_repeat(
                    output, capacity, stream, index, 32, padding
                )
        else:
            index = _format_emit_char(output, capacity, stream, index, 37)
            if index >= 0:
                index = _format_emit_char(
                    output, capacity, stream, index, conversion
                )
        if index < 0:
            return -1
        offset = offset + 1

    if ptr_is_null(stream) and capacity > 0 and not ptr_is_null(output):
        terminator = index
        if terminator >= capacity:
            terminator = capacity - 1
        store_i8(output, terminator, 0)
    return index


@c_abi_variadic_export("snprintf")
def snprintf(output, capacity: i64, format) -> i64:
    cursor = va_start()
    result = _format_core(output, capacity, null(), format, cursor)
    va_end(cursor)
    return result


@c_abi_export("vsnprintf")
def vsnprintf(output, capacity: i64, format, ap) -> i64:
    return _format_core(output, capacity, null(), format, va_cursor(ap))


@c_abi_variadic_export("fprintf")
def fprintf(stream, format) -> i64:
    cursor = va_start()
    result = _format_core(null(), 0, stream, format, cursor)
    va_end(cursor)
    return result


@c_abi_export("popen")
def popen(command, mode) -> c_ptr:
    if ptr_is_null(command) or ptr_is_null(mode):
        return null()
    first = load_i8(mode, 0)
    if first != 114 and first != 119:
        return null()
    mode_offset: i64 = 1
    if load_i8(mode, mode_offset) == 101:
        mode_offset = mode_offset + 1
    if load_i8(mode, mode_offset) != 0:
        return null()

    argv = stack_alloc(32)
    store_ptr(argv, 0, cstr("sh"))
    store_ptr(argv, 8, cstr("-c"))
    store_ptr(argv, 16, command)
    store_ptr(argv, 24, null())
    fd_out = stack_alloc(4)
    envp = initial_environ()
    if ptr_is_null(envp):
        return null()
    parent_reads: i64 = 1 if first == 114 else 0
    pid = spawn_process_pipe(
        cstr("/bin/sh"), argv, envp, parent_reads, fd_out
    )
    if pid <= 0:
        return null()

    flags = abi_constant("stdio.flag.readable")
    if first == 119:
        flags = abi_constant("stdio.flag.writable")
    stream = _stream_new(load_i32(fd_out, 0), flags, pid)
    if ptr_is_null(stream):
        close(load_i32(fd_out, 0))
        status = stack_alloc(4)
        waited = waitpid(pid, status, 0)
        while waited == -4:
            waited = waitpid(pid, status, 0)
        return null()
    return stream


@c_abi_export("pclose")
def pclose(stream) -> i64:
    if ptr_is_null(stream) or load_i64(
        stream, abi_constant("stdio.file.magic_offset")
    ) != abi_constant("stdio.file.magic"):
        return -1
    flags = load_i64(stream, abi_constant("stdio.file.flags_offset"))
    pid = load_i64(stream, abi_constant("stdio.file.aux_offset"))
    if (flags & abi_constant("stdio.flag.standard")) != 0 or pid <= 0:
        return -1
    fd = load_i64(stream, abi_constant("stdio.file.fd_offset"))
    flush_result: i64 = 0
    if (flags & abi_constant("stdio.flag.writable")) != 0:
        flush_result = _flush_output(stream)
    close_result = close(fd)
    _stream_release_buffer(stream)
    store_i64(stream, abi_constant("stdio.file.magic_offset"), 0)
    free(stream)

    status = stack_alloc(4)
    waited = waitpid(pid, status, 0)
    while waited == -4:
        waited = waitpid(pid, status, 0)
    if flush_result != 0 or close_result != 0 or waited != pid:
        return -1
    return load_i32(status, 0)
