"""Exception and error-exit lowering helpers for L1CodeGen."""
from __future__ import annotations

import os
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    Call,
    DynType,
    Expr,
    For,
    If,
    Import,
    Name,
    NoneLit,
    Raise,
    SourceSpan,
    StrLit,
    StrType,
    Try,
    TupleExpr,
    While,
    With,
)


_I8 = ir.IntType(8)
_I1 = ir.IntType(1)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()
_OPTIONAL_IMPORT_MISSING_NONE = "pcc.optional_import_missing.None"


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
    "ReferenceError": 18,
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
    "UnicodeError": 2,
    "UnicodeDecodeError": 2,
    "UnicodeEncodeError": 2,
    "RecursionError": 7,
    "ImportError": 1,
    "ModuleNotFoundError": 1,
    "EOFError": 1,
    "SystemExit": 0,
    "KeyboardInterrupt": 0,
    "GeneratorExit": 0,
    "StopAsyncIteration": 17,
}


def _builtin_exc_tag_or_missing(name: str) -> int:
    if name == "BaseException":
        return 0
    if name == "Exception":
        return 1
    if name == "ValueError":
        return 2
    if name == "TypeError":
        return 3
    if name == "KeyError":
        return 4
    if name == "IndexError":
        return 5
    if name == "AttributeError":
        return 6
    if name == "SyntaxError":
        return 1
    if name == "RuntimeError":
        return 7
    if name == "StopIteration":
        return 8
    if name == "ZeroDivisionError":
        return 9
    if name == "NameError":
        return 10
    if name == "NotImplementedError":
        return 11
    if name == "ArithmeticError":
        return 12
    if name == "LookupError":
        return 13
    if name == "OSError" or name == "IOError":
        return 14
    if name == "OverflowError":
        return 15
    if name == "AssertionError":
        return 16
    if name == "ReferenceError":
        return 18
    if name == "FileNotFoundError":
        return 14
    if name == "FileExistsError":
        return 14
    if name == "IsADirectoryError":
        return 14
    if name == "NotADirectoryError":
        return 14
    if name == "PermissionError":
        return 14
    if name == "BrokenPipeError":
        return 14
    if name == "ConnectionError":
        return 14
    if name == "ConnectionAbortedError":
        return 14
    if name == "ConnectionRefusedError":
        return 14
    if name == "ConnectionResetError":
        return 14
    if name == "BlockingIOError":
        return 14
    if name == "ChildProcessError":
        return 14
    if name == "InterruptedError":
        return 14
    if name == "TimeoutError":
        return 14
    if name == "UnicodeError":
        return 2
    if name == "UnicodeDecodeError":
        return 2
    if name == "UnicodeEncodeError":
        return 2
    if name == "RecursionError":
        return 7
    if name == "ImportError":
        return 1
    if name == "ModuleNotFoundError":
        return 1
    if name == "EOFError":
        return 1
    if name == "SystemExit":
        return 0
    if name == "KeyboardInterrupt":
        return 0
    if name == "GeneratorExit":
        return 0
    if name == "StopAsyncIteration":
        return 17
    return -1


class ExceptionLoweringMixin:
    def _push_try_err_block(self, err_bb):
        prev = self._try_err_block
        self._try_err_block = err_bb
        return prev

    def _restore_try_err_block(self, prev_err_block) -> None:
        self._try_err_block = prev_err_block

    def _current_try_err_block(self):
        return self._try_err_block

    def _maybe_emit_optional_missing_import_try(self, stmt: Try) -> bool:
        if stmt.else_body or stmt.finally_body:
            return False
        if len(stmt.handlers) != 1:
            return False
        handler = stmt.handlers[0]
        exc_type = handler.exc_type
        if not isinstance(exc_type, Name) or exc_type.ident != "ImportError":
            return False
        if len(stmt.body) != 1 or not isinstance(stmt.body[0], Import):
            return False
        assigned_none = {}
        for item in handler.body:
            if not isinstance(item, Assign):
                return False
            if not isinstance(item.value, NoneLit):
                return False
            for target in item.targets:
                if not isinstance(target, Name):
                    return False
                assigned_none[target.ident] = True

        native_table = self._native_module_exports
        missing_locals = []
        for mod_name, as_name in stmt.body[0].names:
            if self._is_test_facade_import_module(mod_name):
                continue
            if (
                mod_name == "typing"
                or mod_name.split(".")[0] in ("__future__", "typing", "abc", "click")
                or mod_name == "pcc.extern"
            ):
                return False
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
                "shutil",
                "shlex",
                "sysconfig",
                "math",
                "json",
                "re",
                "gc",
                "weakref",
                "copy",
                "pickle",
                "threading",
                "pcc.virtual_thread",
                "pcc",
                "importlib",
                "inspect",
                "contextlib",
                "warnings",
            ):
                return False
            if native_table is not None and mod_name in native_table:
                return False
            if self._resolve_pcc_native_extension_path(mod_name) is not None:
                return False
            local_name = as_name or mod_name.split(".")[0]
            if local_name not in assigned_none:
                return False
            missing_locals.append(local_name)
        if not missing_locals:
            return False
        for local_name in missing_locals:
            self._register_native_builtin_value_alias(
                local_name,
                _OPTIONAL_IMPORT_MISSING_NONE,
            )
            self._native_module_object_aliases.pop(local_name, None)
            self.env.pop(local_name, None)
            self.env_class_hint.pop(local_name, None)
            self.env_class_object_hint.pop(local_name, None)
            self._cpy_env_flags.pop(local_name, None)
            self._weak_dict_env_flags.pop(local_name, None)
        return True

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
                self.runtime["py_current_exception"],
                [],
                name=self._fresh("reraise.exc"),
            )
            active_stack = getattr(self, "_active_handler_excs", ())
            if active_stack:
                null = ir.Constant(_CSTR, None)
                has_cur = self.builder.icmp_signed(
                    "!=",
                    cur,
                    null,
                    name=self._fresh("reraise.has_cur"),
                )
                cur = self.builder.select(
                    has_cur,
                    cur,
                    active_stack[-1],
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

        frame_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("raise.frame.exc"),
        )
        self._emit_exception_frame(frame_exc, stmt.span)

        err_target = self._current_try_err_block()
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

    def _emit_try(self, stmt: Try) -> None:
        if self._maybe_emit_optional_missing_import_try(stmt):
            return
        debug_codegen = bool(os.environ.get("PCC_DEBUG_CODEGEN_PHASES"))

        def try_log(label: str) -> None:
            if not debug_codegen:
                return
            mod_name = self.ast_module.name or "<module>"
            func_name = (
                self.current_func_def.name
                if self.current_func_def is not None
                else "<top>"
            )
            sys.stderr.write(
                "[pcc.codegen] "
                + mod_name
                + ":"
                + func_name
                + ":try "
                + label
                + "\n"
            )

        try_log("begin")
        if not stmt.handlers and not stmt.finally_body:
            try_log("plain body begin")
            self._emit_stmts(stmt.body)
            try_log("plain body end")
            return

        fn = self.current_function
        try_log("blocks begin")
        done_bb = fn.append_basic_block(name=self._fresh("try.done"))
        err_bb = fn.append_basic_block(name=self._fresh("try.err"))
        try_log("blocks end")

        try_log("prev err begin")
        prev_err_block = self._push_try_err_block(err_bb)
        try_log("prev err end")

        try_log("finally stack begin")
        if stmt.finally_body:
            finally_stack = list(getattr(self, "_finally_stack", ()))
            finally_stack.append(stmt.finally_body)
            self._finally_stack = finally_stack
        try_log("finally stack end")
        try:
            try_log("body begin")
            self._emit_stmts(stmt.body)
            try_log("body end")
        finally:
            if stmt.finally_body:
                finally_stack = list(getattr(self, "_finally_stack", ()))
                if finally_stack:
                    finally_stack.pop()
                self._finally_stack = finally_stack

        try_log("normal exit begin")
        if not self._builder_block_is_terminated():
            if stmt.else_body:
                self._emit_stmts(stmt.else_body)
            if not self._builder_block_is_terminated():
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self._builder_block_is_terminated():
                    self.builder.branch(done_bb)
        try_log("normal exit end")

        try_log("restore err begin")
        self._restore_try_err_block(prev_err_block)
        try_log("restore err end")

        try_log("dispatch begin")
        self.builder.position_at_end(err_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("try.cur_exc"),
        )

        if not stmt.handlers:
            if stmt.finally_body:
                self._emit_stmts(stmt.finally_body)
            if not self._builder_block_is_terminated():
                outer = prev_err_block or self._ensure_fn_err_exit()
                self.builder.branch(outer)
            self.builder.position_at_end(done_bb)
            try_log("dispatch end no handlers")
            return

        def body_has_bare_raise(stmts: tuple) -> bool:
            for item in stmts:
                if isinstance(item, Raise):
                    if item.exc is None:
                        return True
                    continue
                if isinstance(item, If):
                    if body_has_bare_raise(item.body) or body_has_bare_raise(
                        item.else_body
                    ):
                        return True
                    continue
                if isinstance(item, While):
                    if body_has_bare_raise(item.body) or body_has_bare_raise(
                        item.else_body
                    ):
                        return True
                    continue
                if isinstance(item, For):
                    if body_has_bare_raise(item.body) or body_has_bare_raise(
                        item.else_body
                    ):
                        return True
                    continue
                if isinstance(item, Try):
                    if (
                        body_has_bare_raise(item.body)
                        or body_has_bare_raise(item.else_body)
                        or body_has_bare_raise(item.finally_body)
                    ):
                        return True
                    for nested_handler in item.handlers:
                        if body_has_bare_raise(nested_handler.body):
                            return True
                    continue
                if isinstance(item, With):
                    if body_has_bare_raise(item.body):
                        return True
            return False

        for i, h in enumerate(stmt.handlers):
            try_log("handler begin")
            test_bb = fn.append_basic_block(name=self._fresh(f"except.test.{i}"))
            body_bb = fn.append_basic_block(name=self._fresh(f"except.body.{i}"))
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
                        "!=",
                        match_i32,
                        ir.Constant(_I64, 0),
                        name=self._fresh("exc.matches.i1"),
                    )
                    cond = (
                        this
                        if cond is None
                        else self.builder.or_(
                            cond,
                            this,
                            name=self._fresh("exc.or"),
                        )
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
                    "!=",
                    match_i32,
                    ir.Constant(_I64, 0),
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
                self.builder.position_at_end(propagate_bb)
                outer = prev_err_block or self._ensure_fn_err_exit()
                self.builder.branch(outer)

            self.builder.position_at_end(body_bb)
            handler_exc = current_exc
            retain_handler_exc = h.name is not None or body_has_bare_raise(h.body)
            if retain_handler_exc:
                handler_exc = self._gc_retain(handler_exc)
            self.builder.call(self.runtime["py_clear_exception"], [])
            if h.name is not None:
                slot = self._alloca_in_entry(_CSTR, name=f"{h.name}.addr")
                self.builder.store(handler_exc, slot)
                self.env[h.name] = (slot, _CSTR, DynType(name="dyn"))
                # Mark `e` as an except-binding so a later `saved = e` GC-roots
                # `saved` (the surviving reference once the handler's retain is
                # released at handler end). Otherwise the borrowed-copy local is
                # not a frame root and the tracing collect sweeps the exception's
                # message. See gc-5backend-exception-referent-roots-no-libpython.md.
                binding_names = getattr(self, "_except_binding_names", None)
                if binding_names is not None:
                    binding_names.add(h.name)
            active_excs = list(getattr(self, "_active_handler_excs", ()))
            if retain_handler_exc:
                active_excs.append(handler_exc)
                self._active_handler_excs = active_excs
            if stmt.finally_body:
                finally_stack = list(getattr(self, "_finally_stack", ()))
                finally_stack.append(stmt.finally_body)
                self._finally_stack = finally_stack
            try:
                self._emit_stmts(h.body)
            finally:
                if stmt.finally_body:
                    finally_stack = list(getattr(self, "_finally_stack", ()))
                    if finally_stack:
                        finally_stack.pop()
                    self._finally_stack = finally_stack
                if retain_handler_exc:
                    active_excs.pop()
                    if active_excs:
                        self._active_handler_excs = active_excs
                    else:
                        self._active_handler_excs = ()
            if not self._builder_block_is_terminated():
                if stmt.finally_body:
                    self._emit_stmts(stmt.finally_body)
                if not self._builder_block_is_terminated():
                    if retain_handler_exc:
                        self._gc_release(handler_exc)
                    self.builder.branch(done_bb)

            if next_test_bb is not None:
                self.builder.position_at_end(next_test_bb)
            try_log("handler end")

        self.builder.position_at_end(done_bb)
        try_log("end")

    def _emit_builtin_exception_and_branch(
        self,
        exc_name: str,
        message: str,
        span: Optional[SourceSpan],
        *,
        open_dead_continuation: bool = False,
    ) -> None:
        tag = _BUILTIN_EXC_TAG[exc_name]
        msg_ptr = self._pooled_cstr_ptr(message, ".exc.msg")
        exc = self.builder.call(
            self.runtime["py_exc_new"],
            [ir.Constant(_I64, tag), msg_ptr],
            name=self._fresh(f"exc.{exc_name}"),
        )
        self.builder.call(self.runtime["py_raise"], [exc])
        frame_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("raise.frame.exc"),
        )
        self._emit_exception_frame(frame_exc, span)
        err_target = self._current_try_err_block()
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        cont = self.current_function.append_basic_block(
            name=self._fresh("raise.cont"),
        )
        self.builder.position_at_end(cont)
        if not open_dead_continuation:
            self.builder.unreachable()

    def _emit_attribute_error_if_null(
        self,
        value: ir.Value,
        attr_name: str,
        span: Optional[SourceSpan],
    ) -> None:
        missing = self.builder.icmp_signed(
            "==",
            value,
            ir.Constant(_CSTR, None),
            name=self._fresh(f"attr.{attr_name}.missing"),
        )
        err_bb = self.current_function.append_basic_block(
            name=self._fresh(f"attr.{attr_name}.err"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh(f"attr.{attr_name}.ok"),
        )
        self.builder.cbranch(missing, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        frame_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("attr.frame.exc"),
        )
        self._emit_exception_frame(frame_exc, span)
        err_target = self._current_try_err_block()
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)
        self.builder.position_at_end(ok_bb)

    def _emit_attribute_error_if_status_failed(
        self,
        status: ir.Value,
        attr_name: str,
        span: Optional[SourceSpan],
    ) -> None:
        failed = self.builder.icmp_signed(
            "<",
            status,
            ir.Constant(_I64, 0),
            name=self._fresh(f"setattr.{attr_name}.failed"),
        )
        err_bb = self.current_function.append_basic_block(
            name=self._fresh(f"setattr.{attr_name}.err"),
        )
        ok_bb = self.current_function.append_basic_block(
            name=self._fresh(f"setattr.{attr_name}.ok"),
        )
        self.builder.cbranch(failed, err_bb, ok_bb)
        self.builder.position_at_end(err_bb)
        pending = self.builder.call(
            self.runtime["py_err_occurred"],
            [],
            name=self._fresh(f"setattr.{attr_name}.pending"),
        )
        has_pending = self.builder.icmp_signed(
            "!=",
            pending,
            ir.Constant(_I64, 0),
            name=self._fresh(f"setattr.{attr_name}.has_pending"),
        )
        propagate_bb = self.current_function.append_basic_block(
            name=self._fresh(f"setattr.{attr_name}.propagate"),
        )
        raise_bb = self.current_function.append_basic_block(
            name=self._fresh(f"setattr.{attr_name}.raise"),
        )
        self.builder.cbranch(has_pending, propagate_bb, raise_bb)

        self.builder.position_at_end(propagate_bb)
        frame_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("attr.status.frame.exc"),
        )
        self._emit_exception_frame(frame_exc, span)
        err_target = self._current_try_err_block()
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

        self.builder.position_at_end(raise_bb)
        self._emit_builtin_exception_and_branch(
            "AttributeError",
            f"object has no attribute {attr_name}",
            span,
        )
        self.builder.position_at_end(ok_bb)

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
                return self._pooled_cstr_ptr("", ".exc.msg")
            first = msg_expr
            if isinstance(first, StrLit):
                return self._pooled_cstr_ptr(first.value, ".exc.msg")
            msg_obj = self._emit_as_object(first)
            msg_str = msg_obj
            if not isinstance(first.ty, StrType):
                msg_str = self.builder.call(
                    self.runtime["py_obj_str"],
                    [msg_obj],
                    name=self._fresh("exc.msg.obj"),
                )
            return self.builder.call(
                self.runtime["py_str_utf8"],
                [msg_str],
                name=self._fresh("exc.msg.cstr"),
            )

        if isinstance(exc_expr, Call) and isinstance(exc_expr.func, Name):
            cls_name = exc_expr.func.ident
            tag = _builtin_exc_tag_or_missing(cls_name)
            if tag >= 0:
                msg_ptr = _message_cstr(exc_expr.args, exc_expr.kwargs)
                return self.builder.call(
                    self.runtime["py_exc_new"],
                    [ir.Constant(_I64, tag), msg_ptr],
                    name=self._fresh(f"exc.{cls_name}"),
                )
            info = self.class_lowering.classes.get(cls_name)
            if info is not None:
                # Construct the instance properly so the user __init__ runs
                # (sets instance attrs, and super().__init__ stores the
                # message/args), then raise that instance. The old
                # py_exc_new_with_class(cls, args[0]) captured only args[0] as a
                # message and skipped __init__, losing attributes and
                # mis-taking a non-message first arg as the message. kwargs are
                # rare for exceptions and stay on the message-only path.
                if not exc_expr.kwargs:
                    return self.class_lowering.emit_instantiate(
                        cls_name, exc_expr.args, self
                    )
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
            and exc_expr.ident not in self.env
        ):
            cls_name = exc_expr.ident
            tag = _builtin_exc_tag_or_missing(cls_name)
            if tag >= 0:
                return self.builder.call(
                    self.runtime["py_exc_new"],
                    [ir.Constant(_I64, tag), _message_cstr(())],
                    name=self._fresh(f"exc.{cls_name}"),
                )
        if (
            isinstance(exc_expr, Name)
            and exc_expr.ident not in self.env
        ):
            cls_name = exc_expr.ident
            info = self.class_lowering.classes.get(cls_name)
            if info is None:
                return self._emit_as_object(exc_expr)
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
            tag = _builtin_exc_tag_or_missing(expr.ident)
            if tag >= 0:
                return self.builder.call(
                    self.runtime["py_exc_builtin_class"],
                    [ir.Constant(_I64, tag)],
                    name=self._fresh(f"exc.cls.{expr.ident}"),
                )
            # User class? Look up in class_lowering.
            info = self.class_lowering.classes.get(expr.ident)
            if info is not None:
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
                    cpy_gv,
                    name=self._fresh(f"exc.cpy.{expr.ident}"),
                )
            # Fall back to the generic ``Exception`` base so the except
            # clause still compiles. Runtime semantics are broader than
            # CPython's, but the goal here is self-host compile coverage
            # — the narrow type match is recovered once the referenced
            # exception class reaches pcc's ClassInfo registry.
            return self.builder.call(
                self.runtime["py_exc_builtin_class"],
                [ir.Constant(_I64, 1)],
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
            return self.builder.call(
                self.runtime["py_exc_builtin_class"],
                [ir.Constant(_I64, 1)],
                name=self._fresh(f"exc.cls.attr_fallback.{expr.name}"),
            )
        raise NotImplementedError(
            f"Layer 1 except-clause class expression {type(expr).__name__} "
            "not supported"
        )

    def _emit_exception_frame(
        self,
        exc: ir.Value,
        span: Optional[SourceSpan],
    ) -> None:
        if span is None:
            return
        func_name = "<module>"
        if self.current_func_def is not None:
            func_name = self.current_func_def.name
        func_ptr = self._pooled_cstr_ptr(func_name, ".tb.func")
        file_name = span.file or "<unknown>"
        file_ptr = self._pooled_cstr_ptr(file_name, ".tb.file")
        self.builder.call4_i32(
            self.runtime["py_exc_append_frame"],
            exc,
            func_ptr,
            file_ptr,
            int(span.line),
        )

    def _emit_post_call_err_check(
        self,
        span: Optional[SourceSpan] = None,
    ) -> None:
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
        err_target = self._current_try_err_block()
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
            err_fn,
            [],
            name=self._fresh("err.flag"),
        )
        cmp = self.builder.icmp_signed(
            "!=",
            is_err,
            ir.Constant(_I64, 0),
            name=self._fresh("err.cmp"),
        )
        parent_fn = self.current_function
        cont = parent_fn.append_basic_block(name=self._fresh("call.cont"))
        if span is None:
            self.builder.cbranch(cmp, err_target, cont)
        else:
            frame_bb = self._ensure_post_call_frame_block(err_target, span)
            self.builder.cbranch(cmp, frame_bb, cont)
        self.builder.position_at_end(cont)

    def _ensure_post_call_frame_block(
        self,
        err_target: ir.Block,
        span: SourceSpan,
    ) -> ir.Block:
        parent_fn = self.current_function
        func_name = "<module>"
        if self.current_func_def is not None:
            func_name = self.current_func_def.name
        file_name = span.file or "<unknown>"
        key = (
            parent_fn.name,
            err_target.name,
            func_name,
            file_name,
            int(span.line),
        )
        existing = self._post_call_frame_blocks.get(key)
        if existing is not None:
            return existing

        frame_bb = parent_fn.append_basic_block(
            name=self._fresh("err.frame"),
        )
        self._post_call_frame_blocks[key] = frame_bb
        save_block = self.builder._block
        self.builder.position_at_end(frame_bb)
        exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("err.frame.exc"),
        )
        self._emit_exception_frame(exc, span)
        self.builder.branch(err_target)
        self.builder.position_at_end(save_block)
        return frame_bb

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
            if fn_name == "main":
                exc = self.builder.call(
                    self.runtime["py_current_exception"],
                    [],
                    name=self._fresh("unhandled.exc"),
                )
                self.builder.call(
                    self.runtime["py_exc_print_unhandled"],
                    [exc],
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
        for name in sorted(getattr(self, "_gc_rooted_local_names", set())):
            slot = self.env.get(name)
            if slot is None:
                continue
            alloca, ir_ty, _decl_ty = slot
            if not isinstance(ir_ty, ir.PointerType):
                continue
            if not self._ir_type_matches(ir_ty, _CSTR):
                continue
            self._patch_fn_err_exit_gc_root_leave(name, alloca)
        self.builder.position_at_end(save_block)
        return err_bb
