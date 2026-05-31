"""Native system/process module lowering helpers for layer-1 codegen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, BoolLit, Call, Expr, Name, StrLit, StrType
from . import marshal


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()



class NativeSystemLoweringMixin:
    def _emit_sys_version_info_tuple(self) -> ir.Value:
        values = (3, 13, 0)
        version_info = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, len(values))],
            name=self._fresh("sys.version_info.tuple"),
        )
        for idx, value in enumerate(values):
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [
                    version_info,
                    ir.Constant(_I64, idx),
                    self.builder.call(
                        self.runtime["py_int_from_i64"],
                        [ir.Constant(_I64, value)],
                        name=self._fresh("sys.version_info.part"),
                    ),
                ],
                name=self._fresh("sys.version_info.set"),
            )
        return version_info

    def _emit_sys_version_info_attr(self, name: str) -> Optional[ir.Value]:
        values = {"major": 3, "minor": 13, "micro": 0}
        value = values.get(name)
        if value is None:
            return None
        return self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, value)],
            name=self._fresh(f"sys.version_info.{name}"),
        )

    def _subprocess_text_kwargs_are_native(self, expr: Call) -> bool:
        if not expr.kwargs:
            return False
        seen_text = False
        for key, value in expr.kwargs:
            if key in ("text", "universal_newlines"):
                if not isinstance(value, BoolLit) or not value.value:
                    return False
                seen_text = True
                continue
            if key == "stderr":
                if not (
                    isinstance(value, Attr)
                    and value.name == "STDOUT"
                    and isinstance(value.obj, Name)
                    and self._native_builtin_module_for_name(value.obj.ident)
                    == "subprocess"
                ):
                    return False
                continue
            if key not in ("text", "universal_newlines"):
                return False
        return seen_text

    def _emit_native_subprocess_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "subprocess"
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
        res = self.builder.call(
            self.runtime["py_subprocess_check_output"],
            [
                marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    argv,
                    expr.args[0].ty,
                )
            ],
            name=self._fresh("subprocess.check_output"),
        )
        self._emit_post_call_err_check(expr.span)
        return res

    def _emit_native_subprocess_run_stmt(self, expr: Call) -> bool:
        if not isinstance(expr.func, Attr):
            return False
        attr = expr.func
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "subprocess"
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
                        _I32,
                        1 if value.value else 0,
                    )
                else:
                    raw = self._emit_expr(value)
                    truthy = self._truthy(raw, value.ty)
                    capture_output_val = self.builder.zext(
                        truthy,
                        _I32,
                        name=self._fresh("subprocess.capture"),
                    )
            elif key == "text":
                if not isinstance(value, BoolLit):
                    return False
            elif key == "timeout":
                # The bootstrap driver wraps stage execution with a hard
                # process timeout. Accept this keyword in native no-libpython
                # lowering so pass-runner helper calls do not fall back to
                # CPython just because the host path has a tighter timeout.
                continue
            else:
                return False
        if not check_true:
            return False
        argv = self._emit_expr(expr.args[0])
        if argv in getattr(self, "_cpy_values", ()):
            return False
        argv_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            argv,
            expr.args[0].ty,
        )
        rc = self.builder.call(
            self.runtime["py_subprocess_run"],
            [argv_obj, capture_output_val],
            name=self._fresh("subprocess.run"),
        )
        failed = self.builder.icmp_signed(
            "!=",
            rc,
            ir.Constant(_I64, 0),
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
                ir.Constant(_I64, 7),
                self._ptr_to_cstr(
                    self._cstr_global(
                        "subprocess.run failed",
                        self._fresh(".err.subprocess.run"),
                    )
                ),
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
            or self._native_builtin_module_for_name(attr.obj.ident) != "shutil"
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
        ):
            return None
        if expr.kwargs:
            if len(expr.kwargs) != 1:
                return None
            kw_name, kw_value = expr.kwargs[0]
            if kw_name != "posix":
                return None
            if not isinstance(kw_value, BoolLit) or not kw_value.value:
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
            or self._native_builtin_module_for_name(attr.obj.ident) != "sysconfig"
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

    def _native_builtin_stream_kind_for_expr(self,
        expr: Expr,
    ) -> Optional[str]:
        if (
            isinstance(expr, Attr)
            and expr.name in ("stdout", "stderr")
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
        ):
            return expr.name
        if isinstance(expr, Name):
            value_kind = self._native_builtin_value_for_name(expr.ident)
            if value_kind == "sys.stdout":
                return "stdout"
            if value_kind == "sys.stderr":
                return "stderr"
        return None

    def _emit_native_sys_stream_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        stream_kind = self._native_builtin_stream_kind_for_expr(attr.obj)
        if stream_kind is None:
            return None
        if attr.name == "flush" and not expr.args:
            return self._emit_none_literal()
        if len(expr.args) != 1 or attr.name != "write":
            return None
        helper = (
            "py_sys_stdout_write" if stream_kind == "stdout" else "py_sys_stderr_write"
        )
        return self.builder.call(
            self.runtime[helper],
            [self._emit_as_object(expr.args[0])],
            name=self._fresh(f"sys.{stream_kind}.write"),
        )

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
            "py_sys_stdout_write" if stream_kind == "stdout" else "py_sys_stderr_write"
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
                    self.runtime["py_obj_str"],
                    [arg_obj],
                    name=self._fresh("print.str"),
                )
            self.builder.call(helper, [arg_str])
        end_v = self._emit_str_literal(end_str)
        self.builder.call(helper, [end_v])
        return True

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
            self.runtime["py_process_exit"],
            [code_i64],
        )
        return ir.Constant(_CSTR, None)

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
            self.runtime["py_program_argc"],
            [],
            name=self._fresh("argv.argc"),
        )
        lst = self.builder.call(
            self.runtime["py_list_new"],
            [argc],
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
            "<",
            cur,
            argc,
            name=self._fresh("argv.more"),
        )
        self.builder.cbranch(more, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        raw = self.builder.call(
            self.runtime["py_program_argv"],
            [cur],
            name=self._fresh("argv.raw"),
        )
        n_bytes = self.builder.call(
            self._declare_strlen(),
            [raw],
            name=self._fresh("argv.len"),
        )
        item = self.builder.call(
            self.runtime["py_str_new"],
            [raw, n_bytes],
            name=self._fresh("argv.str"),
        )
        self.builder.call(
            self.runtime["py_list_append"],
            [lst, item],
        )
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("argv.cur2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("argv.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return lst


__all__ = ["NativeSystemLoweringMixin"]
