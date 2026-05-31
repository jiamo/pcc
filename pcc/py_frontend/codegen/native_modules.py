"""Native module alias and import-from helper lowering."""

from __future__ import annotations

import os
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..export_meta import decode_type
from ..py_ast import (
    Attr,
    Call,
    ClassDef,
    DynType,
    Expr,
    FuncDef,
    ImportFrom,
    IntLit,
    Name,
    NoneLit,
    SourceSpan,
    StrLit,
    Subscript,
)
from . import marshal

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()
_DOUBLE = ir.DoubleType()

_MATH_INF = 1e308 * 10.0
_MATH_NAN = (1e308 * 10.0) * 0.0


def _string_constant_value(name: str) -> Optional[str]:
    if name == "ascii_lowercase":
        return "abcdefghijklmnopqrstuvwxyz"
    if name == "ascii_uppercase":
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if name == "ascii_letters":
        return "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if name == "digits":
        return "0123456789"
    if name == "hexdigits":
        return "0123456789abcdefABCDEF"
    if name == "octdigits":
        return "01234567"
    if name == "punctuation":
        return "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    if name == "whitespace":
        return " \t\n\r\x0b\x0c"
    if name == "printable":
        return (
            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ \t\n\r\x0b\x0c"
        )
    return None


def _is_virtual_thread_export(name: str) -> bool:
    return (
        name == "spawn"
        or name == "run"
        or name == "run_until_idle"
        or name == "carrier_pool_start"
        or name == "carrier_pool_stop"
        or name == "result"
        or name == "state"
        or name == "sleep"
        or name == "block_on_fd"
    )


_CODECS_BOM_CONSTANTS: dict[str, bytes] = {
    "BOM_UTF8": b"\xef\xbb\xbf",
    "BOM_UTF32_LE": b"\xff\xfe\x00\x00",
    "BOM_UTF32_BE": b"\x00\x00\xfe\xff",
    "BOM_LE": b"\xff\xfe",
    "BOM_BE": b"\xfe\xff",
}


class NativeModuleAliasMixin:
    def _register_native_builtin_module_alias(
        self,
        local_name: str,
        module_name: str,
    ) -> None:
        self._native_builtin_module_aliases[local_name] = module_name

    def _clear_native_builtin_module_alias(self, local_name: str) -> None:
        aliases = self._native_builtin_module_aliases
        if aliases is not None:
            aliases.pop(local_name, None)

    def _register_native_builtin_value_alias(
        self,
        local_name: str,
        value_kind: str,
    ) -> None:
        self._native_builtin_value_aliases[local_name] = value_kind

    def _clear_native_builtin_value_alias(self, local_name: str) -> None:
        aliases = self._native_builtin_value_aliases
        if aliases is not None:
            aliases.pop(local_name, None)

    def _native_builtin_module_for_name(self, ident: str) -> Optional[str]:
        if ident in self.env:
            return None
        alias = self._native_builtin_module_aliases.get(ident)
        if alias is not None:
            return alias
        if ident in self._module_globals:
            return None
        return None

    def _native_builtin_value_for_name(self, ident: str) -> Optional[str]:
        if ident in self.env:
            return None
        alias = self._native_builtin_value_aliases.get(ident)
        if alias is not None:
            return alias
        if ident in self._module_globals:
            return None
        return None

    def _native_builtin_value_kind_for_expr(self, expr: Expr) -> Optional[str]:
        if isinstance(expr, Name):
            return self._native_builtin_value_for_name(expr.ident)
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "pcc"
            and expr.name == "valueclass"
        ):
            return "pcc.valueclass"
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "weakref"
            and expr.name
            in (
                "ref",
                "proxy",
                "WeakValueDictionary",
                "WeakKeyDictionary",
            )
        ):
            return "weakref." + expr.name
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "copy"
            and expr.name in ("copy", "deepcopy")
        ):
            return "copy." + expr.name
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "functools"
            and expr.name == "partial"
        ):
            return "functools.partial"
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "pickle"
            and expr.name in ("dumps", "loads")
        ):
            return "pickle." + expr.name
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident)
            == "pcc.virtual_thread"
            and _is_virtual_thread_export(expr.name)
        ):
            return "pcc.virtual_thread." + expr.name
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "threading"
            and expr.name
            in (
                "Thread",
                "Lock",
                "RLock",
                "Event",
                "Condition",
                "Semaphore",
                "current_thread",
                "get_ident",
            )
        ):
            return "threading." + expr.name
        if (
            isinstance(expr, Attr)
            and expr.name == "path"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "os"
        ):
            return "os.path"
        if (
            isinstance(expr, Attr)
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "time"
            and expr.name == "monotonic"
        ):
            return "time.monotonic"
        if (
            isinstance(expr, Attr)
            and expr.name == "exit"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
        ):
            return "sys.exit"
        if (
            isinstance(expr, Attr)
            and expr.name == "dedent"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "textwrap"
        ):
            return "textwrap.dedent"
        return None

    def _emit_native_builtin_value_call(
        self,
        builtin_value: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if builtin_value == "time.monotonic":
            return self._emit_native_time_monotonic_call(
                args,
                kwargs,
                "time.monotonic.value",
            )
        if builtin_value == "textwrap.dedent":
            return self._emit_native_textwrap_dedent_call(args, kwargs)
        if builtin_value in (
            "copy.copy",
            "copy.deepcopy",
            "pickle.dumps",
            "pickle.loads",
        ):
            return self._emit_native_copy_pickle_call(
                builtin_value,
                args,
                kwargs,
            )
        if builtin_value == "functools.partial":
            return self._emit_native_functools_partial_call(args, kwargs)
        if builtin_value == "pcc.valueclass":
            if kwargs or len(args) != 1:
                return None
            return marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                self._emit_expr(args[0]),
                args[0].ty,
            )
        return None

    def _emit_native_builtin_module_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        if not isinstance(attr, Attr):
            return None
        if not isinstance(attr.obj, Name):
            return None
        module_name = self._native_builtin_module_for_name(attr.obj.ident)
        if module_name == "time" and attr.name == "monotonic":
            return self._emit_native_time_monotonic_call(
                expr.args,
                expr.kwargs,
                "time.monotonic",
            )
        if module_name == "copy" and attr.name in ("copy", "deepcopy"):
            return self._emit_native_copy_pickle_call(
                "copy." + attr.name,
                expr.args,
                expr.kwargs,
            )
        if module_name == "pickle" and attr.name in ("dumps", "loads"):
            return self._emit_native_copy_pickle_call(
                "pickle." + attr.name,
                expr.args,
                expr.kwargs,
            )
        if module_name == "functools" and attr.name == "partial":
            return self._emit_native_functools_partial_call(
                expr.args, expr.kwargs)
        if module_name == "importlib":
            if attr.name == "import_module" and len(expr.args) == 1 and not expr.kwargs:
                return self._emit_native_importlib_import_module(expr)
            if attr.name == "reload" and len(expr.args) == 1 and not expr.kwargs:
                module = self._native_module_name_for_object_expr(expr.args[0])
                if module is not None:
                    return self._emit_native_module_placeholder(module)
        if module_name == "textwrap" and attr.name == "dedent":
            return self._emit_native_textwrap_dedent_call(expr.args, expr.kwargs)
        return None

    def _is_native_builtin_dynamic_module(self, module_name: str) -> bool:
        return module_name in (
            "math",
            "sys",
            "importlib",
        )

    def _is_native_dynamic_module(self, module_name: str) -> bool:
        if self._is_native_builtin_dynamic_module(module_name):
            return True
        native_table = self._native_module_exports or {}
        return module_name in native_table

    def _native_builtin_module_has_attr(self, module_name: str, attr_name: str) -> bool:
        if module_name == "math":
            return attr_name in (
                "pi",
                "e",
                "tau",
                "inf",
                "nan",
                "floor",
                "sqrt",
                "pow",
                "__dict__",
            )
        if module_name == "pcc":
            return attr_name in ("valueclass", "__dict__")
        if module_name == "codecs":
            return attr_name in _CODECS_BOM_CONSTANTS
        return False

    def _emit_native_builtin_module_dict(self, module_name: str) -> Optional[ir.Value]:
        if module_name != "math":
            return None
        d = self.builder.call(
            self.runtime["py_dict_new"],
            [],
            name=self._fresh("math.__dict__"),
        )
        entries = {
            "pi": 3.141592653589793,
            "e": 2.718281828459045,
            "tau": 6.283185307179586,
            "inf": _MATH_INF,
            "nan": _MATH_NAN,
        }
        for key, value in entries.items():
            self.builder.call(
                self.runtime["py_dict_set"],
                [
                    d,
                    self._emit_str_literal(key),
                    self._emit_native_module_constant(
                        {"value_kind": "float", "value": value},
                    ),
                ],
            )
        return d

    def _emit_native_builtin_module_attr(
        self,
        module_name: str,
        attr_name: str,
    ) -> Optional[ir.Value]:
        if module_name == "math":
            constants = {
                "pi": 3.141592653589793,
                "e": 2.718281828459045,
                "tau": 6.283185307179586,
                "inf": _MATH_INF,
                "nan": _MATH_NAN,
            }
            if attr_name in constants:
                return self._emit_native_module_constant(
                    {"value_kind": "float", "value": constants[attr_name]},
                )
            if attr_name == "__dict__":
                return self._emit_native_builtin_module_dict(module_name)
        if module_name == "typing" and attr_name == "TYPE_CHECKING":
            return self._emit_native_module_constant(
                {"value_kind": "bool", "value": False},
            )
        if module_name == "codecs" and attr_name in _CODECS_BOM_CONSTANTS:
            return self._emit_native_module_constant(
                {"value_kind": "bytes", "value": _CODECS_BOM_CONSTANTS[attr_name]},
            )
        return None

    def _emit_native_module_placeholder(self, module_name: str) -> ir.Value:
        module_dict = self._emit_native_builtin_module_dict(module_name)
        if module_dict is not None:
            return module_dict
        return self._emit_str_literal(module_name)

    def _emit_native_importlib_import_module(self, expr: Call) -> Optional[ir.Value]:
        arg = expr.args[0]
        if not isinstance(arg, StrLit):
            return None
        if self._is_native_dynamic_module(arg.value):
            return self._emit_native_module_placeholder(arg.value)
        self._emit_builtin_exception_and_branch(
            "ImportError",
            f"No module named {arg.value!r}",
            expr.span,
        )
        return ir.Constant(_CSTR, None)

    def _emit_native_functools_partial_call(self, args, kwargs):
        if kwargs or len(args) < 1:
            return None
        old = self._prefer_native_callable_values
        self._prefer_native_callable_values = True
        try:
            fn = self._emit_as_object(args[0])
        finally:
            self._prefer_native_callable_values = old
        bound = self._emit_dynamic_call_args_tuple(args[1:])
        result = self.builder.call(
            self.runtime["py_functools_partial"],
            [fn, bound],
            name=self._fresh("functools.partial"),
        )
        self._gc_release(bound)
        return result

    def _emit_native_copy_pickle_call(
        self,
        kind: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs:
            return None
        if kind == "copy.copy" and len(args) == 1:
            return self.builder.call(
                self.runtime["py_copy_copy"],
                [self._emit_as_object(args[0])],
                name=self._fresh("copy.copy"),
            )
        if kind == "copy.deepcopy" and len(args) == 1:
            return self.builder.call(
                self.runtime["py_copy_deepcopy"],
                [self._emit_as_object(args[0])],
                name=self._fresh("copy.deepcopy"),
            )
        if kind == "pickle.dumps" and 1 <= len(args) <= 2:
            protocol = (
                self._emit_as_object(args[1])
                if len(args) == 2
                else self._emit_none_literal()
            )
            return self.builder.call(
                self.runtime["py_pickle_dumps"],
                [self._emit_as_object(args[0]), protocol],
                name=self._fresh("pickle.dumps"),
            )
        if kind == "pickle.loads" and len(args) == 1:
            return self.builder.call(
                self.runtime["py_pickle_loads"],
                [self._emit_as_object(args[0])],
                name=self._fresh("pickle.loads"),
            )
        return None

    def _emit_native_time_monotonic_call(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
        result_name: str,
    ) -> Optional[ir.Value]:
        if len(args) > 0 or len(kwargs) > 0:
            return None
        return self.builder.call(
            self.runtime["py_time_monotonic"],
            [],
            name=self._fresh(result_name),
        )

    def _all_import_from_names_are_native_builtins(
        self,
        stmt: ImportFrom,
        import_module: str,
    ) -> bool:
        import os as _os
        import sys

        if _os.environ.get("PCC_PY_DEBUG_IMPORTS"):
            sys.stderr.write(
                f"[debug] _all_import_from_names_are_native_builtins mod={import_module!r} names={stmt.names!r}\n"
            )
        if import_module == "builtins":
            return all(attr_name == "int" for attr_name, _as_name in stmt.names)
        if import_module == "sys":
            return all(
                attr_name
                in ("exit", "stdout", "stderr", "prefix", "base_prefix")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "os":
            return all(
                attr_name in ("path", "sep", "linesep", "altsep", "pathsep")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "time":
            return all(attr_name == "monotonic" for attr_name, _as_name in stmt.names)
        if import_module == "string":
            return all(
                _string_constant_value(attr_name) is not None
                for attr_name, _as_name in stmt.names
            )
        if import_module == "dataclasses":
            return all(attr_name == "replace" for attr_name, _as_name in stmt.names)
        if import_module == "functools":
            return all(attr_name == "partial" for attr_name, _as_name in stmt.names)
        if import_module == "math":
            return all(
                attr_name
                in (
                    "floor",
                    "sqrt",
                    "pi",
                    "e",
                    "tau",
                    "inf",
                    "nan",
                    "pow",
                )
                for attr_name, _as_name in stmt.names
            )
        if import_module == "pcc":
            return all(
                attr_name in ("valueclass",) for attr_name, _as_name in stmt.names
            )
        if import_module == "re":
            return all(
                attr_name in ("match", "search")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "codecs":
            return all(
                attr_name in _CODECS_BOM_CONSTANTS
                for attr_name, _as_name in stmt.names
            )
        if import_module == "textwrap":
            return all(
                attr_name == "dedent"
                for attr_name, _as_name in stmt.names
            )
        if import_module == "gc":
            return all(
                attr_name
                in (
                    "collect",
                    "disable",
                    "enable",
                    "isenabled",
                    "is_tracked",
                    "is_finalized",
                    "get_count",
                    "get_threshold",
                    "set_threshold",
                    "get_stats",
                    "freeze",
                    "unfreeze",
                    "get_freeze_count",
                    "get_objects",
                    "get_referents",
                    "get_referrers",
                )
                for attr_name, _as_name in stmt.names
            )
        if import_module == "weakref":
            return all(
                attr_name in ("ref", "WeakValueDictionary", "WeakKeyDictionary")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "copy":
            return all(
                attr_name in ("copy", "deepcopy") for attr_name, _as_name in stmt.names
            )
        if import_module == "pickle":
            return all(
                attr_name in ("dumps", "loads") for attr_name, _as_name in stmt.names
            )
        if import_module == "threading":
            return all(
                attr_name
                in (
                    "Thread",
                    "Lock",
                    "RLock",
                    "Event",
                    "Condition",
                    "Semaphore",
                    "current_thread",
                    "get_ident",
                )
                for attr_name, _as_name in stmt.names
            )
        if import_module == "pcc.virtual_thread":
            for attr_name, _as_name in stmt.names:
                if not _is_virtual_thread_export(attr_name):
                    return False
            return True
        if import_module == "asyncio":
            return all(
                attr_name in ("run", "sleep") for attr_name, _as_name in stmt.names
            )
        if import_module == "contextlib":
            return all(
                attr_name == "contextmanager" for attr_name, _as_name in stmt.names
            )
        if import_module == "enum":
            return all(
                attr_name in ("Enum", "IntEnum", "auto")
                for attr_name, _as_name in stmt.names
            )
        if import_module == "typing":
            return all(
                attr_name
                in (
                    "Generic",
                    "Protocol",
                    "TypeVar",
                    "runtime_checkable",
                    "get_origin",
                    "get_args",
                    "Optional",
                )
                for attr_name, _as_name in stmt.names
            )
        if import_module == "pathlib":
            return all(
                attr_name in ("Path", "PurePath")
                for attr_name, _as_name in stmt.names
            )
        return False

    def _register_native_builtin_import_from_aliases(
        self,
        stmt: ImportFrom,
        import_module: str,
    ) -> bool:
        if not self._all_import_from_names_are_native_builtins(
            stmt,
            import_module,
        ):
            return False
        for attr_name, as_name in stmt.names:
            local_name = as_name or attr_name
            if attr_name == "int" and import_module == "builtins":
                self._register_native_builtin_value_alias(
                    local_name,
                    "builtins.int",
                )
                continue
            if attr_name == "path" and import_module == "os":
                self._register_native_builtin_value_alias(local_name, "os.path")
                continue
            if attr_name in ("Path", "PurePath") and import_module == "pathlib":
                self._register_native_builtin_value_alias(
                    local_name,
                    "pathlib." + attr_name,
                )
                continue
            if attr_name == "monotonic" and import_module == "time":
                self._register_native_builtin_value_alias(
                    local_name,
                    "time.monotonic",
                )
                continue
            if attr_name == "partial" and import_module == "functools":
                self._register_native_builtin_value_alias(
                    local_name,
                    "functools.partial",
                )
                continue
            if attr_name in ("sep", "linesep", "altsep", "pathsep") and import_module == "os":
                self._register_native_builtin_value_alias(
                    local_name,
                    "os." + attr_name,
                )
                continue
            string_value = _string_constant_value(attr_name)
            if string_value is not None and import_module == "string":
                self._register_native_module_constant(
                    local_name,
                    {
                        "kind": "constant",
                        "value_kind": "str",
                        "value": string_value,
                    },
                )
                continue
            if attr_name == "exit" and import_module == "sys":
                self._register_native_builtin_value_alias(local_name, "sys.exit")
                continue
            if attr_name == "replace" and import_module == "dataclasses":
                self._register_native_builtin_value_alias(
                    local_name,
                    "dataclasses.replace",
                )
                continue
            if attr_name in ("floor", "sqrt") and import_module == "math":
                self._register_native_builtin_value_alias(
                    local_name,
                    "math." + attr_name,
                )
                continue
            if attr_name == "pow" and import_module == "math":
                self._register_native_builtin_value_alias(
                    local_name,
                    "math.pow",
                )
                continue
            if attr_name == "valueclass" and import_module == "pcc":
                self._register_native_builtin_value_alias(
                    local_name,
                    "pcc.valueclass",
                )
                continue
            if (
                attr_name in ("pi", "e", "tau", "inf", "nan")
                and import_module == "math"
            ):
                self._register_native_module_constant(
                    local_name,
                    {
                        "kind": "constant",
                        "value_kind": "float",
                        "value": {
                            "pi": 3.141592653589793,
                            "e": 2.718281828459045,
                            "tau": 6.283185307179586,
                            "inf": _MATH_INF,
                            "nan": _MATH_NAN,
                        }[attr_name],
                    },
                )
                continue
            if attr_name in ("match", "search") and import_module == "re":
                self._register_native_builtin_value_alias(
                    local_name,
                    "re." + attr_name,
                )
                continue
            if attr_name == "dedent" and import_module == "textwrap":
                self._register_native_builtin_value_alias(
                    local_name,
                    "textwrap.dedent",
                )
                continue
            if attr_name in _CODECS_BOM_CONSTANTS and import_module == "codecs":
                self._register_native_module_constant(
                    local_name,
                    {
                        "kind": "constant",
                        "value_kind": "bytes",
                        "value": _CODECS_BOM_CONSTANTS[attr_name],
                    },
                )
                continue
            if (
                attr_name
                in (
                    "collect",
                    "disable",
                    "enable",
                    "isenabled",
                    "is_tracked",
                    "is_finalized",
                    "get_count",
                    "get_threshold",
                    "set_threshold",
                    "get_stats",
                    "freeze",
                    "unfreeze",
                    "get_freeze_count",
                    "get_objects",
                    "get_referents",
                    "get_referrers",
                )
                and import_module == "gc"
            ):
                self._register_native_builtin_value_alias(
                    local_name,
                    "gc." + attr_name,
                )
                continue
            if (
                attr_name in ("ref", "WeakValueDictionary", "WeakKeyDictionary")
                and import_module == "weakref"
            ):
                self._register_native_builtin_value_alias(
                    local_name,
                    "weakref." + attr_name,
                )
                continue
            if attr_name in ("copy", "deepcopy") and import_module == "copy":
                self._register_native_builtin_value_alias(
                    local_name,
                    "copy." + attr_name,
                )
                continue
            if attr_name in ("dumps", "loads") and import_module == "pickle":
                self._register_native_builtin_value_alias(
                    local_name,
                    "pickle." + attr_name,
                )
                continue
            if (
                attr_name
                in (
                    "Thread",
                    "Lock",
                    "RLock",
                    "Event",
                    "Condition",
                    "Semaphore",
                    "current_thread",
                    "get_ident",
                )
                and import_module == "threading"
            ):
                self._register_native_builtin_value_alias(
                    local_name,
                    "threading." + attr_name,
                )
                continue
            if import_module == "pcc.virtual_thread" and _is_virtual_thread_export(
                attr_name
            ):
                self._register_native_builtin_value_alias(
                    local_name,
                    "pcc.virtual_thread." + attr_name,
                )
                continue
            if attr_name in ("run", "sleep") and import_module == "asyncio":
                self._register_native_builtin_value_alias(
                    local_name,
                    "asyncio." + attr_name,
                )
                continue
            if attr_name == "contextmanager" and import_module == "contextlib":
                self._register_native_builtin_value_alias(
                    local_name,
                    "contextlib.contextmanager",
                )
                continue
            if attr_name in ("stdout", "stderr") and import_module == "sys":
                self._register_native_builtin_value_alias(
                    local_name,
                    "sys." + attr_name,
                )
                continue
            if attr_name in ("prefix", "base_prefix") and import_module == "sys":
                self._register_native_builtin_value_alias(
                    local_name,
                    "sys." + attr_name,
                )
                continue
            if attr_name in ("Enum", "IntEnum", "auto") and import_module == "enum":
                self._register_native_builtin_value_alias(
                    local_name,
                    "enum." + attr_name,
                )
                continue
            if (
                attr_name
                in (
                    "Generic",
                    "Protocol",
                    "TypeVar",
                    "runtime_checkable",
                    "get_origin",
                    "get_args",
                    "Optional",
                )
                and import_module == "typing"
            ):
                self._register_native_builtin_value_alias(
                    local_name,
                    "typing." + attr_name,
                )
                continue
        return True

    def _bind_native_cross_module_export(
        self,
        *,
        local_name: str,
        src_module: str,
        attr_name: str,
        info: dict,
    ) -> bool:
        """Bind one export from a native sibling module.

        Used by both explicit imports and native star-import expansion. Returns
        False for export kinds that still need the caller's CPython fallback.
        """
        kind = info.get("kind")
        owning_module = info.get("owning_module", src_module)
        export_name = info.get("export_name", attr_name)
        if kind == "function":
            sanitised = owning_module.replace(".", "_").replace("-", "_")
            sym = f"user_{sanitised}_{export_name}"
            existing = self.module.globals.get(sym)
            if isinstance(existing, ir.Function):
                fn = existing
            else:
                box_int_abi = self._export_box_int_abi(info)
                param_tys = [
                    self._abi_ir_type(
                        decode_type(t),
                        box_int_abi=box_int_abi,
                    )
                    for t in info["param_types"]
                ]
                ret_ty = decode_type(info["return_ty"]) or DynType(name="dyn")
                fnty = ir.FunctionType(
                    self._abi_ir_type(ret_ty, box_int_abi=box_int_abi),
                    param_tys,
                )
                fn = ir.Function(self.module, fnty, name=sym)
                fn.linkage = "external"
            self.functions[local_name] = fn
            self._cross_module_func_defs[local_name] = self._extern_info_to_funcdef(
                local_name,
                info,
            )
            return True
        if kind == "class":
            self._declare_native_module_extern_class(
                owning_module=owning_module,
                class_name=info["class_name"],
                field_names=info["field_names"],
                methods=info["methods"],
                local_name=local_name,
                base_names=info.get("base_names", ()),
            )
            return True
        if kind == "constant":
            self._register_native_module_constant(local_name, info)
            return True
        if kind == "module_global":
            self._declare_native_module_extern_global(
                local_name,
                owning_module,
                export_name,
                info,
            )
            return True
        return False

    def _predeclare_native_cross_module(
        self,
        stmt: ImportFrom,
        src_module: str,
        exports: dict,
    ) -> None:
        """First-pass declaration for native cross-module imports:
        declare the extern function globals and bind them in
        ``self.functions`` so user-function bodies lowered in the
        same compilation unit can resolve the call. Class imports
        declare the class global + method externs via
        ``class_lowering.declare_extern_class``."""
        for attr_name, as_name in stmt.names:
            if attr_name == "*":
                for export_name, export_info in exports.items():
                    if export_name.startswith("_"):
                        continue
                    self._bind_native_cross_module_export(
                        local_name=export_name,
                        src_module=src_module,
                        attr_name=export_name,
                        info=export_info,
                    )
                continue
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                full_submodule = self._native_import_from_submodule(
                    src_module,
                    attr_name,
                )
                if full_submodule is not None:
                    self._register_native_module_alias(
                        local_name,
                        full_submodule,
                    )
                    continue
                # Not a native export — pre-seed a CPython-module
                # global so the Name lookup inside function bodies
                # resolves. The main-body walker will emit the actual
                # import via _import_from_cpython_single.
                self._cpy_module_global(local_name)
                self._cpy_modules()[local_name] = self._cpy_module_global(local_name)
                continue
            if self._bind_native_cross_module_export(
                local_name=local_name,
                src_module=src_module,
                attr_name=attr_name,
                info=info,
            ):
                continue
            # Other kinds — fall through to CPython shim.

    def _native_import_from_submodule(
        self,
        src_module: str,
        attr_name: str,
    ) -> Optional[str]:
        """Return ``pkg.attr`` when ``from pkg import attr`` names a
        native sibling submodule compiled in the same invocation."""
        native_table = self._native_module_exports
        if native_table is None or not src_module or attr_name == "*":
            return None
        full_name = f"{src_module}.{attr_name}"
        if full_name in native_table:
            return full_name
        return None

    def _has_native_import_from_targets(
        self,
        stmt: ImportFrom,
        src_module: str,
    ) -> bool:
        """Whether ``from src import ...`` touches any native sibling
        export or native sibling submodule."""
        native_table = self._native_module_exports
        if native_table is None:
            return False
        if src_module in native_table:
            return True
        for attr_name, _ in stmt.names:
            if self._native_import_from_submodule(src_module, attr_name):
                return True
        return False

    def _bind_native_cross_module_imports(
        self,
        stmt: ImportFrom,
        src_module: str,
        exports: dict,
    ) -> None:
        """For each name imported from a native sibling module,
        declare an extern function of matching signature and register
        in ``self.functions`` so subsequent calls resolve to it."""
        for attr_name, as_name in stmt.names:
            if attr_name == "*":
                for export_name, export_info in exports.items():
                    if export_name.startswith("_"):
                        continue
                    self._bind_native_cross_module_export(
                        local_name=export_name,
                        src_module=src_module,
                        attr_name=export_name,
                        info=export_info,
                    )
                continue
            local_name = as_name or attr_name
            info = exports.get(attr_name)
            if info is None:
                full_submodule = self._native_import_from_submodule(
                    src_module,
                    attr_name,
                )
                if full_submodule is not None:
                    self._register_native_module_alias(
                        local_name,
                        full_submodule,
                    )
                    continue
                # Name isn't a top-level FuncDef/ClassDef export of
                # the native sibling — could be a module-alias
                # (``from . import foo as f``), a top-level constant,
                # or something the pre-pass doesn't model yet. Route
                # through CPython import so the binding still exists.
                self._import_from_cpython_single(
                    stmt,
                    src_module,
                    attr_name,
                    as_name,
                )
                continue
            if self._bind_native_cross_module_export(
                local_name=local_name,
                src_module=src_module,
                attr_name=attr_name,
                info=info,
            ):
                continue
            # Other kinds fall through to CPython so the program still links.
            self._import_from_cpython_single(
                stmt,
                src_module,
                attr_name,
                as_name,
            )

    def _register_native_module_constant(
        self,
        local_name: str,
        info: dict,
    ) -> None:
        """Bind ``from native_mod import CONST`` without materialising a
        CPython module object."""
        if not hasattr(self, "_native_module_constant_bindings"):
            self._native_module_constant_bindings = {}
        self._native_module_constant_bindings[local_name] = info

    def _emit_native_module_global_attr_load(
        self,
        module_name: str,
        attr_name: str,
        info: dict,
        span,
    ) -> ir.Value:
        """Load a sibling module's top-level variable via its extern global.

        Used by Attr lowering when ``mod.attr`` resolves to an export with
        ``kind == "module_global"`` (e.g. ``mod.__all__`` where the sibling
        defined ``__all__ = [...]``).  The ``.modvar.<mod>.<attr>`` extern
        is the same one the defining module's ``_pcc_py_module_top_<mod>()``
        populates at init time.  Without this helper the Attr lowering
        falls through to a generic ``py_obj_getattr`` on the module-name
        string and fails with ``AttributeError`` — see
        ``docs/investigations/python-native-module-alias-module-global-attr-attribute-error.md``.
        """
        value_ty = decode_type(info.get("value_ty", ("dyn",))) or DynType(name="dyn")
        if self._is_object(value_ty):
            ir_ty = _CSTR
        else:
            ir_ty = self._storage_ir_type(value_ty)
        sym = self._module_global_symbol_name(module_name, attr_name)
        existing = self.module.globals.get(sym)
        if existing is None:
            gv = ir.GlobalVariable(self.module, ir_ty, name=sym)
            gv.linkage = "external"
        else:
            gv = existing
        return self.builder.load(
            gv,
            name=self._fresh(f"modvar.{attr_name}"),
        )

    def _declare_native_module_extern_global(
        self,
        local_name: str,
        owning_module: str,
        attr_name: str,
        info: dict,
    ) -> None:
        value_ty = decode_type(info.get("value_ty", ("dyn",))) or DynType(name="dyn")
        if self._is_object(value_ty):
            ir_ty = _CSTR
        else:
            ir_ty = self._storage_ir_type(value_ty)
        sym = self._module_global_symbol_name(owning_module, attr_name)
        existing = self.module.globals.get(sym)
        if existing is None:
            gv = ir.GlobalVariable(self.module, ir_ty, name=sym)
            gv.linkage = "external"
        else:
            gv = existing
        self._module_globals[local_name] = (gv, value_ty)

    def _register_native_module_alias(
        self,
        local_name: str,
        module_name: str,
    ) -> None:
        """Track ``from .pkg import submod as name`` bindings for the
        limited native-submodule path.

        This is intentionally narrow: it only exists so later
        ``name.fn(...)`` calls can resolve directly against a sibling
        submodule's exported functions without routing through
        ``py_cpy_import``."""
        self._native_module_aliases[local_name] = module_name

    def _register_native_module_object_alias(
        self,
        local_name: str,
        module_name: str,
    ) -> None:
        """Track ``x = importlib.import_module("native_sibling")``.

        Unlike ``_native_module_aliases``, this binding represents a value
        produced by the dynamic import API, not a source-level ``import mod``
        alias.  Keep it separate so value-position reads of ``x`` do not get
        classified as CPython-backed module objects.
        """
        self._native_module_object_aliases[local_name] = module_name

    def _clear_native_module_object_alias(self, local_name: str) -> None:
        aliases = self._native_module_object_aliases
        if aliases is not None:
            aliases.pop(local_name, None)

    def _native_module_object_for_name(self, local_name: str) -> Optional[str]:
        return self._native_module_object_aliases.get(local_name)

    def _native_module_attr_global(
        self,
        module_name: str,
        attr_name: str,
    ) -> ir.GlobalVariable:
        slots = getattr(self, "_native_module_attr_globals", None)
        if slots is None:
            self._native_module_attr_globals = {}
            slots = self._native_module_attr_globals
        key = (module_name, attr_name)
        existing = slots.get(key)
        if existing is not None:
            return existing
        mod_suffix = self._module_symbol_suffix(module_name)
        attr_suffix = attr_name.replace(".", "_").replace("-", "_")
        gv = ir.GlobalVariable(
            self.module,
            _CSTR,
            name=f".modattr.{mod_suffix}.{attr_suffix}",
        )
        gv.linkage = "internal"
        gv.initializer = ir.Constant(_CSTR, None)
        slots[key] = gv
        return gv

    def _native_module_attr_global_if_exists(
        self,
        module_name: str,
        attr_name: str,
    ) -> Optional[ir.GlobalVariable]:
        slots = getattr(self, "_native_module_attr_globals", None)
        if slots is None:
            return None
        return slots.get((module_name, attr_name))

    def _emit_native_module_attr_load(
        self,
        module_name: str,
        attr_name: str,
        span: Optional[SourceSpan],
    ) -> Optional[ir.Value]:
        gv = self._native_module_attr_global_if_exists(module_name, attr_name)
        if gv is None:
            return None
        value = self.builder.load(
            gv,
            name=self._fresh(f"modattr.{module_name}.{attr_name}"),
        )
        missing = self.builder.icmp_signed(
            "==",
            value,
            ir.Constant(value.type, None),
            name=self._fresh(f"modattr.{attr_name}.missing"),
        )
        err_bb = self.current_function.append_basic_block(
            name=self._fresh(f"modattr.{attr_name}.err"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh(f"modattr.{attr_name}.ok"),
        )
        self.builder.cbranch(missing, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        self._emit_builtin_exception_and_branch(
            "AttributeError",
            f"module {module_name!r} has no attribute {attr_name!r}",
            span,
        )
        self.builder.position_at_end(ok_bb)
        return value

    def _emit_native_module_constant_or_override(
        self,
        module_name: str,
        attr_name: str,
        info: dict,
    ) -> ir.Value:
        gv = self._native_module_attr_global_if_exists(module_name, attr_name)
        if gv is None:
            return self._emit_native_module_constant(info)
        default_value = self._emit_native_module_constant(info)
        return self.builder.call(
            self.runtime["py_module_attr_value_or_default"],
            [gv, default_value],
        )

    def _native_module_alias_export_info(
        self,
        alias_name: str,
        attr_name: str,
    ) -> Optional[tuple[str, dict]]:
        """Return ``(module_name, export_info)`` for ``alias.attr`` when
        ``alias`` names a native sibling submodule or a literal dynamic
        import of one."""
        module_name = self._native_module_aliases.get(alias_name)
        if module_name is None:
            module_name = self._native_module_object_for_name(alias_name)
        if module_name is None:
            return None
        native_table = self._native_module_exports
        if native_table is None:
            return None
        info = native_table.get(module_name, {}).get(attr_name)
        if info is None:
            return None
        return module_name, info

    def _native_module_expr_export_info(
        self,
        module_expr: Expr,
        attr_name: str,
    ) -> Optional[tuple[str, dict]]:
        """Return native export info for ``module_expr.attr``.

        Supports both the existing one-level shape
        ``import string; string.ascii_lowercase`` and the package shape
        ``import urllib.parse; urllib.parse.quote``. The public import
        spelling stays CPython-compatible; this only changes how pcc's
        native module alias table is queried.
        """
        if isinstance(module_expr, Name):
            return self._native_module_alias_export_info(
                module_expr.ident,
                attr_name,
            )
        if isinstance(module_expr, Attr) and isinstance(module_expr.obj, Name):
            base = module_expr.obj.ident
            child = module_expr.name
            module_name = self._native_module_aliases.get(base)
            if module_name is None:
                return None
            native_table = self._native_module_exports
            if native_table is None:
                return None
            if module_name == f"{base}.{child}":
                info = native_table.get(module_name, {}).get(attr_name)
                if info is not None:
                    return module_name, info
            full_name = f"{module_name}.{child}"
            info = native_table.get(full_name, {}).get(attr_name)
            if info is not None:
                return full_name, info
            return None
        # Deeper dotted package access: ``import a.b.c; a.b.c.X`` where the
        # module expression is a 3+-level Attr chain (``a.b.c``) whose ``.obj``
        # is itself an Attr, not a Name — so the one-level branch above does not
        # match.  Flatten the pure Name/Attr chain to its spelled dotted name
        # and look it up directly in the native export table, gated on the root
        # being a known native module alias (so a real object attribute chain
        # ``obj.x.y.z`` is left to ``py_obj_getattr``).  Without this,
        # ``a.b.c.X`` fell through to the runtime getattr chain and raised
        # ``AttributeError`` on the intermediate ``a.b``.  See investigation
        # docs/investigations/python-deep-dotted-package-attr-no-libpython.md
        parts = self._dotted_module_parts(module_expr)
        if parts is not None and len(parts) >= 2:
            if parts[0] in self._native_module_aliases:
                native_table = self._native_module_exports
                if native_table is not None:
                    spelled = ".".join(parts)
                    info = native_table.get(spelled, {}).get(attr_name)
                    if info is not None:
                        return spelled, info
        return None

    def _dotted_module_parts(self, expr: Expr) -> Optional[list]:
        """Flatten a pure ``a.b.c`` Name/Attr chain to ``["a", "b", "c"]``.

        Returns None if ``expr`` is not a chain of ``Attr`` nodes rooted at a
        ``Name`` (e.g. it contains a call or subscript), so non-module attribute
        chains are not mistaken for dotted module access.
        """
        parts: list = []
        cur = expr
        while isinstance(cur, Attr):
            parts.append(cur.name)
            cur = cur.obj
        if isinstance(cur, Name):
            parts.append(cur.ident)
            parts.reverse()
            return parts
        return None

    def _native_importlib_literal_module(self, expr: Expr) -> Optional[str]:
        """Return the native module named by a literal dynamic import call."""
        if not isinstance(expr, Call) or expr.kwargs or len(expr.args) != 1:
            return None
        if isinstance(expr.func, Name) and expr.func.ident == "__import__":
            arg = expr.args[0]
            if isinstance(arg, StrLit) and self._is_native_dynamic_module(arg.value):
                return arg.value
            return None
        if not isinstance(expr.func, Attr) or not isinstance(expr.func.obj, Name):
            return None
        if self._native_builtin_module_for_name(expr.func.obj.ident) != "importlib":
            return None
        if expr.func.name == "import_module":
            arg = expr.args[0]
            if isinstance(arg, StrLit) and self._is_native_dynamic_module(arg.value):
                return arg.value
            return None
        if expr.func.name == "reload":
            return self._native_module_name_for_object_expr(expr.args[0])
        return None

    def _native_module_name_for_object_expr(self, expr: Expr) -> Optional[str]:
        if isinstance(expr, Name):
            module_name = self._native_module_object_for_name(expr.ident)
            if module_name is not None:
                return module_name
            module_name = self._native_module_aliases.get(expr.ident)
            if module_name is not None:
                return module_name
            return self._native_builtin_module_for_name(expr.ident)
        if isinstance(expr, Call):
            return self._native_importlib_literal_module(expr)
        if (
            isinstance(expr, Subscript)
            and isinstance(expr.obj, Attr)
            and expr.obj.name == "modules"
            and isinstance(expr.obj.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.obj.ident) == "sys"
            and isinstance(expr.idx, StrLit)
            and self._is_native_dynamic_module(expr.idx.value)
        ):
            return expr.idx.value
        return None

    def _native_module_object_export_info(
        self,
        module_expr: Expr,
        attr_name: str,
    ) -> Optional[tuple[str, dict]]:
        export = self._native_module_expr_export_info(module_expr, attr_name)
        if export is not None:
            return export
        module_name = self._native_module_name_for_object_expr(module_expr)
        if module_name is None:
            return None
        native_table = self._native_module_exports or {}
        info = native_table.get(module_name, {}).get(attr_name)
        if info is None:
            return None
        return module_name, info

    def _emit_native_module_export_value(
        self,
        module_name: str,
        attr_name: str,
        info: dict,
    ) -> ir.Value:
        kind = info.get("kind")
        if kind == "constant":
            return self._emit_native_module_constant_or_override(
                module_name,
                attr_name,
                info,
            )
        if kind == "module_global":
            owning_module = info.get("owning_module", module_name)
            export_name = info.get("export_name", attr_name)
            value_ty = decode_type(info.get("value_ty", ("dyn",))) or DynType(
                name="dyn"
            )
            if self._is_object(value_ty):
                ir_ty = _CSTR
            else:
                ir_ty = self._storage_ir_type(value_ty)
            sym = self._module_global_symbol_name(owning_module, export_name)
            existing = self.module.globals.get(sym)
            if existing is None:
                gv = ir.GlobalVariable(self.module, ir_ty, name=sym)
                gv.linkage = "external"
            else:
                gv = existing
            return self.builder.load(gv, name=self._fresh(f"modvar.{attr_name}"))
        if kind == "function":
            return self._declare_extern_user_function(
                module_name,
                attr_name,
                info,
            )
        if kind == "class" and hasattr(self, "class_lowering"):
            owning_module = info.get("owning_module", module_name)
            export_name = info.get("export_name", attr_name)
            class_info = self.class_lowering.declare_extern_class(
                owning_module=owning_module,
                class_name=info["class_name"],
                field_names=info["field_names"],
                methods=info["methods"],
                local_name=export_name,
            )
            if class_info is not None:
                return self.builder.load(
                    class_info.global_var,
                    name=self._fresh(f"cls.{attr_name}"),
                )
        raise NotImplementedError(
            f"native module export {module_name}.{attr_name} kind "
            f"{kind!r} is not value-lowerable"
        )

    def _maybe_emit_native_module_hasattr(self, expr: Call) -> Optional[ir.Value]:
        if len(expr.args) != 2 or expr.kwargs:
            return None
        if not isinstance(expr.args[1], StrLit):
            return None
        module_name = self._native_module_name_for_object_expr(expr.args[0])
        if module_name is None:
            return None
        native_table = self._native_module_exports or {}
        attr_name = expr.args[1].value
        present = attr_name in native_table.get(module_name, {})
        if not present:
            present = self._native_builtin_module_has_attr(module_name, attr_name)
        if present:
            return ir.Constant(_I1, 1)
        # Attribute is not in pcc's static native table.  Returning a
        # compile-time False here was the historic behavior, but that made
        # ``hasattr(os, "__all__")``, ``hasattr(os, "path")``,
        # ``hasattr(os, "__name__")`` and similar evaluate to False at
        # compile time even though the runtime can answer correctly
        # (either via the libpython fallback for cpython-backed modules
        # under ``--python-libpython=auto``, or via ``py_obj_getattr`` on
        # the underlying pcc-native module object).  Fall through
        # (return None) so the next lowering branch in
        # ``call_expression_lowering.py::_emit_hasattr`` can emit a runtime
        # hasattr: ``py_cpy_call(builtins.hasattr)`` when the object also
        # looks like a cpython value, or ``py_obj_getattr`` non-NULL
        # otherwise.  Surfaced by numpy/__init__.py:681 via
        # ``_core.__all__`` and minimally as
        # ``hasattr(os, "__all__") → False`` under
        # ``--python-libpython=auto``.  See
        # ``docs/investigations/python-hasattr-static-false-on-builtin-modules.md``.
        return None

    def _maybe_emit_native_module_getattr(self, expr: Call) -> Optional[ir.Value]:
        if not (2 <= len(expr.args) <= 3) or expr.kwargs:
            return None
        if not isinstance(expr.args[1], StrLit):
            return None
        module_name = self._native_module_name_for_object_expr(expr.args[0])
        if module_name is None:
            if self._expr_looks_cpython(expr.args[0]):
                if len(expr.args) == 3:
                    return self._emit_as_object(expr.args[2])
                return None
            return None
        native_table = self._native_module_exports or {}
        info = native_table.get(module_name, {}).get(expr.args[1].value)
        if info is None:
            builtin_attr = self._emit_native_builtin_module_attr(
                module_name,
                expr.args[1].value,
            )
            if builtin_attr is not None:
                return builtin_attr
            if len(expr.args) == 3:
                return self._emit_as_object(expr.args[2])
            return None
        return self._emit_native_module_export_value(
            module_name,
            expr.args[1].value,
            info,
        )

    def _maybe_emit_native_module_type_name(self, expr: Attr) -> Optional[ir.Value]:
        if (
            expr.name != "__name__"
            or not isinstance(expr.obj, Call)
            or not isinstance(expr.obj.func, Name)
            or expr.obj.func.ident != "type"
            or len(expr.obj.args) != 1
        ):
            return None
        if self._native_module_name_for_object_expr(expr.obj.args[0]) is None:
            return None
        return self._emit_str_literal("module")

    def _inspect_module_call_name(self, expr: Call) -> Optional[str]:
        if not isinstance(expr.func, Attr) or expr.kwargs:
            return None
        attr = expr.func
        if not isinstance(attr.obj, Name):
            return None
        if (
            self._native_module_aliases.get(attr.obj.ident) != "inspect"
            and self._native_builtin_module_for_name(attr.obj.ident) != "inspect"
        ):
            return None
        return attr.name

    def _inspect_funcdef_for_expr(self, expr: Expr) -> Optional[FuncDef]:
        if not isinstance(expr, Name):
            return None
        try:
            return self._find_user_funcdef(expr.ident)
        except Exception:
            return None

    def _inspect_default_repr(self, expr: Optional[Expr]) -> Optional[str]:
        if expr is None:
            return None
        if isinstance(expr, IntLit):
            return str(expr.value)
        if isinstance(expr, StrLit):
            return repr(expr.value)
        if isinstance(expr, NoneLit):
            return "None"
        return "..."

    def _inspect_signature_metadata_for_funcdef(self, fd: FuncDef) -> dict:
        display_parts: list[str] = []
        params: list[str] = []
        varargs = None
        varkw = None
        for arg in fd.args:
            if not arg.name:
                continue
            if arg.kind == "**kwargs":
                varkw = arg.name
                display_parts.append("**" + arg.name)
                continue
            if arg.kind == "*args":
                varargs = arg.name
                display_parts.append("*" + arg.name)
                continue
            params.append(arg.name)
            part = arg.name
            default_repr = self._inspect_default_repr(arg.default)
            if default_repr is not None:
                part += "=" + default_repr
            display_parts.append(part)
        return {
            "params": params,
            "varargs": varargs,
            "varkw": varkw,
            "signature": "(" + ", ".join(display_parts) + ")",
        }

    def _inspect_signature_metadata_for_call(self, expr: Expr) -> Optional[dict]:
        if not isinstance(expr, Call):
            return None
        if self._inspect_module_call_name(expr) != "signature":
            return None
        if len(expr.args) != 1:
            return None
        fd = self._inspect_funcdef_for_expr(expr.args[0])
        if fd is None:
            return None
        return self._inspect_signature_metadata_for_funcdef(fd)

    def _inspect_fullargspec_metadata_for_call(self, expr: Expr) -> Optional[dict]:
        if not isinstance(expr, Call):
            return None
        if self._inspect_module_call_name(expr) != "getfullargspec":
            return None
        if len(expr.args) != 1:
            return None
        fd = self._inspect_funcdef_for_expr(expr.args[0])
        if fd is None:
            return None
        return self._inspect_signature_metadata_for_funcdef(fd)

    def _emit_string_list(self, values: list[str], name: str) -> ir.Value:
        out = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh(name),
        )
        for value in values:
            item = self._emit_str_literal(value)
            self.builder.call(self.runtime["py_list_append"], [out, item])
        return out

    def _emit_optional_string(self, value: Optional[str]) -> ir.Value:
        if value is None:
            return self._emit_none_literal()
        return self._emit_str_literal(value)

    def _maybe_emit_inspect_alias_attr(self, expr: Attr) -> Optional[ir.Value]:
        if not isinstance(expr.obj, Name):
            return None
        signature = getattr(self, "_inspect_signature_aliases", {}).get(expr.obj.ident)
        if signature is not None and expr.name == "parameters":
            return self._emit_string_list(
                list(signature.get("params", ())),
                "inspect.signature.parameters",
            )
        fullargspec = getattr(self, "_inspect_fullargspec_aliases", {}).get(
            expr.obj.ident
        )
        if fullargspec is not None:
            if expr.name == "args":
                return self._emit_string_list(
                    list(fullargspec.get("params", ())),
                    "inspect.fullargspec.args",
                )
            if expr.name == "varargs":
                return self._emit_optional_string(fullargspec.get("varargs"))
            if expr.name == "varkw":
                return self._emit_optional_string(fullargspec.get("varkw"))
        return None

    def _emit_inspect_getsource(self, expr: Call) -> Optional[ir.Value]:
        if len(expr.args) != 1:
            return None
        target = expr.args[0]
        fd = self._inspect_funcdef_for_expr(target)
        if fd is None:
            return None
        src_path = fd.span.file or target.span.file
        if not src_path:
            return self._emit_str_literal("")
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                return self._emit_str_literal(f.read())
        except OSError:
            return self._emit_str_literal("")

    def _emit_inspect_getmro(self, expr: Call) -> Optional[ir.Value]:
        if len(expr.args) != 1:
            return None
        target = expr.args[0]
        if not isinstance(target, Name):
            return None
        info = self.class_lowering.classes.get(target.ident)
        if info is None:
            return None
        cls_obj = self.builder.load(
            info.global_var,
            name=self._fresh(f"inspect.mro.{target.ident}"),
        )
        return self.builder.call(
            self.runtime["py_obj_getattr"],
            [cls_obj, self._attr_name_ptr("__mro__")],
            name=self._fresh("inspect.mro"),
        )

    def _maybe_emit_native_inspect_call(self, expr: Call) -> Optional[ir.Value]:
        if len(expr.args) != 1:
            return None
        attr_name = self._inspect_module_call_name(expr)
        if attr_name is None:
            return None
        if attr_name == "signature":
            metadata = self._inspect_signature_metadata_for_call(expr)
            if metadata is None:
                return None
            return self._emit_str_literal(str(metadata.get("signature", "()")))
        if attr_name == "getfullargspec":
            metadata = self._inspect_fullargspec_metadata_for_call(expr)
            if metadata is None:
                return None
            return self._emit_none_literal()
        if attr_name == "getsource":
            return self._emit_inspect_getsource(expr)
        if attr_name == "getmro":
            return self._emit_inspect_getmro(expr)
        if attr_name in ("isfunction", "isclass", "ismethod"):
            target = expr.args[0]
            if attr_name == "isfunction":
                is_func = self._inspect_funcdef_for_expr(target) is not None
                if not is_func and isinstance(target, Attr):
                    export = self._native_module_object_export_info(
                        target.obj,
                        target.name,
                    )
                    is_func = export is not None and export[1].get("kind") == "function"
                return self.builder.call(
                    self.runtime["py_bool_from_bit"],
                    [
                        ir.Constant(
                            _I32,
                            1 if is_func else 0,
                        )
                    ],
                    name=self._fresh("inspect.isfunction"),
                )
            if attr_name == "isclass":
                is_cls = (
                    isinstance(target, Name)
                    and target.ident in self.class_lowering.classes
                )
                return self.builder.call(
                    self.runtime["py_bool_from_bit"],
                    [ir.Constant(_I32, 1 if is_cls else 0)],
                    name=self._fresh("inspect.isclass"),
                )
            is_method = isinstance(target, Attr) and not isinstance(target.obj, Name)
            return self.builder.call(
                self.runtime["py_bool_from_bit"],
                [ir.Constant(_I32, 1 if is_method else 0)],
                name=self._fresh("inspect.ismethod"),
            )
        if attr_name not in ("getdoc", "isfunction"):
            return None
        target = expr.args[0]
        if not isinstance(target, Attr):
            return None
        export = self._native_module_object_export_info(target.obj, target.name)
        if export is None:
            return None
        _module_name, info = export
        is_func = info.get("kind") == "function"
        if attr_name == "isfunction":
            return ir.Constant(_I1, 1 if is_func else 0)
        if not is_func:
            return self._emit_none_literal()
        return self._emit_str_literal(str(info.get("docstring") or ""))

    def _emit_native_module_constant(self, info: dict) -> ir.Value:
        value_kind = info.get("value_kind", info.get("kind"))
        if value_kind == "str":
            return self._emit_str_literal(str(info.get("value", "")))
        if value_kind == "bytes":
            raw = info.get("value", b"")
            if isinstance(raw, str):
                raw = raw.encode("latin-1")
            return self._emit_bytes_literal(bytes(raw))
        if value_kind == "int":
            return self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, int(info.get("value", 0)))],
                name=self._fresh("native.const.int"),
            )
        if value_kind == "bool":
            return self.builder.call(
                self.runtime["py_bool_from_bit"],
                [ir.Constant(_I32, 1 if info.get("value") else 0)],
                name=self._fresh("native.const.bool"),
            )
        if value_kind == "float":
            value = info.get("value", 0.0)
            return self.builder.call(
                self.runtime["py_float_from_f64"],
                [ir.Constant(_DOUBLE, float(value))],
                name=self._fresh("native.const.float"),
            )
        if value_kind == "none":
            return self._emit_none_literal()
        raise NotImplementedError(
            f"unsupported native module constant kind {value_kind!r}"
        )

    def _ensure_native_module_alias_class_export(
        self,
        alias_name: str,
        attr_name: str,
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
        owning_module = info.get("owning_module", module_name)
        export_name = info.get("export_name", attr_name)
        # First, try to find a class that exactly matches the expected global name.
        expected_global = (
            ".class."
            + owning_module.replace(".", "_").replace("-", "_")
            + "."
            + export_name
        )

        # We need to find if this class is already registered under ANY key.
        class_info = None
        for registered in self.class_lowering.classes.values():
            if registered.global_var.name == expected_global:
                class_info = registered
                break

        if class_info is not None:
            return class_info

        # Not found: declare it. The registry will now use a qualified key internally.
        class_info = self._declare_native_module_extern_class(
            owning_module=owning_module,
            class_name=attr_name,
            field_names=info["field_names"],
            methods=info["methods"],
            local_name=attr_name,
            base_names=info.get("base_names", ()),
        )

        # Record the qualified name as the hint for this assignment target.
        if hasattr(self, "env_class_hint"):
            # The caller (_emit_attr) will use this return value to
            # generate an assignment. We need to tell the generator
            # to use the qualified name for the hint.
            pass

        return class_info

    def _declare_native_module_extern_class(
        self,
        *,
        owning_module: str,
        class_name: str,
        field_names: tuple,
        methods: tuple,
        local_name: str,
        base_names: tuple = (),
        _seen: tuple = (),
    ):
        if owning_module == "pcc.py_frontend.py_ast" and class_name not in _seen:
            module_exports = None
            if self._native_module_exports is not None:
                module_exports = self._native_module_exports.get(owning_module)
            if module_exports is not None:
                next_seen = _seen + (class_name,)
                for base_name in base_names:
                    base_info = module_exports.get(base_name)
                    if (
                        not isinstance(base_info, dict)
                        or base_info.get("kind") != "class"
                    ):
                        continue
                    self._declare_native_module_extern_class(
                        owning_module=owning_module,
                        class_name=base_info.get("class_name", base_name),
                        field_names=base_info.get("field_names", ()),
                        methods=base_info.get("methods", ()),
                        local_name=base_name,
                        base_names=base_info.get("base_names", ()),
                        _seen=next_seen,
                    )
        class_lowering = self.class_lowering
        return class_lowering.declare_extern_class(
            owning_module=owning_module,
            class_name=class_name,
            field_names=field_names,
            methods=methods,
            local_name=local_name,
        )

    def _native_class_method_def(
        self,
        class_info,
        method_name: str,
    ) -> Optional[FuncDef]:
        if class_info is not None:
            for candidate_name, candidate_def in class_info.method_defs:
                if candidate_name == method_name:
                    return candidate_def
        if class_info is not None and class_info.expanded_cd is not None:
            for s in class_info.expanded_cd.body:
                if getattr(s, "name", None) == method_name:
                    return s
        if class_info is not None and class_info.extern_method_defs:
            synth = class_info.extern_method_defs.get(method_name)
            if synth is not None:
                return synth
        if class_info is None:
            return None
        for stmt in self.ast_module.body:
            if (
                getattr(stmt, "body", None) is not None
                and getattr(stmt, "name", None) == class_info.name
            ):
                for s in stmt.body:
                    if getattr(s, "name", None) == method_name:
                        return s
        return None

    def _emit_native_class_instantiate(
        self,
        class_name: str,
        args: tuple,
    ) -> ir.Value:
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write(
                "debug: native_class_instantiate enter class_name="
                + str(class_name)
                + " args_len="
                + str(len(args))
                + "\n"
            )
        class_info = self.class_lowering.classes.get(class_name)
        if class_info is None:
            raise NotImplementedError(
                f"instantiation: class {class_name!r} not found in module"
            )
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            try:
                gv = class_info.global_var
                sys.stderr.write(
                    "debug: native_class_instantiate info name="
                    + str(class_info.name)
                    + " fields="
                    + ",".join(class_info.field_names)
                    + " gv_type="
                    + type(gv).__name__
                    + " gv_name="
                    + str(getattr(gv, "name", "<missing>"))
                    + "\n"
                )
            except Exception:
                sys.stderr.write("debug: native_class_instantiate info_dump_failed=1\n")
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate before_load\n")
        cls_ptr = self.builder.load(
            class_info.global_var, name=self._fresh(f".cls.{class_name}")
        )
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate after_load\n")
        inst = self.builder.call(
            self.runtime["py_instance_new"],
            [cls_ptr],
            name=self._fresh(f"inst.{class_name}"),
        )
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate after_new\n")
        init_fn = class_info.init_fn
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate before_methods\n")
        if init_fn is None:
            init_fn = class_info.methods.get("__init__")
        init_info = class_info
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate before_bases\n")
        if init_fn is None:
            visited: set[str] = set()
            queue: list[str] = []
            for base_expr in init_info.bases_ast:
                if isinstance(base_expr, Name) and base_expr.ident != "object":
                    queue.append(base_expr.ident)
            while queue and init_fn is None:
                base_name = queue.pop(0)
                if base_name in visited:
                    continue
                visited.add(base_name)
                base_info = self.class_lowering.classes.get(base_name)
                if base_info is None:
                    continue
                candidate = base_info.init_fn
                if candidate is None:
                    candidate = base_info.methods.get("__init__")
                if candidate is not None:
                    init_info = base_info
                    init_fn = candidate
                    break
                for parent_expr in base_info.bases_ast:
                    if isinstance(parent_expr, Name) and parent_expr.ident != "object":
                        queue.append(parent_expr.ident)
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate before_method_def\n")
        init_ast_fd = self._native_class_method_def(init_info, "__init__")
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write("debug: native_class_instantiate after_method_def\n")
        should_call_init = init_fn is not None
        if not should_call_init and init_ast_fd is not None:
            should_call_init = True
        if not should_call_init and len(args) > 0:
            should_call_init = True
        if should_call_init:
            if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                import sys

                sys.stderr.write(
                    "debug: native_class_instantiate call_init class_name="
                    + str(class_name)
                    + " init_info="
                    + str(init_info.name)
                    + " init_ast="
                    + str(init_ast_fd is not None)
                    + "\n"
                )
            init_args: list[ir.Value] = [inst]
            declared = [
                a for a in (init_ast_fd.args[1:] if init_ast_fd else ()) if a.name != ""
            ]
            for i, arg_expr in enumerate(args):
                v = self._emit_expr(arg_expr)
                if i < len(declared) and declared[i].annotation is not None:
                    v = self._coerce(v, arg_expr.ty, declared[i].annotation)
                else:
                    v = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        v,
                        arg_expr.ty,
                    )
                init_args.append(v)

            for j in range(len(args), len(declared)):
                arg = declared[j]
                arg_kind = getattr(arg, "kind", "pos")
                if arg_kind == "*args":
                    # Unfilled ``*args``: pass an empty tuple so the
                    # callee's iteration over the vararg is well-formed
                    # (``for part in extra:`` immediately terminates).
                    empty_tuple = self.builder.call(
                        self.runtime["py_tuple_new"],
                        [ir.Constant(ir.IntType(64), 0)],
                        name=self._fresh(f"{class_name}.init.extras"),
                    )
                    init_args.append(empty_tuple)
                    continue
                if arg_kind == "**kwargs":
                    # Unfilled ``**kwargs``: pass an empty dict so the
                    # callee's lookup over the varkwarg is well-formed.
                    empty_dict = self.builder.call(
                        self.runtime["py_dict_new"],
                        [],
                        name=self._fresh(f"{class_name}.init.kwextras"),
                    )
                    init_args.append(empty_dict)
                    continue
                if not getattr(arg, "has_default", False):
                    raise NotImplementedError(
                        f"instantiation: {class_name}.__init__ missing "
                        f"argument {arg.name!r} and has no default"
                    )
                v = self._emit_expr(arg.default)
                if arg.annotation is not None:
                    v = self._coerce(v, arg.default.ty, arg.annotation)
                else:
                    v = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        v,
                        arg.default.ty,
                    )
                init_args.append(v)
            if init_fn is None:
                # Same fix as class_gen.py:5566 — no __init__ found in the
                # class or any pcc-known base via MRO; synthesising a phantom
                # @user_<current_module>_<class>___init__ produces an
                # undefined-symbol link error. Skip the call. See
                # docs/investigations/python-class-init-phantom-symbol-link-fail.md.
                pass
            else:
                self.builder.call(init_fn, init_args)
        return inst

    def _emit_no_init_field_instance(
        self,
        class_name: str,
        args: tuple,
        kwargs: tuple,
        *,
        force: bool = False,
    ) -> ir.Value | None:
        info = self.class_lowering.classes.get(class_name)
        if info is None:
            return None
        if info.init_fn is not None and not force:
            return None
        field_names = tuple(info.field_names)
        if not field_names:
            return None
        if len(args) > len(field_names):
            raise NotImplementedError(
                f"class {class_name!r} has no __init__ for extra "
                "positional arguments"
            )

        cls_ptr = self.builder.load(
            info.global_var, name=self._fresh(f".cls.{class_name}.field")
        )
        inst = self.builder.call(
            self.runtime["py_instance_new"],
            [cls_ptr],
            name=self._fresh(f"inst.{class_name}.field"),
        )
        seen = set()

        for i, arg_expr in enumerate(args):
            field_name = field_names[i]
            seen.add(field_name)
            if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                import sys

                sys.stderr.write(
                    "debug: native_field_init arg index="
                    + str(i)
                    + " field="
                    + field_name
                    + " expr_type="
                    + type(arg_expr).__name__
                    + " ty_name="
                    + str(getattr(getattr(arg_expr, "ty", None), "name", ""))
                    + "\n"
                )
            raw_v = self._emit_expr(arg_expr)
            if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                import sys

                try:
                    sys.stderr.write(
                        "debug: native_field_init raw index="
                        + str(i)
                        + " raw_type="
                        + type(raw_v).__name__
                        + " ir_type="
                        + str(getattr(raw_v, "type", "<missing>"))
                        + "\n"
                    )
                except Exception:
                    sys.stderr.write("debug: native_field_init raw_dump_failed=1\n")
            v_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                raw_v,
                arg_expr.ty,
            )
            if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                import sys

                try:
                    setter = self.runtime["py_obj_setattr"]
                    sys.stderr.write(
                        "debug: native_field_init boxed index="
                        + str(i)
                        + " boxed_type="
                        + type(v_obj).__name__
                        + " boxed_ir_type="
                        + str(getattr(v_obj, "type", "<missing>"))
                        + " setter_type="
                        + type(setter).__name__
                        + " setter_ir_type="
                        + str(getattr(setter, "type", "<missing>"))
                        + "\n"
                    )
                except Exception:
                    sys.stderr.write("debug: native_field_init boxed_dump_failed=1\n")
            self.builder.call(
                self.runtime["py_obj_setattr"],
                [inst, self._attr_name_ptr(field_name), v_obj],
            )
            if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                import sys

                sys.stderr.write(
                    "debug: native_field_init set_done index="
                    + str(i)
                    + " field="
                    + field_name
                    + "\n"
                )

        for kw_name, kw_expr in kwargs:
            if kw_name in seen:
                raise NotImplementedError(
                    f"class {class_name!r} got multiple values for "
                    f"field {kw_name!r}"
                )
            seen.add(kw_name)
            raw_v = self._emit_expr(kw_expr)
            v_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                raw_v,
                kw_expr.ty,
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
        owning_module = info.get("owning_module", owning_module)
        func_name = info.get("export_name", func_name)
        sanitised = owning_module.replace(".", "_").replace("-", "_")
        sym = f"user_{sanitised}_{func_name}"
        existing = self.module.globals.get(sym)
        if isinstance(existing, ir.Function):
            fn = existing
        else:
            box_int_abi = self._export_box_int_abi(info)
            param_tys = [
                self._abi_ir_type(
                    decode_type(t),
                    box_int_abi=box_int_abi,
                )
                for t in info["param_types"]
            ]
            ret_ty = decode_type(info["return_ty"]) or DynType(name="dyn")
            fnty = ir.FunctionType(
                self._abi_ir_type(ret_ty, box_int_abi=box_int_abi),
                param_tys,
            )
            fn = ir.Function(self.module, fnty, name=sym)
            fn.linkage = "external"
        if bind_name is not None:
            self.functions[bind_name] = fn
            self._cross_module_func_defs[bind_name] = self._extern_info_to_funcdef(
                bind_name, info
            )
        return fn

    def _maybe_emit_native_module_alias_call(self, expr: Call) -> Optional[ir.Value]:
        """Lower ``submod.fn(...)`` when ``submod`` was imported from a
        native sibling module and ``fn`` is one of that submodule's
        exported top-level functions."""
        attr = expr.func
        if not isinstance(attr, Attr):
            return None
        export = self._native_module_expr_export_info(attr.obj, attr.name)
        if export is None:
            return None
        module_name, info = export
        kind = info.get("kind")
        if kind == "function":
            fn = self._declare_extern_user_function(
                module_name,
                attr.name,
                info,
            )
            ast_func_def = self._extern_info_to_funcdef(attr.name, info)
            if ast_func_def is None:
                return None
            if self._call_would_use_callee_defaults(
                expr.args,
                expr.kwargs,
                ast_func_def.args,
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
        if not isinstance(attr.obj, Name):
            return None
        class_info = self._ensure_native_module_alias_class_export(
            attr.obj.ident,
            attr.name,
        )
        if class_info is None:
            return None
        if str(os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            import sys

            sys.stderr.write(
                "debug: native_module_alias_class_call module="
                + module_name
                + " attr="
                + attr.name
                + " class_info="
                + str(class_info.name)
                + " args_len="
                + str(len(expr.args))
                + "\n"
            )
        if module_name == "pcc.py_frontend.py_ast":
            inst = self._emit_no_init_field_instance(
                class_info.name,
                expr.args,
                expr.kwargs,
                force=True,
            )
            if inst is not None:
                return inst
        init_fd = self._native_class_method_def(class_info, "__init__")
        resolved_args = expr.args
        if init_fd is None:
            if expr.kwargs:
                raise NotImplementedError(
                    f"class {attr.name!r} with kwargs needs __init__ "
                    "to resolve parameter names"
                )
            inst = self._emit_no_init_field_instance(
                class_info.name,
                expr.args,
                expr.kwargs,
            )
            if inst is not None:
                return inst
            return self._emit_native_class_instantiate(
                class_info.name,
                expr.args,
            )
        elif expr.kwargs:
            resolved_args = tuple(
                self._resolve_call_kwargs(
                    expr.args,
                    expr.kwargs,
                    init_fd.args,
                    skip_self=True,
                )
            )
        return self._emit_native_class_instantiate(
            class_info.name,
            resolved_args,
        )


__all__ = ["NativeModuleAliasMixin"]
