"""User-function declaration helpers for Layer-1 Python codegen."""
from __future__ import annotations

import os
import sys

from pcc.llvm_capi.compat import ir

from ..py_ast import FuncDef, IntType, Name, NoneType


_I8 = ir.IntType(8)
_CSTR = _I8.as_pointer()
_VOID = ir.VoidType()


class UserFunctionDeclLoweringMixin:
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
        try:
            decorators = fd.decorators
        except AttributeError:
            return ()
        if not isinstance(decorators, tuple):
            return ()
        return decorators

    def _func_c_abi_export_symbol(self, fd: FuncDef) -> str | None:
        decorators = self._func_decorators(fd)
        i = 0
        while i < len(decorators):
            sym = self._decorator_c_abi_export_symbol(decorators[i])
            if sym is not None:
                return sym
            i += 1
        return None

    def _declare_user_function(self, fd: FuncDef) -> None:
        debug_bootstrap = str(
            os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE", "") or ""
        ).strip().lower() in ("1", "true", "yes", "on")
        if debug_bootstrap:
            sys.stderr.write(
                "debug: declare_user_function begin name="
                + str(getattr(fd, "name", ""))
                + "\n"
            )
        c_abi_sym: str | None = self._func_c_abi_export_symbol(fd)
        decorators = self._func_decorators(fd)
        if decorators:
            unrecognised = []
            i = 0
            while i < len(decorators):
                d = decorators[i]
                sym = self._decorator_c_abi_export_symbol(d)
                if sym is not None:
                    i += 1
                    continue
                if not self._decorator_is_noop_whitelist(d):
                    if not (isinstance(d, Name) and d.ident in self.functions):
                        unrecognised.append(d)
                i += 1
            if unrecognised:
                raise NotImplementedError(
                    "Layer 1 does not handle decorators; received "
                    f"{len(decorators)} on {fd.name!r} "
                    f"(first unrecognised: "
                    f"{self._decorator_repr(unrecognised[0])})"
                )

        box_int_abi = self._funcdef_uses_boxed_int_abi(
            fd,
            c_abi_sym=c_abi_sym,
        )
        param_types: list[ir.Type] = []
        for arg_index, arg in enumerate(fd.args):
            if debug_bootstrap:
                sys.stderr.write(
                    "debug: declare_user_function arg index="
                    + str(arg_index)
                    + " name="
                    + str(getattr(arg, "name", ""))
                    + " type="
                    + type(arg).__name__
                    + "\n"
                )
            # Bare ``*`` separator: no name, no runtime slot — it only
            # marks subsequent params as keyword-only. Skip so the
            # function's IR signature matches ``_resolve_call_kwargs``
            # which already filters the marker from the arg-list side.
            if arg.name == "":
                continue
            ir_ty, _ = self._param_ir_and_bind_type(
                arg,
                require_annotation=True,
                owner_name=fd.name,
                box_int_params=box_int_abi,
            )
            param_types.append(ir_ty)

        is_generator = fd.name in getattr(
            self, "_generator_func_names", set()
        ) or self._funcdef_has_yield_sentinel(fd)
        if is_generator:
            if not hasattr(self, "_generator_func_names"):
                self._generator_func_names = set()
            self._generator_func_names.add(fd.name)
        if is_generator:
            ret_ty = _CSTR
        elif fd.is_async and (
            fd.return_ty is None or isinstance(fd.return_ty, NoneType)
        ):
            ret_ty = _CSTR
        elif fd.return_ty is None or isinstance(fd.return_ty, NoneType):
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
        # @c_abi_export modules are runtime-level code, not user
        # application code. Suppress post-call err checks inside the
        # exported functions and their same-module helpers: traceback
        # and exception helpers run while TLS intentionally holds the
        # pending exception, so checking py_err_occurred() after a
        # pure helper call would mistake that ambient exception for a
        # newly-raised helper failure.
        if c_abi_sym is not None or self._module_has_c_abi_export:
            self._c_abi_export_symbols.add(sym)
        runtime_args = [a for a in fd.args if a.name != ""]
        for ir_arg, ast_arg in zip(fn.args, runtime_args):
            ir_arg.name = ast_arg.name
        self.functions[fd.name] = fn
