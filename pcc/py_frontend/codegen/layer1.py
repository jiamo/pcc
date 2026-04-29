"""Layer-1/2 (typed, mostly-native) LLVM IR codegen for pcc_py.

Covers the Phase 1 MVP scope plus Phase 2 (L2) container / string /
None support from ``docs/plans/python-frontend-plan.md``.

Phase 1 (native-only) constructs:

* ``def`` with typed parameters and return.
* Scalar arithmetic on ``int`` (i64), ``float`` (double), ``bool`` (i1),
  including Python-correct ``//`` (floor) and ``%`` (sign follows
  divisor) on signed integers, and Python ``/`` (int / int → float).
* ``if / elif / else``, ``while``, ``for i in range(...)`` (the range
  literal is typed and lowered to an i64 while-counter).
* Local variables via entry-block ``alloca`` + ``store`` + ``load``.
* Function calls (including recursion).
* ``return``.

Phase 2 (typed objects via runtime lib) constructs:

* String literals (stored as UTF-8 globals, wrapped via ``py_str_new``).
* ``None`` literal (reference to runtime global ``py_None``).
* List / dict / tuple literals with native→PyObject* marshalling per
  element.
* Subscript read/write on list / dict / tuple / str (runtime dispatch).
* ``in`` / ``not in`` on str / list / dict.
* ``x is None`` / ``x is not None`` (pointer comparison against
  ``py_None``).
* String concatenation and repetition via ``py_str_concat`` /
  ``py_str_repeat``.
* ``len(x)`` dispatched to ``py_list_len`` / ``py_str_len`` /
  ``py_dict_len`` / ``py_tuple_len`` as appropriate.
* ``print(...)`` on str / list / dict / tuple via ``py_print``, and
  multi-arg print via ``py_print_many`` (arguments marshalled into a
  tuple PyObject first).

Anything outside the above raises :class:`NotImplementedError` with a
message naming the offending AST node — that's the signal for Layer 3
to pick it up.

Layer discipline per-expression: an expression is "L1" iff its type and
every operand's type is a native-mapping scalar (IntType / FloatType /
BoolType). Otherwise it becomes "L2" and goes through the runtime lib
with marshalling at boundaries.
"""
from __future__ import annotations

import os
from dataclasses import replace as _replace
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..export_meta import decode_type
from ..py_ast import (
    Arg,
    Assign,
    Attr,
    AugAssign,
    BinOp,
    BoolExpr,
    BoolLit,
    BoolType,
    Break,
    Call,
    ClassDef,
    ClassType,
    Compare,
    Continue,
    DictExpr,
    DictType,
    DynType,
    Expr,
    ExprStmt,
    ExceptHandler,
    FloatLit,
    FloatType,
    For,
    FuncDef,
    FuncType,
    Global,
    If,
    IfExpr,
    IntLit,
    IntType,
    Lambda,
    ListExpr,
    ListType,
    Module,
    Name,
    Nonlocal,
    NoneLit,
    NoneType,
    Delete,
    Pass,
    Return,
    Slice,
    SourceSpan,
    Import,
    ImportFrom,
    Raise,
    Stmt,
    StrLit,
    StrType,
    Subscript,
    Try,
    With,
    TupleExpr,
    TupleType,
    Type,
    UnaryOp,
    While,
)
from . import marshal
from .class_gen import ClassLowering
from .runtime_abi import declare_runtime, declare_runtime_global


_Assign = Assign
_AugAssign = AugAssign
_Arg = Arg
_Attr = Attr
_Call = Call
_ClassDef = ClassDef
_Delete = Delete
_DynType = DynType
_ExprStmt = ExprStmt
_For = For
_FuncDef = FuncDef
_If = If
_Lambda = Lambda
_List = ListExpr
_Name = Name
_None = NoneLit
_NoneType = NoneType
_Return = Return
_TopImport = Import
_TopImportFrom = ImportFrom
_Try = Try
_TupleExpr = TupleExpr
_While = While
_With = With
_DYN = DynType(name="dyn")


# -- Canonical IR types ------------------------------------------------------

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_VOID = ir.VoidType()
_CSTR = _I8.as_pointer()

# Cached Python builtin names used by free-variable analysis. Keep this
# static so the self-hosted compiler does not need ``import builtins`` +
# ``dir`` just to decide whether a lambda captures a global name.
_PY_BUILTINS_NS = (
    "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
    "BaseExceptionGroup", "BlockingIOError", "BrokenPipeError",
    "BufferError", "BytesWarning", "ChildProcessError",
    "ConnectionAbortedError", "ConnectionError", "ConnectionRefusedError",
    "ConnectionResetError", "DeprecationWarning", "EOFError", "Ellipsis",
    "EncodingWarning", "EnvironmentError", "Exception", "ExceptionGroup",
    "False", "FileExistsError", "FileNotFoundError", "FloatingPointError",
    "FutureWarning", "GeneratorExit", "IOError", "ImportError",
    "ImportWarning", "IndentationError", "IndexError", "InterruptedError",
    "IsADirectoryError", "KeyError", "KeyboardInterrupt", "LookupError",
    "MemoryError", "ModuleNotFoundError", "NameError", "None",
    "NotADirectoryError", "NotImplemented", "NotImplementedError",
    "OSError", "OverflowError", "PendingDeprecationWarning",
    "PermissionError", "ProcessLookupError", "PythonFinalizationError",
    "RecursionError", "ReferenceError", "ResourceWarning", "RuntimeError",
    "RuntimeWarning", "StopAsyncIteration", "StopIteration", "SyntaxError",
    "SyntaxWarning", "SystemError", "SystemExit", "TabError",
    "TimeoutError", "True", "TypeError", "UnboundLocalError",
    "UnicodeDecodeError", "UnicodeEncodeError", "UnicodeError",
    "UnicodeTranslateError", "UnicodeWarning", "UserWarning",
    "ValueError", "Warning", "ZeroDivisionError", "_IncompleteInputError",
    "__build_class__", "__debug__", "__doc__", "__import__", "__loader__",
    "__name__", "__package__", "__spec__", "abs", "aiter", "all", "anext",
    "any", "ascii", "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "copyright",
    "credits", "delattr", "dict", "dir", "divmod", "enumerate", "eval",
    "exec", "exit", "filter", "float", "format", "frozenset", "getattr",
    "globals", "hasattr", "hash", "help", "hex", "id", "input", "int",
    "isinstance", "issubclass", "iter", "len", "license", "list", "locals",
    "map", "max", "memoryview", "min", "next", "object", "oct", "open",
    "ord", "pow", "print", "property", "quit", "range", "repr",
    "reversed", "round", "set", "setattr", "slice", "sorted",
    "staticmethod", "str", "sum", "super", "tuple", "type", "vars", "zip",
)


def _maybe_fold_str_to_float(s: str):
    """Compile-time fold ``float("X")`` for X in the small set of
    literal forms that lower trivially to a native ``ir.Constant``.

    Returns ``None`` if ``s`` doesn't match a folded shape so the
    caller can fall through to the dynamic path. Issue 11.A.2: avoids
    pulling libpython for what's structurally a compile-time literal.
    """
    stripped = s.strip()
    lowered = stripped.lower()
    if lowered in ("inf", "+inf", "infinity", "+infinity"):
        return 1e309
    if lowered in ("-inf", "-infinity"):
        return -1e309
    if lowered in ("nan", "+nan", "-nan"):
        inf = 1e309
        return inf - inf
    return _parse_simple_decimal_float(stripped)


def _parse_simple_decimal_float(s: str):
    """Parse the decimal string shapes pcc folds for ``float("...")``.

    This intentionally covers the ordinary decimal grammar only:
    optional sign, digits, optional decimal point, and optional
    exponent. Other spellings stay on the dynamic path. Keeping this
    in pcc-friendly Python avoids pulling CPython into the compiler's
    own self-host closure just to parse a literal.
    """
    n = len(s)
    if n == 0:
        return None
    i = 0
    sign = 1.0
    if s[i] == "+":
        i += 1
    elif s[i] == "-":
        sign = -1.0
        i += 1
    if i >= n:
        return None

    value = 0.0
    saw_digit = False
    while i < n:
        d = _ascii_decimal_digit(s[i])
        if d < 0:
            break
        saw_digit = True
        value = value * 10.0 + d
        i += 1

    if i < n and s[i] == ".":
        i += 1
        place = 0.1
        while i < n:
            d = _ascii_decimal_digit(s[i])
            if d < 0:
                break
            saw_digit = True
            value = value + d * place
            place = place * 0.1
            i += 1

    if not saw_digit:
        return None

    if i < n and (s[i] == "e" or s[i] == "E"):
        i += 1
        exp_sign = 1
        if i < n and s[i] == "+":
            i += 1
        elif i < n and s[i] == "-":
            exp_sign = -1
            i += 1
        exp = 0
        saw_exp_digit = False
        while i < n:
            d = _ascii_decimal_digit(s[i])
            if d < 0:
                break
            saw_exp_digit = True
            exp = exp * 10 + d
            i += 1
        if not saw_exp_digit:
            return None
        if exp > 400:
            if exp_sign > 0:
                return sign * 1e309
            return sign * 0.0
        while exp > 0:
            if exp_sign > 0:
                value = value * 10.0
            else:
                value = value * 0.1
            exp -= 1

    if i != n:
        return None
    return sign * value


def _ascii_decimal_digit(ch: str) -> int:
    c = ord(ch)
    if 48 <= c <= 57:
        return c - 48
    return -1


def _dataclass_field_value(obj, field_name: str, default=None):
    return getattr(obj, field_name, default)


def _dataclass_field_names(obj):
    fields = getattr(obj, "__dataclass_fields__", None)
    if fields is not None:
        return fields.keys()
    if isinstance(obj, SourceSpan):
        return ("file", "line", "col", "end_line", "end_col")
    if isinstance(obj, IntType) or isinstance(obj, FloatType):
        return ("name", "width")
    if isinstance(obj, BoolType):
        return ("name",)
    if isinstance(obj, NoneType):
        return ("name",)
    if isinstance(obj, StrType):
        return ("name",)
    if isinstance(obj, ListType):
        return ("name", "elem")
    if isinstance(obj, DictType):
        return ("name", "key", "value")
    if isinstance(obj, TupleType):
        return ("name", "elems")
    if isinstance(obj, FuncType):
        return ("name", "params", "ret")
    if isinstance(obj, ClassType):
        return ("name", "module", "fields", "bases")
    if isinstance(obj, DynType):
        return ("name",)
    if isinstance(obj, Type):
        return ("name",)
    if isinstance(obj, Expr):
        if isinstance(obj, NoneLit):
            return ("span", "ty")
        if (
            isinstance(obj, IntLit)
            or isinstance(obj, FloatLit)
            or isinstance(obj, BoolLit)
            or isinstance(obj, StrLit)
        ):
            return ("span", "ty", "value")
        if isinstance(obj, Name):
            return ("span", "ty", "ident")
        if isinstance(obj, BinOp):
            return ("span", "ty", "op", "lhs", "rhs")
        if isinstance(obj, UnaryOp):
            return ("span", "ty", "op", "operand")
        if isinstance(obj, Compare):
            return ("span", "ty", "op", "lhs", "rhs")
        if isinstance(obj, BoolExpr):
            return ("span", "ty", "op", "left", "right")
        if isinstance(obj, Call):
            return ("span", "ty", "func", "args", "kwargs")
        if isinstance(obj, Attr):
            return ("span", "ty", "obj", "name")
        if isinstance(obj, Subscript):
            return ("span", "ty", "obj", "idx")
        if isinstance(obj, Slice):
            return ("span", "ty", "lo", "hi", "step")
        if isinstance(obj, ListExpr):
            return ("span", "ty", "elems")
        if isinstance(obj, DictExpr):
            return ("span", "ty", "pairs")
        if isinstance(obj, TupleExpr):
            return ("span", "ty", "elems")
        if isinstance(obj, IfExpr):
            return ("span", "ty", "cond", "then_e", "else_e")
        if isinstance(obj, Lambda):
            return ("span", "ty", "params", "body")
    if isinstance(obj, Stmt):
        if isinstance(obj, Assign):
            return ("span", "targets", "value", "annotation")
        if isinstance(obj, AugAssign):
            return ("span", "target", "op", "value")
        if isinstance(obj, ExprStmt):
            return ("span", "expr")
        if isinstance(obj, If):
            return ("span", "cond", "body", "else_body")
        if isinstance(obj, While):
            return ("span", "cond", "body", "else_body")
        if isinstance(obj, For):
            return ("span", "target", "iter", "body", "else_body")
        if isinstance(obj, Return):
            return ("span", "value")
        if (
            isinstance(obj, Pass)
            or isinstance(obj, Break)
            or isinstance(obj, Continue)
        ):
            return ("span",)
        if isinstance(obj, Raise):
            return ("span", "exc", "cause")
        if isinstance(obj, Try):
            return ("span", "body", "handlers", "else_body", "finally_body")
        if isinstance(obj, With):
            return ("span", "items", "body")
        if isinstance(obj, Import):
            return ("span", "names")
        if isinstance(obj, ImportFrom):
            return ("span", "module", "names", "level")
        if isinstance(obj, Global):
            return ("span", "names")
        if isinstance(obj, Nonlocal):
            return ("span", "names")
        if isinstance(obj, Delete):
            return ("span", "targets")
        if isinstance(obj, FuncDef):
            return (
                "span", "name", "args", "return_ty", "body",
                "decorators", "is_method", "is_async",
            )
        if isinstance(obj, ClassDef):
            return (
                "span", "name", "bases", "keywords", "body", "decorators",
            )
    if isinstance(obj, Arg):
        return ("name", "annotation", "default", "kind", "has_default")
    if isinstance(obj, ExceptHandler):
        return ("exc_type", "name", "body", "span")
    if isinstance(obj, Module):
        return ("name", "body", "docstring")
    return ()


def _as_native_float(value) -> float:
    return value


# Well-known Python stdlib callables that fall through to the
# libpython fallback when no pcc-native replacement exists. Listed
# here so codegen can route ``open(...)`` / ``iter(xs)`` / ``next(g,
# default)`` / ``sorted(xs, key=...)`` / ``zip(a, b)`` / ``super()`` /
# ``hasattr(x, "n")`` etc. through ``py_cpy_import("builtins") +
# py_cpy_getattr(name)`` rather than abort. Each entry pulls
# libpython into the link step but unblocks solo-compile of files
# that reference the callable.
_CPY_BUILTIN_TYPE_NAMES = frozenset({
    "int", "str", "list", "dict", "tuple", "float", "bool",
    "bytes", "bytearray", "set", "frozenset", "complex",
    "object", "type", "Exception", "BaseException",
})

_CPY_BUILTIN_FALLBACK = frozenset({
    "open", "iter", "next", "sorted", "zip", "super", "enumerate",
    "min", "max",
    "callable", "exit", "input", "reversed",
    "frozenset", "property", "staticmethod", "classmethod",
    "type", "object", "issubclass", "getattr", "setattr",
    "delattr", "vars", "dir", "globals", "locals",
    "eval", "exec", "compile",
    # Container/bytes constructors that aren't on the pcc-native
    # fast path — route through libpython so solo-compile of files
    # that use them (``pcc/py_stdlib/io.py`` / ``class_gen.py``)
    # keeps advancing.
    "bytearray", "bytes", "memoryview", "slice",
    # Numeric / text converters — ``int`` / ``bool`` / ``float`` /
    # ``str`` have pcc-native fast paths for their common forms;
    # these cover the ``int(str, base)`` / ``float(str)`` variants
    # that fall through.
    "float",
    # Floating-point / numeric helpers
    "complex", "divmod", "round", "pow",
    # Comparison + hashing — ``min`` / ``max`` / ``abs`` / ``sum``
    # are fast-pathed; these complement them.
    "all", "any",
    # Character / ordinal conversion
    "chr", "ord", "hex", "oct", "bin",
    # Identity / introspection
    "id", "repr", "ascii", "format",
})


def _replace_arg_with_none_default(arg):
    """Return a copy of ``arg`` with a ``NoneLit`` default when it has
    no explicit default. Used for click-decorated entry functions so
    ``pcc.main()`` at ``if __name__ == "__main__":`` compiles even
    though click normally supplies the runtime values. The synthesized
    default has the arg's declared annotation or DynType otherwise."""
    from ..py_ast import NoneLit as _NL, NoneType as _NoneType
    from dataclasses import replace as _replace
    return _replace(
        arg,
        default=_NL(
            span=arg.span if hasattr(arg, "span") else None,
            ty=_NoneType(name="None"),
        ),
    )


def _zero_initializer_for(ir_ty):
    if isinstance(ir_ty, ir.IntType):
        return 0
    if isinstance(ir_ty, (ir.FloatType, ir.DoubleType)):
        return 0.0
    if isinstance(ir_ty, ir.PointerType):
        return None
    return 0


class L1CodegenError(Exception):
    """Raised when L1 cannot handle an AST shape it should have.

    Distinct from :class:`NotImplementedError` — which means "this is a
    later-phase feature that belongs in L2/L3" — in that L1CodegenError
    indicates a malformed AST or an internal invariant violation.
    """


class ScaffoldUnsupportedError(NotImplementedError):
    """Raised in ir_scaffold_mode='on' when codegen encounters an
    IRBuilder method or ``ir.X`` symbol that has not yet been migrated
    to native lowering (Path A / Issue 1).

    OFF mode never raises this — it falls back to ``py_cpy_*`` dispatch
    silently. The error surface only exists in ON mode so per-file
    migration can identify exactly which symbols still need coverage.
    """


# Set of ``IRBuilder`` instance methods that scaffold dispatch
# recognises. Membership only triggers detection (raise-or-route);
# whether a method is actually *implemented* is gated by
# ``_IR_SCAFFOLD_METHOD_IMPL`` below. Mirrors the surface listed in
# ``docs/issues/open-bootstrap-issues.md`` (Issue 4).
_IR_BUILDER_METHODS = frozenset({
    # control flow positioning
    "position_at_end", "position_at_start", "position_before",
    "append_basic_block",
    # loads/stores
    "store", "load", "alloca",
    # branches / control flow
    "branch", "cbranch", "ret", "ret_void", "unreachable",
    # comparisons
    "icmp_signed", "icmp_unsigned",
    "fcmp_ordered", "fcmp_unordered",
    # arithmetic (int)
    "add", "sub", "mul", "sdiv", "srem",
    # bitwise / logical
    "and_", "or_", "xor", "not_", "neg",
    "shl", "ashr", "lshr",
    # arithmetic (float)
    "fadd", "fsub", "fmul", "fdiv", "frem", "fneg",
    # float casts
    "fpext", "fptosi", "fptoui", "fptrunc",
    "sitofp", "uitofp",
    # pointer / cast
    "gep", "bitcast", "inttoptr", "ptrtoint",
    "sext", "zext", "trunc",
    # aggregate / phi / selection
    "phi", "select", "extract_value", "insert_value",
    "add_incoming",
    # exception handling
    "landingpad", "resume", "invoke",
    # atomic
    "fence", "atomic_rmw", "cmpxchg",
    # generic
    "call",
    # Type methods (any ir.X type instance — routed by
    # _IR_UNAMBIGUOUS_METHODS regardless of receiver).
    "as_pointer",
})

# Set of ``ir.X`` module-level symbols (types and value constructors)
# scaffold dispatch recognises. Mirrors the public class set in
# ``pcc/llvm_capi/ir.py`` — keep in sync if new types are exported.
_IR_MODULE_SYMBOLS = frozenset({
    "IRBuilder",
    "IntType", "PointerType", "VoidType", "DoubleType",
    "FloatType", "HalfType",
    "ArrayType", "FunctionType",
    "Constant", "Function", "Module", "GlobalVariable",
    "IdentifiedStructType", "LiteralStructType",
    "Block", "Argument", "Context", "Value",
})


# Set of methods/symbols whose scaffold lowering is currently
# implemented. Phase 2+ adds entries here as each method gets a
# native lowering rule and tests. Detection without implementation
# raises ScaffoldUnsupportedError so per-file migration sees a clean
# "method X needs lowering" signal.
# Table-driven IRBuilder method lowering (Phase 2/3).
# Maps method name → (extern return type, fixed positional arg count).
# All positional args are coerced to ``i8*`` opaque handles at the call
# site; kwargs (``name=``) are accepted but discarded — they're SSA
# naming hints, not semantically meaningful in the C runtime.
# Methods whose dispatch isn't a clean N-pointer-args shape (variadic
# ``call``/``gep``, op-string-prefixed ``icmp_*``, etc.) are handled by
# bespoke ``_emit_scaffold_<method>`` functions instead and excluded
# from this table.
_IR_SCAFFOLD_SIMPLE_METHODS: dict = {
    # Void-returning
    "store":              ("void", 2),
    "ret_void":           ("void", 0),
    "unreachable":        ("void", 0),
    "branch":             ("void", 1),
    "position_at_end":    ("void", 1),
    "position_at_start":  ("void", 1),
    "position_before":    ("void", 1),
    "ret":                ("void", 1),
    "cbranch":            ("void", 3),
    "resume":             ("void", 1),
    "fence":              ("void", 1),
    "add_incoming":       ("void", 2),
    # Pointer-returning (handle to an LLVM IR value)
    "load":               ("ptr", 1),
    "alloca":             ("ptr", 1),
    "bitcast":            ("ptr", 2),
    "inttoptr":           ("ptr", 2),
    "ptrtoint":           ("ptr", 2),
    "sext":               ("ptr", 2),
    "zext":               ("ptr", 2),
    "trunc":              ("ptr", 2),
    "sitofp":             ("ptr", 2),
    "uitofp":             ("ptr", 2),
    "fpext":              ("ptr", 2),
    "fptosi":             ("ptr", 2),
    "fptoui":             ("ptr", 2),
    "fptrunc":            ("ptr", 2),
    "add":                ("ptr", 2),
    "sub":                ("ptr", 2),
    "mul":                ("ptr", 2),
    "sdiv":               ("ptr", 2),
    "srem":               ("ptr", 2),
    "and_":               ("ptr", 2),
    "or_":                ("ptr", 2),
    "xor":                ("ptr", 2),
    "shl":                ("ptr", 2),
    "ashr":               ("ptr", 2),
    "lshr":               ("ptr", 2),
    "fadd":               ("ptr", 2),
    "fsub":               ("ptr", 2),
    "fmul":               ("ptr", 2),
    "fdiv":               ("ptr", 2),
    "frem":               ("ptr", 2),
    "not_":               ("ptr", 1),
    "neg":                ("ptr", 1),
    "fneg":               ("ptr", 1),
    "select":             ("ptr", 3),
    "extract_value":      ("ptr", 2),
    "insert_value":       ("ptr", 3),
    "icmp_signed":        ("ptr", 3),
    "icmp_unsigned":      ("ptr", 3),
    "fcmp_ordered":       ("ptr", 3),
    "fcmp_unordered":     ("ptr", 3),
    "atomic_rmw":         ("ptr", 4),
    "cmpxchg":            ("ptr", 5),
    "invoke":             ("ptr", 4),
    # Type method — receiver may be any ir.X type instance; routed
    # via _IR_UNAMBIGUOUS_METHODS with bespoke flexible-arity handler.
    "as_pointer":         ("ptr", 0),
}

_IR_SCAFFOLD_METHOD_OPTIONAL_PARAMS: dict = {
    "load": ("name", "align"),
    "store": ("align",),
    "fence": ("syncscope",),
    "bitcast": ("name",),
    "inttoptr": ("name",),
    "ptrtoint": ("name",),
    "sext": ("name",),
    "zext": ("name",),
    "trunc": ("name",),
    "sitofp": ("name",),
    "uitofp": ("name",),
    "fpext": ("name",),
    "fptosi": ("name",),
    "fptoui": ("name",),
    "fptrunc": ("name",),
    "add": ("name",),
    "sub": ("name",),
    "mul": ("name",),
    "sdiv": ("name",),
    "srem": ("name",),
    "and_": ("name",),
    "or_": ("name",),
    "xor": ("name",),
    "shl": ("name",),
    "ashr": ("name",),
    "lshr": ("name",),
    "not_": ("name",),
    "neg": ("name",),
    "fneg": ("name",),
    "fadd": ("name",),
    "fsub": ("name",),
    "fmul": ("name",),
    "fdiv": ("name",),
    "frem": ("name",),
    "select": ("name",),
    "extract_value": ("name",),
    "insert_value": ("name",),
    "icmp_signed": ("name",),
    "icmp_unsigned": ("name",),
    "fcmp_ordered": ("name",),
    "fcmp_unordered": ("name",),
    "atomic_rmw": ("name",),
    "cmpxchg": ("name",),
    "invoke": ("name",),
}

# Methods with variable arity that can be called on any receiver
# (not just ``self.builder.X``). ``append_basic_block`` accepts
# 0 or 1 positional ``name`` arg plus an optional ``name`` kwarg —
# bespoke handler picks an extern variant.
_IR_SCAFFOLD_VARIABLE_ARITY_METHODS = frozenset({
    "append_basic_block",
})


# Variadic / bespoke methods handled outside the simple table.
_IR_SCAFFOLD_BESPOKE_METHODS = frozenset({
    "call",                # builder.call(fn, [args])
    "gep",                 # builder.gep(ptr, [indices])
    "phi",                 # builder.phi(ty)
    "landingpad",          # builder.landingpad(ty)
    "append_basic_block",  # 0-1 positional + name kwarg
})

# ir.X module-level constructors: same shape as IRBuilder methods but
# without a ``builder`` receiver. Maps name → (positional_arity,
# accepts_name_kwarg). When ``name=`` is present a separate
# ``..._named`` extern variant is emitted so the runtime side can
# distinguish the two surfaces. Variadic constructors
# (``Function``, ``LiteralStructType``) have their own bespoke
# handlers below.
_IR_SCAFFOLD_SIMPLE_SYMBOLS: dict = {
    # Types
    "IntType":              (1, False),  # ir.IntType(width)
    "PointerType":          (1, False),  # ir.PointerType(ty)
    "VoidType":             (0, False),
    "DoubleType":           (0, False),
    "FloatType":            (0, False),
    "HalfType":             (0, False),
    "ArrayType":            (2, False),  # ir.ArrayType(elem_ty, count)
    # Values
    "Constant":             (2, False),  # ir.Constant(ty, value)
    "GlobalVariable":       (2, True),   # ir.GlobalVariable(module, ty, name=...)
    "Module":               (0, True),   # ir.Module(name=...)
    "IRBuilder":            (1, False),  # ir.IRBuilder(block)
    "IdentifiedStructType": (2, False),  # ir.IdentifiedStructType(ctx, name)
    "Context":              (0, False),
}

# Variadic / bespoke ``ir.X`` constructors. ``Function`` already
# bespoke-handled below for the name= kwarg variant; ``FunctionType``
# is variadic in its param-types tuple.
_IR_SYMBOL_VARIADIC = frozenset({"FunctionType", "LiteralStructType"})

_IR_SCAFFOLD_BESPOKE_SYMBOLS = frozenset({
    "Function",            # ir.Function(module, fn_ty, name=...)
    "LiteralStructType",   # ir.LiteralStructType([ty1, ty2, ...])
    "FunctionType",        # ir.FunctionType(return_ty, [param_tys...])
})

_IR_SCAFFOLD_METHOD_IMPL: frozenset = frozenset(
    list(_IR_SCAFFOLD_SIMPLE_METHODS.keys())
) | _IR_SCAFFOLD_BESPOKE_METHODS

_IR_SCAFFOLD_SYMBOL_IMPL: frozenset = frozenset(
    list(_IR_SCAFFOLD_SIMPLE_SYMBOLS.keys())
) | _IR_SCAFFOLD_BESPOKE_SYMBOLS


class L1CodeGen:
    """Layer-1 code generator: typed pcc_py AST → native LLVM IR.

    Construct with a parsed and type-inferred :class:`ast.Module`, then
    call :meth:`generate` to get the module text. Re-invocations on the
    same instance are not supported; build a fresh generator per call
    for clarity.
    """

    class_lowering: ClassLowering

    # ---------------------------------------------------------------- init

    def __init__(
        self,
        module: Module,
        *,
        emit_cpy_main_exitcode: bool = False,
        ir_scaffold_mode: str = "off",
    ):
        self.ast_module = module
        self.emit_cpy_main_exitcode = emit_cpy_main_exitcode
        # Path A (closed-world) opt-in. ``off`` keeps historical
        # ``py_cpy_*`` dispatch behaviour (byte-identical to existing
        # bootstrap output). ``on`` routes ``self.builder.X(...)`` and
        # ``ir.Y(...)`` call sites to direct native IR lowering;
        # individual methods are added incrementally — anything not
        # yet supported raises a clear NotImplementedError instead of
        # silently falling back to ``py_cpy_*``.
        if ir_scaffold_mode not in ("off", "on"):
            raise ValueError(
                f"invalid ir_scaffold_mode {ir_scaffold_mode!r}; "
                f"expected 'off' or 'on'"
            )
        self.ir_scaffold_mode = ir_scaffold_mode
        self.module = ir.Module(name=module.name or "pcc_py_module")
        # target triple/layout are intentionally left empty so the
        # caller (pcc.py CLI) can set them based on the active cross-
        # compile target.
        self.runtime: dict[str, ir.Function] = declare_runtime(self.module)

        # Printf declaration for L1 print().
        self._printf = self._declare_printf()

        # Map from user function name -> ir.Function (filled during
        # the declaration pass before we emit bodies).
        self.functions: dict[str, ir.Function] = {}
        self._c_abi_export_symbols: set[str] = set()
        self._fn_err_exit_blocks: dict[str, ir.Block] = {}

        # Per-function state, reset when entering a new FuncDef:
        self.builder: Optional[ir.IRBuilder] = None
        self.current_function: Optional[ir.Function] = None
        self.current_func_def: Optional[FuncDef] = None
        self.current_class = None
        self.current_method_kind = None
        self._current_global_names: set[str] = set()
        # ident -> (alloca ptr, ir.Type, pcc_py Type)
        self.env: dict[str, tuple[ir.AllocaInstr, ir.Type, Type]] = {}
        self._module_globals: dict[str, tuple[ir.GlobalVariable, Type]] = {}
        self._cpy_module_flags: dict[str, bool] = {}
        # ident -> class name (when a local was last assigned a value
        # that we could statically identify as a known class instance).
        # Used by :meth:`_emit_method_call` to pick the right method
        # when type inference collapsed the variable to ``DynType``.
        self.env_class_hint: dict[str, str] = {}
        self.env_list_elem_class_hint: dict[str, str] = {}
        self._ir_builder_env_flags: dict[str, bool] = {}
        self._class_aliases: dict[str, str] = {}
        # Ordinary Python int values are object-shaped unless the module
        # imports low-level pcc.unsafe / pcc.extern scaffolding. Runtime
        # port modules use those scaffolds as their explicit raw-int opt-in.
        self._module_uses_raw_int_scaffold = False
        self._box_int_locals = False
        self._exact_int_env_flags: dict[str, bool] = {}
        # Loop stack of (continue_block, break_block) for break/continue.
        self.loop_stack: list[tuple[ir.Block, ir.Block]] = []

        # Cached format-string globals for printf.
        self._fmt_int: Optional[ir.GlobalVariable] = None
        self._fmt_float: Optional[ir.GlobalVariable] = None
        self._fmt_bool_true: Optional[ir.GlobalVariable] = None
        self._fmt_bool_false: Optional[ir.GlobalVariable] = None

        # De-duplicated pool of ``.pystr.<N>`` byte arrays and attribute
        # name C-strings for ``py_obj_getattr``.
        self._str_pool: dict[str, ir.GlobalVariable] = {}
        self._attr_pool: dict[str, ir.GlobalVariable] = {}
        self._str_counter = 0
        self._class_type_export_cache: dict[tuple[str, str], Optional[str]] = {}

        self._tmp_counter = 0
        self._skip_program_main = False
        self._sibling_module_inits: tuple[str, ...] = ()
        self._native_module_exports: Optional[dict] = None
        self._native_module_aliases: dict[str, str] = {}
        self._native_builtin_module_aliases: dict[str, str] = {}
        self._native_builtin_value_aliases: dict[str, str] = {}
        self._native_file_values: set = set()
        self._native_file_env_flags: dict[str, bool] = {}
        self._cross_module_func_defs: dict[str, FuncDef] = {}

    # ---------------------------------------------------------------- API

    def generate(self, module: Optional[Module] = None) -> str:
        """Lower the AST module to an LLVM IR text blob.

        ``module`` may be supplied to override the one given to the
        constructor, matching the task contract.
        """
        if module is not None:
            self.ast_module = module
            self.module = ir.Module(name=module.name or "pcc_py_module")
            self.runtime = declare_runtime(self.module)
            self._printf = self._declare_printf()
            self.functions = {}
            self._c_abi_export_symbols = set()
            self._fn_err_exit_blocks = {}
            self._fmt_int = None
            self._fmt_float = None
            self._fmt_bool_true = None
            self._fmt_bool_false = None
            self._str_pool = {}
            self._attr_pool = {}
            self._str_counter = 0
            self._class_type_export_cache = {}
            self._class_aliases = {}
            self._native_module_aliases = {}
            self._native_builtin_module_aliases = {}
            self._native_builtin_value_aliases = {}
            self._native_file_values = set()
            self._native_file_env_flags = {}
            self._cross_module_func_defs = {}
            self.current_class = None
            self.current_method_kind = None

        self.class_lowering = ClassLowering(self)

        # Pre-pass: hoist nested ``def`` blocks out of outer FuncDef /
        # ClassDef method bodies to the module's top level. pcc has
        # no closure-conversion path yet, so the hoisted function is
        # rewritten with a unique ``__nested_<outer>_<name>`` symbol
        # and the original binding in the enclosing body is replaced
        # by an alias-Assign — ``<inner_name> = <hoisted_name>`` —
        # so direct calls ``inner_name(arg)`` continue to route
        # through the existing user-function call path.
        hoisted = self._hoist_nested_funcdefs()
        self._module_uses_raw_int_scaffold = (
            self._module_imports_raw_int_scaffold()
        )

        # Partition module-level statements into (def-shaped,
        # statement-body). Anything that isn't a FuncDef/ClassDef is
        # queued into the synthesized module-main body so that
        # ``main()`` at file scope still runs at program start.
        main_body: list[Stmt] = []

        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef):
                self._prescan_function_module_globals(stmt)
                self._declare_user_function(stmt)
            elif isinstance(stmt, _ClassDef):
                for class_stmt in stmt.body:
                    if isinstance(class_stmt, FuncDef):
                        self._prescan_function_module_globals(class_stmt)
                self.class_lowering.declare_class(stmt)
            elif isinstance(stmt, _TopImport):
                for mod_name, as_name in stmt.names:
                    if mod_name in (
                        "sys", "os", "platform", "subprocess", "tempfile",
                        "shutil", "shlex", "sysconfig",
                    ):
                        self._register_native_builtin_module_alias(
                            as_name or mod_name, mod_name,
                        )
                        continue
                    # ``import os.path`` (no alias): the binding is
                    # ``os`` (top-level), which should still be treated
                    # as the native ``os`` module — register it as an
                    # alias so ``os.path.X(...)`` and ``os.X(...)``
                    # both reach native dispatch.
                    if as_name is None and "." in mod_name:
                        top = mod_name.split(".")[0]
                        if top in (
                            "sys", "os", "platform", "subprocess",
                            "tempfile", "shutil", "shlex", "sysconfig",
                        ):
                            self._register_native_builtin_module_alias(
                                top, top,
                            )
                            continue
                    # Match _emit_import's binding convention: bind
                    # the top-level for ``import a.b`` (no alias) so
                    # ``a.b.c`` lookups via getattr succeed.
                    if as_name is None and "." in mod_name:
                        local_name = mod_name.split(".")[0]
                    else:
                        local_name = as_name or mod_name
                    self._cpy_module_global(local_name)
                    self._cpy_modules()[local_name] = (
                        self._cpy_module_global(local_name)
                    )
                main_body.append(stmt)
            elif isinstance(stmt, _TopImportFrom):
                # Compile-time scaffold imports (pcc.extern / pcc.llvm_capi)
                # carry no runtime CPython globals — their names are
                # consumed by codegen during the emit pass. Seed the
                # binding set now so extern decls that follow (and
                # extern calls in user functions) see them.
                if self._is_extern_scaffold_import_module(stmt.module):
                    self._register_extern_scaffold_imports(stmt)
                elif stmt.module in self._UNSAFE_SCAFFOLD_MODULES:
                    self._register_unsafe_scaffold_imports(stmt)
                elif self._register_native_builtin_import_from_aliases(
                    stmt, self._resolve_relative_import(stmt),
                ):
                    pass
                else:
                    # Multi-file compile: pre-register native sibling
                    # imports in the first pass so user function bodies
                    # emitted immediately after see the extern binding.
                    # The regular CPython-backed side-effect (allocating
                    # a module global) is skipped for native siblings.
                    native_table = getattr(
                        self, "_native_module_exports", None,
                    )
                    resolved = (
                        self._resolve_relative_import(stmt)
                        if native_table is not None else None
                    )
                    handled_as_native_submodule = False
                    if native_table is not None:
                        remaining_names = []
                        for attr_name, as_name in stmt.names:
                            full_submodule = self._native_import_from_submodule(
                                resolved, attr_name,
                            )
                            if full_submodule is None:
                                full_submodule = (
                                    self._resolve_relative_import_submodule(
                                        stmt, resolved, attr_name,
                                    )
                                )
                            if (
                                full_submodule is not None
                                and full_submodule in native_table
                            ):
                                self._register_native_module_alias(
                                    as_name or attr_name, full_submodule,
                                )
                                continue
                            remaining_names.append((attr_name, as_name))
                        handled_as_native_submodule = not remaining_names
                    if handled_as_native_submodule:
                        pass
                    elif (
                        native_table is not None
                        and self._has_native_import_from_targets(
                            stmt, resolved,
                        )
                    ):
                        self._predeclare_native_cross_module(
                            stmt, resolved,
                            native_table.get(resolved, {}),
                        )
                    else:
                        for attr_name, as_name in stmt.names:
                            if attr_name == "*":
                                self._cpy_star_module_global(stmt.module)
                                continue
                            local_name = as_name or attr_name
                            self._cpy_module_global(local_name)
                            self._cpy_modules()[local_name] = (
                                self._cpy_module_global(local_name)
                            )
                main_body.append(stmt)
            elif (
                isinstance(stmt, Assign)
                and self._maybe_register_extern_assign(stmt)
            ):
                # Pre-register extern("symbol", ...) decls during the
                # declare pass so user-function bodies emitted next can
                # resolve the extern callable. Do NOT append to
                # main_body — nothing runtime to emit.
                pass
            elif (
                isinstance(stmt, _ExprStmt)
                and self._maybe_define_unsafe_global_stmt(stmt)
            ):
                # Compile-time data definition for runtime substrate
                # modules. The generated library object must contain the
                # global symbol even though its synthetic main() is later
                # stripped by the runtime Makefile.
                pass
            elif isinstance(stmt, (_ExprStmt, Assign, AugAssign, If, While, For, Try, With, Delete)):
                # Top-level statements that belong in the synthetic
                # module-main function so they execute at program
                # start. Top-level ``Name = <expr>`` also declares a
                # module-level global so other functions can read it.
                if isinstance(stmt, Assign):
                    self._maybe_register_class_alias_assign(stmt)
                    self._declare_module_globals_for(stmt)
                # Nested Try/With/If bodies may contain ``import X`` /
                # ``from X import Y`` statements whose bindings need to
                # be registered as module globals so downstream function
                # bodies can resolve them. Pre-scan the transitive body
                # for imports without altering runtime semantics.
                if isinstance(stmt, (Try, With, If, While, For)):
                    self._prescan_nested_imports(stmt)
                main_body.append(stmt)
            else:
                raise NotImplementedError(
                    "Layer 1 only supports top-level FuncDef / ClassDef / "
                    f"Import / Assign / AugAssign / ExprStmt / If / While / "
                    f"For / Try / With at module scope; got {type(stmt).__name__}"
                )

        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef):
                self._emit_user_function(stmt)
            elif isinstance(stmt, _ClassDef):
                self.class_lowering.emit_methods(stmt)

        self.class_lowering.emit_module_init()
        # Multi-file compile mode: non-entry modules emit a
        # ``_pcc_py_module_top_<mod>()`` initialiser instead of the
        # program entry ``@main``. The entry module's @main is
        # responsible for calling each other module's top-level init
        # before its own body runs.
        if getattr(self, "_skip_program_main", False):
            self._emit_module_top_init(main_body)
        else:
            self._emit_program_main(main_body)

        return str(self.module)

    def _prescan_nested_imports(self, stmt) -> None:
        """Walk ``stmt``'s transitive body for Import / ImportFrom
        statements and seed ``_cpy_module_env`` so downstream user
        function bodies can resolve the names. The runtime import
        still runs inside the original stmt's body at main_body
        execution time; the scan only registers compile-time globals.
        """
        from ..py_ast import (
            Import as _Import, ImportFrom as _ImportFrom,
        )
        def walk(s):
            if isinstance(s, _Import):
                for mod_name, as_name in s.names:
                    if mod_name.split(".")[0] in self._COMPILE_TIME_ONLY_MODULES:
                        continue
                    if mod_name in (
                        "sys", "os", "platform", "subprocess", "tempfile",
                        "shutil", "shlex", "sysconfig",
                    ):
                        self._register_native_builtin_module_alias(
                            as_name or mod_name, mod_name,
                        )
                        continue
                    if as_name is None and "." in mod_name:
                        local_name = mod_name.split(".")[0]
                    else:
                        local_name = as_name or mod_name
                    self._cpy_module_global(local_name)
                    self._cpy_modules()[local_name] = (
                        self._cpy_module_global(local_name)
                    )
                return
            if isinstance(s, _ImportFrom):
                if self._is_extern_scaffold_import_module(s.module):
                    self._register_extern_scaffold_imports(s)
                    return
                if s.module in self._UNSAFE_SCAFFOLD_MODULES:
                    self._register_unsafe_scaffold_imports(s)
                    return
                if self._register_native_builtin_import_from_aliases(
                    s, self._resolve_relative_import(s),
                ):
                    return
                for attr_name, as_name in s.names:
                    if attr_name == "*":
                        self._cpy_star_module_global(s.module)
                        continue
                    local_name = as_name or attr_name
                    self._cpy_module_global(local_name)
                    self._cpy_modules()[local_name] = (
                        self._cpy_module_global(local_name)
                    )
                return
            for slot in _dataclass_field_names(s):
                if slot in ("span",):
                    continue
                v = _dataclass_field_value(s, slot, None)
                if isinstance(v, tuple):
                    for it in v:
                        if _dataclass_field_names(it):
                            walk(it)
                elif v is not None and _dataclass_field_names(v):
                    walk(v)
        walk(stmt)

    def _collect_explicit_global_names(
        self, stmts: tuple[Stmt, ...],
    ) -> set[str]:
        """Return names declared ``global`` in ``stmts``.

        Nested blocks participate in the same function scope; nested
        ``def`` / ``class`` bodies do not.
        """
        from ..py_ast import (
            ClassDef as _ClassDef,
            FuncDef as _FuncDef,
            Global as _Global,
        )

        names: set[str] = set()

        def walk(items: tuple[Stmt, ...]) -> None:
            for s in items:
                if isinstance(s, _Global):
                    names.update(s.names)
                    continue
                if isinstance(s, (_FuncDef, _ClassDef)):
                    continue
                if isinstance(s, If):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, While):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, For):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, With):
                    walk(s.body)
                    continue
                if isinstance(s, Try):
                    walk(s.body)
                    for h in s.handlers:
                        walk(h.body)
                    walk(s.else_body)
                    walk(s.finally_body)

        walk(stmts)
        return names

    def _ensure_module_global_name(
        self, name: str, target_ty: Type,
    ) -> tuple[ir.GlobalVariable, Type]:
        """Return the storage slot for a module-global name."""
        existing = self._module_globals.get(name)
        if existing is not None:
            return existing
        if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
            raise NotImplementedError(
                f"Layer 1/2 cannot allocate module global {name!r} "
                f"of type {type(target_ty).__name__}"
            )
        if isinstance(target_ty, IntType) and self._should_box_python_ints():
            ir_ty = _CSTR
        else:
            ir_ty = self._storage_ir_type(target_ty)
        gv = ir.GlobalVariable(
            self.module, ir_ty, name=f".modvar.{name}",
        )
        gv.linkage = "internal"
        gv.initializer = ir.Constant(ir_ty, _zero_initializer_for(ir_ty))
        self._module_globals[name] = (gv, target_ty)
        return self._module_globals[name]

    def _prescan_function_module_globals(self, fd: FuncDef) -> None:
        """Seed module-global storage for names assigned under
        ``global`` inside ``fd`` so sibling functions can resolve them.
        """
        global_names = self._collect_explicit_global_names(fd.body)
        if not global_names:
            return
        from ..py_ast import (
            ClassDef as _ClassDef,
            FuncDef as _FuncDef,
        )

        def walk(items: tuple[Stmt, ...]) -> None:
            for s in items:
                if isinstance(s, (_FuncDef, _ClassDef)):
                    continue
                if isinstance(s, Assign):
                    target_ty = (
                        s.annotation if s.annotation is not None
                        else s.value.ty
                    )
                    for t in s.targets:
                        if (
                            isinstance(t, Name)
                            and t.ident in global_names
                        ):
                            self._ensure_module_global_name(
                                t.ident, target_ty,
                            )
                    continue
                if isinstance(s, AugAssign):
                    if (
                        isinstance(s.target, Name)
                        and s.target.ident in global_names
                    ):
                        self._ensure_module_global_name(
                            s.target.ident, s.target.ty,
                        )
                    continue
                if isinstance(s, Import):
                    for mod_name, as_name in s.names:
                        bound = as_name or mod_name.split(".", 1)[0]
                        if bound in global_names:
                            gv = self._cpy_module_global(bound)
                            self._cpy_modules()[bound] = gv
                    continue
                if isinstance(s, ImportFrom):
                    for imported_name, as_name in s.names:
                        if imported_name == "*":
                            continue
                        bound = as_name or imported_name
                        if bound in global_names:
                            gv = self._cpy_module_global(bound)
                            self._cpy_modules()[bound] = gv
                    continue
                if isinstance(s, If):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, While):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, For):
                    walk(s.body)
                    walk(s.else_body)
                    continue
                if isinstance(s, With):
                    walk(s.body)
                    continue
                if isinstance(s, Try):
                    walk(s.body)
                    for h in s.handlers:
                        walk(h.body)
                    walk(s.else_body)
                    walk(s.finally_body)

        walk(fd.body)

    def _resolve_class_alias(self, name: str) -> str:
        return getattr(self, "_class_aliases", {}).get(name, name)

    def _maybe_register_class_alias_assign(self, stmt: Assign) -> bool:
        if len(stmt.targets) != 1:
            return False
        target = stmt.targets[0]
        value = stmt.value
        if not isinstance(target, Name) or not isinstance(value, Name):
            return False
        class_name = self._resolve_class_alias(value.ident)
        if class_name not in self.class_lowering.classes:
            return False
        self._class_aliases[target.ident] = class_name
        return True

    def _declare_module_globals_for(self, stmt: Assign) -> None:
        """Allocate a module-level global for each simple Name target of
        a module-scope assignment so user functions can later load
        the same binding."""
        target_ty = stmt.annotation if stmt.annotation is not None \
            else stmt.value.ty
        for t in stmt.targets:
            if not isinstance(t, Name):
                continue
            if not (
                self._is_scalar(target_ty) or self._is_object(target_ty)
            ):
                continue
            self._ensure_module_global_name(t.ident, target_ty)

    def _emit_module_top_init(self, body: list["Stmt"]) -> None:
        """Emit ``void _pcc_py_module_top_<mod>()`` holding the
        module-level statements. Used when this compilation unit is a
        secondary module in a multi-file compile — the entry module's
        ``@main`` must call this before its own top-level body."""
        mod_name = self.ast_module.name or "mod"
        sanitised = mod_name.replace(".", "_").replace("-", "_")
        fnty = ir.FunctionType(_VOID, [])
        fn = ir.Function(
            self.module, fnty, name=f"_pcc_py_module_top_{sanitised}",
        )
        fn.linkage = "external"
        entry = fn.append_basic_block("entry")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn
        self.current_func_def = None
        self.env = {}
        self._box_int_locals = self._should_box_python_ints()
        self._exact_int_env_flags = {}
        self.loop_stack = []

        if self.class_lowering.classes:
            init_name = f"_pcc_py_module_init_{sanitised}"
            init_fn = self.module.globals.get(init_name)
            if isinstance(init_fn, ir.Function):
                self.builder.call(init_fn, [])

        self._emit_stmts(tuple(body))

        if not self._builder_block_is_terminated():
            self.builder.ret_void()

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self._box_int_locals = saved_box_int_locals
        self._exact_int_env_flags = saved_exact_int_flags
        self.loop_stack = saved_loops

    def _emit_program_main(self, body: list["Stmt"]) -> None:
        """Synthesize ``i32 @main(i32 argc, i8** argv)`` holding
        module-level statements.

        Runs the ``_pcc_py_module_init_<mod>`` ctor first (populates
        class globals) and then emits each queued module-level
        statement. Returns 0.
        """
        if self.module.globals.get("main") is not None:
            # User provided a C-style ``main`` function already; leave
            # it alone. This is a pcc-py convention for hand-written
            # entry points.
            return

        fnty = ir.FunctionType(_I32, [_I32, _CSTR.as_pointer()])
        fn = ir.Function(self.module, fnty, name="main")
        fn.args[0].name = "argc"
        fn.args[1].name = "argv"
        entry = fn.append_basic_block("entry")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn
        self.current_func_def = None
        self.env = {}
        self._box_int_locals = self._should_box_python_ints()
        self._exact_int_env_flags = {}
        self.loop_stack = []

        self.builder.call(
            self.runtime["py_set_program_args"],
            [fn.args[0], fn.args[1]],
        )

        # Call other-module top-inits first (multi-file compile).
        # Each declared-external void function executes the sibling
        # module's class init + top-level statements.
        for sibling_mod in getattr(self, "_sibling_module_inits", ()):
            sanitised_sib = sibling_mod.replace(".", "_").replace("-", "_")
            sib_top = f"_pcc_py_module_top_{sanitised_sib}"
            existing = self.module.globals.get(sib_top)
            if existing is None:
                sib_fn = ir.Function(
                    self.module, ir.FunctionType(_VOID, []), name=sib_top,
                )
                sib_fn.linkage = "external"
            else:
                sib_fn = existing
            self.builder.call(sib_fn, [])
            self._emit_post_call_err_check()

        # Call module init (populates class globals) if any classes
        # were lowered.
        if self.class_lowering.classes:
            mod_name = self.ast_module.name or "mod"
            sanitised_mod = mod_name.replace(".", "_").replace("-", "_")
            init_name = f"_pcc_py_module_init_{sanitised_mod}"
            init_fn = self.module.globals.get(init_name)
            if isinstance(init_fn, ir.Function):
                self.builder.call(init_fn, [])
                self._emit_post_call_err_check()

        self._emit_stmts(tuple(body))

        if not self._builder_block_is_terminated():
            if self.emit_cpy_main_exitcode:
                exit_code = self.builder.call(
                    self.runtime["py_cpy_main_exitcode"], [],
                    name=self._fresh("cpy.exitcode"),
                )
            else:
                exit_code = ir.Constant(_I32, 0)
            self.builder.ret(exit_code)

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        self.env = saved_env
        self._box_int_locals = saved_box_int_locals
        self._exact_int_env_flags = saved_exact_int_flags
        self.loop_stack = saved_loops

    # ---------------------------------------------------------------- helpers

    def _fresh(self, hint: str = "t") -> str:
        self._tmp_counter += 1
        return f"{hint}.{self._tmp_counter}"

    def _declare_printf(self) -> ir.Function:
        existing = self.module.globals.get("printf")
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_I32, [_CSTR], var_arg=True)
        fn = ir.Function(self.module, fnty, name="printf")
        fn.linkage = "external"
        return fn

    def _declare_external_function(
        self,
        name: str,
        ret_ty: ir.Type,
        param_tys: list[ir.Type],
        *,
        var_arg: bool = False,
    ) -> ir.Function:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=var_arg)
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    # -- type mapping --------------------------------------------------

    def _module_imports_raw_int_scaffold(self) -> bool:
        mod_name = getattr(self.ast_module, "name", "") or ""
        if mod_name == "pcc" or mod_name.startswith("pcc."):
            return True
        if mod_name == "bootstrap" or mod_name.startswith("bootstrap."):
            return True
        for stmt in self.ast_module.body:
            if isinstance(stmt, ImportFrom):
                if (
                    self._is_extern_scaffold_import_module(stmt.module)
                    or stmt.module in self._UNSAFE_SCAFFOLD_MODULES
                ):
                    return True
            if isinstance(stmt, Import):
                for mod_name, _as_name in stmt.names:
                    if (
                        self._is_extern_scaffold_import_module(mod_name)
                        or mod_name in self._UNSAFE_SCAFFOLD_MODULES
                    ):
                        return True
        return False

    def _should_box_python_ints(self) -> bool:
        return not self._module_uses_raw_int_scaffold

    def _int_exprs_are_boxed(self) -> bool:
        return bool(getattr(self, "_box_int_locals", False))

    def _storage_ir_type(self, ty: Type) -> ir.Type:
        if isinstance(ty, IntType) and self._int_exprs_are_boxed():
            return _CSTR
        return self._map_type(ty)

    def _abi_ir_type(self, ty: Type, *, box_int_abi: bool) -> ir.Type:
        if box_int_abi and isinstance(ty, IntType):
            return _CSTR
        return self._map_type(ty)

    def _export_box_int_abi(self, info: dict) -> bool:
        return bool(info.get("box_int_abi", self._should_box_python_ints()))

    def _map_type(self, ty: Type) -> ir.Type:
        """Map a pcc_py :class:`Type` to its LLVM IR representation.

        Phase 1 scalars lower to native types; Phase 2 object types
        (str / list / dict / tuple / None) lower to ``PyObject*`` (an
        opaque pointer).
        """
        if isinstance(ty, IntType):
            # We always lower to i64 in L1 regardless of the declared
            # width; the type-infer layer is expected to have
            # range-checked narrower widths already. The ``width`` field
            # will matter once tagged-int codegen lands in Phase 2.
            return _I64
        if isinstance(ty, FloatType):
            return _DOUBLE
        if isinstance(ty, BoolType):
            return _I1
        if isinstance(ty, (StrType, ListType, DictType, TupleType, ClassType)):
            return _CSTR  # alias for i8* == PyObject*
        if isinstance(ty, NoneType):
            # None is a PyObject* (points to the global ``py_None``).
            # Using a pointer (not void) lets us store and load None in
            # locals uniformly with other object types.
            return _CSTR
        if isinstance(ty, DynType):
            # A generic PyObject* slot: covers class instances, results
            # of ``MyClass(args)`` construction, attribute fetches, and
            # anything else the type inferer did not narrow.
            return _CSTR
        if isinstance(ty, FuncType):
            # A first-class function value — at L1 the callable is
            # wrapped as a CPython object (lambda lowered to
            # ``operator.<getter>`` or a hoisted pcc FuncDef exposed
            # through PyCFunction wrapping). Either way the local slot
            # holds an opaque PyObject* pointer.
            return _CSTR
        raise NotImplementedError(
            f"Layer 1 does not handle type {type(ty).__name__} "
            f"(name={getattr(ty, 'name', '?')!r})"
        )

    def _is_scalar(self, ty: Type) -> bool:
        return isinstance(ty, (IntType, FloatType, BoolType))

    def _is_object(self, ty: Type) -> bool:
        return isinstance(
            ty,
            (StrType, ListType, DictType, TupleType, ClassType, NoneType, DynType,
             FuncType),
        )

    def _param_ir_and_bind_type(
        self,
        arg,
        *,
        require_annotation: bool,
        owner_name: str,
        box_int_params: bool = False,
    ) -> tuple[ir.Type, Type | None]:
        """Return the IR param type plus the env-binding type for ``arg``.

        ``*args`` and ``**kwargs`` lower as ordinary PyObject* params
        carrying a tuple / dict value respectively. That keeps function
        bodies compilable even before full L3 vararg semantics land.
        """
        if arg.kind in ("pos", "pos_only", "kw_only"):
            if arg.annotation is None:
                if require_annotation:
                    raise L1CodegenError(
                        f"Layer 1 requires an annotation on parameter "
                        f"{arg.name!r} of function {owner_name!r}"
                    )
                return _CSTR, DynType(name="dyn")
            if box_int_params and isinstance(arg.annotation, IntType):
                return _CSTR, arg.annotation
            return self._map_type(arg.annotation), arg.annotation
        if arg.kind == "*args":
            return _CSTR, TupleType(name="tuple", elems=())
        if arg.kind == "**kwargs":
            return _CSTR, DictType(
                name="dict",
                key=StrType(name="str"),
                value=DynType(name="dyn"),
            )
        raise NotImplementedError(
            f"Layer 1 parameter kind {arg.kind!r} "
            f"(in function {owner_name!r}) not supported"
        )

    # -- string / attribute name globals -------------------------------

    def _utf8_byte_values(self, payload: str) -> list[int]:
        """Return UTF-8 byte values for ``payload`` without a bytes object.

        The self-hosted compiler cannot depend on CPython ``bytes`` or
        ``str.encode`` here: these globals are emitted while compiling
        user programs. Keep the helper in ordinary Python so CPython
        and pcc-Python execute the same code path.
        """
        out: list[int] = []
        i = 0
        n = len(payload)
        while i < n:
            cp = ord(payload[i])
            if cp <= 127:
                out.append(cp)
            elif cp <= 2047:
                out.append(192 | (cp >> 6))
                out.append(128 | (cp & 63))
            elif cp <= 65535:
                out.append(224 | (cp >> 12))
                out.append(128 | ((cp >> 6) & 63))
                out.append(128 | (cp & 63))
            else:
                out.append(240 | (cp >> 18))
                out.append(128 | ((cp >> 12) & 63))
                out.append(128 | ((cp >> 6) & 63))
                out.append(128 | (cp & 63))
            i += 1
        return out

    def _cstr_literal(self, payload: str) -> tuple[ir.GlobalVariable, int]:
        """Intern a UTF-8 byte array as an internal global.

        Returns ``(gv, byte_len)`` where ``byte_len`` excludes the
        trailing NUL. Emitted globals are named ``.pystr.<N>`` per the
        L2 convention in the task brief.
        """
        data = self._utf8_byte_values(payload)
        existing = self._str_pool.get(payload)
        if existing is not None:
            # Array length minus the NUL terminator.
            arr_ty = existing.type.pointee
            return existing, arr_ty.count - 1
        self._str_counter += 1
        name = f".pystr.{self._str_counter}"
        body = data + [0]
        arr_ty = ir.ArrayType(_I8, len(body))
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, body)
        self._str_pool[payload] = gv
        return gv, len(data)

    def _attr_name_ptr(self, name: str) -> ir.Value:
        """Return an i8* pointing at a NUL-terminated attribute name.

        These globals are short-lived (attribute-access use only) and
        intentionally distinct from :meth:`_cstr_literal` so a later
        optimiser can fold them if it wishes.
        """
        existing = self._attr_pool.get(name)
        if existing is None:
            data = self._utf8_byte_values(name) + [0]
            arr_ty = ir.ArrayType(_I8, len(data))
            sym = f".pyattr.{name}"
            # Multiple distinct attrs may share a name; disambiguate.
            if sym in self.module.globals:
                sym = f".pyattr.{name}.{len(self._attr_pool)}"
            gv = ir.GlobalVariable(self.module, arr_ty, name=sym)
            gv.linkage = "internal"
            gv.global_constant = True
            gv.initializer = ir.Constant(arr_ty, data)
            self._attr_pool[name] = gv
            existing = gv
        zero = ir.Constant(_I32, 0)
        return self.builder.gep(existing, [zero, zero], inbounds=True)

    def _emit_str_literal(self, value: str) -> ir.Value:
        """Emit ``py_str_new(ptr, byte_len)`` for a string literal."""
        gv, byte_len = self._cstr_literal(value)
        zero = ir.Constant(_I32, 0)
        ptr = self.builder.gep(gv, [zero, zero], inbounds=True,
                                 name=self._fresh("pystr.ptr"))
        length = ir.Constant(_I64, byte_len)
        return self.builder.call(
            self.runtime["py_str_new"], [ptr, length],
            name=self._fresh("str.new"),
        )

    def _emit_none_literal(self) -> ir.Value:
        """Load the runtime ``py_None`` const pointer."""
        gv = declare_runtime_global(self.module, "py_None")
        return self.builder.load(gv, name=self._fresh("none"))

    # -- format-string globals ----------------------------------------

    def _cstr_global(self, payload: str, name: str) -> ir.GlobalVariable:
        data = self._utf8_byte_values(payload) + [0]
        arr_ty = ir.ArrayType(_I8, len(data))
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        gv = ir.GlobalVariable(self.module, arr_ty, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, data)
        return gv

    def _ptr_to_cstr(self, gv: ir.GlobalVariable) -> ir.Value:
        zero = ir.Constant(_I32, 0)
        return self.builder.gep(gv, [zero, zero], inbounds=True)

    def _get_fmt_int(self) -> ir.GlobalVariable:
        if self._fmt_int is None:
            self._fmt_int = self._cstr_global("%ld\n", ".fmt_int")
        return self._fmt_int

    def _get_fmt_float(self) -> ir.GlobalVariable:
        if self._fmt_float is None:
            # Use %g for a Python-ish default; this is NOT bit-for-bit
            # Python's repr and will be upgraded in Phase 2 when the
            # runtime lib is wired in for repr.
            self._fmt_float = self._cstr_global("%g\n", ".fmt_float")
        return self._fmt_float

    def _get_fmt_bool_true(self) -> ir.GlobalVariable:
        if self._fmt_bool_true is None:
            self._fmt_bool_true = self._cstr_global("True\n", ".fmt_true")
        return self._fmt_bool_true

    def _get_fmt_bool_false(self) -> ir.GlobalVariable:
        if self._fmt_bool_false is None:
            self._fmt_bool_false = self._cstr_global("False\n", ".fmt_false")
        return self._fmt_bool_false

    # -- nested-def hoisting -------------------------------------------

    def _hoist_nested_funcdefs(self) -> list:
        """Walk every top-level FuncDef / ClassDef method body and
        collect any nested FuncDef to the module's top-level body.

        The nested ``def`` is renamed ``__nested_<outer>_<name>`` and
        re-attached to ``self.ast_module.body``. No closure conversion
        — if the hoisted function reads an outer local, codegen
        surfaces the usual ``unbound name`` error at its first
        reference. Common use in pcc's own sources (regex callbacks,
        comparator helpers) doesn't capture anything, so the hoist
        alone buys a lot.

        Also rewrites same-scope ``Call(Name(<inner>), ...)`` sites
        in the outer body to route through the new hoisted symbol.
        Returns the list of hoisted FuncDef nodes (caller re-declares
        them via ``_declare_user_function`` during the normal scan).
        """
        hoisted: list = []
        self._hoisted_capture_params = {}
        self._hoisted_class_capture_params = {}

        def clone_funcdef(fd, name, args, return_ty, body):
            return _FuncDef(
                fd.span,
                name,
                args,
                return_ty,
                body,
                fd.decorators,
                fd.is_method,
                fd.is_async,
            )

        def name_in(items, name):
            for item in items:
                if item == name:
                    return True
            return False

        def append_name_once(items, name):
            if not name_in(items, name):
                items.append(name)

        def extend_names_once(items, names):
            for name in names:
                append_name_once(items, name)

        def copy_names(names):
            out = []
            extend_names_once(out, names)
            return out

        def copy_name_map(src):
            out = {}
            for key, value in src.items():
                out[key] = value
            return out

        def update_name_map(dst, src):
            for key, value in src.items():
                dst[key] = value

        def is_discard_capture_name(name):
            return (
                name == "_"
                or name == "*"
                or name == "**"
                or name.startswith("__nested_")
            )

        def filter_capture_names(names):
            out = []
            for name in names:
                if not is_discard_capture_name(name):
                    out.append(name)
            return tuple(out)

        def filter_self_capture_names(names, original_name, hoisted_name):
            out = []
            for name in names:
                if (
                    name != original_name
                    and name != hoisted_name
                    and not is_discard_capture_name(name)
                ):
                    out.append(name)
            return tuple(out)

        def body_reads_self(stmts):
            """Return True if any Name(ident='self') appears read in
            ``stmts`` (not just as a write target). Used to skip
            hoisting defs that close over the enclosing method's
            ``self`` — those would become unbound at module scope."""
            found = [False]
            def walk(x):
                if found[0]:
                    return
                if isinstance(x, _Name) and x.ident == "self":
                    found[0] = True
                    return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it)
                    else:
                        walk(v)
            for s in stmts:
                walk(s)
            return found[0]

        def body_uses_name_as_value(stmts, target_name):
            """Return True if ``target_name`` appears anywhere as a
            non-Call reference (e.g. ``re.sub(target_name, text)``
            passes it as a value). We walk the surrounding body
            (not the def itself) because value-position reads happen
            in the OUTER scope where the def's name is bound."""
            found = [False]

            def walk(x, is_call_func=False, in_lambda=False):
                if found[0]:
                    return
                if isinstance(x, _Name):
                    if (
                        x.ident == target_name
                        and (not is_call_func or in_lambda)
                    ):
                        found[0] = True
                    return
                if isinstance(x, _Lambda):
                    for p in x.params:
                        if p.name == target_name:
                            return
                    walk(x.body, in_lambda=True)
                    return
                if isinstance(x, _Call):
                    walk(x.func, is_call_func=True, in_lambda=in_lambda)
                    for a in x.args:
                        walk(a, in_lambda=in_lambda)
                    for _, v in x.kwargs:
                        walk(v, in_lambda=in_lambda)
                    return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it, in_lambda=in_lambda)
                    else:
                        walk(v, in_lambda=in_lambda)
            for s in stmts:
                if isinstance(s, _FuncDef) and s.name == target_name:
                    # Don't walk the def itself — the name inside its
                    # body is a separate binding.
                    continue
                walk(s)
            return found[0]

        def body_augassigns_free_name(fd, excluded):
            """Return True if the nested def has an ``AugAssign``
            whose target is a name not bound locally (i.e. a free
            var being mutated). Closure-convert-by-value would lose
            the mutation; leave the def unhoisted so the honest
            FuncDef error surfaces rather than silently
            miscompiling."""
            param_names = {a.name for a in fd.args if a.name != ""}
            assigned_names: set = set()
            for s in fd.body:
                if isinstance(s, _Assign):
                    for t in s.targets:
                        if isinstance(t, _Name):
                            assigned_names.add(t.ident)
            local = param_names | assigned_names
            found = [False]

            def walk(x):
                if found[0]:
                    return
                if isinstance(x, _AugAssign) and isinstance(x.target, _Name):
                    if x.target.ident not in local:
                        found[0] = True
                        return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it)
                    else:
                        walk(v)
            for s in fd.body:
                walk(s)
            return found[0]

        def mutable_captures_in_fd(fd, excluded):
            """Return the set of free-var names that the nested ``fd``
            mutates via ``Name = v`` / ``name += v``. These are the
            candidates for list-box closure conversion — the outer
            scope and the hoisted inner both rewrite ``X`` references
            as ``X[0]`` subscripts, with a shared list allocation
            in outer scope."""
            free = compute_free_names(fd, excluded)
            if not free:
                return ()
            mutated = []

            def walk(x):
                if isinstance(x, _Assign):
                    for t in x.targets:
                        if isinstance(t, _Name) and name_in(free, t.ident):
                            append_name_once(mutated, t.ident)
                    for slot in ("value",):
                        walk(_dataclass_field_value(x, slot))
                    return
                if isinstance(x, _AugAssign):
                    if isinstance(x.target, _Name) and name_in(free, x.target.ident):
                        append_name_once(mutated, x.target.ident)
                    walk(x.value)
                    return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it)
                    else:
                        walk(v)
            for s in fd.body:
                walk(s)
            return tuple(mutated)

        def box_expr(expr, boxed):
            """Rewrite every ``Name(X)`` read in ``expr`` to
            ``Subscript(Name(X), IntLit(0))`` for X in ``boxed``.
            ``Call.func`` at the top of a Call is left alone — the
            name-to-box substitution only applies when the reference
            is at VALUE position (reading the box's current value);
            callable boxes are a separate concern."""
            _INT = IntType(name="int")

            def go(e):
                if e is None:
                    return e
                if isinstance(e, _Name) and e.ident in boxed:
                    # The boxed var is a list containing one element.
                    # The read through ``X[0]`` returns the element as
                    # a generic PyObject* (DynType) — downstream
                    # arithmetic unboxes via ``py_int_to_i64``, and
                    # ``print`` goes through the DynType dispatch.
                    return Subscript(
                        span=e.span, ty=_DYN,
                        obj=_replace(e, ty=_DYN),
                        idx=IntLit(span=e.span, ty=_INT, value=0),
                    )
                if isinstance(e, _Call):
                    return _replace(
                        e,
                        func=go(e.func),
                        args=tuple(go(a) for a in e.args),
                        kwargs=tuple((k, go(v)) for (k, v) in e.kwargs),
                    )
                # Generic dataclass-fields recursion.
                fields = _dataclass_field_names(e)
                if not fields:
                    return e
                new_fields = {}
                for slot in fields:
                    v = _dataclass_field_value(e, slot, None)
                    if slot == "span":
                        continue
                    if isinstance(v, tuple):
                        items = tuple(go(it) for it in v)
                        new_fields[slot] = items
                    else:
                        new_fields[slot] = go(v) if _dataclass_field_names(v) else v
                # Only call _replace when we actually changed something
                # and the field names match the dataclass.
                return _replace(e, **new_fields) if new_fields else e
            return go(expr)

        def box_stmts(stmts, boxed):
            """Rewrite a list of statements so assignments to boxed
            names become subscript stores (``X = v`` → ``X[0] = v``)
            and reads become subscript loads (handled via
            ``box_expr``). AugAssign targets are similarly rewritten.
            Recurses into nested blocks (If / For / While / Try /
            With). Does NOT recurse into nested FuncDef bodies —
            that happens during the hoist pass with a fresh scope."""
            _INT = IntType(name="int")

            def make_sub(name_ident, span, ty):
                return Subscript(
                    span=span, ty=ty,
                    obj=_Name(span=span, ty=_DYN, ident=name_ident),
                    idx=IntLit(span=span, ty=_INT, value=0),
                )

            out = []
            for st in stmts:
                if isinstance(st, _Assign) and len(st.targets) == 1:
                    t = st.targets[0]
                    new_value = box_expr(st.value, boxed)
                    if isinstance(t, _Name) and t.ident in boxed:
                        out.append(_replace(
                            st,
                            targets=(make_sub(t.ident, t.span, t.ty),),
                            value=new_value,
                        ))
                        continue
                    out.append(_replace(st, value=new_value))
                    continue
                if isinstance(st, _AugAssign):
                    new_value = box_expr(st.value, boxed)
                    if isinstance(st.target, _Name) and st.target.ident in boxed:
                        out.append(_replace(
                            st,
                            target=make_sub(st.target.ident, st.target.span, st.target.ty),
                            value=new_value,
                        ))
                        continue
                    out.append(_replace(st, value=new_value))
                    continue
                if isinstance(st, _If):
                    out.append(_replace(
                        st,
                        cond=box_expr(st.cond, boxed),
                        body=box_stmts(st.body, boxed),
                        else_body=box_stmts(st.else_body, boxed),
                    ))
                    continue
                if isinstance(st, _While):
                    out.append(_replace(
                        st,
                        cond=box_expr(st.cond, boxed),
                        body=box_stmts(st.body, boxed),
                        else_body=box_stmts(st.else_body, boxed),
                    ))
                    continue
                if isinstance(st, _For):
                    out.append(_replace(
                        st,
                        iter=box_expr(st.iter, boxed),
                        body=box_stmts(st.body, boxed),
                        else_body=box_stmts(st.else_body, boxed),
                    ))
                    continue
                if isinstance(st, _Try):
                    out.append(_replace(
                        st,
                        body=box_stmts(st.body, boxed),
                        else_body=box_stmts(st.else_body, boxed),
                        finally_body=box_stmts(st.finally_body, boxed),
                        handlers=tuple(
                            _replace(h, body=box_stmts(h.body, boxed))
                            for h in st.handlers
                        ),
                    ))
                    continue
                if isinstance(st, _With):
                    out.append(_replace(
                        st, body=box_stmts(st.body, boxed),
                    ))
                    continue
                if isinstance(st, _ExprStmt):
                    out.append(_replace(st, expr=box_expr(st.expr, boxed)))
                    continue
                if isinstance(st, _Return):
                    if st.value is None:
                        out.append(st)
                    else:
                        out.append(_replace(st, value=box_expr(st.value, boxed)))
                    continue
                # Nested FuncDef body — rewrite recursively (its free
                # reads of the boxed var also need to become X[0]).
                if isinstance(st, _FuncDef):
                    out.append(clone_funcdef(
                        st, st.name, st.args, st.return_ty,
                        box_stmts(st.body, boxed),
                    ))
                    continue
                out.append(st)
            return tuple(out)

        def collect_all_mutable_captures(body):
            """Walk a function body and return a set of names that
            any nested FuncDef mutates as a free var. Used to decide
            which outer locals to box."""
            boxed = []
            for st in body:
                if isinstance(st, _FuncDef):
                    extend_names_once(boxed, mutable_captures_in_fd(st, ()))
                    # Recurse into deeply nested defs.
                    extend_names_once(boxed, collect_all_mutable_captures(st.body))
                    continue
                for slot in _dataclass_field_names(st):
                    v = _dataclass_field_value(st, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            if _dataclass_field_names(it):
                                if isinstance(it, _FuncDef):
                                    extend_names_once(
                                        boxed,
                                        mutable_captures_in_fd(it, ()),
                                    )
                                    extend_names_once(
                                        boxed,
                                        collect_all_mutable_captures(it.body),
                                    )
                                else:
                                    pass
            return tuple(boxed)

        def box_outer_body(body):
            """Top-level entry: if any nested def in ``body`` mutates
            free vars, prepend ``X = [None]`` sentinel assigns for
            each and rewrite all reads/writes of those names to go
            through ``X[0]``. Returns the rewritten body."""
            from ..py_ast import ListExpr as _List, NoneLit as _None
            boxed = collect_all_mutable_captures(body)
            if not boxed:
                return body
            # First rewrite the body so every X read/write uses X[0].
            rewritten = box_stmts(body, boxed)
            # Prepend sentinel allocations so the name binds to a list
            # before any user read/write touches it.
            span = body[0].span if body else None
            sentinels = []
            for name in sorted(boxed):
                sentinel = _Assign(
                    span=span,
                    targets=(_Name(span=span, ty=_DYN, ident=name),),
                    value=_List(
                        span=span,
                        ty=ListType(name="list", elem=DynType(name="dyn")),
                        elems=(_None(span=span, ty=NoneType(name="None")),),
                    ),
                    annotation=None,
                )
                sentinels.append(sentinel)
            return tuple(sentinels) + rewritten

        def compute_free_names(fd, excluded, own_name=None):
            """Return the sorted tuple of Name idents that ``fd``'s
            body reads but aren't bound in its param list, a local
            assignment, a module-level symbol, a Python builtin, its
            own self-reference, or one of the ``excluded`` names.

            Callers that only want a bool can use
            ``bool(compute_free_names(...))``. Closure conversion
            uses the actual name set to append synthetic params."""
            param_names = []
            for a in fd.args:
                if a.name != "":
                    append_name_once(param_names, a.name)
            assigned_names = []
            module_names = []
            from ..py_ast import (
                Assign as _AssignStmt,
                Import as _ImportStmtTop,
                ImportFrom as _ImportFromStmtTop,
                TupleExpr as _TupleExprTop,
            )

            def add_module_target_names(t):
                if isinstance(t, _Name):
                    append_name_once(module_names, t.ident)
                elif isinstance(t, _TupleExprTop):
                    for e in t.elems:
                        add_module_target_names(e)

            for s in self.ast_module.body:
                if isinstance(s, (_FuncDef, _ClassDef)):
                    append_name_once(module_names, s.name)
                elif isinstance(s, _ImportStmtTop):
                    for mod_name, as_name in s.names:
                        bound = as_name or mod_name.split(".", 1)[0]
                        if bound:
                            append_name_once(module_names, bound)
                elif isinstance(s, _ImportFromStmtTop):
                    for imported_name, as_name in s.names:
                        if imported_name == "*":
                            continue
                        append_name_once(module_names, as_name or imported_name)
                elif isinstance(s, _AssignStmt):
                    for target in s.targets:
                        add_module_target_names(target)
            extend_names_once(module_names, excluded)
            if own_name is not None:
                append_name_once(module_names, own_name)
            # ``fd.name`` is in scope for recursive self-calls.
            append_name_once(module_names, fd.name)

            from ..py_ast import TupleExpr as _TupleExpr

            def add_target_names(t):
                if isinstance(t, _Name):
                    append_name_once(assigned_names, t.ident)
                elif isinstance(t, _TupleExpr):
                    for e in t.elems:
                        add_target_names(e)

            # Names declared ``nonlocal`` / ``global`` in the body are
            # explicit outer-scope references, not local bindings —
            # even an ``X = v`` assignment to such a name writes to
            # the outer binding, not a local. Track them and exclude
            # from ``assigned_names``.
            nonlocal_names = []
            from ..py_ast import (
                Global as _GL,
                Import as _ImportStmt,
                ImportFrom as _ImportFromStmt,
                Nonlocal as _NL,
            )

            def collect_nonlocal_global(stmts):
                for s in stmts:
                    if isinstance(s, (_NL, _GL)):
                        extend_names_once(nonlocal_names, s.names)
                    elif isinstance(s, _If):
                        collect_nonlocal_global(s.body)
                        collect_nonlocal_global(s.else_body)
                    elif isinstance(s, _For):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _While):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _With):
                        collect_nonlocal_global(s.body)
                    elif isinstance(s, _Try):
                        collect_nonlocal_global(s.body)
                        collect_nonlocal_global(s.else_body)
                        collect_nonlocal_global(s.finally_body)
            collect_nonlocal_global(fd.body)

            def collect_assigned(stmts):
                for s in stmts:
                    if isinstance(s, _Assign):
                        for t in s.targets:
                            if isinstance(t, _Name) and name_in(nonlocal_names, t.ident):
                                continue
                            add_target_names(t)
                    elif isinstance(s, _AugAssign):
                        # AugAssign requires the target to already be
                        # bound; it doesn't create a local on its own.
                        # Don't add to assigned_names so the free-var
                        # walker treats the read-modify-write as a
                        # capture if no pure Assign provides the
                        # binding first.
                        pass
                    elif isinstance(s, _For):
                        add_target_names(s.target)
                        collect_assigned(s.body)
                    elif isinstance(s, _If):
                        collect_assigned(s.body)
                        collect_assigned(s.else_body)
                    elif isinstance(s, _While):
                        collect_assigned(s.body)
                    elif isinstance(s, _With):
                        for _ctx_expr, as_var in s.items:
                            if as_var is not None:
                                add_target_names(as_var)
                        collect_assigned(s.body)
                    elif isinstance(s, _Try):
                        collect_assigned(s.body)
                        collect_assigned(s.else_body)
                        collect_assigned(s.finally_body)
                    elif isinstance(s, _ImportStmt):
                        for mod_name, asname in s.names:
                            bound = asname or mod_name.split(".", 1)[0]
                            if bound:
                                append_name_once(assigned_names, bound)
                    elif isinstance(s, _ImportFromStmt):
                        for imported_name, asname in s.names:
                            if imported_name == "*":
                                continue
                            append_name_once(
                                assigned_names, asname or imported_name,
                            )
                    elif isinstance(s, (_FuncDef, _ClassDef)):
                        # A nested ``def`` / ``class`` binds its name
                        # in the enclosing scope. Uses of that name in
                        # sibling statements are local references, not
                        # captures from an even-further-outer scope.
                        # Don't recurse into its body — that has its
                        # own local scope.
                        append_name_once(assigned_names, s.name)
            collect_assigned(fd.body)

            # Parser sentinel names the codegen special-cases at Call
            # position (comprehensions, walrus, yield). They never
            # refer to a user binding, so never count as a capture.
            sentinel_ns = {
                "__listcomp__", "_list_comp", "_gen_comp", "__genexpr__",
                "__setcomp__", "_set_comp",
                "__dictcomp__", "_dict_comp",
                "_walrus", "__walrus__",
                "_yield", "__yield__", "_yield_from", "__yield_from__",
                "_gen_clause", "__starred__",
                # dataclasses.replace aliases are lowered as a native
                # codegen helper, not a runtime callable capture.
                "replace", "_replace",
                # Treat bare ``_`` as a discard binding for closure
                # analysis. The pcc codebase uses it pervasively in
                # tuple-unpack / loop-target throwaway positions; if it
                # leaks into a propagated capture set, hoisted sibling
                # calls end up demanding an outer ``_`` binding that
                # doesn't semantically exist.
                "_",
                "*", "**",
            }
            local_scope = []
            extend_names_once(local_scope, param_names)
            extend_names_once(local_scope, assigned_names)
            extend_names_once(local_scope, module_names)
            extend_names_once(local_scope, sentinel_ns)
            builtins_ns = _PY_BUILTINS_NS
            free = []

            def _collect_target_names(t, acc):
                if isinstance(t, _Name):
                    append_name_once(acc, t.ident)
                    return
                for slot in _dataclass_field_names(t):
                    v = _dataclass_field_value(t, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            _collect_target_names(it, acc)

            def walk(x, bound=None):
                if bound is None:
                    bound = ()
                if isinstance(x, (_FuncDef, _ClassDef)):
                    # Nested defs/classes introduce their own local
                    # scope. Their bodies must not contribute free vars
                    # to the enclosing function.
                    return
                if isinstance(x, tuple):
                    # Plain tuples show up in places like
                    # ``Call.kwargs = ((name, Expr), ...)``; recurse so
                    # kwarg values still participate in free-var
                    # analysis.
                    for it in x:
                        walk(it, bound)
                    return
                if isinstance(x, _Name):
                    if (
                        not name_in(local_scope, x.ident)
                        and not name_in(builtins_ns, x.ident)
                        and not name_in(bound, x.ident)
                    ):
                        append_name_once(free, x.ident)
                    return
                if isinstance(x, _Call) and isinstance(x.func, _Name):
                    fname = x.func.ident
                    if fname in (
                        "_list_comp", "_set_comp", "_gen_comp",
                        "__listcomp__", "__setcomp__", "__genexpr__",
                    ) and x.args:
                        # _list_comp(elt, _gen_clause(target, iter, ifs), ...)
                        comp_bound = copy_names(bound)
                        for gen_arg in x.args[1:]:
                            if (
                                isinstance(gen_arg, _Call)
                                and isinstance(gen_arg.func, _Name)
                                and gen_arg.func.ident == "_gen_clause"
                                and gen_arg.args
                            ):
                                _collect_target_names(gen_arg.args[0], comp_bound)
                            elif isinstance(gen_arg, _TupleExpr):
                                for clause in gen_arg.elems:
                                    if (
                                        isinstance(clause, _TupleExpr)
                                        and clause.elems
                                    ):
                                        _collect_target_names(
                                            clause.elems[0], comp_bound,
                                        )
                        walk(x.args[0], comp_bound)
                        running_bound = copy_names(bound)
                        for gen_arg in x.args[1:]:
                            if (
                                isinstance(gen_arg, _Call)
                                and isinstance(gen_arg.func, _Name)
                                and gen_arg.func.ident == "_gen_clause"
                                and len(gen_arg.args) == 3
                            ):
                                target, iter_expr, ifs_expr = gen_arg.args
                                walk(iter_expr, running_bound)
                                _collect_target_names(target, running_bound)
                                walk(ifs_expr, running_bound)
                            elif isinstance(gen_arg, _TupleExpr):
                                for clause in gen_arg.elems:
                                    if (
                                        isinstance(clause, _TupleExpr)
                                        and len(clause.elems) >= 3
                                    ):
                                        target = clause.elems[0]
                                        iter_expr = clause.elems[1]
                                        ifs_expr = clause.elems[2]
                                        walk(iter_expr, running_bound)
                                        _collect_target_names(
                                            target, running_bound,
                                        )
                                        walk(ifs_expr, running_bound)
                                    else:
                                        walk(clause, running_bound)
                            else:
                                walk(gen_arg, running_bound)
                        return
                    if fname in ("_dict_comp", "__dictcomp__") and x.args:
                        # _dict_comp(TupleExpr(k, v), _gen_clause(...), ...)
                        comp_bound = copy_names(bound)
                        for gen_arg in x.args[1:]:
                            if (
                                isinstance(gen_arg, _Call)
                                and isinstance(gen_arg.func, _Name)
                                and gen_arg.func.ident == "_gen_clause"
                                and gen_arg.args
                            ):
                                _collect_target_names(gen_arg.args[0], comp_bound)
                            elif isinstance(gen_arg, _TupleExpr):
                                for clause in gen_arg.elems:
                                    if (
                                        isinstance(clause, _TupleExpr)
                                        and clause.elems
                                    ):
                                        _collect_target_names(
                                            clause.elems[0], comp_bound,
                                        )
                        walk(x.args[0], comp_bound)
                        if fname == "__dictcomp__" and len(x.args) > 1:
                            walk(x.args[1], comp_bound)
                        running_bound = copy_names(bound)
                        for gen_arg in x.args[1:]:
                            if (
                                isinstance(gen_arg, _Call)
                                and isinstance(gen_arg.func, _Name)
                                and gen_arg.func.ident == "_gen_clause"
                                and len(gen_arg.args) == 3
                            ):
                                target, iter_expr, ifs_expr = gen_arg.args
                                walk(iter_expr, running_bound)
                                _collect_target_names(target, running_bound)
                                walk(ifs_expr, running_bound)
                            elif isinstance(gen_arg, _TupleExpr):
                                for clause in gen_arg.elems:
                                    if (
                                        isinstance(clause, _TupleExpr)
                                        and len(clause.elems) >= 3
                                    ):
                                        target = clause.elems[0]
                                        iter_expr = clause.elems[1]
                                        ifs_expr = clause.elems[2]
                                        walk(iter_expr, running_bound)
                                        _collect_target_names(
                                            target, running_bound,
                                        )
                                        walk(ifs_expr, running_bound)
                                    else:
                                        walk(clause, running_bound)
                            else:
                                walk(gen_arg, running_bound)
                        return
                    if fname == "_gen_clause" and x.args:
                        # _gen_clause(target, iter, (ifs,))
                        target = x.args[0]
                        new_bound = copy_names(bound)
                        _collect_target_names(target, new_bound)
                        for a in x.args[1:]:
                            walk(a, new_bound)
                        return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it, bound)
                    else:
                        walk(v, bound)

            for s in fd.body:
                walk(s)
            return filter_capture_names(tuple(sorted(free)))

        def body_reads_free_names(fd, excluded):
            return bool(compute_free_names(fd, excluded))

        def collect_scope_bindings(stmts):
            """Return names bound somewhere in the current lexical scope."""
            from ..py_ast import (
                Import as _ImportStmt,
                ImportFrom as _ImportFromStmt,
                TupleExpr as _TupleExpr,
            )

            bindings = []

            def add_target_names(t):
                if isinstance(t, _Name):
                    append_name_once(bindings, t.ident)
                elif isinstance(t, _TupleExpr):
                    for e in t.elems:
                        add_target_names(e)

            def walk(stmts):
                for s in stmts:
                    if isinstance(s, _Assign):
                        for t in s.targets:
                            add_target_names(t)
                    elif isinstance(s, _For):
                        add_target_names(s.target)
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _If):
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _While):
                        walk(s.body)
                        walk(s.else_body)
                    elif isinstance(s, _With):
                        for _, as_var in s.items:
                            if as_var is not None:
                                add_target_names(as_var)
                        walk(s.body)
                    elif isinstance(s, _Try):
                        walk(s.body)
                        walk(s.else_body)
                        walk(s.finally_body)
                        for h in s.handlers:
                            if h.name:
                                append_name_once(bindings, h.name)
                            walk(h.body)
                    elif isinstance(s, _ImportStmt):
                        for mod_name, asname in s.names:
                            bound = asname or mod_name.split(".", 1)[0]
                            if bound:
                                append_name_once(bindings, bound)
                    elif isinstance(s, _ImportFromStmt):
                        for imported_name, asname in s.names:
                            if imported_name == "*":
                                continue
                            append_name_once(
                                bindings, asname or imported_name,
                            )
                    elif isinstance(s, (_FuncDef, _ClassDef)):
                        append_name_once(bindings, s.name)

            walk(stmts)
            return tuple(bindings)

        def rewrite_body(stmts, rename_map, scope_names):
            """Return a new body tuple with nested defs stripped out
            and inner-name Call sites rewritten through rename_map."""
            # Pre-scan: pretend every sibling nested FuncDef already
            # hoisted so mutual-recursive siblings don't capture each
            # other as free vars. Each will get its real hoisted name
            # inserted when the main loop reaches it.
            prescan_map = copy_name_map(rename_map)
            for st in stmts:
                if isinstance(st, _FuncDef) and st.name not in prescan_map:
                    # Placeholder value — actual hoisted name assigned
                    # during the hoist branch below. Existence in the
                    # map is what ``compute_free_names``' excluded
                    # argument needs.
                    prescan_map[st.name] = (f"__nested_{st.name}", ())
            sibling_names = []
            for st in stmts:
                if isinstance(st, _FuncDef):
                    append_name_once(sibling_names, st.name)

            def filter_sibling_capture_names(names):
                out = []
                for name in names:
                    if not name_in(sibling_names, name):
                        out.append(name)
                return tuple(out)

            def filter_renamed_capture_names(names):
                out = []
                for name in names:
                    discard = False
                    for key, mapped in rename_map.items():
                        mapped_name = mapped[0] if isinstance(mapped, tuple) else mapped
                        if name == key or name == mapped_name:
                            discard = True
                            break
                    if not discard:
                        out.append(name)
                return tuple(out)

            def locally_bound_names(fd):
                """Names that are already available inside ``fd`` without
                capturing them from the enclosing scope."""
                bound = []
                for a in fd.args:
                    if a.name != "":
                        append_name_once(bound, a.name)
                extend_names_once(bound, collect_scope_bindings(fd.body))
                return bound

            def called_sibling_names(fd):
                """Collect sibling nested defs called directly from
                ``fd``. Their own captures must be threaded through the
                caller once the call site is rewritten to the hoisted
                form with synthetic capture kwargs."""
                out = []
                local_bound = locally_bound_names(fd)

                def walk(x):
                    if x is None:
                        return
                    if isinstance(x, tuple):
                        for it in x:
                            walk(it)
                        return
                    if isinstance(x, (_FuncDef, _ClassDef)):
                        return
                    if (
                        isinstance(x, _Call)
                        and isinstance(x.func, _Name)
                        and name_in(sibling_names, x.func.ident)
                        and not name_in(local_bound, x.func.ident)
                    ):
                        append_name_once(out, x.func.ident)
                    for slot in _dataclass_field_names(x):
                        walk(_dataclass_field_value(x, slot, None))

                for s in fd.body:
                    walk(s)
                return out

            def forwarded_value_capture_names(
                fd, excluded_names, outer_scope_names, outer_local_bound,
            ):
                """Captures needed by nested defs used as first-class values.

                When ``fd`` returns or stores a nested def (instead of
                calling it directly), the outer hoisted wrapper must carry
                any outer-scope captures that nested def still needs. This
                is the ``make_body_for -> body`` shape in layer1's own
                comprehension helpers.
                """
                out = set()
                nested_defs = [
                    s for s in fd.body if isinstance(s, _FuncDef)
                ]
                for inner_fd in nested_defs:
                    inner_local_bound = locally_bound_names(inner_fd)
                    inner_free = set(compute_free_names(inner_fd, excluded_names))
                    inner_forwarded = forwarded_value_capture_names(
                        inner_fd,
                        excluded_names,
                        outer_scope_names,
                        inner_local_bound,
                    )
                    inner_needed = set(inner_free)
                    inner_needed.update(
                        fv for fv in inner_forwarded
                        if fv not in inner_local_bound
                    )
                    if not body_uses_name_as_value(fd.body, inner_fd.name):
                        continue
                    out.update(
                        fv
                        for fv in inner_needed
                        if fv in outer_scope_names
                        and fv not in outer_local_bound
                    )
                return out

            excluded_prescan = set()
            for k, v in prescan_map.items():
                excluded_prescan.add(k)
                excluded_prescan.add(v[0] if isinstance(v, tuple) else v)

            def sibling_funcdef(name):
                for sibling_stmt in stmts:
                    if (
                        isinstance(sibling_stmt, _FuncDef)
                        and sibling_stmt.name == name
                    ):
                        return sibling_stmt
                return None

            def sibling_effective_free_names(fd, seen_names):
                out = []
                extend_names_once(out, compute_free_names(fd, excluded_prescan))
                local_bound = locally_bound_names(fd)
                for dep in called_sibling_names(fd):
                    if name_in(seen_names, dep):
                        continue
                    dep_fd = sibling_funcdef(dep)
                    if dep_fd is None:
                        continue
                    dep_free = sibling_effective_free_names(
                        dep_fd, tuple(seen_names) + (dep,),
                    )
                    for fv in dep_free:
                        if (
                            name_in(scope_names, fv)
                            and not name_in(local_bound, fv)
                        ):
                            append_name_once(out, fv)
                return tuple(sorted(out))

            effective_free_names: dict[str, tuple] = {}
            for st in stmts:
                if not isinstance(st, _FuncDef):
                    continue
                effective_free_names[st.name] = sibling_effective_free_names(
                    st, (st.name,),
                )
            for name in sibling_names:
                mapped = prescan_map.get(name)
                if isinstance(mapped, tuple):
                    capture_names = filter_capture_names(
                        effective_free_names.get(name, ()),
                    )
                    capture_names = filter_sibling_capture_names(capture_names)
                    capture_names = filter_renamed_capture_names(capture_names)
                    prescan_map[name] = (
                        mapped[0],
                        tuple(sorted(capture_names)),
                    )
            new_stmts = []
            for st in stmts:
                # ``name = lambda params: body`` — rewrite to a regular
                # nested ``def name(params): return body`` statement so
                # the lambda lifting falls out of the existing FuncDef
                # hoist (closure conversion + recursive-call rewrite
                # already apply). Matches Python's equivalence for
                # name-bound lambdas.
                if (
                    isinstance(st, _Assign)
                    and isinstance(st.value, _Lambda)
                    and len(st.targets) == 1
                    and isinstance(st.targets[0], _Name)
                ):
                    lam = st.value
                    # Give each lambda param a DynType annotation when
                    # none was declared (lambdas usually omit them),
                    # matching the annotation gate in
                    # ``_declare_user_function``.
                    params = tuple(
                        a if a.annotation is not None
                        else _replace(a, annotation=_DYN)
                        for a in lam.params
                    )
                    fd_stmt = _FuncDef(
                        span=st.span,
                        name=st.targets[0].ident,
                        args=params,
                        return_ty=_DYN,
                        body=(_Return(span=st.span, value=lam.body),),
                        is_async=False,
                        decorators=(),
                    )
                    st = fd_stmt
                if isinstance(st, _ClassDef):
                    # Hoist nested ClassDef to module top level so
                    # ``_declare_user_function`` / ``emit_methods`` can
                    # process it via the standard class path. Nested
                    # instance methods sometimes read outer locals
                    # (e.g. ``ctx`` in a pass-local helper class); for
                    # those, rewrite bare capture reads to hidden
                    # instance attrs and attach the values at
                    # instantiation time. This is a one-shot closure
                    # approximation, but it unblocks the self-host
                    # survey on current nested-helper-class patterns.
                    existing = {
                        s.name for s in self.ast_module.body
                        if isinstance(s, (_ClassDef, _FuncDef))
                    } | {h.name for h in hoisted}
                    hoist_name = st.name
                    suffix = 0
                    while hoist_name in existing:
                        suffix += 1
                        hoist_name = f"{st.name}_nest{suffix}"
                    class_free_names: set[str] = set()
                    new_class_body = []
                    for body_stmt in st.body:
                        if isinstance(body_stmt, _FuncDef):
                            recv_name = next(
                                (a.name for a in body_stmt.args if a.name != ""),
                                None,
                            )
                            method_free = tuple(
                                fv for fv in compute_free_names(
                                    body_stmt, excluded_prescan,
                                )
                                if fv in scope_names
                                and not is_discard_capture_name(fv)
                            )
                            if recv_name is not None and method_free:
                                cap_names = set(method_free)

                                def rewrite_cap_node(x):
                                    if x is None:
                                        return x
                                    if isinstance(x, (_FuncDef, _ClassDef)):
                                        return x
                                    if isinstance(x, tuple):
                                        return tuple(
                                            rewrite_cap_node(it) for it in x
                                        )
                                    if (
                                        isinstance(x, _Name)
                                        and x.ident in cap_names
                                    ):
                                        return _Attr(
                                            span=x.span,
                                            ty=_DYN,
                                            obj=_Name(
                                                span=x.span, ty=_DYN,
                                                ident=recv_name,
                                            ),
                                            name=f"__pcc_cap_{x.ident}",
                                        )
                                    fields = tuple(_dataclass_field_names(x))
                                    if fields:
                                        replacements = {}
                                        for slot in fields:
                                            v = _dataclass_field_value(x, slot, None)
                                            new_v = rewrite_cap_node(v)
                                            if new_v != v:
                                                replacements[slot] = new_v
                                        if replacements:
                                            return _replace(x, **replacements)
                                    return x

                                body_stmt = _replace(
                                    body_stmt,
                                    body=tuple(
                                        rewrite_cap_node(s)
                                        for s in body_stmt.body
                                    ),
                                )
                                class_free_names.update(method_free)
                        new_class_body.append(body_stmt)
                    hoisted_cd = _replace(
                        st, name=hoist_name, body=tuple(new_class_body),
                    )
                    hoisted.append(hoisted_cd)
                    if class_free_names:
                        self._hoisted_class_capture_params[hoist_name] = (
                            tuple(sorted(class_free_names))
                        )
                    if hoist_name != st.name:
                        rename_map[st.name] = hoist_name
                    continue
                if isinstance(st, _FuncDef):
                    # Closure conversion threads ``self`` through as
                    # just another free-var capture when the nested
                    # def is inside a method body. The hoisted symbol
                    # lands at module scope with ``self`` as a
                    # trailing DynType param; ``self.<attr>`` reads
                    # then resolve via the DynType attribute path
                    # (``py_cpy_getattr`` / ``py_obj_getattr``) at
                    # runtime — slower than the compile-time class
                    # layout path but correct, and good enough to
                    # unblock solo-compile on the affected files.
                    # Skip hoisting when the nested name is used as a
                    # first-class value (passed to ``re.sub`` / stored
                    # in an attr / returned). Our narrow Call.func-
                    # only rewrite leaves value-position references
                    # unchanged, and the hoisted symbol at module
                    # scope has no function-pointer story, so a
                    # ``Name(<inner>)`` read would fail with
                    # ``unbound name X``. Preserve the original def
                    # location so the honest ``does not handle
                    # statement FuncDef`` surfaces instead — unless
                    # the def is closure-free and has a 1-arg shape
                    # compatible with the ``py_cpy_wrap_pcc_1arg``
                    # trampoline; in that case ``_emit_name`` wraps
                    # the hoisted function pointer as a CPython
                    # callable and the value-position reference
                    # resolves there.
                    if body_uses_name_as_value(stmts, st.name):
                        # Check: 1 / 2 / 3 params — and every runtime
                        # param must either be unannotated / DynType or
                        # lower to a PyObject* at the IR level (the
                        # wrap trampoline hands CPython PyObject*s).
                        # Native-ABI types (int, float) would see bad
                        # data, so require pointer-shaped params.
                        _wrap_ok_annotations = (
                            _DynType, StrType, ListType, DictType,
                            TupleType, NoneType,
                        )
                        runtime_params = [
                            a for a in st.args if a.name != ""
                        ]
                        simple_shape = (
                            0 <= len(runtime_params) <= 3
                            and all(
                                a.annotation is None
                                or isinstance(
                                    a.annotation, _wrap_ok_annotations,
                                )
                                for a in runtime_params
                            )
                        )
                        excluded_pre = set()
                        for k, v in prescan_map.items():
                            excluded_pre.add(k)
                            excluded_pre.add(v[0] if isinstance(v, tuple) else v)
                        has_free = bool(
                            effective_free_names.get(
                                st.name,
                                set(compute_free_names(st, excluded_pre)),
                            )
                        )
                        if not simple_shape:
                            new_stmts.append(st)
                            continue
                        if has_free:
                            # Track this nested def for adapter-wrap at
                            # value-position ``_emit_name``. The
                            # hoisted function carries captures as
                            # trailing kwarg params; the adapter
                            # synthesized at value position reads those
                            # captures from per-name internal globals
                            # (populated at wrap time in the outer
                            # scope) and calls the full-arity hoisted
                            # version.
                            if not hasattr(self, "_hoist_wrap_caps"):
                                self._hoist_wrap_caps: dict = {}
                            # Actual capture list is computed further
                            # below once ``free_names`` is resolved;
                            # seed here with empty and patch later.
                            self._hoist_wrap_caps[st.name] = {
                                "original_arity": len(runtime_params),
                                "free_names": (),
                                "hoisted_name": None,
                            }
                    # Mutable-capture path: if the nested def mutates
                    # a free variable (``nonlocal X; X += 1`` pattern),
                    # the outer body has already been preprocessed by
                    # ``box_outer_body`` to box that name into a
                    # 1-element list. Every read/write of X in both
                    # outer and inner goes through ``X[0]`` subscript
                    # lookups, so closure-by-value is now correct —
                    # the list reference is shared.
                    # ``prescan_map`` already includes every sibling
                    # nested FuncDef's (original and hoisted) names —
                    # so mutual-recursive siblings don't capture each
                    # other as free vars. Also folds in outer-scope
                    # hoisted siblings that live in ``rename_map``.
                    excluded = set()
                    for k, v in prescan_map.items():
                        excluded.add(k)
                        excluded.add(v[0] if isinstance(v, tuple) else v)
                    local_bound = locally_bound_names(st)
                    free_names = tuple(sorted(effective_free_names.get(
                        st.name,
                        compute_free_names(st, excluded),
                    )))
                    free_names = filter_capture_names(free_names)
                    free_names = filter_sibling_capture_names(free_names)
                    free_names = filter_renamed_capture_names(free_names)
                    # Pick the hoisted symbol early so self-referential
                    # free-var detection works.
                    hoist_name = f"__nested_{st.name}"
                    suffix = 0
                    existing = {h.name for h in hoisted} | {
                        s.name for s in self.ast_module.body
                        if isinstance(s, _FuncDef)
                    }
                    final_name = hoist_name
                    while final_name in existing:
                        suffix += 1
                        final_name = f"{hoist_name}_{suffix}"
                    free_names = filter_self_capture_names(
                        free_names, st.name, final_name,
                    )
                    while True:
                        # Closure conversion: prepend the free vars as
                        # extra trailing arguments with DynType annotation.
                        # Default to None so CPython-side kwarg fills
                        # aren't required, matching Python's no-default
                        # model for captured variables.
                        cap_args = tuple(
                            _Arg(
                                name=fv, annotation=_DYN,
                                default=None, kind="pos", has_default=True,
                            )
                            for fv in free_names
                        )
                        # Insert the captures BEFORE the first bare ``*``
                        # kw-only separator so the caller's trailing
                        # positional rewrite (appends captures after
                        # regular positionals) still maps correctly. A
                        # nested def like ``def f(a, b, *, k=1):`` stays
                        # ``def __nested_f(a, b, _cap1, _cap2, *, k=1)``
                        # rather than ending up with positional args
                        # after the kw-only separator, which
                        # ``_resolve_call_kwargs`` would reject.
                        orig_args = tuple(st.args)
                        split_idx = len(orig_args)
                        for i, a in enumerate(orig_args):
                            if a.name == "":
                                split_idx = i
                                break
                        new_args = (
                            orig_args[:split_idx] + cap_args + orig_args[split_idx:]
                        )
                        # Inside the inner body, recursive self-calls need
                        # to forward the same captured values. Seed the
                        # inner rename_map with the conversion entry so
                        # ``rewrite_expr`` rewrites the self-call's
                        # ``Call(Name(<inner_name>), args)`` to include
                        # the free vars as trailing positional args.
                        inner_map = copy_name_map(prescan_map)
                        update_name_map(inner_map, rename_map)
                        inner_map[st.name] = (final_name, free_names)
                        inner_scope = copy_names(scope_names)
                        for a in st.args:
                            if a.name != "":
                                append_name_once(inner_scope, a.name)
                        extend_names_once(
                            inner_scope, collect_scope_bindings(st.body),
                        )
                        inner_body = rewrite_body(
                            st.body, inner_map, inner_scope,
                        )
                        forwarded = {
                            fv
                            for fv in compute_free_names(
                                clone_funcdef(
                                    st, st.name, st.args, st.return_ty,
                                    inner_body,
                                ),
                                excluded,
                            )
                            if name_in(scope_names, fv)
                            and not name_in(local_bound, fv)
                            and not is_discard_capture_name(fv)
                        }
                        forwarded.update(
                            forwarded_value_capture_names(
                                st, excluded, scope_names, local_bound,
                            )
                        )
                        widened = tuple(sorted(set(free_names) | forwarded))
                        widened = filter_self_capture_names(
                            widened, st.name, final_name,
                        )
                        widened = filter_sibling_capture_names(widened)
                        widened = filter_renamed_capture_names(widened)
                        if widened == free_names:
                            break
                        free_names = widened
                    # Update the adapter-wrap metadata (if this def was
                    # flagged at the body_uses_name_as_value gate).
                    cap_entry = getattr(self, "_hoist_wrap_caps", {}).get(st.name)
                    if cap_entry is not None:
                        cap_entry["free_names"] = tuple(free_names)
                        cap_entry["hoisted_name"] = final_name
                        cap_entry["original_name"] = st.name
                        # Mirror under the hoisted name so
                        # ``_emit_name(<hoisted>)`` can find the
                        # entry when the rewrite_expr bare-Name
                        # rewrite has already swapped the ident.
                        self._hoist_wrap_caps[final_name] = cap_entry
                    self._hoisted_capture_params[final_name] = tuple(free_names)
                    hoisted_fd = clone_funcdef(
                        st, final_name, new_args, st.return_ty, inner_body,
                    )
                    hoisted.append(hoisted_fd)
                    rename_map[st.name] = (final_name, free_names)
                    # Drop the original def from the current body.
                    continue
                new_stmts.append(rewrite_stmt(st, rename_map))
            return tuple(new_stmts)

        def rewrite_stmt(stmt, rename_map):
            if isinstance(stmt, _If):
                return _replace(
                    stmt,
                    cond=rewrite_expr(stmt.cond, rename_map),
                    body=rewrite_body(stmt.body, rename_map, scope_names),
                    else_body=rewrite_body(
                        stmt.else_body, rename_map, scope_names,
                    ),
                )
            if isinstance(stmt, _While):
                return _replace(
                    stmt,
                    cond=rewrite_expr(stmt.cond, rename_map),
                    body=rewrite_body(stmt.body, rename_map, scope_names),
                    else_body=rewrite_body(
                        stmt.else_body, rename_map, scope_names,
                    ),
                )
            if isinstance(stmt, _For):
                return _replace(
                    stmt,
                    iter=rewrite_expr(stmt.iter, rename_map),
                    body=rewrite_body(stmt.body, rename_map, scope_names),
                    else_body=rewrite_body(
                        stmt.else_body, rename_map, scope_names,
                    ),
                )
            if isinstance(stmt, _Try):
                return _replace(
                    stmt,
                    body=rewrite_body(stmt.body, rename_map, scope_names),
                    else_body=rewrite_body(
                        stmt.else_body, rename_map, scope_names,
                    ),
                    finally_body=rewrite_body(
                        stmt.finally_body, rename_map, scope_names,
                    ),
                    handlers=tuple(
                        _replace(
                            h,
                            body=rewrite_body(h.body, rename_map, scope_names),
                        )
                        for h in stmt.handlers
                    ),
                )
            if isinstance(stmt, _With):
                return _replace(
                    stmt, body=rewrite_body(stmt.body, rename_map, scope_names),
                )
            if isinstance(stmt, _ExprStmt):
                return _replace(stmt, expr=rewrite_expr(stmt.expr, rename_map))
            if isinstance(stmt, _Assign):
                return _replace(
                    stmt,
                    targets=tuple(
                        rewrite_expr(t, rename_map) for t in stmt.targets
                    ),
                    value=rewrite_expr(stmt.value, rename_map),
                )
            if isinstance(stmt, _AugAssign):
                return _replace(
                    stmt,
                    target=rewrite_expr(stmt.target, rename_map),
                    value=rewrite_expr(stmt.value, rename_map),
                )
            if isinstance(stmt, _Return):
                if stmt.value is None:
                    return stmt
                return _replace(
                    stmt, value=rewrite_expr(stmt.value, rename_map),
                )
            # ``del expr[idx]`` / ``del expr.attr`` — the expr and idx
            # may contain a nested Call to a hoisted sibling. Walk
            # each target through rewrite_expr so the rename lands.
            from ..py_ast import Delete as _Delete
            if isinstance(stmt, _Delete):
                return _replace(
                    stmt,
                    targets=tuple(
                        rewrite_expr(t, rename_map) for t in stmt.targets
                    ),
                )
            return stmt

        def rewrite_expr(expr, rename_map):
            if isinstance(expr, _Call):
                # Only rewrite the Call's callee slot — leaves non-call
                # ``Name`` references (e.g. passing ``repl`` as a value
                # to ``re.sub(repl, ...)``) unrewritten so they fail
                # as ``unbound name 'repl'`` at the original call site,
                # which is a more honest error than ``unbound name
                # '__nested_repl'``. First-class function values are a
                # separate, larger feature.
                new_func = expr.func
                extra_kwargs: tuple = ()
                if (
                    isinstance(new_func, _Name)
                    and new_func.ident in rename_map
                ):
                    mapped = rename_map[new_func.ident]
                    if isinstance(mapped, tuple):
                        final_name, free_names = mapped
                    else:
                        final_name, free_names = mapped, ()
                    new_func = _replace(new_func, ident=final_name)
                    # Closure conversion: pass each captured var as a
                    # keyword argument with the capture's own name. The
                    # hoisted function has a synthetic param of the
                    # same ident, so kwarg-by-name fills that slot and
                    # does NOT collide with any user-supplied kwarg
                    # whose formal is closer to the front of the param
                    # list (``_specialize(clones, take_true=True)``
                    # would otherwise treat a positional capture as the
                    # 2nd positional param ``take_true`` and then the
                    # kwarg would duplicate). Name-based passing lets
                    # ``_resolve_call_kwargs`` resolve each by formal
                    # ident, regardless of the capture's position in
                    # the hoisted signature.
                    # Each capture value is ``Name(fv)`` resolved in the
                    # outer (caller) scope. If the outer rename_map has
                    # a hoisted sibling of the same name — e.g.
                    # ``exact_alias_source`` has itself been hoisted —
                    # the capture value needs to be the hoisted sibling
                    # name so ``_emit_name`` resolves it; otherwise a
                    # bare ``Name(original)`` would be unbound.
                    def cap_value(fv):
                        mapped = rename_map.get(fv)
                        if mapped is not None:
                            target = mapped[0] if isinstance(mapped, tuple) else mapped
                        else:
                            target = fv
                        return _Name(span=expr.span, ty=_DYN, ident=target)
                    extra_kwargs = tuple(
                        (fv, cap_value(fv))
                        for fv in free_names
                    )
                else:
                    new_func = rewrite_expr(new_func, rename_map)
                    extra_kwargs = ()
                return _replace(
                    expr,
                    func=new_func,
                    args=tuple(
                        rewrite_expr(a, rename_map) for a in expr.args
                    ),
                    kwargs=(
                        tuple(
                            (k, rewrite_expr(v, rename_map))
                            for (k, v) in expr.kwargs
                        )
                        + extra_kwargs
                    ),
                )
            # Bare Name at value position: if it matches a hoisted
            # nested def in rename_map, rewrite to the hoisted symbol
            # so downstream ``_emit_name`` finds the function in
            # ``self.functions`` and can wrap it as a CPython callable
            # via ``py_cpy_wrap_pcc_1arg``. This converts patterns
            # like ``pattern.sub(repl, text)`` / ``am.register(K, repl)``
            # from ``unbound name`` to a usable callable.
            if isinstance(expr, _Name) and expr.ident in rename_map:
                mapped = rename_map[expr.ident]
                target = mapped[0] if isinstance(mapped, tuple) else mapped
                return _replace(expr, ident=target)
            # Generic recurse for non-Call expressions. Each dataclass
            # field that is another Expr (or a tuple of Exprs) is
            # rewritten; primitive fields pass through. This lets
            # ``alias_base(x) == base`` inside an ``If.cond``, a
            # ``BoolExpr.operands[*]``, or any other composite expr
            # have its inner Call.func rewritten to the hoisted symbol.
            fields = tuple(_dataclass_field_names(expr))
            if fields:
                replacements: dict = {}
                for slot in fields:
                    v = _dataclass_field_value(expr, slot, None)
                    if isinstance(v, tuple):
                        new_v = tuple(
                            rewrite_expr(it, rename_map)
                            if _dataclass_field_names(it) else it
                            for it in v
                        )
                        if new_v != v:
                            replacements[slot] = new_v
                    elif _dataclass_field_names(v):
                        new_v = rewrite_expr(v, rename_map)
                        if new_v is not v:
                            replacements[slot] = new_v
                if replacements:
                    return _replace(expr, **replacements)
            return expr

        from ..py_ast import (
            Attr as _Attr,
            ExprStmt as _ExprStmt2,
            ListExpr as _ListExpr,
        )

        def body_has_yield(stmts):
            """Detect a yield sentinel call anywhere in ``stmts``
            (not descending into nested defs)."""
            found = [False]

            def walk(x):
                if found[0]:
                    return
                if isinstance(x, _FuncDef):
                    return
                if (
                    isinstance(x, _Call)
                    and isinstance(x.func, _Name)
                    and x.func.ident in (
                        "_yield", "_yield_from",
                        "__yield__", "__yield_from__",
                    )
                ):
                    found[0] = True
                    return
                for slot in _dataclass_field_names(x):
                    v = _dataclass_field_value(x, slot, None)
                    if isinstance(v, tuple):
                        for it in v:
                            walk(it)
                    else:
                        walk(v)
            for s in stmts:
                walk(s)
            return found[0]

        def rewrite_yield_in_stmts(stmts, accumulator_name):
            """Convert yield sentinel calls in the body into append /
            extend onto the accumulator list."""
            def rewrite_stmt(s):
                if isinstance(s, _FuncDef):
                    return s
                if isinstance(s, _ExprStmt2):
                    inner = s.expr
                    if (
                        isinstance(inner, _Call)
                        and isinstance(inner.func, _Name)
                        and inner.func.ident in (
                            "_yield", "_yield_from",
                            "__yield__", "__yield_from__",
                        )
                        and len(inner.args) == 1
                    ):
                        method = (
                            "extend"
                            if inner.func.ident in (
                                "_yield_from", "__yield_from__",
                            )
                            else "append"
                        )
                        list_ty = ListType(
                            name="list", elem=DynType(name="dyn"),
                        )
                        recv = _Name(
                            span=inner.span, ty=list_ty,
                            ident=accumulator_name,
                        )
                        call = _Call(
                            span=inner.span, ty=_DYN,
                            func=_Attr(
                                span=inner.span, ty=_DYN,
                                obj=recv, name=method,
                            ),
                            args=(inner.args[0],),
                            kwargs=(),
                        )
                        return _ExprStmt2(span=s.span, expr=call)
                # Recurse into nested blocks.
                if isinstance(s, _If):
                    return _replace(
                        s,
                        body=tuple(rewrite_stmt(x) for x in s.body),
                        else_body=tuple(rewrite_stmt(x) for x in s.else_body),
                    )
                if isinstance(s, _While):
                    return _replace(
                        s,
                        body=tuple(rewrite_stmt(x) for x in s.body),
                        else_body=tuple(rewrite_stmt(x) for x in s.else_body),
                    )
                if isinstance(s, _For):
                    return _replace(
                        s,
                        body=tuple(rewrite_stmt(x) for x in s.body),
                        else_body=tuple(rewrite_stmt(x) for x in s.else_body),
                    )
                if isinstance(s, _Try):
                    return _replace(
                        s,
                        body=tuple(rewrite_stmt(x) for x in s.body),
                        else_body=tuple(rewrite_stmt(x) for x in s.else_body),
                        finally_body=tuple(
                            rewrite_stmt(x) for x in s.finally_body
                        ),
                        handlers=tuple(
                            _replace(
                                h,
                                body=tuple(rewrite_stmt(x) for x in h.body),
                            )
                            for h in s.handlers
                        ),
                    )
                if isinstance(s, _With):
                    return _replace(
                        s, body=tuple(rewrite_stmt(x) for x in s.body),
                    )
                return s

            return tuple(rewrite_stmt(s) for s in stmts)

        def transform_generator_body(fd):
            """If ``fd.body`` contains yield sentinel calls, rewrite the
            function into an eager-collection form that returns a
            list of all yielded values. Matches the common pcc-side
            usage where callers immediately consume the generator
            with ``for x in gen():`` — this just materialises the
            values up-front. True lazy iteration is a separate lift.
            """
            if not body_has_yield(fd.body):
                return fd
            acc_name = "__gen_result"
            span = fd.span
            init = _Assign(
                span=span,
                targets=(_Name(span=span, ty=_DYN, ident=acc_name),),
                value=_ListExpr(
                    span=span,
                    ty=ListType(name="list", elem=DynType(name="dyn")),
                    elems=(),
                ),
                annotation=None,
            )
            new_body = (init,) + rewrite_yield_in_stmts(fd.body, acc_name)
            ret = _Return(
                span=span,
                value=_Name(span=span, ty=_DYN, ident=acc_name),
            )
            new_body = new_body + (ret,)
            new_ret_ty = ListType(name="list", elem=DynType(name="dyn"))
            return clone_funcdef(fd, fd.name, fd.args, new_ret_ty, new_body)

        new_top_body = []
        for stmt in self.ast_module.body:
            if isinstance(stmt, _FuncDef):
                stmt = transform_generator_body(stmt)
                # Pre-pass: box any outer locals that nested defs
                # mutate as free vars. After boxing, the hoist can
                # safely closure-convert-by-value — the list reference
                # is shared between outer and inner.
                boxed_body = box_outer_body(stmt.body)
                scope_names = []
                for a in stmt.args:
                    if a.name != "":
                        append_name_once(scope_names, a.name)
                extend_names_once(scope_names, collect_scope_bindings(boxed_body))
                new_body = rewrite_body(boxed_body, {}, scope_names)
                new_top_body.append(clone_funcdef(
                    stmt, stmt.name, stmt.args, stmt.return_ty, new_body,
                ))
            elif isinstance(stmt, _ClassDef):
                # Rewrite each method's body.
                new_methods = []
                for m in stmt.body:
                    if isinstance(m, _FuncDef):
                        m = transform_generator_body(m)
                        boxed_body = box_outer_body(m.body)
                        scope_names = []
                        for a in m.args:
                            if a.name != "":
                                append_name_once(scope_names, a.name)
                        extend_names_once(
                            scope_names, collect_scope_bindings(boxed_body),
                        )
                        new_body = rewrite_body(
                            boxed_body, {}, scope_names,
                        )
                        new_methods.append(clone_funcdef(
                            m, m.name, m.args, m.return_ty, new_body,
                        ))
                    else:
                        new_methods.append(m)
                new_top_body.append(_replace(stmt, body=tuple(new_methods)))
            else:
                new_top_body.append(stmt)
        new_top_body.extend(hoisted)
        self.ast_module = _replace(
            self.ast_module, body=tuple(new_top_body),
        )
        return hoisted

    # -- user-function declaration / definition -----------------------

    def _user_symbol(self, name: str) -> str:
        """Mangled LLVM symbol for a user function.

        Uses the ``user_<module>_<name>`` convention from
        Section 4 of the interface contract.
        """
        mod_name = self.ast_module.name or "mod"
        # Normalise dotted module names so the mangled symbol is a
        # valid LLVM identifier (dots in LLVM identifiers work when
        # quoted but read oddly).
        sanitized = mod_name.replace(".", "_").replace("-", "_")
        return f"user_{sanitized}_{name}"

    def _func_decorators(self, fd: FuncDef) -> tuple:
        decorators = fd.decorators
        if not isinstance(decorators, tuple):
            return ()
        return decorators

    def _declare_user_function(self, fd: FuncDef) -> None:
        if fd.is_async:
            raise NotImplementedError(
                "Layer 1 does not handle async def; received "
                f"{fd.name!r}"
            )
        c_abi_sym: str | None = None
        decorators = self._func_decorators(fd)
        if decorators:
            unrecognised = []
            for d in decorators:
                sym = self._decorator_c_abi_export_symbol(d)
                if sym is not None:
                    c_abi_sym = sym
                    continue
                if not self._decorator_is_noop_whitelist(d):
                    unrecognised.append(d)
            if unrecognised:
                raise NotImplementedError(
                    "Layer 1 does not handle decorators; received "
                    f"{len(decorators)} on {fd.name!r} "
                    f"(first unrecognised: "
                    f"{self._decorator_repr(unrecognised[0])})"
                )

        box_int_abi = c_abi_sym is None and self._should_box_python_ints()
        param_types: list[ir.Type] = []
        for arg in fd.args:
            # Bare ``*`` separator: no name, no runtime slot — it only
            # marks subsequent params as keyword-only. Skip so the
            # function's IR signature matches ``_resolve_call_kwargs``
            # which already filters the marker from the arg-list side.
            if arg.name == "":
                continue
            ir_ty, _ = self._param_ir_and_bind_type(
                arg, require_annotation=True, owner_name=fd.name,
                box_int_params=box_int_abi,
            )
            param_types.append(ir_ty)

        if fd.return_ty is None or isinstance(fd.return_ty, NoneType):
            # ``-> None`` maps to ``ret void`` — bare ``return`` works
            # without materialising the py_None global.
            ret_ty = _VOID
        elif box_int_abi and isinstance(fd.return_ty, IntType):
            ret_ty = _CSTR
        else:
            ret_ty = self._map_type(fd.return_ty)

        fnty = ir.FunctionType(ret_ty, param_types, var_arg=False)
        sym = c_abi_sym if c_abi_sym is not None else self._user_symbol(fd.name)
        existing = self.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            fn = ir.Function(self.module, fnty, name=sym)
            fn.linkage = "external"
        # @c_abi_export functions are runtime-level code, not user
        # application code. Propagate this flag so post-call err
        # checks are suppressed inside their bodies — a runtime
        # function's internal helper calls happen in a context
        # where TLS may already hold an in-flight exception (e.g.
        # py_exc_matches called during handler dispatch), and a
        # spurious check would misinterpret that as "the helper
        # raised".
        if c_abi_sym is not None:
            self._c_abi_export_symbols.add(sym)
        runtime_args = [a for a in fd.args if a.name != ""]
        for ir_arg, ast_arg in zip(fn.args, runtime_args):
            ir_arg.name = ast_arg.name
        self.functions[fd.name] = fn

    def _emit_user_function(self, fd: FuncDef) -> None:
        fn = self.functions[fd.name]
        box_int_abi = not any(
            self._decorator_c_abi_export_symbol(d) is not None
            for d in self._func_decorators(fd)
        ) and self._should_box_python_ints()
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        saved_ir_builder_flags = self._ir_builder_env_flags
        self.current_function = fn
        self.current_func_def = fd
        saved_global_names = self._current_global_names
        self._current_global_names = self._collect_explicit_global_names(
            fd.body
        )

        # Pick an entry-block name that can't collide with a parameter
        # or local variable of the same name. LLVM keeps labels in the
        # same namespace as SSA value names, so a function with a
        # parameter literally named ``entry`` would otherwise trigger
        # ``unable to create block named 'entry'`` at parse time.
        param_names = {a.name for a in fd.args}
        entry_label = "entry"
        if "entry" in param_names:
            entry_label = "fn.entry"
            while entry_label in param_names:
                entry_label += "_"
        entry = fn.append_basic_block(name=entry_label)
        self.builder = ir.IRBuilder(entry)
        self.env = {}
        self.env_class_hint = {}
        self.env_list_elem_class_hint = {}
        self._ir_builder_env_flags = {}
        self._box_int_locals = box_int_abi
        self._exact_int_env_flags = {}
        self.loop_stack = []

        # Promote each incoming argument to an entry-block alloca so
        # assignments within the function body are uniform. Skip the
        # bare ``*`` separator — it has no IR slot.
        runtime_args = [a for a in fd.args if a.name != ""]
        for ir_arg, ast_arg in zip(fn.args, runtime_args):
            ir_ty = ir_arg.type
            slot = self.builder.alloca(ir_ty, name=f"{ast_arg.name}.addr")
            self.builder.store(ir_arg, slot)
            _decl_ir_ty, bind_ty = self._param_ir_and_bind_type(
                ast_arg, require_annotation=True, owner_name=fd.name,
                box_int_params=box_int_abi,
            )
            self.env[ast_arg.name] = (slot, ir_ty, bind_ty)

        # Emit body.
        self._emit_stmts(fd.body)

        # If the terminator is missing (body fell through), insert a
        # default return. For void, ``ret void``. For typed returns
        # this is a bug in the user program, but we emit a zero-value
        # return to keep the IR well-formed — the type checker is
        # supposed to have rejected it already.
        if not self._builder_block_is_terminated():
            if isinstance(fn.function_type.return_type, ir.VoidType):
                self.builder.ret_void()
            else:
                self.builder.ret(self._zero_of(fn.function_type.return_type))

        self.builder = None
        self.current_function = None
        self.current_func_def = None
        self._current_global_names = saved_global_names
        self.env = {}
        self.env_class_hint = {}
        self.env_list_elem_class_hint = {}
        self._ir_builder_env_flags = saved_ir_builder_flags
        self._box_int_locals = saved_box_int_locals
        self._exact_int_env_flags = saved_exact_int_flags
        self.loop_stack = []

    def _builder_block_is_terminated(self) -> bool:
        return self.builder._block._terminated

    def _zero_of(self, ir_ty: ir.Type) -> ir.Value:
        if isinstance(ir_ty, ir.IntType):
            return ir.Constant(ir_ty, 0)
        if isinstance(ir_ty, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(ir_ty, 0.0)
        if isinstance(ir_ty, ir.PointerType):
            # NULL pointer — used as a safe fall-through return for
            # object-typed functions.
            return ir.Constant(ir_ty, None)
        raise L1CodegenError(f"no zero value for type {ir_ty}")

    # ------------------------------------------------------- statements

    def _emit_stmts(self, stmts: tuple[Stmt, ...]) -> None:
        for stmt in stmts:
            if self._builder_block_is_terminated():
                # Dead code after a return/raise — silently drop.
                return
            self._emit_stmt(stmt)

    def _emit_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, Pass):
            return
        if isinstance(stmt, Return):
            self._emit_return(stmt)
            return
        if isinstance(stmt, Assign):
            self._emit_assign(stmt)
            return
        if isinstance(stmt, AugAssign):
            self._emit_augassign(stmt)
            return
        if isinstance(stmt, ExprStmt):
            self._emit_expr_stmt(stmt)
            return
        if isinstance(stmt, Raise):
            self._emit_raise(stmt)
            return
        if isinstance(stmt, Try):
            self._emit_try(stmt)
            return
        if isinstance(stmt, With):
            self._emit_with(stmt)
            return
        if isinstance(stmt, Import):
            self._emit_import(stmt)
            return
        if isinstance(stmt, ImportFrom):
            self._emit_import_from(stmt)
            return
        if isinstance(stmt, If):
            self._emit_if(stmt)
            return
        if isinstance(stmt, While):
            self._emit_while(stmt)
            return
        if isinstance(stmt, For):
            self._emit_for(stmt)
            return
        if type(stmt).__name__ in ("Nonlocal", "Global"):
            # pcc has no lexical-scope closure story — ``nonlocal`` /
            # ``global`` declarations are recorded for symbol-table
            # hygiene in CPython, but at the pcc level every read/write
            # of the referenced name already routes to ``self.env`` /
            # ``_module_globals``. Accept the directive as a no-op so
            # solo-compile doesn't abort on sources that include one.
            return
        if isinstance(stmt, Break):
            if not self.loop_stack:
                raise L1CodegenError("break outside loop")
            _, break_bb = self.loop_stack[-1]
            self.builder.branch(break_bb)
            return
        if isinstance(stmt, Continue):
            if not self.loop_stack:
                raise L1CodegenError("continue outside loop")
            cont_bb, _ = self.loop_stack[-1]
            self.builder.branch(cont_bb)
            return
        if isinstance(stmt, Delete):
            self._emit_delete(stmt)
            return
        raise NotImplementedError(
            f"Layer 1 does not handle statement {type(stmt).__name__}"
        )

    def _emit_delete(self, stmt: Delete) -> None:
        """Lower ``del x`` / ``del d[k]`` / ``del xs[i]`` — the
        surface covers only what pcc itself uses. Name targets
        become a compile-time binding drop (no runtime IR) since
        pcc doesn't reuse the slot for a different type post-del.
        Subscript targets dispatch on container type."""
        for target in stmt.targets:
            if isinstance(target, Name):
                # Drop the env / cpy-flag entry so future reads
                # surface an unbound-name error. The alloca stays
                # (LLVM drops it through SSA). This is coarser than
                # Python but matches the bootstrap's usage pattern:
                # ``del tmp`` to release a large intermediate value.
                self.env.pop(target.ident, None)
                if hasattr(self, "_cpy_env_flags"):
                    self._cpy_env_flags.pop(target.ident, None)
                if hasattr(self, "env_class_hint"):
                    self.env_class_hint.pop(target.ident, None)
                continue
            if isinstance(target, Subscript):
                # ``del xs[lo:hi]`` / ``del xs[:]`` — slice delete
                # dispatches via CPython ``__delitem__`` (no pcc-
                # native helper). Build the slice as a CPython slice
                # object and call ``PyObject_DelItem``-equivalent via
                # the object's ``__delitem__`` attribute.
                if isinstance(target.idx, Slice):
                    obj = self._emit_expr(target.obj)
                    obj_ty = target.obj.ty
                    obj_cpy = self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"], [obj],
                        name=self._fresh("cpy.del.obj"),
                    )
                    # Build the slice via builtins.slice(lo, hi, step).
                    slice_fn = self._load_cpython_builtin("slice")
                    def _as_cpy(e):
                        if e is None:
                            # Py_None
                            gv = declare_runtime_global(
                                self.module, "py_None",
                            )
                            none = self.builder.load(gv, name="none")
                            return self.builder.call(
                                self.runtime["py_cpy_from_pcc_obj"], [none],
                                name=self._fresh("cpy.none"),
                            )
                        v = self._emit_expr(e)
                        obj_v = marshal.marshal_to_object(
                            self.builder, self.module, self.runtime,
                            v, e.ty,
                        )
                        return self.builder.call(
                            self.runtime["py_cpy_from_pcc_obj"], [obj_v],
                            name=self._fresh("cpy.slice.arg"),
                        )
                    lo_cpy = _as_cpy(target.idx.lo)
                    hi_cpy = _as_cpy(target.idx.hi)
                    step_cpy = _as_cpy(target.idx.step)
                    slice_obj = self.builder.call(
                        self.runtime["py_cpy_call3"],
                        [slice_fn, lo_cpy, hi_cpy, step_cpy],
                        name=self._fresh("cpy.slice"),
                    )
                    # Get __delitem__ and call with slice.
                    delitem_gv = self._cstr_global(
                        "__delitem__", ".cpy.attr.__delitem__",
                    )
                    delitem_fn = self.builder.call(
                        self.runtime["py_cpy_getattr"],
                        [obj_cpy, self._ptr_to_cstr(delitem_gv)],
                        name=self._fresh("cpy.delitem.fn"),
                    )
                    self.builder.call(
                        self.runtime["py_cpy_call1"],
                        [delitem_fn, slice_obj],
                        name=self._fresh("cpy.delitem"),
                    )
                    continue
                obj = self._emit_expr(target.obj)
                obj_ty = target.obj.ty
                idx_val = self._emit_expr(target.idx)
                idx_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    idx_val, target.idx.ty,
                )
                if isinstance(obj_ty, DictType):
                    self.builder.call(
                        self.runtime["py_dict_del"], [obj, idx_obj],
                    )
                    continue
                if isinstance(obj_ty, DynType):
                    # Generic object ``del d[k]`` — runtime dispatcher
                    # handles list / dict / user __delitem__ uniformly.
                    # Route through ``py_cpy_setitem(obj, key, NULL)``
                    # if the object is a CPython value, else through
                    # ``py_obj_delitem``. Both are already runtime-
                    # dispatchable on the type tag, so pass the pcc
                    # form directly.
                    self.builder.call(
                        self.runtime["py_obj_delitem"], [obj, idx_obj],
                    )
                    continue
                raise NotImplementedError(
                    f"Layer 1 'del' on subscript with container type "
                    f"{type(obj_ty).__name__} not yet wired"
                )
            raise NotImplementedError(
                f"Layer 1 'del' on {type(target).__name__} target "
                "not supported"
            )

    # -- Return --------------------------------------------------------

    def _emit_return(self, stmt: Return) -> None:
        fn = self.current_function
        ret_ty = fn.function_type.return_type
        if stmt.value is None:
            if isinstance(ret_ty, ir.VoidType):
                self.builder.ret_void()
                return
            # Python lets you ``return`` (bare) from a function
            # annotated with a non-None return type — it returns None
            # at runtime, which pcc can satisfy with a zero / NULL
            # value matching the declared IR type. Match that
            # behaviour so pcc sources that have early bare returns
            # don't hard-fail.
            if isinstance(ret_ty, ir.PointerType):
                self.builder.ret(ir.Constant(ret_ty, None))
            elif isinstance(ret_ty, ir.IntType):
                self.builder.ret(ir.Constant(ret_ty, 0))
            elif isinstance(ret_ty, (ir.FloatType, ir.DoubleType)):
                self.builder.ret(ir.Constant(ret_ty, 0.0))
            else:
                raise L1CodegenError(
                    f"bare 'return' fallback can't zero-init {ret_ty}"
                )
            return
        # ``return None`` where the function is declared to return None —
        # evaluate the expression for side-effects but emit ``ret void``.
        if isinstance(ret_ty, ir.VoidType):
            # Evaluate the expression for any side-effects even though
            # the value is discarded. NoneLit has none, so this is cheap.
            self._emit_expr(stmt.value)
            self.builder.ret_void()
            return
        if (
            isinstance(ret_ty, ir.PointerType)
            and isinstance(self.current_func_def.return_ty, IntType)
        ):
            value = self._emit_exact_int_operand_object(stmt.value)
            self.builder.ret(value)
            return
        value = self._emit_expr(stmt.value)
        value = self._coerce(value, stmt.value.ty, self.current_func_def.return_ty)
        # Final width / pointer match. ``_coerce`` handles the common
        # cases but not when a Dyn-inferred branch lowers to an
        # unboxed i64 while the function returns an object pointer;
        # box the native scalar back to PyObject* to satisfy the
        # terminator type contract.
        if value.type != ret_ty:
            if isinstance(ret_ty, ir.IntType) and isinstance(value.type, ir.IntType):
                if value.type.width > ret_ty.width:
                    value = self.builder.trunc(
                        value, ret_ty, name=self._fresh("ret.trunc"),
                    )
                elif value.type.width < ret_ty.width:
                    if value.type.width == 1:
                        value = self.builder.zext(
                            value, ret_ty, name=self._fresh("ret.zext"),
                        )
                    else:
                        value = self.builder.sext(
                            value, ret_ty, name=self._fresh("ret.sext"),
                        )
            if isinstance(ret_ty, ir.PointerType) and not isinstance(
                value.type, ir.PointerType
            ):
                value = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    value, stmt.value.ty,
                )
        self.builder.ret(value)

    # -- Assignment ----------------------------------------------------

    def _emit_assign(self, stmt: Assign) -> None:
        if len(stmt.targets) != 1:
            raise NotImplementedError(
                "Layer 1 does not handle tuple-unpacking assignment"
            )
        target = stmt.targets[0]

        # Tuple-unpacking assignment: ``a, b = x, y`` where the RHS is a
        # matching TupleExpr literal. Lower to a sequence of plain
        # assignments; Python semantics require that the whole RHS be
        # evaluated before any LHS is bound, which we mimic by emitting
        # every RHS into an SSA value first and only then storing.
        if isinstance(target, TupleExpr):
            return self._emit_tuple_unpack_assign(stmt, target)

        # Subscript target: ``lst[i] = v`` / ``d[k] = v``.
        if isinstance(target, Subscript):
            self._emit_subscript_store(target, stmt.value)
            return

        # Attribute target: currently only ``self.<attr> = value`` inside
        # a method body. Delegates to the class lowering helper which
        # uses the per-class field layout when known and falls back to
        # ``py_obj_setattr`` otherwise.
        if isinstance(target, Attr):
            self._emit_attr_store(target, stmt.value)
            return

        if not isinstance(target, Name):
            raise NotImplementedError(
                f"Layer 1/2 assignment target must be Name or Subscript; got "
                f"{type(target).__name__}"
            )

        # ``my_fn = extern("symbol", ...)`` — pcc.extern scaffold
        # declaration. No runtime IR emitted; just record the decl.
        if self._maybe_register_extern_assign(stmt):
            return

        builtin_alias_kind = self._native_builtin_value_kind_for_expr(
            stmt.value
        )
        if builtin_alias_kind is not None:
            self._register_native_builtin_value_alias(
                target.ident, builtin_alias_kind,
            )
            self.env.pop(target.ident, None)
            self.env_class_hint.pop(target.ident, None)
            if hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags.pop(target.ident, None)
            return
        self._clear_native_builtin_value_alias(target.ident)

        if not hasattr(self, "_ir_builder_env_flags"):
            self._ir_builder_env_flags = {}
        if self._expr_is_ir_builder_ctor(stmt.value):
            self._ir_builder_env_flags[target.ident] = True
        else:
            self._ir_builder_env_flags.pop(target.ident, None)

        # Track class hint for ``p = MyClass(args)`` so that ``p.method()``
        # can dispatch to ``MyClass``'s method even when type inference
        # labels ``p`` as ``DynType``.
        if isinstance(stmt.value, Call) and isinstance(stmt.value.func, Name):
            callee = stmt.value.func.ident
            if (
                hasattr(self, "class_lowering")
                and callee in self.class_lowering.classes
            ):
                self.env_class_hint[target.ident] = callee
            else:
                self.env_class_hint.pop(target.ident, None)
        elif isinstance(target.ty, ClassType):
            hint = self._ensure_class_type_registered(target.ty)
            if hint is not None:
                self.env_class_hint[target.ident] = hint
            else:
                self.env_class_hint.pop(target.ident, None)
        else:
            # Any other RHS invalidates the class hint.
            self.env_class_hint.pop(target.ident, None)
        list_elem_hint = self._list_elem_class_hint_for_expr(stmt.value)
        if list_elem_hint is not None:
            self.env_list_elem_class_hint[target.ident] = list_elem_hint
        else:
            self.env_list_elem_class_hint.pop(target.ident, None)

        target_ty = stmt.annotation if stmt.annotation is not None else target.ty
        boxed_int_target = (
            isinstance(target_ty, IntType)
            and self._int_exprs_are_boxed()
        )
        exact_int_value = None
        if boxed_int_target and isinstance(stmt.value.ty, (IntType, BoolType)):
            exact_int_value = self._emit_exact_int_operand_object(stmt.value)
        elif (
            isinstance(target_ty, IntType)
            and self._int_expr_needs_exact_object_boundary(stmt.value)
        ):
            exact_int_value = self._maybe_emit_exact_int_object(stmt.value)
        if exact_int_value is not None:
            value = exact_int_value
        else:
            value = self._emit_expr(stmt.value)

        if not hasattr(self, "_native_file_env_flags"):
            self._native_file_env_flags = {}
        if value in getattr(self, "_native_file_values", ()):
            self._native_file_env_flags[target.ident] = True
        else:
            self._native_file_env_flags.pop(target.ident, None)

        # Track "this local holds a CPython PyObject*" so subsequent
        # loads of the variable keep the tag, letting _to_int64 /
        # print / compare dispatch via the libpython helpers.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if value in getattr(self, "_cpy_values", ()):
            self._cpy_env_flags[target.ident] = True
        else:
            self._cpy_env_flags.pop(target.ident, None)
        if not hasattr(self, "_exact_int_env_flags"):
            self._exact_int_env_flags = {}

        # If this is a module-level global (seeded in the first pass),
        # write into the module variable and skip the local alloca
        # path. Guard on being inside the synthetic ``main`` body —
        # user-defined functions may still shadow with a local of the
        # same name, which is what the env fallback below handles.
        module_globals = self._module_globals
        if (
            self.current_func_def is not None
            and target.ident in self._current_global_names
        ):
            target_ty = (
                stmt.annotation if stmt.annotation is not None
                else target.ty
            )
            self._ensure_module_global_name(target.ident, target_ty)
        if (
            target.ident in module_globals
            and (
                self.current_func_def is None
                or target.ident in self._current_global_names
            )
        ):
            gv, declared_ty = module_globals[target.ident]
            value = self._coerce(value, stmt.value.ty, declared_ty)
            if value in getattr(self, "_cpy_values", ()):
                self._cpy_module_flags[target.ident] = True
            else:
                self._cpy_module_flags.pop(target.ident, None)
            self.builder.store(value, gv)
            return

        slot = self.env.get(target.ident)
        if slot is None:
            # First assignment — allocate.
            if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
                raise NotImplementedError(
                    f"Layer 1/2 cannot allocate variable "
                    f"{target.ident!r} of type {type(target_ty).__name__}"
                )
            ir_ty = (
                _CSTR if (boxed_int_target or exact_int_value is not None)
                else self._storage_ir_type(target_ty)
            )
            alloca = self._alloca_in_entry(ir_ty, name=f"{target.ident}.addr")
            self.env[target.ident] = (alloca, ir_ty, target_ty)
            slot = self.env[target.ident]

        alloca, ir_ty, declared_ty = slot
        if (
            (boxed_int_target or exact_int_value is not None)
            and ir_ty is not _CSTR
            and exact_int_value is not None
        ):
            alloca = self._alloca_in_entry(_CSTR, name=f"{target.ident}.obj.addr")
            self.env[target.ident] = (alloca, _CSTR, declared_ty)
            ir_ty = _CSTR
        if isinstance(declared_ty, IntType) and isinstance(ir_ty, ir.PointerType):
            if not isinstance(value.type, ir.PointerType):
                value = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    value, stmt.value.ty,
                )
            self._exact_int_env_flags[target.ident] = True
        else:
            self._exact_int_env_flags.pop(target.ident, None)
            value = self._coerce(value, stmt.value.ty, declared_ty)
        self.builder.store(value, alloca)

    def _emit_tuple_unpack_assign(
        self, stmt: Assign, target: TupleExpr,
    ) -> None:
        """Lower ``a, b = <rhs>`` into pair-wise name/subscript/attr
        assigns.

        Two RHS shapes are handled:

        * ``TupleExpr`` literal — each elem evaluated in source order,
          then bound into the corresponding target.
        * Any expression whose inferred type is ``TupleType`` with the
          correct arity — value is evaluated once, then each target is
          assigned from ``py_tuple_get(result, i)`` marshaled back to
          the declared element type.

        Anything else (e.g. list RHS, unknown iterable) remains
        unsupported.
        """
        rhs = stmt.value
        if isinstance(rhs, TupleExpr):
            if len(rhs.elems) != len(target.elems):
                raise L1CodegenError(
                    f"tuple unpack arity mismatch: {len(target.elems)} "
                    f"targets, {len(rhs.elems)} values"
                )
            rhs_vals: list = []
            for e in rhs.elems:
                rhs_vals.append((self._emit_expr(e), e.ty))
            for lhs, (val, val_ty) in zip(target.elems, rhs_vals):
                self._store_unpack_target(lhs, val, val_ty)
            return

        rhs_ty = rhs.ty
        if isinstance(rhs_ty, TupleType) and len(rhs_ty.elems) == len(target.elems):
            tup_val = self._emit_expr(rhs)
            tup_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, tup_val, rhs_ty,
            )
            for i, (lhs, elem_ty) in enumerate(zip(target.elems, rhs_ty.elems)):
                idx_val = ir.Constant(_I64, i)
                elem_obj = self.builder.call(
                    self.runtime["py_tuple_get"], [tup_obj, idx_val],
                    name=self._fresh(f"tup.{i}"),
                )
                # Marshal the PyObject* back to the declared element
                # type so downstream stores see a native value when
                # possible.
                native_val = elem_obj
                if not isinstance(elem_ty, DynType):
                    native_val = marshal.marshal_from_object(
                        self.builder, self.module, self.runtime,
                        elem_obj, elem_ty,
                    )
                self._store_unpack_target(lhs, native_val, elem_ty)
            return

        # DynType / ListType / TupleType-with-unknown-arity RHS:
        # assume a runtime sequence (any ``py_obj_getitem`` /
        # ``py_list_get``-friendly container). Indices are generated at
        # runtime regardless of the element count, so a mismatch
        # between the declared TupleType arity and the target arity
        # just means the inferer was imprecise (e.g. ``tuple(seq)`` at
        # runtime; inferer gave a TupleType of arbitrary size).
        if isinstance(rhs_ty, TupleType) and len(rhs_ty.elems) != len(target.elems):
            rhs_ty = DynType(name="dyn")
        if isinstance(rhs_ty, (DynType, ListType)):
            tup_val = self._emit_expr(rhs)
            elem_ty = (
                rhs_ty.elem if isinstance(rhs_ty, ListType) else DynType(name="dyn")
            )
            use_list_get = isinstance(rhs_ty, ListType)
            for i, lhs in enumerate(target.elems):
                if use_list_get:
                    elem_obj = self.builder.call(
                        self.runtime["py_list_get"],
                        [tup_val, ir.Constant(_I64, i)],
                        name=self._fresh(f"unpack.{i}"),
                    )
                else:
                    idx_box = self.builder.call(
                        self.runtime["py_int_from_i64"],
                        [ir.Constant(_I64, i)],
                        name=self._fresh("unpack.idx.box"),
                    )
                    elem_obj = self.builder.call(
                        self.runtime["py_obj_getitem"], [tup_val, idx_box],
                        name=self._fresh(f"unpack.{i}"),
                    )
                native_val = elem_obj
                if not isinstance(elem_ty, DynType):
                    native_val = marshal.marshal_from_object(
                        self.builder, self.module, self.runtime,
                        elem_obj, elem_ty,
                    )
                self._store_unpack_target(lhs, native_val, elem_ty)
            return

        raise NotImplementedError(
            "Layer 1 tuple-unpacking supports a TupleExpr RHS or an "
            "expression whose inferred type is a concrete tuple; "
            f"got {type(rhs).__name__} of type {rhs_ty}"
        )

    def _store_unpack_target(
        self, lhs: Expr, value: ir.Value, value_ty: Type,
    ) -> None:
        if isinstance(lhs, Subscript):
            self._store_value_at_subscript(lhs, value, value_ty)
            return
        if isinstance(lhs, Attr):
            self._store_value_at_attr(lhs, value, value_ty)
            return
        if isinstance(lhs, Name):
            self._store_value_at_name(lhs, value, value_ty)
            return
        if isinstance(lhs, TupleExpr):
            # Nested unpack: ``(b, c) = value`` where ``value`` is
            # a PyObject* tuple. Each inner slot fetched via
            # py_obj_getitem so the same code works for list /
            # tuple / dyn.
            for i, sub in enumerate(lhs.elems):
                idx_box = self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [ir.Constant(_I64, i)],
                    name=self._fresh("unpack.nested.idx.box"),
                )
                elem = self.builder.call(
                    self.runtime["py_obj_getitem"], [value, idx_box],
                    name=self._fresh(f"unpack.nested.{i}"),
                )
                # Slot type unknown at this layer — pass Dyn.
                self._store_unpack_target(sub, elem, DynType(name="dyn"))
            return
        raise NotImplementedError(
            f"Layer 1 tuple-unpack target kind "
            f"{type(lhs).__name__} not supported"
        )

    def _store_value_at_name(
        self, target: Name, value: ir.Value, value_ty: Type,
    ) -> None:
        """Store a pre-computed SSA value to a local / module global."""
        self.env_class_hint.pop(target.ident, None)
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if value in getattr(self, "_cpy_values", ()):
            self._cpy_env_flags[target.ident] = True
        else:
            self._cpy_env_flags.pop(target.ident, None)

        module_globals = self._module_globals
        if (
            self.current_func_def is not None
            and target.ident in self._current_global_names
        ):
            self._ensure_module_global_name(target.ident, target.ty)
        if (
            target.ident in module_globals
            and (
                self.current_func_def is None
                or target.ident in self._current_global_names
            )
        ):
            gv, declared_ty = module_globals[target.ident]
            value = self._coerce(value, value_ty, declared_ty)
            if value in getattr(self, "_cpy_values", ()):
                self._cpy_module_flags[target.ident] = True
            else:
                self._cpy_module_flags.pop(target.ident, None)
            self.builder.store(value, gv)
            return

        slot = self.env.get(target.ident)
        if slot is None:
            target_ty = target.ty
            if not (self._is_scalar(target_ty) or self._is_object(target_ty)):
                raise NotImplementedError(
                    f"Layer 1 tuple-unpack target {target.ident!r} has "
                    f"unsupported type {type(target_ty).__name__}"
                )
            ir_ty = self._storage_ir_type(target_ty)
            alloca = self._alloca_in_entry(ir_ty, name=f"{target.ident}.addr")
            self.env[target.ident] = (alloca, ir_ty, target_ty)
            slot = self.env[target.ident]

        alloca, ir_ty, declared_ty = slot
        value = self._coerce(value, value_ty, declared_ty)
        self.builder.store(value, alloca)

    def _store_value_at_subscript(
        self, target: Subscript, value: ir.Value, value_ty: Type,
    ) -> None:
        """Runtime subscript store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        idx_val = self._emit_expr(target.idx)
        v_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )
        k_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            idx_val, target.idx.ty,
        )
        self.builder.call(
            self.runtime["py_obj_setitem"], [obj, k_obj, v_obj],
        )

    def _store_value_at_attr(
        self, target: Attr, value: ir.Value, value_ty: Type,
    ) -> None:
        """Runtime attribute store given a pre-computed value."""
        obj = self._emit_expr(target.obj)
        v_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )
        name_ptr = self._attr_name_ptr(target.name)
        self.builder.call(
            self.runtime["py_obj_setattr"], [obj, name_ptr, v_obj],
        )

    def _emit_subscript_store(self, target: Subscript, value_expr: Expr) -> None:
        obj = self._emit_expr(target.obj)
        obj_ty = target.obj.ty
        idx_expr = target.idx
        rhs = self._emit_expr(value_expr)
        # RHS always marshals to PyObject* for container storage.
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, value_expr.ty
        )
        if obj in getattr(self, "_cpy_values", ()):
            cpy_key, key_owned = self._marshal_to_cpython(
                self._emit_expr(idx_expr), idx_expr.ty,
            )
            cpy_val, val_owned = self._marshal_to_cpython(
                rhs_obj, value_expr.ty,
            )
            self.builder.call(
                self.runtime["py_cpy_setitem"], [obj, cpy_key, cpy_val],
            )
            if key_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if val_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return
        if isinstance(idx_expr, Slice):
            def _as_cpy_obj(obj_v: ir.Value) -> ir.Value:
                if obj_v in getattr(self, "_cpy_values", ()):
                    return obj_v
                return self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [obj_v],
                    name=self._fresh("cpy.slice.obj"),
                )

            def _slice_bound(e):
                if e is None:
                    gv = declare_runtime_global(self.module, "py_None")
                    none = self.builder.load(gv, name=self._fresh("none"))
                    return self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"], [none],
                        name=self._fresh("cpy.none"),
                    )
                v = self._emit_expr(e)
                obj_v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, e.ty,
                )
                return _as_cpy_obj(obj_v)

            obj_cpy = _as_cpy_obj(obj)
            rhs_cpy = _as_cpy_obj(rhs_obj)
            slice_fn = self._load_cpython_builtin("slice")
            lo_cpy = _slice_bound(idx_expr.lo)
            hi_cpy = _slice_bound(idx_expr.hi)
            step_cpy = _slice_bound(idx_expr.step)
            slice_obj = self.builder.call(
                self.runtime["py_cpy_call3"],
                [slice_fn, lo_cpy, hi_cpy, step_cpy],
                name=self._fresh("cpy.slice"),
            )
            setitem_gv = self._cstr_global(
                "__setitem__", ".cpy.attr.__setitem__",
            )
            setitem_fn = self.builder.call(
                self.runtime["py_cpy_getattr"],
                [obj_cpy, self._ptr_to_cstr(setitem_gv)],
                name=self._fresh("cpy.setitem.fn"),
            )
            self.builder.call(
                self.runtime["py_cpy_call2"],
                [setitem_fn, slice_obj, rhs_cpy],
                name=self._fresh("cpy.setitem"),
            )
            return
        if isinstance(obj_ty, ListType):
            idx_i64 = self._emit_expr_as_i64(idx_expr)
            self.builder.call(
                self.runtime["py_list_set"], [obj, idx_i64, rhs_obj]
            )
            return
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(idx_expr)
            self.builder.call(
                self.runtime["py_dict_set"], [obj, key_obj, rhs_obj]
            )
            return
        if isinstance(obj_ty, TupleType):
            raise NotImplementedError(
                "tuples are immutable — subscript-assignment not allowed"
            )
        # Dynamic fallback for anything we didn't type statically.
        key_obj = self._emit_as_object(idx_expr)
        self.builder.call(
            self.runtime["py_obj_setitem"], [obj, key_obj, rhs_obj]
        )

    def _emit_augassign(self, stmt: AugAssign) -> None:
        op_bare = stmt.op.rstrip("=")
        if isinstance(stmt.target, Name):
            slot = self.env.get(stmt.target.ident)
            if slot is None:
                raise L1CodegenError(
                    f"augassign to undefined name {stmt.target.ident!r}"
                )
            alloca, ir_ty, declared_ty = slot
            cur = self.builder.load(
                alloca, name=self._fresh(stmt.target.ident),
            )
            rhs = self._emit_expr(stmt.value)
            result = self._emit_binop_value(
                op_bare, cur, declared_ty, rhs, stmt.value.ty,
                result_ty=declared_ty,
            )
            result = self._coerce(result, declared_ty, declared_ty)
            self.builder.store(result, alloca)
            return
        if isinstance(stmt.target, Subscript):
            # ``d[k] += rhs`` → d[k] = d[k] <op> rhs
            obj_val = self._emit_expr(stmt.target.obj)
            obj_ty = stmt.target.obj.ty
            idx_val = self._emit_expr(stmt.target.idx)
            idx_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                idx_val, stmt.target.idx.ty,
            )
            obj_as_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                obj_val, obj_ty,
            )
            cur_obj = self.builder.call(
                self.runtime["py_obj_getitem"], [obj_as_obj, idx_obj],
                name=self._fresh("augassign.cur"),
            )
            rhs = self._emit_expr(stmt.value)
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                rhs, stmt.value.ty,
            )
            result_raw = self._emit_binop_value(
                op_bare, cur_obj, DynType(name="dyn"),
                rhs_obj, DynType(name="dyn"),
                result_ty=DynType(name="dyn"),
            )
            # Box if not already a PyObject* (Dyn int binops return
            # i64).
            if result_raw.type is not _CSTR:
                result_raw = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    result_raw, IntType(name="int"),
                )
            self.builder.call(
                self.runtime["py_obj_setitem"],
                [obj_as_obj, idx_obj, result_raw],
            )
            return
        if isinstance(stmt.target, Attr):
            # ``self.x += rhs`` — load via attr, op, store back.
            target = stmt.target
            obj_val = self._emit_expr(target.obj)
            name_ptr = self._attr_name_ptr(target.name)
            cur_obj = self.builder.call(
                self.runtime["py_obj_getattr"], [obj_val, name_ptr],
                name=self._fresh("augassign.attr.cur"),
            )
            rhs = self._emit_expr(stmt.value)
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                rhs, stmt.value.ty,
            )
            result_raw = self._emit_binop_value(
                op_bare, cur_obj, DynType(name="dyn"),
                rhs_obj, DynType(name="dyn"),
                result_ty=DynType(name="dyn"),
            )
            if result_raw.type is not _CSTR:
                result_raw = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    result_raw, IntType(name="int"),
                )
            self.builder.call(
                self.runtime["py_obj_setattr"],
                [obj_val, name_ptr, result_raw],
            )
            return
        raise NotImplementedError(
            f"Layer 1 augassign target type "
            f"{type(stmt.target).__name__} not supported"
        )

    def _alloca_in_entry(self, ir_ty: ir.Type, name: str) -> ir.AllocaInstr:
        """Emit an alloca into the function's entry block.

        Matches the pattern used by :mod:`pcc.codegen.c_codegen`:
        allocas cluster at the top so mem2reg can promote them to
        SSA during opt passes.
        """
        fn = self.current_function
        entry = fn.blocks[0]
        cur = self.builder._block
        # Position at the end of entry, but before the first non-alloca
        # instruction if entry already has body content.
        insert_before = None
        for instr in entry._instrs:
            if instr.opname != "alloca":
                insert_before = instr
                break
        tmp_builder = ir.IRBuilder(entry)
        if insert_before is not None:
            tmp_builder.position_before(insert_before)
        else:
            tmp_builder.position_at_end(entry)
        alloca = tmp_builder.alloca(ir_ty, name=name)
        # Restore the main builder's insertion point.
        self.builder.position_at_end(cur)
        return alloca

    # -- Expression statement -----------------------------------------

    def _emit_expr_stmt(self, stmt: ExprStmt) -> None:
        # Special-case top-level ``print(...)``.
        expr = stmt.expr
        if isinstance(expr, Call) and isinstance(expr.func, Name) and expr.func.ident == "print":
            self._emit_print_call(expr)
            return
        if isinstance(expr, Call) and self._emit_native_subprocess_run_stmt(expr):
            return
        # Otherwise evaluate for side-effects.
        self._emit_expr(expr)

    # -- Print --------------------------------------------------------

    def _try_emit_native_file_stream_print(self, call: Call) -> bool:
        """Lower ``print(*args, file=sys.stderr|sys.stdout[, sep=, end=])``
        natively using ``py_sys_X_write``. Returns ``True`` when the
        dispatch fires; ``False`` lets the caller fall back to the
        CPython ``print`` path.

        Restrictions: ``sep`` / ``end`` must be string literals when
        present (otherwise we'd need a runtime helper to read their
        bytes). ``flush`` and any other kwargs trigger fallback.
        """
        file_expr = None
        sep_expr = None
        end_expr = None
        for k, v in call.kwargs:
            if k == "file":
                file_expr = v
            elif k == "sep":
                sep_expr = v
            elif k == "end":
                end_expr = v
            else:
                return False
        if file_expr is None:
            return False
        stream_kind = self._native_builtin_stream_kind_for_expr(file_expr)
        if stream_kind not in ("stdout", "stderr"):
            return False
        helper = self.runtime[
            "py_sys_stdout_write" if stream_kind == "stdout"
            else "py_sys_stderr_write"
        ]
        sep_str = " "
        end_str = "\n"
        if sep_expr is not None:
            if not isinstance(sep_expr, StrLit):
                return False
            sep_str = sep_expr.value
        if end_expr is not None:
            if not isinstance(end_expr, StrLit):
                return False
            end_str = end_expr.value
        # Empty print(file=...) just writes the end string.
        if not call.args:
            end_v = self._emit_str_literal(end_str)
            self.builder.call(helper, [end_v])
            return True
        sep_val = None
        for i, arg in enumerate(call.args):
            if i > 0:
                if sep_val is None:
                    sep_val = self._emit_str_literal(sep_str)
                self.builder.call(helper, [sep_val])
            arg_obj = self._emit_as_object(arg)
            if isinstance(arg.ty, StrType):
                arg_str = arg_obj
            else:
                arg_str = self.builder.call(
                    self.runtime["py_obj_str"], [arg_obj],
                    name=self._fresh("print.str"),
                )
            self.builder.call(helper, [arg_str])
        end_v = self._emit_str_literal(end_str)
        self.builder.call(helper, [end_v])
        return True

    def _emit_print_call(self, call: Call) -> None:
        if call.kwargs:
            # Phase 2 supports sep / end keyword args natively via
            # py_print_many. ``file=<fp>`` / ``flush=<bool>`` pull in
            # the libpython fallback — load CPython's builtin print
            # and dispatch through it with the full kwarg set.
            only_sep_end = True
            for k, _ in call.kwargs:
                if k != "sep" and k != "end":
                    only_sep_end = False
            if only_sep_end:
                self._emit_print_many(call)
                return
            # ``print(..., file=sys.stderr|sys.stdout[, sep=, end=])``
            # lowers natively to py_sys_X_write — no CPython fallback.
            if self._try_emit_native_file_stream_print(call):
                return
            fn_val = self._load_cpython_builtin("print")
            self._finish_cpy_call_kw(
                fn_val, "print", call.args, call.kwargs,
            )
            return

        if len(call.args) == 0:
            # print() with no args → print a bare newline. Emit a
            # printf("\n") to stay in L1 without touching the runtime.
            nl_gv = self._cstr_global("\n", ".fmt_nl")
            self.builder.call(self._printf, [self._ptr_to_cstr(nl_gv)])
            return

        if len(call.args) > 1:
            self._emit_print_many(call)
            return

        arg = call.args[0]
        arg_ty = arg.ty

        if isinstance(arg_ty, IntType):
            exact_obj = self._maybe_emit_exact_int_object(arg)
            if exact_obj is not None:
                self.builder.call(self.runtime["py_print"], [exact_obj])
                return

        value = self._emit_expr(arg)

        # CPython-backed value: convert to a pcc str via py_cpy_to_pcc_str
        # before feeding py_print.
        if value in getattr(self, "_cpy_values", ()):
            pcc_str = self.builder.call(
                self.runtime["py_cpy_to_pcc_str"], [value],
                name=self._fresh("cpy.str"),
            )
            self.builder.call(self.runtime["py_print"], [pcc_str])
            # Release the CPython reference held by ``value``.
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return

        if isinstance(arg_ty, IntType):
            if isinstance(value.type, ir.PointerType):
                self.builder.call(self.runtime["py_print"], [value])
                return
            fmt = self._ptr_to_cstr(self._get_fmt_int())
            self.builder.call(self._printf, [fmt, value])
            return
        if isinstance(arg_ty, FloatType):
            self._emit_print_float_value(value)
            return
        if isinstance(arg_ty, BoolType):
            # Select between "True\n" and "False\n" at runtime.
            true_fmt = self._ptr_to_cstr(self._get_fmt_bool_true())
            false_fmt = self._ptr_to_cstr(self._get_fmt_bool_false())
            chosen = self.builder.select(value, true_fmt, false_fmt,
                                          name=self._fresh("bool_fmt"))
            self.builder.call(self._printf, [chosen])
            return

        # Object-typed print (str / list / dict / tuple / None / dyn) —
        # dispatch to ``py_print``. The runtime handles repr + newline.
        obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, arg_ty
        )
        self.builder.call(self.runtime["py_print"], [obj])

    def _maybe_emit_exact_int_object(
        self, expr: Expr,
    ) -> Optional[ir.Value]:
        """Return a PyObject* for int expressions that need exact
        arbitrary-precision runtime arithmetic at an object boundary.

        The general L1 local int representation is still native i64 unless
        an exact object result has already reached the local. Printing,
        containers, and user-function int object ABI boundaries are places
        where exact Python integer results are directly observable, so
        compound int arithmetic can box operands and call the runtime
        bignum helpers without boxing every hot-loop local.
        """
        if not isinstance(expr.ty, IntType):
            return None
        if isinstance(expr, Name) and getattr(
            self, "_exact_int_env_flags", {},
        ).get(expr.ident, False):
            value = self._emit_expr(expr)
            if isinstance(value.type, ir.PointerType):
                return value
        if isinstance(expr, BinOp):
            fn_name = {
                "+": "py_int_add",
                "-": "py_int_sub",
                "*": "py_int_mul",
                "//": "py_int_floordiv",
                "%": "py_int_mod",
                "**": "py_int_pow",
                "&": "py_int_and",
                "|": "py_int_or",
                "^": "py_int_xor",
                "<<": "py_int_shl",
                ">>": "py_int_shr",
            }.get(expr.op)
            if fn_name is None:
                return None
            if not (
                isinstance(expr.lhs.ty, (IntType, BoolType))
                and isinstance(expr.rhs.ty, (IntType, BoolType))
            ):
                return None
            lhs = self._emit_exact_int_operand_object(expr.lhs)
            rhs = self._emit_exact_int_operand_object(expr.rhs)
            if self._int_exprs_are_boxed():
                inline = self._emit_inline_tagged_int_binop_or_call(
                    expr.op, lhs, rhs, fn_name,
                )
                if inline is not None:
                    return inline
            return self.builder.call(
                self.runtime[fn_name], [lhs, rhs],
                name=self._fresh("exact.int"),
            )
        if (
            isinstance(expr, UnaryOp)
            and expr.op == "-"
            and isinstance(expr.operand.ty, (IntType, BoolType))
        ):
            operand = self._emit_exact_int_operand_object(expr.operand)
            return self.builder.call(
                self.runtime["py_int_neg"], [operand],
                name=self._fresh("exact.int.neg"),
            )
        if isinstance(expr, IntLit):
            value = int(expr.value)
            if value < -(1 << 63) or value > (1 << 63) - 1:
                return self._emit_int_literal_object(value)
        if isinstance(expr, Subscript):
            return self._emit_subscript_load_object(expr)
        return None

    def _int_expr_needs_exact_object_boundary(self, expr: Expr) -> bool:
        if not isinstance(expr.ty, IntType):
            return False
        if isinstance(expr, Name):
            return getattr(self, "_exact_int_env_flags", {}).get(
                expr.ident, False,
            )
        if isinstance(expr, Call) and isinstance(expr.func, Name):
            fn = self.functions.get(expr.func.ident)
            if fn is not None and isinstance(
                fn.function_type.return_type, ir.PointerType
            ):
                return True
        if isinstance(expr, IntLit):
            value = int(expr.value)
            return value < -(1 << 63) or value > (1 << 63) - 1
        if isinstance(expr, BinOp):
            if expr.op == "**":
                return True
            if (
                self._int_expr_needs_exact_object_boundary(expr.lhs)
                or self._int_expr_needs_exact_object_boundary(expr.rhs)
            ):
                return True
            if isinstance(expr.lhs, IntLit) and isinstance(expr.rhs, IntLit):
                try:
                    lhs = int(expr.lhs.value)
                    rhs = int(expr.rhs.value)
                    if expr.op == "+":
                        value = lhs + rhs
                    elif expr.op == "-":
                        value = lhs - rhs
                    elif expr.op == "*":
                        value = lhs * rhs
                    elif expr.op == "//" and rhs != 0:
                        value = lhs // rhs
                    elif expr.op == "%" and rhs != 0:
                        value = lhs % rhs
                    elif expr.op == "<<":
                        value = lhs << rhs
                    elif expr.op == ">>":
                        value = lhs >> rhs
                    elif expr.op == "&":
                        value = lhs & rhs
                    elif expr.op == "|":
                        value = lhs | rhs
                    elif expr.op == "^":
                        value = lhs ^ rhs
                    else:
                        return False
                except (OverflowError, ValueError):
                    return True
                return value < -(1 << 63) or value > (1 << 63) - 1
            return False
        if isinstance(expr, UnaryOp) and expr.op == "-":
            if self._int_expr_needs_exact_object_boundary(expr.operand):
                return True
            if isinstance(expr.operand, IntLit):
                value = -int(expr.operand.value)
                return value < -(1 << 63) or value > (1 << 63) - 1
            return False
        return False

    def _emit_exact_int_compare(
        self, expr: Compare,
    ) -> Optional[ir.Value]:
        if expr.op not in ("==", "!=", "<", "<=", ">", ">="):
            return None
        if not (
            isinstance(expr.lhs.ty, (IntType, BoolType))
            and isinstance(expr.rhs.ty, (IntType, BoolType))
        ):
            return None
        if not (
            self._int_expr_needs_exact_object_boundary(expr.lhs)
            or self._int_expr_needs_exact_object_boundary(expr.rhs)
        ):
            return None
        lhs = self._emit_exact_int_operand_object(expr.lhs)
        rhs = self._emit_exact_int_operand_object(expr.rhs)
        cmp_i32 = self.builder.call(
            self.runtime["py_int_cmp"], [lhs, rhs],
            name=self._fresh("exact.int.cmp"),
        )
        zero = ir.Constant(_I32, 0)
        pred = {
            "==": "==",
            "!=": "!=",
            "<": "<",
            "<=": "<=",
            ">": ">",
            ">=": ">=",
        }[expr.op]
        return self.builder.icmp_signed(
            pred, cmp_i32, zero, name=self._fresh("exact.int.cmp.i1"),
        )

    def _emit_exact_int_operand_object(self, expr: Expr) -> ir.Value:
        exact = self._maybe_emit_exact_int_object(expr)
        if exact is not None:
            return exact
        if isinstance(expr, IntLit):
            return self._emit_int_literal_object(int(expr.value))
        if isinstance(expr, BoolLit):
            return self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, 1 if expr.value else 0)],
                name=self._fresh("print.int.box"),
            )
        value = self._emit_expr(expr)
        if isinstance(value.type, ir.PointerType):
            return value
        i64 = self._to_int64(value, expr.ty)
        return self.builder.call(
            self.runtime["py_int_from_i64"], [i64],
            name=self._fresh("exact.int.box"),
        )

    def _emit_expr_as_pcc_object(self, expr: Expr) -> ir.Value:
        if isinstance(expr.ty, IntType):
            exact = self._maybe_emit_exact_int_object(expr)
            if exact is not None:
                return exact
        value = self._emit_expr(expr)
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, expr.ty,
        )

    def _emit_subscript_load_object(self, expr: Subscript) -> Optional[ir.Value]:
        if isinstance(expr.idx, Slice):
            return None
        obj_ty = expr.obj.ty
        obj = self._emit_expr(expr.obj)
        if obj in getattr(self, "_cpy_values", ()):
            return None
        if isinstance(obj_ty, ListType):
            idx = self._emit_expr_as_i64(expr.idx)
            return self.builder.call(
                self.runtime["py_list_get"], [obj, idx],
                name=self._fresh("list.get.obj"),
            )
        if isinstance(obj_ty, TupleType):
            idx = self._emit_expr_as_i64(expr.idx)
            return self.builder.call(
                self.runtime["py_tuple_get"], [obj, idx],
                name=self._fresh("tup.get.obj"),
            )
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(expr.idx)
            return self.builder.call(
                self.runtime["py_dict_get"], [obj, key_obj],
                name=self._fresh("dict.get.obj"),
            )
        if isinstance(obj_ty, DynType):
            key_obj = self._emit_as_object(expr.idx)
            return self.builder.call(
                self.runtime["py_obj_getitem"], [obj, key_obj],
                name=self._fresh("obj.getitem.obj"),
            )
        return None

    def _emit_int_literal_object(self, value: int) -> ir.Value:
        if -(1 << 63) <= value <= (1 << 63) - 1:
            return self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, value)],
                name=self._fresh("print.int.lit"),
            )
        gv, _ = self._cstr_literal(str(value))
        return self.builder.call(
            self.runtime["py_int_from_cstr"],
            [self._ptr_to_cstr(gv), ir.Constant(_I32, 10)],
            name=self._fresh("print.int.lit.big"),
        )

    def _emit_print_float_value(self, value: ir.Value) -> None:
        floor_fn = self._get_floor_intrinsic()
        floored = self.builder.call(
            floor_fn, [value], name=self._fresh("print.float.floor"),
        )
        integral = self.builder.fcmp_ordered(
            "==", value, floored, name=self._fresh("print.float.integral"),
        )
        int_bb = self.current_function.append_basic_block(
            name=self._fresh("print.float.int"),
        )
        general_bb = self.current_function.append_basic_block(
            name=self._fresh("print.float.general"),
        )
        end_bb = self.current_function.append_basic_block(
            name=self._fresh("print.float.end"),
        )
        self.builder.cbranch(integral, int_bb, general_bb)

        self.builder.position_at_end(int_bb)
        fmt_one = self._ptr_to_cstr(
            self._cstr_global("%.1f\n", ".fmt_float_one_decimal"),
        )
        self.builder.call(self._printf, [fmt_one, value])
        self.builder.branch(end_bb)

        self.builder.position_at_end(general_bb)
        fmt = self._ptr_to_cstr(self._get_fmt_float())
        self.builder.call(self._printf, [fmt, value])
        self.builder.branch(end_bb)

        self.builder.position_at_end(end_bb)

    def _emit_print_many(self, call: Call) -> None:
        """Emit ``py_print_many(args_tuple, sep, end)`` for print with N args.

        Each positional is marshalled into a PyObject* and stored into
        a freshly allocated tuple. Keyword args ``sep`` / ``end`` are
        passed through if present; otherwise defaults (`" "` / `"\\n"`)
        get boxed inline.
        """
        n = len(call.args)
        n_val = ir.Constant(_I64, n)
        tup = self.builder.call(self.runtime["py_tuple_new"], [n_val],
                                  name=self._fresh("pr.args"))
        for i, arg in enumerate(call.args):
            if isinstance(arg.ty, IntType):
                exact_obj = self._maybe_emit_exact_int_object(arg)
                if exact_obj is not None:
                    v_obj = exact_obj
                    idx = ir.Constant(_I64, i)
                    self.builder.call(
                        self.runtime["py_tuple_set_item"], [tup, idx, v_obj]
                    )
                    continue
            v = self._emit_expr(arg)
            # CPython-backed values need to be converted to a pcc
            # PyStrObject before going into a pcc tuple — otherwise
            # py_print_many walks them as if they were pcc PyObject*
            # and prints the raw pointer.
            if v in getattr(self, "_cpy_values", ()):
                pcc_str = self.builder.call(
                    self.runtime["py_cpy_to_pcc_str"], [v],
                    name=self._fresh("cpy.str"),
                )
                self.builder.call(self.runtime["py_cpy_decref"], [v])
                v_obj = pcc_str
            else:
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, arg.ty
                )
            idx = ir.Constant(_I64, i)
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, idx, v_obj]
            )

        sep_obj: Optional[ir.Value] = None
        end_obj: Optional[ir.Value] = None
        for k, vexpr in call.kwargs:
            v = self._emit_expr(vexpr)
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, vexpr.ty
            )
            if k == "sep":
                sep_obj = boxed
            elif k == "end":
                end_obj = boxed
        if sep_obj is None:
            sep_obj = self._emit_literal_str(" ")
        if end_obj is None:
            end_obj = self._emit_literal_str("\n")
        self.builder.call(
            self.runtime["py_print_many"], [tup, sep_obj, end_obj]
        )

    def _emit_literal_str(self, s: str) -> ir.Value:
        return self._emit_str_literal(s)

    # -- Imports (Phase 4 CPython C-API fallback) ---------------------

    def _cpy_module_global(self, local_name: str) -> ir.GlobalVariable:
        """Return (or create) the module-level ``i8*`` global that
        stores the imported CPython ``PyObject *``. Shared across
        functions so a user's ``main()`` can read a module bound by a
        top-level ``import`` statement."""
        gname = f".cpy.modref.{local_name}"
        existing = self.module.globals.get(gname)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        g = ir.GlobalVariable(self.module, _CSTR, name=gname)
        g.linkage = "internal"
        g.initializer = ir.Constant(_CSTR, None)
        return g

    _EXTERN_SCAFFOLD_MODULES = frozenset({
        "pcc.extern", "pcc.llvm_capi", "pcc.llvm_capi.compat",
    })

    _IR_RUNTIME_COMPAT_MODULE = "pcc.llvm_capi.compat"

    _UNSAFE_SCAFFOLD_MODULES = frozenset({
        "pcc.unsafe",
    })

    def _ir_scaffold_enabled(self) -> bool:
        return (
            self.ir_scaffold_mode == "on"
            or self.ast_module.name == "pcc.py_frontend.codegen.runtime_abi"
        )

    def _is_extern_scaffold_import_module(
        self, module_name: Optional[str],
    ) -> bool:
        if module_name == self._IR_RUNTIME_COMPAT_MODULE:
            return self._ir_scaffold_enabled()
        return module_name in self._EXTERN_SCAFFOLD_MODULES

    # No-op decorator whitelist: decorators here are treated as
    # identity transforms at pcc codegen (the function definition
    # proceeds unchanged). Used for libraries like ``click`` whose
    # decorators attach CLI metadata that pcc doesn't model, and for
    # ``functools.wraps`` which is a no-op for pcc's purposes.
    _NOOP_DECORATOR_QUALIFIED = frozenset({
        "click.command",
        "click.option",
        "click.argument",
        "click.pass_context",
        "click.group",
        "click.pass_obj",
        "functools.wraps",
        # functools caching decorators — pcc treats them as identity
        # wrappers. The first call through a cached function does the
        # real work; pcc doesn't carry a per-call memo table, so every
        # call re-runs. Correctness holds because the wrapped bodies
        # are pure. Callers that rely on cache timing fall back to
        # recomputation.
        "functools.lru_cache",
        "functools.cache",
        # Dataclass family — the class body is already handled by
        # ``class_lowering`` (field slot layout + ctor synthesis); the
        # decorator itself is purely informational at codegen time.
        "dataclasses.dataclass",
        # ABC marker — pcc's @abstractmethod stub sets a flag that
        # class_gen reads; no codegen impact beyond the whitelist.
        "abc.abstractmethod",
        # @contextlib.contextmanager — decorated function is a plain
        # generator turned into a context manager; codegen still sees
        # the generator body (now lowered via the generator-to-list
        # transform) and treats the resulting value as a CPython
        # context-manager for ``with ...`` dispatch.
        "contextlib.contextmanager",
    })

    _NOOP_DECORATOR_BARE = frozenset({
        "wraps",
        "lru_cache",
        "cache",
        "dataclass",
        "abstractmethod",
        "contextmanager",
    })

    def _decorator_c_abi_export_symbol(self, dec) -> str | None:
        """If ``dec`` is ``@c_abi_export("sym")`` or
        ``@pcc.extern.c_abi_export("sym")``, return the literal symbol
        string. Else None."""
        if not isinstance(dec, Call):
            return None
        qn = self._decorator_qualname(dec.func)
        if qn not in ("c_abi_export", "pcc.extern.c_abi_export", "extern.c_abi_export"):
            return None
        if len(dec.args) != 1:
            return None
        arg = dec.args[0]
        from ..py_ast import StrLit
        if not isinstance(arg, StrLit):
            return None
        return arg.value

    def _decorator_qualname(self, dec):
        """Return a dotted identifier for a decorator expression or
        ``None`` if the decorator isn't a simple Name / Attr chain."""
        # ``@foo`` — bare Name
        if isinstance(dec, Name):
            return dec.ident
        # ``@foo.bar`` or deeper
        if isinstance(dec, Attr):
            parts: list[str] = [dec.name]
            cur = dec.obj
            while isinstance(cur, Attr):
                parts.append(cur.name)
                cur = cur.obj
            if isinstance(cur, Name):
                parts.append(cur.ident)
                return self._join_reversed_strs(parts)
            return None
        # ``@foo(args)`` or ``@foo.bar(args)`` — Call wrapping a name chain
        if isinstance(dec, Call):
            return self._decorator_qualname(dec.func)
        return None

    def _decorator_is_noop_whitelist(self, dec) -> bool:
        qn = self._decorator_qualname(dec)
        if qn is None:
            return False
        if qn in self._NOOP_DECORATOR_QUALIFIED:
            return True
        if "." not in qn and qn in self._NOOP_DECORATOR_BARE:
            return True
        return False

    def _decorator_repr(self, dec) -> str:
        qn = self._decorator_qualname(dec)
        return qn if qn is not None else type(dec).__name__

    # Compile-time-only stdlib modules whose imports pcc consumes at
    # the frontend (type annotations, decorators) before codegen runs.
    # Routing these through ``py_cpy_import`` would pull libpython into
    # every produced binary — instead the import emits nothing and the
    # names they expose never materialise as runtime values. Anything
    # that *does* need a runtime binding (e.g. ``typing.cast`` used as
    # a first-class value) must be added as an explicit builtin.
    _COMPILE_TIME_ONLY_MODULES = frozenset({
        "__future__",
        "typing",
        # ``click`` contributes decorators and marker values that pcc
        # treats as no-ops; see the decorator whitelist above. Tests
        # that actually need click-parsed CLI args run under CPython
        # and don't reach this path.
        "click",
        # pcc.extern exposes C FFI type markers (``c_int`` / ``c_ptr``
        # etc.) consumed at codegen time — the resulting binary never
        # resolves them at runtime. Drop the import so self-host
        # doesn't chase missing symbols.
        "pcc.extern",
    })

    _COMPILE_TIME_ONLY_IMPORT_FROMS = {
        "dataclasses": frozenset({"dataclass", "field"}),
    }

    def _filter_runtime_import_from_names(self, stmt: ImportFrom) -> list[tuple[str, Optional[str]]]:
        """Drop ``from X import Y`` bindings consumed entirely at compile time.

        Keep this narrower than ``_COMPILE_TIME_ONLY_MODULES``: only
        names whose runtime binding is provably unused by pcc's lowered
        code should vanish here. ``dataclass`` and ``field`` are handled
        natively by class lowering / call lowering, while other
        dataclasses helpers such as ``replace`` still need a real runtime
        binding.
        """
        import_module = stmt.module or ""
        compile_only = self._COMPILE_TIME_ONLY_IMPORT_FROMS.get(import_module)
        if not compile_only:
            return list(stmt.names)
        filtered: list[tuple[str, Optional[str]]] = []
        for attr_name, as_name in stmt.names:
            if (
                attr_name in compile_only
                and (as_name is None or as_name == attr_name)
            ):
                continue
            filtered.append((attr_name, as_name))
        return filtered

    def _register_native_builtin_module_alias(
        self, local_name: str, module_name: str,
    ) -> None:
        if not hasattr(self, "_native_builtin_module_aliases"):
            self._native_builtin_module_aliases = {}
        self._native_builtin_module_aliases[local_name] = module_name

    def _register_native_builtin_value_alias(
        self, local_name: str, value_kind: str,
    ) -> None:
        if not hasattr(self, "_native_builtin_value_aliases"):
            self._native_builtin_value_aliases = {}
        self._native_builtin_value_aliases[local_name] = value_kind

    def _clear_native_builtin_value_alias(self, local_name: str) -> None:
        aliases = getattr(self, "_native_builtin_value_aliases", None)
        if aliases is not None:
            aliases.pop(local_name, None)

    def _native_builtin_module_for_name(self, ident: str) -> Optional[str]:
        if ident in self.env:
            return None
        if ident in getattr(self, "_module_globals", {}):
            return None
        return getattr(self, "_native_builtin_module_aliases", {}).get(ident)

    def _native_builtin_value_for_name(self, ident: str) -> Optional[str]:
        if ident in self.env:
            return None
        if ident in getattr(self, "_module_globals", {}):
            return None
        return getattr(self, "_native_builtin_value_aliases", {}).get(ident)

    def _native_builtin_value_kind_for_expr(self, expr: Expr) -> Optional[str]:
        if isinstance(expr, Name):
            return self._native_builtin_value_for_name(expr.ident)
        if (
            isinstance(expr, Attr)
            and expr.name == "path"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "os"
        ):
            return "os.path"
        if (
            isinstance(expr, Attr)
            and expr.name == "exit"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
        ):
            return "sys.exit"
        return None

    def _all_import_from_names_are_native_builtins(
        self, stmt: ImportFrom, import_module: str,
    ) -> bool:
        if import_module == "sys":
            return all(
                attr_name in ("exit", "stdout", "stderr")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "os":
            return all(
                attr_name == "path"
                for attr_name, _as_name in stmt.names
            )
        if import_module == "dataclasses":
            return all(
                attr_name == "replace"
                for attr_name, _as_name in stmt.names
            )
        return False

    def _register_native_builtin_import_from_aliases(
        self, stmt: ImportFrom, import_module: str,
    ) -> bool:
        if not self._all_import_from_names_are_native_builtins(
            stmt, import_module,
        ):
            return False
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            if attr_name == "path" and import_module == "os":
                self._register_native_builtin_value_alias(local_name, "os.path")
                continue
            if attr_name == "exit" and import_module == "sys":
                self._register_native_builtin_value_alias(local_name, "sys.exit")
                continue
            if attr_name == "replace" and import_module == "dataclasses":
                self._register_native_builtin_value_alias(
                    local_name, "dataclasses.replace",
                )
                continue
            if attr_name in ("stdout", "stderr") and import_module == "sys":
                self._register_native_builtin_value_alias(
                    local_name, "sys." + attr_name,
                )
                continue
        return True

    def _emit_cpython_module_value(self, module_name: str) -> ir.Value:
        self._ensure_cpy_init()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(module_name, f".cpy.mod.{module_name}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(f"cpy.import.{module_name.replace('.', '_')}"),
        )
        return self._mark_cpy_value(mod_val)

    def _is_os_environ_attr(self, expr: Expr) -> bool:
        """Recognise the ``os.environ`` attribute expression."""
        return (
            isinstance(expr, Attr)
            and expr.name == "environ"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "os"
        )

    def _emit_native_os_environ_call(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """Lower ``os.environ.get(name[, default])`` to ``py_os_getenv``.

        ``os.environ.get`` and ``os.getenv`` are semantically equivalent
        for the missing-key case (both return ``default``), so they
        share a runtime helper.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or not self._is_os_environ_attr(attr.obj):
            return None
        if attr.name == "get" and 1 <= len(expr.args) <= 2:
            default_obj = (
                self._emit_none_literal()
                if len(expr.args) == 1 else self._emit_as_object(expr.args[1])
            )
            return self.builder.call(
                self.runtime["py_os_getenv"],
                [self._emit_as_object(expr.args[0]), default_obj],
                name=self._fresh("os.environ.get"),
            )
        return None

    def _emit_native_os_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "os"
        ):
            return None
        name = attr.name
        if name == "getenv" and 1 <= len(expr.args) <= 2:
            default_obj = (
                self._emit_none_literal()
                if len(expr.args) == 1 else self._emit_as_object(expr.args[1])
            )
            return self.builder.call(
                self.runtime["py_os_getenv"],
                [self._emit_as_object(expr.args[0]), default_obj],
                name=self._fresh("os.getenv"),
            )
        if name == "putenv" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_os_putenv"],
                [
                    self._emit_as_object(expr.args[0]),
                    self._emit_as_object(expr.args[1]),
                ],
                name=self._fresh("os.putenv"),
            )
        if name == "unsetenv" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_os_unsetenv"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("os.unsetenv"),
            )
        if name == "getcwd" and len(expr.args) == 0:
            return self.builder.call(
                self.runtime["py_os_getcwd_str"], [],
                name=self._fresh("os.getcwd"),
            )
        if name == "listdir" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_os_listdir"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("os.listdir"),
            )
        if name == "access" and len(expr.args) == 2:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            mode_i64 = self._emit_expr_as_i64(expr.args[1])
            mode_val = self.builder.trunc(
                mode_i64, _I32, name=self._fresh("os.access.mode"),
            )
            i32v = self.builder.call(
                self.runtime["py_os_access"],
                [self._emit_os_path_arg_object(expr.args[0]), mode_val],
                name=self._fresh("os.access"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh("os.access.i1"),
            )
        return None

    def _emit_native_platform_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or expr.args:
            return None
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident)
            != "platform"
        ):
            return None
        if attr.name == "machine":
            return self.builder.call(
                self.runtime["py_platform_machine_str"], [],
                name=self._fresh("platform.machine"),
            )
        if attr.name == "release":
            return self.builder.call(
                self.runtime["py_platform_release_str"], [],
                name=self._fresh("platform.release"),
            )
        return None

    def _subprocess_text_kwargs_are_native(self, expr: Call) -> bool:
        if not expr.kwargs:
            return False
        seen_text = False
        for key, value in expr.kwargs:
            if key not in ("text", "universal_newlines"):
                return False
            if not isinstance(value, BoolLit) or not value.value:
                return False
            seen_text = True
        return seen_text

    def _emit_native_subprocess_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident)
            != "subprocess"
        ):
            return None
        if attr.name != "check_output":
            return None
        if len(expr.args) != 1:
            return None
        if not self._subprocess_text_kwargs_are_native(expr):
            return None
        argv = self._emit_expr(expr.args[0])
        if argv in getattr(self, "_cpy_values", ()):
            return None
        return self.builder.call(
            self.runtime["py_subprocess_check_output"],
            [marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                argv, expr.args[0].ty,
            )],
            name=self._fresh("subprocess.check_output"),
        )

    def _emit_native_subprocess_run_stmt(self, expr: Call) -> bool:
        if not isinstance(expr.func, Attr):
            return False
        attr = expr.func
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident)
            != "subprocess"
            or attr.name != "run"
            or len(expr.args) != 1
        ):
            return False
        check_true = False
        capture_output_val = ir.Constant(_I32, 0)
        for key, value in expr.kwargs:
            if key == "check":
                if not isinstance(value, BoolLit) or not value.value:
                    return False
                check_true = True
            elif key == "capture_output":
                if isinstance(value, BoolLit):
                    capture_output_val = ir.Constant(
                        _I32, 1 if value.value else 0,
                    )
                else:
                    raw = self._emit_expr(value)
                    truthy = self._truthy(raw, value.ty)
                    capture_output_val = self.builder.zext(
                        truthy, _I32, name=self._fresh("subprocess.capture"),
                    )
            elif key == "text":
                if not isinstance(value, BoolLit):
                    return False
            else:
                return False
        if not check_true:
            return False
        argv = self._emit_expr(expr.args[0])
        if argv in getattr(self, "_cpy_values", ()):
            return False
        argv_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, argv, expr.args[0].ty,
        )
        rc = self.builder.call(
            self.runtime["py_subprocess_run"],
            [argv_obj, capture_output_val],
            name=self._fresh("subprocess.run"),
        )
        failed = self.builder.icmp_signed(
            "!=", rc, ir.Constant(_I64, 0),
            name=self._fresh("subprocess.run.failed"),
        )
        fn = self.current_function
        fail_bb = fn.append_basic_block(name=self._fresh("subprocess.run.fail"))
        ok_bb = fn.append_basic_block(name=self._fresh("subprocess.run.ok"))
        self.builder.cbranch(failed, fail_bb, ok_bb)
        self.builder.position_at_end(fail_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, self._BUILTIN_EXC_TAG["RuntimeError"]),
                self._ptr_to_cstr(self._cstr_global(
                    "subprocess.run failed",
                    self._fresh(".err.subprocess.run"),
                )),
            ],
            name=self._fresh("subprocess.run.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        self.builder.position_at_end(ok_bb)
        return True

    def _emit_native_shutil_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident)
            != "shutil"
            or attr.name != "which"
            or len(expr.args) != 1
            or expr.kwargs
        ):
            return None
        name_obj = self._emit_as_object(expr.args[0])
        return self.builder.call(
            self.runtime["py_shutil_which"],
            [name_obj],
            name=self._fresh("shutil.which"),
        )

    def _emit_native_shlex_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "shlex"
            or attr.name != "split"
            or len(expr.args) != 1
            or expr.kwargs
        ):
            return None
        return self.builder.call(
            self.runtime["py_shlex_split"],
            [self._emit_as_object(expr.args[0])],
            name=self._fresh("shlex.split"),
        )

    def _emit_native_sysconfig_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident)
            != "sysconfig"
            or attr.name != "get_config_var"
            or len(expr.args) != 1
            or expr.kwargs
        ):
            return None
        return self.builder.call(
            self.runtime["py_sysconfig_get_config_var"],
            [self._emit_as_object(expr.args[0])],
            name=self._fresh("sysconfig.get_config_var"),
        )

    def _native_builtin_stream_kind_for_expr(
        self, expr: Expr,
    ) -> Optional[str]:
        if (
            isinstance(expr, Attr)
            and expr.name in ("stdout", "stderr")
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
        ):
            return expr.name
        return None

    def _emit_native_sys_stream_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or len(expr.args) != 1 or attr.name != "write":
            return None
        stream_kind = self._native_builtin_stream_kind_for_expr(attr.obj)
        if stream_kind is None:
            return None
        helper = (
            "py_sys_stdout_write"
            if stream_kind == "stdout"
            else "py_sys_stderr_write"
        )
        return self.builder.call(
            self.runtime[helper],
            [self._emit_as_object(expr.args[0])],
            name=self._fresh(f"sys.{stream_kind}.write"),
        )

    def _emit_native_sys_exit_call(self, expr: Call) -> Optional[ir.Value]:
        if expr.kwargs or len(expr.args) > 1:
            return None
        kind = None
        if isinstance(expr.func, Name):
            kind = self._native_builtin_value_for_name(expr.func.ident)
        elif isinstance(expr.func, Attr):
            kind = self._native_builtin_value_kind_for_expr(expr.func)
        if kind != "sys.exit":
            return None
        code_i64 = ir.Constant(_I64, 0)
        if len(expr.args) == 1:
            code_val = self._emit_expr(expr.args[0])
            code_i64 = self._to_int64(code_val, expr.args[0].ty)
        self.builder.call(
            self.runtime["py_process_exit"], [code_i64],
        )
        return ir.Constant(_CSTR, None)

    def _emit_native_dataclasses_replace_call(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
        if kwdict_unpack is None:
            positional = expr.args
            kwdict_expr = None
        else:
            positional, kwdict_expr = kwdict_unpack
        if len(positional) != 1:
            return None
        kind = None
        if isinstance(expr.func, Name):
            kind = self._native_builtin_value_for_name(expr.func.ident)
            if (
                kind is None
                and expr.func.ident in ("replace", "_replace")
                and expr.func.ident not in self.env
                and expr.func.ident not in getattr(self, "_module_globals", {})
            ):
                kind = "dataclasses.replace"
        elif isinstance(expr.func, Attr):
            kind = self._native_builtin_value_kind_for_expr(expr.func)
        if kind != "dataclasses.replace":
            return None
        obj_val = self._emit_expr(positional[0])
        obj_val = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            obj_val, positional[0].ty,
        )
        if kwdict_expr is not None:
            if expr.kwargs:
                return None
            kwdict_val = self._emit_expr(kwdict_expr)
            kwdict_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                kwdict_val, kwdict_expr.ty,
            )
            return self.builder.call(
                self.runtime["py_dataclass_replace_from_dict"],
                [obj_val, kwdict_obj],
                name=self._fresh("dataclasses.replace.kwdict"),
            )

        n_kw = len(expr.kwargs)
        if n_kw == 0:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
        else:
            names_arr_ty = ir.ArrayType(_CSTR, n_kw)
            vals_arr_ty = ir.ArrayType(_CSTR, n_kw)
            names_arr = self._alloca_in_entry(
                names_arr_ty, name=self._fresh("replace.kwn"),
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty, name=self._fresh("replace.kwv"),
            )
            for i, (kw_name, kw_expr) in enumerate(expr.kwargs):
                name_gv = self._cstr_global(
                    kw_name, f".replace.kwname.{i}.{kw_name}",
                )
                ngep = self.builder.gep(
                    names_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"replace.kwn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)

                raw_v = self._emit_expr(kw_expr)
                val_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    raw_v, kw_expr.ty,
                )
                vgep = self.builder.gep(
                    vals_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"replace.kwv.{i}"),
                )
                self.builder.store(val_obj, vgep)
            names_ptr = self.builder.bitcast(
                names_arr, _CSTR, name=self._fresh("replace.kwn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr, _CSTR, name=self._fresh("replace.kwv.p"),
            )

        return self.builder.call(
            self.runtime["py_dataclass_replace"],
            [obj_val, ir.Constant(_I64, n_kw), names_ptr, vals_ptr],
            name=self._fresh("dataclasses.replace"),
        )

    # Method names recognised by `_emit_native_os_path_call` —
    # nested calls of the form ``os.path.X(...)`` return native
    # PyObject* and are therefore safe to feed back into another
    # native os.path dispatch (no CPython contagion).
    _NATIVE_OS_PATH_DISPATCH_METHODS = frozenset({
        "join", "basename", "dirname", "exists",
        "isfile", "isdir", "getmtime", "abspath",
    })

    def _is_native_os_path_call_shape(self, expr: Expr) -> bool:
        """Recognise ``os.path.X(...)`` shapes that the native
        dispatch will lower — used by the arg-stays-native check to
        accept dispatched-call results without forcing the outer
        call back through the CPython path."""
        if not isinstance(expr, Call):
            return False
        func = expr.func
        if not isinstance(func, Attr):
            return False
        if self._native_builtin_value_kind_for_expr(func.obj) != "os.path":
            return False
        return func.name in self._NATIVE_OS_PATH_DISPATCH_METHODS

    def _native_os_path_arg_can_stay_native(self, expr: Expr) -> bool:
        if self._is_starred_unpack_expr(expr):
            return False
        if isinstance(expr, Name):
            ident = expr.ident
            if getattr(self, "_cpy_module_flags", {}).get(ident, False):
                return False
            if ident in getattr(self, "_cpy_module_env", {}):
                return False
            return True
        if isinstance(expr, Call):
            if self._is_native_os_path_call_shape(expr):
                return True
            func = expr.func
            return (
                isinstance(func, Attr)
                and func.name == "getcwd"
                and not expr.args
                and not expr.kwargs
                and isinstance(func.obj, Name)
                and self._native_builtin_module_for_name(func.obj.ident)
                == "os"
            )
        if isinstance(expr, Attr):
            return (
                expr.name == "executable"
                and isinstance(expr.obj, Name)
                and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
            )
        if isinstance(expr, Subscript):
            return False
        return True

    def _emit_os_path_arg_object(self, expr: Expr) -> ir.Value:
        value = self._emit_expr(expr)
        if value in getattr(self, "_cpy_values", ()):
            return self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"], [value],
                name=self._fresh("cpy.path.arg"),
            )
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, expr.ty,
        )

    def _emit_native_os_path_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        if self._native_builtin_value_kind_for_expr(attr.obj) != "os.path":
            return None
        name = attr.name
        if name == "join":
            if len(expr.args) == 0:
                return None
            # Each arg is either positional (must be a known-native
            # value) or a splat ``*xs`` (xs must be a known-native
            # list/tuple of strings — e.g. another local).
            for arg in expr.args:
                if self._is_starred_unpack_expr(arg):
                    inner = arg.args[0]
                    inner_ty = inner.ty
                    inner_native = (
                        self._native_os_path_arg_can_stay_native(inner)
                        and isinstance(inner_ty, (ListType, TupleType))
                    )
                    if not inner_native:
                        return None
                else:
                    if not self._native_os_path_arg_can_stay_native(arg):
                        return None
            lst = self.builder.call(
                self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("os.path.join.args"),
            )
            for arg in expr.args:
                if self._is_starred_unpack_expr(arg):
                    inner_val = self._emit_as_object(arg.args[0])
                    self.builder.call(
                        self.runtime["py_list_extend"], [lst, inner_val],
                    )
                else:
                    self.builder.call(
                        self.runtime["py_list_append"],
                        [lst, self._emit_os_path_arg_object(arg)],
                    )
            return self.builder.call(
                self.runtime["py_os_path_join"], [lst],
                name=self._fresh("os.path.join"),
            )
        if name == "basename" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_basename"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.basename"),
            )
        if name == "dirname" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_dirname"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.dirname"),
            )
        if name == "exists" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            i32v = self.builder.call(
                self.runtime["py_os_path_exists"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.exists"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh("os.path.exists.i1"),
            )
        if name in ("isfile", "isdir") and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            helper = (
                "py_os_path_isfile" if name == "isfile"
                else "py_os_path_isdir"
            )
            i32v = self.builder.call(
                self.runtime[helper],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh(f"os.path.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"os.path.{name}.i1"),
            )
        if name == "getmtime" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_getmtime"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.getmtime"),
            )
        if name == "abspath" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_abspath"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.abspath"),
            )
        return None

    def _declare_strlen(self) -> ir.Function:
        fn = self.module.globals.get("strlen")
        if isinstance(fn, ir.Function):
            return fn
        fnty = ir.FunctionType(_I64, [_CSTR], var_arg=False)
        fn = ir.Function(self.module, fnty, name="strlen")
        fn.linkage = "external"
        return fn

    def _emit_program_argv_list(self) -> ir.Value:
        argc = self.builder.call(
            self.runtime["py_program_argc"], [],
            name=self._fresh("argv.argc"),
        )
        lst = self.builder.call(
            self.runtime["py_list_new"], [argc],
            name=self._fresh("argv.list"),
        )
        idx_slot = self._alloca_in_entry(_I64, name=self._fresh("argv.i"))
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        fn = self.builder._block.function
        cond_bb = fn.append_basic_block(name=self._fresh("argv.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("argv.body"))
        step_bb = fn.append_basic_block(name=self._fresh("argv.step"))
        end_bb = fn.append_basic_block(name=self._fresh("argv.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("argv.cur"))
        more = self.builder.icmp_signed(
            "<", cur, argc, name=self._fresh("argv.more"),
        )
        self.builder.cbranch(more, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        raw = self.builder.call(
            self.runtime["py_program_argv"], [cur],
            name=self._fresh("argv.raw"),
        )
        n_bytes = self.builder.call(
            self._declare_strlen(), [raw],
            name=self._fresh("argv.len"),
        )
        item = self.builder.call(
            self.runtime["py_str_new"], [raw, n_bytes],
            name=self._fresh("argv.str"),
        )
        self.builder.call(
            self.runtime["py_list_append"], [lst, item],
        )
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("argv.cur2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("argv.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return lst

    def _emit_import(self, stmt: Import) -> None:
        """Lower ``import a`` / ``import a.b`` / ``import a.b as c`` via
        py_cpy_import. For dotted names without an alias, we import the
        full path (to ensure submodules are loaded) but bind the
        top-level package under its short name, matching CPython's
        ``import a.b`` semantics (access via ``a.b``). With an
        ``as`` alias we bind the leaf module to that alias."""
        # Compile-time-only modules drop out entirely.
        stmt_names = [
            (m, a) for (m, a) in stmt.names
            if m.split(".")[0] not in self._COMPILE_TIME_ONLY_MODULES
        ]
        if not stmt_names:
            return
        # Issue 11.B.1.2: when recursive_stdlib pulled the imported
        # module into the multi-file native compile set, skip the
        # py_cpy_import call and register as a native module alias so
        # subsequent ``module.X`` access resolves to ``user_<mod>_<X>``.
        native_table = getattr(self, "_native_module_exports", None)
        for mod_name, as_name in stmt_names:
            if mod_name in (
                "sys", "os", "platform", "subprocess", "tempfile", "shutil",
                "shlex", "sysconfig",
            ):
                self._register_native_builtin_module_alias(
                    as_name or mod_name, mod_name,
                )
                continue
            if (
                native_table is not None
                and mod_name in native_table
            ):
                # Native sibling: register the alias and skip the
                # CPython import call. ``module.X`` access goes
                # through ``_native_module_alias_export_info``.
                local_name = as_name or mod_name.split(".")[0]
                self._register_native_module_alias(local_name, mod_name)
                continue
            self._ensure_cpy_init()
            cpy_modules = self._cpy_modules()
            # Always import the full dotted path so side-effect
            # submodule registration runs.
            full_ptr = self._ptr_to_cstr(
                self._cstr_global(mod_name, f".cpy.mod.{mod_name}")
            )
            leaf_val = self.builder.call(
                self.runtime["py_cpy_import"], [full_ptr],
                name=self._fresh(f"cpy.import.{mod_name.replace('.', '_')}"),
            )
            if as_name is None and "." in mod_name:
                # ``import urllib.parse`` — bind urllib (the top-level),
                # not the leaf, so ``urllib.parse.quote`` works.
                top_name = mod_name.split(".")[0]
                top_ptr = self._ptr_to_cstr(
                    self._cstr_global(top_name, f".cpy.mod.{top_name}")
                )
                top_val = self.builder.call(
                    self.runtime["py_cpy_import"], [top_ptr],
                    name=self._fresh(f"cpy.import.{top_name}"),
                )
                gv = self._cpy_module_global(top_name)
                self.builder.store(top_val, gv)
                cpy_modules[top_name] = gv
                # Leaf reference is no longer needed — release.
                self.builder.call(self.runtime["py_cpy_decref"], [leaf_val])
            else:
                local_name = as_name or mod_name
                gv = self._cpy_module_global(local_name)
                self.builder.store(leaf_val, gv)
                cpy_modules[local_name] = gv

    def _predeclare_native_cross_module(
        self, stmt: ImportFrom, src_module: str, exports: dict,
    ) -> None:
        """First-pass declaration for native cross-module imports:
        declare the extern function globals and bind them in
        ``self.functions`` so user-function bodies lowered in the
        same compilation unit can resolve the call. Class imports
        declare the class global + method externs via
        ``class_lowering.declare_extern_class``."""
        sanitised = src_module.replace(".", "_").replace("-", "_")
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                full_submodule = self._native_import_from_submodule(
                    src_module, attr_name,
                )
                if full_submodule is not None:
                    self._register_native_module_alias(
                        local_name, full_submodule,
                    )
                    continue
                # Not a native export — pre-seed a CPython-module
                # global so the Name lookup inside function bodies
                # resolves. The main-body walker will emit the actual
                # import via _import_from_cpython_single.
                self._cpy_module_global(local_name)
                self._cpy_modules()[local_name] = (
                    self._cpy_module_global(local_name)
                )
                continue
            if info["kind"] == "function":
                sym = f"user_{sanitised}_{attr_name}"
                existing = self.module.globals.get(sym)
                if isinstance(existing, ir.Function):
                    fn = existing
                else:
                    box_int_abi = self._export_box_int_abi(info)
                    param_tys = [
                        self._abi_ir_type(
                            decode_type(t), box_int_abi=box_int_abi,
                        )
                        for t in info["param_types"]
                    ]
                    ret_ty = decode_type(info["return_ty"]) or DynType(
                        name="dyn"
                    )
                    fnty = ir.FunctionType(
                        self._abi_ir_type(ret_ty, box_int_abi=box_int_abi),
                        param_tys,
                    )
                    fn = ir.Function(self.module, fnty, name=sym)
                    fn.linkage = "external"
                self.functions[local_name] = fn
                if not hasattr(self, "_cross_module_func_defs"):
                    self._cross_module_func_defs = {}
                self._cross_module_func_defs[local_name] = (
                    self._extern_info_to_funcdef(local_name, info)
                )
                continue
            if info["kind"] == "class":
                self.class_lowering.declare_extern_class(
                    owning_module=src_module,
                    class_name=info["class_name"],
                    field_names=info["field_names"],
                    methods=info["methods"],
                    local_name=local_name,
                )
                continue
            # Other kinds — fall through to CPython shim.

    def _native_import_from_submodule(
        self, src_module: str, attr_name: str,
    ) -> Optional[str]:
        """Return ``pkg.attr`` when ``from pkg import attr`` names a
        native sibling submodule compiled in the same invocation."""
        native_table = getattr(self, "_native_module_exports", None)
        if native_table is None or not src_module or attr_name == "*":
            return None
        full_name = f"{src_module}.{attr_name}"
        if full_name in native_table:
            return full_name
        return None

    def _has_native_import_from_targets(
        self, stmt: ImportFrom, src_module: str,
    ) -> bool:
        """Whether ``from src import ...`` touches any native sibling
        export or native sibling submodule."""
        native_table = getattr(self, "_native_module_exports", None)
        if native_table is None:
            return False
        if src_module in native_table:
            return True
        for attr_name, _ in stmt.names:
            if self._native_import_from_submodule(src_module, attr_name):
                return True
        return False

    def _bind_native_cross_module_imports(
        self, stmt: ImportFrom, src_module: str, exports: dict,
    ) -> None:
        """For each name imported from a native sibling module,
        declare an extern function of matching signature and register
        in ``self.functions`` so subsequent calls resolve to it."""
        sanitised = src_module.replace(".", "_").replace("-", "_")
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                full_submodule = self._native_import_from_submodule(
                    src_module, attr_name,
                )
                if full_submodule is not None:
                    self._register_native_module_alias(
                        local_name, full_submodule,
                    )
                    continue
                # Name isn't a top-level FuncDef/ClassDef export of
                # the native sibling — could be a module-alias
                # (``from . import foo as f``), a top-level constant,
                # or something the pre-pass doesn't model yet. Route
                # through CPython import so the binding still exists.
                self._import_from_cpython_single(
                    stmt, src_module, attr_name, as_name,
                )
                continue
            kind = info["kind"]
            if kind == "function":
                sym = f"user_{sanitised}_{attr_name}"
                existing = self.module.globals.get(sym)
                if isinstance(existing, ir.Function):
                    fn = existing
                else:
                    box_int_abi = self._export_box_int_abi(info)
                    param_tys = [
                        self._abi_ir_type(
                            decode_type(t), box_int_abi=box_int_abi,
                        )
                        for t in info["param_types"]
                    ]
                    ret_ty = decode_type(info["return_ty"]) or DynType(
                        name="dyn"
                    )
                    fnty = ir.FunctionType(
                        self._abi_ir_type(ret_ty, box_int_abi=box_int_abi),
                        param_tys,
                    )
                    fn = ir.Function(self.module, fnty, name=sym)
                    fn.linkage = "external"
                self.functions[local_name] = fn
                # Record the original FuncDef-like signature so the
                # call-site kwargs resolver can map keyword → position.
                if not hasattr(self, "_cross_module_func_defs"):
                    self._cross_module_func_defs: dict = {}
                self._cross_module_func_defs[local_name] = (
                    self._extern_info_to_funcdef(local_name, info)
                )
                continue
            if kind == "class":
                # Function-scope ``from .mod import Class`` does not go
                # through the module-level predeclare pass, so make the
                # extern class registration here as well. Idempotent for
                # top-level imports that were already predeclared.
                self.class_lowering.declare_extern_class(
                    owning_module=src_module,
                    class_name=info["class_name"],
                    field_names=info["field_names"],
                    methods=info["methods"],
                    local_name=local_name,
                )
                continue
            # Other kinds (constants) fall through to CPython so the
            # program still links.
            self._import_from_cpython_single(
                stmt, src_module, attr_name, as_name,
            )

    def _register_native_module_alias(
        self, local_name: str, module_name: str,
    ) -> None:
        """Track ``from .pkg import submod as name`` bindings for the
        limited native-submodule path.

        This is intentionally narrow: it only exists so later
        ``name.fn(...)`` calls can resolve directly against a sibling
        submodule's exported functions without routing through
        ``py_cpy_import``."""
        if not hasattr(self, "_native_module_aliases"):
            self._native_module_aliases = {}
        self._native_module_aliases[local_name] = module_name

    def _native_module_alias_export_info(
        self, alias_name: str, attr_name: str,
    ) -> Optional[tuple[str, dict]]:
        """Return ``(module_name, export_info)`` for ``alias.attr`` when
        ``alias`` names a native sibling submodule."""
        module_name = getattr(self, "_native_module_aliases", {}).get(alias_name)
        if module_name is None:
            return None
        native_table = getattr(self, "_native_module_exports", None)
        if native_table is None:
            return None
        info = native_table.get(module_name, {}).get(attr_name)
        if info is None:
            return None
        return module_name, info

    def _ensure_native_module_alias_class_export(
        self, alias_name: str, attr_name: str,
    ):
        """Declare ``alias.Class`` as an extern class on demand.

        Keep the local registration keyed by the leaf class name so
        existing ``isinstance(..., mod.Class)`` logic, which only looks
        at the tail token, can stay unchanged. If the current module
        already owns a different class with that same leaf name, fall
        back to the CPython path instead of silently rebinding it.
        """
        export = self._native_module_alias_export_info(alias_name, attr_name)
        if export is None:
            return None
        module_name, info = export
        if info.get("kind") != "class":
            return None
        class_name = attr_name
        expected_global = (
            ".class."
            + module_name.replace(".", "_").replace("-", "_")
            + "."
            + class_name
        )
        existing = self.class_lowering.classes.get(attr_name)
        if (
            existing is not None
            and existing.global_var.name != expected_global
        ):
            return None
        return self.class_lowering.declare_extern_class(
            owning_module=module_name,
            class_name=class_name,
            field_names=info["field_names"],
            methods=info["methods"],
            local_name=attr_name,
        )

    def _emit_no_init_field_instance(
        self, class_name: str, args: tuple, kwargs: tuple,
    ) -> ir.Value | None:
        info = self.class_lowering.classes.get(class_name)
        if info is None:
            return None
        field_names = tuple(getattr(info, "field_names", ()))
        if not field_names:
            return None
        if len(args) > len(field_names):
            raise NotImplementedError(
                f"class {class_name!r} has no __init__ for extra "
                "positional arguments"
            )

        inst = self.class_lowering.emit_instantiate(class_name, (), self)
        seen = set()

        for i, arg_expr in enumerate(args):
            field_name = field_names[i]
            seen.add(field_name)
            raw_v = self._emit_expr(arg_expr)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, raw_v, arg_expr.ty,
            )
            self.builder.call(
                self.runtime["py_obj_setattr"],
                [inst, self._attr_name_ptr(field_name), v_obj],
            )

        for kw_name, kw_expr in kwargs:
            if kw_name in seen:
                raise NotImplementedError(
                    f"class {class_name!r} got multiple values for "
                    f"field {kw_name!r}"
                )
            if kw_name not in field_names:
                raise NotImplementedError(
                    f"class {class_name!r} has no field {kw_name!r}"
                )
            seen.add(kw_name)
            raw_v = self._emit_expr(kw_expr)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, raw_v, kw_expr.ty,
            )
            self.builder.call(
                self.runtime["py_obj_setattr"],
                [inst, self._attr_name_ptr(kw_name), v_obj],
            )
        return inst

    def _declare_extern_user_function(
        self,
        owning_module: str,
        func_name: str,
        info: dict,
        *,
        bind_name: Optional[str] = None,
    ) -> ir.Function:
        """Declare (or reuse) the extern symbol for a native sibling
        function export, optionally binding it under ``bind_name``."""
        sanitised = owning_module.replace(".", "_").replace("-", "_")
        sym = f"user_{sanitised}_{func_name}"
        existing = self.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            box_int_abi = self._export_box_int_abi(info)
            param_tys = [
                self._abi_ir_type(
                    decode_type(t), box_int_abi=box_int_abi,
                )
                for t in info["param_types"]
            ]
            ret_ty = decode_type(info["return_ty"]) or DynType(
                name="dyn"
            )
            fnty = ir.FunctionType(
                self._abi_ir_type(ret_ty, box_int_abi=box_int_abi),
                param_tys,
            )
            fn = ir.Function(self.module, fnty, name=sym)
            fn.linkage = "external"
        if bind_name is not None:
            self.functions[bind_name] = fn
            if not hasattr(self, "_cross_module_func_defs"):
                self._cross_module_func_defs = {}
            self._cross_module_func_defs[bind_name] = (
                self._extern_info_to_funcdef(bind_name, info)
            )
        return fn

    def _maybe_emit_native_module_alias_call(self, expr: Call) -> Optional[ir.Value]:
        """Lower ``submod.fn(...)`` when ``submod`` was imported from a
        native sibling module and ``fn`` is one of that submodule's
        exported top-level functions."""
        attr = expr.func
        if not isinstance(attr, Attr) or not isinstance(attr.obj, Name):
            return None
        export = self._native_module_alias_export_info(
            attr.obj.ident, attr.name,
        )
        if export is None:
            return None
        module_name, info = export
        kind = info.get("kind")
        if kind == "function":
            fn = self._declare_extern_user_function(
                module_name, attr.name, info,
            )
            ast_func_def = self._extern_info_to_funcdef(attr.name, info)
            if ast_func_def is None:
                return None
            if self._call_would_use_callee_defaults(
                expr.args, expr.kwargs, ast_func_def.args,
            ):
                # Cross-module default expressions live in the callee's
                # module scope. The direct-native extern path cannot safely
                # inline them in the caller, so fall back to the existing
                # CPython-backed module binding for these calls.
                return None
            return self._emit_direct_user_function_call(
                display_name=attr.name,
                fn=fn,
                ast_func_def=ast_func_def,
                args=expr.args,
                kwargs=expr.kwargs,
            )
        if kind != "class":
            return None
        class_info = self._ensure_native_module_alias_class_export(
            attr.obj.ident, attr.name,
        )
        if class_info is None:
            return None
        init_fd = self.class_lowering._find_method_def(attr.name, "__init__")
        resolved_args = expr.args
        if init_fd is None:
            inst = self._emit_no_init_field_instance(
                attr.name, expr.args, expr.kwargs,
            )
            if inst is not None:
                return inst
            if expr.kwargs:
                raise NotImplementedError(
                    f"class {attr.name!r} with kwargs needs __init__ "
                    "to resolve parameter names"
                )
        elif expr.kwargs:
            resolved_args = tuple(self._resolve_call_kwargs(
                expr.args, expr.kwargs, init_fd.args, skip_self=True,
            ))
        return self.class_lowering.emit_instantiate(
            attr.name, resolved_args, self,
        )

    def _import_cpython_module_single(
        self, module_name: str, local_name: str,
    ) -> None:
        """Bind ``local_name`` to a CPython module object."""
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(module_name, f".cpy.mod.{module_name}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(
                f"cpy.import.{module_name.replace('.', '_')}"
            ),
        )
        gv = self._cpy_module_global(local_name)
        self.builder.store(mod_val, gv)
        cpy_modules[local_name] = gv

    def _import_from_cpython_single(
        self, stmt: ImportFrom, src_module: str,
        attr_name: str, as_name,
    ) -> None:
        """Route a single ``from X import Y`` entry through the
        existing CPython-import machinery — used when the multi-file
        path can't model the exported symbol (class / constant for
        now)."""
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(src_module, f".cpy.mod.{src_module}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(f"cpy.fromimport.{src_module}"),
        )
        local_name = as_name or attr_name
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
        )
        val = self.builder.call(
            self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
            name=self._fresh(f"cpy.from.{local_name}"),
        )
        gv = self._cpy_module_global(local_name)
        self.builder.store(val, gv)
        cpy_modules[local_name] = gv

    def _resolve_relative_import(self, stmt: ImportFrom) -> str:
        """Turn a relative ``from .lib import X`` into its absolute
        dotted module name using ``self.ast_module.name`` as the
        current package context. Non-relative imports are returned
        unchanged."""
        level = stmt.level or 0
        if level == 0:
            return stmt.module or ""
        cur = self.ast_module.name or ""
        parts = cur.split(".") if cur else []
        cur_file = stmt.span.file or ""
        cur_file = cur_file.replace("\\", "/")
        is_package_init = (
            cur_file == "__init__.py"
            or cur_file.endswith("/__init__.py")
        )
        package_parts = parts if is_package_init else parts[:-1]
        # Relative imports count from the current package:
        # ``from .x`` means "stay in this package", ``from ..x`` pops
        # one package level, etc.
        up = level - 1
        if up > len(package_parts):
            # Over-dotted relative import; fall back to the raw name.
            return stmt.module or ""
        base_parts = package_parts[: len(package_parts) - up]
        if stmt.module:
            return ".".join(base_parts + [stmt.module])
        return ".".join(base_parts)

    def _path_dirname(self, path: str) -> str:
        path = (path or "").replace("\\", "/")
        if not path:
            return ""
        i = len(path) - 1
        while i >= 0 and path[i] == "/":
            i -= 1
        while i >= 0 and path[i] != "/":
            i -= 1
        if i < 0:
            return ""
        if i == 0:
            return "/"
        return path[:i]

    def _path_basename(self, path: str) -> str:
        path = (path or "").replace("\\", "/")
        i = len(path) - 1
        while i >= 0 and path[i] == "/":
            i -= 1
        if i < 0:
            return ""
        end = i + 1
        while i >= 0 and path[i] != "/":
            i -= 1
        return path[i + 1 : end]

    def _module_root_from_src_path(self, src_path: str, module_name: str) -> str:
        cur_dir = self._path_dirname(src_path)
        parts = module_name.split(".")
        up = len(parts) if self._path_basename(src_path) == "__init__.py" else max(
            0, len(parts) - 1,
        )
        i = 0
        while i < up and cur_dir:
            parent = self._path_dirname(cur_dir)
            if parent == cur_dir:
                break
            cur_dir = parent
            i += 1
        return cur_dir

    def _module_src_exists_under_root(self, root_dir: str, dotted_name: str) -> bool:
        if not root_dir or not dotted_name:
            return False
        rel = dotted_name.replace(".", "/")
        py_path = root_dir + "/" + rel + ".py"
        init_path = root_dir + "/" + rel + "/__init__.py"
        return os.path.isfile(py_path) or os.path.isfile(init_path)

    def _resolve_relative_import_submodule(
        self, stmt: ImportFrom, import_module: str, attr_name: str,
    ) -> Optional[str]:
        """Return ``pkg.mod.attr`` when a relative ``from`` import name
        refers to a real sibling submodule on disk.

        This keeps CPython fallback semantics correct for shapes such as
        ``from . import parser`` / ``from .codegen import layer1`` when
        the surrounding package was not compiled natively into the same
        multi-file closure."""
        if not stmt.level or not import_module or attr_name == "*":
            return None
        src_file = stmt.span.file or ""
        cur_mod = self.ast_module.name or ""
        if not src_file or not cur_mod:
            return None
        root_dir = self._module_root_from_src_path(src_file, cur_mod)
        full_name = import_module + "." + attr_name
        if self._module_src_exists_under_root(root_dir, full_name):
            return full_name
        return None

    def _emit_import_from(self, stmt: ImportFrom) -> None:
        """Lower ``from a import b`` via py_cpy_import + py_cpy_getattr,
        UNLESS ``a`` is one of the pcc compile-time scaffold modules
        (``pcc.extern`` / ``pcc.llvm_capi`` / ``pcc.unsafe``) — in that
        case the names are compile-time markers and we register each
        one in a per-module registry without emitting any runtime IR."""
        if self._is_extern_scaffold_import_module(stmt.module):
            self._register_extern_scaffold_imports(stmt)
            return
        if stmt.module in self._UNSAFE_SCAFFOLD_MODULES:
            self._register_unsafe_scaffold_imports(stmt)
            return
        if (
            stmt.module is not None
            and stmt.module.split(".")[0] in self._COMPILE_TIME_ONLY_MODULES
        ):
            # Consumed at parse / type-inference time; no runtime IR.
            return
        stmt_names = self._filter_runtime_import_from_names(stmt)
        if not stmt_names:
            return

        # Multi-file compile: if the source module is a sibling being
        # compiled in the same invocation, declare each imported name
        # as an external function (for now only functions — classes and
        # constants are follow-ups) and register in the user-function
        # table so direct calls emit ``call @user_<mod>_<fn>``.
        native_table = getattr(self, "_native_module_exports", None)
        import_module = stmt.module or ""
        if native_table is not None:
            import_module = self._resolve_relative_import(stmt)
            remaining_names: list[tuple[str, Optional[str]]] = []
            for attr_name, as_name in stmt_names:
                full_submodule = self._native_import_from_submodule(
                    import_module, attr_name,
                )
                if full_submodule is None:
                    full_submodule = self._resolve_relative_import_submodule(
                        stmt, import_module, attr_name,
                    )
                if full_submodule is not None and full_submodule in native_table:
                    self._register_native_module_alias(
                        as_name or attr_name, full_submodule,
                    )
                    continue
                remaining_names.append((attr_name, as_name))
            if not remaining_names:
                return
            stmt_names = remaining_names
            if self._has_native_import_from_targets(
                stmt, import_module,
            ):
                self._bind_native_cross_module_imports(
                    stmt, import_module,
                    native_table.get(import_module, {}),
                )
                return
        elif getattr(stmt, "level", 0):
            import_module = self._resolve_relative_import(stmt)
        if self._register_native_builtin_import_from_aliases(
            stmt, import_module,
        ):
            return
        self._ensure_cpy_init()
        cpy_modules = self._cpy_modules()
        mod_ptr = self._ptr_to_cstr(
            self._cstr_global(import_module, f".cpy.mod.{import_module}")
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"], [mod_ptr],
            name=self._fresh(f"cpy.fromimport.{import_module}"),
        )
        for attr_name, as_name in stmt_names:
            if attr_name == "*":
                gv = self._cpy_star_module_global(import_module)
                self.builder.store(mod_val, gv)
                continue
            local_name = as_name or attr_name
            submodule_name = self._resolve_relative_import_submodule(
                stmt, import_module, attr_name,
            )
            if submodule_name is not None:
                sub_ptr = self._ptr_to_cstr(
                    self._cstr_global(
                        submodule_name, f".cpy.mod.{submodule_name}",
                    )
                )
                val = self.builder.call(
                    self.runtime["py_cpy_import"], [sub_ptr],
                    name=self._fresh(
                        f"cpy.fromimport.{submodule_name.replace('.', '_')}"
                    ),
                )
            else:
                attr_ptr = self._ptr_to_cstr(
                    self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
                )
                val = self.builder.call(
                    self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
                    name=self._fresh(f"cpy.from.{local_name}"),
                )
            gv = self._cpy_module_global(local_name)
            self.builder.store(val, gv)
            cpy_modules[local_name] = gv

    _EXTERN_CTYPE_IR = {
        "c_void": _VOID,
        "c_bool": _I1,
        "c_int8": ir.IntType(8),
        "c_int16": ir.IntType(16),
        "c_int32": _I32,
        "c_int": _I32,
        "c_int64": _I64,
        "c_long": _I64,
        "c_uint8": ir.IntType(8),
        "c_uint16": ir.IntType(16),
        "c_uint32": _I32,
        "c_uint64": _I64,
        "c_size_t": _I64,
        "c_float": ir.FloatType(),
        "c_double": _DOUBLE,
        "c_ptr": _CSTR,  # opaque i8*
        "c_str": _CSTR,
    }

    def _register_extern_scaffold_imports(self, stmt: "ImportFrom") -> None:
        """Track ``from pcc.extern import extern, c_int, ...`` bindings
        so the Name-based check in :meth:`_maybe_register_extern_assign`
        can recognize the ``extern`` factory call.
        """
        if not hasattr(self, "_extern_bindings"):
            self._extern_bindings: dict[str, str] = {}
        for attr_name, as_name in stmt.names:
            local = as_name or attr_name
            self._extern_bindings[local] = attr_name

    _UNSAFE_INTRINSICS = frozenset({
        "malloc",
        "cstr",
        "global_addr",
        "global_load_ptr",
        "global_store_ptr",
        "define_global_i8",
        "define_global_i32",
        "define_global_header",
        "define_global_ptr_null",
        "define_global_ptr_to_global",
        "define_global_cstr",
        "define_global_ptr_array",
        "define_global_null_ptr_array",
        "define_global_i32_array",
        "calloc",
        "realloc",
        "free",
        "ptr_add",
        "null",
        "ptr_eq",
        "ptr_is_null",
        "is_tagged_int",
        "tag_int",
        "untag_int",
        "load_i64",
        "load_i32",
        "load_i8",
        "load_ptr",
        "load_f64",
        "store_i64",
        "store_i32",
        "store_i8",
        "store_ptr",
        "store_f64",
        "memset",
        "memcpy",
        "memmove",
        "write",
        "strlen",
        "getenv",
        "setenv",
        "unsetenv",
        "access",
    })

    def _register_unsafe_scaffold_imports(self, stmt: "ImportFrom") -> None:
        """Track ``from pcc.unsafe import load_i64, ...`` bindings.

        These names are compiler intrinsics; no runtime import or CPython
        module object should be emitted for them.
        """
        if not hasattr(self, "_unsafe_bindings"):
            self._unsafe_bindings: dict[str, str] = {}
        for attr_name, as_name in stmt.names:
            if attr_name == "*":
                continue
            if attr_name not in self._UNSAFE_INTRINSICS:
                raise NotImplementedError(
                    f"unknown pcc.unsafe intrinsic {attr_name!r}"
                )
            local = as_name or attr_name
            self._unsafe_bindings[local] = attr_name

    def _unsafe_intrinsic_for_name(self, name: str) -> Optional[str]:
        if name in self.env:
            return None
        if name in getattr(self, "_module_globals", {}):
            return None
        return getattr(self, "_unsafe_bindings", {}).get(name)

    def _unsafe_expect_arity(
        self, intrinsic: str, expr: Call, n_args: int,
    ) -> None:
        if expr.kwargs or len(expr.args) != n_args:
            raise NotImplementedError(
                f"pcc.unsafe.{intrinsic} expects {n_args} positional args"
            )

    def _unsafe_ptr_arg(self, expr: Expr) -> ir.Value:
        ptr = self._emit_expr(expr)
        if isinstance(ptr.type, ir.PointerType):
            if self._ir_type_matches(ptr.type, _CSTR):
                return ptr
            return self.builder.bitcast(
                ptr, _CSTR, name=self._fresh("unsafe.ptr.cast"),
            )
        raise NotImplementedError(
            "pcc.unsafe pointer argument must be a pointer-typed value"
        )

    def _ir_type_matches(self, actual: ir.Type, expected: ir.Type) -> bool:
        if actual is expected:
            return True
        if isinstance(actual, ir.PointerType) and isinstance(expected, ir.PointerType):
            return actual.pointee is expected.pointee
        return False

    def _unsafe_i64_arg(self, expr: Expr) -> ir.Value:
        return self._to_int64(self._emit_expr(expr), expr.ty)

    def _unsafe_i32_arg(self, expr: Expr) -> ir.Value:
        return self.builder.trunc(
            self._unsafe_i64_arg(expr), _I32,
            name=self._fresh("unsafe.i64.to.i32"),
        )

    def _unsafe_f64_arg(self, expr: Expr) -> ir.Value:
        return self._to_double(self._emit_expr(expr), expr.ty)

    def _unsafe_cstr_literal_arg(self, expr: Expr) -> ir.Value:
        if not isinstance(expr, StrLit):
            raise NotImplementedError(
                "pcc.unsafe.cstr only accepts a string literal"
            )
        gv = self._cstr_global(
            expr.value, self._fresh(".unsafe.cstr"),
        )
        return self._ptr_to_cstr(gv)

    def _unsafe_symbol_literal_arg(self, expr: Expr) -> str:
        if not isinstance(expr, StrLit):
            raise NotImplementedError(
                "pcc.unsafe global intrinsics require a string literal symbol"
            )
        symbol = expr.value
        if not symbol:
            raise NotImplementedError("empty external symbol name")

        def is_alpha_code(c: int) -> bool:
            return (97 <= c <= 122) or (65 <= c <= 90)

        def is_alnum_code(c: int) -> bool:
            return is_alpha_code(c) or (48 <= c <= 57)

        first = ord(symbol[0])
        if not (first == 95 or is_alpha_code(first)):
            raise NotImplementedError(f"invalid external symbol {symbol!r}")
        for ch in symbol[1:]:
            c = ord(ch)
            if not (c == 95 or is_alnum_code(c)):
                raise NotImplementedError(f"invalid external symbol {symbol!r}")
        return symbol

    def _unsafe_const_i64_arg(self, expr: Expr) -> int:
        if isinstance(expr, IntLit):
            return int(expr.value)
        if (
            isinstance(expr, UnaryOp)
            and expr.op == "-"
            and isinstance(expr.operand, IntLit)
        ):
            return -int(expr.operand.value)
        raise NotImplementedError(
            "pcc.unsafe global definition intrinsics require integer literals"
        )

    def _define_global(
        self, name: str, value_ty: ir.Type, initializer: ir.Constant,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            gv = existing
            gv.linkage = ""
        elif existing is None:
            gv = ir.GlobalVariable(self.module, value_ty, name=name)
        else:
            raise L1CodegenError(f"{name!r} already declared as non-global")
        gv.initializer = initializer
        return gv

    def _get_or_declare_global_for_initializer(
        self, name: str,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        if existing is not None:
            raise L1CodegenError(f"{name!r} already declared as non-global")
        gv = ir.GlobalVariable(self.module, _I8, name=name)
        gv.linkage = "external"
        return gv

    def _define_unsafe_global_intrinsic(
        self, intrinsic: str, expr: Call,
    ) -> bool:
        if intrinsic == "define_global_i8":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            value = self._unsafe_const_i64_arg(expr.args[1])
            self._define_global(
                name, _I8, ir.Constant(_I8, value & 0xFF),
            )
            return True
        if intrinsic == "define_global_i32":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            value = self._unsafe_const_i64_arg(expr.args[1])
            self._define_global(name, _I32, ir.Constant(_I32, value))
            return True
        if intrinsic == "define_global_header":
            self._unsafe_expect_arity(intrinsic, expr, 4)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            refcount = self._unsafe_const_i64_arg(expr.args[1])
            type_tag = self._unsafe_const_i64_arg(expr.args[2])
            flags = self._unsafe_const_i64_arg(expr.args[3])
            header_ty = ir.LiteralStructType([_I64, _I32, _I32])
            init = ir.Constant(header_ty, [
                ir.Constant(_I64, refcount),
                ir.Constant(_I32, type_tag),
                ir.Constant(_I32, flags),
            ])
            self._define_global(name, header_ty, init)
            return True
        if intrinsic == "define_global_ptr_null":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            self._define_global(name, _CSTR, ir.Constant(_CSTR, None))
            return True
        if intrinsic == "define_global_ptr_to_global":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            target = self._unsafe_symbol_literal_arg(expr.args[1])
            target_gv = self._get_or_declare_global_for_initializer(target)
            self._define_global(name, _CSTR, ir.Constant(_CSTR, target_gv))
            return True
        if intrinsic == "define_global_cstr":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            if not isinstance(expr.args[1], StrLit):
                raise NotImplementedError(
                    "pcc.unsafe.define_global_cstr requires a string literal"
                )
            payload = self._utf8_byte_values(expr.args[1].value) + [0]
            arr_ty = ir.ArrayType(_I8, len(payload))
            init = ir.Constant(arr_ty, [ir.Constant(_I8, b) for b in payload])
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_ptr_array":
            if expr.kwargs or len(expr.args) < 1:
                raise NotImplementedError(
                    "pcc.unsafe.define_global_ptr_array expects a name"
                )
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            targets = [
                self._unsafe_symbol_literal_arg(arg)
                for arg in expr.args[1:]
            ]
            arr_ty = ir.ArrayType(_CSTR, len(targets))
            init = ir.Constant(arr_ty, [
                ir.Constant(
                    _CSTR, self._get_or_declare_global_for_initializer(t),
                )
                for t in targets
            ])
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_null_ptr_array":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            n_items = self._unsafe_const_i64_arg(expr.args[1])
            arr_ty = ir.ArrayType(_CSTR, n_items)
            init = ir.Constant(arr_ty, [
                ir.Constant(_CSTR, None) for _ in range(n_items)
            ])
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_i32_array":
            if expr.kwargs or len(expr.args) < 1:
                raise NotImplementedError(
                    "pcc.unsafe.define_global_i32_array expects a name"
                )
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            values = [
                self._unsafe_const_i64_arg(arg)
                for arg in expr.args[1:]
            ]
            arr_ty = ir.ArrayType(_I32, len(values))
            init = ir.Constant(arr_ty, [
                ir.Constant(_I32, v) for v in values
            ])
            self._define_global(name, arr_ty, init)
            return True
        return False

    def _maybe_define_unsafe_global_stmt(self, stmt: "ExprStmt") -> bool:
        expr = stmt.expr
        if not isinstance(expr, Call) or not isinstance(expr.func, Name):
            return False
        intrinsic = self._unsafe_intrinsic_for_name(expr.func.ident)
        if intrinsic is None:
            return False
        return self._define_unsafe_global_intrinsic(intrinsic, expr)

    def _declare_external_global(
        self, name: str, value_ty: ir.Type,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        if existing is not None:
            raise L1CodegenError(f"{name!r} already declared as non-global")
        gv = ir.GlobalVariable(self.module, value_ty, name=name)
        gv.linkage = "external"
        return gv

    def _unsafe_typed_addr(
        self, base: ir.Value, offset: ir.Value, pointee: ir.Type,
    ) -> ir.Value:
        byte_addr = self.builder.gep(
            base, [offset], name=self._fresh("unsafe.addr"),
        )
        ptr_ty = pointee.as_pointer()
        if self._ir_type_matches(byte_addr.type, ptr_ty):
            return byte_addr
        return self.builder.bitcast(
            byte_addr, ptr_ty, name=self._fresh("unsafe.addr.cast"),
        )

    def _emit_unsafe_intrinsic_call(
        self, intrinsic: str, expr: Call,
    ) -> ir.Value:
        if self._define_unsafe_global_intrinsic(intrinsic, expr):
            return self._emit_none_literal()
        if intrinsic == "cstr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            return self._unsafe_cstr_literal_arg(expr.args[0])
        if intrinsic == "global_addr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _I8)
            if self._ir_type_matches(gv.type, _CSTR):
                return gv
            return self.builder.bitcast(
                gv, _CSTR, name=self._fresh("unsafe.global.addr"),
            )
        if intrinsic == "global_load_ptr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _CSTR)
            return self.builder.load(
                gv, name=self._fresh("unsafe.global.load.ptr"),
            )
        if intrinsic == "global_store_ptr":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _CSTR)
            self.builder.store(self._unsafe_ptr_arg(expr.args[1]), gv)
            return self._emit_none_literal()
        if intrinsic == "malloc":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            malloc_fn = self._declare_external_function(
                "malloc", _CSTR, [_I64],
            )
            return self.builder.call(
                malloc_fn, [self._unsafe_i64_arg(expr.args[0])],
                name=self._fresh("unsafe.malloc"),
            )
        if intrinsic == "calloc":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            calloc_fn = self._declare_external_function(
                "calloc", _CSTR, [_I64, _I64],
            )
            return self.builder.call(
                calloc_fn,
                [
                    self._unsafe_i64_arg(expr.args[0]),
                    self._unsafe_i64_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.calloc"),
            )
        if intrinsic == "realloc":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            realloc_fn = self._declare_external_function(
                "realloc", _CSTR, [_CSTR, _I64],
            )
            return self.builder.call(
                realloc_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i64_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.realloc"),
            )
        if intrinsic == "free":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            free_fn = self._declare_external_function(
                "free", _VOID, [_CSTR],
            )
            self.builder.call(
                free_fn, [self._unsafe_ptr_arg(expr.args[0])],
            )
            return self._emit_none_literal()
        if intrinsic == "ptr_add":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            return self.builder.gep(
                self._unsafe_ptr_arg(expr.args[0]),
                [self._unsafe_i64_arg(expr.args[1])],
                name=self._fresh("unsafe.ptr.add"),
            )
        if intrinsic == "null":
            self._unsafe_expect_arity(intrinsic, expr, 0)
            return ir.Constant(_CSTR, None)
        if intrinsic in ("ptr_eq", "ptr_is_null", "is_tagged_int"):
            if intrinsic == "ptr_eq":
                self._unsafe_expect_arity(intrinsic, expr, 2)
                lhs_i = self.builder.ptrtoint(
                    self._unsafe_ptr_arg(expr.args[0]), _I64,
                    name=self._fresh("unsafe.ptr.eq.l"),
                )
                rhs_i = self.builder.ptrtoint(
                    self._unsafe_ptr_arg(expr.args[1]), _I64,
                    name=self._fresh("unsafe.ptr.eq.r"),
                )
                return self.builder.icmp_unsigned(
                    "==", lhs_i, rhs_i, name=self._fresh("unsafe.ptr.eq"),
                )
            self._unsafe_expect_arity(intrinsic, expr, 1)
            ptr_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[0]), _I64,
                name=self._fresh(f"unsafe.{intrinsic}.i"),
            )
            if intrinsic == "ptr_is_null":
                return self.builder.icmp_unsigned(
                    "==", ptr_i, ir.Constant(_I64, 0),
                    name=self._fresh("unsafe.ptr.null"),
                )
            tagged = self.builder.and_(
                ptr_i, ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.bit"),
            )
            return self.builder.icmp_unsigned(
                "==", tagged, ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tagged"),
            )
        if intrinsic == "tag_int":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            value = self._unsafe_i64_arg(expr.args[0])
            shifted = self.builder.shl(
                value, ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.int.shift"),
            )
            tagged = self.builder.or_(
                shifted, ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.int.bits"),
            )
            return self.builder.inttoptr(
                tagged, _CSTR, name=self._fresh("unsafe.tag.int.ptr"),
            )
        if intrinsic == "untag_int":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            ptr_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[0]), _I64,
                name=self._fresh("unsafe.untag.int.bits"),
            )
            return self.builder.ashr(
                ptr_i, ir.Constant(_I64, 1),
                name=self._fresh("unsafe.untag.int"),
            )
        if intrinsic in ("load_i64", "load_i32", "load_i8", "load_ptr", "load_f64"):
            self._unsafe_expect_arity(intrinsic, expr, 2)
            base = self._unsafe_ptr_arg(expr.args[0])
            offset = self._unsafe_i64_arg(expr.args[1])
            if intrinsic == "load_i64":
                addr = self._unsafe_typed_addr(base, offset, _I64)
                return self.builder.load(
                    addr, name=self._fresh("unsafe.load.i64"), align=1,
                )
            if intrinsic == "load_i32":
                addr = self._unsafe_typed_addr(base, offset, _I32)
                raw = self.builder.load(
                    addr, name=self._fresh("unsafe.load.i32"), align=1,
                )
                return self.builder.sext(
                    raw, _I64, name=self._fresh("unsafe.i32.to.i64"),
                )
            if intrinsic == "load_i8":
                addr = self._unsafe_typed_addr(base, offset, _I8)
                raw = self.builder.load(
                    addr, name=self._fresh("unsafe.load.i8"), align=1,
                )
                return self.builder.sext(
                    raw, _I64, name=self._fresh("unsafe.i8.to.i64"),
                )
            if intrinsic == "load_f64":
                addr = self._unsafe_typed_addr(base, offset, _DOUBLE)
                return self.builder.load(
                    addr, name=self._fresh("unsafe.load.f64"), align=1,
                )
            addr = self._unsafe_typed_addr(base, offset, _CSTR)
            return self.builder.load(
                addr, name=self._fresh("unsafe.load.ptr"), align=1,
            )
        if intrinsic in ("store_i64", "store_i32", "store_i8", "store_ptr", "store_f64"):
            self._unsafe_expect_arity(intrinsic, expr, 3)
            base = self._unsafe_ptr_arg(expr.args[0])
            offset = self._unsafe_i64_arg(expr.args[1])
            if intrinsic == "store_i64":
                addr = self._unsafe_typed_addr(base, offset, _I64)
                self.builder.store(
                    self._unsafe_i64_arg(expr.args[2]), addr, align=1,
                )
                return self._emit_none_literal()
            if intrinsic == "store_i32":
                addr = self._unsafe_typed_addr(base, offset, _I32)
                raw = self.builder.trunc(
                    self._unsafe_i64_arg(expr.args[2]), _I32,
                    name=self._fresh("unsafe.i64.to.i32"),
                )
                self.builder.store(raw, addr, align=1)
                return self._emit_none_literal()
            if intrinsic == "store_i8":
                addr = self._unsafe_typed_addr(base, offset, _I8)
                raw = self.builder.trunc(
                    self._unsafe_i64_arg(expr.args[2]), _I8,
                    name=self._fresh("unsafe.i64.to.i8"),
                )
                self.builder.store(raw, addr, align=1)
                return self._emit_none_literal()
            if intrinsic == "store_f64":
                addr = self._unsafe_typed_addr(base, offset, _DOUBLE)
                self.builder.store(
                    self._unsafe_f64_arg(expr.args[2]), addr, align=1,
                )
                return self._emit_none_literal()
            addr = self._unsafe_typed_addr(base, offset, _CSTR)
            self.builder.store(
                self._unsafe_ptr_arg(expr.args[2]), addr, align=1,
            )
            return self._emit_none_literal()
        if intrinsic == "memset":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            memset_fn = self._declare_external_function(
                "memset", _CSTR, [_CSTR, _I32, _I64],
            )
            return self.builder.call(
                memset_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i32_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.memset"),
            )
        if intrinsic in ("memcpy", "memmove"):
            self._unsafe_expect_arity(intrinsic, expr, 3)
            copy_fn = self._declare_external_function(
                intrinsic, _CSTR, [_CSTR, _CSTR, _I64],
            )
            return self.builder.call(
                copy_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh(f"unsafe.{intrinsic}"),
            )
        if intrinsic == "write":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            write_fn = self._declare_external_function(
                "write", _I64, [_I32, _CSTR, _I64],
            )
            return self.builder.call(
                write_fn,
                [
                    self._unsafe_i32_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.write"),
            )
        if intrinsic == "strlen":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            strlen_fn = self._declare_external_function(
                "strlen", _I64, [_CSTR],
            )
            return self.builder.call(
                strlen_fn,
                [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.strlen"),
            )
        if intrinsic == "getenv":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            getenv_fn = self._declare_external_function(
                "getenv", _CSTR, [_CSTR],
            )
            return self.builder.call(
                getenv_fn, [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.getenv"),
            )
        if intrinsic == "setenv":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            setenv_fn = self._declare_external_function(
                "setenv", _I32, [_CSTR, _CSTR, _I32],
            )
            raw = self.builder.call(
                setenv_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i32_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.setenv.i32"),
            )
            return self.builder.sext(
                raw, _I64, name=self._fresh("unsafe.setenv"),
            )
        if intrinsic == "unsetenv":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            unsetenv_fn = self._declare_external_function(
                "unsetenv", _I32, [_CSTR],
            )
            raw = self.builder.call(
                unsetenv_fn, [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.unsetenv.i32"),
            )
            return self.builder.sext(
                raw, _I64, name=self._fresh("unsafe.unsetenv"),
            )
        if intrinsic == "access":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            access_fn = self._declare_external_function(
                "access", _I32, [_CSTR, _I32],
            )
            raw = self.builder.call(
                access_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i32_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.access.i32"),
            )
            return self.builder.sext(
                raw, _I64, name=self._fresh("unsafe.access"),
            )
        raise NotImplementedError(
            f"unknown pcc.unsafe intrinsic {intrinsic!r}"
        )

    def _maybe_register_extern_assign(self, stmt: "Assign") -> bool:
        """If the RHS is a call to the imported ``extern`` factory,
        record the decl and suppress runtime emission. Returns True if
        handled."""
        bindings = getattr(self, "_extern_bindings", {})
        if not bindings:
            return False
        value = stmt.value
        # ``LLVMContextRef = c_ptr`` / ``c_int_alias = c_int`` —
        # module-level alias of an extern-imported type marker. pcc
        # doesn't materialise the marker at runtime, so treat the
        # assignment as a no-op. Also register the alias so later
        # ``LLVMContextRef`` references (e.g. in extern(...) decls)
        # resolve back to the same type marker.
        if (
            isinstance(value, Name)
            and value.ident in bindings
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], Name)
        ):
            bindings[stmt.targets[0].ident] = bindings[value.ident]
            return True
        if not isinstance(value, Call) or not isinstance(value.func, Name):
            return False
        if bindings.get(value.func.ident) != "extern":
            return False
        if not value.args:
            return False
        symbol_expr = value.args[0]
        if not isinstance(symbol_expr, StrLit):
            return False
        symbol = symbol_expr.value
        # Parse argtypes tuple and restype from kwargs or positional.
        argtype_exprs: tuple = ()
        restype_name: str = "c_void"
        variadic = False
        for k, kv in value.kwargs:
            if k == "argtypes" and isinstance(kv, TupleExpr):
                argtype_exprs = kv.elems
            elif k == "restype" and isinstance(kv, Name):
                restype_name = kv.ident
            elif k == "variadic" and isinstance(kv, BoolLit):
                variadic = kv.value
        if not argtype_exprs and len(value.args) >= 2:
            a = value.args[1]
            if isinstance(a, TupleExpr):
                argtype_exprs = a.elems
        if restype_name == "c_void" and len(value.args) >= 3:
            rt = value.args[2]
            if isinstance(rt, Name):
                restype_name = rt.ident
        argtype_names: list[str] = []
        for ae in argtype_exprs:
            if not isinstance(ae, Name):
                return False
            argtype_names.append(ae.ident)
        for target in stmt.targets:
            if not isinstance(target, Name):
                continue
            if not hasattr(self, "_extern_decls"):
                self._extern_decls: dict[str, tuple[str, list[str], str, bool]] = {}
            self._extern_decls[target.ident] = (
                symbol, argtype_names, restype_name, variadic,
            )
        return True

    def _emit_extern_call(
        self, decl: tuple[str, list[str], str, bool], args: tuple,
    ) -> ir.Value:
        symbol, argtype_names, restype_name, variadic = decl
        # Build / get the declared function.
        param_tys = [
            self._EXTERN_CTYPE_IR[n] for n in argtype_names
        ]
        ret_ty = self._EXTERN_CTYPE_IR[restype_name]
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=variadic)
        fn = self.module.globals.get(symbol)
        if not isinstance(fn, ir.Function):
            fn = ir.Function(self.module, fnty, name=symbol)
            fn.linkage = "external"

        # Marshal each actual arg to the declared IR type.
        ir_args: list[ir.Value] = []
        for i, a in enumerate(args):
            v = self._emit_expr(a)
            if i < len(argtype_names):
                want = self._EXTERN_CTYPE_IR[argtype_names[i]]
                v = self._coerce_to_extern(v, a.ty, want, argtype_names[i])
            ir_args.append(v)
        call_name = (
            "" if isinstance(ret_ty, ir.VoidType)
            else self._fresh(f"extern.{symbol}.ret")
        )
        return self.builder.call(fn, ir_args, name=call_name)

    def _coerce_to_extern(
        self, v: ir.Value, ty: "Type", want: ir.Type, ctype_name: str,
    ) -> ir.Value:
        """Narrow bridge between pcc-native scalar types and the
        extern declaration's IR type. Handles int→i32/i64 truncate+
        sext, pcc str → i8*, bool zext."""
        if isinstance(want, ir.VoidType):
            return v
        if ctype_name in {"c_str", "c_ptr"}:
            # pcc str is already i8* (points to PyStrObject); for the
            # narrow P6C.1 case we want the underlying C string, not
            # the PyStrObject. This requires a runtime helper — for
            # now pass through and document the sharp edge.
            return v
        if isinstance(want, ir.IntType):
            i64 = self._to_int64(v, ty)
            if want.width == 64:
                return i64
            if want.width < 64:
                return self.builder.trunc(
                    i64, want, name=self._fresh(f"extern.trunc{want.width}"),
                )
            return self.builder.sext(
                i64, want, name=self._fresh(f"extern.sext{want.width}"),
            )
        if isinstance(want, ir.DoubleType):
            return self._to_double(v, ty)
        return v

    def _cpy_modules(self) -> dict:
        """Module-wide map of imported local name → global variable."""
        if not hasattr(self, "_cpy_module_env"):
            self._cpy_module_env = {}
        return self._cpy_module_env

    def _cpy_star_modules(self) -> dict[str, ir.GlobalVariable]:
        """Globals storing modules imported via ``from x import *``."""
        if not hasattr(self, "_cpy_star_module_env"):
            self._cpy_star_module_env = {}
        return self._cpy_star_module_env

    def _cpy_star_module_global(self, module_name: str) -> ir.GlobalVariable:
        star_modules = self._cpy_star_modules()
        gv = star_modules.get(module_name)
        if gv is not None:
            return gv
        gv = self._cpy_module_global(f"starimport.{module_name}")
        star_modules[module_name] = gv
        return gv

    def _load_from_cpy_star_imports(self, name: str) -> Optional[ir.Value]:
        """Resolve an otherwise-unbound name from a prior star import."""
        star_modules = getattr(self, "_cpy_star_module_env", {})
        if not star_modules:
            return None
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(name, f".cpy.star.attr.{name}")
        )
        values = tuple(star_modules.values())
        idx = len(values) - 1
        gv = values[idx]
        mod_val = self.builder.load(
            gv, name=self._fresh(f"cpy.star.mod.{idx}")
        )
        val = self.builder.call(
            self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
            name=self._fresh(f"cpy.star.{name}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(val)
        return val

    def _ensure_cpy_init(self) -> None:
        """Emit a one-time ``py_cpy_ensure_init()`` in the current
        function. Idempotent both in IR (py_cpy_ensure_init's atomic
        guard) and in emission (we only emit it once per function
        compilation)."""
        if not hasattr(self, "_cpy_init_emitted_fns"):
            self._cpy_init_emitted_fns = set()
        fn_id = id(self.current_function)
        if fn_id in self._cpy_init_emitted_fns:
            return
        self.builder.call(self.runtime["py_cpy_ensure_init"], [])
        self._cpy_init_emitted_fns.add(fn_id)

    # -- Native file objects -----------------------------------------

    def _emit_native_open_call(self, expr: Call) -> Optional[ir.Value]:
        """Lower the common text-mode ``open(path, mode?, encoding=...)``
        surface to pcc's runtime file object.

        The runtime stores bytes and assumes UTF-8 text. ``encoding`` /
        ``errors`` / ``newline`` are accepted as compatibility kwargs
        and ignored; unsupported kwargs fall back to CPython.
        """
        if not isinstance(expr.func, Name) or expr.func.ident != "open":
            return None
        if len(expr.args) < 1 or len(expr.args) > 2:
            return None
        for key, _value in expr.kwargs:
            if key not in ("encoding", "errors", "newline"):
                return None
        path_obj = self._emit_os_path_arg_object(expr.args[0])
        if len(expr.args) == 2:
            mode_v = self._emit_expr(expr.args[1])
            mode_obj = self._emit_value_as_pcc_object_or_bridge(
                mode_v, expr.args[1].ty, "cpy.file.mode",
            )
        else:
            mode_obj = self._emit_str_literal("r")
        result = self.builder.call(
            self.runtime["py_file_open"], [path_obj, mode_obj],
            name=self._fresh("file.open"),
        )
        if not hasattr(self, "_native_file_values"):
            self._native_file_values = set()
        self._native_file_values.add(result)
        return result

    def _emit_native_file_method(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        if not isinstance(attr.obj, Name):
            return None
        if not getattr(self, "_native_file_env_flags", {}).get(
            attr.obj.ident, False,
        ):
            return None
        recv = self._emit_expr(attr.obj)
        if attr.name == "read" and not expr.args:
            return self.builder.call(
                self.runtime["py_file_read_all"], [recv],
                name=self._fresh("file.read"),
            )
        if attr.name == "write" and len(expr.args) == 1:
            text_v = self._emit_expr(expr.args[0])
            text_obj = self._emit_value_as_pcc_object_or_bridge(
                text_v, expr.args[0].ty, "cpy.file.write.arg",
            )
            return self.builder.call(
                self.runtime["py_file_write"], [recv, text_obj],
                name=self._fresh("file.write"),
            )
        if attr.name == "close" and not expr.args:
            self.builder.call(self.runtime["py_file_close"], [recv])
            return self._emit_none_literal()
        return None

    def _emit_native_file_with(self, stmt: With) -> bool:
        ctx_expr, as_expr = stmt.items[0]
        if (
            not isinstance(ctx_expr, Call)
            or not isinstance(ctx_expr.func, Name)
            or ctx_expr.func.ident != "open"
            or not isinstance(as_expr, Name)
        ):
            return False
        file_val = self._emit_native_open_call(ctx_expr)
        if file_val is None:
            return False

        slot = self.env.get(as_expr.ident)
        if slot is None:
            alloca = self._alloca_in_entry(_CSTR, name=f"{as_expr.ident}.addr")
            self.env[as_expr.ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[as_expr.ident]
        self.builder.store(file_val, slot[0])
        if not hasattr(self, "_native_file_env_flags"):
            self._native_file_env_flags = {}
        self._native_file_env_flags[as_expr.ident] = True
        if hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags.pop(as_expr.ident, None)

        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.call(self.runtime["py_file_close"], [file_val])
        return True

    def _emit_native_tempdir_with(self, stmt: With) -> bool:
        ctx_expr, as_expr = stmt.items[0]
        if (
            not isinstance(ctx_expr, Call)
            or not isinstance(ctx_expr.func, Attr)
            or not isinstance(ctx_expr.func.obj, Name)
            or self._native_builtin_module_for_name(ctx_expr.func.obj.ident)
            != "tempfile"
            or ctx_expr.func.name != "TemporaryDirectory"
            or ctx_expr.args
            or not isinstance(as_expr, Name)
        ):
            return False
        prefix_expr = None
        for key, value in ctx_expr.kwargs:
            if key != "prefix":
                return False
            prefix_expr = value
        prefix_obj = (
            self._emit_as_object(prefix_expr)
            if prefix_expr is not None else
            self._emit_str_literal("tmp")
        )
        tmp_val = self.builder.call(
            self.runtime["py_tempdir_new"], [prefix_obj],
            name=self._fresh("tempfile.TemporaryDirectory"),
        )

        slot = self.env.get(as_expr.ident)
        if slot is None:
            alloca = self._alloca_in_entry(_CSTR, name=f"{as_expr.ident}.addr")
            self.env[as_expr.ident] = (alloca, _CSTR, StrType(name="str"))
            slot = self.env[as_expr.ident]
        self.builder.store(tmp_val, slot[0])
        if hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags.pop(as_expr.ident, None)

        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.call(self.runtime["py_tempdir_cleanup"], [tmp_val])
        return True

    # -- With-statement (context manager) -----------------------------

    def _emit_with(self, stmt: With) -> None:
        """Narrow-subset ``with EXPR as VAR: BODY`` lowering.

        For a single context expression whose value is CPython-backed
        (e.g. ``with open(...) as f:``), emit the happy-path sequence::

            _cm   = <expr>
            _val  = py_cpy_call1(py_cpy_getattr(_cm, "__enter__"), _cm)  # bound method
            VAR   = _val                                                 # if as-clause
            <body>
            py_cpy_call3(py_cpy_getattr(_cm, "__exit__"), None, None, None)

        Exception-path unwinding through __exit__ is deferred —
        exceptions inside the body propagate past __exit__ in this
        subset.
        """
        if len(stmt.items) != 1:
            raise NotImplementedError(
                "Layer 1 with-statement only handles a single context expression"
            )
        if self._emit_native_file_with(stmt):
            return
        if self._emit_native_tempdir_with(stmt):
            return
        ctx_expr, as_expr = stmt.items[0]
        ctx_val = self._emit_expr(ctx_expr)
        if ctx_val not in getattr(self, "_cpy_values", ()):
            # Not already a CPython-tagged value — try to marshal
            # whatever came back (most likely a pcc PyObject*
            # wrapping a contextmanager-decorated user generator or a
            # local CPython callable's return) through the universal
            # converter so the downstream ``__enter__`` / ``__exit__``
            # dispatch has a CPython ref to work with.
            marshalled, _owned = self._marshal_to_cpython(
                ctx_val, ctx_expr.ty,
            )
            ctx_val = marshalled
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(ctx_val)

        # Call __enter__ via py_cpy_call_noargs on a bound method — i.e.
        # PyObject_GetAttr returns a bound method that already knows the
        # receiver, so we don't pass self explicitly.
        enter_ptr = self._ptr_to_cstr(
            self._cstr_global("__enter__", ".cpy.attr.__enter__")
        )
        enter_fn = self.builder.call(
            self.runtime["py_cpy_getattr"], [ctx_val, enter_ptr],
            name=self._fresh("with.enter.fn"),
        )
        enter_val = self.builder.call(
            self.runtime["py_cpy_call_noargs"], [enter_fn],
            name=self._fresh("with.enter.val"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [enter_fn])

        # Tag the __enter__ result as CPython.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(enter_val)

        if as_expr is not None:
            if not isinstance(as_expr, Name):
                raise NotImplementedError(
                    "Layer 1 with: as-clause must be a bare name"
                )
            slot = self.env.get(as_expr.ident)
            if slot is None:
                alloca = self._alloca_in_entry(
                    _CSTR, name=f"{as_expr.ident}.addr",
                )
                self.env[as_expr.ident] = (
                    alloca, _CSTR, DynType(name="dyn"),
                )
                slot = self.env[as_expr.ident]
            self.builder.store(enter_val, slot[0])
            if not hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags = {}
            self._cpy_env_flags[as_expr.ident] = True

        self._emit_stmts(stmt.body)

        if not self._builder_block_is_terminated():
            # __exit__(None, None, None). Reuse a single CPython None
            # ref for all three args; py_cpy_call3 borrows them.
            exit_ptr = self._ptr_to_cstr(
                self._cstr_global("__exit__", ".cpy.attr.__exit__")
            )
            exit_fn = self.builder.call(
                self.runtime["py_cpy_getattr"], [ctx_val, exit_ptr],
                name=self._fresh("with.exit.fn"),
            )
            none_gv = declare_runtime_global(self.module, "py_None")
            none = self.builder.load(none_gv, name=self._fresh("none"))
            cpy_none = self.builder.call(
                self.runtime["py_cpy_from_pcc_obj"], [none],
                name=self._fresh("cpy.none"),
            )
            self.builder.call(
                self.runtime["py_cpy_call3"],
                [exit_fn, cpy_none, cpy_none, cpy_none],
                name=self._fresh("with.exit.val"),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [cpy_none])
            self.builder.call(self.runtime["py_cpy_decref"], [exit_fn])
            self.builder.call(self.runtime["py_cpy_decref"], [enter_val])
            # ctx_val was obtained from _emit_expr(ctx_expr); if it
            # was freshly produced (not a borrowed module ref), it's
            # also owned. Leave the decref policy to whoever emitted
            # ctx_expr.

    # -- Exceptions: raise + try/except/finally -----------------------

    _BUILTIN_EXC_TAG = {
        "BaseException": 0,
        "Exception": 1,
        "ValueError": 2,
        "TypeError": 3,
        "KeyError": 4,
        "IndexError": 5,
        "AttributeError": 6,
        "SyntaxError": 1,
        "RuntimeError": 7,
        "StopIteration": 8,
        "ZeroDivisionError": 9,
        "NameError": 10,
        "NotImplementedError": 11,
        "ArithmeticError": 12,
        "LookupError": 13,
        "OSError": 14,
        "IOError": 14,
        "OverflowError": 15,
        "AssertionError": 16,
        # OSError sub-hierarchy — pcc doesn't ship distinct tags for
        # these (they'd require py_exc.c to grow), so alias to
        # ``OSError``. Matches ``isinstance(e, OSError)`` behaviour
        # and keeps raise/except usage compiling.
        "FileNotFoundError": 14,
        "FileExistsError": 14,
        "IsADirectoryError": 14,
        "NotADirectoryError": 14,
        "PermissionError": 14,
        "BrokenPipeError": 14,
        "ConnectionError": 14,
        "ConnectionAbortedError": 14,
        "ConnectionRefusedError": 14,
        "ConnectionResetError": 14,
        "BlockingIOError": 14,
        "ChildProcessError": 14,
        "InterruptedError": 14,
        "TimeoutError": 14,
        # ValueError + LookupError subclasses
        "UnicodeError": 2,
        "UnicodeDecodeError": 2,
        "UnicodeEncodeError": 2,
        # IndexError / KeyError sibling
        "RecursionError": 7,
        # misc common builtins mapped to their parent
        "ImportError": 1,
        "ModuleNotFoundError": 1,
        "EOFError": 1,
        "SystemExit": 0,
        "KeyboardInterrupt": 0,
        "GeneratorExit": 0,
    }

    def _emit_raise(self, stmt: Raise) -> None:
        """Lower `raise`: set the TLS exception slot via py_raise,
        then branch to the active error-handler block (either a
        surrounding try's err block or this function's err-exit
        epilogue). No unwinder, no Itanium ABI.
        """
        if stmt.exc is None:
            # Bare ``raise`` inside a handler: py_raise with the
            # currently-pending exception.
            cur = self.builder.call(
                self.runtime["py_current_exception"], [],
                name=self._fresh("reraise.exc"),
            )
            active_stack = getattr(self, "_active_handler_excs", ())
            if active_stack:
                null = ir.Constant(_CSTR, None)
                has_cur = self.builder.icmp_signed(
                    "!=", cur, null, name=self._fresh("reraise.has_cur"),
                )
                cur = self.builder.select(
                    has_cur, cur, active_stack[-1],
                    name=self._fresh("reraise.active"),
                )
            self.builder.call(self.runtime["py_raise"], [cur])
        else:
            exc_val = self._build_exception_value(stmt.exc)
            if stmt.cause is not None:
                cause_val = self._emit_expr(stmt.cause)
                self.builder.call(
                    self.runtime["py_exc_set_cause"], [exc_val, cause_val]
                )
            self.builder.call(self.runtime["py_raise"], [exc_val])

        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

    def _build_exception_value(self, exc_expr: Expr) -> ir.Value:
        """Lower a ``raise`` operand expression to a ``PyObject*``.

        Supports ``ExceptionClass("msg")`` / ``ExceptionClass()`` for
        builtin exception names, plus a bare ``Name`` for re-raising an
        already-bound exception.
        """
        def _message_cstr(
            args: tuple[Expr, ...],
            kwargs: tuple[tuple[str, Expr], ...] = (),
        ) -> ir.Value:
            msg_expr: Optional[Expr] = None
            if args:
                msg_expr = args[0]
            else:
                for key, value in kwargs:
                    if key == "message" or key == "msg":
                        msg_expr = value
                        break
            if msg_expr is None:
                return self._ptr_to_cstr(
                    self._cstr_global("", self._fresh(".exc.msg.empty"))
                )
            first = msg_expr
            if isinstance(first, StrLit):
                return self._ptr_to_cstr(
                    self._cstr_global(first.value, self._fresh(".exc.msg"))
                )
            msg_obj = self._emit_as_object(first)
            msg_str = msg_obj
            if not isinstance(first.ty, StrType):
                msg_str = self.builder.call(
                    self.runtime["py_obj_str"], [msg_obj],
                    name=self._fresh("exc.msg.obj"),
                )
            return self.builder.call(
                self.runtime["py_str_utf8"], [msg_str],
                name=self._fresh("exc.msg.cstr"),
            )

        if isinstance(exc_expr, Call) and isinstance(exc_expr.func, Name):
            cls_name = exc_expr.func.ident
            tag = self._BUILTIN_EXC_TAG.get(cls_name)
            if tag is not None:
                msg_ptr = _message_cstr(exc_expr.args, exc_expr.kwargs)
                return self.builder.call(
                    self.runtime["py_exc_new"],
                    [ir.Constant(_I64, tag), msg_ptr],
                    name=self._fresh(f"exc.{cls_name}"),
                )
            if (
                hasattr(self, "class_lowering")
                and cls_name in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[cls_name]
                cls_val = self.builder.load(
                    info.global_var,
                    name=self._fresh(f"exc.ucls.{cls_name}"),
                )
                msg_ptr = _message_cstr(exc_expr.args, exc_expr.kwargs)
                return self.builder.call(
                    self.runtime["py_exc_new_with_class"],
                    [cls_val, msg_ptr],
                    name=self._fresh(f"exc.{cls_name}"),
                )
        # ``raise NotImplementedError`` (bare builtin exception name, no
        # call). Instantiate the exception with an empty message.
        if (
            isinstance(exc_expr, Name)
            and exc_expr.ident in self._BUILTIN_EXC_TAG
            and exc_expr.ident not in self.env
        ):
            cls_name = exc_expr.ident
            tag = self._BUILTIN_EXC_TAG[cls_name]
            return self.builder.call(
                self.runtime["py_exc_new"],
                [ir.Constant(_I64, tag), _message_cstr(())],
                name=self._fresh(f"exc.{cls_name}"),
            )
        if (
            isinstance(exc_expr, Name)
            and exc_expr.ident not in self.env
            and hasattr(self, "class_lowering")
            and exc_expr.ident in self.class_lowering.classes
        ):
            cls_name = exc_expr.ident
            info = self.class_lowering.classes[cls_name]
            cls_val = self.builder.load(
                info.global_var,
                name=self._fresh(f"exc.ucls.{cls_name}"),
            )
            return self.builder.call(
                self.runtime["py_exc_new_with_class"],
                [cls_val, _message_cstr(())],
                name=self._fresh(f"exc.{cls_name}"),
            )
        # Fallback: evaluate as an object (e.g. re-raising a bound var).
        return self._emit_as_object(exc_expr)

    def _emit_try(self, stmt: Try) -> None:
        if not stmt.handlers and not stmt.finally_body:
            # Plain try with neither handlers nor finally: just emit
            # body (shouldn't occur from the parser, but be defensive).
            self._emit_stmts(stmt.body)
            return

        # Return-code exception model (replaces earlier Itanium ABI
        # landingpad design). Any call in the body that raises sets
        # the TLS exception slot via py_raise and branches directly to
        # ``err_bb``; normal body falls through to else/finally/done.
        fn = self.current_function
        done_bb = fn.append_basic_block(name=self._fresh("try.done"))
        err_bb = fn.append_basic_block(name=self._fresh("try.err"))

        prev_err_block = getattr(self, "_try_err_block", None)
        self._try_err_block = err_bb

        # Emit the body.
        self._emit_stmts(stmt.body)

        # After body: fall through to else_body (if any), then to done.
        if not self._builder_block_is_terminated():
            if stmt.else_body:
                self._emit_stmts(stmt.else_body)
            if not self._builder_block_is_terminated():
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self._builder_block_is_terminated():
                    self.builder.branch(done_bb)

        # Pop the err-block — nested raises within handler bodies
        # propagate to the enclosing frame (or to that frame's err
        # exit).
        self._try_err_block = prev_err_block

        # Error-dispatch block: runs when py_err_occurred() after any
        # call in the body returned true.
        self.builder.position_at_end(err_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"], [],
            name=self._fresh("try.cur_exc"),
        )

        if not stmt.handlers:
            # Pure try/finally: run finally body, then propagate. The
            # TLS slot still holds the pending exception, so branching
            # to the enclosing err block (or function err-exit)
            # continues the propagation.
            if stmt.finally_body:
                self._emit_stmts(stmt.finally_body)
            if not self._builder_block_is_terminated():
                outer = prev_err_block or self._ensure_fn_err_exit()
                self.builder.branch(outer)
            self.builder.position_at_end(done_bb)
            return

        # Walk handlers. Each handler either claims (py_err_clear,
        # bind, run handler body, fall to done) or forwards to the next
        # handler. After the last handler, unclaimed exceptions fall
        # through to the enclosing error block (or function err-exit).
        for i, h in enumerate(stmt.handlers):
            test_bb = fn.append_basic_block(
                name=self._fresh(f"except.test.{i}")
            )
            body_bb = fn.append_basic_block(
                name=self._fresh(f"except.body.{i}")
            )
            if not self._builder_block_is_terminated():
                self.builder.branch(test_bb)
            self.builder.position_at_end(test_bb)

            if h.exc_type is None:
                cond = ir.Constant(_I1, 1)
            elif isinstance(h.exc_type, TupleExpr):
                cond = None
                for sub in h.exc_type.elems:
                    cls_val = self._emit_exception_class_ref(sub)
                    match_i32 = self.builder.call(
                        self.runtime["py_exc_matches"],
                        [current_exc, cls_val],
                        name=self._fresh("exc.matches"),
                    )
                    this = self.builder.icmp_signed(
                        "!=", match_i32, ir.Constant(_I64, 0),
                        name=self._fresh("exc.matches.i1"),
                    )
                    cond = this if cond is None else self.builder.or_(
                        cond, this, name=self._fresh("exc.or"),
                    )
                assert cond is not None
            else:
                cls_val = self._emit_exception_class_ref(h.exc_type)
                match_i32 = self.builder.call(
                    self.runtime["py_exc_matches"],
                    [current_exc, cls_val],
                    name=self._fresh("exc.matches"),
                )
                cond = self.builder.icmp_signed(
                    "!=", match_i32, ir.Constant(_I64, 0),
                    name=self._fresh("exc.matches.i1"),
                )

            if i + 1 < len(stmt.handlers):
                next_test_bb = fn.append_basic_block(
                    name=self._fresh(f"except.next.{i + 1}")
                )
                self.builder.cbranch(cond, body_bb, next_test_bb)
            else:
                propagate_bb = fn.append_basic_block(
                    name=self._fresh("except.propagate")
                )
                self.builder.cbranch(cond, body_bb, propagate_bb)
                next_test_bb = None
                # Unclaimed: err is still set in TLS, branch outward
                # for the enclosing try or the function epilogue to
                # handle.
                self.builder.position_at_end(propagate_bb)
                outer = prev_err_block or self._ensure_fn_err_exit()
                self.builder.branch(outer)

            # Handler body: clear the err flag, bind `as` var, run body,
            # then finally + done.
            self.builder.position_at_end(body_bb)
            self.builder.call(self.runtime["py_clear_exception"], [])
            if h.name is not None:
                slot = self._alloca_in_entry(_CSTR, name=f"{h.name}.addr")
                self.builder.store(current_exc, slot)
                self.env[h.name] = (slot, _CSTR, DynType(name="dyn"))
            active_excs = list(getattr(self, "_active_handler_excs", ()))
            active_excs.append(current_exc)
            self._active_handler_excs = active_excs
            try:
                self._emit_stmts(h.body)
            finally:
                active_excs.pop()
                if active_excs:
                    self._active_handler_excs = active_excs
                else:
                    self._active_handler_excs = ()
            if not self._builder_block_is_terminated():
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self._builder_block_is_terminated():
                    self.builder.branch(done_bb)

            if next_test_bb is not None:
                self.builder.position_at_end(next_test_bb)

        self.builder.position_at_end(done_bb)

    def _emit_exception_class_ref(self, expr: Expr) -> ir.Value:
        """Build a PyObject* for an exception class used in
        ``except <Expr>:``. Supports a bare builtin Name, a local user
        class, or a CPython-imported class (routed through the
        libpython fallback). Falls back to the builtin ``Exception``
        class when the name can't be resolved — catches strictly more
        than requested, but lets pcc continue compiling files that
        reference exception classes declared in modules not yet
        reachable on the self-host path."""
        if isinstance(expr, Name):
            tag = self._BUILTIN_EXC_TAG.get(expr.ident)
            if tag is not None:
                return self.builder.call(
                    self.runtime["py_exc_builtin_class"],
                    [ir.Constant(_I64, tag)],
                    name=self._fresh(f"exc.cls.{expr.ident}"),
                )
            # User class? Look up in class_lowering.
            if (
                hasattr(self, "class_lowering")
                and expr.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.ident]
                return self.builder.load(
                    info.global_var,
                    name=self._fresh(f"exc.ucls.{expr.ident}"),
                )
            # CPython-imported class (``from foo import FooError``).
            # The module env has the class as a ``py_cpy_import``-
            # rooted value; passing it to ``py_exc_matches`` makes the
            # runtime fall through to a type-identity compare. Works
            # for the common "catch a specific user exception" case
            # without requiring a pcc-side ClassInfo.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.ident)
            if cpy_gv is not None:
                return self.builder.load(
                    cpy_gv, name=self._fresh(f"exc.cpy.{expr.ident}"),
                )
            # Fall back to the generic ``Exception`` base so the except
            # clause still compiles. Runtime semantics are broader than
            # CPython's, but the goal here is self-host compile coverage
            # — the narrow type match is recovered once the referenced
            # exception class reaches pcc's ClassInfo registry.
            exc_tag = self._BUILTIN_EXC_TAG.get("Exception", 1)
            return self.builder.call(
                self.runtime["py_exc_builtin_class"],
                [ir.Constant(_I64, exc_tag)],
                name=self._fresh(f"exc.cls.fallback.{expr.ident}"),
            )
        # Attribute access: ``except json.JSONDecodeError:`` etc. Fall
        # back to the generic Exception class so the clause at least
        # compiles. Runtime match is broader than CPython would do;
        # recovering the narrow type match requires exposing the
        # imported class's CPython PyTypeObject through py_exc_matches,
        # which is a separate runtime extension.
        try:
            from ..py_ast import Attr as _AttrExpr  # local import
        except Exception:
            _AttrExpr = None
        if _AttrExpr is not None and isinstance(expr, _AttrExpr):
            exc_tag = self._BUILTIN_EXC_TAG.get("Exception", 1)
            return self.builder.call(
                self.runtime["py_exc_builtin_class"],
                [ir.Constant(_I64, exc_tag)],
                name=self._fresh(f"exc.cls.attr_fallback.{expr.name}"),
            )
        raise NotImplementedError(
            f"Layer 1 except-clause class expression {type(expr).__name__} "
            "not supported"
        )

    # -- Control flow --------------------------------------------------

    def _emit_if(self, stmt: If) -> None:
        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("if.then"))
        else_bb = fn.append_basic_block(name=self._fresh("if.else"))
        merge_bb = fn.append_basic_block(name=self._fresh("if.end"))

        self.builder.cbranch(cond_i1, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.branch(merge_bb)

        self.builder.position_at_end(else_bb)
        if stmt.else_body:
            self._emit_stmts(stmt.else_body)
        if not self._builder_block_is_terminated():
            self.builder.branch(merge_bb)

        # If both branches terminated, merge block is unreachable —
        # still position into it so subsequent stmts have a home.
        self.builder.position_at_end(merge_bb)

    def _emit_while(self, stmt: While) -> None:
        if stmt.else_body:
            from dataclasses import replace as _replace

            broke_name = self._fresh("whileelse_broke")
            span = stmt.span
            broke_lit_false = BoolLit(
                span=span, ty=BoolType(name="bool"), value=False,
            )
            broke_lit_true = BoolLit(
                span=span, ty=BoolType(name="bool"), value=True,
            )
            broke_ref = Name(
                span=span, ty=BoolType(name="bool"), ident=broke_name,
            )
            set_broke_true = Assign(
                span=span,
                targets=(broke_ref,),
                value=broke_lit_true,
                annotation=BoolType(name="bool"),
            )

            def tag_breaks(stmts):
                out = []
                for s in stmts:
                    if isinstance(s, Break):
                        out.append(set_broke_true)
                        out.append(s)
                        continue
                    if isinstance(s, If):
                        out.append(_replace(
                            s,
                            body=tag_breaks(s.body),
                            else_body=tag_breaks(s.else_body),
                        ))
                        continue
                    if isinstance(s, Try):
                        new_handlers = tuple(
                            _replace(h, body=tag_breaks(h.body))
                            for h in s.handlers
                        )
                        out.append(_replace(
                            s,
                            body=tag_breaks(s.body),
                            else_body=tag_breaks(s.else_body),
                            finally_body=tag_breaks(s.finally_body),
                            handlers=new_handlers,
                        ))
                        continue
                    out.append(s)
                return tuple(out)

            ir_ty = self._map_type(BoolType(name="bool"))
            alloca = self._alloca_in_entry(
                ir_ty, name=f"{broke_name}.addr",
            )
            self.builder.store(ir.Constant(ir_ty, 0), alloca)
            self.env[broke_name] = (alloca, ir_ty, BoolType(name="bool"))

            new_stmt = _replace(
                stmt,
                body=tag_breaks(stmt.body),
                else_body=(),
            )
            self._emit_while(new_stmt)
            post_if = If(
                span=span,
                cond=Compare(
                    span=span, ty=BoolType(name="bool"),
                    op="==",
                    lhs=broke_ref,
                    rhs=broke_lit_false,
                ),
                body=tuple(stmt.else_body),
                else_body=(),
            )
            self._emit_if(post_if)
            return
        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("while.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("while.body"))
        end_bb = fn.append_basic_block(name=self._fresh("while.end"))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond = self._emit_expr(stmt.cond)
        cond_i1 = self._truthy(cond, stmt.cond.ty)
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((cond_bb, end_bb))
        self.builder.position_at_end(body_bb)
        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.branch(cond_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(end_bb)

    def _emit_for_cpython_iter(
        self, stmt: For, iter_src_val: ir.Value,
    ) -> None:
        """Lower ``for <name> in <cpython_iterable>:`` via PyObject_GetIter
        + PyIter_Next. Each iteration binds the target name to the
        returned CPython PyObject* (tagged as cpy)."""
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"], [iter_src_val],
            name=self._fresh("cpy.iter"),
        )

        header_bb = fn.append_basic_block(name=self._fresh("for.cpy.header"))
        body_bb = fn.append_basic_block(name=self._fresh("for.cpy.body"))
        after_bb = fn.append_basic_block(name=self._fresh("for.cpy.after"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        item = self.builder.call(
            self.runtime["py_cpy_iter_next"], [iter_obj],
            name=self._fresh("cpy.next"),
        )
        is_null = self.builder.icmp_signed(
            "==", item, ir.Constant(_CSTR, None),
            name=self._fresh("cpy.next.isnull"),
        )
        self.builder.cbranch(is_null, after_bb, body_bb)

        self.builder.position_at_end(body_bb)
        # Bind the target name: alloca if new, then store.
        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(_CSTR, name=f"{target_ident}.addr")
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]
        self.builder.store(item, slot[0])
        # Mark target as CPython-backed.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        self._cpy_env_flags[target_ident] = True

        # Loop control stack: continue -> header, break -> after.
        self.loop_stack.append((header_bb, after_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            # Release item (we took ownership from PyIter_Next).
            # Note: storing into the slot didn't bump ref; we hold
            # exactly one.
            self.builder.branch(header_bb)

        self.builder.position_at_end(after_bb)
        self.builder.call(self.runtime["py_cpy_decref"], [iter_obj])

    def _emit_for_list_index(
        self, stmt: For, iter_val: ir.Value, iter_ty: Type,
    ) -> None:
        """Lower ``for <name> in <list|tuple>:`` via index + length.

        Covers ``ListType`` / ``TupleType`` iters where the runtime
        value is a PyObject* tuple/list. Element type flows from
        ``iter_ty.elem`` (list) or ``DynType`` (tuple — element types
        differ per slot, so we fall back to Dyn here).
        """
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            iter_val, iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty: Type = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
            if isinstance(iter_ty, TupleType) and iter_ty.elems:
                first = iter_ty.elems[0]
                if (
                    iter_ty.name == "tuple_variadic"
                    or self._tuple_elems_are_uniform(iter_ty.elems, first)
                ):
                    elem_ty = first
        n_val = self.builder.call(
            self.runtime[len_helper], [iter_obj],
            name=self._fresh("for.len"),
        )

        idx_slot = self._alloca_in_entry(_I64, name="for.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            # Allocate as PyObject* when element is dyn, else native.
            if isinstance(elem_ty, DynType):
                target_ir_ty = _CSTR
            else:
                target_ir_ty = self._storage_ir_type(elem_ty)
            alloca = self._alloca_in_entry(
                target_ir_ty, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, target_ir_ty, elem_ty)
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.lst.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.lst.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.lst.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.lst.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        elem_obj = self.builder.call(
            self.runtime[get_helper], [iter_obj, cur],
            name=self._fresh("for.elem"),
        )
        target_alloca, target_ir_ty, _ = slot
        if isinstance(elem_ty, DynType) or isinstance(target_ir_ty, ir.PointerType):
            self.builder.store(elem_obj, target_alloca)
        else:
            native_val = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, elem_ty,
            )
            self.builder.store(native_val, target_alloca)

        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("for.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _for_iter_is_enumerate(self, stmt: For) -> bool:
        it = stmt.iter
        if not (
            isinstance(it, Call)
            and isinstance(it.func, Name)
            and it.func.ident == "enumerate"
        ):
            return False
        if len(it.args) == 1 and not it.kwargs:
            return True
        # ``enumerate(iterable, start)`` / ``enumerate(iterable, start=N)``
        # — second arg is the starting index. Accept any literal/int
        # expression; the codegen adds the offset to the counter.
        if len(it.args) == 2 and not it.kwargs:
            return True
        if len(it.args) == 1 and len(it.kwargs) == 1:
            (kn, _kv) = it.kwargs[0]
            return kn == "start"
        return False

    def _for_iter_is_zip(self, stmt: For) -> bool:
        """``for <...> in zip(xs, ys, ...):`` — optionally with
        ``strict=True``, which we accept and drop."""
        it = stmt.iter
        if not (
            isinstance(it, Call)
            and isinstance(it.func, Name)
            and it.func.ident == "zip"
            and len(it.args) >= 2
        ):
            return False
        for kwn, _ in it.kwargs:
            if kwn != "strict":
                return False
        return True

    def _normalise_for_zip(self, stmt: For) -> For:
        """Rewrite ``for (a, b, ...) in zip(xs, ys, ...):`` into::

            for __zip_i__<k> in range(min(len(xs), len(ys), ...)):
                (a, b, ...) = (xs[__zip_i__<k>], ys[__zip_i__<k>], ...)
                <orig body>

        Accepts any tuple-arity on the target (pcc normalises tuple
        targets further down the pipeline) and any iterable count.
        """
        from dataclasses import replace as _replace
        it = stmt.iter
        assert isinstance(it, Call)
        xs_list = it.args
        span = stmt.span
        int_ty = IntType(name="int")
        idx_name = self._fresh("zip_i")
        idx_ref = Name(span=span, ty=int_ty, ident=idx_name)

        # Build ``min(len(xs0), len(xs1), ...)``.
        def _len_call(e):
            return Call(
                span=span, ty=int_ty,
                func=Name(span=span, ty=DynType(name="dyn"), ident="len"),
                args=(e,),
            )
        if len(xs_list) == 1:
            stop_expr = _len_call(xs_list[0])
        else:
            # ``min(a, b, c, ...)`` — only the 2-arg form is wired as a
            # builtin fast path, so chain it left-associatively.
            stop_expr = _len_call(xs_list[0])
            for rest in xs_list[1:]:
                stop_expr = Call(
                    span=span, ty=int_ty,
                    func=Name(span=span, ty=DynType(name="dyn"), ident="min"),
                    args=(stop_expr, _len_call(rest)),
                )
        # ``range(stop_expr)`` drives the indexed walk.
        new_iter = Call(
            span=span, ty=DynType(name="dyn"),
            func=Name(span=span, ty=DynType(name="dyn"), ident="range"),
            args=(stop_expr,),
        )
        # Build ``(a, b, ...) = (xs0[i], xs1[i], ...)`` prelude.
        # Derive each subscript's type from its list/tuple elem type so
        # downstream store code picks the correct i64/ptr slot rather
        # than defaulting to DynType (which would mix ptr and i64 in
        # the same alloca).
        def _subscript_ty(xs: Expr) -> Type:
            xt = xs.ty
            if isinstance(xt, ListType):
                return xt.elem
            if isinstance(xt, TupleType) and xt.elems:
                # Assume homogenous for zip purposes — falls back to
                # the first element type which is usually correct.
                return xt.elems[0]
            return DynType(name="dyn")
        pair_elems = tuple(
            Subscript(
                span=span, ty=_subscript_ty(xs), obj=xs, idx=idx_ref,
            )
            for xs in xs_list
        )
        if isinstance(stmt.target, TupleExpr):
            # Re-use the existing tuple target.
            assign_unpack = Assign(
                span=span,
                targets=(stmt.target,),
                value=TupleExpr(
                    span=span,
                    ty=TupleType(
                        name="tuple", elems=tuple(e.ty for e in pair_elems),
                    ),
                    elems=pair_elems,
                ),
                annotation=None,
            )
        elif isinstance(stmt.target, Name):
            # ``for pair in zip(...):`` — bind whole tuple.
            assign_unpack = Assign(
                span=span,
                targets=(stmt.target,),
                value=TupleExpr(
                    span=span,
                    ty=TupleType(
                        name="tuple", elems=tuple(e.ty for e in pair_elems),
                    ),
                    elems=pair_elems,
                ),
                annotation=None,
            )
        else:
            raise NotImplementedError(
                "zip() target must be a Name or TupleExpr of Names"
            )
        new_body = (assign_unpack,) + tuple(stmt.body)
        return _replace(
            stmt, target=idx_ref, iter=new_iter, body=new_body,
        )

    def _normalise_for_enumerate(self, stmt: For) -> For:
        """Rewrite ``for <target> in enumerate(xs):`` into the
        equivalent manually-indexed loop::

            __enum_i__<k> = 0
            for <target-sans-index> in xs:
                <target> = (__enum_i__<k>, <target-sans-index>)
                <orig body>
                __enum_i__<k> = __enum_i__<k> + 1

        The synthetic counter is an annotated int so inference keeps
        it on the native path; tuple-target unpacking picks up the
        rest via the existing ``_normalise_for_tuple_target`` helper.
        """
        it = stmt.iter
        assert isinstance(it, Call)
        inner_iter = it.args[0]
        # ``enumerate(iter, start)`` — optional start value, positional
        # or keyword. Evaluate at loop entry and seed the counter with it.
        start_expr = None
        if len(it.args) == 2:
            start_expr = it.args[1]
        elif len(it.kwargs) == 1 and it.kwargs[0][0] == "start":
            start_expr = it.kwargs[0][1]
        cnt_name = self._fresh("enum_i")
        span = stmt.span
        int_ty = IntType(name="int")
        one_lit = IntLit(span=span, ty=int_ty, value=1)
        cnt_ref = Name(span=span, ty=int_ty, ident=cnt_name)

        # Insert the counter init *before* the for-loop itself by
        # synthesising an Assign statement and prepending it to the
        # caller's scope. Since we can't rewrite the surrounding
        # body here, fold the init into the pre-loop region by
        # stashing it on the codegen — the emitter will see it on
        # the next ``_emit_stmt`` entry. That infra is intrusive;
        # instead, emit the init *inline* here via a direct alloca
        # + store, side-stepping the need to touch the parent list.
        # The tuple-normaliser runs after us, so pre-loop allocation
        # inside the body avoids re-running inside each iteration.
        # We use a dedicated "pre-loop bootstrap" list on ``self``.
        # Simplest: add the init as the first statement of the new
        # stmt's body. This re-inits the counter every iteration —
        # wrong. Instead, emit the init via a small runtime routine
        # using an explicit @_pcc_py_* helper. Since that's overkill
        # for a desugar, we register a per-loop alloca outside the
        # body using an auxiliary stash consumed by _emit_stmts.
        #
        # Pragmatic approach: leave the alloca/store emission to
        # ``_emit_for`` itself, and only rewrite the AST to handle
        # the bookkeeping. Hand the ``_emit_for`` a counter name
        # plus target binding via a side-channel on ``stmt``.
        from dataclasses import replace as _replace

        # Synthesize a target Name for the "value" slot.
        if isinstance(stmt.target, TupleExpr) and len(stmt.target.elems) == 2:
            idx_target, val_target = stmt.target.elems
            if not isinstance(idx_target, Name):
                raise NotImplementedError(
                    "enumerate() index target must be a Name"
                )
            assign_idx = Assign(
                span=span,
                targets=(idx_target,),
                value=cnt_ref,
                annotation=int_ty,
            )
            if isinstance(val_target, Name):
                new_target = val_target
                prelude = (assign_idx,)
            elif isinstance(val_target, TupleExpr):
                # ``for i, (a, b) in enumerate(xs):`` — introduce a
                # synthetic single-Name target for the outer loop,
                # then unpack that Name into the user's TupleExpr
                # before the body runs.
                fresh_name = self._fresh("enum_val_pair")
                new_target = Name(
                    span=span, ty=DynType(name="dyn"), ident=fresh_name,
                )
                unpack_inner = Assign(
                    span=span,
                    targets=(val_target,),
                    value=new_target,
                    annotation=None,
                )
                prelude = (assign_idx, unpack_inner)
            else:
                raise NotImplementedError(
                    "enumerate() value target must be a Name or TupleExpr"
                )
        elif isinstance(stmt.target, Name):
            # ``for pair in enumerate(xs):`` — bind the whole tuple.
            tup_target = stmt.target
            val_name = self._fresh("enum_val")
            val_target = Name(span=span, ty=DynType(name="dyn"), ident=val_name)
            pair_expr = TupleExpr(
                span=span,
                ty=TupleType(name="tuple", elems=(int_ty, val_target.ty)),
                elems=(cnt_ref, val_target),
            )
            assign_pair = Assign(
                span=span,
                targets=(tup_target,),
                value=pair_expr,
                annotation=None,
            )
            new_target = val_target
            prelude = (assign_pair,)
        else:
            raise NotImplementedError(
                "enumerate() target must be a Name or (Name, Name)"
            )

        incr_stmt = AugAssign(
            span=span,
            target=cnt_ref,
            op="+=",
            value=one_lit,
        )
        new_body = prelude + tuple(stmt.body) + (incr_stmt,)

        # Emit the counter alloca + zero-store *now* so the rewritten
        # for-loop body sees ``__enum_i__`` already bound in ``self.env``.
        # Requires an active IRBuilder; _emit_for is called during
        # body lowering so the builder is positioned on the enclosing
        # block.
        ir_ty = self._storage_ir_type(int_ty)
        alloca = self._alloca_in_entry(ir_ty, name=f"{cnt_name}.addr")
        if start_expr is None:
            start_val = ir.Constant(_I64, 0)
        else:
            start_val = self._emit_expr_as_i64(start_expr)
        if isinstance(ir_ty, ir.PointerType):
            start_val = self.builder.call(
                self.runtime["py_int_from_i64"], [start_val],
                name=self._fresh("enum.start.box"),
            )
        self.builder.store(start_val, alloca)
        self.env[cnt_name] = (alloca, ir_ty, int_ty)

        return _replace(stmt, target=new_target, iter=inner_iter, body=new_body)

    def _normalise_for_tuple_target(self, stmt: For) -> For:
        """Rewrite ``for (a, b) in items:`` into::

            for __foritem__<k> in items:
                a, b = __foritem__<k>
                <original body>

        The fresh Name carries the iter's element type so the existing
        tuple-unpack assignment codegen (literal / runtime branch)
        picks the right shape.
        """
        target = stmt.target
        assert isinstance(target, TupleExpr)
        tmp_name = self._fresh("foritem")
        iter_ty = stmt.iter.ty
        elem_ty: Type = DynType(name="dyn")
        if isinstance(iter_ty, ListType):
            elem_ty = iter_ty.elem
        elif isinstance(iter_ty, TupleType) and iter_ty.elems:
            first = iter_ty.elems[0]
            if all(type(e) is type(first) and e == first for e in iter_ty.elems):
                elem_ty = first
        tmp_ref = Name(
            span=target.span, ty=elem_ty, ident=tmp_name,
        )
        unpack_stmt = Assign(
            span=target.span,
            targets=(target,),
            value=tmp_ref,
            annotation=None,
        )
        new_body = (unpack_stmt,) + tuple(stmt.body)
        from dataclasses import replace as _replace
        return _replace(stmt, target=tmp_ref, body=new_body)

    def _emit_for_obj_index(self, stmt: For, iter_val: ir.Value) -> None:
        """DynType for-loop: iterate by index using ``py_obj_len`` +
        ``py_obj_getitem``. Each iteration binds the target to a
        PyObject*; downstream callers see it as DynType."""
        fn = self.current_function
        # If inference pegged the iter as DynType but the IR value is
        # a native scalar (i1 from a short-circuit ``or`` branch,
        # i64 from an unboxed DynType int, etc.), box before calling
        # py_obj_len — the helper expects a pointer operand.
        if not isinstance(iter_val.type, ir.PointerType):
            iter_val = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                iter_val, stmt.iter.ty,
            )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [iter_val],
            name=self._fresh("for.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.obj.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.obj.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.obj.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        # Box the index as a PyObject* int for py_obj_getitem.
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("for.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"], [iter_val, idx_box],
            name=self._fresh("for.obj.elem"),
        )
        alloca, _, _ = slot
        self.builder.store(elem, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.obj.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("for.obj.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_for_str_chars(self, stmt: For, iter_val: ir.Value) -> None:
        """StrType for-loop: iterate codepoints via ``py_str_slice(s, i, i+1, 1)``.
        Target binds to a 1-char StrType slice each iteration."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"], [iter_val],
            name=self._fresh("for.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="for.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 1)],
            name=self._fresh("for.str.step"),
        )

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, _CSTR, StrType(name="str"))
            slot = self.env[target_ident]

        cond_bb = fn.append_basic_block(name=self._fresh("for.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("for.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("for.str.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("for.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("for.str.lo"),
        )
        hi = self.builder.add(
            cur, ir.Constant(_I64, 1), name=self._fresh("for.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"], [hi],
            name=self._fresh("for.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("for.str.ch"),
        )
        alloca, _, _ = slot
        self.builder.store(ch, alloca)
        self.loop_stack.append((step_bb, end_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("for.str.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("for.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_for_native_iterator(
        self, stmt: For, iter_val: ir.Value, class_hint: str,
    ) -> None:
        iter_info = self._resolve_method_mro(class_hint, "__iter__")
        if iter_info is None:
            return self._emit_for_obj_index(stmt, iter_val)
        iter_fn = iter_info.methods["__iter__"]
        iterator = self._emit_direct_method_call(
            iter_fn, iter_val, iter_info, "__iter__", (),
        )
        iter_fd = self.class_lowering._find_method_def(
            iter_info.name, "__iter__",
        )
        iterator_hint = class_hint
        if iter_fd is not None:
            ann_hint = self._class_hint_from_annotation(iter_fd.return_ty)
            if ann_hint is not None:
                iterator_hint = ann_hint
        next_info = self._resolve_method_mro(iterator_hint, "__next__")
        if next_info is None:
            return self._emit_for_obj_index(stmt, iterator)
        next_fn = next_info.methods["__next__"]
        next_fd = self.class_lowering._find_method_def(
            next_info.name, "__next__",
        )
        target_ty: Type = DynType(name="dyn")
        if next_fd is not None and isinstance(next_fd.return_ty, Type):
            target_ty = next_fd.return_ty
        target_ir_ty = self._map_type(target_ty)

        target_ident = stmt.target.ident
        slot = self.env.get(target_ident)
        if slot is None or slot[1] != target_ir_ty:
            alloca = self._alloca_in_entry(
                target_ir_ty, name=f"{target_ident}.addr",
            )
            self.env[target_ident] = (alloca, target_ir_ty, target_ty)
            slot = self.env[target_ident]

        fn = self.current_function
        header_bb = fn.append_basic_block(name=self._fresh("for.iter.header"))
        body_bb = fn.append_basic_block(name=self._fresh("for.iter.body"))
        err_bb = fn.append_basic_block(name=self._fresh("for.iter.err"))
        after_bb = fn.append_basic_block(name=self._fresh("for.iter.after"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        prev_err_block = getattr(self, "_try_err_block", None)
        self._try_err_block = err_bb
        item = self._emit_direct_method_call(
            next_fn, iterator, next_info, "__next__", (),
        )
        self._try_err_block = prev_err_block
        if item.type != target_ir_ty:
            item = self._coerce(item, target_ty, target_ty)
        self.builder.store(item, slot[0])
        self.builder.branch(body_bb)

        self.builder.position_at_end(body_bb)
        self.loop_stack.append((header_bb, after_bb))
        self._emit_stmts(stmt.body)
        self.loop_stack.pop()
        if not self._builder_block_is_terminated():
            self.builder.branch(header_bb)

        self.builder.position_at_end(err_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"], [],
            name=self._fresh("for.iter.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, self._BUILTIN_EXC_TAG["StopIteration"])],
            name=self._fresh("for.iter.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("for.iter.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=", match_i64, ir.Constant(_I64, 0),
            name=self._fresh("for.iter.stop_i1"),
        )
        clear_bb = fn.append_basic_block(name=self._fresh("for.iter.clear"))
        propagate_bb = fn.append_basic_block(
            name=self._fresh("for.iter.propagate")
        )
        self.builder.cbranch(is_stop, clear_bb, propagate_bb)
        self.builder.position_at_end(clear_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.branch(after_bb)
        self.builder.position_at_end(propagate_bb)
        outer = prev_err_block or self._ensure_fn_err_exit()
        self.builder.branch(outer)

        self.builder.position_at_end(after_bb)

    def _emit_for(self, stmt: For) -> None:
        if stmt.else_body:
            # Desugar for-else into a flag-guarded post-loop if:
            #
            #   for <t> in <iter>:
            #       <body>
            #   else:
            #       <else_body>
            #
            # becomes
            #
            #   __forelse_broke__<k> = False
            #   for <t> in <iter>:
            #       <body>  # any ``break`` in body also sets broke=True
            #   if not __forelse_broke__<k>:
            #       <else_body>
            #
            # Every ``break`` stmt inside ``<body>`` gets rewritten to
            # ``<broke> = True; break``. We don't descend into nested
            # For/While because those have their own iteration scope;
            # a break in a nested loop breaks the inner, not outer.
            from dataclasses import replace as _replace
            broke_name = self._fresh("forelse_broke")
            span = stmt.span
            broke_lit_false = BoolLit(span=span, ty=BoolType(name="bool"), value=False)
            broke_lit_true = BoolLit(span=span, ty=BoolType(name="bool"), value=True)
            broke_ref = Name(
                span=span, ty=BoolType(name="bool"), ident=broke_name,
            )
            set_broke_true = Assign(
                span=span,
                targets=(broke_ref,),
                value=broke_lit_true,
                annotation=BoolType(name="bool"),
            )

            def tag_breaks(stmts):
                out = []
                for s in stmts:
                    if isinstance(s, Break):
                        out.append(set_broke_true)
                        out.append(s)
                        continue
                    if isinstance(s, If):
                        out.append(_replace(
                            s,
                            body=tag_breaks(s.body),
                            else_body=tag_breaks(s.else_body),
                        ))
                        continue
                    if isinstance(s, Try):
                        new_handlers = tuple(
                            _replace(h, body=tag_breaks(h.body))
                            for h in s.handlers
                        )
                        out.append(_replace(
                            s,
                            body=tag_breaks(s.body),
                            else_body=tag_breaks(s.else_body),
                            finally_body=tag_breaks(s.finally_body),
                            handlers=new_handlers,
                        ))
                        continue
                    out.append(s)
                return tuple(out)

            # Initialise the broke flag in the enclosing scope.
            ir_ty = self._map_type(BoolType(name="bool"))
            alloca = self._alloca_in_entry(
                ir_ty, name=f"{broke_name}.addr",
            )
            self.builder.store(ir.Constant(ir_ty, 0), alloca)
            self.env[broke_name] = (alloca, ir_ty, BoolType(name="bool"))

            new_stmt = _replace(
                stmt,
                body=tag_breaks(stmt.body),
                else_body=(),
            )
            self._emit_for(new_stmt)
            # Emit the post-loop ``if not broke:`` guard.
            post_if = If(
                span=span,
                cond=Compare(
                    span=span, ty=BoolType(name="bool"),
                    op="==",
                    lhs=broke_ref,
                    rhs=broke_lit_false,
                ),
                body=tuple(stmt.else_body),
                else_body=(),
            )
            self._emit_if(post_if)
            return
        # ``for (i, x) in enumerate(xs):`` — desugar to an indexed
        # iteration so the rest of this function never sees
        # ``enumerate`` as a special iter form.
        if self._for_iter_is_enumerate(stmt):
            stmt = self._normalise_for_enumerate(stmt)
        # ``for (a, b, ...) in zip(xs, ys, ...):`` — desugar to indexed
        # iteration over the shortest-length iterable. The strict=True
        # kwarg is accepted and dropped (pcc doesn't yet raise on
        # length mismatch, but CPython-matching min-length is close
        # enough for stdlib-style usage).
        if self._for_iter_is_zip(stmt):
            stmt = self._normalise_for_zip(stmt)
        # ``for (a, b) in items:`` — normalise by introducing a fresh
        # scalar target and prepending an unpack assign to the loop body.
        if isinstance(stmt.target, TupleExpr):
            stmt = self._normalise_for_tuple_target(stmt)
        if not isinstance(stmt.target, Name):
            raise NotImplementedError(
                "Layer 1 for-loop target must be a plain Name or a "
                "TupleExpr of Names"
            )
        # ``for <name> in range(...)`` stays on the L1 fast path.
        is_range_call = (
            isinstance(stmt.iter, Call)
            and isinstance(stmt.iter.func, Name)
            and stmt.iter.func.ident in ("range", "xrange")
        )
        if not is_range_call:
            # CPython iterable? Use PyObject_GetIter + PyIter_Next.
            iter_val = self._emit_expr(stmt.iter)
            if iter_val in getattr(self, "_cpy_values", ()):
                return self._emit_for_cpython_iter(stmt, iter_val)
            # ListType / TupleType iteration via index: length from
            # ``py_{list,tuple}_len``, element via ``py_{list,tuple}_get``.
            iter_ty = stmt.iter.ty
            if isinstance(iter_ty, (ListType, TupleType)):
                return self._emit_for_list_index(
                    stmt, iter_val, iter_ty,
                )
            # DictType: ``for k in d:`` iterates keys. Materialise
            # ``py_dict_keys(d)`` (returns a list) and reuse the
            # list-index loop with the key type.
            if isinstance(iter_ty, DictType):
                keys_val = self.builder.call(
                    self.runtime["py_dict_keys"], [iter_val],
                    name=self._fresh("for.dict.keys"),
                )
                synthetic_ty = ListType(name="list", elem=iter_ty.key)
                return self._emit_for_list_index(
                    stmt, keys_val, synthetic_ty,
                )
            # StrType: ``for ch in s:`` iterates codepoints. Slice each
            # index into a 1-char str — keeps the whole loop libpython-
            # free. The bound target is typed str.
            if isinstance(iter_ty, StrType):
                return self._emit_for_str_chars(stmt, iter_val)
            class_hint = self._class_hint_for_expr(stmt.iter)
            if (
                class_hint is not None
                and self._resolve_method_mro(class_hint, "__next__") is not None
            ):
                return self._emit_for_native_iterator(
                    stmt, iter_val, class_hint,
                )
            # DynType: fall back to ``py_obj_len`` + ``py_obj_getitem``
            # — works for any pcc-native sequence (list, tuple, dict
            # keys, etc.) and stays libpython-free. The bound target is
            # tagged DynType, so subsequent uses see a PyObject*.
            if isinstance(iter_ty, DynType):
                return self._emit_for_obj_index(stmt, iter_val)
            raise NotImplementedError(
                "Layer 1 only handles 'for <name> in range(...)', a "
                "CPython-backed iterable, a list/tuple/dict/dyn "
                "container; other iterables need L3"
            )
        call = stmt.iter
        if call.kwargs:
            raise NotImplementedError("Layer 1 range() has no keyword args")
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(
                f"range() takes 1–3 args; got {len(call.args)}"
            )

        # Allocate the loop variable. In normal Python-int mode the range
        # counter stays native i64 as the hot-loop fast path, while the
        # user-visible target is refreshed with a tagged int object on each
        # iteration.
        target_name = stmt.target.ident
        boxed_range_target = self._int_exprs_are_boxed()
        if boxed_range_target:
            counter_alloca = self._alloca_in_entry(
                _I64, name=f"{target_name}.range.addr",
            )
            existing = self.env.get(target_name)
            if existing is None or not isinstance(existing[1], ir.PointerType):
                target_alloca = self._alloca_in_entry(
                    _CSTR, name=f"{target_name}.addr",
                )
                self.env[target_name] = (
                    target_alloca, _CSTR, IntType(name="int"),
                )
            else:
                target_alloca = existing[0]
        else:
            existing = self.env.get(target_name)
            if existing is None:
                counter_alloca = self._alloca_in_entry(
                    _I64, name=f"{target_name}.addr",
                )
                self.env[target_name] = (
                    counter_alloca, _I64, IntType(name="int"),
                )
            else:
                counter_alloca, ir_ty, _decl = existing
                if ir_ty is not _I64:
                    if target_name == "_":
                        # ``for _ in ...`` is a discard idiom; if an earlier
                        # binding for ``_`` was non-int (e.g. bound as ptr by
                        # a prior ``for _ in <list>`` or ``_, x = tuple``),
                        # allocate a fresh slot rather than error. The two
                        # loops are semantically independent; ``_`` is not
                        # read across them.
                        counter_alloca = self._alloca_in_entry(
                            _I64, name=f"{target_name}.addr",
                        )
                        self.env[target_name] = (
                            counter_alloca, _I64, IntType(name="int"),
                        )
                    else:
                        raise L1CodegenError(
                            f"for-range target {target_name!r} already bound "
                            f"with type {ir_ty}, expected i64"
                        )
        self.builder.store(start_val, counter_alloca)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("for.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("for.body"))
        step_bb = fn.append_basic_block(name=self._fresh("for.step"))
        end_bb = fn.append_basic_block(name=self._fresh("for.end"))

        # Hoist step as a stable SSA value — we already have it in
        # ``step_val`` so no further work.
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(counter_alloca, name=self._fresh(target_name))
        # Condition depends on step sign: positive step -> i<stop,
        # negative step -> i>stop. We emit both and select.
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(">", step_val, zero64,
                                              name=self._fresh("step_pos"))
        cond_pos = self.builder.icmp_signed("<", cur, stop_val,
                                              name=self._fresh("fwd_cmp"))
        cond_neg = self.builder.icmp_signed(">", cur, stop_val,
                                              name=self._fresh("bwd_cmp"))
        cond_i1 = self.builder.select(step_pos, cond_pos, cond_neg,
                                        name=self._fresh("for_cond"))
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.loop_stack.append((step_bb, end_bb))
        self.builder.position_at_end(body_bb)
        if boxed_range_target:
            cur_body = self.builder.load(
                counter_alloca, name=self._fresh(f"{target_name}.body"),
            )
            cur_obj = self.builder.call(
                self.runtime["py_int_from_i64"], [cur_body],
                name=self._fresh("range.int.obj"),
            )
            self.builder.store(cur_obj, target_alloca)
            self._exact_int_env_flags[target_name] = True
        self._emit_stmts(stmt.body)
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.loop_stack.pop()

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(counter_alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, counter_alloca)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_expr_as_i64(self, expr: Expr) -> ir.Value:
        """Emit an expression and coerce the result to ``i64``.

        Accepts native int/bool (fast path) and object-typed integers
        (via ``py_int_to_i64``, for e.g. a ``dict`` value that was typed
        as int but materialised as PyObject*).
        """
        if isinstance(expr, IntLit):
            return ir.Constant(_I64, int(expr.value))
        if isinstance(expr, BoolLit):
            return ir.Constant(_I64, 1 if expr.value else 0)
        value = self._emit_expr(expr)
        if isinstance(expr.ty, IntType):
            if value.type is _I64:
                return value
            if isinstance(value.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, value, expr.ty
                )
            return self.builder.sext(value, _I64, name=self._fresh("sext64"))
        if isinstance(expr.ty, BoolType):
            if value.type is _I1:
                return self.builder.zext(value, _I64, name=self._fresh("b2i"))
            if isinstance(value.type, ir.PointerType):
                i = marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, value,
                    IntType(name="int"),
                )
                return i
            return self.builder.zext(value, _I64, name=self._fresh("b2i"))
        if isinstance(expr.ty, FloatType):
            return self.builder.fptosi(value, _I64, name=self._fresh("f2i"))
        if isinstance(expr.ty, DynType) or self._is_object(expr.ty):
            # Go through the runtime.
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, value, expr.ty
            )
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime, boxed,
                IntType(name="int"),
            )
        raise NotImplementedError(
            f"Layer 1 cannot reduce {type(expr.ty).__name__} to i64"
        )

    # ------------------------------------------------------- expressions

    def _emit_expr(self, expr: Expr) -> ir.Value:
        if isinstance(expr, IntLit):
            if self._int_exprs_are_boxed():
                return self._emit_int_literal_object(int(expr.value))
            return ir.Constant(_I64, int(expr.value))
        if isinstance(expr, FloatLit):
            return ir.Constant(_DOUBLE, _as_native_float(expr.value))
        if isinstance(expr, BoolLit):
            return ir.Constant(_I1, 1 if expr.value else 0)
        if isinstance(expr, NoneLit):
            return self._emit_none_literal()
        if isinstance(expr, StrLit):
            return self._emit_str_literal(expr.value)
        if isinstance(expr, ListExpr):
            return self._emit_list_literal(expr)
        if isinstance(expr, DictExpr):
            return self._emit_dict_literal(expr)
        if isinstance(expr, TupleExpr):
            return self._emit_tuple_literal(expr)
        if isinstance(expr, Name):
            return self._emit_name(expr)
        if isinstance(expr, Subscript):
            return self._emit_subscript_load(expr)
        if isinstance(expr, Attr):
            return self._emit_attr(expr)
        if isinstance(expr, BinOp):
            # Class-based arithmetic dunder fast path: ``a + b`` on a
            # hinted class with ``__add__`` dispatches there before
            # falling back to numeric coercion. Mirrors the compare
            # path in ``_emit_compare``.
            arith_dunder = {
                "+": "__add__",
                "-": "__sub__",
                "*": "__mul__",
                "/": "__truediv__",
                "//": "__floordiv__",
                "%": "__mod__",
            }.get(expr.op)
            if arith_dunder is not None:
                dunder = self._try_dispatch_dunder_unary(
                    expr.lhs, arith_dunder, (expr.rhs,)
                )
                if dunder is not None:
                    return dunder
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            return self._emit_binop_value(
                expr.op, lhs, expr.lhs.ty, rhs, expr.rhs.ty, result_ty=expr.ty
            )
        if isinstance(expr, UnaryOp):
            return self._emit_unary(expr)
        if isinstance(expr, Compare):
            return self._emit_compare(expr)
        if isinstance(expr, BoolExpr):
            return self._emit_boolexpr(expr)
        if isinstance(expr, Call):
            return self._emit_call(expr)
        if isinstance(expr, IfExpr):
            return self._emit_if_expr(expr)
        # Simple lambda → CPython ``operator`` callable. Covers the
        # common ``sorted(xs, key=lambda x: x.attr)`` and
        # ``sorted(xs, key=lambda x: x[i])`` idioms that dominate
        # pcc's own source (method / subscript getters used as sort
        # keys).
        from ..py_ast import Lambda as _Lambda
        if isinstance(expr, _Lambda):
            simple = self._maybe_emit_simple_lambda(expr)
            if simple is not None:
                return simple
            # Fall back to the general lambda-wrap path: hoist the
            # lambda body into a dedicated pcc FuncDef and wrap the
            # function pointer as a CPython PyCFunction via
            # ``py_cpy_wrap_pcc_1arg``.
            wrapped = self._maybe_emit_lambda_wrap(expr)
            if wrapped is not None:
                return wrapped
        raise NotImplementedError(
            f"Layer 1 does not handle expression {type(expr).__name__}"
        )

    def _emit_hoist_adapter(
        self, orig_name: str, full_fn: ir.Function, entry: dict,
    ) -> ir.Function:
        """Synthesize an adapter ir.Function for a hoisted nested def
        with captures, matching the ORIGINAL arity. The adapter:

        1. Reads each capture from an internal global (populated in
           the outer scope at wrap time via ``_emit_hoist_adapter_caps``).
        2. Calls the full hoisted function with
           ``(original_args..., cap_0, cap_1, ...)``.
        3. Returns the result.

        Stores the capture-globals dict in ``entry`` so the outer
        scope's wrap-site can store into the same globals. Caller is
        the wrap site; it invokes this method which is idempotent
        (re-using the adapter on subsequent references)."""
        cached = entry.get("adapter_ir")
        if cached is not None:
            # Still need to populate capture globals from the outer
            # scope. Emit the stores at the CURRENT builder position.
            for fv in entry["free_names"]:
                gv = entry["capture_globals"][fv]
                cpy_val = self._capture_value_as_cpython(fv)
                if cpy_val is None:
                    continue
                self.builder.store(cpy_val, gv)
            return cached

        arity = entry["original_arity"]
        free_names = entry["free_names"]
        adapter_name = (
            f"user_{(self.ast_module.name or 'mod').replace('.', '_')}"
            f"_{orig_name}_adapter"
        )
        fnty = ir.FunctionType(_CSTR, [_CSTR] * arity)
        existing = self.module.globals.get(adapter_name)
        if isinstance(existing, ir.Function):
            adapter_ir = existing
        else:
            adapter_ir = ir.Function(self.module, fnty, name=adapter_name)
            adapter_ir.linkage = "internal"

        # Create a capture-global per free var (if not already).
        capture_globals: dict = {}
        for fv in free_names:
            gv_name = f".hoist_cap_{orig_name}_{fv}"
            gv = self.module.globals.get(gv_name)
            if gv is None:
                gv = ir.GlobalVariable(self.module, _CSTR, name=gv_name)
                gv.linkage = "internal"
                gv.initializer = ir.Constant(_CSTR, None)
            capture_globals[fv] = gv
        entry["capture_globals"] = capture_globals

        # Emit the adapter body. Save outer builder state.
        saved_builder = self.builder
        entry_block = adapter_ir.append_basic_block(name="entry")
        tmp_builder = ir.IRBuilder(entry_block)

        # Load each capture from its global.
        cap_vals = []
        for fv in free_names:
            gv = capture_globals[fv]
            v = tmp_builder.load(gv, name=f"{fv}.cap")
            cap_vals.append(v)
        # Call the full hoisted function with original args + captures.
        all_args = list(adapter_ir.args) + cap_vals
        ret_ty = full_fn.function_type.return_type
        if isinstance(ret_ty, ir.PointerType):
            result = tmp_builder.call(full_fn, all_args, name="result")
            tmp_builder.ret(result)
        elif isinstance(ret_ty, ir.VoidType):
            tmp_builder.call(full_fn, all_args)
            py_none_gv = declare_runtime_global(self.module, "py_None")
            tmp_builder.ret(tmp_builder.load(py_none_gv, name="none"))
        elif isinstance(ret_ty, ir.IntType) and ret_ty.width == 1:
            raw = tmp_builder.call(full_fn, all_args, name="raw")
            bit = tmp_builder.zext(raw, _I32, name="b2i32")
            boxed = tmp_builder.call(
                self.runtime["py_bool_from_bit"], [bit], name="boxed",
            )
            tmp_builder.ret(boxed)
        elif isinstance(ret_ty, ir.IntType) and ret_ty.width == 64:
            raw = tmp_builder.call(full_fn, all_args, name="raw")
            boxed = tmp_builder.call(
                self.runtime["py_int_from_i64"], [raw], name="boxed",
            )
            tmp_builder.ret(boxed)
        else:
            raise NotImplementedError(
                f"capture adapter for return type {ret_ty} not supported"
            )

        # Restore the outer builder.
        self.builder = saved_builder
        entry["adapter_ir"] = adapter_ir

        # Store outer-scope capture values into the globals before the
        # adapter is handed to ``py_cpy_wrap_pcc_Narg``. If a capture
        # isn't in env (e.g. the capture is a top-level user function
        # reference), skip the store — the initializer None will leave
        # it unset, which is wrong but matches the existing hoist's
        # fall-through case.
        for fv in free_names:
            gv = capture_globals[fv]
            cpy_val = self._capture_value_as_cpython(fv)
            if cpy_val is None:
                continue
            self.builder.store(cpy_val, gv)

        return adapter_ir

    def _capture_value_as_cpython(self, name: str) -> Optional[ir.Value]:
        """Load ``name`` from the current outer scope as a CPython object.

        Captured values already tagged as CPython must stay in that object
        space; re-marshalling them through ``py_cpy_from_pcc_obj`` treats
        the CPython heap pointer as a pcc runtime object and can corrupt
        closure-wrapped lambdas / hoisted helpers.
        """
        def _native_capture_type(ty: Type) -> bool:
            return isinstance(
                ty,
                (
                    IntType,
                    BoolType,
                    FloatType,
                    StrType,
                    NoneType,
                    ListType,
                    DictType,
                    TupleType,
                ),
            )

        if name in self.env:
            slot, _ir_ty, ty = self.env[name]
            val = self.builder.load(slot, name=self._fresh(f"{name}.outer"))
            if (
                getattr(self, "_cpy_env_flags", {}).get(name, False)
                and isinstance(val.type, ir.PointerType)
                and not _native_capture_type(ty)
            ):
                self.builder.call(self.runtime["py_cpy_incref"], [val])
                return val
            cpy_val, _ = self._marshal_to_cpython(val, ty)
            return cpy_val

        cap_span = None
        cur = getattr(self, "current_func_def", None)
        if cur is not None:
            cap_span = cur.span
        elif getattr(self.ast_module, "body", ()):
            cap_span = self.ast_module.body[0].span
        else:
            cap_span = SourceSpan(
                file=self.ast_module.name or "<generated>",
                line=1,
                col=1,
                end_line=1,
                end_col=1,
            )
        cap_name = Name(
            span=cap_span,
            ty=DynType(name="dyn"),
            ident=name,
        )
        val = self._emit_name(cap_name)
        if (
            val in getattr(self, "_cpy_values", ())
            and isinstance(val.type, ir.PointerType)
        ):
            self.builder.call(self.runtime["py_cpy_incref"], [val])
            return val
        cpy_val, _ = self._marshal_to_cpython(val, cap_name.ty)
        return cpy_val

    def _maybe_emit_lambda_wrap(self, expr) -> Optional[ir.Value]:
        """Emit a 1/2/3-arg lambda as a pcc FuncDef and wrap its
        function pointer as a CPython PyCFunction callable.

        Supports lambdas with no free variables (beyond builtins /
        module-level symbols). Each parameter is tagged as a CPython
        value, so attribute / method / subscript operations inside
        the body go through the CPython dispatch.
        Returns the CPython callable SSA value, or ``None`` if the
        lambda has >3 params, has free vars, or the body type isn't
        marshallable back to PyObject*."""
        arity = len(expr.params)
        if arity > 3:
            return None
        param_names = [p.name for p in expr.params]
        if any(n == "" for n in param_names):
            return None
        # Reject free vars beyond builtins / module globals. We don't
        # yet thread captures through the trampoline.
        builtins_ns = _PY_BUILTINS_NS
        module_names = set(
            getattr(self, "_module_globals", {}).keys()
        )
        module_names.update(self.functions.keys())
        module_names.update(getattr(self, "_hoist_wrap_caps", {}).keys())
        if hasattr(self, "class_lowering"):
            module_names.update(self.class_lowering.classes.keys())
        module_names.update(
            getattr(self, "_cpy_module_env", {}).keys()
        )
        from ..py_ast import Lambda as _Lambda

        def collect_free_vars(e, bound, acc):
            if isinstance(e, _Lambda):
                nested_bound = set(bound)
                for p in getattr(e, "params", ()) or ():
                    pname = getattr(p, "name", "")
                    if pname:
                        nested_bound.add(pname)
                collect_free_vars(e.body, nested_bound, acc)
                return
            if isinstance(e, Name):
                if (
                    e.ident not in bound
                    and e.ident not in builtins_ns
                    and e.ident not in module_names
                    and e.ident not in (
                        "True", "False", "None", "...",
                        "*", "__starred__", "**",
                    )
                ):
                    acc.add(e.ident)
                return
            if isinstance(e, tuple):
                # Inner tuples (e.g. ``Call.kwargs = ((name, Expr), ...)``)
                # — recurse into each element.
                for it in e:
                    collect_free_vars(it, bound, acc)
                return
            for slot in _dataclass_field_names(e):
                if slot in ("span", "ty"):
                    continue
                v = _dataclass_field_value(e, slot, None)
                if isinstance(v, tuple):
                    for it in v:
                        collect_free_vars(it, bound, acc)
                elif v is not None and _dataclass_field_names(v):
                    collect_free_vars(v, bound, acc)

        free_vars: set = set()
        collect_free_vars(expr.body, set(param_names), free_vars)
        # Each free var must resolve in the current scope (present in
        # ``self.env`` or via the value-position name resolver). If
        # any doesn't, bail.
        for fv in free_vars:
            if fv not in self.env and fv not in module_names:
                direct_hoist = f"__nested_{fv}"
                if direct_hoist in self.functions:
                    continue
                if any(
                    name.startswith(f"{direct_hoist}_")
                    for name in self.functions
                ):
                    continue
                return None

        # Build a new top-level ir.Function with (ptr, ...) -> ptr ABI
        # where the number of ptr params matches the lambda arity.
        # Save IRBuilder state so we can emit the body in isolation.
        sym_base = f"__lambda_{len(getattr(self, '_lambda_counter', []))}"
        if not hasattr(self, "_lambda_counter"):
            self._lambda_counter = []
        self._lambda_counter.append(sym_base)
        fn_name = f"user_{(self.ast_module.name or 'mod').replace('.', '_')}_{sym_base}"
        fnty = ir.FunctionType(_CSTR, [_CSTR] * arity)
        # Reuse an existing declaration if we've already laid this
        # lambda down (shouldn't happen in practice with the counter
        # but belt-and-braces).
        existing = self.module.globals.get(fn_name)
        if isinstance(existing, ir.Function):
            fn_ir = existing
        else:
            fn_ir = ir.Function(self.module, fnty, name=fn_name)
            fn_ir.linkage = "internal"

        # Snapshot outer state. ``env`` + ``builder`` + function fields
        # are the only things we need to swap — the module globals,
        # class_lowering, runtime map, and module itself are shared
        # across functions so they stay put.
        saved_builder = self.builder
        saved_env = self.env
        saved_env_class_hint = getattr(self, "env_class_hint", {})
        saved_current_fn = self.current_function
        saved_current_fd = self.current_func_def
        saved_loops = getattr(self, "loop_stack", [])

        entry = fn_ir.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(entry)
        self.current_function = fn_ir
        self.current_func_def = None
        self.env = {}
        self.env_class_hint = {}
        self.loop_stack = []

        # Allocate a slot per lambda param, store each incoming arg,
        # tag both the stored value and subsequent loads as CPython —
        # the trampoline hands us CPython PyObject*s, so every
        # attr / method dispatch inside the body must take the CPython
        # path.
        if not hasattr(self, "_cpy_env_flags"):
            self._cpy_env_flags = {}
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        for ir_arg, pname in zip(fn_ir.args, param_names):
            slot = self.builder.alloca(_CSTR, name=f"{pname}.addr")
            self.builder.store(ir_arg, slot)
            self.env[pname] = (slot, _CSTR, DynType(name="dyn"))
            self._cpy_env_flags[pname] = True

        # Create one internal global per free var. The lambda body
        # reads the capture through a load of this global. The wrap-
        # site (in the outer body) stores the captured value into the
        # global before producing the wrapped callable. This gives
        # correct one-shot closure semantics for ``sorted(xs, key=<fn>)``
        # patterns where the lambda is used immediately and not
        # retained. Multi-shot retention with differing captures
        # would need per-capsule state which is TODO.
        capture_globals: dict = {}
        for fv in sorted(free_vars):
            gv_name = f".lambda_capture_{sym_base}_{fv}"
            gv = ir.GlobalVariable(self.module, _CSTR, name=gv_name)
            gv.linkage = "internal"
            gv.initializer = ir.Constant(_CSTR, None)
            capture_globals[fv] = gv
            # Expose via env so reads resolve. Store type is DynType
            # and tag as CPython so attr / method dispatch uses
            # py_cpy_*.
            cap_slot = self.builder.alloca(
                _CSTR, name=f"{fv}.cap.addr",
            )
            loaded = self.builder.load(gv, name=self._fresh(f"{fv}.cap"))
            self.builder.store(loaded, cap_slot)
            self.env[fv] = (cap_slot, _CSTR, DynType(name="dyn"))
            self._cpy_env_flags[fv] = True

        # Evaluate the body expression. Any unsupported shape bubbles
        # up as NotImplementedError — callers see the original error
        # rather than a silent bad wrapper.
        try:
            body_val = self._emit_expr(expr.body)
            # Marshal the result back to a CPython PyObject*. Body
            # values that are already CPython-tagged (e.g. from
            # ``x.attr`` via py_cpy_getattr) pass through; scalar
            # (i64 / double / i1) values get boxed and re-converted.
            if isinstance(getattr(body_val, "type", None), ir.VoidType):
                # A lambda body can call a side-effecting helper that
                # returns ``None``. Codegen may lower that call to a
                # ``void`` SSA value even when type-infer left the
                # expression as DynType; the trampoline still needs to
                # hand CPython a real ``None`` object.
                body_val = self._emit_none_literal()
                cpy_val, _owned = self._marshal_to_cpython(
                    body_val, NoneType(name="None"),
                )
            else:
                cpy_val, _owned = self._marshal_to_cpython(
                    body_val, expr.body.ty,
                )
            self.builder.ret(cpy_val)
        except NotImplementedError:
            # Restore outer state and drop this synthesized function
            # (ir.Function stays declared but will be unused — the
            # linker discards internal symbols with no callers).
            self.builder = saved_builder
            self.env = saved_env
            self.env_class_hint = saved_env_class_hint
            self.current_function = saved_current_fn
            self.current_func_def = saved_current_fd
            self.loop_stack = saved_loops
            return None

        # Restore outer state now that the lambda body is fully emitted.
        self.builder = saved_builder
        self.env = saved_env
        self.env_class_hint = saved_env_class_hint
        self.current_function = saved_current_fn
        self.current_func_def = saved_current_fd
        self.loop_stack = saved_loops

        # Before wrapping, store each captured outer-scope value into
        # its dedicated lambda-capture global. Next time the lambda
        # body runs it will see the value current at wrap time — good
        # enough for one-shot ``sorted(xs, key=<fn>)`` semantics.
        for fv, gv in capture_globals.items():
            cpy_val = self._capture_value_as_cpython(fv)
            if cpy_val is None:
                continue
            self.builder.store(cpy_val, gv)

        # Bitcast the function to ``ptr`` (i8*) and wrap via the
        # arity-matched runtime helper. Returns a CPython callable
        # we tag in ``_cpy_values``.
        wrap_helper = {
            0: "py_cpy_wrap_pcc_0arg",
            1: "py_cpy_wrap_pcc_1arg",
            2: "py_cpy_wrap_pcc_2arg",
            3: "py_cpy_wrap_pcc_3arg",
        }[arity]
        fn_ptr = self.builder.bitcast(
            fn_ir, _CSTR, name=self._fresh(f"{sym_base}.fnptr"),
        )
        result = self.builder.call(
            self.runtime[wrap_helper], [fn_ptr],
            name=self._fresh(f"cpy.{sym_base}"),
        )
        self._cpy_values.add(result)
        return result

    def _maybe_emit_simple_lambda(self, expr) -> Optional[ir.Value]:
        """Lower a restricted set of lambdas to ``operator.attrgetter``
        / ``operator.itemgetter`` / ``operator.methodcaller`` CPython
        callables.

        Supported shapes (single-param lambda with body):
        - ``lambda x: x.a`` / ``lambda x: x.a.b`` → attrgetter
        - ``lambda x: x[N]`` (integer literal) → itemgetter(N)
        - ``lambda x: x[S]`` (string literal) → itemgetter(S)
        - ``lambda x: x.method()`` (no-arg method) → methodcaller

        Returns the CPython callable SSA value, tagged in
        ``_cpy_values``, or ``None`` if the lambda doesn't match
        a simple shape (caller then raises NotImplementedError).
        """
        if len(expr.params) != 1 or expr.params[0].name == "":
            return None
        param = expr.params[0].name
        body = expr.body
        dotted = self._lambda_attr_chain(body, param)
        if dotted is not None:
            return self._emit_operator_getter(
                "attrgetter", dotted,
            )
        idx = self._lambda_simple_subscript(body, param)
        if idx is not None:
            return self._emit_operator_getter(
                "itemgetter", idx,
            )
        method = self._lambda_method_call(body, param)
        if method is not None:
            return self._emit_operator_getter(
                "methodcaller", method,
            )
        return None

    def _lambda_method_call(self, expr, param_name):
        """If ``expr`` is ``Name(param).method()`` (no-arg method
        call), return the method name. Else None."""
        if not isinstance(expr, Call):
            return None
        if not isinstance(expr.func, Attr):
            return None
        if expr.args or expr.kwargs:
            return None
        if not (
            isinstance(expr.func.obj, Name)
            and expr.func.obj.ident == param_name
        ):
            return None
        return expr.func.name

    def _lambda_attr_chain(self, expr, param_name):
        """If ``expr`` is ``Name(param)`` / ``Attr(Attr(Name(param)), ...)``,
        return the dotted attr chain (e.g. ``"a.b.c"``). Else None."""
        parts: list[str] = []
        cur = expr
        while isinstance(cur, Attr):
            parts.append(cur.name)
            cur = cur.obj
        if isinstance(cur, Name) and cur.ident == param_name and parts:
            return self._join_reversed_strs(parts)
        return None

    def _join_reversed_strs(self, parts: list[str]) -> str:
        rev: list[str] = []
        i = len(parts) - 1
        while i >= 0:
            rev.append(parts[i])
            i -= 1
        return ".".join(rev)

    def _tuple_elems_are_uniform(
        self, elems: tuple[Type, ...], first: Type,
    ) -> bool:
        i = 0
        while i < len(elems):
            if elems[i] != first:
                return False
            i += 1
        return True

    def _lambda_simple_subscript(self, expr, param_name):
        """If ``expr`` is ``Name(param)[IntLit|StrLit]``, return the
        Python literal value. Else None."""
        if not isinstance(expr, Subscript):
            return None
        if not (isinstance(expr.obj, Name) and expr.obj.ident == param_name):
            return None
        if isinstance(expr.idx, IntLit):
            return expr.idx.value
        if isinstance(expr.idx, StrLit):
            return expr.idx.value
        return None

    def _emit_operator_getter(self, getter_name: str, key) -> ir.Value:
        """Emit ``operator.<getter_name>(key)`` at the current builder
        position, returning the resulting CPython callable pointer.
        The result is registered in ``_cpy_values`` so downstream uses
        route through the CPython path."""
        mod_name_gv = self._cstr_global("operator", ".cpy.operator_modname")
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"],
            [self._ptr_to_cstr(mod_name_gv)],
            name=self._fresh("cpy.operator"),
        )
        attr_gv = self._cstr_global(
            getter_name, f".cpy.operator.{getter_name}",
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, self._ptr_to_cstr(attr_gv)],
            name=self._fresh(f"cpy.{getter_name}"),
        )
        # Marshal the key: str → CPython str, int → CPython int.
        if isinstance(key, int):
            key_cpy = self.builder.call(
                self.runtime["py_cpy_from_i64"],
                [ir.Constant(_I64, key)],
                name=self._fresh(f"{getter_name}.key.int"),
            )
        else:
            key_bytes = self._utf8_byte_values(key)
            key_gv = self._cstr_global(key, f".cpy.getter.key.{getter_name}")
            # Build CPython str from the C string. ``PyUnicode_FromString``
            # isn't exposed via the pcc ABI directly; use
            # ``py_str_new + py_cpy_from_pccstr`` as the bridge.
            pcc_str = self.builder.call(
                self.runtime["py_str_new"],
                [
                    self._ptr_to_cstr(key_gv),
                    ir.Constant(_I64, len(key_bytes)),
                ],
                name=self._fresh(f"{getter_name}.key.pccstr"),
            )
            key_cpy = self.builder.call(
                self.runtime["py_cpy_from_pccstr"], [pcc_str],
                name=self._fresh(f"{getter_name}.key.cpystr"),
            )
        result = self.builder.call(
            self.runtime["py_cpy_call1"], [fn_val, key_cpy],
            name=self._fresh(f"cpy.{getter_name}.call"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_if_expr(self, expr: IfExpr) -> ir.Value:
        """Lower ``then_e if cond else else_e`` into a diamond CFG +
        phi. Both arms are coerced to ``expr.ty`` so downstream uses
        see a consistent SSA value type."""
        result_ty = expr.ty
        cond_val = self._emit_expr(expr.cond)
        cond_b = self._truthy(cond_val, expr.cond.ty)

        fn = self.current_function
        then_bb = fn.append_basic_block(name=self._fresh("ternary_true"))
        else_bb = fn.append_basic_block(name=self._fresh("ternary_false"))
        join_bb = fn.append_basic_block(name=self._fresh("ternary_end"))
        self.builder.cbranch(cond_b, then_bb, else_bb)

        self.builder.position_at_end(then_bb)
        then_val = self._emit_expr(expr.then_e)
        then_val = self._coerce(then_val, expr.then_e.ty, result_ty)
        then_exit = self.builder._block

        self.builder.position_at_end(else_bb)
        else_val = self._emit_expr(expr.else_e)
        else_val = self._coerce(else_val, expr.else_e.ty, result_ty)
        else_exit = self.builder._block

        # Final phi-type alignment: if the result_ty is Dyn/object but
        # one arm still holds an i64 / i1 (inference sometimes leaves
        # a native-scalar arm when ``common_type`` picks the
        # non-scalar side but ``_coerce`` can't bridge int→ptr
        # directly), marshal the native arm through ``py_int_from_i64``
        # / ``py_bool_from_bit`` so the phi sees matching pointer
        # types on both edges.
        phi_ty = self._storage_ir_type(result_ty)
        if isinstance(phi_ty, ir.PointerType):
            if not isinstance(then_val.type, ir.PointerType):
                self.builder.position_at_end(then_exit)
                then_val = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    then_val, expr.then_e.ty,
                )
                then_exit = self.builder._block
            if not isinstance(else_val.type, ir.PointerType):
                self.builder.position_at_end(else_exit)
                else_val = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    else_val, expr.else_e.ty,
                )
                else_exit = self.builder._block

        self.builder.position_at_end(then_exit)
        self.builder.branch(join_bb)
        self.builder.position_at_end(else_exit)
        self.builder.branch(join_bb)

        self.builder.position_at_end(join_bb)
        phi = self.builder.phi(phi_ty, name=self._fresh("ternary"))
        phi.add_incoming(then_val, then_exit)
        phi.add_incoming(else_val, else_exit)
        return phi

    # -- Collection literals ------------------------------------------

    def _emit_value_as_pcc_object_or_bridge(
        self, value: ir.Value, value_ty: Type, name_hint: str,
    ) -> ir.Value:
        if value in getattr(self, "_cpy_values", ()):
            bridged = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"], [value],
                name=self._fresh(name_hint),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [value])
            return bridged
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )

    def _emit_list_literal(self, expr: ListExpr) -> ir.Value:
        has_exact_int_boundary = any(
            self._int_expr_needs_exact_object_boundary(el)
            for el in expr.elems
        )
        if has_exact_int_boundary and not any(
            self._expr_looks_cpython(
                el.args[0]
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident == "*"
                    and len(el.args) == 1
                )
                else el
            )
            for el in expr.elems
        ):
            n_val = ir.Constant(_I64, len(expr.elems))
            lst = self.builder.call(
                self.runtime["py_list_new"], [n_val],
                name=self._fresh("list.new"),
            )
            for el in expr.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident == "*"
                    and len(el.args) == 1
                ):
                    inner = self._emit_expr(el.args[0])
                    self.builder.call(
                        self.runtime["py_list_extend"], [lst, inner],
                    )
                    continue
                v_obj = self._emit_expr_as_pcc_object(el)
                self.builder.call(
                    self.runtime["py_list_append"], [lst, v_obj],
                )
            return lst

        # Each element is either a normal value or ``*xs`` splat. We
        # collect (op_kind, ir_value, ast_ty, is_cpy) so the lowering
        # below can decide between native ``py_list_append/extend``
        # (with a per-element cpy→pcc bridge for any CPython-backed
        # values) and the legacy ``_emit_cpython_list_ops`` path used
        # only when the bridge can't apply (e.g. splat-of-cpy iterable
        # — bridging an iterable here would copy out every element
        # eagerly which the splat-extend path does not promise).
        ops: list[tuple[str, ir.Value, Type, bool]] = []
        cpy_values = getattr(self, "_cpy_values", ())
        cpy_extend = False
        for el in expr.elems:
            if (
                isinstance(el, Call)
                and isinstance(el.func, Name)
                and el.func.ident == "*"
                and len(el.args) == 1
            ):
                inner = self._emit_expr(el.args[0])
                is_cpy = inner in cpy_values
                ops.append(("extend", inner, el.args[0].ty, is_cpy))
                if is_cpy:
                    cpy_extend = True
                continue
            v = self._emit_expr(el)
            ops.append(("append", v, el.ty, v in cpy_values))
        if cpy_extend:
            return self._emit_cpython_list_ops(
                [(k, v, t) for (k, v, t, _) in ops]
            )
        n_val = ir.Constant(_I64, len(expr.elems))
        lst = self.builder.call(
            self.runtime["py_list_new"], [n_val],
            name=self._fresh("list.new"),
        )
        for op_kind, value, value_ty, is_cpy in ops:
            if op_kind == "extend":
                self.builder.call(
                    self.runtime["py_list_extend"], [lst, value],
                )
                continue
            v_obj = self._emit_value_as_pcc_object_or_bridge(
                value, value_ty, "cpy.list.elem" if is_cpy else "list.elem",
            )
            self.builder.call(
                self.runtime["py_list_append"], [lst, v_obj],
            )
        return lst

    def _emit_dict_literal(self, expr: DictExpr) -> ir.Value:
        has_exact_int_boundary = any(
            self._int_expr_needs_exact_object_boundary(k_expr)
            or self._int_expr_needs_exact_object_boundary(v_expr)
            for k_expr, v_expr in expr.pairs
        )
        if has_exact_int_boundary and not any(
            self._expr_looks_cpython(k_expr)
            or self._expr_looks_cpython(v_expr)
            for k_expr, v_expr in expr.pairs
        ):
            d = self.builder.call(
                self.runtime["py_dict_new"], [],
                name=self._fresh("dict.new"),
            )
            for k_expr, v_expr in expr.pairs:
                k_obj = self._emit_expr_as_pcc_object(k_expr)
                v_obj = self._emit_expr_as_pcc_object(v_expr)
                self.builder.call(
                    self.runtime["py_dict_set"], [d, k_obj, v_obj],
                )
            return d

        items: list[tuple[ir.Value, Type, bool, ir.Value, Type, bool]] = []
        has_cpy_key = False
        key_type_kinds: set[type] = set()
        value_type_kinds: set[type] = set()
        for k_expr, v_expr in expr.pairs:
            k = self._emit_expr(k_expr)
            v = self._emit_expr(v_expr)
            k_is_cpy = k in getattr(self, "_cpy_values", ())
            v_is_cpy = v in getattr(self, "_cpy_values", ())
            items.append((k, k_expr.ty, k_is_cpy, v, v_expr.ty, v_is_cpy))
            key_type_kinds.add(type(k_expr.ty))
            value_type_kinds.add(type(v_expr.ty))
            if k_is_cpy:
                has_cpy_key = True
        if has_cpy_key:
            return self._emit_cpython_dict_items(
                [(k, k_ty, v, v_ty) for (k, k_ty, _, v, v_ty, _) in items]
            )
        d = self.builder.call(
            self.runtime["py_dict_new"], [],
            name=self._fresh("dict.new"),
        )
        for k, k_ty, _k_is_cpy, v, v_ty, v_is_cpy in items:
            k_obj = self._emit_value_as_pcc_object_or_bridge(
                k, k_ty, "dict.key",
            )
            v_obj = self._emit_value_as_pcc_object_or_bridge(
                v, v_ty, "cpy.dict.val" if v_is_cpy else "dict.val",
            )
            self.builder.call(
                self.runtime["py_dict_set"], [d, k_obj, v_obj],
            )
        return d

    def _emit_tuple_literal(self, expr: TupleExpr) -> ir.Value:
        # Detect ``*iterable`` splats: build a dynamically-sized tuple
        # by materialising elements into a list first then converting
        # to a tuple through CPython. Slow path but correct for the
        # ``(a, b, *rest)`` pattern pcc self-host uses in a few spots.
        has_splat = any(
            isinstance(el, Call)
            and isinstance(el.func, Name)
            and el.func.ident in ("*", "__starred__")
            and len(el.args) == 1
            for el in expr.elems
        )
        if not has_splat:
            has_exact_int_boundary = any(
                self._int_expr_needs_exact_object_boundary(el)
                for el in expr.elems
            )
            if has_exact_int_boundary and not any(
                self._expr_looks_cpython(el) for el in expr.elems
            ):
                n = len(expr.elems)
                n_val = ir.Constant(_I64, n)
                tup = self.builder.call(
                    self.runtime["py_tuple_new"], [n_val],
                    name=self._fresh("tup.new"),
                )
                for i, el in enumerate(expr.elems):
                    v_obj = self._emit_expr_as_pcc_object(el)
                    idx = ir.Constant(_I64, i)
                    self.builder.call(
                        self.runtime["py_tuple_set_item"], [tup, idx, v_obj],
                    )
                return tup

            ops: list[tuple[str, ir.Value, Type, bool]] = []
            for el in expr.elems:
                v = self._emit_expr(el)
                ops.append(
                    ("append", v, el.ty, v in getattr(self, "_cpy_values", ()))
                )
            n = len(ops)
            n_val = ir.Constant(_I64, n)
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tup.new"),
            )
            for i, (_op_kind, value, value_ty, is_cpy) in enumerate(ops):
                v_obj = self._emit_value_as_pcc_object_or_bridge(
                    value, value_ty, "cpy.tup.elem" if is_cpy else "tup.elem",
                )
                idx = ir.Constant(_I64, i)
                self.builder.call(
                    self.runtime["py_tuple_set_item"], [tup, idx, v_obj],
                )
            return tup
        if has_splat:
            ops: list[tuple[str, ir.Value, Type, bool]] = []
            cpy_extend = False
            for el in expr.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident in ("*", "__starred__")
                    and len(el.args) == 1
                ):
                    inner = self._emit_expr(el.args[0])
                    is_cpy = inner in getattr(self, "_cpy_values", ())
                    ops.append(("extend", inner, el.args[0].ty, is_cpy))
                    if is_cpy:
                        cpy_extend = True
                    continue
                v = self._emit_expr(el)
                ops.append(
                    ("append", v, el.ty, v in getattr(self, "_cpy_values", ()))
                )
            if cpy_extend:
                return self._emit_cpython_tuple_ops(
                    [(kind, value, ty) for kind, value, ty, _ in ops]
                )
            lst = self.builder.call(
                self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("tup.splat.list"),
            )
            for op_kind, value, value_ty, is_cpy in ops:
                if op_kind == "extend":
                    self.builder.call(
                        self.runtime["py_list_extend"], [lst, value],
                    )
                    continue
                v_obj = self._emit_value_as_pcc_object_or_bridge(
                    value,
                    value_ty,
                    "cpy.tup.splat.elem" if is_cpy else "tup.splat.elem",
                )
                self.builder.call(
                    self.runtime["py_list_append"], [lst, v_obj],
                )
            # Convert the list to a tuple. Use py_obj_len + copy loop
            # rather than a dedicated helper (none exists yet).
            n_val = self.builder.call(
                self.runtime["py_list_len"], [lst],
                name=self._fresh("tup.splat.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tup.splat.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tup.splat.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tup.sp.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tup.sp.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tup.sp.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tup.sp.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tup.sp.i"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("tup.sp.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"], [lst, cur],
                name=self._fresh("tup.sp.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("tup.sp.inc"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup

    def _emit_dict_builtin(self, expr: Call) -> ir.Value:
        """Lower ``dict()`` / ``dict(iterable_of_pairs)`` /
        ``dict(key=value, ...)``.

        The one-arg form covers the common ``dict(items)`` pattern by
        iterating the source container through ``py_obj_len`` +
        ``py_obj_getitem`` and inserting pair[0] / pair[1]."""
        if len(expr.args) > 1:
            raise NotImplementedError(
                f"dict() takes at most 1 positional arg at L2; got {len(expr.args)}"
            )
        out = self.builder.call(
            self.runtime["py_dict_new"], [],
            name=self._fresh("dict.new"),
        )
        if len(expr.args) == 0:
            for kw_name, kw_expr in expr.kwargs:
                key_obj = self._emit_str_literal(kw_name)
                raw_val = self._emit_expr(kw_expr)
                val_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    raw_val, kw_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_dict_set"], [out, key_obj, val_obj],
                )
            return out

        src_expr = expr.args[0]
        src_val = self._emit_expr(src_expr)
        src_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, src_val, src_expr.ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [src_obj],
            name=self._fresh("dict.src.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="dict.src.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("dict.from.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("dict.from.body"))
        step_bb = fn.append_basic_block(name=self._fresh("dict.from.step"))
        end_bb = fn.append_basic_block(name=self._fresh("dict.from.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("dict.src.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("dict.src.more"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("dict.src.idx.box"),
        )
        pair_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, idx_box],
            name=self._fresh("dict.src.pair"),
        )
        zero_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 0)],
            name=self._fresh("dict.pair.zero"),
        )
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 1)],
            name=self._fresh("dict.pair.one"),
        )
        key_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [pair_obj, zero_box],
            name=self._fresh("dict.pair.key"),
        )
        val_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [pair_obj, one_box],
            name=self._fresh("dict.pair.val"),
        )
        self.builder.call(
            self.runtime["py_dict_set"], [out, key_obj, val_obj],
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("dict.src.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("dict.src.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        for kw_name, kw_expr in expr.kwargs:
            key_obj = self._emit_str_literal(kw_name)
            raw_val = self._emit_expr(kw_expr)
            val_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                raw_val, kw_expr.ty,
            )
            self.builder.call(
                self.runtime["py_dict_set"], [out, key_obj, val_obj],
            )
        return out

    # -- Comprehensions -----------------------------------------------

    def _emit_comprehension(self, expr: Call, kind: str) -> ir.Value:
        """Lower list/set/dict comprehension sentinels into explicit
        loops over a freshly-allocated runtime container.

        The native parser lifts comprehensions to::

            _list_comp(elt,          _gen_clause(target, iter, (ifs,)), ...)
            _set_comp(elt,           _gen_clause(...), ...)
            _dict_comp(TupleExpr(k,v), _gen_clause(...), ...)

        while the CPython-AST path emits::

            __listcomp__(elt,
                         ((target, iter, (ifs,), is_async), ...))
            __setcomp__(elt,  ...)
            __dictcomp__(key, val,
                         ((target, iter, (ifs,), is_async), ...))

        Only single-generator, non-async forms with a plain ``Name``
        target are supported here.
        """
        if not isinstance(expr.func, Name):
            raise NotImplementedError("comprehension sentinel lost its name")
        sentinel = expr.func.ident
        is_native = not sentinel.startswith("__")

        # Extract the element expression + any auxiliary per-kind value.
        if kind == "dict":
            if is_native:
                if len(expr.args) < 2:
                    raise NotImplementedError(
                        "_dict_comp expects (TupleExpr(k,v), _gen_clause…)"
                    )
                elt = expr.args[0]
                if (
                    not isinstance(elt, TupleExpr)
                    or len(elt.elems) != 2
                ):
                    raise NotImplementedError(
                        "_dict_comp element must be TupleExpr(k, v)"
                    )
                key_expr, val_expr = elt.elems
                gen_args = expr.args[1:]
            else:  # __dictcomp__(key, val, ((...),))
                if len(expr.args) != 3:
                    raise NotImplementedError(
                        "__dictcomp__ expects (key, val, generators)"
                    )
                key_expr, val_expr, gens_tuple = expr.args
                gen_args = (gens_tuple,)
        else:
            if len(expr.args) < 2:
                raise NotImplementedError(
                    f"{sentinel} expects elt plus at least one generator"
                )
            elt_expr = expr.args[0]
            gen_args = expr.args[1:]

        # Decode generator clauses.
        def _native_gen(gen_call: Expr):
            if not (
                isinstance(gen_call, Call)
                and isinstance(gen_call.func, Name)
                and gen_call.func.ident == "_gen_clause"
                and len(gen_call.args) == 3
            ):
                return None
            target, iter_e, ifs_tuple = gen_call.args
            return target, iter_e, ifs_tuple, False

        def _cpy_gen(gen_tuple: Expr):
            if not (
                isinstance(gen_tuple, TupleExpr)
                and len(gen_tuple.elems) == 4
            ):
                return None
            target, iter_e, ifs_tuple, is_async = gen_tuple.elems
            async_flag = isinstance(is_async, BoolLit) and is_async.value
            return target, iter_e, ifs_tuple, async_flag

        generators: list = []
        if is_native:
            for g in gen_args:
                u = _native_gen(g)
                if u is None:
                    raise NotImplementedError(
                        f"malformed {sentinel} generator clause"
                    )
                generators.append(u)
        else:
            gens_tuple = gen_args[0]
            if not isinstance(gens_tuple, TupleExpr):
                raise NotImplementedError(
                    f"{sentinel} generators arg must be a TupleExpr"
                )
            for g in gens_tuple.elems:
                u = _cpy_gen(g)
                if u is None:
                    raise NotImplementedError(
                        f"malformed {sentinel} generator tuple"
                    )
                generators.append(u)

        for _, _, _, is_async in generators:
            if is_async:
                raise NotImplementedError(
                    "Layer 1 comprehensions are sync-only"
                )
        # Desugar tuple targets: ``for (a, b) in pairs`` becomes a fresh
        # scalar name + an unpack-assign that the inner body emits
        # before its own work. Stash the unpacks per-generator so the
        # innermost body in the chain below sees them at the right
        # nesting level.
        tuple_unpacks: list = []
        desugared = []
        for target, iter_e, ifs_tuple, is_async in generators:
            if isinstance(target, TupleExpr):
                tmp_name = self._fresh("comp_pair")
                # The temp carries the iter's *element* type so the
                # tuple-unpack runtime branch picks the right shape.
                elem_ty = DynType(name="dyn")
                if isinstance(iter_e.ty, ListType):
                    elem_ty = iter_e.ty.elem
                elif isinstance(iter_e.ty, TupleType) and iter_e.ty.elems:
                    first = iter_e.ty.elems[0]
                    if all(type(e) is type(first) and e == first
                           for e in iter_e.ty.elems):
                        elem_ty = first
                tmp_ref = Name(
                    span=target.span, ty=elem_ty, ident=tmp_name,
                )
                unpack_stmt = Assign(
                    span=target.span,
                    targets=(target,),
                    value=tmp_ref,
                    annotation=None,
                )
                desugared.append(
                    (tmp_ref, iter_e, ifs_tuple, is_async)
                )
                tuple_unpacks.append(unpack_stmt)
            elif isinstance(target, Name):
                desugared.append((target, iter_e, ifs_tuple, is_async))
                tuple_unpacks.append(None)
            else:
                raise NotImplementedError(
                    "Layer 1 comprehension target must be a Name or "
                    "TupleExpr"
                )
        generators = desugared

        # Allocate result container.
        if kind == "list":
            container = self.builder.call(
                self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("listcomp"),
            )
        elif kind == "set":
            container = self.builder.call(
                self.runtime["py_set_new"], [],
                name=self._fresh("setcomp"),
            )
        elif kind == "dict":
            container = self.builder.call(
                self.runtime["py_dict_new"], [],
                name=self._fresh("dictcomp"),
            )
        else:
            raise NotImplementedError(
                f"comprehension kind {kind!r} not supported"
            )

        self._emit_comprehension_level(
            kind, container, generators, tuple_unpacks, 0,
            elt_expr if kind != "dict" else None,
            key_expr if kind == "dict" else None,
            val_expr if kind == "dict" else None,
        )
        return container

    def _emit_comprehension_innermost(
        self, kind: str, container: ir.Value, elt_expr, key_expr, val_expr,
    ) -> None:
        if kind == "dict":
            k = self._emit_expr(key_expr)
            v = self._emit_expr(val_expr)
            k_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, k, key_expr.ty,
            )
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, val_expr.ty,
            )
            self.builder.call(
                self.runtime["py_dict_set"], [container, k_obj, v_obj],
            )
            return
        v = self._emit_expr(elt_expr)
        v_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, elt_expr.ty,
        )
        fn_name = "py_list_append" if kind == "list" else "py_set_add"
        self.builder.call(self.runtime[fn_name], [container, v_obj])

    def _emit_comprehension_level(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        if idx >= len(generators):
            self._emit_comprehension_innermost(
                kind, container, elt_expr, key_expr, val_expr,
            )
            return
        self._emit_comprehension_generator(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )

    def _emit_comprehension_after_bind(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        _target, _iter_e, ifs_tuple, _is_async = generators[idx]
        if_exprs: tuple = ()
        if isinstance(ifs_tuple, TupleExpr):
            if_exprs = ifs_tuple.elems
        unpack_stmt = None
        if idx < len(tuple_unpacks):
            unpack_stmt = tuple_unpacks[idx]
        if unpack_stmt is not None:
            self._emit_assign(unpack_stmt)

        if_exits: list = []
        for cond_expr in if_exprs:
            cond_val = self._emit_expr(cond_expr)
            cond_b = self._truthy(cond_val, cond_expr.ty)
            keep_bb = self.current_function.append_basic_block(
                name=self._fresh(f"{kind}comp.keep"),
            )
            skip_bb = self.current_function.append_basic_block(
                name=self._fresh(f"{kind}comp.skip"),
            )
            self.builder.cbranch(cond_b, keep_bb, skip_bb)
            self.builder.position_at_end(keep_bb)
            if_exits.append(skip_bb)

        self._emit_comprehension_level(
            kind, container, generators, tuple_unpacks, idx + 1,
            elt_expr, key_expr, val_expr,
        )

        i = len(if_exits) - 1
        while i >= 0:
            skip_bb = if_exits[i]
            if not self._builder_block_is_terminated():
                self.builder.branch(skip_bb)
            self.builder.position_at_end(skip_bb)
            i -= 1

    def _emit_enumerate_loop_in_comp(
        self,
        target: Name,
        inner_iter: Expr,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Lower ``enumerate(xs)`` inside a comprehension.

        The comprehension's tuple-target desugar already rewrote
        ``[... for (i, x) in enumerate(xs)]`` so ``target`` is a
        fresh scalar Name carrying the iter's element type
        (``tuple[int, X]``); the ``i, x = __comp_pair__`` unpack
        statement is emitted by ``_emit_comprehension_after_bind``.

        We build a 2-element tuple per iteration and bind it to
        ``target`` so the unpack sees the expected shape.
        """
        inner_val = self._emit_expr(inner_iter)
        if inner_val in getattr(self, "_cpy_values", ()):
            raise NotImplementedError(
                "enumerate() over CPython iterables in comprehensions "
                "is not supported in native self-host mode"
            )
        if isinstance(inner_iter.ty, DictType):
            iter_obj = self.builder.call(
                self.runtime["py_dict_keys"], [inner_val],
                name=self._fresh("enum.dict.keys"),
            )
        else:
            iter_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                inner_val, inner_iter.ty,
            )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [iter_obj],
            name=self._fresh("enum.len"),
        )

        target_alloca = self._alloca_in_entry(
            _CSTR, name=f"{target.ident}.addr",
        )
        self.env[target.ident] = (
            target_alloca, _CSTR, DynType(name="dyn"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="enum.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("enum.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("enum.body"))
        step_bb = fn.append_basic_block(name=self._fresh("enum.step"))
        end_bb = fn.append_basic_block(name=self._fresh("enum.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("enum.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("enum.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("enum.idx.box"),
        )
        elem_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [iter_obj, idx_box],
            name=self._fresh("enum.elem"),
        )
        pair = self.builder.call(
            self.runtime["py_tuple_new"], [ir.Constant(_I64, 2)],
            name=self._fresh("enum.pair.new"),
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [pair, ir.Constant(_I64, 0), idx_box],
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [pair, ir.Constant(_I64, 1), elem_obj],
        )
        self.builder.store(pair, target_alloca)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("enum.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("enum.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _emit_comprehension_indexed(
        self,
        target: Name,
        iter_val: ir.Value,
        iter_ty,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Indexed iteration over a typed list / tuple: same shape
        as ``_emit_for_list_index`` but the inner block advances the
        explicit comprehension context instead of a Python callback."""
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, iter_val, iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
        n_val = self.builder.call(
            self.runtime[len_helper], [iter_obj],
            name=self._fresh("comp.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        if isinstance(elem_ty, DynType):
            target_ir_ty = _CSTR
        else:
            target_ir_ty = self._map_type(elem_ty)
        alloca = self._alloca_in_entry(
            target_ir_ty, name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, target_ir_ty, elem_ty)

        cond_bb = fn.append_basic_block(name=self._fresh("comp.idx.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.idx.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.idx.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.idx.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        elem_obj = self.builder.call(
            self.runtime[get_helper], [iter_obj, cur],
            name=self._fresh("comp.elem"),
        )
        if isinstance(elem_ty, DynType):
            self.builder.store(elem_obj, alloca)
        else:
            native_val = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, elem_ty,
            )
            self.builder.store(native_val, alloca)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("comp.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _emit_comprehension_obj_indexed(
        self,
        target: Name,
        iter_val: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Generic DynType iteration via ``py_obj_len`` +
        ``py_obj_getitem`` — mirrors ``_emit_for_obj_index``."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [iter_val],
            name=self._fresh("comp.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        alloca = self._alloca_in_entry(
            _CSTR, name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.obj.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.obj.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("comp.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"], [iter_val, idx_box],
            name=self._fresh("comp.obj.elem"),
        )
        self.builder.store(elem, alloca)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1), name=self._fresh("comp.obj.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _emit_comprehension_generator(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Emit one generator level for an explicit comprehension
        context. Supports ``range(...)`` iters,
        ``enumerate(xs)`` (desugar to indexed loop with a synthetic
        counter), CPython iterables, typed list / tuple / dict
        containers, and generic DynType containers via ``py_obj_len``
        + ``py_obj_getitem``."""
        target, iter_e, _ifs_tuple, _is_async = generators[idx]
        # Fast path: range(...) iter.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident in ("range", "xrange")
        ):
            self._emit_range_loop(
                target, iter_e, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        # enumerate(xs) — the comprehension tuple-target desugaring
        # above synthesises ``__comp_pair__`` whose value we build
        # here as a ``(i, xs_elem)`` pair to feed into the unpack.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident == "enumerate"
            and len(iter_e.args) == 1
            and not iter_e.kwargs
        ):
            self._emit_enumerate_loop_in_comp(
                target, iter_e.args[0], kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        iter_val = self._emit_expr(iter_e)
        if iter_val in getattr(self, "_cpy_values", ()):
            self._emit_cpy_iter_loop(
                target, iter_val, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        iter_ty = iter_e.ty
        if isinstance(iter_ty, (ListType, TupleType)):
            self._emit_comprehension_indexed(
                target, iter_val, iter_ty, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        if isinstance(iter_ty, DictType):
            keys_val = self.builder.call(
                self.runtime["py_dict_keys"], [iter_val],
                name=self._fresh("comp.dict.keys"),
            )
            synthetic_ty = ListType(name="list", elem=iter_ty.key)
            self._emit_comprehension_indexed(
                target, keys_val, synthetic_ty, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        if isinstance(iter_ty, StrType):
            self._emit_comprehension_str_chars(
                target, iter_val, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        if isinstance(iter_ty, DynType):
            self._emit_comprehension_obj_indexed(
                target, iter_val, kind, container, generators,
                tuple_unpacks, idx, elt_expr, key_expr, val_expr,
            )
            return
        raise NotImplementedError(
            "Layer 1 comprehension iter must be range(...) or a "
            "CPython-backed iterable"
        )

    def _emit_comprehension_str_chars(
        self,
        target: Name,
        iter_val: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """StrType comprehension iter: slice each char."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"], [iter_val],
            name=self._fresh("comp.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 1)],
            name=self._fresh("comp.str.step"),
        )
        tgt_name = target.ident
        if tgt_name not in self.env:
            alloca = self._alloca_in_entry(
                _CSTR, name=f"{tgt_name}.addr",
            )
            self.env[tgt_name] = (alloca, _CSTR, StrType(name="str"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.str.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("comp.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("comp.str.lo"),
        )
        hi = self.builder.add(
            cur, ir.Constant(_I64, 1), name=self._fresh("comp.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"], [hi],
            name=self._fresh("comp.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("comp.str.ch"),
        )
        alloca, _, _ = self.env[tgt_name]
        self.builder.store(ch, alloca)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.str.idx2"))
        nxt = self.builder.add(
            cur2, ir.Constant(_I64, 1),
            name=self._fresh("comp.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_range_loop(
        self,
        target: Name,
        call: Call,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        if call.kwargs:
            raise NotImplementedError(
                "range() with keyword args not supported in comprehension"
            )
        if len(call.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(call.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(call.args) == 2:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(call.args) == 3:
            start_val = self._emit_expr_as_i64(call.args[0])
            stop_val = self._emit_expr_as_i64(call.args[1])
            step_val = self._emit_expr_as_i64(call.args[2])
        else:
            raise L1CodegenError(
                f"range() takes 1–3 args; got {len(call.args)}"
            )
        target_name = target.ident
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(_I64, name=f"{target_name}.addr")
            self.env[target_name] = (alloca, _I64, IntType(name="int"))
        else:
            alloca, ir_ty, _decl = existing
            if ir_ty is not _I64:
                raise L1CodegenError(
                    f"comprehension target {target_name!r} bound to "
                    f"{ir_ty}, expected i64"
                )
        self.builder.store(start_val, alloca)
        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(alloca, name=self._fresh(target_name))
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(
            ">", step_val, zero64, name=self._fresh("step_pos"),
        )
        cond_pos = self.builder.icmp_signed(
            "<", cur, stop_val, name=self._fresh("fwd_cmp"),
        )
        cond_neg = self.builder.icmp_signed(
            ">", cur, stop_val, name=self._fresh("bwd_cmp"),
        )
        cond_i1 = self.builder.select(
            step_pos, cond_pos, cond_neg, name=self._fresh("comp_cond"),
        )
        self.builder.cbranch(cond_i1, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(alloca, name=self._fresh(target_name))
        next_val = self.builder.add(cur2, step_val, name=self._fresh("next"))
        self.builder.store(next_val, alloca)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _emit_cpy_iter_loop(
        self,
        target: Name,
        iter_src: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Shared CPython-iteration loop for comprehensions."""
        target_name = target.ident
        fn = self.current_function
        iter_obj = self.builder.call(
            self.runtime["py_cpy_iter"], [iter_src],
            name=self._fresh("comp.iter"),
        )
        cond_bb = fn.append_basic_block(name=self._fresh("comp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.body"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        nxt = self.builder.call(
            self.runtime["py_cpy_iter_next"], [iter_obj],
            name=self._fresh("comp.next"),
        )
        null_p = ir.Constant(nxt.type, None)
        is_done = self.builder.icmp_unsigned(
            "==", nxt, null_p, name=self._fresh("comp.done"),
        )
        self.builder.cbranch(is_done, end_bb, body_bb)
        self.builder.position_at_end(body_bb)
        existing = self.env.get(target_name)
        if existing is None:
            alloca = self._alloca_in_entry(
                nxt.type, name=f"{target_name}.addr",
            )
            self.env[target_name] = (alloca, nxt.type, DynType(name="dyn"))
            if not hasattr(self, "_cpy_env_flags"):
                self._cpy_env_flags = {}
            self._cpy_env_flags[target_name] = True
        else:
            alloca, _, _ = existing
        self.builder.store(nxt, alloca)
        self._emit_comprehension_after_bind(
            kind, container, generators, tuple_unpacks, idx,
            elt_expr, key_expr, val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    # -- Subscript / attribute load -----------------------------------

    def _emit_slice_load(self, expr: Subscript) -> ir.Value:
        """Lower ``xs[lo:hi:step]`` for list / tuple / str. Each bound
        lowers to a PyObject* (None when absent) and dispatches to the
        type-specific ``py_{list,tuple,str}_slice`` runtime helper —
        all of which return a freshly-allocated container of the
        same kind."""
        sl = expr.idx
        assert isinstance(sl, Slice)
        obj = self._emit_expr(expr.obj)
        if obj in getattr(self, "_cpy_values", ()):
            def _bound_cpy(e: Optional[Expr]) -> ir.Value:
                if e is None:
                    gv = declare_runtime_global(self.module, "py_None")
                    none = self.builder.load(gv, name=self._fresh("none"))
                    return self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"], [none],
                        name=self._fresh("cpy.none"),
                    )
                v = self._emit_expr(e)
                boxed = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, e.ty,
                )
                if boxed in getattr(self, "_cpy_values", ()):
                    return boxed
                return self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [boxed],
                    name=self._fresh("cpy.slice.bound"),
                )

            slice_fn = self._load_cpython_builtin("slice")
            lo_cpy = _bound_cpy(sl.lo)
            hi_cpy = _bound_cpy(sl.hi)
            step_cpy = _bound_cpy(sl.step)
            slice_obj = self.builder.call(
                self.runtime["py_cpy_call3"],
                [slice_fn, lo_cpy, hi_cpy, step_cpy],
                name=self._fresh("cpy.slice"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_getitem"], [obj, slice_obj],
                name=self._fresh("cpy.slice.getitem"),
            )
            return self._mark_cpy_value(result)
        obj_ty = expr.obj.ty

        def _bound(e: Optional[Expr]) -> ir.Value:
            if e is None:
                return ir.Constant(_CSTR, None)
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        lo = _bound(sl.lo)
        hi = _bound(sl.hi)
        step = _bound(sl.step)
        if isinstance(obj_ty, ListType):
            helper = "py_list_slice"
        elif isinstance(obj_ty, TupleType):
            helper = "py_tuple_slice"
        elif isinstance(obj_ty, StrType):
            helper = "py_str_slice"
        elif isinstance(obj_ty, DynType):
            helper = "py_obj_slice"
        else:
            raise NotImplementedError(
                f"Layer 1 slice on type {type(obj_ty).__name__} "
                "not supported"
            )
        return self.builder.call(
            self.runtime[helper], [obj, lo, hi, step],
            name=self._fresh("slice"),
        )

    def _mark_cpy_value(self, value: ir.Value) -> ir.Value:
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(value)
        return value

    def _emit_cpython_list_ops(
        self, ops: list[tuple[str, ir.Value, Type]],
    ) -> ir.Value:
        list_ctor = self._load_cpython_builtin("list")
        lst = self.builder.call(
            self.runtime["py_cpy_call_noargs"], [list_ctor],
            name=self._fresh("cpy.list"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [list_ctor])
        self._mark_cpy_value(lst)
        append_fn = None
        extend_fn = None
        for op_kind, value, value_ty in ops:
            cpy_value, owned = self._marshal_to_cpython(value, value_ty)
            if op_kind == "append":
                if append_fn is None:
                    append_fn = self._emit_cpy_attr(lst, "append")
                result = self.builder.call(
                    self.runtime["py_cpy_call1"], [append_fn, cpy_value],
                    name=self._fresh("cpy.list.append"),
                )
            else:
                if extend_fn is None:
                    extend_fn = self._emit_cpy_attr(lst, "extend")
                result = self.builder.call(
                    self.runtime["py_cpy_call1"], [extend_fn, cpy_value],
                    name=self._fresh("cpy.list.extend"),
                )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_value])
            self.builder.call(self.runtime["py_cpy_decref"], [result])
        if append_fn is not None:
            self.builder.call(self.runtime["py_cpy_decref"], [append_fn])
        if extend_fn is not None:
            self.builder.call(self.runtime["py_cpy_decref"], [extend_fn])
        return lst

    def _emit_cpython_tuple_ops(
        self, ops: list[tuple[str, ir.Value, Type]],
    ) -> ir.Value:
        lst = self._emit_cpython_list_ops(ops)
        tuple_ctor = self._load_cpython_builtin("tuple")
        tup = self.builder.call(
            self.runtime["py_cpy_call1"], [tuple_ctor, lst],
            name=self._fresh("cpy.tuple"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [tuple_ctor])
        self.builder.call(self.runtime["py_cpy_decref"], [lst])
        return self._mark_cpy_value(tup)

    def _emit_cpython_dict_items(
        self,
        items: list[tuple[ir.Value, Type, ir.Value, Type]],
    ) -> ir.Value:
        dict_ctor = self._load_cpython_builtin("dict")
        d = self.builder.call(
            self.runtime["py_cpy_call_noargs"], [dict_ctor],
            name=self._fresh("cpy.dict"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [dict_ctor])
        self._mark_cpy_value(d)
        for k_val, k_ty, v_val, v_ty in items:
            cpy_key, key_owned = self._marshal_to_cpython(k_val, k_ty)
            cpy_val, val_owned = self._marshal_to_cpython(v_val, v_ty)
            self.builder.call(
                self.runtime["py_cpy_setitem"], [d, cpy_key, cpy_val],
            )
            if key_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if val_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
        return d

    def _maybe_emit_runtime_dict_lookup(
        self, expr: Subscript,
    ) -> Optional[ir.Value]:
        """Path A: recognize ``self.runtime["NAME"]`` literal dict
        lookups and return a direct reference to the named extern
        function instead of routing through ``py_cpy_getattr`` +
        ``py_cpy_getitem``.

        Returns the function reference value if the pattern matches
        AND ``NAME`` is a known runtime symbol; ``None`` otherwise so
        the caller falls through to the existing dynamic path.
        """
        if not isinstance(expr.obj, Attr):
            return None
        if expr.obj.name != "runtime":
            return None
        if not isinstance(expr.obj.obj, Name) or expr.obj.obj.ident != "self":
            return None
        if not isinstance(expr.idx, StrLit):
            return None
        name = expr.idx.value
        fn = self.runtime.get(name)
        if isinstance(fn, ir.Function):
            return fn
        return None

    def _emit_subscript_load(self, expr: Subscript) -> ir.Value:
        # Keep ``self.runtime["NAME"]`` as a real pcc-native dict lookup.
        # The value is an IR Function object owned by the compiler being
        # compiled. Folding it to a native symbol address would make the
        # stage1 binary link against target-program runtime symbols such as
        # ``py_cpy_call1`` instead of merely emitting those names into IR.
        # Slice form ``xs[lo:hi:step]`` routes to the type-specific
        # runtime slicer before any dunder / scalar-index path.
        if isinstance(expr.idx, Slice):
            return self._emit_slice_load(expr)
        # Class-based __getitem__ fast path: if ``expr.obj`` is a Name
        # bound to a known class that defines ``__getitem__``, dispatch
        # directly rather than going through ``py_obj_getitem`` (which
        # doesn't yet do user-class dispatch in the runtime).
        dunder = self._try_dispatch_dunder_unary(expr, "__getitem__", (expr.idx,))
        if dunder is not None:
            return dunder

        obj = self._emit_expr(expr.obj)
        # CPython-backed object: dispatch via py_cpy_getitem
        # (PyObject_GetItem) with a boxed key. Result is a fresh
        # CPython reference — tagged for the caller.
        if obj in getattr(self, "_cpy_values", ()):
            key_val = self._emit_expr(expr.idx)
            cpy_key, owned = self._marshal_to_cpython(key_val, expr.idx.ty)
            result = self.builder.call(
                self.runtime["py_cpy_getitem"], [obj, cpy_key],
                name=self._fresh("cpy.getitem"),
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result
        obj_ty = expr.obj.ty
        if isinstance(obj_ty, ListType):
            idx = self._emit_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_list_get"], [obj, idx],
                name=self._fresh("list.get"),
            )
            return self._coerce_from_object(got, obj_ty.elem)
        if isinstance(obj_ty, TupleType):
            idx = self._emit_expr_as_i64(expr.idx)
            got = self.builder.call(
                self.runtime["py_tuple_get"], [obj, idx],
                name=self._fresh("tup.get"),
            )
            # Best-effort: if all tuple elements share one type, unbox
            # to that; otherwise leave as PyObject*.
            elem_ty: Type
            if obj_ty.elems:
                first = obj_ty.elems[0]
                if all(type(e) is type(first) for e in obj_ty.elems):
                    elem_ty = first
                else:
                    return got
            else:
                return got
            return self._coerce_from_object(got, elem_ty)
        if isinstance(obj_ty, DictType):
            key_obj = self._emit_as_object(expr.idx)
            got = self.builder.call(
                self.runtime["py_dict_get"], [obj, key_obj],
                name=self._fresh("dict.get"),
            )
            # Phase 3 will raise KeyError on NULL result; for now, pass
            # the PyObject* through (callers can test against NULL).
            return self._coerce_from_object(got, obj_ty.value)
        if isinstance(obj_ty, StrType):
            idx_obj = self._emit_as_object(expr.idx)
            return self.builder.call(
                self.runtime["py_str_index"], [obj, idx_obj],
                name=self._fresh("str.idx"),
            )
        # Generic dyn/object fallback.
        key_obj = self._emit_as_object(expr.idx)
        return self.builder.call(
            self.runtime["py_obj_getitem"], [obj, key_obj],
            name=self._fresh("obj.getitem"),
        )

    def _emit_attr(self, expr: Attr) -> ir.Value:
        if isinstance(expr.obj, Name):
            alias_export = self._native_module_alias_export_info(
                expr.obj.ident, expr.name,
            )
            if alias_export is not None:
                module_name, info = alias_export
                kind = info.get("kind")
                if kind == "class":
                    class_info = self._ensure_native_module_alias_class_export(
                        expr.obj.ident, expr.name,
                    )
                    if class_info is not None:
                        return self.builder.load(
                            class_info.global_var,
                            name=self._fresh(f"cls.{expr.name}"),
                        )
                elif kind == "function":
                    return self._declare_extern_user_function(
                        module_name, expr.name, info,
                    )
            builtin_module = self._native_builtin_module_for_name(
                expr.obj.ident
            )
            if builtin_module is not None:
                if builtin_module == "sys" and expr.name == "argv":
                    return self._emit_program_argv_list()
                if builtin_module == "sys" and expr.name == "executable":
                    return self.builder.call(
                        self.runtime["py_sys_executable_str"], [],
                        name=self._fresh("sys.executable"),
                    )
                if builtin_module == "sys" and expr.name == "platform":
                    return self.builder.call(
                        self.runtime["py_sys_platform_str"], [],
                        name=self._fresh("sys.platform"),
                    )
                if builtin_module == "os":
                    # POSIX access(2) mode constants — same on every
                    # platform pcc supports (F_OK=0, X_OK=1, W_OK=2,
                    # R_OK=4). Emit as direct PyInt constants instead
                    # of routing through CPython's os module.
                    _OS_ACCESS_CONSTS = {
                        "F_OK": 0,
                        "X_OK": 1,
                        "W_OK": 2,
                        "R_OK": 4,
                    }
                    if expr.name in _OS_ACCESS_CONSTS:
                        return self.builder.call(
                            self.runtime["py_int_from_i64"],
                            [ir.Constant(
                                _I64, _OS_ACCESS_CONSTS[expr.name]
                            )],
                            name=self._fresh(f"os.{expr.name}"),
                        )
                return self._emit_cpy_attr(
                    self._emit_cpython_module_value(builtin_module),
                    expr.name,
                )
            if (
                hasattr(self, "class_lowering")
                and expr.obj.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.obj.ident]
                class_attr = self.class_lowering.emit_class_attr_load(
                    info, expr.name,
                )
                if class_attr is not None:
                    return class_attr
        # CPython-backed fast path: if the object evaluates to a
        # CPython ``PyObject*`` (either bound directly via a Name in
        # _cpy_module_env / _cpy_env_flags, or through a nested
        # ``a.b.c`` chain where an inner node is CPython), route the
        # attribute load through py_cpy_getattr. Otherwise fall
        # through to the pcc native path.
        if isinstance(expr.obj, Name):
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.obj.ident)
            if cpy_gv is not None:
                mod_val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.{expr.obj.ident}")
                )
                return self._emit_cpy_attr(mod_val, expr.name)
            if getattr(self, "_cpy_env_flags", {}).get(expr.obj.ident, False):
                obj_val = self._emit_expr(expr.obj)
                return self._emit_cpy_attr(obj_val, expr.name)
        if isinstance(expr.obj, (Attr, Subscript, Call)):
            chain_val = self._emit_expr(expr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_attr(chain_val, expr.name)

        # Property getter fast path: if the attribute is a @property on
        # a hinted class, dispatch to the getter function.
        if isinstance(expr.obj, Name):
            hint = self.env_class_hint.get(expr.obj.ident)
            if hint is not None:
                info = self._resolve_property_mro(hint, expr.name)
                if info is not None:
                    getter = info.properties[expr.name]
                    obj_val = self._emit_expr(expr.obj)
                    return self.builder.call(
                        getter, [obj_val],
                        name=self._fresh(f"prop.{expr.name}"),
                    )
                class_info = self.class_lowering.classes.get(hint)
                if class_info is not None:
                    class_attr = self.class_lowering.emit_class_attr_load(
                        class_info, expr.name,
                    )
                    if class_attr is not None:
                        return class_attr

        # Fast path for ``self.<attr>`` inside a method body: use the
        # declared-field index when known, otherwise fall through to the
        # generic ``py_obj_getattr`` call.
        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "self"
        ):
            # self.<prop> — dispatch to getter when present.
            info_p = self._resolve_property_mro(current_class.name, expr.name)
            if info_p is not None:
                getter = info_p.properties[expr.name]
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                return self.builder.call(
                    getter, [self_val],
                    name=self._fresh(f"self.prop.{expr.name}"),
                )
            self_val = self.builder.load(
                self.env["self"][0], name=self._fresh("self")
            )
            return self.class_lowering.emit_self_attr_load(
                current_class, expr.name, self_val
            )
        if (
            current_class is not None
            and isinstance(expr.obj, Name)
            and expr.obj.ident == "cls"
            and getattr(self, "current_method_kind", None) == "classmethod"
        ):
            class_attr = self.class_lowering.emit_class_attr_load(
                current_class, expr.name,
            )
            if class_attr is not None:
                return class_attr

        obj = self._emit_expr(expr.obj)
        # Any object goes through py_obj_getattr; if the object is
        # ``None`` at runtime the runtime lib raises AttributeError —
        # that's the correct Python semantic (no segfault).
        name_ptr = self._attr_name_ptr(expr.name)
        return self.builder.call(
            self.runtime["py_obj_getattr"], [obj, name_ptr],
            name=self._fresh(f"attr.{expr.name}"),
        )

    def _emit_attr_store_value(
        self, target: Attr, value: ir.Value, value_ty: Type,
    ) -> None:
        if isinstance(target.obj, Name):
            if (
                hasattr(self, "class_lowering")
                and target.obj.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[target.obj.ident]
                if self.class_lowering.emit_class_attr_store(
                    info, target.name, value, value_ty,
                ):
                    return
            if (
                getattr(self, "current_class", None) is not None
                and target.obj.ident == "cls"
                and getattr(self, "current_method_kind", None) == "classmethod"
            ):
                if self.class_lowering.emit_class_attr_store(
                    self.current_class, target.name, value, value_ty,
                ):
                    return
        # Property setter fast path.
        if isinstance(target.obj, Name):
            hint = self.env_class_hint.get(target.obj.ident)
            if hint is not None:
                info = self._resolve_property_setter_mro(hint, target.name)
                if info is not None:
                    setter_fn = info.property_setters[target.name]
                    obj_val = self._emit_expr(target.obj)
                    if len(setter_fn.args) >= 2:
                        param_ty = setter_fn.args[1].type
                        if isinstance(param_ty, ir.IntType) and param_ty.width == 64:
                            value = self._coerce(value, value_ty, IntType(name="int"))
                        elif isinstance(param_ty, ir.PointerType):
                            value = marshal.marshal_to_object(
                                self.builder, self.module, self.runtime,
                                value, value_ty,
                            )
                    self._call_user(setter_fn, [obj_val, value], "")
                    return

        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(target.obj, Name)
            and target.obj.ident == "self"
        ):
            self_val = self.builder.load(
                self.env["self"][0], name=self._fresh("self")
            )
            # The value needs to reach the runtime as PyObject*.
            value = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, value, value_ty,
            )
            self.class_lowering.emit_self_attr_store(
                current_class, target.name, self_val, value
            )
            return
        # Generic fallback: obj.name = value via py_obj_setattr.
        obj = self._emit_expr(target.obj)
        name_ptr = self._attr_name_ptr(target.name)
        if (
            obj in getattr(self, "_cpy_values", ())
            or (
                isinstance(target.obj, Name)
                and getattr(self, "_cpy_env_flags", {}).get(
                    target.obj.ident, False,
                )
            )
        ):
            cpy_value, owned = self._marshal_to_cpython(value, value_ty)
            self.builder.call(
                self.runtime["py_cpy_setattr"], [obj, name_ptr, cpy_value]
            )
            if owned:
                self.builder.call(
                    self.runtime["py_cpy_decref"], [cpy_value]
                )
            return
        value = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, value, value_ty,
        )
        self.builder.call(
            self.runtime["py_obj_setattr"], [obj, name_ptr, value]
        )

    def _emit_attr_store(self, target: Attr, value_expr: Expr) -> None:
        value = self._emit_expr(value_expr)
        self._emit_attr_store_value(target, value, value_expr.ty)

    def _emit_as_object(self, expr: Expr) -> ir.Value:
        """Emit ``expr`` and marshal the result to PyObject*."""
        if isinstance(expr.ty, IntType):
            exact = self._maybe_emit_exact_int_object(expr)
            if exact is not None:
                return exact
        v = self._emit_expr(expr)
        return marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, expr.ty
        )

    def _emit_name(self, expr: Name) -> ir.Value:
        slot = self.env.get(expr.ident)
        if slot is None:
            # Module-level dunder that pcc can resolve at compile time
            # when the file is being compiled as a top-level script.
            # Matches CPython's behavior for ``python myscript.py``.
            if expr.ident == "__name__":
                return self._emit_str_literal("__main__")
            if expr.ident == "__file__":
                # Compile-time approximation — CPython sets ``__file__``
                # to the absolute path of the compiled script. pcc
                # doesn't have the source path here at codegen time;
                # return the sanitized module name instead so code that
                # logs / path-derives from ``__file__`` keeps working.
                return self._emit_str_literal(
                    (self.ast_module.name or "pcc_py_module") + ".py"
                )
            if expr.ident == "__doc__":
                return self._emit_str_literal("")
            if expr.ident == "Ellipsis":
                # ``...`` / ``Ellipsis`` used as an expression — pcc
                # doesn't have a distinct Ellipsis type; reuse the
                # None-literal emitter so code that stashes
                # ``Ellipsis`` as a sentinel keeps working.
                return self._emit_none_literal()
            if expr.ident == "NotImplemented":
                return self._load_cpython_builtin("NotImplemented")
            if expr.ident in ("True", "False"):
                return ir.Constant(_I1, 1 if expr.ident == "True" else 0)
            # Built-in type names at value position (``isinstance(x,
            # int)`` already folds compile-time; this covers the
            # residual ``obj_type = int`` / ``self.ty = str`` uses).
            # Route through CPython's ``builtins`` module so the value
            # is a real type object.
            if expr.ident in _CPY_BUILTIN_TYPE_NAMES:
                return self._load_cpython_builtin(expr.ident)
            # Module-level constant? Emit a load of the global.
            module_globals = self._module_globals
            if expr.ident in module_globals:
                gv, _declared_ty = module_globals[expr.ident]
                val = self.builder.load(
                    gv, name=self._fresh(expr.ident),
                )
                if self._cpy_module_flags.get(expr.ident, False):
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(val)
                return val
            # User class reference at value position — load the class
            # global so ``ClassName.ATTR`` and similar look-ups work.
            if (
                hasattr(self, "class_lowering")
                and expr.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.ident]
                return self.builder.load(
                    info.global_var,
                    name=self._fresh(f"cls.{expr.ident}"),
                )
            native_alias_module = getattr(
                self, "_native_module_aliases", {},
            ).get(expr.ident)
            if native_alias_module is not None:
                return self._emit_cpython_module_value(native_alias_module)
            # Fall back to the module-wide CPython import registry for
            # ``from os import sep`` / ``import sys`` style bindings.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.ident)
            if cpy_gv is not None:
                val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.{expr.ident}")
                )
                if not hasattr(self, "_cpy_values"):
                    self._cpy_values = set()
                self._cpy_values.add(val)
                return val
            builtin_module = self._native_builtin_module_for_name(
                expr.ident
            )
            if builtin_module is not None:
                return self._emit_cpython_module_value(builtin_module)
            star_val = self._load_from_cpy_star_imports(expr.ident)
            if star_val is not None:
                return star_val
            # User FuncDef at value position: wrap the pcc function
            # pointer as a CPython PyCFunction so it can be passed to
            # ``re.sub(pat, <repl>, text)`` / ``am.register(KEY, <fn>)``
            # / ``{c_ast.FileAST: _children_FileAST}`` / any other
            # CPython API that consumes a callable. Covers 1 / 2 / 3
            # arg DynType-in / DynType-out. Higher arity still falls
            # through.
            resolved_name = expr.ident
            fn_ir = self.functions.get(expr.ident)
            if fn_ir is None:
                direct_hoist = f"__nested_{expr.ident}"
                if direct_hoist in self.functions:
                    resolved_name = direct_hoist
                    fn_ir = self.functions[direct_hoist]
                else:
                    matches = [
                        name for name in self.functions
                        if name.startswith(f"{direct_hoist}_")
                    ]
                    if len(matches) == 1:
                        resolved_name = matches[0]
                        fn_ir = self.functions[resolved_name]
            # Adapter-wrap path: the ident may originally have been
            # a nested def flagged for captures-via-globals wrap.
            # ``rename_map`` at hoist time remapped the original name
            # to the hoisted one already, but the metadata dict is
            # keyed on the original name. Try both.
            adapter_entry = None
            for candidate in (expr.ident, resolved_name):
                adapter_entry = getattr(
                    self, "_hoist_wrap_caps", {},
                ).get(candidate)
                if adapter_entry is not None:
                    break
            if fn_ir is None and adapter_entry is not None:
                hoisted_name = adapter_entry.get("hoisted_name")
                if hoisted_name:
                    fn_ir = self.functions.get(hoisted_name)
                    resolved_name = hoisted_name
            if (
                adapter_entry is None
                and fn_ir is not None
                and resolved_name != expr.ident
            ):
                free_names = getattr(
                    self, "_hoisted_capture_params", {},
                ).get(resolved_name, ())
                if free_names:
                    fnty = getattr(fn_ir, "function_type", None)
                    total_arity = len(getattr(fnty, "args", ()))
                    adapter_entry = {
                        "original_arity": max(total_arity - len(free_names), 0),
                        "free_names": tuple(free_names),
                        "hoisted_name": resolved_name,
                        "original_name": expr.ident,
                    }
            if fn_ir is not None:
                fnty = getattr(fn_ir, "function_type", None)
                all_ptr_args = (
                    fnty is not None
                    and all(
                        isinstance(a, ir.PointerType) for a in fnty.args
                    )
                )
                ret_ok = fnty is not None and isinstance(
                    fnty.return_type, ir.PointerType
                )
                ret_void = fnty is not None and isinstance(
                    fnty.return_type, ir.VoidType
                )
                ret_int_width = (
                    fnty.return_type.width
                    if fnty is not None and isinstance(
                        fnty.return_type, ir.IntType,
                    ) else 0
                )
                wrap_helper = None
                arity = None
                if all_ptr_args and (
                    ret_ok or ret_void or ret_int_width in (1, 64)
                ):
                    arity = len(fnty.args)
                    # Captures-adapter has original arity.
                    if adapter_entry is not None and adapter_entry.get("hoisted_name"):
                        arity = adapter_entry["original_arity"]
                    wrap_helper = {
                        0: "py_cpy_wrap_pcc_0arg",
                        1: "py_cpy_wrap_pcc_1arg",
                        2: "py_cpy_wrap_pcc_2arg",
                        3: "py_cpy_wrap_pcc_3arg",
                        4: "py_cpy_wrap_pcc_4arg",
                        5: "py_cpy_wrap_pcc_5arg",
                        6: "py_cpy_wrap_pcc_6arg",
                        7: "py_cpy_wrap_pcc_7arg",
                        8: "py_cpy_wrap_pcc_8arg",
                        9: "py_cpy_wrap_pcc_9arg",
                    }.get(arity)
                if wrap_helper is not None:
                    target_fn = fn_ir
                    if adapter_entry is not None and adapter_entry.get("free_names"):
                        # Hoisted-captures adapter. ``_emit_hoist_adapter``
                        # already boxes non-ptr returns internally.
                        target_fn = self._emit_hoist_adapter(
                            expr.ident, fn_ir, adapter_entry,
                        )
                    elif not ret_ok:
                        # Standalone adapter for a value-position ref to
                        # a pcc FuncDef whose return is void / bool / int.
                        # Box the result via the appropriate py_* helper.
                        adapter_name = (
                            f"{fn_ir.name}_v2pyobj_{arity}"
                        )
                        existing_adapter = self.module.globals.get(adapter_name)
                        if isinstance(existing_adapter, ir.Function):
                            target_fn = existing_adapter
                        else:
                            adapter_fnty = ir.FunctionType(
                                _CSTR, [_CSTR] * arity,
                            )
                            target_fn = ir.Function(
                                self.module, adapter_fnty, name=adapter_name,
                            )
                            target_fn.linkage = "internal"
                            ab = target_fn.append_basic_block("entry")
                            ab_b = ir.IRBuilder(ab)
                            if ret_void:
                                ab_b.call(fn_ir, list(target_fn.args))
                                py_none_gv = declare_runtime_global(
                                    self.module, "py_None",
                                )
                                ab_b.ret(ab_b.load(py_none_gv, name="none"))
                            elif ret_int_width == 1:
                                raw = ab_b.call(
                                    fn_ir, list(target_fn.args), name="raw",
                                )
                                bit = ab_b.zext(raw, _I32, name="b2i32")
                                boxed = ab_b.call(
                                    self.runtime["py_bool_from_bit"],
                                    [bit], name="boxed",
                                )
                                ab_b.ret(boxed)
                            else:
                                # ret_int_width == 64
                                raw = ab_b.call(
                                    fn_ir, list(target_fn.args), name="raw",
                                )
                                boxed = ab_b.call(
                                    self.runtime["py_int_from_i64"],
                                    [raw], name="boxed",
                                )
                                ab_b.ret(boxed)
                    fn_ptr = self.builder.bitcast(
                        target_fn, _CSTR,
                        name=self._fresh(f"{expr.ident}.fnptr"),
                    )
                    result = self.builder.call(
                        self.runtime[wrap_helper], [fn_ptr],
                        name=self._fresh(f"cpy.{expr.ident}"),
                    )
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(result)
                    return result
            span = getattr(expr, "span", None)
            where = ""
            if span is not None:
                where = f" at {span.file}:{span.line}:{span.col}"
            raise L1CodegenError(
                f"reference to unbound name {expr.ident!r}{where}"
            )
        alloca, ir_ty, _ = slot
        val = self.builder.load(alloca, name=self._fresh(expr.ident))
        # Re-tag as a CPython value when the binding was recorded as
        # one. Without this, downstream coercions see a bare DynType
        # and route through the pcc (non-CPython) unbox path.
        if getattr(self, "_cpy_env_flags", {}).get(expr.ident, False):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(val)
        return val

    # -- BinOp ---------------------------------------------------------

    def _emit_binop_value(
        self,
        op: str,
        lhs: ir.Value,
        lhs_ty: Type,
        rhs: ir.Value,
        rhs_ty: Type,
        result_ty: Type,
    ) -> ir.Value:
        # Phase 2 object ops (str concat / repeat, list concat). Keeping
        # the dispatch here lets augassign (``s += "x"``, ``lst += ...``)
        # take the same code path as the value-form expression.
        if op == "+" and isinstance(lhs_ty, StrType) and isinstance(rhs_ty, StrType):
            return self.builder.call(
                self.runtime["py_str_concat"], [lhs, rhs],
                name=self._fresh("str.concat"),
            )
        if op == "%" and isinstance(lhs_ty, StrType):
            # ``"fmt" % args`` — Python old-style string formatting.
            # pcc has no native formatter, so route through CPython:
            # convert both sides to CPython objects, call
            # ``str.__mod__``, convert the result back to a pcc str.
            # Pulls libpython. Use the universal pcc→CPython converter
            # so tuple / dict rhs shapes keep working.
            lhs_cpy = self.builder.call(
                self.runtime["py_cpy_from_pccstr"], [lhs],
                name=self._fresh("str.mod.lhs"),
            )
            # ``py_cpy_from_pcc_obj`` expects a pcc PyObject*. If the
            # rhs is a native int / float / bool, box it first through
            # ``marshal_to_object`` so the converter receives a tagged
            # pcc object rather than a raw scalar.
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty,
            )
            rhs_cpy = self.builder.call(
                self.runtime["py_cpy_from_pcc_obj"], [rhs_obj],
                name=self._fresh("str.mod.rhs"),
            )
            # ``PyNumber_Remainder`` is the dispatch that routes to
            # ``str.__mod__`` for str operands. Expose it via a tiny
            # wrapper in the builtins path; fall back to a manual
            # ``getattr(lhs, '__mod__')(rhs)`` through py_cpy_call1.
            mod_attr_gv = self._cstr_global(
                "__mod__", ".cpy.attr.__mod__",
            )
            fn_val = self.builder.call(
                self.runtime["py_cpy_getattr"],
                [lhs_cpy, self._ptr_to_cstr(mod_attr_gv)],
                name=self._fresh("str.mod.fn"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, rhs_cpy],
                name=self._fresh("str.mod.call"),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
            self.builder.call(self.runtime["py_cpy_decref"], [rhs_cpy])
            self.builder.call(self.runtime["py_cpy_decref"], [lhs_cpy])
            pcc_str = self.builder.call(
                self.runtime["py_cpy_to_pcc_str"], [result],
                name=self._fresh("str.mod.pcc"),
            )
            self.builder.call(self.runtime["py_cpy_decref"], [result])
            return pcc_str
        if op == "*" and isinstance(lhs_ty, StrType) and isinstance(rhs_ty, (IntType, BoolType)):
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [lhs, rhs_obj],
                name=self._fresh("str.rep"),
            )
        if op == "*" and isinstance(rhs_ty, StrType) and isinstance(lhs_ty, (IntType, BoolType)):
            lhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [rhs, lhs_obj],
                name=self._fresh("str.rep"),
            )
        if op == "*" and isinstance(lhs_ty, ListType) and isinstance(rhs_ty, (IntType, BoolType)):
            n_i64 = self._to_int64(rhs, rhs_ty)
            return self.builder.call(
                self.runtime["py_list_repeat"], [lhs, n_i64],
                name=self._fresh("list.rep"),
            )
        if op == "*" and isinstance(rhs_ty, ListType) and isinstance(lhs_ty, (IntType, BoolType)):
            n_i64 = self._to_int64(lhs, lhs_ty)
            return self.builder.call(
                self.runtime["py_list_repeat"], [rhs, n_i64],
                name=self._fresh("list.rep"),
            )
        # Narrow fallback: ``ListType * DynType`` where the DynType
        # payload is a runtime integer. Unbox the Dyn to i64 and
        # route through py_list_repeat. Covers ``[x] * some_dyn``
        # where typing didn't pin DynType as IntType.
        if op == "*" and isinstance(lhs_ty, ListType) and isinstance(rhs_ty, DynType):
            n_i64 = marshal.marshal_from_object(
                self.builder, self.module, self.runtime, rhs,
                IntType(name="int"),
            )
            return self.builder.call(
                self.runtime["py_list_repeat"], [lhs, n_i64],
                name=self._fresh("list.rep.dyn"),
            )
        if op == "*" and isinstance(lhs_ty, TupleType) and isinstance(rhs_ty, (IntType, BoolType)):
            # Tuple-repeat: fall back through CPython ``__mul__``.
            # Materialise the tuple as a CPython tuple, multiply, and
            # keep the result as a CPython-tagged value — the downstream
            # subscript / iter paths already handle that.
            cpy_tup = self.builder.call(
                self.runtime["py_cpy_from_pcc_obj"], [lhs],
                name=self._fresh("cpy.from_tup"),
            )
            n_cpy = self.builder.call(
                self.runtime["py_cpy_from_i64"],
                [self._to_int64(rhs, rhs_ty)],
                name=self._fresh("cpy.from_i64"),
            )
            mod_attr_gv = self._cstr_global(
                "__mul__", ".cpy.attr.__mul__",
            )
            fn_val = self.builder.call(
                self.runtime["py_cpy_getattr"],
                [cpy_tup, self._ptr_to_cstr(mod_attr_gv)],
                name=self._fresh("cpy.tup.mul"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, n_cpy],
                name=self._fresh("tup.rep"),
            )
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result
        if op == "+" and (
            (isinstance(lhs_ty, ListType) and isinstance(rhs_ty, (ListType, DynType)))
            or (isinstance(rhs_ty, ListType) and isinstance(lhs_ty, DynType))
        ):
            # ListType + (ListType | DynType) or DynType + ListType —
            # ``py_list_concat`` accepts any pcc PyObject* and builds a
            # new list. The Dyn side is trusted to be list-shaped at
            # runtime (mirrors CPython's ``+`` which would raise at
            # runtime for a non-list).
            return self.builder.call(
                self.runtime["py_list_concat"], [lhs, rhs],
                name=self._fresh("list.concat"),
            )
        if op == "+" and (
            (
                isinstance(lhs_ty, TupleType)
                and isinstance(rhs_ty, (TupleType, DynType))
            )
            or (
                isinstance(rhs_ty, TupleType)
                and isinstance(lhs_ty, DynType)
            )
        ):
            # TupleType + (TupleType | DynType) stays native. The DynType
            # side is trusted to be tuple-shaped at runtime, mirroring the
            # existing ListType + DynType fast path above.
            return self.builder.call(
                self.runtime["py_tuple_concat"], [lhs, rhs],
                name=self._fresh("tup.concat"),
            )

        if (
            op == "|"
            and self._is_native_set_dyn(lhs_ty)
            and self._is_native_set_dyn(rhs_ty)
        ):
            return self._emit_set_union_values(lhs, rhs)

        if (
            self._int_exprs_are_boxed()
            and isinstance(result_ty, IntType)
            and isinstance(lhs_ty, (IntType, BoolType))
            and isinstance(rhs_ty, (IntType, BoolType))
        ):
            return self._emit_runtime_int_binop_value(
                op, lhs, lhs_ty, rhs, rhs_ty,
            )

        # Shortcut: bitwise ops + shifts are integer-only.
        if op in ("&", "|", "^", "<<", ">>"):
            lv = self._to_int64(lhs, lhs_ty)
            rv = self._to_int64(rhs, rhs_ty)
            if op == "&":
                return self.builder.and_(lv, rv, name=self._fresh("and"))
            if op == "|":
                return self.builder.or_(lv, rv, name=self._fresh("or"))
            if op == "^":
                return self.builder.xor(lv, rv, name=self._fresh("xor"))
            if op == "<<":
                return self.builder.shl(lv, rv, name=self._fresh("shl"))
            if op == ">>":
                return self.builder.ashr(lv, rv, name=self._fresh("ashr"))

        # Python ``/`` always returns float even if both operands are
        # integers.
        if op == "/":
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self.builder.fdiv(lf, rf, name=self._fresh("fdiv"))

        # Pick the result's IR type: float if either operand is float.
        if isinstance(lhs_ty, FloatType) or isinstance(rhs_ty, FloatType):
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self._emit_binop_float(op, lf, rf)

        # String ops: ``s * n`` / ``n * s`` → ``py_str_repeat``;
        # ``s + t`` → ``py_str_concat``. Any Dyn operand is boxed
        # via the marshal helper so the runtime's py_str_* helpers
        # see PyObject*.
        if op == "*" and (
            isinstance(lhs_ty, StrType) or isinstance(rhs_ty, StrType)
        ):
            if isinstance(lhs_ty, StrType):
                s_val, s_ty = lhs, lhs_ty
                n_val, n_ty = rhs, rhs_ty
            else:
                s_val, s_ty = rhs, rhs_ty
                n_val, n_ty = lhs, lhs_ty
            s_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, s_val, s_ty,
            )
            n_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, n_val, n_ty,
            )
            return self.builder.call(
                self.runtime["py_str_repeat"], [s_obj, n_obj],
                name=self._fresh("str.repeat"),
            )
        if op == "+" and (
            (isinstance(lhs_ty, StrType) or isinstance(rhs_ty, StrType))
            and (
                isinstance(lhs_ty, (StrType, DynType))
                and isinstance(rhs_ty, (StrType, DynType))
            )
        ):
            l_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, lhs_ty,
            )
            r_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, rhs_ty,
            )
            return self.builder.call(
                self.runtime["py_str_concat"], [l_obj, r_obj],
                name=self._fresh("str.concat"),
            )

        # Integer (and bool-as-int) path.
        lv = self._to_int64(lhs, lhs_ty)
        rv = self._to_int64(rhs, rhs_ty)
        return self._emit_binop_int(op, lv, rv)

    def _emit_binop_int(self, op: str, lv: ir.Value, rv: ir.Value) -> ir.Value:
        if op == "+":
            return self.builder.add(lv, rv, name=self._fresh("add"))
        if op == "-":
            return self.builder.sub(lv, rv, name=self._fresh("sub"))
        if op == "*":
            return self.builder.mul(lv, rv, name=self._fresh("mul"))
        if op == "//":
            return self._python_floordiv_i64(lv, rv)
        if op == "%":
            return self._python_mod_i64(lv, rv)
        if op == "**":
            # Route through the runtime ``py_int_pow`` helper. Both
            # operands box first, then unbox the result back to i64.
            lbox = self.builder.call(
                self.runtime["py_int_from_i64"], [lv],
                name=self._fresh("pow.l"),
            )
            rbox = self.builder.call(
                self.runtime["py_int_from_i64"], [rv],
                name=self._fresh("pow.r"),
            )
            pow_obj = self.builder.call(
                self.runtime["py_int_pow"], [lbox, rbox],
                name=self._fresh("int.pow"),
            )
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                pow_obj, IntType(name="int"),
            )
        raise NotImplementedError(f"Layer 1 int binop {op!r} not supported")

    def _emit_runtime_int_binop_value(
        self,
        op: str,
        lhs: ir.Value,
        lhs_ty: Type,
        rhs: ir.Value,
        rhs_ty: Type,
    ) -> ir.Value:
        fn_name = {
            "+": "py_int_add",
            "-": "py_int_sub",
            "*": "py_int_mul",
            "//": "py_int_floordiv",
            "%": "py_int_mod",
            "**": "py_int_pow",
            "&": "py_int_and",
            "|": "py_int_or",
            "^": "py_int_xor",
            "<<": "py_int_shl",
            ">>": "py_int_shr",
        }.get(op)
        if fn_name is None:
            raise NotImplementedError(f"Layer 1 int binop {op!r} not supported")
        lhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, lhs, lhs_ty,
        )
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, rhs_ty,
        )
        inline = self._emit_inline_tagged_int_binop_or_call(
            op, lhs_obj, rhs_obj, fn_name,
        )
        if inline is not None:
            return inline
        return self.builder.call(
            self.runtime[fn_name], [lhs_obj, rhs_obj],
            name=self._fresh("int.obj"),
        )

    def _emit_inline_tagged_int_binop_or_call(
        self,
        op: str,
        lhs_obj: ir.Value,
        rhs_obj: ir.Value,
        fn_name: str,
    ) -> ir.Value | None:
        if op not in ("+", "-", "&", "|", "^"):
            return None

        ptr_one = ir.Constant(_I64, 1)
        lhs_bits = self.builder.ptrtoint(
            lhs_obj, _I64, name=self._fresh("tag.l.bits"),
        )
        rhs_bits = self.builder.ptrtoint(
            rhs_obj, _I64, name=self._fresh("tag.r.bits"),
        )
        lhs_tag = self.builder.icmp_signed(
            "==",
            self.builder.and_(
                lhs_bits, ptr_one, name=self._fresh("tag.l.low"),
            ),
            ptr_one,
            name=self._fresh("tag.l.ok"),
        )
        rhs_tag = self.builder.icmp_signed(
            "==",
            self.builder.and_(
                rhs_bits, ptr_one, name=self._fresh("tag.r.low"),
            ),
            ptr_one,
            name=self._fresh("tag.r.ok"),
        )
        both_tagged = self.builder.and_(
            lhs_tag, rhs_tag, name=self._fresh("tag.both"),
        )

        fn = self.current_function
        if fn is None:
            return None
        fast_bb = fn.append_basic_block(
            name=self._fresh("int.tag.fast"),
        )
        slow_bb = fn.append_basic_block(
            name=self._fresh("int.tag.slow"),
        )
        join_bb = fn.append_basic_block(
            name=self._fresh("int.tag.join"),
        )
        self.builder.cbranch(both_tagged, fast_bb, slow_bb)

        self.builder.position_at_end(fast_bb)
        lhs_val = self.builder.ashr(
            lhs_bits, ptr_one, name=self._fresh("tag.l.val"),
        )
        rhs_val = self.builder.ashr(
            rhs_bits, ptr_one, name=self._fresh("tag.r.val"),
        )
        if op == "+":
            raw = self.builder.add(
                lhs_val, rhs_val, name=self._fresh("tag.add"),
            )
        elif op == "-":
            raw = self.builder.sub(
                lhs_val, rhs_val, name=self._fresh("tag.sub"),
            )
        elif op == "&":
            raw = self.builder.and_(
                lhs_val, rhs_val, name=self._fresh("tag.and"),
            )
        elif op == "|":
            raw = self.builder.or_(
                lhs_val, rhs_val, name=self._fresh("tag.or"),
            )
        else:
            raw = self.builder.xor(
                lhs_val, rhs_val, name=self._fresh("tag.xor"),
            )

        if op in ("+", "-"):
            min_tagged = ir.Constant(_I64, -(1 << 62))
            max_tagged = ir.Constant(_I64, (1 << 62) - 1)
            ge_min = self.builder.icmp_signed(
                ">=", raw, min_tagged, name=self._fresh("tag.ge_min"),
            )
            le_max = self.builder.icmp_signed(
                "<=", raw, max_tagged, name=self._fresh("tag.le_max"),
            )
            fits = self.builder.and_(
                ge_min, le_max, name=self._fresh("tag.fits"),
            )
            tag_bb = fn.append_basic_block(
                name=self._fresh("int.tag.pack"),
            )
            self.builder.cbranch(fits, tag_bb, slow_bb)
            self.builder.position_at_end(tag_bb)

        tag_bits = self.builder.or_(
            self.builder.shl(
                raw, ptr_one, name=self._fresh("tag.shift"),
            ),
            ptr_one,
            name=self._fresh("tag.bits"),
        )
        fast_result = self.builder.inttoptr(
            tag_bits, _CSTR, name=self._fresh("tag.ptr"),
        )
        fast_exit = self.builder._block
        self.builder.branch(join_bb)

        self.builder.position_at_end(slow_bb)
        slow_result = self.builder.call(
            self.runtime[fn_name], [lhs_obj, rhs_obj],
            name=self._fresh("int.obj"),
        )
        slow_exit = self.builder._block
        self.builder.branch(join_bb)

        self.builder.position_at_end(join_bb)
        phi = self.builder.phi(_CSTR, name=self._fresh("int.tag.result"))
        phi.add_incoming(fast_result, fast_exit)
        phi.add_incoming(slow_result, slow_exit)
        return phi

    def _emit_binop_float(self, op: str, lv: ir.Value, rv: ir.Value) -> ir.Value:
        if op == "+":
            return self.builder.fadd(lv, rv, name=self._fresh("fadd"))
        if op == "-":
            return self.builder.fsub(lv, rv, name=self._fresh("fsub"))
        if op == "*":
            return self.builder.fmul(lv, rv, name=self._fresh("fmul"))
        if op == "//":
            # Python float-floor div: floor(a / b).
            q = self.builder.fdiv(lv, rv, name=self._fresh("fdiv_q"))
            # Inline llvm.floor.f64 intrinsic.
            floor_fn = self._get_floor_intrinsic()
            return self.builder.call(floor_fn, [q], name=self._fresh("ffloor"))
        if op == "%":
            # Python float mod uses fmod + correction; simplest is to
            # call libc ``fmod`` and adjust sign.
            fmod_fn = self._get_fmod_function()
            r = self.builder.call(fmod_fn, [lv, rv], name=self._fresh("fmod"))
            # Correct sign: if (r != 0) and (sign(r) != sign(b)) → r += b.
            zero_f = ir.Constant(_DOUBLE, 0.0)
            r_nz = self.builder.fcmp_ordered("!=", r, zero_f,
                                              name=self._fresh("fmod_nz"))
            r_neg = self.builder.fcmp_ordered("<", r, zero_f,
                                              name=self._fresh("fmod_r_neg"))
            b_neg = self.builder.fcmp_ordered("<", rv, zero_f,
                                              name=self._fresh("fmod_b_neg"))
            sign_diff = self.builder.xor(r_neg, b_neg,
                                          name=self._fresh("fmod_sign_diff"))
            need_fix = self.builder.and_(r_nz, sign_diff,
                                           name=self._fresh("fmod_fix"))
            corrected = self.builder.fadd(r, rv, name=self._fresh("fmod_corr"))
            return self.builder.select(need_fix, corrected, r,
                                         name=self._fresh("fmod_res"))
        if op == "**":
            pow_fn = self._get_pow_function()
            return self.builder.call(pow_fn, [lv, rv], name=self._fresh("fpow"))
        raise NotImplementedError(f"Layer 1 float binop {op!r} not supported")

    def _python_floordiv_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed floor division on i64.

        ``q = a sdiv b; r = a srem b; if (r != 0) && ((r < 0) != (b < 0))
        then q = q - 1``.
        """
        q = self.builder.sdiv(a, b, name=self._fresh("q"))
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        one = ir.Constant(_I64, 1)
        r_nz = self.builder.icmp_signed("!=", r, zero,
                                         name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero,
                                          name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero,
                                          name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg,
                                      name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff,
                                      name=self._fresh("need_fix"))
        q_minus_1 = self.builder.sub(q, one, name=self._fresh("q_fix"))
        return self.builder.select(need_fix, q_minus_1, q,
                                     name=self._fresh("floordiv"))

    def _python_mod_i64(self, a: ir.Value, b: ir.Value) -> ir.Value:
        """Python-correct signed mod on i64; sign follows divisor.

        ``r = a srem b; if (r != 0) && ((r < 0) != (b < 0)) then r = r + b``.
        """
        r = self.builder.srem(a, b, name=self._fresh("r"))
        zero = ir.Constant(_I64, 0)
        r_nz = self.builder.icmp_signed("!=", r, zero,
                                         name=self._fresh("r_nz"))
        r_neg = self.builder.icmp_signed("<", r, zero,
                                          name=self._fresh("r_neg"))
        b_neg = self.builder.icmp_signed("<", b, zero,
                                          name=self._fresh("b_neg"))
        sign_diff = self.builder.xor(r_neg, b_neg,
                                      name=self._fresh("sign_diff"))
        need_fix = self.builder.and_(r_nz, sign_diff,
                                      name=self._fresh("need_fix"))
        r_plus_b = self.builder.add(r, b, name=self._fresh("r_fix"))
        return self.builder.select(need_fix, r_plus_b, r,
                                     name=self._fresh("mod"))

    def _get_floor_intrinsic(self) -> ir.Function:
        name = "llvm.floor.f64"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_fmod_function(self) -> ir.Function:
        name = "fmod"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE, _DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    def _get_pow_function(self) -> ir.Function:
        name = "pow"
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.Function):
            return existing
        fnty = ir.FunctionType(_DOUBLE, [_DOUBLE, _DOUBLE])
        fn = ir.Function(self.module, fnty, name=name)
        fn.linkage = "external"
        return fn

    # -- UnaryOp -------------------------------------------------------

    def _emit_unary(self, expr: UnaryOp) -> ir.Value:
        operand = self._emit_expr(expr.operand)
        ty = expr.operand.ty
        if expr.op == "+":
            return operand
        if expr.op == "-":
            if isinstance(ty, FloatType):
                zero = ir.Constant(_DOUBLE, 0.0)
                return self.builder.fsub(zero, operand,
                                           name=self._fresh("fneg"))
            if self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType)):
                operand_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, operand, ty,
                )
                return self.builder.call(
                    self.runtime["py_int_neg"], [operand_obj],
                    name=self._fresh("int.obj.neg"),
                )
            ival = self._to_int64(operand, ty)
            return self.builder.neg(ival, name=self._fresh("neg"))
        if expr.op == "~":
            if self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType)):
                operand_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, operand, ty,
                )
                minus_one = self._emit_int_literal_object(-1)
                return self.builder.call(
                    self.runtime["py_int_xor"], [operand_obj, minus_one],
                    name=self._fresh("int.obj.invert"),
                )
            ival = self._to_int64(operand, ty)
            return self.builder.not_(ival, name=self._fresh("bnot"))
        if expr.op == "not":
            b = self._truthy(operand, ty)
            return self.builder.not_(b, name=self._fresh("not"))
        raise NotImplementedError(f"Layer 1 unary {expr.op!r} not supported")

    # -- Compare -------------------------------------------------------

    def _emit_compare(self, expr: Compare) -> ir.Value:
        # Identity against None: pointer compare against @py_None.
        if expr.op in ("is", "is not"):
            return self._emit_identity_compare(expr)
        if expr.op in ("in", "not in"):
            return self._emit_membership(expr)

        if (
            self._int_exprs_are_boxed()
            and expr.op in ("==", "!=", "<", "<=", ">", ">=")
            and isinstance(expr.lhs.ty, (IntType, BoolType))
            and isinstance(expr.rhs.ty, (IntType, BoolType))
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            lhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, lhs, expr.lhs.ty,
            )
            rhs_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, rhs, expr.rhs.ty,
            )
            cmp_i32 = self.builder.call(
                self.runtime["py_int_cmp"], [lhs_obj, rhs_obj],
                name=self._fresh("int.obj.cmp"),
            )
            return self.builder.icmp_signed(
                expr.op, cmp_i32, ir.Constant(_I32, 0),
                name=self._fresh("int.obj.cmp.i1"),
            )

        exact_int_cmp = self._emit_exact_int_compare(expr)
        if exact_int_cmp is not None:
            return exact_int_cmp

        # Class-based comparison dunder fast path.
        cmp_dunder = {
            "==": "__eq__",
            "!=": "__ne__",
            "<":  "__lt__",
            "<=": "__le__",
            ">":  "__gt__",
            ">=": "__ge__",
        }.get(expr.op)
        if cmp_dunder is not None:
            dunder = self._try_dispatch_dunder_unary(
                expr.lhs, cmp_dunder, (expr.rhs,)
            )
            if dunder is not None:
                if dunder.type is _I1:
                    return dunder
                if isinstance(dunder.type, ir.IntType) and dunder.type.width > 1:
                    return self.builder.icmp_signed(
                        "!=", dunder, ir.Constant(dunder.type, 0),
                        name=self._fresh("dunder.i1"),
                    )
                if isinstance(dunder.type, ir.PointerType):
                    # Returned PyObject*: run py_obj_truthy to get i1.
                    as_i32 = self.builder.call(
                        self.runtime["py_obj_truthy"], [dunder],
                        name=self._fresh("dunder.truthy"),
                    )
                    return self.builder.trunc(
                        as_i32, _I1, name=self._fresh("dunder.truthy.i1"),
                    )
                return dunder

        lhs_ty = expr.lhs.ty
        rhs_ty = expr.rhs.ty
        lhs_looks_cpy = self._expr_looks_cpython(expr.lhs)
        rhs_looks_cpy = self._expr_looks_cpython(expr.rhs)

        if lhs_looks_cpy or rhs_looks_cpy:
            if (
                expr.op in ("==", "!=")
                and lhs_looks_cpy != rhs_looks_cpy
            ):
                cpy_expr = expr.lhs if lhs_looks_cpy else expr.rhs
                other_expr = expr.rhs if lhs_looks_cpy else expr.lhs
                if isinstance(
                    other_expr.ty,
                    (StrType, NoneType, IntType, BoolType, FloatType),
                ):
                    cpy_raw = self._emit_expr(cpy_expr)
                    cpy_obj = self._emit_value_as_pcc_object_or_bridge(
                        cpy_raw, cpy_expr.ty, "cpy.cmp.bridge",
                    )
                    other_obj = self._emit_as_object(other_expr)
                    eq = self.builder.call(
                        self.runtime["py_obj_eq"], [cpy_obj, other_obj],
                        name=self._fresh("cpy.obj.eq"),
                    )
                    eq_i1 = self.builder.icmp_signed(
                        "!=", eq, ir.Constant(_I32, 0),
                        name=self._fresh("cpy.obj.eq.i1"),
                    )
                    if expr.op == "!=":
                        return self.builder.not_(
                            eq_i1, name=self._fresh("cpy.obj.ne"),
                        )
                    return eq_i1
            recv_expr = expr.lhs
            other_expr = expr.rhs
            recv_op = expr.op
            if not lhs_looks_cpy and rhs_looks_cpy:
                recv_expr = expr.rhs
                other_expr = expr.lhs
                recv_op = {
                    "==": "==",
                    "!=": "!=",
                    "<": ">",
                    "<=": ">=",
                    ">": "<",
                    ">=": "<=",
                }.get(expr.op, expr.op)
            method_name = {
                "==": "__eq__",
                "!=": "__ne__",
                "<": "__lt__",
                "<=": "__le__",
                ">": "__gt__",
                ">=": "__ge__",
            }.get(recv_op)
            if method_name is not None:
                recv_val = self._emit_expr(recv_expr)
                recv_cpy, recv_owned = self._marshal_to_cpython(
                    recv_val, recv_expr.ty,
                )
                result = self._emit_cpy_method_call_src(
                    recv_cpy, method_name, (other_expr,),
                )
                if recv_owned:
                    self.builder.call(
                        self.runtime["py_cpy_decref"], [recv_cpy],
                    )
                as_i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"], [result],
                    name=self._fresh("cpy.cmp.i32"),
                )
                return self.builder.icmp_signed(
                    "!=", as_i32, ir.Constant(_I32, 0),
                    name=self._fresh("cpy.cmp.i1"),
                )

        # String equality → runtime py_str_eq fast path. Relational str
        # ops fall through to the generic object compare helpers.
        if (
            isinstance(lhs_ty, StrType)
            and isinstance(rhs_ty, StrType)
            and expr.op in ("==", "!=")
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            eq = self.builder.call(
                self.runtime["py_str_eq"], [lhs, rhs],
                name=self._fresh("str.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=", eq, ir.Constant(_I32, 0), name=self._fresh("str.eq.i1")
            )
            if expr.op == "!=":
                return self.builder.not_(eq_i1, name=self._fresh("str.ne"))
            return eq_i1

        if (
            expr.op in ("==", "!=")
            and (
                isinstance(lhs_ty, StrType)
                or isinstance(rhs_ty, StrType)
            )
        ):
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            if not isinstance(lhs.type, ir.PointerType):
                lhs = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    lhs, lhs_ty,
                )
            if not isinstance(rhs.type, ir.PointerType):
                rhs = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    rhs, rhs_ty,
                )
            eq = self.builder.call(
                self.runtime["py_obj_eq"], [lhs, rhs],
                name=self._fresh("obj.str.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=", eq, ir.Constant(_I32, 0),
                name=self._fresh("obj.str.eq.i1"),
            )
            if expr.op == "!=":
                return self.builder.not_(
                    eq_i1, name=self._fresh("obj.str.ne"),
                )
            return eq_i1

        # Object-vs-object equality (for two boxed operands): delegate.
        if self._is_object(lhs_ty) and self._is_object(rhs_ty):
            runtime_name = {
                "==": "py_obj_eq",
                "!=": "py_obj_eq",
                "<": "py_obj_lt",
                "<=": "py_obj_le",
                ">": "py_obj_gt",
                ">=": "py_obj_ge",
            }.get(expr.op)
            if runtime_name is None:
                raise NotImplementedError(
                    f"Layer 2 does not handle object compare op {expr.op!r}"
                )
            lhs = self._emit_expr(expr.lhs)
            rhs = self._emit_expr(expr.rhs)
            if not isinstance(lhs.type, ir.PointerType):
                lhs = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    lhs, lhs_ty,
                )
            if not isinstance(rhs.type, ir.PointerType):
                rhs = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    rhs, rhs_ty,
                )
            cmp_i32 = self.builder.call(
                self.runtime[runtime_name], [lhs, rhs],
                name=self._fresh("obj.cmp"),
            )
            cmp_i1 = self.builder.icmp_signed(
                "!=", cmp_i32, ir.Constant(_I32, 0),
                name=self._fresh("obj.cmp.i1"),
            )
            if expr.op == "!=":
                return self.builder.not_(cmp_i1, name=self._fresh("obj.ne"))
            return cmp_i1

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        if isinstance(lhs_ty, FloatType) or isinstance(rhs_ty, FloatType):
            lf = self._to_double(lhs, lhs_ty)
            rf = self._to_double(rhs, rhs_ty)
            return self.builder.fcmp_ordered(expr.op, lf, rf,
                                               name=self._fresh("fcmp"))
        lv = self._to_int64(lhs, lhs_ty)
        rv = self._to_int64(rhs, rhs_ty)
        return self.builder.icmp_signed(expr.op, lv, rv,
                                          name=self._fresh("icmp"))

    def _emit_identity_compare(self, expr: Compare) -> ir.Value:
        """``is`` / ``is not`` — pointer compare, typically against None.

        Both operands are marshalled to PyObject* and compared as
        pointers. Interning of small ints / bools is handled by the
        runtime (``py_int_from_i64`` returns the canonical global for
        small ints), so ``is`` behaves consistently with CPython on
        those.

        Fast path: if one operand is a NoneLit and the other is a native
        scalar (int/float/bool), the answer is a compile-time constant
        (False for ``is``, True for ``is not``).
        """
        # Constant-fold ``<native> is None`` and ``<native> is not None``.
        native_lhs = self._is_native_scalar_type(expr.lhs.ty)
        native_rhs = self._is_native_scalar_type(expr.rhs.ty)
        none_lhs = isinstance(expr.lhs, NoneLit) or isinstance(expr.lhs.ty, NoneType)
        none_rhs = isinstance(expr.rhs, NoneLit) or isinstance(expr.rhs.ty, NoneType)
        if (native_lhs and none_rhs) or (native_rhs and none_lhs):
            # The native value can never be literally the py_None pointer.
            return ir.Constant(_I1, 1 if expr.op == "is not" else 0)

        lhs = self._emit_expr(expr.lhs)
        rhs = self._emit_expr(expr.rhs)
        lhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, lhs, expr.lhs.ty
        )
        rhs_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, rhs, expr.rhs.ty
        )
        # Compare pointers as integers so the IR is independent of the
        # llvmlite version's pointer-compare support.
        lhs_i = self.builder.ptrtoint(lhs_obj, _I64, name=self._fresh("is.l"))
        rhs_i = self.builder.ptrtoint(rhs_obj, _I64, name=self._fresh("is.r"))
        eq = self.builder.icmp_signed(
            "==", lhs_i, rhs_i, name=self._fresh("is")
        )
        if expr.op == "is not":
            return self.builder.not_(eq, name=self._fresh("is_not"))
        return eq

    def _emit_membership(self, expr: Compare) -> ir.Value:
        """``in`` / ``not in`` over str / list / dict / set / tuple."""
        container_ty = expr.rhs.ty
        rhs = self._emit_expr(expr.rhs)
        if rhs in getattr(self, "_cpy_values", ()):
            container_cpy, container_owned = self._marshal_to_cpython(
                rhs, container_ty,
            )
            result = self._emit_cpy_method_call_src(
                container_cpy, "__contains__", (expr.lhs,),
            )
            if container_owned:
                self.builder.call(
                    self.runtime["py_cpy_decref"], [container_cpy],
                )
            as_i32 = self.builder.call(
                self.runtime["py_cpy_truthy"], [result],
                name=self._fresh("cpy.contains.i32"),
            )
            contains = self.builder.icmp_signed(
                "!=", as_i32, ir.Constant(_I32, 0),
                name=self._fresh("cpy.contains.i1"),
            )
            if expr.op == "not in":
                return self.builder.not_(
                    contains, name=self._fresh("cpy.not_in"),
                )
            return contains
        lhs = self._emit_expr(expr.lhs)
        lhs_ty = expr.lhs.ty

        if isinstance(container_ty, StrType):
            # Needle is expected to be a pcc str (single char or
            # substring). When the lhs type is DynType (e.g. a
            # comprehension loop variable bound by ``for ch in s``
            # where the comp-scope inference didn't propagate the
            # element type), we still have a ``PyObject*`` — py_str_*
            # helpers tolerate foreign types by length/bytes compare.
            needle = lhs
            if not isinstance(lhs.type, ir.PointerType):
                needle = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, lhs, lhs_ty
                )
            res_i32 = self.builder.call(
                self.runtime["py_str_contains"], [rhs, needle],
                name=self._fresh("str.in"),
            )
        elif isinstance(container_ty, ListType):
            needle = self._emit_value_as_pcc_object_or_bridge(
                lhs, lhs_ty, "cpy.list.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_list_contains"], [rhs, needle],
                name=self._fresh("list.in"),
            )
        elif isinstance(container_ty, DictType):
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs, lhs_ty, "cpy.dict.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_dict_contains"], [rhs, key],
                name=self._fresh("dict.in"),
            )
        elif isinstance(container_ty, TupleType):
            # Tuple literal fast path: unroll against static elements.
            # General tuple values can use the runtime's generic
            # ``py_obj_contains`` dispatcher, which already handles
            # tuple containers via linear scan.
            if isinstance(expr.rhs, TupleExpr):
                return self._emit_membership_tuple_literal(
                    lhs, lhs_ty, expr.rhs, negate=(expr.op == "not in")
                )
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs, lhs_ty, "cpy.tuple.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"], [rhs, key],
                name=self._fresh("tuple.in"),
            )
        elif isinstance(container_ty, DynType):
            # DynType container — route through the runtime
            # ``py_obj_contains`` dispatcher. Accepts any pcc-native
            # container type at runtime; no libpython needed.
            key = self._emit_value_as_pcc_object_or_bridge(
                lhs, lhs_ty, "cpy.obj.in.key",
            )
            res_i32 = self.builder.call(
                self.runtime["py_obj_contains"], [rhs, key],
                name=self._fresh("obj.in"),
            )
            result = self.builder.icmp_signed(
                "!=", res_i32, ir.Constant(_I32, 0),
                name=self._fresh("obj.in.i1"),
            )
            if expr.op == "not in":
                result = self.builder.not_(
                    result, name=self._fresh("obj.notin"),
                )
            return result
        else:
            # Other object fallback — not yet wired.
            raise NotImplementedError(
                f"Layer 2 'in' on type {type(container_ty).__name__} "
                "needs L3"
            )

        res = self.builder.icmp_signed(
            "!=", res_i32, ir.Constant(_I32, 0), name=self._fresh("in.i1")
        )
        if expr.op == "not in":
            return self.builder.not_(res, name=self._fresh("not_in"))
        return res

    def _emit_membership_tuple_literal(
        self, lhs: ir.Value, lhs_ty: Type, rhs: TupleExpr, negate: bool
    ) -> ir.Value:
        """Unroll ``x in (a, b, c)`` as ``x==a or x==b or x==c``."""
        lhs_obj = self._emit_value_as_pcc_object_or_bridge(
            lhs, lhs_ty, "cpy.tup.lit.in.key",
        )
        acc: Optional[ir.Value] = None
        for el in rhs.elems:
            v = self._emit_expr(el)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, el.ty
            )
            eq_i32 = self.builder.call(
                self.runtime["py_obj_eq"], [lhs_obj, v_obj],
                name=self._fresh("tup.eq"),
            )
            eq_i1 = self.builder.icmp_signed(
                "!=", eq_i32, ir.Constant(_I32, 0),
                name=self._fresh("tup.eq.i1"),
            )
            if acc is None:
                acc = eq_i1
            else:
                acc = self.builder.or_(acc, eq_i1, name=self._fresh("tup.or"))
        if acc is None:
            # Empty tuple: ``x in ()`` is always False.
            acc = ir.Constant(_I1, 0)
        if negate:
            return self.builder.not_(acc, name=self._fresh("tup.not_in"))
        return acc

    # -- BoolExpr ------------------------------------------------------

    def _emit_boolexpr(self, expr: BoolExpr) -> ir.Value:
        # Short-circuit via branch. ``and`` / ``or`` return either the
        # left operand or the right operand; only the pure-bool case
        # should collapse to i1.
        fn = self.current_function

        lhs = self._emit_expr(expr.left)
        lhs_b = self._truthy(lhs, expr.left.ty)
        result_ty = expr.ty
        lhs_val = None
        if not isinstance(result_ty, BoolType):
            lhs_val = self._coerce(lhs, expr.left.ty, result_ty)

        rhs_bb = fn.append_basic_block(name=self._fresh("bool.rhs"))
        end_bb = fn.append_basic_block(name=self._fresh("bool.end"))
        entry_bb = self.builder._block

        if expr.op == "and":
            # if lhs then compute rhs else short-circuit false.
            self.builder.cbranch(lhs_b, rhs_bb, end_bb)
        elif expr.op == "or":
            # if lhs then short-circuit true else compute rhs.
            self.builder.cbranch(lhs_b, end_bb, rhs_bb)
        else:
            raise NotImplementedError(
                f"Layer 1 bool op {expr.op!r} not supported"
            )

        if not isinstance(result_ty, BoolType):
            self.builder.position_at_end(rhs_bb)
            rhs = self._emit_expr(expr.right)
            rhs_val = self._coerce(rhs, expr.right.ty, result_ty)
            rhs_exit = self.builder._block
            self.builder.branch(end_bb)

            self.builder.position_at_end(end_bb)
            phi = self.builder.phi(
                self._storage_ir_type(result_ty), name=self._fresh(expr.op)
            )
            phi.add_incoming(lhs_val, entry_bb)
            phi.add_incoming(rhs_val, rhs_exit)
            return phi

        self.builder.position_at_end(rhs_bb)
        rhs = self._emit_expr(expr.right)
        rhs_b = self._truthy(rhs, expr.right.ty)
        rhs_exit = self.builder._block
        self.builder.branch(end_bb)

        self.builder.position_at_end(end_bb)
        phi = self.builder.phi(_I1, name=self._fresh(expr.op))
        if expr.op == "and":
            phi.add_incoming(ir.Constant(_I1, 0), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        else:  # "or"
            phi.add_incoming(ir.Constant(_I1, 1), entry_bb)
            phi.add_incoming(rhs_b, rhs_exit)
        return phi

    # -- Call ----------------------------------------------------------

    def _emit_call(self, expr: Call) -> ir.Value:
        native_sys_exit_call = self._emit_native_sys_exit_call(expr)
        if native_sys_exit_call is not None:
            return native_sys_exit_call
        native_replace_call = self._emit_native_dataclasses_replace_call(expr)
        if native_replace_call is not None:
            return native_replace_call
        if isinstance(expr.func, Attr):
            return self._emit_method_call(expr)
        if not isinstance(expr.func, Name):
            fn_val = self._emit_expr(expr.func)
            if fn_val not in getattr(self, "_cpy_values", ()):
                fn_val = self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [fn_val],
                    name=self._fresh("cpy.callable"),
                )
            if expr.kwargs:
                return self._finish_cpy_call_kw(
                    fn_val, "expr", expr.args, expr.kwargs,
                )
            return self._emit_cpy_func_call(fn_val, "expr", expr.args)
        name = expr.func.ident
        unsafe_intrinsic = self._unsafe_intrinsic_for_name(name)
        if unsafe_intrinsic is not None:
            return self._emit_unsafe_intrinsic_call(unsafe_intrinsic, expr)
        # Comprehension sentinels emitted by the parser. Lowered to an
        # explicit loop that appends into a runtime list/dict/set.
        if name in ("__listcomp__", "_list_comp", "_gen_comp", "__genexpr__"):
            # Generator expressions eagerly materialise to a list —
            # pcc doesn't support lazy generators yet; the common use
            # sites (``sum(x for x in xs)``, ``"".join(s for …)``)
            # iterate the result once so a list works identically.
            return self._emit_comprehension(expr, "list")
        if name in ("__setcomp__", "_set_comp"):
            return self._emit_comprehension(expr, "set")
        if name in ("__dictcomp__", "_dict_comp"):
            return self._emit_comprehension(expr, "dict")
        # print() has a bespoke kwarg parser (sep=, end=) handled inline.
        if name == "print":
            self._emit_print_call(expr)
            return ir.Constant(_I1, 0)
        if name == "open":
            native_open = self._emit_native_open_call(expr)
            if native_open is not None:
                return native_open
        # Builtins below don't support kwargs — reject early.
        if expr.kwargs and name in ("range", "xrange", "len", "str", "isinstance"):
            raise NotImplementedError(
                f"Layer 1 builtin {name}() does not accept keyword args"
            )
        if name in ("range", "xrange"):
            raise NotImplementedError(
                "Layer 1 only supports range() inside 'for'"
            )
        if name == "_walrus":
            return self._emit_walrus(expr)
        if name == "len":
            return self._emit_len_call(expr)
        if name == "str":
            return self._emit_str_builtin(expr)
        if name == "dict":
            return self._emit_dict_builtin(expr)
        if name == "list" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("list.new"),
            )
        if name == "set" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_set_new"], [],
                name=self._fresh("set.new"),
            )
        if name == "tuple" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_tuple_new"], [ir.Constant(_I64, 0)],
                name=self._fresh("tuple.new"),
            )
        if name == "isinstance":
            return self._emit_isinstance_call(expr)
        # ``field(default_factory=F)`` from ``dataclasses.field``
        # appears as the RHS of a dataclass body assign. At codegen
        # time we collapse it to a call of ``F()``. The other
        # ``field`` kwargs (init, repr, ...) are informational — pcc
        # doesn't vary emission based on them.
        if name == "field" and not expr.args:
            for k, v in expr.kwargs:
                if k == "default_factory":
                    if isinstance(v, Name):
                        # Known builtin factories.
                        if v.ident == "list":
                            return self.builder.call(
                                self.runtime["py_list_new"],
                                [ir.Constant(_I64, 0)],
                                name=self._fresh("field.list"),
                            )
                        if v.ident == "dict":
                            return self.builder.call(
                                self.runtime["py_dict_new"], [],
                                name=self._fresh("field.dict"),
                            )
                        if v.ident == "set":
                            return self.builder.call(
                                self.runtime["py_set_new"], [],
                                name=self._fresh("field.set"),
                            )
                        if v.ident == "tuple":
                            return self.builder.call(
                                self.runtime["py_tuple_new"],
                                [ir.Constant(_I64, 0)],
                                name=self._fresh("field.tuple"),
                            )
                    # Unknown factory — attempt to call it as a
                    # user function. Falls back to regular dispatch.
                    return self._emit_call(Call(
                        span=expr.span, ty=expr.ty,
                        func=v, args=(), kwargs=(),
                    ))
            # No default_factory → default value (None).
            return ir.Constant(_CSTR, None)
        # ``cls(args)`` inside a @classmethod body — treat as a
        # normal instantiation of the owning class. pcc doesn't
        # support calling arbitrary ``cls`` pointers yet, so we
        # resolve to the enclosing class statically. Note: gated on
        # ``current_method_kind == "classmethod"`` so that a local
        # re-bind ``cls = SomeClass if cond else OtherClass`` inside
        # an instance method doesn't masquerade as the classmethod
        # receiver.
        if (
            name == "cls"
            and "cls" in self.env
            and getattr(self, "current_class", None) is not None
            and getattr(self, "current_method_kind", None) == "classmethod"
        ):
            args = expr.args
            if expr.kwargs:
                # Walk the class's MRO looking for an ``__init__`` —
                # dataclass inheritance means SSAConstant may inherit
                # SSAValue's synthesized init. ``_resolve_method_mro``
                # already handles that walk.
                mro_info = self._resolve_method_mro(
                    self.current_class.name, "__init__",
                )
                init_fd = None
                if mro_info is not None:
                    init_fd = self.class_lowering._find_method_def(
                        mro_info.name, "__init__",
                    )
                if init_fd is not None:
                    args = tuple(self._resolve_call_kwargs(
                        expr.args, expr.kwargs, init_fd.args,
                        skip_self=True,
                    ))
                else:
                    args = expr.args  # fallthrough to original
            return self.class_lowering.emit_instantiate(
                self.current_class.name, args, self,
            )
        if name in ("min", "max") and not expr.kwargs and len(expr.args) == 2:
            return self._emit_min_max_builtin(expr, name)
        if name in ("min", "max") and not expr.kwargs and len(expr.args) == 1:
            result = self._maybe_emit_min_max_iter(expr, name)
            if result is not None:
                return result
        if name == "abs" and len(expr.args) == 1:
            return self._emit_abs_builtin(expr)
        if name in ("any", "all") and len(expr.args) == 1:
            result = self._maybe_emit_any_all_literal(expr, name)
            if result is not None:
                return result
        if name == "sum" and 1 <= len(expr.args) <= 2:
            result = self._maybe_emit_sum_literal(expr)
            if result is not None:
                return result
        if name == "zip":
            result = self._maybe_emit_zip_builtin(expr)
            if result is not None:
                return result
        if name == "next":
            result = self._maybe_emit_next_builtin(expr)
            if result is not None:
                return result
        if name == "int" and 1 <= len(expr.args) <= 2:
            result = self._maybe_emit_int_builtin(expr)
            if result is not None:
                return result
        if name == "bool" and len(expr.args) == 1:
            # ``bool(x)`` — truthiness check; reuse ``_truthy`` on the
            # operand's type. Zero args (``bool()`` → ``False``)
            # handled trivially.
            v = self._emit_expr(expr.args[0])
            return self._truthy(v, expr.args[0].ty)
        if name == "bool" and not expr.args:
            return ir.Constant(_I1, 0)
        if name == "chr" and len(expr.args) == 1 and not expr.kwargs:
            v = self._emit_expr(expr.args[0])
            ty = expr.args[0].ty
            if isinstance(ty, BoolType) and v.type is _I1:
                v = self.builder.zext(
                    v, _I64, name=self._fresh("chr.from_bool"),
                )
            elif isinstance(ty, IntType):
                v = self._to_int64(v, ty)
            else:
                v = None
            if v is not None:
                return self.builder.call(
                    self.runtime["py_chr_from_i64"], [v],
                    name=self._fresh("chr"),
                )
        if name == "float" and len(expr.args) == 1:
            arg = expr.args[0]
            ty = arg.ty
            if isinstance(ty, FloatType):
                return self._emit_expr(arg)
            if isinstance(ty, (IntType, BoolType)):
                v = self._emit_expr(arg)
                if v.type is _I1:
                    v = self.builder.zext(
                        v, _I64, name=self._fresh("float.from_bool"),
                    )
                return self.builder.sitofp(
                    v, _DOUBLE, name=self._fresh("float.from_int"),
                )
            # Issue 11.A.2: ``float("inf")`` / ``float("-inf")`` /
            # ``float("nan")`` and other StrLit args fold to a native
            # constant at codegen time so we don't pull libpython for
            # what should be a compile-time literal.
            if isinstance(arg, StrLit):
                folded = _maybe_fold_str_to_float(arg.value)
                if folded is not None:
                    return ir.Constant(_DOUBLE, folded)
        if name in ("set", "frozenset") and len(expr.args) <= 1:
            # pcc has no distinct ``frozenset`` runtime type; treat
            # as ``set`` — immutable vs mutable doesn't matter for
            # the compile-free pcc path since we don't mutate the
            # constant containers declared as module globals.
            result = self._maybe_emit_set_builtin(expr)
            if result is not None:
                return result
        if name == "list" and len(expr.args) <= 1:
            result = self._maybe_emit_list_builtin(expr)
            if result is not None:
                return result
        if name == "tuple" and len(expr.args) <= 1:
            result = self._maybe_emit_tuple_builtin(expr)
            if result is not None:
                return result
        if name == "dict" and len(expr.args) <= 1:
            result = self._maybe_emit_dict_builtin(expr)
            if result is not None:
                return result
        if name == "sorted" and len(expr.args) == 1 and not expr.kwargs:
            src_val = self._emit_expr(expr.args[0])
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, expr.args[0].ty,
            )
            return self.builder.call(
                self.runtime["py_obj_sorted"], [src_obj],
                name=self._fresh("sorted"),
            )
        if name == "repr" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_repr"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("repr"),
            )
        if name == "hash" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_hash"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("hash"),
            )
        if name == "id" and len(expr.args) == 1:
            v = self._emit_as_object(expr.args[0])
            return self.builder.ptrtoint(
                v, _I64, name=self._fresh("id"),
            )
        if name == "hasattr" and len(expr.args) == 2:
            # ``hasattr(x, "name")`` — pcc doesn't distinguish "missing"
            # from "present but None" without full dunder support, but
            # for the common usage (gate on attribute existence) the
            # presence-check via py_obj_getattr returning non-NULL
            # works on pcc-native classes.
            if self._expr_looks_cpython(expr.args[0]):
                fn_val = self._load_cpython_builtin("hasattr")
                got = self._emit_cpy_func_call(
                    fn_val, "hasattr", tuple(expr.args),
                )
                as_i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"], [got],
                    name=self._fresh("hasattr.cpy.i32"),
                )
                return self.builder.icmp_signed(
                    "!=", as_i32, ir.Constant(_I32, 0),
                    name=self._fresh("hasattr.cpy.i1"),
                )
            obj = self._emit_as_object(expr.args[0])
            nm = expr.args[1]
            if isinstance(nm, StrLit):
                name_ptr = self._attr_name_ptr(nm.value)
            else:
                nv = self._emit_expr(nm)
                n_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    nv, nm.ty,
                )
                name_ptr = self.builder.call(
                    self.runtime["py_str_utf8"], [n_obj],
                    name=self._fresh("hasattr.name"),
                )
            got = self.builder.call(
                self.runtime["py_obj_getattr"], [obj, name_ptr],
                name=self._fresh("hasattr.got"),
            )
            null = ir.Constant(_CSTR, None)
            return self.builder.icmp_signed(
                "!=", got, null, name=self._fresh("hasattr.i1"),
            )
        if name == "ord" and len(expr.args) == 1:
            # ``ord(s)`` where s is a one-char str. Return the first
            # Unicode codepoint, matching CPython for valid pcc strings.
            ord_arg = expr.args[0]
            if (
                isinstance(ord_arg, Subscript)
                and not isinstance(ord_arg.idx, Slice)
                and isinstance(ord_arg.obj.ty, StrType)
            ):
                s_val = self._emit_expr(ord_arg.obj)
                idx_val = self._emit_expr_as_i64(ord_arg.idx)
                return self.builder.call(
                    self.runtime["py_str_ord_at_i64"], [s_val, idx_val],
                    name=self._fresh("ord.at"),
                )
            s_val = self._emit_as_object(ord_arg)
            return self.builder.call(
                self.runtime["py_str_ord"], [s_val],
                name=self._fresh("ord"),
            )
        if (
            self.ast_module.name == "pcc.parse.py_lex"
            and name == "_pcc_str_byte_len"
            and len(expr.args) == 1
        ):
            s_val = self._emit_expr(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_byte_len"], [s_val],
                name=self._fresh("str.byte_len"),
            )
        if (
            self.ast_module.name == "pcc.parse.py_lex"
            and name == "_pcc_str_byte_at"
            and len(expr.args) == 2
        ):
            s_val = self._emit_expr(expr.args[0])
            idx_val = self._emit_expr_as_i64(expr.args[1])
            return self.builder.call(
                self.runtime["py_str_byte_at_i64"], [s_val, idx_val],
                name=self._fresh("str.byte_at"),
            )
        if (
            self.ast_module.name == "pcc.parse.py_lex"
            and name == "_pcc_str_byte_slice"
            and len(expr.args) == 3
        ):
            s_val = self._emit_expr(expr.args[0])
            lo_val = self._emit_expr_as_i64(expr.args[1])
            hi_val = self._emit_expr_as_i64(expr.args[2])
            return self.builder.call(
                self.runtime["py_str_byte_slice_i64"],
                [s_val, lo_val, hi_val],
                name=self._fresh("str.byte_slice"),
            )
        if name == "getattr" and 2 <= len(expr.args) <= 3:
            return self._emit_getattr_builtin(expr)
        if name == "type" and len(expr.args) == 1:
            return self._emit_type_builtin(expr)

        # Extern-C direct call (P6C.1): name bound to extern("symbol"...).
        extern_decls = getattr(self, "_extern_decls", {})
        if name in extern_decls:
            if expr.kwargs:
                raise NotImplementedError(
                    "Layer 1 extern-C calls do not accept keyword args"
                )
            return self._emit_extern_call(extern_decls[name], expr.args)

        # User class instantiation: ``MyClass(args)``.
        class_name = self._resolve_class_alias(name)
        if (
            hasattr(self, "class_lowering")
            and class_name in self.class_lowering.classes
        ):
            def attach_hoisted_class_captures(inst: ir.Value) -> ir.Value:
                class_caps = getattr(
                    self, "_hoisted_class_capture_params", {},
                ).get(class_name, ())
                for fv in class_caps:
                    cap_expr = Name(
                        span=expr.span, ty=DynType(name="dyn"), ident=fv,
                    )
                    raw_v = self._emit_name(cap_expr)
                    v_obj = marshal.marshal_to_object(
                        self.builder, self.module, self.runtime,
                        raw_v, cap_expr.ty,
                    )
                    self.builder.call(
                        self.runtime["py_obj_setattr"],
                        [
                            inst,
                            self._attr_name_ptr(f"__pcc_cap_{fv}"),
                            v_obj,
                        ],
                    )
                return inst

            resolved_args = expr.args
            init_fd = self.class_lowering._find_method_def(
                class_name, "__init__"
            )
            if init_fd is None:
                # Walk MRO for an inherited __init__.
                mro_info = self._resolve_method_mro(class_name, "__init__")
                if mro_info is not None:
                    init_fd = self.class_lowering._find_method_def(
                        mro_info.name, "__init__",
                    )
            if init_fd is None:
                inst = self._emit_no_init_field_instance(
                    class_name, expr.args, expr.kwargs,
                )
                if inst is not None:
                    return attach_hoisted_class_captures(inst)
                if expr.kwargs:
                    from ..py_ast import (
                        Assign as _AssignAst,
                        ClassDef as _ClassDefAst,
                        Name as _NameAst,
                    )
                    info = self.class_lowering.classes.get(class_name)
                    cd = info.expanded_cd if info is not None else None
                    if cd is None:
                        for stmt in self.ast_module.body:
                            if (
                                isinstance(stmt, _ClassDefAst)
                                and stmt.name == class_name
                            ):
                                cd = stmt
                                break
                    has_fields_decl = False
                    if cd is not None:
                        for body_stmt in cd.body:
                            if not isinstance(body_stmt, _AssignAst):
                                continue
                            if any(
                                isinstance(t, _NameAst) and t.ident == "_fields_"
                                for t in body_stmt.targets
                            ):
                                has_fields_decl = True
                                break
                    if has_fields_decl:
                        inst = attach_hoisted_class_captures(
                            self.class_lowering.emit_instantiate(
                                class_name, expr.args, self,
                            )
                        )
                        for kw_name, kw_expr in expr.kwargs:
                            raw_v = self._emit_expr(kw_expr)
                            v_obj = marshal.marshal_to_object(
                                self.builder, self.module, self.runtime,
                                raw_v, kw_expr.ty,
                            )
                            self.builder.call(
                                self.runtime["py_obj_setattr"],
                                [inst, self._attr_name_ptr(kw_name), v_obj],
                            )
                        return inst
                    raise NotImplementedError(
                        f"class {class_name!r} with kwargs needs __init__ "
                        "to resolve parameter names"
                    )
            else:
                resolved_args = tuple(self._resolve_call_kwargs(
                    expr.args, expr.kwargs, init_fd.args, skip_self=True,
                ))
            return attach_hoisted_class_captures(
                self.class_lowering.emit_instantiate(
                    class_name, resolved_args, self,
                )
            )

        # Callable instance via ``__call__`` — ``double(5)`` where
        # ``double`` was assigned a class instance that defines
        # ``__call__``.
        if hasattr(self, "env_class_hint"):
            hint = self.env_class_hint.get(name)
            if hint is not None:
                info = self._resolve_method_mro(hint, "__call__")
                if info is not None:
                    obj_val = self._emit_name(Name(
                        span=expr.span, ty=DynType(name="dyn"), ident=name,
                    ))
                    method_fn = info.methods["__call__"]
                    return self._emit_direct_method_call(
                        method_fn, obj_val, info, "__call__", expr.args,
                        kwargs=expr.kwargs,
                    )

        fn = self.functions.get(name)
        if fn is None:
            # CPython-backed callable (e.g. a ``from .sibling import
            # foo`` where ``foo`` isn't a native-sibling FuncDef)
            # dispatches via PyObject_Call. Pulls libpython but is
            # correct for the import route.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(name)
            if cpy_gv is not None:
                fn_val = self.builder.load(
                    cpy_gv, name=self._fresh(f"cpy.fn.{name}"),
                )
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val, name, expr.args, expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            star_val = self._load_from_cpy_star_imports(name)
            if star_val is not None:
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        star_val, name, expr.args, expr.kwargs,
                    )
                return self._emit_cpy_func_call(star_val, name, expr.args)
            # Fallback: route well-known CPython stdlib builtins
            # (``open`` / ``iter`` / ``next`` / ``sorted`` / ``zip`` /
            # ``super`` / ``hasattr`` / etc.) through the libpython
            # fallback. Pulls libpython into the link step but lets
            # the solo-compile survey keep advancing on files that
            # use those callables.
            if name in _CPY_BUILTIN_FALLBACK:
                fn_val = self._load_cpython_builtin(name)
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val, name, expr.args, expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            # Local variable holding a callable (e.g. ``klass = self.
            # _select_struct_union_class(p[1]); klass(args)``). The
            # binding lives in env / module_globals; route through
            # ``py_cpy_call*`` with the local's pointer value as the
            # callable. Works for anything CPython can invoke — user
            # classes, CPython callables, etc. Pulls libpython for
            # this dispatch.
            if name in self.env or name in getattr(self, "_module_globals", {}):
                fn_val = self._emit_name(
                    Name(span=expr.span, ty=DynType(name="dyn"), ident=name),
                )
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val, name, expr.args, expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            raise NotImplementedError(
                f"Layer 1 unknown function {name!r}; builtins other than "
                "print/range/len/str need L2/L3"
            )
        ast_func_def = self._find_user_funcdef(name)
        # Click-decorated entry functions (``@click.command``,
        # ``@click.pass_context``) expose params like
        # ``main(ctx, path, ...)`` that pcc treats as required, but
        # the module's own ``if __name__ == "__main__": main()`` call
        # invokes with no args because click fills them at runtime.
        # Synthesize NoneLit defaults for missing args when the callee
        # carries a click decorator — the ``main()`` call is compiled
        # but never actually exercised unless the binary is run as a
        # script (and in that case click's runtime wrapper supplies
        # the values).
        has_click_decorator = any(
            self._decorator_is_noop_whitelist(d)
            and (self._decorator_qualname(d) or "").startswith("click.")
            for d in ast_func_def.decorators
        )
        call_kwargs = expr.kwargs
        hoist_caps = getattr(self, "_hoisted_capture_params", {}).get(name)
        if hoist_caps:
            present_kw = {k for k, _ in call_kwargs}
            extra_kw = tuple(
                (fv, Name(span=expr.span, ty=DynType(name="dyn"), ident=fv))
                for fv in hoist_caps
                if fv not in present_kw
            )
            if extra_kw:
                call_kwargs = call_kwargs + extra_kw
        if has_click_decorator:
            from ..py_ast import NoneLit as _NL, Arg as _Arg
            patched = tuple(
                (
                    a if a.default is not None
                    else _replace_arg_with_none_default(a)
                )
                for a in ast_func_def.args
            )
            try:
                resolved_args = self._resolve_call_kwargs(
                    expr.args, call_kwargs, patched,
                )
            except L1CodegenError:
                resolved_args = self._resolve_call_kwargs(
                    expr.args, call_kwargs, ast_func_def.args,
                )
        else:
            resolved_args = self._resolve_call_kwargs(
                expr.args, call_kwargs, ast_func_def.args,
            )
        runtime_formals = [a for a in ast_func_def.args if a.name != ""]
        args_ir: list[ir.Value] = []
        for idx, (ast_arg, arg_def) in enumerate(
            zip(resolved_args, runtime_formals)
        ):
            param_ir_ty = fn.args[idx].type
            target_ty = arg_def.annotation or DynType(name="dyn")
            if (
                isinstance(target_ty, IntType)
                and isinstance(param_ir_ty, ir.PointerType)
            ):
                v = self._emit_exact_int_operand_object(ast_arg)
            else:
                v = self._emit_expr(ast_arg)
                v = self._coerce(v, ast_arg.ty, target_ty)
            args_ir.append(v)
        call_name = "" if isinstance(fn.function_type.return_type, ir.VoidType) \
                       else self._fresh(f"{name}_ret")
        result = self._call_user(fn, args_ir, call_name)
        if self._user_func_returns_cpython(
            ast_func_def, runtime_formals, resolved_args,
        ):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
        return result

    def _expr_looks_cpython(self, expr: Expr) -> bool:
        """Best-effort predicate for expressions that already produce a
        CPython PyObject* at runtime."""
        if isinstance(expr, Name):
            if expr.ident in getattr(self, "_cpy_module_env", {}):
                return True
            if expr.ident in getattr(self, "_cpy_star_module_env", {}):
                return True
            if expr.ident in getattr(self, "_native_module_aliases", {}):
                return True
            if getattr(self, "_cpy_module_flags", {}).get(expr.ident, False):
                return True
            if getattr(self, "_cpy_env_flags", {}).get(expr.ident, False):
                return True
            return False
        if isinstance(expr, Attr):
            return self._expr_looks_cpython(expr.obj)
        if isinstance(expr, Subscript):
            return self._expr_looks_cpython(expr.obj)
        if isinstance(expr, Call):
            if isinstance(expr.func, Name):
                if expr.func.ident == "getattr" and expr.args:
                    return self._expr_looks_cpython(expr.args[0])
                if expr.func.ident in _CPY_BUILTIN_FALLBACK:
                    return True
                return self._expr_looks_cpython(expr.func)
            if isinstance(expr.func, Attr):
                return self._expr_looks_cpython(expr.func.obj)
            return False
        return False

    def _collect_return_exprs(self, stmts: tuple[Stmt, ...]) -> list[Expr]:
        """Best-effort recursive return collector for a user function."""
        out: list[Expr] = []

        def _walk(block: tuple[Stmt, ...]) -> None:
            for stmt in block:
                if isinstance(stmt, Return):
                    if stmt.value is not None:
                        out.append(stmt.value)
                    continue
                if isinstance(stmt, (If, While, For)):
                    _walk(stmt.body)
                    _walk(stmt.else_body)
                    continue
                if isinstance(stmt, Try):
                    _walk(stmt.body)
                    for handler in stmt.handlers:
                        _walk(handler.body)
                    _walk(stmt.else_body)
                    _walk(stmt.finally_body)
                    continue
                if isinstance(stmt, With):
                    _walk(stmt.body)

        _walk(stmts)
        return out

    def _callable_expr_returns_cpython(self, expr: Expr) -> bool:
        """Best-effort predicate for callables whose call result stays
        in CPython object space."""
        from ..py_ast import Lambda as _Lambda

        if isinstance(expr, _Lambda):
            return self._expr_looks_cpython(expr.body)
        if isinstance(expr, Name):
            try:
                return self._user_func_returns_cpython(
                    self._find_user_funcdef(expr.ident),
                )
            except L1CodegenError:
                return False
        return False

    def _return_expr_looks_cpython(
        self,
        expr: Expr,
        call_arg_map: dict[str, Expr],
    ) -> bool:
        if self._expr_looks_cpython(expr):
            return True
        if isinstance(expr, Call) and isinstance(expr.func, Name):
            if expr.func.ident == "getattr" and expr.args:
                return self._expr_looks_cpython(expr.args[0])
            actual = call_arg_map.get(expr.func.ident)
            if actual is not None:
                return self._callable_expr_returns_cpython(actual)
        return False

    def _user_func_returns_cpython(
        self,
        ast_fd,
        formals = (),
        actual_args: Optional[list[Expr]] = None,
    ) -> bool:
        """Return True when ``ast_fd`` obviously returns a CPython value.

        This is intentionally narrow; it exists to preserve CPython
        result tagging across direct calls to hoisted nested helpers
        synthesized from lambdas / local defs, plus thin wrappers like
        ``_timed(..., fn)`` that return ``fn()``.
        """
        ret_exprs = self._collect_return_exprs(getattr(ast_fd, "body", ()))
        if not ret_exprs:
            return False
        call_arg_map: dict[str, Expr] = {}
        if actual_args is not None:
            for formal, actual in zip(formals, actual_args):
                if getattr(formal, "name", ""):
                    call_arg_map[formal.name] = actual
        return all(
            self._return_expr_looks_cpython(expr, call_arg_map)
            for expr in ret_exprs
        )

    def _call_user(
        self,
        fn: ir.Function,
        args_ir: list[ir.Value],
        call_name: str,
    ) -> ir.Value:
        """Call a user function; after the call, check py_err_occurred()
        and branch to the active error-propagation block if a Python
        exception is pending. When we're inside a try block that block
        is the except-dispatch (self._try_err_block); otherwise it is
        the enclosing function's err-exit epilogue.

        This replaces an earlier Itanium-ABI design that used `invoke`
        + landingpad to route exceptions via libc++abi. Return-code
        style (CPython ceval.c) is portable, debuggable, and keeps
        libc++abi out of the runtime link.
        """
        result = self.builder.call(fn, args_ir, name=call_name)
        self._emit_post_call_err_check()
        return result

    def _emit_post_call_err_check(self) -> None:
        """After any call that could raise a Python exception, emit
        `if (py_err_occurred()) goto err_target` where err_target is
        the try's handler block or (fallback) the function's
        err-exit epilogue.

        Suppressed inside @c_abi_export-marked runtime functions: they
        may be invoked while TLS already holds a pending exception
        (e.g. inside the except-handler dispatch), and a spurious
        check would misinterpret that as "the internal helper raised".
        Runtime functions propagate errors via explicit NULL-return
        plus the caller's own check, matching the cc-C runtime.
        """
        cur_fn = self.current_function
        if cur_fn is not None and cur_fn.name in self._c_abi_export_symbols:
            return
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        err_fn = self.runtime.get("py_err_occurred")
        if err_fn is None:
            # Declare lazily: i64 (void). i64 matches the pcc-Python
            # port's default `int` lowering so the runtime-abi table
            # and the Python-emitted function agree.
            err_ty = ir.FunctionType(_I64, [])
            err_fn = ir.Function(self.module, err_ty, name="py_err_occurred")
            err_fn.linkage = "external"
            self.runtime["py_err_occurred"] = err_fn
        is_err = self.builder.call(
            err_fn, [], name=self._fresh("err.flag"),
        )
        cmp = self.builder.icmp_signed(
            "!=", is_err, ir.Constant(_I64, 0), name=self._fresh("err.cmp"),
        )
        parent_fn = self.current_function
        cont = parent_fn.append_basic_block(name=self._fresh("call.cont"))
        self.builder.cbranch(cmp, err_target, cont)
        self.builder.position_at_end(cont)

    def _ensure_fn_err_exit(self) -> ir.Block:
        """Return the current function's error-exit epilogue block,
        creating it on first use. The epilogue returns the function's
        sentinel value (NULL for PyObject*, 0 for integer return
        types, undef void for void returns).
        """
        fn = self.current_function
        fn_name = fn.name
        existing = self._fn_err_exit_blocks.get(fn_name)
        if existing is not None:
            return existing
        err_bb = fn.append_basic_block(name="err.exit")
        # Position a small builder at err_bb to emit the sentinel return.
        save_block = self.builder._block
        self.builder.position_at_end(err_bb)
        ret_ty = fn.function_type.return_type
        if isinstance(ret_ty, ir.VoidType):
            self.builder.ret_void()
        elif isinstance(ret_ty, ir.PointerType):
            self.builder.ret(ir.Constant(ret_ty, None))
        elif isinstance(ret_ty, ir.IntType):
            if getattr(fn, "name", "") == "main":
                exc = self.builder.call(
                    self.runtime["py_current_exception"], [],
                    name=self._fresh("unhandled.exc"),
                )
                self.builder.call(
                    self.runtime["py_exc_print_unhandled"], [exc],
                )
                self.builder.call(self.runtime["py_clear_exception"], [])
                self.builder.ret(ir.Constant(ret_ty, 1))
            else:
                self.builder.ret(ir.Constant(ret_ty, 0))
        else:
            # Unsupported return type for error-path sentinel; fall
            # back to `unreachable` so at least the build surface fails
            # obviously if it ever triggers (shouldn't happen for pcc's
            # current emitted types).
            self.builder.unreachable()
        self._fn_err_exit_blocks[fn_name] = err_bb
        self.builder.position_at_end(save_block)
        return err_bb

    def _emit_direct_user_function_call(
        self,
        *,
        display_name: str,
        fn: ir.Function,
        ast_func_def: FuncDef,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
        hoist_capture_name: Optional[str] = None,
    ) -> ir.Value:
        """Shared direct-call lowering for user / extern-native
        functions that already resolved to a concrete IR function."""
        has_click_decorator = any(
            self._decorator_is_noop_whitelist(d)
            and (self._decorator_qualname(d) or "").startswith("click.")
            for d in ast_func_def.decorators
        )
        call_kwargs = kwargs
        hoist_key = (
            hoist_capture_name if hoist_capture_name is not None
            else display_name
        )
        hoist_caps = getattr(self, "_hoisted_capture_params", {}).get(hoist_key)
        if hoist_caps:
            present_kw = {k for k, _ in call_kwargs}
            extra_kw = tuple(
                (fv, Name(span=ast_arg.span, ty=DynType(name="dyn"), ident=fv))
                for ast_arg in args[:1] or (
                    Name(
                        span=ast_func_def.span,
                        ty=DynType(name="dyn"),
                        ident="__unused__",
                    ),
                )
                for fv in hoist_caps
                if fv not in present_kw
            )
            if extra_kw:
                call_kwargs = call_kwargs + extra_kw
        if has_click_decorator:
            patched = tuple(
                (
                    a if a.default is not None
                    else _replace_arg_with_none_default(a)
                )
                for a in ast_func_def.args
            )
            try:
                resolved_args = self._resolve_call_kwargs(
                    args, call_kwargs, patched,
                )
            except L1CodegenError:
                resolved_args = self._resolve_call_kwargs(
                    args, call_kwargs, ast_func_def.args,
                )
        else:
            resolved_args = self._resolve_call_kwargs(
                args, call_kwargs, ast_func_def.args,
            )
        runtime_formals = [a for a in ast_func_def.args if a.name != ""]
        args_ir: list[ir.Value] = []
        for idx, (ast_arg, arg_def) in enumerate(
            zip(resolved_args, runtime_formals)
        ):
            param_ir_ty = fn.args[idx].type
            target_ty = arg_def.annotation or DynType(name="dyn")
            if (
                isinstance(target_ty, IntType)
                and isinstance(param_ir_ty, ir.PointerType)
            ):
                v = self._emit_exact_int_operand_object(ast_arg)
            else:
                v = self._emit_expr(ast_arg)
                v = self._coerce(v, ast_arg.ty, target_ty)
            args_ir.append(v)
        call_name = "" if isinstance(fn.function_type.return_type, ir.VoidType) \
                       else self._fresh(f"{display_name}_ret")
        result = self._call_user(fn, args_ir, call_name)
        if self._user_func_returns_cpython(
            ast_func_def, runtime_formals, resolved_args,
        ):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
        return result

    def _call_would_use_callee_defaults(
        self,
        positional: tuple[Expr, ...],
        kwargs_pairs: tuple[tuple[str, Expr], ...],
        formal_args: tuple[Arg, ...],
        *,
        skip_self: bool = False,
    ) -> bool:
        """Best-effort test for calls that would need caller-side
        default filling.

        Cross-module direct calls cannot safely inline default
        expressions when those defaults reference names from the
        callee's module scope. For those cases, callers should fall
        back to the CPython-backed module path instead."""
        formals = list(formal_args)
        if skip_self and formals:
            formals = formals[1:]
        formals = [f for f in formals if f.name != ""]
        resolved = [False] * len(formals)
        var_pos_idx = next(
            (i for i, f in enumerate(formals) if f.kind == "*args"), None,
        )
        var_kw_idx = next(
            (i for i, f in enumerate(formals) if f.kind == "**kwargs"), None,
        )
        pos_formal_indices = [
            i for i, f in enumerate(formals)
            if f.kind in ("pos", "pos_only")
        ]
        for i, _expr in enumerate(positional):
            if i < len(pos_formal_indices):
                resolved[pos_formal_indices[i]] = True
                continue
            if var_pos_idx is not None:
                continue
            return False
        name_to_idx = {
            f.name: i
            for i, f in enumerate(formals)
            if f.kind not in ("*args", "**kwargs")
        }
        for kw_name, _kw_expr in kwargs_pairs:
            idx = name_to_idx.get(kw_name)
            if idx is None:
                if var_kw_idx is not None:
                    continue
                return False
            if formals[idx].kind == "pos_only" or resolved[idx]:
                return False
            resolved[idx] = True
        for i, formal in enumerate(formals):
            if resolved[i]:
                continue
            if formal.kind in ("*args", "**kwargs"):
                continue
            return True
        return False

    # Method names so specific to LLVM IR types that any receiver
    # passing the same name should route through scaffold dispatch.
    # Catches ``self.current_function.append_basic_block(...)``,
    # ``phi.add_incoming(...)``, ``ty.as_pointer()`` patterns without
    # full receiver-type tracking.
    _IR_UNAMBIGUOUS_METHODS = frozenset({
        "append_basic_block",
        "add_incoming",
        "as_pointer",
    })

    def _ir_scaffold_target(self, attr: Attr) -> Optional[str]:
        """Detect whether ``attr`` is an IR method call shape.

        Returns the recognised method name when:
        - ``self.builder.METHOD`` / ``builder.METHOD`` /
          ``parent.builder.METHOD`` / ``self.parent.builder.METHOD``
          and METHOD is in ``_IR_BUILDER_METHODS`` (general case), OR
        - ``<any expr>.METHOD`` and METHOD is in
          ``_IR_UNAMBIGUOUS_METHODS`` (chained-receiver IR-specific
          methods).
        """
        if not isinstance(attr, Attr):
            return None
        if attr.name in self._IR_UNAMBIGUOUS_METHODS:
            return attr.name
        if attr.name not in _IR_BUILDER_METHODS:
            return None
        obj = attr.obj
        # ``self.builder.METHOD`` / ``parent.builder.METHOD`` /
        # ``self.parent.builder.METHOD``.  The latter two cover
        # ClassLowering helper methods that emit through their owning
        # L1CodeGen instance.
        if (
            isinstance(obj, Attr)
            and obj.name == "builder"
            and (
                (
                    isinstance(obj.obj, Name)
                    and obj.obj.ident in ("self", "parent")
                )
                or (
                    isinstance(obj.obj, Attr)
                    and obj.obj.name == "parent"
                    and isinstance(obj.obj.obj, Name)
                    and obj.obj.obj.ident == "self"
                )
            )
        ):
            return attr.name
        # ``builder.METHOD`` (legacy local alias) and locals assigned
        # from ``ir.IRBuilder(...)``.
        if isinstance(obj, Name) and (
            obj.ident == "builder"
            or getattr(self, "_ir_builder_env_flags", {}).get(obj.ident, False)
        ):
            return attr.name
        return None

    def _ir_module_symbol_target(self, attr: Attr) -> Optional[str]:
        """Detect whether ``attr`` is an ``ir.SYMBOL`` constructor.

        Returns the recognised symbol name (e.g. ``IntType``,
        ``PointerType``) if ``attr`` looks like ``ir.SYMBOL`` and
        SYMBOL is in the recognised ``ir.X`` set.
        """
        if not isinstance(attr, Attr):
            return None
        if attr.name not in _IR_MODULE_SYMBOLS:
            return None
        if isinstance(attr.obj, Name) and attr.obj.ident == "ir":
            return attr.name
        return None

    def _expr_is_ir_builder_ctor(self, expr: Expr) -> bool:
        return (
            isinstance(expr, Call)
            and isinstance(expr.func, Attr)
            and self._ir_module_symbol_target(expr.func) == "IRBuilder"
        )

    def _maybe_emit_ir_scaffold_call(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """Path A scaffold dispatch entry point.

        Called from ``_emit_method_call`` when ``ir_scaffold_mode ==
        'on'``. Returns the lowered ``ir.Value`` if the call site
        matches a scaffold pattern AND the targeted method is
        implemented. Raises :class:`ScaffoldUnsupportedError` when the
        pattern matches but the method has not yet been migrated.
        Returns ``None`` when the call site is unrelated to scaffold
        lowering (caller falls through to existing dispatch).
        """
        attr = expr.func
        assert isinstance(attr, Attr)

        method = self._ir_scaffold_target(attr)
        if method is not None:
            if method in _IR_SCAFFOLD_METHOD_IMPL:
                return self._emit_ir_scaffold_method(method, expr)
            raise ScaffoldUnsupportedError(
                f"IRBuilder method '{method}' has no scaffold lowering "
                f"yet (ir_scaffold_mode=on). Add it to "
                f"_IR_SCAFFOLD_METHOD_IMPL and implement "
                f"_emit_ir_scaffold_method, or compile this file with "
                f"--ir-scaffold=off until coverage lands."
            )

        symbol = self._ir_module_symbol_target(attr)
        if symbol is not None:
            if symbol in _IR_SCAFFOLD_SYMBOL_IMPL:
                return self._emit_ir_scaffold_symbol(symbol, expr)
            raise ScaffoldUnsupportedError(
                f"ir.{symbol} has no scaffold lowering yet "
                f"(ir_scaffold_mode=on). Add it to "
                f"_IR_SCAFFOLD_SYMBOL_IMPL and implement "
                f"_emit_ir_scaffold_symbol, or compile this file with "
                f"--ir-scaffold=off until coverage lands."
            )

        return None

    def _emit_ir_scaffold_method(
        self, method: str, expr: Call,
    ) -> ir.Value:
        """Dispatch implemented IRBuilder methods to their lowering.

        Simple-shape methods (fixed arity, all-pointer args, void or
        ptr return) come from ``_IR_SCAFFOLD_SIMPLE_METHODS``. Methods
        with non-trivial argument shapes (variadic ``call``/``gep``,
        ``phi``, ``landingpad``) get bespoke handlers below.
        """
        if method == "alloca":
            return self._emit_scaffold_alloca(expr)
        sig = _IR_SCAFFOLD_SIMPLE_METHODS.get(method)
        if sig is not None:
            return self._emit_scaffold_simple(method, expr, sig)
        if method == "call":
            return self._emit_scaffold_call(expr)
        if method == "gep":
            return self._emit_scaffold_gep(expr)
        if method == "phi":
            return self._emit_scaffold_phi(expr)
        if method == "landingpad":
            return self._emit_scaffold_landingpad(expr)
        if method == "append_basic_block":
            return self._emit_scaffold_append_basic_block(expr)
        raise ScaffoldUnsupportedError(
            f"_emit_ir_scaffold_method dispatch for {method!r} not "
            f"yet wired"
        )

    def _scaffold_unfold_list_literal(
        self, expr: Expr, what: str,
    ) -> list:
        """Pull the literal element list out of a ``ListExpr`` /
        ``TupleExpr`` argument. Scaffold mode rejects non-literal
        sequences for variadic methods because there's no way to fold
        runtime-iterated args into a fixed-arity extern signature
        without reintroducing dynamic dispatch.
        """
        if isinstance(expr, (ListExpr, TupleExpr)):
            return list(expr.elems)
        raise ScaffoldUnsupportedError(
            f"scaffold {what} requires a literal list/tuple of args; "
            f"got {type(expr).__name__}. Inline the list at the call "
            f"site or factor the dynamic case out before migrating."
        )

    def _emit_scaffold_call(self, expr: Call) -> ir.Value:
        """Lower ``builder.call(fn, args)``.

        Two paths:
        - Literal args list/tuple → per-arity ``call<N>`` variant
          (preferred — fully static).
        - Non-literal args (Name pointing to a list) → ``call`` with
          a dyn-list handle — the call dispatch is static, the list
          construction may still use py_cpy_*. Relief valve for
          per-file migration when a literal isn't immediately
          available.

        ``pcc.llvm_capi.ir.IRBuilder.call`` is the natively-compiled
        target. Per-arity variants and the dyn fallback assume
        helpers in ``pcc.llvm_capi.ir`` (or its scaffold helpers
        module) named ``IRBuilder_call<N>`` / ``IRBuilder_call_dyn``;
        these are introduced when Task 24 lands the multi-file pull.
        """
        for key, _ in expr.kwargs:
            if key not in self._SCAFFOLD_IGNORABLE_KWARGS:
                raise ScaffoldUnsupportedError(
                    f"builder.call(...) scaffold accepts only "
                    f"{sorted(self._SCAFFOLD_IGNORABLE_KWARGS)} kwargs; "
                    f"got {key!r}"
                )
        if len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                f"builder.call expects (fn, args); got "
                f"{len(expr.args)}"
            )
        receiver = self._scaffold_to_handle(expr.func.obj)
        fn_handle = self._scaffold_to_handle(expr.args[0])
        args_expr = expr.args[1]
        if isinstance(args_expr, (ListExpr, TupleExpr)):
            arg_handles = [
                self._scaffold_to_handle(a) for a in args_expr.elems
            ]
            n = len(arg_handles)
            extern_name = f"{self._IR_BUILDER_SYMBOL_PREFIX}call{n}"
            param_tys = [_CSTR, _CSTR] + [_CSTR] * n
            fn = self._declare_external_function(
                extern_name, _CSTR, param_tys,
            )
            return self.builder.call(
                fn, [receiver, fn_handle] + arg_handles,
                name=self._fresh("scaffold.call"),
            )
        # Non-literal args: dynamic-list extern. Args list still
        # gets constructed via py_cpy_* so this is a partial win,
        # but better than full py_cpy_call dispatch on the call site
        # itself.
        list_handle = self._scaffold_to_handle(args_expr)
        fn = self._declare_external_function(
            f"{self._IR_BUILDER_SYMBOL_PREFIX}call_dyn",
            _CSTR, [_CSTR, _CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [receiver, fn_handle, list_handle],
            name=self._fresh("scaffold.call_dyn"),
        )

    def _emit_scaffold_alloca(self, expr: Call) -> ir.Value:
        """Lower ``builder.alloca(ty, size=None, name="")``.

        The compiled ``pcc.llvm_capi.ir.IRBuilder.alloca`` method has
        four native parameters: ``self``, ``ty``, ``size`` and ``name``.
        Scaffold lowering must therefore fill Python defaults at the
        call site instead of treating ``name=`` as an ignorable SSA hint.
        """
        if not (1 <= len(expr.args) <= 3):
            raise ScaffoldUnsupportedError(
                f"builder.alloca expects 1-3 positional args; got "
                f"{len(expr.args)}"
            )
        ty_expr = expr.args[0]
        size_expr: Expr | None = None
        name_expr: Expr | None = None
        if len(expr.args) >= 2:
            size_expr = expr.args[1]
        if len(expr.args) >= 3:
            name_expr = expr.args[2]
        for key, val in expr.kwargs:
            if key == "size":
                if size_expr is not None:
                    raise ScaffoldUnsupportedError(
                        "builder.alloca got multiple values for 'size'"
                    )
                size_expr = val
                continue
            if key == "name":
                if name_expr is not None:
                    raise ScaffoldUnsupportedError(
                        "builder.alloca got multiple values for 'name'"
                    )
                name_expr = val
                continue
            raise ScaffoldUnsupportedError(
                f"builder.alloca(...) scaffold accepts only 'size' and "
                f"'name' kwargs; got {key!r}"
            )

        receiver = self._scaffold_to_handle(expr.func.obj)
        ty_h = self._scaffold_to_handle(ty_expr)
        if size_expr is None:
            size_h = self._emit_none_literal()
        else:
            size_h = self._scaffold_to_handle(size_expr)
        if name_expr is None:
            name_h = self._emit_literal_str("")
        else:
            name_h = self._scaffold_to_handle(name_expr)

        fn = self._declare_external_function(
            f"{self._IR_BUILDER_SYMBOL_PREFIX}alloca",
            _CSTR, [_CSTR, _CSTR, _CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [receiver, ty_h, size_h, name_h],
            name=self._fresh("scaffold.alloca"),
        )

    def _emit_scaffold_gep(self, expr: Call) -> ir.Value:
        """Lower ``builder.gep(ptr, indices, inbounds=...)``.

        Literal indices list → per-arity ``gep<N>[_inbounds]`` variant.
        Non-literal → ``gep_dyn[_inbounds]``.
        """
        inbounds = False
        for key, val in expr.kwargs:
            if key in self._SCAFFOLD_IGNORABLE_KWARGS:
                continue
            if key == "inbounds":
                if isinstance(val, BoolLit):
                    inbounds = bool(val.value)
                    continue
                raise ScaffoldUnsupportedError(
                    "builder.gep(inbounds=...) scaffold requires a "
                    "literal True/False; dynamic value not supported"
                )
            raise ScaffoldUnsupportedError(
                f"builder.gep(...) scaffold accepts kwargs "
                f"name/align/.../inbounds; got {key!r}"
            )
        if len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                f"builder.gep expects (ptr, indices); got "
                f"{len(expr.args)}"
            )
        receiver = self._scaffold_to_handle(expr.func.obj)
        ptr_handle = self._scaffold_to_handle(expr.args[0])
        idx_expr = expr.args[1]
        suffix = "_inbounds" if inbounds else ""
        if isinstance(idx_expr, (ListExpr, TupleExpr)):
            idx_handles = [
                self._scaffold_to_handle(a) for a in idx_expr.elems
            ]
            n = len(idx_handles)
            extern_name = f"{self._IR_BUILDER_SYMBOL_PREFIX}gep{n}{suffix}"
            param_tys = [_CSTR, _CSTR] + [_CSTR] * n
            fn = self._declare_external_function(
                extern_name, _CSTR, param_tys,
            )
            return self.builder.call(
                fn, [receiver, ptr_handle] + idx_handles,
                name=self._fresh("scaffold.gep"),
            )
        list_handle = self._scaffold_to_handle(idx_expr)
        extern_name = f"{self._IR_BUILDER_SYMBOL_PREFIX}gep_dyn{suffix}"
        fn = self._declare_external_function(
            extern_name, _CSTR, [_CSTR, _CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [receiver, ptr_handle, list_handle],
            name=self._fresh("scaffold.gep_dyn"),
        )

    def _emit_scaffold_phi(self, expr: Call) -> ir.Value:
        """Lower ``builder.phi(ty)`` (the IRBuilder.phi shape — incoming
        edges are added later via ``.add_incoming(...)``).

        For Phase 3, only the constructor call shape is recognised.
        ``add_incoming`` would route through the same scaffold (Phase
        16+ if it surfaces).
        """
        name_expr = None
        for key, val in expr.kwargs:
            if key == "name":
                name_expr = val
                continue
            if key != "name":
                raise ScaffoldUnsupportedError(
                    f"builder.phi(...) scaffold ignores kwargs "
                    f"except 'name'; got {key!r}"
                )
        if not (1 <= len(expr.args) <= 2):
            raise ScaffoldUnsupportedError(
                f"builder.phi expects 1-2 positional args; got "
                f"{len(expr.args)}"
            )
        if len(expr.args) == 2:
            if name_expr is not None:
                raise ScaffoldUnsupportedError(
                    "builder.phi got multiple values for 'name'"
                )
            name_expr = expr.args[1]
        receiver = self._scaffold_to_handle(expr.func.obj)
        ty_handle = self._scaffold_to_handle(expr.args[0])
        name_h = (
            self._emit_literal_str("")
            if name_expr is None else self._scaffold_to_handle(name_expr)
        )
        fn = self._declare_external_function(
            f"{self._IR_BUILDER_SYMBOL_PREFIX}phi",
            _CSTR, [_CSTR, _CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [receiver, ty_handle, name_h],
            name=self._fresh("scaffold.phi"),
        )

    def _emit_scaffold_append_basic_block(self, expr: Call) -> ir.Value:
        """Lower ``<receiver>.append_basic_block([name], name=...)``.

        Accepts 0 or 1 positional name plus an optional ``name``
        kwarg. Always emit the same extern (matching the natively-
        compiled ``IRBuilder.append_basic_block(name="")``); when
        no name is given, synthesize an empty-string handle as the
        default. Receiver may be IRBuilder or Function — both have
        the same method signature in pcc.llvm_capi.ir.
        """
        for key, _ in expr.kwargs:
            if key not in self._SCAFFOLD_IGNORABLE_KWARGS and key != "name":
                raise ScaffoldUnsupportedError(
                    f"append_basic_block(...) scaffold accepts only "
                    f"'name' (positional or kwarg); got {key!r}"
                )
        if len(expr.args) > 1:
            raise ScaffoldUnsupportedError(
                f"append_basic_block expects 0 or 1 positional args; "
                f"got {len(expr.args)}"
            )
        name_expr = None
        if len(expr.args) == 1:
            name_expr = expr.args[0]
        else:
            for key, val in expr.kwargs:
                if key == "name":
                    name_expr = val
        receiver = self._scaffold_to_handle(expr.func.obj)
        if name_expr is None:
            name_h = self._emit_literal_str("")
        else:
            name_h = self._scaffold_to_handle(name_expr)
        receiver_expr = expr.func.obj
        suffix = "scaffold_Function_append_basic_block"
        if (
            isinstance(receiver_expr, Name)
            and (
                receiver_expr.ident == "builder"
                or getattr(self, "_ir_builder_env_flags", {}).get(
                    receiver_expr.ident, False,
                )
            )
        ):
            suffix = "scaffold_IRBuilder_append_basic_block"
        elif (
            isinstance(receiver_expr, Attr)
            and receiver_expr.name == "builder"
        ):
            suffix = "scaffold_IRBuilder_append_basic_block"
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}{suffix}",
            _CSTR, [_CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [receiver, name_h],
            name=self._fresh("scaffold.append_basic_block"),
        )

    def _emit_scaffold_landingpad(self, expr: Call) -> ir.Value:
        """Lower ``builder.landingpad(ty)``. Cleanup / catch clauses are
        added by separate methods (out of Phase 3 scope)."""
        name_expr = None
        cleanup_value = ir.Constant(_I1, 0)
        for key, val in expr.kwargs:
            if key == "name":
                name_expr = val
                continue
            if key == "cleanup":
                if isinstance(val, BoolLit):
                    cleanup_value = ir.Constant(_I1, 1 if val.value else 0)
                    continue
                raise ScaffoldUnsupportedError(
                    "builder.landingpad(cleanup=...) scaffold requires "
                    "a literal bool"
                )
            if key != "name":
                raise ScaffoldUnsupportedError(
                    f"builder.landingpad(...) scaffold ignores kwargs "
                    f"except 'name'/'cleanup'; got {key!r}"
                )
        if not (1 <= len(expr.args) <= 3):
            raise ScaffoldUnsupportedError(
                f"builder.landingpad expects 1-3 positional args; got "
                f"{len(expr.args)}"
            )
        if len(expr.args) >= 2:
            if name_expr is not None:
                raise ScaffoldUnsupportedError(
                    "builder.landingpad got multiple values for 'name'"
                )
            name_expr = expr.args[1]
        if len(expr.args) == 3:
            if isinstance(expr.args[2], BoolLit):
                cleanup_value = ir.Constant(
                    _I1, 1 if expr.args[2].value else 0,
                )
            else:
                raise ScaffoldUnsupportedError(
                    "builder.landingpad positional cleanup scaffold "
                    "requires a literal bool"
                )
        receiver = self._scaffold_to_handle(expr.func.obj)
        ty_handle = self._scaffold_to_handle(expr.args[0])
        name_h = (
            self._emit_literal_str("")
            if name_expr is None else self._scaffold_to_handle(name_expr)
        )
        fn = self._declare_external_function(
            f"{self._IR_BUILDER_SYMBOL_PREFIX}landingpad",
            _CSTR, [_CSTR, _CSTR, _CSTR, _I1],
        )
        return self.builder.call(
            fn, [receiver, ty_handle, name_h, cleanup_value],
            name=self._fresh("scaffold.landingpad"),
        )

    def _scaffold_to_handle(self, expr: Expr) -> ir.Value:
        """Lower ``expr`` to an opaque ``i8*`` handle for scaffold
        extern calls. Lowers via the regular ``_emit_expr`` then
        bitcasts/inttoptrs the result to ``i8*`` regardless of source
        type.

        Different source types reach the C runtime as different opaque
        pointers — the C side treats them as black boxes (the runtime
        is the source of truth for what each handle represents).
        """
        value = self._emit_expr(expr)
        ty = value.type
        if isinstance(ty, ir.PointerType):
            return self.builder.bitcast(value, _CSTR)
        if isinstance(ty, ir.IntType):
            return self.builder.inttoptr(value, _CSTR)
        if isinstance(ty, ir.DoubleType):
            # Bitcast double → i64 (same bit width), then inttoptr to
            # i8*. Preserves bits so the C runtime can re-interpret if
            # it knows the slot was originally a double.
            as_i64 = self.builder.bitcast(value, _I64)
            return self.builder.inttoptr(as_i64, _CSTR)
        if isinstance(ty, ir.VoidType):
            raise ScaffoldUnsupportedError(
                "scaffold extern call argument is void"
            )
        raise ScaffoldUnsupportedError(
            f"scaffold extern call argument has unsupported type "
            f"{ty} ({type(ty).__name__})"
        )

    # kwargs the simple dispatcher accepts and discards (no semantic
    # impact at scaffold level — the C runtime re-derives them from
    # types/contextual info). Loud-fail on any kwarg outside this set
    # so per-file migration sees the gap.
    _SCAFFOLD_IGNORABLE_KWARGS = frozenset({
        "name",       # SSA naming hint
        "align",      # alignment hint on load/store
        "flags",      # FP / int flag bag
        "fastmath",   # FP fast-math bag
        "tail",       # tail-call hint on call
        "cconv",      # calling convention
    })

    # Pcc-Python symbol mangling for cross-module references. The
    # natively-compiled pcc.llvm_capi.ir uses these names. Path A
    # routes scaffold dispatch through them so the produced IR
    # references the REAL functions provided by pcc.llvm_capi —
    # not synthetic placeholders.
    _IR_BUILDER_SYMBOL_PREFIX = "user_pcc_llvm_capi_ir_IRBuilder_"
    _IR_TOPLEVEL_SYMBOL_PREFIX = "user_pcc_llvm_capi_ir_"

    def _emit_scaffold_simple(
        self,
        method: str,
        expr: Call,
        sig: tuple,
    ) -> ir.Value:
        """Lower a simple-shape IRBuilder method via table dispatch.

        Emits ``call <ret> @user_pcc_llvm_capi_ir_IRBuilder_<method>(
        builder, args...)`` — references the real natively-compiled
        method provided by ``pcc.llvm_capi.ir``. Multi-file compile
        (Phase 6 Task 24) pulls that file into the same link so the
        symbol is satisfied; without that, link would fail with
        undefined symbol — that's the next step's problem.

        Optional Python parameters are made explicit here. The native
        pcc-compiled method symbols use the full Python signature, so
        a source call like ``builder.load(ptr, name="x")`` must become
        ``IRBuilder_load(builder, ptr, "x", None)`` rather than a
        short ABI call with only the required operands.
        """
        return_kind, required_count = sig
        optional_params = _IR_SCAFFOLD_METHOD_OPTIONAL_PARAMS.get(
            method, (),
        )
        if not (
            required_count
            <= len(expr.args)
            <= required_count + len(optional_params)
        ):
            raise ScaffoldUnsupportedError(
                f"builder.{method} expects {required_count}"
                f"-{required_count + len(optional_params)} positional "
                f"args; got {len(expr.args)}"
            )

        required_args = expr.args[:required_count]
        optional_values: dict = {}
        for idx, arg in enumerate(expr.args[required_count:]):
            optional_values[optional_params[idx]] = arg

        for key, val in expr.kwargs:
            if key in optional_params:
                if key in optional_values:
                    raise ScaffoldUnsupportedError(
                        f"builder.{method} got multiple values for "
                        f"{key!r}"
                    )
                optional_values[key] = val
                continue
            if key in self._SCAFFOLD_IGNORABLE_KWARGS:
                continue
            raise ScaffoldUnsupportedError(
                f"builder.{method}(...) scaffold accepts only "
                f"{list(optional_params)} kwargs; got {key!r}"
            )

        receiver = self._scaffold_to_handle(expr.func.obj)
        lowered_args = [self._scaffold_to_handle(a) for a in required_args]
        for param in optional_params:
            val = optional_values.get(param)
            if val is None:
                if param == "name":
                    lowered_args.append(self._emit_literal_str(""))
                    continue
                if param in ("align", "syncscope"):
                    lowered_args.append(self._emit_none_literal())
                    continue
                raise ScaffoldUnsupportedError(
                    f"builder.{method} has no scaffold default for "
                    f"{param!r}"
                )
            lowered_args.append(self._scaffold_to_handle(val))

        ret_ty = _VOID if return_kind == "void" else _CSTR
        param_tys = [_CSTR] * (1 + len(lowered_args))
        extern_name = self._IR_BUILDER_SYMBOL_PREFIX + method
        fn = self._declare_external_function(extern_name, ret_ty, param_tys)
        if return_kind == "void":
            self.builder.call(fn, [receiver] + lowered_args)
            return ir.Constant(_CSTR, None)
        return self.builder.call(
            fn, [receiver] + lowered_args,
            name=self._fresh(f"scaffold.{method}"),
        )

    def _emit_ir_scaffold_symbol(
        self, symbol: str, expr: Call,
    ) -> ir.Value:
        """Dispatch implemented ``ir.X`` symbols to their lowering.

        Simple-shape symbols (fixed arity, all-pointer args) come
        from ``_IR_SCAFFOLD_SIMPLE_SYMBOLS``. Variadic constructors
        (``Function``, ``LiteralStructType``) get bespoke handlers.
        """
        if symbol == "IntType":
            return self._emit_scaffold_int_type(expr)
        if symbol == "PointerType":
            return self._emit_scaffold_pointer_type(expr)
        if symbol == "ArrayType":
            return self._emit_scaffold_array_type(expr)
        if symbol == "Constant":
            return self._emit_scaffold_constant(expr)
        if symbol == "IRBuilder":
            return self._emit_scaffold_irbuilder_ctor(expr)
        if symbol == "Context":
            return self._emit_scaffold_context_ctor(expr)
        if symbol == "IdentifiedStructType":
            return self._emit_scaffold_identified_struct_type(expr)
        sig = _IR_SCAFFOLD_SIMPLE_SYMBOLS.get(symbol)
        if sig is not None:
            arity, accepts_name = sig
            return self._emit_scaffold_simple_symbol(
                symbol, expr, arity, accepts_name,
            )
        if symbol == "Function":
            return self._emit_scaffold_function_ctor(expr)
        if symbol == "LiteralStructType":
            return self._emit_scaffold_literal_struct(expr)
        if symbol == "FunctionType":
            return self._emit_scaffold_function_type_ctor(expr)
        raise ScaffoldUnsupportedError(
            f"_emit_ir_scaffold_symbol dispatch for {symbol!r} not "
            f"yet wired"
        )

    def _emit_scaffold_int_type(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 1:
            raise ScaffoldUnsupportedError("ir.IntType expects one width arg")
        width = self._emit_expr_as_i64(expr.args[0])
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_IntType",
            _CSTR, [_I64],
        )
        return self.builder.call(
            fn, [width], name=self._fresh("scaffold.IntType"),
        )

    def _emit_scaffold_pointer_type(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 1:
            raise ScaffoldUnsupportedError(
                "ir.PointerType scaffold expects one pointee arg"
            )
        pointee = self._scaffold_to_handle(expr.args[0])
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_PointerType",
            _CSTR, [_CSTR],
        )
        return self.builder.call(
            fn, [pointee], name=self._fresh("scaffold.PointerType"),
        )

    def _emit_scaffold_array_type(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                "ir.ArrayType scaffold expects (element, count)"
            )
        element = self._scaffold_to_handle(expr.args[0])
        count = self._emit_expr_as_i64(expr.args[1])
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_ArrayType",
            _CSTR, [_CSTR, _I64],
        )
        return self.builder.call(
            fn, [element, count], name=self._fresh("scaffold.ArrayType"),
        )

    def _emit_scaffold_constant(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                "ir.Constant scaffold expects (ty, value)"
            )
        ty = self._scaffold_to_handle(expr.args[0])
        value_expr = expr.args[1]
        if isinstance(value_expr, NoneLit):
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_none",
                _CSTR, [_CSTR],
            )
            return self.builder.call(
                fn, [ty], name=self._fresh("scaffold.Constant"),
            )
        if isinstance(value_expr, FloatLit):
            value = self._emit_expr(value_expr)
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_f64",
                _CSTR, [_CSTR, _DOUBLE],
            )
            return self.builder.call(
                fn, [ty, value], name=self._fresh("scaffold.Constant"),
            )
        if isinstance(value_expr, (IntLit, BoolLit)):
            value = self._emit_expr_as_i64(value_expr)
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_i64",
                _CSTR, [_CSTR, _I64],
            )
            return self.builder.call(
                fn, [ty, value], name=self._fresh("scaffold.Constant"),
            )
        if isinstance(value_expr.ty, (IntType, BoolType)):
            value = self._emit_expr_as_i64(value_expr)
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_i64",
                _CSTR, [_CSTR, _I64],
            )
            return self.builder.call(
                fn, [ty, value], name=self._fresh("scaffold.Constant"),
            )
        if isinstance(value_expr.ty, FloatType):
            raw = self._emit_expr(value_expr)
            value = self._to_double(raw, value_expr.ty)
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_f64",
                _CSTR, [_CSTR, _DOUBLE],
            )
            return self.builder.call(
                fn, [ty, value], name=self._fresh("scaffold.Constant"),
            )
        raw_value = self._emit_expr(value_expr)
        if isinstance(raw_value.type, ir.IntType):
            value = raw_value
            if raw_value.type.width < 64:
                value = self.builder.sext(
                    raw_value, _I64, name=self._fresh("scaffold.const.i64")
                )
            elif raw_value.type.width > 64:
                value = self.builder.trunc(
                    raw_value, _I64, name=self._fresh("scaffold.const.i64")
                )
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_i64",
                _CSTR, [_CSTR, _I64],
            )
            return self.builder.call(
                fn, [ty, value], name=self._fresh("scaffold.Constant"),
            )
        if raw_value.type is _DOUBLE:
            fn = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_f64",
                _CSTR, [_CSTR, _DOUBLE],
            )
            return self.builder.call(
                fn, [ty, raw_value], name=self._fresh("scaffold.Constant"),
            )
        if not isinstance(raw_value.type, ir.PointerType):
            raise ScaffoldUnsupportedError(
                "ir.Constant scaffold value has unsupported IR type "
                f"{raw_value.type}"
            )
        value = self.builder.bitcast(raw_value, _CSTR)
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Constant_obj",
            _CSTR, [_CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [ty, value], name=self._fresh("scaffold.Constant"),
        )

    def _emit_scaffold_irbuilder_ctor(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 1:
            raise ScaffoldUnsupportedError(
                "ir.IRBuilder scaffold expects one block arg"
            )
        block = self._scaffold_to_handle(expr.args[0])
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_IRBuilder",
            _CSTR, [_CSTR],
        )
        return self.builder.call(
            fn, [block], name=self._fresh("scaffold.IRBuilder"),
        )

    def _emit_scaffold_context_ctor(self, expr: Call) -> ir.Value:
        if expr.kwargs or expr.args:
            raise ScaffoldUnsupportedError("ir.Context scaffold expects no args")
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_Context",
            _CSTR, [],
        )
        return self.builder.call(
            fn, [], name=self._fresh("scaffold.Context"),
        )

    def _emit_scaffold_identified_struct_type(self, expr: Call) -> ir.Value:
        if expr.kwargs or len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                "ir.IdentifiedStructType scaffold expects (context, name)"
            )
        context = self._scaffold_to_handle(expr.args[0])
        name = self._scaffold_to_handle(expr.args[1])
        fn = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}scaffold_IdentifiedStructType",
            _CSTR, [_CSTR, _CSTR],
        )
        return self.builder.call(
            fn, [context, name],
            name=self._fresh("scaffold.IdentifiedStructType"),
        )

    def _emit_scaffold_simple_symbol(
        self, symbol: str, expr: Call, arity: int,
        accepts_name: bool,
    ) -> ir.Value:
        """Lower an ``ir.X(args...)`` constructor with fixed arity to
        ``call ptr @user_pcc_llvm_capi_ir_<symbol>___init__(arg0, ...)``.

        When ``accepts_name`` is True and a ``name=`` kwarg is present,
        a separate ``..._named`` variant is used so the runtime can
        runtime can distinguish named vs unnamed constructors.
        """
        name_arg = None
        for key, val in expr.kwargs:
            if key == "name" and accepts_name:
                name_arg = val
                continue
            raise ScaffoldUnsupportedError(
                f"ir.{symbol}(...) scaffold accepts only "
                + ("'name' " if accepts_name else "")
                + f"kwarg{'s' if accepts_name else ''}; got {key!r}"
            )
        if len(expr.args) != arity:
            raise ScaffoldUnsupportedError(
                f"ir.{symbol} expects {arity} positional args; got "
                f"{len(expr.args)}"
            )
        if symbol == "GlobalVariable" and name_arg is None:
            raise ScaffoldUnsupportedError(
                "ir.GlobalVariable scaffold requires name=..."
            )
        lowered = [self._scaffold_to_handle(a) for a in expr.args]
        if name_arg is not None:
            name_h = self._scaffold_to_handle(name_arg)
            extern_name = (
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}{symbol}___init___named"
            )
            param_tys = [_CSTR] * (arity + 1)
            fn = self._declare_external_function(
                extern_name, _CSTR, param_tys,
            )
            return self.builder.call(
                fn, lowered + [name_h],
                name=self._fresh(f"scaffold.{symbol}"),
            )
        extern_name = (
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}{symbol}___init__"
        )
        param_tys = [_CSTR] * arity
        fn = self._declare_external_function(extern_name, _CSTR, param_tys)
        return self.builder.call(
            fn, lowered, name=self._fresh(f"scaffold.{symbol}"),
        )

    def _emit_scaffold_function_ctor(self, expr: Call) -> ir.Value:
        """``ir.Function(module, fn_ty, name=...)`` — 2 positional args
        plus an optional ``name`` kwarg that is meaningful here (it's
        the LLVM symbol name, not just an SSA hint). Map name kwarg to
        a third extern argument when present.
        """
        if len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                f"ir.Function expects (module, fn_ty); got "
                f"{len(expr.args)}"
            )
        name_arg = None
        for key, val in expr.kwargs:
            if key == "name":
                name_arg = val
            else:
                raise ScaffoldUnsupportedError(
                    f"ir.Function(...) scaffold accepts only 'name' "
                    f"kwarg; got {key!r}"
                )
        module_h = self._scaffold_to_handle(expr.args[0])
        fnty_h = self._scaffold_to_handle(expr.args[1])
        if name_arg is None:
            extern = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}Function___init__",
                _CSTR, [_CSTR, _CSTR],
            )
            return self.builder.call(
                extern, [module_h, fnty_h],
                name=self._fresh("scaffold.Function"),
            )
        name_h = self._scaffold_to_handle(name_arg)
        extern = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}Function___init___named",
            _CSTR, [_CSTR, _CSTR, _CSTR],
        )
        return self.builder.call(
            extern, [module_h, fnty_h, name_h],
            name=self._fresh("scaffold.Function"),
        )

    def _emit_scaffold_function_type_ctor(self, expr: Call) -> ir.Value:
        """``ir.FunctionType(return_ty, [param_tys...], var_arg=False)``.

        Variadic param-types tuple plus an optional ``var_arg`` boolean
        kwarg. Folds at codegen time when ``var_arg`` is a literal
        True/False; emits per-arity ``__init__<N>[_varargs]`` variants.
        """
        var_arg_expr = None
        var_arg_lit = None
        for key, val in expr.kwargs:
            if key == "name":
                continue
            if key == "var_arg":
                if isinstance(val, BoolLit):
                    var_arg_lit = bool(val.value)
                else:
                    # Dynamic var_arg flag — pass it through as an
                    # extra runtime argument to a ``_dyn_va`` variant.
                    var_arg_expr = val
                continue
            raise ScaffoldUnsupportedError(
                f"ir.FunctionType(...) scaffold accepts only 'name' / "
                f"'var_arg' kwargs; got {key!r}"
            )
        if len(expr.args) != 2:
            raise ScaffoldUnsupportedError(
                f"ir.FunctionType expects (return_ty, params); got "
                f"{len(expr.args)}"
            )
        ret_ty_h = self._scaffold_to_handle(expr.args[0])
        params_arg = expr.args[1]
        if not isinstance(params_arg, (ListExpr, TupleExpr)):
            # Non-literal params: dyn fallback. Pass list handle +
            # var_arg handle (or null) to a ``_dyn`` extern.
            params_h = self._scaffold_to_handle(params_arg)
            if var_arg_expr is not None:
                va_h = self._scaffold_to_handle(var_arg_expr)
            elif var_arg_lit:
                va_h = ir.Constant(_CSTR, 1)
            else:
                va_h = ir.Constant(_CSTR, None)
            extern = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}FunctionType___init___dyn",
                _CSTR, [_CSTR, _CSTR, _CSTR],
            )
            return self.builder.call(
                extern, [ret_ty_h, params_h, va_h],
                name=self._fresh("scaffold.FunctionType_dyn"),
            )
        param_exprs = list(params_arg.elems)
        param_handles = [self._scaffold_to_handle(p) for p in param_exprs]
        n = len(param_handles)
        if var_arg_expr is not None:
            # Variadic-flag pass-through: emit
            # ``__init__<N>_dyn_va(ret, params..., var_arg_handle)``.
            va_h = self._scaffold_to_handle(var_arg_expr)
            extern = self._declare_external_function(
                f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}FunctionType___init__"
                f"{n}_dyn_va",
                _CSTR, [_CSTR] + [_CSTR] * n + [_CSTR],
            )
            return self.builder.call(
                extern, [ret_ty_h] + param_handles + [va_h],
                name=self._fresh("scaffold.FunctionType"),
            )
        suffix = "_varargs" if var_arg_lit else ""
        extern = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}FunctionType___init__"
            f"{n}{suffix}",
            _CSTR, [_CSTR] + [_CSTR] * n,
        )
        return self.builder.call(
            extern, [ret_ty_h] + param_handles,
            name=self._fresh("scaffold.FunctionType"),
        )

    def _emit_scaffold_literal_struct(self, expr: Call) -> ir.Value:
        """``ir.LiteralStructType([ty1, ty2, ...])`` — variadic
        element list. Uses per-arity extern variants the same way
        ``builder.call`` does.
        """
        for key, _ in expr.kwargs:
            if key != "name":
                raise ScaffoldUnsupportedError(
                    f"ir.LiteralStructType(...) scaffold accepts only "
                    f"'name' kwarg; got {key!r}"
                )
        if len(expr.args) != 1:
            raise ScaffoldUnsupportedError(
                f"ir.LiteralStructType expects (elements_list,); got "
                f"{len(expr.args)}"
            )
        elem_exprs = self._scaffold_unfold_list_literal(
            expr.args[0], "ir.LiteralStructType elements",
        )
        elem_handles = [self._scaffold_to_handle(e) for e in elem_exprs]
        n = len(elem_handles)
        extern = self._declare_external_function(
            f"{self._IR_TOPLEVEL_SYMBOL_PREFIX}LiteralStructType___init__"
            f"{n}",
            _CSTR, [_CSTR] * n,
        )
        return self.builder.call(
            extern, elem_handles,
            name=self._fresh("scaffold.LiteralStructType"),
        )

    def _emit_method_call(self, expr: Call) -> ir.Value:
        """Lower ``obj.method(args)`` using the class method registry.

        Fast path: if ``obj`` is a Name bound in the local env to a
        ``DynType`` instance of a known class in the current module,
        and ``method`` is a direct member of that class (no MRO
        walking), dispatch to the declared pcc method function. The
        generic ``py_obj_call_method`` path is used otherwise.
        """
        attr = expr.func
        assert isinstance(attr, Attr)

        # Path A scaffold dispatch (Issue 1). ON mode routes
        # ``self.builder.X(...)`` and similar IRBuilder method calls
        # through the closed-world lowering instead of py_cpy_* dynamic
        # dispatch. Method coverage is added incrementally; anything
        # not yet implemented raises ScaffoldUnsupportedError with a
        # clear message rather than silently falling back.
        if self._ir_scaffold_enabled():
            scaffold_value = self._maybe_emit_ir_scaffold_call(expr)
            if scaffold_value is not None:
                return scaffold_value

        # Typed-container method dispatch on pcc-native containers —
        # stays on pcc runtime so the produced binary has no libpython
        # dep. Only a curated method set is recognised; anything else
        # surfaces as NotImplementedError rather than falling through
        # to the generic CPython helper.
        obj_ty0 = attr.obj.ty
        if isinstance(obj_ty0, ListType):
            native = self._maybe_emit_list_method(expr, obj_ty0)
            if native is not None:
                return native
        if isinstance(obj_ty0, DictType):
            native = self._maybe_emit_dict_method(expr, obj_ty0)
            if native is not None:
                return native
        if self._is_native_set_dyn(obj_ty0):
            native = self._maybe_emit_set_method(expr)
            if native is not None:
                return native
        if isinstance(obj_ty0, StrType):
            native = self._maybe_emit_str_method(expr)
            if native is not None:
                return native
        builtin_type_call = self._maybe_emit_builtin_type_method(expr)
        if builtin_type_call is not None:
            return builtin_type_call
        native_module_alias_call = self._maybe_emit_native_module_alias_call(expr)
        if native_module_alias_call is not None:
            return native_module_alias_call
        native_os_call = self._emit_native_os_call(expr)
        if native_os_call is not None:
            return native_os_call
        native_platform_call = self._emit_native_platform_call(expr)
        if native_platform_call is not None:
            return native_platform_call
        native_subprocess_call = self._emit_native_subprocess_call(expr)
        if native_subprocess_call is not None:
            return native_subprocess_call
        native_shutil_call = self._emit_native_shutil_call(expr)
        if native_shutil_call is not None:
            return native_shutil_call
        native_shlex_call = self._emit_native_shlex_call(expr)
        if native_shlex_call is not None:
            return native_shlex_call
        native_sysconfig_call = self._emit_native_sysconfig_call(expr)
        if native_sysconfig_call is not None:
            return native_sysconfig_call
        native_os_environ_call = self._emit_native_os_environ_call(expr)
        if native_os_environ_call is not None:
            return native_os_environ_call
        native_sys_stream_call = self._emit_native_sys_stream_call(expr)
        if native_sys_stream_call is not None:
            return native_sys_stream_call
        native_file_method = self._emit_native_file_method(expr)
        if native_file_method is not None:
            return native_file_method
        native_sys_exit_call = self._emit_native_sys_exit_call(expr)
        if native_sys_exit_call is not None:
            return native_sys_exit_call
        native_os_path_call = self._emit_native_os_path_call(expr)
        if native_os_path_call is not None:
            return native_os_path_call
        if isinstance(attr.obj, Name):
            builtin_value = self._native_builtin_value_for_name(attr.obj.ident)
            if builtin_value == "os.path":
                return self._emit_cpy_method_call_src(
                    self._emit_cpy_attr(
                        self._emit_cpython_module_value("os"), "path",
                    ),
                    attr.name, expr.args, kwargs=expr.kwargs,
                )

        # Case -1: ``<CPython value>.method(args)``.
        #
        # Chained access (``os.path.join``) lowers the inner attr chain
        # through ``_emit_attr``, which already routes through
        # ``py_cpy_getattr`` whenever the root is an imported module or
        # a CPython-flagged local. If the resulting SSA value lands in
        # ``_cpy_values``, dispatch the method call through libpython.
        if isinstance(attr.obj, Name):
            builtin_module = self._native_builtin_module_for_name(
                attr.obj.ident
            )
            if builtin_module is not None:
                return self._emit_cpy_method_call_src(
                    self._emit_cpython_module_value(builtin_module),
                    attr.name, expr.args, kwargs=expr.kwargs,
                )
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(attr.obj.ident)
            if cpy_gv is not None:
                return self._emit_cpy_method_call_src(
                    self.builder.load(cpy_gv, name=self._fresh("cpy.mod")),
                    attr.name, expr.args, kwargs=expr.kwargs,
                )
            if getattr(self, "_cpy_env_flags", {}).get(attr.obj.ident, False):
                if attr.name in self._STR_METHOD_NATIVE:
                    native = self._maybe_emit_str_method_via_dyn(expr)
                    if native is not None:
                        return native
                return self._emit_cpy_method_call_src(
                    self._emit_expr(attr.obj), attr.name, expr.args,
                    kwargs=expr.kwargs,
                )
        if isinstance(attr.obj, Attr):
            # Evaluate the chain eagerly; if the result was tagged as a
            # CPython value (e.g. ``os.path``), dispatch there.
            chain_val = self._emit_expr(attr.obj)
            if chain_val in getattr(self, "_cpy_values", ()):
                return self._emit_cpy_method_call_src(
                    chain_val, attr.name, expr.args, kwargs=expr.kwargs,
                )

        # Case 0: ``super().method(args)`` inside a method body.
        # Resolve the method by walking the current class's declared
        # bases. The ``self`` argument is forwarded unchanged.
        current_class = getattr(self, "current_class", None)
        if (
            current_class is not None
            and isinstance(attr.obj, Call)
            and isinstance(attr.obj.func, Name)
            and attr.obj.func.ident == "super"
            and not attr.obj.args
        ):
            parent_info = self._resolve_super_method(current_class, attr.name)
            if parent_info is not None:
                receiver_name = "self"
                if (
                    getattr(self, "current_func_def", None) is not None
                    and self.current_func_def.args
                ):
                    receiver_name = self.current_func_def.args[0].name or "self"
                recv_slot = self.env.get(receiver_name)
                if recv_slot is None:
                    raise L1CodegenError(
                        f"super() receiver {receiver_name!r} not bound in "
                        f"{self.current_func_def.name!r}"
                    )
                recv_val = self.builder.load(
                    recv_slot[0], name=self._fresh(receiver_name)
                )
                method_ptr = self.class_lowering.emit_super_lookup(
                    current_class, recv_val, attr.name,
                )
                kind = parent_info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    method_fn = parent_info.methods[attr.name]
                    return self._emit_static_method_ptr_call(
                        method_ptr,
                        method_fn, parent_info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                method_fn = parent_info.methods[attr.name]
                return self._emit_direct_method_ptr_call(
                    method_ptr,
                    method_fn, recv_val,
                    parent_info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )
            # Parent is a foreign base (e.g. ``Exception``) not tracked
            # by pcc's ClassInfo registry. For the well-known dunders
            # (``__init__`` / ``__new__``) we fall through quietly —
            # pcc-emitted classes already have their ctor state
            # populated by ``_pcc_py_module_init_*``, and calling an
            # unknown foreign super is typically only used for its
            # side effects which have no equivalent on the pcc side.
            if attr.name in ("__init__", "__new__"):
                return ir.Constant(_CSTR, None)

        # Case 1: ``self.method(...)`` inside a method body of the
        # currently-lowered class. Try the method on the class itself,
        # then walk the declared bases.
        if (
            current_class is not None
            and isinstance(attr.obj, Name)
            and attr.obj.ident == "self"
        ):
            method_info = self._resolve_method_mro(
                current_class.name, attr.name
            )
            if method_info is not None:
                kind = method_info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    # ``self.static_method(args)`` — Python lets you
                    # call staticmethods via the instance; drop the
                    # self receiver and dispatch as a plain class call.
                    method_fn = method_info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn, method_info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    cls_ptr = self.builder.load(
                        method_info.global_var,
                        name=self._fresh(".cls.recv"),
                    )
                    method_fn = method_info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, cls_ptr,
                        method_info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                self_val = self.builder.load(
                    self.env["self"][0], name=self._fresh("self")
                )
                method_fn = method_info.methods[attr.name]
                return self._emit_direct_method_call(
                    method_fn, self_val,
                    method_info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )

        # Case 1b: ``cls.method(...)`` inside a ``@classmethod`` body.
        # ``cls`` is the class itself, so dispatch exactly like
        # ``ClassName.method`` — respect the target method's kind and
        # pass the class pointer as the receiver for classmethods /
        # drop it for staticmethods.
        if (
            current_class is not None
            and isinstance(attr.obj, Name)
            and attr.obj.ident == "cls"
        ):
            method_info = self._resolve_method_mro(
                current_class.name, attr.name
            )
            if method_info is not None:
                kind = method_info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    method_fn = method_info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn, method_info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    cls_ptr = self.builder.load(
                        method_info.global_var,
                        name=self._fresh(".cls.recv"),
                    )
                    method_fn = method_info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, cls_ptr,
                        method_info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                # Instance method called via ``cls`` — pcc can't
                # construct a bound instance, so treat as a static
                # dispatch on the class. Matches CPython's
                # ``ClassName.instance_method(instance, …)`` when the
                # user reaches for it this way; the first arg is then
                # the real ``self``.
                method_fn = method_info.methods[attr.name]
                return self._emit_static_method_call(
                    method_fn, method_info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )

        # Case 2: ``ClassName.method(...)`` — direct static/classmethod
        # dispatch on a bare class reference (no instance).
        if (
            isinstance(attr.obj, Name)
            and attr.obj.ident in self.class_lowering.classes
        ):
            info = self._resolve_method_mro(attr.obj.ident, attr.name)
            if info is not None:
                kind = info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    method_fn = info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn, info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                if kind == "classmethod":
                    cls_ptr = self.builder.load(
                        info.global_var, name=self._fresh(".cls.recv")
                    )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, cls_ptr, info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                # Explicit base-class instance dispatch such as
                # ``Base.method(self, ...)`` should pass the caller's
                # explicit receiver as the first positional argument.
                method_fn = info.methods[attr.name]
                return self._emit_static_method_call(
                    method_fn, info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )

        native_external = self._maybe_emit_class_lowering_extern_method(expr)
        if native_external is not None:
            return native_external

        # Case 3: ``other_obj.method(...)`` — first try the class hint
        # recorded at assignment time, walking up the MRO of that
        # class for the first definition of the method. Fall back to
        # the first class in the module that declares the method so
        # single-class programs keep working when the hint is missing.
        if isinstance(attr.obj, Name):
            hint = self.env_class_hint.get(attr.obj.ident)
            if hint is not None:
                info = self._resolve_method_mro(hint, attr.name)
                if info is not None:
                    kind = info.method_kinds.get(attr.name, "instance")
                    if kind == "static":
                        method_fn = info.methods[attr.name]
                        return self._emit_static_method_call(
                            method_fn, info, attr.name, expr.args,
                            kwargs=expr.kwargs,
                        )
                    obj_val = self._emit_expr(attr.obj)
                    if kind == "classmethod":
                        obj_val = self.builder.load(
                            info.global_var, name=self._fresh(".cls.recv")
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, obj_val,
                        info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )

        receiver_hint = self._class_hint_for_expr(attr.obj)
        if receiver_hint is not None:
            info = self._resolve_method_mro(receiver_hint, attr.name)
            if info is not None:
                kind = info.method_kinds.get(attr.name, "instance")
                if kind == "static":
                    self._emit_expr(attr.obj)
                    method_fn = info.methods[attr.name]
                    return self._emit_static_method_call(
                        method_fn, info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )
                obj_val = self._emit_expr(attr.obj)
                if kind == "classmethod":
                    obj_val = self.builder.load(
                        info.global_var, name=self._fresh(".cls.recv")
                    )
                method_fn = info.methods[attr.name]
                return self._emit_direct_method_call(
                    method_fn, obj_val,
                    info, attr.name, expr.args,
                    kwargs=expr.kwargs,
                )

        if isinstance(attr.obj, Name):
            # Last closed-world fallback: any class declaring the method.
            # Keep this after receiver_hint so typed loop variables like
            # ``for gv in self._globals: gv.render()`` dispatch to the
            # receiver's class instead of the first same-named method found.
            for info in self.class_lowering.classes.values():
                if attr.name in info.methods:
                    kind = info.method_kinds.get(attr.name, "instance")
                    if kind == "static":
                        method_fn = info.methods[attr.name]
                        return self._emit_static_method_call(
                            method_fn, info, attr.name, expr.args,
                            kwargs=expr.kwargs,
                        )
                    obj_val = self._emit_expr(attr.obj)
                    if kind == "classmethod":
                        obj_val = self.builder.load(
                            info.global_var, name=self._fresh(".cls.recv")
                        )
                    method_fn = info.methods[attr.name]
                    return self._emit_direct_method_call(
                        method_fn, obj_val,
                        info, attr.name, expr.args,
                        kwargs=expr.kwargs,
                    )

        # Last-resort CPython fallback: when the receiver is a DynType
        # value (typical for foreign / imported-module classes whose
        # annotations such as ``llvm.ModuleRef`` resolve to DynType at
        # type-inference time), dispatch the method via
        # ``PyObject_CallMethod``. This unlocks ``module.verify()``
        # and similar idioms without requiring a CPython-class registry
        # on the pcc side.
        #
        # Typed containers (list / dict / tuple / str) deliberately do
        # *not* fall through here: pcc's pure-self-host story requires
        # that typed-collection methods stay on pcc-native runtime paths
        # so the produced binary has no libpython dependency. Missing
        # methods there surface as NotImplementedError so we can add a
        # dedicated fast path rather than silently pulling libpython in.
        obj_ty = attr.obj.ty
        if isinstance(obj_ty, DynType):
            native = self._maybe_emit_dict_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_list_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_set_method_via_dyn(expr)
            if native is not None:
                return native
            # DynType receiver: when the method is a known pcc-native
            # str helper, dispatch through the runtime (assumes the
            # value really is a str at runtime — matches CPython
            # behaviour which would raise AttributeError on type
            # mismatch; we emit a probable crash). Keeps the binary
            # libpython-free for the common ``DynType str result``
            # idiom (function return + splitlines / rstrip / …).
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, StrType):
            # Typed-str receiver with a method not on the pcc-native
            # fast path (``encode`` / ``rsplit`` / ``format`` / …).
            # Pcc's pure-self-host policy prefers a native helper here,
            # but to let the solo-compile survey advance we route the
            # unknown method through the libpython fallback and leave
            # a TODO. The emitted binary then links libpython for this
            # specific use. Revisit and add the helper once the call
            # shows up in the self-host critical path.
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, NoneType):
            # Flow-insensitive inference can leave a guarded Optional[str]
            # receiver as ``NoneType`` inside branches like
            # ``x.strip() if x is not None else None``. Marshal the
            # runtime value to a CPython object and dispatch there so a
            # real str still works while an actual None preserves
            # CPython's AttributeError behavior.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, (ListType, DictType, TupleType)):
            # Typed-collection receiver whose method isn't on the pcc-
            # native fast path (e.g. ``.reverse`` / ``.clear`` /
            # ``.update``). Fall through to the CPython fallback the
            # same way StrType does: pulls libpython for that specific
            # use but lets the solo-compile survey advance. Revisit
            # with a native helper when the call reaches the
            # self-host critical path.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result
        if isinstance(obj_ty, (IntType, FloatType, BoolType)):
            # Numeric method call (``int.to_bytes``, ``float.is_integer``,
            # ``bool.conjugate``, etc.) — box to a CPython object and
            # dispatch through the libpython fallback. Pulls libpython
            # for that specific use. The numeric value is marshalled
            # through the appropriate boxer so CPython sees a proper
            # Py_Long / Py_Float.
            raw_val = self._emit_expr(attr.obj)
            boxed = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, raw_val, obj_ty,
            )
            cpy_val = self.builder.call(
                self.runtime["py_cpy_from_pcc_obj"], [boxed],
                name=self._fresh(f"cpy.num.{attr.name}"),
            )
            return self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
        if isinstance(obj_ty, ClassType):
            # A schema-bearing local class should have matched one of
            # the direct method cases above. If it did not, or the type
            # is only an unresolved imported annotation shell, preserve
            # Python semantics by dispatching the runtime object through
            # CPython instead of treating the annotation as a closed
            # pcc class registry entry.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython(raw_val, obj_ty)
            result = self._emit_cpy_method_call_src(
                cpy_val, attr.name, expr.args, kwargs=expr.kwargs,
            )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
            return result

        raise NotImplementedError(
            f"Layer 1 method call {attr.name!r}: no matching class "
            "method found in module (dynamic dispatch via dunder path "
            "is deferred)"
        )

    def _is_native_set_dyn(self, ty: Type) -> bool:
        return isinstance(ty, DynType) and ty.name in ("set", "frozenset")

    def _maybe_emit_list_method_via_dyn(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in (
            "append", "extend", "insert", "pop", "remove", "index", "sort",
        ):
            return None
        list_ty = ListType(name="list", elem=DynType(name="dyn"))
        return self._maybe_emit_list_method(expr, list_ty)

    def _maybe_emit_dict_method_via_dyn(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in (
            "get", "keys", "values", "items", "setdefault", "pop",
        ):
            return None
        dict_ty = DictType(
            name="dict", key=DynType(name="dyn"), value=DynType(name="dyn"),
        )
        return self._maybe_emit_dict_method(expr, dict_ty)

    def _maybe_emit_set_method_via_dyn(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in ("add", "remove", "discard", "update"):
            return None
        return self._maybe_emit_set_method(expr)

    def _maybe_emit_builtin_type_method(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """Handle ``int.__new__(cls, x)``-style builtin type dispatch.

        Without this guard, the generic "any class declaring the
        method" fallback can accidentally resolve ``int.__new__`` to a
        user class's own ``__new__`` in the same module.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if not isinstance(attr.obj, Name):
            return None
        builtin_name = attr.obj.ident
        if builtin_name not in _CPY_BUILTIN_TYPE_NAMES:
            return None
        if attr.name == "__new__":
            ctor_args = expr.args
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident == "cls"
                and builtin_name == "object"
                and getattr(self, "current_class", None) is not None
            ):
                return self.class_lowering.emit_instantiate(
                    self.current_class.name, (), self,
                )
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident in ("cls", "self")
            ):
                ctor_args = ctor_args[1:]
            return self._emit_call(Call(
                span=expr.span,
                ty=expr.ty,
                func=Name(
                    span=attr.obj.span,
                    ty=attr.obj.ty,
                    ident=builtin_name,
                ),
                args=ctor_args,
                kwargs=expr.kwargs,
            ))
        fn_val = self._load_cpython_builtin(builtin_name)
        return self._emit_cpy_method_call_src(
            fn_val, attr.name, expr.args, kwargs=expr.kwargs,
        )

    def _try_dispatch_dunder_unary(
        self,
        host_expr: "Expr",
        dunder_name: str,
        arg_exprs: tuple["Expr", ...],
    ) -> Optional[ir.Value]:
        """If ``host_expr`` is a Name bound to a hinted class that
        defines ``dunder_name`` (via MRO), emit the direct method call
        with ``arg_exprs`` and return the result. Otherwise return None.
        """
        if isinstance(host_expr, Subscript):
            host = host_expr.obj
        else:
            host = host_expr
        if not isinstance(host, Name):
            return None
        hint = self.env_class_hint.get(host.ident)
        if hint is None:
            return None
        info = self._resolve_method_mro(hint, dunder_name)
        if info is None:
            return None
        obj_val = self._emit_expr(host)
        method_fn = info.methods[dunder_name]
        return self._emit_direct_method_call(
            method_fn, obj_val, info, dunder_name, arg_exprs,
        )

    def _class_hint_for_expr(self, expr: Expr) -> Optional[str]:
        if isinstance(expr, Name):
            hint = self.env_class_hint.get(expr.ident)
            if hint is not None:
                return hint
            slot = self.env.get(expr.ident)
            if slot is not None and isinstance(slot[2], ClassType):
                return self._ensure_class_type_registered(slot[2])
        if isinstance(expr.ty, ClassType):
            return self._ensure_class_type_registered(expr.ty)
        if not isinstance(expr, Call):
            return None
        if isinstance(expr.func, Name):
            name = expr.func.ident
            if name in self.class_lowering.classes:
                return name
            return None
        if not isinstance(expr.func, Attr):
            return None
        receiver_hint = self._class_hint_for_expr(expr.func.obj)
        if receiver_hint is None:
            return None
        info = self._resolve_method_mro(receiver_hint, expr.func.name)
        if info is None:
            return None
        fd = self.class_lowering._find_method_def(info.name, expr.func.name)
        if fd is None:
            return None
        ret_hint = self._class_hint_from_annotation(fd.return_ty)
        if ret_hint is not None:
            return ret_hint
        if self._method_returns_receiver(fd):
            return receiver_hint
        return None

    def _ensure_class_type_registered(self, ty: ClassType) -> Optional[str]:
        if ty.name in self.class_lowering.classes:
            return ty.name
        cache_key = (ty.module, ty.name)
        if cache_key in self._class_type_export_cache:
            return self._class_type_export_cache[cache_key]
        native_table = getattr(self, "_native_module_exports", None)
        if native_table is None:
            self._class_type_export_cache[cache_key] = None
            return None
        candidates: list[tuple[str, dict]] = []
        for module_name, exports in native_table.items():
            if ty.module and module_name != ty.module:
                continue
            info = exports.get(ty.name)
            if isinstance(info, dict) and info.get("kind") == "class":
                candidates.append((module_name, info))
        if len(candidates) != 1:
            self._class_type_export_cache[cache_key] = None
            return None
        module_name, info = candidates[0]
        self.class_lowering.declare_extern_class(
            owning_module=module_name,
            class_name=info["class_name"],
            field_names=info["field_names"],
            methods=info["methods"],
            local_name=ty.name,
        )
        self._class_type_export_cache[cache_key] = ty.name
        return ty.name

    def _maybe_emit_class_lowering_extern_method(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        attr = expr.func
        if not isinstance(attr, Attr):
            return None
        obj_ty = attr.obj.ty
        if not (
            isinstance(obj_ty, ClassType)
            and obj_ty.name == "ClassLowering"
            and attr.name == "declare_extern_class"
        ):
            return None
        ordered = self._ordered_declare_extern_class_args(expr)
        if ordered is None:
            return None
        recv = self._emit_expr(attr.obj)
        args_ir: list[ir.Value] = [recv]
        for arg in ordered:
            raw = self._emit_expr(arg)
            args_ir.append(marshal.marshal_to_object(
                self.builder, self.module, self.runtime, raw, arg.ty,
            ))
        sym = (
            "user_pcc_py_frontend_codegen_class_gen_"
            "ClassLowering_declare_extern_class"
        )
        existing = self.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            param_tys: list[ir.Type] = []
            i = 0
            while i < len(args_ir):
                param_tys.append(_CSTR)
                i += 1
            fnty = ir.FunctionType(
                _CSTR, param_tys, var_arg=False,
            )
            fn = ir.Function(self.module, fnty, name=sym)
            fn.linkage = "external"
        return self._call_user(
            fn, args_ir, self._fresh("ClassLowering.declare_extern_class.ret"),
        )

    def _ordered_declare_extern_class_args(
        self, expr: Call,
    ) -> Optional[list[Expr]]:
        names = (
            "owning_module",
            "class_name",
            "field_names",
            "methods",
            "local_name",
        )
        out: list[Expr] = []
        i = 0
        while i < len(expr.args):
            if i >= len(names):
                return None
            out.append(expr.args[i])
            i += 1
        while i < len(names):
            found: Optional[Expr] = None
            for name, value in expr.kwargs:
                if name == names[i]:
                    found = value
                    break
            if found is None:
                return None
            out.append(found)
            i += 1
        return out

    def _list_elem_class_hint_for_expr(self, expr: Expr) -> Optional[str]:
        if not isinstance(expr, ListExpr) or not expr.elems:
            return None
        hint: Optional[str] = None
        for elem in expr.elems:
            elem_hint = self._class_hint_for_expr(elem)
            if elem_hint is None:
                return None
            if hint is None:
                hint = elem_hint
            elif hint != elem_hint:
                return None
        return hint

    def _class_hint_from_annotation(self, ann) -> Optional[str]:
        if isinstance(ann, ClassType):
            return ann.name
        if isinstance(ann, Name) and ann.ident in self.class_lowering.classes:
            return ann.ident
        if isinstance(ann, StrLit) and ann.value in self.class_lowering.classes:
            return ann.value
        return None

    def _method_returns_receiver(self, fd: FuncDef) -> bool:
        if not fd.args:
            return False
        receiver_name = fd.args[0].name or "self"
        saw_return = False
        for stmt in fd.body:
            if isinstance(stmt, Return):
                saw_return = True
                if not (
                    isinstance(stmt.value, Name)
                    and stmt.value.ident == receiver_name
                ):
                    return False
        return saw_return

    def _resolve_super_method(self, info, method_name: str):
        """Walk the bases of ``info`` and return the first one that
        defines ``method_name``. Models a single-inheritance ``super()``
        call — the multi-base case needs full C3 linearisation which
        remains TODO in :class:`ClassLowering`.
        """
        for base_expr in info.bases_ast:
            if not isinstance(base_expr, Name) or base_expr.ident == "object":
                continue
            found = self._resolve_method_mro(base_expr.ident, method_name)
            if found is not None:
                return found
        return None

    def _resolve_property_setter_mro(self, class_name: str, prop_name: str):
        """Walk the MRO of ``class_name`` for a ``@<prop>.setter``."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if prop_name in info.property_setters:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _resolve_property_mro(self, class_name: str, prop_name: str):
        """Walk the MRO of ``class_name`` for a ``@property`` ``prop_name``."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if prop_name in info.properties:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _resolve_method_mro(self, class_name: str, method_name: str):
        """Walk the declared bases of ``class_name`` looking for the
        first class that defines ``method_name``. Uses the AST base
        list order (a shallow subset of full C3 MRO, sufficient for
        the single-inheritance + simple multi-inheritance cases in the
        current phase-3 corpus)."""
        visited: set[str] = set()
        queue = [class_name]
        while queue:
            cname = queue.pop(0)
            if cname in visited:
                continue
            visited.add(cname)
            info = self.class_lowering.classes.get(cname)
            if info is None:
                continue
            if method_name in info.methods:
                return info
            for base_expr in info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
        return None

    def _emit_cpy_attr(self, obj_val: ir.Value, name: str) -> ir.Value:
        """Lower ``cpy_obj.<name>`` through py_cpy_getattr, tagging the
        result as CPython-backed."""
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(name, f".cpy.attr.{name}")
        )
        val = self.builder.call(
            self.runtime["py_cpy_getattr"], [obj_val, attr_ptr],
            name=self._fresh(f"cpy.get.{name}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(val)
        return val

    def _load_cpython_builtin(self, name: str) -> ir.Value:
        """Emit ``py_cpy_import("builtins") + py_cpy_getattr(name)`` at
        the current builder position. No cross-block caching — each
        call-site gets its own load so dominance holds regardless of
        which block the caller is emitting into. ``py_cpy_import``
        returns the cached module pointer from the runtime; overhead
        is a single pointer-lookup call."""
        mod_name_gv = self._cstr_global(
            "builtins", ".cpy.builtins_modname",
        )
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"],
            [self._ptr_to_cstr(mod_name_gv)],
            name=self._fresh("cpy.builtins"),
        )
        attr_gv = self._cstr_global(
            name, f".cpy.builtin.{name}",
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, self._ptr_to_cstr(attr_gv)],
            name=self._fresh(f"cpy.builtin.{name}"),
        )
        return self._mark_cpy_value(fn_val)

    def _is_starred_unpack(self, arg_exprs: tuple) -> bool:
        """Return True if ``arg_exprs`` is exactly ``(*iterable,)``.

        Two parser flavours both produce a synthetic ``Call`` wrapping
        the starred inner:

        * Native py_lift (default) emits ``Name("*")`` (see
          ``pcc.parse.py_lift._Lifter._e_Starred``).
        * Legacy CPython-AST parser emits ``Name("__starred__")``.

        One-element splat calls (``fn(*args)``, ``obj.meth(*xs)``) are
        handled by routing through ``py_cpy_call_list``; anything else
        (mixed positional + splat, multiple splats) still falls through.
        """
        if len(arg_exprs) != 1:
            return False
        arg = arg_exprs[0]
        return (
            isinstance(arg, Call)
            and isinstance(arg.func, Name)
            and arg.func.ident in ("*", "__starred__")
            and len(arg.args) == 1
            and not arg.kwargs
        )

    def _is_starred_unpack_expr(self, arg: Expr) -> bool:
        return (
            isinstance(arg, Call)
            and isinstance(arg.func, Name)
            and arg.func.ident in ("*", "__starred__")
            and len(arg.args) == 1
            and not arg.kwargs
        )

    def _has_starred_unpack(self, arg_exprs: tuple[Expr, ...]) -> bool:
        return any(self._is_starred_unpack_expr(arg) for arg in arg_exprs)

    def _split_starstar_kwargs_unpack(
        self, arg_exprs: tuple[Expr, ...],
    ) -> tuple[tuple[Expr, ...], Expr] | None:
        """Return ``(positional_args, kwargs_mapping)`` for a single
        trailing ``**mapping`` sentinel, else ``None``.

        Native ``py_lift`` encodes ``fn(a, **kw)`` as a positional arg
        ``Call(Name(\"**\"), (kw,))``. CPython fallback calls can pass
        the mapping through ``PyObject_Call``, but L1's direct-call
        paths still reject true ``**kwargs``/``**mapping`` signatures.
        """
        positional: list[Expr] = []
        kwargs_expr: Expr | None = None
        for arg in arg_exprs:
            if (
                isinstance(arg, Call)
                and isinstance(arg.func, Name)
                and arg.func.ident == "**"
                and len(arg.args) == 1
                and not arg.kwargs
            ):
                if kwargs_expr is not None:
                    return None
                kwargs_expr = arg.args[0]
                continue
            if kwargs_expr is not None:
                return None
            positional.append(arg)
        if kwargs_expr is None:
            return None
        return tuple(positional), kwargs_expr

    def _emit_cpy_call_kwdict(
        self, fn_val: ir.Value, name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(*pos_exprs, **kwargs_expr)`` through a
        CPython kwargs-dict helper."""
        if self._is_starred_unpack(pos_exprs):
            return self._emit_cpy_call_list_kwdict(
                fn_val, name_hint, pos_exprs[0], kwargs_expr,
            )
        n_pos = len(pos_exprs)
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            v = self._emit_expr(arg)
            ca, _ = self._marshal_to_cpython(v, arg.ty)
            pos_vals.append(ca)
        if n_pos == 0:
            pos_argv_ptr = ir.Constant(_CSTR, None)
        else:
            pos_arr_ty = ir.ArrayType(_CSTR, n_pos)
            pos_argv = self._alloca_in_entry(
                pos_arr_ty, name=f"cpy.pos.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"pos.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv, _CSTR, name=self._fresh("pos.p"),
            )
        kw_v = self._emit_expr(kwargs_expr)
        kw_cpy, kw_owned = self._marshal_to_cpython(kw_v, kwargs_expr.ty)
        result = self.builder.call(
            self.runtime["py_cpy_call_kwdict"],
            [fn_val, ir.Constant(_I64, n_pos), pos_argv_ptr, kw_cpy],
            name=self._fresh(f"cpy.callkwdict.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_pcc_args_list(
        self, arg_exprs: tuple[Expr, ...], name_hint: str,
    ) -> ir.Value:
        """Materialize positional args as a pcc list object.

        Plain args are marshalled to pcc ``PyObject*`` and appended;
        starred sentinels extend the list from their inner iterable."""
        lst = self.builder.call(
            self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
            name=self._fresh(f"call.args.{name_hint}"),
        )
        for arg in arg_exprs:
            if self._is_starred_unpack_expr(arg):
                inner = self._emit_expr(arg.args[0])
                self.builder.call(
                    self.runtime["py_list_extend"], [lst, inner],
                )
                continue
            v = self._emit_expr(arg)
            v_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, arg.ty,
            )
            self.builder.call(
                self.runtime["py_list_append"], [lst, v_obj],
            )
        return lst

    def _emit_cpy_call_arglist(
        self, fn_val: ir.Value, name_hint: str,
        arg_exprs: tuple[Expr, ...],
    ) -> ir.Value:
        """Dispatch ``callable(pos..., *iters...)`` via ``py_cpy_call_list``."""
        args_list = self._emit_pcc_args_list(arg_exprs, name_hint)
        result = self.builder.call(
            self.runtime["py_cpy_call_list"],
            [fn_val, args_list],
            name=self._fresh(f"cpy.callargs.{name_hint}"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_arglist_kwdict(
        self,
        fn_val: ir.Value,
        name_hint: str,
        arg_exprs: tuple[Expr, ...],
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(pos..., *iters..., **mapping)`` via the
        list+kwdict helper."""
        args_list = self._emit_pcc_args_list(arg_exprs, name_hint)
        kw_v = self._emit_expr(kwargs_expr)
        kw_cpy, kw_owned = self._marshal_to_cpython(kw_v, kwargs_expr.ty)
        result = self.builder.call(
            self.runtime["py_cpy_call_list_kwdict"],
            [fn_val, args_list, kw_cpy],
            name=self._fresh(f"cpy.callargskw.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_list_kwdict(
        self, fn_val: ir.Value, name_hint: str,
        starred_call: "Call", kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``fn(*args, **kwargs_dict)`` through a dedicated
        helper that converts the pcc container to a CPython tuple."""
        inner = starred_call.args[0]
        iter_val = self._emit_expr(inner)
        kw_v = self._emit_expr(kwargs_expr)
        kw_cpy, kw_owned = self._marshal_to_cpython(kw_v, kwargs_expr.ty)
        result = self.builder.call(
            self.runtime["py_cpy_call_list_kwdict"],
            [fn_val, iter_val, kw_cpy],
            name=self._fresh(f"cpy.calllistkw.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_kwdict_plus(
        self,
        fn_val: ir.Value,
        name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs: tuple,
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(*pos, k=v, **mapping)`` through a helper
        that merges explicit kwargs into the mapping before the call."""
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            v = self._emit_expr(arg)
            ca, _ = self._marshal_to_cpython(v, arg.ty)
            pos_vals.append(ca)
        if pos_vals:
            pos_arr_ty = ir.ArrayType(_CSTR, len(pos_vals))
            pos_argv = self._alloca_in_entry(
                pos_arr_ty, name=f"cpy.posmix.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"posmix.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv, _CSTR, name=self._fresh("posmix.p"),
            )
        else:
            pos_argv_ptr = ir.Constant(_CSTR, None)

        kw_v = self._emit_expr(kwargs_expr)
        kw_cpy, kw_owned = self._marshal_to_cpython(kw_v, kwargs_expr.ty)

        if kwargs:
            names_arr_ty = ir.ArrayType(_CSTR, len(kwargs))
            vals_arr_ty = ir.ArrayType(_CSTR, len(kwargs))
            names_arr = self._alloca_in_entry(
                names_arr_ty, name=f"cpy.mixn.{name_hint}",
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty, name=f"cpy.mixv.{name_hint}",
            )
            kw_vals: list[ir.Value] = []
            kw_owned_flags: list[bool] = []
            for i, (kw_name, kw_expr) in enumerate(kwargs):
                name_gv = self._cstr_global(
                    kw_name, f".cpy.mixkw.{name_hint}.{i}",
                )
                ngep = self.builder.gep(
                    names_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"mixn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)
                raw_v = self._emit_expr(kw_expr)
                ca, is_owned = self._marshal_to_cpython(raw_v, kw_expr.ty)
                kw_vals.append(ca)
                kw_owned_flags.append(is_owned)
                vgep = self.builder.gep(
                    vals_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"mixv.{i}"),
                )
                self.builder.store(ca, vgep)
            names_ptr = self.builder.bitcast(
                names_arr, _CSTR, name=self._fresh("mixn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr, _CSTR, name=self._fresh("mixv.p"),
            )
        else:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
            kw_vals = []
            kw_owned_flags = []

        result = self.builder.call(
            self.runtime["py_cpy_call_kwdict_plus"],
            [
                fn_val,
                ir.Constant(_I64, len(pos_vals)),
                pos_argv_ptr,
                ir.Constant(_I64, len(kwargs)),
                names_ptr,
                vals_ptr,
                kw_cpy,
            ],
            name=self._fresh(f"cpy.callmix.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        for ca, is_owned in zip(kw_vals, kw_owned_flags):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_list(
        self, fn_val: ir.Value, name_hint: str,
        starred_call: "Call",
    ) -> ir.Value:
        """Dispatch ``fn_val(*iterable)`` through ``py_cpy_call_list``.

        ``starred_call`` is the ``Call(Name("__starred__"), (iterable,))``
        sentinel wrapping the single splat arg."""
        inner = starred_call.args[0]
        iter_val = self._emit_expr(inner)
        # py_cpy_call_list expects the pcc PyObject* directly (it
        # converts to a CPython tuple internally). If we received a
        # plain CPython ref (e.g. iter_val already came from a CPython
        # path), the universal converter inside the helper still
        # accepts CPython containers — but generator-returning inner
        # exprs aren't common enough to route specially.
        result = self.builder.call(
            self.runtime["py_cpy_call_list"],
            [fn_val, iter_val],
            name=self._fresh(f"cpy.calllist.{name_hint}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_func_call(
        self, fn_val: ir.Value, name_hint: str,
        arg_exprs: tuple[Expr, ...],
    ) -> ir.Value:
        """Dispatch ``fn_val(args)`` via py_cpy_callN for a CPython
        callable already loaded into ``fn_val`` (e.g. from a
        ``from mod import fn`` binding). Args marshal via
        ``_marshal_to_cpython``. Shares the argv path with
        ``_emit_cpy_method_call_src``."""
        kwdict_unpack = self._split_starstar_kwargs_unpack(arg_exprs)
        if kwdict_unpack is not None:
            pos_exprs, kwargs_expr = kwdict_unpack
            if self._has_starred_unpack(pos_exprs):
                return self._emit_cpy_call_arglist_kwdict(
                    fn_val, name_hint, pos_exprs, kwargs_expr,
                )
            if self._is_starred_unpack(pos_exprs):
                return self._emit_cpy_call_list_kwdict(
                    fn_val, name_hint, pos_exprs[0], kwargs_expr,
                )
            return self._emit_cpy_call_kwdict(
                fn_val, name_hint, pos_exprs, kwargs_expr,
            )
        if self._has_starred_unpack(arg_exprs):
            return self._emit_cpy_call_arglist(fn_val, name_hint, arg_exprs)
        if self._is_starred_unpack(arg_exprs):
            return self._emit_cpy_call_list(
                fn_val, name_hint, arg_exprs[0],
            )
        cpy_args: list[ir.Value] = []
        for arg in arg_exprs:
            v = self._emit_expr(arg)
            cpy_arg, _owned = self._marshal_to_cpython(v, arg.ty)
            cpy_args.append(cpy_arg)
        n = len(cpy_args)
        if n == 0:
            result = self.builder.call(
                self.runtime["py_cpy_call_noargs"], [fn_val],
                name=self._fresh(f"cpy.call0.{name_hint}"),
            )
        elif n == 1:
            result = self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{name_hint}"),
            )
        elif n == 2:
            result = self.builder.call(
                self.runtime["py_cpy_call2"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{name_hint}"),
            )
        elif n == 3:
            result = self.builder.call(
                self.runtime["py_cpy_call3"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{name_hint}"),
            )
        else:
            ptr_arr_ty = ir.ArrayType(_CSTR, n)
            argv = self._alloca_in_entry(
                ptr_arr_ty, name=f"cpy.argv.{name_hint}",
            )
            for i, ca in enumerate(cpy_args):
                gep = self.builder.gep(
                    argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"argv.{i}"),
                )
                self.builder.store(ca, gep)
            argv_p = self.builder.gep(
                argv, [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
                inbounds=True, name=self._fresh("argv.p"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call_argv"],
                [fn_val, ir.Constant(_I64, n), argv_p],
                name=self._fresh(f"cpy.callN.{name_hint}"),
            )
        # Tag the result as a CPython value so ``print(it)`` and
        # similar downstream operations go through the conversion
        # path rather than treating the PyObject* as a pcc str.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_method_call_src(
        self, mod_val: ir.Value, attr_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``<CPython value>.method(args)`` through py_cpy_getattr
        + py_cpy_callN with scalar → CPython marshalling for typed args
        (int / float / str)."""
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"], [mod_val, attr_ptr],
            name=self._fresh(f"cpy.fn.{attr_name}"),
        )

        kwdict_unpack = self._split_starstar_kwargs_unpack(arg_exprs)
        if kwdict_unpack is not None:
            pos_exprs, kwargs_expr = kwdict_unpack
            if kwargs:
                return self._emit_cpy_call_kwdict_plus(
                    fn_val, attr_name, pos_exprs, kwargs, kwargs_expr,
                )
            if self._has_starred_unpack(pos_exprs):
                return self._emit_cpy_call_arglist_kwdict(
                    fn_val, attr_name, pos_exprs, kwargs_expr,
                )
            return self._emit_cpy_call_kwdict(
                fn_val, attr_name, pos_exprs, kwargs_expr,
            )

        if kwargs:
            return self._finish_cpy_call_kw(
                fn_val, attr_name, arg_exprs, kwargs,
            )

        if self._has_starred_unpack(arg_exprs):
            return self._emit_cpy_call_arglist(fn_val, attr_name, arg_exprs)
        if self._is_starred_unpack(arg_exprs):
            return self._emit_cpy_call_list(
                fn_val, attr_name, arg_exprs[0],
            )

        # Marshal each arg from its pcc native form to a CPython PyObject*.
        # ``owned`` parallel tracks whether we created the CPython ref
        # (and therefore must decref after the call).
        cpy_args: list[ir.Value] = []
        owned: list[bool] = []
        for arg in arg_exprs:
            v = self._emit_expr(arg)
            cpy_arg, is_owned = self._marshal_to_cpython(v, arg.ty)
            cpy_args.append(cpy_arg)
            owned.append(is_owned)

        n = len(cpy_args)
        if n == 0:
            result = self.builder.call(
                self.runtime["py_cpy_call_noargs"], [fn_val],
                name=self._fresh(f"cpy.call0.{attr_name}"),
            )
        elif n == 1:
            result = self.builder.call(
                self.runtime["py_cpy_call1"], [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{attr_name}"),
            )
        elif n == 2:
            result = self.builder.call(
                self.runtime["py_cpy_call2"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{attr_name}"),
            )
        elif n == 3:
            result = self.builder.call(
                self.runtime["py_cpy_call3"], [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{attr_name}"),
            )
        else:
            # Build an alloca argv[n] array and dispatch via
            # py_cpy_call_argv (PyObject_Call over a fresh tuple). The
            # runtime helper steals each argv[i] ref, so we do NOT
            # decref the owned args afterwards — only borrowed args
            # need a fresh ref (py_cpy_from_* produces one already).
            ptr_arr_ty = ir.ArrayType(_CSTR, n)
            argv = self._alloca_in_entry(
                ptr_arr_ty, name=f"cpy.argv.{attr_name}",
            )
            for i, (ca, is_owned) in enumerate(zip(cpy_args, owned)):
                if not is_owned:
                    # Caller-owned borrowed ref — promote to owned via
                    # ``py_cpy_incref`` so ``py_cpy_call_argv``'s
                    # ref-stealing via PyTuple_SetItem doesn't double-
                    # free the caller's handle. The bumped ref is
                    # balanced by the PyTuple_SetItem steal inside
                    # the helper.
                    self.builder.call(
                        self.runtime["py_cpy_incref"], [ca],
                    )
                idx0 = ir.Constant(_I32, 0)
                idx = ir.Constant(_I32, i)
                slot = self.builder.gep(argv, [idx0, idx], inbounds=True,
                                          name=self._fresh(f"argv.{i}"))
                self.builder.store(ca, slot)
            # Decay the array pointer to a ``ptr`` for the varargs call.
            argv_ptr = self.builder.bitcast(
                argv, _CSTR, name=self._fresh("argv.ptr"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call_argv"],
                [fn_val, ir.Constant(_I64, n), argv_ptr],
                name=self._fresh(f"cpy.calln.{attr_name}"),
            )
            # py_cpy_call_argv stole each owned ref; skip the decref
            # loop below.
            self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result

        # Release only the CPython args we owned (native scalars we
        # boxed). Borrowed DynType/CPython values keep their
        # caller-owned ref.
        for ca, is_owned in zip(cpy_args, owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])

        # Mark the result as a CPython value so downstream print/str go
        # through the conversion path.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _finish_cpy_call_kw(
        self, fn_val: ir.Value, name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs: tuple,
    ) -> ir.Value:
        """Dispatch a CPython callable with mixed positional + keyword
        arguments through ``py_cpy_call_kw``. Positional refs are stolen
        into the tuple; keyword refs are borrowed by PyDict_SetItem so
        we still decref our owned kw values after."""
        n_pos = len(pos_exprs)
        n_kw = len(kwargs)
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            v = self._emit_expr(arg)
            ca, _ = self._marshal_to_cpython(v, arg.ty)
            pos_vals.append(ca)
        kw_vals: list[ir.Value] = []
        kw_owned: list[bool] = []
        for _name, kv in kwargs:
            v = self._emit_expr(kv)
            ca, is_owned = self._marshal_to_cpython(v, kv.ty)
            kw_vals.append(ca)
            kw_owned.append(is_owned)

        # Build positional argv[n_pos]
        if n_pos == 0:
            pos_argv_ptr = ir.Constant(_CSTR, None)
        else:
            pos_arr_ty = ir.ArrayType(_CSTR, n_pos)
            pos_argv = self._alloca_in_entry(
                pos_arr_ty, name=f"cpy.pos.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"pos.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv, _CSTR, name=self._fresh("pos.p"),
            )

        if n_kw == 0:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
        else:
            names_arr_ty = ir.ArrayType(_CSTR, n_kw)
            vals_arr_ty = ir.ArrayType(_CSTR, n_kw)
            names_arr = self._alloca_in_entry(
                names_arr_ty, name=f"cpy.kwn.{name_hint}",
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty, name=f"cpy.kwv.{name_hint}",
            )
            for i, (kwn, _kv) in enumerate(kwargs):
                name_gv = self._cstr_global(
                    kwn, f".cpy.kwname.{name_hint}.{i}.{kwn}",
                )
                ngep = self.builder.gep(
                    names_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"kwn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)
                vgep = self.builder.gep(
                    vals_arr, [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True, name=self._fresh(f"kwv.{i}"),
                )
                self.builder.store(kw_vals[i], vgep)
            names_ptr = self.builder.bitcast(
                names_arr, _CSTR, name=self._fresh("kwn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr, _CSTR, name=self._fresh("kwv.p"),
            )

        result = self.builder.call(
            self.runtime["py_cpy_call_kw"],
            [fn_val, ir.Constant(_I64, n_pos), pos_argv_ptr,
             ir.Constant(_I64, n_kw), names_ptr, vals_ptr],
            name=self._fresh(f"cpy.callkw.{name_hint}"),
        )
        # kw_vals are borrowed by PyDict_SetItemString (refcount
        # incremented by CPython); decref any we owned.
        for ca, is_owned in zip(kw_vals, kw_owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _marshal_to_cpython(self, v: ir.Value, ty: Type) -> tuple[ir.Value, bool]:
        """Convert a pcc-native value to a CPython PyObject*.

        Returns (cpython_value, owned) — ``owned`` is True when the
        caller must decref the result after use. Borrowed values
        (already-CPython DynType) return False.
        """
        # IR-level guard that fires before the declared-type dispatch:
        # if we actually hold a native-scalar payload (double / float /
        # int) we need to box, regardless of what inference claimed.
        # This catches cases where the type inferrer widens to ``int``
        # or a class annotation but the IR value is a raw double from
        # a ``load double, ptr %f.addr``.
        if isinstance(v.type, (ir.DoubleType, ir.FloatType)):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_f64"], [v],
                    name=self._fresh("cpy.from_f64.ir"),
                ),
                True,
            )
        if isinstance(v.type, ir.IntType):
            if v.type.width == 1:
                i64 = self.builder.zext(v, _I64, name=self._fresh("cpy.b2i64.ir"))
            elif v.type.width == 64:
                i64 = v
            elif v.type.width < 64:
                i64 = self.builder.sext(
                    v, _I64, name=self._fresh("cpy.sext64.ir"),
                )
            else:
                i64 = self.builder.trunc(
                    v, _I64, name=self._fresh("cpy.trunc64.ir"),
                )
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_i64"], [i64],
                    name=self._fresh("cpy.from_i64.ir"),
                ),
                True,
            )
        if v in getattr(self, "_cpy_values", ()):
            return v, False
        if isinstance(ty, IntType) or isinstance(ty, BoolType):
            i64 = self._to_int64(v, ty)
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_i64"], [i64],
                    name=self._fresh("cpy.from_i64"),
                ),
                True,
            )
        if isinstance(ty, FloatType):
            if isinstance(v.type, ir.PointerType):
                # CPython fallback paths like ``float(x)`` already
                # return a CPython ``float`` object; forwarding that
                # object is correct, and avoids trying to re-box a ptr
                # as though it were a native ``double``.
                return v, False
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_f64"], [v],
                    name=self._fresh("cpy.from_f64"),
                ),
                True,
            )
        if isinstance(ty, StrType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pccstr"], [v],
                    name=self._fresh("cpy.from_pccstr"),
                ),
                True,
            )
        if isinstance(ty, NoneType):
            # None → CPython's Py_None (borrowed ref from the universal
            # converter on a pcc py_None). Use the same converter so we
            # don't have to teach codegen about the CPython Py_None sym.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [v],
                    name=self._fresh("cpy.from_pcc_none"),
                ),
                True,
            )
        if isinstance(ty, (ListType, DictType, TupleType)):
            # pcc-native list/dict/tuple — rebuild as a CPython container.
            # The universal converter walks the pcc object via type tag
            # and recurses through nested containers.
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [v],
                    name=self._fresh(f"cpy.from_pcc_{type(ty).__name__.lower()[:-4]}"),
                ),
                True,
            )
        # DynType with a native integer / float / pointer payload: pick
        # the marshaller that matches the IR type we actually hold.
        if isinstance(ty, DynType):
            if isinstance(v.type, ir.IntType):
                if v.type.width == 1:
                    # bool → CPython bool via int(0/1).
                    i64 = self.builder.zext(v, _I64, name=self._fresh("b2i64"))
                else:
                    i64 = v if v.type.width == 64 else self.builder.sext(
                        v, _I64, name=self._fresh("sext64"),
                    )
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_i64"], [i64],
                        name=self._fresh("cpy.from_i64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, (ir.FloatType, ir.DoubleType)):
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_f64"], [v],
                        name=self._fresh("cpy.from_f64.dyn"),
                    ),
                    True,
                )
            if isinstance(v.type, ir.PointerType):
                # Native pcc object (not already a CPython ref) —
                # rebuild the corresponding CPython object by walking
                # the runtime type tag.
                return (
                    self.builder.call(
                        self.runtime["py_cpy_from_pcc_obj"], [v],
                        name=self._fresh("cpy.from_pcc.dynobj"),
                    ),
                    True,
                )
        # ClassType / instance values are PyObject* in pcc-native form,
        # so route through the same py_cpy_from_pcc_obj bridge as the
        # DynType-pointer case. Without this clause, isinstance type
        # narrowing would surface ``Layer 1 cannot marshal ClassType``
        # at sites where the narrowed variable feeds a CPython call.
        if isinstance(ty, ClassType) and isinstance(v.type, ir.PointerType):
            return (
                self.builder.call(
                    self.runtime["py_cpy_from_pcc_obj"], [v],
                    name=self._fresh("cpy.from_pcc.cls"),
                ),
                True,
            )
        raise NotImplementedError(
            f"Layer 1 cannot marshal {type(ty).__name__} to CPython yet"
        )

    def _emit_static_method_call(
        self, method_fn: ir.Function, info,
        method_name: str, arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``ClassName.staticmethod(args)`` without any receiver
        and with argument coercion honouring declared annotations."""
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        # Always resolve kwargs + fill defaults so trailing params
        # with defaults land even when the call omitted them.
        if ast_fd is not None:
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args,
            ))
        elif kwargs:
            raise NotImplementedError(
                f"staticmethod {info.name}.{method_name} with kwargs "
                "needs a FuncDef to resolve parameter names"
            )
        declared = ast_fd.args if ast_fd else ()
        args_ir: list[ir.Value] = []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(method_fn, args_ir, call_name)

    def _emit_direct_method_call(
        self, method_fn: ir.Function, self_val: ir.Value,
        info, method_name: str, arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        # Always resolve positional → full arg list so defaults land on
        # omitted trailing params, not just when kwargs were supplied.
        # The earlier ``if kwargs:`` gate let calls like
        # ``self._mark(action, node)`` — where ``_mark(self, action,
        # node, detail="")`` has a default — slip through with only
        # 2 SSA operands, then clang rejected the resulting call as
        # ``not enough parameters specified``.
        if ast_fd is not None:
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args, skip_self=True,
            ))
        elif kwargs:
            raise NotImplementedError(
                f"method {info.name}.{method_name} with kwargs needs a "
                "FuncDef to resolve parameter names"
            )
        # Skip the receiver (``self`` / ``cls``) and the bare ``*``
        # kw-only separator when zipping against ``arg_exprs`` (which
        # ``_resolve_call_kwargs`` has already filtered).
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(method_fn, args_ir, call_name)

    def _emit_static_method_ptr_call(
        self, method_ptr: ir.Value, method_fn: ir.Function, info,
        method_name: str, arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if ast_fd is not None:
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args,
            ))
        elif kwargs:
            raise NotImplementedError(
                f"staticmethod {info.name}.{method_name} with kwargs "
                "needs a FuncDef to resolve parameter names"
            )
        declared = ast_fd.args if ast_fd else ()
        args_ir: list[ir.Value] = []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        callee = self.builder.bitcast(
            method_ptr, method_fn.type,
            name=self._fresh(f"{info.name}.{method_name}.super.fn"),
        )
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(callee, args_ir, call_name)

    def _emit_direct_method_ptr_call(
        self, method_ptr: ir.Value, method_fn: ir.Function,
        self_val: ir.Value, info, method_name: str,
        arg_exprs: tuple[Expr, ...], kwargs: tuple = (),
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        if ast_fd is not None:
            arg_exprs = tuple(self._resolve_call_kwargs(
                arg_exprs, kwargs, ast_fd.args, skip_self=True,
            ))
        elif kwargs:
            raise NotImplementedError(
                f"method {info.name}.{method_name} with kwargs needs a "
                "FuncDef to resolve parameter names"
            )
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        for i, arg_expr in enumerate(arg_exprs):
            v = self._emit_expr(arg_expr)
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, arg_expr.ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, arg_expr.ty,
                )
            args_ir.append(v)
        callee = self.builder.bitcast(
            method_ptr, method_fn.type,
            name=self._fresh(f"{info.name}.{method_name}.super.fn"),
        )
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(callee, args_ir, call_name)

    def _emit_direct_method_value_call(
        self, method_fn: ir.Function, self_val: ir.Value,
        info, method_name: str,
        arg_values: tuple[tuple[ir.Value, Type], ...],
    ) -> ir.Value:
        args_ir: list[ir.Value] = [self_val]
        ast_fd = self.class_lowering._find_method_def(info.name, method_name)
        declared = [a for a in ast_fd.args[1:] if a.name != ""] if ast_fd else []
        for i, (v, v_ty) in enumerate(arg_values):
            if i < len(declared) and declared[i].annotation is not None:
                v = self._coerce(v, v_ty, declared[i].annotation)
            else:
                v = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, v_ty,
                )
            args_ir.append(v)
        ret_ty = method_fn.function_type.return_type
        call_name = "" if isinstance(ret_ty, ir.VoidType) \
                       else self._fresh(f"{info.name}.{method_name}.ret")
        return self._call_user(method_fn, args_ir, call_name)

    def _maybe_emit_list_method(
        self, expr: Call, list_ty: ListType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``list`` methods directly to runtime helpers
        (libpython-free). Returns None when the method isn't in the fast
        path so callers can fall through to the generic dispatch."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None  # generic path handles or errors
        # Starred argument (``lst.method(*args)``) — bail to generic
        # CPython dispatch which handles splats via py_cpy_call_list.
        if any(
            isinstance(a, Call)
            and isinstance(a.func, Name)
            and a.func.ident in ("*", "__starred__")
            for a in expr.args
        ):
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv, name, expr.args, kwargs=expr.kwargs,
            )

        if name == "sort":
            if expr.args:
                return None
            elem_hint = None
            if isinstance(attr.obj, Name):
                elem_hint = self.env_list_elem_class_hint.get(attr.obj.ident)
            if elem_hint is None:
                return None
            return self._emit_list_sort_with_dunder_lt(
                recv, elem_hint, list_ty.elem,
            )

        def _box(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name == "append":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_append"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "extend":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_extend"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "insert":
            if len(expr.args) != 2:
                return None
            idx_val = self._emit_expr_as_i64(expr.args[0])
            self.builder.call(
                self.runtime["py_list_insert"],
                [recv, idx_val, _box(expr.args[1])],
            )
            return ir.Constant(_I1, 0)
        if name == "pop":
            if len(expr.args) == 0:
                idx_val = ir.Constant(_I64, -1)
            elif len(expr.args) == 1:
                idx_val = self._emit_expr_as_i64(expr.args[0])
            else:
                return None
            popped = self.builder.call(
                self.runtime["py_list_pop"], [recv, idx_val],
                name=self._fresh("list.pop"),
            )
            if not isinstance(list_ty.elem, DynType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime,
                    popped, list_ty.elem,
                )
            return popped
        if name == "remove":
            if len(expr.args) != 1:
                return None
            self.builder.call(
                self.runtime["py_list_remove"],
                [recv, _box(expr.args[0])],
            )
            return ir.Constant(_I1, 0)
        if name == "index":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_list_index"],
                [recv, _box(expr.args[0])],
                name=self._fresh("list.index"),
            )
        return None

    def _emit_list_sort_with_dunder_lt(
        self, recv: ir.Value, elem_hint: str, elem_ty: Type,
    ) -> ir.Value:
        lt_info = self._resolve_method_mro(elem_hint, "__lt__")
        if lt_info is None:
            return self._emit_none_literal()
        lt_fn = lt_info.methods["__lt__"]

        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_list_len"], [recv],
            name=self._fresh("list.sort.len"),
        )
        i_slot = self._alloca_in_entry(_I64, name="list.sort.i.addr")
        j_slot = self._alloca_in_entry(_I64, name="list.sort.j.addr")
        cur_slot = self._alloca_in_entry(_CSTR, name="list.sort.cur.addr")
        self.builder.store(ir.Constant(_I64, 1), i_slot)

        outer_cond = fn.append_basic_block(name=self._fresh("list.sort.cond"))
        outer_body = fn.append_basic_block(name=self._fresh("list.sort.body"))
        inner_cond = fn.append_basic_block(name=self._fresh("list.sort.inner"))
        inner_cmp = fn.append_basic_block(name=self._fresh("list.sort.cmp"))
        inner_shift = fn.append_basic_block(name=self._fresh("list.sort.shift"))
        inner_done = fn.append_basic_block(name=self._fresh("list.sort.place"))
        outer_step = fn.append_basic_block(name=self._fresh("list.sort.step"))
        done_bb = fn.append_basic_block(name=self._fresh("list.sort.done"))

        self.builder.branch(outer_cond)
        self.builder.position_at_end(outer_cond)
        i_val = self.builder.load(i_slot, name=self._fresh("list.sort.i"))
        outer_ok = self.builder.icmp_signed(
            "<", i_val, n_val, name=self._fresh("list.sort.outer_ok"),
        )
        self.builder.cbranch(outer_ok, outer_body, done_bb)

        self.builder.position_at_end(outer_body)
        cur = self.builder.call(
            self.runtime["py_list_get"], [recv, i_val],
            name=self._fresh("list.sort.cur"),
        )
        self.builder.store(cur, cur_slot)
        self.builder.store(i_val, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_cond)
        j_val = self.builder.load(j_slot, name=self._fresh("list.sort.j"))
        j_gt_zero = self.builder.icmp_signed(
            ">", j_val, ir.Constant(_I64, 0),
            name=self._fresh("list.sort.j_gt_zero"),
        )
        self.builder.cbranch(j_gt_zero, inner_cmp, inner_done)

        self.builder.position_at_end(inner_cmp)
        j_prev = self.builder.sub(
            j_val, ir.Constant(_I64, 1), name=self._fresh("list.sort.j_prev"),
        )
        prev = self.builder.call(
            self.runtime["py_list_get"], [recv, j_prev],
            name=self._fresh("list.sort.prev"),
        )
        cur = self.builder.load(cur_slot, name=self._fresh("list.sort.cur2"))
        less = self._emit_direct_method_value_call(
            lt_fn, cur, lt_info, "__lt__", ((prev, elem_ty),),
        )
        if less.type is not _I1:
            if isinstance(less.type, ir.IntType):
                less = self.builder.icmp_signed(
                    "!=", less, ir.Constant(less.type, 0),
                    name=self._fresh("list.sort.less_i1"),
                )
            else:
                truth = self.builder.call(
                    self.runtime["py_obj_truthy"], [less],
                    name=self._fresh("list.sort.less_truthy"),
                )
                less = self.builder.icmp_signed(
                    "!=", truth, ir.Constant(_I32, 0),
                    name=self._fresh("list.sort.less_truthy_i1"),
                )
        self.builder.cbranch(less, inner_shift, inner_done)

        self.builder.position_at_end(inner_shift)
        self.builder.call(self.runtime["py_list_set"], [recv, j_val, prev])
        self.builder.store(j_prev, j_slot)
        self.builder.branch(inner_cond)

        self.builder.position_at_end(inner_done)
        j_final = self.builder.load(j_slot, name=self._fresh("list.sort.j_final"))
        cur_final = self.builder.load(cur_slot, name=self._fresh("list.sort.cur3"))
        self.builder.call(
            self.runtime["py_list_set"], [recv, j_final, cur_final],
        )
        self.builder.branch(outer_step)

        self.builder.position_at_end(outer_step)
        i_next = self.builder.add(
            self.builder.load(i_slot, name=self._fresh("list.sort.i2")),
            ir.Constant(_I64, 1),
            name=self._fresh("list.sort.i_next"),
        )
        self.builder.store(i_next, i_slot)
        self.builder.branch(outer_cond)

        self.builder.position_at_end(done_bb)
        return self._emit_none_literal()

    def _maybe_emit_set_method(self, expr: Call) -> Optional[ir.Value]:
        """Dispatch selected pcc-native set methods.

        Sets are represented as ``DynType(name="set")`` until the frozen
        AST grows a first-class SetType, so this is keyed off the type
        hint rather than ``isinstance``.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        name = attr.name
        if name not in ("add", "remove", "discard", "update"):
            return None
        if len(expr.args) != 1:
            return None
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv, name, expr.args, kwargs=expr.kwargs,
            )
        if name == "update":
            self._spread_into_set(recv, expr.args[0])
            return self._emit_none_literal()
        item = self._emit_as_object(expr.args[0])
        if name == "add":
            self.builder.call(self.runtime["py_set_add"], [recv, item])
            return self._emit_none_literal()
        removed = self.builder.call(
            self.runtime["py_set_remove"], [recv, item],
            name=self._fresh("set.remove"),
        )
        if name == "discard":
            return self._emit_none_literal()
        missing = self.builder.icmp_signed(
            "<", removed, ir.Constant(_I64, 0),
            name=self._fresh("set.remove.missing"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.ok"),
        )
        miss_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.miss"),
        )
        end_bb = self.current_function.append_basic_block(
            name=self._fresh("set.remove.end"),
        )
        self.builder.cbranch(missing, miss_bb, ok_bb)
        self.builder.position_at_end(miss_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, 12),
                self._ptr_to_cstr(self._cstr_global(
                    "set.remove(x): x not in set",
                    ".err.set.remove",
                )),
            ],
            name=self._fresh("set.remove.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        self.builder.branch(end_bb)
        self.builder.position_at_end(ok_bb)
        self.builder.branch(end_bb)
        self.builder.position_at_end(end_bb)
        return self._emit_none_literal()

    _STR_METHOD_NATIVE = frozenset({
        "upper", "lower", "strip", "lstrip", "rstrip",
        "split", "join", "replace", "find", "count",
        "startswith", "endswith", "splitlines",
        "isdigit", "isalpha", "isspace", "isalnum",
    })

    def _extract_splitlines_keepends(self, expr: Call):
        """Return the ``keepends`` constant bool for a
        ``splitlines(keepends=…)`` call, or ``None`` if the caller
        didn't pass the keyword."""
        for key, v in (expr.kwargs or ()):
            if key == "keepends":
                if isinstance(v, BoolLit):
                    return bool(v.value)
                # Non-constant keepends — treat as ``True`` to be
                # safe; produced output preserves line endings.
                return True
        return None

    def _maybe_emit_str_method_via_dyn(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """DynType receiver whose method name matches one of the
        pcc-native str helpers — dispatch through the same runtime
        entries used by the StrType fast path. If the runtime value
        isn't actually a str, the helper crashes cleanly, matching
        Python's AttributeError behaviour in the spirit of 'no
        libpython'."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if attr.name not in self._STR_METHOD_NATIVE:
            return None
        # The only kwarg we recognise on a str method today is
        # ``splitlines(keepends=…)``. Everything else is routed via
        # the caller's fallback.
        if expr.kwargs and not (
            attr.name == "splitlines"
            and self._kwargs_are_only_keepends(expr.kwargs)
        ):
            return None
        # Re-use the StrType fast path by recovering the StrType
        # marshal for the receiver. The dyn value is already a
        # PyObject*; marshal_to_object is a no-op when it already
        # is.
        # Build an expr clone whose obj.ty is StrType so the
        # existing helper's type checks line up. Because ``expr`` is
        # a frozen dataclass we go directly to the dispatch using
        # the same implementation inlined here.
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            recv = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"], [recv],
                name=self._fresh(f"cpy.str.{name}.recv"),
            )
        # Guard against a native-scalar Dyn payload (i1 from a short-
        # circuit ``or``, i64 from an unboxed attribute read, etc.).
        # Box to PyObject* before passing to the py_str_* helpers
        # which all expect a pointer operand.
        if not isinstance(recv.type, ir.PointerType):
            recv = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                recv, attr.obj.ty,
            )

        def _str_arg(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name in (
            "upper", "lower", "strip", "lstrip", "rstrip",
        ) and not expr.args:
            fn = {
                "upper": "py_str_upper", "lower": "py_str_lower",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip", "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"dyn.str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.count"),
            )
        if name in (
            "isdigit", "isalpha", "isspace", "isalnum",
        ) and not expr.args:
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        if name == "splitlines" and not expr.args:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"], [recv],
                    name=self._fresh("dyn.str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("dyn.str.splitlines.keepends"),
            )
        if name == "split" and len(expr.args) <= 2:
            # ``split()`` with no args splits on whitespace — pass
            # NULL PyObject* to the runtime sep arg, which switches
            # py_str_split to the whitespace path.
            if expr.args:
                sep = _str_arg(expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_split_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("dyn.str.split.maxsplit"),
                )
            return self.builder.call(
                self.runtime["py_str_split"], [recv, sep],
                name=self._fresh("dyn.str.split"),
            )
        if name == "join" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.join"),
            )
        if name == "replace" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_str_replace"],
                [recv, _str_arg(expr.args[0]), _str_arg(expr.args[1])],
                name=self._fresh("dyn.str.replace"),
            )
        if name == "replace" and len(expr.args) == 3:
            maxreplace = self._emit_expr_as_i64(expr.args[2])
            return self.builder.call(
                self.runtime["py_str_replace_count"],
                [
                    recv,
                    _str_arg(expr.args[0]),
                    _str_arg(expr.args[1]),
                    maxreplace,
                ],
                name=self._fresh("dyn.str.replace.count"),
            )
        if name == "find" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("dyn.str.find"),
            )
        if name in ("startswith", "endswith") and len(expr.args) == 1:
            fn = {"startswith": "py_str_startswith",
                  "endswith": "py_str_endswith"}[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"dyn.str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"dyn.str.{name}.i1"),
            )
        return None

    def _maybe_emit_str_method(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``str`` methods via the pcc str runtime."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs and not (
            attr.name == "splitlines"
            and self._kwargs_are_only_keepends(expr.kwargs)
        ):
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            recv = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"], [recv],
                name=self._fresh(f"cpy.str.{name}.recv"),
            )
        # Receiver may be a non-pointer when it came from an ``or``
        # / ``and`` phi that ended at i1 / i64. Box to PyObject* so
        # the py_str_* runtime sees a proper pcc string.
        if not isinstance(recv.type, ir.PointerType):
            recv = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                recv, attr.obj.ty,
            )

        def _str_arg(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        def _i32_to_i1(v: ir.Value, nm: str) -> ir.Value:
            return self.builder.icmp_signed(
                "!=", v, ir.Constant(_I32, 0),
                name=self._fresh(nm),
            )

        if name in (
            "upper", "lower", "strip", "lstrip", "rstrip",
        ) and not expr.args:
            fn = {
                "upper": "py_str_upper", "lower": "py_str_lower",
                "strip": "py_str_strip",
                "lstrip": "py_str_lstrip", "rstrip": "py_str_rstrip",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"str.{name}"),
            )
        if name in ("strip", "lstrip", "rstrip") and len(expr.args) == 1:
            fn = {
                "strip": "py_str_strip_chars",
                "lstrip": "py_str_lstrip_chars",
                "rstrip": "py_str_rstrip_chars",
            }[name]
            return self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"str.{name}.chars"),
            )
        if name == "count" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_str_count"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.count"),
            )
        if name in (
            "isdigit", "isalpha", "isspace", "isalnum",
        ) and not expr.args:
            fn = {
                "isdigit": "py_str_isdigit",
                "isalpha": "py_str_isalpha",
                "isspace": "py_str_isspace",
                "isalnum": "py_str_isalnum",
            }[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv],
                name=self._fresh(f"str.{name}"),
            )
            return self.builder.icmp_signed(
                "!=", i32v, ir.Constant(_I32, 0),
                name=self._fresh(f"str.{name}.i1"),
            )
        if name == "splitlines" and not expr.args:
            keepends = self._extract_splitlines_keepends(expr)
            if keepends is None:
                return self.builder.call(
                    self.runtime["py_str_splitlines"], [recv],
                    name=self._fresh("str.splitlines"),
                )
            return self.builder.call(
                self.runtime["py_str_splitlines_keepends"],
                [recv, ir.Constant(_I32, 1 if keepends else 0)],
                name=self._fresh("str.splitlines.keepends"),
            )
        if name == "split":
            if len(expr.args) > 2:
                return None
            if expr.args:
                sep = _str_arg(expr.args[0])
            else:
                sep = ir.Constant(_CSTR, None)
            if len(expr.args) == 2:
                maxsplit = self._emit_expr_as_i64(expr.args[1])
                return self.builder.call(
                    self.runtime["py_str_split_maxsplit"],
                    [recv, sep, maxsplit],
                    name=self._fresh("str.split.maxsplit"),
                )
            return self.builder.call(
                self.runtime["py_str_split"], [recv, sep],
                name=self._fresh("str.split"),
            )
        if name == "join":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_join"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.join"),
            )
        if name == "replace":
            if len(expr.args) == 2:
                return self.builder.call(
                    self.runtime["py_str_replace"],
                    [recv, _str_arg(expr.args[0]), _str_arg(expr.args[1])],
                    name=self._fresh("str.replace"),
                )
            if len(expr.args) == 3:
                maxreplace = self._emit_expr_as_i64(expr.args[2])
                return self.builder.call(
                    self.runtime["py_str_replace_count"],
                    [
                        recv,
                        _str_arg(expr.args[0]),
                        _str_arg(expr.args[1]),
                        maxreplace,
                    ],
                    name=self._fresh("str.replace.count"),
                )
            return None
        if name == "find":
            if len(expr.args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_str_find"],
                [recv, _str_arg(expr.args[0])],
                name=self._fresh("str.find"),
            )
        if name in ("startswith", "endswith"):
            if len(expr.args) != 1:
                return None
            fn = {"startswith": "py_str_startswith",
                  "endswith": "py_str_endswith"}[name]
            i32v = self.builder.call(
                self.runtime[fn], [recv, _str_arg(expr.args[0])],
                name=self._fresh(f"str.{name}"),
            )
            return _i32_to_i1(i32v, f"str.{name}.i1")
        return None

    def _kwargs_are_only_keepends(self, kwargs: tuple) -> bool:
        for key, _value in kwargs:
            if key != "keepends":
                return False
        return True

    def _maybe_emit_dict_method(
        self, expr: Call, dict_ty: DictType,
    ) -> Optional[ir.Value]:
        """Dispatch selected ``dict`` methods directly to runtime helpers."""
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        name = attr.name
        recv = self._emit_expr(attr.obj)
        if recv in getattr(self, "_cpy_values", ()):
            return self._emit_cpy_method_call_src(
                recv, name, expr.args, kwargs=expr.kwargs,
            )

        def _box(e: Expr) -> ir.Value:
            v = self._emit_expr(e)
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, e.ty,
            )

        if name == "get":
            if len(expr.args) == 1:
                default = self._emit_none_literal()
                return self.builder.call(
                    self.runtime["py_dict_get_default"],
                    [recv, _box(expr.args[0]), default],
                    name=self._fresh("dict.get"),
                )
            if len(expr.args) == 2:
                return self.builder.call(
                    self.runtime["py_dict_get_default"],
                    [recv, _box(expr.args[0]), _box(expr.args[1])],
                    name=self._fresh("dict.get.dflt"),
                )
            return None
        if name == "keys":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_keys"], [recv],
                name=self._fresh("dict.keys"),
            )
        if name == "values":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_values"], [recv],
                name=self._fresh("dict.values"),
            )
        if name == "items":
            if expr.args:
                return None
            return self.builder.call(
                self.runtime["py_dict_items"], [recv],
                name=self._fresh("dict.items"),
            )
        if name == "setdefault" and len(expr.args) == 2:
            # ``d.setdefault(k, default)`` — if ``k`` exists, return
            # its value; otherwise insert and return ``default``.
            # Compile to: existing = py_dict_get(d, k); if existing is
            # NULL then py_dict_set(d, k, default); existing = default;
            # return existing.
            k_obj = _box(expr.args[0])
            default_obj = _box(expr.args[1])
            fn = self.current_function
            existing = self.builder.call(
                self.runtime["py_dict_get"], [recv, k_obj],
                name=self._fresh("setdefault.get"),
            )
            null_p = ir.Constant(_CSTR, None)
            is_missing = self.builder.icmp_signed(
                "==", existing, null_p,
                name=self._fresh("setdefault.miss"),
            )
            miss_bb = fn.append_basic_block(
                name=self._fresh("setdefault.miss"),
            )
            join_bb = fn.append_basic_block(
                name=self._fresh("setdefault.join"),
            )
            cur_bb = self.builder._block
            self.builder.cbranch(is_missing, miss_bb, join_bb)
            self.builder.position_at_end(miss_bb)
            self.builder.call(
                self.runtime["py_dict_set"], [recv, k_obj, default_obj],
            )
            miss_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(
                _CSTR, name=self._fresh("setdefault.result"),
            )
            phi.add_incoming(default_obj, miss_exit)
            phi.add_incoming(existing, cur_bb)
            return phi
        if name == "pop" and len(expr.args) == 2:
            k_obj = _box(expr.args[0])
            default_obj = _box(expr.args[1])
            fn = self.current_function
            existing = self.builder.call(
                self.runtime["py_dict_get"], [recv, k_obj],
                name=self._fresh("dict.pop.get"),
            )
            null_p = ir.Constant(_CSTR, None)
            is_missing = self.builder.icmp_signed(
                "==", existing, null_p,
                name=self._fresh("dict.pop.miss"),
            )
            hit_bb = fn.append_basic_block(name=self._fresh("dict.pop.hit"))
            miss_bb = fn.append_basic_block(name=self._fresh("dict.pop.miss"))
            join_bb = fn.append_basic_block(name=self._fresh("dict.pop.join"))
            self.builder.cbranch(is_missing, miss_bb, hit_bb)
            self.builder.position_at_end(hit_bb)
            self.builder.call(
                self.runtime["py_dict_del"], [recv, k_obj],
                name=self._fresh("dict.pop.del"),
            )
            hit_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(miss_bb)
            miss_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(_CSTR, name=self._fresh("dict.pop.result"))
            phi.add_incoming(existing, hit_exit)
            phi.add_incoming(default_obj, miss_exit)
            return phi
        return None

    _BUILTIN_TYPE_MATCHERS = {
        "str": StrType,
        "int": IntType,
        "float": FloatType,
        "bool": BoolType,
        "list": ListType,
        "dict": DictType,
        "tuple": TupleType,
    }

    _BUILTIN_TYPE_TAGS = {
        "bool": 1,
        "int": 2,
        "float": 3,
        "str": 4,
        "list": 5,
        "dict": 6,
        "tuple": 7,
    }

    def _compile_time_isinstance(
        self, obj_expr: Expr, class_ident: str,
    ) -> Optional[ir.Value]:
        """Resolve ``isinstance(x, BuiltinType)`` at compile time when
        the operand's static type is known, returning a constant ``i1``.
        Returns None if class_ident isn't a known builtin or the
        operand's type is DynType (needs runtime check, which today
        doesn't have a per-builtin helper)."""
        matcher = self._BUILTIN_TYPE_MATCHERS.get(class_ident)
        if matcher is None:
            return None
        ty = obj_expr.ty
        if isinstance(ty, DynType):
            return None
        return ir.Constant(_I1, 1 if isinstance(ty, matcher) else 0)

    def _emit_builtin_runtime_isinstance(
        self,
        obj_expr: Expr,
        class_ident: str,
        obj_val: Optional[ir.Value] = None,
    ) -> Optional[ir.Value]:
        tag = self._BUILTIN_TYPE_TAGS.get(class_ident)
        if tag is None:
            return None
        if obj_val is None:
            obj_val = self._emit_as_object(obj_expr)
        actual = self.builder.call(
            self.runtime["py_obj_type_tag"], [obj_val],
            name=self._fresh("obj.type_tag"),
        )
        return self.builder.icmp_signed(
            "==", actual, ir.Constant(_I64, tag),
            name=self._fresh("builtin.isinstance"),
        )

    def _ir_scaffold_class_symbol(self, expr: Expr) -> Optional[str]:
        """Return the ``pcc.llvm_capi.ir`` class name for ``ir.X``.

        In scaffold ON mode, ``from pcc.llvm_capi.compat import ir`` is
        compile-time sugar for calls into the native ``pcc.llvm_capi.ir``
        provider. ``isinstance(x, ir.PointerType)`` therefore needs to
        test against the provider's pcc class global, not fall through as
        an unknown foreign Python class.
        """
        if not self._ir_scaffold_enabled():
            return None
        if not isinstance(expr, Attr):
            return None
        symbol = self._ir_module_symbol_target(expr)
        if symbol is None:
            return None
        return symbol

    def _emit_ir_scaffold_isinstance(
        self, obj_val: ir.Value, class_name: str,
    ) -> ir.Value:
        g_name = ".class.pcc_llvm_capi_ir." + class_name
        existing = self.module.globals.get(g_name)
        if existing is None:
            gv = ir.GlobalVariable(self.module, _CSTR, name=g_name)
            gv.linkage = "external"
        else:
            gv = existing
        cls_ptr = self.builder.load(
            gv, name=self._fresh("ir.cls." + class_name),
        )
        res_i64 = self.builder.call(
            self.runtime["py_isinstance"], [obj_val, cls_ptr],
            name=self._fresh("ir.isinstance." + class_name),
        )
        return self.builder.icmp_signed(
            "!=", res_i64, ir.Constant(_I64, 0),
            name=self._fresh("ir.isinstance.i1"),
        )

    def _maybe_emit_dict_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``dict()`` → empty dict. ``dict(k1=v1, k2=v2)`` → set
        each kwarg. ``dict(another_dict)`` where arg is DictType
        → shallow copy via iterator-over-keys.
        Iterable-of-pairs form isn't supported yet."""
        new_dict = self.builder.call(
            self.runtime["py_dict_new"], [],
            name=self._fresh("dict.new"),
        )
        # kwargs form
        if not expr.args and expr.kwargs:
            for kw_name, kw_expr in expr.kwargs:
                k_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    self._emit_str_literal(kw_name),
                    StrType(name="str"),
                )
                v = self._emit_expr(kw_expr)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, kw_expr.ty,
                )
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [new_dict, k_obj, v_obj],
                )
            return new_dict
        if not expr.args:
            return new_dict
        arg = expr.args[0]
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType) or isinstance(arg_ty, DynType):
            # Shallow copy of a dict — iterate keys, get values,
            # insert into the new dict.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            keys_list = self.builder.call(
                self.runtime["py_dict_keys"], [src_obj],
                name=self._fresh("dict.copy.keys"),
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [keys_list],
                name=self._fresh("dict.copy.len"),
            )
            idx_slot = self._alloca_in_entry(
                _I64, name="dict.copy.idx.addr",
            )
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.cond"),
            )
            body_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.body"),
            )
            step_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.step"),
            )
            end_bb = fn.append_basic_block(
                name=self._fresh("dict.copy.end"),
            )
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            k_elem = self.builder.call(
                self.runtime["py_list_get"], [keys_list, cur],
                name=self._fresh("dict.copy.key"),
            )
            v_elem = self.builder.call(
                self.runtime["py_dict_get"], [src_obj, k_elem],
                name=self._fresh("dict.copy.val"),
            )
            self.builder.call(
                self.runtime["py_dict_set"],
                [new_dict, k_elem, v_elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_dict
        return None

    def _maybe_emit_tuple_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``tuple()`` / ``tuple([a, b])`` — small subset that matches
        pcc's own usage. Literal lists/tuples fold inline into a new
        ``py_tuple_new`` + per-element ``py_tuple_set_item``. Other
        iterable shapes return None (caller surfaces the original
        unknown-builtin error)."""
        if not expr.args:
            n_val = ir.Constant(_I64, 0)
            return self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            n = len(arg.elems)
            n_val = ir.Constant(_I64, n)
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
            for i, el in enumerate(arg.elems):
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [tup, ir.Constant(_I64, i), v_obj],
                )
            return tup
        # DynType / ListType / generic iterable: get the length,
        # allocate a tuple of that size, fill via py_obj_getitem.
        # DictType iterates keys (matching ``tuple(dict)`` semantics);
        # we materialise the keys as a list first then build the tuple
        # over that list.
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType):
            src_val = self._emit_expr(arg)
            src_val = self.builder.call(
                self.runtime["py_dict_keys"], [src_val],
                name=self._fresh("tuple.dict.keys"),
            )
            arg_ty = ListType(name="list", elem=arg_ty.key)
            n_val = self.builder.call(
                self.runtime["py_list_len"], [src_val],
                name=self._fresh("tuple.dict.keys.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tuple.dk.idx")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.dk.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.dk.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.dk.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.dk.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.dk.i"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("tuple.dk.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            elem = self.builder.call(
                self.runtime["py_list_get"], [src_val, cur],
                name=self._fresh("tuple.dk.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("tuple.dk.inc"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        if isinstance(arg_ty, (ListType, TupleType, DynType)):
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("tuple.src.len"),
            )
            tup = self.builder.call(
                self.runtime["py_tuple_new"], [n_val],
                name=self._fresh("tuple.new"),
            )
            fn = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="tuple.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("tuple.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("tuple.body"))
            step_bb = fn.append_basic_block(name=self._fresh("tuple.step"))
            end_bb = fn.append_basic_block(name=self._fresh("tuple.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("tuple.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("tuple.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("tuple.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("tuple.elem"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"], [tup, cur, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("tuple.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return tup
        return None

    def _emit_getattr_builtin(self, expr: Call) -> ir.Value:
        """``getattr(obj, name)`` / ``getattr(obj, name, default)``.
        CPython-backed receivers go through the real CPython builtin so
        module objects and the three-arg default form keep Python
        semantics. Native receivers use ``py_obj_getattr`` directly,
        with a null-check fallback for the defaulted form.
        """
        if self._expr_looks_cpython(expr.args[0]):
            fn_val = self._load_cpython_builtin("getattr")
            return self._emit_cpy_func_call(
                fn_val, "getattr", tuple(expr.args),
            )
        obj_val = self._emit_as_object(expr.args[0])
        name_expr = expr.args[1]
        if isinstance(name_expr, StrLit):
            name_ptr = self._attr_name_ptr(name_expr.value)
        else:
            # Dynamic name — marshal and use py_str_utf8 to grab
            # the C string.
            nv = self._emit_expr(name_expr)
            n_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                nv, name_expr.ty,
            )
            name_ptr = self.builder.call(
                self.runtime["py_str_utf8"], [n_obj],
                name=self._fresh("getattr.name"),
            )
        got = self.builder.call(
            self.runtime["py_obj_getattr"], [obj_val, name_ptr],
            name=self._fresh("getattr"),
        )
        if len(expr.args) == 2:
            return got
        default_obj = self._emit_as_object(expr.args[2])
        is_missing = self.builder.icmp_signed(
            "==", got, ir.Constant(_CSTR, None),
            name=self._fresh("getattr.missing"),
        )
        return self.builder.select(
            is_missing, default_obj, got,
            name=self._fresh("getattr.default"),
        )

    def _emit_type_builtin(self, expr: Call) -> ir.Value:
        """``type(obj)`` — returns the runtime class PyObject*.
        Uses ``py_obj_getattr(obj, "__class__")`` which the runtime
        resolves on any pcc-native object."""
        obj_val = self._emit_as_object(expr.args[0])
        name_ptr = self._attr_name_ptr("__class__")
        return self.builder.call(
            self.runtime["py_obj_getattr"], [obj_val, name_ptr],
            name=self._fresh("type"),
        )

    def _maybe_emit_zip_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``zip(a, b, ...)`` materialised as a pcc-native list of tuples.

        The Python frontend already normalises ``for ... in zip(...)`` to
        indexed loops. This builtin path covers value-position uses such
        as ``list(zip(xs, ys))`` and dict construction helpers in the
        bootstrap pipeline without pulling in CPython's iterator object.
        """
        if expr.kwargs or not expr.args:
            return None
        src_objs: list[ir.Value] = []
        lengths: list[ir.Value] = []
        for i, arg in enumerate(expr.args):
            raw = self._emit_expr(arg)
            obj = self._emit_value_as_pcc_object_or_bridge(
                raw, arg.ty, f"zip.arg.{i}.bridge",
            )
            src_objs.append(obj)
            lengths.append(
                self.builder.call(
                    self.runtime["py_obj_len"], [obj],
                    name=self._fresh(f"zip.len.{i}"),
                )
            )
        min_len = lengths[0]
        for i, n_val in enumerate(lengths[1:], start=1):
            take_n = self.builder.icmp_signed(
                "<", n_val, min_len, name=self._fresh(f"zip.min.cmp.{i}"),
            )
            min_len = self.builder.select(
                take_n, n_val, min_len, name=self._fresh(f"zip.min.{i}"),
            )
        result = self.builder.call(
            self.runtime["py_list_new"], [min_len],
            name=self._fresh("zip.list"),
        )
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name="zip.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("zip.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("zip.body"))
        step_bb = fn.append_basic_block(name=self._fresh("zip.step"))
        end_bb = fn.append_basic_block(name=self._fresh("zip.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("zip.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, min_len, name=self._fresh("zip.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("zip.idx.box"),
        )
        item = self.builder.call(
            self.runtime["py_tuple_new"], [ir.Constant(_I64, len(src_objs))],
            name=self._fresh("zip.item"),
        )
        for i, src_obj in enumerate(src_objs):
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh(f"zip.elem.{i}"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [item, ir.Constant(_I64, i), elem],
            )
        self.builder.call(self.runtime["py_list_append"], [result, item])
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur, ir.Constant(_I64, 1), name=self._fresh("zip.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return result

    def _maybe_emit_next_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``next(<genexpr>[, default])`` over pcc's eager genexpr list.

        pcc currently lowers generator expressions to materialised
        lists. This path keeps the common ``next((x for ...), None)``
        idiom native by selecting index 0 when the list is non-empty
        and the default otherwise.
        """
        if expr.kwargs or not (1 <= len(expr.args) <= 2):
            return None
        source = expr.args[0]
        if not (
            isinstance(source, Call)
            and isinstance(source.func, Name)
            and source.func.ident in ("_gen_comp", "__genexpr__")
        ):
            return None
        src_raw = self._emit_expr(source)
        src_obj = self._emit_value_as_pcc_object_or_bridge(
            src_raw, source.ty, "next.gen.bridge",
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [src_obj],
            name=self._fresh("next.gen.len"),
        )
        is_empty = self.builder.icmp_signed(
            "==", n_val, ir.Constant(_I64, 0),
            name=self._fresh("next.gen.empty"),
        )
        zero = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 0)],
            name=self._fresh("next.gen.zero"),
        )
        first = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, zero],
            name=self._fresh("next.gen.first"),
        )
        if len(expr.args) == 2:
            default_obj = self._emit_as_object(expr.args[1])
        else:
            default_obj = self._emit_none_literal()
        return self.builder.select(
            is_empty, default_obj, first, name=self._fresh("next.gen"),
        )

    def _maybe_emit_list_builtin(
        self, expr: Call,
    ) -> Optional[ir.Value]:
        """``list()`` / ``list([a, b])`` / ``list((a, b))`` / ``list(dict_keys)``.

        - no args → empty ``py_list_new(0)``.
        - list/tuple literal → alloc + per-element ``py_list_append``.
        - list-typed arg → same (materialises a copy).
        - dict-typed arg → ``py_dict_keys(d)`` (already a list).
        """
        new_list = self.builder.call(
            self.runtime["py_list_new"], [ir.Constant(_I64, 0)],
            name=self._fresh("list.new"),
        )
        if not expr.args:
            return new_list
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_list_append"], [new_list, v_obj],
                )
            return new_list
        arg_ty = arg.ty
        if isinstance(arg_ty, DictType):
            obj = self._emit_expr(arg)
            return self.builder.call(
                self.runtime["py_dict_keys"], [obj],
                name=self._fresh("list.from_dict"),
            )
        if isinstance(arg_ty, ListType):
            # list(x) where x is already a list — evaluate and return
            # the same PyObject*. (No copy; downstream mutation would
            # leak to the source. Phase-1 acceptable.)
            return self._emit_expr(arg)
        if isinstance(arg_ty, (TupleType, DynType, ClassType)):
            # Iterate source via py_obj_len + py_obj_getitem and
            # append to a fresh list. Works for any pcc-native
            # container that supports length + index access.
            # ClassType joins the same arm so ``list(<class instance>)``
            # in user code (e.g. shlex) compiles — runtime semantics
            # match the legacy DynType path; iterator-only classes
            # without __getitem__ fall through to the cpy fallback at
            # call site, but at least codegen succeeds.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("list.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="list.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("list.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("list.body"))
            step_bb = fn.append_basic_block(name=self._fresh("list.step"))
            end_bb = fn.append_basic_block(name=self._fresh("list.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("list.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("list.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("list.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("list.elem"),
            )
            self.builder.call(
                self.runtime["py_list_append"], [new_list, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("list.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_list
        return None

    def _maybe_emit_set_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``set()`` / ``set([a, b])`` / ``set((a, b, c))`` / ``set(iterable)``.

        - no args → empty ``py_set_new``.
        - literal list/tuple → allocate + add each element.
        - any other iterable (ListType / TupleType / DictType /
          DynType) → materialise as PyObject*, iterate via the
          generic ``py_obj_len`` + ``py_obj_getitem``, and add
          each element to the set.
        """
        new_set = self.builder.call(
            self.runtime["py_set_new"], [],
            name=self._fresh("set.new"),
        )
        if not expr.args:
            return new_set
        arg = expr.args[0]
        if isinstance(arg, (ListExpr, TupleExpr)):
            for el in arg.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident in ("*", "__starred__")
                    and len(el.args) == 1
                ):
                    # ``{x, *iterable}`` — spread iterable into the
                    # new set by iterating py_obj_len / py_obj_getitem
                    # and adding each element. Matches the list-literal
                    # splat ergonomics.
                    self._spread_into_set(new_set, el.args[0])
                    continue
                v = self._emit_expr(el)
                v_obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime,
                    v, el.ty,
                )
                self.builder.call(
                    self.runtime["py_set_add"], [new_set, v_obj],
                )
            return new_set
        arg_ty = arg.ty
        if isinstance(arg_ty, DynType) and arg_ty.name == "set":
            src_val = self._emit_expr(arg)
            self.builder.call(
                self.runtime["py_set_update"], [new_set, src_val],
            )
            return new_set
        if isinstance(arg_ty, (ListType, TupleType, DictType, DynType, StrType)):
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("set.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name="set.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            cond_bb = fn.append_basic_block(name=self._fresh("set.cond"))
            body_bb = fn.append_basic_block(name=self._fresh("set.body"))
            step_bb = fn.append_basic_block(name=self._fresh("set.step"))
            end_bb = fn.append_basic_block(name=self._fresh("set.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("set.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("set.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("set.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("set.elem"),
            )
            self.builder.call(
                self.runtime["py_set_add"], [new_set, elem],
            )
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("set.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return new_set
        return None

    def _emit_set_union_values(
        self, lhs: ir.Value, rhs: ir.Value,
    ) -> ir.Value:
        new_set = self.builder.call(
            self.runtime["py_set_new"], [],
            name=self._fresh("set.union"),
        )
        self.builder.call(self.runtime["py_set_update"], [new_set, lhs])
        self.builder.call(self.runtime["py_set_update"], [new_set, rhs])
        return new_set

    def _spread_into_set(self, dst_set: ir.Value, src_expr: "Expr") -> None:
        """Iterate ``src_expr`` and ``py_set_add`` each element to
        ``dst_set``. Used to lower the set-literal splat element
        ``{x, *iterable}`` (see ``_maybe_emit_set_builtin``)."""
        src_val = self._emit_expr(src_expr)
        src_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime,
            src_val, src_expr.ty,
        )
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [src_obj],
            name=self._fresh("set.spread.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="set.spread.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("set.spread.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("set.spread.body"))
        step_bb = fn.append_basic_block(name=self._fresh("set.spread.step"))
        end_bb = fn.append_basic_block(name=self._fresh("set.spread.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("set.spread.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh("set.spread.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh("set.spread.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, idx_box],
            name=self._fresh("set.spread.elem"),
        )
        self.builder.call(
            self.runtime["py_set_add"], [dst_set, elem],
        )
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur, ir.Constant(_I64, 1),
            name=self._fresh("set.spread.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def _maybe_emit_int_builtin(self, expr: Call) -> Optional[ir.Value]:
        """``int(x)`` / ``int(s, base)``:

        - int argument → identity (already int).
        - bool argument → ``zext`` to i64.
        - float argument → ``fptosi``.
        - str argument → ``py_int_from_cstr(utf8, base)`` then unbox.
        Returns None for unsupported shapes so the caller errors.
        """
        arg = expr.args[0]
        arg_ty = arg.ty
        base_val: ir.Value
        if len(expr.args) == 2:
            base_val = self._emit_expr_as_i64(expr.args[1])
            base_val = self.builder.trunc(
                base_val, _I32, name=self._fresh("int.base"),
            )
        else:
            base_val = ir.Constant(_I32, 10)
        if isinstance(arg_ty, IntType):
            return self._emit_expr(arg)
        if isinstance(arg_ty, BoolType):
            v = self._emit_expr(arg)
            if v.type is _I1:
                return self.builder.zext(
                    v, _I64, name=self._fresh("int.from_bool"),
                )
            return v
        if isinstance(arg_ty, FloatType):
            v = self._emit_expr(arg)
            return self.builder.fptosi(
                v, _I64, name=self._fresh("int.from_float"),
            )
        if isinstance(arg_ty, StrType):
            s_obj = self._emit_expr(arg)
            cstr = self.builder.call(
                self.runtime["py_str_utf8"], [s_obj],
                name=self._fresh("int.cstr"),
            )
            boxed = self.builder.call(
                self.runtime["py_int_from_cstr"], [cstr, base_val],
                name=self._fresh("int.parse"),
            )
            # Unbox to native i64 via the existing marshal helper.
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                boxed, IntType(name="int"),
            )
        if isinstance(arg_ty, DynType):
            obj = self._emit_expr(arg)
            if not isinstance(obj.type, ir.PointerType):
                obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, obj, arg_ty,
                )
            fn = self.current_function
            if fn is None:
                return None
            tag = self.builder.call(
                self.runtime["py_obj_type_tag"], [obj],
                name=self._fresh("int.dyn.tag"),
            )
            str_bb = fn.append_basic_block(name=self._fresh("int.dyn.str"))
            non_str_bb = fn.append_basic_block(
                name=self._fresh("int.dyn.non_str"),
            )
            float_bb = fn.append_basic_block(
                name=self._fresh("int.dyn.float"),
            )
            non_float_bb = fn.append_basic_block(
                name=self._fresh("int.dyn.non_float"),
            )
            bool_bb = fn.append_basic_block(name=self._fresh("int.dyn.bool"))
            int_bb = fn.append_basic_block(name=self._fresh("int.dyn.int"))
            join_bb = fn.append_basic_block(name=self._fresh("int.dyn.join"))

            is_str = self.builder.icmp_signed(
                "==", tag, ir.Constant(_I64, 4),
                name=self._fresh("int.dyn.is_str"),
            )
            self.builder.cbranch(is_str, str_bb, non_str_bb)

            self.builder.position_at_end(str_bb)
            cstr = self.builder.call(
                self.runtime["py_str_utf8"], [obj],
                name=self._fresh("int.dyn.cstr"),
            )
            boxed = self.builder.call(
                self.runtime["py_int_from_cstr"], [cstr, base_val],
                name=self._fresh("int.dyn.parse"),
            )
            str_val = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                boxed, IntType(name="int"),
            )
            str_exit = self.builder._block
            self.builder.branch(join_bb)

            self.builder.position_at_end(non_str_bb)
            is_float = self.builder.icmp_signed(
                "==", tag, ir.Constant(_I64, 3),
                name=self._fresh("int.dyn.is_float"),
            )
            self.builder.cbranch(is_float, float_bb, non_float_bb)

            self.builder.position_at_end(float_bb)
            f64 = self.builder.call(
                self.runtime["py_float_to_f64"], [obj],
                name=self._fresh("int.dyn.f64"),
            )
            float_val = self.builder.fptosi(
                f64, _I64, name=self._fresh("int.dyn.from_float"),
            )
            float_exit = self.builder._block
            self.builder.branch(join_bb)

            self.builder.position_at_end(non_float_bb)
            is_bool = self.builder.icmp_signed(
                "==", tag, ir.Constant(_I64, 1),
                name=self._fresh("int.dyn.is_bool"),
            )
            self.builder.cbranch(is_bool, bool_bb, int_bb)

            self.builder.position_at_end(bool_bb)
            bool_val = self.builder.call(
                self.runtime["py_obj_truthy"], [obj],
                name=self._fresh("int.dyn.from_bool"),
            )
            bool_exit = self.builder._block
            self.builder.branch(join_bb)

            self.builder.position_at_end(int_bb)
            int_val = self.builder.call(
                self.runtime["py_int_to_i64"],
                [obj, ir.Constant(_I32.as_pointer(), None)],
                name=self._fresh("int.dyn.from_int"),
            )
            int_exit = self.builder._block
            self.builder.branch(join_bb)

            self.builder.position_at_end(join_bb)
            phi = self.builder.phi(_I64, name=self._fresh("int.dyn"))
            phi.add_incoming(str_val, str_exit)
            phi.add_incoming(float_val, float_exit)
            phi.add_incoming(bool_val, bool_exit)
            phi.add_incoming(int_val, int_exit)
            return phi
        return None

    def _maybe_emit_sum_literal(self, expr: Call) -> Optional[ir.Value]:
        """``sum([a, b, c])`` / ``sum((a, b), start)`` for numeric
        literal containers — fold element-wise add, seeded with the
        start value if given else 0.

        Also handles the runtime case ``sum(iterable)`` when the
        iterable's static type is ``ListType`` / ``TupleType`` /
        ``DynType`` — assumes int elements and uses the generic
        ``py_obj_len`` / ``py_obj_getitem`` loop.
        """
        arg = expr.args[0]
        start = expr.args[1] if len(expr.args) == 2 else None
        if not isinstance(arg, (TupleExpr, ListExpr)):
            if not isinstance(
                arg.ty, (ListType, TupleType, DynType),
            ):
                return None
            # Runtime iteration path — always int-result; float
            # sum(iterable) falls through to NotImplementedError.
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg.ty,
            )
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh("sum.src.len"),
            )
            fn_ = self.current_function
            idx_slot = self._alloca_in_entry(_I64, name="sum.idx.addr")
            acc_slot = self._alloca_in_entry(_I64, name="sum.acc.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            if start is not None:
                start_i64 = self._emit_expr_as_i64(start)
                self.builder.store(start_i64, acc_slot)
            else:
                self.builder.store(ir.Constant(_I64, 0), acc_slot)
            cond_bb = fn_.append_basic_block(name=self._fresh("sum.cond"))
            body_bb = fn_.append_basic_block(name=self._fresh("sum.body"))
            step_bb = fn_.append_basic_block(name=self._fresh("sum.step"))
            end_bb = fn_.append_basic_block(name=self._fresh("sum.end"))
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(idx_slot, name=self._fresh("sum.idx"))
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh("sum.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh("sum.idx.box"),
            )
            elem_obj = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh("sum.elem"),
            )
            elem_i64 = marshal.marshal_from_object(
                self.builder, self.module, self.runtime,
                elem_obj, IntType(name="int"),
            )
            acc_cur = self.builder.load(
                acc_slot, name=self._fresh("sum.acc"),
            )
            new_acc = self.builder.add(
                acc_cur, elem_i64, name=self._fresh("sum.acc.next"),
            )
            self.builder.store(new_acc, acc_slot)
            self.builder.branch(step_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh("sum.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return self.builder.load(
                acc_slot, name=self._fresh("sum.result"),
            )
        elems = arg.elems
        start = expr.args[1] if len(expr.args) == 2 else None
        any_float = any(isinstance(e.ty, FloatType) for e in elems)
        if start is not None and isinstance(start.ty, FloatType):
            any_float = True
        if not all(
            isinstance(e.ty, (IntType, FloatType, BoolType))
            for e in elems
        ):
            return None
        if start is not None and not isinstance(
            start.ty, (IntType, FloatType, BoolType),
        ):
            return None
        if any_float:
            if start is not None:
                acc = self._emit_expr(start)
                if not isinstance(start.ty, FloatType):
                    acc = self.builder.sitofp(
                        acc, _DOUBLE, name=self._fresh("promote"),
                    )
            else:
                acc = ir.Constant(_DOUBLE, 0.0)
            for e in elems:
                v = self._emit_expr(e)
                if not isinstance(e.ty, FloatType):
                    v = self.builder.sitofp(
                        v, _DOUBLE, name=self._fresh("promote"),
                    )
                acc = self.builder.fadd(
                    acc, v, name=self._fresh("sum"),
                )
            return acc
        # All-int path.
        if start is not None:
            acc = self._emit_expr_as_i64(start)
        else:
            acc = ir.Constant(_I64, 0)
        for e in elems:
            v = self._emit_expr_as_i64(e)
            acc = self.builder.add(acc, v, name=self._fresh("sum"))
        return acc

    def _maybe_emit_any_all_literal(
        self, expr: Call, name: str,
    ) -> Optional[ir.Value]:
        """``any((a, b, c))`` / ``all([a, b, c])`` over a literal tuple
        or list — lower via a short-circuit chain of ``or`` / ``and``
        over the elements' truthiness. For runtime iterables
        (ListType / TupleType / DictType / DynType) iterate via
        ``py_obj_len`` / ``py_obj_getitem`` with early exit."""
        arg = expr.args[0]
        if not isinstance(arg, (TupleExpr, ListExpr)):
            arg_ty = arg.ty
            if not isinstance(
                arg_ty, (ListType, TupleType, DictType, DynType),
            ):
                return None
            src_val = self._emit_expr(arg)
            src_obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime,
                src_val, arg_ty,
            )
            fn_ = self.current_function
            n_val = self.builder.call(
                self.runtime["py_obj_len"], [src_obj],
                name=self._fresh(f"{name}.src.len"),
            )
            idx_slot = self._alloca_in_entry(_I64, name=f"{name}.idx.addr")
            self.builder.store(ir.Constant(_I64, 0), idx_slot)
            # Result alloca — default identity (any=False, all=True).
            result_slot = self._alloca_in_entry(
                _I1, name=f"{name}.result.addr",
            )
            init = 0 if name == "any" else 1
            self.builder.store(
                ir.Constant(_I1, init), result_slot,
            )
            cond_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.cond"),
            )
            body_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.body"),
            )
            step_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.step"),
            )
            end_bb = fn_.append_basic_block(
                name=self._fresh(f"{name}.end"),
            )
            self.builder.branch(cond_bb)
            self.builder.position_at_end(cond_bb)
            cur = self.builder.load(
                idx_slot, name=self._fresh(f"{name}.idx"),
            )
            cond = self.builder.icmp_signed(
                "<", cur, n_val, name=self._fresh(f"{name}.cond.i1"),
            )
            self.builder.cbranch(cond, body_bb, end_bb)
            self.builder.position_at_end(body_bb)
            idx_box = self.builder.call(
                self.runtime["py_int_from_i64"], [cur],
                name=self._fresh(f"{name}.idx.box"),
            )
            elem = self.builder.call(
                self.runtime["py_obj_getitem"], [src_obj, idx_box],
                name=self._fresh(f"{name}.elem"),
            )
            truthy_i32 = self.builder.call(
                self.runtime["py_obj_truthy"], [elem],
                name=self._fresh(f"{name}.truthy"),
            )
            truthy = self.builder.icmp_signed(
                "!=", truthy_i32, ir.Constant(_I32, 0),
                name=self._fresh(f"{name}.truthy.i1"),
            )
            if name == "any":
                # Truthy → result True, exit. Falsy → continue.
                exit_bb = fn_.append_basic_block(
                    name=self._fresh("any.hit"),
                )
                self.builder.cbranch(truthy, exit_bb, step_bb)
                self.builder.position_at_end(exit_bb)
                self.builder.store(ir.Constant(_I1, 1), result_slot)
                self.builder.branch(end_bb)
            else:  # all
                exit_bb = fn_.append_basic_block(
                    name=self._fresh("all.miss"),
                )
                self.builder.cbranch(truthy, step_bb, exit_bb)
                self.builder.position_at_end(exit_bb)
                self.builder.store(ir.Constant(_I1, 0), result_slot)
                self.builder.branch(end_bb)
            self.builder.position_at_end(step_bb)
            nxt = self.builder.add(
                cur, ir.Constant(_I64, 1),
                name=self._fresh(f"{name}.idx.next"),
            )
            self.builder.store(nxt, idx_slot)
            self.builder.branch(cond_bb)
            self.builder.position_at_end(end_bb)
            return self.builder.load(
                result_slot, name=self._fresh(f"{name}.result"),
            )
        elems = arg.elems
        if not elems:
            return ir.Constant(_I1, 0 if name == "any" else 1)

        # Open the diamond per element to get a true short-circuit
        # chain; phi at the join carries either the accumulated result
        # or the per-element truthy value.
        fn = self.current_function
        join_bb = fn.append_basic_block(
            name=self._fresh(f"{name}.join"),
        )
        incoming: list[tuple[ir.Value, object]] = []
        for i, elem in enumerate(elems):
            v = self._emit_expr(elem)
            truthy = self._truthy(v, elem.ty)
            is_last = (i == len(elems) - 1)
            if is_last:
                incoming.append((truthy, self.builder._block))
                self.builder.branch(join_bb)
                break
            next_bb = fn.append_basic_block(
                name=self._fresh(f"{name}.next"),
            )
            if name == "any":
                # Truthy wins — branch to join with True.
                true_val = ir.Constant(_I1, 1)
                # Need to go through a small block so the incoming
                # value recorded at join is the constant rather than
                # a phi-dependent SSA mapping.
                true_bb = fn.append_basic_block(
                    name=self._fresh("any.true"),
                )
                self.builder.cbranch(truthy, true_bb, next_bb)
                self.builder.position_at_end(true_bb)
                incoming.append((true_val, true_bb))
                self.builder.branch(join_bb)
            else:  # all
                false_val = ir.Constant(_I1, 0)
                false_bb = fn.append_basic_block(
                    name=self._fresh("all.false"),
                )
                self.builder.cbranch(truthy, next_bb, false_bb)
                self.builder.position_at_end(false_bb)
                incoming.append((false_val, false_bb))
                self.builder.branch(join_bb)
            self.builder.position_at_end(next_bb)

        self.builder.position_at_end(join_bb)
        phi = self.builder.phi(_I1, name=self._fresh(name))
        for val, pred_bb in incoming:
            phi.add_incoming(val, pred_bb)
        return phi

    def _emit_walrus(self, expr: Call) -> ir.Value:
        """Lower ``x := value`` from the ``_walrus`` sentinel emitted
        by ``pcc.parse.py_lift._e_Assign`` — evaluate the value,
        store into ``x`` via the name-assign helper, return the value
        for use in the surrounding expression."""
        if len(expr.args) != 2:
            raise L1CodegenError(
                "_walrus sentinel expects (target, value) args"
            )
        target, value_expr = expr.args
        if not isinstance(target, (Name, Attr)):
            raise NotImplementedError(
                "walrus target must be a plain Name or Attr"
            )
        value = self._emit_expr(value_expr)
        if isinstance(target, Name):
            self._store_value_at_name(target, value, value_expr.ty)
        else:
            self._emit_attr_store_value(target, value, value_expr.ty)
        return value

    def _maybe_emit_min_max_iter(
        self, expr: Call, name: str,
    ) -> Optional[ir.Value]:
        """``min(xs)`` / ``max(xs)`` on a ListType / TupleType /
        DynType iterable of ints. ``default`` kwarg is accepted via
        the resolver and seeds the accumulator on empty."""
        arg = expr.args[0]
        arg_ty = arg.ty
        if not isinstance(arg_ty, (ListType, TupleType, DynType)):
            return None
        # Optional ``default=`` kwarg.
        default_val = None
        for k, v in (expr.kwargs or ()):
            if k == "default":
                default_val = self._emit_expr_as_i64(v)
            else:
                return None  # unknown kwarg
        src_val = self._emit_expr(arg)
        src_obj = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, src_val, arg_ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"], [src_obj],
            name=self._fresh(f"{name}.src.len"),
        )
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name=f"{name}.idx.addr")
        acc_slot = self._alloca_in_entry(_I64, name=f"{name}.acc.addr")
        # Initial fill: if empty and no default → runtime error (we'd
        # have to emit a trap). With default, seed. With non-empty,
        # seed from elem[0] below.
        is_empty = self.builder.icmp_signed(
            "==", n_val, ir.Constant(_I64, 0),
            name=self._fresh(f"{name}.empty"),
        )
        empty_bb = fn.append_basic_block(name=self._fresh(f"{name}.empty"))
        seed_bb = fn.append_basic_block(name=self._fresh(f"{name}.seed"))
        self.builder.cbranch(is_empty, empty_bb, seed_bb)
        self.builder.position_at_end(empty_bb)
        if default_val is not None:
            self.builder.store(default_val, acc_slot)
        else:
            # No default: store 0 as a fallback (Python would raise
            # ValueError; we don't have exception wiring here).
            self.builder.store(ir.Constant(_I64, 0), acc_slot)
        self.builder.store(ir.Constant(_I64, 1), idx_slot)
        end_bb = fn.append_basic_block(name=self._fresh(f"{name}.end"))
        self.builder.branch(end_bb)

        self.builder.position_at_end(seed_bb)
        # Seed accumulator from index 0.
        zero_box = self.builder.call(
            self.runtime["py_int_from_i64"], [ir.Constant(_I64, 0)],
            name=self._fresh(f"{name}.seed.box"),
        )
        first = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, zero_box],
            name=self._fresh(f"{name}.first"),
        )
        first_i64 = marshal.marshal_from_object(
            self.builder, self.module, self.runtime,
            first, IntType(name="int"),
        )
        self.builder.store(first_i64, acc_slot)
        self.builder.store(ir.Constant(_I64, 1), idx_slot)

        cond_bb = fn.append_basic_block(name=self._fresh(f"{name}.cond"))
        body_bb = fn.append_basic_block(name=self._fresh(f"{name}.body"))
        step_bb = fn.append_basic_block(name=self._fresh(f"{name}.step"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh(f"{name}.idx"))
        cond = self.builder.icmp_signed(
            "<", cur, n_val, name=self._fresh(f"{name}.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"], [cur],
            name=self._fresh(f"{name}.idx.box"),
        )
        elem_obj = self.builder.call(
            self.runtime["py_obj_getitem"], [src_obj, idx_box],
            name=self._fresh(f"{name}.elem"),
        )
        elem_i64 = marshal.marshal_from_object(
            self.builder, self.module, self.runtime,
            elem_obj, IntType(name="int"),
        )
        acc_cur = self.builder.load(
            acc_slot, name=self._fresh(f"{name}.acc"),
        )
        cmp_op = "<" if name == "min" else ">"
        is_better = self.builder.icmp_signed(
            cmp_op, elem_i64, acc_cur,
            name=self._fresh(f"{name}.cmp"),
        )
        new_acc = self.builder.select(
            is_better, elem_i64, acc_cur,
            name=self._fresh(f"{name}.pick"),
        )
        self.builder.store(new_acc, acc_slot)
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur, ir.Constant(_I64, 1),
            name=self._fresh(f"{name}.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
        return self.builder.load(
            acc_slot, name=self._fresh(f"{name}.result"),
        )

    def _emit_min_max_builtin(self, expr: Call, name: str) -> ir.Value:
        """Lower ``min(a, b)`` / ``max(a, b)`` when both args are
        native int / float / bool (or DynType narrowed via
        ``_emit_expr_as_i64``). Non-numeric container forms fall
        through to NotImplementedError."""
        a_expr, b_expr = expr.args
        a_ty, b_ty = a_expr.ty, b_expr.ty

        def is_numeric_type(ty):
            return (
                isinstance(ty, IntType)
                or isinstance(ty, FloatType)
                or isinstance(ty, BoolType)
                or isinstance(ty, DynType)
            )

        if not (is_numeric_type(a_ty) and is_numeric_type(b_ty)):
            raise NotImplementedError(
                f"Layer 1 {name}() with non-numeric args "
                f"({a_ty!r}, {b_ty!r}) needs runtime support"
            )
        if isinstance(a_ty, FloatType) or isinstance(b_ty, FloatType):
            av = self._emit_expr(a_expr)
            bv = self._emit_expr(b_expr)
            if not isinstance(a_ty, FloatType):
                av = self.builder.sitofp(
                    av, _DOUBLE, name=self._fresh("promote"),
                )
            if not isinstance(b_ty, FloatType):
                bv = self.builder.sitofp(
                    bv, _DOUBLE, name=self._fresh("promote"),
                )
            cmp = self.builder.fcmp_ordered(
                "<" if name == "min" else ">",
                av, bv, name=self._fresh(f"{name}.cmp"),
            )
        else:
            av = self._emit_expr_as_i64(a_expr)
            bv = self._emit_expr_as_i64(b_expr)
            cmp = self.builder.icmp_signed(
                "<" if name == "min" else ">",
                av, bv, name=self._fresh(f"{name}.cmp"),
            )
        return self.builder.select(
            cmp, av, bv, name=self._fresh(name),
        )

    def _emit_abs_builtin(self, expr: Call) -> ir.Value:
        """``abs(x)`` for native int / float / bool, with DynType
        routed through CPython."""
        a_expr = expr.args[0]
        a_ty = a_expr.ty
        if isinstance(a_ty, DynType):
            fn_val = self._load_cpython_builtin("abs")
            return self._emit_cpy_func_call(fn_val, "abs", expr.args)
        if isinstance(a_ty, (IntType, BoolType)):
            v = self._emit_expr_as_i64(a_expr)
            zero = ir.Constant(_I64, 0)
            neg = self.builder.icmp_signed(
                "<", v, zero, name=self._fresh("abs.neg"),
            )
            negated = self.builder.sub(
                zero, v, name=self._fresh("abs.negate"),
            )
            return self.builder.select(
                neg, negated, v, name=self._fresh("abs"),
            )
        if isinstance(a_ty, FloatType):
            v = self._emit_expr(a_expr)
            zero = ir.Constant(_DOUBLE, 0.0)
            neg = self.builder.fcmp_ordered(
                "<", v, zero, name=self._fresh("abs.neg"),
            )
            negated = self.builder.fsub(
                zero, v, name=self._fresh("abs.negate"),
            )
            return self.builder.select(
                neg, negated, v, name=self._fresh("abs"),
            )
        raise NotImplementedError(
            f"Layer 1 abs() with arg type {a_ty!r} needs runtime support"
        )

    def _emit_isinstance_call(self, expr: Call) -> ir.Value:
        if len(expr.args) != 2:
            raise L1CodegenError(
                "isinstance expects exactly two arguments"
            )
        class_arg = expr.args[1]

        # Tuple form ``isinstance(x, (A, B, C))`` → OR of per-class checks.
        if isinstance(class_arg, TupleExpr):
            if not class_arg.elems:
                # Still emit the operand for side-effect parity.
                self._emit_as_object(expr.args[0])
                return ir.Constant(_I1, 0)
            names: list[str] = []
            ir_class_names: list[Optional[str]] = []
            for e in class_arg.elems:
                ir_symbol = self._ir_scaffold_class_symbol(e)
                if isinstance(e, Name):
                    names.append(e.ident)
                    ir_class_names.append(None)
                elif isinstance(e, Attr):
                    if isinstance(e.obj, Name):
                        self._ensure_native_module_alias_class_export(
                            e.obj.ident, e.name,
                        )
                    # ``mod.Class`` — use the tail token; the Name
                    # isn't in scope locally but may be a pcc-class
                    # name or a builtin (e.g. ``c_ast.Switch`` →
                    # ``Switch``). Unknown names fall through to
                    # compile-time False in the OR chain below.
                    names.append(e.name)
                    ir_class_names.append(ir_symbol)
                else:
                    raise NotImplementedError(
                        "isinstance tuple form requires bare class names "
                        "or module.name chains; got "
                        f"{type(e).__name__}"
                    )
            acc: Optional[ir.Value] = None
            obj_val: Optional[ir.Value] = None
            for idx, nm in enumerate(names):
                nm = self._resolve_class_alias(nm)
                ir_symbol = ir_class_names[idx]
                if ir_symbol is not None:
                    if obj_val is None:
                        obj_val = self._emit_as_object(expr.args[0])
                    ct = self._emit_ir_scaffold_isinstance(
                        obj_val, ir_symbol,
                    )
                else:
                    ct = self._compile_time_isinstance(expr.args[0], nm)
                if ct is None:
                    if nm in self._BUILTIN_TYPE_TAGS:
                        if obj_val is None:
                            obj_val = self._emit_as_object(expr.args[0])
                        ct = self._emit_builtin_runtime_isinstance(
                            expr.args[0], nm, obj_val,
                        )
                    elif nm in self.class_lowering.classes:
                        if obj_val is None:
                            obj_val = self._emit_as_object(expr.args[0])
                        ct = self.class_lowering.emit_isinstance(obj_val, nm)
                    else:
                        # Unknown class (imported from an external module
                        # that pcc can't introspect). Assume False so
                        # the OR-chain still resolves correctly when
                        # one of the builtin/native branches matches.
                        ct = ir.Constant(_I1, 0)
                acc = ct if acc is None else self.builder.or_(
                    acc, ct, name=self._fresh("isinstance_or"),
                )
            assert acc is not None
            return acc

        # ``mod.Class`` second-arg: use tail token as the class name.
        if isinstance(class_arg, Attr):
            if isinstance(class_arg.obj, Name):
                self._ensure_native_module_alias_class_export(
                    class_arg.obj.ident, class_arg.name,
                )
            cls_ident = class_arg.name
        elif isinstance(class_arg, Name):
            cls_ident = class_arg.ident
        else:
            raise NotImplementedError(
                "isinstance second argument must be a bare class name, "
                "a tuple of bare class names, or a module.attr chain"
            )
        cls_ident = self._resolve_class_alias(cls_ident)
        # Compile-time check for builtin types when operand type is known.
        ct = self._compile_time_isinstance(expr.args[0], cls_ident)
        if ct is not None:
            return ct
        ct = self._emit_builtin_runtime_isinstance(expr.args[0], cls_ident)
        if ct is not None:
            return ct
        ir_symbol = self._ir_scaffold_class_symbol(class_arg)
        if ir_symbol is not None:
            obj_val = self._emit_as_object(expr.args[0])
            return self._emit_ir_scaffold_isinstance(obj_val, ir_symbol)
        if cls_ident not in self.class_lowering.classes:
            if (
                isinstance(class_arg, Name)
                and (
                    class_arg.ident in self.env
                    or class_arg.ident in getattr(self, "_module_globals", {})
                )
            ):
                obj_val = self._emit_as_object(expr.args[0])
                cls_val = self._emit_expr(class_arg)
                raw = self.builder.call(
                    self.runtime["py_obj_isinstance"],
                    [obj_val, cls_val],
                    name=self._fresh("obj.isinstance"),
                )
                return self.builder.icmp_signed(
                    "!=", raw, ir.Constant(_I64, 0),
                    name=self._fresh("obj.isinstance.i1"),
                )
            # Unknown/foreign class — treat as False (see tuple form).
            self._emit_as_object(expr.args[0])
            return ir.Constant(_I1, 0)
        obj_val = self._emit_as_object(expr.args[0])
        return self.class_lowering.emit_isinstance(obj_val, cls_ident)

    def _emit_len_call(self, expr: Call) -> ir.Value:
        """``len(x)`` → type-specialised runtime call.

        For typed containers we dispatch to the type-specific runtime
        helper (``py_list_len`` etc.); otherwise we go through the
        generic ``py_obj_len``.
        """
        if len(expr.args) != 1:
            raise L1CodegenError(f"len() takes exactly 1 arg, got {len(expr.args)}")
        arg = expr.args[0]
        # Class-based ``__len__`` fast path.
        dunder = self._try_dispatch_dunder_unary(arg, "__len__", ())
        if dunder is not None:
            return dunder
        obj = self._emit_expr(arg)
        # CPython-backed value: dispatch through py_cpy_len (PyObject_Length).
        if obj in getattr(self, "_cpy_values", ()):
            return self.builder.call(
                self.runtime["py_cpy_len"], [obj],
                name=self._fresh("cpy.len"),
            )
        aty = arg.ty
        if isinstance(aty, ListType):
            return self.builder.call(
                self.runtime["py_list_len"], [obj], name=self._fresh("list.len")
            )
        if isinstance(aty, StrType):
            return self.builder.call(
                self.runtime["py_str_len"], [obj], name=self._fresh("str.len")
            )
        if isinstance(aty, DictType):
            return self.builder.call(
                self.runtime["py_dict_len"], [obj], name=self._fresh("dict.len")
            )
        if isinstance(aty, TupleType):
            return self.builder.call(
                self.runtime["py_tuple_len"], [obj], name=self._fresh("tup.len")
            )
        # Fallback through the generic helper. Any object with a
        # __len__ gets the right answer; non-sized types raise via the
        # runtime.
        boxed = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, obj, aty
        )
        return self.builder.call(
            self.runtime["py_obj_len"], [boxed], name=self._fresh("obj.len")
        )

    def _emit_str_builtin(self, expr: Call) -> ir.Value:
        """``str(x)`` → ``py_obj_str``; pass-through on already-str."""
        if len(expr.args) != 1:
            raise NotImplementedError("str() with multi-arg not supported")
        arg = expr.args[0]
        v = self._emit_expr(arg)
        if v in getattr(self, "_cpy_values", ()):
            return self.builder.call(
                self.runtime["py_cpy_to_pcc_str"], [v],
                name=self._fresh("cpy.to_pcc_str"),
            )
        if isinstance(arg.ty, StrType):
            return v
        boxed = marshal.marshal_to_object(
            self.builder, self.module, self.runtime, v, arg.ty
        )
        return self.builder.call(
            self.runtime["py_obj_str"], [boxed], name=self._fresh("obj.str")
        )

    def _extern_info_to_funcdef(self, name: str, info: dict) -> Optional[FuncDef]:
        call_sig = info.get("call_sig")
        if call_sig is None:
            return None
        span = SourceSpan(
            file="<extern>", line=0, col=0, end_line=0, end_col=0,
        )
        args = []
        for arg in call_sig:
            args.append(Arg(
                name=arg["name"],
                annotation=decode_type(arg.get("annotation")),
                default=arg.get("default"),
                kind=arg.get("kind", "pos"),
                has_default=arg.get(
                    "has_default", arg.get("default") is not None,
                ),
            ))
        return FuncDef(
            span=span,
            name=name,
            args=tuple(args),
            return_ty=decode_type(info.get("return_ty")) or DynType(name="dyn"),
            body=(),
            decorators=(),
        )

    def _find_user_funcdef(self, name: str) -> FuncDef:
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef) and stmt.name == name:
                return stmt
        # Cross-module: name was imported from a native sibling via
        # ``from .other import name`` during multi-file compile.
        cm = getattr(self, "_cross_module_func_defs", {})
        if name in cm and cm[name] is not None:
            return cm[name]
        raise L1CodegenError(f"no FuncDef for user function {name!r}")

    def _resolve_call_kwargs(
        self,
        positional: tuple,
        kwargs_pairs: tuple,
        formal_args: tuple,
        skip_self: bool = False,
    ) -> list:
        """Reorder positional + keyword call args to match formals.

        Returns an Expr list in formal-parameter order. Missing slots
        are filled from ``Arg.default``; an unbound slot without a
        default raises L1CodegenError, as do duplicate binds, unknown
        keywords, and surplus positionals.
        """
        formals: list = []
        src_i = 0
        while src_i < len(formal_args):
            formal = formal_args[src_i]
            # Bound instance/class method calls already provide the
            # receiver separately. Strip the first formal regardless of
            # its source-level name (`self`, `cls`, `self_or_op`, ...).
            if skip_self and src_i == 0:
                src_i += 1
                continue
            # Filter out the bare ``*`` separator — a kw_only marker
            # with an empty name has no runtime param. ``*args`` /
            # ``**kwargs`` with real names are separate kinds.
            if formal.name != "":
                formals.append(formal)
            src_i += 1
        n_formal = len(formals)
        resolved: list = []
        i = 0
        while i < n_formal:
            resolved.append(None)
            i += 1
        var_pos_idx = -1
        var_kw_idx = -1
        pos_formal_indices: list = []
        i = 0
        while i < n_formal:
            f = formals[i]
            if f.kind == "*args":
                var_pos_idx = i
            elif f.kind == "**kwargs":
                var_kw_idx = i
            elif f.kind == "pos" or f.kind == "pos_only":
                pos_formal_indices.append(i)
            i += 1
        extra_pos: list[Expr] = []
        i = 0
        while i < len(positional):
            e = positional[i]
            if i < len(pos_formal_indices):
                resolved[pos_formal_indices[i]] = e
                i += 1
                continue
            if var_pos_idx >= 0:
                extra_pos.append(e)
                i += 1
                continue
            raise L1CodegenError(
                f"too many positional args: got {len(positional)}, "
                f"expected at most {len(pos_formal_indices)}"
            )

        def _synthetic_span() -> SourceSpan:
            for expr in positional:
                return expr.span
            for _kw_name, kw_expr in kwargs_pairs:
                return kw_expr.span
            for formal in formals:
                if formal.default is not None:
                    return formal.default.span
            cur = getattr(self, "current_func_def", None)
            if cur is not None:
                return cur.span
            mod = self.ast_module.name or "<generated>"
            return SourceSpan(
                file=mod, line=1, col=1, end_line=1, end_col=1,
            )

        synth_span = _synthetic_span()
        if var_pos_idx >= 0:
            resolved[var_pos_idx] = TupleExpr(
                span=synth_span,
                ty=TupleType(
                    name="tuple", elems=tuple(e.ty for e in extra_pos),
                ),
                elems=tuple(extra_pos),
            )

        name_to_idx = {}
        i = 0
        while i < n_formal:
            f = formals[i]
            if f.kind != "*args" and f.kind != "**kwargs":
                name_to_idx[f.name] = i
            i += 1
        extra_kwargs: list[tuple[str, Expr]] = []
        for kw_name, kw_expr in kwargs_pairs:
            idx = name_to_idx.get(kw_name)
            if idx is None:
                if var_kw_idx >= 0:
                    extra_kwargs.append((kw_name, kw_expr))
                    continue
                formal_names = ",".join(f.name for f in formals)
                raise L1CodegenError(
                    f"unexpected keyword argument {kw_name!r}; "
                    f"formals=({formal_names})"
                )
            if formals[idx].kind == "pos_only":
                formal_names = ",".join(f.name for f in formals)
                raise L1CodegenError(
                    f"unexpected keyword argument {kw_name!r}; "
                    f"formals=({formal_names})"
                )
            if resolved[idx] is not None:
                raise L1CodegenError(
                    f"duplicate value for argument {kw_name!r}"
                )
            resolved[idx] = kw_expr

        if var_kw_idx >= 0:
            kw_pairs: list = []
            for kw_name, kw_expr in extra_kwargs:
                kw_pairs.append(
                    (
                        StrLit(
                            span=kw_expr.span,
                            ty=StrType(name="str"),
                            value=kw_name,
                        ),
                        kw_expr,
                    )
                )
            resolved[var_kw_idx] = DictExpr(
                span=synth_span,
                ty=DictType(
                    name="dict",
                    key=StrType(name="str"),
                    value=DynType(name="dyn"),
                ),
                pairs=tuple(kw_pairs),
            )

        i = 0
        while i < n_formal:
            formal = formals[i]
            if resolved[i] is None:
                if formal.kind == "*args":
                    resolved[i] = TupleExpr(
                        span=synth_span,
                        ty=TupleType(name="tuple", elems=()),
                        elems=(),
                    )
                    continue
                if formal.kind == "**kwargs":
                    resolved[i] = DictExpr(
                        span=synth_span,
                        ty=DictType(
                            name="dict",
                            key=StrType(name="str"),
                            value=DynType(name="dyn"),
                        ),
                        pairs=(),
                    )
                    continue
                if not getattr(formal, "has_default", False):
                    raise L1CodegenError(
                        f"missing required argument {formal.name!r} "
                        f"(positional={len(positional)}, "
                        f"kwargs={len(kwargs_pairs)}, "
                        f"formals={len(formals)})"
                    )
                resolved[i] = formal.default
            i += 1
        return resolved

    # -- Coercions / helpers ------------------------------------------

    def _to_int64(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, IntType):
            if v.type is _I64:
                return v
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, ty
                )
            # Should not happen in L1 (always i64), but guard anyway.
            return self.builder.sext(v, _I64, name=self._fresh("sext"))
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
            return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
        if isinstance(ty, NoneType):
            # Flow-insensitive inference can leave an Optional[int]-like
            # local typed as None even after guards refine the runtime
            # payload to an actual integer.
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
            if isinstance(v.type, ir.IntType):
                if v.type.width == 64:
                    return v
                if v.type.width == 1:
                    return self.builder.zext(v, _I64, name=self._fresh("none.b2i64"))
                return self.builder.sext(v, _I64, name=self._fresh("none.sext"))
        if isinstance(ty, FloatType):
            # Python semantic: ``int(3.7) == 3`` (truncate toward zero).
            return self.builder.fptosi(v, _I64, name=self._fresh("f2i"))
        if isinstance(ty, DynType):
            # Dynamic values: unbox via ``py_int_to_i64`` when we hold a
            # ``PyObject*``, or pass the native integer through if an
            # earlier coercion already produced one (common for chained
            # binops where the inner result is already ``i64``).
            if isinstance(v.type, ir.PointerType):
                # CPython-backed DynType values use a different unbox
                # path than pcc-native PyObject*.
                if v in getattr(self, "_cpy_values", ()):
                    return self.builder.call(
                        self.runtime["py_cpy_to_i64"], [v],
                        name=self._fresh("cpy.to_i64"),
                    )
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
            if isinstance(v.type, ir.IntType):
                if v.type.width == 64:
                    return v
                if v.type.width == 1:
                    return self.builder.zext(v, _I64, name=self._fresh("b2i64"))
                return self.builder.sext(v, _I64, name=self._fresh("sext"))
            if isinstance(v.type, (ir.FloatType, ir.DoubleType)):
                return self.builder.fptosi(v, _I64, name=self._fresh("f2i"))
        raise NotImplementedError(
            f"Layer 1 cannot coerce {type(ty).__name__} to int"
        )

    def _to_double(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, FloatType):
            return v
        if isinstance(ty, IntType):
            if isinstance(v.type, ir.PointerType):
                i64 = marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    IntType(name="int"),
                )
                return self.builder.sitofp(
                    i64, _DOUBLE, name=self._fresh("iobj2f"),
                )
            return self.builder.sitofp(v, _DOUBLE, name=self._fresh("i2f"))
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return self.builder.uitofp(v, _DOUBLE, name=self._fresh("b2f"))
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    FloatType(name="float"),
                )
            return self.builder.uitofp(v, _DOUBLE, name=self._fresh("b2f"))
        if isinstance(ty, DynType):
            if isinstance(v.type, ir.PointerType):
                if v in getattr(self, "_cpy_values", ()):
                    return self.builder.call(
                        self.runtime["py_cpy_to_f64"], [v],
                        name=self._fresh("cpy.to_f64"),
                    )
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v,
                    FloatType(name="float"),
                )
            # Raw native scalar (i64 / i1) held in a DynType slot.
            if v.type is _I64:
                return self.builder.sitofp(v, _DOUBLE, name=self._fresh("i2f"))
            if isinstance(v.type, ir.IntType):
                if v.type.width == 1:
                    return self.builder.uitofp(
                        v, _DOUBLE, name=self._fresh("b2f"),
                    )
                widened = self.builder.sext(
                    v, _I64, name=self._fresh("dyn.sext64"),
                )
                return self.builder.sitofp(
                    widened, _DOUBLE, name=self._fresh("i2f"),
                )
            if v.type is ir.DoubleType():
                return v
        raise NotImplementedError(
            f"Layer 1 cannot coerce {type(ty).__name__} to float"
        )

    def _truthy(self, v: ir.Value, ty: Type) -> ir.Value:
        if isinstance(ty, BoolType):
            if v.type is _I1:
                return v
            if isinstance(v.type, ir.PointerType):
                i32 = self.builder.call(
                    self.runtime["py_obj_truthy"], [v],
                    name=self._fresh("truthy_obj"),
                )
                return self.builder.trunc(i32, _I1,
                                            name=self._fresh("truthy_obj_i1"))
            return self.builder.icmp_signed("!=", v, ir.Constant(v.type, 0),
                                              name=self._fresh("truthy_int"))
        if isinstance(ty, IntType):
            if isinstance(v.type, ir.PointerType):
                i64 = marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, ty
                )
                zero = ir.Constant(_I64, 0)
                return self.builder.icmp_signed("!=", i64, zero,
                                                  name=self._fresh("truthy_i"))
            zero = ir.Constant(_I64, 0)
            return self.builder.icmp_signed("!=", v, zero,
                                              name=self._fresh("truthy_i"))
        if isinstance(ty, FloatType):
            zero = ir.Constant(_DOUBLE, 0.0)
            return self.builder.fcmp_ordered("!=", v, zero,
                                               name=self._fresh("truthy_f"))
        if self._is_object(ty) or isinstance(ty, DynType):
            # CPython-backed values must go through py_cpy_truthy
            # (PyObject_IsTrue) — the pcc py_obj_truthy only knows
            # about pcc's own PyObject layout.
            if v in getattr(self, "_cpy_values", ()):
                i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"], [v],
                    name=self._fresh("cpy.truthy"),
                )
                return self.builder.trunc(i32, _I1,
                                            name=self._fresh("cpy.truthy.i1"))
            # Any object: route through py_obj_truthy, which honours
            # container emptiness, None == False, etc.
            obj = marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, ty
            )
            i32 = self.builder.call(
                self.runtime["py_obj_truthy"], [obj],
                name=self._fresh("truthy_obj"),
            )
            return self.builder.trunc(i32, _I1,
                                        name=self._fresh("truthy_obj_i1"))
        raise NotImplementedError(
            f"Layer 1 cannot compute truthiness of {type(ty).__name__}"
        )

    def _coerce(self, v: ir.Value, from_ty: Type, to_ty: Type) -> ir.Value:
        """Coerce ``v`` (typed ``from_ty``) to ``to_ty``.

        Covers the L1 scalar matrix plus the L2 object-pass-through and
        native-↔-object marshalling cases.
        """
        if from_ty is None or to_ty is None:
            return v
        if isinstance(to_ty, IntType) and self._int_exprs_are_boxed():
            if isinstance(v.type, ir.PointerType):
                return v
            i64 = self._to_int64(v, from_ty)
            return self.builder.call(
                self.runtime["py_int_from_i64"], [i64],
                name=self._fresh("coerce.int.obj"),
            )
        if type(from_ty) is type(to_ty):
            # Same pcc_py type class. IR-level representations are
            # usually identical — but watch out for inference lying
            # about the payload.
            if isinstance(to_ty, (IntType, BoolType, FloatType)) and \
                    isinstance(v.type, ir.PointerType):
                # CPython-dispatched call returned PyObject* when
                # inference claimed a native scalar.
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, to_ty,
                )
            if self._is_object(to_ty) and not isinstance(v.type, ir.PointerType):
                # Short-circuit ``x or default`` over two StrType
                # operands bottoms at an i1 from the truthiness test
                # even though both operands and the result are object-
                # typed; box to a ptr before the caller consumes.
                return marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, from_ty,
                )
            return v
        # Native -> object marshal.
        if self._is_object(to_ty) and self._is_native_scalar_type(from_ty):
            if isinstance(v.type, ir.PointerType):
                return v
            return marshal.marshal_to_object(
                self.builder, self.module, self.runtime, v, from_ty
            )
        # Object -> native unbox.
        if self._is_native_scalar_type(to_ty) and self._is_object(from_ty):
            # A ``DynType`` value may already carry a native scalar at
            # the IR level (e.g. a BinOp that unboxed its operands);
            # only go through the runtime if we actually hold a
            # ``PyObject*``.
            if isinstance(v.type, ir.PointerType):
                return marshal.marshal_from_object(
                    self.builder, self.module, self.runtime, v, to_ty
                )
            if isinstance(to_ty, IntType):
                return self._to_int64(v, from_ty)
            if isinstance(to_ty, BoolType):
                return self._truthy(v, from_ty)
            if isinstance(to_ty, FloatType):
                return self._to_double(v, from_ty)
            return v
        # Object -> object (e.g. list -> dyn): ptr pass-through.
        if self._is_object(to_ty) and self._is_object(from_ty):
            # Guard: when inference widened to an object type but the
            # concrete IR value is still a native scalar (e.g. a
            # short-circuit ``x or default`` over two objects that
            # bottoms out at an i1 from the empty-str truthiness
            # test), box before continuing so the callee sees a ptr.
            if not isinstance(v.type, ir.PointerType):
                return marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, from_ty,
                )
            return v
        if isinstance(to_ty, FloatType):
            return self._to_double(v, from_ty)
        if isinstance(to_ty, IntType):
            return self._to_int64(v, from_ty)
        if isinstance(to_ty, BoolType):
            return self._truthy(v, from_ty)
        if isinstance(to_ty, NoneType):
            # Caller is expected to discard; leave value intact.
            return v
        if isinstance(to_ty, DynType):
            # Dyn accepts anything; upcast scalars to PyObject* so the
            # generic runtime helpers can handle them uniformly.
            if isinstance(v.type, ir.PointerType):
                return v
            if (
                self._is_native_scalar_type(from_ty)
                or isinstance(v.type, (ir.IntType, ir.FloatType, ir.DoubleType))
            ):
                return marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, v, to_ty
                )
            return v
        raise NotImplementedError(
            f"Layer 1/2 cannot coerce {type(from_ty).__name__} -> "
            f"{type(to_ty).__name__}"
        )

    def _is_native_scalar_type(self, ty: Type) -> bool:
        return isinstance(ty, (IntType, FloatType, BoolType))

    def _coerce_from_object(self, pyobj: ir.Value, target_ty: Type) -> ir.Value:
        """Unwrap ``pyobj`` into the representation of ``target_ty``.

        Object-typed targets stay as PyObject*; native targets go
        through :func:`marshal.marshal_from_object`.
        """
        if self._is_object(target_ty) or isinstance(target_ty, DynType):
            return pyobj
        if isinstance(target_ty, IntType) and self._int_exprs_are_boxed():
            return pyobj
        if self._is_native_scalar_type(target_ty):
            return marshal.marshal_from_object(
                self.builder, self.module, self.runtime, pyobj, target_ty
            )
        # Unknown target — return the boxed form untouched.
        return pyobj


__all__ = ["L1CodeGen", "L1CodegenError"]
