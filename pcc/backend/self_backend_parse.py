from __future__ import annotations

"""Target-neutral LLVM IR parser/decoder layer for the self backend.

This module intentionally owns the textual LLVM-IR-facing logic shared by
current and future self-backend targets. The parsed result should be reusable
by AArch64 Darwin today and x86_64 Linux later without reimplementing the LLVM
IR text handling per target.
"""

import re
import struct

from . import BackendUnavailable
from .self_backend_ir import (
    ArgInfo,
    GlobalDef,
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    ParsedModule,
    PhiIncoming,
    PhiInstr,
    TypeDesc,
    _align_to,
    aggregate_member_info,
)


_SCALAR_TYPE_TOKEN = (
    r'(?:void|ptr|float|double|i\d+|%(?:"[^"]+"|(?:[A-Za-z_.$][\w.$-]*|\d+)))'
)
_AGG_ELEM_TYPE_TOKEN = rf"(?:{_SCALAR_TYPE_TOKEN})(?:\*+)?"
_STRUCT_LITERAL_TYPE_TOKEN = r"\{[^{}]+\}"
_BASE_TYPE_TOKEN = (
    rf"(?:{_SCALAR_TYPE_TOKEN}|"
    rf"{_STRUCT_LITERAL_TYPE_TOKEN}|"
    rf"\[\d+ x {_AGG_ELEM_TYPE_TOKEN}\]|"
    rf"<\d+ x {_AGG_ELEM_TYPE_TOKEN}>)"
)
_TYPE_TOKEN = rf"{_BASE_TYPE_TOKEN}(?:\*+)?"
_VALUE_REF_TOKEN = r'(?:%(?:"[^"]+"|(?:[A-Za-z_.$][\w.$-]*|\d+))|@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))'
_LABEL_REF_TOKEN = r'%(?:"[^"]+"|(?:[A-Za-z_.$][\w.$-]*|\d+))'
_TARGET_TRIPLE_RE = re.compile(r'^target triple = "([^"]+)"$', re.MULTILINE)
_NAMED_TYPEDEF_RE = re.compile(
    r'^(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = type \{(?P<fields>[^}]*)\}$',
    re.MULTILINE,
)
_GLOBAL_PTR_GEP_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf'(?P<linkage>(?:internal|private)\s+)?global (?P<type>{_TYPE_TOKEN}) '
    r'getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<base_type>.+?),\s+(?P<ptr_type>.+?)\s+'
    r'(?P<base>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)),\s+i64\s+0,\s+i64\s+0\)$',
    re.MULTILINE,
)
_GEP_INIT_RE = re.compile(
    r'^getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<base_type>.+?),\s+(?P<ptr_type>.+?)\s+'
    r'(?P<base>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)),\s+i64\s+0,\s+i64\s+0\)$'
)
_CONST_GEP_RE = re.compile(r"^getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<body>.*)\)$")
_GLOBAL_HEADER_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf'(?P<prefix>.*?)(?P<kind>global|constant)\s+(?P<body>.+)$'
)
_SSA_NAME_RE = re.compile(r'^%(?:"([^"]+)"|((?:[A-Za-z_.$][\w.$-]*|\d+)))$')
_GLOBAL_NAME_RE = re.compile(r'^@(?:"([^"]+)"|([A-Za-z_.$][\w.$-]*))$')
_FUNCTION_NAME_RE = re.compile(r'(@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))\(')
_LABEL_REF_RE = re.compile(r'^(?:label\s+)?%(?:"([^"]+)"|((?:[A-Za-z_.$][\w.$-]*|\d+)))$')
_PLAIN_LABEL_RE = re.compile(r'^([A-Za-z_.$][\w.$-]*|\.[0-9A-Za-z_.$-]+|\d+):(?:\s*;.*)?$')
_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_.$][A-Za-z0-9_.$]*$")
_INT_RE = re.compile(r"^-?\d+$")
_HEX_RE = re.compile(r"^0x[0-9A-Fa-f]+$")
_FLOAT_RE = re.compile(r"^-?(?:(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)$")
_ARG_RE = re.compile(
    rf'^(?P<type>{_TYPE_TOKEN})(?:\s+[A-Za-z_][A-Za-z0-9_.$-]*)*\s+'
    r'(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))$'
)
_ALLOCA_RE = re.compile(rf"^(?P<dest>%.*)\s*=\s*alloca\s+(?P<type>{_TYPE_TOKEN})(?:\s*,.*)?$")
_STORE_RE = re.compile(
    rf"^store\s+(?P<val_type>{_TYPE_TOKEN})\s+(?P<value>.+?),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$"
)
_LOAD_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*load\s+(?P<val_type>{_TYPE_TOKEN}),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$"
)
_BINOP_RE = re.compile(
    r"^(?P<dest>%.*)\s*=\s*"
    r"(?P<op>add|sub|mul|sdiv|udiv|srem|urem|and|or|xor|shl|lshr|ashr)(?:\s+[A-Za-z_][A-Za-z0-9_()]*)*\s+"
    rf"(?P<type>{_TYPE_TOKEN})\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$"
)
_FBINOP_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*"
    r"(?P<op>fadd|fsub|fmul|fdiv)(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+"
    r"(?P<type>float|double)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$"
)
_FNEG_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*fneg(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+"
    r"(?P<type>float|double)\s+(?P<value>.+)$"
)
_ICMP_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*icmp(?:\s+samesign)?\s+"
    r"(?P<cond>eq|ne|slt|sle|sgt|sge|ult|ule|ugt|uge)\s+"
    rf"(?P<type>{_TYPE_TOKEN})\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$"
)
_FCMP_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*fcmp\s+"
    r"(?P<cond>oeq|one|ogt|oge|olt|ole|ord|ueq|une|ugt|uge|ult|ule|uno)\s+"
    r"(?P<type>float|double)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$"
)
_SWITCH_RE = re.compile(
    rf"^switch\s+(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+?),\s+label\s+"
    rf"(?P<default>{_LABEL_REF_TOKEN})\s+\[(?P<cases>.*)\]$"
)
_SWITCH_CASE_RE = re.compile(
    rf"(?P<type>{_TYPE_TOKEN})\s+(?P<value>-?\d+),\s+label\s+"
    rf"(?P<label>{_LABEL_REF_TOKEN})"
)
_CAST_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*"
    r"(?P<op>zext|sext|trunc|bitcast|ptrtoint|inttoptr|sitofp|uitofp|fptosi|fptoui|fpext|fptrunc)(?:\s+[A-Za-z_][A-Za-z0-9_()]*)*\s+"
    rf"(?P<src_type>{_TYPE_TOKEN})\s+(?P<value>.+?)\s+to\s+(?P<dst_type>{_TYPE_TOKEN})$"
)
_SELECT_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*select(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+i1\s+"
    rf"(?P<cond>.+?),\s+(?P<true_type>{_TYPE_TOKEN})\s+(?P<true_value>.+?),\s+"
    rf"(?P<false_type>{_TYPE_TOKEN})\s+(?P<false_value>.+)$"
)
_EXTRACTVALUE_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*extractvalue\s+(?P<agg_type>{_TYPE_TOKEN})\s+"
    r"(?P<value>.+?)(?P<indices>(?:,\s+\d+)+)$"
)
_INSERTVALUE_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*insertvalue\s+(?P<agg_type>{_TYPE_TOKEN})\s+"
    rf"(?P<agg_value>.+?),\s+(?P<elem_type>{_TYPE_TOKEN})\s+"
    r"(?P<elem_value>.+?)(?P<indices>(?:,\s+\d+)+)$"
)
_EXTRACTELEMENT_RE = re.compile(r"^(?P<dest>%.*)\s*=\s*extractelement\s+(?P<body>.+)$")
_FREEZE_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*freeze\s+(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+)$"
)
_INSERTELEMENT_RE = re.compile(r"^(?P<dest>%.*)\s*=\s*insertelement\s+(?P<body>.+)$")
_SHUFFLEVECTOR_RE = re.compile(r"^(?P<dest>%.*)\s*=\s*shufflevector\s+(?P<body>.+)$")
_CALL_RE = re.compile(
    rf"^(?:(?P<dest>%.*)\s*=\s*)?(?:tail\s+)?call(?:\s+[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?)*?\s+"
    rf"(?P<ret>{_TYPE_TOKEN})(?P<sig>\s*\([^)]*\))?\s+"
    rf"(?P<callee>{_VALUE_REF_TOKEN})\((?P<args>.*)\)(?:\s+#\d+)?(?:,\s*!.*)?$"
)
_VAARG_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*va_arg\s+(?P<ap_type>{_TYPE_TOKEN})\s+(?P<ap>.+?),\s+(?P<value_type>{_TYPE_TOKEN})$"
)
_GEP_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+(?P<base_type>.+?),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?P<indices>(?:,\s+i\d+\s+.+)+)$"
)
_PHI_RE = re.compile(rf"^(?P<dest>%.*)\s*=\s*phi\s+(?P<type>{_TYPE_TOKEN})\s+(?P<incoming>.+)$")
_BR_COND_RE = re.compile(
    r"^br\s+i1\s+(?P<cond>(?:%.*?|[01])),\s+label\s+(?P<true>.*?),\s+label\s+(?P<false>.+)$"
)
_BR_RE = re.compile(r"^br\s+label\s+(?P<target>.+)$")
_RET_VOID_RE = re.compile(r"^ret\s+void$")
_RET_RE = re.compile(rf"^ret\s+(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+)$")
_UNREACHABLE_RE = re.compile(r"^unreachable$")
_INDEX_RE = re.compile(r"^(?P<type>i\d+)\s+(?P<value>.+)$")
_TYPED_INIT_RE = re.compile(rf"^(?P<type>{_TYPE_TOKEN})\s+(?P<init>.+)$")


_NAMED_TYPES: dict[str, TypeDesc] = {}


def parse_self_backend_target_triple(ir_text: str) -> str:
    match = _TARGET_TRIPLE_RE.search(ir_text)
    if match is None:
        raise BackendUnavailable("self backend requires a target triple in LLVM IR text")
    return match.group(1)


def parse_self_backend_module(ir_text: str) -> ParsedModule:
    _parse_named_types(ir_text)
    return ParsedModule(
        triple=parse_self_backend_target_triple(ir_text),
        globals_=tuple(_parse_globals(ir_text)),
        functions=tuple(_parse_functions(ir_text)),
    )


def check_simple_symbol_name(name: str) -> None:
    if not _SYMBOL_NAME_RE.match(name):
        raise BackendUnavailable(
            f"self backend MVP only supports simple C identifier symbols, got {name!r}"
        )


def split_top_level(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    angle_depth = 0
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                i += 1
                current.append(text[i])
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
        elif ch == "{":
            brace_depth += 1
            current.append(ch)
        elif ch == "}":
            brace_depth -= 1
            current.append(ch)
        elif ch == "[":
            bracket_depth += 1
            current.append(ch)
        elif ch == "]":
            bracket_depth -= 1
            current.append(ch)
        elif ch == "(":
            paren_depth += 1
            current.append(ch)
        elif ch == ")":
            paren_depth -= 1
            current.append(ch)
        elif ch == "<":
            angle_depth += 1
            current.append(ch)
        elif ch == ">":
            angle_depth -= 1
            current.append(ch)
        elif ch == "," and brace_depth == 0 and bracket_depth == 0 and paren_depth == 0 and angle_depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def strip_typed_initializer(item: str) -> str:
    text = item.strip()
    if not text or text == "zeroinitializer":
        return text
    if text.startswith("["):
        depth = 0
        for index, ch in enumerate(text):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    rest = text[index + 1 :].strip()
                    if rest:
                        return rest
                    break
    match = _TYPED_INIT_RE.match(text)
    if match is not None:
        return match.group("init").strip()
    if text.startswith(('c"', "{", "[", "@", "null", "gep0:")):
        return text
    if _INT_RE.match(text) or _HEX_RE.match(text):
        return text
    return text


def decode_llvm_c_string(token: str) -> bytes:
    if not (token.startswith('c"') and token.endswith('"')):
        raise BackendUnavailable(f"self backend expected LLVM c-string initializer, got {token!r}")
    body = token[2:-1]
    data = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                raise BackendUnavailable(f"self backend saw truncated LLVM string escape in {token!r}")
            if i + 2 < len(body) and re.fullmatch(r"[0-9A-Fa-f]{2}", body[i + 1 : i + 3]):
                data.append(int(body[i + 1 : i + 3], 16))
                i += 3
                continue
            data.append(ord(body[i + 1]))
            i += 2
            continue
        data.append(ord(ch))
        i += 1
    return bytes(data)


def decode_value_token(token: str) -> str:
    token = token.strip()
    typed_token = _decode_parenthesized_typed_value(token)
    if typed_token is not None:
        return typed_token
    cast_token = _decode_parenthesized_constant_cast(token)
    if cast_token is not None:
        return cast_token
    expr_token = _decode_parenthesized_constant_expr(token)
    if expr_token is not None:
        return expr_token
    while token:
        if token.startswith("align "):
            pieces = token.split(None, 2)
            if len(pieces) >= 3:
                token = pieces[2].strip()
                continue
        if token.startswith(("null", "zeroinitializer", "getelementptr", "c\"", "{", "[", "<")):
            break
        if token.startswith("%") or token.startswith("@"):
            break
        if _INT_RE.match(token) or _HEX_RE.match(token) or _FLOAT_RE.match(token):
            break
        if token[0].isalpha():
            depth = 0
            attr_end = None
            for index, ch in enumerate(token):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch.isspace() and depth == 0:
                    attr_end = index
                    break
            if attr_end is not None:
                    token = token[attr_end + 1 :].strip()
                    continue
        break
    typed_token = _decode_parenthesized_typed_value(token)
    if typed_token is not None:
        return typed_token
    cast_token = _decode_parenthesized_constant_cast(token)
    if cast_token is not None:
        return cast_token
    expr_token = _decode_parenthesized_constant_expr(token)
    if expr_token is not None:
        return expr_token
    if token == "null":
        return token
    if token in {"poison", "undef"}:
        return token
    if token in {"false", "true"}:
        return token
    if token == "zeroinitializer":
        return token
    if token.startswith("{") or token.startswith("[") or token.startswith("<"):
        return token
    if token.startswith("getelementptr"):
        base, offset = parse_constant_gep(token)
        return f"gepconst:{base}:{offset}"
    if gep := _GEP_INIT_RE.match(token):
        return f"gep0:{decode_global_name(gep.group('base'))}"
    if _INT_RE.match(token):
        return token
    if _HEX_RE.match(token):
        return token
    if _FLOAT_RE.match(token):
        return token
    if token.startswith("%"):
        return decode_ssa_name(token)
    if token.startswith("@"):
        return "@" + decode_global_name(token)
    raise BackendUnavailable(f"unsupported value syntax for self backend: {token!r}")


def parse_ir_type(text: str) -> TypeDesc:
    return _parse_type(text)


def extract_leading_type_token(text: str) -> tuple[str, str]:
    return _extract_leading_type_token(text)


def _split_top_level_keyword(text: str, keyword: str) -> tuple[str, str]:
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    depth_angle = 0
    in_quote = False
    escape = False
    index = 0
    while index <= len(text) - len(keyword):
        ch = text[index]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            index += 1
            continue
        if ch == '"':
            in_quote = True
            index += 1
            continue
        if ch == "[":
            depth_square += 1
            index += 1
            continue
        if ch == "]":
            depth_square -= 1
            index += 1
            continue
        if ch == "{":
            depth_brace += 1
            index += 1
            continue
        if ch == "}":
            depth_brace -= 1
            index += 1
            continue
        if ch == "(":
            depth_paren += 1
            index += 1
            continue
        if ch == ")":
            depth_paren -= 1
            index += 1
            continue
        if ch == "<":
            depth_angle += 1
            index += 1
            continue
        if ch == ">":
            depth_angle -= 1
            index += 1
            continue
        if (
            text.startswith(keyword, index)
            and depth_square == 0
            and depth_brace == 0
            and depth_paren == 0
            and depth_angle == 0
        ):
            return text[:index].strip(), text[index + len(keyword) :].strip()
        index += 1
    raise BackendUnavailable(f"self backend could not split {text!r} on top-level {keyword!r}")


def split_top_level_keyword(text: str, keyword: str) -> tuple[str, str]:
    return _split_top_level_keyword(text, keyword)


def _decode_parenthesized_typed_value(token: str) -> str | None:
    text = token.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    body = text[1:-1].strip()
    try:
        type_text, value_text = _extract_leading_type_token(body)
        _parse_type(type_text)
    except BackendUnavailable:
        return None
    if not value_text:
        return None
    return decode_value_token(value_text)


def _decode_parenthesized_constant_cast(token: str) -> str | None:
    text = token.strip()
    original_text = text
    op: str | None = None
    for candidate in ("inttoptr", "ptrtoint", "trunc", "zext", "sext"):
        if text.startswith(candidate):
            op = candidate
            text = text[len(candidate) :].strip()
            break
    if op is None:
        return None
    if not (text.startswith("(") and text.endswith(")")):
        return None
    body = text[1:-1].strip()
    try:
        _src_type_text, remainder = _extract_leading_type_token(body)
    except BackendUnavailable:
        return None
    if " to " not in remainder:
        return decode_value_token(remainder.strip())
    try:
        value_text, dst_type_text = _split_top_level_keyword(remainder, " to ")
    except BackendUnavailable:
        return None
    try:
        dst_type = _parse_type(dst_type_text.strip())
    except BackendUnavailable:
        return None
    decoded_value = decode_value_token(value_text.strip())
    if op == "ptrtoint":
        if not dst_type.is_int:
            return None
        return f"ptrtointconst:{decoded_value}"
    if op in {"trunc", "zext", "sext"}:
        if not dst_type.is_int:
            return None
        return f"cexpr:{original_text}"
    if not dst_type.is_ptr:
        return None
    return f"inttoptrconst:{decoded_value}"


def _decode_parenthesized_constant_expr(token: str) -> str | None:
    text = token.strip()
    op: str | None = None
    pieces = text.split(None, 1)
    if len(pieces) == 2 and pieces[0] in {"add", "sub", "mul", "and", "or", "xor", "shl", "lshr", "ashr"}:
        op = pieces[0]
        text = pieces[1].strip()
    if op is None:
        return None
    while text and not text.startswith("("):
        pieces = text.split(None, 1)
        if len(pieces) != 2:
            return None
        text = pieces[1].strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    body = text[1:-1].strip()
    parts = split_top_level(body)
    if len(parts) != 2:
        return None
    try:
        _lhs_type_text, lhs_value_text = _extract_leading_type_token(parts[0])
        _rhs_type_text, rhs_value_text = _extract_leading_type_token(parts[1])
    except BackendUnavailable:
        return None
    lhs_value = decode_value_token(lhs_value_text.strip())
    rhs_value = decode_value_token(rhs_value_text.strip())
    if op == "sub" and lhs_value == "0":
        return f"negconst:{rhs_value}"
    if op == "add":
        if const_int_from_value(rhs_value) is not None:
            return f"addconst:{lhs_value}:{rhs_value}"
        if const_int_from_value(lhs_value) is not None:
            return f"addconst:{rhs_value}:{lhs_value}"
    return f"cexpr:{token.strip()}"


def decode_ssa_name(token: str) -> str:
    match = _SSA_NAME_RE.match(token.strip())
    if match is None:
        raise BackendUnavailable(f"unsupported SSA value syntax for self backend: {token!r}")
    name = match.group(1) or match.group(2)
    if name.isdigit():
        return f"%{name}"
    return name


def decode_global_name(token: str) -> str:
    match = _GLOBAL_NAME_RE.match(token.strip())
    if match is None:
        raise BackendUnavailable(f"unsupported global symbol syntax for self backend: {token!r}")
    return match.group(1) or match.group(2)


def decode_label_ref(token: str) -> str:
    token = token.strip()
    if "," in token:
        token = token.split(",", 1)[0].strip()
    if token.endswith(":") and _PLAIN_LABEL_RE.match(token):
        return token[:-1]
    match = _LABEL_REF_RE.match(token)
    if match is None:
        raise BackendUnavailable(f"unsupported label syntax for self backend: {token!r}")
    return match.group(1) or match.group(2)


def const_int_from_value(value: str) -> int | None:
    if value == "false":
        return 0
    if value == "true":
        return 1
    if _INT_RE.match(value):
        return int(value)
    return None


def is_hex_literal(value: str) -> bool:
    return _HEX_RE.match(value) is not None


def is_float_literal(value: str) -> bool:
    return _FLOAT_RE.match(value) is not None and not value.startswith(".")


def is_aggregate_literal_value(value: str) -> bool:
    return value.startswith("{") or value.startswith("[") or value.startswith("<")


def _write_bytes(dst: bytearray, offset: int, src: bytes) -> None:
    i = 0
    while i < len(src):
        dst[offset + i] = src[i]
        i += 1


def aggregate_literal_to_bytes(value_type: TypeDesc, value: str) -> bytes:
    text = value.strip()
    if value_type.is_array:
        if text in {"zeroinitializer", "poison", "undef"}:
            return bytes(value_type.slot_size)
        if text.startswith('c"') and text.endswith('"'):
            if value_type.elem is None or not value_type.elem.is_int or value_type.elem.width != 8:
                raise BackendUnavailable(
                    f"self backend c-string aggregate literal expected i8 array for {value_type.describe()}, got {value!r}"
                )
            data = decode_llvm_c_string(text)
            if len(data) > value_type.count:
                raise BackendUnavailable(
                    f"self backend c-string aggregate literal too large for {value_type.describe()}: {value!r}"
                )
            return data + bytes(value_type.count - len(data))
        if text.startswith("<") and text.endswith(">"):
            text = "[" + text[1:-1].strip() + "]"
        if not (text.startswith("[") and text.endswith("]")):
            raise BackendUnavailable(
                f"self backend expected array aggregate literal for {value_type.describe()}, got {value!r}"
            )
        assert value_type.elem is not None
        items = split_top_level(text[1:-1].strip())
        if len(items) != value_type.count:
            raise BackendUnavailable(
                f"self backend aggregate literal element count mismatch for {value_type.describe()}: {value!r}"
            )
        data = bytearray(value_type.slot_size)
        stride = _align_to(value_type.elem.slot_size, value_type.elem.align)
        for index, item in enumerate(items):
            item_bytes = aggregate_literal_to_bytes(value_type.elem, strip_typed_initializer(item))
            start = index * stride
            _write_bytes(data, start, item_bytes)
        return bytes(data)
    if value_type.is_struct:
        if text in {"zeroinitializer", "poison", "undef"}:
            return bytes(value_type.slot_size)
        if not (text.startswith("{") and text.endswith("}")):
            raise BackendUnavailable(
                f"self backend expected struct aggregate literal for {value_type.describe()}, got {value!r}"
            )
        items = split_top_level(text[1:-1].strip())
        if len(items) != len(value_type.fields):
            raise BackendUnavailable(
                f"self backend aggregate literal field count mismatch for {value_type.describe()}: {value!r}"
            )
        data = bytearray(value_type.slot_size)
        for index, (field_type, item) in enumerate(zip(value_type.fields, items, strict=False)):
            field_bytes = aggregate_literal_to_bytes(field_type, strip_typed_initializer(item))
            field_offset = value_type.field_offset(index)
            _write_bytes(data, field_offset, field_bytes)
        return bytes(data)
    if value_type.is_ptr:
        if text in {"null", "poison", "undef"}:
            return (0).to_bytes(8, byteorder="little", signed=False)
        if text.startswith("inttoptr"):
            decoded = decode_value_token(text)
            if decoded.startswith("inttoptrconst:"):
                text = decoded.split(":", 1)[1]
        int_value = const_int_from_value(text)
        if int_value is not None:
            return (int_value & ((1 << 64) - 1)).to_bytes(8, byteorder="little", signed=False)
        raise BackendUnavailable(
            f"self backend pointer aggregate literal value not translated yet for {value_type.describe()}: {value!r}"
        )
    if value_type.is_int:
        if text in {"poison", "undef"}:
            return bytes(value_type.slot_size)
        int_value = const_int_from_value(text)
        if int_value is None:
            raise BackendUnavailable(
                f"self backend integer aggregate literal value not translated yet for {value_type.describe()}: {value!r}"
            )
        bits = max(8, value_type.slot_size * 8)
        mask = (1 << bits) - 1
        return (int_value & mask).to_bytes(value_type.slot_size, byteorder="little", signed=False)
    if value_type.is_fp:
        if text in {"poison", "undef"}:
            return bytes(value_type.slot_size)
        if value_type.width <= 32:
            if text.startswith("0x"):
                bits = int(text, 16) & 0xFFFFFFFF
            else:
                bits = struct.unpack("<I", struct.pack("<f", float(text)))[0]
            return bits.to_bytes(4, byteorder="little", signed=False)
        if text.startswith("0x"):
            bits = int(text, 16) & 0xFFFFFFFFFFFFFFFF
        else:
            bits = struct.unpack("<Q", struct.pack("<d", float(text)))[0]
        return bits.to_bytes(8, byteorder="little", signed=False)
    raise BackendUnavailable(
        f"self backend aggregate literal type not translated yet: {value_type.describe()}"
    )


def gep_result_type(base_type: TypeDesc, indices: tuple[tuple[TypeDesc, str], ...]) -> TypeDesc:
    if not indices:
        raise BackendUnavailable("self backend getelementptr requires at least one index")
    current = base_type
    for _index_type, index_value in indices[1:]:
        if current.is_array:
            assert current.elem is not None
            current = current.elem
            continue
        if current.is_struct:
            field_index = const_int_from_value(index_value)
            if field_index is None:
                raise BackendUnavailable(
                    "self backend struct getelementptr currently requires constant field indices"
                )
            current = current.field_type(field_index)
            continue
        raise BackendUnavailable(
            f"self backend cannot index into scalar pointee {current.describe()} with more getelementptr indices"
        )
    return current.ptr()


def parse_constant_gep(text: str) -> tuple[str, int]:
    match = _CONST_GEP_RE.match(text.strip())
    if match is None:
        raise BackendUnavailable(f"self backend could not parse constant getelementptr {text!r}")
    parts = split_top_level(match.group("body"))
    if len(parts) < 3:
        raise BackendUnavailable(f"self backend constant getelementptr is incomplete: {text!r}")
    base_type = _parse_type(parts[0])
    ptr_type_text, base_value = _extract_leading_type_token(parts[1])
    ptr_type = _parse_type(ptr_type_text)
    if not ptr_type.is_ptr or not base_value.startswith("@"):
        raise BackendUnavailable(
            f"self backend constant getelementptr currently requires a global pointer base, got {parts[1]!r}"
        )
    indices: list[tuple[TypeDesc, str]] = []
    for chunk in parts[2:]:
        index_type_text, index_value = _extract_leading_type_token(chunk)
        indices.append((_parse_type(index_type_text), decode_value_token(index_value)))
    return decode_global_name(base_value), _constant_gep_offset(base_type, tuple(indices))


def _arg_list_is_vararg(args_text: str) -> bool:
    pieces = [piece.strip() for piece in (args_text or "").split(",") if piece.strip()]
    return bool(pieces) and pieces[-1] == "..."


def _parse_type(text: str, *, resolve_named=None) -> TypeDesc:
    token = text.strip()
    stars = 0
    while token.endswith("*"):
        stars += 1
        token = token[:-1]
    if token == "void":
        base = TypeDesc("void")
    elif token == "ptr":
        base = TypeDesc("ptr", pointee=TypeDesc("void"))
    elif token == "float":
        base = TypeDesc("fp", 32)
    elif token == "double":
        base = TypeDesc("fp", 64)
    elif token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        count_text, elem_text = inner.split(" x ", 1)
        base = TypeDesc(
            "array",
            count=int(count_text),
            elem=_parse_type(elem_text.strip(), resolve_named=resolve_named),
        )
    elif token.startswith("<") and token.endswith(">") and " x " in token:
        inner = token[1:-1].strip()
        count_text, elem_text = inner.split(" x ", 1)
        if not count_text.isdigit():
            raise BackendUnavailable(f"self backend does not understand LLVM vector type {text!r}")
        base = TypeDesc(
            "array",
            count=int(count_text),
            elem=_parse_type(elem_text.strip(), resolve_named=resolve_named),
        )
    elif token.startswith("{") and token.endswith("}"):
        inner = token[1:-1].strip()
        fields = tuple(
            _parse_type(chunk.strip(), resolve_named=resolve_named)
            for chunk in split_top_level(inner)
            if chunk.strip()
        )
        base = TypeDesc("struct", fields=fields)
    elif token.startswith("%"):
        if token not in _NAMED_TYPES and resolve_named is not None:
            resolve_named(token)
        if token not in _NAMED_TYPES:
            raise BackendUnavailable(f"self backend does not know named LLVM type {text!r}")
        base = _NAMED_TYPES[token]
    elif token.startswith("i") and token[1:].isdigit():
        base = TypeDesc("int", int(token[1:]))
    else:
        raise BackendUnavailable(f"self backend does not understand LLVM type {text!r}")
    for _ in range(stars):
        base = TypeDesc("ptr", pointee=base)
    return base


def _parse_named_types(ir_text: str) -> None:
    _NAMED_TYPES.clear()
    pending: dict[str, str] = {}
    for match in _NAMED_TYPEDEF_RE.finditer(ir_text):
        pending[match.group("name")] = match.group("fields").strip()

    def resolve(name: str) -> TypeDesc:
        existing = _NAMED_TYPES.get(name)
        if existing is not None:
            return existing
        if name not in pending:
            raise BackendUnavailable(f"self backend has no definition for named type {name!r}")
        field_text = pending[name]
        placeholder = TypeDesc("struct", name=name)
        _NAMED_TYPES[name] = placeholder
        fields = tuple(
            _parse_type(chunk.strip(), resolve_named=resolve)
            for chunk in field_text.split(",")
            if chunk.strip()
        )
        resolved = TypeDesc("struct", name=name, fields=fields)
        _NAMED_TYPES[name] = resolved
        return resolved

    for name in list(pending):
        resolve(name)


def _parse_functions(ir_text: str) -> list[ParsedFunction]:
    functions: list[ParsedFunction] = []
    for header_text, body_text in _iter_function_defs(ir_text):
        prefix_text, ret_type_text, name_text, args_text = _parse_function_header(header_text)
        prefix = prefix_text.strip().split()
        name = decode_global_name(name_text)
        check_simple_symbol_name(name)
        ret_type = _parse_type(ret_type_text)
        args = _parse_arg_infos(name, args_text)
        blocks = _parse_blocks(name, body_text)
        functions.append(
            ParsedFunction(
                name=name,
                ret_type=ret_type,
                args=args,
                is_global="internal" not in prefix,
                is_vararg=_arg_list_is_vararg(args_text),
                blocks=blocks,
            )
        )
    return functions


def _iter_function_defs(ir_text: str) -> list[tuple[str, str]]:
    defs: list[tuple[str, str]] = []
    for match in re.finditer(r"^define\s+", ir_text, re.MULTILINE):
        start = match.start()
        open_brace = _find_function_open_brace(ir_text, start)
        close_match = re.search(r"^\}", ir_text[open_brace + 1 :], re.MULTILINE)
        if close_match is None:
            raise BackendUnavailable("self backend saw unterminated function body")
        close_index = open_brace + 1 + close_match.start()
        defs.append((ir_text[start : open_brace + 1], ir_text[open_brace + 1 : close_index]))
    return defs


def _find_function_open_brace(text: str, start: int) -> int:
    name_match = _FUNCTION_NAME_RE.search(text, start)
    if name_match is None:
        raise BackendUnavailable("self backend could not find function name while splitting function body")
    args_close = _find_matching_paren(text, name_match.end() - 1)
    in_quote = False
    escape = False
    for index in range(args_close + 1, len(text)):
        ch = text[index]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == "{":
            return index
    raise BackendUnavailable("self backend could not find function header/body split")


def _find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    in_quote = False
    escape = False
    for index in range(open_index, len(text)):
        ch = text[index]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return index
    raise BackendUnavailable("self backend could not find matching ')' in function header")


def _parse_function_header(header_text: str) -> tuple[str, str, str, str]:
    text = header_text.strip()
    if not text.endswith("{"):
        raise BackendUnavailable("self backend expected function header to end with '{'")
    text = text[:-1].rstrip()
    if not text.startswith("define "):
        raise BackendUnavailable("self backend expected function header to start with 'define'")
    text = text[len("define ") :].strip()
    name_match = _FUNCTION_NAME_RE.search(text)
    if name_match is None:
        raise BackendUnavailable(f"self backend could not decode function name from header: {header_text!r}")
    prefix_and_ret = text[: name_match.start()].rstrip()
    match = re.match(rf"^(?P<prefix>.*?)(?P<ret>{_TYPE_TOKEN})$", prefix_and_ret)
    if match is None:
        raise BackendUnavailable(f"self backend could not decode return type from header: {header_text!r}")
    args_open = name_match.end() - 1
    args_close = _find_matching_paren(text, args_open)
    return (
        match.group("prefix") or "",
        match.group("ret"),
        name_match.group(1),
        text[args_open + 1 : args_close],
    )


def _parse_globals(ir_text: str) -> list[GlobalDef]:
    globals_: list[GlobalDef] = []
    seen: set[str] = set()
    for line in ir_text.splitlines():
        if not line.startswith("@"):
            continue
        match = _GLOBAL_HEADER_RE.match(line)
        if match is None:
            continue
        if "external" in (match.group("prefix") or "").split():
            continue
        name = decode_global_name(match.group("name"))
        if name in seen:
            continue
        check_simple_symbol_name(name)
        type_text, initializer = _split_leading_type_token(match.group("body"))
        if gep := _GLOBAL_PTR_GEP_RE.match(line):
            initializer = f"gep0:{decode_global_name(gep.group('base'))}"
        globals_.append(
            GlobalDef(
                name=name,
                type=_parse_type(type_text),
                initializer=initializer,
                is_constant=match.group("kind") == "constant",
                is_internal=("internal" in match.group("prefix")) or ("private" in match.group("prefix")),
            )
        )
        seen.add(name)
    return globals_


def _split_leading_type_token(text: str) -> tuple[str, str]:
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    in_quote = False
    escape = False

    for index, ch in enumerate(text):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == "[":
            depth_square += 1
            continue
        if ch == "]":
            depth_square -= 1
            continue
        if ch == "{":
            depth_brace += 1
            continue
        if ch == "}":
            depth_brace -= 1
            continue
        if ch == "(":
            depth_paren += 1
            continue
        if ch == ")":
            depth_paren -= 1
            continue
        if ch == " " and depth_square == 0 and depth_brace == 0 and depth_paren == 0:
            type_text = text[:index].strip()
            initializer = text[index + 1 :].strip()
            if type_text and initializer:
                return type_text, initializer
            break

    raise BackendUnavailable(f"self backend could not split global type from initializer: {text!r}")


def _extract_leading_type_token(text: str) -> tuple[str, str]:
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    depth_angle = 0
    in_quote = False
    escape = False
    saw_type_char = False

    for index, ch in enumerate(text):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            saw_type_char = True
            continue
        if ch == "[":
            depth_square += 1
            saw_type_char = True
            continue
        if ch == "]":
            depth_square -= 1
            continue
        if ch == "{":
            depth_brace += 1
            saw_type_char = True
            continue
        if ch == "}":
            depth_brace -= 1
            continue
        if ch == "(":
            depth_paren += 1
            saw_type_char = True
            continue
        if ch == ")":
            depth_paren -= 1
            continue
        if ch == "<":
            depth_angle += 1
            saw_type_char = True
            continue
        if ch == ">":
            depth_angle -= 1
            continue
        if (
            ch in {" ", ","}
            and depth_square == 0
            and depth_brace == 0
            and depth_paren == 0
            and depth_angle == 0
            and saw_type_char
        ):
            type_text = text[:index].strip()
            rest = text[index:].lstrip(" ,")
            if type_text:
                return type_text, rest
            break
        if not ch.isspace():
            saw_type_char = True

    type_text = text.strip()
    if type_text:
        return type_text, ""
    raise BackendUnavailable(f"self backend could not extract leading type token from {text!r}")


def _split_top_level_once(text: str, sep: str) -> tuple[str, str]:
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    depth_angle = 0
    in_quote = False
    escape = False

    for index, ch in enumerate(text):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == "[":
            depth_square += 1
            continue
        if ch == "]":
            depth_square -= 1
            continue
        if ch == "{":
            depth_brace += 1
            continue
        if ch == "}":
            depth_brace -= 1
            continue
        if ch == "(":
            depth_paren += 1
            continue
        if ch == ")":
            depth_paren -= 1
            continue
        if ch == "<":
            depth_angle += 1
            continue
        if ch == ">":
            depth_angle -= 1
            continue
        if ch == sep and depth_square == 0 and depth_brace == 0 and depth_paren == 0 and depth_angle == 0:
            return text[:index].strip(), text[index + 1 :].strip()
    raise BackendUnavailable(f"self backend could not split {text!r} on top-level {sep!r}")


def _parse_phi_incoming_entries(text: str) -> list[str]:
    entries: list[str] = []
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    depth_angle = 0
    in_quote = False
    escape = False
    entry_start: int | None = None

    for index, ch in enumerate(text):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == "[":
            if depth_square == 0 and depth_brace == 0 and depth_paren == 0 and depth_angle == 0:
                entry_start = index + 1
            depth_square += 1
            continue
        if ch == "]":
            depth_square -= 1
            if (
                depth_square == 0
                and depth_brace == 0
                and depth_paren == 0
                and depth_angle == 0
                and entry_start is not None
            ):
                entries.append(text[entry_start:index].strip())
                entry_start = None
            continue
        if ch == "{":
            depth_brace += 1
            continue
        if ch == "}":
            depth_brace -= 1
            continue
        if ch == "(":
            depth_paren += 1
            continue
        if ch == ")":
            depth_paren -= 1
            continue
        if ch == "<":
            depth_angle += 1
            continue
        if ch == ">":
            depth_angle -= 1
            continue

    if entry_start is not None or depth_square != 0 or depth_brace != 0 or depth_paren != 0 or depth_angle != 0:
        raise BackendUnavailable(f"self backend malformed phi incoming list: {text!r}")
    return entries


def _first_top_level_piece(text: str) -> str:
    pieces = split_top_level(text)
    return pieces[0].strip() if pieces else text.strip()


def _parse_arg_infos(function_name: str, args_text: str) -> list[ArgInfo]:
    text = (args_text or "").strip()
    if not text:
        return []

    raw_chunks = [chunk.strip() for chunk in split_top_level(text) if chunk.strip()]
    chunks: list[str] = []
    saw_varargs = False
    for index, chunk in enumerate(raw_chunks):
        if chunk == "...":
            if index != len(raw_chunks) - 1:
                raise BackendUnavailable(
                    f"self backend expects variadic marker at end of arg list in {function_name!r}"
                )
            saw_varargs = True
            continue
        if saw_varargs:
            raise BackendUnavailable(
                f"self backend expects no fixed args after variadic marker in {function_name!r}"
            )
        chunks.append(chunk)

    args: list[ArgInfo] = []
    for chunk in chunks:
        try:
            type_text, remainder = _extract_leading_type_token(chunk)
        except BackendUnavailable as exc:
            raise BackendUnavailable(
                f"self backend could not decode argument in {function_name!r}: {chunk}"
            ) from exc
        name_match = re.search(r'(%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))\s*$', remainder)
        if name_match is None:
            raise BackendUnavailable(
                f"self backend could not decode argument in {function_name!r}: {chunk}"
            )
        args.append(
            ArgInfo(
                name=decode_ssa_name(name_match.group(1)),
                type=_parse_type(type_text),
            )
        )
    return args


def _parse_blocks(function_name: str, body: str) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    current: ParsedBlock | None = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        label_match = _PLAIN_LABEL_RE.match(line)
        if label_match is not None:
            current = ParsedBlock(name=label_match.group(1))
            blocks.append(current)
            continue
        if current is None:
            raise BackendUnavailable(
                f"self backend expected a labeled basic block in {function_name!r}: {line}"
            )
        current.raw_lines.append(line)

    if not blocks:
        raise BackendUnavailable(f"self backend found no basic blocks in {function_name!r}")

    for block in blocks:
        _parse_block(function_name, block)
    return blocks


def _parse_block(function_name: str, block: ParsedBlock) -> None:
    lines = list(block.raw_lines)
    while lines and _PHI_RE.match(lines[0]):
        phi_match = _PHI_RE.match(lines.pop(0))
        assert phi_match is not None
        incoming_entries = []
        for item in _parse_phi_incoming_entries(phi_match.group("incoming")):
            value_text, label_text = _split_top_level_once(item, ",")
            incoming_entries.append(
                PhiIncoming(
                    value=decode_value_token(value_text.strip()),
                    label=decode_label_ref(label_text.strip()),
                )
            )
        block.phis.append(
            PhiInstr(
                dest=decode_ssa_name(phi_match.group("dest")),
                type=_parse_type(phi_match.group("type")),
                incoming=tuple(incoming_entries),
            )
        )

    if not lines:
        raise BackendUnavailable(
            f"self backend block {block.name!r} in {function_name!r} has no terminator"
        )

    if lines[-1] == "]" or lines[-1].startswith("],"):
        switch_start = len(lines) - 2
        while switch_start >= 0 and not lines[switch_start].startswith("switch "):
            switch_start -= 1
        if switch_start < 0:
            raise BackendUnavailable(
                f"self backend could not recover multi-line switch terminator in {function_name!r}/{block.name!r}"
            )
        instruction_lines = lines[:switch_start]
        terminator_line = " ".join(lines[switch_start:])
    else:
        instruction_lines = lines[:-1]
        terminator_line = lines[-1]

    for line in instruction_lines:
        block.instructions.append(_parse_instruction(function_name, block.name, line))
    block.terminator = _parse_terminator(function_name, block.name, terminator_line)


def _parse_binop_instruction(line: str) -> ParsedInstr | None:
    if "=" not in line:
        return None
    dest_text, rest = line.split("=", 1)
    rest = rest.strip()
    pieces = rest.split(None, 1)
    if len(pieces) != 2:
        return None
    op = pieces[0]
    if op not in {"add", "sub", "mul", "sdiv", "udiv", "srem", "urem", "and", "or", "xor", "shl", "lshr", "ashr"}:
        return None
    rest = pieces[1].strip()
    while True:
        type_text, remainder = _extract_leading_type_token(rest)
        try:
            value_type = _parse_type(type_text)
            break
        except BackendUnavailable:
            attr_pieces = rest.split(None, 1)
            if len(attr_pieces) != 2:
                return None
            rest = attr_pieces[1].strip()
    lhs_text, rhs_text = _split_top_level_once(remainder, ",")
    return ParsedInstr(
        "binop",
        (
            op,
            decode_ssa_name(dest_text.strip()),
            value_type,
            decode_value_token(lhs_text),
            decode_value_token(rhs_text),
        ),
    )


def _parse_icmp_instruction(line: str) -> ParsedInstr | None:
    if "=" not in line:
        return None
    dest_text, rest = line.split("=", 1)
    rest = rest.strip()
    if not rest.startswith("icmp "):
        return None
    rest = rest[len("icmp ") :].strip()
    if rest.startswith("samesign "):
        rest = rest[len("samesign ") :].strip()
    pieces = rest.split(None, 1)
    if len(pieces) != 2:
        return None
    cond, rest = pieces
    if cond not in {"eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge"}:
        return None
    type_text, rest = _extract_leading_type_token(rest)
    lhs_text, rhs_text = _split_top_level_once(rest, ",")
    return ParsedInstr(
        "icmp",
        (
            cond,
            decode_ssa_name(dest_text.strip()),
            _parse_type(type_text),
            decode_value_token(lhs_text),
            decode_value_token(rhs_text),
        ),
    )


def _parse_fcmp_instruction(line: str) -> ParsedInstr | None:
    if "=" not in line:
        return None
    dest_text, rest = line.split("=", 1)
    rest = rest.strip()
    if not rest.startswith("fcmp "):
        return None
    rest = rest[len("fcmp ") :].strip()
    pieces = rest.split(None, 1)
    if len(pieces) != 2:
        return None
    cond, rest = pieces
    if cond not in {"oeq", "one", "ogt", "oge", "olt", "ole", "ord", "ueq", "une", "ugt", "uge", "ult", "ule", "uno"}:
        return None
    type_text, rest = _extract_leading_type_token(rest)
    lhs_text, rhs_text = _split_top_level_once(rest, ",")
    return ParsedInstr(
        "fcmp",
        (
            cond,
            decode_ssa_name(dest_text.strip()),
            _parse_type(type_text),
            decode_value_token(lhs_text),
            decode_value_token(rhs_text),
        ),
    )


def _parse_insertvalue_instruction(function_name: str, block_name: str, line: str) -> ParsedInstr | None:
    if "= insertvalue " not in line:
        return None
    dest_text, rest = line.split("= insertvalue ", 1)
    pieces = split_top_level(rest.strip())
    if len(pieces) < 3:
        raise BackendUnavailable(
            f"self backend malformed insertvalue in {function_name!r}/{block_name!r}: {line}"
        )
    aggregate_type_text, aggregate_value_text = _extract_leading_type_token(pieces[0])
    elem_type_text, elem_value_text = _extract_leading_type_token(pieces[1])
    if not aggregate_value_text or not elem_value_text:
        raise BackendUnavailable(
            f"self backend malformed insertvalue operands in {function_name!r}/{block_name!r}: {line}"
        )
    aggregate_type = _parse_type(aggregate_type_text)
    elem_type = _parse_type(elem_type_text)
    indices = tuple(_parse_extractvalue_indices(",".join(pieces[2:])))
    result_type, offset = aggregate_member_info(aggregate_type, indices)
    if result_type.describe() != elem_type.describe():
        raise BackendUnavailable(
            "self backend insertvalue parser expected inserted value type to match aggregate member type in "
            f"{function_name!r}/{block_name!r}: {line}"
        )
    return ParsedInstr(
        "insertvalue",
        (
            decode_ssa_name(dest_text.strip()),
            aggregate_type,
            decode_value_token(aggregate_value_text),
            elem_type,
            decode_value_token(elem_value_text),
            indices,
            offset,
        ),
    )


def _parse_instruction(function_name: str, block_name: str, line: str) -> ParsedInstr:
    if match := _ALLOCA_RE.match(line):
        return ParsedInstr("alloca", (decode_ssa_name(match.group("dest")), _parse_type(match.group("type"))))
    if "= alloca " in line:
        dest_text, rest = line.split("= alloca ", 1)
        type_text, _tail = _extract_leading_type_token(rest)
        return ParsedInstr("alloca", (decode_ssa_name(dest_text.strip()), _parse_type(type_text)))
    if line.startswith("store "):
        rest = line[len("store ") :].strip()
        val_type_text, rest = _extract_leading_type_token(rest)
        value_text, rest = _split_top_level_once(rest, ",")
        ptr_type_text, rest = _extract_leading_type_token(rest)
        ptr_text = _first_top_level_piece(rest)
        return ParsedInstr(
            "store",
            (
                _parse_type(val_type_text),
                decode_value_token(value_text),
                _parse_type(ptr_type_text),
                decode_value_token(ptr_text),
            ),
        )
    if "= load " in line:
        dest_text, rest = line.split("= load ", 1)
        val_type_text, rest = _split_top_level_once(rest.strip(), ",")
        ptr_type_text, rest = _extract_leading_type_token(rest)
        ptr_text = _first_top_level_piece(rest)
        return ParsedInstr(
            "load",
            (
                decode_ssa_name(dest_text.strip()),
                _parse_type(val_type_text),
                _parse_type(ptr_type_text),
                decode_value_token(ptr_text),
            ),
        )
    if parsed := _parse_binop_instruction(line):
        return parsed
    if match := _FBINOP_RE.match(line):
        return ParsedInstr(
            "fbinop",
            (
                match.group("op"),
                decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                decode_value_token(match.group("lhs")),
                decode_value_token(match.group("rhs")),
            ),
        )
    if match := _FNEG_RE.match(line):
        return ParsedInstr(
            "fneg",
            (
                decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                decode_value_token(match.group("value")),
            ),
        )
    if parsed := _parse_icmp_instruction(line):
        return parsed
    if parsed := _parse_fcmp_instruction(line):
        return parsed
    if match := _CAST_RE.match(line):
        return ParsedInstr(
            "cast",
            (
                match.group("op"),
                decode_ssa_name(match.group("dest")),
                _parse_type(match.group("src_type")),
                decode_value_token(match.group("value")),
                _parse_type(match.group("dst_type")),
            ),
        )
    if "= select " in line:
        dest_text, rest = line.split("= select ", 1)
        rest = rest.strip()
        while not rest.startswith("i1 "):
            pieces = rest.split(None, 1)
            if len(pieces) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pieces[0]):
                break
            rest = pieces[1].strip()
        cond_type_text, rest = _extract_leading_type_token(rest)
        cond_value_text, rest = _split_top_level_once(rest, ",")
        true_type_text, rest = _extract_leading_type_token(rest)
        true_value_text, rest = _split_top_level_once(rest, ",")
        false_type_text, false_value_text = _extract_leading_type_token(rest)
        cond_type = _parse_type(cond_type_text)
        true_type = _parse_type(true_type_text)
        false_type = _parse_type(false_type_text)
        if not (
            (cond_type.is_int and cond_type.width == 1)
            or (
                cond_type.is_array
                and cond_type.elem is not None
                and cond_type.elem.is_int
                and cond_type.elem.width == 1
            )
        ):
            raise BackendUnavailable(
                f"self backend select parser expected i1 or vector-i1 condition in {function_name!r}/{block_name!r}: {line}"
            )
        if true_type.describe() != false_type.describe():
            raise BackendUnavailable(
                "self backend select parser expected matching arm types in "
                f"{function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "select",
            (
                decode_ssa_name(dest_text.strip()),
                true_type,
                decode_value_token(cond_value_text),
                decode_value_token(true_value_text),
                decode_value_token(false_value_text),
            ),
        )
    if match := _SELECT_RE.match(line):
        true_type = _parse_type(match.group("true_type"))
        false_type = _parse_type(match.group("false_type"))
        if true_type.describe() != false_type.describe():
            raise BackendUnavailable(
                "self backend select parser expected matching arm types in "
                f"{function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "select",
            (
                decode_ssa_name(match.group("dest")),
                true_type,
                decode_value_token(match.group("cond")),
                decode_value_token(match.group("true_value")),
                decode_value_token(match.group("false_value")),
            ),
        )
    if match := _FREEZE_RE.match(line):
        return ParsedInstr(
            "freeze",
            (
                decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                decode_value_token(match.group("value")),
            ),
        )
    if match := _INSERTELEMENT_RE.match(line):
        pieces = split_top_level(match.group("body").strip())
        if len(pieces) != 3:
            raise BackendUnavailable(
                f"self backend malformed insertelement in {function_name!r}/{block_name!r}: {line}"
            )
        vector_type_text, vector_value_text = _extract_leading_type_token(pieces[0])
        elem_type_text, elem_value_text = _extract_leading_type_token(pieces[1])
        index_type_text, index_value_text = _extract_leading_type_token(pieces[2])
        index_type = _parse_type(index_type_text)
        if not index_type.is_int:
            raise BackendUnavailable(
                f"self backend insertelement expects integer lane index in {function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "insertelement",
            (
                decode_ssa_name(match.group("dest")),
                _parse_type(vector_type_text),
                decode_value_token(vector_value_text),
                _parse_type(elem_type_text),
                decode_value_token(elem_value_text),
                decode_value_token(index_value_text),
            ),
        )
    if match := _SHUFFLEVECTOR_RE.match(line):
        pieces = split_top_level(match.group("body").strip())
        if len(pieces) != 3:
            raise BackendUnavailable(
                f"self backend malformed shufflevector in {function_name!r}/{block_name!r}: {line}"
            )
        lhs_type_text, lhs_value_text = _extract_leading_type_token(pieces[0])
        rhs_type_text, rhs_value_text = _extract_leading_type_token(pieces[1])
        mask_type_text, mask_value_text = _extract_leading_type_token(pieces[2])
        lhs_type = _parse_type(lhs_type_text)
        rhs_type = _parse_type(rhs_type_text)
        mask_type = _parse_type(mask_type_text)
        if lhs_type.describe() != rhs_type.describe():
            raise BackendUnavailable(
                "self backend shufflevector parser expected matching operand vector types in "
                f"{function_name!r}/{block_name!r}: {line}"
            )
        if not lhs_type.is_array or lhs_type.elem is None:
            raise BackendUnavailable(
                f"self backend shufflevector expects vector lhs type in {function_name!r}/{block_name!r}: {line}"
            )
        if not mask_type.is_array:
            raise BackendUnavailable(
                f"self backend shufflevector expects vector mask type in {function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "shufflevector",
            (
                decode_ssa_name(match.group("dest")),
                TypeDesc("array", count=mask_type.count, elem=lhs_type.elem),
                decode_value_token(lhs_value_text),
                decode_value_token(rhs_value_text),
                mask_type,
                decode_value_token(mask_value_text),
            ),
        )
    if match := _EXTRACTVALUE_RE.match(line):
        aggregate_type = _parse_type(match.group("agg_type"))
        indices = tuple(_parse_extractvalue_indices(match.group("indices")))
        result_type, offset = aggregate_member_info(aggregate_type, indices)
        return ParsedInstr(
            "extractvalue",
            (
                decode_ssa_name(match.group("dest")),
                aggregate_type,
                decode_value_token(match.group("value")),
                indices,
                result_type,
                offset,
            ),
        )
    if parsed := _parse_insertvalue_instruction(function_name, block_name, line):
        return parsed
    if match := _EXTRACTELEMENT_RE.match(line):
        pieces = split_top_level(match.group("body").strip())
        if len(pieces) != 2:
            raise BackendUnavailable(
                f"self backend malformed extractelement in {function_name!r}/{block_name!r}: {line}"
            )
        vector_type_text, vector_value_text = _extract_leading_type_token(pieces[0])
        index_type_text, index_value_text = _extract_leading_type_token(pieces[1])
        vector_type = _parse_type(vector_type_text)
        index_type = _parse_type(index_type_text)
        if not vector_type.is_array or vector_type.elem is None:
            raise BackendUnavailable(
                f"self backend extractelement expects vector/array source in {function_name!r}/{block_name!r}: {line}"
            )
        if not index_type.is_int:
            raise BackendUnavailable(
                f"self backend extractelement expects integer lane index in {function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "extractelement",
            (
                decode_ssa_name(match.group("dest")),
                vector_type,
                decode_value_token(vector_value_text),
                decode_value_token(index_value_text),
                vector_type.elem,
            ),
        )
    if match := _CALL_RE.match(line):
        ret_type = _parse_type(match.group("ret"))
        dest = match.group("dest")
        callee_token = match.group("callee")
        fixed_arg_count, is_vararg_call = _parse_call_signature(match.group("sig"))
        if ret_type.is_void and dest is not None:
            raise BackendUnavailable(
                f"self backend saw void call with SSA destination in {function_name!r}/{block_name!r}: {line}"
            )
        if (not ret_type.is_void) and dest is None:
            raise BackendUnavailable(
                f"self backend saw non-void call without destination in {function_name!r}/{block_name!r}: {line}"
            )
        return ParsedInstr(
            "call",
            (
                None if dest is None else decode_ssa_name(dest),
                ret_type,
                decode_global_name(callee_token) if callee_token.startswith("@") else decode_ssa_name(callee_token),
                callee_token.startswith("%"),
                tuple(_parse_call_args(function_name, match.group("args"))),
                fixed_arg_count,
                is_vararg_call,
            ),
        )
    if match := _VAARG_RE.match(line):
        return ParsedInstr(
            "va_arg",
            (
                decode_ssa_name(match.group("dest")),
                _parse_type(match.group("ap_type")),
                decode_value_token(match.group("ap")),
                _parse_type(match.group("value_type")),
            ),
        )
    if "= getelementptr" in line:
        dest_text, rest = line.split("= getelementptr", 1)
        parts = split_top_level(rest.strip())
        if len(parts) < 3:
            raise BackendUnavailable(
                f"self backend malformed getelementptr in {function_name!r}/{block_name!r}: {line}"
            )
        base_type_text = parts[0].strip()
        while True:
            try:
                base_type = _parse_type(base_type_text)
                break
            except BackendUnavailable:
                pieces = base_type_text.split(None, 1)
                if len(pieces) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pieces[0]):
                    raise
                base_type_text = pieces[1].strip()
        ptr_type_text, ptr_value_text = _extract_leading_type_token(parts[1])
        return ParsedInstr(
            "gep",
            (
                decode_ssa_name(dest_text.strip()),
                base_type,
                _parse_type(ptr_type_text),
                decode_value_token(ptr_value_text),
                tuple(_parse_gep_indices("," + ",".join(parts[2:]))),
            ),
        )

    raise BackendUnavailable(
        f"self backend does not support instruction in {function_name!r}/{block_name!r}: {line}"
    )


def _parse_call_args(function_name: str, args_text: str) -> list[tuple[TypeDesc, str]]:
    text = (args_text or "").strip()
    if not text:
        return []
    chunks = [chunk.strip() for chunk in split_top_level(text) if chunk.strip()]
    args: list[tuple[TypeDesc, str]] = []
    for chunk in chunks:
        try:
            type_text, value_text = _extract_leading_type_token(chunk)
        except BackendUnavailable as exc:
            raise BackendUnavailable(
                f"self backend could not decode call arg in {function_name!r}: {chunk}"
            ) from exc
        if not value_text:
            raise BackendUnavailable(
                f"self backend call arg missing value in {function_name!r}: {chunk}"
            )
        args.append((_parse_type(type_text), decode_value_token(value_text)))
    return args


def _parse_extractvalue_indices(indices_text: str) -> list[int]:
    indices: list[int] = []
    for chunk in indices_text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if not piece.isdigit():
            raise BackendUnavailable(
                f"self backend extractvalue only supports constant indices right now, got {piece!r}"
            )
        indices.append(int(piece))
    if not indices:
        raise BackendUnavailable("self backend extractvalue requires at least one index")
    return indices


def _parse_call_signature(sig_text: str | None) -> tuple[int, bool]:
    text = (sig_text or "").strip()
    if not text:
        return 0, False
    inner = text[1:-1].strip()
    if not inner:
        return 0, False
    pieces = [piece.strip() for piece in inner.split(",") if piece.strip()]
    is_vararg = bool(pieces) and pieces[-1] == "..."
    fixed = pieces[:-1] if is_vararg else pieces
    for piece in fixed:
        _parse_type(piece)
    return len(fixed), is_vararg


def _parse_gep_indices(indices_text: str) -> list[tuple[TypeDesc, str]]:
    indices: list[tuple[TypeDesc, str]] = []
    for chunk in indices_text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        match = _INDEX_RE.match(piece)
        if match is None:
            raise BackendUnavailable(f"self backend could not decode getelementptr index {piece!r}")
        indices.append((_parse_type(match.group("type")), decode_value_token(match.group("value"))))
    return indices


def _constant_gep_offset(base_type: TypeDesc, indices: tuple[tuple[TypeDesc, str], ...]) -> int:
    if not indices:
        raise BackendUnavailable("self backend constant getelementptr requires at least one index")
    first_index = const_int_from_value(indices[0][1])
    if first_index is None:
        raise BackendUnavailable("self backend constant getelementptr requires constant first index")
    offset = first_index * base_type.slot_size
    current = base_type
    for _index_type, index_value in indices[1:]:
        const_index = const_int_from_value(index_value)
        if const_index is None:
            raise BackendUnavailable("self backend constant getelementptr requires constant indices")
        if current.is_array:
            assert current.elem is not None
            stride = _align_to(current.elem.slot_size, current.elem.align)
            offset += const_index * stride
            current = current.elem
            continue
        if current.is_struct:
            offset += current.field_offset(const_index)
            current = current.field_type(const_index)
            continue
        raise BackendUnavailable(
            f"self backend constant getelementptr cannot index into scalar {current.describe()}"
        )
    return offset


def _parse_terminator(function_name: str, block_name: str, line: str) -> ParsedInstr:
    switch_line = _normalize_switch_terminator_line(line)
    if match := _BR_COND_RE.match(line):
        cond_text = match.group("cond").strip()
        true_label = decode_label_ref(match.group("true").strip())
        false_label = decode_label_ref(match.group("false").strip())
        if cond_text == "1":
            return ParsedInstr("br", (true_label,))
        if cond_text == "0":
            return ParsedInstr("br", (false_label,))
        return ParsedInstr(
            "br_cond",
            (
                decode_ssa_name(cond_text),
                true_label,
                false_label,
            ),
        )
    if match := _BR_RE.match(line):
        return ParsedInstr("br", (decode_label_ref(match.group("target").strip()),))
    if _RET_VOID_RE.match(line):
        return ParsedInstr("ret_void", ())
    if match := _RET_RE.match(line):
        return ParsedInstr("ret", (_parse_type(match.group("type")), decode_value_token(match.group("value"))))
    if _UNREACHABLE_RE.match(line):
        return ParsedInstr("unreachable", ())
    if match := _SWITCH_RE.match(switch_line):
        value_type = _parse_type(match.group("type"))
        if not value_type.is_int:
            raise BackendUnavailable(
                f"self backend only supports integer switch values, got {value_type.describe()}"
            )
        cases_text = match.group("cases").strip()
        cases: list[tuple[int, str]] = []
        if cases_text:
            consumed = _SWITCH_CASE_RE.sub("", cases_text)
            if consumed.strip():
                raise BackendUnavailable(
                    f"self backend could not decode switch table in {function_name!r}/{block_name!r}: {line}"
                )
            for case_match in _SWITCH_CASE_RE.finditer(cases_text):
                case_type = _parse_type(case_match.group("type"))
                if case_type != value_type:
                    raise BackendUnavailable(
                        "self backend requires switch case values to use the switch operand type"
                    )
                cases.append((int(case_match.group("value")), decode_label_ref(case_match.group("label"))))
        return ParsedInstr(
            "switch",
            (
                value_type,
                decode_value_token(match.group("value")),
                decode_label_ref(match.group("default")),
                tuple(cases),
            ),
        )

    raise BackendUnavailable(
        f"self backend does not support terminator in {function_name!r}/{block_name!r}: {line}"
    )


def _normalize_switch_terminator_line(line: str) -> str:
    text = line.strip()
    if not text.startswith("switch "):
        return text
    close_index = text.rfind("]")
    if close_index < 0:
        return text
    return text[: close_index + 1]
