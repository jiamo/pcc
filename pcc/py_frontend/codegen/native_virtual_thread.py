"""Native ``pcc.virtual_thread`` lowering helpers."""

from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, DynType, Expr, FuncDef, Name, NoneType
from . import marshal

_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


_VTHREAD_EXPORTS = (
    "spawn",
    "run",
    "run_until_idle",
    "carrier_pool_start",
    "carrier_pool_stop",
    "current",
    "yield_now",
    "sleep_current",
    "block_current_on_fd",
    "result",
    "state",
    "sleep",
    "block_on_fd",
)


def _is_vthread_export(name: str) -> bool:
    return (
        name == "spawn"
        or name == "run"
        or name == "run_until_idle"
        or name == "carrier_pool_start"
        or name == "carrier_pool_stop"
        or name == "current"
        or name == "yield_now"
        or name == "sleep_current"
        or name == "block_current_on_fd"
        or name == "result"
        or name == "state"
        or name == "sleep"
        or name == "block_on_fd"
    )


class NativeVirtualThreadLoweringMixin:
    def _emit_native_virtual_thread_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        if not isinstance(attr, Attr):
            return None
        if not isinstance(attr.obj, Name):
            return None
        if self._native_builtin_module_for_name(attr.obj.ident) != "pcc.virtual_thread":
            return None
        if not _is_vthread_export(attr.name):
            return None
        return self._emit_native_virtual_thread_value_call(
            "pcc.virtual_thread." + attr.name,
            expr.args,
            expr.kwargs,
        )

    def _virtual_thread_frame_map(self, n_slots: int) -> ir.GlobalVariable:
        name = f".pcc.vthread.frame.map.{n_slots}"
        existing = self.module.globals.get(name)
        if existing is not None:
            return existing
        gv = ir.GlobalVariable(self.module, _I32, name=name)
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(_I32, n_slots)
        return gv

    def _emit_virtual_thread_rc_check(self, rc: ir.Value, hint: str) -> None:
        failed = self.builder.icmp_signed(
            "<",
            rc,
            ir.Constant(_I64, 0),
            name=self._fresh("vthread.rc.failed"),
        )
        fail_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.rc.fail"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.rc.ok"),
        )
        self.builder.cbranch(failed, fail_bb, ok_bb)
        self.builder.position_at_end(fail_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, 7),
                self._ptr_to_cstr(
                    self._cstr_global(
                        hint + " failed",
                        self._fresh(".vthread.rc.err"),
                    )
                ),
            ],
            name=self._fresh("vthread.rc.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        self.builder.position_at_end(ok_bb)

    def _emit_virtual_thread_resume_function(
        self,
        name: str,
        fn: ir.Function,
        ast_func_def: FuncDef,
        n_args: int,
    ) -> ir.Function:
        resume_name = f"{fn.name}__vthread_resume_{n_args}"
        existing = self.module.globals.get(resume_name)
        if isinstance(existing, ir.Function):
            return existing

        fnty = ir.FunctionType(_I64, [_CSTR, _CSTR])
        resume_fn = ir.Function(self.module, fnty, name=resume_name)
        resume_fn.linkage = "internal"
        resume_fn.args[0].name = "vthread"
        resume_fn.args[1].name = "continuation"

        saved_builder = self.builder
        saved_fn = self.current_function
        saved_fd = self.current_func_def

        entry = resume_fn.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(entry)
        self.current_function = resume_fn
        self.current_func_def = ast_func_def

        runtime_formals = tuple(a for a in ast_func_def.args if a.name != "")
        call_args: list[ir.Value] = []
        loaded_slots: list[ir.Value] = []
        for idx, formal in enumerate(runtime_formals[:n_args]):
            slot_obj = self.builder.call(
                self.runtime["py_continuation_get_slot"],
                [resume_fn.args[1], ir.Constant(_I64, idx)],
                name=self._fresh(f"vthread.slot.{idx}"),
            )
            loaded_slots.append(slot_obj)
            bind_ty = formal.annotation or DynType(name="dyn")
            call_args.append(
                marshal.marshal_from_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    slot_obj,
                    bind_ty,
                )
            )

        ret_ty = ast_func_def.return_ty
        if ret_ty is None or isinstance(ret_ty, NoneType):
            result_val = self.builder.call(fn, call_args)
        else:
            result_val = self.builder.call(
                fn,
                call_args,
                name=self._fresh(f"{name}.vthread.result"),
            )
        for slot_obj in loaded_slots:
            self._gc_release(slot_obj)

        if ret_ty is None or isinstance(ret_ty, NoneType):
            result_obj = self._emit_none_literal()
        else:
            result_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                result_val,
                ret_ty,
            )
        rc = self.builder.call(
            self.runtime["py_virtual_thread_complete"],
            [resume_fn.args[0], result_obj],
            name=self._fresh("vthread.complete.rc"),
        )
        self.builder.ret(rc)

        self.builder = saved_builder
        self.current_function = saved_fn
        self.current_func_def = saved_fd
        return resume_fn

    def _emit_virtual_thread_spawn(
        self,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kwargs or len(args) < 1:
            return None
        target = args[0]
        if not isinstance(target, Name):
            return None
        fn = self.functions.get(target.ident)
        if fn is None:
            return None
        ast_func_def = self._find_user_funcdef(target.ident)
        if ast_func_def is None:
            return None

        value_args = args[1:]
        runtime_formals = tuple(a for a in ast_func_def.args if a.name != "")
        if len(value_args) != len(runtime_formals):
            return None

        if target.ident in getattr(
            self,
            "_generator_func_names",
            set(),
        ) or self._funcdef_has_yield_sentinel(ast_func_def):
            return self._emit_virtual_thread_generator_spawn(
                target.ident,
                fn,
                ast_func_def,
                value_args,
                runtime_formals,
            )

        resume_fn = self._emit_virtual_thread_resume_function(
            target.ident,
            fn,
            ast_func_def,
            len(value_args),
        )
        frame_map = self._virtual_thread_frame_map(len(value_args))
        frame_map_ptr = self.builder.bitcast(
            frame_map,
            _CSTR,
            name=self._fresh("vthread.frame.map"),
        )

        boxed_slots: list[ir.Value] = []
        if value_args:
            slots_ty = ir.ArrayType(_CSTR, len(value_args))
            slots_arr = self._alloca_in_entry(
                slots_ty,
                name=self._fresh("vthread.slots"),
            )
            for idx, (arg_expr, formal) in enumerate(zip(value_args, runtime_formals)):
                raw = self._emit_expr(arg_expr)
                obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    raw,
                    formal.annotation or arg_expr.ty or DynType(name="dyn"),
                )
                boxed_slots.append(obj)
                gep = self.builder.gep(
                    slots_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, idx)],
                    inbounds=True,
                    name=self._fresh(f"vthread.slot.addr.{idx}"),
                )
                self.builder.store(obj, gep)
            slots_ptr = self.builder.gep(
                slots_arr,
                [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
                inbounds=True,
                name=self._fresh("vthread.slots.ptr"),
            )
            slots_arg = self.builder.bitcast(
                slots_ptr,
                _CSTR,
                name=self._fresh("vthread.slots.arg"),
            )
        else:
            slots_arg = ir.Constant(_CSTR, None)

        resume_ptr = self.builder.bitcast(
            resume_fn,
            _CSTR,
            name=self._fresh("vthread.resume.ptr"),
        )
        cont = self.builder.call(
            self.runtime["py_continuation_new_typed"],
            [frame_map_ptr, slots_arg, resume_ptr],
            name=self._fresh("vthread.cont"),
        )
        for boxed in boxed_slots:
            self._gc_release(boxed)
        vt = self.builder.call(
            self.runtime["py_virtual_thread_new"],
            [cont],
            name=self._fresh("vthread.new"),
        )
        self._gc_release(cont)
        rc = self.builder.call(
            self.runtime["py_virtual_thread_start"],
            [vt],
            name=self._fresh("vthread.start.rc"),
        )
        failed = self.builder.icmp_signed(
            "!=",
            rc,
            ir.Constant(_I64, 0),
            name=self._fresh("vthread.start.failed"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.start.ok"),
        )
        fail_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.start.fail"),
        )
        self.builder.cbranch(failed, fail_bb, ok_bb)
        self.builder.position_at_end(fail_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, 7),
                self._ptr_to_cstr(
                    self._cstr_global(
                        "virtual thread start failed",
                        self._fresh(".vthread.start.err"),
                    )
                ),
            ],
            name=self._fresh("vthread.start.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        self.builder.position_at_end(ok_bb)
        return vt

    def _emit_virtual_thread_generator_spawn(
        self,
        name: str,
        fn: ir.Function,
        ast_func_def: FuncDef,
        value_args: tuple[Expr, ...],
        runtime_formals: tuple,
    ) -> ir.Value:
        call_args: list[ir.Value] = []
        for arg_expr, formal in zip(value_args, runtime_formals):
            raw = self._emit_expr(arg_expr)
            call_args.append(
                self._coerce(
                    raw,
                    arg_expr.ty,
                    formal.annotation or DynType(name="dyn"),
                )
            )
        gen = self.builder.call(
            fn,
            call_args,
            name=self._fresh(f"{name}.vthread.gen"),
        )

        slots_ty = ir.ArrayType(_CSTR, 1)
        slots_arr = self._alloca_in_entry(
            slots_ty,
            name=self._fresh("vthread.gen.slots"),
        )
        gep = self.builder.gep(
            slots_arr,
            [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
            inbounds=True,
            name=self._fresh("vthread.gen.slot.addr"),
        )
        self.builder.store(gen, gep)
        slots_ptr = self.builder.gep(
            slots_arr,
            [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
            inbounds=True,
            name=self._fresh("vthread.gen.slots.ptr"),
        )
        slots_arg = self.builder.bitcast(
            slots_ptr,
            _CSTR,
            name=self._fresh("vthread.gen.slots.arg"),
        )
        frame_map = self._virtual_thread_frame_map(1)
        frame_map_ptr = self.builder.bitcast(
            frame_map,
            _CSTR,
            name=self._fresh("vthread.gen.frame.map"),
        )
        resume_ptr = self.builder.bitcast(
            self.runtime["py_virtual_thread_resume_generator"],
            _CSTR,
            name=self._fresh("vthread.gen.resume.ptr"),
        )
        cont = self.builder.call(
            self.runtime["py_continuation_new_typed"],
            [frame_map_ptr, slots_arg, resume_ptr],
            name=self._fresh("vthread.gen.cont"),
        )
        self._gc_release(gen)
        vt = self.builder.call(
            self.runtime["py_virtual_thread_new"],
            [cont],
            name=self._fresh("vthread.gen.new"),
        )
        self._gc_release(cont)
        rc = self.builder.call(
            self.runtime["py_virtual_thread_start"],
            [vt],
            name=self._fresh("vthread.gen.start.rc"),
        )
        failed = self.builder.icmp_signed(
            "!=",
            rc,
            ir.Constant(_I64, 0),
            name=self._fresh("vthread.gen.start.failed"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.gen.start.ok"),
        )
        fail_bb = self.current_function.append_basic_block(
            name=self._fresh("vthread.gen.start.fail"),
        )
        self.builder.cbranch(failed, fail_bb, ok_bb)
        self.builder.position_at_end(fail_bb)
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [
                ir.Constant(_I64, 7),
                self._ptr_to_cstr(
                    self._cstr_global(
                        "virtual thread generator start failed",
                        self._fresh(".vthread.gen.start.err"),
                    )
                ),
            ],
            name=self._fresh("vthread.gen.start.exc"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        self.builder.position_at_end(ok_bb)
        return vt

    def _emit_native_virtual_thread_value_call(
        self,
        kind: str,
        args: tuple[Expr, ...],
        kwargs: tuple[tuple[str, Expr], ...],
    ) -> Optional[ir.Value]:
        if kind == "pcc.virtual_thread.spawn":
            return self._emit_virtual_thread_spawn(args, kwargs)
        if kwargs:
            return None
        if kind == "pcc.virtual_thread.run":
            if len(args) != 2:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_run_carrier_pool"],
                [self._emit_expr_as_i64(args[0]), self._emit_expr_as_i64(args[1])],
                name=self._fresh("vthread.run"),
            )
        if kind == "pcc.virtual_thread.run_until_idle":
            if len(args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_run_until_idle"],
                [self._emit_expr_as_i64(args[0])],
                name=self._fresh("vthread.run_until_idle"),
            )
        if kind == "pcc.virtual_thread.carrier_pool_start":
            if len(args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_carrier_pool_start"],
                [self._emit_expr_as_i64(args[0])],
                name=self._fresh("vthread.pool.start"),
            )
        if kind == "pcc.virtual_thread.carrier_pool_stop":
            if args:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_carrier_pool_stop"],
                [],
                name=self._fresh("vthread.pool.stop"),
            )
        if kind == "pcc.virtual_thread.current":
            if args:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_current"],
                [],
                name=self._fresh("vthread.current"),
            )
        if kind == "pcc.virtual_thread.yield_now":
            if args:
                return None
            if len(self._generator_ctx_stack) > 0:
                self._emit_generator_yield_value(self._emit_none_literal())
            return self._emit_none_literal()
        if kind == "pcc.virtual_thread.sleep_current":
            if len(args) != 1:
                return None
            current = self.builder.call(
                self.runtime["py_virtual_thread_current"],
                [],
                name=self._fresh("vthread.sleep.current"),
            )
            rc = self.builder.call(
                self.runtime["py_virtual_thread_sleep"],
                [current, self._emit_expr_as_i64(args[0])],
                name=self._fresh("vthread.sleep.current.rc"),
            )
            self._gc_release(current)
            self._emit_virtual_thread_rc_check(rc, "virtual thread sleep")
            if len(self._generator_ctx_stack) > 0:
                self._emit_generator_yield_value(self._emit_none_literal())
            return self._emit_none_literal()
        if kind == "pcc.virtual_thread.result":
            if len(args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_result"],
                [self._emit_as_object(args[0])],
                name=self._fresh("vthread.result"),
            )
        if kind == "pcc.virtual_thread.state":
            if len(args) != 1:
                return None
            return self.builder.call(
                self.runtime["py_virtual_thread_state"],
                [self._emit_as_object(args[0])],
                name=self._fresh("vthread.state"),
            )
        if kind == "pcc.virtual_thread.sleep":
            if len(args) != 2:
                return None
            raw = self.builder.call(
                self.runtime["py_virtual_thread_sleep"],
                [self._emit_as_object(args[0]), self._emit_expr_as_i64(args[1])],
                name=self._fresh("vthread.sleep.rc"),
            )
            _ = raw
            return self._emit_none_literal()
        if kind == "pcc.virtual_thread.block_on_fd":
            if len(args) != 4:
                return None
            raw = self.builder.call(
                self.runtime["py_virtual_thread_block_on_fd"],
                [
                    self._emit_as_object(args[0]),
                    self._emit_expr_as_i64(args[1]),
                    self._emit_expr_as_i64(args[2]),
                    self._emit_expr_as_i64(args[3]),
                ],
                name=self._fresh("vthread.block_fd.rc"),
            )
            _ = raw
            return self._emit_none_literal()
        if kind == "pcc.virtual_thread.block_current_on_fd":
            if len(args) != 3:
                return None
            current = self.builder.call(
                self.runtime["py_virtual_thread_current"],
                [],
                name=self._fresh("vthread.block.current"),
            )
            rc = self.builder.call(
                self.runtime["py_virtual_thread_block_on_fd"],
                [
                    current,
                    self._emit_expr_as_i64(args[0]),
                    self._emit_expr_as_i64(args[1]),
                    self._emit_expr_as_i64(args[2]),
                ],
                name=self._fresh("vthread.block.current.rc"),
            )
            self._gc_release(current)
            self._emit_virtual_thread_rc_check(rc, "virtual thread fd block")
            if len(self._generator_ctx_stack) > 0:
                self._emit_generator_yield_value(self._emit_none_literal())
            return self._emit_none_literal()
        return None


__all__ = ["NativeVirtualThreadLoweringMixin", "_VTHREAD_EXPORTS"]
