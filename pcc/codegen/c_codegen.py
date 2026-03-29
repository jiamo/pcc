import logging
import llvmlite.ir as ir
import math
import re
import struct
from collections import ChainMap
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from itertools import count
from llvmlite.ir import IRBuilder
from ..ast import c_ast as c_ast

_logger = logging.getLogger("pcc.codegen")

bool_t = ir.IntType(1)
int8_t = ir.IntType(8)
int32_t = ir.IntType(32)
int64_t = ir.IntType(64)
int128_t = ir.IntType(128)
voidptr_t = int8_t.as_pointer()
int64ptr_t = int64_t.as_pointer()
true_bit = bool_t(1)
false_bit = bool_t(0)
true_byte = int8_t(1)
false_byte = int8_t(0)
cstring = voidptr_t
struct_types = {}
_aggregate_namespace_counter = count(1)


class SemanticError(ValueError):
    pass


@dataclass
class FileScopeObjectState:
    type_key: str
    linkage: str
    definition_kind: str
    symbol_name: str
    ir_type: object


@dataclass
class FileScopeFunctionState:
    type_key: str
    linkage: str
    defined: bool
    symbol_name: str


@dataclass
class StructFieldLayout:
    name: object
    byte_offset: int
    semantic_ir_type: object
    decl_type: object
    is_bitfield: bool = False
    storage_byte_offset: int = 0
    storage_ir_type: object = None
    bit_offset: int = 0
    bit_width: int = 0
    is_unsigned: bool = False


@dataclass
class StructStorageSegment:
    kind: str
    byte_offset: int
    ir_type: object
    field_index: object = None
    bitfield_indices: tuple = ()


@dataclass
class BitFieldRef:
    container_ptr: object
    storage_ir_type: object
    bit_offset: int
    bit_width: int
    semantic_ir_type: object
    is_unsigned: bool

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
    "getlogin": (cstring, [], False),
    "getpwent": (voidptr_t, [], False),
    "getpwnam": (voidptr_t, [cstring], False),
    "getpwuid": (voidptr_t, [int32_t], False),
    "setpwent": (_VOID, [], False),
    "endpwent": (_VOID, [], False),
    "setenv": (int32_t, [cstring, cstring, int32_t], False),
    "putenv": (int32_t, [cstring], False),
    "unsetenv": (int32_t, [cstring], False),
    "system": (int32_t, [cstring], False),
    # === string.h ===
    "strlen": (_size_t, [cstring], False),
    "strcmp": (int32_t, [cstring, cstring], False),
    "strncmp": (int32_t, [cstring, cstring, _size_t], False),
    "strcpy": (cstring, [cstring, cstring], False),
    "strncpy": (cstring, [cstring, cstring, _size_t], False),
    "strcat": (cstring, [cstring, cstring], False),
    "strncat": (cstring, [cstring, cstring, _size_t], False),
    "strlcpy": (_size_t, [cstring, cstring, _size_t], False),
    "strlcat": (_size_t, [cstring, cstring, _size_t], False),
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
    # === wchar.h / wctype.h ===
    "mbtowc": (int32_t, [int32_t.as_pointer(), cstring, _size_t], False),
    "wctomb": (int32_t, [cstring, int32_t], False),
    "mbrlen": (_size_t, [cstring, _size_t, voidptr_t], False),
    "mbrtowc": (_size_t, [int32_t.as_pointer(), cstring, _size_t, voidptr_t], False),
    "mbsrtowcs": (_size_t, [int32_t.as_pointer(), voidptr_t, _size_t, voidptr_t], False),
    "mbstowcs": (_size_t, [int32_t.as_pointer(), cstring, _size_t], False),
    "wcrtomb": (_size_t, [cstring, int32_t, voidptr_t], False),
    "wcsrtombs": (_size_t, [cstring, voidptr_t, _size_t, voidptr_t], False),
    "wcstombs": (_size_t, [cstring, voidptr_t, _size_t], False),
    "wcwidth": (int32_t, [int32_t], False),
    "wcswidth": (int32_t, [voidptr_t, _size_t], False),
    "wmemchr": (voidptr_t, [voidptr_t, int32_t, _size_t], False),
    "wmemcpy": (voidptr_t, [voidptr_t, voidptr_t, _size_t], False),
    "wmemmove": (voidptr_t, [voidptr_t, voidptr_t, _size_t], False),
    "wmemcmp": (int32_t, [voidptr_t, voidptr_t, _size_t], False),
    "iswalnum": (int32_t, [int32_t], False),
    "iswalpha": (int32_t, [int32_t], False),
    "iswcntrl": (int32_t, [int32_t], False),
    "iswctype": (int32_t, [int32_t, int32_t], False),
    "iswgraph": (int32_t, [int32_t], False),
    "iswlower": (int32_t, [int32_t], False),
    "iswprint": (int32_t, [int32_t], False),
    "iswspace": (int32_t, [int32_t], False),
    "iswupper": (int32_t, [int32_t], False),
    "towlower": (int32_t, [int32_t], False),
    "towupper": (int32_t, [int32_t], False),
    "wctype": (int32_t, [cstring], False),
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
    "fabsf": (_float, [_float], False),
    "fabs": (_double, [_double], False),
    "fabsl": (_double, [_double], False),
    "ldexp": (_double, [_double, int32_t], False),
    # === time.h ===
    "time": (_time_t, [voidptr_t], False),
    "clock": (int64_t, [], False),
    "difftime": (_double, [_time_t, _time_t], False),
    "gmtime_r": (voidptr_t, [voidptr_t, voidptr_t], False),
    "localtime_r": (voidptr_t, [voidptr_t, voidptr_t], False),
    "nanosleep": (int32_t, [voidptr_t, voidptr_t], False),
    # === unistd.h (POSIX) ===
    "sleep": (int32_t, [int32_t], False),
    "alarm": (int32_t, [int32_t], False),
    "usleep": (int32_t, [int32_t], False),
    "read": (int64_t, [int32_t, voidptr_t, _size_t], False),
    "write": (int64_t, [int32_t, voidptr_t, _size_t], False),
    "getuid": (int32_t, [], False),
    "geteuid": (int32_t, [], False),
    "open": (int32_t, [cstring, int32_t], True),
    "close": (int32_t, [int32_t], False),
    "access": (int32_t, [cstring, int32_t], False),
    "fcntl": (int32_t, [int32_t, int32_t], True),
    "fsync": (int32_t, [int32_t], False),
    "ftruncate": (int32_t, [int32_t, int64_t], False),
    "pread": (int64_t, [int32_t, voidptr_t, _size_t, int64_t], False),
    "pwrite": (int64_t, [int32_t, voidptr_t, _size_t, int64_t], False),
    "unlink": (int32_t, [cstring], False),
    "readlink": (int64_t, [cstring, cstring, _size_t], False),
    "getpid": (int32_t, [], False),
    "getppid": (int32_t, [], False),
    "sysconf": (int64_t, [int32_t], False),
    "isatty": (int32_t, [int32_t], False),
    "mkstemp": (int32_t, [cstring], False),
    "tcdrain": (int32_t, [int32_t], False),
    "tcflow": (int32_t, [int32_t, int32_t], False),
    "tcgetattr": (int32_t, [int32_t, voidptr_t], False),
    "tcsetattr": (int32_t, [int32_t, int32_t, voidptr_t], False),
    "select": (int32_t, [int32_t, voidptr_t, voidptr_t, voidptr_t, voidptr_t], False),
    "pselect": (int32_t, [int32_t, voidptr_t, voidptr_t, voidptr_t, voidptr_t, voidptr_t], False),
    # === setjmp.h ===
    "setjmp": (int32_t, [voidptr_t], False),
    "longjmp": (_VOID, [voidptr_t, int32_t], False),
    "_setjmp": (int32_t, [voidptr_t], False),
    "_longjmp": (_VOID, [voidptr_t, int32_t], False),
    "sigsetjmp": (int32_t, [voidptr_t, int32_t], False),
    "siglongjmp": (_VOID, [voidptr_t, int32_t], False),
    # === signal.h ===
    "signal": (voidptr_t, [int32_t, voidptr_t], False),
    "sigaction": (int32_t, [int32_t, voidptr_t, voidptr_t], False),
    "sigaddset": (int32_t, [voidptr_t, int32_t], False),
    "sigdelset": (int32_t, [voidptr_t, int32_t], False),
    "sigemptyset": (int32_t, [voidptr_t], False),
    "sigismember": (int32_t, [voidptr_t, int32_t], False),
    "sigprocmask": (int32_t, [int32_t, voidptr_t, voidptr_t], False),
    "sigsuspend": (int32_t, [voidptr_t], False),
    "kill": (int32_t, [int32_t, int32_t], False),
    "raise": (int32_t, [int32_t], False),
    # === errno ===
    "__errno_location": (ir.IntType(32).as_pointer(), [], False),
    # === locale.h ===
    "setlocale": (cstring, [int32_t, cstring], False),
    "localeconv": (voidptr_t, [], False),
    "nl_langinfo": (cstring, [int32_t], False),
    # === misc ===
    "tmpnam": (cstring, [cstring], False),
    "tmpfile": (voidptr_t, [], False),
    "__errno_location": (int32_t.as_pointer(), [], False),
    "stat": (int32_t, [cstring, voidptr_t], False),
    "fstat": (int32_t, [int32_t, voidptr_t], False),
    "lstat": (int32_t, [cstring, voidptr_t], False),
    "chmod": (int32_t, [cstring, int32_t], False),
    "fchmod": (int32_t, [int32_t, int32_t], False),
    "mkdir": (int32_t, [cstring, int32_t], False),
    "umask": (int32_t, [int32_t], False),
    "utime": (int32_t, [cstring, voidptr_t], False),
    "utimes": (int32_t, [cstring, voidptr_t], False),
    "futimes": (int32_t, [int32_t, voidptr_t], False),
    "gettimeofday": (int32_t, [voidptr_t, voidptr_t], False),
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
    "__builtin_alloca": (voidptr_t, [int64_t], False),
    "__builtin_expect": (int64_t, [int64_t, int64_t], False),
    "__builtin_assume": (_VOID, [int64_t], False),
    "__builtin_prefetch": (_VOID, [voidptr_t, int32_t, int32_t], False),
    "__builtin_unreachable": (_VOID, [], False),
    "__builtin_add_overflow": (int32_t, [int64_t, int64_t, voidptr_t], False),
    "__builtin_sub_overflow": (int32_t, [int64_t, int64_t, voidptr_t], False),
    "__builtin_mul_overflow": (int32_t, [int64_t, int64_t, voidptr_t], False),
    "__builtin_bswap16": (int32_t, [int32_t], False),
    "__builtin_bswap32": (int32_t, [int32_t], False),
    "__builtin_bswap64": (int64_t, [int64_t], False),
    "__builtin_clz": (int32_t, [int32_t], False),
    "__builtin_clzll": (int32_t, [int64_t], False),
    "__builtin_ctz": (int32_t, [int32_t], False),
    "__builtin_ctzll": (int32_t, [int64_t], False),
    "__builtin_rotateleft32": (int32_t, [int32_t, int32_t], False),
    "__builtin_rotateleft64": (int64_t, [int64_t, int64_t], False),
    "__builtin_rotateright32": (int32_t, [int32_t, int32_t], False),
    "__builtin_rotateright64": (int64_t, [int64_t, int64_t], False),
    "__sync_synchronize": (_VOID, [], False),
    "__sync_fetch_and_add": (int64_t, [voidptr_t, int64_t], False),
    "__sync_bool_compare_and_swap": (int32_t, [voidptr_t, int64_t, int64_t], False),
    "__atomic_load_n": (int64_t, [voidptr_t, int32_t], False),
    "__atomic_store_n": (_VOID, [voidptr_t, int64_t, int32_t], False),
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


def postprocess_ir_text(text):
    """Apply the minimal textual lowering that llvmlite cannot express directly."""

    text = _PCC_VAARG_DECL_RE.sub("", text)

    def repl(match):
        lhs = match.group("lhs")
        rettype = match.group("rettype")
        argtype = match.group("argtype")
        argval = match.group("argval")
        return f"{lhs} = va_arg {argtype} {argval}, {rettype}"

    text = _PCC_VAARG_CALL_RE.sub(repl, text)
    return text


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
        "_Float16": _float,
        "short": int16_t,
        "long": int64_t,
        "int short": int16_t,
        "int long": int64_t,
        "long long": int64_t,
        "int long long": int64_t,
        "__int128": int128_t,
        "char unsigned": int8_t,
        "int unsigned": int32_t,
        "unsigned": int32_t,
        "int short unsigned": int16_t,
        "short unsigned": int16_t,
        "int long unsigned": int64_t,
        "long unsigned": int64_t,
        "long long unsigned": int64_t,
        "__int128 unsigned": int128_t,
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
    if "_Float16" in names:
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
            is_var_arg = False
            if inner.args:
                for p in inner.args.params:
                    if isinstance(p, c_ast.EllipsisParam):
                        is_var_arg = True
                        continue
                    t = get_ir_type_from_node(p)
                    if not isinstance(t, ir.VoidType):
                        param_types.append(t)
            return ir.FunctionType(
                ret_type,
                param_types,
                var_arg=is_var_arg,
            ).as_pointer()
        pointee = _resolve_node_type(inner)
        if isinstance(pointee, ir.VoidType):
            return voidptr_t
        return ir.PointerType(pointee)
    elif isinstance(node_type, c_ast.TypeDecl):
        if isinstance(node_type.type, c_ast.IdentifierType):
            return get_ir_type(node_type.type.names)
        elif isinstance(node_type.type, c_ast.Struct):
            snode = node_type.type
            if snode.decls is not None:
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
            if unode.decls is not None:
                # Inline union with declarations — compute max size
                if len(unode.decls) == 0:
                    ut = ir.LiteralStructType([])
                    ut.members = []
                    ut.member_types = {}
                    ut.member_decl_types = {}
                    ut.is_union = True
                    return ut
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
                    if _is_struct_ir_type(ir_t):
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
        elif isinstance(node_type.type, c_ast.Enum):
            return int32_t
        return int64_t
    elif isinstance(node_type, c_ast.ArrayDecl):
        return voidptr_t  # array params decay to pointer
    return int64_t


def _is_struct_ir_type(ir_type):
    return isinstance(ir_type, (ir.LiteralStructType, ir.IdentifiedStructType))


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
        self._function_display_name = None
        self._frame_address_marker = None
        self.in_global = True
        self._declared_libc = set()
        self._unsigned_bindings = set()  # alloca/global ids with unsigned type
        self._unsigned_pointee_bindings = set()
        self._unsigned_return_bindings = set()
        self._vla_bindings = set()
        self._expr_ir_types = {}
        self._decl_ast_types = ChainMap()
        self._typedef_ast_types = ChainMap()
        self._labels = {}
        self._label_value_tags = {}
        self._vaarg_counter = 0
        self._anon_type_counter = 0
        self._file_scope_object_states = {}
        self._file_scope_function_states = {}
        self._future_file_scope_object_ir_types = {}
        self._future_file_scope_function_ir_types = {}
        self.translation_unit_name = self._sanitize_translation_unit_name(
            translation_unit_name
        )
        self._global_compound_literal_cache = {}
        self._switch_contexts = []
        base_namespace = self.translation_unit_name or "pcc"
        self._aggregate_namespace = (
            f"{base_namespace}_{next(_aggregate_namespace_counter)}"
        )
        self._scope_id_counter = 0
        self._current_scope_id = 0

    def define(self, name, val):
        self.env[name] = val

    def _record_decl_ast_type(self, name, node_type):
        if name:
            self._decl_ast_types[name] = node_type

    def _lookup_decl_ast_type(self, name):
        return self._decl_ast_types.get(name)

    def _record_typedef_ast_type(self, name, node_type):
        if name:
            self._typedef_ast_types[name] = node_type

    def _lookup_typedef_ast_type(self, name):
        return self._typedef_ast_types.get(name)

    @staticmethod
    def _sanitize_translation_unit_name(name):
        if not name:
            return None
        return re.sub(r"\W+", "_", name)

    @staticmethod
    def _tag_type_key(name):
        return f"__struct_{name}"

    @staticmethod
    def _enum_tag_key(name):
        return f"__enum_{name}"

    def _next_anon_struct_name(self, kind):
        self._anon_type_counter += 1
        return (
            f"__pcc_{self._aggregate_namespace}_{kind}_{self._anon_type_counter}"
        )

    def _aggregate_type_name(self, kind, name=None, scope_id=None):
        if name:
            active_scope_id = (
                self._current_scope_id if scope_id is None else scope_id
            )
            return (
                f"__pcc_{self._aggregate_namespace}_{kind}_{active_scope_id}_{name}"
            )
        return self._next_anon_struct_name(kind)

    def _identified_aggregate_type(self, kind, name, body):
        aggregate_type = self.module.context.get_identified_type(
            self._aggregate_type_name(kind, name)
        )
        if aggregate_type.is_opaque:
            aggregate_type.set_body(*body)
        return aggregate_type

    def _is_file_scope_static(self, storage=None):
        return (
            self.translation_unit_name
            and self.in_global
            and storage
            and "static" in storage
        )

    def _has_internal_inline_linkage(self, storage=None, funcspec=None):
        return (
            self.translation_unit_name
            and self.in_global
            and funcspec
            and "inline" in funcspec
            and not storage
        )

    def _file_scope_symbol_name(self, name, storage=None, funcspec=None, linkage=None):
        if linkage == "internal" or self._is_file_scope_static(storage) or self._has_internal_inline_linkage(storage, funcspec):
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

    def _decl_linkage(self, storage=None, funcspec=None, existing_state=None):
        if storage and "static" in storage:
            return "internal"
        if self._has_internal_inline_linkage(storage, funcspec):
            return "internal"
        if existing_state is not None:
            return existing_state.linkage
        return "external"

    def _effective_file_scope_symbol_name(
        self, name, storage=None, funcspec=None, existing_state=None, linkage=None
    ):
        if linkage is None:
            linkage = self._decl_linkage(
                storage, funcspec=funcspec, existing_state=existing_state
            )
        if linkage == "internal":
            if existing_state is not None and existing_state.linkage == "internal":
                return existing_state.symbol_name
            return self._file_scope_symbol_name(
                name, storage=storage, funcspec=funcspec, linkage=linkage
            )
        if existing_state is not None and existing_state.linkage == "internal":
            return existing_state.symbol_name
        return name

    def _file_scope_object_definition_kind(self, storage=None, has_initializer=False):
        if storage and "extern" in storage and not has_initializer:
            return "extern"
        if has_initializer:
            return "definition"
        return "tentative"

    def _is_incomplete_array_ir_type(self, ir_type):
        return isinstance(ir_type, ir.ArrayType) and ir_type.count == 0

    def _are_compatible_object_ir_types(self, existing_ir_type, new_ir_type):
        if str(existing_ir_type) == str(new_ir_type):
            return True
        if not (
            isinstance(existing_ir_type, ir.ArrayType)
            and isinstance(new_ir_type, ir.ArrayType)
        ):
            return False
        if str(existing_ir_type.element) != str(new_ir_type.element):
            return False
        return (
            existing_ir_type.count == 0
            or new_ir_type.count == 0
            or existing_ir_type.count == new_ir_type.count
        )

    def _merge_object_ir_types(self, existing_ir_type, new_ir_type):
        if self._is_incomplete_array_ir_type(existing_ir_type) and not self._is_incomplete_array_ir_type(new_ir_type):
            return new_ir_type
        return existing_ir_type

    def _preferred_file_scope_object_ir_type(self, name, ir_type):
        future_ir_type = self._future_file_scope_object_ir_types.get(name)
        if future_ir_type is None:
            return ir_type
        if not self._are_compatible_object_ir_types(ir_type, future_ir_type):
            return ir_type
        return self._merge_object_ir_types(ir_type, future_ir_type)

    def _preferred_file_scope_function_ir_type(self, name, function_type, has_prototype):
        if has_prototype:
            return function_type
        future_type = self._future_file_scope_function_ir_types.get(name)
        if future_type is None:
            return function_type
        if str(function_type.return_type) != str(future_type.return_type):
            return function_type
        return future_type

    def _prepare_file_scope_object(self, name, ir_type, storage=None, has_initializer=False):
        if not self.in_global or name is None:
            return None, True
        if name in self._file_scope_function_states:
            raise SemanticError(f"'{name}' redeclared as object after function declaration")
        ir_type = self._preferred_file_scope_object_ir_type(name, ir_type)

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
                ir_type=ir_type,
            )
            self._file_scope_object_states[name] = state
            if definition_kind == "extern":
                self._record_extern_global(name, ir_type, storage=storage)
                return None, False
            gv = self._create_bound_global(
                name, ir_type, symbol_name=symbol_name, storage=storage
            )
            return gv, True

        if not self._are_compatible_object_ir_types(state.ir_type, ir_type):
            raise SemanticError(f"conflicting types for global '{name}'")
        merged_ir_type = self._merge_object_ir_types(state.ir_type, ir_type)
        state.ir_type = merged_ir_type
        state.type_key = str(merged_ir_type)
        if state.linkage != linkage:
            raise SemanticError(f"conflicting linkage for global '{name}'")
        if state.symbol_name != symbol_name:
            raise SemanticError(f"conflicting symbol binding for global '{name}'")

        existing = self.module.globals.get(symbol_name)
        if definition_kind == "extern":
            if existing is not None:
                self.define(name, (merged_ir_type, existing))
            else:
                self._record_extern_global(name, merged_ir_type, storage=storage)
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
        self, name, function_type, storage=None, funcspec=None, is_definition=False
    ):
        if not self.in_global or name is None:
            return
        if name in self._file_scope_object_states:
            raise SemanticError(f"'{name}' redeclared as function after object declaration")

        type_key = str(function_type)
        state = self._file_scope_function_states.get(name)
        linkage = self._decl_linkage(storage, funcspec=funcspec, existing_state=state)
        symbol_name = self._effective_file_scope_symbol_name(
            name,
            storage=storage,
            funcspec=funcspec,
            existing_state=state,
            linkage=linkage,
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

    @staticmethod
    def _function_arg_types_match(lhs_args, rhs_args):
        if len(lhs_args) != len(rhs_args):
            return False
        return all(str(lhs) == str(rhs) for lhs, rhs in zip(lhs_args, rhs_args))

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

    def _extern_decl_ir_type(self, name, node_type):
        if isinstance(node_type, c_ast.ArrayDecl):
            ir_type = self._build_array_ir_type(node_type)
        else:
            ir_type = self._resolve_ast_type(node_type)
        return self._preferred_file_scope_object_ir_type(name, ir_type)

    def _static_local_ir_type(self, node_type, init_node=None):
        if isinstance(node_type, c_ast.ArrayDecl):
            return self._build_array_ir_type(node_type, init_node=init_node)
        return self._resolve_ast_type(node_type)

    def _collect_file_scope_object_ir_types(self, ext_nodes):
        merged_types = {}
        for ext in ext_nodes:
            if not (
                isinstance(ext, c_ast.Decl)
                and ext.name is not None
                and not isinstance(ext.type, c_ast.FuncDecl)
            ):
                continue
            try:
                if isinstance(ext.type, c_ast.ArrayDecl):
                    ir_type = self._build_array_ir_type(ext.type, init_node=ext.init)
                else:
                    ir_type = self._resolve_ast_type(ext.type)
            except Exception as exc:
                _logger.debug("skipping object type for %r: %s", ext.name, exc)
                continue
            existing_ir_type = merged_types.get(ext.name)
            if existing_ir_type is None:
                merged_types[ext.name] = ir_type
                continue
            if self._are_compatible_object_ir_types(existing_ir_type, ir_type):
                merged_types[ext.name] = self._merge_object_ir_types(
                    existing_ir_type, ir_type
                )
        self._future_file_scope_object_ir_types = merged_types

    def _collect_file_scope_function_ir_types(self, ext_nodes):
        future_types = {}
        for ext in ext_nodes:
            decl = None
            if isinstance(ext, c_ast.FuncDef):
                decl = ext.decl
            elif isinstance(ext, c_ast.Decl) and isinstance(ext.type, c_ast.FuncDecl):
                decl = ext
            if decl is None or decl.name is None:
                continue
            if getattr(decl.type, "args", None) is None:
                continue
            try:
                if isinstance(ext, c_ast.FuncDef):
                    function_type, _ = self._build_future_funcdef_ir_type(ext)
                else:
                    function_type, _ = self._build_function_ir_type(decl.type)
            except Exception as exc:
                _logger.debug("skipping function type for %r: %s", decl.name, exc)
                continue
            future_types[decl.name] = function_type
        self._future_file_scope_function_ir_types = future_types

    def _record_extern_global(self, name, ir_type, storage=None):
        ir_type = self._preferred_file_scope_object_ir_type(name, ir_type)
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

    def _mark_vla_binding(self, binding):
        if binding is not None:
            self._vla_bindings.add(id(binding))

    def _is_vla_binding(self, binding):
        return binding is not None and id(binding) in self._vla_bindings

    def _collect_function_label_names(self, node):
        labels = []

        def visit(current):
            if current is None:
                return
            if isinstance(current, c_ast.Switch):
                visit(current.cond)
                return
            if isinstance(current, c_ast.Label):
                labels.append(current.name)
                visit(current.stmt)
                return
            for _child_name, child in current.children():
                if isinstance(child, list):
                    for item in child:
                        visit(item)
                else:
                    visit(child)

        visit(node)
        ordered = []
        seen = set()
        for name in labels:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    def _label_address_constant(self, label_name, ptr_type=voidptr_t):
        tag = self._label_value_tags.get(label_name)
        if tag is None:
            raise SemanticError(f"unknown label '{label_name}'")
        return ir.Constant(int64_t, tag).inttoptr(ptr_type)

    def _ensure_label_block(self, label_name):
        block_name = f"label_{label_name}"
        if block_name in self._labels:
            return self._labels[block_name]
        block = self.builder.function.append_basic_block(block_name)
        self._labels[block_name] = block
        return block

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

    def _integer_promotion_ir_type(self, ir_type):
        if not isinstance(ir_type, ir.IntType):
            return ir_type
        if ir_type.width < int32_t.width:
            return int32_t
        return ir_type

    def _usual_arithmetic_conversion_ir_type(self, lhs_type, rhs_type):
        lhs_type = self._integer_promotion_ir_type(lhs_type)
        rhs_type = self._integer_promotion_ir_type(rhs_type)

        if self._is_floating_ir_type(lhs_type) or self._is_floating_ir_type(rhs_type):
            if self._is_floating_ir_type(lhs_type) and self._is_floating_ir_type(
                rhs_type
            ):
                return self._common_float_type(lhs_type, rhs_type)
            return lhs_type if self._is_floating_ir_type(lhs_type) else rhs_type

        if isinstance(lhs_type, ir.IntType) and isinstance(rhs_type, ir.IntType):
            return lhs_type if lhs_type.width >= rhs_type.width else rhs_type

        return lhs_type

    def _decay_ir_type(self, ir_type):
        if isinstance(ir_type, ir.ArrayType):
            return ir.PointerType(ir_type.element)
        return ir_type

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
        is_float32 = raw.endswith(("f", "F"))
        value = raw.rstrip("fFlL")
        if value.lower().startswith("0x") and "p" in value.lower():
            parsed = float.fromhex(value)
        else:
            parsed = float(value)
        if is_float32:
            try:
                return struct.unpack("!f", struct.pack("!f", parsed))[0]
            except OverflowError:
                return math.copysign(float("inf"), parsed)
        return parsed

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

    def _implicit_function_ir_type(self, name, call_arg_count=0):
        future_type = self._future_file_scope_function_ir_types.get(name)
        if future_type is not None:
            return future_type, future_type.return_type
        return ir.FunctionType(int32_t, [], var_arg=call_arg_count > 0), int32_t

    def _declare_implicit_function(self, name, call_arg_count=0):
        function_type, ret_ir = self._implicit_function_ir_type(
            name, call_arg_count=call_arg_count
        )
        state = self._file_scope_function_states.get(name)
        if state is None:
            self._file_scope_function_states[name] = FileScopeFunctionState(
                type_key=str(function_type),
                linkage="external",
                defined=False,
                symbol_name=name,
            )
        existing = self.module.globals.get(name)
        if existing is None:
            func = ir.Function(self.module, function_type, name=name)
        else:
            func = existing
        self.define(name, (ret_ir, func))
        return ret_ir, func

    def new_label(self, name):
        self.nlabels += 1
        return f"label_{name}_{self.nlabels}"

    @contextmanager
    def new_scope(self):
        old_scope_id = self._current_scope_id
        self._scope_id_counter += 1
        self._current_scope_id = self._scope_id_counter
        self.env = self.env.new_child()
        self._decl_ast_types = self._decl_ast_types.new_child()
        self._typedef_ast_types = self._typedef_ast_types.new_child()
        try:
            yield
        finally:
            self.env = self.env.parents
            self._decl_ast_types = self._decl_ast_types.parents
            self._typedef_ast_types = self._typedef_ast_types.parents
            self._current_scope_id = old_scope_id

    @contextmanager
    def new_function(self):
        oldfunc = self.function
        old_display_name = self._function_display_name
        old_frame_address_marker = self._frame_address_marker
        oldbuilder = self.builder
        oldenv = self.env
        old_decl_ast_types = self._decl_ast_types
        old_typedef_ast_types = self._typedef_ast_types
        oldlabels = self._labels
        old_scope_id = self._current_scope_id
        self.in_global = False
        self._scope_id_counter += 1
        self._current_scope_id = self._scope_id_counter
        self.env = self.env.new_child()
        self._decl_ast_types = self._decl_ast_types.new_child()
        self._typedef_ast_types = self._typedef_ast_types.new_child()
        self._labels = {}
        self._frame_address_marker = None
        try:
            yield
        finally:
            self.function = oldfunc
            self._function_display_name = old_display_name
            self._frame_address_marker = old_frame_address_marker
            self.builder = oldbuilder
            self.env = oldenv
            self._decl_ast_types = old_decl_ast_types
            self._typedef_ast_types = old_typedef_ast_types
            self._labels = oldlabels
            self._current_scope_id = old_scope_id
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
                if ext.name is None and isinstance(
                    ext.type, (c_ast.Struct, c_ast.Union, c_ast.Enum)
                ):
                    is_type_def = True
                elif ext.name is None and isinstance(ext.type, c_ast.TypeDecl) and isinstance(
                    ext.type.type, (c_ast.Struct, c_ast.Union)
                ):
                    is_type_def = True
            elif isinstance(ext, c_ast.Typedef):
                is_type_def = True
            if is_type_def:
                try:
                    self.codegen(ext)
                except Exception as exc:
                    _logger.debug("pass1: skipping typedef: %s", exc)
                pass1.add(i)
        remaining_exts = [ext for i, ext in enumerate(node.ext) if i not in pass1]
        self._collect_file_scope_function_ir_types(remaining_exts)
        self._collect_file_scope_object_ir_types(remaining_exts)
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
                        _logger.debug("pass2: skipping non-fatal %s: %s", ename, e)
                        continue
                    if isinstance(e, KeyError) and e.args and e.args[0] is None:
                        _logger.debug("pass2: skipping KeyError(None)")
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

    @staticmethod
    def _is_string_constant(node):
        return isinstance(node, c_ast.Constant) and getattr(node, "type", None) in (
            "string",
            "wstring",
        )

    @staticmethod
    def _is_wide_string_constant(node):
        return isinstance(node, c_ast.Constant) and (
            getattr(node, "type", None) == "wstring"
            or (
                getattr(node, "type", None) == "string"
                and str(getattr(node, "value", "")).startswith('L"')
            )
        )

    def _string_literal_content(self, raw, *, wide=False):
        if wide or raw.startswith('L"'):
            body = raw[2:-1]
        else:
            body = raw[1:-1]
        return self._process_escapes(body)

    def _string_literal_data(self, node):
        raw = node.value
        wide = self._is_wide_string_constant(node)
        content = self._string_literal_content(raw, wide=wide)
        if wide:
            return [ord(ch) for ch in content] + [0]
        return list(self._string_bytes(content + "\00"))

    def _char_constant_value(self, raw):
        if raw and raw.startswith("L'") and raw.endswith("'"):
            raw = raw[1:]
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
            raw_lower = raw.lower()
            has_unsigned_suffix = "u" in raw_lower
            has_long_suffix = "l" in raw_lower
            val_str = raw.rstrip("uUlL")
            if val_str.startswith("0x") or val_str.startswith("0X"):
                int_val = int(val_str, 16)
                is_non_decimal = True
            elif val_str.startswith("0") and len(val_str) > 1 and val_str[1:].isdigit():
                int_val = int(val_str, 8)
                is_non_decimal = True
            else:
                int_val = int(val_str)
                is_non_decimal = False

            if has_long_suffix or int_val > 0xFFFFFFFF:
                ir_type = int64_t
                is_unsigned = has_unsigned_suffix
            elif has_unsigned_suffix:
                ir_type = int32_t
                is_unsigned = True
            elif is_non_decimal and int_val > 0x7FFFFFFF:
                ir_type = int32_t
                is_unsigned = True
            elif int_val > 0x7FFFFFFF:
                ir_type = int64_t
                is_unsigned = False
            else:
                ir_type = int32_t
                is_unsigned = False

            result = ir.values.Constant(ir_type, int_val)
            if is_unsigned:
                self._tag_unsigned(result)
            return result, None
        elif node.type == "char":
            # char constant like 'a' -> i8
            is_wide_char = str(getattr(node, "value", "")).startswith("L'")
            ir_type = int32_t if is_wide_char else int8_t
            mask = 0xFFFFFFFF if is_wide_char else 0xFF
            return (
                ir.values.Constant(
                    ir_type, self._char_constant_value(node.value) & mask
                ),
                None,
            )
        elif node.type in ("string", "wstring"):
            data = self._string_literal_data(node)
            if self._is_wide_string_constant(node):
                array = ir.ArrayType(int32_t, len(data))
                tmp = ir.values.Constant(
                    array, [ir.Constant(int32_t, cp) for cp in data]
                )
                return tmp, None
            array = ir.ArrayType(ir.IntType(8), len(data))
            tmp = ir.values.Constant(array, data)
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
        is_bitfield = isinstance(lv_addr, BitFieldRef)

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
            if is_bitfield:
                target_type = lv_addr.semantic_ir_type
            elif lv_addr and hasattr(lv_addr.type, "pointee"):
                target_type = lv_addr.type.pointee
            else:
                target_type = lv.type
            if rv.type != target_type:
                rv = self._implicit_convert(rv, target_type)
            if is_bitfield:
                self._store_bitfield(rv, lv_addr)
                rv = self._load_bitfield(lv_addr)
            else:
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
            if is_bitfield:
                self._store_bitfield(addresult, lv_addr)
                return self._load_bitfield(lv_addr), lv_addr
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
            elif isinstance(lv.type, (ir.FloatType, ir.DoubleType)):
                one = ir.Constant(lv.type, 1.0)
                new_val = (
                    self.builder.fadd(lv, one, "inc")
                    if is_inc
                    else self.builder.fsub(lv, one, "dec")
                )
            else:
                one = ir.Constant(lv.type, 1)
                new_val = (
                    self.builder.add(lv, one, "inc")
                    if is_inc
                    else self.builder.sub(lv, one, "dec")
                )
                if self._is_unsigned_val(lv):
                    self._tag_unsigned(new_val)
            if isinstance(lv_addr, BitFieldRef):
                self._store_bitfield(new_val, lv_addr)
                result = lv if is_post else self._load_bitfield(lv_addr)
            else:
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
                    ap_addr = self._builtin_va_list_storage(va_args[0])
                    if ap_addr is not None:
                        aggregate_type = target_ptr_type.pointee
                        self._vaarg_counter += 1
                        if self._is_aggregate_ir_type(aggregate_type):
                            result, result_ptr = self._codegen_aggregate_va_arg(
                                ap_addr, aggregate_type
                            )
                            if result is not None:
                                return result, result_ptr
                        name = f"__pcc_va_arg_{self._vaarg_counter}"
                        placeholder = self.module.globals.get(name)
                        if placeholder is None:
                            placeholder = ir.Function(
                                self.module,
                                ir.FunctionType(
                                    aggregate_type, [ap_addr.type]
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
            if isinstance(getattr(result_ptr, "type", None), ir.PointerType) and isinstance(
                result_ptr.type.pointee, ir.ArrayType
            ):
                self._set_expr_ir_type(node, result_ptr.type.pointee)
                return result_ptr, result_ptr
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

        elif node.op in ("_Alignof", "__alignof", "__alignof__"):
            result = self._codegen_alignof(node.expr)

        elif node.op == "&&" and isinstance(node.expr, c_ast.ID):
            result = self._label_address_constant(node.expr.name, voidptr_t)
            self._set_expr_ir_type(node, voidptr_t)

        return result, result_ptr

    def _codegen_sizeof(self, expr):
        """Return sizeof as an i64 constant (always unsigned in C)."""
        if isinstance(expr, c_ast.Typename):
            ir_t = self._resolve_ast_type(expr.type)
            size = self._ir_type_size(ir_t)
        elif self._is_string_constant(expr):
            size = len(self._string_literal_data(expr))
        elif isinstance(expr, c_ast.ID):
            ir_type, _ = self.lookup(expr.name)
            size = self._ir_type_size(ir_type)
        else:
            semantic_type = self._infer_sizeof_operand_ir_type(expr)
            size = self._ir_type_size(semantic_type)
        result = ir.Constant(int64_t, size)
        return self._tag_unsigned(result)

    def _codegen_alignof(self, expr):
        """Return alignment as an i64 constant (always unsigned in C)."""
        if isinstance(expr, c_ast.Typename):
            ir_t = self._resolve_ast_type(expr.type)
        elif self._is_string_constant(expr):
            ir_t = self._get_ir_type("int")
        elif isinstance(expr, c_ast.ID):
            ir_type, _ = self.lookup(expr.name)
            ir_t = ir_type
        else:
            ir_t = self._infer_sizeof_operand_ir_type(expr)
        result = ir.Constant(int64_t, self._ir_type_align(ir_t))
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
                    if resolved in self.env:
                        return self.env[resolved][0]
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

    def _enum_value_range(self, enum_node):
        values = getattr(enum_node, "values", None)
        if values is None and getattr(enum_node, "name", None):
            return self.env.get(self._enum_tag_key(enum_node.name))
        if values is None:
            return None
        enumerators = getattr(values, "enumerators", None) or []
        if not enumerators:
            return None

        current = 0
        min_value = None
        max_value = None
        for enumerator in enumerators:
            if enumerator.value is not None:
                current = int(self._eval_const_expr(enumerator.value))
            if min_value is None or current < min_value:
                min_value = current
            if max_value is None or current > max_value:
                max_value = current
            current += 1
        return min_value, max_value

    def _bitfield_decl_is_unsigned(self, node_type, bit_width):
        if self._is_unsigned_scalar_decl_type(node_type):
            return True
        if not isinstance(node_type, c_ast.TypeDecl):
            return False
        inner = node_type.type
        if not isinstance(inner, c_ast.Enum):
            return False
        enum_range = self._enum_value_range(inner)
        if enum_range is None:
            return False
        min_value, max_value = enum_range
        if min_value < 0 or bit_width <= 0:
            return False
        return max_value >= (1 << (bit_width - 1))

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

    def _make_global_string_literal_constant(self, node, name_hint="str"):
        data = self._string_literal_data(node)
        if self._is_wide_string_constant(node):
            elem_type = int32_t
            values = [ir.Constant(int32_t, cp) for cp in data]
            arr_type = ir.ArrayType(elem_type, len(values))
            initializer = ir.Constant(arr_type, values)
        else:
            elem_type = int8_t
            arr_type = ir.ArrayType(elem_type, len(data))
            initializer = ir.Constant(arr_type, data)
        gv = ir.GlobalVariable(
            self.module, arr_type, self.module.get_unique_name(name_hint)
        )
        gv.initializer = initializer
        gv.global_constant = True
        gv.linkage = "internal"
        return gv

    def _compound_literal_ir_type(self, ast_type, init_node=None):
        if isinstance(ast_type, c_ast.ArrayDecl):
            return self._build_array_ir_type(ast_type, init_node=init_node)
        return self._resolve_ast_type(ast_type)

    def _materialize_global_compound_literal(self, ast_type, init_node):
        cache_key = id(init_node)
        cached = self._global_compound_literal_cache.get(cache_key)
        if cached is not None:
            return cached

        ir_type = self._compound_literal_ir_type(ast_type, init_node)
        gv = ir.GlobalVariable(
            self.module,
            ir_type,
            self.module.get_unique_name("compoundlit"),
        )
        gv.initializer = self._build_const_init(init_node, ir_type)
        gv.linkage = "internal"
        self._global_compound_literal_cache[cache_key] = gv
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

    def _initializer_slot_count(self, ir_type):
        if getattr(ir_type, "is_union", False):
            member_names = self._aggregate_member_names(ir_type)
            if not member_names:
                return 1
            return self._initializer_slot_count(
                self._aggregate_member_ir_type(ir_type, 0)
            )

        if isinstance(ir_type, ir.ArrayType):
            return ir_type.count * self._initializer_slot_count(ir_type.element)

        if _is_struct_ir_type(ir_type):
            if getattr(ir_type, "has_custom_layout", False):
                layouts = getattr(ir_type, "field_layouts_by_index", None) or []
                if layouts:
                    return sum(
                        self._initializer_slot_count(layout.semantic_ir_type)
                        for layout in layouts
                    )
            return sum(
                self._initializer_slot_count(member_type)
                for member_type in getattr(ir_type, "elements", ())
            )

        return 1

    def _initializer_expr_consumption(self, exprs, ir_type):
        if not exprs:
            return 0

        first_expr = exprs[0]
        if (
            isinstance(first_expr, c_ast.InitList)
            or self._is_array_string_initializer(first_expr, ir_type)
            or self._initializer_expr_matches_type(first_expr, ir_type)
        ):
            return 1

        if getattr(ir_type, "is_union", False):
            return 1

        if isinstance(ir_type, ir.ArrayType):
            consumed = 0
            remaining = list(exprs)
            for _ in range(ir_type.count):
                if not remaining:
                    break
                step = self._initializer_expr_consumption(remaining, ir_type.element)
                if step <= 0:
                    break
                consumed += step
                remaining = remaining[step:]
            return max(consumed, 1)

        if _is_struct_ir_type(ir_type):
            consumed = 0
            remaining = list(exprs)
            for member_type in self._aggregate_member_ir_types(ir_type):
                if not remaining:
                    break
                step = self._initializer_expr_consumption(remaining, member_type)
                if step <= 0:
                    break
                consumed += step
                remaining = remaining[step:]
            return max(consumed, 1)

        return 1

    def _is_char_array_string_initializer(self, init_node, ir_type):
        return (
            self._is_string_constant(init_node)
            and not self._is_wide_string_constant(init_node)
            and isinstance(ir_type, ir.ArrayType)
            and isinstance(ir_type.element, ir.IntType)
            and ir_type.element.width == 8
        )

    def _is_wchar_array_string_initializer(self, init_node, ir_type):
        return (
            self._is_wide_string_constant(init_node)
            and isinstance(ir_type, ir.ArrayType)
            and isinstance(ir_type.element, ir.IntType)
            and ir_type.element.width == 32
        )

    def _is_array_string_initializer(self, init_node, ir_type):
        return self._is_char_array_string_initializer(
            init_node, ir_type
        ) or self._is_wchar_array_string_initializer(init_node, ir_type)

    def _normalize_array_init_list(self, init_node, elem_ir_type):
        if not isinstance(init_node, c_ast.InitList):
            return init_node

        exprs = list(getattr(init_node, "exprs", None) or [])
        if not exprs:
            return init_node

        if any(
            isinstance(expr, (c_ast.InitList, c_ast.NamedInitializer))
            or self._is_array_string_initializer(expr, elem_ir_type)
            for expr in exprs
        ):
            return init_node

        slots = self._initializer_slot_count(elem_ir_type)
        if slots <= 1:
            return init_node

        grouped_exprs = []
        cursor = 0
        while cursor < len(exprs):
            consumed = self._initializer_expr_consumption(exprs[cursor:], elem_ir_type)
            if consumed <= 1:
                grouped_exprs.append(exprs[cursor])
                cursor += 1
                continue
            grouped_exprs.append(
                c_ast.InitList(exprs[cursor : cursor + consumed], init_node.coord)
            )
            cursor += consumed
        return c_ast.InitList(grouped_exprs, init_node.coord)

    def _designator_index_bounds(self, designator):
        if isinstance(designator, c_ast.ID):
            return None
        if isinstance(designator, c_ast.RangeDesignator):
            try:
                start = int(self._eval_const_expr(designator.start))
                end = int(self._eval_const_expr(designator.end))
            except Exception:
                return None
            if end < start:
                start, end = end, start
            return start, end
        try:
            index = int(self._eval_const_expr(designator))
        except Exception:
            return None
        return index, index

    def _ordered_array_init_exprs(self, init_node, ir_type):
        exprs = list(getattr(init_node, "exprs", None) or [])
        if not exprs:
            return exprs

        if not any(isinstance(expr, c_ast.NamedInitializer) for expr in exprs):
            return exprs

        ordered = [None] * ir_type.count
        cursor = 0

        for expr in exprs:
            target_expr = expr
            if isinstance(expr, c_ast.NamedInitializer):
                designators = getattr(expr, "name", None) or []
                if not designators:
                    continue
                bounds = self._designator_index_bounds(designators[0])
                if bounds is None:
                    continue
                start, end = bounds
                if start < 0:
                    continue
                if end >= len(ordered):
                    ordered.extend([None] * (end + 1 - len(ordered)))
                cursor = start
                if len(designators) > 1:
                    target_expr = c_ast.InitList(
                        [
                            c_ast.NamedInitializer(
                                designators[1:],
                                expr.expr,
                                expr.coord,
                            )
                        ],
                        expr.coord,
                    )
                    normalized = self._normalize_initializer_for_type(
                        target_expr, ir_type.element
                    )
                else:
                    target_expr = expr.expr
                    normalized = self._normalize_initializer_for_type(
                        target_expr, ir_type.element
                    )
                for index in range(start, end + 1):
                    if len(designators) == 1:
                        ordered[index] = normalized
                    elif ordered[index] is None:
                        ordered[index] = normalized
                    else:
                        ordered[index] = self._merge_initializer_nodes(
                            ordered[index],
                            normalized,
                            expr.coord,
                        )
                cursor = end + 1
                continue

            while cursor < len(ordered) and ordered[cursor] is not None:
                cursor += 1
            if cursor >= len(ordered):
                break
            ordered[cursor] = self._normalize_initializer_for_type(
                target_expr, ir_type.element
            )
            cursor += 1

        return ordered

    def _merge_initializer_nodes(self, existing, new_expr, coord=None):
        if existing is None:
            return new_expr
        if new_expr is None:
            return existing

        merged_exprs = []
        if isinstance(existing, c_ast.InitList):
            merged_exprs.extend(list(existing.exprs or ()))
        else:
            merged_exprs.append(existing)

        if isinstance(new_expr, c_ast.InitList):
            merged_exprs.extend(list(new_expr.exprs or ()))
        else:
            merged_exprs.append(new_expr)

        merged_coord = (
            coord
            or getattr(existing, "coord", None)
            or getattr(new_expr, "coord", None)
        )
        return c_ast.InitList(merged_exprs, merged_coord)

    def _aggregate_member_ir_types(self, ir_type):
        if getattr(ir_type, "is_union", False):
            if getattr(ir_type, "elements", None):
                return [self._aggregate_member_ir_type(ir_type, 0)]
            return []

        if getattr(ir_type, "has_custom_layout", False):
            layouts = getattr(ir_type, "field_layouts_by_index", None) or []
            if layouts:
                return [layout.semantic_ir_type for layout in layouts]

        if _is_struct_ir_type(ir_type):
            return list(getattr(ir_type, "elements", ()) or [])

        return []

    def _normalize_struct_init_list(self, init_node, ir_type):
        if not isinstance(init_node, c_ast.InitList):
            return init_node

        exprs = list(getattr(init_node, "exprs", None) or [])
        if not exprs:
            return init_node

        if any(isinstance(expr, c_ast.NamedInitializer) for expr in exprs):
            return init_node

        member_types = self._aggregate_member_ir_types(ir_type)
        if not member_types:
            return init_node

        normalized = []
        cursor = 0
        for member_type in member_types:
            if cursor >= len(exprs):
                break

            expr = exprs[cursor]
            if isinstance(expr, c_ast.InitList):
                normalized.append(self._normalize_initializer_for_type(expr, member_type))
                cursor += 1
                continue

            if self._is_array_string_initializer(expr, member_type):
                normalized.append(expr)
                cursor += 1
                continue

            if self._initializer_expr_matches_type(expr, member_type):
                normalized.append(expr)
                cursor += 1
                continue

            consumed = self._initializer_expr_consumption(exprs[cursor:], member_type)
            if consumed <= 1:
                normalized.append(expr)
                cursor += 1
                continue

            member_init = c_ast.InitList(
                exprs[cursor : cursor + consumed], init_node.coord
            )
            normalized.append(self._normalize_initializer_for_type(member_init, member_type))
            cursor += consumed

        if cursor < len(exprs):
            normalized.extend(exprs[cursor:])

        return c_ast.InitList(normalized, init_node.coord)

    def _normalize_union_init_list(self, init_node, ir_type):
        if not isinstance(init_node, c_ast.InitList):
            return init_node

        exprs = list(getattr(init_node, "exprs", None) or [])
        if not exprs:
            return init_node

        field_index, field_type, member_init = self._select_union_initializer(
            init_node, ir_type
        )
        if field_index is None or member_init is None:
            return init_node

        normalized_member = self._normalize_initializer_for_type(member_init, field_type)
        if normalized_member is member_init:
            return init_node

        first_expr = exprs[0]
        if isinstance(first_expr, c_ast.NamedInitializer):
            rewritten = c_ast.NamedInitializer(
                first_expr.name,
                normalized_member,
                first_expr.coord,
            )
            return c_ast.InitList([rewritten] + exprs[1:], init_node.coord)

        return normalized_member

    def _initializer_expr_matches_type(self, expr, ir_type):
        if expr is None:
            return False
        try:
            expr_ir_type = self._infer_sizeof_operand_ir_type(expr)
        except Exception:
            return False

        if str(expr_ir_type) == str(ir_type):
            return True

        if isinstance(expr_ir_type, ir.ArrayType) and isinstance(ir_type, ir.ArrayType):
            return self._are_compatible_object_ir_types(expr_ir_type, ir_type)

        return False

    def _normalize_initializer_for_type(self, init_node, ir_type):
        if self._is_array_string_initializer(init_node, ir_type):
            return init_node

        if isinstance(init_node, c_ast.CompoundLiteral):
            if self._initializer_expr_matches_type(init_node, ir_type):
                return init_node
            init_node = init_node.init

        if not isinstance(init_node, c_ast.InitList):
            if (
                isinstance(ir_type, ir.ArrayType)
                and not self._initializer_expr_matches_type(init_node, ir_type)
            ):
                return c_ast.InitList([init_node], getattr(init_node, "coord", None))
            if (
                (_is_struct_ir_type(ir_type) or getattr(ir_type, "is_union", False))
                and not self._initializer_expr_matches_type(init_node, ir_type)
            ):
                return c_ast.InitList([init_node], getattr(init_node, "coord", None))
            return init_node

        exprs = list(getattr(init_node, "exprs", None) or [])
        if (
            len(exprs) == 1
            and self._is_array_string_initializer(exprs[0], ir_type)
        ):
            return exprs[0]

        if any(isinstance(expr, c_ast.NamedInitializer) for expr in exprs):
            if (
                isinstance(ir_type, ir.ArrayType)
                or getattr(ir_type, "is_union", False)
                or _is_struct_ir_type(ir_type)
            ):
                return init_node

        if isinstance(ir_type, ir.ArrayType):
            grouped = self._normalize_array_init_list(init_node, ir_type.element)
            exprs = list(getattr(grouped, "exprs", None) or [])
            normalized = []
            for expr in exprs:
                normalized.append(self._normalize_initializer_for_type(expr, ir_type.element))
            return c_ast.InitList(normalized, grouped.coord)

        if getattr(ir_type, "is_union", False):
            return self._normalize_union_init_list(init_node, ir_type)

        if _is_struct_ir_type(ir_type):
            grouped = self._normalize_struct_init_list(init_node, ir_type)
            exprs = list(getattr(grouped, "exprs", None) or [])
            member_types = self._aggregate_member_ir_types(ir_type)
            normalized = []
            for index, expr in enumerate(exprs):
                if index < len(member_types):
                    normalized.append(
                        self._normalize_initializer_for_type(expr, member_types[index])
                    )
                else:
                    normalized.append(expr)
            return c_ast.InitList(normalized, grouped.coord)

        return init_node

    def _struct_field_names(self, ir_type):
        member_names = list(getattr(ir_type, "members", ()) or [])
        if member_names:
            return member_names
        member_types = getattr(ir_type, "member_types", None)
        if isinstance(member_types, dict):
            return list(member_types.keys())
        return []

    def _ordered_struct_init_exprs(self, init_node, ir_type):
        exprs = list(getattr(init_node, "exprs", None) or [])
        field_names = self._struct_field_names(ir_type)
        if not exprs or not field_names:
            return exprs
        if not any(isinstance(expr, c_ast.NamedInitializer) for expr in exprs):
            return exprs

        ordered = [None] * len(field_names)
        cursor = 0
        index_by_name = {name: i for i, name in enumerate(field_names)}

        for expr in exprs:
            if isinstance(expr, c_ast.NamedInitializer):
                designators = getattr(expr, "name", None) or []
                if designators and isinstance(designators[0], c_ast.ID):
                    target = index_by_name.get(designators[0].name)
                    if target is not None:
                        if len(designators) == 1:
                            ordered[target] = expr.expr
                        else:
                            target_expr = c_ast.InitList(
                                [
                                    c_ast.NamedInitializer(
                                        designators[1:],
                                        expr.expr,
                                        expr.coord,
                                    )
                                ],
                                expr.coord,
                            )
                            if ordered[target] is None:
                                ordered[target] = target_expr
                            else:
                                ordered[target] = self._merge_initializer_nodes(
                                    ordered[target],
                                    target_expr,
                                    expr.coord,
                                )
                        cursor = target + 1
                continue

            while cursor < len(ordered) and ordered[cursor] is not None:
                cursor += 1
            if cursor >= len(ordered):
                break
            ordered[cursor] = expr
            cursor += 1

        return ordered

    def _build_const_address(self, init_node):
        if isinstance(init_node, c_ast.ID):
            try:
                _, sym = self.lookup(init_node.name)
            except Exception:
                return None
            if isinstance(sym, (ir.Function, ir.GlobalVariable)):
                return sym
            return None

        if isinstance(init_node, c_ast.CompoundLiteral):
            return self._materialize_global_compound_literal(
                init_node.type.type,
                init_node.init,
            )

        if isinstance(init_node, c_ast.Cast):
            return self._build_const_address(init_node.expr)

        if isinstance(init_node, c_ast.ArrayRef):
            base_addr = self._build_const_address(init_node.name)
            if base_addr is None or not isinstance(
                getattr(base_addr, "type", None), ir.PointerType
            ):
                return None
            try:
                idx_val = int(self._eval_const_expr(init_node.subscript))
            except Exception:
                return None
            idx0 = ir.Constant(ir.IntType(32), 0)
            idx = ir.Constant(ir.IntType(32), idx_val)
            pointee = base_addr.type.pointee
            try:
                if isinstance(pointee, ir.ArrayType):
                    return base_addr.gep([idx0, idx])
                return base_addr.gep([idx])
            except Exception:
                return None

        if isinstance(init_node, c_ast.BinaryOp) and init_node.op in ("+", "-"):
            base_addr = self._build_const_address(init_node.left)
            offset_node = init_node.right
            offset_sign = 1
            if base_addr is None and init_node.op == "+":
                base_addr = self._build_const_address(init_node.right)
                offset_node = init_node.left
            elif base_addr is None:
                return None
            if base_addr is None or not isinstance(
                getattr(base_addr, "type", None), ir.PointerType
            ):
                return None
            try:
                idx_val = int(self._eval_const_expr(offset_node))
            except Exception:
                return None
            if init_node.op == "-":
                idx_val = -idx_val
            idx0 = ir.Constant(ir.IntType(32), 0)
            idx = ir.Constant(ir.IntType(32), idx_val)
            pointee = base_addr.type.pointee
            try:
                if isinstance(pointee, ir.ArrayType):
                    return base_addr.gep([idx0, idx])
                return base_addr.gep([idx])
            except Exception:
                return None

        if isinstance(init_node, c_ast.StructRef):
            base_addr = self._build_const_address(init_node.name)
            if base_addr is None or not isinstance(
                getattr(base_addr, "type", None), ir.PointerType
            ):
                return None
            if (
                init_node.type == "->"
                and isinstance(base_addr.type.pointee, ir.ArrayType)
            ):
                idx0 = ir.Constant(ir.IntType(32), 0)
                try:
                    base_addr = base_addr.gep([idx0, idx0])
                except Exception:
                    return None
            aggregate_type = base_addr.type.pointee
            try:
                offset, field_type = self._get_aggregate_field_info(
                    aggregate_type, init_node.field.name
                )
            except Exception:
                return None

            if (
                hasattr(aggregate_type, "members")
                and init_node.field.name in aggregate_type.members
                and not getattr(aggregate_type, "has_custom_layout", False)
                and not getattr(aggregate_type, "is_union", False)
            ):
                idx0 = ir.Constant(ir.IntType(32), 0)
                field_index = aggregate_type.members.index(init_node.field.name)
                try:
                    return base_addr.gep(
                        [idx0, ir.Constant(ir.IntType(32), field_index)]
                    )
                except Exception:
                    return None

            try:
                byte_base = base_addr.bitcast(ir.PointerType(int8_t))
                byte_addr = byte_base.gep([ir.Constant(ir.IntType(32), offset)])
                return byte_addr.bitcast(ir.PointerType(field_type))
            except Exception:
                return None

        return None

    def _build_pointer_const(self, init_node, ir_type):
        if isinstance(init_node, c_ast.InitList):
            if init_node.exprs:
                return self._build_pointer_const(init_node.exprs[0], ir_type)
            return ir.Constant(ir_type, None)
        if isinstance(init_node, c_ast.Cast):
            return self._build_pointer_const(init_node.expr, ir_type)
        if self._is_string_constant(init_node):
            gv = self._make_global_string_literal_constant(init_node)
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
            and init_node.op == "&&"
            and isinstance(init_node.expr, c_ast.ID)
        ):
            return self._label_address_constant(init_node.expr.name, ir_type)
        if isinstance(init_node, c_ast.ArrayRef):
            addr = self._build_const_address(init_node)
            if addr is not None:
                if addr.type == ir_type:
                    return addr
                if isinstance(addr.type, ir.PointerType):
                    return addr.bitcast(ir_type)
        if (
            isinstance(init_node, c_ast.UnaryOp)
            and init_node.op == "&"
        ):
            addr = self._build_const_address(init_node.expr)
            if addr is not None:
                if addr.type == ir_type:
                    return addr
                if isinstance(addr.type, ir.PointerType):
                    return addr.bitcast(ir_type)
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

    def _raw_bytes_to_unsigned_int(self, byte_values):
        result = 0
        width = len(byte_values)
        for i, byte_val in enumerate(byte_values):
            shift_bits = 8 * (i if self._is_little_endian() else (width - 1 - i))
            raw = getattr(byte_val, "constant", 0)
            if not isinstance(raw, int):
                raw = 0
            result |= (raw & 0xFF) << shift_bits
        return result

    def _const_init_bytes(self, init_node, ir_type):
        if isinstance(init_node, c_ast.CompoundLiteral):
            init_node = init_node.init
        size = self._ir_type_size(ir_type)
        if init_node is None:
            return self._zero_bytes(size)

        if getattr(ir_type, "is_union", False):
            init_node = self._normalize_initializer_for_type(init_node, ir_type)
            raw = self._zero_bytes(size)
            field_index, member_type, member_init = self._select_union_initializer(
                init_node, ir_type
            )
            if field_index is None:
                return raw

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
            if self._is_array_string_initializer(init_node, ir_type):
                data = self._string_literal_data(init_node)
                if len(data) < ir_type.count:
                    data.extend([0] * (ir_type.count - len(data)))
                else:
                    data = data[: ir_type.count]
                return [ir.Constant(ir_type.element, v) for v in data]

            if isinstance(init_node, c_ast.InitList):
                init_node = self._normalize_initializer_for_type(init_node, ir_type)
                values = []
                ordered_exprs = self._ordered_array_init_exprs(init_node, ir_type)
                for i in range(ir_type.count):
                    expr = ordered_exprs[i] if i < len(ordered_exprs) else None
                    values.extend(self._const_init_bytes(expr, ir_type.element))
                return values

            return self._zero_bytes(size)

        if _is_struct_ir_type(ir_type):
            if getattr(ir_type, "has_custom_layout", False):
                raw = self._zero_bytes(size)
                if not isinstance(init_node, c_ast.InitList):
                    return raw
                init_node = self._normalize_initializer_for_type(init_node, ir_type)

                exprs = self._ordered_struct_init_exprs(init_node, ir_type)
                for i, field_name in enumerate(getattr(ir_type, "members", ())):
                    if i >= len(exprs):
                        break
                    expr = exprs[i]
                    layout = ir_type.field_layouts.get(field_name)
                    if layout is None:
                        continue

                    if layout.is_bitfield:
                        scalar_node = self._scalar_init_node(expr)
                        if scalar_node is None:
                            continue
                        try:
                            field_value = int(self._eval_const_expr(scalar_node))
                        except Exception:
                            continue
                        storage_size = self._ir_type_size(layout.storage_ir_type)
                        start = layout.storage_byte_offset
                        current = self._raw_bytes_to_unsigned_int(
                            raw[start : start + storage_size]
                        )
                        field_mask = self._bitfield_mask(layout.bit_width)
                        clear_mask = ((1 << (storage_size * 8)) - 1) ^ (
                            field_mask << layout.bit_offset
                        )
                        current = (current & clear_mask) | (
                            (field_value & field_mask) << layout.bit_offset
                        )
                        raw[start : start + storage_size] = self._const_int_to_bytes(
                            current, storage_size
                        )
                        continue

                    field_size = self._ir_type_size(layout.semantic_ir_type)
                    field_bytes = self._const_init_bytes(expr, layout.semantic_ir_type)
                    start = layout.byte_offset
                    raw[start : start + field_size] = field_bytes[:field_size]
                return raw

            raw = self._zero_bytes(size)
            if not isinstance(init_node, c_ast.InitList):
                return raw
            init_node = self._normalize_initializer_for_type(init_node, ir_type)

            exprs = self._ordered_struct_init_exprs(init_node, ir_type)
            offset = 0
            for i, member_type in enumerate(ir_type.elements):
                align = self._ir_type_align(member_type)
                offset = (offset + align - 1) & ~(align - 1)
                expr = exprs[i] if i < len(exprs) else None
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

        if isinstance(init_node, c_ast.CompoundLiteral):
            init_node = init_node.init

        if getattr(ir_type, "is_union", False):
            try:
                init_node = self._normalize_initializer_for_type(init_node, ir_type)
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
            if self._is_array_string_initializer(init_node, ir_type):
                data = self._string_literal_data(init_node)
                if len(data) < ir_type.count:
                    data.extend([0] * (ir_type.count - len(data)))
                else:
                    data = data[: ir_type.count]
                try:
                    if self._is_wide_string_constant(init_node):
                        return ir.Constant(
                            ir_type, [ir.Constant(ir_type.element, v) for v in data]
                        )
                    return ir.Constant(ir_type, data)
                except Exception:
                    return self._zero_initializer(ir_type)

            if isinstance(init_node, c_ast.InitList):
                init_node = self._normalize_initializer_for_type(init_node, ir_type)
                values = []
                ordered_exprs = self._ordered_array_init_exprs(init_node, ir_type)
                for i in range(ir_type.count):
                    expr = ordered_exprs[i] if i < len(ordered_exprs) else None
                    values.append(self._build_const_init(expr, ir_type.element))
                try:
                    return ir.Constant(ir_type, values)
                except Exception:
                    return self._zero_initializer(ir_type)

            return self._zero_initializer(ir_type)

        if _is_struct_ir_type(ir_type):
            if getattr(ir_type, "has_custom_layout", False):
                try:
                    if isinstance(init_node, c_ast.InitList):
                        init_node = self._normalize_initializer_for_type(
                            init_node, ir_type
                        )
                    storage_segments = getattr(ir_type, "storage_segments", None)
                    if storage_segments is None:
                        raw = self._const_init_bytes(init_node, ir_type)
                        values = []
                        offset = 0
                        for member_type in ir_type.elements:
                            field_size = self._ir_type_size(member_type)
                            field_bytes = raw[offset : offset + field_size]
                            if isinstance(member_type, ir.IntType):
                                values.append(
                                    self._bytes_to_int_constant(
                                        field_bytes, member_type
                                    )
                                )
                            elif (
                                isinstance(member_type, ir.ArrayType)
                                and isinstance(member_type.element, ir.IntType)
                                and member_type.element.width == 8
                            ):
                                values.append(ir.Constant(member_type, field_bytes))
                            else:
                                values.append(self._zero_initializer(member_type))
                            offset += field_size
                        return ir.Constant(ir_type, values)
                    values = []
                    exprs = (
                        self._ordered_struct_init_exprs(init_node, ir_type)
                        if isinstance(init_node, c_ast.InitList)
                        else []
                    )
                    field_layouts_by_index = getattr(
                        ir_type, "field_layouts_by_index", None
                    ) or []
                    for segment in storage_segments:
                        member_type = segment.ir_type
                        if segment.kind == "padding":
                            values.append(self._zero_initializer(member_type))
                            continue
                        if segment.kind == "field":
                            expr = None
                            if segment.field_index is not None and segment.field_index < len(exprs):
                                expr = exprs[segment.field_index]
                            values.append(self._build_const_init(expr, member_type))
                            continue

                        storage_size = self._ir_type_size(member_type)
                        current = 0
                        for field_index in segment.bitfield_indices:
                            if field_index >= len(exprs):
                                continue
                            expr = exprs[field_index]
                            if expr is None:
                                continue
                            scalar_node = self._scalar_init_node(expr)
                            if scalar_node is None:
                                continue
                            try:
                                field_value = int(self._eval_const_expr(scalar_node))
                            except Exception:
                                continue
                            layout = field_layouts_by_index[field_index]
                            field_mask = self._bitfield_mask(layout.bit_width)
                            clear_mask = ((1 << (storage_size * 8)) - 1) ^ (
                                field_mask << layout.bit_offset
                            )
                            current = (current & clear_mask) | (
                                (field_value & field_mask) << layout.bit_offset
                            )
                        values.append(
                            self._bytes_to_int_constant(
                                self._const_int_to_bytes(current, storage_size),
                                member_type,
                            )
                        )
                    return ir.Constant(ir_type, values)
                except Exception:
                    return self._zero_initializer(ir_type)
            if isinstance(init_node, c_ast.InitList):
                init_node = self._normalize_initializer_for_type(init_node, ir_type)
                exprs = self._ordered_struct_init_exprs(init_node, ir_type)
                values = []
                for i, member_type in enumerate(ir_type.elements):
                    expr = exprs[i] if i < len(exprs) else None
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
            if isinstance(expr, c_ast.InitList) and isinstance(elem_ir_type, ir.ArrayType):
                self._init_array(base_addr, expr, elem_ir_type.element, idx)
                continue
            elem_ptr = self.builder.gep(base_addr, idx, inbounds=True)
            self._init_runtime_value(elem_ptr, elem_ir_type, expr)

    def _init_runtime_value(self, dest_ptr, target_type, init_node):
        if dest_ptr is None or init_node is None:
            return

        if isinstance(init_node, c_ast.CompoundLiteral):
            init_node = init_node.init

        if isinstance(target_type, ir.ArrayType):
            if self._is_array_string_initializer(init_node, target_type):
                data = self._string_literal_data(init_node)
                idx0 = ir.Constant(ir.IntType(32), 0)
                for i, value in enumerate(data[: target_type.count]):
                    elem_ptr = self.builder.gep(
                        dest_ptr,
                        [idx0, ir.Constant(ir.IntType(32), i)],
                        inbounds=True,
                    )
                    self.builder.store(ir.Constant(target_type.element, value), elem_ptr)
                return
            if isinstance(init_node, c_ast.InitList):
                init_node = self._normalize_initializer_for_type(init_node, target_type)
                ordered_exprs = self._ordered_array_init_exprs(init_node, target_type)
                self._init_array(
                    dest_ptr,
                    c_ast.InitList(ordered_exprs, init_node.coord),
                    target_type.element,
                    [ir.Constant(ir.IntType(32), 0)],
                )
                return

        if getattr(target_type, "is_union", False) or _is_struct_ir_type(target_type):
            if isinstance(init_node, c_ast.InitList):
                self._init_runtime_aggregate(dest_ptr, init_node, target_type)
                return
            init_val, _ = self.codegen(init_node)
            if init_val is not None:
                if init_val.type != target_type:
                    init_val = self._implicit_convert(init_val, target_type)
                self._safe_store(init_val, dest_ptr)
            return

        scalar_node = self._scalar_init_node(init_node)
        if scalar_node is None:
            return
        init_val, _ = self.codegen(scalar_node)
        if init_val is None:
            return
        if init_val.type != target_type:
            init_val = self._implicit_convert(init_val, target_type)
        self._safe_store(init_val, dest_ptr)

    def _init_runtime_aggregate(self, base_addr, init_node, ir_type):
        init_node = self._normalize_initializer_for_type(init_node, ir_type)
        exprs = list(getattr(init_node, "exprs", None) or [])
        if getattr(ir_type, "is_union", False):
            field_index, field_type, member_init = self._select_union_initializer(
                init_node, ir_type
            )
            if field_index is None or member_init is None:
                return
            field_ptr = self.builder.bitcast(
                base_addr,
                ir.PointerType(field_type),
                name="unioninit",
            )
            self._init_runtime_value(field_ptr, field_type, member_init)
            return

        if getattr(ir_type, "has_custom_layout", False):
            exprs = self._ordered_struct_init_exprs(init_node, ir_type)
            for i, field_name in enumerate(getattr(ir_type, "members", ())):
                if i >= len(exprs):
                    break
                expr = exprs[i]
                layout = ir_type.field_layouts.get(field_name)
                if layout is None:
                    continue
                if layout.is_bitfield:
                    scalar_node = self._scalar_init_node(expr)
                    if scalar_node is None:
                        continue
                    field_val, _ = self.codegen(scalar_node)
                    if field_val is None:
                        continue
                    if field_val.type != layout.semantic_ir_type:
                        field_val = self._implicit_convert(
                            field_val,
                            layout.semantic_ir_type,
                        )
                    ref = BitFieldRef(
                        container_ptr=self._byte_offset_ptr(
                            base_addr,
                            layout.storage_byte_offset,
                            ir.PointerType(layout.storage_ir_type),
                            name="bitfieldptr",
                        ),
                        storage_ir_type=layout.storage_ir_type,
                        bit_offset=layout.bit_offset,
                        bit_width=layout.bit_width,
                        semantic_ir_type=layout.semantic_ir_type,
                        is_unsigned=layout.is_unsigned,
                    )
                    self._store_bitfield(field_val, ref)
                    continue

                field_ptr = self._byte_offset_ptr(
                    base_addr,
                    layout.byte_offset,
                    ir.PointerType(layout.semantic_ir_type),
                    name="fieldptr",
                )
                self._init_runtime_value(field_ptr, layout.semantic_ir_type, expr)
            return

        exprs = self._ordered_struct_init_exprs(init_node, ir_type)
        for i, field_type in enumerate(ir_type.elements):
            if i >= len(exprs):
                break
            expr = exprs[i]
            field_addr = self.builder.gep(
                base_addr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
                inbounds=True,
            )
            semantic_field_type = self._refine_member_ir_type(ir_type, i, field_type)
            typed_field_addr = field_addr
            target_ptr_type = ir.PointerType(semantic_field_type)
            if field_addr.type != target_ptr_type:
                try:
                    typed_field_addr = self.builder.bitcast(
                        field_addr,
                        target_ptr_type,
                    )
                except Exception:
                    typed_field_addr = field_addr
            self._init_runtime_value(typed_field_addr, semantic_field_type, expr)

    def _select_union_initializer(self, init_node, ir_type):
        member_names = self._aggregate_member_names(ir_type)
        if not member_names:
            return None, None, None

        field_index = 0
        field_type = self._refine_member_ir_type(
            ir_type,
            field_index,
            self._aggregate_member_ir_type(ir_type, field_index),
        )
        member_init = init_node

        if isinstance(init_node, c_ast.InitList):
            exprs = init_node.exprs or []
            if not exprs:
                return field_index, field_type, None

            first_expr = exprs[0]
            if isinstance(first_expr, c_ast.NamedInitializer):
                designators = getattr(first_expr, "name", None) or []
                if len(designators) == 1 and isinstance(designators[0], c_ast.ID):
                    candidate = designators[0].name
                    named_member_indices = getattr(
                        ir_type, "named_member_indices", None
                    ) or {}
                    if candidate in named_member_indices:
                        field_index = named_member_indices[candidate]
                        field_type = self._refine_member_ir_type(
                            ir_type,
                            field_index,
                            self._aggregate_member_ir_type(ir_type, field_index),
                        )
                        return field_index, field_type, first_expr.expr
                first_expr = first_expr.expr

            if isinstance(field_type, (ir.ArrayType, ir.IdentifiedStructType, ir.LiteralStructType)):
                member_init = (
                    first_expr
                    if len(exprs) == 1 and isinstance(first_expr, c_ast.InitList)
                    else init_node
                )
            else:
                member_init = first_expr

        return field_index, field_type, member_init

    def _build_array_ir_type(self, array_decl, init_node=None):
        dims = []
        node = array_decl
        top_elem_ir_type = None
        try:
            if isinstance(node.type, c_ast.ArrayDecl):
                top_elem_ir_type = self._build_array_ir_type(node.type)
            else:
                top_elem_ir_type = self._resolve_ast_type(node.type)
        except Exception:
            top_elem_ir_type = None
        inferred_top_dim = self._infer_array_count_from_initializer(
            init_node, top_elem_ir_type
        )
        is_top_level = True
        while isinstance(node, c_ast.ArrayDecl):
            dim = self._eval_dim(node.dim) if node.dim else 0
            if dim == 0 and is_top_level and inferred_top_dim is not None:
                dim = inferred_top_dim
            dims.append(dim)
            node = node.type
            is_top_level = False
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
        node_type = param.type if hasattr(param, "type") else param
        if isinstance(node_type, c_ast.ArrayDecl):
            inner = node_type.type
            if isinstance(inner, c_ast.ArrayDecl):
                return ir.PointerType(self._build_array_ir_type(inner))
            elem_ir_type = self._resolve_ast_type(inner)
            if isinstance(elem_ir_type, ir.VoidType):
                elem_ir_type = int8_t
            return ir.PointerType(elem_ir_type)
        t = self._resolve_ast_type(node_type)
        if isinstance(t, ir.ArrayType):
            return ir.PointerType(t.element)
        if isinstance(t, ir.FunctionType):
            return t.as_pointer()
        if isinstance(t, ir.VoidType):
            return None  # void params mean "no params" in C
        return t

    def _emit_vla_param_bound_side_effects(self, node_type):
        current = node_type
        while isinstance(current, c_ast.ArrayDecl):
            dim = current.dim
            if dim is not None and not isinstance(dim, c_ast.Constant):
                self.codegen(dim)
            current = current.type

    def _resolve_ast_type(self, node_type):
        """Recursively resolve an AST type to IR type, with typedef support."""
        if isinstance(node_type, c_ast.Struct):
            return self.codegen_Struct(node_type)
        elif isinstance(node_type, c_ast.Union):
            return self.codegen_Union(node_type)
        elif isinstance(node_type, c_ast.Enum):
            self.codegen_Enum(node_type)
            return int32_t
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
            elif isinstance(node_type.type, c_ast.Enum):
                self.codegen_Enum(node_type.type)
                return int32_t
            return int64_t
        elif isinstance(node_type, c_ast.ArrayDecl):
            return voidptr_t
        elif isinstance(node_type, c_ast.FuncDecl):
            func_type, _ = self._build_function_ir_type(node_type)
            return func_type
        return int64_t

    def _eval_dim(self, dim_node):
        """Evaluate array dimension (may be a constant or expression)."""
        if dim_node is None:
            return 0
        if isinstance(dim_node, c_ast.Constant):
            v = dim_node.value.rstrip("uUlL")
            return int(v, 0)  # handles hex/octal/decimal
        return self._eval_const_expr(dim_node)

    def _infer_array_count_from_initializer(self, init_node, elem_ir_type=None):
        if init_node is None:
            return None
        if isinstance(init_node, c_ast.InitList):
            exprs = list(getattr(init_node, "exprs", None) or [])
            if any(isinstance(expr, c_ast.NamedInitializer) for expr in exprs):
                cursor = 0
                max_index = 0
                for expr in exprs:
                    if isinstance(expr, c_ast.NamedInitializer):
                        designators = getattr(expr, "name", None) or []
                        if designators:
                            bounds = self._designator_index_bounds(designators[0])
                            if bounds is not None:
                                start, end = bounds
                                if start >= 0:
                                    cursor = start
                                    max_index = max(max_index, end + 1)
                                    cursor = end + 1
                                    continue
                    max_index = max(max_index, cursor + 1)
                    cursor += 1
                return max_index
            if elem_ir_type is not None:
                init_node = self._normalize_initializer_for_type(
                    init_node, ir.ArrayType(elem_ir_type, 0)
                )
            return len(init_node.exprs)
        if (
            self._is_string_constant(init_node)
        ):
            return len(self._string_literal_data(init_node))
        return None

    def _build_func_ptr_type(self, func_decl_node):
        """Build an IR function pointer type from a FuncDecl AST node."""
        func_type, _ = self._build_function_ir_type(func_decl_node)
        return func_type.as_pointer()

    def _build_function_ir_type(self, func_decl_node):
        """Build an IR function type from a FuncDecl AST node."""
        ret_ir, _ = self.codegen(func_decl_node)
        param_types = []
        is_var_arg = False
        if func_decl_node.args:
            for param in func_decl_node.args.params:
                if isinstance(param, c_ast.EllipsisParam):
                    is_var_arg = True
                    continue
                if isinstance(param, (c_ast.Typename, c_ast.Decl)):
                    t = self._resolve_param_type(param)
                    if t is not None:
                        param_types.append(t)
        if isinstance(ret_ir, ir.VoidType):
            ret_ir = ir.VoidType()
        return ir.FunctionType(ret_ir, param_types, var_arg=is_var_arg), ret_ir

    def _build_future_funcdef_ir_type(self, func_def_node):
        """Build the most specific callable type we can infer for a FuncDef."""
        ret_ir, _ = self.codegen(func_def_node.decl.type)
        param_infos, is_var_arg = self._funcdef_param_infos(func_def_node)
        arg_types = [param_type for _name, param_type, _decl in param_infos]
        if isinstance(ret_ir, ir.VoidType):
            ret_ir = ir.VoidType()
        return ir.FunctionType(ret_ir, arg_types, var_arg=is_var_arg), ret_ir

    def _funcdef_param_infos(self, node):
        infos = []
        is_var_arg = False
        if not node.decl.type.args:
            return infos, is_var_arg

        knr_param_decls = {
            decl.name: decl for decl in (getattr(node, "param_decls", None) or [])
        }

        for index, param in enumerate(node.decl.type.args.params):
            if isinstance(param, c_ast.EllipsisParam):
                is_var_arg = True
                continue

            decl = param
            if isinstance(param, c_ast.ID):
                decl = knr_param_decls.get(param.name)
                if decl is None:
                    infos.append((param.name, int32_t, None))
                    continue

            t = self._resolve_param_type(decl)
            if t is None:
                continue

            pname = getattr(decl, "name", None)
            if not isinstance(pname, str):
                pname = f"arg{index}"
            infos.append((pname, t, decl))

        return infos, is_var_arg

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
        elif not isinstance(getattr(value, "type", None), ir.PointerType):
            base = self._alloca_in_entry(value.type, f"{name}.tmp")
            self._safe_store(value, base)
        idx0 = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(base, [idx0, idx0], name=name)

    def _decay_array_expr_to_pointer(self, expr_node, value, name="arrayexprdecay"):
        """Apply array-to-pointer decay using the expression's semantic type."""
        semantic_type = self._get_expr_ir_type(expr_node)
        if isinstance(semantic_type, ir.ArrayType):
            if isinstance(getattr(value, "type", None), ir.ArrayType):
                return self._decay_array_value_to_pointer(value, name)
            if (
                isinstance(getattr(value, "type", None), ir.PointerType)
                and value.type.pointee == semantic_type
            ):
                idx0 = ir.Constant(ir.IntType(32), 0)
                return self.builder.gep(value, [idx0, idx0], name=name)
        return self._decay_array_value_to_pointer(value, name)

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
            if isinstance(target_type, ir.VoidType):
                return val
            return self._zero_initializer(target_type)
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

    def _is_scalar_ir_type(self, ir_type):
        return (
            isinstance(ir_type, (ir.IntType, ir.PointerType))
            or self._is_floating_ir_type(ir_type)
        )

    def _is_aggregate_ir_type(self, ir_type):
        return _is_struct_ir_type(ir_type) or getattr(ir_type, "is_union", False)

    def _validate_explicit_cast(self, source_type, target_type):
        if isinstance(target_type, ir.VoidType):
            return

        if self._is_aggregate_ir_type(target_type) or isinstance(
            target_type, ir.ArrayType
        ):
            raise SemanticError("invalid cast to non-scalar type")

        if self._is_aggregate_ir_type(source_type):
            raise SemanticError("invalid cast from non-scalar type")

        if isinstance(source_type, ir.ArrayType):
            if not isinstance(target_type, ir.PointerType):
                raise SemanticError("invalid cast from array type")
            return

        if self._is_floating_ir_type(source_type) and isinstance(
            target_type, ir.PointerType
        ):
            raise SemanticError("invalid cast from floating type to pointer type")

        if isinstance(source_type, ir.PointerType) and self._is_floating_ir_type(
            target_type
        ):
            raise SemanticError("invalid cast from pointer type to floating type")

        if self._is_scalar_ir_type(source_type) and self._is_scalar_ir_type(
            target_type
        ):
            return

        raise SemanticError("invalid cast expression")

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
        custom_align = getattr(ir_type, "custom_align", None)
        if custom_align is not None:
            return custom_align
        if isinstance(ir_type, ir.VoidType):
            return 1
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
        elif _is_struct_ir_type(ir_type):
            if not ir_type.elements:
                return 1
            return max(self._ir_type_align(e) for e in ir_type.elements)
        return 8

    def _ir_type_size(self, ir_type):
        """Compute byte size of an IR type with proper alignment/padding."""
        custom_size = getattr(ir_type, "custom_size", None)
        if custom_size is not None:
            return custom_size
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
        elif _is_struct_ir_type(ir_type):
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

    @staticmethod
    def _align_up(value, align):
        if align <= 1:
            return value
        return (value + align - 1) & ~(align - 1)

    def _resolve_struct_member_ir_type(self, decl):
        if isinstance(decl.type, c_ast.Struct):
            return self.codegen_Struct(decl.type)
        if isinstance(decl.type, c_ast.Union):
            return self.codegen_Union(decl.type)
        if isinstance(decl.type, c_ast.TypeDecl) and isinstance(
            decl.type.type, c_ast.Struct
        ):
            return self.codegen_Struct(decl.type.type)
        if isinstance(decl.type, c_ast.TypeDecl) and isinstance(
            decl.type.type, c_ast.Union
        ):
            return self.codegen_Union(decl.type.type)
        if isinstance(decl.type, c_ast.ArrayDecl):
            def _build_array_type(arr_node):
                dim = self._eval_dim(arr_node.dim) if arr_node.dim else 0
                if isinstance(arr_node.type, c_ast.ArrayDecl):
                    inner = _build_array_type(arr_node.type)
                else:
                    inner = self._resolve_ast_type(arr_node.type)
                return ir.ArrayType(inner, dim)

            return _build_array_type(decl.type)
        if isinstance(decl.type, c_ast.PtrDecl):
            return self._resolve_ast_type(decl.type)
        if isinstance(decl.type, c_ast.TypeDecl):
            return self._resolve_ast_type(decl.type)
        return int64_t

    def _aggregate_member_names(self, aggregate_type):
        return list(getattr(aggregate_type, "members", ()) or [])

    def _aggregate_member_ir_type(self, aggregate_type, field_index):
        if getattr(aggregate_type, "is_union", False):
            member_types_by_index = getattr(
                aggregate_type, "member_types_by_index", None
            )
            if member_types_by_index is not None:
                return member_types_by_index[field_index]
            member_names = self._aggregate_member_names(aggregate_type)
            return aggregate_type.member_types[member_names[field_index]]
        return aggregate_type.elements[field_index]

    def _aggregate_member_decl_type(self, aggregate_type, field_index):
        member_decl_types_by_index = getattr(
            aggregate_type, "member_decl_types_by_index", None
        )
        if member_decl_types_by_index is not None:
            if field_index < len(member_decl_types_by_index):
                return member_decl_types_by_index[field_index]
            return None

        member_decl_types = getattr(aggregate_type, "member_decl_types", None)
        if isinstance(member_decl_types, dict):
            member_names = self._aggregate_member_names(aggregate_type)
            if field_index < len(member_names):
                return member_decl_types.get(member_names[field_index])
            return None
        if member_decl_types is not None and field_index < len(member_decl_types):
            return member_decl_types[field_index]
        return None

    def _aggregate_visible_field_paths(self, aggregate_type):
        visible_paths = getattr(aggregate_type, "visible_field_paths", None)
        if isinstance(visible_paths, dict):
            return visible_paths

        visible_paths = {}
        member_names = self._aggregate_member_names(aggregate_type)
        for field_index, member_name in enumerate(member_names):
            if member_name is not None:
                visible_paths.setdefault(member_name, (field_index,))
        return visible_paths

    def _compute_visible_field_paths(self, member_names, member_types):
        visible_paths = {}
        for field_index, (member_name, member_type) in enumerate(
            zip(member_names, member_types)
        ):
            if member_name is not None:
                visible_paths.setdefault(member_name, (field_index,))
                continue
            if not self._is_aggregate_ir_type(member_type):
                continue
            nested_paths = self._aggregate_visible_field_paths(member_type)
            for nested_name, nested_path in nested_paths.items():
                visible_paths.setdefault(
                    nested_name, (field_index,) + tuple(nested_path)
                )
        return visible_paths

    def _aggregate_field_path(self, aggregate_type, field_name):
        return self._aggregate_visible_field_paths(aggregate_type).get(field_name)

    def _aggregate_direct_member_index(self, aggregate_type, field_name):
        member_names = self._aggregate_member_names(aggregate_type)
        for field_index, member_name in enumerate(member_names):
            if member_name == field_name:
                return field_index
        named_member_indices = getattr(aggregate_type, "named_member_indices", None)
        if isinstance(named_member_indices, dict):
            return named_member_indices.get(field_name)
        return None

    def _aggregate_layout_by_index(self, aggregate_type, field_index):
        field_layouts_by_index = getattr(aggregate_type, "field_layouts_by_index", None)
        if field_layouts_by_index is None or field_index >= len(field_layouts_by_index):
            return None
        return field_layouts_by_index[field_index]

    def _bitfield_storage_ir_type(self, decl):
        storage_ir_type = self._resolve_ast_type(decl.type)
        if not isinstance(storage_ir_type, ir.IntType):
            return int32_t
        return storage_ir_type

    def _raw_layout_struct_type(
        self, size_bytes, align_bytes, type_name=None, existing_type=None, body=None
    ):
        align_map = {8: int64_t, 4: int32_t, 2: int16_t, 1: int8_t}
        identified_name = type_name or self._aggregate_type_name("layout")
        if body is None:
            if size_bytes <= 0:
                body = []
            elif align_bytes <= 1:
                body = [ir.ArrayType(int8_t, size_bytes)]
            else:
                align_type = align_map.get(align_bytes)
                if align_type is None or size_bytes < align_bytes:
                    body = [ir.ArrayType(int8_t, size_bytes)]
                else:
                    pad_size = size_bytes - align_bytes
                    if pad_size > 0:
                        body = [align_type, ir.ArrayType(int8_t, pad_size)]
                    else:
                        body = [align_type]
        if existing_type is not None:
            storage_type = existing_type
        else:
            storage_type = self.module.context.get_identified_type(identified_name)
        if storage_type.is_opaque:
            storage_type.set_body(*body)
        storage_type.custom_size = size_bytes
        storage_type.custom_align = align_bytes
        storage_type.has_custom_layout = True
        return storage_type

    def _custom_layout_storage_segments(self, field_layouts_by_index, size_bytes):
        segments = []
        cursor = 0
        storage_segment_by_offset = {}

        for field_index, layout in enumerate(field_layouts_by_index):
            if layout.is_bitfield:
                start = layout.storage_byte_offset
                segment = storage_segment_by_offset.get(start)
                if segment is None:
                    storage_size = self._ir_type_size(layout.storage_ir_type)
                    if start < cursor:
                        return None
                    if start > cursor:
                        segments.append(
                            StructStorageSegment(
                                kind="padding",
                                byte_offset=cursor,
                                ir_type=ir.ArrayType(int8_t, start - cursor),
                            )
                        )
                    segment = StructStorageSegment(
                        kind="bitfield_storage",
                        byte_offset=start,
                        ir_type=layout.storage_ir_type,
                        bitfield_indices=(field_index,),
                    )
                    storage_segment_by_offset[start] = segment
                    segments.append(segment)
                    cursor = max(cursor, start + storage_size)
                else:
                    segment.bitfield_indices = segment.bitfield_indices + (field_index,)
                continue

            start = layout.byte_offset
            if start < cursor:
                return None
            if start > cursor:
                segments.append(
                    StructStorageSegment(
                        kind="padding",
                        byte_offset=cursor,
                        ir_type=ir.ArrayType(int8_t, start - cursor),
                    )
                )
            segments.append(
                StructStorageSegment(
                    kind="field",
                    byte_offset=start,
                    ir_type=layout.semantic_ir_type,
                    field_index=field_index,
                )
            )
            cursor = start + self._ir_type_size(layout.semantic_ir_type)

        if cursor < size_bytes:
            segments.append(
                StructStorageSegment(
                    kind="padding",
                    byte_offset=cursor,
                    ir_type=ir.ArrayType(int8_t, size_bytes - cursor),
                )
            )

        return segments

    def _build_layout_backed_struct(self, node):
        current_bit = 0
        max_align = 1
        member_names = []
        member_decl_types = []
        member_types = []
        field_layouts = {}
        field_layouts_by_index = []

        for decl in node.decls:
            if decl.bitsize is not None:
                storage_ir_type = self._bitfield_storage_ir_type(decl)
                storage_bits = storage_ir_type.width
                bit_width = self._eval_const_expr(decl.bitsize)
                max_align = max(max_align, self._ir_type_align(storage_ir_type))
                if bit_width == 0:
                    current_bit = self._align_up(current_bit, storage_bits)
                    continue
                unit_start = (current_bit // storage_bits) * storage_bits
                if current_bit + bit_width > unit_start + storage_bits:
                    current_bit = self._align_up(current_bit, storage_bits)
                    unit_start = current_bit
                layout = StructFieldLayout(
                    name=decl.name,
                    byte_offset=current_bit // 8,
                    semantic_ir_type=storage_ir_type,
                    decl_type=decl.type,
                    is_bitfield=True,
                    storage_byte_offset=unit_start // 8,
                    storage_ir_type=storage_ir_type,
                    bit_offset=current_bit - unit_start,
                    bit_width=bit_width,
                    is_unsigned=self._bitfield_decl_is_unsigned(
                        decl.type, bit_width
                    ),
                )
                if decl.name is not None:
                    field_layouts[decl.name] = layout
                field_layouts_by_index.append(layout)
                member_names.append(decl.name)
                member_decl_types.append(decl.type)
                member_types.append(storage_ir_type)
                current_bit += bit_width
                continue

            semantic_ir_type = self._resolve_struct_member_ir_type(decl)
            align_bits = self._ir_type_align(semantic_ir_type) * 8
            current_bit = self._align_up(current_bit, align_bits)
            max_align = max(max_align, self._ir_type_align(semantic_ir_type))
            layout = StructFieldLayout(
                name=decl.name,
                byte_offset=current_bit // 8,
                semantic_ir_type=semantic_ir_type,
                decl_type=decl.type,
            )
            if decl.name is not None:
                field_layouts[decl.name] = layout
            field_layouts_by_index.append(layout)
            member_names.append(decl.name)
            member_decl_types.append(decl.type)
            member_types.append(semantic_ir_type)
            current_bit += self._ir_type_size(semantic_ir_type) * 8

        size_bits = self._align_up(current_bit, max_align * 8)
        size_bytes = max(1, size_bits // 8)
        type_name = None
        existing_type = None
        if node.name:
            tag_key = self._tag_type_key(node.name)
            if tag_key in self.env:
                existing_type = self.env[tag_key][0]
            type_name = self._aggregate_type_name("struct", node.name)
        storage_segments = self._custom_layout_storage_segments(
            field_layouts_by_index, size_bytes
        )
        struct_type = self._raw_layout_struct_type(
            size_bytes,
            max_align,
            type_name,
            existing_type=existing_type,
            body=(
                [segment.ir_type for segment in storage_segments]
                if storage_segments is not None
                else None
            ),
        )
        struct_type.members = member_names
        struct_type.member_decl_types = member_decl_types
        struct_type.field_layouts = field_layouts
        struct_type.field_layouts_by_index = field_layouts_by_index
        if storage_segments is not None:
            struct_type.storage_segments = storage_segments
        struct_type.named_member_indices = {
            name: index
            for index, name in enumerate(member_names)
            if name is not None
        }
        struct_type.visible_field_paths = self._compute_visible_field_paths(
            member_names, member_types
        )
        if node.name:
            self.define(self._tag_type_key(node.name), (struct_type, None))
        return struct_type

    def _byte_offset_ptr(self, base_ptr, byte_offset, target_ptr_type, name="fieldptr"):
        byte_ptr = self.builder.bitcast(base_ptr, voidptr_t, name=f"{name}.base")
        if byte_offset:
            byte_ptr = self.builder.gep(
                byte_ptr,
                [ir.Constant(ir.IntType(32), byte_offset)],
                name=f"{name}.offs",
            )
        if byte_ptr.type != target_ptr_type:
            return self.builder.bitcast(byte_ptr, target_ptr_type, name=name)
        return byte_ptr

    @staticmethod
    def _bitfield_mask(bit_width):
        if bit_width <= 0:
            return 0
        return (1 << bit_width) - 1

    def _load_bitfield(self, ref):
        align = max(1, self._ir_type_align(ref.storage_ir_type))
        raw = self.builder.load(ref.container_ptr, align=align)
        if ref.bit_offset:
            raw = self.builder.lshr(
                raw, ir.Constant(ref.storage_ir_type, ref.bit_offset), "bitshift"
            )

        semantic_width = ref.semantic_ir_type.width
        if ref.is_unsigned:
            if ref.bit_width < ref.storage_ir_type.width:
                raw = self.builder.and_(
                    raw,
                    ir.Constant(ref.storage_ir_type, self._bitfield_mask(ref.bit_width)),
                    "bitmask",
                )
            if raw.type.width > semantic_width:
                raw = self.builder.trunc(raw, ref.semantic_ir_type, "bittrunc")
            elif raw.type.width < semantic_width:
                raw = self.builder.zext(raw, ref.semantic_ir_type, "bitzext")
            self._tag_unsigned(raw)
            return raw

        if ref.bit_width < ref.storage_ir_type.width:
            narrow_type = ir.IntType(ref.bit_width)
            raw = self.builder.trunc(raw, narrow_type, "bitsigned.trunc")
            return self.builder.sext(raw, ref.semantic_ir_type, "bitsigned.sext")
        if raw.type.width > semantic_width:
            return self.builder.trunc(raw, ref.semantic_ir_type, "bittrunc")
        if raw.type.width < semantic_width:
            return self.builder.sext(raw, ref.semantic_ir_type, "bitsext")
        return raw

    def _store_bitfield(self, value, ref):
        if value is None:
            return
        align = max(1, self._ir_type_align(ref.storage_ir_type))
        storage_value = self.builder.load(ref.container_ptr, align=align)
        if value.type != ref.semantic_ir_type:
            value = self._implicit_convert(value, ref.semantic_ir_type)
        value = self._convert_int_value(
            value, ref.storage_ir_type, result_unsigned=ref.is_unsigned
        )
        field_mask = self._bitfield_mask(ref.bit_width)
        field_mask_const = ir.Constant(ref.storage_ir_type, field_mask)
        if ref.bit_width < ref.storage_ir_type.width:
            value = self.builder.and_(value, field_mask_const, "bitstore.mask")
            if ref.bit_offset:
                value = self.builder.shl(
                    value,
                    ir.Constant(ref.storage_ir_type, ref.bit_offset),
                    "bitstore.shift",
                )
            clear_mask = ((1 << ref.storage_ir_type.width) - 1) ^ (
                field_mask << ref.bit_offset
            )
            storage_value = self.builder.and_(
                storage_value,
                ir.Constant(ref.storage_ir_type, clear_mask),
                "bitstore.clear",
            )
            value = self.builder.or_(storage_value, value, "bitstore.merge")
        self.builder.store(value, ref.container_ptr, align=align)

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
            if isinstance(resolved, (ir.ArrayType, ir.PointerType)) or _is_struct_ir_type(
                resolved
            ):
                return resolved
        except Exception:
            pass

        return semantic_field_type

    def _get_aggregate_field_info_by_path(self, aggregate_type, field_path):
        field_index = field_path[0]

        if getattr(aggregate_type, "is_union", False):
            field_type = self._aggregate_member_ir_type(aggregate_type, field_index)
            semantic_field_type = self._refine_member_ir_type(
                aggregate_type, field_index, field_type
            )
            if len(field_path) == 1:
                return 0, semantic_field_type
            if not self._is_aggregate_ir_type(semantic_field_type):
                raise CodegenError("field path descends into non-aggregate union member")
            nested_offset, nested_type = self._get_aggregate_field_info_by_path(
                semantic_field_type, field_path[1:]
            )
            return nested_offset, nested_type

        if getattr(aggregate_type, "has_custom_layout", False):
            layout = self._aggregate_layout_by_index(aggregate_type, field_index)
            if layout is None:
                raise CodegenError(f"Field index {field_index} not found in aggregate")
            if len(field_path) == 1:
                if layout.is_bitfield:
                    raise CodegenError("offsetof on bit-field is not supported")
                return layout.byte_offset, layout.semantic_ir_type
            if layout.is_bitfield or not self._is_aggregate_ir_type(
                layout.semantic_ir_type
            ):
                raise CodegenError("field path descends into non-aggregate member")
            nested_offset, nested_type = self._get_aggregate_field_info_by_path(
                layout.semantic_ir_type, field_path[1:]
            )
            return layout.byte_offset + nested_offset, nested_type

        if not hasattr(aggregate_type, "members"):
            raise CodegenError(f"Aggregate has no named fields: {aggregate_type}")

        offset = 0
        field_type = None
        for i, member_type in enumerate(aggregate_type.elements):
            align = self._ir_type_align(member_type)
            offset = self._align_up(offset, align)
            if i == field_index:
                field_type = member_type
                break
            offset += self._ir_type_size(member_type)

        if field_type is None:
            raise CodegenError(f"Field index {field_index} not found in aggregate")

        semantic_field_type = self._refine_member_ir_type(
            aggregate_type, field_index, field_type
        )
        if len(field_path) == 1:
            return offset, semantic_field_type
        if not self._is_aggregate_ir_type(semantic_field_type):
            raise CodegenError("field path descends into non-aggregate member")
        nested_offset, nested_type = self._get_aggregate_field_info_by_path(
            semantic_field_type, field_path[1:]
        )
        return offset + nested_offset, nested_type

    def _get_aggregate_field_info(self, aggregate_type, field_name):
        """Return byte offset and semantic IR type for a struct/union field."""
        field_path = self._aggregate_field_path(aggregate_type, field_name)
        if field_path is None:
            raise CodegenError(f"Field '{field_name}' not found in aggregate")
        return self._get_aggregate_field_info_by_path(aggregate_type, field_path)

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

    def _infer_sizeof_operand_ir_type(self, node):
        """Infer the operand type for sizeof without emitting runtime IR."""
        cached = self._get_expr_ir_type(node)
        if cached is not None:
            return cached

        if isinstance(node, c_ast.Constant):
            if node.type == "int":
                raw = node.value
                lower = raw.lower()
                val_str = raw.rstrip("uUlL")
                if val_str.startswith("0x") or val_str.startswith("0X"):
                    value = int(val_str, 16)
                elif val_str.startswith("0") and len(val_str) > 1 and val_str[1:].isdigit():
                    value = int(val_str, 8)
                else:
                    value = int(val_str)
                if "l" in lower or value > 0x7FFFFFFF:
                    return int64_t
                return int32_t
            if node.type == "char":
                return int32_t
            if node.type in ("string", "wstring"):
                data = self._string_literal_data(node)
                elem_type = int32_t if self._is_wide_string_constant(node) else int8_t
                return ir.ArrayType(elem_type, len(data))
            return self._float_literal_ir_type(node.value)

        if isinstance(node, c_ast.ID):
            ir_type, _ = self.lookup(node.name)
            return ir_type

        if isinstance(node, c_ast.CompoundLiteral):
            return self._compound_literal_ir_type(node.type.type, init_node=node.init)

        if isinstance(node, c_ast.StructRef):
            base_type = self._infer_sizeof_operand_ir_type(node.name)
            aggregate_type = base_type
            if node.type == "->":
                if isinstance(base_type, ir.ArrayType):
                    aggregate_type = base_type.element
                elif not isinstance(base_type, ir.PointerType):
                    raise CodegenError(
                        f"sizeof operand is not a pointer for '->': {base_type}"
                    )
                else:
                    aggregate_type = base_type.pointee
            _, field_type = self._get_aggregate_field_info(
                aggregate_type, node.field.name
            )
            return field_type

        if isinstance(node, c_ast.ArrayRef):
            base_type = self._infer_sizeof_operand_ir_type(node.name)
            if isinstance(base_type, ir.ArrayType):
                return base_type.element
            if isinstance(base_type, ir.PointerType):
                return base_type.pointee
            raise CodegenError(
                f"sizeof operand is not indexable: {type(base_type).__name__}"
            )

        if isinstance(node, c_ast.Cast):
            return self._resolve_ast_type(node.to_type.type)

        if isinstance(node, c_ast.UnaryOp):
            if node.op == "&":
                return ir.PointerType(self._infer_sizeof_operand_ir_type(node.expr))
            if node.op == "*":
                base_type = self._infer_sizeof_operand_ir_type(node.expr)
                if isinstance(base_type, ir.ArrayType):
                    return base_type.element
                if not isinstance(base_type, ir.PointerType):
                    raise CodegenError(
                        f"sizeof operand is not a pointer for '*': {base_type}"
                    )
                return base_type.pointee
            if node.op in ("+", "-", "~"):
                base_type = self._infer_sizeof_operand_ir_type(node.expr)
                if isinstance(base_type, ir.IntType):
                    return self._integer_promotion_ir_type(base_type)
                return base_type
            if node.op == "!":
                return int32_t
            if node.op in ("p++", "p--", "++", "--"):
                return self._infer_sizeof_operand_ir_type(node.expr)
            if node.op == "sizeof":
                return int64_t

        if isinstance(node, c_ast.BinaryOp):
            lhs_type = self._decay_ir_type(
                self._infer_sizeof_operand_ir_type(node.left)
            )
            rhs_type = self._decay_ir_type(
                self._infer_sizeof_operand_ir_type(node.right)
            )

            if node.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
                return int32_t

            if (
                node.op in ("+", "-")
                and isinstance(lhs_type, ir.PointerType)
                and isinstance(rhs_type, ir.IntType)
            ):
                return lhs_type
            if (
                node.op == "+"
                and isinstance(rhs_type, ir.PointerType)
                and isinstance(lhs_type, ir.IntType)
            ):
                return rhs_type
            if (
                node.op == "-"
                and isinstance(lhs_type, ir.PointerType)
                and isinstance(rhs_type, ir.PointerType)
            ):
                return int64_t

            if node.op in ("<<", ">>") and isinstance(lhs_type, ir.IntType):
                return self._integer_promotion_ir_type(lhs_type)

            return self._usual_arithmetic_conversion_ir_type(lhs_type, rhs_type)

        if isinstance(node, c_ast.TernaryOp):
            true_type = self._infer_sizeof_operand_ir_type(node.iftrue)
            false_type = self._infer_sizeof_operand_ir_type(node.iffalse)
            if true_type == false_type:
                return true_type
            if (
                isinstance(true_type, (ir.IntType, ir.FloatType, ir.DoubleType))
                and isinstance(false_type, (ir.IntType, ir.FloatType, ir.DoubleType))
            ):
                return self._usual_arithmetic_conversion_ir_type(
                    true_type, false_type
                )
            if isinstance(true_type, ir.PointerType) and isinstance(
                false_type, ir.PointerType
            ):
                return true_type
            return true_type

        if isinstance(node, c_ast.ExprList) and node.exprs:
            return self._infer_sizeof_operand_ir_type(node.exprs[-1])

        if isinstance(node, c_ast.StmtExpr) and getattr(node.stmt, "block_items", None):
            for item in reversed(node.stmt.block_items):
                if self._is_expression_node(item):
                    return self._infer_sizeof_operand_ir_type(item)

        if isinstance(node, c_ast.GenericSelection):
            selected = self._select_generic_association(node)
            if selected is not None:
                return self._infer_sizeof_operand_ir_type(selected)

        if isinstance(node, c_ast.FuncCall):
            if isinstance(node.name, c_ast.ID):
                ret_type = getattr(self, "func_return_types", {}).get(node.name.name)
                if ret_type is not None:
                    return ret_type

        raise CodegenError(f"Cannot infer sizeof operand type: {type(node).__name__}")

    @staticmethod
    def _make_identifier_type(names, quals=None, declname=None):
        return c_ast.TypeDecl(
            declname,
            list(quals or []),
            c_ast.IdentifierType(list(names)),
        )

    @staticmethod
    def _is_expression_node(node):
        return isinstance(
            node,
            (
                c_ast.ArrayRef,
                c_ast.Assignment,
                c_ast.BinaryOp,
                c_ast.Cast,
                c_ast.CompoundLiteral,
                c_ast.Constant,
                c_ast.ExprList,
                c_ast.FuncCall,
                c_ast.GenericSelection,
                c_ast.ID,
                c_ast.InitList,
                c_ast.StmtExpr,
                c_ast.StructRef,
                c_ast.TernaryOp,
                c_ast.UnaryOp,
            ),
        )

    @staticmethod
    def _canonical_identifier_names(names):
        canonical = list(names)
        if "signed" in canonical and "char" not in canonical:
            canonical = [name for name in canonical if name != "signed"]
        if "int" in canonical and any(name in canonical for name in ("short", "long")):
            canonical = [name for name in canonical if name != "int"]
        return tuple(sorted(canonical))

    def _generic_type_key_from_type(
        self,
        node_type,
        *,
        inherited_quals=(),
        top_level=True,
        strip_top_level_quals=False,
        decay_top_level=False,
    ):
        if node_type is None:
            return None

        if isinstance(node_type, c_ast.Typename):
            return self._generic_type_key_from_type(
                node_type.type,
                inherited_quals=inherited_quals,
                top_level=top_level,
                strip_top_level_quals=strip_top_level_quals,
                decay_top_level=decay_top_level,
            )

        if isinstance(node_type, c_ast.TypeDecl):
            merged_quals = tuple(sorted(inherited_quals + tuple(node_type.quals or ())))
            inner = node_type.type
            if isinstance(inner, c_ast.IdentifierType) and len(inner.names) == 1:
                resolved = self._lookup_typedef_ast_type(inner.names[0])
                if resolved is not None:
                    return self._generic_type_key_from_type(
                        resolved,
                        inherited_quals=merged_quals,
                        top_level=top_level,
                        strip_top_level_quals=strip_top_level_quals,
                        decay_top_level=decay_top_level,
                    )
            effective_quals = (
                ()
                if top_level and strip_top_level_quals
                else merged_quals
            )
            if isinstance(inner, c_ast.IdentifierType):
                return (
                    "base",
                    effective_quals,
                    self._canonical_identifier_names(inner.names),
                )
            if isinstance(inner, c_ast.Struct):
                return ("struct", effective_quals, inner.name or f"anon:{id(inner)}")
            if isinstance(inner, c_ast.Union):
                return ("union", effective_quals, inner.name or f"anon:{id(inner)}")
            if isinstance(inner, c_ast.Enum):
                return ("enum", effective_quals, inner.name or f"anon:{id(inner)}")
            return self._generic_type_key_from_type(
                inner,
                inherited_quals=effective_quals,
                top_level=top_level,
                strip_top_level_quals=False,
                decay_top_level=decay_top_level,
            )

        if isinstance(node_type, c_ast.PtrDecl):
            quals = tuple(
                sorted(
                    ()
                    if top_level and strip_top_level_quals
                    else inherited_quals + tuple(node_type.quals or ())
                )
            )
            return (
                "ptr",
                quals,
                self._generic_type_key_from_type(
                    node_type.type,
                    top_level=False,
                    strip_top_level_quals=False,
                    decay_top_level=False,
                ),
            )

        if isinstance(node_type, c_ast.ArrayDecl):
            if top_level and decay_top_level:
                return (
                    "ptr",
                    (),
                    self._generic_type_key_from_type(
                        node_type.type,
                        top_level=False,
                        strip_top_level_quals=False,
                        decay_top_level=False,
                    ),
                )
            dim = None
            if node_type.dim is not None:
                try:
                    dim = int(self._eval_const_expr(node_type.dim))
                except Exception:
                    dim = None
            return (
                "array",
                dim,
                self._generic_type_key_from_type(
                    node_type.type,
                    top_level=False,
                    strip_top_level_quals=False,
                    decay_top_level=False,
                ),
            )

        if isinstance(node_type, c_ast.FuncDecl):
            params = []
            is_var_arg = False
            if node_type.args:
                for param in node_type.args.params:
                    if isinstance(param, c_ast.EllipsisParam):
                        is_var_arg = True
                        continue
                    param_type = param.type if hasattr(param, "type") else param
                    params.append(
                        self._generic_type_key_from_type(
                            param_type,
                            top_level=False,
                            strip_top_level_quals=False,
                            decay_top_level=False,
                        )
                    )
            func_key = (
                "func",
                self._generic_type_key_from_type(
                    node_type.type,
                    top_level=False,
                    strip_top_level_quals=False,
                    decay_top_level=False,
                ),
                tuple(params),
                is_var_arg,
            )
            if top_level and decay_top_level:
                return ("ptr", (), func_key)
            return func_key

        return None

    def _generic_integer_literal_type(self, raw):
        lower = raw.lower()
        val_str = raw.rstrip("uUlL")
        names = []
        if "u" in lower:
            names.append("unsigned")
        if "ll" in lower:
            names.extend(["long", "long"])
        elif "l" in lower:
            names.append("long")
        else:
            names.append("int")
        return self._make_identifier_type(names)

    def _generic_base_rank(self, names):
        if "long" in names and names.count("long") > 1:
            return 4
        if "long" in names:
            return 3
        if "int" in names:
            return 2
        if "short" in names:
            return 1
        if "char" in names:
            return 0
        return -1

    @staticmethod
    def _generic_is_base_key(key):
        return isinstance(key, tuple) and len(key) == 3 and key[0] == "base"

    def _generic_integer_promotion_key(self, key):
        if not self._generic_is_base_key(key):
            return key
        _kind, quals, names = key
        if "float" in names or "double" in names:
            return key
        if self._generic_base_rank(names) < self._generic_base_rank(("int",)):
            return ("base", quals, ("int",))
        return key

    def _generic_usual_arithmetic_conversion_key(self, lhs_key, rhs_key):
        lhs = self._generic_integer_promotion_key(lhs_key)
        rhs = self._generic_integer_promotion_key(rhs_key)
        if not (self._generic_is_base_key(lhs) and self._generic_is_base_key(rhs)):
            return lhs

        lhs_names = lhs[2]
        rhs_names = rhs[2]

        if "double" in lhs_names or "double" in rhs_names:
            return ("base", (), ("double",))
        if "float" in lhs_names or "float" in rhs_names:
            return ("base", (), ("float",))

        lhs_rank = self._generic_base_rank(lhs_names)
        rhs_rank = self._generic_base_rank(rhs_names)
        if lhs_rank > rhs_rank:
            return ("base", (), lhs_names)
        if rhs_rank > lhs_rank:
            return ("base", (), rhs_names)
        if "unsigned" in lhs_names:
            return ("base", (), lhs_names)
        if "unsigned" in rhs_names:
            return ("base", (), rhs_names)
        return ("base", (), lhs_names)

    def _generic_expr_type_key(self, node):
        if node is None:
            return None

        if isinstance(node, c_ast.ID):
            decl_type = self._lookup_decl_ast_type(node.name)
            if decl_type is not None:
                return self._generic_type_key_from_type(
                    decl_type,
                    strip_top_level_quals=True,
                    decay_top_level=True,
                )
            return None

        if isinstance(node, c_ast.Constant):
            if node.type == "int":
                return self._generic_type_key_from_type(
                    self._generic_integer_literal_type(node.value)
                )
            if node.type == "char":
                return self._generic_type_key_from_type(
                    self._make_identifier_type(["int"])
                )
            if node.type == "float":
                raw = node.value.lower()
                names = ["float"] if raw.endswith("f") else ["double"]
                return self._generic_type_key_from_type(
                    self._make_identifier_type(names)
                )
            if node.type == "wstring":
                return (
                    "ptr",
                    (),
                    self._generic_type_key_from_type(
                        self._make_identifier_type(["wchar_t"])
                    ),
                )
            if node.type == "string":
                return (
                    "ptr",
                    (),
                    self._generic_type_key_from_type(
                        self._make_identifier_type(["char"])
                    ),
                )
            return None

        if isinstance(node, c_ast.Cast):
            return self._generic_type_key_from_type(node.to_type.type)

        if isinstance(node, c_ast.UnaryOp):
            if node.op == "&":
                expr_key = self._generic_expr_type_key(node.expr)
                return ("ptr", (), expr_key) if expr_key is not None else None
            if node.op == "*":
                expr_key = self._generic_expr_type_key(node.expr)
                if isinstance(expr_key, tuple) and expr_key[0] == "ptr":
                    return expr_key[2]
                return None
            if node.op == "!":
                return self._generic_type_key_from_type(
                    self._make_identifier_type(["int"])
                )
            if node.op in ("+", "-", "~", "p++", "p--", "++", "--"):
                expr_key = self._generic_expr_type_key(node.expr)
                return self._generic_integer_promotion_key(expr_key)
            if node.op == "sizeof":
                return self._generic_type_key_from_type(
                    self._make_identifier_type(["unsigned", "long"])
                )

        if isinstance(node, c_ast.BinaryOp):
            if node.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
                return self._generic_type_key_from_type(
                    self._make_identifier_type(["int"])
                )
            lhs_key = self._generic_expr_type_key(node.left)
            rhs_key = self._generic_expr_type_key(node.right)
            if lhs_key is None or rhs_key is None:
                return lhs_key or rhs_key
            if node.op in ("+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^"):
                return self._generic_usual_arithmetic_conversion_key(lhs_key, rhs_key)
            return lhs_key

        if isinstance(node, c_ast.TernaryOp):
            true_key = self._generic_expr_type_key(node.iftrue)
            false_key = self._generic_expr_type_key(node.iffalse)
            if true_key == false_key:
                return true_key
            if true_key is None or false_key is None:
                return true_key or false_key
            if self._generic_is_base_key(true_key) and self._generic_is_base_key(false_key):
                return self._generic_usual_arithmetic_conversion_key(
                    true_key, false_key
                )
            return true_key

        if isinstance(node, c_ast.ExprList) and node.exprs:
            return self._generic_expr_type_key(node.exprs[-1])

        if isinstance(node, c_ast.FuncCall):
            callee_key = self._generic_expr_type_key(node.name)
            if (
                isinstance(callee_key, tuple)
                and callee_key[0] == "ptr"
                and isinstance(callee_key[2], tuple)
                and callee_key[2][0] == "func"
            ):
                return callee_key[2][1]
            return None

        if isinstance(node, c_ast.StmtExpr) and getattr(node.stmt, "block_items", None):
            for item in reversed(node.stmt.block_items):
                if self._is_expression_node(item):
                    return self._generic_expr_type_key(item)
            return None

        if isinstance(node, c_ast.GenericSelection):
            selected = self._select_generic_association(node)
            if selected is not None:
                return self._generic_expr_type_key(selected)

        return None

    def _select_generic_association(self, node):
        controlling_key = self._generic_expr_type_key(node.expr)
        default_expr = None
        for assoc in node.associations or []:
            if assoc.type is None:
                default_expr = assoc.expr
                continue
            assoc_key = self._generic_type_key_from_type(assoc.type)
            if assoc_key == controlling_key:
                return assoc.expr
        return default_expr

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
        lhs = self._decay_array_expr_to_pointer(node.left, lhs, "lhsarraydecay")
        rhs = self._decay_array_expr_to_pointer(node.right, rhs, "rhsarraydecay")

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
        elif isinstance(cond_val.type, ir.IntType) and cond_val.type.width < int32_t.width:
            cond_val = self._integer_promotion(cond_val)

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
        prelabel_items = []
        hoisted_items = []
        labels = []
        label_bodies = {}

        label_ids = set()

        def contains_switch_label(item):
            return self._stmt_contains_switch_label(item)

        def add_label(item):
            if id(item) in label_ids:
                return
            label_ids.add(id(item))
            labels.append(item)
            label_bodies.setdefault(id(item), [])

        def collect_nested_labels(item):
            if item is None or isinstance(item, c_ast.Switch):
                return
            if isinstance(item, (c_ast.Case, c_ast.Default)):
                add_label(item)
                for child in item.stmts or []:
                    collect_nested_labels(child)
                return
            for _name, child in item.children():
                if isinstance(child, list):
                    for entry in child:
                        collect_nested_labels(entry)
                else:
                    collect_nested_labels(child)

        def collect_guarded_label_sequence(items, active_label=None):
            active = active_label
            for item in list(items or []):
                if isinstance(item, (c_ast.Case, c_ast.Default)):
                    add_label(item)
                    active = collect_guarded_label_sequence(item.stmts or [], item)
                    continue
                if isinstance(item, c_ast.Compound):
                    if contains_switch_label(item):
                        active = collect_guarded_label_sequence(
                            item.block_items or [], active
                        )
                    elif active is not None:
                        label_bodies[id(active)].append(item)
                    continue
                if isinstance(item, c_ast.If) and contains_switch_label(item):
                    if active is not None:
                        label_bodies[id(active)].append(item)
                    collect_guarded_label_bodies(item)
                    continue
                if contains_switch_label(item):
                    if active is not None:
                        label_bodies[id(active)].append(item)
                    collect_nested_labels(item)
                    continue
                if active is not None:
                    label_bodies[id(active)].append(item)
            return active

        def collect_guarded_label_bodies(item):
            if item is None or isinstance(item, c_ast.Switch):
                return None
            if isinstance(item, (c_ast.Case, c_ast.Default)):
                add_label(item)
                return collect_guarded_label_sequence(item.stmts or [], item)
            if isinstance(item, c_ast.Compound):
                return collect_guarded_label_sequence(item.block_items or [], None)
            if isinstance(item, c_ast.If):
                active_true = collect_guarded_label_bodies(item.iftrue)
                active_false = collect_guarded_label_bodies(item.iffalse)
                return active_false or active_true
            for _name, child in item.children():
                if isinstance(child, list):
                    for entry in child:
                        collect_guarded_label_bodies(entry)
                else:
                    collect_guarded_label_bodies(child)
            return None

        def process_items(items, active_label):
            active = active_label
            items = list(items or [])
            for idx, item in enumerate(items):
                later_has_label = any(
                    contains_switch_label(later) for later in items[idx + 1 :]
                )
                if isinstance(item, (c_ast.Case, c_ast.Default)):
                    add_label(item)
                    active = process_items(item.stmts or [], item)
                    continue
                if isinstance(item, c_ast.Compound) and contains_switch_label(item):
                    active = process_items(item.block_items or [], active)
                    continue
                if isinstance(item, c_ast.If) and contains_switch_label(item):
                    if active is None:
                        prelabel_items.append(item)
                    else:
                        label_bodies[id(active)].append(item)
                    collect_guarded_label_bodies(item)
                    continue
                if contains_switch_label(item):
                    if active is None:
                        prelabel_items.append(item)
                    else:
                        label_bodies[id(active)].append(item)
                    collect_nested_labels(item)
                    continue
                if active is None:
                    prelabel_items.append(item)
                    continue
                if isinstance(item, c_ast.Decl) and later_has_label:
                    if item.init is not None:
                        raise CodegenError(
                            "switch-scope declaration before later case with initializer is not supported"
                        )
                    hoisted_items.append(item)
                    continue
                if later_has_label and isinstance(
                    item, (c_ast.Typedef, c_ast.EmptyStatement)
                ):
                    hoisted_items.append(item)
                    continue
                label_bodies[id(active)].append(item)
            return active

        process_items(switch_items, None)

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

        with self.new_scope():
            self.define("break", after_bb)

            for item in prelabel_items + hoisted_items:
                if isinstance(item, c_ast.Decl):
                    if item.init is not None:
                        raise CodegenError(
                            "switch-scope declaration before first case with initializer is not supported"
                        )
                    self.codegen(item)
                elif isinstance(item, (c_ast.Typedef, c_ast.EmptyStatement)):
                    self.codegen(item)

            switch_inst = self.builder.switch(cond_val, default_bb)

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

            self._switch_contexts.append({"blocks": label_blocks})
            try:
                for idx, item in enumerate(labels):
                    self.builder.position_at_end(label_blocks[id(item)])
                    for stmt in label_bodies.get(id(item), []):
                        if self.builder.block.is_terminated:
                            if isinstance(stmt, c_ast.Label):
                                self.codegen(stmt)
                            continue
                        self.codegen(stmt)
                    if not self.builder.block.is_terminated:
                        next_bb = after_bb
                        has_nested_switch_labels = any(
                            self._stmt_contains_switch_label(stmt)
                            for stmt in label_bodies.get(id(item), [])
                        )
                        if idx + 1 < len(labels) and not has_nested_switch_labels:
                            next_bb = label_blocks[id(labels[idx + 1])]
                        self.builder.branch(next_bb)
            finally:
                self._switch_contexts.pop()

        self.builder.position_at_end(after_bb)
        return None, None

    def codegen_TernaryOp(self, node):
        try:
            cond_const = self._eval_const_expr(node.cond)
        except Exception:
            cond_const = None

        if cond_const is not None:
            if cond_const:
                chosen = node.iftrue if node.iftrue is not None else node.cond
            else:
                chosen = node.iffalse
            result = self.codegen(chosen)
            result_val, _ = result
            if result_val is not None:
                semantic_type = self._get_expr_ir_type(
                    chosen, getattr(result_val, "type", None)
                )
                if semantic_type is not None:
                    self._set_expr_ir_type(node, semantic_type)
            return result

        cond_val, _ = self.codegen(node.cond)
        cmp = self._to_bool(cond_val)

        then_bb = self.builder.function.append_basic_block("ternary_true")
        else_bb = self.builder.function.append_basic_block("ternary_false")
        merge_bb = self.builder.function.append_basic_block("ternary_end")

        self.builder.cbranch(cmp, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        if node.iftrue is None:
            true_val = cond_val
        else:
            true_val, _ = self.codegen(node.iftrue)
        true_bb_end = self.builder.block

        self.builder.position_at_end(else_bb)
        false_val, _ = self.codegen(node.iffalse)
        false_bb_end = self.builder.block

        def zero_value(target_type):
            return self._zero_initializer(target_type)

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
        result_is_unsigned = isinstance(target, ir.IntType) and any(
            self._is_unsigned_val(value) for _pred, value in incoming
        )
        result_has_unsigned_pointee = isinstance(target, ir.PointerType) and any(
            self._is_unsigned_pointee(value) for _pred, value in incoming
        )
        result_has_unsigned_return = isinstance(target, ir.PointerType) and any(
            self._is_unsigned_return(value) for _pred, value in incoming
        )
        if len(incoming) == 1:
            result = incoming[0][1]
            if result_is_unsigned:
                self._tag_unsigned(result)
            if result_has_unsigned_pointee:
                self._tag_unsigned_pointee(result)
            if result_has_unsigned_return:
                self._tag_unsigned_return(result)
            return result, None

        phi = self.builder.phi(target, "ternary")
        for pred, value in incoming:
            phi.add_incoming(value, pred)
        if result_is_unsigned:
            self._tag_unsigned(phi)
        if result_has_unsigned_pointee:
            self._tag_unsigned_pointee(phi)
        if result_has_unsigned_return:
            self._tag_unsigned_return(phi)
        return phi, None

    def codegen_Cast(self, node):
        dest_ir_type = self._resolve_ast_type(node.to_type.type)
        if isinstance(node.expr, c_ast.InitList) and (
            isinstance(dest_ir_type, ir.ArrayType)
            or _is_struct_ir_type(dest_ir_type)
            or getattr(dest_ir_type, "is_union", False)
        ):
            return self._materialize_compound_literal(node.to_type.type, node.expr)

        expr, ptr = self.codegen(node.expr)

        if (
            expr is not None
            and expr.type == dest_ir_type
            and (
                self._is_aggregate_ir_type(dest_ir_type)
                or isinstance(dest_ir_type, ir.ArrayType)
            )
        ):
            self._set_expr_ir_type(node, dest_ir_type)
            return expr, ptr

        self._validate_explicit_cast(expr.type, dest_ir_type)
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

    def codegen_CompoundLiteral(self, node):
        return self._materialize_compound_literal(node.type.type, node.init)

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
            if callee == "__builtin_alloca":
                return self._codegen_builtin_alloca(node)
            if callee == "alloca":
                return self._codegen_builtin_alloca(node)
            if callee == "__builtin_va_arg":
                return ir.Constant(voidptr_t, None), None
            if callee == "__builtin_expect":
                return self._codegen_builtin_expect(node)
            if callee == "__builtin_trap":
                return self._codegen_builtin_trap(node)
            if callee == "__builtin_assume":
                return ir.Constant(int64_t, 0), None
            if callee == "__builtin_prefetch":
                return ir.Constant(int64_t, 0), None
            if callee == "__builtin_unreachable":
                return self._codegen_builtin_unreachable(node)
            if callee == "__builtin_classify_type":
                return self._codegen_builtin_classify_type(node)
            if callee == "__builtin_add_overflow":
                return self._codegen_builtin_overflow(node, "add")
            if callee == "__builtin_sub_overflow":
                return self._codegen_builtin_overflow(node, "sub")
            if callee == "__builtin_mul_overflow":
                return self._codegen_builtin_overflow(node, "mul")
            if callee == "__builtin_bswap16":
                return self._codegen_builtin_bswap(node, 16)
            if callee == "__builtin_bswap32":
                return self._codegen_builtin_bswap(node, 32)
            if callee == "__builtin_bswap64":
                return self._codegen_builtin_bswap(node, 64)
            if callee == "__builtin_rotateleft32":
                return self._codegen_builtin_rotate(node, 32, "left")
            if callee == "__builtin_rotateleft64":
                return self._codegen_builtin_rotate(node, 64, "left")
            if callee == "__builtin_rotateright32":
                return self._codegen_builtin_rotate(node, 32, "right")
            if callee == "__builtin_rotateright64":
                return self._codegen_builtin_rotate(node, 64, "right")
            if callee == "__builtin_clz":
                return self._codegen_builtin_bitcount(node, 32, "ctlz")
            if callee == "__builtin_clzll":
                return self._codegen_builtin_bitcount(node, 64, "ctlz")
            if callee == "__builtin_ctz":
                return self._codegen_builtin_bitcount(node, 32, "cttz")
            if callee == "__builtin_ctzll":
                return self._codegen_builtin_bitcount(node, 64, "cttz")
            if callee == "__builtin_ffs":
                return self._codegen_builtin_ffs(node, 32)
            if callee == "__builtin_ffsll":
                return self._codegen_builtin_ffs(node, 64)
            if callee == "__builtin_frame_address":
                return self._codegen_builtin_frame_address(node)
            if callee == "__builtin_memcmp":
                callee = "memcmp"
            if callee == "__builtin_memchr":
                callee = "memchr"
            if callee == "__builtin_strcmp":
                callee = "strcmp"
            if callee == "__builtin_strcpy":
                callee = "strcpy"
            if callee == "__builtin_sprintf":
                callee = "sprintf"
            if callee == "__builtin_snprintf":
                callee = "snprintf"
            if callee == "__builtin_inf":
                return ir.Constant(_double, float("inf")), None
            if callee == "__builtin_inff":
                return ir.Constant(_float, float("inf")), None
            if callee == "__builtin_infl":
                return ir.Constant(_double, float("inf")), None
            if callee == "__builtin_nan":
                return ir.Constant(_double, float("nan")), None
            if callee == "__builtin_nanf":
                return ir.Constant(_float, float("nan")), None
            if callee == "__builtin_nanl":
                return ir.Constant(_double, float("nan")), None
            if callee in ("__builtin_isnan", "__builtin_isnanf", "__builtin_isnanl"):
                return self._codegen_builtin_isnan(node)
            if callee in ("__builtin_isfinite", "__builtin_finite"):
                return self._codegen_builtin_isfinite(node)
            if callee in ("__builtin_isinf", "__builtin_isinff", "__builtin_isinfl"):
                return self._codegen_builtin_isinf(node)
            if callee == "__builtin_signbit":
                return self._codegen_builtin_signbit(node)
            if callee == "__builtin_isunordered":
                return self._codegen_builtin_isunordered(node)
            if callee == "__builtin_isless":
                return self._codegen_builtin_ordered_compare(node, "<", "isless")
            if callee == "__builtin_islessequal":
                return self._codegen_builtin_ordered_compare(
                    node, "<=", "islessequal"
                )
            if callee == "__builtin_isgreater":
                return self._codegen_builtin_ordered_compare(node, ">", "isgreater")
            if callee == "__builtin_isgreaterequal":
                return self._codegen_builtin_ordered_compare(
                    node, ">=", "isgreaterequal"
                )
            if callee == "__builtin_islessgreater":
                return self._codegen_builtin_islessgreater(node)
            if callee == "__builtin_copysign":
                return self._codegen_builtin_copysign(node, _double)
            if callee == "__builtin_copysignf":
                return self._codegen_builtin_copysign(node, _float)
            if callee == "__builtin_copysignl":
                return self._codegen_builtin_copysign(node, _double)
            if callee == "__sync_synchronize":
                return self._codegen_builtin_sync_synchronize(node)
            if callee == "__sync_fetch_and_add":
                return self._codegen_builtin_sync_fetch_and_add(node)
            if callee == "__sync_bool_compare_and_swap":
                return self._codegen_builtin_sync_bool_compare_and_swap(node)
            if callee == "__atomic_load_n":
                return self._codegen_builtin_atomic_load(node)
            if callee == "__atomic_store_n":
                return self._codegen_builtin_atomic_store(node)
        else:
            # Calling function pointer in struct: s.fn(args)
            call_args = []
            arg_nodes = []
            if node.args:
                arg_nodes = list(node.args.exprs)
                call_args = [self.codegen(arg)[0] for arg in arg_nodes]
            fp_val, _ = self.codegen(node.name)
            if isinstance(fp_val.type, ir.PointerType) and isinstance(
                fp_val.type.pointee, ir.FunctionType
            ):
                # Coerce args to match function pointer param types
                ftype = fp_val.type.pointee
                coerced = []
                for j, a in enumerate(call_args):
                    arg_node = arg_nodes[j] if j < len(arg_nodes) else None
                    if j < len(ftype.args):
                        coerced.append(
                            self._coerce_arg(a, ftype.args[j], arg_node=arg_node)
                        )
                    else:
                        coerced.append(
                            self._default_arg_promotion(a, arg_node=arg_node)
                        )
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

        try:
            _, callee_func = self.lookup(callee)
        except KeyError:
            _, callee_func = self._declare_implicit_function(
                callee,
                call_arg_count=len(node.args.exprs) if node.args else 0,
            )

        call_args = []
        arg_nodes = []
        if node.args:
            arg_nodes = list(node.args.exprs)
            call_args = [self.codegen(arg)[0] for arg in arg_nodes]

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
                        self._coerce_arg(
                            a,
                            ftype.args[j],
                            arg_node=arg_nodes[j] if j < len(arg_nodes) else None,
                        )
                        if j < len(ftype.args)
                        else self._default_arg_promotion(
                            a,
                            arg_node=arg_nodes[j] if j < len(arg_nodes) else None,
                        )
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
        converted = self._convert_call_args(
            call_args, callee_func, arg_nodes=arg_nodes
        )

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

    def _builtin_va_list_storage(self, expr):
        value, addr = self.codegen(expr)
        storage = addr if addr is not None else value
        if not isinstance(getattr(storage, "type", None), ir.PointerType):
            return None
        return storage

    def _codegen_builtin_va_start(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int64_t, 0), None
        ap_addr = self._builtin_va_list_storage(node.args.exprs[0])
        if ap_addr is None:
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
        ap_addr = self._builtin_va_list_storage(node.args.exprs[0])
        if ap_addr is None:
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
        dst_addr = self._builtin_va_list_storage(node.args.exprs[0])
        src_addr = self._builtin_va_list_storage(node.args.exprs[1])
        if dst_addr is None:
            return ir.Constant(int64_t, 0), None
        if src_addr is None:
            return ir.Constant(int64_t, 0), None
        src_val = self._safe_load(src_addr)
        dst_pointee = dst_addr.type.pointee
        if src_val.type != dst_pointee:
            src_val = self._implicit_convert(src_val, dst_pointee)
        self._safe_store(src_val, dst_addr)
        return ir.Constant(int64_t, 0), None

    def _codegen_aggregate_va_arg(self, ap_addr, aggregate_type):
        if ap_addr is None or not isinstance(getattr(ap_addr, "type", None), ir.PointerType):
            return None, None

        current_ap = self._safe_load(ap_addr)
        if not isinstance(getattr(current_ap, "type", None), ir.PointerType):
            return None, None

        src_ptr = current_ap if current_ap.type == voidptr_t else self.builder.bitcast(
            current_ap, voidptr_t, name="vaargsrc"
        )

        value_size = self._ir_type_size(aggregate_type)
        slot_size = self._align_up(value_size, 8)
        aggregate_align = max(1, self._ir_type_align(aggregate_type))

        temp = self._alloca_in_entry(
            aggregate_type, f"vaarg_agg_{self._vaarg_counter}"
        )
        try:
            temp.align = aggregate_align
        except Exception:
            pass
        dst_ptr = self.builder.bitcast(temp, voidptr_t, name="vaargdst")

        memcpy = self._get_or_declare_intrinsic(
            "llvm.memcpy.p0.p0.i64",
            ir.VoidType(),
            [voidptr_t, voidptr_t, int64_t, ir.IntType(1)],
        )
        self.builder.call(
            memcpy,
            [
                dst_ptr,
                src_ptr,
                ir.Constant(int64_t, value_size),
                ir.Constant(ir.IntType(1), 0),
            ],
            name=f"vaargcpy.{self._vaarg_counter}",
        )

        next_ptr = self.builder.gep(
            src_ptr,
            [ir.Constant(int64_t, slot_size)],
            inbounds=True,
            name=f"vaargnext.{self._vaarg_counter}",
        )
        stored_next_ptr = next_ptr
        if ap_addr.type.pointee != next_ptr.type:
            stored_next_ptr = self.builder.bitcast(
                next_ptr,
                ap_addr.type.pointee,
                name=f"vaargnextcast.{self._vaarg_counter}",
            )
        self._safe_store(stored_next_ptr, ap_addr)

        return self._safe_load(temp), temp

    def _flatten_homogeneous_floating_members(self, ir_type):
        if self._is_floating_ir_type(ir_type):
            return [ir_type]

        if isinstance(ir_type, ir.ArrayType):
            nested = self._flatten_homogeneous_floating_members(ir_type.element)
            if nested is None:
                return None
            return nested * ir_type.count

        if not _is_struct_ir_type(ir_type):
            return None

        flattened = []
        for member_type in self._aggregate_member_ir_types(ir_type):
            nested = self._flatten_homogeneous_floating_members(member_type)
            if nested is None:
                return None
            flattened.extend(nested)

        if not flattened:
            return None

        first = flattened[0]
        if not all(str(member_type) == str(first) for member_type in flattened):
            return None
        return flattened

    def _coerce_variadic_aggregate_arg(self, arg):
        if not self._is_aggregate_ir_type(arg.type):
            return arg

        source_type = arg.type
        source_size = self._ir_type_size(source_type)
        source_align = max(1, self._ir_type_align(source_type))

        source_tmp = self._alloca_in_entry(source_type, "varargagg.src")
        try:
            source_tmp.align = source_align
        except Exception:
            pass
        self._safe_store(arg, source_tmp)
        source_ptr = self.builder.bitcast(source_tmp, voidptr_t, name="varargaggsrc")

        hfa_members = self._flatten_homogeneous_floating_members(source_type)
        if hfa_members and 1 <= len(hfa_members) <= 4:
            packed_type = ir.ArrayType(hfa_members[0], len(hfa_members))
            if self._ir_type_size(packed_type) == source_size:
                packed_align = max(1, self._ir_type_align(packed_type))
                packed_tmp = self._alloca_in_entry(packed_type, "varargagg.hfa")
                try:
                    packed_tmp.align = packed_align
                except Exception:
                    pass
                packed_ptr = self.builder.bitcast(
                    packed_tmp, voidptr_t, name="varargagghfaptr"
                )
                memcpy = self._get_or_declare_intrinsic(
                    "llvm.memcpy.p0.p0.i64",
                    ir.VoidType(),
                    [voidptr_t, voidptr_t, int64_t, ir.IntType(1)],
                )
                self.builder.call(
                    memcpy,
                    [
                        packed_ptr,
                        source_ptr,
                        ir.Constant(int64_t, source_size),
                        ir.Constant(ir.IntType(1), 0),
                    ],
                    name="varargagg.hfacpy",
                )
                return self._safe_load(packed_tmp)

        chunk_count = max(1, self._align_up(source_size, 8) // 8)
        packed_type = ir.ArrayType(int64_t, chunk_count)
        packed_tmp = self._alloca_in_entry(packed_type, "varargagg.i64")
        try:
            packed_tmp.align = 8
        except Exception:
            pass
        self._safe_store(ir.Constant(packed_type, None), packed_tmp)
        packed_ptr = self.builder.bitcast(packed_tmp, voidptr_t, name="varargaggi64ptr")
        memcpy = self._get_or_declare_intrinsic(
            "llvm.memcpy.p0.p0.i64",
            ir.VoidType(),
            [voidptr_t, voidptr_t, int64_t, ir.IntType(1)],
        )
        self.builder.call(
            memcpy,
            [
                packed_ptr,
                source_ptr,
                ir.Constant(int64_t, source_size),
                ir.Constant(ir.IntType(1), 0),
            ],
            name="varargagg.i64cpy",
        )
        return self._safe_load(packed_tmp)

    def _codegen_builtin_expect(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int64_t, 0), None
        value, _ = self.codegen(node.args.exprs[0])
        return value, None

    def _codegen_builtin_trap(self, node):
        intrinsic = self._get_or_declare_intrinsic("llvm.trap", ir.VoidType(), [])
        self.builder.call(intrinsic, [])
        if self.builder is not None and not self.builder.block.is_terminated:
            self.builder.unreachable()
            dead_bb = self.function.append_basic_block(name="after_trap")
            self.builder.position_at_end(dead_bb)
        return ir.Constant(int64_t, 0), None

    def _codegen_builtin_classify_type(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 1), None
        arg_node = node.args.exprs[0]
        expr_key = self._generic_expr_type_key(arg_node)
        if isinstance(expr_key, tuple) and expr_key and expr_key[0] == "base":
            names = expr_key[2]
            if "float" in names or "double" in names:
                return ir.Constant(int32_t, 8), None
            return ir.Constant(int32_t, 1), None
        value, _ = self.codegen(arg_node)
        if self._is_floating_ir_type(getattr(value, "type", None)):
            return ir.Constant(int32_t, 8), None
        return ir.Constant(int32_t, 1), None

    def _codegen_builtin_unreachable(self, node):
        if self.builder is not None and not self.builder.block.is_terminated:
            self.builder.unreachable()
            dead_bb = self.function.append_basic_block(name="after_unreachable")
            self.builder.position_at_end(dead_bb)
        return ir.Constant(int64_t, 0), None

    def _codegen_builtin_alloca(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(voidptr_t, None), None

        size_val, _ = self.codegen(node.args.exprs[0])
        if not isinstance(getattr(size_val, "type", None), ir.IntType):
            size_val = self._implicit_convert(size_val, int64_t)

        return self.builder.alloca(int8_t, size=size_val, name="builtinalloca"), None

    def _codegen_builtin_frame_address(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(voidptr_t, None), None
        try:
            level = int(self._eval_const_expr(node.args.exprs[0]))
        except Exception:
            level = 0
        if level != 0 or self.builder is None:
            return ir.Constant(voidptr_t, None), None
        if self._frame_address_marker is None:
            self._frame_address_marker = self._alloca_in_entry(
                int8_t, "__builtin_frame_address"
            )
        if self._frame_address_marker.type == voidptr_t:
            return self._frame_address_marker, None
        return self.builder.bitcast(
            self._frame_address_marker, voidptr_t, name="frameaddrcast"
        ), None

    def _codegen_builtin_ffs(self, node, width):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None

        arg, _ = self.codegen(node.args.exprs[0])
        arg_type = ir.IntType(width)
        if not isinstance(getattr(arg, "type", None), ir.IntType) or arg.type != arg_type:
            arg = self._implicit_convert(arg, arg_type)

        zero = ir.Constant(arg_type, 0)
        is_zero = self.builder.icmp_unsigned("==", arg, zero, name="ffsiszero")
        intrinsic = self._get_or_declare_intrinsic(
            f"llvm.cttz.i{width}",
            arg_type,
            [arg_type, ir.IntType(1)],
        )
        cttz = self.builder.call(
            intrinsic,
            [arg, ir.Constant(ir.IntType(1), 0)],
            name="ffstmp",
        )
        if cttz.type != int32_t:
            cttz = self.builder.trunc(cttz, int32_t, name="ffsi32")
        plus_one = self.builder.add(cttz, ir.Constant(int32_t, 1), name="ffsplusone")
        result = self.builder.select(is_zero, ir.Constant(int32_t, 0), plus_one)
        self._clear_unsigned(result)
        return result, None

    def _coerce_builtin_float_arg(self, expr, target_type=None):
        value, _ = self.codegen(expr)
        if target_type is None:
            if isinstance(getattr(value, "type", None), ir.FloatType):
                target_type = _float
            else:
                target_type = _double
        if not self._is_floating_ir_type(getattr(value, "type", None)):
            value = self._implicit_convert(value, target_type)
        elif value.type != target_type:
            value = self._implicit_convert(value, target_type)
        return value

    def _materialize_compound_literal(self, ast_type, init_node):
        dest_ir_type = self._compound_literal_ir_type(ast_type, init_node)
        if self.builder is None:
            return self._build_const_init(init_node, dest_ir_type), None
        tmp_ptr = self._alloca_in_entry(dest_ir_type, "compoundlit")
        self._safe_store(self._zero_initializer(dest_ir_type), tmp_ptr)
        self._init_runtime_value(tmp_ptr, dest_ir_type, init_node)
        value = self._safe_load(tmp_ptr, name="compoundlitval")
        self._tag_value_from_decl_type(value, ast_type)
        return value, tmp_ptr

    def _builtin_bool_to_i32(self, value, name):
        return self.builder.zext(value, int32_t, name=name), None

    def _codegen_builtin_isnan(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None
        value = self._coerce_builtin_float_arg(node.args.exprs[0])
        result = self.builder.fcmp_unordered("!=", value, value, name="isnan")
        return self._builtin_bool_to_i32(result, "isnani32")

    def _codegen_builtin_isinf(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None
        value = self._coerce_builtin_float_arg(node.args.exprs[0])
        pos_inf = ir.Constant(value.type, float("inf"))
        neg_inf = ir.Constant(value.type, float("-inf"))
        is_pos = self.builder.fcmp_ordered("==", value, pos_inf, name="isinfpos")
        is_neg = self.builder.fcmp_ordered("==", value, neg_inf, name="isinfneg")
        return self._builtin_bool_to_i32(
            self.builder.or_(is_pos, is_neg, name="isinf"),
            "isinfi32",
        )

    def _codegen_builtin_isfinite(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None
        value = self._coerce_builtin_float_arg(node.args.exprs[0])
        not_nan = self.builder.fcmp_ordered("==", value, value, name="isfinitenotnan")
        pos_inf = self.builder.fcmp_ordered(
            "==", value, ir.Constant(value.type, float("inf")), name="isfiniteposinf"
        )
        neg_inf = self.builder.fcmp_ordered(
            "==", value, ir.Constant(value.type, float("-inf")), name="isfiniteneginf"
        )
        is_inf = self.builder.or_(pos_inf, neg_inf, name="isfiniteisinf")
        result = self.builder.and_(
            not_nan,
            self.builder.not_(is_inf, name="isfinite_not_inf"),
            name="isfinite",
        )
        return self._builtin_bool_to_i32(result, "isfinitei32")

    def _codegen_builtin_signbit(self, node):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None
        value = self._coerce_builtin_float_arg(node.args.exprs[0])
        if isinstance(value.type, ir.FloatType):
            int_type = ir.IntType(32)
            sign_mask = ir.Constant(int_type, 0x80000000)
        else:
            int_type = ir.IntType(64)
            sign_mask = ir.Constant(int_type, 0x8000000000000000)
        bits = self.builder.bitcast(value, int_type, name="signbitbits")
        masked = self.builder.and_(bits, sign_mask, name="signbitmask")
        result = self.builder.icmp_unsigned(
            "!=", masked, ir.Constant(int_type, 0), name="signbit"
        )
        return self._builtin_bool_to_i32(result, "signbiti32")

    def _codegen_builtin_isunordered(self, node):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int32_t, 0), None
        lhs = self._coerce_builtin_float_arg(node.args.exprs[0])
        rhs = self._coerce_builtin_float_arg(node.args.exprs[1], lhs.type)
        lhs_nan = self.builder.fcmp_unordered("!=", lhs, lhs, name="lhsnan")
        rhs_nan = self.builder.fcmp_unordered("!=", rhs, rhs, name="rhsnan")
        result = self.builder.or_(lhs_nan, rhs_nan, name="isunord")
        return self._builtin_bool_to_i32(result, "isunordi32")

    def _codegen_builtin_ordered_compare(self, node, op, name):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int32_t, 0), None
        lhs = self._coerce_builtin_float_arg(node.args.exprs[0])
        rhs = self._coerce_builtin_float_arg(node.args.exprs[1], lhs.type)
        result = self.builder.fcmp_ordered(op, lhs, rhs, name=name)
        return self._builtin_bool_to_i32(result, f"{name}i32")

    def _codegen_builtin_islessgreater(self, node):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int32_t, 0), None
        lhs = self._coerce_builtin_float_arg(node.args.exprs[0])
        rhs = self._coerce_builtin_float_arg(node.args.exprs[1], lhs.type)
        result = self.builder.fcmp_ordered("!=", lhs, rhs, name="islessgreater")
        return self._builtin_bool_to_i32(result, "islessgreateri32")

    def _codegen_builtin_copysign(self, node, target_type):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(target_type, 0.0), None
        magnitude = self._coerce_builtin_float_arg(node.args.exprs[0], target_type)
        sign = self._coerce_builtin_float_arg(node.args.exprs[1], target_type)

        if isinstance(target_type, ir.FloatType):
            int_type = ir.IntType(32)
            sign_mask = ir.Constant(int_type, 0x80000000)
            value_mask = ir.Constant(int_type, 0x7FFFFFFF)
        else:
            int_type = ir.IntType(64)
            sign_mask = ir.Constant(int_type, 0x8000000000000000)
            value_mask = ir.Constant(int_type, 0x7FFFFFFFFFFFFFFF)

        magnitude_bits = self.builder.bitcast(
            magnitude, int_type, name="copysignmagbits"
        )
        sign_bits = self.builder.bitcast(sign, int_type, name="copysignsignbits")
        magnitude_bits = self.builder.and_(
            magnitude_bits, value_mask, name="copysignmag"
        )
        sign_bits = self.builder.and_(sign_bits, sign_mask, name="copysignsign")
        result_bits = self.builder.or_(
            magnitude_bits, sign_bits, name="copysignbits"
        )
        return self.builder.bitcast(result_bits, target_type, name="copysigntmp"), None

    def _codegen_builtin_overflow(self, node, operation):
        if not node.args or len(node.args.exprs) < 3:
            return ir.Constant(int32_t, 0), None

        lhs, _ = self.codegen(node.args.exprs[0])
        rhs, _ = self.codegen(node.args.exprs[1])
        out_ptr, _ = self.codegen(node.args.exprs[2])

        if not isinstance(getattr(out_ptr, "type", None), ir.PointerType):
            return ir.Constant(int32_t, 0), None

        result_type = out_ptr.type.pointee
        if not isinstance(result_type, ir.IntType):
            return ir.Constant(int32_t, 0), None

        lhs = self._implicit_convert(lhs, result_type)
        rhs = self._implicit_convert(rhs, result_type)

        is_unsigned = self._is_unsigned_val(lhs) or self._is_unsigned_val(rhs)
        if is_unsigned:
            self._tag_unsigned(lhs)
            self._tag_unsigned(rhs)

        intrinsic_prefix = {
            ("add", False): "sadd",
            ("add", True): "uadd",
            ("sub", False): "ssub",
            ("sub", True): "usub",
            ("mul", False): "smul",
            ("mul", True): "umul",
        }[(operation, is_unsigned)]
        pair_type = ir.LiteralStructType([result_type, ir.IntType(1)])
        intrinsic = self._get_or_declare_intrinsic(
            f"llvm.{intrinsic_prefix}.with.overflow.i{result_type.width}",
            pair_type,
            [result_type, result_type],
        )
        pair = self.builder.call(intrinsic, [lhs, rhs], name=f"{operation}ovtmp")
        result = self.builder.extract_value(pair, 0, name=f"{operation}ovval")
        overflow = self.builder.extract_value(pair, 1, name=f"{operation}ovflag")
        if is_unsigned:
            self._tag_unsigned(result)
        self._safe_store(result, out_ptr)

        overflow_i32 = self.builder.zext(overflow, int32_t, name=f"{operation}ovi32")
        self._clear_unsigned(overflow_i32)
        return overflow_i32, None

    def _codegen_builtin_sync_synchronize(self, node):
        self.builder.fence("seq_cst")
        return ir.Constant(int64_t, 0), None

    def _codegen_builtin_sync_fetch_and_add(self, node):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int64_t, 0), None
        ptr, _ = self.codegen(node.args.exprs[0])
        value, _ = self.codegen(node.args.exprs[1])
        if not isinstance(getattr(ptr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        pointee_type = ptr.type.pointee
        if not isinstance(pointee_type, ir.IntType):
            return ir.Constant(int64_t, 0), None
        if value.type != pointee_type:
            value = self._implicit_convert(value, pointee_type)
        result = self.builder.atomic_rmw(
            "add", ptr, value, "seq_cst", name="sync.fetch_add"
        )
        if self._is_unsigned_pointee(ptr):
            self._tag_unsigned(result)
        return result, ptr

    def _codegen_builtin_sync_bool_compare_and_swap(self, node):
        if not node.args or len(node.args.exprs) < 3:
            return ir.Constant(int32_t, 0), None
        ptr, _ = self.codegen(node.args.exprs[0])
        expected, _ = self.codegen(node.args.exprs[1])
        desired, _ = self.codegen(node.args.exprs[2])
        if not isinstance(getattr(ptr, "type", None), ir.PointerType):
            return ir.Constant(int32_t, 0), None
        pointee_type = ptr.type.pointee
        if expected.type != pointee_type:
            expected = self._implicit_convert(expected, pointee_type)
        if desired.type != pointee_type:
            desired = self._implicit_convert(desired, pointee_type)
        pair = self.builder.cmpxchg(
            ptr,
            expected,
            desired,
            "seq_cst",
            "seq_cst",
            name="sync.cmpxchg",
        )
        success = self.builder.extract_value(pair, 1, name="sync.cas.success")
        result = self.builder.zext(success, int32_t, name="sync.cas.i32")
        self._clear_unsigned(result)
        return result, None

    def _codegen_builtin_bswap(self, node, width):
        if not node.args or not node.args.exprs:
            return ir.Constant(ir.IntType(width), 0), None

        arg, _ = self.codegen(node.args.exprs[0])
        arg_type = ir.IntType(width)
        returns_unsigned = self._is_unsigned_val(arg)

        if not isinstance(getattr(arg, "type", None), ir.IntType) or arg.type != arg_type:
            arg = self._implicit_convert(arg, arg_type)

        mask = ir.Constant(arg_type, 0xFF)
        result = ir.Constant(arg_type, 0)
        byte_count = width // 8

        for index in range(byte_count):
            piece = arg
            if index:
                piece = self.builder.lshr(
                    piece,
                    ir.Constant(arg_type, index * 8),
                    name=f"bswapshr{index}",
                )
            piece = self.builder.and_(piece, mask, name=f"bswapmask{index}")
            shift = (byte_count - 1 - index) * 8
            if shift:
                piece = self.builder.shl(
                    piece,
                    ir.Constant(arg_type, shift),
                    name=f"bswapshl{index}",
                )
            result = self.builder.or_(result, piece, name=f"bswapor{index}")

        return self._extend_call_result(result, returns_unsigned=returns_unsigned), None

    def _codegen_builtin_rotate(self, node, width, direction):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(ir.IntType(width), 0), None

        value, _ = self.codegen(node.args.exprs[0])
        amount, _ = self.codegen(node.args.exprs[1])
        value_type = ir.IntType(width)
        returns_unsigned = self._is_unsigned_val(value)

        if (
            not isinstance(getattr(value, "type", None), ir.IntType)
            or value.type != value_type
        ):
            value = self._implicit_convert(value, value_type)
        if (
            not isinstance(getattr(amount, "type", None), ir.IntType)
            or amount.type != value_type
        ):
            amount = self._implicit_convert(amount, value_type)

        mask = ir.Constant(value_type, width - 1)
        amount = self.builder.and_(amount, mask, name=f"rot{direction}amt")
        inverse = self.builder.sub(
            ir.Constant(value_type, 0), amount, name=f"rot{direction}invtmp"
        )
        inverse = self.builder.and_(inverse, mask, name=f"rot{direction}inv")

        if direction == "left":
            lhs = self.builder.shl(value, amount, name=f"rot{direction}lhs")
            rhs = self.builder.lshr(value, inverse, name=f"rot{direction}rhs")
        else:
            lhs = self.builder.lshr(value, amount, name=f"rot{direction}lhs")
            rhs = self.builder.shl(value, inverse, name=f"rot{direction}rhs")

        result = self.builder.or_(lhs, rhs, name=f"rot{direction}")
        return self._extend_call_result(result, returns_unsigned=returns_unsigned), None

    def _codegen_builtin_bitcount(self, node, width, intrinsic_base):
        if not node.args or not node.args.exprs:
            return ir.Constant(int32_t, 0), None

        arg, _ = self.codegen(node.args.exprs[0])
        arg_type = ir.IntType(width)
        if not isinstance(getattr(arg, "type", None), ir.IntType) or arg.type != arg_type:
            arg = self._implicit_convert(arg, arg_type)

        intrinsic = self._get_or_declare_intrinsic(
            f"llvm.{intrinsic_base}.i{width}",
            arg_type,
            [arg_type, ir.IntType(1)],
        )
        result = self.builder.call(
            intrinsic,
            [arg, ir.Constant(ir.IntType(1), 0)],
            name=f"{intrinsic_base}tmp",
        )
        if result.type != int32_t:
            result = self.builder.trunc(result, int32_t, name=f"{intrinsic_base}i32")
        self._clear_unsigned(result)
        return result, None

    def _atomic_ordering(self, node, is_store):
        try:
            value = self._eval_const_expr(node)
        except Exception:
            return "monotonic"
        if is_store:
            return {
                0: "monotonic",  # __ATOMIC_RELAXED
                3: "release",    # __ATOMIC_RELEASE
                4: "release",    # __ATOMIC_ACQ_REL
                5: "seq_cst",    # __ATOMIC_SEQ_CST
            }.get(value, "monotonic")
        return {
            0: "monotonic",  # __ATOMIC_RELAXED
            1: "acquire",    # __ATOMIC_CONSUME
            2: "acquire",    # __ATOMIC_ACQUIRE
            5: "seq_cst",    # __ATOMIC_SEQ_CST
        }.get(value, "monotonic")

    def _codegen_builtin_atomic_load(self, node):
        if not node.args or len(node.args.exprs) < 2:
            return ir.Constant(int64_t, 0), None
        ptr, _ = self.codegen(node.args.exprs[0])
        if not isinstance(getattr(ptr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        pointee_type = ptr.type.pointee
        align = max(1, self._ir_type_align(pointee_type))
        ordering = self._atomic_ordering(node.args.exprs[1], is_store=False)
        result = self.builder.load_atomic(ptr, ordering, align)
        if self._is_unsigned_pointee(ptr):
            self._tag_unsigned(result)
        return result, ptr

    def _codegen_builtin_atomic_store(self, node):
        if not node.args or len(node.args.exprs) < 3:
            return ir.Constant(int64_t, 0), None
        ptr, _ = self.codegen(node.args.exprs[0])
        value, _ = self.codegen(node.args.exprs[1])
        if not isinstance(getattr(ptr, "type", None), ir.PointerType):
            return ir.Constant(int64_t, 0), None
        pointee_type = ptr.type.pointee
        if value.type != pointee_type:
            value = self._implicit_convert(value, pointee_type)
        align = max(1, self._ir_type_align(pointee_type))
        ordering = self._atomic_ordering(node.args.exprs[2], is_store=True)
        self.builder.store_atomic(value, ptr, ordering, align)
        return ir.Constant(int64_t, 0), None

    def _convert_call_args(self, call_args, callee_func, arg_nodes=None):
        """Convert call arguments to match function parameter types."""
        converted = []
        param_types = [p.type for p in callee_func.args]

        for i, arg in enumerate(call_args):
            arg_node = arg_nodes[i] if arg_nodes and i < len(arg_nodes) else None
            if i < len(param_types):
                expected = param_types[i]
                arg = self._coerce_arg(arg, expected, arg_node=arg_node)
            else:
                arg = self._default_arg_promotion(arg, arg_node=arg_node)
            converted.append(arg)
        return converted

    def _default_arg_promotion(self, arg, arg_node=None):
        """Apply C default argument promotions for variadic calls."""
        if arg is None or isinstance(getattr(arg, "type", None), ir.VoidType):
            return ir.Constant(int64_t, 0)
        arg = self._decay_array_expr_to_pointer(arg_node, arg, "varargarraydecay")
        if isinstance(arg.type, ir.ArrayType):
            return self._implicit_convert(arg, ir.PointerType(arg.type.element))
        if self._is_aggregate_ir_type(arg.type):
            return self._coerce_variadic_aggregate_arg(arg)
        if isinstance(arg.type, ir.FloatType):
            return self.builder.fpext(arg, ir.DoubleType())
        if isinstance(arg.type, ir.IntType) and arg.type.width < int32_t.width:
            return self._integer_promotion(arg)
        return arg

    def _coerce_arg(self, arg, expected, arg_node=None):
        """Coerce a single argument to the expected type."""
        if arg is None or isinstance(getattr(arg, "type", None), ir.VoidType):
            return (
                ir.Constant(expected, None)
                if isinstance(expected, ir.PointerType)
                else ir.Constant(int64_t, 0)
            )
        arg = self._decay_array_expr_to_pointer(arg_node, arg, "argarraydecay")
        if arg.type == expected:
            return arg
        # Array values decay to pointers at the call site; do not try to
        # synthesize globals from function-local SSA array values here.
        if isinstance(arg.type, ir.ArrayType) and isinstance(expected, ir.PointerType):
            return self._implicit_convert(arg, expected)
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

        if node.name is not None:
            self._record_decl_ast_type(node.name, node.type)

        # Standalone tag definitions such as:
        #   struct S { ... };
        #   union U { ... };
        #   enum E { ... };
        # do not declare objects. Register the aggregate/enum type and stop.
        if node.name is None and isinstance(node.type, c_ast.TypeDecl):
            inner = node.type.type
            if isinstance(inner, c_ast.Struct):
                self.codegen_Struct(inner)
                return None, None
            if isinstance(inner, c_ast.Union):
                self.codegen_Union(inner)
                return None, None
            if isinstance(inner, c_ast.Enum):
                self.codegen_Enum(inner)
                return None, None

        # Static local objects: stored as internal globals with function-scoped names
        is_static = node.storage and "static" in node.storage
        if is_static and not self.in_global and not isinstance(node.type, c_ast.FuncDecl):
            ir_type = self._static_local_ir_type(node.type, init_node=node.init)
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
            ir_type = self._extern_decl_ir_type(node.name, node.type)
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
            function_type, ir_type = self._build_function_ir_type(node.type)
            symbol_name = funcname
            if self.in_global:
                function_type = self._preferred_file_scope_function_ir_type(
                    funcname,
                    function_type,
                    getattr(node.type, "args", None) is not None,
                )
                symbol_name = self._register_file_scope_function(
                    funcname,
                    function_type,
                    storage=node.storage,
                    funcspec=node.funcspec,
                    is_definition=False,
                )
            # Skip if already exists (module globals, libc, or env)
            existing = self.module.globals.get(symbol_name)
            if existing:
                if self._func_decl_returns_unsigned(node.type):
                    self._mark_unsigned_return(existing)
                self.define(funcname, (None, existing))
                return None, None
            try:
                func = ir.Function(
                    self.module,
                    function_type,
                    name=symbol_name,
                )
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
                if isinstance(resolved, ir.FunctionType):
                    funcname = node.type.declname
                    symbol_name = funcname
                    if self.in_global:
                        symbol_name = self._register_file_scope_function(
                            funcname,
                            resolved,
                            storage=node.storage,
                            funcspec=node.funcspec,
                            is_definition=False,
                        )
                    existing = self.module.globals.get(symbol_name)
                    if existing:
                        self.define(funcname, (resolved.return_type, existing))
                        return None, None
                    try:
                        func = ir.Function(self.module, resolved, name=symbol_name)
                        self.define(funcname, (resolved.return_type, func))
                    except Exception:
                        existing = self.module.globals.get(symbol_name)
                        if existing:
                            self.define(funcname, (resolved.return_type, existing))
                    return None, None
                if isinstance(resolved, (ir.PointerType, ir.ArrayType)) or _is_struct_ir_type(
                    resolved
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
                            if isinstance(node.init, c_ast.InitList) and (
                                getattr(ir_type, "is_union", False)
                                or _is_struct_ir_type(ir_type)
                            ):
                                self._safe_store(self._zero_initializer(ir_type), ret)
                                self._init_runtime_aggregate(ret, node.init, ir_type)
                            else:
                                init_val, _ = self.codegen(node.init)
                                if init_val is not None:
                                    if init_val.type != ir_type:
                                        init_val = self._implicit_convert(
                                            init_val, ir_type
                                        )
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
                if node.type.type.name is None or getattr(node.type.type, "decls", None) is not None:
                    struct_type = codegen_fn(node.type.type)
                    if self.in_global and node.name and node.type.type.name is None:
                        # Preserve the repository's legacy behavior for
                        # file-scope anonymous aggregates declared as:
                        #   struct { ... } Name;
                        # Existing tests rely on a later `struct Name`
                        # resolving to the same aggregate type.
                        self.define(self._tag_type_key(node.name), (struct_type, None))
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
                            if isinstance(node.init, c_ast.InitList):
                                self._safe_store(
                                    self._zero_initializer(struct_type), ret
                                )
                                self._init_runtime_aggregate(
                                    ret, node.init, struct_type
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
                    struct_type = self.env[
                        self._tag_type_key(node.type.type.name)
                    ][0]
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
                            if isinstance(node.init, c_ast.InitList):
                                self._safe_store(
                                    self._zero_initializer(struct_type), ret
                                )
                                self._init_runtime_aggregate(
                                    ret, node.init, struct_type
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
                if isinstance(node.type.type, c_ast.IdentifierType):
                    type_str = node.type.type.names
                    is_unsigned = self._is_unsigned_type_names(type_str)
                    ir_type = self._get_ir_type(type_str)
                    type_str = self._resolve_type_str(type_str)
                    if isinstance(type_str, ir.Type):
                        type_str = "int"  # fallback for alloca name
                else:
                    if isinstance(node.type.type, c_ast.Enum):
                        self.codegen_Enum(node.type.type)
                    type_str = "int"
                    is_unsigned = False
                    ir_type = self._resolve_ast_type(node.type)
                if self._is_floating_ir_type(ir_type):
                    init = 0.0
                else:
                    init = 0

                if node.init is not None:
                    if self.in_global:
                        init_val = self._build_const_init(node.init, ir_type)
                    else:
                        var_addr, var_ir_type = self.create_entry_block_alloca(
                            node.name, type_str, 1, storage=node.storage
                        )
                        if is_unsigned:
                            self._mark_unsigned(var_addr)
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
                    if node.init is None:
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
            array_list = []
            array_node = node.type
            var_addr = None
            var_ir_type = None
            elem_ir_type = None
            write_initializer = True
            inferred_elem_type = None
            try:
                if isinstance(node.type.type, c_ast.ArrayDecl):
                    inferred_elem_type = self._build_array_ir_type(node.type.type)
                else:
                    inferred_elem_type = self._resolve_ast_type(node.type.type)
            except Exception:
                inferred_elem_type = None
            inferred_top_dim = self._infer_array_count_from_initializer(
                node.init, inferred_elem_type
            )
            while True:
                array_next_type = array_node.type
                if isinstance(array_next_type, c_ast.TypeDecl):
                    dynamic_dim_val = None
                    if array_node.dim:
                        try:
                            dim_val = self._eval_dim(array_node.dim)
                        except CodegenError:
                            dim_val = None
                            dynamic_dim_val, _ = self.codegen(array_node.dim)
                    else:
                        dim_val = 0
                    if (
                        dim_val == 0
                        and array_node is node.type
                        and inferred_top_dim is not None
                    ):
                        dim_val = inferred_top_dim
                    if dynamic_dim_val is not None:
                        if self.in_global or array_node is not node.type:
                            raise CodegenError(
                                "only one-dimensional local VLAs are supported"
                            )
                        elem_ir_type = self._resolve_ast_type(array_next_type)
                        if not isinstance(dynamic_dim_val.type, ir.IntType):
                            dynamic_dim_val = self.builder.fptoui(
                                dynamic_dim_val, ir.IntType(64)
                            )
                        elif dynamic_dim_val.type.width != 64:
                            dynamic_dim_val = self._implicit_convert(
                                dynamic_dim_val, ir.IntType(64)
                            )
                        var_addr = self.builder.alloca(
                            elem_ir_type,
                            size=dynamic_dim_val,
                            name=node.name,
                        )
                        self.define(node.name, (ir.PointerType(elem_ir_type), var_addr))
                        self._mark_vla_binding(var_addr)
                        if self._has_unsigned_scalar_pointee(node.type):
                            self._mark_unsigned_pointee(var_addr)
                        return None, var_addr
                    array_list.append(dim_val)
                    elem_ir_type = self._resolve_ast_type(array_next_type)
                    break

                elif isinstance(array_next_type, c_ast.ArrayDecl):
                    dim_val = self._eval_dim(array_node.dim)
                    if (
                        dim_val == 0
                        and array_node is node.type
                        and inferred_top_dim is not None
                    ):
                        dim_val = inferred_top_dim
                    array_list.append(dim_val)
                    array_node = array_next_type
                    continue
                elif isinstance(array_next_type, c_ast.PtrDecl):
                    # Array of pointers: int *arr[3]
                    dim = self._eval_dim(array_node.dim)
                    if (
                        dim == 0
                        and array_node is node.type
                        and inferred_top_dim is not None
                    ):
                        dim = inferred_top_dim
                    elem_ir = self._resolve_ast_type(array_next_type)
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
                    self._safe_store(self._zero_initializer(var_ir_type), var_addr)
                    self._init_runtime_value(var_addr, var_ir_type, node.init)
                elif self._is_array_string_initializer(node.init, var_ir_type):
                    self._safe_store(self._zero_initializer(var_ir_type), var_addr)
                    data = self._string_literal_data(node.init)
                    idx0 = ir.Constant(ir.IntType(32), 0)
                    for i, value in enumerate(data[: var_ir_type.count]):
                        elem_ptr = self.builder.gep(
                            var_addr,
                            [idx0, ir.Constant(ir.IntType(32), i)],
                            inbounds=True,
                        )
                        self.builder.store(ir.Constant(elem_ir_type, value), elem_ptr)
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
                    elif isinstance(sub_next_type.type, c_ast.Enum):
                        self.codegen_Enum(sub_next_type.type)
                        resolved_pointee_type = int32_t
                        type_str = "int"
                    else:
                        type_str = sub_next_type.type.names
                        resolved = self._get_ir_type(type_str)
                        if isinstance(resolved, ir.Type):
                            resolved_pointee_type = resolved
                        if _is_struct_ir_type(resolved):
                            type_str = "struct"
                    break
                elif isinstance(sub_next_type, c_ast.PtrDecl):
                    point_level += 1
                    sub_node = sub_next_type
                    continue
                elif isinstance(sub_next_type, c_ast.ArrayDecl):
                    resolved_pointee_type = self._build_array_ir_type(sub_next_type)
                    type_str = "array"
                    break
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
                        # For global scope, set as initializer directly
                        if self.in_global and isinstance(var_addr, ir.GlobalVariable):
                            # NULL (i64 0) → null pointer of correct type
                            if (isinstance(init_val.type, ir.IntType)
                                    and isinstance(init_val, ir.Constant)
                                    and init_val.constant == 0):
                                init_val = ir.Constant(
                                    var_addr.value_type, None
                                )
                            var_addr.initializer = init_val
                        else:
                            if (
                                isinstance(init_val, ir.Constant)
                                and isinstance(init_val.type, ir.IntType)
                                and init_val.constant == 0
                            ):
                                init_val = ir.Constant(func_ir_type, None)
                            elif init_val.type != func_ir_type:
                                init_val = self._implicit_convert(
                                    init_val, func_ir_type
                                )
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
                    init_val = self._decay_array_expr_to_pointer(
                        node.init, init_val, f"{node.name}.initdecay"
                    )
                    if isinstance(init_val.type, ir.ArrayType) and isinstance(
                        var_ir_type, ir.PointerType
                    ):
                        init_val = self._implicit_convert(init_val, var_ir_type)
                    elif init_val.type != var_ir_type:
                        init_val = self._implicit_convert(init_val, var_ir_type)
                    self._safe_store(init_val, var_addr)
        else:
            return None, None

        return None, var_addr

    def codegen_ID(self, node):
        if node.name in {"__func__", "__FUNCTION__", "__PRETTY_FUNCTION__"}:
            func_name = self._function_display_name or (
                self.function.name if self.function is not None else node.name
            )
            gv = self._make_global_string_constant(func_name, name_hint="funcname")
            ptr = self._const_pointer_to_first_elem(gv, cstring)
            node.ir_type = cstring
            return ptr, gv

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
        if self._is_vla_binding(var):
            if self._is_unsigned_pointee_binding(var):
                self._tag_unsigned_pointee(var)
            return var, var
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
                self._implicit_convert(name_ir, ir.PointerType(int8_t))
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
            if isinstance(func_ret_type, ir.VoidType):
                self.builder.ret_void()
                return None, None
            if retval.type != func_ret_type:
                retval = self._implicit_convert(retval, func_ret_type)
            self.builder.ret(retval)
        return None, None

    def codegen_Compound(self, node):
        return self._codegen_compound_items(node, use_new_scope=True)

    def codegen_StmtExpr(self, node):
        result = ir.Constant(int64_t, 0)
        result_ptr = None
        with self.new_scope():
            items = list(getattr(node.stmt, "block_items", None) or [])
            for stmt in items:
                if self.builder and self.builder.block.is_terminated:
                    if self._switch_contexts and (
                        isinstance(stmt, (c_ast.Case, c_ast.Default))
                        or self._stmt_contains_switch_label(stmt)
                    ):
                        current = self.codegen(stmt)
                    elif isinstance(stmt, c_ast.Label):
                        current = self.codegen(stmt)
                    elif isinstance(stmt, c_ast.Compound) and self._stmt_contains_label(
                        stmt
                    ):
                        current = self._codegen_compound_with_forward_labels(stmt)
                    else:
                        continue
                else:
                    current = self.codegen(stmt)

                if self._is_expression_node(stmt):
                    current_val, current_ptr = current
                    if current_val is not None:
                        result = current_val
                        result_ptr = current_ptr
                        semantic_type = self._get_expr_ir_type(
                            stmt, getattr(current_val, "type", None)
                        )
                        if semantic_type is not None:
                            self._set_expr_ir_type(node, semantic_type)
        return result, result_ptr

    def _stmt_contains_label(self, node):
        if node is None:
            return False
        if isinstance(node, c_ast.Label):
            return True
        for _name, child in node.children():
            if isinstance(child, list):
                if any(self._stmt_contains_label(item) for item in child):
                    return True
                continue
            if self._stmt_contains_label(child):
                return True
        return False

    def _stmt_contains_switch_label(self, node):
        if node is None:
            return False
        if isinstance(node, (c_ast.Case, c_ast.Default)):
            return True
        if isinstance(node, c_ast.Switch):
            return False
        for _name, child in node.children():
            if isinstance(child, list):
                if any(self._stmt_contains_switch_label(item) for item in child):
                    return True
                continue
            if self._stmt_contains_switch_label(child):
                return True
        return False

    def _codegen_compound_with_forward_labels(self, node):
        with self.new_scope():
            seen_label = False
            for stmt in node.block_items or []:
                if seen_label:
                    self.codegen(stmt)
                    continue

                if isinstance(stmt, c_ast.Label):
                    seen_label = True
                    self.codegen(stmt)
                    continue

                if isinstance(stmt, c_ast.Compound) and self._stmt_contains_label(stmt):
                    seen_label = True
                    self._codegen_compound_with_forward_labels(stmt)
                    continue

                if isinstance(stmt, c_ast.Decl):
                    if stmt.init is not None:
                        raise CodegenError(
                            "goto into block skips declaration with initializer is not supported"
                        )
                    self.codegen(stmt)
                    continue

                if isinstance(stmt, (c_ast.Typedef, c_ast.EmptyStatement)):
                    self.codegen(stmt)
                    continue

    def _codegen_compound_items(self, node, use_new_scope):
        scope = self.new_scope() if use_new_scope else nullcontext()

        with scope:
            if node.block_items:
                for stmt in node.block_items:
                    if self.builder and self.builder.block.is_terminated:
                        # After a terminator (goto/break/continue/return),
                        # only process reachable label paths — skip other unreachable code
                        if self._switch_contexts and (
                            isinstance(stmt, (c_ast.Case, c_ast.Default))
                            or self._stmt_contains_switch_label(stmt)
                        ):
                            self.codegen(stmt)
                            continue
                        if isinstance(stmt, c_ast.Label):
                            self.codegen(stmt)
                        elif isinstance(stmt, c_ast.Compound) and self._stmt_contains_label(
                            stmt
                        ):
                            self._codegen_compound_with_forward_labels(stmt)
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
        self._record_decl_ast_type(funcname, node.decl.type)

        self.return_type = ir_type  # for call in C
        if not hasattr(self, "func_return_types"):
            self.func_return_types = {}
        self.func_return_types[funcname] = ir_type

        param_infos, is_var_arg = self._funcdef_param_infos(node)
        arg_types = [param_type for _name, param_type, _decl in param_infos]

        function_type = ir.FunctionType(ir_type, arg_types, var_arg=is_var_arg)
        prior_state = self._file_scope_function_states.get(funcname)
        if node.param_decls and prior_state is not None:
            prior_decl = self.module.globals.get(prior_state.symbol_name)
            if isinstance(prior_decl, ir.Function):
                prior_type = prior_decl.function_type
                if self._function_arg_types_match(prior_type.args, arg_types):
                    function_type = prior_type
        symbol_name = self._register_file_scope_function(
            funcname,
            function_type,
            storage=node.decl.storage,
            funcspec=node.decl.funcspec,
            is_definition=True,
        )
        function_state = self._file_scope_function_states.get(funcname)
        needs_internal_linkage = (
            function_state is not None and function_state.linkage == "internal"
        )

        with self.new_function():
            self._function_display_name = funcname
            self._label_value_tags = {
                label_name: index + 1
                for index, label_name in enumerate(
                    self._collect_function_label_names(node.body)
                )
            }

            existing = self.module.globals.get(symbol_name)
            if existing and isinstance(existing, ir.Function):
                if existing.is_declaration:
                    self.function = existing
                    if needs_internal_linkage:
                        self.function.linkage = "internal"
                else:
                    raise SemanticError(f"redefinition of function '{funcname}'")
            else:
                try:
                    self.function = ir.Function(
                        self.module,
                        function_type,
                        name=symbol_name,
                    )
                    if needs_internal_linkage:
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
            for param_idx, (pname, arg_type, p) in enumerate(param_infos):
                if param_idx >= len(arg_types):
                    break
                var = self._alloca_in_entry(arg_type, pname)
                self.define(pname, (arg_type, var))
                if isinstance(p, c_ast.Decl):
                    self._record_decl_ast_type(pname, p.type)
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
                    if isinstance(p.type, c_ast.PtrDecl) and self._func_decl_returns_unsigned(
                        p.type.type
                    ):
                        self._mark_unsigned_return(var)

            for _pname, _arg_type, p in param_infos:
                if isinstance(p, c_ast.Decl):
                    self._emit_vla_param_bound_side_effects(p.type)

            self._codegen_compound_items(node.body, use_new_scope=False)

            if not self.builder.block.is_terminated:
                if isinstance(ir_type, ir.VoidType):
                    self.builder.ret_void()
                else:
                    self.builder.ret(self._zero_initializer(ir_type))

            return None, None

    def codegen_Struct(self, node):
        # Generate LLVM types for struct members

        # If this is a reference to a named struct without decls, look it up
        if node.name and node.decls is None:
            tag_key = self._tag_type_key(node.name)
            if tag_key in self.env:
                return self.env[tag_key][0]
            opaque = self.module.context.get_identified_type(
                self._aggregate_type_name("struct", node.name)
            )
            self.define(tag_key, (opaque, None))
            return opaque

        if any(decl.bitsize is not None for decl in node.decls):
            return self._build_layout_backed_struct(node)

        member_types = []
        member_names = []
        member_decl_types = []
        for decl in node.decls:
            member_types.append(self._resolve_struct_member_ir_type(decl))
            member_names.append(decl.name)
            member_decl_types.append(decl.type)
        # Create the struct type
        struct_type = self._identified_aggregate_type("struct", node.name, member_types)
        struct_type.members = member_names
        struct_type.member_decl_types = member_decl_types
        struct_type.visible_field_paths = self._compute_visible_field_paths(
            member_names, member_types
        )

        # Register named structs for later reuse
        if node.name:
            self.define(self._tag_type_key(node.name), (struct_type, None))

        return struct_type

    def codegen_Union(self, node):
        """Model union as a struct with alignment-preserving storage."""
        if node.name and node.decls is None:
            tag_key = self._tag_type_key(node.name)
            if tag_key in self.env:
                return self.env[tag_key][0]
            opaque = self.module.context.get_identified_type(
                self._aggregate_type_name("union", node.name)
            )
            self.define(tag_key, (opaque, None))
            return opaque

        if node.decls is not None and len(node.decls) == 0:
            union_type = self._identified_aggregate_type("union", node.name, [])
            union_type.members = []
            union_type.member_types = {}
            union_type.member_decl_types = {}
            union_type.member_types_by_index = []
            union_type.member_decl_types_by_index = []
            union_type.named_member_indices = {}
            union_type.visible_field_paths = {}
            union_type.is_union = True
            if node.name:
                self.define(self._tag_type_key(node.name), (union_type, None))
            return union_type

        member_names = []
        member_types = {}
        member_decl_types = {}
        member_types_by_index = []
        member_decl_types_by_index = []
        named_member_indices = {}
        max_size = 0
        max_align = 1
        for field_index, decl in enumerate(node.decls):
            if isinstance(decl.type, c_ast.ArrayDecl):
                ir_t = self._build_array_ir_type(decl.type)
            else:
                ir_t = self._resolve_ast_type(decl.type)
            member_names.append(decl.name)
            member_types_by_index.append(ir_t)
            member_decl_types_by_index.append(decl.type)
            if decl.name is not None:
                member_types[decl.name] = ir_t
                member_decl_types[decl.name] = decl.type
                named_member_indices[decl.name] = field_index
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
            union_body = [align_type, ir.ArrayType(int8_t, pad_size)]
        else:
            union_body = [align_type]
        union_type = self._identified_aggregate_type("union", node.name, union_body)
        union_type.members = member_names
        union_type.member_types = member_types
        union_type.member_decl_types = member_decl_types
        union_type.member_types_by_index = member_types_by_index
        union_type.member_decl_types_by_index = member_decl_types_by_index
        union_type.named_member_indices = named_member_indices
        union_type.visible_field_paths = self._compute_visible_field_paths(
            member_names, member_types_by_index
        )
        union_type.is_union = True

        if node.name:
            self.define(self._tag_type_key(node.name), (union_type, None))

        return union_type

    def _finalize_aggregate_field_access(
        self, node, typed_field_addr, semantic_field_type, decl_type=None
    ):
        if isinstance(semantic_field_type, ir.ArrayType):
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

    def _codegen_aggregate_path_access(
        self, node, aggregate_addr, aggregate_type, field_path
    ):
        field_index = field_path[0]
        is_last = len(field_path) == 1

        if getattr(aggregate_type, "has_custom_layout", False):
            layout = self._aggregate_layout_by_index(aggregate_type, field_index)
            if layout is None:
                raise RuntimeError(
                    f"Field '{node.field.name}' not found in struct"
                )

            if layout.is_bitfield:
                if not is_last:
                    raise RuntimeError(
                        f"Field '{node.field.name}' not found in struct"
                    )
                container_ptr = self._byte_offset_ptr(
                    aggregate_addr,
                    layout.storage_byte_offset,
                    ir.PointerType(layout.storage_ir_type),
                    name="bitfieldptr",
                )
                ref = BitFieldRef(
                    container_ptr=container_ptr,
                    storage_ir_type=layout.storage_ir_type,
                    bit_offset=layout.bit_offset,
                    bit_width=layout.bit_width,
                    semantic_ir_type=layout.semantic_ir_type,
                    is_unsigned=layout.is_unsigned,
                )
                val = self._load_bitfield(ref)
                if layout.decl_type is not None:
                    self._tag_value_from_decl_type(val, layout.decl_type)
                if layout.is_unsigned:
                    self._tag_unsigned(val)
                self._set_expr_ir_type(node, layout.semantic_ir_type)
                return val, ref

            typed_field_addr = self._byte_offset_ptr(
                aggregate_addr,
                layout.byte_offset,
                ir.PointerType(layout.semantic_ir_type),
                name="fieldptr",
            )
            if is_last:
                return self._finalize_aggregate_field_access(
                    node,
                    typed_field_addr,
                    layout.semantic_ir_type,
                    layout.decl_type,
                )
            if not self._is_aggregate_ir_type(layout.semantic_ir_type):
                raise RuntimeError(f"Field '{node.field.name}' not found in struct")
            return self._codegen_aggregate_path_access(
                node,
                typed_field_addr,
                layout.semantic_ir_type,
                field_path[1:],
            )

        if getattr(aggregate_type, "is_union", False):
            member_ir_type = self._aggregate_member_ir_type(aggregate_type, field_index)
            decl_type = self._aggregate_member_decl_type(aggregate_type, field_index)
            semantic_field_type = self._refine_member_ir_type(
                aggregate_type, field_index, member_ir_type
            )
            typed_field_addr = self.builder.bitcast(
                aggregate_addr,
                ir.PointerType(semantic_field_type),
            )
            if is_last:
                return self._finalize_aggregate_field_access(
                    node,
                    typed_field_addr,
                    semantic_field_type,
                    decl_type,
                )
            if not self._is_aggregate_ir_type(semantic_field_type):
                raise RuntimeError(f"Field '{node.field.name}' not found in struct")
            return self._codegen_aggregate_path_access(
                node,
                typed_field_addr,
                semantic_field_type,
                field_path[1:],
            )

        if not hasattr(aggregate_type, "members"):
            raise SemanticError(
                f"field '{node.field.name}' accessed on incomplete struct"
            )

        if field_index >= len(aggregate_type.elements):
            raise RuntimeError(f"Field '{node.field.name}' not found in struct")

        field_addr = self.builder.gep(
            aggregate_addr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            inbounds=True,
        )

        field_type = self._aggregate_member_ir_type(aggregate_type, field_index)
        decl_type = self._aggregate_member_decl_type(aggregate_type, field_index)
        semantic_field_type = self._refine_member_ir_type(
            aggregate_type, field_index, field_type
        )

        typed_field_addr = field_addr
        target_ptr_type = ir.PointerType(semantic_field_type)
        if field_addr.type != target_ptr_type:
            try:
                typed_field_addr = self.builder.bitcast(
                    field_addr, target_ptr_type
                )
            except Exception:
                typed_field_addr = field_addr

        if is_last:
            return self._finalize_aggregate_field_access(
                node,
                typed_field_addr,
                semantic_field_type,
                decl_type,
            )
        if not self._is_aggregate_ir_type(semantic_field_type):
            raise RuntimeError(f"Field '{node.field.name}' not found in struct")
        return self._codegen_aggregate_path_access(
            node,
            typed_field_addr,
            semantic_field_type,
            field_path[1:],
        )

    def _codegen_aggregate_field_access(
        self, node, aggregate_addr, aggregate_type, field_name
    ):
        direct_field_index = self._aggregate_direct_member_index(
            aggregate_type, field_name
        )
        if direct_field_index is not None:
            return self._codegen_direct_aggregate_field_access(
                node,
                aggregate_addr,
                aggregate_type,
                direct_field_index,
            )

        field_path = self._aggregate_field_path(aggregate_type, field_name)
        if field_path is None:
            if not hasattr(aggregate_type, "members") and not getattr(
                aggregate_type, "is_union", False
            ):
                raise SemanticError(
                    f"field '{field_name}' accessed on incomplete struct"
                )
            raise RuntimeError(f"Field '{field_name}' not found in struct")
        return self._codegen_aggregate_path_access(
            node,
            aggregate_addr,
            aggregate_type,
            field_path,
        )

    def _codegen_direct_aggregate_field_access(
        self, node, aggregate_addr, aggregate_type, field_index
    ):
        if getattr(aggregate_type, "has_custom_layout", False):
            layout = self._aggregate_layout_by_index(aggregate_type, field_index)
            if layout is None:
                raise RuntimeError(f"Field '{node.field.name}' not found in struct")

            if layout.is_bitfield:
                container_ptr = self._byte_offset_ptr(
                    aggregate_addr,
                    layout.storage_byte_offset,
                    ir.PointerType(layout.storage_ir_type),
                    name="bitfieldptr",
                )
                ref = BitFieldRef(
                    container_ptr=container_ptr,
                    storage_ir_type=layout.storage_ir_type,
                    bit_offset=layout.bit_offset,
                    bit_width=layout.bit_width,
                    semantic_ir_type=layout.semantic_ir_type,
                    is_unsigned=layout.is_unsigned,
                )
                val = self._load_bitfield(ref)
                if layout.decl_type is not None:
                    self._tag_value_from_decl_type(val, layout.decl_type)
                if layout.is_unsigned:
                    self._tag_unsigned(val)
                self._set_expr_ir_type(node, layout.semantic_ir_type)
                return val, ref

            typed_field_addr = self._byte_offset_ptr(
                aggregate_addr,
                layout.byte_offset,
                ir.PointerType(layout.semantic_ir_type),
                name="fieldptr",
            )
            return self._finalize_aggregate_field_access(
                node,
                typed_field_addr,
                layout.semantic_ir_type,
                layout.decl_type,
            )

        if getattr(aggregate_type, "is_union", False):
            member_ir_type = self._aggregate_member_ir_type(aggregate_type, field_index)
            decl_type = self._aggregate_member_decl_type(aggregate_type, field_index)
            semantic_field_type = self._refine_member_ir_type(
                aggregate_type, field_index, member_ir_type
            )
            typed_field_addr = self.builder.bitcast(
                aggregate_addr,
                ir.PointerType(semantic_field_type),
            )
            return self._finalize_aggregate_field_access(
                node,
                typed_field_addr,
                semantic_field_type,
                decl_type,
            )

        if not hasattr(aggregate_type, "members"):
            raise SemanticError(
                f"field '{node.field.name}' accessed on incomplete struct"
            )

        if field_index >= len(aggregate_type.elements):
            raise RuntimeError(f"Field '{node.field.name}' not found in struct")

        field_addr = self.builder.gep(
            aggregate_addr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
            inbounds=True,
        )
        field_type = self._aggregate_member_ir_type(aggregate_type, field_index)
        decl_type = self._aggregate_member_decl_type(aggregate_type, field_index)
        semantic_field_type = self._refine_member_ir_type(
            aggregate_type, field_index, field_type
        )

        typed_field_addr = field_addr
        target_ptr_type = ir.PointerType(semantic_field_type)
        if field_addr.type != target_ptr_type:
            try:
                typed_field_addr = self.builder.bitcast(field_addr, target_ptr_type)
            except Exception:
                typed_field_addr = field_addr

        return self._finalize_aggregate_field_access(
            node,
            typed_field_addr,
            semantic_field_type,
            decl_type,
        )

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
            _, struct_instance_addr = self.lookup(node.name.name)
            if not isinstance(struct_instance_addr.type, ir.PointerType):
                raise Exception("Invalid struct reference")

            if node.type == "->":
                if isinstance(struct_instance_addr.type.pointee, ir.ArrayType):
                    ptr_val = self.builder.gep(
                        struct_instance_addr,
                        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                        name="structrefarraydecay",
                    )
                else:
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
                if (
                    addr is None
                    and not isinstance(getattr(struct_addr, "type", None), ir.PointerType)
                    and (
                        getattr(struct_type, "has_custom_layout", False)
                        or _is_struct_ir_type(struct_type)
                    )
                ):
                    materialized = self._alloca_in_entry(val.type, "structrval")
                    self._safe_store(val, materialized)
                    struct_addr = materialized

        return self._codegen_aggregate_field_access(
            node,
            struct_addr,
            struct_type,
            node.field.name,
        )

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

    def codegen_GenericSelection(self, node):
        selected = self._select_generic_association(node)
        if selected is None:
            raise SemanticError("no matching association in _Generic selection")
        result = self.codegen(selected)
        result_val, _ = result
        if result_val is not None:
            semantic_type = self._get_expr_ir_type(
                selected, getattr(result_val, "type", None)
            )
            if semantic_type is not None:
                self._set_expr_ir_type(node, semantic_type)
        return result

    def codegen_Label(self, node):
        label_bb = self._ensure_label_block(node.name)
        if not self.builder.block.is_terminated:
            self.builder.branch(label_bb)
        self.builder.position_at_end(label_bb)
        if node.stmt:
            self.codegen(node.stmt)
        return None, None

    def codegen_Case(self, node):
        if not self._switch_contexts:
            return None, None
        label_bb = self._switch_contexts[-1]["blocks"].get(id(node))
        if label_bb is None:
            return None, None
        if self.builder.block is not label_bb and not self.builder.block.is_terminated:
            self.builder.branch(label_bb)
        self.builder.position_at_end(label_bb)
        for stmt in node.stmts or []:
            self.codegen(stmt)
        return None, None

    def codegen_Default(self, node):
        if not self._switch_contexts:
            return None, None
        label_bb = self._switch_contexts[-1]["blocks"].get(id(node))
        if label_bb is None:
            return None, None
        if self.builder.block is not label_bb and not self.builder.block.is_terminated:
            self.builder.branch(label_bb)
        self.builder.position_at_end(label_bb)
        for stmt in node.stmts or []:
            self.codegen(stmt)
        return None, None

    def codegen_Goto(self, node):
        target_bb = self._ensure_label_block(node.name)
        self.builder.branch(target_bb)
        return None, None

    def codegen_ComputedGoto(self, node):
        target_val, _ = self.codegen(node.expr)
        if target_val is None:
            raise SemanticError("computed goto requires a target expression")

        if isinstance(target_val.type, ir.PointerType):
            target_tag = self.builder.ptrtoint(target_val, int64_t)
        elif isinstance(target_val.type, ir.IntType):
            target_tag = self._implicit_convert(target_val, int64_t)
        else:
            raise SemanticError("computed goto target must be an integer or pointer")

        current_bb = self.builder.block
        default_bb = self.builder.function.append_basic_block("computed_goto_default")
        switch_inst = self.builder.switch(target_tag, default_bb)
        for label_name, tag in self._label_value_tags.items():
            switch_inst.add_case(
                ir.Constant(int64_t, tag),
                self._ensure_label_block(label_name),
            )

        default_builder = ir.IRBuilder(default_bb)
        default_builder.unreachable()
        self.builder.position_at_end(current_bb)
        return None, None

    def codegen_Enum(self, node):
        # Define each enumerator as a constant in the environment
        enum_range = None
        if node.values:
            current_val = 0
            min_value = None
            max_value = None
            for enumerator in node.values.enumerators:
                if enumerator.value:
                    current_val = self._eval_const_expr(enumerator.value)
                self.define(
                    enumerator.name, (int32_t, ir.Constant(int32_t, current_val))
                )
                if min_value is None or current_val < min_value:
                    min_value = current_val
                if max_value is None or current_val > max_value:
                    max_value = current_val
                current_val += 1
            enum_range = (min_value, max_value)
        if getattr(node, "name", None) and enum_range is not None:
            self.env[self._enum_tag_key(node.name)] = enum_range
        return None, None

    def _eval_const_expr(self, node):
        """Evaluate a constant expression at compile time (for enum values)."""
        def is_float_value(value):
            return isinstance(value, float)

        def cast_int_value(value, width, is_unsigned):
            mask = (1 << width) - 1
            value = int(value) & mask
            if is_unsigned:
                return value
            sign_bit = 1 << (width - 1)
            if value & sign_bit:
                value -= 1 << width
            return value

        def cast_const_value(value, target_decl_type):
            target_ir_type = self._resolve_ast_type(target_decl_type)
            if isinstance(target_ir_type, ir.IntType):
                return cast_int_value(
                    value,
                    target_ir_type.width,
                    self._is_unsigned_scalar_decl_type(target_decl_type),
                )
            if self._is_floating_ir_type(target_ir_type):
                return float(value)
            return value

        def c_int_div(lhs, rhs):
            return int(lhs / rhs)

        def c_int_mod(lhs, rhs):
            return lhs - c_int_div(lhs, rhs) * rhs

        def c_float_div(lhs, rhs):
            if rhs == 0.0:
                if lhs == 0.0:
                    return float("nan")
                sign = math.copysign(1.0, lhs) * math.copysign(1.0, rhs)
                return math.copysign(float("inf"), sign)
            return lhs / rhs

        if isinstance(node, c_ast.Constant):
            if node.type in ("string", "wstring"):
                return 0  # string constants can't be int-evaluated
            if node.type in ("float", "double"):
                return self._parse_float_constant(node.value)
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
                if self._is_string_constant(node.expr):
                    return len(self._string_literal_data(node.expr))
                ir_t = self._infer_sizeof_operand_ir_type(node.expr)
                return self._ir_type_size(ir_t)
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
            use_float = is_float_value(l) or is_float_value(r)
            ops = {
                "+": lambda a, b: a + b,
                "-": lambda a, b: a - b,
                "*": lambda a, b: a * b,
                "/": lambda a, b: c_float_div(a, b) if use_float else c_int_div(a, b),
                "%": lambda a, b: a % b if use_float else c_int_mod(a, b),
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
            # Only true integer constant bindings (for example enum values)
            # participate in constant-expression evaluation. Ordinary locals
            # and globals must not silently fold to zero.
            if node.name in self.env:
                _, val = self.env[node.name]
                if isinstance(val, ir.values.Constant) and isinstance(
                    val.type, ir.IntType
                ):
                    return int(val.constant)
            raise CodegenError(f"Not a constant expression: identifier '{node.name}'")
        elif isinstance(node, c_ast.Cast):
            value = self._eval_const_expr(node.expr)
            return cast_const_value(value, node.to_type.type)
        elif isinstance(node, c_ast.FuncCall):
            if isinstance(node.name, c_ast.ID):
                callee = node.name.name
                if callee in ("__builtin_inf", "__builtin_inff", "__builtin_infl"):
                    return float("inf")
                if callee in ("__builtin_nan", "__builtin_nanf", "__builtin_nanl"):
                    return float("nan")
            raise CodegenError(f"Not a constant expression: {type(node).__name__}")
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
        self._record_typedef_ast_type(node.name, node.type)
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
                self.define(f"__typedef_{node.name}", int32_t)
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
        elif isinstance(node.type, c_ast.FuncDecl):
            func_type, _ = self._build_function_ir_type(node.type)
            self.define(f"__typedef_{node.name}", func_type)
        return None, None
