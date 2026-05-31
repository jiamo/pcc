"""Top-level module generation entrypoint for L1CodeGen."""

from __future__ import annotations

import os
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    AugAssign,
    ClassDef,
    Delete,
    ExprStmt,
    For,
    FuncDef,
    If,
    Module,
    Stmt,
    Try,
    While,
    With,
)
from .layer1_support import (
    _import_from_module_or_empty,
    _import_names_from_stmt,
    _is_import_from_stmt,
    _is_import_stmt,
)
from .runtime_abi import declare_runtime

_UNSAFE_SCAFFOLD_MODULES = frozenset({"pcc.unsafe"})


def _codegen_log(parent, enabled: bool, label: str) -> None:
    if not enabled:
        return
    mod_name = parent.ast_module.name or "<module>"
    sys.stderr.write("[pcc.codegen] " + mod_name + ":" + label + "\n")


def _iter_module_block_decls(stmt: Stmt):
    """Yield ``def``/``class`` statements nested in module-scope blocks.

    Python treats ``def`` and ``class`` as executable statements, so packages
    commonly put them under import-time ``if`` blocks. pcc still lowers the
    callable/class bodies statically; the block execution step is a no-op for
    those declarations.
    """
    if isinstance(stmt, (FuncDef, ClassDef)):
        yield stmt
        return
    if isinstance(stmt, (If, While, For)):
        for child in stmt.body:
            yield from _iter_module_block_decls(child)
        for child in stmt.else_body:
            yield from _iter_module_block_decls(child)
        return
    if isinstance(stmt, Try):
        for child in stmt.body:
            yield from _iter_module_block_decls(child)
        for handler in stmt.handlers:
            for child in handler.body:
                yield from _iter_module_block_decls(child)
        for child in stmt.else_body:
            yield from _iter_module_block_decls(child)
        for child in stmt.finally_body:
            yield from _iter_module_block_decls(child)
        return
    if isinstance(stmt, With):
        for child in stmt.body:
            yield from _iter_module_block_decls(child)


class GenerationLoweringMixin:
    def _generate_impl(self, module: Optional[Module] = None) -> str:
        """Lower the AST module to an LLVM IR text blob.

        ``module`` may be supplied to override the one given to the
        constructor, matching the task contract.
        """
        debug_codegen = bool(os.environ.get("PCC_DEBUG_CODEGEN_PHASES"))

        _codegen_log(self, debug_codegen, "start")
        saved_skip_program_main = self._skip_program_main
        saved_sibling_module_inits = self._sibling_module_inits
        saved_native_module_exports = self._native_module_exports
        if module is not None:
            setattr(self, "ast_module", module)
            setattr(self, "_ast_body", module.body)
            setattr(self, "_try_err_block", None)
            setattr(self, "module", ir.Module(name=module.name or "pcc_py_module"))
            setattr(self, "runtime", declare_runtime(self.module))
            setattr(self, "_printf", self._declare_printf())
            setattr(self, "functions", {})
            setattr(self, "_c_abi_export_symbols", set())
            setattr(self, "_module_has_c_abi_export", False)
            setattr(self, "_fn_err_exit_blocks", {})
            setattr(self, "_fn_err_exit_gc_root_names", {})
            setattr(self, "_post_call_frame_blocks", {})
            setattr(self, "_fmt_int", None)
            setattr(self, "_fmt_float", None)
            setattr(self, "_fmt_bool_true", None)
            setattr(self, "_fmt_bool_false", None)
            setattr(self, "_str_pool", {})
            setattr(self, "_str_obj_pool", {})
            setattr(self, "_attr_pool", {})
            setattr(self, "_cstr_pool", {})
            setattr(self, "_str_counter", 0)
            setattr(self, "_cstr_counter", 0)
            setattr(self, "_class_type_export_cache", {})
            setattr(self, "_class_aliases", {})
            setattr(self, "_native_module_aliases", {})
            setattr(self, "_native_builtin_module_aliases", {})
            setattr(self, "_native_builtin_value_aliases", {})
            setattr(self, "_native_module_attr_globals", {})
            setattr(self, "_native_file_values", set())
            setattr(self, "_native_file_env_flags", {})
            setattr(self, "_cross_module_func_defs", {})
            setattr(self, "_module_block_func_defs", {})
            setattr(self, "_unboxed_typed_int_abi_cache", {})
            setattr(self, "_typed_int_abi_call_arg_safety", [])
            setattr(self, "current_class", None)
            setattr(self, "current_method_kind", None)
            setattr(self, "_skip_program_main", saved_skip_program_main)
            setattr(self, "_sibling_module_inits", saved_sibling_module_inits)
            setattr(self, "_native_module_exports", saved_native_module_exports)

        # ``self.class_lowering`` is constructed once in __init__; do
        # NOT reassign here — under pcc-py self-host the default-None
        # → ClassLowering(self) reassignment dropped the new value
        # and made every ``self.class_lowering.X(...)`` fall through
        # to dynamic ``py_cpy_*`` dispatch (forcing libpython).

        # Pre-pass: hoist nested ``def`` blocks out of outer FuncDef /
        # ClassDef method bodies to the module's top level. pcc has
        # no closure-conversion path yet, so the hoisted function is
        # rewritten with a unique ``__nested_<outer>_<name>`` symbol
        # and the original binding in the enclosing body is replaced
        # by an alias-Assign — ``<inner_name> = <hoisted_name>`` —
        # so direct calls ``inner_name(arg)`` continue to route
        # through the existing user-function call path.
        _codegen_log(self, debug_codegen, "hoist begin")
        hoisted = self._hoist_nested_funcdefs()
        _codegen_log(self, debug_codegen, "hoist end " + str(len(hoisted)))
        _codegen_log(self, debug_codegen, "module flags raw-int begin")
        setattr(
            self,
            "_module_uses_raw_int_scaffold",
            self._module_imports_raw_int_scaffold(),
        )
        _codegen_log(self, debug_codegen, "module flags raw-int end")
        _codegen_log(self, debug_codegen, "module flags c-abi begin")
        setattr(self, "_module_has_c_abi_export", self._module_imports_c_abi_export())
        _codegen_log(self, debug_codegen, "module flags c-abi end")
        _codegen_log(self, debug_codegen, "module flags typed-int begin")
        setattr(
            self,
            "_typed_int_abi_call_arg_safety",
            self._compute_typed_int_abi_call_arg_safety(),
        )
        _codegen_log(self, debug_codegen, "module flags typed-int end")
        _codegen_log(self, debug_codegen, "module flags done")

        # Partition module-level statements into (def-shaped,
        # statement-body). Anything that isn't a FuncDef/ClassDef is
        # queued into the synthesized module-main body so that
        # ``main()`` at file scope still runs at program start.
        main_body: list[Stmt] = []
        module_block_decls: list[Stmt] = []
        declared_module_funcs: set[str] = set()
        declared_module_classes: set[str] = set()

        stmt_index = 0
        for stmt in self.ast_module.body:
            if debug_codegen:
                _codegen_log(self, debug_codegen, "declare begin")
            if isinstance(stmt, FuncDef):
                declared_module_funcs.add(stmt.name)
                self._prescan_function_module_globals(stmt)
                self._declare_user_function(stmt)
            elif isinstance(stmt, ClassDef):
                declared_module_classes.add(stmt.name)
                for class_stmt in stmt.body:
                    if isinstance(class_stmt, FuncDef):
                        self._prescan_function_module_globals(class_stmt)
                self.class_lowering.declare_class(stmt)
                main_body.append(stmt)
            elif _is_import_stmt(stmt) and not _is_import_from_stmt(stmt):
                for mod_name, as_name in _import_names_from_stmt(stmt):
                    if self._is_test_facade_import_module(mod_name):
                        continue
                    if mod_name in (
                        "builtins",
                        "sys",
                        "os",
                        "time",
                        "string",
                        "platform",
                        "subprocess",
                        "asyncio",
                        "tempfile",
                        "fileinput",
                        "shutil",
                        "shlex",
                        "sysconfig",
                        "math",
                        "json",
                        "re",
                        "codecs",
                        "copy",
                        "pickle",
                        "gc",
                        "weakref",
                        "threading",
                        "pcc.virtual_thread",
                        "pcc",
                        "importlib",
                        "inspect",
                        "contextlib",
                        "warnings",
                        "textwrap",
                    ):
                        self._register_native_builtin_module_alias(
                            as_name or mod_name,
                            mod_name,
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
                            "builtins",
                            "sys",
                            "os",
                            "platform",
                            "subprocess",
                            "asyncio",
                            "tempfile",
                            "fileinput",
                            "shutil",
                            "shlex",
                            "sysconfig",
                            "math",
                            "json",
                            "re",
                            "codecs",
                            "copy",
                            "pickle",
                            "gc",
                            "weakref",
                            "threading",
                            "pcc.virtual_thread",
                            "pcc",
                            "importlib",
                            "inspect",
                            "contextlib",
                            "warnings",
                            "textwrap",
                        ):
                            self._register_native_builtin_module_alias(
                                top,
                                top,
                            )
                            continue
                    native_table = self._native_module_exports
                    if native_table is not None and mod_name in native_table:
                        local_name = (
                            mod_name.split(".")[0]
                            if as_name is None and "." in mod_name
                            else as_name or mod_name
                        )
                        self._register_native_module_alias(local_name, mod_name)
                        continue
                    # Match _emit_import's binding convention: bind
                    # the top-level for ``import a.b`` (no alias) so
                    # ``a.b.c`` lookups via getattr succeed.
                    if as_name is None and "." in mod_name:
                        local_name = mod_name.split(".")[0]
                    else:
                        local_name = as_name or mod_name
                    if self._resolve_pcc_native_extension_path(mod_name) is not None:
                        gv = self._native_extension_module_global(local_name)
                        self._native_extension_modules()[local_name] = gv
                        continue
                    self._cpy_module_global(local_name)
                    self._cpy_modules()[local_name] = self._cpy_module_global(
                        local_name
                    )
                main_body.append(stmt)
            elif _is_import_from_stmt(stmt):
                # Compile-time scaffold imports (pcc.extern / pcc.llvm_capi)
                # carry no runtime CPython globals — their names are
                # consumed by codegen during the emit pass. Seed the
                # binding set now so extern decls that follow (and
                # extern calls in user functions) see them.
                import_module = _import_from_module_or_empty(stmt)
                if self._is_extern_scaffold_import_module(import_module):
                    self._register_extern_scaffold_imports(stmt)
                elif self._is_test_facade_import_module(import_module):
                    pass
                elif import_module in _UNSAFE_SCAFFOLD_MODULES:
                    self._register_unsafe_scaffold_imports(stmt)
                elif self._register_native_builtin_import_from_aliases(
                    stmt,
                    self._resolve_relative_import(stmt),
                ):
                    pass
                else:
                    # Multi-file compile: pre-register native sibling
                    # imports in the first pass so user function bodies
                    # emitted immediately after see the extern binding.
                    # The regular CPython-backed side-effect (allocating
                    # a module global) is skipped for native siblings.
                    native_table = self._native_module_exports
                    resolved = (
                        self._resolve_relative_import(stmt)
                        if native_table is not None
                        else None
                    )
                    handled_as_native_submodule = False
                    if native_table is not None:
                        remaining_names = []
                        for attr_name, as_name in _import_names_from_stmt(stmt):
                            full_submodule = self._native_import_from_submodule(
                                resolved,
                                attr_name,
                            )
                            if full_submodule is None:
                                full_submodule = (
                                    self._resolve_relative_import_submodule(
                                        stmt,
                                        resolved,
                                        attr_name,
                                    )
                                )
                            if (
                                full_submodule is not None
                                and full_submodule in native_table
                            ):
                                self._register_native_module_alias(
                                    as_name or attr_name,
                                    full_submodule,
                                )
                                continue
                            remaining_names.append((attr_name, as_name))
                        handled_as_native_submodule = not remaining_names
                    if handled_as_native_submodule:
                        pass
                    elif (
                        native_table is not None
                        and self._has_native_import_from_targets(
                            stmt,
                            resolved,
                        )
                    ):
                        self._predeclare_native_cross_module(
                            stmt,
                            resolved,
                            native_table.get(resolved, {}),
                        )
                    else:
                        for attr_name, as_name in _import_names_from_stmt(stmt):
                            if attr_name == "*":
                                self._cpy_star_module_global(import_module)
                                continue
                            local_name = as_name or attr_name
                            self._cpy_module_global(local_name)
                            self._cpy_modules()[local_name] = self._cpy_module_global(
                                local_name
                            )
                main_body.append(stmt)
            elif isinstance(stmt, Assign) and self._maybe_register_extern_assign(stmt):
                # Pre-register extern("symbol", ...) decls during the
                # declare pass so user-function bodies emitted next can
                # resolve the extern callable. Do NOT append to
                # main_body — nothing runtime to emit.
                pass
            elif isinstance(stmt, ExprStmt) and self._maybe_define_unsafe_global_stmt(
                stmt
            ):
                # Compile-time data definition for runtime substrate
                # modules. The generated library object must contain the
                # global symbol even though its synthetic main() is later
                # stripped by the runtime Makefile.
                pass
            elif isinstance(
                stmt, (ExprStmt, Assign, AugAssign, If, While, For, Try, With, Delete)
            ):
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
                    for decl in _iter_module_block_decls(stmt):
                        if isinstance(decl, FuncDef):
                            if decl.name in declared_module_funcs:
                                continue
                            declared_module_funcs.add(decl.name)
                            self._prescan_function_module_globals(decl)
                            self._declare_user_function(decl)
                            self._module_block_func_defs[decl.name] = decl
                            module_block_decls.append(decl)
                            continue
                        if isinstance(decl, ClassDef):
                            if decl.name in declared_module_classes:
                                continue
                            declared_module_classes.add(decl.name)
                            for class_stmt in decl.body:
                                if isinstance(class_stmt, FuncDef):
                                    self._prescan_function_module_globals(class_stmt)
                            self.class_lowering.declare_class(decl)
                            module_block_decls.append(decl)
                main_body.append(stmt)
            else:
                mod_name = self.ast_module.name or "<mod>"
                raise NotImplementedError(
                    "Layer 1 only supports top-level FuncDef / ClassDef / "
                    f"Import / Assign / AugAssign / ExprStmt / If / While / "
                    f"For / Try / With at module scope; got {type(stmt).__name__}"
                    f" (in module {mod_name!r}, stmt_index={stmt_index})"
                )
            if debug_codegen:
                _codegen_log(self, debug_codegen, "declare end")
            stmt_index += 1

        self._predeclare_native_builtin_module_attr_stores(tuple(self.ast_module.body))

        stmt_index = 0
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef):
                _codegen_log(self, debug_codegen, "emit func begin " + str(stmt_index) + " " + stmt.name)
                self._emit_user_function(stmt)
                _codegen_log(self, debug_codegen, "emit func end " + str(stmt_index) + " " + stmt.name)
            elif isinstance(stmt, ClassDef):
                _codegen_log(self, debug_codegen, "emit class begin " + str(stmt_index) + " " + stmt.name)
                self.class_lowering.emit_methods(stmt)
                _codegen_log(self, debug_codegen, "emit class end " + str(stmt_index) + " " + stmt.name)
            stmt_index += 1
        for decl in module_block_decls:
            if isinstance(decl, FuncDef):
                _codegen_log(self, debug_codegen, "emit block func begin " + decl.name)
                self._emit_user_function(decl)
                _codegen_log(self, debug_codegen, "emit block func end " + decl.name)
            elif isinstance(decl, ClassDef):
                _codegen_log(self, debug_codegen, "emit block class begin " + decl.name)
                self.class_lowering.emit_methods(decl)
                _codegen_log(self, debug_codegen, "emit block class end " + decl.name)

        _codegen_log(self, debug_codegen, "class module init begin")
        self.class_lowering.emit_module_init()
        _codegen_log(self, debug_codegen, "class module init end")
        # Multi-file compile mode: non-entry modules emit a
        # ``_pcc_py_module_top_<mod>()`` initialiser instead of the
        # program entry ``@main``. The entry module's @main is
        # responsible for calling each other module's top-level init
        # before its own body runs.
        if self._skip_program_main:
            _codegen_log(self, debug_codegen, "module top init begin")
            self._emit_module_top_init(main_body)
            _codegen_log(self, debug_codegen, "module top init end")
        else:
            _codegen_log(self, debug_codegen, "program main begin")
            self._emit_program_main(main_body)
            _codegen_log(self, debug_codegen, "program main end")

        _codegen_log(self, debug_codegen, "module teardown begin")
        self._emit_module_teardown()
        _codegen_log(self, debug_codegen, "module teardown end")

        _codegen_log(self, debug_codegen, "module str begin")
        out = str(self.module)
        _codegen_log(self, debug_codegen, "module str end " + str(len(out)))
        return out
