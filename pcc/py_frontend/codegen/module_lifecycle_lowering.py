"""Module lifecycle lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Stmt


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_VOID = ir.VoidType()
_CSTR = _I8.as_pointer()


class ModuleLifecycleLoweringMixin:
    def _emit_module_teardown(self) -> None:
        """Emit a per-module function that clears object globals."""
        fn_name = self._module_teardown_name()
        existing = self.module.globals.get(fn_name)
        if existing is not None:
            if existing.blocks:
                return
            fn = existing
        else:
            fnty = ir.FunctionType(_VOID, [])
            fn = ir.Function(self.module, fnty, name=fn_name)
            fn.linkage = "external"
        entry = fn.append_basic_block("entry")
        body_bb = fn.append_basic_block("body")
        done_bb = fn.append_basic_block("done")

        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        setattr(self, "builder", ir.IRBuilder(entry))
        setattr(self, "current_function", fn)
        setattr(self, "current_func_def", None)

        guard_name = f".pcc.module.fini.{self._module_symbol_suffix()}"
        guard = self.module.globals.get(guard_name)
        if not isinstance(guard, ir.GlobalVariable):
            guard = ir.GlobalVariable(self.module, _I32, name=guard_name)
            guard.linkage = "internal"
            guard.initializer = ir.Constant(_I32, 0)
        seen = self.builder.load(guard, name=self._fresh("mod.fini.seen"))
        already = self.builder.icmp_signed(
            "!=",
            seen,
            ir.Constant(_I32, 0),
            name=self._fresh("mod.fini.done"),
        )
        self.builder.cbranch(already, done_bb, body_bb)

        self.builder.position_at_end(body_bb)
        self.builder.store(ir.Constant(_I32, 1), guard)
        for _name, item in reversed(list(self._module_globals.items())):
            gv, declared_ty = item
            if not self._module_global_needs_teardown(gv, declared_ty):
                continue
            if self._cpy_module_flags.get(_name, False):
                value = self.builder.load(
                    gv,
                    name=self._fresh("mod.fini.cpy.value"),
                )
                self.builder.store(ir.Constant(value.type, None), gv)
                self.builder.call(self.runtime["py_cpy_decref"], [value])
                continue
            value = self.builder.load(
                gv,
                name=self._fresh("mod.fini.value"),
            )
            self.builder.call(self.runtime["pcc_gc_unpin"], [value])
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [
                    self._as_gc_ptr(gv, name=self._fresh("mod.fini.slot")),
                    ir.Constant(_CSTR, None),
                ],
            )
        for gv in reversed(
            list(getattr(self, "_native_module_attr_globals", {}).values())
        ):
            value = self.builder.load(
                gv,
                name=self._fresh("mod.attr.fini.value"),
            )
            self.builder.call(self.runtime["pcc_gc_unpin"], [value])
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [
                    self._as_gc_ptr(gv, name=self._fresh("mod.attr.fini.slot")),
                    ir.Constant(_CSTR, None),
                ],
            )
        self.builder.branch(done_bb)

        self.builder.position_at_end(done_bb)
        self.builder.ret_void()

        setattr(self, "builder", saved_builder)
        setattr(self, "current_function", saved_fn)
        setattr(self, "current_func_def", saved_fd)

    def _emit_module_top_init(self, body: list["Stmt"]) -> None:
        """Emit ``void _pcc_py_module_top_<mod>()`` holding the
        module-level statements. Used when this compilation unit is a
        secondary module in a multi-file compile — the entry module's
        ``@main`` must call this before its own top-level body."""
        mod_name = self.ast_module.name or "mod"
        sanitised = mod_name.replace(".", "_").replace("-", "_")
        fnty = ir.FunctionType(_VOID, [])
        fn = ir.Function(
            self.module,
            fnty,
            name=f"_pcc_py_module_top_{sanitised}",
        )
        fn.linkage = "external"
        entry = fn.append_basic_block("entry")
        body_bb = fn.append_basic_block("body")
        done_bb = fn.append_basic_block("done")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        setattr(self, "builder", ir.IRBuilder(entry))
        setattr(self, "current_function", fn)
        setattr(self, "current_func_def", None)
        setattr(self, "env", {})
        setattr(self, "_box_int_locals", self._should_box_python_ints())
        setattr(self, "_exact_int_env_flags", {})
        setattr(self, "loop_stack", [])
        self._emit_thread_safepoint()

        guard_name = f".pcc.module.init.{sanitised}"
        guard = self.module.globals.get(guard_name)
        if not isinstance(guard, ir.GlobalVariable):
            guard = ir.GlobalVariable(self.module, _I32, name=guard_name)
            guard.linkage = "internal"
            guard.initializer = ir.Constant(_I32, 0)
        seen = self.builder.load(guard, name=self._fresh("mod.init.seen"))
        already = self.builder.icmp_signed(
            "!=",
            seen,
            ir.Constant(_I32, 0),
            name=self._fresh("mod.init.done"),
        )
        self.builder.cbranch(already, done_bb, body_bb)

        self.builder.position_at_end(body_bb)
        self.builder.store(ir.Constant(_I32, 1), guard)
        self._emit_module_root_enters()

        self._emit_stmts(tuple(body))

        if not self._builder_block_is_terminated():
            self.builder.branch(done_bb)

        self.builder.position_at_end(done_bb)
        if not self._builder_block_is_terminated():
            self.builder.ret_void()

        setattr(self, "builder", saved_builder)
        setattr(self, "current_function", saved_fn)
        setattr(self, "current_func_def", saved_fd)
        setattr(self, "env", saved_env)
        setattr(self, "_box_int_locals", saved_box_int_locals)
        setattr(self, "_exact_int_env_flags", saved_exact_int_flags)
        setattr(self, "loop_stack", saved_loops)

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
        entry = fn.append_basic_block("entry")
        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def
        saved_env = self.env
        saved_loops = self.loop_stack
        saved_box_int_locals = self._box_int_locals
        saved_exact_int_flags = self._exact_int_env_flags
        setattr(self, "builder", ir.IRBuilder(entry))
        setattr(self, "current_function", fn)
        setattr(self, "current_func_def", None)
        setattr(self, "env", {})
        setattr(self, "_box_int_locals", self._should_box_python_ints())
        setattr(self, "_exact_int_env_flags", {})
        setattr(self, "loop_stack", [])
        self._emit_thread_safepoint()

        self.builder.call(
            self.runtime["py_set_program_args"],
            [fn.args[0], fn.args[1]],
        )

        # Call other-module top-inits first (multi-file compile).
        # Each declared-external void function executes the sibling
        # module's class init + top-level statements.
        for sibling_mod in self._sibling_module_inits:
            sanitised_sib = sibling_mod.replace(".", "_").replace("-", "_")
            sib_top = f"_pcc_py_module_top_{sanitised_sib}"
            existing = self.module.globals.get(sib_top)
            if existing is None:
                sib_fn = ir.Function(
                    self.module,
                    ir.FunctionType(_VOID, []),
                    name=sib_top,
                )
                sib_fn.linkage = "external"
            else:
                sib_fn = existing
            self.builder.call(sib_fn, [])
            self._emit_post_call_err_check()

        self._emit_module_root_enters()
        self._emit_stmts(tuple(body))

        if not self._builder_block_is_terminated():
            if self.emit_cpy_main_exitcode:
                exit_code = self.builder.call(
                    self.runtime["py_cpy_main_exitcode"],
                    [],
                    name=self._fresh("cpy.exitcode"),
                )
            else:
                exit_code = ir.Constant(_I32, 0)
            self._emit_module_teardown_call(self.module.name or "mod")
            for sibling_mod in reversed(self._sibling_module_inits):
                self._emit_module_teardown_call(sibling_mod)
            self.builder.ret(exit_code)

        setattr(self, "builder", saved_builder)
        setattr(self, "current_function", saved_fn)
        setattr(self, "current_func_def", saved_fd)
        setattr(self, "env", saved_env)
        setattr(self, "_box_int_locals", saved_box_int_locals)
        setattr(self, "_exact_int_env_flags", saved_exact_int_flags)
        setattr(self, "loop_stack", saved_loops)
