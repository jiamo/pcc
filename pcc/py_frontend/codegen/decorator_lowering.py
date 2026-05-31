"""Decorator helper lowering for Layer-1 Python codegen."""
from __future__ import annotations

from ..py_ast import Attr, Call, Name, StrLit


_NOOP_DECORATOR_QUALIFIED = frozenset(
    {
        "click.command",
        "click.option",
        "click.argument",
        "click.pass_context",
        "click.group",
        "click.pass_obj",
        "functools.wraps",
        "functools.lru_cache",
        "functools.cache",
        "dataclasses.dataclass",
        "abc.abstractmethod",
        "contextlib.contextmanager",
        "pytest.fixture",
        "pytest.mark.parametrize",
        "pcc.test_runner.fixture",
        "pcc.test_runner.parametrize",
    }
)

_NOOP_DECORATOR_BARE = frozenset(
    {
        "wraps",
        "lru_cache",
        "cache",
        "dataclass",
        "abstractmethod",
        "contextmanager",
        "fixture",
        "parametrize",
        "_recursive_guard",
        "recursive_guard",
    }
)


class DecoratorLoweringMixin:
    def _decorator_root_name(self, dec) -> str | None:
        if isinstance(dec, Name):
            return dec.ident
        if isinstance(dec, Attr):
            try:
                cur = dec.obj
            except AttributeError:
                return None
            while isinstance(cur, Attr):
                try:
                    cur = cur.obj
                except AttributeError:
                    return None
            if isinstance(cur, Name):
                return cur.ident
            return None
        if isinstance(dec, Call):
            try:
                func = dec.func
            except AttributeError:
                return None
            return self._decorator_root_name(func)
        return None

    def _decorator_c_abi_export_symbol(self, dec) -> str | None:
        """If ``dec`` is ``@c_abi_export("sym")`` or
        ``@pcc.extern.c_abi_export("sym")``, return the literal symbol
        string. Else None."""
        if not isinstance(dec, Call):
            return None
        try:
            func = dec.func
            args = dec.args
        except AttributeError:
            return None
        qn = self._decorator_qualname(func)
        if qn not in ("c_abi_export", "pcc.extern.c_abi_export", "extern.c_abi_export"):
            return None
        if len(args) != 1:
            return None
        arg = args[0]
        if not isinstance(arg, StrLit):
            return None
        try:
            return arg.value
        except AttributeError:
            return None

    def _decorator_qualname(self, dec):
        """Return a dotted identifier for a decorator expression or
        ``None`` if the decorator isn't a simple Name / Attr chain."""
        # ``@foo`` — bare Name
        if isinstance(dec, Name):
            return dec.ident
        # ``@foo.bar`` or deeper
        if isinstance(dec, Attr):
            parts: list[str] = [dec.name]
            try:
                cur = dec.obj
            except AttributeError:
                return None
            while isinstance(cur, Attr):
                parts.append(cur.name)
                try:
                    cur = cur.obj
                except AttributeError:
                    return None
            if isinstance(cur, Name):
                parts.append(cur.ident)
                return self._join_reversed_strs(parts)
            return None
        # ``@foo(args)`` or ``@foo.bar(args)`` — Call wrapping a name chain
        if isinstance(dec, Call):
            try:
                func = dec.func
            except AttributeError:
                return None
            return self._decorator_qualname(func)
        return None

    def _decorator_is_noop_whitelist(self, dec) -> bool:
        qn = self._decorator_qualname(dec)
        if qn is None:
            return False
        if qn in _NOOP_DECORATOR_QUALIFIED:
            return True
        if qn == "errstate" or qn.endswith(".errstate"):
            return True
        if "." not in qn and qn in _NOOP_DECORATOR_BARE:
            return True
        if self._decorator_is_external_metadata_factory(dec):
            return True
        return False

    def _decorator_is_external_metadata_factory(self, dec) -> bool:
        """Treat imported decorator factories as compile-time metadata.

        A common package pattern is ``@imported_factory(...metadata...)`` where
        the wrapper only affects CPython dispatch/reflection. pcc cannot execute
        arbitrary import-time decorator factories during native declaration, but
        it can still compile the underlying function body. Keep this limited to
        call-shaped decorators whose root name came from another module; a
        same-module decorator remains semantic user code and is not ignored.
        """
        if not isinstance(dec, Call):
            return False
        root = self._decorator_root_name(dec.func)
        if root is None:
            return False
        if root in getattr(self, "_cross_module_func_defs", {}):
            return True
        if (
            root in getattr(self, "_module_globals", {})
            and root not in getattr(self, "functions", {})
        ):
            return True
        if root in getattr(self, "_cpy_module_env", {}):
            return True
        if root in getattr(self, "_native_module_aliases", {}):
            return True
        if root in getattr(self, "_native_builtin_module_aliases", {}):
            return True
        if root in getattr(self, "_native_builtin_value_aliases", {}):
            return True
        return False

    def _decorator_repr(self, dec) -> str:
        qn = self._decorator_qualname(dec)
        return qn if qn is not None else type(dec).__name__

    # -- For loops -----------------------------------------------------
