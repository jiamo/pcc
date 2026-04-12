from __future__ import annotations

"""Asm-first self backend bootstrap for AArch64 Darwin.

This backend consumes current LLVM IR text as a bootstrap input and lowers a
bounded but growing truthful subset to native AArch64 Darwin assembly.

Supported slice today:
- scalar integer types (`i1`, `i8`, `i16`, `i32`, `i64`)
- pointer scalars (`T*`, including pointer args/returns/local slots)
- `void` functions / calls / returns
- local `alloca`, `load`, `store`
- direct calls
- integer arithmetic / compares / branches / phi / simple loops
- scalar casts: `zext`, `sext`, `trunc`, `bitcast`, `ptrtoint`, `inttoptr`

Unsupported shapes still raise ``BackendUnavailable`` instead of guessing.
"""

from dataclasses import dataclass, field
import re
import struct

from . import BackendUnavailable


_BASE_TYPE_TOKEN = (
    r'(?:void|ptr|float|double|i\d+|%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)|'
    r'\[\d+ x (?:void|ptr|float|double|i\d+|%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))(?:\*+)?\])'
)
_TYPE_TOKEN = rf'{_BASE_TYPE_TOKEN}(?:\*+)?'
_VALUE_REF_TOKEN = r'(?:%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)|@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))'
_TARGET_TRIPLE_RE = re.compile(r'^target triple = "([^"]+)"$', re.MULTILINE)
_NAMED_TYPEDEF_RE = re.compile(
    r'^(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = type \{(?P<fields>[^}]*)\}$',
    re.MULTILINE,
)
_GLOBAL_SCALAR_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf'(?P<linkage>(?:internal|private)\s+)?(?P<kind>global|constant) '
    rf'(?P<type>(?:i\d+)(?:\*+)?) (?P<init>(?:null|-?\d+|@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)))$',
    re.MULTILINE,
)
_GLOBAL_ARRAY_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf'(?P<linkage>(?:internal|private)\s+)?(?P<kind>global|constant) '
    rf'(?P<type>\[\d+ x i\d+\]) \[(?P<items>.+)\]$',
    re.MULTILINE,
)
_GLOBAL_PTR_GEP_RE = re.compile(
    rf'^(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)) = '
    rf'(?P<linkage>(?:internal|private)\s+)?global (?P<type>{_TYPE_TOKEN}) '
    r'getelementptr(?: inbounds)? \((?P<base_type>.+?),\s+(?P<ptr_type>.+?)\s+'
    r'(?P<base>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*)),\s+i64\s+0,\s+i64\s+0\)$',
    re.MULTILINE,
)
_FUNCTION_RE = re.compile(
    rf'^define\s+(?P<prefix>.*?)(?P<ret>{_TYPE_TOKEN})\s+'
    r'(?P<name>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))'
    r'\((?P<args>[^)]*)\)[^\n]*\{\n(?P<body>.*?)^\}',
    re.MULTILINE | re.DOTALL,
)
_SSA_NAME_RE = re.compile(r'^%(?:"([^"]+)"|([A-Za-z_.$][\w.$-]*))$')
_GLOBAL_NAME_RE = re.compile(r'^@(?:"([^"]+)"|([A-Za-z_.$][\w.$-]*))$')
_LABEL_REF_RE = re.compile(r'^(?:label\s+)?%(?:"([^"]+)"|([A-Za-z_.$][\w.$-]*))$')
_PLAIN_LABEL_RE = re.compile(r'^([A-Za-z_.$][\w.$-]*|\.[0-9A-Za-z_.$-]+):$')
_SYMBOL_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_INT_RE = re.compile(r'^-?\d+$')
_HEX_RE = re.compile(r'^0x[0-9A-Fa-f]+$')
_ARG_RE = re.compile(
    rf'^(?P<type>{_TYPE_TOKEN})(?:\s+[A-Za-z_][A-Za-z0-9_.$-]*)*\s+'
    r'(?P<name>%(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))$'
)
_CALL_ARG_RE = re.compile(rf'^(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+)$')
_ALLOCA_RE = re.compile(rf'^(?P<dest>%.*)\s*=\s*alloca\s+(?P<type>{_TYPE_TOKEN})(?:\s*,.*)?$')
_STORE_RE = re.compile(
    rf'^store\s+(?P<val_type>{_TYPE_TOKEN})\s+(?P<value>.+?),\s+'
    rf'(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$'
)
_LOAD_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*load\s+(?P<val_type>{_TYPE_TOKEN}),\s+'
    rf'(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?:,\s+align\s+\d+)?$'
)
_BINOP_RE = re.compile(
    r'^(?P<dest>%.*)\s*=\s*'
    r'(?P<op>add|sub|mul|sdiv|udiv|srem|urem|and|or|xor|shl|lshr|ashr)\s+'
    r'(?P<type>i\d+)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$'
)
_FBINOP_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*'
    r'(?P<op>fadd|fsub|fmul|fdiv)(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+'
    r'(?P<type>float|double)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$'
)
_ICMP_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*icmp\s+'
    r'(?P<cond>eq|ne|slt|sle|sgt|sge|ult|ule|ugt|uge)\s+'
    rf'(?P<type>(?:i\d+)(?:\*+)?)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$'
)
_FCMP_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*fcmp\s+'
    r'(?P<cond>oeq|one|ogt|oge|olt|ole)\s+'
    r'(?P<type>float|double)\s+(?P<lhs>.+?),\s+(?P<rhs>.+)$'
)
_CAST_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*'
    r'(?P<op>zext|sext|trunc|bitcast|ptrtoint|inttoptr|sitofp|uitofp|fptosi|fptoui|fpext|fptrunc)\s+'
    rf'(?P<src_type>{_TYPE_TOKEN})\s+(?P<value>.+?)\s+to\s+(?P<dst_type>{_TYPE_TOKEN})$'
)
_CALL_RE = re.compile(
    rf'^(?:(?P<dest>%.*)\s*=\s*)?(?:tail\s+)?call\s+(?P<ret>{_TYPE_TOKEN})(?:\s*\([^)]*\))?\s+'
    r'(?P<callee>@(?:"[^"]+"|[A-Za-z_.$][\w.$-]*))\((?P<args>.*)\)$'
)
_GEP_RE = re.compile(
    rf'^(?P<dest>%.*)\s*=\s*getelementptr(?:\s+inbounds)?\s+(?P<base_type>{_TYPE_TOKEN}),\s+'
    rf'(?P<ptr_type>{_TYPE_TOKEN})\s+(?P<ptr>{_VALUE_REF_TOKEN})(?P<indices>(?:,\s+i\d+\s+.+)+)$'
)
_PHI_RE = re.compile(rf'^(?P<dest>%.*)\s*=\s*phi\s+(?P<type>{_TYPE_TOKEN})\s+(?P<incoming>.+)$')
_BR_COND_RE = re.compile(
    r'^br\s+i1\s+(?P<cond>%.*?),\s+label\s+(?P<true>.*?),\s+label\s+(?P<false>.+)$'
)
_BR_RE = re.compile(r'^br\s+label\s+(?P<target>.+)$')
_RET_VOID_RE = re.compile(r'^ret\s+void$')
_RET_RE = re.compile(rf'^ret\s+(?P<type>{_TYPE_TOKEN})\s+(?P<value>.+)$')
_PHI_INCOMING_RE = re.compile(r'\[(.*?)\]')
_INDEX_RE = re.compile(r'^(?P<type>i\d+)\s+(?P<value>.+)$')


_NAMED_TYPES: dict[str, "TypeDesc"] = {}


@dataclass(frozen=True)
class TypeDesc:
    kind: str
    width: int = 0
    pointee: "TypeDesc | None" = None
    count: int = 0
    elem: "TypeDesc | None" = None
    name: str = ""
    fields: tuple["TypeDesc", ...] = ()

    @property
    def is_void(self) -> bool:
        return self.kind == "void"

    @property
    def is_int(self) -> bool:
        return self.kind == "int"

    @property
    def is_fp(self) -> bool:
        return self.kind == "fp"

    @property
    def is_ptr(self) -> bool:
        return self.kind == "ptr"

    @property
    def is_array(self) -> bool:
        return self.kind == "array"

    @property
    def is_struct(self) -> bool:
        return self.kind == "struct"

    @property
    def bits(self) -> int:
        if self.is_ptr:
            return 64
        if self.is_int or self.is_fp:
            return self.width
        return 0

    @property
    def slot_size(self) -> int:
        if self.is_void:
            return 0
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        if self.is_array:
            assert self.elem is not None
            stride = _align_to(self.elem.slot_size, self.elem.align)
            return stride * self.count
        if self.is_struct:
            offset = 0
            max_align = 1
            for field in self.fields:
                offset = _align_to(offset, field.align)
                offset += field.slot_size
                max_align = max(max_align, field.align)
            return _align_to(offset, max_align)
        if self.width <= 8:
            return 1
        if self.width <= 16:
            return 2
        return 4

    @property
    def value_slot_size(self) -> int:
        if self.is_void:
            return 0
        if self.is_array or self.is_struct:
            return self.slot_size
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        return 4

    @property
    def align(self) -> int:
        if self.is_void:
            return 1
        if self.is_array:
            assert self.elem is not None
            return self.elem.align
        if self.is_struct:
            return max((field.align for field in self.fields), default=1)
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        if self.width <= 8:
            return 1
        if self.width <= 16:
            return 2
        return 4

    @property
    def value_align(self) -> int:
        if self.is_void:
            return 1
        if self.is_array or self.is_struct:
            return self.align
        if self.is_fp:
            return 4 if self.width <= 32 else 8
        if self.is_ptr or self.width > 32:
            return 8
        return 4

    @property
    def reg_prefix(self) -> str:
        if self.is_array or self.is_struct:
            chunks = _aggregate_reg_chunks(self)
            if len(chunks) == 1:
                return "x" if chunks[0] > 4 else "w"
            raise BackendUnavailable(
                f"self backend cannot use aggregate type in a register directly: {self.describe()}"
            )
        if self.is_fp:
            return "s" if self.width <= 32 else "d"
        if self.is_ptr or self.width > 32:
            return "x"
        return "w"

    def ptr(self) -> "TypeDesc":
        return TypeDesc("ptr", pointee=self)

    def describe(self) -> str:
        if self.is_void:
            return "void"
        if self.is_int:
            return f"i{self.width}"
        if self.is_fp:
            return "float" if self.width <= 32 else "double"
        if self.is_array:
            assert self.elem is not None
            return f"[{self.count} x {self.elem.describe()}]"
        if self.is_struct:
            return self.name or "<anon-struct>"
        assert self.pointee is not None
        return self.pointee.describe() + "*"

    def field_offset(self, index: int) -> int:
        if not self.is_struct:
            raise BackendUnavailable(f"field_offset requested on non-struct {self.describe()}")
        if index < 0 or index >= len(self.fields):
            raise BackendUnavailable(f"struct field index {index} out of range for {self.describe()}")
        offset = 0
        for field_index, field in enumerate(self.fields):
            offset = _align_to(offset, field.align)
            if field_index == index:
                return offset
            offset += field.slot_size
        raise BackendUnavailable(f"struct field index {index} out of range for {self.describe()}")

    def field_type(self, index: int) -> "TypeDesc":
        if not self.is_struct:
            raise BackendUnavailable(f"field_type requested on non-struct {self.describe()}")
        return self.fields[index]


@dataclass(frozen=True)
class ArgInfo:
    name: str
    type: TypeDesc


@dataclass(frozen=True)
class PhiIncoming:
    value: str
    label: str


@dataclass(frozen=True)
class PhiInstr:
    dest: str
    type: TypeDesc
    incoming: tuple[PhiIncoming, ...]


@dataclass(frozen=True)
class ParsedInstr:
    kind: str
    data: tuple


@dataclass
class ParsedBlock:
    name: str
    raw_lines: list[str] = field(default_factory=list)
    phis: list[PhiInstr] = field(default_factory=list)
    instructions: list[ParsedInstr] = field(default_factory=list)
    terminator: ParsedInstr | None = None


@dataclass(frozen=True)
class SlotInfo:
    offset: int
    type: TypeDesc


@dataclass(frozen=True)
class AllocaInfo:
    offset: int
    allocated_type: TypeDesc


@dataclass(frozen=True)
class GlobalDef:
    name: str
    type: TypeDesc
    initializer: str
    is_constant: bool
    is_internal: bool


@dataclass
class ParsedFunction:
    name: str
    ret_type: TypeDesc
    args: list[ArgInfo]
    is_global: bool
    blocks: list[ParsedBlock]
    value_types: dict[str, TypeDesc] = field(default_factory=dict)
    value_slots: dict[str, SlotInfo] = field(default_factory=dict)
    alloca_slots: dict[str, AllocaInfo] = field(default_factory=dict)
    block_map: dict[str, ParsedBlock] = field(default_factory=dict)
    frame_size: int = 0


I1 = TypeDesc("int", 1)


def emit_aarch64_darwin_asm(ir_text: str) -> str:
    triple = _parse_target_triple(ir_text)
    if not _is_supported_triple(triple):
        raise BackendUnavailable(
            f"self backend asm MVP only supports AArch64 Darwin, got {triple!r}"
        )

    _parse_named_types(ir_text)
    globals_ = _parse_globals(ir_text)
    functions = _parse_functions(ir_text)
    if not functions:
        raise BackendUnavailable("self backend found no supported function definitions")

    lines = _emit_globals(globals_)
    lines.append(".section __TEXT,__text,regular,pure_instructions")
    for func in functions:
        lines.extend(_emit_function(func))
    lines.append(".subsections_via_symbols")
    return "\n".join(lines) + "\n"


def _parse_target_triple(ir_text: str) -> str:
    match = _TARGET_TRIPLE_RE.search(ir_text)
    if match is None:
        raise BackendUnavailable("self backend requires a target triple in LLVM IR text")
    return match.group(1)


def _is_supported_triple(triple: str) -> bool:
    triple = triple.lower()
    return (
        (triple.startswith("arm64-") or triple.startswith("aarch64-"))
        and "apple" in triple
        and "darwin" in triple
    )


def _parse_type(text: str) -> TypeDesc:
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
        base = TypeDesc("array", count=int(count_text), elem=_parse_type(elem_text.strip()))
    elif token.startswith("%"):
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
            _parse_type(chunk.strip())
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
    for match in _FUNCTION_RE.finditer(ir_text):
        prefix = (match.group("prefix") or "").strip().split()
        name = _decode_global_name(match.group("name"))
        _check_symbol_name(name)
        ret_type = _parse_type(match.group("ret"))
        args = _parse_arg_infos(name, match.group("args"))
        blocks = _parse_blocks(name, match.group("body"))
        func = ParsedFunction(
            name=name,
            ret_type=ret_type,
            args=args,
            is_global="internal" not in prefix,
            blocks=blocks,
        )
        func.block_map = {block.name: block for block in blocks}
        for arg in args:
            func.value_types[arg.name] = arg.type
        _assign_stack_slots(func)
        functions.append(func)
    return functions


def _parse_globals(ir_text: str) -> list[GlobalDef]:
    globals_: list[GlobalDef] = []
    for match in _GLOBAL_SCALAR_RE.finditer(ir_text):
        name = _decode_global_name(match.group("name"))
        _check_symbol_name(name)
        globals_.append(
            GlobalDef(
                name=name,
                type=_parse_type(match.group("type")),
                initializer=match.group("init").strip(),
                is_constant=match.group("kind") == "constant",
                is_internal=bool(match.group("linkage")),
            )
        )
    for match in _GLOBAL_ARRAY_RE.finditer(ir_text):
        name = _decode_global_name(match.group("name"))
        _check_symbol_name(name)
        globals_.append(
            GlobalDef(
                name=name,
                type=_parse_type(match.group("type")),
                initializer="[" + match.group("items").strip() + "]",
                is_constant=match.group("kind") == "constant",
                is_internal=bool(match.group("linkage")),
            )
        )
    for match in _GLOBAL_PTR_GEP_RE.finditer(ir_text):
        name = _decode_global_name(match.group("name"))
        _check_symbol_name(name)
        globals_.append(
            GlobalDef(
                name=name,
                type=_parse_type(match.group("type")),
                initializer=f"gep0:{_decode_global_name(match.group('base'))}",
                is_constant=False,
                is_internal=bool(match.group("linkage")),
            )
        )
    return globals_


def _parse_arg_infos(function_name: str, args_text: str) -> list[ArgInfo]:
    text = (args_text or "").strip()
    if not text:
        return []

    chunks = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    if len(chunks) > 8:
        raise BackendUnavailable(
            f"self backend MVP only supports up to 8 args in {function_name!r}"
        )

    args: list[ArgInfo] = []
    for chunk in chunks:
        match = _ARG_RE.match(chunk)
        if match is None:
            raise BackendUnavailable(
                f"self backend could not decode argument in {function_name!r}: {chunk}"
            )
        args.append(
            ArgInfo(
                name=_decode_ssa_name(match.group("name")),
                type=_parse_type(match.group("type")),
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
        for item in _PHI_INCOMING_RE.findall(phi_match.group("incoming")):
            value_text, label_text = item.split(",", 1)
            incoming_entries.append(
                PhiIncoming(
                    value=_decode_value_token(value_text.strip()),
                    label=_decode_label_ref(label_text.strip()),
                )
            )
        block.phis.append(
            PhiInstr(
                dest=_decode_ssa_name(phi_match.group("dest")),
                type=_parse_type(phi_match.group("type")),
                incoming=tuple(incoming_entries),
            )
        )

    if not lines:
        raise BackendUnavailable(
            f"self backend block {block.name!r} in {function_name!r} has no terminator"
        )

    terminator_line = lines.pop()
    for line in lines:
        block.instructions.append(_parse_instruction(function_name, block.name, line))
    block.terminator = _parse_terminator(function_name, block.name, terminator_line)


def _parse_instruction(function_name: str, block_name: str, line: str) -> ParsedInstr:
    if match := _ALLOCA_RE.match(line):
        return ParsedInstr(
            "alloca",
            (_decode_ssa_name(match.group("dest")), _parse_type(match.group("type"))),
        )
    if match := _STORE_RE.match(line):
        return ParsedInstr(
            "store",
            (
                _parse_type(match.group("val_type")),
                _decode_value_token(match.group("value")),
                _parse_type(match.group("ptr_type")),
                _decode_value_token(match.group("ptr")),
            ),
        )
    if match := _LOAD_RE.match(line):
        return ParsedInstr(
            "load",
            (
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("val_type")),
                _parse_type(match.group("ptr_type")),
                _decode_value_token(match.group("ptr")),
            ),
        )
    if match := _BINOP_RE.match(line):
        return ParsedInstr(
            "binop",
            (
                match.group("op"),
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                _decode_value_token(match.group("lhs")),
                _decode_value_token(match.group("rhs")),
            ),
        )
    if match := _FBINOP_RE.match(line):
        return ParsedInstr(
            "fbinop",
            (
                match.group("op"),
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                _decode_value_token(match.group("lhs")),
                _decode_value_token(match.group("rhs")),
            ),
        )
    if match := _ICMP_RE.match(line):
        return ParsedInstr(
            "icmp",
            (
                match.group("cond"),
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                _decode_value_token(match.group("lhs")),
                _decode_value_token(match.group("rhs")),
            ),
        )
    if match := _FCMP_RE.match(line):
        return ParsedInstr(
            "fcmp",
            (
                match.group("cond"),
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("type")),
                _decode_value_token(match.group("lhs")),
                _decode_value_token(match.group("rhs")),
            ),
        )
    if match := _CAST_RE.match(line):
        return ParsedInstr(
            "cast",
            (
                match.group("op"),
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("src_type")),
                _decode_value_token(match.group("value")),
                _parse_type(match.group("dst_type")),
            ),
        )
    if match := _CALL_RE.match(line):
        ret_type = _parse_type(match.group("ret"))
        dest = match.group("dest")
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
                None if dest is None else _decode_ssa_name(dest),
                ret_type,
                _decode_global_name(match.group("callee")),
                tuple(_parse_call_args(function_name, match.group("args"))),
            ),
        )
    if match := _GEP_RE.match(line):
        return ParsedInstr(
            "gep",
            (
                _decode_ssa_name(match.group("dest")),
                _parse_type(match.group("base_type")),
                _parse_type(match.group("ptr_type")),
                _decode_value_token(match.group("ptr")),
                tuple(_parse_gep_indices(match.group("indices"))),
            ),
        )

    raise BackendUnavailable(
        f"self backend does not support instruction in {function_name!r}/{block_name!r}: {line}"
    )


def _parse_call_args(function_name: str, args_text: str) -> list[tuple[TypeDesc, str]]:
    text = (args_text or "").strip()
    if not text:
        return []
    chunks = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    if len(chunks) > 8:
        raise BackendUnavailable(
            f"self backend MVP only supports up to 8 call args in {function_name!r}"
        )
    args: list[tuple[TypeDesc, str]] = []
    for chunk in chunks:
        match = _CALL_ARG_RE.match(chunk)
        if match is None:
            raise BackendUnavailable(
                f"self backend could not decode call arg in {function_name!r}: {chunk}"
            )
        args.append((_parse_type(match.group("type")), _decode_value_token(match.group("value"))))
    return args


def _parse_gep_indices(indices_text: str) -> list[tuple[TypeDesc, str]]:
    indices: list[tuple[TypeDesc, str]] = []
    for chunk in indices_text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        match = _INDEX_RE.match(piece)
        if match is None:
            raise BackendUnavailable(f"self backend could not decode getelementptr index {piece!r}")
        indices.append((_parse_type(match.group("type")), _decode_value_token(match.group("value"))))
    return indices


def _const_int_from_value(value: str) -> int | None:
    if _INT_RE.match(value):
        return int(value)
    return None


def _gep_result_type(base_type: TypeDesc, indices: tuple[tuple[TypeDesc, str], ...]) -> TypeDesc:
    if base_type.is_array:
        if len(indices) != 2:
            raise BackendUnavailable(
                f"self backend array getelementptr expects 2 indices, got {len(indices)}"
            )
        first = _const_int_from_value(indices[0][1])
        if first not in (0, None):
            raise BackendUnavailable(
                "self backend array getelementptr currently requires first index 0"
            )
        assert base_type.elem is not None
        return base_type.elem.ptr()
    if base_type.is_struct:
        if len(indices) != 2:
            raise BackendUnavailable(
                f"self backend struct getelementptr expects 2 indices, got {len(indices)}"
            )
        first = _const_int_from_value(indices[0][1])
        field_index = _const_int_from_value(indices[1][1])
        if first != 0 or field_index is None:
            raise BackendUnavailable(
                "self backend struct getelementptr currently requires indices [0, const-field]"
            )
        return base_type.field_type(field_index).ptr()
    return base_type.ptr()


def _parse_terminator(function_name: str, block_name: str, line: str) -> ParsedInstr:
    if match := _BR_COND_RE.match(line):
        return ParsedInstr(
            "br_cond",
            (
                _decode_ssa_name(match.group("cond")),
                _decode_label_ref(match.group("true").strip()),
                _decode_label_ref(match.group("false").strip()),
            ),
        )
    if match := _BR_RE.match(line):
        return ParsedInstr("br", (_decode_label_ref(match.group("target").strip()),))
    if _RET_VOID_RE.match(line):
        return ParsedInstr("ret_void", ())
    if match := _RET_RE.match(line):
        return ParsedInstr(
            "ret",
            (_parse_type(match.group("type")), _decode_value_token(match.group("value"))),
        )

    raise BackendUnavailable(
        f"self backend does not support terminator in {function_name!r}/{block_name!r}: {line}"
    )


def _assign_stack_slots(func: ParsedFunction) -> None:
    offset = 0

    def alloc(size: int, align: int) -> int:
        nonlocal offset
        offset = _align_to(offset, align)
        offset += size
        return offset

    for arg in func.args:
        if arg.type.is_void:
            continue
        func.value_slots[arg.name] = SlotInfo(alloc(arg.type.value_slot_size, arg.type.value_align), arg.type)

    for block in func.blocks:
        for phi in block.phis:
            func.value_types[phi.dest] = phi.type
            func.value_slots.setdefault(
                phi.dest,
                SlotInfo(alloc(phi.type.value_slot_size, phi.type.value_align), phi.type),
            )
        for instr in block.instructions:
            kind = instr.kind
            data = instr.data
            if kind == "alloca":
                name, allocated_type = data
                func.value_types[name] = allocated_type.ptr()
                func.alloca_slots.setdefault(
                    name,
                    AllocaInfo(alloc(allocated_type.slot_size, allocated_type.align), allocated_type),
                )
            elif kind == "load":
                dest, value_type, _ptr_type, _ptr = data
                func.value_types[dest] = value_type
                func.value_slots.setdefault(
                    dest,
                    SlotInfo(alloc(value_type.value_slot_size, value_type.value_align), value_type),
                )
            elif kind == "binop":
                _op, dest, value_type, _lhs, _rhs = data
                func.value_types[dest] = value_type
                func.value_slots.setdefault(
                    dest,
                    SlotInfo(alloc(value_type.value_slot_size, value_type.value_align), value_type),
                )
            elif kind == "fbinop":
                _op, dest, value_type, _lhs, _rhs = data
                func.value_types[dest] = value_type
                func.value_slots.setdefault(
                    dest,
                    SlotInfo(alloc(value_type.value_slot_size, value_type.value_align), value_type),
                )
            elif kind == "icmp":
                _cond, dest, _value_type, _lhs, _rhs = data
                func.value_types[dest] = I1
                func.value_slots.setdefault(dest, SlotInfo(alloc(4, 4), I1))
            elif kind == "fcmp":
                _cond, dest, _value_type, _lhs, _rhs = data
                func.value_types[dest] = I1
                func.value_slots.setdefault(dest, SlotInfo(alloc(4, 4), I1))
            elif kind == "cast":
                _op, dest, _src_type, _value, dst_type = data
                func.value_types[dest] = dst_type
                func.value_slots.setdefault(
                    dest,
                    SlotInfo(alloc(dst_type.value_slot_size, dst_type.value_align), dst_type),
                )
            elif kind == "gep":
                dest, base_type, _ptr_type, _ptr, indices = data
                result_type = _gep_result_type(base_type, indices)
                func.value_types[dest] = result_type
                func.value_slots.setdefault(
                    dest,
                    SlotInfo(alloc(result_type.value_slot_size, result_type.value_align), result_type),
                )
            elif kind == "call":
                dest, ret_type, _callee, _args = data
                if dest is not None:
                    func.value_types[dest] = ret_type
                    func.value_slots.setdefault(
                        dest,
                        SlotInfo(alloc(ret_type.value_slot_size, ret_type.value_align), ret_type),
                    )

    func.frame_size = _align_to(offset, 16)


def _emit_function(func: ParsedFunction) -> list[str]:
    symbol = _asm_symbol(func.name)
    lines = ["", ".p2align 2"]
    if func.is_global:
        lines.append(f".globl {symbol}")
    lines.append(f"{symbol}:")
    lines.extend(
        [
            "  stp x29, x30, [sp, #-16]!",
            "  mov x29, sp",
        ]
    )
    if func.frame_size:
        lines.append(f"  sub sp, sp, #{func.frame_size}")

    for arg, regs in zip(func.args, _assign_abi_arg_regs([arg.type for arg in func.args])):
        if not regs:
            continue
        lines.extend(_store_value_regs_to_slot(func.value_slots[arg.name], int(regs[0][1:])))

    for index, block in enumerate(func.blocks):
        if index == 0:
            lines.append(_block_label(func, block.name) + ":")
        else:
            lines.append("")
            lines.append(_block_label(func, block.name) + ":")
        for instr in block.instructions:
            lines.extend(_emit_instruction(func, block, instr))
        assert block.terminator is not None
        lines.extend(_emit_terminator(func, block, block.terminator))

    return lines


def _emit_instruction(func: ParsedFunction, block: ParsedBlock, instr: ParsedInstr) -> list[str]:
    kind = instr.kind
    data = instr.data

    if kind == "alloca":
        return []

    if kind == "store":
        value_type, value, _ptr_type, ptr_name = data
        if ptr_name in func.alloca_slots and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe():
            lines = _materialize_value(func, value, value_type, 9)
            lines.extend(_store_value_regs_to_slot(SlotInfo(func.alloca_slots[ptr_name].offset, value_type), 9))
            return lines
        lines = _materialize_pointer(func, ptr_name, 9)
        lines.extend(_materialize_value(func, value, value_type, 10))
        lines.extend(_store_value_to_address("x9", value_type, 10))
        return lines

    if kind == "load":
        dest, value_type, _ptr_type, ptr_name = data
        lines: list[str] = []
        if ptr_name in func.alloca_slots and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe():
            lines.extend(_load_slot_to_value_regs(SlotInfo(func.alloca_slots[ptr_name].offset, value_type), 10))
        else:
            lines.extend(_materialize_pointer(func, ptr_name, 9))
            lines.extend(_load_value_from_address("x9", value_type, 10))
        lines.extend(_store_value_regs_to_slot(func.value_slots[dest], 10))
        return lines

    if kind == "binop":
        op, dest, value_type, lhs, rhs = data
        lines = _materialize_value(func, lhs, value_type, 9)
        lines.extend(_materialize_value(func, rhs, value_type, 10))
        lines.extend(_emit_binop(op, value_type))
        lines.extend(_store_reg_to_slot(_reg_name(value_type, 11), func.value_slots[dest]))
        return lines

    if kind == "fbinop":
        op, dest, value_type, lhs, rhs = data
        lines = _materialize_value(func, lhs, value_type, 9)
        lines.extend(_materialize_value(func, rhs, value_type, 10))
        lines.extend(_emit_fbinop(op, value_type))
        lines.extend(_store_reg_to_slot(_reg_name(value_type, 11), func.value_slots[dest]))
        return lines

    if kind == "icmp":
        cond, dest, value_type, lhs, rhs = data
        lines = _materialize_value(func, lhs, value_type, 9)
        lines.extend(_materialize_value(func, rhs, value_type, 10))
        lines.append(f"  cmp {_reg_name(value_type, 9)}, {_reg_name(value_type, 10)}")
        lines.append(f"  cset w11, {_aarch64_cc(cond)}")
        lines.extend(_store_reg_to_slot("w11", func.value_slots[dest]))
        return lines

    if kind == "fcmp":
        cond, dest, value_type, lhs, rhs = data
        lines = _materialize_value(func, lhs, value_type, 9)
        lines.extend(_materialize_value(func, rhs, value_type, 10))
        lines.append(f"  fcmp {_reg_name(value_type, 9)}, {_reg_name(value_type, 10)}")
        lines.append(f"  cset w11, {_aarch64_fcc(cond)}")
        lines.extend(_store_reg_to_slot("w11", func.value_slots[dest]))
        return lines

    if kind == "cast":
        op, dest, src_type, value, dst_type = data
        lines = _materialize_value(func, value, src_type, 9)
        lines.extend(_emit_cast(op, src_type, dst_type))
        lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest]))
        return lines

    if kind == "gep":
        dest, base_type, _ptr_type, ptr_value, indices = data
        lines = _materialize_pointer(func, ptr_value, 9)
        lines.extend(_emit_gep_offset(func, base_type, indices))
        lines.extend(_store_reg_to_slot("x11", func.value_slots[dest]))
        return lines

    if kind == "call":
        dest, ret_type, callee, args = data
        _check_symbol_name(callee)
        lines: list[str] = []
        arg_regs = _assign_abi_arg_regs([arg_type for arg_type, _value in args])
        for regs, (arg_type, value) in zip(arg_regs, args):
            if not regs:
                continue
            lines.extend(_materialize_value(func, value, arg_type, int(regs[0][1:])))
        lines.append(f"  bl {_asm_symbol(callee)}")
        if dest is not None:
            lines.extend(_store_value_regs_to_slot(func.value_slots[dest], 0))
        return lines

    raise BackendUnavailable(
        f"self backend hit unknown instruction kind in {func.name!r}/{block.name!r}: {kind}"
    )


def _emit_terminator(func: ParsedFunction, block: ParsedBlock, term: ParsedInstr) -> list[str]:
    kind = term.kind
    data = term.data

    if kind == "ret_void":
        return _emit_epilogue(func)

    if kind == "ret":
        ret_type, value = data
        lines = _materialize_value(func, value, ret_type, 0)
        lines.extend(_emit_epilogue(func))
        return lines

    if kind == "br":
        target = data[0]
        lines = _emit_phi_assignments(func, source_block=block.name, target_block=target)
        lines.append(f"  b {_block_label(func, target)}")
        return lines

    if kind == "br_cond":
        cond_name, true_target, false_target = data
        false_prep = _block_edge_label(func, block.name, false_target)
        lines = _materialize_value(func, cond_name, I1, 9)
        lines.append("  cbz w9, " + false_prep)
        lines.extend(_emit_phi_assignments(func, source_block=block.name, target_block=true_target))
        lines.append(f"  b {_block_label(func, true_target)}")
        lines.append(f"{false_prep}:")
        lines.extend(_emit_phi_assignments(func, source_block=block.name, target_block=false_target))
        lines.append(f"  b {_block_label(func, false_target)}")
        return lines

    raise BackendUnavailable(
        f"self backend hit unknown terminator kind in {func.name!r}/{block.name!r}: {kind}"
    )


def _emit_epilogue(func: ParsedFunction) -> list[str]:
    lines: list[str] = []
    if func.frame_size:
        lines.append(f"  add sp, sp, #{func.frame_size}")
    lines.extend([
        "  ldp x29, x30, [sp], #16",
        "  ret",
    ])
    return lines


def _emit_phi_assignments(
    func: ParsedFunction,
    *,
    source_block: str,
    target_block: str,
) -> list[str]:
    target = func.block_map.get(target_block)
    if target is None:
        raise BackendUnavailable(
            f"self backend branch targets unknown block {target_block!r} in {func.name!r}"
        )

    lines: list[str] = []
    for phi in target.phis:
        match = None
        for incoming in phi.incoming:
            if incoming.label == source_block:
                match = incoming
                break
        if match is None:
            raise BackendUnavailable(
                f"self backend could not resolve phi incoming for {phi.dest!r} from {source_block!r}"
            )
        lines.extend(_materialize_value(func, match.value, phi.type, 9))
        lines.extend(_store_value_regs_to_slot(func.value_slots[phi.dest], 9))
    return lines


def _materialize_pointer(func: ParsedFunction, value: str, reg_index: int) -> list[str]:
    if value.startswith("@"):
        return _materialize_global_address(value[1:], f"x{reg_index}")
    ptr_type = func.value_types.get(value)
    if value in func.alloca_slots:
        ptr_type = func.alloca_slots[value].allocated_type.ptr()
    if ptr_type is None or not ptr_type.is_ptr:
        raise BackendUnavailable(
            f"self backend expected pointer value {value!r} in {func.name!r}"
        )
    return _materialize_value(func, value, ptr_type, reg_index)


def _materialize_value(func: ParsedFunction, value: str, expected_type: TypeDesc, reg_index: int) -> list[str]:
    regs = _abi_value_reg_names(expected_type, reg_index)
    reg = regs[0] if regs else ""
    if value == "null":
        return [f"  movz {reg}, #0"]
    if _HEX_RE.match(value):
        if not expected_type.is_fp:
            raise BackendUnavailable(
                f"self backend only accepts hexadecimal immediates for floating values, got {value!r}"
            )
        return _emit_fp_hex_constant(expected_type, reg, value)
    if _INT_RE.match(value):
        if expected_type.is_fp:
            raise BackendUnavailable(
                f"self backend does not yet support immediate floating constants: {value!r}"
            )
        return _emit_const_to_reg(expected_type, reg, int(value))
    if value.startswith("@"):
        if not expected_type.is_ptr:
            raise BackendUnavailable(
                f"self backend cannot use global symbol {value!r} as non-pointer in {func.name!r}"
            )
        return _materialize_global_address(value[1:], reg)
    if value in func.alloca_slots:
        if not expected_type.is_ptr:
            raise BackendUnavailable(
                f"self backend cannot use alloca address {value!r} as non-pointer in {func.name!r}"
            )
        return [f"  sub {reg}, x29, #{func.alloca_slots[value].offset}"]
    slot = func.value_slots.get(value)
    if slot is not None:
        return _load_slot_to_value_regs(slot, reg_index)
    raise BackendUnavailable(
        f"self backend could not materialize value {value!r} in {func.name!r}"
    )


def _emit_binop(op: str, value_type: TypeDesc) -> list[str]:
    if not value_type.is_int:
        raise BackendUnavailable(f"self backend only supports integer binops, got {value_type.describe()}")
    r9 = _reg_name(value_type, 9)
    r10 = _reg_name(value_type, 10)
    r11 = _reg_name(value_type, 11)
    mapping = {
        "add": f"  add {r11}, {r9}, {r10}",
        "sub": f"  sub {r11}, {r9}, {r10}",
        "mul": f"  mul {r11}, {r9}, {r10}",
        "sdiv": f"  sdiv {r11}, {r9}, {r10}",
        "udiv": f"  udiv {r11}, {r9}, {r10}",
        "and": f"  and {r11}, {r9}, {r10}",
        "or": f"  orr {r11}, {r9}, {r10}",
        "xor": f"  eor {r11}, {r9}, {r10}",
        "shl": f"  lslv {r11}, {r9}, {r10}",
        "lshr": f"  lsrv {r11}, {r9}, {r10}",
        "ashr": f"  asrv {r11}, {r9}, {r10}",
    }
    if op == "srem":
        return [
            f"  sdiv {r11}, {r9}, {r10}",
            f"  msub {r11}, {r11}, {r10}, {r9}",
        ]
    if op == "urem":
        return [
            f"  udiv {r11}, {r9}, {r10}",
            f"  msub {r11}, {r11}, {r10}, {r9}",
        ]
    if op not in mapping:
        raise BackendUnavailable(f"self backend does not support binop {op!r}")
    return [mapping[op]]


def _emit_fbinop(op: str, value_type: TypeDesc) -> list[str]:
    if not value_type.is_fp:
        raise BackendUnavailable(f"self backend only supports floating binops on fp values, got {value_type.describe()}")
    r9 = _reg_name(value_type, 9)
    r10 = _reg_name(value_type, 10)
    r11 = _reg_name(value_type, 11)
    mapping = {
        "fadd": f"  fadd {r11}, {r9}, {r10}",
        "fsub": f"  fsub {r11}, {r9}, {r10}",
        "fmul": f"  fmul {r11}, {r9}, {r10}",
        "fdiv": f"  fdiv {r11}, {r9}, {r10}",
    }
    if op not in mapping:
        raise BackendUnavailable(f"self backend does not support floating binop {op!r}")
    return [mapping[op]]


def _emit_cast(op: str, src_type: TypeDesc, dst_type: TypeDesc) -> list[str]:
    src9 = _reg_name(src_type, 9)
    dst10 = _reg_name(dst_type, 10)

    if op == "bitcast":
        if src_type.bits != dst_type.bits:
            raise BackendUnavailable(
                f"self backend bitcast requires same-size scalars, got {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.is_fp and dst_type.is_int:
            return [f"  fmov {dst10}, {src9}"]
        if src_type.is_int and dst_type.is_fp:
            return [f"  fmov {dst10}, {src9}"]
        return [f"  mov {dst10}, {src9}"]

    if op == "fpext":
        if not src_type.is_fp or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend fpext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvt {dst10}, {src9}"]

    if op == "fptrunc":
        if not src_type.is_fp or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend fptrunc mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvt {dst10}, {src9}"]

    if op == "ptrtoint":
        if not src_type.is_ptr or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend ptrtoint mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if dst_type.width > 32:
            return [f"  mov {dst10}, x9"]
        return [f"  mov {dst10}, w9"]

    if op == "inttoptr":
        if not src_type.is_int or not dst_type.is_ptr:
            raise BackendUnavailable(
                f"self backend inttoptr mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  mov x10, x9"]
        return [f"  mov w10, w9"]

    if op == "trunc":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend trunc mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width <= dst_type.width:
            raise BackendUnavailable(
                f"self backend trunc expects narrowing cast, got {src_type.describe()} -> {dst_type.describe()}"
            )
        if dst_type.width <= 32:
            return [f"  mov {dst10}, w9"]
        return [f"  mov {dst10}, x9"]

    if op == "zext":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend zext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width == 1:
            if dst_type.width > 32:
                return [
                    "  and w10, w9, #1",
                ]
            return ["  and w10, w9, #1"]
        if src_type.width <= 32 and dst_type.width > 32:
            return ["  mov w10, w9"]
        return [f"  mov {dst10}, {src9}"]

    if op == "sext":
        if not src_type.is_int or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend sext mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width == 8:
            return ["  sxtb x10, w9" if dst_type.width > 32 else "  sxtb w10, w9"]
        if src_type.width == 16:
            return ["  sxth x10, w9" if dst_type.width > 32 else "  sxth w10, w9"]
        if src_type.width == 32 and dst_type.width > 32:
            return ["  sxtw x10, w9"]
        if src_type.width == 1:
            return [
                "  and w10, w9, #1",
                "  neg w10, w10",
            ] if dst_type.width <= 32 else [
                "  and w10, w9, #1",
                "  neg x10, x10",
            ]
        return [f"  mov {dst10}, {src9}"]

    if op == "sitofp":
        if not src_type.is_int or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend sitofp mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  scvtf {dst10}, x9"]
        return [f"  scvtf {dst10}, w9"]

    if op == "uitofp":
        if not src_type.is_int or not dst_type.is_fp:
            raise BackendUnavailable(
                f"self backend uitofp mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        if src_type.width > 32:
            return [f"  ucvtf {dst10}, x9"]
        return [f"  ucvtf {dst10}, w9"]

    if op == "fptosi":
        if not src_type.is_fp or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend fptosi mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvtzs {dst10}, {src9}"]

    if op == "fptoui":
        if not src_type.is_fp or not dst_type.is_int:
            raise BackendUnavailable(
                f"self backend fptoui mismatch: {src_type.describe()} -> {dst_type.describe()}"
            )
        return [f"  fcvtzu {dst10}, {src9}"]

    raise BackendUnavailable(f"self backend does not support cast op {op!r}")


def _emit_gep_offset(
    func: ParsedFunction,
    base_type: TypeDesc,
    indices: tuple[tuple[TypeDesc, str], ...],
) -> list[str]:
    if base_type.is_array:
        if len(indices) != 2:
            raise BackendUnavailable(
                f"self backend array getelementptr expects 2 indices, got {len(indices)}"
            )
        if _const_int_from_value(indices[0][1]) not in (0, None):
            raise BackendUnavailable(
                "self backend array getelementptr currently requires first index 0"
            )
        assert base_type.elem is not None
        return _emit_indexed_pointer_add(func, indices[1][1], base_type.elem.slot_size)

    if base_type.is_struct:
        if len(indices) != 2:
            raise BackendUnavailable(
                f"self backend struct getelementptr expects 2 indices, got {len(indices)}"
            )
        if _const_int_from_value(indices[0][1]) != 0:
            raise BackendUnavailable(
                "self backend struct getelementptr currently requires first index 0"
            )
        field_index = _const_int_from_value(indices[1][1])
        if field_index is None:
            raise BackendUnavailable(
                "self backend struct getelementptr currently requires constant field indices"
            )
        offset = base_type.field_offset(field_index)
        if offset == 0:
            return ["  mov x11, x9"]
        return [f"  add x11, x9, #{offset}"]

    if len(indices) != 1:
        raise BackendUnavailable(
            f"self backend scalar-pointer getelementptr expects 1 index, got {len(indices)}"
        )
    return _emit_indexed_pointer_add(func, indices[0][1], base_type.slot_size)


def _emit_indexed_pointer_add(func: ParsedFunction, index_value: str, elem_size: int) -> list[str]:
    if elem_size == 0:
        raise BackendUnavailable("self backend cannot index into zero-sized element type")
    const_index = _const_int_from_value(index_value)
    if const_index is not None:
        offset = const_index * elem_size
        if offset == 0:
            return ["  mov x11, x9"]
        return [f"  add x11, x9, #{offset}"]

    lines = _materialize_index_to_x10(func, index_value)
    if elem_size == 1:
        lines.append("  add x11, x9, x10")
        return lines
    if elem_size in (2, 4, 8, 16):
        shift = {2: 1, 4: 2, 8: 3, 16: 4}[elem_size]
        lines.append(f"  add x11, x9, x10, lsl #{shift}")
        return lines
    lines.extend(
        [
            f"  movz x12, #{elem_size}",
            "  mul x10, x10, x12",
            "  add x11, x9, x10",
        ]
    )
    return lines


def _materialize_index_to_x10(func: ParsedFunction, index_value: str) -> list[str]:
    if _INT_RE.match(index_value):
        return _emit_const_to_reg(TypeDesc("int", 64), "x10", int(index_value))
    if index_value.startswith("@"):
        raise BackendUnavailable("self backend does not support symbol-valued getelementptr indices")
    if index_value not in func.value_slots:
        raise BackendUnavailable(f"self backend does not know getelementptr index value {index_value!r}")
    index_slot = func.value_slots[index_value]
    lines = _load_slot_to_reg(index_slot, _reg_name(index_slot.type, 10))
    if index_slot.type.bits < 64 and not index_slot.type.is_ptr:
        lines.append("  sxtw x10, w10")
    elif index_slot.type.is_ptr:
        raise BackendUnavailable("self backend does not support pointer-typed getelementptr indices")
    return lines


def _aarch64_cc(cond: str) -> str:
    mapping = {
        "eq": "eq",
        "ne": "ne",
        "slt": "lt",
        "sle": "le",
        "sgt": "gt",
        "sge": "ge",
        "ult": "lo",
        "ule": "ls",
        "ugt": "hi",
        "uge": "hs",
    }
    if cond not in mapping:
        raise BackendUnavailable(f"self backend does not support icmp {cond!r}")
    return mapping[cond]


def _aarch64_fcc(cond: str) -> str:
    mapping = {
        "oeq": "eq",
        "one": "ne",
        "ogt": "gt",
        "oge": "ge",
        "olt": "lt",
        "ole": "le",
    }
    if cond not in mapping:
        raise BackendUnavailable(f"self backend does not support fcmp {cond!r}")
    return mapping[cond]


def _store_reg_to_slot(reg: str, slot: SlotInfo) -> list[str]:
    op = _stack_store_op(slot.type)
    return [f"  {op} {reg}, [x29, #-{slot.offset}]"]


def _load_slot_to_reg(slot: SlotInfo, reg: str) -> list[str]:
    op = _stack_load_op(slot.type)
    return [f"  {op} {reg}, [x29, #-{slot.offset}]"]


def _load_from_address(addr_reg: str, dest_reg: str, value_type: TypeDesc) -> list[str]:
    return [f"  {_mem_load_op(value_type)} {dest_reg}, [{addr_reg}]"]


def _store_to_address(addr_reg: str, src_reg: str, value_type: TypeDesc) -> list[str]:
    return [f"  {_mem_store_op(value_type)} {src_reg}, [{addr_reg}]"]


def _materialize_global_address(name: str, reg: str) -> list[str]:
    symbol = _asm_symbol(name)
    return [
        f"  adrp {reg}, {symbol}@PAGE",
        f"  add {reg}, {reg}, {symbol}@PAGEOFF",
    ]


def _emit_globals(globals_: list[GlobalDef]) -> list[str]:
    lines: list[str] = []
    for global_ in globals_:
        section = ".section __DATA,__const" if global_.is_constant else ".section __DATA,__data"
        lines.append(section)
        lines.append(f".p2align {_align_pow2(global_.type.align)}")
        if not global_.is_internal:
            lines.append(f".globl {_asm_symbol(global_.name)}")
        lines.append(f"{_asm_symbol(global_.name)}:")
        lines.append(_emit_global_initializer(global_))
        lines.append("")
    return lines


def _emit_global_initializer(global_: GlobalDef) -> str:
    ty = global_.type
    init = global_.initializer
    if init == "null":
        init = "0"
    if ty.is_array:
        assert ty.elem is not None
        body = init.strip()[1:-1].strip()
        items = [] if not body else [piece.strip() for piece in body.split(",")]
        values = []
        for item in items:
            parts = item.split(None, 1)
            if len(parts) != 2:
                raise BackendUnavailable(
                    f"self backend could not decode array initializer element {item!r} for {global_.name!r}"
                )
            values.append(int(parts[1]))
        directive = {
            1: ".byte",
            2: ".short",
            4: ".long",
            8: ".quad",
        }.get(ty.elem.slot_size)
        if directive is None:
            raise BackendUnavailable(
                f"self backend does not support array initializer element size {ty.elem.slot_size} for {global_.name!r}"
            )
        return f"  {directive} " + ", ".join(str(v) for v in values)
    if ty.is_ptr:
        if init.startswith("gep0:"):
            return f"  .quad {_asm_symbol(init.split(':', 1)[1])}"
        if init.startswith("@"):
            return f"  .quad {_asm_symbol(_decode_global_name(init))}"
        return f"  .quad {int(init)}"
    if ty.is_int:
        if ty.width <= 8:
            return f"  .byte {int(init)}"
        if ty.width <= 16:
            return f"  .short {int(init)}"
        if ty.width <= 32:
            return f"  .long {int(init)}"
        return f"  .quad {int(init)}"
    raise BackendUnavailable(
        f"self backend does not support global initializer for {global_.name!r}: {ty.describe()}"
    )


def _stack_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "ldur"
    return "ldur"


def _stack_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "stur"
    return "stur"


def _mem_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "ldr"
    if value_type.is_int and value_type.width <= 8:
        return "ldrb"
    if value_type.is_int and value_type.width <= 16:
        return "ldrh"
    return "ldr"


def _mem_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "str"
    if value_type.is_int and value_type.width <= 8:
        return "strb"
    if value_type.is_int and value_type.width <= 16:
        return "strh"
    return "str"


def _emit_const_to_reg(value_type: TypeDesc, reg: str, value: int) -> list[str]:
    bits = 64 if value_type.is_ptr or (value_type.is_int and value_type.width > 32) else 32
    mask = (1 << bits) - 1
    unsigned = value & mask
    chunks = [((unsigned >> shift) & 0xFFFF) for shift in range(0, bits, 16)]
    first_index = 0
    while first_index < len(chunks) and chunks[first_index] == 0:
        first_index += 1
    if first_index == len(chunks):
        return [f"  movz {reg}, #0"]
    lines = [f"  movz {reg}, #{chunks[first_index]}, lsl #{first_index * 16}"]
    for index, chunk in enumerate(chunks):
        if index == first_index or chunk == 0:
            continue
        lines.append(f"  movk {reg}, #{chunk}, lsl #{index * 16}")
    return lines


def _emit_fp_hex_constant(value_type: TypeDesc, reg: str, token: str) -> list[str]:
    bits = int(token, 16)
    if not value_type.is_fp:
        raise BackendUnavailable(f"self backend fp constant helper expects fp type, got {value_type.describe()}")
    if value_type.width <= 32:
        as_double = struct.unpack(">d", bits.to_bytes(8, byteorder="big", signed=False))[-1]
        fp_bits = struct.unpack(">I", struct.pack(">f", float(as_double)))[0]
        lines = _emit_const_to_reg(TypeDesc("int", 32), "w12", fp_bits)
        lines.append(f"  fmov {reg}, w12")
        return lines
    lines = _emit_const_to_reg(TypeDesc("int", 64), "x12", bits)
    lines.append(f"  fmov {reg}, x12")
    return lines


def _decode_value_token(token: str) -> str:
    token = token.strip()
    if token == "null":
        return token
    if _INT_RE.match(token):
        return token
    if _HEX_RE.match(token):
        return token
    if token.startswith("%"):
        return _decode_ssa_name(token)
    if token.startswith("@"):
        return "@" + _decode_global_name(token)
    raise BackendUnavailable(f"unsupported value syntax for self backend: {token!r}")


def _decode_ssa_name(token: str) -> str:
    match = _SSA_NAME_RE.match(token.strip())
    if match is None:
        raise BackendUnavailable(f"unsupported SSA value syntax for self backend: {token!r}")
    return match.group(1) or match.group(2)


def _decode_global_name(token: str) -> str:
    match = _GLOBAL_NAME_RE.match(token.strip())
    if match is None:
        raise BackendUnavailable(f"unsupported global symbol syntax for self backend: {token!r}")
    return match.group(1) or match.group(2)


def _decode_label_ref(token: str) -> str:
    token = token.strip()
    if token.endswith(":") and _PLAIN_LABEL_RE.match(token):
        return token[:-1]
    match = _LABEL_REF_RE.match(token)
    if match is None:
        raise BackendUnavailable(f"unsupported label syntax for self backend: {token!r}")
    return match.group(1) or match.group(2)


def _check_symbol_name(name: str) -> None:
    if not _SYMBOL_NAME_RE.match(name):
        raise BackendUnavailable(
            f"self backend MVP only supports simple C identifier symbols, got {name!r}"
        )


def _asm_symbol(name: str) -> str:
    _check_symbol_name(name)
    return f"_{name}"


def _block_label(func: ParsedFunction, block_name: str) -> str:
    return f"L_{func.name}_{_sanitize_label(block_name)}"


def _block_edge_label(func: ParsedFunction, source: str, target: str) -> str:
    return f"L_{func.name}_{_sanitize_label(source)}_to_{_sanitize_label(target)}"


def _sanitize_label(value: str) -> str:
    text = value.replace(".", "dot")
    return re.sub(r'[^A-Za-z0-9_]', '_', text)


def _aggregate_is_gpr_only(value_type: TypeDesc) -> bool:
    if value_type.is_int or value_type.is_ptr:
        return True
    if value_type.is_array:
        assert value_type.elem is not None
        return _aggregate_is_gpr_only(value_type.elem)
    if value_type.is_struct:
        return all(_aggregate_is_gpr_only(field) for field in value_type.fields)
    return False


def _aggregate_reg_chunks(value_type: TypeDesc) -> tuple[int, ...]:
    if not (value_type.is_array or value_type.is_struct):
        raise BackendUnavailable(
            f"self backend aggregate register helper expected aggregate type, got {value_type.describe()}"
        )
    if not _aggregate_is_gpr_only(value_type):
        raise BackendUnavailable(
            "self backend aggregate register ABI currently only supports integer/pointer-only "
            f"aggregates, got {value_type.describe()}"
        )
    size = value_type.slot_size
    if size in (1, 2, 4, 8):
        return (size,)
    if 8 < size <= 16:
        tail = size - 8
        if tail in (1, 2, 4, 8):
            return (8, tail)
    raise BackendUnavailable(
        "self backend aggregate register ABI currently only supports aggregate sizes "
        "<=8 or two-register 8+{1,2,4,8}-byte shapes, got "
        f"{value_type.describe()} ({size} bytes)"
    )


def _abi_value_reg_names(value_type: TypeDesc, start_index: int) -> tuple[str, ...]:
    if value_type.is_void:
        return ()
    if value_type.is_array or value_type.is_struct:
        names: list[str] = []
        for index, chunk_size in enumerate(_aggregate_reg_chunks(value_type)):
            prefix = "x" if chunk_size > 4 else "w"
            names.append(f"{prefix}{start_index + index}")
        return tuple(names)
    return (_reg_name(value_type, start_index),)


def _assign_abi_arg_regs(arg_types: list[TypeDesc]) -> list[tuple[str, ...]]:
    gpr_index = 0
    fpr_index = 0
    assignments: list[tuple[str, ...]] = []
    for arg_type in arg_types:
        if arg_type.is_void:
            assignments.append(())
            continue
        if arg_type.is_fp:
            regs = _abi_value_reg_names(arg_type, fpr_index)
            if fpr_index + len(regs) > 8:
                raise BackendUnavailable(
                    "self backend MVP only supports up to 8 floating-point argument registers"
                )
            fpr_index += len(regs)
            assignments.append(regs)
            continue
        regs = _abi_value_reg_names(arg_type, gpr_index)
        if gpr_index + len(regs) > 8:
            raise BackendUnavailable(
                "self backend MVP only supports up to 8 general-purpose argument registers"
            )
        gpr_index += len(regs)
        assignments.append(regs)
    return assignments


def _chunk_load_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "ldur" if stack else "ldr"
    if size == 4:
        return "ldur" if stack else "ldr"
    if size == 2:
        return "ldurh" if stack else "ldrh"
    if size == 1:
        return "ldurb" if stack else "ldrb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk load size {size}")


def _chunk_store_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "stur" if stack else "str"
    if size == 4:
        return "stur" if stack else "str"
    if size == 2:
        return "sturh" if stack else "strh"
    if size == 1:
        return "sturb" if stack else "strb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk store size {size}")


def _store_value_regs_to_slot(slot: SlotInfo, start_index: int) -> list[str]:
    if not (slot.type.is_array or slot.type.is_struct):
        return _store_reg_to_slot(_reg_name(slot.type, start_index), slot)
    lines: list[str] = []
    offset = slot.offset
    for reg, chunk_size in zip(_abi_value_reg_names(slot.type, start_index), _aggregate_reg_chunks(slot.type)):
        lines.append(f"  {_chunk_store_op(chunk_size, stack=True)} {reg}, [x29, #-{offset}]")
        offset -= chunk_size
    return lines


def _load_slot_to_value_regs(slot: SlotInfo, start_index: int) -> list[str]:
    if not (slot.type.is_array or slot.type.is_struct):
        return _load_slot_to_reg(slot, _reg_name(slot.type, start_index))
    lines: list[str] = []
    offset = slot.offset
    for reg, chunk_size in zip(_abi_value_reg_names(slot.type, start_index), _aggregate_reg_chunks(slot.type)):
        lines.append(f"  {_chunk_load_op(chunk_size, stack=True)} {reg}, [x29, #-{offset}]")
        offset -= chunk_size
    return lines


def _load_value_from_address(addr_reg: str, value_type: TypeDesc, start_index: int) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return _load_from_address(addr_reg, _reg_name(value_type, start_index), value_type)
    lines: list[str] = []
    offset = 0
    for reg, chunk_size in zip(_abi_value_reg_names(value_type, start_index), _aggregate_reg_chunks(value_type)):
        suffix = "" if offset == 0 else f", #{offset}"
        lines.append(f"  {_chunk_load_op(chunk_size, stack=False)} {reg}, [{addr_reg}{suffix}]")
        offset += chunk_size
    return lines


def _store_value_to_address(addr_reg: str, value_type: TypeDesc, start_index: int) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return _store_to_address(addr_reg, _reg_name(value_type, start_index), value_type)
    lines: list[str] = []
    offset = 0
    for reg, chunk_size in zip(_abi_value_reg_names(value_type, start_index), _aggregate_reg_chunks(value_type)):
        suffix = "" if offset == 0 else f", #{offset}"
        lines.append(f"  {_chunk_store_op(chunk_size, stack=False)} {reg}, [{addr_reg}{suffix}]")
        offset += chunk_size
    return lines


def _reg_name(value_type: TypeDesc, index: int) -> str:
    return f"{value_type.reg_prefix}{index}"


def _align_to(value: int, alignment: int) -> int:
    if value == 0:
        return 0
    return ((value + alignment - 1) // alignment) * alignment


def _align_pow2(alignment: int) -> int:
    power = 0
    value = 1
    while value < max(1, alignment):
        power += 1
        value <<= 1
    return power
