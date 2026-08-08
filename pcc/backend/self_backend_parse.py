from __future__ import annotations

"""Target-neutral LLVM IR parser/decoder layer for the self backend.

This module intentionally owns the textual LLVM-IR-facing logic shared by
current and future self-backend targets. The parsed result should be reusable
by AArch64 Darwin today and x86_64 Linux later without reimplementing the LLVM
IR text handling per target.
"""

import re

from . import BackendUnavailable
from .self_backend_float_bits import float32_to_bits, float64_to_bits
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
# These regex fragments only recognize simple instruction fast paths.  Type
# completeness and every nested boundary are owned by the tokenizer/parser
# below; structured instruction fallbacks must feed their type text to it.
_STRUCT_LITERAL_TYPE_TOKEN = r"\{[^{}]+\}"
_BASE_TYPE_TOKEN = (
    rf"(?:{_SCALAR_TYPE_TOKEN}|"
    rf"{_STRUCT_LITERAL_TYPE_TOKEN}|"
    rf"\[\d+ x {_AGG_ELEM_TYPE_TOKEN}\]|"
    rf"<\d+ x {_AGG_ELEM_TYPE_TOKEN}>)"
)
_TYPE_TOKEN = rf"{_BASE_TYPE_TOKEN}(?:\*+)?"
_VALUE_REF_TOKEN = (
    r'(?:%(?:"[^"]+"|(?:[A-Za-z_.$][\w.$-]*|\d+))|@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))'
)
_LABEL_REF_TOKEN = r'%(?:"[^"]+"|(?:[A-Za-z_.$][\w.$-]*|\d+))'
_TARGET_TRIPLE_RE = re.compile(r'^target triple = "([^"]+)"$', re.MULTILINE)
_NAMED_TYPEDEF_RE = re.compile(
    r'^(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = type (?P<body>.+)$',
    re.MULTILINE,
)
_GLOBAL_PTR_GEP_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf"(?P<linkage>(?:internal|private)\s+)?global (?P<type>{_TYPE_TOKEN}) "
    r"getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<base_type>.+?),\s+(?P<ptr_type>.+?)\s+"
    r'(?P<base>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)),\s+i64\s+0,\s+i64\s+0\)$',
    re.MULTILINE,
)
_GEP_INIT_RE = re.compile(
    r"^getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<base_type>.+?),\s+(?P<ptr_type>.+?)\s+"
    r'(?P<base>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)),\s+i64\s+0,\s+i64\s+0\)$'
)
_CONST_GEP_RE = re.compile(
    r"^getelementptr(?:\s+inbounds)?(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\((?P<body>.*)\)$"
)
_GLOBAL_HEADER_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf"(?P<prefix>.*?)(?P<kind>global|constant)\s+(?P<body>.+)$"
)
_SSA_NAME_RE = re.compile(r'^%(?:"([^"]+)"|((?:[A-Za-z_.$][\w.$-]*|\d+)))$')
_GLOBAL_NAME_RE = re.compile(r'^@(?:"([^"]+)"|([A-Za-z_.$][\w.$-]*))$')
_FUNCTION_NAME_RE = re.compile(r'(@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))\(')
_FUNCTION_DEF_RE = re.compile(r"^define\s+", re.MULTILINE)
_FUNCTION_CLOSE_RE = re.compile(r"^\}", re.MULTILINE)
_LABEL_REF_RE = re.compile(
    r'^(?:label\s+)?%(?:"([^"]+)"|((?:[A-Za-z_.$][\w.$-]*|\d+)))$'
)
_PLAIN_LABEL_RE = re.compile(
    r'^(?:"(?P<quoted>[^"]+)"|(?P<plain>[A-Za-z_.$][\w.$-]*|\.[0-9A-Za-z_.$-]+|\d+)):(?:\s*;.*)?$'
)
_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_.$][A-Za-z0-9_.$]*$")
_HEX_DIGITS = "0123456789abcdefABCDEF"


# Token classifiers, not regexes: these three ran 7.8M of the 11.0M regex
# calls needed to parse one 27 MB module.  Under pcc1 every call enters the
# pcc-Python regex engine (pattern-cache walk + matcher), which is orders of
# magnitude more expensive than the str methods below; that per-call cost is
# what made `pcc1 -> pcc2` take minutes per huge module.  Each helper uses
# whole-string runtime calls (`isdecimal`/`find`/`strip`) rather than per-index
# character reads, because `text[i]` allocates a fresh one-character str in the
# pcc runtime.
#
# `str.isdecimal()` accepts exactly the Unicode Nd set that `\d` matches in a
# str pattern, so these stay equivalent to the retired
# `^-?\d+$` / `^0x[0-9A-Fa-f]+$` / float patterns for every token the IR
# parser sees.  The one deliberate difference: `$` also matches just before a
# trailing newline, so a trailing "\n" is trimmed first to preserve that.


_U64_MASK = (1 << 64) - 1  # computed, NOT a literal: see M5-SELFHOST-BIG-INT-LITERAL


def _without_trailing_newline(text: str) -> str:
    if text.endswith("\n"):
        return text[:-1]
    return text


def _is_int_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if body.startswith("-"):
        body = body[1:]
    return body.isdecimal()


def _is_hex_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if not body.startswith("0x") or len(body) < 3:
        return False
    # strip() removes maximal runs of the allowed set from both ends, so the
    # result is empty exactly when every character is a hex digit.
    return body[2:].strip(_HEX_DIGITS) == ""


def _is_float_token(text: str) -> bool:
    body = _without_trailing_newline(text)
    if body.startswith("-"):
        body = body[1:]
    exponent_at = body.find("e")
    if exponent_at < 0:
        exponent_at = body.find("E")
    if exponent_at >= 0:
        exponent = body[exponent_at + 1:]
        body = body[:exponent_at]
        if exponent.startswith("-") or exponent.startswith("+"):
            exponent = exponent[1:]
        if not exponent.isdecimal():
            return False
    dot_at = body.find(".")
    if dot_at < 0:
        return body.isdecimal()
    whole = body[:dot_at]
    fraction = body[dot_at + 1:]
    if whole and not whole.isdecimal():
        return False
    if fraction and not fraction.isdecimal():
        return False
    return bool(whole) or bool(fraction)
_ARG_RE = re.compile(
    rf"^(?P<type>{_TYPE_TOKEN})(?:\s+[A-Za-z_][A-Za-z0-9_.$-]*)*\s+"
    r'(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))$'
)
_ALLOCA_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*alloca\s+(?P<type>{_TYPE_TOKEN})(?:\s*,.*)?$"
)
_STORE_RE = re.compile(
    rf"^store\s+(?P<val_type>{_TYPE_TOKEN})\s+(?P<value>.+?),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$"
)
_LOAD_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*load\s+(?P<val_type>{_TYPE_TOKEN}),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$"
)
_LOAD_ATOMIC_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*load\s+atomic\s+(?P<val_type>{_TYPE_TOKEN}),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})\s+"
    r"(?P<ordering>unordered|monotonic|acquire|seq_cst)(?:,\s+align\s+\d+)?$"
)
_STORE_ATOMIC_RE = re.compile(
    rf"^store\s+atomic\s+(?P<val_type>{_TYPE_TOKEN})\s+(?P<value>.+?),\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})\s+"
    r"(?P<ordering>unordered|monotonic|release|seq_cst)(?:,\s+align\s+\d+)?$"
)
_ATOMICRMW_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*atomicrmw\s+(?P<op>add|sub|and|or|xchg)\s+"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN}),\s+"
    rf"(?P<val_type>{_TYPE_TOKEN})\s+(?P<value>.+?)\s+"
    r"(?P<ordering>monotonic|acquire|release|acq_rel|seq_cst)(?:,\s+align\s+\d+)?$"
)
_CMPXCHG_RE = re.compile(
    rf"^(?P<dest>%.*)\s*=\s*cmpxchg\s+(?:weak\s+)?"
    rf"(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN}),\s+"
    rf"(?P<expected_type>{_TYPE_TOKEN})\s+(?P<expected>.+?),\s+"
    rf"(?P<desired_type>{_TYPE_TOKEN})\s+(?P<desired>.+?)\s+"
    r"(?P<success>monotonic|acquire|release|acq_rel|seq_cst)\s+"
    r"(?P<failure>monotonic|acquire|seq_cst)(?:,\s+align\s+\d+)?$"
)
_FENCE_RE = re.compile(
    r"^fence\s+(?P<ordering>acquire|release|acq_rel|seq_cst)$"
)
# The one inline-asm shape pcc emits (pcc.unsafe.syscall6, musl x86_64 ABI).
# Anything else containing " asm " stays fail-closed in the call parser.
_SYSCALL6_ASM_RE = re.compile(
    r"^(?P<dest>%.*)\s*=\s*call\s+i64\s+asm\s+sideeffect\s+"
    r'"syscall",\s*"=\{rax\},\{rax\},\{rdi\},\{rsi\},\{rdx\},\{r10\},\{r8\},\{r9\},'
    r'~\{rcx\},~\{r11\},~\{memory\}"\s*'
    r"\((?P<args>[^)]*)\)(?:\s+#\d+)?(?:,\s*!.*)?$"
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
_PHI_RE = re.compile(r"^(?P<dest>%.*)\s*=\s*phi\s+(?P<body>.+)$")
_BR_COND_RE = re.compile(
    r"^br\s+i1\s+(?P<cond>(?:%.*?|[01]|true|false|undef|poison)),\s+label\s+(?P<true>.*?),\s+label\s+(?P<false>.+)$"
)
_BR_RE = re.compile(r"^br\s+label\s+(?P<target>.+)$")
_RET_VOID_RE = re.compile(r"^ret\s+void$")
_RET_RE = re.compile(rf"^ret\s+(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+)$")
_UNREACHABLE_RE = re.compile(r"^unreachable$")
_INDEX_RE = re.compile(r"^(?P<type>i\d+)\s+(?P<value>.+)$")
_NAMED_TYPES: dict[str, TypeDesc] = {}
_TYPE_CACHE: dict[str, TypeDesc] = {}
_CALL_SIGNATURE_CACHE: dict[str, tuple[int, bool]] = {}
_NUMERIC_SSA_NAME_CACHE: dict[int, str] = {}
_DOT_NUMERIC_SSA_NAME_CACHE: dict[int, str] = {}
_SPLIT_NESTING_MARKERS = '"{}[]()<>'


def parse_self_backend_target_triple(ir_text: str) -> str:
    match = _TARGET_TRIPLE_RE.search(ir_text)
    if match is None:
        raise BackendUnavailable(
            "self backend requires a target triple in LLVM IR text"
        )
    return match.group(1)


def parse_self_backend_module(ir_text: str) -> ParsedModule:
    _TYPE_CACHE.clear()
    _CALL_SIGNATURE_CACHE.clear()
    _NUMERIC_SSA_NAME_CACHE.clear()
    _DOT_NUMERIC_SSA_NAME_CACHE.clear()
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
    if not _has_split_nesting_markers(text):
        if not text:
            return []
        return [piece.strip() for piece in text.split(",") if piece.strip()]
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
        elif (
            ch == ","
            and brace_depth == 0
            and bracket_depth == 0
            and paren_depth == 0
            and angle_depth == 0
        ):
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _has_split_nesting_markers(text: str) -> bool:
    for marker in _SPLIT_NESTING_MARKERS:
        if marker in text:
            return True
    return False


def strip_typed_initializer(item: str) -> str:
    text = item.strip()
    if not text or text == "zeroinitializer":
        return text
    try:
        _type_text, initializer = _extract_leading_type_token(text)
    except BackendUnavailable:
        initializer = ""
    if initializer:
        return initializer
    if text.startswith(('c"', "{", "[", "@", "null", "gep0:")):
        return text
    if _is_int_token(text) or _is_hex_token(text):
        return text
    return text


def decode_llvm_c_string(token: str) -> bytes:
    if not (token.startswith('c"') and token.endswith('"')):
        raise BackendUnavailable(
            f"self backend expected LLVM c-string initializer, got {token!r}"
        )
    body = token[2:-1]
    data = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                raise BackendUnavailable(
                    f"self backend saw truncated LLVM string escape in {token!r}"
                )
            if i + 2 < len(body) and re.fullmatch(
                r"[0-9A-Fa-f]{2}", body[i + 1 : i + 3]
            ):
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
    simple = _decode_simple_value_token(token)
    if simple is not None:
        return simple
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
        if token.startswith(
            ("null", "zeroinitializer", "getelementptr", 'c"', "{", "[", "<")
        ):
            break
        if token.startswith("%") or token.startswith("@"):
            break
        if _is_int_token(token) or _is_hex_token(token) or _is_float_token(token):
            break
        if token.startswith(("inttoptr ", "ptrtoint ", "trunc ", "zext ", "sext ")):
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
    simple = _decode_simple_value_token(token)
    if simple is not None:
        return simple
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
    if _is_int_token(token):
        return token
    if _is_hex_token(token):
        return token
    if _is_float_token(token):
        return token
    if token.startswith("%"):
        return decode_ssa_name(token)
    if token.startswith("@"):
        return "@" + decode_global_name(token)
    raise BackendUnavailable(f"unsupported value syntax for self backend: {token!r}")


def _decode_simple_value_token(token: str) -> str | None:
    if not token:
        return None
    if token in {"null", "poison", "undef", "false", "true", "zeroinitializer"}:
        return token
    first = token[0]
    if first == "%":
        return decode_ssa_name(token)
    if first == "@":
        return "@" + decode_global_name(token)
    if first == "0" and token.startswith("0x") and _is_hex_token(token):
        return token
    if first.isdigit():
        if token.isdigit() or _is_float_token(token):
            return token
        return None
    if first == "-":
        if len(token) > 1 and token[1:].isdigit():
            return token
        if _is_float_token(token):
            return token
    return None


_IR_TYPE_PUNCTUATION = "{}[]<>,*()"


def _next_ir_type_token(
    text: str,
    index: int,
) -> tuple[tuple[str, str, int, int], int]:
    """Return one token and the next character offset.

    Keeping this cursor lazy matters for globals with large initializers: a
    leading-type query must not materialize tokens for the value suffix.
    """
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return ("eof", "", index, index), index
    ch = text[index]
    if ch in _IR_TYPE_PUNCTUATION:
        return ("punct", ch, index, index + 1), index + 1

    start = index
    quote_index = -1
    if ch == '"':
        quote_index = index
    elif ch in {"%", "@"} and index + 1 < len(text) and text[index + 1] == '"':
        quote_index = index + 1
    if quote_index >= 0:
        index = quote_index + 1
        escaped = False
        closed = False
        while index < len(text):
            current = text[index]
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                index += 1
                closed = True
                break
            index += 1
        if not closed:
            raise BackendUnavailable(
                f"self backend saw unterminated quoted LLVM type token in {text!r}"
            )
        return ("atom", text[start:index], start, index), index

    while (
        index < len(text)
        and not text[index].isspace()
        and text[index] not in _IR_TYPE_PUNCTUATION
        and text[index] != '"'
    ):
        index += 1
    if index == start:
        raise BackendUnavailable(
            f"self backend does not understand LLVM type token at {text[start:]!r}"
        )
    return ("atom", text[start:index], start, index), index


def _tokenize_ir_type(text: str) -> list[tuple[str, str, int, int]]:
    """Materialize the small LLVM type token stream for diagnostics/tests."""
    tokens: list[tuple[str, str, int, int]] = []
    index = 0
    while True:
        token, index = _next_ir_type_token(text, index)
        if token[0] == "eof":
            return tokens
        tokens.append(token)


def _ir_type_parse_error(text: str, detail: str) -> Exception:
    """Build a parse failure through the stable builtin exception ABI.

    Returning the imported concrete subclass in the public annotation made a
    self-hosted multi-module compile create two nominal
    ``BackendUnavailable`` identities.  The value is intentionally raised by
    every caller, so the builtin base class is the honest cross-module return
    contract while preserving the concrete diagnostic at runtime.
    """
    return BackendUnavailable(
        f"self backend does not understand LLVM type {text.strip()!r}: {detail}"
    )


def _parse_ir_type_tokens(
    text: str,
    index: int,
    *,
    resolve_named=None,
) -> tuple[TypeDesc, int]:
    """Recursively parse one TypeDesc from the lazy token cursor."""
    token_info, index = _next_ir_type_token(text, index)
    if token_info[0] == "eof":
        raise _ir_type_parse_error(text, "expected a type")
    token = token_info[1]

    if token == "[" or token == "<":
        close = "]" if token == "[" else ">"
        count_info, index = _next_ir_type_token(text, index)
        if count_info[0] == "eof" or not count_info[1].isdigit():
            kind = "array" if token == "[" else "vector"
            raise _ir_type_parse_error(text, f"expected {kind} element count")
        count = int(count_info[1])
        x_info, index = _next_ir_type_token(text, index)
        if x_info[0] == "eof" or x_info[1] != "x":
            raise _ir_type_parse_error(text, "expected 'x' after aggregate count")
        elem, index = _parse_ir_type_tokens(
            text,
            index,
            resolve_named=resolve_named,
        )
        close_info, index = _next_ir_type_token(text, index)
        if close_info[0] == "eof" or close_info[1] != close:
            raise _ir_type_parse_error(text, f"expected closing {close!r}")
        base = TypeDesc("array", count=count, elem=elem)
    elif token == "{":
        fields: list[TypeDesc] = []
        next_info, next_offset = _next_ir_type_token(text, index)
        if next_info[1] == "}":
            index = next_offset
        else:
            while True:
                field, index = _parse_ir_type_tokens(
                    text,
                    index,
                    resolve_named=resolve_named,
                )
                fields.append(field)
                delimiter_info, next_offset = _next_ir_type_token(text, index)
                if delimiter_info[0] == "eof":
                    raise _ir_type_parse_error(text, "expected closing '}'")
                delimiter = delimiter_info[1]
                index = next_offset
                if delimiter == "}":
                    break
                if delimiter != ",":
                    raise _ir_type_parse_error(
                        text,
                        f"expected ',' or '}}', got {delimiter!r}",
                    )
                field_info, _field_offset = _next_ir_type_token(text, index)
                if field_info[0] == "eof" or field_info[1] == "}":
                    raise _ir_type_parse_error(text, "expected type after ','")
        base = TypeDesc("struct", fields=tuple(fields))
    elif token == "void":
        base = TypeDesc("void")
    elif token == "ptr":
        base = TypeDesc("ptr", pointee=TypeDesc("void"))
    elif token == "float":
        base = TypeDesc("fp", 32)
    elif token == "double":
        base = TypeDesc("fp", 64)
    elif token.startswith("i") and token[1:].isdigit():
        base = TypeDesc("int", int(token[1:]))
    elif token.startswith("%"):
        if token not in _NAMED_TYPES and resolve_named is not None:
            resolve_named(token)
        if token not in _NAMED_TYPES:
            raise BackendUnavailable(
                f"self backend does not know named LLVM type {token!r}"
            )
        base = _NAMED_TYPES[token]
    else:
        raise _ir_type_parse_error(text, f"unsupported token {token!r}")

    while True:
        star_index = index
        while star_index < len(text) and text[star_index].isspace():
            star_index += 1
        if star_index >= len(text) or text[star_index] != "*":
            break
        base = TypeDesc("ptr", pointee=base)
        index = star_index + 1
    return base, index


def _parse_ir_type_prefix(
    text: str,
    *,
    resolve_named=None,
) -> tuple[TypeDesc, int]:
    return _parse_ir_type_tokens(
        text,
        0,
        resolve_named=resolve_named,
    )


def _parse_ir_type_list(
    text: str,
    *,
    allow_vararg: bool = False,
    resolve_named=None,
) -> tuple[tuple[TypeDesc, ...], bool]:
    parsed: list[TypeDesc] = []
    index = 0
    is_vararg = False
    while True:
        item_info, next_offset = _next_ir_type_token(text, index)
        if item_info[0] == "eof":
            return tuple(parsed), is_vararg
        if item_info[1] == "...":
            if not allow_vararg:
                raise _ir_type_parse_error(
                    text,
                    "variadic marker is not allowed here",
                )
            is_vararg = True
            trailing_info, _trailing_offset = _next_ir_type_token(
                text,
                next_offset,
            )
            if trailing_info[0] != "eof":
                raise _ir_type_parse_error(
                    text,
                    "variadic marker must terminate the type list",
                )
            return tuple(parsed), is_vararg
        item, index = _parse_ir_type_tokens(
            text,
            index,
            resolve_named=resolve_named,
        )
        parsed.append(item)
        delimiter_info, next_offset = _next_ir_type_token(text, index)
        if delimiter_info[0] == "eof":
            return tuple(parsed), is_vararg
        if delimiter_info[1] != ",":
            raise _ir_type_parse_error(
                text,
                f"expected ',' between types, got {delimiter_info[1]!r}",
            )
        index = next_offset
        trailing_info, _trailing_offset = _next_ir_type_token(text, index)
        if trailing_info[0] == "eof":
            raise _ir_type_parse_error(text, "trailing comma in type list")


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
            text[index : index + len(keyword)] == keyword
            and depth_square == 0
            and depth_brace == 0
            and depth_paren == 0
            and depth_angle == 0
        ):
            return text[:index].strip(), text[index + len(keyword) :].strip()
        index += 1
    raise BackendUnavailable(
        f"self backend could not split {text!r} on top-level {keyword!r}"
    )


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
    if len(pieces) == 2 and pieces[0] in {
        "add",
        "sub",
        "mul",
        "and",
        "or",
        "xor",
        "shl",
        "lshr",
        "ashr",
    }:
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
        raise BackendUnavailable(
            f"unsupported SSA value syntax for self backend: {token!r}"
        )
    name = match.group(1) or match.group(2)
    if name.isdigit():
        numeric_name = int(name)
        cached_name = _NUMERIC_SSA_NAME_CACHE.get(numeric_name)
        if cached_name is None:
            cached_name = f"%{numeric_name}"
            _NUMERIC_SSA_NAME_CACHE[numeric_name] = cached_name
        return cached_name
    if len(name) > 1 and name.startswith(".") and name[1:].isdigit():
        numeric_name = int(name[1:])
        cached_name = _DOT_NUMERIC_SSA_NAME_CACHE.get(numeric_name)
        if cached_name is None:
            cached_name = f"%.{numeric_name}"
            _DOT_NUMERIC_SSA_NAME_CACHE[numeric_name] = cached_name
        return cached_name
    return name


def decode_global_name(token: str) -> str:
    match = _GLOBAL_NAME_RE.match(token.strip())
    if match is None:
        raise BackendUnavailable(
            f"unsupported global symbol syntax for self backend: {token!r}"
        )
    return match.group(1) or match.group(2)


def decode_label_ref(token: str) -> str:
    token = token.strip()
    if "," in token:
        token = token.split(",", 1)[0].strip()
    if token.endswith(":"):
        plain_match = _PLAIN_LABEL_RE.match(token)
        if plain_match is not None:
            return plain_match.group("quoted") or plain_match.group("plain")
    match = _LABEL_REF_RE.match(token)
    if match is None:
        raise BackendUnavailable(
            f"unsupported label syntax for self backend: {token!r}"
        )
    return match.group(1) or match.group(2)


def const_int_from_value(value: str) -> int | None:
    if value == "false":
        return 0
    if value == "true":
        return 1
    if _is_int_token(value):
        return int(value)
    return None


def is_hex_literal(value: str) -> bool:
    return _is_hex_token(value)


def is_float_literal(value: str) -> bool:
    return _is_float_token(value) and not value.startswith(".")


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
            if (
                value_type.elem is None
                or not value_type.elem.is_int
                or value_type.elem.width != 8
            ):
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
            item_bytes = aggregate_literal_to_bytes(
                value_type.elem, strip_typed_initializer(item)
            )
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
        for index, (field_type, item) in enumerate(zip(value_type.fields, items)):
            field_bytes = aggregate_literal_to_bytes(
                field_type, strip_typed_initializer(item)
            )
            field_offset = value_type.field_offset(index)
            _write_bytes(data, field_offset, field_bytes)
        return bytes(data)
    if value_type.is_ptr:
        if text in {"null", "poison", "undef"}:
            return (0).to_bytes(8, "little")
        if text.startswith("inttoptr"):
            decoded = decode_value_token(text)
            if decoded.startswith("inttoptrconst:"):
                text = decoded.split(":", 1)[1]
        int_value = const_int_from_value(text)
        if int_value is not None:
            return (int_value & ((1 << 64) - 1)).to_bytes(8, "little")
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
        return (int_value & mask).to_bytes(value_type.slot_size, "little")
    if value_type.is_fp:
        if text in {"poison", "undef"}:
            return bytes(value_type.slot_size)
        if value_type.width <= 32:
            if text.startswith("0x"):
                bits = int(text, 16) & 0xFFFFFFFF
            else:
                bits = float32_to_bits(float(text))
            return bits.to_bytes(4, "little")
        if text.startswith("0x"):
            bits = int(text, 16) & _U64_MASK
        else:
            bits = float64_to_bits(float(text))
        return bits.to_bytes(8, "little")
    raise BackendUnavailable(
        f"self backend aggregate literal type not translated yet: {value_type.describe()}"
    )


def gep_result_type(
    base_type: TypeDesc, indices: tuple[tuple[TypeDesc, str], ...]
) -> TypeDesc:
    if not indices:
        raise BackendUnavailable(
            "self backend getelementptr requires at least one index"
        )
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
        raise BackendUnavailable(
            f"self backend could not parse constant getelementptr {text!r}"
        )
    parts = split_top_level(match.group("body"))
    if len(parts) < 3:
        raise BackendUnavailable(
            f"self backend constant getelementptr is incomplete: {text!r}"
        )
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
    return decode_global_name(base_value), _constant_gep_offset(
        base_type, tuple(indices)
    )


def _arg_list_is_vararg(args_text: str) -> bool:
    pieces = [
        piece.strip()
        for piece in split_top_level(args_text or "")
        if piece.strip()
    ]
    return bool(pieces) and pieces[-1] == "..."


def _parse_type(text: str, *, resolve_named=None) -> TypeDesc:
    token = text.strip()
    if not token:
        raise BackendUnavailable("self backend does not understand empty LLVM type")
    if resolve_named is None:
        cached = _TYPE_CACHE.get(token)
        if cached is not None:
            return cached
    base, end = _parse_ir_type_tokens(
        token,
        0,
        resolve_named=resolve_named,
    )
    trailing_info, _trailing_offset = _next_ir_type_token(token, end)
    if trailing_info[0] != "eof":
        raise _ir_type_parse_error(
            token,
            f"unexpected trailing token {trailing_info[1]!r}",
        )
    if resolve_named is None:
        _TYPE_CACHE[token] = base
    return base


def _strip_volatile_memory_op_prefix(text: str) -> str:
    token = text.strip()
    if token.startswith("volatile "):
        return token[len("volatile ") :].strip()
    return token


def _parse_named_types(ir_text: str) -> None:
    _NAMED_TYPES.clear()
    _TYPE_CACHE.clear()
    pending: dict[str, str] = {}
    search_pos = 0
    while search_pos < len(ir_text):
        match = _NAMED_TYPEDEF_RE.search(ir_text, search_pos)
        if match is None:
            break
        body_text = match.group("body").strip()
        # Preserve the existing boundary for opaque and packed declarations:
        # they may be present but unused.  A reference still fails closed as
        # an unknown named type; regular structs are parsed structurally.
        if body_text.startswith("{"):
            pending[match.group("name")] = body_text
        search_pos = match.end()

    def resolve(name: str) -> TypeDesc:
        existing = _NAMED_TYPES.get(name)
        if existing is not None:
            return existing
        if name not in pending:
            raise BackendUnavailable(
                f"self backend has no definition for named type {name!r}"
            )
        body_text = pending[name]
        placeholder = TypeDesc("struct", name=name)
        _NAMED_TYPES[name] = placeholder
        parsed = _parse_type(body_text, resolve_named=resolve)
        if not parsed.is_struct:
            raise BackendUnavailable(
                f"self backend named LLVM type {name!r} must be a struct, got {body_text!r}"
            )
        resolved = TypeDesc(
            "struct",
            name=name,
            fields=parsed.fields,
        )
        _NAMED_TYPES[name] = resolved
        return resolved

    for name in list(pending):
        resolve(name)


def _parse_functions(ir_text: str) -> list[ParsedFunction]:
    functions: list[ParsedFunction] = []
    for header_text, body_text in _iter_function_defs(ir_text):
        prefix_text, ret_type_text, name_text, args_text = _parse_function_header(
            header_text
        )
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
                value_types={},
                value_slots={},
                alloca_slots={},
                block_map={},
                used_values=[],
                # Keep every target-owned mutable field explicit here.  Host
                # Python applies dataclass ``default_factory`` values, but the
                # pcc1 closed-world constructor signature cannot currently
                # materialize those factories at an imported class call site.
                # Explicit fresh containers preserve the same per-function
                # ownership without making bootstrap depend on that fallback.
                value_registers={},
                aarch64_madd_fusions=[],
                aarch64_block_layout=[],
                aarch64_cold_fallthrough_edges=[],
                hidden_sret_slot=None,
                frame_size=0,
            )
        )
    return functions


def _iter_function_defs(ir_text: str) -> list[tuple[str, str]]:
    defs: list[tuple[str, str]] = []
    header_lines: list[str] = []
    body_lines: list[str] = []
    in_header = False
    in_body = False
    for line in ir_text.splitlines():
        if not in_header and not in_body:
            if not line.startswith("define "):
                continue
            header_lines = [line]
            in_header = True
            if line.rstrip().endswith("{"):
                in_header = False
                in_body = True
            continue
        if in_header:
            header_lines.append(line)
            if line.rstrip().endswith("{"):
                in_header = False
                in_body = True
            continue
        if line == "}":
            defs.append(("\n".join(header_lines), "\n".join(body_lines)))
            header_lines = []
            body_lines = []
            in_body = False
            continue
        body_lines.append(line)
    if in_header or in_body:
        raise BackendUnavailable("self backend saw unterminated function body")
    return defs


def _find_function_close_brace(text: str, open_index: int) -> int:
    index = open_index + 1
    while index < len(text):
        if text[index] == "}" and index > 0 and text[index - 1] == "\n":
            return index
        index += 1
    return -1


def _find_function_open_brace(text: str, start: int) -> int:
    name_match = _FUNCTION_NAME_RE.search(text, start)
    if name_match is None:
        raise BackendUnavailable(
            "self backend could not find function name while splitting function body"
        )
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


def _find_function_args_open(text: str, start: int) -> int:
    saw_global_name = False
    in_quote = False
    escape = False
    index = start
    while index < len(text):
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
        elif ch == "@":
            saw_global_name = True
        elif ch == "(" and saw_global_name:
            return index
        elif ch == "\n":
            return -1
        index += 1
    return -1


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
    raise BackendUnavailable(
        "self backend could not find matching ')' in function header"
    )


def _split_trailing_type_token(text: str) -> tuple[str, str]:
    source = text.strip()
    tokens = _tokenize_ir_type(source)
    for token in tokens:
        start = token[2]
        candidate = source[start:].strip()
        try:
            _parse_type(candidate)
        except BackendUnavailable:
            continue
        return source[:start].rstrip(), candidate
    raise BackendUnavailable(
        f"self backend could not split trailing type token from {text!r}"
    )


def _parse_function_header(header_text: str) -> tuple[str, str, str, str]:
    text = header_text.strip()
    if not text.endswith("{"):
        raise BackendUnavailable(
            "self backend expected function header to end with '{'"
        )
    text = text[:-1].rstrip()
    if not text.startswith("define "):
        raise BackendUnavailable(
            "self backend expected function header to start with 'define'"
        )
    text = text[len("define ") :].strip()
    name_match = _FUNCTION_NAME_RE.search(text)
    if name_match is None:
        raise BackendUnavailable(
            f"self backend could not decode function name from header: {header_text!r}"
        )
    prefix_and_ret = text[: name_match.start()].rstrip()
    prefix, ret = _split_trailing_type_token(prefix_and_ret)
    try:
        _parse_type(ret)
    except BackendUnavailable:
        raise BackendUnavailable(
            f"self backend could not decode return type from header: {header_text!r}"
        )
    args_open = name_match.end() - 1
    args_close = _find_matching_paren(text, args_open)
    return (
        prefix,
        ret,
        name_match.group(1),
        text[args_open + 1 : args_close],
    )


def _thread_local_models(prefix: str, line: str) -> list[str]:
    """Decode LLVM's finite ``thread_local[(model)]`` prefix syntax.

    This deliberately uses string operations rather than Python's general
    regex iterator surface.  The parser is part of the pcc1 self-host closure,
    whose no-libpython ``re`` implementation does not own ``finditer`` or this
    pattern's word-boundary/named-group combination.
    """

    needle = "thread_local"
    models: list[str] = []
    search_pos = 0
    while True:
        index = prefix.find(needle, search_pos)
        if index < 0:
            return models
        after = index + len(needle)
        search_pos = after
        before_char = prefix[index - 1] if index > 0 else ""
        after_char = prefix[after] if after < len(prefix) else ""
        if before_char and (before_char.isalnum() or before_char == "_"):
            continue
        if after_char and (after_char.isalnum() or after_char == "_"):
            continue
        if after_char != "(":
            models.append("default")
            continue
        close = prefix.find(")", after + 1)
        if close < 0:
            raise BackendUnavailable(
                f"self backend found an unterminated thread_local model: {line!r}"
            )
        raw_model = prefix[after + 1 : close]
        if "(" in raw_model:
            raise BackendUnavailable(
                f"self backend found a nested thread_local model: {line!r}"
            )
        model = raw_model.strip().lower()
        if not model:
            raise BackendUnavailable(
                f"self backend found an empty thread_local model: {line!r}"
            )
        models.append(model)
        search_pos = close + 1


def _parse_globals(ir_text: str) -> list[GlobalDef]:
    globals_: list[GlobalDef] = []
    seen: set[str] = set()
    for line in ir_text.splitlines():
        if not line.startswith("@"):
            continue
        match = _GLOBAL_HEADER_RE.match(line)
        if match is None:
            continue
        prefix = match.group("prefix") or ""
        tls_models = _thread_local_models(prefix, line)
        if len(tls_models) > 1:
            raise BackendUnavailable(
                f"self backend found duplicate thread_local storage classes: {line!r}"
            )
        tls_model = tls_models[0] if tls_models else ""
        if "external" in prefix.split():
            if tls_model:
                raise BackendUnavailable(
                    "self backend target TLS lowering does not support external "
                    f"thread-local declarations yet: {line!r}"
                )
            continue
        name = decode_global_name(match.group("name"))
        if name in seen:
            continue
        check_simple_symbol_name(name)
        body_text = match.group("body")
        try:
            type_text, initializer = _extract_leading_type_token(body_text)
        except BackendUnavailable as exc:
            raise BackendUnavailable(
                f"self backend could not split global type from initializer: {body_text!r}"
            ) from exc
        if not type_text or not initializer:
            raise BackendUnavailable(
                f"self backend could not split global type from initializer: {body_text!r}"
            )
        initializer, trailing_attributes = _split_global_trailing_attrs(initializer)
        alignment = 0
        for attribute in trailing_attributes:
            align_match = re.fullmatch(r"align\s+(\d+)", attribute)
            if align_match is not None:
                alignment = int(align_match.group(1))
        if gep := _GLOBAL_PTR_GEP_RE.match(line):
            initializer = f"gep0:{decode_global_name(gep.group('base'))}"
        try:
            parsed_type = _parse_type(type_text)
        except TypeError as exc:
            raise BackendUnavailable(type_text) from exc
        globals_.append(
            GlobalDef(
                name=name,
                type=parsed_type,
                initializer=initializer,
                is_constant=match.group("kind") == "constant",
                is_internal=("internal" in prefix) or ("private" in prefix),
                tls_model=tls_model,
                alignment=alignment,
                ir_prefix=prefix.strip(),
                trailing_attributes=trailing_attributes,
            )
        )
        seen.add(name)
    return globals_


_GLOBAL_TRAILING_ATTR_RE = re.compile(
    r",\s*(?:align\s+\d+|section\s+\"[^\"]*\"|comdat(?:\s*\([^)]*\))?)\s*$"
)


def _strip_global_trailing_attrs(initializer: str) -> str:
    current, _attributes = _split_global_trailing_attrs(initializer)
    return current


def _split_global_trailing_attrs(initializer: str) -> tuple[str, tuple[str, ...]]:
    current = initializer + ""
    attributes: list[str] = []
    while True:
        last_comma = _last_top_level_comma(current)
        if last_comma < 0:
            attributes.reverse()
            return current, tuple(attributes)
        head, tail = current[:last_comma], current[last_comma:]
        if _GLOBAL_TRAILING_ATTR_RE.match(tail):
            attributes.append(tail[1:].strip())
            current = head.rstrip()
            continue
        attributes.reverse()
        return current, tuple(attributes)


def _last_top_level_comma(text: str) -> int:
    depth_square = 0
    depth_brace = 0
    depth_paren = 0
    in_quote = False
    escape = False
    last = -1
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
        elif ch == "]":
            depth_square -= 1
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
        elif ch == "," and depth_square == 0 and depth_brace == 0 and depth_paren == 0:
            last = index
    return last


def _extract_leading_type_token(text: str) -> tuple[str, str]:
    source = text.lstrip()
    if not source:
        raise BackendUnavailable(
            f"self backend could not extract leading type token from {text!r}"
        )
    parsed, end = _parse_ir_type_prefix(source)
    if end < len(source) and not source[end].isspace() and source[end] != ",":
        raise BackendUnavailable(
            f"self backend found no boundary after leading LLVM type in {text!r}"
        )
    type_text = source[:end].strip()
    _TYPE_CACHE[type_text] = parsed
    rest = source[end:].lstrip()
    if rest.startswith("addrspace"):
        raise BackendUnavailable(
            f"self backend does not support address-space LLVM types in {text!r}"
        )
    if rest.startswith("("):
        close = _find_matching_paren(rest, 0)
        if rest[close + 1 :].lstrip().startswith("*"):
            raise BackendUnavailable(
                f"self backend does not support LLVM function types in {text!r}"
            )
    if rest.startswith(","):
        rest = rest[1:].lstrip()
    return type_text, rest


def _split_top_level_once(text: str, sep: str) -> tuple[str, str]:
    if sep == "," and not _has_split_nesting_markers(text):
        left, found, right = text.partition(sep)
        if found:
            return left.strip(), right.strip()
        raise BackendUnavailable(
            f"self backend could not split {text!r} on top-level {sep!r}"
        )
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
        if (
            ch == sep
            and depth_square == 0
            and depth_brace == 0
            and depth_paren == 0
            and depth_angle == 0
        ):
            return text[:index].strip(), text[index + 1 :].strip()
    raise BackendUnavailable(
        f"self backend could not split {text!r} on top-level {sep!r}"
    )


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
            if (
                depth_square == 0
                and depth_brace == 0
                and depth_paren == 0
                and depth_angle == 0
            ):
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

    if (
        entry_start is not None
        or depth_square != 0
        or depth_brace != 0
        or depth_paren != 0
        or depth_angle != 0
    ):
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
            current = ParsedBlock(
                name=label_match.group("quoted") or label_match.group("plain"),
                raw_lines=[],
                phis=[],
                instructions=[],
                terminator=None,
            )
            blocks.append(current)
            continue
        if current is None:
            raise BackendUnavailable(
                f"self backend expected a labeled basic block in {function_name!r}: {line}"
            )
        current.raw_lines.append(line)

    if not blocks:
        raise BackendUnavailable(
            f"self backend found no basic blocks in {function_name!r}"
        )

    for block in blocks:
        _parse_block(function_name, block)
    return _filter_reachable_blocks(blocks)


def _terminator_successors(term: ParsedInstr | None) -> tuple[str, ...]:
    if term is None:
        return ()
    if term.kind == "br":
        return (term.data[0],)
    if term.kind == "br_cond":
        _cond, true_target, false_target = term.data
        return (true_target, false_target)
    if term.kind == "switch":
        _value_type, _value, default_target, cases = term.data
        return (default_target, *(target for _case_value, target in cases))
    return ()


def _stable_block_name_key(text: str) -> int:
    """Return a small deterministic integer key for a basic-block name."""
    modulus = 1099511627776
    value = 0
    index = 0
    while index < len(text):
        value = (value * 131 + ord(text[index])) % modulus
        index += 1
    return value


def _block_index_for_name(
    blocks: list[ParsedBlock],
    indices_by_key: dict[int, list[int]],
    name: str,
) -> int:
    for index in indices_by_key.get(_stable_block_name_key(name), []):
        if blocks[index].name == name:
            return index
    return -1


def _block_branches_to(block: ParsedBlock, target_name: str) -> bool:
    """Return whether the canonicalized terminator still owns this CFG edge."""
    for successor in _terminator_successors(block.terminator):
        if successor == target_name:
            return True
    return False


def _indexed_phi_edge_survives(
    blocks: list[ParsedBlock],
    indices_by_key: dict[int, list[int]],
    reachable_indices: set[int],
    predecessor_name: str,
    target_name: str,
) -> bool:
    predecessor_index = _block_index_for_name(
        blocks, indices_by_key, predecessor_name
    )
    if predecessor_index not in reachable_indices:
        return False
    return _block_branches_to(blocks[predecessor_index], target_name)


def _filter_reachable_blocks(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    if not blocks:
        return blocks

    # Native bootstrap can produce equal text values whose cached hashes no
    # longer agree.  Reachability must therefore not key dicts or sets by the
    # block-name strings themselves.  A deterministic integer key keeps the
    # hot path O(B + E); collision buckets preserve exact string equality.
    indices_by_key: dict[int, list[int]] = {}
    for index, block in enumerate(blocks):
        key = _stable_block_name_key(block.name)
        bucket = indices_by_key.get(key)
        if bucket is None:
            bucket = []
            indices_by_key[key] = bucket
        bucket.append(index)

    reachable_indices: set[int] = set()
    worklist = [0]
    while worklist:
        block_index = worklist.pop()
        if block_index in reachable_indices:
            continue
        reachable_indices.add(block_index)
        block = blocks[block_index]
        for successor in _terminator_successors(block.terminator):
            successor_index = _block_index_for_name(blocks, indices_by_key, successor)
            if successor_index >= 0 and successor_index not in reachable_indices:
                worklist.append(successor_index)

    filtered = [
        block for index, block in enumerate(blocks) if index in reachable_indices
    ]
    for block in filtered:
        if not block.phis:
            continue
        block.phis = [
            PhiInstr(
                dest=phi.dest,
                type=phi.type,
                incoming=tuple(
                    incoming
                    for incoming in phi.incoming
                    if _indexed_phi_edge_survives(
                        blocks,
                        indices_by_key,
                        reachable_indices,
                        incoming.label,
                        block.name,
                    )
                ),
            )
            for phi in block.phis
        ]
    return filtered


def _blocks_contain_name_linear(blocks: list[ParsedBlock], name: str) -> bool:
    for block in blocks:
        if block.name == name:
            return True
    return False


def _block_for_name_linear(
    blocks: list[ParsedBlock],
    name: str,
) -> ParsedBlock | None:
    for block in blocks:
        if block.name == name:
            return block
    return None


def _name_in_list_linear(names: list[str], name: str) -> bool:
    for item in names:
        if item == name:
            return True
    return False


def _linear_phi_edge_survives(
    blocks: list[ParsedBlock],
    reachable_names: list[str],
    predecessor_name: str,
    target_name: str,
) -> bool:
    if not _name_in_list_linear(reachable_names, predecessor_name):
        return False
    predecessor = _block_for_name_linear(blocks, predecessor_name)
    if predecessor is None:
        return False
    return _block_branches_to(predecessor, target_name)


def _filter_reachable_blocks_linear(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """Recompute reachability without native dict/set string-key operations."""
    if not blocks:
        return blocks
    reachable: list[str] = []
    worklist = [blocks[0].name]
    while worklist:
        name = worklist.pop()
        if _name_in_list_linear(reachable, name):
            continue
        block = _block_for_name_linear(blocks, name)
        if block is None:
            continue
        reachable.append(name)
        for successor in _terminator_successors(block.terminator):
            if not _name_in_list_linear(reachable, successor):
                worklist.append(successor)

    filtered = [
        block for block in blocks if _name_in_list_linear(reachable, block.name)
    ]
    for block in filtered:
        if not block.phis:
            continue
        block.phis = [
            PhiInstr(
                dest=phi.dest,
                type=phi.type,
                incoming=tuple(
                    incoming
                    for incoming in phi.incoming
                    if _linear_phi_edge_survives(
                        blocks,
                        reachable,
                        incoming.label,
                        block.name,
                    )
                ),
            )
            for phi in block.phis
        ]
    return filtered


def _filtered_blocks_drop_referenced_target(
    blocks: list[ParsedBlock],
    filtered: list[ParsedBlock],
) -> bool:
    for block in filtered:
        for successor in _terminator_successors(block.terminator):
            if _blocks_contain_name_linear(filtered, successor):
                continue
            if _blocks_contain_name_linear(blocks, successor):
                return True
    return False


_EMPTY_BLOCK_LINES: tuple = ()


def _parse_block(function_name: str, block: ParsedBlock) -> None:
    lines = list(block.raw_lines)
    while lines and _PHI_RE.match(lines[0]):
        phi_match = _PHI_RE.match(lines.pop(0))
        assert phi_match is not None
        type_text, incoming_text = _extract_leading_type_token(phi_match.group("body"))
        incoming_entries = []
        for item in _parse_phi_incoming_entries(incoming_text):
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
                type=_parse_type(type_text),
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

    for idx, line in enumerate(instruction_lines):
        if _UNREACHABLE_RE.match(line):
            instruction_lines = instruction_lines[:idx]
            terminator_line = line
            break

    for line in instruction_lines:
        block.instructions.append(_parse_instruction(function_name, block.name, line))
    block.terminator = _parse_terminator(function_name, block.name, terminator_line)
    # Release the source lines: this is their only consumer, and every later
    # phase reads the parsed instruction arena instead.  Holding them kept one
    # Python str per IR line alive for the whole emit -- 423698 of them for a
    # single 43 MB module -- which is pure duplication of text the emitter has
    # already finished with.
    block.raw_lines = _EMPTY_BLOCK_LINES


def _parse_binop_instruction(line: str) -> ParsedInstr | None:
    if "=" not in line:
        return None
    dest_text, rest = line.split("=", 1)
    rest = rest.strip()
    pieces = rest.split(None, 1)
    if len(pieces) != 2:
        return None
    op = pieces[0]
    if op not in {
        "add",
        "sub",
        "mul",
        "sdiv",
        "udiv",
        "srem",
        "urem",
        "and",
        "or",
        "xor",
        "shl",
        "lshr",
        "ashr",
    }:
        return None
    rest = pieces[1].strip()
    arithmetic_flags: list[str] = []
    while True:
        try:
            type_text, remainder = _extract_leading_type_token(rest)
            value_type = _parse_type(type_text)
            break
        except BackendUnavailable:
            attr_pieces = rest.split(None, 1)
            if len(attr_pieces) != 2:
                return None
            arithmetic_flags.append(attr_pieces[0])
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
        arithmetic_flags=tuple(arithmetic_flags),
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
    if cond not in {
        "oeq",
        "one",
        "ogt",
        "oge",
        "olt",
        "ole",
        "ord",
        "ueq",
        "une",
        "ugt",
        "uge",
        "ult",
        "ule",
        "uno",
    }:
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


def _parse_insertvalue_instruction(
    function_name: str, block_name: str, line: str
) -> ParsedInstr | None:
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


def _parse_extractvalue_instruction(
    function_name: str, block_name: str, line: str
) -> ParsedInstr | None:
    if "= extractvalue " not in line:
        return None
    dest_text, rest = line.split("= extractvalue ", 1)
    pieces = split_top_level(rest.strip())
    if len(pieces) < 2:
        raise BackendUnavailable(
            f"self backend malformed extractvalue in {function_name!r}/{block_name!r}: {line}"
        )
    aggregate_type_text, aggregate_value_text = _extract_leading_type_token(pieces[0])
    if not aggregate_value_text:
        raise BackendUnavailable(
            f"self backend malformed extractvalue operand in {function_name!r}/{block_name!r}: {line}"
        )
    aggregate_type = _parse_type(aggregate_type_text)
    indices = tuple(_parse_extractvalue_indices(",".join(pieces[1:])))
    result_type, offset = aggregate_member_info(aggregate_type, indices)
    return ParsedInstr(
        "extractvalue",
        (
            decode_ssa_name(dest_text.strip()),
            aggregate_type,
            decode_value_token(aggregate_value_text),
            indices,
            result_type,
            offset,
        ),
    )


def _call_instr_from_parts(
    function_name: str,
    block_name: str,
    line: str,
    dest: str | None,
    ret_text: str,
    sig_text: str | None,
    callee_token: str,
    args_text: str,
) -> ParsedInstr:
    ret_type = _parse_type(ret_text)
    fixed_arg_count, is_vararg_call = _parse_call_signature(sig_text)
    args, arg_alignments = _parse_call_args(function_name, args_text)
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
            (
                decode_global_name(callee_token)
                if callee_token.startswith("@")
                else decode_ssa_name(callee_token)
            ),
            callee_token.startswith("%"),
            tuple(args),
            fixed_arg_count,
            is_vararg_call,
            arg_alignments,
        ),
    )


def _parse_call_instruction(
    function_name: str, block_name: str, line: str
) -> ParsedInstr | None:
    is_call_shape = (
        line.startswith("call ")
        or line.startswith("tail call ")
        or " = call " in line
        or " = tail call " in line
    )
    if not is_call_shape:
        return None

    if match := _CALL_RE.match(line):
        return _call_instr_from_parts(
            function_name,
            block_name,
            line,
            match.group("dest"),
            match.group("ret"),
            match.group("sig"),
            match.group("callee"),
            match.group("args"),
        )

    dest: str | None = None
    rest = line.strip()
    if " = " in rest:
        dest_text, rest = rest.split(" = ", 1)
        dest = dest_text.strip()
    if rest.startswith("tail call "):
        rest = rest[len("tail call ") :].strip()
    elif rest.startswith("call "):
        rest = rest[len("call ") :].strip()
    else:
        raise BackendUnavailable(
            f"self backend malformed call in {function_name!r}/{block_name!r}: {line}"
        )

    while True:
        try:
            ret_text, rest_after_ret = _extract_leading_type_token(rest)
            _parse_type(ret_text)
            break
        except BackendUnavailable:
            attr_parts = rest.split(None, 1)
            if len(attr_parts) != 2:
                raise BackendUnavailable(
                    f"self backend malformed call in {function_name!r}/{block_name!r}: {line}"
                )
            rest = attr_parts[1].strip()

    sig_text: str | None = None
    rest = rest_after_ret.strip()
    if rest.startswith("("):
        sig_close = _find_matching_paren(rest, 0)
        sig_text = rest[: sig_close + 1]
        rest = rest[sig_close + 1 :].strip()

    callee_match = re.match(rf"(?P<callee>{_VALUE_REF_TOKEN})\(", rest)
    if callee_match is None:
        raise BackendUnavailable(
            f"self backend malformed call in {function_name!r}/{block_name!r}: {line}"
        )
    args_open = callee_match.end() - 1
    args_close = _find_matching_paren(rest, args_open)
    return _call_instr_from_parts(
        function_name,
        block_name,
        line,
        dest,
        ret_text,
        sig_text,
        callee_match.group("callee"),
        rest[args_open + 1 : args_close],
    )


def _parse_instruction(function_name: str, block_name: str, line: str) -> ParsedInstr:
    if match := _ALLOCA_RE.match(line):
        return ParsedInstr(
            "alloca",
            (decode_ssa_name(match.group("dest")), _parse_type(match.group("type"))),
        )
    if "= alloca " in line:
        dest_text, rest = line.split("= alloca ", 1)
        type_text, _tail = _extract_leading_type_token(rest)
        return ParsedInstr(
            "alloca", (decode_ssa_name(dest_text.strip()), _parse_type(type_text))
        )
    if line.startswith("store atomic "):
        if match := _STORE_ATOMIC_RE.match(line):
            return ParsedInstr(
                "store_atomic",
                (
                    _parse_type(match.group("val_type")),
                    decode_value_token(match.group("value")),
                    _parse_type(match.group("ptr_type")),
                    decode_value_token(match.group("ptr")),
                    match.group("ordering"),
                ),
            )
        raise BackendUnavailable(
            f"self backend malformed atomic store in {function_name!r}/{block_name!r}: {line}"
        )
    if "= load atomic " in line:
        if match := _LOAD_ATOMIC_RE.match(line):
            return ParsedInstr(
                "load_atomic",
                (
                    decode_ssa_name(match.group("dest").strip()),
                    _parse_type(match.group("val_type")),
                    _parse_type(match.group("ptr_type")),
                    decode_value_token(match.group("ptr")),
                    match.group("ordering"),
                ),
            )
        raise BackendUnavailable(
            f"self backend malformed atomic load in {function_name!r}/{block_name!r}: {line}"
        )
    if "= atomicrmw " in line:
        if match := _ATOMICRMW_RE.match(line):
            return ParsedInstr(
                "atomicrmw",
                (
                    decode_ssa_name(match.group("dest").strip()),
                    match.group("op"),
                    _parse_type(match.group("ptr_type")),
                    decode_value_token(match.group("ptr")),
                    _parse_type(match.group("val_type")),
                    decode_value_token(match.group("value")),
                    match.group("ordering"),
                ),
            )
        raise BackendUnavailable(
            f"self backend atomicrmw shape not supported in {function_name!r}/{block_name!r}: {line}"
        )
    if "= cmpxchg " in line:
        if match := _CMPXCHG_RE.match(line):
            value_type_text = match.group("expected_type")
            if value_type_text != match.group("desired_type"):
                raise BackendUnavailable(
                    f"self backend cmpxchg operand types disagree in {function_name!r}/{block_name!r}: {line}"
                )
            value_type = _parse_type(value_type_text)
            pair_type = _parse_type("{ " + value_type_text + ", i1 }")
            return ParsedInstr(
                "cmpxchg",
                (
                    decode_ssa_name(match.group("dest").strip()),
                    pair_type,
                    _parse_type(match.group("ptr_type")),
                    decode_value_token(match.group("ptr")),
                    value_type,
                    decode_value_token(match.group("expected")),
                    decode_value_token(match.group("desired")),
                    match.group("success"),
                    match.group("failure"),
                ),
            )
        raise BackendUnavailable(
            f"self backend cmpxchg shape not supported in {function_name!r}/{block_name!r}: {line}"
        )
    if line.startswith("fence "):
        if match := _FENCE_RE.match(line):
            return ParsedInstr("fence", (match.group("ordering"),))
        raise BackendUnavailable(
            f"self backend fence shape not supported in {function_name!r}/{block_name!r}: {line}"
        )
    if " asm " in line and "= call " in line:
        if match := _SYSCALL6_ASM_RE.match(line):
            arg_values = []
            for piece in split_top_level(match.group("args")):
                arg_type_text, arg_value_text = _extract_leading_type_token(
                    piece.strip()
                )
                if arg_type_text != "i64" or not arg_value_text:
                    raise BackendUnavailable(
                        f"self backend syscall6 argument must be i64 in {function_name!r}/{block_name!r}: {line}"
                    )
                arg_values.append(decode_value_token(arg_value_text))
            if len(arg_values) != 7:
                raise BackendUnavailable(
                    f"self backend syscall6 expects 7 arguments in {function_name!r}/{block_name!r}: {line}"
                )
            return ParsedInstr(
                "syscall6",
                (decode_ssa_name(match.group("dest").strip()), tuple(arg_values)),
            )
        raise BackendUnavailable(
            f"self backend inline asm shape not supported in {function_name!r}/{block_name!r}: {line}"
        )
    if line.startswith("store "):
        raw_rest = line[len("store ") :]
        is_volatile = raw_rest.strip().startswith("volatile ")
        rest = _strip_volatile_memory_op_prefix(raw_rest)
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
            is_volatile,
        )
    if "= load " in line:
        dest_text, rest = line.split("= load ", 1)
        is_volatile = rest.strip().startswith("volatile ")
        rest = _strip_volatile_memory_op_prefix(rest)
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
            is_volatile,
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
                if len(pieces) != 2 or not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*", pieces[0]
                ):
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
    if parsed := _parse_call_instruction(function_name, block_name, line):
        return parsed
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
            if len(pieces) != 2 or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", pieces[0]
            ):
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
    if parsed := _parse_extractvalue_instruction(function_name, block_name, line):
        return parsed
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
    raise BackendUnavailable(
        f"self backend does not support instruction in {function_name!r}/{block_name!r}: {line}"
    )


def _parse_call_arg_alignment(value_text: str) -> int:
    # LLVM call-argument attributes are whitespace-delimited here.  Parse the
    # finite ``align N`` pair directly: the previous regex used a lookahead,
    # which host Python accepted but pcc's strict no-libpython regex engine
    # correctly rejected while a compiled pcc was emitting its own output.
    pieces = value_text.split()
    align_index = -1
    for index, piece in enumerate(pieces):
        if piece == "align":
            align_index = index
            break
    if align_index < 0:
        return 0
    if align_index + 1 >= len(pieces):
        raise BackendUnavailable(
            "self backend call argument has an align attribute without a value"
        )
    try:
        alignment = int(pieces[align_index + 1])
    except ValueError as exc:
        raise BackendUnavailable(
            "self backend call argument has a non-integer alignment"
        ) from exc
    if alignment <= 0 or alignment & (alignment - 1):
        raise BackendUnavailable(
            f"self backend call argument has invalid alignment {alignment}"
        )
    return alignment


def _parse_call_args(
    function_name: str, args_text: str
) -> tuple[list[tuple[TypeDesc, str]], tuple[int, ...]]:
    text = (args_text or "").strip()
    if not text:
        return [], ()
    args: list[tuple[TypeDesc, str]] = []
    alignments: list[int] = []
    chunks = [chunk.strip() for chunk in split_top_level(text) if chunk.strip()]
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
        alignments.append(_parse_call_arg_alignment(value_text))
        arg_type = _parse_type(type_text)
        arg_value = decode_value_token(value_text)
        # Keep the internal call ABI canonical: integer operands are numeric
        # strings throughout the emitters.  LLVM permits the aliases
        # ``true``/``false`` for i1, but preserving them here made call parsing
        # depend on every downstream intrinsic/materializer remembering both
        # spellings (and diverged from calls emitted with i1 0/1).
        if arg_type.is_int and arg_type.width == 1:
            if arg_value == "false":
                arg_value = "0"
            elif arg_value == "true":
                arg_value = "1"
        args.append((arg_type, arg_value))
    return args, tuple(alignments)


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
        raise BackendUnavailable(
            "self backend extractvalue requires at least one index"
        )
    return indices


def _parse_call_signature(sig_text: str | None) -> tuple[int, bool]:
    text = (sig_text or "").strip()
    cached = _CALL_SIGNATURE_CACHE.get(text)
    if cached is not None:
        return cached
    if not text:
        result = (0, False)
        _CALL_SIGNATURE_CACHE[text] = result
        return result
    if not (text.startswith("(") and text.endswith(")")):
        raise BackendUnavailable(
            f"self backend malformed explicit call signature {text!r}"
        )
    inner = text[1:-1].strip()
    if not inner:
        result = (0, False)
        _CALL_SIGNATURE_CACHE[text] = result
        return result
    fixed, is_vararg = _parse_ir_type_list(inner, allow_vararg=True)
    result = (len(fixed), is_vararg)
    _CALL_SIGNATURE_CACHE[text] = result
    return result


def _parse_gep_indices(indices_text: str) -> list[tuple[TypeDesc, str]]:
    indices: list[tuple[TypeDesc, str]] = []
    if not _has_split_nesting_markers(indices_text):
        for chunk in indices_text.split(","):
            piece = chunk.strip()
            if not piece:
                continue
            type_text, sep, value_text = piece.partition(" ")
            if not sep or not value_text.strip():
                raise BackendUnavailable(
                    f"self backend could not decode getelementptr index {piece!r}"
                )
            indices.append((_parse_type(type_text), decode_value_token(value_text)))
        return indices
    for chunk in indices_text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        match = _INDEX_RE.match(piece)
        if match is None:
            raise BackendUnavailable(
                f"self backend could not decode getelementptr index {piece!r}"
            )
        indices.append(
            (_parse_type(match.group("type")), decode_value_token(match.group("value")))
        )
    return indices


def _constant_gep_offset(
    base_type: TypeDesc, indices: tuple[tuple[TypeDesc, str], ...]
) -> int:
    if not indices:
        raise BackendUnavailable(
            "self backend constant getelementptr requires at least one index"
        )
    first_index = const_int_from_value(indices[0][1])
    if first_index is None:
        raise BackendUnavailable(
            "self backend constant getelementptr requires constant first index"
        )
    offset = first_index * base_type.slot_size
    current = base_type
    for _index_type, index_value in indices[1:]:
        const_index = const_int_from_value(index_value)
        if const_index is None:
            raise BackendUnavailable(
                "self backend constant getelementptr requires constant indices"
            )
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
        if cond_text in {"1", "true"}:
            return ParsedInstr("br", (true_label,))
        if cond_text in {"0", "false", "undef", "poison"}:
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
    if line.startswith("ret "):
        ret_type_text, value_text = _extract_leading_type_token(
            line[len("ret ") :].strip()
        )
        if value_text:
            return ParsedInstr(
                "ret",
                (_parse_type(ret_type_text), decode_value_token(value_text)),
            )
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
            case_pos = 0
            while case_pos < len(cases_text):
                case_match = _SWITCH_CASE_RE.search(cases_text, case_pos)
                if (
                    case_match is None
                    or cases_text[case_pos : case_match.start()].strip()
                ):
                    raise BackendUnavailable(
                        f"self backend could not decode switch table in {function_name!r}/{block_name!r}: {line}"
                    )
                case_type = _parse_type(case_match.group("type"))
                if case_type.describe() != value_type.describe():
                    raise BackendUnavailable(
                        "self backend requires switch case values to use the switch operand type"
                    )
                cases.append(
                    (
                        int(case_match.group("value")),
                        decode_label_ref(case_match.group("label")),
                    )
                )
                case_pos = case_match.end()
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
