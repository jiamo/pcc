"""Call expression lowering for L1CodeGen."""

from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Arg,
    Attr,
    BoolLit,
    BoolType,
    Call,
    DictExpr,
    DictType,
    DynType,
    Expr,
    FloatLit,
    FloatType,
    IntLit,
    IntType,
    Lambda,
    ListExpr,
    ListType,
    Name,
    NoneLit,
    NoneType,
    Slice,
    StrLit,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
)
from . import marshal
from .errors import L1CodegenError

_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = ir.IntType(8).as_pointer()
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
_CPY_BUILTIN_FALLBACK = frozenset(
    {
        "open",
        "iter",
        "next",
        "sorted",
        "super",
        "property",
        "classmethod",
        "staticmethod",
        "hasattr",
        "hash",
        "id",
        "repr",
        "ord",
        "chr",
        "dir",
        "vars",
        "locals",
        "globals",
    }
)


def _ascii_decimal_digit(ch: str) -> int:
    c = ord(ch)
    if 48 <= c <= 57:
        return c - 48
    return -1


def _parse_simple_decimal_float(s: str):
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

    if i != len(s):
        return None
    return sign * value


def _maybe_fold_str_to_float(s: str):
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


def _call_name_ident(expr: object):
    if isinstance(expr, Name):
        return expr.ident
    try:
        return expr.ident
    except AttributeError:
        return None


def _call_attr_name(expr: object):
    if isinstance(expr, Attr):
        return expr.name
    try:
        return expr.name
    except AttributeError:
        return None


def _call_attr_obj(expr: object):
    if isinstance(expr, Attr):
        return expr.obj
    try:
        return expr.obj
    except AttributeError:
        return None


def _call_is_attr(expr: object) -> bool:
    return _call_attr_name(expr) is not None and _call_attr_obj(expr) is not None


def _replace_arg_with_none_default(arg):
    return Arg(
        name=arg.name,
        annotation=arg.annotation,
        default=NoneLit(
            span=None,
            ty=NoneType(name="None"),
        ),
        kind=arg.kind,
        has_default=True,
    )


class CallExpressionLoweringMixin:
    def _expr_span_or_none(self, expr):
        try:
            return expr.span
        except AttributeError:
            return None

    def _function_arg_ir_type_or_none(self, fn, index: int, ir_arg):
        try:
            return ir_arg.type
        except AttributeError:
            pass
        try:
            fty = fn.function_type
            args = fty.args
            if index < len(args):
                return args[index]
        except AttributeError:
            pass
        try:
            fty = fn.ftype
            args = fty.args
            if index < len(args):
                return args[index]
        except AttributeError:
            pass
        return None

    def _func_has_click_decorator(self, fd) -> bool:
        decorators = self._func_decorators(fd)
        i = 0
        while i < len(decorators):
            d = decorators[i]
            if self._decorator_is_noop_whitelist(d):
                qn = self._decorator_qualname(d) or ""
                if qn.startswith("click."):
                    return True
            i += 1
        return False

    def _materialize_class_init_call_args(self, args: tuple) -> tuple:
        out = []
        i = 0
        while i < len(args):
            arg = args[i]
            if isinstance(arg, Call):
                raw = self._emit_expr(arg)
                base = "__pcc_ctor_arg_" + str(i) + "_" + str(len(self.env))
                name = base
                suffix = 0
                while name in self.env:
                    suffix += 1
                    name = base + "_" + str(suffix)
                init_null = isinstance(raw.type, ir.PointerType)
                alloca = self._alloca_in_entry(
                    raw.type,
                    name=name + ".addr",
                    init_null=init_null,
                )
                self.builder.store(raw, alloca)
                self.env[name] = (alloca, raw.type, arg.ty)
                out.append(
                    Name(
                        span=self._expr_span_or_none(arg),
                        ty=arg.ty,
                        ident=name,
                    )
                )
            else:
                out.append(arg)
            i += 1
        return tuple(out)

    def _maybe_emit_known_dunder_class_constructor(self, expr: Call):
        func = expr.func
        if not isinstance(func, Attr):
            return None
        if func.name != "__class__":
            return None
        try:
            class_name = self._class_hint_for_expr(func.obj)
        except Exception:
            class_name = None
        if class_name is None:
            if isinstance(func.obj, Name) and func.obj.ident == "self":
                class_name = self._self_receiver_class_name()
        if class_name is None:
            return None
        try:
            classes = self.class_lowering.classes
        except AttributeError:
            return None
        if class_name not in classes:
            return None
        return self._emit_call(
            Call(
                span=self._expr_span_or_none(expr),
                ty=expr.ty,
                func=Name(
                    span=self._expr_span_or_none(func),
                    ty=DynType(name="dyn"),
                    ident=class_name,
                ),
                args=expr.args,
                kwargs=expr.kwargs,
            )
        )

    def _emit_range_value_call(self, expr: Call) -> ir.Value:
        if expr.kwargs:
            raise NotImplementedError("Layer 1 range() has no keyword args")
        if len(expr.args) == 1:
            start_val: ir.Value = ir.Constant(_I64, 0)
            stop_val = self._emit_expr_as_i64(expr.args[0])
            step_val: ir.Value = ir.Constant(_I64, 1)
        elif len(expr.args) == 2:
            start_val = self._emit_expr_as_i64(expr.args[0])
            stop_val = self._emit_expr_as_i64(expr.args[1])
            step_val = ir.Constant(_I64, 1)
        elif len(expr.args) == 3:
            start_val = self._emit_expr_as_i64(expr.args[0])
            stop_val = self._emit_expr_as_i64(expr.args[1])
            step_val = self._emit_expr_as_i64(expr.args[2])
        else:
            raise L1CodegenError(f"range() takes 1-3 args; got {len(expr.args)}")

        out = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("range.list"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="range.value.idx.addr")
        self.builder.store(start_val, idx_slot)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("range.value.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("range.value.body"))
        step_bb = fn.append_basic_block(name=self._fresh("range.value.step"))
        end_bb = fn.append_basic_block(name=self._fresh("range.value.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("range.value.i"))
        zero64 = ir.Constant(_I64, 0)
        step_pos = self.builder.icmp_signed(
            ">", step_val, zero64, name=self._fresh("range.value.step.pos")
        )
        cond_pos = self.builder.icmp_signed(
            "<", cur, stop_val, name=self._fresh("range.value.fwd")
        )
        cond_neg = self.builder.icmp_signed(
            ">", cur, stop_val, name=self._fresh("range.value.bwd")
        )
        keep = self.builder.select(
            step_pos, cond_pos, cond_neg, name=self._fresh("range.value.keep")
        )
        self.builder.cbranch(keep, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        item = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("range.value.item"),
        )
        self.builder.call(
            self.runtime["py_list_append"],
            [out, item],
            name=self._fresh("range.value.append"),
        )
        self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        next_val = self.builder.add(
            cur,
            step_val,
            name=self._fresh("range.value.next"),
        )
        self.builder.store(next_val, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        return out

    def _emit_call(self, expr: Call) -> ir.Value:
        if self.module.name == "pcc.parse.py_lift":
            import os
            import sys

            if os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE"):
                try:
                    func_type = type(expr.func).__name__
                except AttributeError:
                    func_type = ""
                try:
                    raw_ident = expr.func.ident
                except AttributeError:
                    raw_ident = "<missing>"
                try:
                    raw_name = expr.func.name
                except AttributeError:
                    raw_name = "<missing>"
                sys.stderr.write(
                    "debug: py_lift_call func_type="
                    + str(func_type)
                    + " ident="
                    + str(raw_ident)
                    + " name="
                    + str(raw_name)
                    + "\n"
                )
        func_expr = expr.func
        func_name = _call_name_ident(func_expr)
        func_attr_name = _call_attr_name(func_expr)
        func_attr_obj = _call_attr_obj(func_expr)
        if (
            (func_name == "cast")
            or (
                func_attr_name == "cast"
                and _call_name_ident(func_attr_obj) == "typing"
            )
        ) and len(expr.args) == 2:
            return self._emit_expr(expr.args[1])

        native_sys_exit_call = self._emit_native_sys_exit_call(expr)
        if native_sys_exit_call is not None:
            return native_sys_exit_call
        native_replace_call = self._emit_native_dataclasses_replace_call(expr)
        if native_replace_call is not None:
            return native_replace_call
        if func_attr_name == "__class__":
            dunder_class_ctor = self._maybe_emit_known_dunder_class_constructor(expr)
            if dunder_class_ctor is not None:
                return dunder_class_ctor
        if _call_is_attr(func_expr):
            return self._emit_method_call(expr)
        if func_name is None:
            fn_val = self._emit_expr(func_expr)
            if fn_val in getattr(self, "_cpy_values", ()):
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val,
                        "expr",
                        expr.args,
                        expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, "expr", expr.args)
            kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
            arg_exprs = expr.args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_owned = not self._is_starred_unpack(arg_exprs)
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                self._expr_span_or_none(expr),
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [fn_val, args_tuple, kwargs_obj],
                name=self._fresh("obj.call"),
            )
            if args_owned:
                self._gc_release(args_tuple)
            if expr.kwargs:
                self._gc_release(kwargs_obj)
            return result
        name = func_name
        unsafe_intrinsic = self._unsafe_intrinsic_for_name(name)
        if unsafe_intrinsic is not None:
            return self._emit_unsafe_intrinsic_call(unsafe_intrinsic, expr)
        if name in _BUILTIN_EXC_TAG and name not in self.env:
            return self._build_exception_value(expr)
        if name == "__await__":
            return self._emit_await_expr(expr)
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
        if name == "__import__" and len(expr.args) == 1 and not expr.kwargs:
            imported = self._native_importlib_literal_module(expr)
            if imported is not None:
                return self._emit_native_module_placeholder(imported)
            if isinstance(expr.args[0], StrLit):
                self._emit_builtin_exception_and_branch(
                    "ImportError",
                    f"No module named {expr.args[0].value!r}",
                    self._expr_span_or_none(expr),
                )
                return ir.Constant(_CSTR, None)
        if name == "open":
            native_open = self._emit_native_open_call(expr)
            if native_open is not None:
                return native_open
        native_fileinput = self._emit_native_fileinput_call(expr)
        if native_fileinput is not None:
            return native_fileinput
        # Builtins below don't support kwargs — reject early.
        if expr.kwargs and name in ("range", "xrange", "len", "str", "isinstance"):
            raise NotImplementedError(
                f"Layer 1 builtin {name}() does not accept keyword args"
            )
        if name in ("range", "xrange"):
            return self._emit_range_value_call(expr)
        if name in ("_walrus", "__walrus__"):
            return self._emit_walrus(expr)
        if name == "__pcc_format_spec":
            result = self._emit_format_spec_builtin(expr)
            if result is not None:
                return result
        builtin_value = self._native_builtin_value_for_name(name)
        if builtin_value == "builtins.int" and 1 <= len(expr.args) <= 2:
            result = self._maybe_emit_int_builtin(expr)
            if result is not None:
                return result
        if builtin_value in ("math.floor", "math.sqrt", "math.pow"):
            result = self._emit_native_math_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value in ("re.match", "re.search"):
            result = self._emit_native_re_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value is not None:
            result = self._emit_native_builtin_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value is not None and builtin_value.startswith("gc."):
            result = self._emit_native_gc_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value is not None and builtin_value.startswith("weakref."):
            result = self._emit_native_weakref_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value in ("asyncio.run", "asyncio.sleep"):
            result = self._emit_native_asyncio_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value is not None and builtin_value.startswith("threading."):
            result = self._emit_native_threading_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value is not None and builtin_value.startswith(
            "pcc.virtual_thread."
        ):
            result = self._emit_native_virtual_thread_value_call(
                builtin_value,
                expr.args,
                expr.kwargs,
            )
            if result is not None:
                return result
        if builtin_value == "enum.auto" and not expr.args and not expr.kwargs:
            return self._emit_none_literal()
        if name == "len":
            return self._emit_len_call(expr)
        if name == "str":
            return self._emit_str_builtin(expr)
        if name in ("bytes", "bytearray", "memoryview"):
            result = self._emit_bytes_family_builtin(expr, name)
            if result is not None:
                return result
        if name == "object" and not expr.args and not expr.kwargs:
            # pcc only needs bare object() today as a unique identity sentinel.
            return self.builder.call(
                self.runtime["py_dict_new"],
                [],
                name=self._fresh("object.sentinel"),
            )
        if name == "dict":
            return self._emit_dict_builtin(expr)
        if name == "list" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("list.new"),
            )
        if name == "set" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_set_new"],
                [],
                name=self._fresh("set.new"),
            )
        if name == "tuple" and not expr.args and not expr.kwargs:
            return self.builder.call(
                self.runtime["py_tuple_new"],
                [ir.Constant(_I64, 0)],
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
                                self.runtime["py_dict_new"],
                                [],
                                name=self._fresh("field.dict"),
                            )
                        if v.ident == "set":
                            return self.builder.call(
                                self.runtime["py_set_new"],
                                [],
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
                    return self._emit_call(
                        Call(
                            span=self._expr_span_or_none(expr),
                            ty=expr.ty,
                            func=v,
                            args=(),
                            kwargs=(),
                        )
                    )
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
            and self.current_class is not None
            and self.current_method_kind == "classmethod"
        ):
            args = expr.args
            if expr.kwargs:
                # Walk the class's MRO looking for an ``__init__`` —
                # dataclass inheritance means SSAConstant may inherit
                # SSAValue's synthesized init. ``_resolve_method_mro``
                # already handles that walk.
                mro_info = self._resolve_method_mro(
                    self.current_class.name,
                    "__init__",
                )
                init_fd = None
                if mro_info is not None:
                    init_fd = self.class_lowering._find_method_def(
                        mro_info.name,
                        "__init__",
                    )
                if init_fd is not None:
                    args = tuple(
                        self._resolve_call_kwargs(
                            expr.args,
                            expr.kwargs,
                            init_fd.args,
                            skip_self=True,
                        )
                    )
                else:
                    args = expr.args  # fallthrough to original
            return self.class_lowering.emit_instantiate(
                self.current_class.name,
                args,
                self,
            )
        if name in ("min", "max") and not expr.kwargs and len(expr.args) == 2:
            return self._emit_min_max_builtin(expr, name)
        if name in ("min", "max") and not expr.kwargs and len(expr.args) >= 3:
            result = self._emit_min_max_variadic(expr, name)
            if result is not None:
                return result
        if name in ("min", "max") and len(expr.args) == 1:
            # Allow the ``default=`` kwarg: _maybe_emit_min_max_iter consumes it
            # (seeding the accumulator on an empty iterable) and returns None for
            # any other kwarg (e.g. key=, which falls through to libpython).
            result = self._maybe_emit_min_max_iter(expr, name)
            if result is not None:
                return result
        if name == "pow" and not expr.kwargs and len(expr.args) == 2:
            lhs = self._emit_expr(expr.args[0])
            rhs = self._emit_expr(expr.args[1])
            if isinstance(expr.ty, FloatType):
                return self._emit_binop_float("**", lhs, rhs)
            return self._emit_binop_int(
                "**",
                self._to_int64(lhs, expr.args[0].ty),
                self._to_int64(rhs, expr.args[1].ty),
            )
        if name == "pow" and not expr.kwargs and len(expr.args) == 3:
            # 3-arg pow(b, e, mod): modular exponentiation. Box the three int
            # operands and call the runtime square-and-multiply helper, which
            # reduces mod ``mod`` every step (never materialises b**e, so it is
            # usable for crypto-size exponents). Result is a boxed int < mod;
            # marshal to the expr's int representation.
            b_obj = self._emit_as_object(expr.args[0])
            e_obj = self._emit_as_object(expr.args[1])
            m_obj = self._emit_as_object(expr.args[2])
            res = self.builder.call(
                self.runtime["py_int_pow_mod"],
                [b_obj, e_obj, m_obj],
                name=self._fresh("pow.mod"),
            )
            self._emit_post_call_err_check(self._expr_span_or_none(expr))
            return marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                res,
                IntType(name="int"),
            )
        if name == "divmod" and not expr.kwargs and len(expr.args) == 2:
            if isinstance(expr.args[0].ty, FloatType) or isinstance(
                expr.args[1].ty, FloatType
            ):
                # float divmod: (floor(a/b), a - floor(a/b)*b), both floats,
                # matching CPython (divmod(7.5, 2) == (3.0, 1.5)).
                fa = self._to_double(self._emit_expr(expr.args[0]), expr.args[0].ty)
                fb = self._to_double(self._emit_expr(expr.args[1]), expr.args[1].ty)
                fq = self.builder.call(
                    self._get_floor_intrinsic(),
                    [self.builder.fdiv(fa, fb, name=self._fresh("divmod.f.div"))],
                    name=self._fresh("divmod.f.floor"),
                )
                fr = self.builder.fsub(
                    fa,
                    self.builder.fmul(fq, fb, name=self._fresh("divmod.f.qb")),
                    name=self._fresh("divmod.f.rem"),
                )
                fout = self.builder.call(
                    self.runtime["py_tuple_new"],
                    [ir.Constant(_I64, 2)],
                    name=self._fresh("divmod.f.tuple"),
                )
                fq_obj = self.builder.call(
                    self.runtime["py_float_from_f64"],
                    [fq],
                    name=self._fresh("divmod.f.q"),
                )
                fr_obj = self.builder.call(
                    self.runtime["py_float_from_f64"],
                    [fr],
                    name=self._fresh("divmod.f.r"),
                )
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [fout, ir.Constant(_I64, 0), fq_obj],
                )
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [fout, ir.Constant(_I64, 1), fr_obj],
                )
                self._gc_release(fq_obj)
                self._gc_release(fr_obj)
                return fout
            lhs = self._emit_expr_as_i64(expr.args[0])
            rhs = self._emit_expr_as_i64(expr.args[1])
            q_val = self._python_floordiv_i64(lhs, rhs)
            r_val = self._python_mod_i64(lhs, rhs)
            out = self.builder.call(
                self.runtime["py_tuple_new"],
                [ir.Constant(_I64, 2)],
                name=self._fresh("divmod.tuple"),
            )
            q_obj = self.builder.call(
                self.runtime["py_int_from_i64"],
                [q_val],
                name=self._fresh("divmod.q"),
            )
            r_obj = self.builder.call(
                self.runtime["py_int_from_i64"],
                [r_val],
                name=self._fresh("divmod.r"),
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [out, ir.Constant(_I64, 0), q_obj],
            )
            self.builder.call(
                self.runtime["py_tuple_set_item"],
                [out, ir.Constant(_I64, 1), r_obj],
            )
            self._gc_release(q_obj)
            self._gc_release(r_obj)
            return out
        if name == "abs" and len(expr.args) == 1:
            return self._emit_abs_builtin(expr)
        if name in ("bin", "hex", "oct") and len(expr.args) == 1 and not expr.kwargs:
            # bin()/hex()/oct() -> base-prefixed string via the runtime; the
            # arg is boxed so int (tagged/heap) and bool all route natively.
            arg_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_builtin_" + name],
                [arg_obj],
                name=self._fresh(name),
            )
            self._emit_post_call_err_check(self._expr_span_or_none(expr))
            return result
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
        if name == "iter":
            result = self._maybe_emit_iter_builtin(expr)
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
        if name == "format" and not expr.kwargs and 1 <= len(expr.args) <= 2:
            value_obj = self._emit_as_object(expr.args[0])
            if len(expr.args) == 2:
                spec_obj = self._emit_as_object(expr.args[1])
            else:
                spec_obj = self._emit_str_literal("")
            result = self.builder.call(
                self.runtime["py_obj_format"],
                [value_obj, spec_obj],
                name=self._fresh("format.obj"),
            )
            self._emit_post_call_err_check(self._expr_span_or_none(expr))
            return result
        if name == "chr" and len(expr.args) == 1 and not expr.kwargs:
            v = self._emit_expr(expr.args[0])
            ty = expr.args[0].ty
            if isinstance(ty, BoolType) and self._ir_type_matches(v.type, _I1):
                v = self.builder.zext(
                    v,
                    _I64,
                    name=self._fresh("chr.from_bool"),
                )
            elif isinstance(ty, IntType):
                v = self._to_int64(v, ty)
            else:
                v = None
            if v is not None:
                return self.builder.call(
                    self.runtime["py_chr_from_i64"],
                    [v],
                    name=self._fresh("chr"),
                )
        if name == "float" and len(expr.args) == 1:
            arg = expr.args[0]
            ty = arg.ty
            if isinstance(ty, FloatType):
                return self._emit_expr(arg)
            if isinstance(ty, (IntType, BoolType)):
                v = self._emit_expr(arg)
                return self._to_double(v, ty)
            # Issue 11.A.2: ``float("inf")`` / ``float("-inf")`` /
            # ``float("nan")`` and other StrLit args fold to a native
            # constant at codegen time so we don't pull libpython for
            # what should be a compile-time literal.
            if isinstance(arg, StrLit):
                folded = _maybe_fold_str_to_float(arg.value)
                if folded is not None:
                    return ir.Constant(_DOUBLE, folded)
            # DynType receiver — unbox via the pcc-native runtime helper
            # (``py_float_to_f64`` for native dyn pointers, ``py_cpy_to_f64``
            # for already-CPython refs). Without this branch ``float(<dyn>)``
            # falls through to ``py_cpy_import('builtins') + py_cpy_getattr +
            # py_cpy_call1`` which reintroduces libpython linkage.
            if isinstance(ty, DynType):
                v = self._emit_expr(arg)
                if isinstance(v.type, ir.PointerType):
                    return self._to_double(v, ty)
        if name == "round" and not expr.kwargs and 1 <= len(expr.args) <= 2:
            raw = self._emit_expr(expr.args[0])
            value = self._to_double(raw, expr.args[0].ty)
            if len(expr.args) == 1:
                # round(x) uses banker's rounding (round half to even) via
                # libm rint(), matching CPython (round(2.5)==2, round(0.5)==0).
                rounded = self.builder.call(
                    self._get_rint_function(),
                    [value],
                    name=self._fresh("round.rint"),
                )
                as_i64 = self.builder.fptosi(
                    rounded,
                    _I64,
                    name=self._fresh("round.i64"),
                )
                return self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [as_i64],
                    name=self._fresh("round.int"),
                )
            digits_raw = self._emit_expr(expr.args[1])
            digits_i64 = self._to_int64(digits_raw, expr.args[1].ty)
            digits_f = self.builder.sitofp(
                digits_i64,
                _DOUBLE,
                name=self._fresh("round.ndigits.f64"),
            )
            scale = self.builder.call(
                self._get_pow_function(),
                [ir.Constant(_DOUBLE, 10.0), digits_f],
                name=self._fresh("round.scale"),
            )
            scaled = self.builder.fmul(value, scale, name=self._fresh("round.mul"))
            # banker's rounding (round half to even) via libm rint(), matching
            # CPython's round(x, ndigits) tie behaviour.
            rounded = self.builder.call(
                self._get_rint_function(),
                [scaled],
                name=self._fresh("round.scaled.rint"),
            )
            out = self.builder.fdiv(rounded, scale, name=self._fresh("round.div"))
            return self.builder.call(
                self.runtime["py_float_from_f64"],
                [out],
                name=self._fresh("round.float"),
            )
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
            if self.module.name == "pcc.parse.py_lift":
                import os
                import sys

                if os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE"):
                    sys.stderr.write(
                        "debug: call_tuple_branch args_len="
                        + str(len(expr.args))
                        + "\n"
                    )
            result = self._maybe_emit_tuple_builtin(expr)
            if self.module.name == "pcc.parse.py_lift":
                import os
                import sys

                if os.environ.get("PCC_DEBUG_BOOTSTRAP_TRACE"):
                    sys.stderr.write(
                        "debug: call_tuple_branch result="
                        + str(result is not None)
                        + "\n"
                    )
            if result is not None:
                return result
        if name == "dict" and len(expr.args) <= 1:
            result = self._maybe_emit_dict_builtin(expr)
            if result is not None:
                return result
        if name == "staticmethod" and len(expr.args) == 1 and not expr.kwargs:
            return self._emit_expr_with_native_callable_values(expr.args[0])
        if name == "property" and 1 <= len(expr.args) <= 3 and not expr.kwargs:
            prop_args: list[ir.Value] = []
            for arg in expr.args:
                value = self._emit_expr_with_native_callable_values(arg)
                prop_args.append(
                    marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        value,
                        arg.ty,
                    )
                )
            while len(prop_args) < 3:
                prop_args.append(ir.Constant(_CSTR, None))
            return self.builder.call(
                self.runtime["py_property_new"],
                prop_args,
                name=self._fresh("property"),
            )
        if name == "classmethod" and len(expr.args) == 1 and not expr.kwargs:
            func_obj = self._emit_expr_with_native_callable_values(expr.args[0])
            return self.builder.call(
                self.runtime["py_classmethod_new"],
                [func_obj],
                name=self._fresh("classmethod"),
            )
        if name == "sorted" and len(expr.args) == 1:
            # sorted(x) or sorted(x, reverse=<bool const>). A constant
            # reverse=True reverses the result list in place after sorting.
            # key= (first-class function) and a non-constant reverse fall
            # through to the libpython path.
            reverse_const = None
            key_lambda = None
            other_kwarg = False
            for kw_name, kw_val in (expr.kwargs or ()):
                if kw_name == "reverse" and isinstance(kw_val, BoolLit):
                    reverse_const = bool(kw_val.value)
                elif kw_name == "key" and isinstance(kw_val, Lambda):
                    key_lambda = kw_val
                else:
                    other_kwarg = True
            # sorted(xs, key=<simple attr/index lambda>): inline the key
            # extraction (no first-class-function boxing). A non-simple key
            # lambda (or any other kwarg) yields None / falls through to the
            # libpython path — we must NOT run the plain py_obj_sorted below,
            # which would silently ignore the key.
            if key_lambda is not None:
                if not other_kwarg:
                    keyed = self._emit_sorted_with_key_lambda(
                        expr, key_lambda, reverse_const
                    )
                    if keyed is not None:
                        return keyed
            elif not other_kwarg:
                # Custom-class elements with a user __lt__: the runtime
                # comparison primitive (py_obj_cmp_threeway, behind
                # py_obj_sorted) pointer-compares instances and never
                # dispatches a Python __lt__, so sorted() over such a list
                # would return it unordered. Route to the SAME static
                # __lt__ insertion sort that list.sort() uses, on a COPY
                # (sorted() returns a new list). See docs/investigations/
                # sorted-min-max-custom-lt-not-used-no-libpython.md.
                elem_hint = self._list_elem_class_hint_for_expr(expr.args[0])
                if elem_hint is None and isinstance(expr.args[0], Name):
                    elem_hint = self.env_list_elem_class_hint.get(
                        expr.args[0].ident
                    )
                if (
                    elem_hint is not None
                    and self._resolve_method_mro(elem_hint, "__lt__")
                    is not None
                ):
                    src_obj = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        self._emit_expr(expr.args[0]),
                        expr.args[0].ty,
                    )
                    new_list = self.builder.call(
                        self.runtime["py_list_new"],
                        [ir.Constant(_I64, 0)],
                        name=self._fresh("sorted.lt.copy"),
                    )
                    self.builder.call(
                        self.runtime["py_list_extend"],
                        [new_list, src_obj],
                    )
                    elem_ty = (
                        expr.args[0].ty.elem
                        if isinstance(expr.args[0].ty, ListType)
                        else DynType(name="dyn")
                    )
                    self._emit_list_sort_with_dunder_lt(
                        new_list, elem_hint, elem_ty
                    )
                    if reverse_const:
                        self.builder.call(
                            self.runtime["py_list_reverse"],
                            [new_list],
                        )
                    return new_list
                src_val = self._emit_expr(expr.args[0])
                src_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    src_val,
                    expr.args[0].ty,
                )
                result = self.builder.call(
                    self.runtime["py_obj_sorted"],
                    [src_obj],
                    name=self._fresh("sorted"),
                )
                if reverse_const:
                    self.builder.call(
                        self.runtime["py_list_reverse"],
                        [result],
                    )
                return result
        if name == "reversed" and len(expr.args) == 1 and not expr.kwargs:
            return self._emit_reversed_builtin(expr)
        if name == "repr" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_repr"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("repr"),
            )
        if name == "ascii" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_obj_ascii"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("ascii"),
            )
        if name == "hash" and len(expr.args) == 1:
            result = self.builder.call(
                self.runtime["py_obj_hash"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("hash"),
            )
            self._emit_post_call_err_check(self._expr_span_or_none(expr))
            return result
        if name == "id" and len(expr.args) == 1:
            v = self._emit_as_object(expr.args[0])
            return self.builder.ptrtoint(
                v,
                _I64,
                name=self._fresh("id"),
            )
        if name == "hasattr" and len(expr.args) == 2:
            native_module_hasattr = self._maybe_emit_native_module_hasattr(expr)
            if native_module_hasattr is not None:
                return native_module_hasattr
            # ``hasattr(x, "name")`` — pcc doesn't distinguish "missing"
            # from "present but None" without full dunder support, but
            # for the common usage (gate on attribute existence) the
            # presence-check via py_obj_getattr returning non-NULL
            # works on pcc-native classes.
            if self._expr_looks_cpython(expr.args[0]):
                fn_val = self._load_cpython_builtin("hasattr")
                got = self._emit_cpy_func_call(
                    fn_val,
                    "hasattr",
                    tuple(expr.args),
                )
                as_i32 = self.builder.call(
                    self.runtime["py_cpy_truthy"],
                    [got],
                    name=self._fresh("hasattr.cpy.i32"),
                )
                return self.builder.icmp_signed(
                    "!=",
                    as_i32,
                    ir.Constant(_I32, 0),
                    name=self._fresh("hasattr.cpy.i1"),
                )
            obj = self._emit_as_object(expr.args[0])
            nm = expr.args[1]
            if isinstance(nm, StrLit):
                name_ptr = self._attr_name_ptr(nm.value)
            else:
                nv = self._emit_expr(nm)
                n_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    nv,
                    nm.ty,
                )
                name_ptr = self.builder.call(
                    self.runtime["py_str_utf8"],
                    [n_obj],
                    name=self._fresh("hasattr.name"),
                )
            got = self.builder.call(
                self.runtime["py_obj_getattr"],
                [obj, name_ptr],
                name=self._fresh("hasattr.got"),
            )
            null = ir.Constant(_CSTR, None)
            present = self.builder.icmp_signed(
                "!=",
                got,
                null,
                name=self._fresh("hasattr.i1"),
            )
            parent_fn = self.current_function
            missing_bb = parent_fn.append_basic_block(
                name=self._fresh("hasattr.missing"),
            )
            present_bb = parent_fn.append_basic_block(
                name=self._fresh("hasattr.present"),
            )
            end_bb = parent_fn.append_basic_block(
                name=self._fresh("hasattr.end"),
            )
            self.builder.cbranch(present, present_bb, missing_bb)
            self.builder.position_at_end(missing_bb)
            self.builder.call(self.runtime["py_clear_exception"], [])
            self.builder.branch(end_bb)
            missing_exit = self.builder._block
            self.builder.position_at_end(present_bb)
            self.builder.branch(end_bb)
            present_exit = self.builder._block
            self.builder.position_at_end(end_bb)
            phi = self.builder.phi(_I1, name=self._fresh("hasattr.result"))
            phi.add_incoming(ir.Constant(_I1, 0), missing_exit)
            phi.add_incoming(ir.Constant(_I1, 1), present_exit)
            return phi
        if name == "issubclass" and len(expr.args) == 2:
            result = self._maybe_emit_issubclass_builtin(expr)
            if result is not None:
                return result
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
                    self.runtime["py_str_ord_at_i64"],
                    [s_val, idx_val],
                    name=self._fresh("ord.at"),
                )
            s_val = self._emit_as_object(ord_arg)
            return self.builder.call(
                self.runtime["py_str_ord"],
                [s_val],
                name=self._fresh("ord"),
            )
        module_name_for_call = self.module.name or ""
        if (
            module_name_for_call == "pcc.parse.py_lex"
            and name == "_pcc_str_byte_len"
            and len(expr.args) == 1
        ):
            s_val = self._emit_expr(expr.args[0])
            return self.builder.call(
                self.runtime["py_str_byte_len"],
                [s_val],
                name=self._fresh("str.byte_len"),
            )
        if (
            module_name_for_call == "pcc.parse.py_lex"
            and name == "_pcc_str_byte_at"
            and len(expr.args) == 2
        ):
            s_val = self._emit_expr(expr.args[0])
            idx_val = self._emit_expr_as_i64(expr.args[1])
            return self.builder.call(
                self.runtime["py_str_byte_at_i64"],
                [s_val, idx_val],
                name=self._fresh("str.byte_at"),
            )
        if (
            module_name_for_call == "pcc.parse.py_lex"
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
        if name == "setattr" and len(expr.args) == 3:
            return self._emit_setattr_builtin(expr)
        if name == "delattr" and len(expr.args) == 2:
            return self._emit_delattr_builtin(expr)
        if name == "getattr" and 2 <= len(expr.args) <= 3:
            return self._emit_getattr_builtin(expr)
        if name == "type" and len(expr.args) == 3:
            dynamic_type = self._maybe_emit_dynamic_type_constructor(expr)
            if dynamic_type is not None:
                return dynamic_type
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
            class_info = self.class_lowering.classes.get(class_name)
            if class_info is not None:
                metaclass_name = getattr(class_info, "metaclass_name", None)
                if metaclass_name is not None:
                    meta_info = self.class_lowering.classes.get(metaclass_name)
                    if meta_info is not None and "__call__" in meta_info.methods:
                        cls_ptr = self.builder.load(
                            class_info.global_var,
                            name=self._fresh(".meta.call.cls"),
                        )
                        return self._emit_direct_method_call(
                            meta_info.methods["__call__"],
                            cls_ptr,
                            meta_info,
                            "__call__",
                            expr.args,
                            kwargs=expr.kwargs,
                        )

            def attach_hoisted_class_captures(inst: ir.Value) -> ir.Value:
                class_caps = getattr(
                    self,
                    "_hoisted_class_capture_params",
                    {},
                ).get(class_name, ())
                for fv in class_caps:
                    cap_expr = Name(
                        span=self._expr_span_or_none(expr),
                        ty=DynType(name="dyn"),
                        ident=fv,
                    )
                    raw_v = self._emit_name(cap_expr)
                    v_obj = marshal.marshal_to_object(
                        self.builder,
                        self.module,
                        self.runtime,
                        raw_v,
                        cap_expr.ty,
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
            init_fd = self.class_lowering._find_method_def(class_name, "__init__")
            if init_fd is None:
                # Walk MRO for an inherited __init__.
                mro_info = self._resolve_method_mro(class_name, "__init__")
                if mro_info is not None:
                    init_fd = self.class_lowering._find_method_def(
                        mro_info.name,
                        "__init__",
                    )
            if init_fd is None:
                inst = self._emit_no_init_field_instance(
                    class_name,
                    expr.args,
                    expr.kwargs,
                )
                if inst is not None:
                    return attach_hoisted_class_captures(inst)
                if expr.kwargs:
                    inst = attach_hoisted_class_captures(
                        self.class_lowering.emit_instantiate(
                            class_name,
                            expr.args,
                            self,
                        )
                    )
                    for kw_name, kw_expr in expr.kwargs:
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
            else:
                resolved_args = tuple(
                    self._resolve_call_kwargs(
                        expr.args,
                        expr.kwargs,
                        init_fd.args,
                        skip_self=True,
                    )
                )
            if (
                class_info is not None
                and getattr(class_info, "owning_module", None)
                == "pcc.py_frontend.py_ast"
                and len(expr.args) + len(expr.kwargs)
                >= len(tuple(class_info.field_names))
            ):
                inst = self._emit_no_init_field_instance(
                    class_name,
                    expr.args,
                    expr.kwargs,
                    force=True,
                )
                if inst is not None:
                    return attach_hoisted_class_captures(inst)
            resolved_args = self._materialize_class_init_call_args(resolved_args)
            return attach_hoisted_class_captures(
                self.class_lowering.emit_instantiate(
                    class_name,
                    resolved_args,
                    self,
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
                    obj_val = self._emit_name(
                        Name(
                            span=self._expr_span_or_none(expr),
                            ty=DynType(name="dyn"),
                            ident=name,
                        )
                    )
                    method_fn = info.methods["__call__"]
                    return self._emit_direct_method_call(
                        method_fn,
                        obj_val,
                        info,
                        "__call__",
                        expr.args,
                        kwargs=expr.kwargs,
                    )

        # Runtime bindings shadow same-named FuncDefs. This matters for
        # Python patterns such as ``f = obj.f; return f(...)`` inside a
        # function named ``f``.
        if name in self.env or name in getattr(self, "_module_globals", {}):
            fn_val = self._emit_name(
                Name(
                    span=self._expr_span_or_none(expr),
                    ty=DynType(name="dyn"),
                    ident=name,
                ),
            )
            is_cpy_local = (
                fn_val in getattr(self, "_cpy_values", ())
                or getattr(self, "_cpy_env_flags", {}).get(name, False)
                or getattr(self, "_cpy_module_flags", {}).get(name, False)
            )
            if is_cpy_local:
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val,
                        name,
                        expr.args,
                        expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
            arg_exprs = expr.args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_owned = not self._is_starred_unpack(arg_exprs)
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                self._expr_span_or_none(expr),
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [fn_val, args_tuple, kwargs_obj],
                name=self._fresh(f"{name}.obj.call"),
            )
            if args_owned:
                self._gc_release(args_tuple)
            if expr.kwargs:
                self._gc_release(kwargs_obj)
            return result

        fn = self.functions.get(name)
        if fn is None:
            # CPython-backed callable (e.g. a ``from .sibling import
            # foo`` where ``foo`` isn't a native-sibling FuncDef)
            # dispatches via PyObject_Call. Pulls libpython but is
            # correct for the import route.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(name)
            if cpy_gv is not None:
                fn_val = self.builder.load(
                    cpy_gv,
                    name=self._fresh(f"cpy.fn.{name}"),
                )
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        fn_val,
                        name,
                        expr.args,
                        expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            star_val = self._load_from_cpy_star_imports(name)
            if star_val is not None:
                if expr.kwargs:
                    return self._finish_cpy_call_kw(
                        star_val,
                        name,
                        expr.args,
                        expr.kwargs,
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
                        fn_val,
                        name,
                        expr.args,
                        expr.kwargs,
                    )
                return self._emit_cpy_func_call(fn_val, name, expr.args)
            # Local variable holding a callable (e.g. ``klass = self.
            # _select_struct_union_class(p[1]); klass(args)``). The
            # binding lives in env / module_globals. CPython-tagged
            # bindings still route through libpython; pcc-native
            # function/class objects dispatch through py_obj_call.
            if name in self.env or name in getattr(self, "_module_globals", {}):
                fn_val = self._emit_name(
                    Name(
                        span=self._expr_span_or_none(expr),
                        ty=DynType(name="dyn"),
                        ident=name,
                    ),
                )
                is_cpy_local = (
                    fn_val in getattr(self, "_cpy_values", ())
                    or getattr(self, "_cpy_env_flags", {}).get(name, False)
                    or getattr(self, "_cpy_module_flags", {}).get(name, False)
                )
                if is_cpy_local:
                    if expr.kwargs:
                        return self._finish_cpy_call_kw(
                            fn_val,
                            name,
                            expr.args,
                            expr.kwargs,
                        )
                    return self._emit_cpy_func_call(fn_val, name, expr.args)
                kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
                arg_exprs = expr.args
                kwargs_expr = None
                if kwdict_unpack is not None:
                    arg_exprs, kwargs_expr = kwdict_unpack
                args_owned = not self._is_starred_unpack(arg_exprs)
                args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
                kwargs_obj = self._emit_dynamic_call_kwargs_object(
                    expr.kwargs,
                    kwargs_expr,
                    self._expr_span_or_none(expr),
                )
                result = self.builder.call(
                    self.runtime["py_obj_call"],
                    [fn_val, args_tuple, kwargs_obj],
                    name=self._fresh(f"{name}.obj.call"),
                )
                if args_owned:
                    self._gc_release(args_tuple)
                if expr.kwargs:
                    self._gc_release(kwargs_obj)
                return result
            # Python resolves function names at runtime. If the compiler
            # cannot statically bind the callable, emit a normal name load
            # followed by the pcc-native dynamic call path. An actually
            # missing name then raises NameError at runtime instead of being
            # rejected during compilation; names populated by dynamic import
            # machinery can still be called.
            fn_val = self._emit_name(
                Name(
                    span=self._expr_span_or_none(expr),
                    ty=DynType(name="dyn"),
                    ident=name,
                ),
            )
            kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
            arg_exprs = expr.args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_owned = not self._is_starred_unpack(arg_exprs)
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                self._expr_span_or_none(expr),
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [fn_val, args_tuple, kwargs_obj],
                name=self._fresh(f"{name}.dyn.call"),
            )
            if args_owned:
                self._gc_release(args_tuple)
            if expr.kwargs:
                self._gc_release(kwargs_obj)
            return result
        ast_func_def = self._find_user_funcdef(name)
        if ast_func_def.is_async:
            return self._emit_async_user_function_call(
                name,
                fn,
                ast_func_def,
                expr.args,
                expr.kwargs,
            )
        if self._decorators_are_native_functions(ast_func_def):
            return self._emit_decorated_user_function_call(
                name=name,
                fn=fn,
                ast_func_def=ast_func_def,
                args=expr.args,
                kwargs=expr.kwargs,
            )
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
        has_click_decorator = self._func_has_click_decorator(ast_func_def)
        call_kwargs = expr.kwargs
        hoist_caps = getattr(self, "_hoisted_capture_params", {}).get(name)
        if hoist_caps:
            present_kw = set()
            i = 0
            while i < len(call_kwargs):
                k, _value = call_kwargs[i]
                present_kw.add(k)
                i += 1
            extra_kw_list = []
            i = 0
            while i < len(hoist_caps):
                fv = hoist_caps[i]
                if fv not in present_kw:
                    extra_kw_list.append(
                        (
                            fv,
                            Name(
                                span=self._expr_span_or_none(expr),
                                ty=DynType(name="dyn"),
                                ident=fv,
                            ),
                        )
                    )
                i += 1
            extra_kw = tuple(extra_kw_list)
            if extra_kw:
                call_kwargs = call_kwargs + extra_kw
        if has_click_decorator:
            from ..py_ast import NoneLit as _NL, Arg as _Arg

            patched_list = []
            i = 0
            while i < len(ast_func_def.args):
                a = ast_func_def.args[i]
                patched_list.append(
                    a if a.default is not None else _replace_arg_with_none_default(a)
                )
                i += 1
            patched = tuple(patched_list)
            try:
                resolved_args = self._resolve_call_kwargs(
                    expr.args,
                    call_kwargs,
                    patched,
                )
            except L1CodegenError:
                resolved_args = self._resolve_call_kwargs(
                    expr.args,
                    call_kwargs,
                    ast_func_def.args,
                )
        else:
            try:
                resolved_args = self._resolve_call_kwargs(
                    expr.args,
                    call_kwargs,
                    ast_func_def.args,
                )
            except L1CodegenError as exc:
                raise L1CodegenError(
                    str(exc) + " while resolving call to " + repr(name)
                )
        runtime_formals = []
        i = 0
        while i < len(ast_func_def.args):
            a = ast_func_def.args[i]
            if a.name != "":
                runtime_formals.append(a)
            i += 1
        args_ir: list[ir.Value] = []
        i = 0
        while i < len(resolved_args) and i < len(runtime_formals) and i < len(fn.args):
            ast_arg = resolved_args[i]
            arg_def = runtime_formals[i]
            ir_arg = fn.args[i]
            target_ty = arg_def.annotation or DynType(name="dyn")
            param_ir_ty = self._function_arg_ir_type_or_none(fn, i, ir_arg)
            if param_ir_ty is None:
                param_ir_ty = self._abi_ir_type(
                    target_ty,
                    box_int_abi=self._should_box_python_ints(),
                )
            v = self._emit_arg_for_abi_param(ast_arg, target_ty, param_ir_ty)
            args_ir.append(v)
            i += 1
        call_name = (
            ""
            if isinstance(fn.function_type.return_type, ir.VoidType)
            else self._fresh(f"{name}_ret")
        )
        result = self._call_user(
            fn,
            args_ir,
            call_name,
            span=self._expr_span_or_none(expr),
        )
        if self._user_func_returns_cpython(
            ast_func_def,
            runtime_formals,
            resolved_args,
        ):
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
        return result
