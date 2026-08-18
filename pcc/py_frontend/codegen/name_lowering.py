"""Name expression lowering helpers for L1CodeGen."""

from __future__ import annotations

import os
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolType,
    ByteArrayType,
    BytesType,
    ClassType,
    ComplexType,
    DictType,
    DynType,
    FloatType,
    IntType,
    ListType,
    MemoryViewType,
    Name,
    NoneType,
    StrType,
    TupleType,
    Type,
)
from .builtin_exceptions import BUILTIN_EXC_TAG as _BUILTIN_EXC_TAG
from .freestanding_abi_constants import (
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_COMPLEX,
    PY_TYPE_DICT,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_MEMORYVIEW,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
)
from .runtime_abi import declare_runtime_global

_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = _I8.as_pointer()
_CPY_BUILTIN_TYPE_NAMES = frozenset(
    {
        "int",
        "str",
        "list",
        "dict",
        "tuple",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "set",
        "frozenset",
        "complex",
        "memoryview",
        "object",
        "type",
        "Exception",
        "BaseException",
    }
)
_NATIVE_BUILTIN_CALLABLE_NAMES = frozenset(
    {
        "bool",
        "bytes",
        "bytearray",
        "chr",
        "complex",
        "dict",
        "float",
        "id",
        "int",
        "isinstance",
        "iter",
        "list",
        "memoryview",
        "object",
        "range",
        "repr",
        "str",
        "tuple",
    }
)


def _is_nested_hoist_collision_name(name: str, direct_hoist: str) -> bool:
    prefix = f"{direct_hoist}_"
    if not name.startswith(prefix):
        return False
    suffix = name[len(prefix) :]
    return suffix.isdigit()


class NameLoweringMixin:
    def _name_returns_native_builtin_callable_value(self, name: str) -> bool:
        if name in _NATIVE_BUILTIN_CALLABLE_NAMES:
            return True
        return self._native_builtin_value_for_name(name) in (
            "builtins.bool",
            "builtins.bytes",
            "builtins.bytearray",
            "builtins.complex",
            "builtins.dict",
            "builtins.float",
            "builtins.int",
            "builtins.list",
            "builtins.memoryview",
            "builtins.object",
            "builtins.str",
            "builtins.tuple",
        )

    def _emit_native_builtin_callable_type_error(
        self,
        builder: ir.IRBuilder,
        message: str,
        name: str,
        suffix: str,
    ) -> None:
        zero = ir.Constant(_I32, 0)
        gv = self._cstr_global(message, f".builtin.{name}.{suffix}.type_error")
        msg_ptr = builder.gep(gv, [zero, zero], inbounds=True)
        exc = builder.call(
            self.runtime["py_exc_new"],
            [ir.Constant(_I64, 3), msg_ptr],
            name=f"{name}.type_error",
        )
        builder.call(self.runtime["py_raise"], [exc])
        builder.ret(ir.Constant(_CSTR, None))

    def _emit_native_range_callable_value(self) -> ir.Value:
        """Materialize ``range`` as a native first-class callable.

        Direct ``range(...)`` calls use the static lowering in
        ``_emit_range_value_call``.  Aliases that escape through a module
        global need an ordinary ``PyFunc`` object as well; otherwise
        ``saved_range = range`` is compiler metadata only and importing
        ``saved_range`` from a sibling observes an uninitialized binding.
        """
        name = "range"
        adapter_name = "__pcc_builtin_callable_range"
        adapter = self.module.globals.get(adapter_name)
        if not isinstance(adapter, ir.Function):
            fnty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, fnty, name=adapter_name)
            adapter.linkage = "internal"
            entry_bb = adapter.append_basic_block("entry")
            builder = ir.IRBuilder(entry_bb)
            _captures_arg, args_arg = adapter.args
            argc = builder.call(
                self.runtime["py_tuple_len"],
                [args_arg],
                name="range.argc",
            )
            one_bb = adapter.append_basic_block("range.one")
            two_check_bb = adapter.append_basic_block("range.two_check")
            two_bb = adapter.append_basic_block("range.two")
            three_check_bb = adapter.append_basic_block("range.three_check")
            three_bb = adapter.append_basic_block("range.three")
            arity_error_bb = adapter.append_basic_block("range.arity_error")
            setup_bb = adapter.append_basic_block("range.setup")

            is_one = builder.icmp_signed(
                "==", argc, ir.Constant(_I64, 1), name="range.argc1"
            )
            builder.cbranch(is_one, one_bb, two_check_bb)

            builder.position_at_end(two_check_bb)
            is_two = builder.icmp_signed(
                "==", argc, ir.Constant(_I64, 2), name="range.argc2"
            )
            builder.cbranch(is_two, two_bb, three_check_bb)

            builder.position_at_end(three_check_bb)
            is_three = builder.icmp_signed(
                "==", argc, ir.Constant(_I64, 3), name="range.argc3"
            )
            builder.cbranch(is_three, three_bb, arity_error_bb)

            builder.position_at_end(arity_error_bb)
            self._emit_native_builtin_callable_type_error(
                builder,
                "range() takes 1 to 3 arguments",
                name,
                "arity",
            )

            builder.position_at_end(one_bb)
            one_arg = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 0)],
                name="range.one.arg",
            )
            one_stop = builder.call(
                self.runtime["py_obj_index_i64"],
                [one_arg],
                name="range.one.stop",
            )
            builder.call(self.runtime["py_decref"], [one_arg])
            builder.branch(setup_bb)

            builder.position_at_end(two_bb)
            two_arg0 = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 0)],
                name="range.two.arg0",
            )
            two_arg1 = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 1)],
                name="range.two.arg1",
            )
            two_start = builder.call(
                self.runtime["py_obj_index_i64"],
                [two_arg0],
                name="range.two.start",
            )
            two_stop = builder.call(
                self.runtime["py_obj_index_i64"],
                [two_arg1],
                name="range.two.stop",
            )
            builder.call(self.runtime["py_decref"], [two_arg0])
            builder.call(self.runtime["py_decref"], [two_arg1])
            builder.branch(setup_bb)

            builder.position_at_end(three_bb)
            three_arg0 = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 0)],
                name="range.three.arg0",
            )
            three_arg1 = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 1)],
                name="range.three.arg1",
            )
            three_arg2 = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 2)],
                name="range.three.arg2",
            )
            three_start = builder.call(
                self.runtime["py_obj_index_i64"],
                [three_arg0],
                name="range.three.start",
            )
            three_stop = builder.call(
                self.runtime["py_obj_index_i64"],
                [three_arg1],
                name="range.three.stop",
            )
            three_step = builder.call(
                self.runtime["py_obj_index_i64"],
                [three_arg2],
                name="range.three.step",
            )
            builder.call(self.runtime["py_decref"], [three_arg0])
            builder.call(self.runtime["py_decref"], [three_arg1])
            builder.call(self.runtime["py_decref"], [three_arg2])
            builder.branch(setup_bb)

            builder.position_at_end(setup_bb)
            start = builder.phi(_I64, name="range.start")
            start.add_incoming(ir.Constant(_I64, 0), one_bb)
            start.add_incoming(two_start, two_bb)
            start.add_incoming(three_start, three_bb)
            stop = builder.phi(_I64, name="range.stop")
            stop.add_incoming(one_stop, one_bb)
            stop.add_incoming(two_stop, two_bb)
            stop.add_incoming(three_stop, three_bb)
            step = builder.phi(_I64, name="range.step")
            step.add_incoming(ir.Constant(_I64, 1), one_bb)
            step.add_incoming(ir.Constant(_I64, 1), two_bb)
            step.add_incoming(three_step, three_bb)

            out = builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name="range.list",
            )
            idx_slot = builder.alloca(_I64, name="range.idx.addr")
            builder.store(start, idx_slot)
            cond_bb = adapter.append_basic_block("range.cond")
            body_bb = adapter.append_basic_block("range.body")
            step_bb = adapter.append_basic_block("range.advance")
            end_bb = adapter.append_basic_block("range.end")
            builder.branch(cond_bb)

            builder.position_at_end(cond_bb)
            current = builder.load(idx_slot, name="range.current")
            step_positive = builder.icmp_signed(
                ">", step, ir.Constant(_I64, 0), name="range.step.positive"
            )
            forward = builder.icmp_signed("<", current, stop, name="range.forward")
            backward = builder.icmp_signed(">", current, stop, name="range.backward")
            keep = builder.select(
                step_positive,
                forward,
                backward,
                name="range.keep",
            )
            builder.cbranch(keep, body_bb, end_bb)

            builder.position_at_end(body_bb)
            item = builder.call(
                self.runtime["py_int_from_i64"],
                [current],
                name="range.item",
            )
            builder.call(self.runtime["py_list_append"], [out, item])
            builder.branch(step_bb)

            builder.position_at_end(step_bb)
            next_value = builder.add(current, step, name="range.next")
            builder.store(next_value, idx_slot)
            builder.branch(cond_bb)

            builder.position_at_end(end_bb)
            builder.ret(out)

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("range.captures"),
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_named"],
            [adapter, captures, self._attr_name_ptr(name)],
            name=self._fresh("range.builtin.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _emit_native_builtin_callable_value(self, name: str) -> Optional[ir.Value]:
        builtin_value = self._native_builtin_value_for_name(name)
        canonical_names = {
            "builtins.bool": "bool",
            "builtins.bytes": "bytes",
            "builtins.bytearray": "bytearray",
            "builtins.complex": "complex",
            "builtins.dict": "dict",
            "builtins.float": "float",
            "builtins.int": "int",
            "builtins.list": "list",
            "builtins.memoryview": "memoryview",
            "builtins.object": "object",
            "builtins.range": "range",
            "builtins.str": "str",
            "builtins.tuple": "tuple",
        }
        canonical_name = canonical_names.get(builtin_value, name)
        if canonical_name not in _NATIVE_BUILTIN_CALLABLE_NAMES:
            return None
        if canonical_name == "range":
            return self._emit_native_range_callable_value()
        builtin_tags = {
            "bool": PY_TYPE_BOOL,
            "int": PY_TYPE_INT,
            "float": PY_TYPE_FLOAT,
            "str": PY_TYPE_STR,
            "list": PY_TYPE_LIST,
            "dict": PY_TYPE_DICT,
            "tuple": PY_TYPE_TUPLE,
            "bytes": PY_TYPE_BYTES,
            "bytearray": PY_TYPE_BYTEARRAY,
            "memoryview": PY_TYPE_MEMORYVIEW,
            "complex": PY_TYPE_COMPLEX,
            # ``py_builtin_type_for_tag`` maps unknown tags to the runtime's
            # canonical object class. There is deliberately no object-instance
            # tag in the object header enum: ordinary instances carry their
            # concrete user class.
            "object": -1,
        }
        if canonical_name in builtin_tags:
            return self.builder.call(
                self.runtime["py_builtin_type_for_tag"],
                [ir.Constant(_I64, builtin_tags[canonical_name])],
                name=self._fresh(f"{canonical_name}.type.value"),
            )
        if canonical_name in ("chr", "id", "isinstance", "iter", "repr"):
            name = canonical_name
            adapter_name = f"__pcc_builtin_callable_{name}"
            adapter = self.module.globals.get(adapter_name)
            if not isinstance(adapter, ir.Function):
                fnty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
                adapter = ir.Function(self.module, fnty, name=adapter_name)
                adapter.linkage = "internal"
                builder = ir.IRBuilder(adapter.append_basic_block("entry"))
                args_arg = adapter.args[1]
                argc = builder.call(
                    self.runtime["py_tuple_len"],
                    [args_arg],
                    name=f"{name}.argc",
                )
                expected = 2 if name == "isinstance" else 1
                is_valid = builder.icmp_signed(
                    "==",
                    argc,
                    ir.Constant(_I64, expected),
                    name=f"{name}.arity.ok",
                )
                ok_bb = adapter.append_basic_block("arity.ok")
                error_bb = adapter.append_basic_block("arity.error")
                builder.cbranch(is_valid, ok_bb, error_bb)

                builder.position_at_end(error_bb)
                self._emit_native_builtin_callable_type_error(
                    builder,
                    f"{name}() takes exactly {expected} argument"
                    + ("s" if expected != 1 else ""),
                    name,
                    "arity",
                )

                builder.position_at_end(ok_bb)
                arg0 = builder.call(
                    self.runtime["py_tuple_get"],
                    [args_arg, ir.Constant(_I64, 0)],
                    name=f"{name}.arg0",
                )
                if name == "chr":
                    codepoint = builder.call(
                        self.runtime["py_obj_index_i64"],
                        [arg0],
                        name="chr.index",
                    )
                    result = builder.call(
                        self.runtime["py_chr_from_i64"],
                        [codepoint],
                        name="chr.result",
                    )
                elif name == "id":
                    address = builder.ptrtoint(arg0, _I64, name="id.address")
                    result = builder.call(
                        self.runtime["py_int_from_i64"],
                        [address],
                        name="id.result",
                    )
                elif name == "iter":
                    result = builder.call(
                        self.runtime["py_obj_iter"],
                        [arg0],
                        name="iter.result",
                    )
                elif name == "repr":
                    result = builder.call(
                        self.runtime["py_obj_repr"],
                        [arg0],
                        name="repr.result",
                    )
                else:
                    arg1 = builder.call(
                        self.runtime["py_tuple_get"],
                        [args_arg, ir.Constant(_I64, 1)],
                        name="isinstance.arg1",
                    )
                    bit = builder.call(
                        self.runtime["py_obj_isinstance"],
                        [arg0, arg1],
                        name="isinstance.bit",
                    )
                    result = builder.call(
                        self.runtime["py_bool_from_bit"],
                        [builder.trunc(bit, _I32)],
                        name="isinstance.result",
                    )
                    builder.call(self.runtime["py_decref"], [arg1])
                builder.call(self.runtime["py_decref"], [arg0])
                builder.ret(result)

            captures = self.builder.call(
                self.runtime["py_tuple_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh(f"{name}.captures"),
            )
            fn_obj = self.builder.call(
                self.runtime["py_func_new_named"],
                [adapter, captures, self._attr_name_ptr(name)],
                name=self._fresh(f"{name}.builtin.func"),
            )
            self._gc_release(captures)
            return fn_obj
        adapter_name = f"__pcc_builtin_callable_{name}"
        adapter = self.module.globals.get(adapter_name)
        if not isinstance(adapter, ir.Function):
            fnty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            adapter = ir.Function(self.module, fnty, name=adapter_name)
            adapter.linkage = "internal"
            builder = ir.IRBuilder(adapter.append_basic_block("entry"))
            captures_arg, args_arg = adapter.args
            del captures_arg
            argc = builder.call(
                self.runtime["py_tuple_len"],
                [args_arg],
                name=f"{name}.argc",
            )
            no_args_bb = adapter.append_basic_block(f"{name}.no_args")
            one_arg_bb = adapter.append_basic_block(f"{name}.one_arg")
            too_many_bb = adapter.append_basic_block(f"{name}.too_many")
            has_no_args = builder.icmp_signed(
                "==",
                argc,
                ir.Constant(_I64, 0),
                name=f"{name}.argc0",
            )
            has_one_arg = builder.icmp_signed(
                "==",
                argc,
                ir.Constant(_I64, 1),
                name=f"{name}.argc1",
            )
            argc_check_bb = adapter.append_basic_block(f"{name}.argc_check")
            builder.cbranch(has_no_args, no_args_bb, argc_check_bb)

            builder.position_at_end(argc_check_bb)
            builder.cbranch(has_one_arg, one_arg_bb, too_many_bb)

            builder.position_at_end(no_args_bb)
            if name == "int":
                zero_obj = builder.call(
                    self.runtime["py_int_from_i64"],
                    [ir.Constant(_I64, 0)],
                    name="int.zero",
                )
                builder.ret(zero_obj)
            elif name == "float":
                zero_obj = builder.call(
                    self.runtime["py_float_from_f64"],
                    [ir.Constant(_DOUBLE, 0.0)],
                    name="float.zero",
                )
                builder.ret(zero_obj)
            else:
                empty_gv = self._cstr_global("", ".builtin.str.empty")
                empty_ptr = builder.gep(
                    empty_gv,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
                    inbounds=True,
                )
                empty = builder.call(
                    self.runtime["py_str_new"],
                    [empty_ptr, ir.Constant(_I64, 0)],
                    name="str.empty",
                )
                builder.ret(empty)

            builder.position_at_end(too_many_bb)
            self._emit_native_builtin_callable_type_error(
                builder,
                f"{name}() takes at most 1 argument",
                name,
                "arity",
            )

            builder.position_at_end(one_arg_bb)
            arg = builder.call(
                self.runtime["py_tuple_get"],
                [args_arg, ir.Constant(_I64, 0)],
                name=f"{name}.arg",
            )
            if name == "str":
                result = builder.call(
                    self.runtime["py_obj_str"],
                    [arg],
                    name="str.result",
                )
                builder.call(self.runtime["py_decref"], [arg])
                builder.ret(result)
            elif name == "float":
                value = builder.call(
                    self.runtime["py_float_value_of"],
                    [arg],
                    name="float.value",
                )
                builder.call(self.runtime["py_decref"], [arg])
                result = builder.call(
                    self.runtime["py_float_from_f64"],
                    [value],
                    name="float.result",
                )
                builder.ret(result)
            else:
                tag = builder.call(
                    self.runtime["py_obj_type_tag"],
                    [arg],
                    name="int.tag",
                )
                is_int = builder.icmp_signed(
                    "==",
                    tag,
                    ir.Constant(_I64, PY_TYPE_INT),
                    name="int.is_int",
                )
                int_bb = adapter.append_basic_block("int.from_int")
                non_int_bb = adapter.append_basic_block("int.non_int")
                builder.cbranch(is_int, int_bb, non_int_bb)

                builder.position_at_end(int_bb)
                builder.ret(arg)

                builder.position_at_end(non_int_bb)
                is_bool = builder.icmp_signed(
                    "==",
                    tag,
                    ir.Constant(_I64, PY_TYPE_BOOL),
                    name="int.is_bool",
                )
                bool_bb = adapter.append_basic_block("int.from_bool")
                non_bool_bb = adapter.append_basic_block("int.non_bool")
                builder.cbranch(is_bool, bool_bb, non_bool_bb)

                builder.position_at_end(bool_bb)
                truth = builder.call(
                    self.runtime["py_obj_truthy"],
                    [arg],
                    name="int.bool.truth",
                )
                bool_int = builder.call(
                    self.runtime["py_int_from_i64"],
                    [truth],
                    name="int.bool.result",
                )
                builder.call(self.runtime["py_decref"], [arg])
                builder.ret(bool_int)

                builder.position_at_end(non_bool_bb)
                is_float = builder.icmp_signed(
                    "==",
                    tag,
                    ir.Constant(_I64, PY_TYPE_FLOAT),
                    name="int.is_float",
                )
                float_bb = adapter.append_basic_block("int.from_float")
                non_float_bb = adapter.append_basic_block("int.non_float")
                builder.cbranch(is_float, float_bb, non_float_bb)

                builder.position_at_end(float_bb)
                f64 = builder.call(
                    self.runtime["py_float_to_f64"],
                    [arg],
                    name="int.float.f64",
                )
                i64 = builder.fptosi(f64, _I64, name="int.float.i64")
                float_int = builder.call(
                    self.runtime["py_int_from_i64"],
                    [i64],
                    name="int.float.result",
                )
                builder.call(self.runtime["py_decref"], [arg])
                builder.ret(float_int)

                builder.position_at_end(non_float_bb)
                is_str = builder.icmp_signed(
                    "==",
                    tag,
                    ir.Constant(_I64, PY_TYPE_STR),
                    name="int.is_str",
                )
                str_bb = adapter.append_basic_block("int.from_str")
                type_error_bb = adapter.append_basic_block("int.type_error")
                builder.cbranch(is_str, str_bb, type_error_bb)

                builder.position_at_end(str_bb)
                cstr = builder.call(
                    self.runtime["py_str_utf8"],
                    [arg],
                    name="int.str.cstr",
                )
                parsed = builder.call(
                    self.runtime["py_int_from_cstr_or_raise"],
                    [cstr, ir.Constant(_I32, 10)],
                    name="int.str.result",
                )
                builder.call(self.runtime["py_decref"], [arg])
                builder.ret(parsed)

                builder.position_at_end(type_error_bb)
                builder.call(self.runtime["py_decref"], [arg])
                self._emit_native_builtin_callable_type_error(
                    builder,
                    "int() argument must be a string, a bytes-like object, or a real number",
                    name,
                    "unsupported",
                )

        captures = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh(f"{name}.captures"),
        )
        fn_obj = self.builder.call(
            self.runtime["py_func_new_named"],
            [adapter, captures, self._attr_name_ptr(name)],
            name=self._fresh(f"{name}.builtin.func"),
        )
        self._gc_release(captures)
        return fn_obj

    def _static_runtime_type_name(self, ty: Type) -> Optional[str]:
        if isinstance(ty, StrType):
            return "str"
        if isinstance(ty, BytesType):
            return "bytes"
        if isinstance(ty, ByteArrayType):
            return "bytearray"
        if isinstance(ty, MemoryViewType):
            return "memoryview"
        if isinstance(ty, BoolType):
            return "bool"
        if isinstance(ty, IntType):
            return "int"
        if isinstance(ty, FloatType):
            return "float"
        if isinstance(ty, ComplexType):
            return "complex"
        if isinstance(ty, ListType):
            return "list"
        if isinstance(ty, DictType):
            return "dict"
        if isinstance(ty, TupleType):
            return "tuple"
        if isinstance(ty, NoneType):
            return "NoneType"
        if isinstance(ty, ClassType):
            # Class types (including exceptions) are subclassable; their dynamic type at
            # runtime can differ from the static type. We return None so that
            # type(obj).__name__ resolves dynamically via py_obj_type_name.
            return None
        return None

    def _annotation_runtime_name(self, ann: object) -> str:
        if isinstance(ann, IntType):
            return "int"
        if isinstance(ann, FloatType):
            return "float"
        if isinstance(ann, BoolType):
            return "bool"
        if isinstance(ann, StrType):
            return "str"
        if isinstance(ann, NoneType):
            return "None"
        if isinstance(ann, ListType):
            return "list"
        if isinstance(ann, DictType):
            return "dict"
        if isinstance(ann, TupleType):
            return "tuple"
        if isinstance(ann, ClassType):
            return ann.name
        if isinstance(ann, DynType):
            return "dyn"
        if isinstance(ann, Name):
            return ann.ident
        return type(ann).__name__

    def _emit_name(self, expr: Name) -> ir.Value:
        slot = self.env.get(expr.ident)
        if slot is None:
            # Method-body ``__class__`` is a compiler-created cell in
            # CPython. For the currently supported direct method lowering,
            # current_class is the defining class, so expose that class
            # object when no local binding shadows the name.
            if expr.ident == "__class__":
                current_class = getattr(self, "current_class", None)
                if current_class is not None:
                    return self.builder.load(
                        current_class.global_var,
                        name=self._fresh(f"cls.{current_class.name}"),
                    )
            # Module-level dunder that pcc can resolve at compile time.
            # The entry module matches CPython's ``python myscript.py``
            # behavior; sibling modules in a multi-file compile see their
            # own module name so ``if __name__ == "__main__":`` blocks
            # stay dead in them (they are only entry-skipped via
            # _skip_program_main, not omitted from codegen).
            if expr.ident == "__name__":
                if self._skip_program_main and self.ast_module.name:
                    return self._emit_str_literal(self.ast_module.name)
                return self._emit_str_literal("__main__")
            if expr.ident == "__file__":
                # Source spans retain the parser input path. Use the same
                # value published on the compiled module object so code inside
                # a module and ``module.__file__`` agree.
                source_filename = getattr(self, "_module_source_path", "") or ""
                if source_filename:
                    source_filename = os.path.abspath(source_filename)
                else:
                    for stmt in self.ast_module.body:
                        span = getattr(stmt, "span", None)
                        filename = getattr(span, "file", "")
                        if filename and not filename.startswith("<"):
                            source_filename = os.path.abspath(filename)
                            break
                if source_filename == "":
                    source_filename = (
                        (self.ast_module.name or "pcc_py_module") + ".py"
                    )
                return self._emit_str_literal(source_filename)
            if expr.ident == "__package__":
                if not self._skip_program_main:
                    return self._emit_str_literal("")
                module_name = self.ast_module.name or ""
                source_filename = getattr(self, "_module_source_path", "") or ""
                if source_filename == "":
                    for stmt in self.ast_module.body:
                        span = getattr(stmt, "span", None)
                        filename = getattr(span, "file", "")
                        if filename and not filename.startswith("<"):
                            source_filename = filename
                            break
                if os.path.basename(source_filename) == "__init__.py":
                    return self._emit_str_literal(module_name)
                if "." in module_name:
                    return self._emit_str_literal(module_name.rsplit(".", 1)[0])
                return self._emit_str_literal("")
            if expr.ident == "__doc__":
                module_global = self._module_globals.get("__doc__")
                if module_global is not None:
                    return self.builder.load(
                        module_global[0],
                        name=self._fresh("__doc__"),
                    )
                imported = getattr(
                    self,
                    "_native_extension_module_env",
                    {},
                ).get("__doc__")
                if imported is not None:
                    return self.builder.load(
                        imported,
                        name=self._fresh("pcc.ext.__doc__"),
                    )
                cpy_imported = getattr(self, "_cpy_module_env", {}).get("__doc__")
                if cpy_imported is not None:
                    value = self.builder.load(
                        cpy_imported,
                        name=self._fresh("cpy.__doc__"),
                    )
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(value)
                    return value
                if self.ast_module.docstring is None:
                    return self._emit_none_literal()
                return self._emit_str_literal(self.ast_module.docstring)
            if expr.ident == "Ellipsis":
                # ``...`` / ``Ellipsis`` used as an expression — pcc
                # doesn't have a distinct Ellipsis type; reuse the
                # None-literal emitter so code that stashes
                # ``Ellipsis`` as a sentinel keeps working.
                return self._emit_none_literal()
            if expr.ident == "NotImplemented":
                gv = declare_runtime_global(self.module, "py_NotImplemented")
                return self.builder.load(gv, name=self._fresh("notimplemented"))
            if expr.ident == "super":
                # ``super`` is also a first-class built-in type object.  The
                # call-lowering path handles actual ``super(...)`` semantics;
                # value-position uses (for example copyreg's dispatch table)
                # need a stable, hashable native identity of their own.
                return self.builder.call(
                    self.runtime["py_builtin_type_for_tag"],
                    [ir.Constant(_I64, -3)],
                    name=self._fresh("super.type.value"),
                )
            if expr.ident in ("True", "False"):
                return ir.Constant(_I1, 1 if expr.ident == "True" else 0)
            if expr.ident in _BUILTIN_EXC_TAG:
                # Exception classes are values too: packages capture them in
                # defaults (``ValueError=ValueError``), containers, and
                # aliases.  Reuse the runtime's cached native class object so
                # these value-position uses stay no-libpython and preserve
                # identity with exception matching/constructors.
                return self.builder.call(
                    self.runtime["py_exc_builtin_class"],
                    [ir.Constant(_I64, _BUILTIN_EXC_TAG[expr.ident])],
                    name=self._fresh(f"exc.class.{expr.ident}"),
                )
            # Built-in type names at value position (``isinstance(x,
            # int)`` already folds compile-time; this covers the
            # residual ``obj_type = int`` / ``self.ty = str`` uses).
            # pcc-native callable values must stay native under
            # --python-libpython=off; real type-object identity/equality
            # cases are handled before name lowering by type-tag compare
            # fast paths.
            if expr.ident in _CPY_BUILTIN_TYPE_NAMES:
                native_callable = self._emit_native_builtin_callable_value(expr.ident)
                if native_callable is not None:
                    return native_callable
                return self._load_cpython_builtin(expr.ident)
            if self._name_returns_native_builtin_callable_value(expr.ident):
                native_callable = self._emit_native_builtin_callable_value(expr.ident)
                if native_callable is not None:
                    return native_callable
            builtin_value = self._native_builtin_value_for_name(expr.ident)
            if builtin_value == "os.sep":
                return self._emit_str_literal("/")
            if builtin_value == "os.linesep":
                return self._emit_str_literal("\n")
            if builtin_value == "os.altsep":
                return self._emit_none_literal()
            if builtin_value == "os.pathsep":
                return self._emit_str_literal(":")
            if builtin_value in ("sys.prefix", "sys.base_prefix"):
                return self.builder.call(
                    self.runtime["py_sys_prefix_str"],
                    [
                        ir.Constant(
                            _I64,
                            1 if builtin_value == "sys.base_prefix" else 0,
                        )
                    ],
                    name=self._fresh(builtin_value),
                )
            if builtin_value == "pcc.optional_import_missing.None":
                return self._emit_none_literal()
            # Module-level constant? Emit a load of the global.
            module_globals = self._module_globals
            if expr.ident in module_globals:
                gv, _declared_ty = module_globals[expr.ident]
                # Only names this module deletes somewhere can be unbound, so
                # every other global keeps a plain load with no added branch.
                if self._module_global_needs_bound_check(expr.ident):
                    self._emit_module_global_bound_check(expr.ident, expr)
                val = self.builder.load(
                    gv,
                    name=self._fresh(expr.ident),
                )
                if self._cpy_module_flags.get(expr.ident, False):
                    if not hasattr(self, "_cpy_values"):
                        self._cpy_values = set()
                    self._cpy_values.add(val)
                return val
            # User class reference at value position — load the class
            # global so ``ClassName.ATTR`` and similar look-ups work.
            if (
                hasattr(self, "class_lowering")
                and expr.ident in self.class_lowering.classes
            ):
                info = self.class_lowering.classes[expr.ident]
                return self.builder.load(
                    info.global_var,
                    name=self._fresh(f"cls.{expr.ident}"),
                )
            native_alias_module = getattr(
                self,
                "_native_module_aliases",
                {},
            ).get(expr.ident)
            native_ext_gv = getattr(self, "_native_extension_module_env", {}).get(
                expr.ident
            )
            if native_alias_module is not None:
                # Compiled sibling imports keep both a static alias (for
                # direct ``mod.attr`` lowering) and the real live module
                # object.  In value position Python passes the object, not
                # the historical module-name string placeholder.
                if native_ext_gv is not None:
                    return self.builder.load(
                        native_ext_gv,
                        name=self._fresh(f"pcc.ext.{expr.ident}"),
                    )
                return self._emit_native_module_placeholder(native_alias_module)
            native_constant = getattr(
                self,
                "_native_module_constant_bindings",
                {},
            ).get(expr.ident)
            if native_constant is not None:
                return self._emit_native_module_constant(native_constant)
            if native_ext_gv is not None:
                return self.builder.load(
                    native_ext_gv,
                    name=self._fresh(f"pcc.ext.{expr.ident}"),
                )
            # Fall back to the module-wide CPython import registry for
            # ``from os import sep`` / ``import sys`` style bindings.
            cpy_gv = getattr(self, "_cpy_module_env", {}).get(expr.ident)
            if cpy_gv is not None:
                val = self.builder.load(cpy_gv, name=self._fresh(f"cpy.{expr.ident}"))
                if not hasattr(self, "_cpy_values"):
                    self._cpy_values = set()
                self._cpy_values.add(val)
                return val
            builtin_module = self._native_builtin_module_for_name(expr.ident)
            if builtin_module is not None:
                return self._emit_cpython_module_value(builtin_module)
            star_val = self._load_from_native_extension_star_imports(expr.ident)
            if star_val is not None:
                return star_val
            star_val = self._load_from_cpy_star_imports(expr.ident)
            if star_val is not None:
                return star_val
            # User FuncDef at value position: wrap the pcc function
            # pointer as a CPython PyCFunction so it can be passed to
            # ``re.sub(pat, <repl>, text)`` / ``am.register(KEY, <fn>)``
            # / ``{c_ast.FileAST: _children_FileAST}`` / any other
            # CPython API that consumes a callable. Covers 1 / 2 / 3
            # arg DynType-in / DynType-out. Higher arity still falls
            # through.
            resolved_name = expr.ident
            fn_ir = self.functions.get(expr.ident)
            if fn_ir is None:
                direct_hoist = f"__nested_{expr.ident}"
                if direct_hoist in self.functions:
                    resolved_name = direct_hoist
                    fn_ir = self.functions[direct_hoist]
                else:
                    matches = [
                        name
                        for name in self.functions
                        if _is_nested_hoist_collision_name(name, direct_hoist)
                    ]
                    if len(matches) == 1:
                        resolved_name = matches[0]
                        fn_ir = self.functions[resolved_name]
            # Adapter-wrap path: the ident may originally have been
            # a nested def flagged for captures-via-globals wrap.
            # ``rename_map`` at hoist time remapped the original name
            # to the hoisted one already, but the metadata dict is
            # keyed on the original name. Try both.
            adapter_entry = None
            for candidate in (expr.ident, resolved_name):
                adapter_entry = getattr(
                    self,
                    "_hoist_wrap_caps",
                    {},
                ).get(candidate)
                if adapter_entry is not None:
                    break
            if fn_ir is None and adapter_entry is not None:
                hoisted_name = adapter_entry.get("hoisted_name")
                if hoisted_name:
                    fn_ir = self.functions.get(hoisted_name)
                    resolved_name = hoisted_name
            if (
                adapter_entry is None
                and fn_ir is not None
                and resolved_name != expr.ident
            ):
                free_names = getattr(
                    self,
                    "_hoisted_capture_params",
                    {},
                ).get(resolved_name, ())
                if free_names:
                    fnty = getattr(fn_ir, "function_type", None)
                    total_arity = len(getattr(fnty, "args", ()))
                    adapter_entry = {
                        "original_arity": max(total_arity - len(free_names), 0),
                        "free_names": tuple(free_names),
                        "hoisted_name": resolved_name,
                        "original_name": expr.ident,
                    }
            if fn_ir is not None:
                native_free_names = getattr(
                    self,
                    "_hoisted_capture_params",
                    {},
                ).get(resolved_name)
                if native_free_names is not None or getattr(
                    self,
                    "_prefer_native_callable_values",
                    False,
                ):
                    return self._emit_native_func_value(
                        expr.ident,
                        resolved_name,
                        fn_ir,
                        tuple(native_free_names or ()),
                    )
                fnty = getattr(fn_ir, "function_type", None)
                all_ptr_args = fnty is not None and all(
                    isinstance(a, ir.PointerType) for a in fnty.args
                )
                ret_ok = fnty is not None and isinstance(
                    fnty.return_type, ir.PointerType
                )
                ret_void = fnty is not None and isinstance(
                    fnty.return_type, ir.VoidType
                )
                ret_int_width = (
                    fnty.return_type.width
                    if fnty is not None
                    and isinstance(
                        fnty.return_type,
                        ir.IntType,
                    )
                    else 0
                )
                wrap_helper = None
                arity = None
                if all_ptr_args and (ret_ok or ret_void or ret_int_width in (1, 64)):
                    arity = len(fnty.args)
                    # Captures-adapter has original arity.
                    if adapter_entry is not None and adapter_entry.get("hoisted_name"):
                        arity = adapter_entry["original_arity"]
                    wrap_helper = {
                        0: "py_cpy_wrap_pcc_0arg",
                        1: "py_cpy_wrap_pcc_1arg",
                        2: "py_cpy_wrap_pcc_2arg",
                        3: "py_cpy_wrap_pcc_3arg",
                        4: "py_cpy_wrap_pcc_4arg",
                        5: "py_cpy_wrap_pcc_5arg",
                        6: "py_cpy_wrap_pcc_6arg",
                        7: "py_cpy_wrap_pcc_7arg",
                        8: "py_cpy_wrap_pcc_8arg",
                        9: "py_cpy_wrap_pcc_9arg",
                    }.get(arity)
                if wrap_helper is not None:
                    target_fn = fn_ir
                    if adapter_entry is not None and adapter_entry.get("free_names"):
                        # Hoisted-captures adapter. ``_emit_hoist_adapter``
                        # already boxes non-ptr returns internally.
                        target_fn = self._emit_hoist_adapter(
                            expr.ident,
                            fn_ir,
                            adapter_entry,
                        )
                    elif not ret_ok:
                        # Standalone adapter for a value-position ref to
                        # a pcc FuncDef whose return is void / bool / int.
                        # Box the result via the appropriate py_* helper.
                        adapter_name = f"{fn_ir.name}_v2pyobj_{arity}"
                        existing_adapter = self.module.globals.get(adapter_name)
                        if isinstance(existing_adapter, ir.Function):
                            target_fn = existing_adapter
                        else:
                            adapter_fnty = ir.FunctionType(
                                _CSTR,
                                [_CSTR] * arity,
                            )
                            target_fn = ir.Function(
                                self.module,
                                adapter_fnty,
                                name=adapter_name,
                            )
                            target_fn.linkage = "internal"
                            ab = target_fn.append_basic_block("entry")
                            ab_b = ir.IRBuilder(ab)
                            if ret_void:
                                ab_b.call(fn_ir, list(target_fn.args))
                                py_none_gv = declare_runtime_global(
                                    self.module,
                                    "py_None",
                                )
                                ab_b.ret(ab_b.load(py_none_gv, name="none"))
                            elif ret_int_width == 1:
                                raw = ab_b.call(
                                    fn_ir,
                                    list(target_fn.args),
                                    name="raw",
                                )
                                bit = ab_b.zext(raw, _I32, name="b2i32")
                                boxed = ab_b.call(
                                    self.runtime["py_bool_from_bit"],
                                    [bit],
                                    name="boxed",
                                )
                                ab_b.ret(boxed)
                            else:
                                # ret_int_width == 64
                                raw = ab_b.call(
                                    fn_ir,
                                    list(target_fn.args),
                                    name="raw",
                                )
                                boxed = ab_b.call(
                                    self.runtime["py_int_from_i64"],
                                    [raw],
                                    name="boxed",
                                )
                                ab_b.ret(boxed)
                    fn_ptr = self.builder.bitcast(
                        target_fn,
                        _CSTR,
                        name=self._fresh(f"{expr.ident}.fnptr"),
                    )
                    result = self.builder.call(
                        self.runtime[wrap_helper],
                        [fn_ptr],
                        name=self._fresh(f"cpy.{expr.ident}"),
                    )
                    return self._mark_owned_cpy_value(result)
            # ``globals()[dynamic_name] = value`` writes the shared module
            # namespace even when no statically declared LLVM global exists.
            # Python's LOAD_GLOBAL observes those writes before consulting
            # builtins / raising NameError, so make the dynamic namespace the
            # final lookup before the existing missing-name error.
            module_name = self.ast_module.name or "__main__"
            module_name_ptr = self._ptr_to_cstr(
                self._cstr_global(
                    module_name,
                    self._fresh(".name.module"),
                )
            )
            dynamic_value = self.builder.call(
                self.runtime["py_module_attr_get"],
                [
                    module_name_ptr,
                    self._pooled_cstr_ptr(expr.ident, ".name.dynamic.attr"),
                ],
                name=self._fresh(f"name.dynamic.{expr.ident}"),
            )
            missing = self.builder.icmp_signed(
                "==",
                dynamic_value,
                ir.Constant(_CSTR, None),
                name=self._fresh(f"name.dynamic.{expr.ident}.missing"),
            )
            err_bb = self.current_function.append_basic_block(
                name=self._fresh(f"name.dynamic.{expr.ident}.err")
            )
            ok_bb = self.current_function.append_basic_block(
                name=self._fresh(f"name.dynamic.{expr.ident}.ok")
            )
            self.builder.cbranch(missing, err_bb, ok_bb)

            self.builder.position_at_end(err_bb)
            msg = self._pooled_cstr_ptr(
                "name '" + expr.ident + "' is not defined",
                ".name_error",
            )
            exc = self.builder.call(
                self.runtime["py_exc_new"],
                [ir.Constant(_I64, 10), msg],
                name=self._fresh("name_error"),
            )
            self.builder.call(self.runtime["py_raise"], [exc])
            frame_exc = self.builder.call(
                self.runtime["py_current_exception"],
                [],
                name=self._fresh("name.frame.exc"),
            )
            self._emit_exception_frame(frame_exc, getattr(expr, "span", None))
            err_target = self._current_try_err_block()
            if err_target is None:
                err_target = self._ensure_fn_err_exit()
            self.builder.branch(err_target)

            self.builder.position_at_end(ok_bb)
            # py_module_attr_get returns an owned lookup reference. Static
            # global-name loads are borrowed, so release the lookup ownership;
            # the module namespace remains the value's owner/root.
            self._gc_release(dynamic_value)
            return dynamic_value
        alloca, ir_ty, _ = slot
        if (
            expr.ident in getattr(self, "_gc_rooted_local_names", set())
            and isinstance(ir_ty, ir.PointerType)
            and self._ir_type_matches(ir_ty, _CSTR)
        ):
            load_name = "pcc_gc_load_ptr"
            if expr.ident in getattr(
                self, "_current_param_names", set()
            ) or expr.ident in getattr(self, "_borrowed_gc_rooted_local_names", set()):
                load_name = "pcc_gc_load_borrowed_ptr"
            val = self.builder.call(
                self.runtime[load_name],
                [
                    ir.Constant(_CSTR, None),
                    self._as_gc_ptr(
                        alloca,
                        name=self._fresh(expr.ident + ".gc.slot"),
                    ),
                ],
                name=self._fresh(expr.ident),
            )
        else:
            val = self.builder.load(alloca, name=self._fresh(expr.ident))
        # Re-tag as a CPython value when the binding was recorded as
        # one. Without this, downstream coercions see a bare DynType
        # and route through the pcc (non-CPython) unbox path.
        if getattr(self, "_cpy_env_flags", {}).get(expr.ident, False):
            # J2': inside a generator, cpy loop targets live in their
            # slot as CpyHandle boxes (frame-safe). Unbox here — every
            # downstream consumer keeps receiving the raw cpy pointer.
            gen_stack = getattr(self, "_generator_ctx_stack", ())
            if len(gen_stack) > 0 and expr.ident in gen_stack[-1].get(
                "cpy_boxed_names", ()
            ):
                val = self.builder.call(
                    self.runtime["py_cpy_handle_get"],
                    [val],
                    name=self._fresh(expr.ident + ".cpy.unbox"),
                )
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(val)
        return val
