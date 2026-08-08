"""LLVM text sharding for pass workers and self-backend object workers."""

from __future__ import annotations

from .pipeline_ir_text import (
    defined_function_name_from_line,
    export_split_function_declaration,
    export_split_function_text,
    function_declaration_from_define_line,
    global_declaration_from_definition_line,
    line_has_internal_linkage,
    private_symbol_rename_map,
    rename_llvm_global_refs,
    rename_symbol_name,
)


def ir_global_definition_line(line: str) -> bool:
    if not line.startswith("@"):
        return False
    if " = " not in line:
        return False
    if " external " in (" " + line + " "):
        return False
    padded = " " + line + " "
    return " global " in padded or " constant " in padded


def export_split_global_line(line: str) -> str:
    line = line.replace(" = internal ", " = ", 1)
    return line.replace(" = private ", " = ", 1)


def self_backend_local_frame_map_line(line: str) -> bool:
    """Return whether *line* is an immutable precise-root descriptor.

    Self-backend object shards are emitted independently, and precise
    stack-map planning needs the descriptor initializer before the objects are
    linked.  These tiny internal constants are therefore copied into every
    function shard instead of being exported through the ordinary global-only
    shard.  Mutable globals must continue to use the single-definition path.
    """
    if not line_has_internal_linkage(line):
        return False
    padded = " " + line + " "
    if " constant i32 " not in padded:
        return False
    return (
        ".pcc.gc.frame.map." in line
        or ".pcc.vthread.frame.map." in line
    )


def split_python_ir_module_for_pass_shards(
    ir_text: str,
    *,
    export_prefix: str,
    shard_bytes: int,
) -> list[str]:
    lines = str(ir_text).splitlines()
    shared_lines: list[str] = []
    global_defs_raw: list[str] = []
    functions_raw: list[tuple[str, str, str, bool]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("define "):
            body_lines = [line]
            index += 1
            while index < len(lines):
                body_lines.append(lines[index])
                if lines[index].startswith("}"):
                    index += 1
                    break
                index += 1
            body = "\n".join(body_lines)
            name = defined_function_name_from_line(line)
            functions_raw.append(
                (
                    name,
                    body,
                    function_declaration_from_define_line(line),
                    line_has_internal_linkage(line),
                )
            )
            continue
        if ir_global_definition_line(line):
            global_defs_raw.append(line)
        else:
            shared_lines.append(line)
        index += 1

    if len(functions_raw) <= 1:
        return [ir_text]

    rename_map = private_symbol_rename_map(
        global_defs_raw,
        functions_raw,
        export_prefix,
    )
    shared = rename_llvm_global_refs(
        "\n".join(shared_lines), rename_map
    ).strip()
    global_defs = [
        export_split_global_line(rename_llvm_global_refs(line, rename_map))
        for line in global_defs_raw
    ]
    global_declarations = []
    for line in global_defs_raw:
        declaration = global_declaration_from_definition_line(
            rename_llvm_global_refs(line, rename_map)
        )
        if declaration:
            global_declarations.append(declaration)
    functions = []
    for name, body, declaration, _is_internal in functions_raw:
        functions.append(
            (
                rename_symbol_name(name, rename_map),
                export_split_function_text(
                    rename_llvm_global_refs(body, rename_map)
                ),
                export_split_function_declaration(
                    rename_llvm_global_refs(declaration, rename_map)
                ),
            )
        )
    all_function_declarations: list[tuple[str, str]] = []
    for name, _body, declaration in functions:
        if declaration:
            all_function_declarations.append((name, declaration))

    def make_shard(
        body_parts: list[tuple[str, str, str]],
        *,
        include_global_defs: bool,
    ) -> str:
        body_names = set()
        for name, _body, _declaration in body_parts:
            body_names.add(name)
        pieces: list[str] = []
        if shared:
            pieces.append(shared)
        if include_global_defs:
            pieces.extend(global_defs)
        else:
            pieces.extend(global_declarations)
        for name, declaration in all_function_declarations:
            if name not in body_names:
                pieces.append(declaration)
        for _name, body, _declaration in body_parts:
            pieces.append(body)
        non_empty_pieces: list[str] = []
        for piece in pieces:
            if piece:
                non_empty_pieces.append(piece)
        return "\n\n".join(non_empty_pieces).strip() + "\n"

    shards: list[str] = []
    if global_defs:
        shards.append(make_shard([], include_global_defs=True))
    current: list[tuple[str, str, str]] = []
    current_bytes = 0
    for function in functions:
        function_bytes = len(function[1])
        if current and current_bytes + function_bytes > shard_bytes:
            shards.append(make_shard(current, include_global_defs=False))
            current = []
            current_bytes = 0
        current.append(function)
        current_bytes += function_bytes
    if current:
        shards.append(make_shard(current, include_global_defs=False))
    if len(shards) <= 1:
        return [ir_text]
    return shards


def split_self_backend_ir_module_for_object_shards(
    ir_text: str,
    *,
    export_prefix: str,
    shard_bytes: int,
) -> list[str]:
    lines = str(ir_text).splitlines()
    shared_lines: list[str] = []
    global_lines_raw: list[str] = []
    functions_raw: list[tuple[str, str, str, bool]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("define "):
            body_lines = [line]
            index += 1
            while index < len(lines):
                body_lines.append(lines[index])
                if lines[index].startswith("}"):
                    index += 1
                    break
                index += 1
            body = "\n".join(body_lines)
            functions_raw.append(
                (
                    defined_function_name_from_line(line),
                    body,
                    "",
                    line_has_internal_linkage(line),
                )
            )
            continue
        if ir_global_definition_line(line):
            global_lines_raw.append(line)
        else:
            shared_lines.append(line)
        index += 1

    if len(functions_raw) <= 1:
        return [ir_text]
    local_frame_map_lines = []
    exported_global_lines_raw = []
    for line in global_lines_raw:
        if self_backend_local_frame_map_line(line):
            local_frame_map_lines.append(line)
        else:
            exported_global_lines_raw.append(line)
    rename_map = private_symbol_rename_map(
        exported_global_lines_raw,
        functions_raw,
        export_prefix,
    )
    shared = rename_llvm_global_refs(
        "\n".join(shared_lines), rename_map
    ).strip()
    global_lines = []
    for line in exported_global_lines_raw:
        global_lines.append(
            export_split_global_line(rename_llvm_global_refs(line, rename_map))
        )
    functions = []
    for _name, body, _declaration, _is_internal in functions_raw:
        functions.append(
            export_split_function_text(rename_llvm_global_refs(body, rename_map))
        )

    def make_shard(body_parts: list[str]) -> str:
        pieces = []
        if shared:
            pieces.append(shared)
        if body_parts:
            pieces.extend(local_frame_map_lines)
        for part in body_parts:
            if part:
                pieces.append(part)
        return "\n\n".join(pieces).strip() + "\n"

    shards: list[str] = []
    if global_lines:
        shards.append(make_shard(global_lines))
    current: list[str] = []
    current_bytes = 0
    for function_text in functions:
        function_bytes = len(function_text)
        if current and current_bytes + function_bytes > shard_bytes:
            shards.append(make_shard(current))
            current = []
            current_bytes = 0
        current.append(function_text)
        current_bytes += function_bytes
    if current:
        shards.append(make_shard(current))
    if len(shards) <= 1:
        return [ir_text]
    return shards
