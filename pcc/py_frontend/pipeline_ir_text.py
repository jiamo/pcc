"""Pure LLVM-text structure helpers shared by pipeline splitters."""

from __future__ import annotations


def substring_at(text: str, needle: str, index: int) -> bool:
    if index < 0:
        return False
    if index + len(needle) > len(text):
        return False
    offset = 0
    while offset < len(needle):
        if text[index + offset] != needle[offset]:
            return False
        offset += 1
    return True


def find_substring(text: str, needle: str, start: int) -> int:
    if not needle:
        return start
    index = max(0, start)
    limit = len(text) - len(needle)
    while index <= limit:
        if substring_at(text, needle, index):
            return index
        index += 1
    return -1


def find_last_char(text: str, target: str) -> int:
    index = len(text) - 1
    while index >= 0:
        if text[index] == target:
            return index
        index -= 1
    return -1


def defined_function_name_from_line(line: str) -> str:
    marker = " @"
    position = find_substring(line, marker, 0)
    if position < 0:
        return ""
    start = position + len(marker)
    end = find_substring(line, "(", start)
    if end < 0:
        return ""
    return line[start:end]


def function_declaration_from_define_line(line: str) -> str:
    brace = find_last_char(line, "{")
    if brace < 0:
        return ""
    head = line[:brace].strip()
    if not head.startswith("define "):
        return ""
    declaration = "declare " + head[len("define ") :]
    declaration = declaration.replace("declare internal ", "declare ", 1)
    declaration = declaration.replace("declare private ", "declare ", 1)
    return declaration


def export_split_function_text(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    lines[0] = lines[0].replace("define internal ", "define ", 1)
    lines[0] = lines[0].replace("define private ", "define ", 1)
    return "\n".join(lines)


def export_split_function_declaration(text: str) -> str:
    text = text.replace("declare internal ", "declare ", 1)
    return text.replace("declare private ", "declare ", 1)


def split_export_prefix(module_name: str) -> str:
    out = "__pccsplit_"
    for char in str(module_name):
        if char.isalnum() or char == "_" or char == ".":
            out += char
        else:
            out += "_"
    return out + "_"


def line_has_internal_linkage(line: str) -> bool:
    padded = " " + line + " "
    return " internal " in padded or " private " in padded


def global_name_from_definition_line(line: str) -> str:
    if not line.startswith("@"):
        return ""
    separator = find_substring(line, " = ", 0)
    if separator < 0:
        return ""
    return line[1:separator]


def private_symbol_rename_map(
    global_lines: list[str],
    functions: list[tuple[str, str, str, bool]],
    export_prefix: str,
) -> dict[str, str]:
    rename_map: dict[str, str] = {}
    for line in global_lines:
        if line_has_internal_linkage(line):
            name = global_name_from_definition_line(line)
            if name:
                rename_map[name] = export_prefix + name
    for name, _body, _declaration, is_internal in functions:
        if is_internal and name:
            rename_map[name] = export_prefix + name
    return rename_map


def rename_symbol_name(name: str, rename_map: dict[str, str]) -> str:
    replacement = rename_map.get(name)
    return name if replacement is None else replacement


def llvm_global_name_char(char: str) -> bool:
    return char.isalnum() or char in ("_", ".", "$", "-")


def rename_llvm_global_refs(text: str, rename_map: dict[str, str]) -> str:
    if not rename_map:
        return text
    pieces: list[str] = []
    index = 0
    literal_start = 0
    in_quote = False
    escape = False
    while index < len(text):
        char = text[index]
        if in_quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_quote = False
            index += 1
            continue
        if char == '"':
            in_quote = True
            index += 1
            continue
        if char != "@":
            index += 1
            continue
        start = index + 1
        end = start
        while end < len(text) and llvm_global_name_char(text[end]):
            end += 1
        name = text[start:end]
        replacement = rename_map.get(name)
        if replacement is not None:
            if literal_start < index:
                pieces.append(text[literal_start:index])
            pieces.append("@" + replacement)
            literal_start = end
        index = end
    if not pieces:
        return text
    if literal_start < len(text):
        pieces.append(text[literal_start:])
    return "".join(pieces)


def find_global_kind_pos(rest: str) -> int:
    best = -1
    if rest.startswith("global ") or rest.startswith("constant "):
        best = 0
    index = 0
    while index < len(rest):
        if substring_at(rest, " global ", index):
            candidate = index + 1
            if best < 0 or candidate < best:
                best = candidate
        if substring_at(rest, " constant ", index):
            candidate = index + 1
            if best < 0 or candidate < best:
                best = candidate
        index += 1
    return best


def global_initializer_type_text(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    first = text[0]
    if first not in "{[<":
        end = 0
        while end < len(text) and not text[end].isspace():
            end += 1
        return text[:end]
    matching = {"{": "}", "[": "]", "<": ">"}[first]
    depth = 0
    for index, char in enumerate(text):
        if char == first:
            depth += 1
        elif char == matching:
            depth -= 1
            if depth == 0:
                return text[: index + 1]
    return ""


def global_declaration_from_definition_line(line: str) -> str:
    separator = find_substring(line, " = ", 0)
    if separator < 0:
        return ""
    name = line[:separator]
    rest = line[separator + 3 :]
    kind_position = find_global_kind_pos(rest)
    if kind_position < 0:
        return ""
    kind_end = (
        kind_position + 8
        if substring_at(rest, "constant", kind_position)
        else kind_position + 6
    )
    kind = rest[kind_position:kind_end].strip()
    type_text = global_initializer_type_text(rest[kind_end:].strip())
    if not type_text:
        return ""
    return name + " = external " + kind + " " + type_text
