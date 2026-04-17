from __future__ import annotations

"""x86_64 Linux global/data emission helpers for the self backend."""

from . import BackendUnavailable
from .self_backend_ir import GlobalDef, TypeDesc, _align_to
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import (
    decode_global_name,
    decode_llvm_c_string,
    parse_constant_gep,
    split_top_level,
    strip_typed_initializer,
)


def asm_symbol(name: str, module_symbols: PreparedModuleSymbols) -> str:
    if name in module_symbols.internal_symbols:
        return f"{module_symbols.internal_prefix}{name}"
    return name


def emit_scalar_initializer(
    ty: TypeDesc,
    init: str,
    global_name: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    if init == "null":
        init = "0"
    if ty.is_fp:
        if ty.width <= 32:
            return [f"  .float {init}"]
        return [f"  .double {init}"]
    if ty.is_ptr:
        if init.startswith("gep0:"):
            return [f"  .quad {asm_symbol(init.split(':', 1)[1], module_symbols)}"]
        if init.startswith("gepconst:"):
            base, offset_text = init.split(":", 2)[1:]
            offset = int(offset_text)
            suffix = "" if offset == 0 else f"+{offset}"
            return [f"  .quad {asm_symbol(base, module_symbols)}{suffix}"]
        if init.startswith("getelementptr"):
            base, offset = parse_constant_gep(init)
            suffix = "" if offset == 0 else f"+{offset}"
            return [f"  .quad {asm_symbol(base, module_symbols)}{suffix}"]
        if init.startswith("@"):
            return [f"  .quad {asm_symbol(decode_global_name(init), module_symbols)}"]
        return [f"  .quad {int(init)}"]
    if ty.is_int:
        if ty.width <= 8:
            return [f"  .byte {int(init)}"]
        if ty.width <= 16:
            return [f"  .short {int(init)}"]
        if ty.width <= 32:
            return [f"  .long {int(init)}"]
        return [f"  .quad {int(init)}"]
    raise BackendUnavailable(
        f"self backend does not support scalar global initializer for {global_name!r}: {ty.describe()}"
    )


def emit_zero_fill(size: int) -> list[str]:
    if size <= 0:
        return []
    return [f"  .zero {size}"]


def emit_byte_data(data: bytes) -> list[str]:
    if not data:
        return []
    lines: list[str] = []
    for start in range(0, len(data), 16):
        chunk = ", ".join(str(byte) for byte in data[start : start + 16])
        lines.append(f"  .byte {chunk}")
    return lines


def emit_typed_initializer(
    ty: TypeDesc,
    init: str,
    global_name: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    init = init.strip()
    if init == "zeroinitializer":
        return emit_zero_fill(ty.slot_size)
    if ty.is_array:
        assert ty.elem is not None
        if ty.elem.is_int and ty.elem.width == 8 and init.startswith('c"'):
            data = decode_llvm_c_string(init)
            if len(data) != ty.count:
                raise BackendUnavailable(
                    f"x86_64 self backend expected {ty.count} bytes in c-string initializer for {global_name!r}, got {len(data)}"
                )
            return emit_byte_data(data)
        if not (init.startswith("[") and init.endswith("]")):
            raise BackendUnavailable(
                f"x86_64 self backend expected array initializer for {global_name!r}, got {init!r}"
            )
        body = init[1:-1].strip()
        items = [] if not body else split_top_level(body)
        if len(items) != ty.count:
            raise BackendUnavailable(
                f"x86_64 self backend expected {ty.count} array items for {global_name!r}, got {len(items)}"
            )
        lines: list[str] = []
        stride = _align_to(ty.elem.slot_size, ty.elem.align)
        for item in items:
            lines.extend(emit_typed_initializer(ty.elem, strip_typed_initializer(item), global_name, module_symbols))
            lines.extend(emit_zero_fill(stride - ty.elem.slot_size))
        return lines
    if ty.is_struct:
        if not (init.startswith("{") and init.endswith("}")):
            raise BackendUnavailable(
                f"x86_64 self backend expected struct initializer for {global_name!r}, got {init!r}"
            )
        body = init[1:-1].strip()
        items = [] if not body else split_top_level(body)
        if len(items) != len(ty.fields):
            raise BackendUnavailable(
                f"x86_64 self backend expected {len(ty.fields)} struct fields for {global_name!r}, got {len(items)}"
            )
        lines: list[str] = []
        offset = 0
        for index, (field_ty, item) in enumerate(zip(ty.fields, items)):
            field_offset = ty.field_offset(index)
            lines.extend(emit_zero_fill(field_offset - offset))
            lines.extend(emit_typed_initializer(field_ty, strip_typed_initializer(item), global_name, module_symbols))
            offset = field_offset + field_ty.slot_size
        lines.extend(emit_zero_fill(ty.slot_size - offset))
        return lines
    return emit_scalar_initializer(ty, init, global_name, module_symbols)


def emit_global_initializer(global_: GlobalDef, module_symbols: PreparedModuleSymbols) -> str:
    return "\n".join(
        emit_typed_initializer(global_.type, global_.initializer, global_.name, module_symbols)
    )


def emit_globals(globals_: list[GlobalDef], module_symbols: PreparedModuleSymbols) -> list[str]:
    lines: list[str] = []
    for global_ in globals_:
        if not (
            global_.type.is_int
            or global_.type.is_ptr
            or global_.type.is_fp
            or global_.type.is_array
            or global_.type.is_struct
        ):
            raise BackendUnavailable(
                f"x86_64 self backend global type not translated yet for {global_.name!r}: {global_.type.describe()}"
            )
        section = ".section .rodata" if global_.is_constant else ".data"
        lines.append(section)
        lines.append(f".p2align {max(global_.type.align.bit_length() - 1, 0)}")
        symbol = asm_symbol(global_.name, module_symbols)
        if not global_.is_internal:
            lines.append(f".globl {symbol}")
        lines.append(f".type {symbol}, @object")
        lines.append(f".size {symbol}, {global_.type.slot_size}")
        lines.append(f"{symbol}:")
        lines.append(emit_global_initializer(global_, module_symbols))
        lines.append("")
    return lines
