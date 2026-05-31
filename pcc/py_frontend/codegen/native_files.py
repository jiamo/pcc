"""Native file-object lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import Attr, Call, DynType, Name, StrType, With


_I8 = ir.IntType(8)
_CSTR = _I8.as_pointer()



class NativeFilesLoweringMixin:
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
                mode_v,
                expr.args[1].ty,
                "cpy.file.mode",
            )
        else:
            mode_obj = self._emit_str_literal("r")
        result = self.builder.call(
            self.runtime["py_file_open"],
            [path_obj, mode_obj],
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
        if not self._native_file_env_flags.get(attr.obj.ident, False):
            return None
        recv = self._emit_expr(attr.obj)
        if attr.name == "read":
            if not expr.args:
                return self.builder.call(
                    self.runtime["py_file_read_all"],
                    [recv],
                    name=self._fresh("file.read"),
                )
            if len(expr.args) == 1:
                limit_v = self._emit_expr(expr.args[0])
                limit_i64 = self._to_int64(limit_v, expr.args[0].ty)
                return self.builder.call(
                    self.runtime["py_file_read"],
                    [recv, limit_i64],
                    name=self._fresh("file.read"),
                )
        if attr.name == "write" and len(expr.args) == 1:
            text_v = self._emit_expr(expr.args[0])
            text_obj = self._emit_value_as_pcc_object_or_bridge(
                text_v,
                expr.args[0].ty,
                "cpy.file.write.arg",
            )
            return self.builder.call(
                self.runtime["py_file_write"],
                [recv, text_obj],
                name=self._fresh("file.write"),
            )
        if attr.name == "close" and not expr.args:
            self.builder.call(self.runtime["py_file_close"], [recv])
            return self._emit_none_literal()
        return None

    def _emit_native_fileinput_call(self, expr: Call) -> Optional[ir.Value]:
        func = expr.func
        if (
            not isinstance(func, Attr)
            or func.name != "FileInput"
            or not isinstance(func.obj, Name)
            or self._native_builtin_module_for_name(func.obj.ident) != "fileinput"
            or len(expr.args) != 1
        ):
            return None
        openhook = self._emit_none_literal()
        for key, value in expr.kwargs:
            if key != "openhook":
                return None
            openhook = self._emit_expr_with_native_callable_values(value)
        files = self._emit_as_object(expr.args[0])
        result = self.builder.call(
            self.runtime["py_fileinput_new"],
            [files, openhook],
            name=self._fresh("fileinput.FileInput"),
        )
        if not hasattr(self, "_native_fileinput_values"):
            self._native_fileinput_values = set()
        self._native_fileinput_values.add(result)
        return result

    def _emit_native_fileinput_method(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or expr.args:
            return None
        if not isinstance(attr.obj, Name):
            return None
        if not getattr(self, "_native_fileinput_env_flags", {}).get(attr.obj.ident, False):
            return None
        helpers = {
            "readline": "py_fileinput_readline",
            "filename": "py_fileinput_filename",
            "lineno": "py_fileinput_lineno",
            "filelineno": "py_fileinput_filelineno",
            "isfirstline": "py_fileinput_isfirstline",
            "close": "py_fileinput_close",
        }
        helper = helpers.get(attr.name)
        if helper is None:
            return None
        recv = self._emit_expr(attr.obj)
        return self.builder.call(
            self.runtime[helper],
            [recv],
            name=self._fresh("fileinput." + attr.name),
        )

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
        self._native_file_env_flags[as_expr.ident] = True
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
            if prefix_expr is not None
            else self._emit_str_literal("tmp")
        )
        tmp_val = self.builder.call(
            self.runtime["py_tempdir_new"],
            [prefix_obj],
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


__all__ = ["NativeFilesLoweringMixin"]
