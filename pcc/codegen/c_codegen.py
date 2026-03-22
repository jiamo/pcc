import llvmlite.ir as ir
import re
import struct
from collections import ChainMap
from contextlib import contextmanager
from dataclasses import dataclass
from llvmlite.ir import IRBuilder
from ..ast import c_ast as c_ast

bool_t = ir.IntType(1)
int8_t = ir.IntType(8)
int32_t = ir.IntType(32)
int64_t = ir.IntType(64)
voidptr_t = int8_t.as_pointer()
int64ptr_t = int64_t.as_pointer()
true_bit = bool_t(1)
false_bit = bool_t(0)
true_byte = int8_t(1)
false_byte = int8_t(0)
cstring = voidptr_t
struct_types = {}


class SemanticError(ValueError):
    pass


@dataclass
class FileScopeObjectState:
    type_key: str
    linkage: str
    definition_kind: str
    symbol_name: str


@dataclass
class FileScopeFunctionState:
    type_key: str
    linkage: str
    defined: bool
    symbol_name: str

# Libc function signature registry: name -> (return_type, [param_types], var_arg)
# Covers: stdio.h, stdlib.h, string.h, ctype.h, math.h, unistd.h, time.h
_VOID = ir.VoidType()
_float = ir.FloatType()
_double = ir.DoubleType()
_FILE_ptr = voidptr_t  # FILE* modeled as opaque void*
_size_t = int64_t
_time_t = int64_t

LIBC_FUNCTIONS = {
    # === stdio.h ===
    "printf": (int32_t, [cstring], True),
    "fprintf": (int32_t, [_FILE_ptr, cstring], True),
    "sprintf": (int32_t, [cstring, cstring], True),
    "snprintf": (int32_t, [cstring, _size_t, cstring], True),
    "vprintf": (int32_t, [cstring, voidptr_t], False),
    "vfprintf": (int32_t, [_FILE_ptr, cstring, voidptr_t], False),
    "vsprintf": (int32_t, [cstring, cstring, voidptr_t], False),
    "vsnprintf": (int32_t, [cstring, _size_t, cstring, voidptr_t], False),
    "scanf": (int32_t, [cstring], True),
    "fscanf": (int32_t, [_FILE_ptr, cstring], True),
    "sscanf": (int32_t, [cstring, cstring], True),
    "fopen": (_FILE_ptr, [cstring, cstring], False),
    "fclose": (int32_t, [_FILE_ptr], False),
    "fread": (_size_t, [voidptr_t, _size_t, _size_t, _FILE_ptr], False),
    "fwrite": (_size_t, [voidptr_t, _size_t, _size_t, _FILE_ptr], False),
    "fseek": (int32_t, [_FILE_ptr, int64_t, int32_t], False),
    "ftell": (int64_t, [_FILE_ptr], False),
    "rewind": (_VOID, [_FILE_ptr], False),
    "feof": (int32_t, [_FILE_ptr], False),
    "ferror": (int32_t, [_FILE_ptr], False),
    "fflush": (int32_t, [_FILE_ptr], False),
    "fgets": (cstring, [cstring, int32_t, _FILE_ptr], False),
    "fputs": (int32_t, [cstring, _FILE_ptr], False),
    "fgetc": (int32_t, [_FILE_ptr], False),
    "fputc": (int32_t, [int32_t, _FILE_ptr], False),
    "getc": (int32_t, [_FILE_ptr], False),
    "getc_unlocked": (int32_t, [_FILE_ptr], False),
    "putc": (int32_t, [int32_t, _FILE_ptr], False),
    "getchar": (int32_t, [], False),
    "putchar": (int32_t, [int32_t], False),
    "ungetc": (int32_t, [int32_t, _FILE_ptr], False),
    "flockfile": (_VOID, [_FILE_ptr], False),
    "funlockfile": (_VOID, [_FILE_ptr], False),
    "puts": (int32_t, [cstring], False),
    "perror": (_VOID, [cstring], False),
    "remove": (int32_t, [cstring], False),
    "rename": (int32_t, [cstring, cstring], False),
    "fseeko": (int32_t, [_FILE_ptr, int64_t, int32_t], False),
    "ftello": (int64_t, [_FILE_ptr], False),
    # === stdlib.h ===
    "malloc": (voidptr_t, [_size_t], False),
    "calloc": (voidptr_t, [_size_t, _size_t], False),
    "realloc": (voidptr_t, [voidptr_t, _size_t], False),
    "free": (_VOID, [voidptr_t], False),
    "exit": (_VOID, [int32_t], False),
    "_Exit": (_VOID, [int32_t], False),
    "abort": (_VOID, [], False),
    "atexit": (int32_t, [voidptr_t], False),
    "abs": (int32_t, [int32_t], False),
    "labs": (int64_t, [int64_t], False),
    "atoi": (int32_t, [cstring], False),
    "atol": (int64_t, [cstring], False),
    "atof": (_double, [cstring], False),
    "strtol": (int64_t, [cstring, voidptr_t, int32_t], False),
    "strtoul": (int64_t, [cstring, voidptr_t, int32_t], False),
    "strtod": (_double, [cstring, voidptr_t], False),
    "strtof": (_double, [cstring, voidptr_t], False),
    "rand": (int32_t, [], False),
    "srand": (_VOID, [int32_t], False),
    "qsort": (_VOID, [voidptr_t, _size_t, _size_t, voidptr_t], False),
    "bsearch": (voidptr_t, [voidptr_t, voidptr_t, _size_t, _size_t, voidptr_t], False),
    "getenv": (cstring, [cstring], False),
    "system": (int32_t, [cstring], False),
    # === string.h ===
    "strlen": (_size_t, [cstring], False),
    "strcmp": (int32_t, [cstring, cstring], False),
    "strncmp": (int32_t, [cstring, cstring, _size_t], False),
    "strcpy": (cstring, [cstring, cstring], False),
    "strncpy": (cstring, [cstring, cstring, _size_t], False),
    "strcat": (cstring, [cstring, cstring], False),
    "strncat": (cstring, [cstring, cstring, _size_t], False),
    "strchr": (cstring, [cstring, int32_t], False),
    "strrchr": (cstring, [cstring, int32_t], False),
    "strstr": (cstring, [cstring, cstring], False),
    "strpbrk": (cstring, [cstring, cstring], False),
    "strspn": (_size_t, [cstring, cstring], False),
    "strcspn": (_size_t, [cstring, cstring], False),
    "strtok": (cstring, [cstring, cstring], False),
    "memset": (voidptr_t, [voidptr_t, int32_t, _size_t], False),
    "memcpy": (voidptr_t, [voidptr_t, voidptr_t, _size_t], False),
    "memmove": (voidptr_t, [voidptr_t, voidptr_t, _size_t], False),
    "memcmp": (int32_t, [voidptr_t, voidptr_t, _size_t], False),
    "memchr": (voidptr_t, [voidptr_t, int32_t, _size_t], False),
    "strerror": (cstring, [int32_t], False),
    # === ctype.h ===
    "isalpha": (int32_t, [int32_t], False),
    "isdigit": (int32_t, [int32_t], False),
    "isalnum": (int32_t, [int32_t], False),
    "isspace": (int32_t, [int32_t], False),
    "isupper": (int32_t, [int32_t], False),
    "islower": (int32_t, [int32_t], False),
    "isprint": (int32_t, [int32_t], False),
    "ispunct": (int32_t, [int32_t], False),
    "iscntrl": (int32_t, [int32_t], False),
    "isxdigit": (int32_t, [int32_t], False),
    "isgraph": (int32_t, [int32_t], False),
    "toupper": (int32_t, [int32_t], False),
    "tolower": (int32_t, [int32_t], False),
    # === math.h ===
    "sin": (_double, [_double], False),
    "cos": (_double, [_double], False),
    "tan": (_double, [_double], False),
    "asin": (_double, [_double], False),
    "acos": (_double, [_double], False),
    "atan": (_double, [_double], False),
    "atan2": (_double, [_double, _double], False),
    "sinh": (_double, [_double], False),
    "cosh": (_double, [_double], False),
    "tanh": (_double, [_double], False),
    "exp": (_double, [_double], False),
    "exp2": (_double, [_double], False),
    "log": (_double, [_double], False),
    "log2": (_double, [_double], False),
    "log10": (_double, [_double], False),
    "pow": (_double, [_double, _double], False),
    "sqrt": (_double, [_double], False),
    "cbrt": (_double, [_double], False),
    "hypot": (_double, [_double, _double], False),
    "ceil": (_double, [_double], False),
    "floor": (_double, [_double], False),
    "round": (_double, [_double], False),
    "trunc": (_double, [_double], False),
    "fmod": (_double, [_double, _double], False),
    "fabs": (_double, [_double], False),
    "ldexp": (_double, [_double, int32_t], False),
    # === time.h ===
    "time": (_time_t, [voidptr_t], False),
    "clock": (int64_t, [], False),
    "difftime": (_double, [_time_t, _time_t], False),
    "gmtime_r": (voidptr_t, [voidptr_t, voidptr_t], False),
    "localtime_r": (voidptr_t, [voidptr_t, voidptr_t], False),
    # === unistd.h (POSIX) ===
    "sleep": (int32_t, [int32_t], False),
    "usleep": (int32_t, [int32_t], False),
    "read": (int64_t, [int32_t, voidptr_t, _size_t], False),
    "write": (int64_t, [int32_t, voidptr_t, _size_t], False),
    "open": (int32_t, [cstring, int32_t], True),
    "close": (int32_t, [int32_t], False),
    "getpid": (int32_t, [], False),
    "getppid": (int32_t, [], False),
    "isatty": (int32_t, [int32_t], False),
    "mkstemp": (int32_t, [cstring], False),
    # === setjmp.h ===
    "setjmp": (int32_t, [voidptr_t], False),
    "longjmp": (_VOID, [voidptr_t, int32_t], False),
    "_setjmp": (int32_t, [voidptr_t], False),
    "_longjmp": (_VOID, [voidptr_t, int32_t], False),
    # === signal.h ===
    "signal": (voidptr_t, [int32_t, voidptr_t], False),
    "sigaction": (int32_t, [int32_t, voidptr_t, voidptr_t], False),
    "sigemptyset": (int32_t, [voidptr_t], False),
    "raise": (int32_t, [int32_t], False),
    # === errno ===
    "__errno_location": (ir.IntType(32).as_pointer(), [], False),
    # === locale.h ===
    "setlocale": (cstring, [int32_t, cstring], False),
    "localeconv": (voidptr_t, [], False),
    # === misc ===
    "tmpnam": (cstring, [cstring], False),
    "tmpfile": (voidptr_t, [], False),
    "__errno_location": (int32_t.as_pointer(), [], False),
    "gmtime": (voidptr_t, [voidptr_t], False),
    "localtime": (voidptr_t, [voidptr_t], False),
    "mktime": (_time_t, [voidptr_t], False),
    "strftime": (_size_t, [cstring, _size_t, cstring, voidptr_t], False),
    "ctime": (cstring, [voidptr_t], False),
    "asctime": (cstring, [voidptr_t], False),
    "frexp": (_double, [_double, int32_t.as_pointer()], False),
    # GCC/Clang builtins (no-op stubs)
    "__builtin_va_start": (_VOID, [voidptr_t], False),
    "__builtin_va_end": (_VOID, [voidptr_t], False),
    "__builtin_va_copy": (_VOID, [voidptr_t, voidptr_t], False),
    "__builtin_expect": (int64_t, [int64_t, int64_t], False),
    "__builtin_unreachable": (_VOID, [], False),
    "__builtin_clz": (int32_t, [int32_t], False),
    "__builtin_ctz": (int32_t, [int32_t], False),
    "modf": (_double, [_double, ir.DoubleType().as_pointer()], False),
    "ldexp": (_double, [_double, int32_t], False),
    "__builtin_va_arg": (voidptr_t, [voidptr_t, int64_t], False),
    "strcoll": (int32_t, [cstring, cstring], False),
    "clearerr": (_VOID, [voidptr_t], False),
    "fileno": (int32_t, [voidptr_t], False),
    "popen": (voidptr_t, [cstring, cstring], False),
    "pclose": (int32_t, [voidptr_t], False),
    "dlopen": (voidptr_t, [cstring, int32_t], False),
    "dlsym": (voidptr_t, [voidptr_t, cstring], False),
    "dlclose": (int32_t, [voidptr_t], False),
    "dlerror": (cstring, [], False),
    "setvbuf": (int32_t, [voidptr_t, cstring, int32_t, _size_t], False),
    "freopen": (voidptr_t, [cstring, cstring, voidptr_t], False),
    "getc": (int32_t, [voidptr_t], False),
}


class CodegenError(Exception):
    pass


class ExternGlobalRef:
    def __init__(self, symbol_name, ir_type):
        self.symbol_name = symbol_name
        self.ir_type = ir_type


int16_t = ir.IntType(16)


def get_ir_type(type_str):
    """Get IR type from a single type name string."""
    return get_ir_type_from_names([type_str] if isinstance(type_str, str) else type_str)


def _names_to_key(names):
    """Convert a names list like ['unsigned', 'int'] to a canonical key string."""
    return names[0] if len(names) == 1 else " ".join(sorted(names))


def _is_unsigned_names(names):
    """Check if a type name list represents an unsigned type."""
    return "unsigned" in names


# Known unsigned type names (after typedef resolution)
_UNSIGNED_TYPE_NAMES = frozenset(
    {
        "char unsigned",
        "int unsigned",
        "unsigned",
        "int short unsigned",
        "short unsigned",
        "int long unsigned",
        "long unsigned",
        "long long unsigned",
        "size_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
    }
)


_PCC_VAARG_DECL_RE = re.compile(r'^declare .+@"__pcc_va_arg_\d+"\(.+\)\n?', re.M)
_PCC_VAARG_CALL_RE = re.compile(
    r"^(?P<lhs>\s*%\S+)\s*=\s*call\s+"
    r'(?P<rettype>.+?)\s+@"(?P<name>__pcc_va_arg_\d+)"\('
    r'(?P<argtype>.+?)\s+(?P<argval>%".+?"|%\S+)\)$',
    re.M,
)


_INVALID_VOID_INSTR_RE = re.compile(
    r"^(?:%\S+\s*=\s*)?(?:alloca|load|bitcast) void(?! \()([,\s]|$)|^store void(?! \()([,\s]|$)"
)

_LABEL_RE = re.compile(r"^[A-Za-z$._][A-Za-z0-9$._-]*:$")


def postprocess_ir_text(text):
    """Apply textual IR rewrites that llvmlite cannot express directly."""

    # --- simple regex rewrites ---
    text = _PCC_VAARG_DECL_RE.sub("", text)
    text = re.sub(
        r"bitcast i64 (%\S+) to (i8\*|[^,\n]+\*)", r"inttoptr i64 \1 to \2", text
    )
    text = re.sub(
        r"bitcast i8 (%\S+) to (i8\*|[^,\n]+\*)", r"inttoptr i8 \1 to \2", text
    )
    text = re.sub(r"ptrtoint \[\d+ x i8\] [^\n]+ to i64", "add i64 0, 0", text)

    def repl(match):
        lhs = match.group("lhs")
        rettype = match.group("rettype")
        argtype = match.group("argtype")
        argval = match.group("argval")
        return f"{lhs} = va_arg {argtype} {argval}, {rettype}"

    text = _PCC_VAARG_CALL_RE.sub(repl, text)

    # --- line-level fixups ---
    lines = []
    for line in text.splitlines():
        # Fix Python repr leak in array initializers → zeroinitializer
        if "<ir.Constant" in line:
            m = re.match(r'(@"[^"]*"\s*=\s*(?:global|constant)\s*\[[^\]]*\]).*', line)
            if m:
                line = m.group(1) + " zeroinitializer"
            else:
                continue
        s = line.strip()
        # Drop invalid void instructions (alloca void, load void, store void)
        if _INVALID_VOID_INSTR_RE.match(s):
            continue
        lines.append(line)

    # Deduplicate switch case values
    deduped = []
    for line in lines:
        s = line.strip()
        if s.startswith("switch i64 ") and "[" in line and "]" in line:
            prefix, rest = line.split("[", 1)
            case_text, suffix = rest.rsplit("]", 1)
            cases = re.findall(r'(i64 -?\d+, label %"[^"]*")', case_text)
            if cases:
                seen = set()
                unique = []
                for case in cases:
                    val = re.match(r"i64 (-?\d+)", case).group(1)
                    if val not in seen:
                        seen.add(val)
                        unique.append(case)
                line = prefix + "[" + " ".join(unique) + "]" + suffix
        deduped.append(line)

    # Repair control flow: drop dead code after terminators, bridge empty labels
    def _is_label(s):
        return bool(_LABEL_RE.match(s))

    def _is_terminator(s):
        return s.startswith(("br ", "ret ", "switch ", "unreachable", "resume "))

    repaired = []
    skip_dead = False
    for raw in deduped:
        s = raw.strip()
        if skip_dead:
            if _is_label(s):
                skip_dead = False
            else:
                continue
        if (
            repaired
            and _is_terminator(repaired[-1].strip())
            and s
            and raw.startswith("  ")
            and not _is_label(s)
        ):
            skip_dead = True
            continue
        if repaired and _is_label(s):
            prev = repaired[-1].strip()
            if _is_label(prev):
                repaired.append(f'  br label %"{s[:-1]}"')
            elif prev not in {"", "{"} and not _is_terminator(prev):
                repaired.append(f'  br label %"{s[:-1]}"')
        if s == "}" and repaired and _is_label(repaired[-1].strip()):
            repaired.append("  unreachable")
        repaired.append(raw)

    return "\n".join(repaired)


def get_ir_type_from_names(names):
    """Get IR type from a list of type specifier names like ['unsigned', 'int']."""
    names = [
        n
        for n in names
        if n
        not in (
            "const",
            "volatile",
            "register",
            "restrict",
            "inline",
            "_Noreturn",
            "signed",
            "extern",
            "static",
        )
    ]
    s = " ".join(sorted(names))

    # Exact matches
    type_map = {
        "int": int32_t,
        "char": int8_t,
        "void": ir.VoidType(),
        "double": _double,
        "float": _float,
        "short": int16_t,
        "long": int64_t,
        "int short": int16_t,
        "int long": int64_t,
        "long long": int64_t,
        "int long long": int64_t,
        "char unsigned": int8_t,
        "int unsigned": int32_t,
        "unsigned": int32_t,
        "int short unsigned": int16_t,
        "short unsigned": int16_t,
        "int long unsigned": int64_t,
        "long unsigned": int64_t,
        "long long unsigned": int64_t,
        # size_t, etc.
        "size_t": int64_t,
        "ssize_t": int64_t,
        "ptrdiff_t": int64_t,
        "int8_t": int8_t,
        "int16_t": int16_t,
        "int32_t": int32_t,
        "int64_t": int64_t,
        "uint8_t": int8_t,
        "uint16_t": int16_t,
        "uint32_t": int32_t,
        "uint64_t": int64_t,
    }

    if s in type_map:
        return type_map[s]

    if "double" in names:
        return _double
    if "float" in names:
        return _float
    # If it contains 'char', return i8
    if "char" in names:
        return int8_t
    # If it contains 'short', return i16
    if "short" in names:
        return int16_t
    # Default to i64
    return int64_t


def get_ir_type_from_node(node):
    if isinstance(node, c_ast.EllipsisParam):
        return voidptr_t  # shouldn't be called, but be safe

    return _resolve_node_type(node.type)


def _resolve_node_type(node_type):
    """Resolve an AST type node to an IR type."""
    if isinstance(node_type, c_ast.PtrDecl):
        inner = node_type.type
        if isinstance(inner, c_ast.FuncDecl):
            ret_type = _resolve_node_type(inner.type)
            param_types = []
            if inner.args:
                for p in inner.args.params:
                    if isinstance(p, c_ast.EllipsisParam):
                        continue
                    t = get_ir_type_from_node(p)
                    if not isinstance(t, ir.VoidType):
                        param_types.append(t)
            return ir.FunctionType(ret_type, param_types).as_pointer()
        pointee = _resolve_node_type(inner)
        if isinstance(pointee, ir.VoidType):
            return voidptr_t
        return ir.PointerType(pointee)
    elif isinstance(node_type, c_ast.TypeDecl):
        if isinstance(node_type.type, c_ast.IdentifierType):
            return get_ir_type(node_type.type.names)
        elif isinstance(node_type.type, c_ast.Struct):
            snode = node_type.type
            if snode.decls:
                # Inline struct with declarations — build real type
                member_types = []
                for decl in snode.decls:
                    member_types.append(_resolve_node_type(decl.type))
                st = ir.LiteralStructType(member_types)
                st.members = [d.name for d in snode.decls]
                st.member_decl_types = [d.type for d in snode.decls]
                return st
            return int8_t  # opaque/forward-declared struct
        elif isinstance(node_type.type, c_ast.Union):
            unode = node_type.type
            if unode.decls:
                # Inline union with declarations — compute max size
                max_size = 0
                max_align = 1
                member_types = {}

                def _resolve_union_member_type(decl_type):
                    if isinstance(decl_type, c_ast.ArrayDecl):
                        dims = []
                        arr_node = decl_type
                        while isinstance(arr_node, c_ast.ArrayDecl):
                            dim = 0
                            if isinstance(arr_node.dim, c_ast.Constant):
                                dim = int(arr_node.dim.value.rstrip("uUlL"), 0)
                            elif arr_node.dim is not None:
                                dim = 0
                            dims.append(dim)
                            arr_node = arr_node.type
                        elem_ir_type = _resolve_node_type(arr_node)
                        arr_ir_type = elem_ir_type
                        for dim in reversed(dims):
                            arr_ir_type = ir.ArrayType(arr_ir_type, dim)
                        return arr_ir_type
                    return _resolve_node_type(decl_type)

                for decl in unode.decls:
                    ir_t = _resolve_union_member_type(decl.type)
                    member_types[decl.name] = ir_t
                    sz = ir_t.width // 8 if isinstance(ir_t, ir.IntType) else 8
                    if isinstance(ir_t, ir.LiteralStructType):
                        sz = sum(
                            e.width // 8 if isinstance(e, ir.IntType) else 8
                            for e in ir_t.elements
                        )
                    if isinstance(ir_t, ir.PointerType):
                        sz = 8
                    if self._is_floating_ir_type(ir_t):
                        sz = self._ir_type_size(ir_t)
                    al = self._ir_type_align(ir_t)
                    if sz > max_size:
                        max_size = sz
                    if al > max_align:
                        max_align = al
                align_map = {8: int64_t, 4: int32_t, 2: int16_t, 1: int8_t}
                align_type = align_map.get(max_align, int64_t)
                pad_size = max_size - max_align
                if pad_size > 0:
                    ut = ir.LiteralStructType(
                        [align_type, ir.ArrayType(int8_t, pad_size)]
                    )
                else:
                    ut = ir.LiteralStructType([align_type])
                ut.members = list(member_types.keys())
                ut.member_types = member_types
                ut.is_union = True
                return ut
            return int8_t  # opaque/forward-declared union
        return int64_t
    elif isinstance(node_type, c_ast.ArrayDecl):
        return voidptr_t  # array params decay to pointer
    return int64_t


class LLVMCodeGenerator(object):

    def __init__(self, translation_unit_name=None):
        self.module = ir.Module()
        # Set proper data layout for struct padding/alignment
        import llvmlite.binding as _llvm

        _llvm.initialize_native_target()
        _triple = _llvm.get_default_triple()
        _tm = _llvm.Target.from_default_triple().create_target_machine()
        self.module.triple = _triple
        self.module.data_layout = str(_tm.target_data)

        #
        self.builder = None
        self.global_builder: IRBuilder = ir.IRBuilder()
        self.env = ChainMap()
        self.nlabels = 0
        self.function = None
        self.in_global = True
        self._declared_libc = set()
        self._unsigned_bindings = set()  # alloca/global ids with unsigned type
        self._unsigned_pointee_bindings = set()
        self._unsigned_return_bindings = set()
        self._expr_ir_types = {}
        self._labels = {}
        self._vaarg_counter = 0
        self._file_scope_object_states = {}
        self._file_scope_function_states = {}
        self.translation_unit_name = self._sanitize_translation_unit_name(
            translation_unit_name
        )

    def define(self, name, val):
        self.env[name] = val

    @staticmethod
    def _sanitize_translation_unit_name(name):
        if not name:
            return None
        return re.sub(r"\W+", "_", name)

    def _is_file_scope_static(self, storage=None):
        return (
            self.translation_unit_name
            and self.in_global
            and storage
            and "static" in storage
        )

    def _file_scope_symbol_name(self, name, storage=None):
        if self._is_file_scope_static(storage):
            return f"__pcc_internal_{self.translation_unit_name}_{name}"
        return name

    def _static_local_symbol_name(self, name):
        if self.translation_unit_name:
            return f"__static_{self.translation_unit_name}_{self.function.name}_{name}"
        return f"__static_{self.function.name}_{name}"

    def _create_bound_global(
        self, bind_name, ir_type, symbol_name=None, external=False, storage=None
    ):
        actual_name = symbol_name or bind_name
        gv = self.module.globals.get(actual_name)
        if gv is None:
            gv = ir.GlobalVariable(self.module, ir_type, actual_name)
        if self._is_file_scope_static(storage):
            gv.linkage = "internal"
        elif not external and getattr(gv, "initializer", None) is None:
            gv.initializer = ir.Constant(ir_type, None)
        self.define(bind_name, (ir_type, gv))
        return gv

    def _decl_linkage(self, storage=None, existing_state=None):
        if storage and "static" in storage:
            return "internal"
        if existing_state is not None:
            return existing_state.linkage
        return "external"

    def _effective_file_scope_symbol_name(self, name, storage=None, existing_state=None):
        if storage and "static" in storage:
            return f"__pcc_internal_{self.translation_unit_name}_{name}"
        if existing_state is not None and existing_state.linkage == "internal":
            return existing_state.symbol_name
        return name

    def _file_scope_object_definition_kind(self, storage=None, has_initializer=False):
        if storage and "extern" in storage and not has_initializer:
            return "extern"
        if has_initializer:
            return "definition"
        return "tentative"

    def _prepare_file_scope_object(self, name, ir_type, storage=None, has_initializer=False):
        if not self.in_global or name is None:
            return None, True
        if name in self._file_scope_function_states:
            raise SemanticError(f"'{name}' redeclared as object after function declaration")

        type_key = str(ir_type)
        definition_kind = self._file_scope_object_definition_kind(
            storage, has_initializer
        )
        state = self._file_scope_object_states.get(name)
        linkage = self._decl_linkage(storage, existing_state=state)
        symbol_name = self._effective_file_scope_symbol_name(
            name, storage=storage, existing_state=state
        )

        if state is None:
            state = FileScopeObjectState(
                type_key=type_key,
                linkage=linkage,
                definition_kind=definition_kind,
                symbol_name=symbol_name,
            )
            self._file_scope_object_states[name] = state
            if definition_kind == "extern":
                self._record_extern_global(name, ir_type, storage=storage)
                return None, False
            gv = self._create_bound_global(
                name, ir_type, symbol_name=symbol_name, storage=storage
            )
            return gv, True

        if state.type_key != type_key:
            raise SemanticError(f"conflicting types for global '{name}'")
        if state.linkage != linkage:
            raise SemanticError(f"conflicting linkage for global '{name}'")
        if state.symbol_name != symbol_name:
            raise SemanticError(f"conflicting symbol binding for global '{name}'")

        existing = self.module.globals.get(symbol_name)
        if definition_kind == "extern":
            if existing is not None:
                self.define(name, (ir_type, existing))
            else:
                self._record_extern_global(name, ir_type, storage=storage)
            return None, False

        if existing is None:
            gv = self._create_bound_global(
                name, ir_type, symbol_name=symbol_name, storage=storage
            )
        else:
            gv = existing
            self.define(name, (ir_type, gv))

        if state.definition_kind == "definition":
            if definition_kind == "definition":
                raise SemanticError(f"redefinition of global '{name}'")
            return gv, False

        if state.definition_kind == "tentative":
            if definition_kind == "definition":
                state.definition_kind = "definition"
                return gv, True
            return gv, False

        state.definition_kind = definition_kind
        return gv, True

    def _register_file_scope_function(
        self, name, function_type, storage=None, is_definition=False
    ):
        if not self.in_global or name is None:
            return
        if name in self._file_scope_object_states:
            raise SemanticError(f"'{name}' redeclared as function after object declaration")

        type_key = str(function_type)
        state = self._file_scope_function_states.get(name)
        linkage = self._decl_linkage(storage, existing_state=state)
        symbol_name = self._effective_file_scope_symbol_name(
            name, storage=storage, existing_state=state
        )

        if state is None:
            self._file_scope_function_states[name] = FileScopeFunctionState(
                type_key=type_key,
                linkage=linkage,
                defined=is_definition,
                symbol_name=symbol_name,
            )
            return symbol_name

        if state.type_key != type_key:
            raise SemanticError(f"conflicting types for function '{name}'")
        if state.linkage != linkage:
            raise SemanticError(f"conflicting linkage for function '{name}'")
        if state.symbol_name != symbol_name:
            raise SemanticError(f"conflicting symbol binding for function '{name}'")
        if is_definition:
            if state.defined:
                raise SemanticError(f"redefinition of function '{name}'")
            state.defined = True
        return state.symbol_name

    def external_definitions(self):
        defs = []
        for name, state in self._file_scope_function_states.items():
            if state.linkage == "external" and state.defined:
                defs.append(("function", state.symbol_name, name))
        for name, state in self._file_scope_object_states.items():
            if (
                state.linkage == "external"
                and state.definition_kind in ("tentative", "definition")
            ):
                defs.append(("object", state.symbol_name, name))
        return defs

    def _is_global_extern_decl(self, node):
        return (
            self.in_global
            and node.init is None
            and node.storage
            and "extern" in node.storage
            and not isinstance(node.type, c_ast.FuncDecl)
            and node.name is not None
        )

    def _extern_decl_ir_type(self, node_type):
        if isinstance(node_type, c_ast.ArrayDecl):
            return self._build_array_ir_type(node_type)
        return self._resolve_ast_type(node_type)

    def _record_extern_global(self, name, ir_type, storage=None):
        self.define(
            name,
            (
                ir_type,
                ExternGlobalRef(
                    self._file_scope_symbol_name(name, storage), ir_type
                ),
            ),
        )

    def _mark_unsigned(self, binding):
        """Mark a concrete IR binding as having unsigned type."""
        if binding is not None:
            self._unsigned_bindings.add(id(binding))

    def _mark_unsigned_pointee(self, binding):
        """Mark a pointer/array binding whose immediate pointee is unsigned."""
        if binding is not None:
            self._unsigned_pointee_bindings.add(id(binding))

    def _mark_unsigned_return(self, binding):
        """Mark a function or function-pointer binding with unsigned return."""
        if binding is not None:
            self._unsigned_return_bindings.add(id(binding))

    def _is_unsigned_val(self, val):
        """Check if a value should use unsigned operations."""
        # Check if the value was produced by an unsigned operation
        return getattr(val, "_is_unsigned", False)

    def _is_unsigned_binding(self, binding):
        return binding is not None and id(binding) in self._unsigned_bindings

    def _is_unsigned_pointee_binding(self, binding):
        return binding is not None and id(binding) in self._unsigned_pointee_bindings

    def _is_unsigned_return_binding(self, binding):
        return binding is not None and id(binding) in self._unsigned_return_bindings

    def _tag_unsigned(self, val):
        """Tag an IR value as unsigned."""
        try:
            val._is_unsigned = True
        except (AttributeError, TypeError):
            pass
        return val

    def _clear_unsigned(self, val):
        """Clear unsigned metadata from an IR value."""
        try:
            val._is_unsigned = False
        except (AttributeError, TypeError):
            pass
        return val

    def _tag_unsigned_pointee(self, val):
        try:
            val._pcc_unsigned_pointee = True
        except (AttributeError, TypeError):
            pass
        return val

    def _is_unsigned_pointee(self, val):
        return getattr(val, "_pcc_unsigned_pointee", False)

    def _tag_unsigned_return(self, val):
        try:
            val._pcc_unsigned_return = True
        except (AttributeError, TypeError):
            pass
        return val

    def _is_unsigned_return(self, val):
        return getattr(val, "_pcc_unsigned_return", False)

    def _set_expr_ir_type(self, node, ir_type):
        if node is not None:
            self._expr_ir_types[id(node)] = ir_type

    def _get_expr_ir_type(self, node, default=None):
        if node is None:
            return default
        return self._expr_ir_types.get(id(node), getattr(node, "ir_type", default))

    def _either_unsigned(self, lhs, rhs):
        """Check if either operand is unsigned (C promotion rules)."""
        return self._is_unsigned_val(lhs) or self._is_unsigned_val(rhs)

    def _int_to_float(self, val, target_type):
        if self._is_unsigned_val(val):
            return self.builder.uitofp(val, target_type)
        return self.builder.sitofp(val, target_type)

    def _convert_int_value(self, val, target_type, result_unsigned=None):
        if not (
            isinstance(getattr(val, "type", None), ir.IntType)
            and isinstance(target_type, ir.IntType)
        ):
            return self._implicit_convert(val, target_type)

        source_unsigned = self._is_unsigned_val(val)
        if val.type.width < target_type.width:
            if source_unsigned:
                result = self.builder.zext(val, target_type)
            else:
                result = self.builder.sext(val, target_type)
        elif val.type.width > target_type.width:
            result = self.builder.trunc(val, target_type)
        else:
            result = val

        if result_unsigned is None:
            result_unsigned = source_unsigned
        if result_unsigned:
            return self._tag_unsigned(result)
        return self._clear_unsigned(result)

    def _integer_promotion(self, val):
        if not isinstance(getattr(val, "type", None), ir.IntType):
            return val
        if val.type.width == 1:
            return self._clear_unsigned(self.builder.zext(val, int32_t))
        if val.type.width < int32_t.width:
            return self._convert_int_value(val, int32_t, result_unsigned=False)
        return val

    def _usual_arithmetic_conversion(self, lhs, rhs):
        lhs = self._integer_promotion(lhs)
        rhs = self._integer_promotion(rhs)

        lhs_unsigned = self._is_unsigned_val(lhs)
        rhs_unsigned = self._is_unsigned_val(rhs)
        lhs_width = lhs.type.width
        rhs_width = rhs.type.width

        if lhs_unsigned == rhs_unsigned:
            target_type = lhs.type if lhs_width >= rhs_width else rhs.type
            result_unsigned = lhs_unsigned
        elif lhs_unsigned:
            if lhs_width >= rhs_width:
                target_type = lhs.type
                result_unsigned = True
            else:
                target_type = rhs.type
                result_unsigned = False
        else:
            if rhs_width >= lhs_width:
                target_type = rhs.type
                result_unsigned = True
            else:
                target_type = lhs.type
                result_unsigned = False

        lhs = self._convert_int_value(lhs, target_type, result_unsigned)
        rhs = self._convert_int_value(rhs, target_type, result_unsigned)
        return lhs, rhs, result_unsigned

    def _shift_operand_conversion(self, lhs, rhs):
        lhs = self._integer_promotion(lhs)
        rhs = self._integer_promotion(rhs)
        if lhs.type != rhs.type:
            rhs = self._convert_int_value(
                rhs, lhs.type, result_unsigned=self._is_unsigned_val(rhs)
            )
        return lhs, rhs, self._is_unsigned_val(lhs)

    def _is_floating_ir_type(self, ir_type):
        return isinstance(ir_type, (ir.FloatType, ir.DoubleType))

    def _common_float_type(self, lhs_type, rhs_type):
        if isinstance(lhs_type, ir.DoubleType) or isinstance(rhs_type, ir.DoubleType):
            return _double
        return _float

    def _parse_float_constant(self, raw):
        value = raw.rstrip("fFlL")
        if value.lower().startswith("0x") and "p" in value.lower():
            return float.fromhex(value)
        return float(value)

    def _float_literal_ir_type(self, raw):
        if raw.endswith(("f", "F")):
            return _float
        return _double

    def _float_compare(self, op, lhs, rhs, name):
        if op == "!=":
            return self.builder.fcmp_unordered(op, lhs, rhs, name)
        return self.builder.fcmp_ordered(op, lhs, rhs, name)

    def _safe_global_var(self, ir_type, name, external=False):
        """Create or reuse a global variable, avoiding DuplicatedNameError."""
        existing = self.module.globals.get(name)
        if existing:
            return existing
        try:
            gv = ir.GlobalVariable(self.module, ir_type, name)
            if not external:
                gv.initializer = ir.Constant(ir_type, None)
            return gv
        except Exception:
            gv = self.module.globals.get(name) or ir.GlobalVariable(
                self.module, ir_type, self.module.get_unique_name(name)
            )
            if not external and getattr(gv, "initializer", None) is None:
                gv.initializer = ir.Constant(ir_type, None)
            return gv

    # External C globals lazily declared on first use.
    _EXTERN_GLOBAL_VARS = {
        "stdout": voidptr_t,
        "stderr": voidptr_t,
        "stdin": voidptr_t,
        "__stdoutp": voidptr_t,
        "__stderrp": voidptr_t,
        "__stdinp": voidptr_t,
        "errno": int32_t,
    }

    def lookup(self, name):
        if not isinstance(name, str):
            name = name.name if hasattr(name, "name") else str(name)
        if name not in self.env:
            if name in LIBC_FUNCTIONS:
                self._declare_libc(name)
            elif name in self._EXTERN_GLOBAL_VARS:
                gv_type = self._EXTERN_GLOBAL_VARS[name]
                gv = self._safe_global_var(gv_type, name, external=True)
                self.define(name, (gv_type, gv))
        stored = self.env[name]
        if not (isinstance(stored, tuple) and len(stored) == 2):
            return stored
        valtype, binding = stored
        if isinstance(binding, ExternGlobalRef):
            gv = self._safe_global_var(
                binding.ir_type, binding.symbol_name, external=True
            )
            self.define(name, (binding.ir_type, gv))
            return self.env[name]
        return valtype, binding

    def _declare_libc(self, name):
        """Lazily declare a libc function on first use."""
        existing = self.module.globals.get(name)
        if existing:
            self.define(name, (None, existing))
            self._declared_libc.add(name)
            return
        ret_type, param_types, var_arg = LIBC_FUNCTIONS[name]
        fnty = ir.FunctionType(ret_type, param_types, var_arg=var_arg)
        try:
            func = ir.Function(self.module, fnty, name=name)
        except Exception:
            func = self.module.globals.get(name)
        if isinstance(func, ir.Function):
            try:
                if name in ("setjmp", "_setjmp"):
                    func.attributes.add("returns_twice")
                elif name in ("longjmp", "_longjmp"):
                    func.attributes.add("noreturn")
            except Exception:
                pass
        self.define(name, (fnty, func))
        self._declared_libc.add(name)

    def new_label(self, name):
        self.nlabels += 1
        return f"label_{name}_{self.nlabels}"

    @contextmanager
    def new_scope(self):
        self.env = self.env.new_child()
        yield
        self.env = self.env.parents

    @contextmanager
    def new_function(self):
        oldfunc = self.function
        oldbuilder = self.builder
        oldenv = self.env
        oldlabels = self._labels
        self.in_global = False
        self.env = self.env.new_child()
        self._labels = {}
        try:
            yield
        finally:
            self.function = oldfunc
            self.builder = oldbuilder
            self.env = oldenv
            self._labels = oldlabels
            self.in_global = True

    def generate_code(self, node):
        normal = self.codegen(node)

        # for else end have no instruction
        if self.builder:
            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(64), int(0)))

        pass  # empty block fixes done in IR post-processing

        return normal

    def create_entry_block_alloca(
        self,
        name,
        type_str,
        size,
        array_list=None,
        point_level=0,
        storage=None,
        symbol_name=None,
    ):

        ir_type = get_ir_type(type_str)

        if array_list is not None:
            reversed_list = reversed(array_list)
            for dim in reversed_list:
                ir_type = ir.ArrayType(ir_type, dim)
            ir_type.dim_array = array_list

        if point_level != 0:
            if isinstance(ir_type, ir.VoidType):
                ir_type = int8_t  # void* -> i8*
            for level in range(point_level):
                ir_type = ir.PointerType(ir_type)

        if not self.in_global:
            ret = self._alloca_in_entry(ir_type, name)
            self.define(name, (ir_type, ret))
        else:
            ret = self._create_bound_global(
                name,
                ir_type,
                symbol_name=symbol_name or self._file_scope_symbol_name(name, storage),
                storage=storage,
            )

        return ret, ir_type

    def _alloca_in_entry(self, ir_type, name):
        if self.function is None:
            return self.builder.alloca(ir_type, size=None, name=name)
        entry_block = self.function.entry_basic_block
        current_block = self.builder.block if self.builder is not None else None
        entry_builder = ir.IRBuilder(entry_block)
        insert_before = None
        for inst in entry_block.instructions:
            if inst.opname not in ("phi", "alloca"):
                insert_before = inst
                break
        if insert_before is not None:
            entry_builder.position_before(insert_before)
        else:
            entry_builder.position_at_end(entry_block)
        ret = entry_builder.alloca(ir_type, size=None, name=name)
        if (
            self.builder is not None
            and current_block is entry_block
            and not current_block.is_terminated
        ):
            self.builder.position_at_end(current_block)
        return ret

    def codegen(self, node):
        if node is None:
            return None, None
        method = "codegen_" + node.__class__.__name__
        handler = getattr(self, method, None)
        if handler is None:
            return None, None
        return handler(node)

    def codegen_FileAST(self, node):
        # Collect names of functions that have definitions (FuncDef)
        funcdef_names = set()
        for ext in node.ext:
            if isinstance(ext, c_ast.FuncDef) and ext.decl:
                funcdef_names.add(ext.decl.name)
        self._funcdef_names = funcdef_names

        # Two-pass: first types/typedefs, then everything else
        pass1 = set()
        for i, ext in enumerate(node.ext):
            is_type_def = False
            if isinstance(ext, c_ast.Decl):
                if isinstance(ext.type, (c_ast.Struct, c_ast.Union, c_ast.Enum)):
                    is_type_def = True
                elif isinstance(ext.type, c_ast.TypeDecl) and isinstance(
                    ext.type.type, (c_ast.Struct, c_ast.Union)
                ):
                    is_type_def = True
            elif isinstance(ext, c_ast.Typedef):
                is_type_def = True
            if is_type_def:
                try:
                    self.codegen(ext)
                except Exception:
                    pass
                pass1.add(i)
        for i, ext in enumerate(node.ext):
            if i not in pass1:
                try:
                    self.codegen(ext)
                except Exception as e:
                    ename = type(e).__name__
                    # Non-fatal errors: skip the problematic declaration/definition
                    if ename in ("DuplicatedNameError",) or isinstance(
                        e, (AssertionError, TypeError)
                    ):
                        continue
                    if isinstance(e, KeyError) and e.args and e.args[0] is None:
                        continue
                    raise

    _escape_map = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "\\": "\\",
        "0": "\0",
        "'": "'",
        '"': '"',
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "v": "\v",
    }

    def _process_escapes(self, s):
        """Process C escape sequences in a string."""
        result = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                if s[i + 1] == "x":
                    j = i + 2
                    hex_digits = []
                    while j < len(s) and s[j] in "0123456789abcdefABCDEF":
                        hex_digits.append(s[j])
                        j += 1
                    if hex_digits:
                        result.append(chr(int("".join(hex_digits), 16) & 0xFF))
                        i = j
                        continue
                if s[i + 1] in "01234567":
                    j = i + 1
                    oct_digits = []
                    while j < len(s) and len(oct_digits) < 3 and s[j] in "01234567":
                        oct_digits.append(s[j])
                        j += 1
                    result.append(chr(int("".join(oct_digits), 8) & 0xFF))
                    i = j
                    continue
                esc = self._escape_map.get(s[i + 1])
                if esc is not None:
                    result.append(esc)
                    i += 2
                    continue
            result.append(s[i])
            i += 1
        return "".join(result)

    @staticmethod
    def _string_bytes(s):
        return bytearray((ord(ch) & 0xFF) for ch in s)

    def _char_constant_value(self, raw):
        if not raw or len(raw) < 2 or raw[0] != "'" or raw[-1] != "'":
            return 0
        processed = self._process_escapes(raw[1:-1])
        if not processed:
            return 0
        value = 0
        for ch in processed:
            value = (value << 8) | (ord(ch) & 0xFF)
        return value

    def codegen_Constant(self, node):

        if node.type == "int":
            # Support hex (0xFF), octal (077), and decimal literals
            raw = node.value
            is_unsigned = "u" in raw.lower() or "U" in raw
            val_str = raw.rstrip("uUlL")
            if val_str.startswith("0x") or val_str.startswith("0X"):
                int_val = int(val_str, 16)
            elif val_str.startswith("0") and len(val_str) > 1 and val_str[1:].isdigit():
                int_val = int(val_str, 8)
            else:
                int_val = int(val_str)
            result = ir.values.Constant(ir.IntType(64), int_val)
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.type == "char":
            # char constant like 'a' -> i8
            return (
                ir.values.Constant(
                    int8_t, self._char_constant_value(node.value) & 0xFF
                ),
                None,
            )
        elif node.type == "string":
            raw = node.value[1:-1]
            processed = self._process_escapes(raw)
            b = self._string_bytes(processed + "\00")
            n = len(b)
            array = ir.ArrayType(ir.IntType(8), n)
            tmp = ir.values.Constant(array, b)
            return tmp, None
        else:
            ir_type = self._float_literal_ir_type(node.value)
            return (
                ir.values.Constant(ir_type, self._parse_float_constant(node.value)),
                None,
            )

    def codegen_Assignment(self, node):

        lv, lv_addr = self.codegen(node.lvalue)
        rv, _ = self.codegen(node.rvalue)
        if lv is None or rv is None:
            return ir.Constant(int64_t, 0), None
        result = None

        dispatch_type_double = 1
        dispatch_type_int = 0
        dispatch_dict = {
            ("+=", dispatch_type_double): self.builder.fadd,
            ("+=", dispatch_type_int): self.builder.add,
            ("-=", dispatch_type_double): self.builder.fsub,
            ("-=", dispatch_type_int): self.builder.sub,
            ("*=", dispatch_type_double): self.builder.fmul,
            ("*=", dispatch_type_int): self.builder.mul,
            ("/=", dispatch_type_double): self.builder.fdiv,
            ("/=", dispatch_type_int): self.builder.sdiv,
            ("%=", dispatch_type_int): self.builder.srem,
            ("%=", dispatch_type_double): self.builder.frem,
            ("<<=", dispatch_type_int): self.builder.shl,
            (">>=", dispatch_type_int): self.builder.ashr,
            ("&=", dispatch_type_int): self.builder.and_,
            ("|=", dispatch_type_int): self.builder.or_,
            ("^=", dispatch_type_int): self.builder.xor,
        }
        is_unsigned = False
        # Promote mismatched types before compound assignment
        if isinstance(lv.type, ir.IntType) and isinstance(rv.type, ir.IntType):
            if node.op in ("<<=", ">>="):
                lv, rv, is_unsigned = self._shift_operand_conversion(lv, rv)
            else:
                lv, rv, is_unsigned = self._usual_arithmetic_conversion(lv, rv)
            dispatch_type = dispatch_type_int
        elif isinstance(lv.type, ir.IntType) and self._is_floating_ir_type(rv.type):
            lv = self._implicit_convert(lv, rv.type)
            dispatch_type = dispatch_type_double
        elif self._is_floating_ir_type(lv.type) and isinstance(rv.type, ir.IntType):
            rv = self._implicit_convert(rv, lv.type)
            dispatch_type = dispatch_type_double
        elif self._is_floating_ir_type(lv.type) and self._is_floating_ir_type(rv.type):
            if lv.type != rv.type:
                target = self._common_float_type(lv.type, rv.type)
                lv = self._implicit_convert(lv, target)
                rv = self._implicit_convert(rv, target)
            dispatch_type = dispatch_type_double
        else:
            dispatch_type = dispatch_type_double
        dispatch = (node.op, dispatch_type)
        handle = dispatch_dict.get(dispatch)
        # Override to unsigned for /= %= >>= when operands are unsigned
        if dispatch_type == dispatch_type_int and is_unsigned:
            if node.op == "/=":
                handle = self.builder.udiv
            elif node.op == "%=":
                handle = self.builder.urem
            elif node.op == ">>=":
                handle = self.builder.lshr

        if node.op == "=":
            # Type coercion: match rv to the target's pointee type
            if lv_addr and hasattr(lv_addr.type, "pointee"):
                target_type = lv_addr.type.pointee
            else:
                target_type = lv.type
            if rv.type != target_type:
                rv = self._implicit_convert(rv, target_type)
            self._safe_store(rv, lv_addr)
            return rv, lv_addr  # return value for chained assignment
        else:
            # Pointer compound assignment: p += n, p -= n
            if isinstance(lv.type, ir.PointerType) and isinstance(rv.type, ir.IntType):
                rv = self._integer_promotion(rv)
                rv = self._convert_int_value(rv, int64_t, result_unsigned=False)
                if node.op == "+=":
                    addresult = self.builder.gep(lv, [rv], name="ptradd")
                elif node.op == "-=":
                    neg = self.builder.neg(rv, "neg")
                    addresult = self.builder.gep(lv, [neg], name="ptrsub")
                else:
                    addresult = handle(lv, rv, "addtmp")
            else:
                addresult = handle(lv, rv, "addtmp")
            if dispatch_type == dispatch_type_int and is_unsigned:
                self._tag_unsigned(addresult)
            self._safe_store(addresult, lv_addr)
            return addresult, lv_addr

    def codegen_UnaryOp(self, node):

        result = None
        result_ptr = None

        if node.op in ("p++", "p--", "++", "--"):
            lv, lv_addr = self.codegen(node.expr)
            if lv is None:
                return ir.Constant(int64_t, 0), None
            is_post = node.op.startswith("p")
            is_inc = "+" in node.op
            if isinstance(lv.type, ir.PointerType):
                delta = ir.Constant(int64_t, 1 if is_inc else -1)
                new_val = self.builder.gep(lv, [delta], name="ptrincdec")
            else:
                one = ir.Constant(lv.type, 1)
                new_val = (
                    self.builder.add(lv, one, "inc")
                    if is_inc
                    else self.builder.sub(lv, one, "dec")
                )
                if self._is_unsigned_val(lv):
                    self._tag_unsigned(new_val)
            self._safe_store(new_val, lv_addr)
            result = lv if is_post else new_val

        elif node.op == "*":
            if (
                isinstance(node.expr, c_ast.Cast)
                and isinstance(node.expr.expr, c_ast.FuncCall)
                and isinstance(node.expr.expr.name, c_ast.ID)
                and node.expr.expr.name.name == "__builtin_va_arg"
            ):
                target_ptr_type = self._resolve_ast_type(node.expr.to_type.type)
                va_args = (
                    node.expr.expr.args.exprs if node.expr.expr.args is not None else []
                )
                if isinstance(target_ptr_type, ir.PointerType) and va_args:
                    ap_addr, _ = self.codegen(va_args[0])
                    if isinstance(getattr(ap_addr, "type", None), ir.PointerType):
                        self._vaarg_counter += 1
                        name = f"__pcc_va_arg_{self._vaarg_counter}"
                        placeholder = self.module.globals.get(name)
                        if placeholder is None:
                            placeholder = ir.Function(
                                self.module,
                                ir.FunctionType(
                                    target_ptr_type.pointee, [ap_addr.type]
                                ),
                                name=name,
                            )
                        result = self.builder.call(
                            placeholder,
                            [ap_addr],
                            name=f"vaargtmp.{self._vaarg_counter}",
                        )
                        return result, None
            name_ir, name_ptr = self.codegen(node.expr)
            if name_ptr is None and isinstance(name_ir.type, ir.ArrayType):
                result_ptr = self._decay_array_value_to_pointer(name_ir, "derefarray")
            else:
                result_ptr = name_ir
            result = self._safe_load(result_ptr)
            if self._is_unsigned_pointee(name_ir) or self._is_unsigned_pointee(
                result_ptr
            ):
                self._tag_unsigned(result)

        elif node.op == "&":
            name_ir, name_ptr = self.codegen(node.expr)
            if name_ptr is None:
                # Functions are already first-class pointers in LLVM IR.
                # Taking their address should preserve the function symbol,
                # not turn it into a null pointer.
                result = name_ir
                result_ptr = None
            else:
                result_ptr = name_ptr
                result = result_ptr
            if self._is_unsigned_binding(result_ptr):
                self._tag_unsigned_pointee(result)
            if self._is_unsigned_return_binding(result_ptr):
                self._tag_unsigned_return(result)

        elif node.op == "+":
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.IntType):
                operand = self._integer_promotion(operand)
            result = operand  # unary plus is a no-op

        elif node.op == "-":
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.IntType):
                operand = self._integer_promotion(operand)
                result = self.builder.neg(operand, "negtmp")
                if self._is_unsigned_val(operand):
                    self._tag_unsigned(result)
            else:
                result = self.builder.fneg(operand, "negtmp")

        elif node.op == "!":
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.PointerType):
                null = ir.Constant(operand.type, None)
                cmp = self.builder.icmp_unsigned("==", operand, null, "nottmp")
                result = self.builder.zext(cmp, int64_t, "notres")
            elif isinstance(operand.type, ir.IntType):
                cmp = self.builder.icmp_signed(
                    "==", operand, ir.Constant(operand.type, 0), "nottmp"
                )
                result = self.builder.zext(cmp, int64_t, "notres")
            else:
                cmp = self.builder.fcmp_ordered(
                    "==", operand, ir.Constant(operand.type, 0.0), "nottmp"
                )
                result = self.builder.zext(cmp, int64_t, "notres")

        elif node.op == "~":
            operand, _ = self.codegen(node.expr)
            if isinstance(operand.type, ir.IntType):
                operand = self._integer_promotion(operand)
            result = self.builder.not_(operand, "invtmp")
            if self._is_unsigned_val(operand):
                self._tag_unsigned(result)

        elif node.op == "sizeof":
            result = self._codegen_sizeof(node.expr)

        return result, result_ptr

    def _codegen_sizeof(self, expr):
        """Return sizeof as an i64 constant (always unsigned in C)."""
        if isinstance(expr, c_ast.Typename):
            ir_t = self._resolve_ast_type(expr.type)
            size = self._ir_type_size(ir_t)
        elif isinstance(expr, c_ast.ID):
            ir_type, _ = self.lookup(expr.name)
            size = self._ir_type_size(ir_type)
        else:
            val, _ = self.codegen(expr)
            semantic_type = self._get_expr_ir_type(expr, getattr(val, "type", None))
            size = self._ir_type_size(semantic_type)
        result = ir.Constant(int64_t, size)
        return self._tag_unsigned(result)

    def _resolve_type_str(self, type_str, depth=0):
        """Resolve typedef'd type names to their base type string."""
        if depth > 10:
            return type_str  # prevent infinite recursion
        if isinstance(type_str, list):
            type_str = type_str[0] if len(type_str) == 1 else type_str
        if isinstance(type_str, list):
            return type_str  # multi-word type, not a typedef
        key = f"__typedef_{type_str}"
        if key in self.env:
            resolved = self.env[key]
            if isinstance(resolved, str):
                # Could be a __struct_ reference or a base type name
                if resolved.startswith("__struct_"):
                    struct_name = resolved[len("__struct_") :]
                    if struct_name in self.env:
                        return self.env[struct_name][0]
                    return int8_t  # opaque
                # Recursively resolve further typedefs
                return self._resolve_type_str(resolved, depth + 1)
            if isinstance(resolved, ir.Type):
                return resolved
            # resolved is a list — recursively resolve single-element lists
            if isinstance(resolved, list) and len(resolved) == 1:
                return self._resolve_type_str(resolved[0], depth + 1)
            return resolved
        return type_str

    def _get_ir_type(self, type_str):
        """Get IR type, resolving typedefs."""
        resolved = self._resolve_type_str(type_str)
        if isinstance(resolved, ir.Type):
            return resolved
        return get_ir_type(resolved)

    def _is_unsigned_type_names(self, type_str):
        """Check if a type name list resolves to an unsigned type."""
        if isinstance(type_str, list):
            if _is_unsigned_names(type_str):
                return True
            # Single-element list: check typedef chain
            if len(type_str) == 1:
                return self._is_unsigned_type_names(type_str[0])
            s = " ".join(sorted(type_str))
            return s in _UNSIGNED_TYPE_NAMES
        # String: check typedef chain
        key = f"__typedef_{type_str}"
        if key in self.env:
            resolved = self.env[key]
            if isinstance(resolved, list):
                return self._is_unsigned_type_names(resolved)
            if isinstance(resolved, str):
                return self._is_unsigned_type_names(resolved)
        return type_str in _UNSIGNED_TYPE_NAMES or type_str == "size_t"

    def _is_unsigned_scalar_decl_type(self, node_type):
        if not isinstance(node_type, c_ast.TypeDecl):
            return False
        inner = node_type.type
        if not isinstance(inner, c_ast.IdentifierType):
            return False
        return self._is_unsigned_type_names(inner.names)

    def _has_unsigned_scalar_pointee(self, node_type):
        if isinstance(node_type, (c_ast.ArrayDecl, c_ast.PtrDecl)):
            return self._is_unsigned_scalar_decl_type(node_type.type)
        return False

    def _func_decl_returns_unsigned(self, node_type):
        return isinstance(
            node_type, c_ast.FuncDecl
        ) and self._is_unsigned_scalar_decl_type(node_type.type)

    def _tag_value_from_decl_type(self, value, decl_type):
        if value is None:
            return value
        if isinstance(getattr(value, "type", None), ir.IntType):
            if self._is_unsigned_scalar_decl_type(decl_type):
                self._tag_unsigned(value)
            elif isinstance(decl_type, c_ast.TypeDecl):
                self._clear_unsigned(value)
        if self._has_unsigned_scalar_pointee(decl_type) and isinstance(
            getattr(value, "type", None), ir.PointerType
        ):
            self._tag_unsigned_pointee(value)
        if (
            isinstance(decl_type, c_ast.PtrDecl)
            and self._func_decl_returns_unsigned(decl_type.type)
            and isinstance(getattr(value, "type", None), ir.PointerType)
        ):
            self._tag_unsigned_return(value)
        return value

    def _build_const_array_init(self, init_list, array_type, elem_ir_type):
        """Build a constant initializer for a global array."""
        actual_elem = (
            array_type.element if isinstance(array_type, ir.ArrayType) else elem_ir_type
        )
        values = []
        for expr in init_list.exprs:
            if isinstance(expr, c_ast.InitList):
                sub_type = actual_elem
                values.append(
                    self._build_const_array_init(expr, sub_type, elem_ir_type)
                )
            else:
                try:
                    val = self._eval_const_expr(expr)
                    c = ir.Constant(actual_elem, val)
                    str(c)  # verify serializable
                    values.append(c)
                except Exception:
                    values.append(ir.Constant(actual_elem, None))
        try:
            result = ir.Constant(array_type, values)
            str(result)  # verify
            return result
        except Exception:
            return ir.Constant(array_type, None)

    def _zero_initializer(self, ir_type):
        if isinstance(ir_type, ir.PointerType):
            return ir.Constant(ir_type, None)
        if self._is_floating_ir_type(ir_type):
            return ir.Constant(ir_type, 0.0)
        if isinstance(ir_type, ir.IntType):
            return ir.Constant(ir_type, 0)
        return ir.Constant(ir_type, None)

    def _make_global_string_constant(self, raw, name_hint="str"):
        processed = self._process_escapes(raw)
        data = self._string_bytes(processed + "\00")
        arr_type = ir.ArrayType(int8_t, len(data))
        gv = ir.GlobalVariable(
            self.module, arr_type, self.module.get_unique_name(name_hint)
        )
        gv.initializer = ir.Constant(arr_type, data)
        gv.global_constant = True
        gv.linkage = "internal"
        return gv

    def _const_pointer_to_first_elem(self, gv, target_type):
        idx0 = ir.Constant(ir.IntType(32), 0)
        ptr = gv.gep([idx0, idx0])
        return ptr if ptr.type == target_type else ptr.bitcast(target_type)

    def _is_little_endian(self):
        return not str(self.module.data_layout).startswith("E")

    def _zero_bytes(self, size):
        return [ir.Constant(int8_t, 0) for _ in range(size)]

    def _scalar_init_node(self, init_node):
        if not isinstance(init_node, c_ast.InitList):
            return init_node
        if not init_node.exprs:
            return None
        return self._scalar_init_node(init_node.exprs[0])

    def _build_pointer_const(self, init_node, ir_type):
        if isinstance(init_node, c_ast.InitList):
            if init_node.exprs:
                return self._build_pointer_const(init_node.exprs[0], ir_type)
            return ir.Constant(ir_type, None)
        if (
            isinstance(init_node, c_ast.Constant)
            and getattr(init_node, "type", None) == "string"
        ):
            gv = self._make_global_string_constant(init_node.value[1:-1])
            return self._const_pointer_to_first_elem(gv, ir_type)
        if isinstance(init_node, c_ast.ID):
            try:
                _, sym = self.lookup(init_node.name)
            except Exception:
                sym = None
            if isinstance(sym, ir.Function):
                return sym if sym.type == ir_type else sym.bitcast(ir_type)
            if isinstance(sym, ir.GlobalVariable):
                if isinstance(sym.value_type, ir.ArrayType):
                    return self._const_pointer_to_first_elem(sym, ir_type)
                if sym.type == ir_type:
                    return sym
                if isinstance(sym.type, ir.PointerType):
                    return sym.bitcast(ir_type)
        if (
            isinstance(init_node, c_ast.UnaryOp)
            and init_node.op == "&"
            and isinstance(init_node.expr, c_ast.ID)
        ):
            try:
                _, sym = self.lookup(init_node.expr.name)
            except Exception:
                sym = None
            if isinstance(sym, ir.Function):
                return sym if sym.type == ir_type else sym.bitcast(ir_type)
            if isinstance(sym, ir.GlobalVariable):
                if sym.type == ir_type:
                    return sym
                if isinstance(sym.type, ir.PointerType):
                    return sym.bitcast(ir_type)
        try:
            val = self._eval_const_expr(init_node)
            if val == 0:
                return ir.Constant(ir_type, None)
        except Exception:
            return None
        return None

    def _const_int_to_bytes(self, value, byte_width):
        if byte_width <= 0:
            return []
        mask = (1 << (byte_width * 8)) - 1
        raw = int(value) & mask
        return [
            ir.Constant(int8_t, b)
            for b in raw.to_bytes(
                byte_width,
                byteorder="little" if self._is_little_endian() else "big",
                signed=False,
            )
        ]

    def _split_int_constant_to_bytes(self, int_const, byte_width):
        if byte_width <= 0:
            return []

        raw_const = getattr(int_const, "constant", None)
        if isinstance(raw_const, int):
            return self._const_int_to_bytes(raw_const, byte_width)

        int_bits = byte_width * 8
        if int_const.type.width != int_bits:
            if int_const.type.width < int_bits:
                int_const = int_const.zext(ir.IntType(int_bits))
            else:
                int_const = int_const.trunc(ir.IntType(int_bits))

        byte_values = []
        for i in range(byte_width):
            shift_bits = 8 * (i if self._is_little_endian() else (byte_width - 1 - i))
            part = int_const
            if shift_bits:
                part = part.lshr(ir.Constant(part.type, shift_bits))
            if part.type.width != 8:
                part = part.trunc(int8_t)
            byte_values.append(part)
        return byte_values

    def _pointer_const_to_bytes(self, ptr_const):
        if (
            isinstance(ptr_const, ir.Constant)
            and getattr(ptr_const, "constant", None) is None
        ):
            return self._zero_bytes(self._ir_type_size(ptr_const.type))
        return self._split_int_constant_to_bytes(
            ptr_const.ptrtoint(int64_t), self._ir_type_size(ptr_const.type)
        )

    def _bytes_to_int_constant(self, byte_values, int_type):
        byte_width = int_type.width // 8
        values = list(byte_values[:byte_width])
        if len(values) < byte_width:
            values.extend(self._zero_bytes(byte_width - len(values)))

        result = 0
        for i, byte_val in enumerate(values):
            shift_bits = 8 * (i if self._is_little_endian() else (byte_width - 1 - i))
            raw = getattr(byte_val, "constant", 0)
            if not isinstance(raw, int):
                raw = 0
            result |= (raw & 0xFF) << shift_bits

        bits = int_type.width
        mask = (1 << bits) - 1
        result &= mask
        sign_bit = 1 << (bits - 1)
        if result & sign_bit:
            result -= 1 << bits
        return ir.Constant(int_type, result)

    def _const_init_bytes(self, init_node, ir_type):
        size = self._ir_type_size(ir_type)
        if init_node is None:
            return self._zero_bytes(size)

        if getattr(ir_type, "is_union", False):
            raw = self._zero_bytes(size)
            member_names = getattr(ir_type, "members", None) or list(
                ir_type.member_types.keys()
            )
            if not member_names:
                return raw

            first_name = member_names[0]
            member_type = ir_type.member_types[first_name]
            member_init = init_node
            if isinstance(init_node, c_ast.InitList):
                exprs = init_node.exprs or []
                if not exprs:
                    member_init = None
                elif isinstance(member_type, (ir.ArrayType, ir.LiteralStructType)):
                    member_init = (
                        exprs[0]
                        if len(exprs) == 1 and isinstance(exprs[0], c_ast.InitList)
                        else init_node
                    )
                else:
                    member_init = exprs[0]

            member_bytes = self._const_init_bytes(member_init, member_type)
            raw[: min(size, len(member_bytes))] = member_bytes[:size]
            return raw

        if isinstance(ir_type, ir.PointerType):
            ptr_const = self._build_pointer_const(init_node, ir_type)
            if ptr_const is None:
                return self._zero_bytes(size)
            return self._pointer_const_to_bytes(ptr_const)

        if self._is_floating_ir_type(ir_type):
            scalar_node = self._scalar_init_node(init_node)
            if scalar_node is None:
                value = 0.0
            elif isinstance(scalar_node, c_ast.Constant):
                try:
                    value = self._parse_float_constant(scalar_node.value)
                except ValueError:
                    value = float(self._eval_const_expr(scalar_node))
            else:
                value = float(self._eval_const_expr(scalar_node))
            fmt = "d" if isinstance(ir_type, ir.DoubleType) else "f"
            packed = struct.pack(
                ("<" if self._is_little_endian() else ">") + fmt,
                value,
            )
            return [ir.Constant(int8_t, b) for b in packed]

        if isinstance(ir_type, ir.IntType):
            scalar_node = self._scalar_init_node(init_node)
            if scalar_node is None:
                return self._zero_bytes(size)
            return self._const_int_to_bytes(self._eval_const_expr(scalar_node), size)

        if isinstance(ir_type, ir.ArrayType):
            if (
                isinstance(init_node, c_ast.Constant)
                and getattr(init_node, "type", None) == "string"
                and isinstance(ir_type.element, ir.IntType)
                and ir_type.element.width == 8
            ):
                raw = init_node.value[1:-1]
                processed = self._process_escapes(raw)
                data = self._string_bytes(processed + "\00")
                if len(data) < ir_type.count:
                    data.extend(b"\x00" * (ir_type.count - len(data)))
                else:
                    data = data[: ir_type.count]
                return [ir.Constant(int8_t, b) for b in data]

            if isinstance(init_node, c_ast.InitList):
                values = []
                for i in range(ir_type.count):
                    expr = init_node.exprs[i] if i < len(init_node.exprs) else None
                    values.extend(self._const_init_bytes(expr, ir_type.element))
                return values

            return self._zero_bytes(size)

        if isinstance(ir_type, ir.LiteralStructType):
            raw = self._zero_bytes(size)
            if not isinstance(init_node, c_ast.InitList):
                return raw

            offset = 0
            for i, member_type in enumerate(ir_type.elements):
                align = self._ir_type_align(member_type)
                offset = (offset + align - 1) & ~(align - 1)
                expr = init_node.exprs[i] if i < len(init_node.exprs) else None
                field_bytes = self._const_init_bytes(expr, member_type)
                field_size = self._ir_type_size(member_type)
                raw[offset : offset + field_size] = field_bytes[:field_size]
                offset += field_size
            return raw

        scalar_node = self._scalar_init_node(init_node)
        if scalar_node is None:
            return self._zero_bytes(size)
        try:
            return self._const_int_to_bytes(self._eval_const_expr(scalar_node), size)
        except Exception:
            return self._zero_bytes(size)

    def _build_const_init(self, init_node, ir_type):
        if init_node is None:
            return self._zero_initializer(ir_type)

        if getattr(ir_type, "is_union", False):
            try:
                raw = self._const_init_bytes(init_node, ir_type)
                fields = []
                head_type = ir_type.elements[0]
                if not isinstance(head_type, ir.IntType):
                    return self._zero_initializer(ir_type)
                head_size = self._ir_type_size(head_type)
                fields.append(self._bytes_to_int_constant(raw[:head_size], head_type))
                if len(ir_type.elements) > 1:
                    tail_type = ir_type.elements[1]
                    tail_size = self._ir_type_size(tail_type)
                    tail_bytes = raw[head_size : head_size + tail_size]
                    fields.append(ir.Constant(tail_type, tail_bytes))
                return ir.Constant(ir_type, fields)
            except Exception:
                return self._zero_initializer(ir_type)

        if isinstance(ir_type, ir.PointerType):
            ptr_const = self._build_pointer_const(init_node, ir_type)
            if ptr_const is not None:
                return ptr_const
            return self._zero_initializer(ir_type)

        if isinstance(ir_type, ir.ArrayType):
            if (
                isinstance(init_node, c_ast.Constant)
                and getattr(init_node, "type", None) == "string"
            ):
                raw = init_node.value[1:-1]
                processed = self._process_escapes(raw)
                data = self._string_bytes(processed + "\00")
                if len(data) < ir_type.count:
                    data.extend(b"\x00" * (ir_type.count - len(data)))
                else:
                    data = data[: ir_type.count]
                try:
                    return ir.Constant(ir_type, data)
                except Exception:
                    return self._zero_initializer(ir_type)

            if isinstance(init_node, c_ast.InitList):
                values = []
                for i in range(ir_type.count):
                    expr = init_node.exprs[i] if i < len(init_node.exprs) else None
                    values.append(self._build_const_init(expr, ir_type.element))
                try:
                    return ir.Constant(ir_type, values)
                except Exception:
                    return self._zero_initializer(ir_type)

            return self._zero_initializer(ir_type)

        if isinstance(ir_type, ir.LiteralStructType):
            if isinstance(init_node, c_ast.InitList):
                values = []
                for i, member_type in enumerate(ir_type.elements):
                    expr = init_node.exprs[i] if i < len(init_node.exprs) else None
                    values.append(self._build_const_init(expr, member_type))
                try:
                    return ir.Constant(ir_type, values)
                except Exception:
                    return self._zero_initializer(ir_type)
            return self._zero_initializer(ir_type)

        if isinstance(init_node, c_ast.InitList):
            if init_node.exprs:
                return self._build_const_init(init_node.exprs[0], ir_type)
            return self._zero_initializer(ir_type)

        try:
            val = self._eval_const_expr(init_node)
            result = ir.Constant(ir_type, val)
            str(result)
            return result
        except Exception:
            return self._zero_initializer(ir_type)

    def _init_array(self, base_addr, init_list, elem_ir_type, prefix_idx):
        """Recursively initialize array elements from an InitList."""
        for i, expr in enumerate(init_list.exprs):
            idx = prefix_idx + [ir.Constant(ir.IntType(32), i)]
            if isinstance(expr, c_ast.InitList):
                self._init_array(base_addr, expr, elem_ir_type, idx)
            else:
                val, _ = self.codegen(expr)
                val = self._implicit_convert(val, elem_ir_type)
                elem_ptr = self.builder.gep(base_addr, idx, inbounds=True)
                self._safe_store(val, elem_ptr)

    def _build_array_ir_type(self, array_decl):
        dims = []
        node = array_decl
        while isinstance(node, c_ast.ArrayDecl):
            dims.append(self._eval_dim(node.dim) if node.dim else 0)
            node = node.type
        elem_ir_type = self._resolve_ast_type(node)
        if isinstance(elem_ir_type, ir.VoidType):
            elem_ir_type = int8_t
        arr_ir_type = elem_ir_type
        for dim in reversed(dims):
            arr_ir_type = ir.ArrayType(arr_ir_type, dim)
        arr_ir_type.dim_array = dims
        return arr_ir_type

    def _resolve_param_type(self, param):
        """Resolve a function parameter type, handling typedefs and pointers."""
        if isinstance(param.type, c_ast.ArrayDecl):
            arr_type = self._build_array_ir_type(param.type)
            return ir.PointerType(arr_type.element)
        t = self._resolve_ast_type(param.type)
        if isinstance(t, ir.ArrayType):
            return ir.PointerType(t.element)
        if isinstance(t, ir.VoidType):
            return None  # void params mean "no params" in C
        return t

    def _resolve_ast_type(self, node_type):
        """Recursively resolve an AST type to IR type, with typedef support."""
        if isinstance(node_type, c_ast.PtrDecl):
            inner = node_type.type
            if isinstance(inner, c_ast.FuncDecl):
                return self._build_func_ptr_type(inner)
            pointee = self._resolve_ast_type(inner)
            if isinstance(pointee, ir.VoidType):
                return voidptr_t
            return ir.PointerType(pointee)
        elif isinstance(node_type, c_ast.TypeDecl):
            if isinstance(node_type.type, c_ast.IdentifierType):
                return self._get_ir_type(node_type.type.names)
            elif isinstance(node_type.type, c_ast.Struct):
                return self.codegen_Struct(node_type.type)
            elif isinstance(node_type.type, c_ast.Union):
                return self.codegen_Union(node_type.type)
            return int64_t
        elif isinstance(node_type, c_ast.ArrayDecl):
            return voidptr_t
        return int64_t

    def _eval_dim(self, dim_node):
        """Evaluate array dimension (may be a constant or expression)."""
        if dim_node is None:
            return 0
        if isinstance(dim_node, c_ast.Constant):
            v = dim_node.value.rstrip("uUlL")
            return int(v, 0)  # handles hex/octal/decimal
        return self._eval_const_expr(dim_node)

    def _build_func_ptr_type(self, func_decl_node):
        """Build an IR function pointer type from a FuncDecl AST node."""
        ret_ir, _ = self.codegen(func_decl_node)
        param_types = []
        if func_decl_node.args:
            for param in func_decl_node.args.params:
                if isinstance(param, c_ast.EllipsisParam):
                    continue
                if isinstance(param, c_ast.Typename):
                    t = self._resolve_ast_type(param.type)
                    if not isinstance(t, ir.VoidType):
                        param_types.append(t)
                elif isinstance(param, c_ast.Decl):
                    t = self._resolve_param_type(param)
                    if t is not None:
                        param_types.append(t)
        if isinstance(ret_ir, ir.VoidType):
            ret_ir = ir.VoidType()
        func_type = ir.FunctionType(ret_ir, param_types)
        return func_type.as_pointer()

    def _safe_load(self, ptr, name=""):
        """Load from ptr, guard against non-pointer types."""
        if not isinstance(ptr.type, ir.PointerType):
            return ptr
        if isinstance(ptr.type.pointee, ir.FunctionType):
            return ptr  # function pointers are first-class as pointers
        try:
            return self.builder.load(ptr, name=name)
        except Exception:
            return ptr

    def _decay_array_value_to_pointer(self, value, name="arraydecay"):
        """Convert an array value (including string literals) to &value[0]."""
        if not isinstance(value.type, ir.ArrayType):
            return value
        base = value
        if isinstance(value, ir.values.Constant):
            gv = ir.GlobalVariable(
                self.module, value.type, self.module.get_unique_name("strlit")
            )
            gv.initializer = value
            gv.global_constant = True
            gv.linkage = "internal"
            base = gv
        idx0 = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(base, [idx0, idx0], name=name)

    def _safe_store(self, value, ptr):
        """Store value to ptr, auto-converting types if needed."""
        if value is None or ptr is None:
            return
        if isinstance(value.type, ir.VoidType):
            return  # Can't store void
        if not isinstance(ptr.type, ir.PointerType):
            return
        if hasattr(ptr.type, "pointee") and value.type != ptr.type.pointee:
            value = self._implicit_convert(value, ptr.type.pointee)
        try:
            self.builder.store(value, ptr)
        except (TypeError, Exception):
            pass

    def _implicit_convert(self, val, target_type):
        """Convert val to target_type if needed (implicit C promotion/truncation)."""
        if val is None or isinstance(val.type, ir.VoidType):
            # Can't convert void — return a zero of target type
            if isinstance(target_type, ir.PointerType):
                return ir.Constant(target_type, None)
            elif isinstance(target_type, ir.VoidType):
                return val
            return ir.Constant(target_type, 0)
        if val.type == target_type:
            return val
        if isinstance(val.type, ir.IntType) and self._is_floating_ir_type(target_type):
            return self._int_to_float(val, target_type)
        if self._is_floating_ir_type(val.type) and isinstance(target_type, ir.IntType):
            return self.builder.fptosi(val, target_type)
        if self._is_floating_ir_type(val.type) and self._is_floating_ir_type(
            target_type
        ):
            if isinstance(val.type, ir.FloatType) and isinstance(
                target_type, ir.DoubleType
            ):
                return self.builder.fpext(val, target_type)
            if isinstance(val.type, ir.DoubleType) and isinstance(
                target_type, ir.FloatType
            ):
                return self.builder.fptrunc(val, target_type)
            return val
        # int -> int (wider or narrower)
        if isinstance(val.type, ir.IntType) and isinstance(target_type, ir.IntType):
            if val.type.width < target_type.width:
                if self._is_unsigned_val(val):
                    result = self.builder.zext(val, target_type)
                    return self._tag_unsigned(result)
                return self.builder.sext(val, target_type)
            elif val.type.width > target_type.width:
                result = self.builder.trunc(val, target_type)
                if self._is_unsigned_val(val):
                    return self._tag_unsigned(result)
                return result
        # int -> pointer (e.g., NULL assignment, p = 0)
        if isinstance(val.type, ir.IntType) and isinstance(target_type, ir.PointerType):
            # inttoptr only works for simple pointer types, not function pointers
            raw_ptr = self.builder.inttoptr(val, voidptr_t)
            if target_type == voidptr_t:
                return raw_ptr
            return self.builder.bitcast(raw_ptr, target_type)
        # pointer -> int
        if isinstance(val.type, ir.PointerType) and isinstance(target_type, ir.IntType):
            return self.builder.ptrtoint(val, target_type)
        # pointer -> different pointer
        if isinstance(val.type, ir.PointerType) and isinstance(
            target_type, ir.PointerType
        ):
            result = self.builder.bitcast(val, target_type)
            if self._is_unsigned_pointee(val):
                self._tag_unsigned_pointee(result)
            if self._is_unsigned_return(val):
                self._tag_unsigned_return(result)
            return result
        # array -> pointer (string literal to char*)
        if isinstance(val.type, ir.ArrayType) and isinstance(
            target_type, ir.PointerType
        ):
            ptr = self._decay_array_value_to_pointer(val)
            if ptr.type == target_type:
                return ptr
            return self.builder.bitcast(ptr, target_type)
        return val

    def _extend_call_result(self, result, returns_unsigned=False):
        if not isinstance(result.type, ir.IntType):
            return result
        if returns_unsigned:
            self._tag_unsigned(result)
        else:
            self._clear_unsigned(result)
        return result

    def _to_bool(self, val, name="cond"):
        """Convert any value to an i1 boolean (!=0)."""
        if isinstance(val.type, ir.IntType):
            if val.type.width == 1:
                return val
            return self.builder.icmp_signed("!=", val, ir.Constant(val.type, 0), name)
        elif isinstance(val.type, ir.PointerType):
            null = ir.Constant(val.type, None)
            return self.builder.icmp_unsigned("!=", val, null, name)
        else:
            return self.builder.fcmp_unordered(
                "!=", val, ir.Constant(val.type, 0.0), name
            )

    def _ir_type_align(self, ir_type):
        """Return natural alignment of an IR type in bytes."""
        if isinstance(ir_type, ir.IntType):
            return min(ir_type.width // 8, 8)
        elif isinstance(ir_type, ir.FloatType):
            return 4
        elif isinstance(ir_type, ir.DoubleType):
            return 8
        elif isinstance(ir_type, ir.PointerType):
            return 8
        elif isinstance(ir_type, ir.ArrayType):
            return self._ir_type_align(ir_type.element)
        elif isinstance(ir_type, ir.LiteralStructType):
            if not ir_type.elements:
                return 1
            return max(self._ir_type_align(e) for e in ir_type.elements)
        return 8

    def _ir_type_size(self, ir_type):
        """Compute byte size of an IR type with proper alignment/padding."""
        if isinstance(ir_type, ir.IntType):
            return ir_type.width // 8
        elif isinstance(ir_type, ir.FloatType):
            return 4
        elif isinstance(ir_type, ir.DoubleType):
            return 8
        elif isinstance(ir_type, ir.PointerType):
            return 8
        elif isinstance(ir_type, ir.ArrayType):
            return int(ir_type.count) * self._ir_type_size(ir_type.element)
        elif isinstance(ir_type, ir.LiteralStructType):
            offset = 0
            for elem in ir_type.elements:
                align = self._ir_type_align(elem)
                offset = (offset + align - 1) & ~(align - 1)  # align up
                offset += self._ir_type_size(elem)
            # Tail padding: align to struct's overall alignment
            struct_align = self._ir_type_align(ir_type)
            offset = (offset + struct_align - 1) & ~(struct_align - 1)
            return offset
        return 8

    def _refine_member_ir_type(self, aggregate_type, member_key, field_type):
        """Prefer semantic member types over storage types when available."""
        semantic_field_type = field_type
        member_decl_types = getattr(aggregate_type, "member_decl_types", None)
        decl_type = None

        if isinstance(member_decl_types, dict):
            decl_type = member_decl_types.get(member_key)
        elif (
            isinstance(member_key, int)
            and member_decl_types is not None
            and member_key < len(member_decl_types)
        ):
            decl_type = member_decl_types[member_key]

        if decl_type is None:
            return semantic_field_type

        try:
            resolved = self._resolve_ast_type(decl_type)
            if isinstance(field_type, ir.ArrayType) and isinstance(
                resolved, ir.PointerType
            ):
                return semantic_field_type
            if isinstance(
                resolved, (ir.ArrayType, ir.LiteralStructType, ir.PointerType)
            ):
                return resolved
        except Exception:
            pass

        return semantic_field_type

    def _get_aggregate_field_info(self, aggregate_type, field_name):
        """Return byte offset and semantic IR type for a struct/union field."""
        if getattr(aggregate_type, "is_union", False):
            field_type = aggregate_type.member_types[field_name]
            semantic_field_type = self._refine_member_ir_type(
                aggregate_type, field_name, field_type
            )
            return 0, semantic_field_type

        if not hasattr(aggregate_type, "members"):
            raise CodegenError(f"Aggregate has no named fields: {aggregate_type}")

        field_index = None
        for i, member in enumerate(aggregate_type.members):
            if member == field_name:
                field_index = i
                break

        if field_index is None:
            raise CodegenError(f"Field '{field_name}' not found in aggregate")

        offset = 0
        for i, elem in enumerate(aggregate_type.elements):
            align = self._ir_type_align(elem)
            offset = (offset + align - 1) & ~(align - 1)
            if i == field_index:
                field_type = aggregate_type.elements[field_index]
                semantic_field_type = self._refine_member_ir_type(
                    aggregate_type, field_index, field_type
                )
                return offset, semantic_field_type
            offset += self._ir_type_size(elem)

        raise CodegenError(f"Field '{field_name}' not found in aggregate")

    def _eval_offsetof_structref(self, node):
        """Evaluate offsetof-like expressions expanded as &((T*)0)->field."""
        if isinstance(node, c_ast.StructRef):
            base_offset, base_type = self._eval_offsetof_structref(node.name)
            aggregate_type = base_type
            if node.type == "->" and isinstance(aggregate_type, ir.PointerType):
                aggregate_type = aggregate_type.pointee
            field_offset, field_type = self._get_aggregate_field_info(
                aggregate_type, node.field.name
            )
            return base_offset + field_offset, field_type

        if isinstance(node, c_ast.Cast):
            target_type = self._resolve_ast_type(node.to_type.type)
            return 0, target_type

        raise CodegenError(f"Not an offsetof base: {type(node).__name__}")

    def codegen_Typename(self, node):
        # Used inside sizeof(type) — not directly code-generated
        return None, None

    def codegen_BinaryOp(self, node):
        # Short-circuit && and || before evaluating both sides
        if node.op == "&&":
            return self._codegen_short_circuit_and(node)
        elif node.op == "||":
            return self._codegen_short_circuit_or(node)

        lhs, _ = self.codegen(node.left)
        rhs, _ = self.codegen(node.right)
        if lhs is None or rhs is None:
            return ir.Constant(int64_t, 0), None

        # Pointer arithmetic: ptr + int or ptr - int
        if (
            node.op in ("+", "-")
            and isinstance(lhs.type, ir.PointerType)
            and isinstance(rhs.type, ir.IntType)
        ):
            rhs = self._integer_promotion(rhs)
            rhs = self._convert_int_value(rhs, int64_t, result_unsigned=False)
            if node.op == "-":
                rhs = self.builder.neg(rhs, "negidx")
            return self.builder.gep(lhs, [rhs], name="ptradd"), None
        if (
            node.op == "+"
            and isinstance(rhs.type, ir.PointerType)
            and isinstance(lhs.type, ir.IntType)
        ):
            lhs = self._integer_promotion(lhs)
            lhs = self._convert_int_value(lhs, int64_t, result_unsigned=False)
            return self.builder.gep(rhs, [lhs], name="ptradd"), None

        # Pointer subtraction: ptr - ptr -> int (element count)
        if (
            node.op == "-"
            and isinstance(lhs.type, ir.PointerType)
            and isinstance(rhs.type, ir.PointerType)
        ):
            lhs_int = self.builder.ptrtoint(lhs, int64_t)
            rhs_int = self.builder.ptrtoint(rhs, int64_t)
            diff = self.builder.sub(lhs_int, rhs_int, "ptrdiff")
            elem_size = self._ir_type_size(lhs.type.pointee)
            return (
                self.builder.sdiv(
                    diff, ir.Constant(int64_t, elem_size), "ptrdiff_elems"
                ),
                None,
            )

        # Promote int/pointer mix
        if isinstance(lhs.type, ir.PointerType) and isinstance(rhs.type, ir.IntType):
            rhs = self._implicit_convert(rhs, lhs.type)
        elif isinstance(rhs.type, ir.PointerType) and isinstance(lhs.type, ir.IntType):
            lhs = self._implicit_convert(lhs, rhs.type)

        # Promotion above can turn int/pointer into ptr/ptr; handle subtraction
        if (
            node.op == "-"
            and isinstance(lhs.type, ir.PointerType)
            and isinstance(rhs.type, ir.PointerType)
        ):
            lhs_int = self.builder.ptrtoint(lhs, int64_t)
            rhs_int = self.builder.ptrtoint(rhs, int64_t)
            diff = self.builder.sub(lhs_int, rhs_int, "ptrdiff")
            elem_size = self._ir_type_size(lhs.type.pointee)
            return (
                self.builder.sdiv(
                    diff, ir.Constant(int64_t, elem_size), "ptrdiff_elems"
                ),
                None,
            )

        is_unsigned = False
        if isinstance(lhs.type, ir.IntType) and self._is_floating_ir_type(rhs.type):
            lhs = self._implicit_convert(lhs, rhs.type)
        elif self._is_floating_ir_type(lhs.type) and isinstance(rhs.type, ir.IntType):
            rhs = self._implicit_convert(rhs, lhs.type)
        elif self._is_floating_ir_type(lhs.type) and self._is_floating_ir_type(
            rhs.type
        ):
            if lhs.type != rhs.type:
                target = self._common_float_type(lhs.type, rhs.type)
                lhs = self._implicit_convert(lhs, target)
                rhs = self._implicit_convert(rhs, target)
        elif isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.IntType):
            if node.op in ("<<", ">>"):
                lhs, rhs, is_unsigned = self._shift_operand_conversion(lhs, rhs)
            else:
                lhs, rhs, is_unsigned = self._usual_arithmetic_conversion(lhs, rhs)

        dispatch_type_double = 1
        dispatch_type_int = 0

        if isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.IntType):
            dispatch_type = dispatch_type_int
        else:
            dispatch_type = dispatch_type_double

        if node.op in ["+", "-", "*", "/", "%"]:
            if dispatch_type == dispatch_type_double:
                ops = {
                    "+": self.builder.fadd,
                    "-": self.builder.fsub,
                    "*": self.builder.fmul,
                    "/": self.builder.fdiv,
                    "%": self.builder.frem,
                }
                return ops[node.op](lhs, rhs, "tmp"), None
            else:
                if node.op in ("/", "%") and is_unsigned:
                    op = self.builder.udiv if node.op == "/" else self.builder.urem
                else:
                    ops = {
                        "+": self.builder.add,
                        "-": self.builder.sub,
                        "*": self.builder.mul,
                        "/": self.builder.sdiv,
                        "%": self.builder.srem,
                    }
                    op = ops[node.op]
                result = op(lhs, rhs, "tmp")
                if is_unsigned:
                    self._tag_unsigned(result)
                return result, None
        elif node.op in [">", "<", ">=", "<=", "!=", "=="]:
            if isinstance(lhs.type, ir.PointerType) and isinstance(
                rhs.type, ir.PointerType
            ):
                lhs_i = self.builder.ptrtoint(lhs, int64_t)
                rhs_i = self.builder.ptrtoint(rhs, int64_t)
                cmp = self.builder.icmp_unsigned(node.op, lhs_i, rhs_i, "ptrcmp")
            elif dispatch_type == dispatch_type_int:
                if is_unsigned:
                    cmp = self.builder.icmp_unsigned(node.op, lhs, rhs, "cmptmp")
                else:
                    cmp = self.builder.icmp_signed(node.op, lhs, rhs, "cmptmp")
            else:
                cmp = self._float_compare(node.op, lhs, rhs, "cmptmp")
            return self.builder.zext(cmp, int64_t, "booltmp"), None
        elif node.op == "&":
            result = self.builder.and_(lhs, rhs, "andtmp")
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.op == "|":
            result = self.builder.or_(lhs, rhs, "ortmp")
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.op == "^":
            result = self.builder.xor(lhs, rhs, "xortmp")
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.op == "<<":
            result = self.builder.shl(lhs, rhs, "shltmp")
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.op == ">>":
            if is_unsigned:
                result = self.builder.lshr(lhs, rhs, "shrtmp")
                self._tag_unsigned(result)
                return result, None
            return self.builder.ashr(lhs, rhs, "shrtmp"), None
        else:
            func = self.module.globals.get("binary{0}".format(node.op))
            return self.builder.call(func, [lhs, rhs], "binop"), None

    def _codegen_short_circuit_and(self, node):
        """Short-circuit &&: if lhs is false, skip rhs."""
        lhs, _ = self.codegen(node.left)
        lhs_bool = self._to_bool(lhs, "and_lhs")

        rhs_bb = self.builder.function.append_basic_block("and_rhs")
        merge_bb = self.builder.function.append_basic_block("and_merge")
        lhs_bb = self.builder.block

        self.builder.cbranch(lhs_bool, rhs_bb, merge_bb)

        self.builder.position_at_end(rhs_bb)
        rhs, _ = self.codegen(node.right)
        rhs_bool = self._to_bool(rhs, "and_rhs")
        rhs_result = self.builder.zext(rhs_bool, int64_t, "and_rhs_ext")
        rhs_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(int64_t, "and_result")
        phi.add_incoming(ir.Constant(int64_t, 0), lhs_bb)
        phi.add_incoming(rhs_result, rhs_bb_end)
        return phi, None

    def _codegen_short_circuit_or(self, node):
        """Short-circuit ||: if lhs is true, skip rhs."""
        lhs, _ = self.codegen(node.left)
        lhs_bool = self._to_bool(lhs, "or_lhs")

        rhs_bb = self.builder.function.append_basic_block("or_rhs")
        merge_bb = self.builder.function.append_basic_block("or_merge")
        lhs_bb = self.builder.block

        self.builder.cbranch(lhs_bool, merge_bb, rhs_bb)

        self.builder.position_at_end(rhs_bb)
        rhs, _ = self.codegen(node.right)
        rhs_bool = self._to_bool(rhs, "or_rhs")
        rhs_result = self.builder.zext(rhs_bool, int64_t, "or_rhs_ext")
        rhs_bb_end = self.builder.block
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(int64_t, "or_result")
        phi.add_incoming(ir.Constant(int64_t, 1), lhs_bb)
        phi.add_incoming(rhs_result, rhs_bb_end)
        return phi, None

    def codegen_If(self, node):

        cond_val, _ = self.codegen(node.cond)
        cmp = self._to_bool(cond_val)

        then_bb = self.builder.function.append_basic_block("then")
        else_bb = self.builder.function.append_basic_block("else")
        merge_bb = self.builder.function.append_basic_block("ifend")

        self.builder.cbranch(cmp, then_bb, else_bb)

        with self.new_scope():
            self.builder.position_at_end(then_bb)
            self.codegen(node.iftrue)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        with self.new_scope():
            self.builder.position_at_end(else_bb)
            if node.iffalse:
                self.codegen(node.iffalse)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        self.builder.position_at_end(merge_bb)
        # self.builder.block = merge_bb

        return None, None

    def codegen_NoneType(self, node):
        return None, None

    def codegen_For(self, node):

        saved_block = self.builder.block
        self.builder.position_at_end(saved_block)  # why the save_block at the end

        if node.init is not None:
            self.codegen(node.init)

        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block("test")
        loop_bb = self.builder.function.append_basic_block("loop")
        next_bb = self.builder.function.append_basic_block("next")

        # append by name nor just add it
        after_loop_label = self.new_label("afterloop")
        after_bb = self.builder.function.append_basic_block(after_loop_label)

        self.builder.branch(test_bb)
        self.builder.position_at_end(test_bb)

        if node.cond is not None:
            endcond, _ = self.codegen(node.cond)
            cmp = self._to_bool(endcond, "loopcond")
            self.builder.cbranch(cmp, loop_bb, after_bb)
        else:
            # for(;;) - infinite loop, always branch to body
            self.builder.branch(loop_bb)

        with self.new_scope():
            self.define("break", after_bb)
            self.define("continue", next_bb)
            self.builder.position_at_end(loop_bb)
            body_val, _ = self.codegen(node.stmt)  # if was ready codegen
            if not self.builder.block.is_terminated:
                self.builder.branch(next_bb)
            self.builder.position_at_end(next_bb)
            if node.next is not None:
                self.codegen(node.next)
            self.builder.branch(test_bb)
        self.builder.position_at_end(after_bb)

        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_While(self, node):

        saved_block = self.builder.block
        id_name = node.__class__.__name__
        self.builder.position_at_end(saved_block)
        # The builder is what? loop is a block which begin with loop
        test_bb = self.builder.function.append_basic_block(
            "test"
        )  # just create some block need to be filled
        loop_bb = self.builder.function.append_basic_block("loop")
        after_bb = self.builder.function.append_basic_block("afterloop")

        self.builder.branch(test_bb)
        self.builder.position_at_start(test_bb)
        endcond, _ = self.codegen(node.cond)
        cmp = self._to_bool(endcond, "loopcond")
        self.builder.cbranch(cmp, loop_bb, after_bb)

        with self.new_scope():
            self.define("break", after_bb)
            self.define("continue", test_bb)
            self.builder.position_at_end(loop_bb)
            body_val, _ = self.codegen(node.stmt)
            # after eval body we need to goto test_bb
            # New code will be inserted into after_bb
            if not self.builder.block.is_terminated:
                self.builder.branch(test_bb)
            self.builder.position_at_end(after_bb)

        # The 'for' expression always returns 0
        return ir.values.Constant(ir.DoubleType(), 0.0)

    def codegen_Break(self, node):
        target = self.lookup("break")
        if isinstance(target, tuple):
            target = target[1]
        self.builder.branch(target)
        return None, None

    def codegen_Continue(self, node):
        target = self.lookup("continue")
        if isinstance(target, tuple):
            target = target[1]
        self.builder.branch(target)
        return None, None

    def codegen_DoWhile(self, node):

        saved_block = self.builder.block
        self.builder.position_at_end(saved_block)

        loop_bb = self.builder.function.append_basic_block("dowhile_body")
        test_bb = self.builder.function.append_basic_block("dowhile_test")
        after_bb = self.builder.function.append_basic_block("dowhile_end")

        self.builder.branch(loop_bb)

        with self.new_scope():
            self.define("break", after_bb)
            self.define("continue", test_bb)
            self.builder.position_at_end(loop_bb)
            self.codegen(node.stmt)
            if not self.builder.block.is_terminated:
                self.builder.branch(test_bb)

        self.builder.position_at_end(test_bb)
        endcond, _ = self.codegen(node.cond)
        cmp = self._to_bool(endcond, "loopcond")
        self.builder.cbranch(cmp, loop_bb, after_bb)

        self.builder.position_at_end(after_bb)
        return ir.values.Constant(ir.DoubleType(), 0.0), None

    def codegen_Switch(self, node):

        cond_val, _ = self.codegen(node.cond)
        # Switch requires integer condition
        if isinstance(cond_val.type, ir.PointerType):
            cond_val = self.builder.ptrtoint(cond_val, int64_t)
        elif self._is_floating_ir_type(cond_val.type):
            cond_val = self.builder.fptosi(cond_val, int64_t)
        elif isinstance(cond_val.type, ir.IntType) and cond_val.type.width != 64:
            cond_val = self._implicit_convert(cond_val, int64_t)

        after_bb = self.builder.function.append_basic_block("switch_end")

        # Preserve C switch semantics: grouped case labels and fallthrough
        # share code by jumping into the next label block, not directly to
        # the switch epilogue.
        if isinstance(node.stmt, c_ast.Compound):
            switch_items = list(node.stmt.block_items or [])
        elif node.stmt is not None:
            switch_items = [node.stmt]
        else:
            switch_items = []
        labels = [
            item
            for item in switch_items
            if isinstance(item, (c_ast.Case, c_ast.Default))
        ]

        label_blocks = {}
        default_bb = after_bb
        for item in labels:
            bb_name = (
                "switch_default" if isinstance(item, c_ast.Default) else "switch_case"
            )
            bb = self.builder.function.append_basic_block(bb_name)
            label_blocks[id(item)] = bb
            if isinstance(item, c_ast.Default):
                default_bb = bb

        switch_inst = self.builder.switch(cond_val, default_bb)

        with self.new_scope():
            self.define("break", after_bb)

            for item in labels:
                if not isinstance(item, c_ast.Case):
                    continue
                # Case values must be compile-time constants
                try:
                    const_int = self._eval_const_expr(item.expr)
                    case_val = ir.Constant(cond_val.type, const_int)
                except Exception:
                    case_val, _ = self.codegen(item.expr)
                    if case_val is None:
                        continue
                    if not isinstance(case_val, ir.Constant):
                        # Non-constant case: skip (LLVM requires constants)
                        continue
                    if case_val.type != cond_val.type:
                        case_val = ir.Constant(cond_val.type, case_val.constant)
                switch_inst.add_case(case_val, label_blocks[id(item)])

            for idx, item in enumerate(labels):
                self.builder.position_at_end(label_blocks[id(item)])
                for stmt in item.stmts or []:
                    self.codegen(stmt)
                    if self.builder.block.is_terminated:
                        break
                if not self.builder.block.is_terminated:
                    next_bb = after_bb
                    if idx + 1 < len(labels):
                        next_bb = label_blocks[id(labels[idx + 1])]
                    self.builder.branch(next_bb)

        self.builder.position_at_end(after_bb)
        return None, None

    def codegen_TernaryOp(self, node):

        cond_val, _ = self.codegen(node.cond)
        cmp = self._to_bool(cond_val)

        then_bb = self.builder.function.append_basic_block("ternary_true")
        else_bb = self.builder.function.append_basic_block("ternary_false")
        merge_bb = self.builder.function.append_basic_block("ternary_end")

        self.builder.cbranch(cmp, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        true_val, _ = self.codegen(node.iftrue)
        true_bb_end = self.builder.block

        self.builder.position_at_end(else_bb)
        false_val, _ = self.codegen(node.iffalse)
        false_bb_end = self.builder.block

        def zero_value(target_type):
            if isinstance(target_type, ir.PointerType):
                return ir.Constant(target_type, None)
            if self._is_floating_ir_type(target_type):
                return ir.Constant(target_type, 0.0)
            return ir.Constant(target_type, 0)

        def pick_target_type(lhs, rhs):
            if lhs is None and rhs is None:
                return int64_t
            if lhs is None:
                return rhs.type
            if rhs is None:
                return lhs.type
            if isinstance(lhs.type, ir.ArrayType) or isinstance(rhs.type, ir.ArrayType):
                if isinstance(lhs.type, ir.PointerType):
                    return lhs.type
                if isinstance(rhs.type, ir.PointerType):
                    return rhs.type
                if isinstance(lhs.type, ir.ArrayType):
                    return ir.PointerType(lhs.type.element)
                return ir.PointerType(rhs.type.element)
            if lhs.type == rhs.type:
                return lhs.type
            if isinstance(lhs.type, ir.PointerType) and isinstance(
                rhs.type, ir.PointerType
            ):
                if lhs.type == rhs.type:
                    return lhs.type
                return voidptr_t
            if isinstance(lhs.type, ir.PointerType) and isinstance(
                rhs.type, ir.IntType
            ):
                return lhs.type
            if isinstance(rhs.type, ir.PointerType) and isinstance(
                lhs.type, ir.IntType
            ):
                return rhs.type
            if self._is_floating_ir_type(lhs.type) or self._is_floating_ir_type(
                rhs.type
            ):
                return self._common_float_type(lhs.type, rhs.type)
            if isinstance(lhs.type, ir.IntType) and isinstance(rhs.type, ir.IntType):
                return lhs.type if lhs.type.width >= rhs.type.width else rhs.type
            return lhs.type

        target = pick_target_type(true_val, false_val)
        incoming = []
        for branch_end, branch_val in (
            (true_bb_end, true_val),
            (false_bb_end, false_val),
        ):
            if branch_end.is_terminated:
                continue
            self.builder.position_at_end(branch_end)
            value = branch_val if branch_val is not None else zero_value(target)
            if value.type != target or isinstance(value.type, ir.ArrayType):
                value = self._implicit_convert(value, target)
            incoming.append((self.builder.block, value))
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        if not incoming:
            return zero_value(target), None
        if len(incoming) == 1:
            return incoming[0][1], None

        phi = self.builder.phi(target, "ternary")
        for pred, value in incoming:
            phi.add_incoming(value, pred)
        return phi, None

    def codegen_Cast(self, node):

        expr, ptr = self.codegen(node.expr)

        dest_ir_type = self._resolve_ast_type(node.to_type.type)
        # Check if casting to unsigned type
        is_unsigned = False
        if isinstance(node.to_type.type, c_ast.TypeDecl) and isinstance(
            node.to_type.type.type, c_ast.IdentifierType
        ):
            is_unsigned = self._is_unsigned_type_names(node.to_type.type.type.names)
        if self._is_floating_ir_type(expr.type) and isinstance(
            dest_ir_type, ir.IntType
        ):
            if is_unsigned:
                result = self.builder.fptoui(expr, dest_ir_type)
                self._tag_value_from_decl_type(result, node.to_type.type)
                return result, None
            result = self.builder.fptosi(expr, dest_ir_type)
            self._clear_unsigned(result)
            self._tag_value_from_decl_type(result, node.to_type.type)
            return result, None
        if expr.type == dest_ir_type:
            if isinstance(dest_ir_type, ir.IntType):
                if is_unsigned:
                    if self._is_unsigned_val(expr):
                        self._tag_value_from_decl_type(expr, node.to_type.type)
                        return expr, None
                    result = self.builder.add(
                        expr, ir.Constant(dest_ir_type, 0), "casttmp"
                    )
                    self._tag_unsigned(result)
                    self._tag_value_from_decl_type(result, node.to_type.type)
                    return result, None
                if self._is_unsigned_val(expr):
                    result = self.builder.add(
                        expr, ir.Constant(dest_ir_type, 0), "casttmp"
                    )
                    self._tag_value_from_decl_type(result, node.to_type.type)
                    return result, None
                self._clear_unsigned(expr)
            if is_unsigned:
                self._tag_unsigned(expr)
            self._tag_value_from_decl_type(expr, node.to_type.type)
            return expr, ptr
        result = self._implicit_convert(expr, dest_ir_type)
        if is_unsigned:
            self._tag_unsigned(result)
        elif isinstance(dest_ir_type, ir.IntType):
            self._clear_unsigned(result)
        self._tag_value_from_decl_type(result, node.to_type.type)
        return result, None

    def codegen_FuncCall(self, node):

        callee = None
        if isinstance(node.name, c_ast.ID):
            callee = node.name.name
            if callee == "__builtin_va_start":
                return self._codegen_builtin_va_start(node)
            if callee == "__builtin_va_end":
                return self._codegen_builtin_va_end(node)
            if callee == "__builtin_va_copy":
                return self._codegen_builtin_va_copy(node)
            if callee == "__builtin_va_arg":
                return ir.Constant(voidptr_t, None), None
        else:
            # Calling function pointer in struct: s.fn(args)
            call_args = []
            if node.args:
                call_args = [self.codegen(arg)[0] for arg in node.args.exprs]
            fp_val, _ = self.codegen(node.name)
            if isinstance(fp_val.type, ir.PointerType) and isinstance(
                fp_val.type.pointee, ir.FunctionType
            ):
                # Coerce args to match function pointer param types
                ftype = fp_val.type.pointee
                coerced = []
                for j, a in enumerate(call_args):
                    if j < len(ftype.args):
                        coerced.append(self._coerce_arg(a, ftype.args[j]))
                    else:
                        coerced.append(a)
                call_args = coerced
                ret_type = ftype.return_type
                if isinstance(ret_type, ir.VoidType):
                    self.builder.call(fp_val, call_args)
                    return ir.Constant(int64_t, 0), None
                result = self.builder.call(fp_val, call_args, "fpcall")
                return (
                    self._extend_call_result(
                        result, returns_unsigned=self._is_unsigned_return(fp_val)
                    ),
                    None,
                )
            # Not a function pointer — can't call, return dummy
            return ir.Constant(int64_t, 0), None

        _, callee_func = self.lookup(callee)

        call_args = []
        if node.args:
            call_args = [self.codegen(arg)[0] for arg in node.args.exprs]

        # Function pointer: load the pointer and call through it
        if not isinstance(callee_func, ir.Function):
            if hasattr(callee_func, "type") and isinstance(
                callee_func.type, ir.PointerType
            ):
                loaded = self._safe_load(callee_func, name="fptr")
                if self._is_unsigned_return_binding(callee_func):
                    self._tag_unsigned_return(loaded)
                # loaded could be a function pointer (ptr to FunctionType)
                # or the alloca's pointee could be a function ptr
                func_val = loaded
                if isinstance(func_val.type, ir.PointerType) and isinstance(
                    func_val.type.pointee, ir.FunctionType
                ):
                    ftype = func_val.type.pointee
                    coerced = [
                        self._coerce_arg(a, ftype.args[j]) if j < len(ftype.args) else a
                        for j, a in enumerate(call_args)
                    ]
                    ret_type = ftype.return_type
                    is_void = isinstance(ret_type, ir.VoidType)
                    if is_void:
                        self.builder.call(func_val, coerced)
                        return ir.Constant(int64_t, 0), None
                    result = self.builder.call(func_val, coerced, "fpcall")
                    return (
                        self._extend_call_result(
                            result, returns_unsigned=self._is_unsigned_return(func_val)
                        ),
                        None,
                    )
            return ir.Constant(int64_t, 0), None  # unknown function — return dummy

        if callee_func is None or not isinstance(callee_func, (ir.Function,)):
            return ir.Constant(int64_t, 0), None

        # Convert arguments to match function parameter types
        converted = self._convert_call_args(call_args, callee_func)

        # Call and handle return type
        try:
            is_void = isinstance(callee_func.return_value.type, ir.VoidType)
        except Exception:
            is_void = False
        try:
            if is_void:
                self.builder.call(callee_func, converted)
                return ir.Constant(int64_t, 0), None
            result = self.builder.call(callee_func, converted, "calltmp")
        except (TypeError, IndexError):
            # Arg count/type mismatch — return dummy value
            return ir.Constant(int64_t, 0), None

        # Widen small int returns (e.g., i32 from strcmp) to i64
        return (
            self._extend_call_result(
                result, returns_unsigned=self._is_unsigned_return_binding(callee_func)
            ),
            None,
        )

    def _get_or_declare_intrinsic(self, name, ret_type, arg_types):
        existing = self.module.globals.get(name)
        if existing is not None:
            return existing
        return ir.Function(self.module, ir.FunctionType(ret_type, arg_types), name=name)

    def _codegen_builtin_va_start(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int64_t, 0), None
        ap_addr, _ = self.codegen(node.args.exprs[0])
        if not isinstance(getattr(ap_addr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        intrinsic = self._get_or_declare_intrinsic(
            "llvm.va_start", ir.VoidType(), [voidptr_t]
        )
        arg = ap_addr
        if arg.type != voidptr_t:
            arg = self.builder.bitcast(arg, voidptr_t, name="vastartarg")
        self.builder.call(intrinsic, [arg])
        return ir.Constant(int64_t, 0), None

    def _codegen_builtin_va_end(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int64_t, 0), None
        ap_addr, _ = self.codegen(node.args.exprs[0])
        if not isinstance(getattr(ap_addr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        intrinsic = self._get_or_declare_intrinsic(
            "llvm.va_end", ir.VoidType(), [voidptr_t]
        )
        arg = ap_addr
        if arg.type != voidptr_t:
            arg = self.builder.bitcast(arg, voidptr_t, name="vaendarg")
        self.builder.call(intrinsic, [arg])
        return ir.Constant(int64_t, 0), None

    def _codegen_builtin_va_copy(self, node):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int64_t, 0), None
        dst_addr, _ = self.codegen(node.args.exprs[0])
        src_addr, _ = self.codegen(node.args.exprs[1])
        if not isinstance(getattr(dst_addr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        if not isinstance(getattr(src_addr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        src_val = self._safe_load(src_addr)
        dst_pointee = dst_addr.type.pointee
        if src_val.type != dst_pointee:
            src_val = self._implicit_convert(src_val, dst_pointee)
        self._safe_store(src_val, dst_addr)
        return ir.Constant(int64_t, 0), None

    def _convert_call_args(self, call_args, callee_func):
        """Convert call arguments to match function parameter types."""
        converted = []
        param_types = [p.type for p in callee_func.args]

        for i, arg in enumerate(call_args):
            if i < len(param_types):
                expected = param_types[i]
                arg = self._coerce_arg(arg, expected)
            else:
                arg = self._default_arg_promotion(arg)
            converted.append(arg)
        return converted

    def _default_arg_promotion(self, arg):
        """Apply C default argument promotions for variadic calls."""
        if arg is None or isinstance(getattr(arg, "type", None), ir.VoidType):
            return ir.Constant(int64_t, 0)
        if isinstance(arg.type, ir.ArrayType):
            return self._implicit_convert(arg, ir.PointerType(arg.type.element))
        if isinstance(arg.type, ir.FloatType):
            return self.builder.fpext(arg, ir.DoubleType())
        if isinstance(arg.type, ir.IntType) and arg.type.width < int32_t.width:
            return self._integer_promotion(arg)
        return arg

    def _coerce_arg(self, arg, expected):
        """Coerce a single argument to the expected type."""
        if arg is None or isinstance(getattr(arg, "type", None), ir.VoidType):
            return (
                ir.Constant(expected, None)
                if isinstance(expected, ir.PointerType)
                else ir.Constant(int64_t, 0)
            )
        if arg.type == expected:
            return arg
        # String literal [N x i8] -> pointer
        if isinstance(arg.type, ir.ArrayType) and isinstance(expected, ir.PointerType):
            gv = ir.GlobalVariable(
                self.module, arg.type, self.module.get_unique_name("str")
            )
            gv.initializer = arg
            gv.global_constant = True
            return self.builder.bitcast(gv, expected)
        # Pointer -> different pointer: bitcast
        if isinstance(arg.type, ir.PointerType) and isinstance(
            expected, ir.PointerType
        ):
            return self.builder.bitcast(arg, expected)
        # Numeric conversions
        return self._implicit_convert(arg, expected)

    def codegen_Decl(self, node):

        type_str = ""

        # Skip anonymous/unnamed declarations
        if node.name is None and not isinstance(
            node.type, (c_ast.Struct, c_ast.Union, c_ast.Enum, c_ast.FuncDecl)
        ):
            if not (
                isinstance(node.type, c_ast.TypeDecl)
                and isinstance(node.type.type, (c_ast.Struct, c_ast.Union, c_ast.Enum))
            ):
                return None, None

        # Static local variables: stored as globals with function-scoped names
        is_static = node.storage and "static" in node.storage
        if is_static and not self.in_global and isinstance(node.type, c_ast.TypeDecl):
            type_str = node.type.type.names
            ir_type = self._get_ir_type(type_str)
            # Create unique global name
            global_name = self._static_local_symbol_name(node.name)
            gv = self._create_bound_global(node.name, ir_type, symbol_name=global_name)
            gv.linkage = "internal"
            if node.init:
                gv.initializer = self._build_const_init(node.init, ir_type)
            else:
                gv.initializer = self._zero_initializer(ir_type)
            return None, None

        if self._is_global_extern_decl(node):
            ir_type = self._extern_decl_ir_type(node.type)
            self._prepare_file_scope_object(
                node.name,
                ir_type,
                storage=node.storage,
                has_initializer=False,
            )
            return None, None

        if isinstance(node.type, c_ast.Enum):
            return self.codegen_Enum(node.type)

        # Forward function declaration: int foo(int x);
        if isinstance(node.type, c_ast.FuncDecl):
            funcname = node.name
            ir_type, _ = self.codegen(node.type)
            arg_types = []
            is_va = False
            if node.type.args:
                for arg in node.type.args.params:
                    if isinstance(arg, c_ast.EllipsisParam):
                        is_va = True
                        continue
                    t = self._resolve_param_type(arg)
                    if t is not None:
                        arg_types.append(t)
            function_type = ir.FunctionType(ir_type, arg_types, var_arg=is_va)
            symbol_name = self._register_file_scope_function(
                funcname,
                function_type,
                storage=node.storage,
                is_definition=False,
            )
            # Skip if already exists (module globals, libc, or env)
            existing = self.module.globals.get(symbol_name)
            if existing:
                if self._func_decl_returns_unsigned(node.type):
                    self._mark_unsigned_return(existing)
                self.define(funcname, (None, existing))
                return None, None
            if funcname in LIBC_FUNCTIONS:
                self._declare_libc(funcname)
                return None, None
            try:
                func = ir.Function(
                    self.module,
                    function_type,
                    name=symbol_name,
                )
                if self._is_file_scope_static(node.storage):
                    func.linkage = "internal"
                if self._func_decl_returns_unsigned(node.type):
                    self._mark_unsigned_return(func)
                self.define(funcname, (ir_type, func))
            except Exception:
                # Already exists (libc or previous decl)
                existing = self.module.globals.get(symbol_name)
                if existing:
                    if self._func_decl_returns_unsigned(node.type):
                        self._mark_unsigned_return(existing)
                    self.define(funcname, (ir_type, existing))
            return None, None

        # Bare struct/union/type definition
        if isinstance(node.type, c_ast.Union):
            if node.name is None:
                self.codegen_Union(node.type)
            return None, None

        if isinstance(node.type, c_ast.Struct) and node.name is None:
            self.codegen_Struct(node.type)
            return None, None

        if isinstance(node.type, c_ast.TypeDecl):
            if isinstance(node.type.type, c_ast.IdentifierType):
                # Check if the type resolves to a struct or pointer via typedef
                resolved = self._resolve_type_str(node.type.type.names)
                if isinstance(
                    resolved, (ir.LiteralStructType, ir.PointerType, ir.ArrayType)
                ):
                    name = node.type.declname
                    ir_type = resolved
                    if not self.in_global:
                        ret = self._alloca_in_entry(ir_type, name)
                        self.define(name, (ir_type, ret))
                    else:
                        ret, write_initializer = self._prepare_file_scope_object(
                            name,
                            ir_type,
                            storage=node.storage,
                            has_initializer=node.init is not None,
                        )
                        if ret is None:
                            return None, None
                    if node.init is not None:
                        if self.in_global:
                            if write_initializer:
                                ret.initializer = self._build_const_init(
                                    node.init, ir_type
                                )
                        else:
                            init_val, _ = self.codegen(node.init)
                            if init_val is not None:
                                if init_val.type != ir_type:
                                    init_val = self._implicit_convert(init_val, ir_type)
                                self._safe_store(init_val, ret)
                    elif self.in_global and write_initializer:
                        ret.initializer = self._zero_initializer(ir_type)
                    return None, None

            if isinstance(node.type.type, (c_ast.Struct, c_ast.Union)):
                name = node.type.declname
                codegen_fn = (
                    self.codegen_Union
                    if isinstance(node.type.type, c_ast.Union)
                    else self.codegen_Struct
                )
                if node.type.type.name is None:
                    struct_type = codegen_fn(node.type.type)
                    if not self.in_global:
                        ret = self._alloca_in_entry(struct_type, name)
                        self.define(name, (struct_type, ret))
                    else:
                        ret, write_initializer = self._prepare_file_scope_object(
                            name,
                            struct_type,
                            storage=node.storage,
                            has_initializer=node.init is not None,
                        )
                        if ret is None:
                            return None, None
                    if node.init is not None:
                        if self.in_global:
                            if write_initializer:
                                ret.initializer = self._build_const_init(
                                    node.init, struct_type
                                )
                        else:
                            init_val, _ = self.codegen(node.init)
                            if init_val is not None:
                                if init_val.type != struct_type:
                                    init_val = self._implicit_convert(
                                        init_val, struct_type
                                    )
                                self._safe_store(init_val, ret)
                    elif self.in_global and write_initializer:
                        ret.initializer = self._zero_initializer(struct_type)
                    return None, None
                else:
                    struct_type = self.env[node.type.type.name][0]
                    if not self.in_global:
                        ret = self._alloca_in_entry(struct_type, name)
                        self.define(name, (struct_type, ret))
                    else:
                        ret, write_initializer = self._prepare_file_scope_object(
                            name,
                            struct_type,
                            storage=node.storage,
                            has_initializer=node.init is not None,
                        )
                        if ret is None:
                            return None, None
                    if node.init is not None:
                        if self.in_global:
                            if write_initializer:
                                ret.initializer = self._build_const_init(
                                    node.init, struct_type
                                )
                        else:
                            init_val, _ = self.codegen(node.init)
                            if init_val is not None:
                                if init_val.type != struct_type:
                                    init_val = self._implicit_convert(
                                        init_val, struct_type
                                    )
                                self._safe_store(init_val, ret)
                    elif self.in_global and write_initializer:
                        ret.initializer = self._zero_initializer(struct_type)
                    return None, None
            else:
                type_str = node.type.type.names
                is_unsigned = self._is_unsigned_type_names(type_str)
                ir_type = self._get_ir_type(type_str)
                type_str = self._resolve_type_str(type_str)
                if isinstance(type_str, ir.Type):
                    type_str = "int"  # fallback for alloca name
                if self._is_floating_ir_type(ir_type):
                    init = 0.0
                else:
                    init = 0

                if node.init is not None:
                    if self.in_global:
                        init_val = self._build_const_init(node.init, ir_type)
                    else:
                        init_val, _ = self.codegen(node.init)
                else:
                    init_val = self._zero_initializer(ir_type)
                if self.in_global:
                    var_addr, write_initializer = self._prepare_file_scope_object(
                        node.name,
                        ir_type,
                        storage=node.storage,
                        has_initializer=node.init is not None,
                    )
                    if var_addr is None:
                        return None, None
                    var_ir_type = ir_type
                    if write_initializer:
                        var_addr.initializer = init_val
                else:
                    var_addr, var_ir_type = self.create_entry_block_alloca(
                        node.name, type_str, 1, storage=node.storage
                    )
                    if is_unsigned:
                        self._mark_unsigned(var_addr)
                    init_val = self._implicit_convert(init_val, ir_type)
                    self._safe_store(init_val, var_addr)
                if self.in_global and is_unsigned:
                    self._mark_unsigned(var_addr)

        elif isinstance(node.type, c_ast.ArrayDecl):
            global_symbol_name = self._file_scope_symbol_name(node.name, node.storage)
            array_list = []
            array_node = node.type
            var_addr = None
            var_ir_type = None
            elem_ir_type = None
            write_initializer = True
            while True:
                array_next_type = array_node.type
                if isinstance(array_next_type, c_ast.TypeDecl):
                    dim_val = self._eval_dim(array_node.dim) if array_node.dim else 0
                    array_list.append(dim_val)
                    elem_ir_type = self._resolve_ast_type(array_next_type)
                    break

                elif isinstance(array_next_type, c_ast.ArrayDecl):
                    array_list.append(self._eval_dim(array_node.dim))
                    array_node = array_next_type
                    continue
                elif isinstance(array_next_type, c_ast.PtrDecl):
                    # Array of pointers: int *arr[3]
                    dim = self._eval_dim(array_node.dim)
                    inner = array_next_type.type
                    if isinstance(inner, c_ast.TypeDecl):
                        elem_type_str = inner.type.names
                    else:
                        elem_type_str = "int"
                    elem_ir = ir.PointerType(get_ir_type(elem_type_str))
                    elem_ir_type = elem_ir
                    arr_ir = ir.ArrayType(elem_ir, dim)
                    arr_ir.dim_array = [dim]
                    if not self.in_global:
                        var_addr = self._alloca_in_entry(arr_ir, node.name)
                        self.define(node.name, (arr_ir, var_addr))
                    else:
                        var_addr, write_initializer = self._prepare_file_scope_object(
                            node.name,
                            arr_ir,
                            storage=node.storage,
                            has_initializer=node.init is not None,
                        )
                        if var_addr is None:
                            return None, None
                    var_ir_type = arr_ir
                    break
                else:
                    raise Exception("TODO implement")

            if var_addr is None:
                var_ir_type = elem_ir_type
                for dim in reversed(array_list):
                    var_ir_type = ir.ArrayType(var_ir_type, dim)
                var_ir_type.dim_array = array_list
                if not self.in_global:
                    var_addr = self._alloca_in_entry(var_ir_type, node.name)
                else:
                    var_addr, write_initializer = self._prepare_file_scope_object(
                        node.name,
                        var_ir_type,
                        storage=node.storage,
                        has_initializer=node.init is not None,
                    )
                    if var_addr is None:
                        return None, None
                self.define(node.name, (var_ir_type, var_addr))

            if self._has_unsigned_scalar_pointee(node.type):
                self._mark_unsigned_pointee(var_addr)

            # Infer the size of zero-length arrays from the initializer.
            if (
                isinstance(var_ir_type, ir.ArrayType)
                and var_ir_type.count == 0
                and node.init is not None
            ):
                actual_count = None
                if isinstance(node.init, c_ast.InitList):
                    actual_count = len(node.init.exprs)
                elif (
                    isinstance(node.init, c_ast.Constant)
                    and getattr(node.init, "type", None) == "string"
                ):
                    raw = node.init.value[1:-1]
                    actual_count = len(self._process_escapes(raw)) + 1
                if actual_count is not None and elem_ir_type is not None:
                    var_ir_type = ir.ArrayType(elem_ir_type, actual_count)
                    var_ir_type.dim_array = [actual_count]
                    if self.in_global:
                        new_name = self.module.get_unique_name(global_symbol_name)
                        var_addr = self._create_bound_global(
                            node.name,
                            var_ir_type,
                            symbol_name=new_name,
                            storage=node.storage,
                        )
                        self.define(node.name, (var_ir_type, var_addr))
                        if not hasattr(self, "_array_renames"):
                            self._array_renames = {}
                        self._array_renames[
                            f'@"{global_symbol_name}"'
                        ] = f'@"{new_name}"'
                    else:
                        var_addr = self._alloca_in_entry(var_ir_type, node.name)
                        self.define(node.name, (var_ir_type, var_addr))

            if self._has_unsigned_scalar_pointee(node.type):
                self._mark_unsigned_pointee(var_addr)

            # Handle array initialization: int a[3] = {1, 2, 3}; or
            # char s[] = "hi"; or const char *names[] = {"a", helper};
            if node.init is not None:
                if self.in_global:
                    if write_initializer:
                        try:
                            const_init = self._build_const_init(node.init, var_ir_type)
                            str(const_init)
                            var_addr.initializer = const_init
                        except Exception:
                            var_addr.initializer = self._zero_initializer(var_ir_type)
                elif isinstance(node.init, c_ast.InitList):
                    self._init_array(
                        var_addr,
                        node.init,
                        elem_ir_type,
                        [ir.Constant(ir.IntType(32), 0)],
                    )
                elif (
                    isinstance(node.init, c_ast.Constant)
                    and getattr(node.init, "type", None) == "string"
                    and isinstance(elem_ir_type, ir.IntType)
                    and elem_ir_type.width == 8
                ):
                    raw = self._process_escapes(node.init.value[1:-1]) + "\00"
                    idx0 = ir.Constant(ir.IntType(32), 0)
                    for i, ch in enumerate(raw[: var_ir_type.count]):
                        elem_ptr = self.builder.gep(
                            var_addr,
                            [idx0, ir.Constant(ir.IntType(32), i)],
                            inbounds=True,
                        )
                        self.builder.store(int8_t(ord(ch)), elem_ptr)
            elif self.in_global and write_initializer:
                var_addr.initializer = self._zero_initializer(var_ir_type)

        elif isinstance(node.type, c_ast.PtrDecl):

            point_level = 1
            sub_node = node.type
            resolved_pointee_type = None
            write_initializer = True

            while True:
                sub_next_type = sub_node.type
                if isinstance(sub_next_type, c_ast.TypeDecl):
                    if isinstance(sub_next_type.type, c_ast.Struct):
                        # pointer to struct: struct { int x; } *p
                        resolved_pointee_type = self.codegen_Struct(sub_next_type.type)
                        type_str = "struct"
                    elif isinstance(sub_next_type.type, c_ast.Union):
                        resolved_pointee_type = self.codegen_Union(sub_next_type.type)
                        type_str = "union"
                    else:
                        type_str = sub_next_type.type.names
                        resolved = self._get_ir_type(type_str)
                        if isinstance(resolved, ir.Type):
                            resolved_pointee_type = resolved
                        if isinstance(resolved, ir.LiteralStructType):
                            type_str = "struct"
                    break
                elif isinstance(sub_next_type, c_ast.PtrDecl):
                    point_level += 1
                    sub_node = sub_next_type
                    continue
                elif isinstance(sub_next_type, c_ast.FuncDecl):
                    # Function pointer: int (*fp)(int, int)
                    func_ir_type = self._build_func_ptr_type(sub_next_type)
                    if not self.in_global:
                        var_addr = self._alloca_in_entry(func_ir_type, node.name)
                        self.define(node.name, (func_ir_type, var_addr))
                    else:
                        var_addr, write_initializer = self._prepare_file_scope_object(
                            node.name,
                            func_ir_type,
                            storage=node.storage,
                            has_initializer=node.init is not None,
                        )
                        if var_addr is None:
                            return None, None
                    if self._func_decl_returns_unsigned(sub_next_type):
                        self._mark_unsigned_return(var_addr)
                    if node.init is not None:
                        init_val, _ = self.codegen(node.init)
                        # init_val is an ir.Function, bitcast to func ptr type
                        if init_val.type != func_ir_type:
                            init_val = self.builder.bitcast(init_val, func_ir_type)
                        self._safe_store(init_val, var_addr)
                    return None, var_addr
                pass

            if resolved_pointee_type is not None:
                ir_type = resolved_pointee_type
                if isinstance(ir_type, ir.VoidType):
                    ir_type = int8_t
                for _ in range(point_level):
                    ir_type = ir.PointerType(ir_type)
                if not self.in_global:
                    var_addr = self._alloca_in_entry(ir_type, node.name)
                    self.define(node.name, (ir_type, var_addr))
                else:
                    var_addr, write_initializer = self._prepare_file_scope_object(
                        node.name,
                        ir_type,
                        storage=node.storage,
                        has_initializer=node.init is not None,
                    )
                    if var_addr is None:
                        return None, None
                var_ir_type = ir_type
            else:
                if self.in_global:
                    pointee_ir_type = get_ir_type(type_str)
                    if isinstance(pointee_ir_type, ir.VoidType):
                        pointee_ir_type = int8_t
                    for _ in range(point_level):
                        pointee_ir_type = ir.PointerType(pointee_ir_type)
                    var_ir_type = pointee_ir_type
                    var_addr, write_initializer = self._prepare_file_scope_object(
                        node.name,
                        var_ir_type,
                        storage=node.storage,
                        has_initializer=node.init is not None,
                    )
                    if var_addr is None:
                        return None, None
                else:
                    var_addr, var_ir_type = self.create_entry_block_alloca(
                        node.name,
                        type_str,
                        1,
                        point_level=point_level,
                        storage=node.storage,
                    )

            if self._has_unsigned_scalar_pointee(node.type):
                self._mark_unsigned_pointee(var_addr)

            if node.init is not None:
                if self.in_global:
                    if write_initializer:
                        try:
                            const_init = self._build_const_init(node.init, var_ir_type)
                            str(const_init)
                            var_addr.initializer = const_init
                        except Exception:
                            var_addr.initializer = ir.Constant(var_ir_type, None)
                else:
                    init_val, _ = self.codegen(node.init)
                    if isinstance(init_val.type, ir.ArrayType) and isinstance(
                        var_ir_type, ir.PointerType
                    ):
                        gv = ir.GlobalVariable(
                            self.module,
                            init_val.type,
                            self.module.get_unique_name("str"),
                        )
                        gv.initializer = init_val
                        gv.global_constant = True
                        init_val = self.builder.bitcast(gv, var_ir_type)
                    elif init_val.type != var_ir_type:
                        init_val = self._implicit_convert(init_val, var_ir_type)
                    self._safe_store(init_val, var_addr)
        else:
            return None, None

        return None, var_addr

    def codegen_ID(self, node):

        valtype, var = self.lookup(node.name)
        node.ir_type = valtype
        # Enum constants are stored as ir.Constant, not alloca'd
        if isinstance(var, ir.values.Constant):
            return var, None
        # Function reference: return function pointer directly
        if isinstance(var, ir.Function):
            if self._is_unsigned_return_binding(var):
                self._tag_unsigned_return(var)
            return var, None
        # Array types: decay to pointer to first element
        if isinstance(valtype, ir.ArrayType):
            ptr = self.builder.gep(
                var,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                name="arraydecay",
            )
            if self._is_unsigned_pointee_binding(var):
                self._tag_unsigned_pointee(ptr)
            return ptr, var
        # Guard: only load from pointer types
        if not isinstance(var.type, ir.PointerType):
            return var, None
        result = self._safe_load(var)
        # Propagate unsigned-ness from variable to loaded value
        if self._is_unsigned_binding(var):
            self._tag_unsigned(result)
        if self._is_unsigned_pointee_binding(var):
            self._tag_unsigned_pointee(result)
        if self._is_unsigned_return_binding(var):
            self._tag_unsigned_return(result)
        return result, var

    def codegen_ArrayRef(self, node):

        name = node.name
        subscript = node.subscript
        name_ir, name_ptr = self.codegen(name)
        if name_ir is None:
            return ir.Constant(int64_t, 0), None
        if (
            name_ptr is None
            and isinstance(name_ir, ir.values.Constant)
            and isinstance(name_ir.type, ir.ArrayType)
        ):
            gv = ir.GlobalVariable(
                self.module, name_ir.type, self.module.get_unique_name("strlit")
            )
            gv.initializer = name_ir
            gv.global_constant = True
            gv.linkage = "internal"
            name_ptr = gv
        subscript_ir, subscript_ptr = self.codegen(subscript)
        if subscript_ir is None:
            return ir.Constant(int64_t, 0), None

        if isinstance(subscript_ir.type, ir.IntType):
            subscript_ir = self._implicit_convert(subscript_ir, ir.IntType(64))
        else:
            subscript_ir = self.builder.fptoui(subscript_ir, ir.IntType(64))

        # Pointer subscript: p[i] -> *(p + i)
        name_type = self._get_expr_ir_type(name) or name_ir.type
        if isinstance(name_type, ir.PointerType) and isinstance(
            name_ir.type, ir.PointerType
        ):
            value_ir_type = name_type.pointee
            elem_ptr = self.builder.gep(name_ir, [subscript_ir], name="ptridx")
            # If GEP result points to an array, return pointer (array decay)
            if isinstance(elem_ptr.type, ir.PointerType) and isinstance(
                elem_ptr.type.pointee, ir.ArrayType
            ):
                node.ir_type = elem_ptr.type.pointee
                return elem_ptr, elem_ptr
            value_result = self._safe_load(elem_ptr)
            if self._is_unsigned_pointee(name_ir) or self._is_unsigned_pointee(
                name_ptr
            ):
                self._tag_unsigned(value_result)
            node.ir_type = value_ir_type
            return value_result, elem_ptr

        # Non-array type (opaque struct etc): treat as pointer subscript
        if not isinstance(name_type, ir.ArrayType):
            ptr = (
                self.builder.bitcast(name_ir, ir.PointerType(int8_t))
                if not isinstance(name_ir.type, ir.PointerType)
                else name_ir
            )
            elem_ptr = self.builder.gep(ptr, [subscript_ir], name="ptridx")
            value_result = self._safe_load(elem_ptr)
            if self._is_unsigned_pointee(name_ir) or self._is_unsigned_pointee(ptr):
                self._tag_unsigned(value_result)
            node.ir_type = (
                elem_ptr.type.pointee
                if isinstance(elem_ptr.type, ir.PointerType)
                else name_type
            )
            return value_result, elem_ptr

        # Array subscript: a[i] using GEP for correct stride calculation
        value_ir_type = name_type.element

        # If no address pointer, use name_ir as base
        if name_ptr is None:
            name_ptr = name_ir
        if name_ptr is None:
            return ir.Constant(int64_t, 0), None

        # GEP requires a pointer base; if name_ptr is a pointer to array, use GEP
        if isinstance(name_ptr.type, ir.PointerType):
            zero = ir.Constant(ir.IntType(32), 0)
            idx = (
                self.builder.trunc(subscript_ir, ir.IntType(32))
                if isinstance(subscript_ir.type, ir.IntType)
                and subscript_ir.type.width > 32
                else subscript_ir
            )
            elem_ptr = self.builder.gep(name_ptr, [zero, idx], name="arridx")

            # If element is sub-array, return pointer (array decay)
            if isinstance(value_ir_type, ir.ArrayType):
                node.ir_type = value_ir_type
                return elem_ptr, elem_ptr
            else:
                value_result = self._safe_load(elem_ptr)
                if self._is_unsigned_pointee(name_ir) or self._is_unsigned_pointee(
                    name_ptr
                ):
                    self._tag_unsigned(value_result)
                node.ir_type = value_ir_type
                return value_result, elem_ptr

        # Fallback: byte offset arithmetic (for non-pointer base)
        elem_size = self._ir_type_size(value_ir_type)
        stride = ir.Constant(ir.IntType(64), elem_size)
        offset = self.builder.mul(stride, subscript_ir, "array_add")
        base_int = (
            self.builder.ptrtoint(name_ptr, ir.IntType(64))
            if isinstance(name_ptr.type, ir.PointerType)
            else (
                name_ptr
                if isinstance(name_ptr.type, ir.IntType)
                else self.builder.ptrtoint(name_ptr, ir.IntType(64))
            )
        )
        addr = self.builder.add(offset, base_int, "addtmp")
        value_ptr = self.builder.inttoptr(addr, ir.PointerType(value_ir_type))
        if isinstance(value_ir_type, ir.ArrayType):
            node.ir_type = value_ir_type
            return value_ptr, value_ptr
        else:
            value_result = self._safe_load(value_ptr)
            if self._is_unsigned_pointee(name_ir) or self._is_unsigned_pointee(
                name_ptr
            ):
                self._tag_unsigned(value_result)
            node.ir_type = value_ir_type
            return value_result, value_ptr

    def codegen_Return(self, node):

        if node.expr is None:
            self.builder.ret_void()
        else:
            retval, _ = self.codegen(node.expr)
            # Implicit convert to function return type
            func_ret_type = self.function.return_value.type
            if retval.type != func_ret_type:
                retval = self._implicit_convert(retval, func_ret_type)
            self.builder.ret(retval)
        return None, None

    def codegen_Compound(self, node):

        if node.block_items:
            for stmt in node.block_items:
                if self.builder and self.builder.block.is_terminated:
                    # After a terminator (goto/break/continue/return),
                    # only process labels — skip unreachable code
                    if isinstance(stmt, c_ast.Label):
                        self.codegen(stmt)
                    continue
                self.codegen(stmt)
        return None, None

    def codegen_FuncDecl(self, node):
        ir_type = self._resolve_ast_type(node.type)
        return ir_type, None

    def codegen_FuncDef(self, node):

        # deep level func have deep level
        # we don't want funcdecl in codegen_decl too
        ir_type, _ = self.codegen(node.decl.type)
        funcname = node.decl.name

        self.return_type = ir_type  # for call in C
        if not hasattr(self, "func_return_types"):
            self.func_return_types = {}
        self.func_return_types[funcname] = ir_type

        arg_types = []
        is_var_arg = False
        if node.decl.type.args:
            for arg_type in node.decl.type.args.params:
                if isinstance(arg_type, c_ast.EllipsisParam):
                    is_var_arg = True
                    continue
                t = self._resolve_param_type(arg_type)
                if t is not None:
                    arg_types.append(t)

        function_type = ir.FunctionType(ir_type, arg_types, var_arg=is_var_arg)
        symbol_name = self._register_file_scope_function(
            funcname,
            function_type,
            storage=node.decl.storage,
            is_definition=True,
        )

        with self.new_function():

            existing = self.module.globals.get(symbol_name)
            if existing and isinstance(existing, ir.Function):
                if existing.is_declaration:
                    self.function = existing
                else:
                    raise SemanticError(f"redefinition of function '{funcname}'")
            else:
                try:
                    self.function = ir.Function(
                        self.module,
                        function_type,
                        name=symbol_name,
                    )
                    if self._is_file_scope_static(node.decl.storage):
                        self.function.linkage = "internal"
                except Exception:
                    raise SemanticError(f"failed to define function '{funcname}'")
            if self._func_decl_returns_unsigned(node.decl.type):
                self._mark_unsigned_return(self.function)
            self.block = self.function.append_basic_block()
            self.builder = ir.IRBuilder(self.block)
            if len(self.env.maps) > 1:
                self.env.maps[1][funcname] = (ir_type, self.function)
            self.define(funcname, (ir_type, self.function))
            if node.decl.type.args:
                param_idx = 0
                for p in node.decl.type.args.params:
                    if isinstance(p, c_ast.EllipsisParam):
                        continue
                    # Skip void params (f(void) means no params)
                    if isinstance(p, c_ast.Typename) and isinstance(
                        getattr(p, "type", None), c_ast.TypeDecl
                    ):
                        if isinstance(
                            p.type.type, c_ast.IdentifierType
                        ) and p.type.type.names == ["void"]:
                            continue
                    if param_idx >= len(arg_types):
                        break
                    arg_type = arg_types[param_idx]
                    pname = p.name if isinstance(p.name, str) else f"arg{param_idx}"
                    var = self._alloca_in_entry(arg_type, pname)
                    self.define(pname, (arg_type, var))
                    self._safe_store(self.function.args[param_idx], var)
                    # Track unsigned params
                    if isinstance(p, c_ast.Decl) and isinstance(
                        getattr(p, "type", None), c_ast.TypeDecl
                    ):
                        if isinstance(p.type.type, c_ast.IdentifierType):
                            if self._is_unsigned_type_names(p.type.type.names):
                                self._mark_unsigned(var)
                    if isinstance(p, c_ast.Decl):
                        if self._has_unsigned_scalar_pointee(p.type):
                            self._mark_unsigned_pointee(var)
                        if isinstance(
                            p.type, c_ast.PtrDecl
                        ) and self._func_decl_returns_unsigned(p.type.type):
                            self._mark_unsigned_return(var)
                    param_idx += 1

            self.codegen(node.body)

            if not self.builder.block.is_terminated:
                if isinstance(ir_type, ir.VoidType):
                    self.builder.ret_void()
                elif isinstance(ir_type, ir.PointerType):
                    self.builder.ret(ir.Constant(ir_type, None))
                elif self._is_floating_ir_type(ir_type):
                    self.builder.ret(ir.Constant(ir_type, 0.0))
                else:
                    self.builder.ret(ir.Constant(ir_type, 0))

            return None, None

    def codegen_Struct(self, node):
        # Generate LLVM types for struct members

        # If this is a reference to a named struct without decls, look it up
        if node.name and (node.decls is None or len(node.decls) == 0):
            if node.name in self.env:
                return self.env[node.name][0]
            # Opaque/forward-declared struct: treat as i8 (byte) for pointer use
            opaque = ir.IntType(8)
            self.define(node.name, (opaque, None))
            return opaque

        member_types = []
        member_names = []
        member_decl_types = []
        for decl in node.decls:
            if isinstance(decl.type, c_ast.TypeDecl) and isinstance(
                decl.type.type, c_ast.Struct
            ):
                nested_type = self.codegen_Struct(decl.type.type)
                member_types.append(nested_type)
            elif isinstance(decl.type, c_ast.TypeDecl) and isinstance(
                decl.type.type, c_ast.Union
            ):
                nested_type = self.codegen_Union(decl.type.type)
                member_types.append(nested_type)
            elif isinstance(decl.type, c_ast.ArrayDecl):
                # Handle multi-dimensional arrays: a[N][M] -> [N x [M x T]]
                def _build_array_type(arr_node):
                    dim = self._eval_dim(arr_node.dim) if arr_node.dim else 0
                    if isinstance(arr_node.type, c_ast.ArrayDecl):
                        inner = _build_array_type(arr_node.type)
                    else:
                        inner = self._resolve_ast_type(arr_node.type)
                    return ir.ArrayType(inner, dim)

                member_types.append(_build_array_type(decl.type))
            elif isinstance(decl.type, c_ast.PtrDecl):
                member_types.append(self._resolve_ast_type(decl.type))
            elif isinstance(decl.type, c_ast.TypeDecl):
                type_str = decl.type.type.names
                member_types.append(self._get_ir_type(type_str))
            else:
                member_types.append(int64_t)  # fallback
            member_names.append(decl.name)
            member_decl_types.append(decl.type)
        # Create the struct type
        struct_type = ir.LiteralStructType(member_types)
        struct_type.members = member_names
        struct_type.member_decl_types = member_decl_types

        # Register named structs for later reuse
        if node.name:
            self.define(node.name, (struct_type, None))

        return struct_type

    def codegen_Union(self, node):
        """Model union as a struct with alignment-preserving storage."""
        if node.name and (node.decls is None or len(node.decls) == 0):
            return self.env[node.name][0]

        member_types = {}
        member_decl_types = {}
        max_size = 0
        max_align = 1
        for decl in node.decls:
            if isinstance(decl.type, c_ast.ArrayDecl):
                ir_t = self._build_array_ir_type(decl.type)
            else:
                ir_t = self._resolve_ast_type(decl.type)
            member_types[decl.name] = ir_t
            member_decl_types[decl.name] = decl.type
            sz = self._ir_type_size(ir_t)
            al = self._ir_type_align(ir_t)
            if sz > max_size:
                max_size = sz
            if al > max_align:
                max_align = al

        # Use a struct {align_type, [padding x i8]} to preserve alignment
        # Pick an alignment element: i64 for 8, i32 for 4, i16 for 2, i8 for 1
        align_map = {8: int64_t, 4: int32_t, 2: int16_t, 1: int8_t}
        align_type = align_map.get(max_align, int64_t)
        align_size = max_align
        pad_size = max_size - align_size
        if pad_size > 0:
            union_type = ir.LiteralStructType(
                [align_type, ir.ArrayType(int8_t, pad_size)]
            )
        else:
            union_type = ir.LiteralStructType([align_type])
        union_type.members = list(member_types.keys())
        union_type.member_types = member_types
        union_type.member_decl_types = member_decl_types
        union_type.is_union = True

        if node.name:
            self.define(node.name, (union_type, None))

        return union_type

    def codegen_StructRef(self, node):

        if isinstance(node.name, c_ast.StructRef):
            inner_val, inner_addr = self.codegen_StructRef(node.name)
            if node.type == "->":
                # Chain: (a->b)->c — need to use the VALUE of a->b as pointer base
                # inner_val is the loaded field value (a pointer to next struct)
                base = inner_val
                semantic_base_type = self._get_expr_ir_type(node.name)
                if (
                    isinstance(semantic_base_type, ir.PointerType)
                    and base.type != semantic_base_type
                ):
                    try:
                        base = self.builder.bitcast(base, semantic_base_type)
                    except Exception:
                        pass
                struct_type = (
                    base.type.pointee if hasattr(base.type, "pointee") else int8_t
                )
                struct_addr = base
            else:
                # Chain: (a->b).c — use the ADDRESS of a->b as struct base
                semantic_base_type = self._get_expr_ir_type(node.name)
                if semantic_base_type is not None:
                    expected_addr_type = ir.PointerType(semantic_base_type)
                    if inner_addr.type != expected_addr_type:
                        try:
                            inner_addr = self.builder.bitcast(
                                inner_addr, expected_addr_type
                            )
                        except Exception:
                            pass
                struct_type = (
                    inner_addr.type.pointee
                    if hasattr(inner_addr.type, "pointee")
                    else int8_t
                )
                struct_addr = inner_addr
        elif isinstance(node.name, c_ast.ID):
            struct_instance_addr = self.env[node.name.name][1]
            if not isinstance(struct_instance_addr.type, ir.PointerType):
                raise Exception("Invalid struct reference")

            if node.type == "->":
                ptr_val = self._safe_load(struct_instance_addr)
                struct_type = (
                    ptr_val.type.pointee if hasattr(ptr_val.type, "pointee") else int8_t
                )
                struct_addr = ptr_val
            else:
                struct_type = (
                    struct_instance_addr.type.pointee
                    if hasattr(struct_instance_addr.type, "pointee")
                    else int8_t
                )
                struct_addr = struct_instance_addr
        else:
            # Cast/UnaryOp/other expression as struct base: ((Type*)ptr)->field
            val, addr = self.codegen(node.name)
            semantic_base_type = self._get_expr_ir_type(node.name)
            if node.type == "->":
                struct_addr = val
                if (
                    isinstance(semantic_base_type, ir.PointerType)
                    and struct_addr.type != semantic_base_type
                ):
                    try:
                        struct_addr = self.builder.bitcast(
                            struct_addr, semantic_base_type
                        )
                    except Exception:
                        pass
                struct_type = (
                    struct_addr.type.pointee
                    if hasattr(struct_addr.type, "pointee")
                    else int8_t
                )
            else:
                struct_addr = addr if addr else val
                if addr is not None and semantic_base_type is not None:
                    expected_addr_type = ir.PointerType(semantic_base_type)
                    if struct_addr.type != expected_addr_type:
                        try:
                            struct_addr = self.builder.bitcast(
                                struct_addr, expected_addr_type
                            )
                        except Exception:
                            pass
                    struct_type = (
                        struct_addr.type.pointee
                        if hasattr(struct_addr.type, "pointee")
                        else int8_t
                    )
                else:
                    struct_type = (
                        semantic_base_type
                        if semantic_base_type is not None
                        else (val.type if hasattr(val.type, "members") else int8_t)
                    )

        # Union access: all fields share offset 0, use bitcast
        if getattr(struct_type, "is_union", False):
            member_ir_type = struct_type.member_types[node.field.name]
            semantic_field_type = member_ir_type
            member_decl_types = getattr(struct_type, "member_decl_types", None)
            decl_type = None
            if member_decl_types and node.field.name in member_decl_types:
                decl_type = member_decl_types[node.field.name]
                try:
                    resolved = self._resolve_ast_type(decl_type)
                    if isinstance(member_ir_type, ir.ArrayType) and isinstance(
                        resolved, ir.PointerType
                    ):
                        pass
                    elif isinstance(
                        resolved, (ir.ArrayType, ir.LiteralStructType, ir.PointerType)
                    ):
                        semantic_field_type = resolved
                except Exception:
                    pass
            ptr = self.builder.bitcast(struct_addr, ir.PointerType(semantic_field_type))
            if isinstance(semantic_field_type, ir.ArrayType):
                elem_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    name="unionarraydecay",
                )
                if decl_type is not None:
                    self._tag_value_from_decl_type(elem_ptr, decl_type)
                self._set_expr_ir_type(node, semantic_field_type)
                return elem_ptr, ptr
            val = self._safe_load(ptr)
            if decl_type is not None:
                self._tag_value_from_decl_type(val, decl_type)
            self._set_expr_ir_type(node, semantic_field_type)
            return val, ptr

        # Opaque struct (no members) — treat as byte-offset access
        if not hasattr(struct_type, "members"):
            ptr = self.builder.bitcast(struct_addr, voidptr_t)
            val = self._safe_load(self.builder.bitcast(ptr, ir.PointerType(int64_t)))
            self._set_expr_ir_type(node, int64_t)
            return val, ptr

        field_index = None
        for i, field in enumerate(struct_type.members):
            if field == node.field.name:
                field_index = i
                break

        if field_index is None:
            raise RuntimeError(f"Field '{node.field.name}' not found in struct")

        field_addr = self.builder.gep(
            struct_addr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            inbounds=True,
        )

        field_type = struct_type.elements[field_index]
        semantic_field_type = field_type
        member_decl_types = getattr(struct_type, "member_decl_types", None)
        decl_type = None
        if member_decl_types and field_index < len(member_decl_types):
            decl_type = member_decl_types[field_index]
            try:
                resolved = self._resolve_ast_type(decl_type)
                # Only use semantic type if it's more specific (pointer/struct),
                # not if it decayed an array to pointer
                if isinstance(field_type, ir.ArrayType) and isinstance(
                    resolved, ir.PointerType
                ):
                    pass  # keep original array type
                elif isinstance(resolved, (ir.LiteralStructType, ir.PointerType)):
                    semantic_field_type = resolved
            except Exception:
                pass

        typed_field_addr = field_addr
        target_ptr_type = ir.PointerType(semantic_field_type)
        if field_addr.type != target_ptr_type:
            try:
                typed_field_addr = self.builder.bitcast(field_addr, target_ptr_type)
            except Exception:
                typed_field_addr = field_addr

        if isinstance(semantic_field_type, ir.ArrayType):
            # Array field: decay to pointer to first element
            elem_ptr = self.builder.gep(
                typed_field_addr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                name="arraydecay",
            )
            if decl_type is not None:
                self._tag_value_from_decl_type(elem_ptr, decl_type)
            self._set_expr_ir_type(node, semantic_field_type)
            return elem_ptr, typed_field_addr

        field_value = self._safe_load(typed_field_addr)
        if decl_type is not None:
            self._tag_value_from_decl_type(field_value, decl_type)
        self._set_expr_ir_type(node, semantic_field_type)
        return field_value, typed_field_addr

    def codegen_EmptyStatement(self, node):
        return None, None

    def codegen_ExprList(self, node):
        # Comma operator: evaluate all, return last
        result = None
        result_ptr = None
        last_expr = None
        for expr in node.exprs:
            last_expr = expr
            result, result_ptr = self.codegen(expr)
        if last_expr is not None:
            semantic_result_type = self._get_expr_ir_type(
                last_expr, getattr(result, "type", None)
            )
            if semantic_result_type is not None:
                self._set_expr_ir_type(node, semantic_result_type)
        return result, result_ptr

    def codegen_Label(self, node):
        label_name = f"label_{node.name}"
        # Check if block already created by a forward goto
        if label_name in self._labels:
            label_bb = self._labels[label_name]
        else:
            label_bb = self.builder.function.append_basic_block(label_name)
            self._labels[label_name] = label_bb
        if not self.builder.block.is_terminated:
            self.builder.branch(label_bb)
        self.builder.position_at_end(label_bb)
        if node.stmt:
            self.codegen(node.stmt)
        return None, None

    def codegen_Goto(self, node):
        label_name = f"label_{node.name}"
        if label_name in self._labels:
            target_bb = self._labels[label_name]
        else:
            # Forward reference: create the block now
            target_bb = self.builder.function.append_basic_block(label_name)
            self._labels[label_name] = target_bb
        self.builder.branch(target_bb)
        return None, None

    def codegen_Enum(self, node):
        # Define each enumerator as a constant in the environment
        if node.values:
            current_val = 0
            for enumerator in node.values.enumerators:
                if enumerator.value:
                    current_val = self._eval_const_expr(enumerator.value)
                self.define(
                    enumerator.name, (int64_t, ir.Constant(int64_t, current_val))
                )
                current_val += 1
        return None, None

    def _eval_const_expr(self, node):
        """Evaluate a constant expression at compile time (for enum values)."""
        if isinstance(node, c_ast.Constant):
            if node.type == "string":
                return 0  # string constants can't be int-evaluated
            v = node.value.rstrip("uUlL")
            if v.startswith("'"):
                return self._char_constant_value(v)
            if v.startswith("0x") or v.startswith("0X"):
                return int(v, 16)
            elif v.startswith("0") and len(v) > 1 and v[1:].isdigit():
                return int(v, 8)
            try:
                return int(v)
            except ValueError:
                return 0
        elif isinstance(node, c_ast.UnaryOp):
            if node.op == "sizeof":
                if isinstance(node.expr, c_ast.Typename):
                    ir_t = self._resolve_ast_type(node.expr.type)
                    return self._ir_type_size(ir_t)
                if isinstance(node.expr, c_ast.Constant) and node.expr.type == "string":
                    raw = node.expr.value[1:-1]
                    processed = self._process_escapes(raw)
                    return len(self._string_bytes(processed + "\00"))
                val = self._eval_const_expr(node.expr)
                return 8  # default sizeof for expressions
            if node.op == "&" and isinstance(node.expr, c_ast.StructRef):
                offset, _ = self._eval_offsetof_structref(node.expr)
                return offset
            val = self._eval_const_expr(node.expr)
            if node.op == "-":
                return -val
            elif node.op == "+":
                return val
            elif node.op == "~":
                return ~val
            elif node.op == "!":
                return 0 if val else 1
        elif isinstance(node, c_ast.BinaryOp):
            l = self._eval_const_expr(node.left)
            r = self._eval_const_expr(node.right)
            ops = {
                "+": lambda a, b: a + b,
                "-": lambda a, b: a - b,
                "*": lambda a, b: a * b,
                "/": lambda a, b: a // b,
                "%": lambda a, b: a % b,
                "<<": lambda a, b: a << b,
                ">>": lambda a, b: a >> b,
                "&": lambda a, b: a & b,
                "|": lambda a, b: a | b,
                "^": lambda a, b: a ^ b,
                "==": lambda a, b: int(a == b),
                "!=": lambda a, b: int(a != b),
                "<": lambda a, b: int(a < b),
                "<=": lambda a, b: int(a <= b),
                ">": lambda a, b: int(a > b),
                ">=": lambda a, b: int(a >= b),
                "&&": lambda a, b: int(bool(a) and bool(b)),
                "||": lambda a, b: int(bool(a) or bool(b)),
            }
            return ops[node.op](l, r)
        elif isinstance(node, c_ast.TernaryOp):
            cond = self._eval_const_expr(node.cond)
            if cond:
                return self._eval_const_expr(node.iftrue)
            return self._eval_const_expr(node.iffalse)
        elif isinstance(node, c_ast.ID):
            # Try to look up as enum constant or defined value
            if node.name in self.env:
                _, val = self.env[node.name]
                if isinstance(val, ir.values.Constant) and isinstance(
                    val.type, ir.IntType
                ):
                    return int(val.constant)
            return 0  # unknown identifier defaults to 0
        elif isinstance(node, c_ast.Cast):
            return self._eval_const_expr(node.expr)
        elif isinstance(node, c_ast.Typename):
            return 0
        raise CodegenError(f"Not a constant expression: {type(node).__name__}")

    def codegen_InitList(self, node):
        # InitList as expression — return first element or zero
        if node.exprs:
            return self.codegen(node.exprs[0])
        return ir.Constant(int64_t, 0), None

    def codegen_DeclList(self, node):
        for decl in node.decls:
            self.codegen(decl)
        return None, None

    def codegen_Typedef(self, node):
        # typedef int myint; / typedef int* intptr; / typedef struct{...} Name;
        if isinstance(node.type, c_ast.TypeDecl):
            if isinstance(node.type.type, c_ast.IdentifierType):
                base_type = node.type.type.names
                self.define(f"__typedef_{node.name}", base_type)
            elif isinstance(node.type.type, c_ast.Struct):
                if node.type.type.name:
                    # Named struct: store reference to struct name for lazy resolution
                    self.codegen_Struct(node.type.type)  # ensure it's registered
                    self.define(
                        f"__typedef_{node.name}", f"__struct_{node.type.type.name}"
                    )
                else:
                    struct_type = self.codegen_Struct(node.type.type)
                    self.define(f"__typedef_{node.name}", struct_type)
            elif isinstance(node.type.type, c_ast.Union):
                if node.type.type.name:
                    self.codegen_Union(node.type.type)
                    self.define(
                        f"__typedef_{node.name}", f"__struct_{node.type.type.name}"
                    )
                else:
                    union_type = self.codegen_Union(node.type.type)
                    self.define(f"__typedef_{node.name}", union_type)
            elif isinstance(node.type.type, c_ast.Enum):
                # typedef enum { A, B, C } MyEnum;
                self.codegen_Enum(node.type.type)
                self.define(f"__typedef_{node.name}", int64_t)
        elif isinstance(node.type, c_ast.ArrayDecl):
            self.define(f"__typedef_{node.name}", self._build_array_ir_type(node.type))
        elif isinstance(node.type, c_ast.PtrDecl):
            inner = node.type.type
            if isinstance(inner, c_ast.FuncDecl):
                fp_type = self._build_func_ptr_type(inner)
                self.define(f"__typedef_{node.name}", fp_type)
            elif isinstance(inner, c_ast.TypeDecl):
                if isinstance(inner.type, c_ast.IdentifierType):
                    base_ir = self._get_ir_type(inner.type.names)
                elif isinstance(inner.type, c_ast.Struct):
                    base_ir = self.codegen_Struct(inner.type)
                elif isinstance(inner.type, c_ast.Union):
                    base_ir = self.codegen_Union(inner.type)
                else:
                    base_ir = get_ir_type(
                        inner.type.names if hasattr(inner.type, "names") else ["int"]
                    )
                if isinstance(base_ir, ir.VoidType):
                    ptr_type = voidptr_t
                else:
                    ptr_type = ir.PointerType(base_ir)
                self.define(f"__typedef_{node.name}", ptr_type)
        return None, None
