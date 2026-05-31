"""Comprehension lowering helpers for L1CodeGen."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    BoolLit,
    ByteArrayType,
    BytesType,
    Call,
    ClassType,
    DictType,
    DynType,
    Expr,
    IntType,
    ListExpr,
    ListType,
    MemoryViewType,
    Name,
    StrType,
    Subscript,
    TupleExpr,
    TupleType,
    Type,
)
from . import marshal
from .errors import L1CodegenError


_I1 = ir.IntType(1)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = ir.IntType(8).as_pointer()
_BUILTIN_EXC_TAG = {
    "StopIteration": 8,
}


def _same_type_kind(a: Type, b: Type) -> bool:
    return type(a) is type(b)


class ComprehensionLoweringMixin:
    def _emit_operator_getter(self, getter_name: str, key) -> ir.Value:
        """Emit ``operator.<getter_name>(key)`` at the current builder
        position, returning the resulting CPython callable pointer.
        The result is registered in ``_cpy_values`` so downstream uses
        route through the CPython path."""
        mod_name_gv = self._cstr_global("operator", ".cpy.operator_modname")
        mod_val = self.builder.call(
            self.runtime["py_cpy_import"],
            [self._ptr_to_cstr(mod_name_gv)],
            name=self._fresh("cpy.operator"),
        )
        attr_gv = self._cstr_global(
            getter_name,
            f".cpy.operator.{getter_name}",
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, self._ptr_to_cstr(attr_gv)],
            name=self._fresh(f"cpy.{getter_name}"),
        )
        # Marshal the key: str → CPython str, int → CPython int.
        if isinstance(key, int):
            key_cpy = self.builder.call(
                self.runtime["py_cpy_from_i64"],
                [ir.Constant(_I64, key)],
                name=self._fresh(f"{getter_name}.key.int"),
            )
        else:
            key_bytes = self._utf8_byte_values(key)
            key_gv = self._cstr_global(key, f".cpy.getter.key.{getter_name}")
            # Build CPython str from the C string. ``PyUnicode_FromString``
            # isn't exposed via the pcc ABI directly; use
            # ``py_str_new + py_cpy_from_pccstr`` as the bridge.
            pcc_str = self.builder.call(
                self.runtime["py_str_new"],
                [
                    self._ptr_to_cstr(key_gv),
                    ir.Constant(_I64, len(key_bytes)),
                ],
                name=self._fresh(f"{getter_name}.key.pccstr"),
            )
            key_cpy = self.builder.call(
                self.runtime["py_cpy_from_pccstr"],
                [pcc_str],
                name=self._fresh(f"{getter_name}.key.cpystr"),
            )
        result = self.builder.call(
            self.runtime["py_cpy_call1"],
            [fn_val, key_cpy],
            name=self._fresh(f"cpy.{getter_name}.call"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result
    def _emit_comprehension(self, expr: Call, kind: str) -> ir.Value:
        """Lower list/set/dict comprehension sentinels into explicit
        loops over a freshly-allocated runtime container.

        The native parser lifts comprehensions to::

            _list_comp(elt,          _gen_clause(target, iter, (ifs,)), ...)
            _set_comp(elt,           _gen_clause(...), ...)
            _dict_comp(TupleExpr(k,v), _gen_clause(...), ...)

        while the CPython-AST path emits::

            __listcomp__(elt,
                         ((target, iter, (ifs,), is_async), ...))
            __setcomp__(elt,  ...)
            __dictcomp__(key, val,
                         ((target, iter, (ifs,), is_async), ...))

        Only single-generator, non-async forms with a plain ``Name``
        target are supported here.
        """
        if not isinstance(expr.func, Name):
            raise NotImplementedError("comprehension sentinel lost its name")
        sentinel = expr.func.ident
        is_native = not sentinel.startswith("__")

        # Extract the element expression + any auxiliary per-kind value.
        if kind == "dict":
            if is_native:
                if len(expr.args) < 2:
                    raise NotImplementedError(
                        "_dict_comp expects (TupleExpr(k,v), _gen_clause…)"
                    )
                elt = expr.args[0]
                if not isinstance(elt, TupleExpr) or len(elt.elems) != 2:
                    raise NotImplementedError(
                        "_dict_comp element must be TupleExpr(k, v)"
                    )
                key_expr, val_expr = elt.elems
                gen_args = expr.args[1:]
            else:  # __dictcomp__(key, val, ((...),))
                if len(expr.args) != 3:
                    raise NotImplementedError(
                        "__dictcomp__ expects (key, val, generators)"
                    )
                key_expr, val_expr, gens_tuple = expr.args
                gen_args = (gens_tuple,)
        else:
            if len(expr.args) < 2:
                raise NotImplementedError(
                    f"{sentinel} expects elt plus at least one generator"
                )
            elt_expr = expr.args[0]
            gen_args = expr.args[1:]

        # Decode generator clauses.
        def _native_gen(gen_call: Expr):
            if not (
                isinstance(gen_call, Call)
                and isinstance(gen_call.func, Name)
                and gen_call.func.ident == "_gen_clause"
                and len(gen_call.args) == 3
            ):
                return None
            target, iter_e, ifs_tuple = gen_call.args
            return target, iter_e, ifs_tuple, False

        def _cpy_gen(gen_tuple: Expr):
            if not (isinstance(gen_tuple, TupleExpr) and len(gen_tuple.elems) == 4):
                return None
            target, iter_e, ifs_tuple, is_async = gen_tuple.elems
            async_flag = isinstance(is_async, BoolLit) and is_async.value
            return target, iter_e, ifs_tuple, async_flag

        generators: list = []
        if is_native:
            for g in gen_args:
                u = _native_gen(g)
                if u is None:
                    raise NotImplementedError(f"malformed {sentinel} generator clause")
                generators.append(u)
        else:
            gens_tuple = gen_args[0]
            if not isinstance(gens_tuple, TupleExpr):
                raise NotImplementedError(
                    f"{sentinel} generators arg must be a TupleExpr"
                )
            for g in gens_tuple.elems:
                u = _cpy_gen(g)
                if u is None:
                    raise NotImplementedError(f"malformed {sentinel} generator tuple")
                generators.append(u)

        for _, _, _, is_async in generators:
            if is_async:
                raise NotImplementedError("Layer 1 comprehensions are sync-only")
        # Desugar tuple targets: ``for (a, b) in pairs`` becomes a fresh
        # scalar name + an unpack-assign that the inner body emits
        # before its own work. Stash the unpacks per-generator so the
        # innermost body in the chain below sees them at the right
        # nesting level.
        tuple_unpacks: list = []
        desugared = []
        for target, iter_e, ifs_tuple, is_async in generators:
            if isinstance(target, TupleExpr):
                tmp_name = self._fresh("comp_pair")
                # The temp carries the iter's *element* type so the
                # tuple-unpack runtime branch picks the right shape.
                elem_ty = DynType(name="dyn")
                if isinstance(iter_e.ty, ListType):
                    elem_ty = iter_e.ty.elem
                elif isinstance(iter_e.ty, TupleType) and iter_e.ty.elems:
                    first = iter_e.ty.elems[0]
                    if all(
                        _same_type_kind(e, first) and e == first
                        for e in iter_e.ty.elems
                    ):
                        elem_ty = first
                tmp_ref = Name(
                    span=target.span,
                    ty=elem_ty,
                    ident=tmp_name,
                )
                unpack_stmt = Assign(
                    span=target.span,
                    targets=(target,),
                    value=tmp_ref,
                    annotation=None,
                )
                desugared.append((tmp_ref, iter_e, ifs_tuple, is_async))
                tuple_unpacks.append(unpack_stmt)
            elif isinstance(target, Name):
                desugared.append((target, iter_e, ifs_tuple, is_async))
                tuple_unpacks.append(None)
            elif isinstance(target, (Attr, Subscript)):
                tmp_name = self._fresh("comp_target")
                tmp_ref = Name(
                    span=target.span,
                    ty=DynType(name="dyn"),
                    ident=tmp_name,
                )
                assign_stmt = Assign(
                    span=target.span,
                    targets=(target,),
                    value=tmp_ref,
                    annotation=None,
                )
                desugared.append((tmp_ref, iter_e, ifs_tuple, is_async))
                tuple_unpacks.append(assign_stmt)
            else:
                raise NotImplementedError(
                    "Layer 1 comprehension target must be a Name or "
                    "TupleExpr/Attr/Subscript"
                )
        generators = desugared

        # Allocate result container.
        if kind == "list":
            container = self.builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("listcomp"),
            )
        elif kind == "set":
            container = self.builder.call(
                self.runtime["py_set_new"],
                [],
                name=self._fresh("setcomp"),
            )
        elif kind == "dict":
            container = self.builder.call(
                self.runtime["py_dict_new"],
                [],
                name=self._fresh("dictcomp"),
            )
        else:
            raise NotImplementedError(f"comprehension kind {kind!r} not supported")

        self._emit_comprehension_level(
            kind,
            container,
            generators,
            tuple_unpacks,
            0,
            elt_expr if kind != "dict" else None,
            key_expr if kind == "dict" else None,
            val_expr if kind == "dict" else None,
        )
        return container
    def _emit_comprehension_after_bind(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        _target, _iter_e, ifs_tuple, _is_async = generators[idx]
        if_exprs: tuple = ()
        if isinstance(ifs_tuple, TupleExpr):
            if_exprs = ifs_tuple.elems
        unpack_stmt = None
        if idx < len(tuple_unpacks):
            unpack_stmt = tuple_unpacks[idx]
        if unpack_stmt is not None:
            self._emit_assign(unpack_stmt)

        if_exits: list = []
        for cond_expr in if_exprs:
            cond_val = self._emit_expr(cond_expr)
            cond_b = self._truthy(cond_val, cond_expr.ty)
            keep_bb = self.current_function.append_basic_block(
                name=self._fresh(f"{kind}comp.keep"),
            )
            skip_bb = self.current_function.append_basic_block(
                name=self._fresh(f"{kind}comp.skip"),
            )
            self.builder.cbranch(cond_b, keep_bb, skip_bb)
            self.builder.position_at_end(keep_bb)
            if_exits.append(skip_bb)

        self._emit_comprehension_level(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx + 1,
            elt_expr,
            key_expr,
            val_expr,
        )

        i = len(if_exits) - 1
        while i >= 0:
            skip_bb = if_exits[i]
            if not self._builder_block_is_terminated():
                self.builder.branch(skip_bb)
            self.builder.position_at_end(skip_bb)
            i -= 1
    def _emit_enumerate_loop_in_comp(
        self,
        target: Name,
        inner_iter: Expr,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Lower ``enumerate(xs)`` inside a comprehension.

        The comprehension's tuple-target desugar already rewrote
        ``[... for (i, x) in enumerate(xs)]`` so ``target`` is a
        fresh scalar Name carrying the iter's element type
        (``tuple[int, X]``); the ``i, x = __comp_pair__`` unpack
        statement is emitted by ``_emit_comprehension_after_bind``.

        We build a 2-element tuple per iteration and bind it to
        ``target`` so the unpack sees the expected shape.
        """
        inner_val = self._emit_expr(inner_iter)
        if inner_val in getattr(self, "_cpy_values", ()):
            iter_obj = self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [inner_val],
                name=self._fresh("enum.cpy.bridge"),
            )
        elif isinstance(inner_iter.ty, DictType):
            iter_obj = self.builder.call(
                self.runtime["py_dict_keys"],
                [inner_val],
                name=self._fresh("enum.dict.keys"),
            )
        else:
            iter_obj = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                inner_val,
                inner_iter.ty,
            )
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [iter_obj],
            name=self._fresh("enum.len"),
        )

        target_alloca = self._alloca_in_entry(
            _CSTR,
            name=f"{target.ident}.addr",
        )
        self.env[target.ident] = (
            target_alloca,
            _CSTR,
            DynType(name="dyn"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="enum.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("enum.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("enum.body"))
        step_bb = fn.append_basic_block(name=self._fresh("enum.step"))
        end_bb = fn.append_basic_block(name=self._fresh("enum.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("enum.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("enum.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("enum.idx.box"),
        )
        elem_obj = self.builder.call(
            self.runtime["py_obj_getitem"],
            [iter_obj, idx_box],
            name=self._fresh("enum.elem"),
        )
        pair = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(_I64, 2)],
            name=self._fresh("enum.pair.new"),
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [pair, ir.Constant(_I64, 0), idx_box],
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [pair, ir.Constant(_I64, 1), elem_obj],
        )
        self.builder.store(pair, target_alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("enum.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("enum.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
    def _emit_comprehension_indexed(
        self,
        target: Name,
        iter_val: ir.Value,
        iter_ty,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Indexed iteration over a typed list / tuple: same shape
        as ``_emit_for_list_index`` but the inner block advances the
        explicit comprehension context instead of a Python callback."""
        fn = self.current_function
        iter_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            iter_val,
            iter_ty,
        )
        if isinstance(iter_ty, ListType):
            len_helper = "py_list_len"
            get_helper = "py_list_get"
            elem_ty = iter_ty.elem
        else:
            len_helper = "py_tuple_len"
            get_helper = "py_tuple_get"
            elem_ty = DynType(name="dyn")
        n_val = self.builder.call(
            self.runtime[len_helper],
            [iter_obj],
            name=self._fresh("comp.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        if isinstance(elem_ty, DynType):
            target_ir_ty = _CSTR
        else:
            target_ir_ty = self._map_type(elem_ty)
        alloca = self._alloca_in_entry(
            target_ir_ty,
            name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, target_ir_ty, elem_ty)

        cond_bb = fn.append_basic_block(name=self._fresh("comp.idx.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.idx.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.idx.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.idx.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("comp.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        elem_obj = self.builder.call(
            self.runtime[get_helper],
            [iter_obj, cur],
            name=self._fresh("comp.elem"),
        )
        if isinstance(elem_ty, DynType):
            self.builder.store(elem_obj, alloca)
        else:
            native_val = marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                elem_obj,
                elem_ty,
            )
            self.builder.store(native_val, alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("comp.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
    def _emit_comprehension_obj_indexed(
        self,
        target: Name,
        iter_val: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Generic DynType iteration via ``py_obj_len`` +
        ``py_obj_getitem`` — mirrors ``_emit_for_obj_index``."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [iter_val],
            name=self._fresh("comp.obj.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.obj.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        target_ident = target.ident
        alloca = self._alloca_in_entry(
            _CSTR,
            name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.obj.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.obj.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.obj.step"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.obj.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("comp.obj.cond"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("comp.obj.idx.box"),
        )
        elem = self.builder.call(
            self.runtime["py_obj_getitem"],
            [iter_val, idx_box],
            name=self._fresh("comp.obj.elem"),
        )
        self.builder.store(elem, alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.obj.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("comp.obj.idx.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
    def _emit_comprehension_obj_iterator(
        self,
        target: Name,
        iter_val: ir.Value,
        iter_ty: Type,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Generic object iteration via ``iter(obj)`` / ``next(it)``."""
        fn = self.current_function
        if not isinstance(iter_val.type, ir.PointerType):
            iter_val = marshal.marshal_to_object(
                self.builder,
                self.module,
                self.runtime,
                iter_val,
                iter_ty,
            )
        iterator = self.builder.call(
            self.runtime["py_obj_iter"],
            [iter_val],
            name=self._fresh("comp.iter.obj"),
        )
        self._emit_post_call_err_check(getattr(target, "span", None))

        target_ident = target.ident
        alloca = self._alloca_in_entry(
            _CSTR,
            name=f"{target_ident}.addr",
        )
        self.env[target_ident] = (alloca, _CSTR, DynType(name="dyn"))

        header_bb = fn.append_basic_block(name=self._fresh("comp.iter.next"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.iter.body"))
        maybe_end_bb = fn.append_basic_block(name=self._fresh("comp.iter.maybe_end"))
        clear_bb = fn.append_basic_block(name=self._fresh("comp.iter.clear"))
        propagate_bb = fn.append_basic_block(name=self._fresh("comp.iter.propagate"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.iter.end"))

        self.builder.branch(header_bb)
        self.builder.position_at_end(header_bb)
        item = self.builder.call(
            self.runtime["py_obj_next"],
            [iterator],
            name=self._fresh("comp.iter.item"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            item,
            ir.Constant(_CSTR, None),
            name=self._fresh("comp.iter.null"),
        )
        self.builder.cbranch(is_null, maybe_end_bb, body_bb)

        self.builder.position_at_end(body_bb)
        self.builder.store(item, alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(header_bb)

        self.builder.position_at_end(maybe_end_bb)
        current_exc = self.builder.call(
            self.runtime["py_current_exception"],
            [],
            name=self._fresh("comp.iter.cur_exc"),
        )
        stop_cls = self.builder.call(
            self.runtime["py_exc_builtin_class"],
            [ir.Constant(_I64, _BUILTIN_EXC_TAG["StopIteration"])],
            name=self._fresh("comp.iter.stop_cls"),
        )
        match_i64 = self.builder.call(
            self.runtime["py_exc_matches"],
            [current_exc, stop_cls],
            name=self._fresh("comp.iter.stop_match"),
        )
        is_stop = self.builder.icmp_signed(
            "!=",
            match_i64,
            ir.Constant(_I64, 0),
            name=self._fresh("comp.iter.stop_i1"),
        )
        self.builder.cbranch(is_stop, clear_bb, propagate_bb)

        self.builder.position_at_end(clear_bb)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.branch(end_bb)

        self.builder.position_at_end(propagate_bb)
        err_target = getattr(self, "_try_err_block", None)
        if err_target is None:
            err_target = self._ensure_fn_err_exit()
        self.builder.branch(err_target)

        self.builder.position_at_end(end_bb)
    def _emit_comprehension_generator(
        self,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """Emit one generator level for an explicit comprehension
        context. Supports ``range(...)`` iters,
        ``enumerate(xs)`` (desugar to indexed loop with a synthetic
        counter), CPython iterables, typed list / tuple / dict
        containers, and generic DynType containers via ``py_obj_len``
        + ``py_obj_getitem``."""
        target, iter_e, _ifs_tuple, _is_async = generators[idx]
        # Fast path: range(...) iter.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident in ("range", "xrange")
        ):
            self._emit_range_loop(
                target,
                iter_e,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        # enumerate(xs) — the comprehension tuple-target desugaring
        # above synthesises ``__comp_pair__`` whose value we build
        # here as a ``(i, xs_elem)`` pair to feed into the unpack.
        if (
            isinstance(iter_e, Call)
            and isinstance(iter_e.func, Name)
            and iter_e.func.ident == "enumerate"
            and len(iter_e.args) == 1
            and not iter_e.kwargs
        ):
            self._emit_enumerate_loop_in_comp(
                target,
                iter_e.args[0],
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        iter_val = self._emit_expr(iter_e)
        if iter_val in getattr(self, "_cpy_values", ()):
            self._emit_cpy_iter_loop(
                target,
                iter_val,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        iter_ty = iter_e.ty
        if isinstance(iter_ty, (ListType, TupleType)):
            self._emit_comprehension_indexed(
                target,
                iter_val,
                iter_ty,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        if isinstance(iter_ty, DictType):
            keys_val = self.builder.call(
                self.runtime["py_dict_keys"],
                [iter_val],
                name=self._fresh("comp.dict.keys"),
            )
            synthetic_ty = ListType(name="list", elem=iter_ty.key)
            self._emit_comprehension_indexed(
                target,
                keys_val,
                synthetic_ty,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        if isinstance(iter_ty, StrType):
            self._emit_comprehension_str_chars(
                target,
                iter_val,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        if isinstance(iter_ty, (BytesType, ByteArrayType, MemoryViewType)) or (
            isinstance(iter_ty, ClassType)
            and iter_ty.name in ("bytes", "bytearray", "memoryview")
        ):
            self._emit_comprehension_obj_indexed(
                target,
                iter_val,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        if isinstance(iter_ty, DynType):
            # Iterate DynType sources via the iterator protocol
            # (py_obj_iter/py_obj_next), matching the statement for-loop and
            # the ClassType arm below. The older len+py_obj_getitem path
            # (_emit_comprehension_obj_indexed) silently produced an empty
            # result for iterator-only objects such as generators, which have
            # no length / __getitem__. See
            # docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md
            self._emit_comprehension_obj_iterator(
                target,
                iter_val,
                iter_ty,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        if isinstance(iter_ty, ClassType):
            self._emit_comprehension_obj_iterator(
                target,
                iter_val,
                iter_ty,
                kind,
                container,
                generators,
                tuple_unpacks,
                idx,
                elt_expr,
                key_expr,
                val_expr,
            )
            return
        span = getattr(iter_e, "span", None)
        where = f" at {span.file}:{span.line}:{span.col}" if span is not None else ""
        raise NotImplementedError(
            "Layer 1 comprehension iter must be range(...) or a "
            "CPython-backed iterable; got "
            f"{type(iter_ty).__name__}(name={getattr(iter_ty, 'name', '?')!r})"
            f"{where}"
        )
    def _emit_comprehension_str_chars(
        self,
        target: Name,
        iter_val: ir.Value,
        kind: str,
        container: ir.Value,
        generators: list,
        tuple_unpacks: list,
        idx: int,
        elt_expr,
        key_expr,
        val_expr,
    ) -> None:
        """StrType comprehension iter: slice each char."""
        fn = self.current_function
        n_val = self.builder.call(
            self.runtime["py_str_len"],
            [iter_val],
            name=self._fresh("comp.str.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="comp.str.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, 1)],
            name=self._fresh("comp.str.step"),
        )
        tgt_name = target.ident
        if tgt_name not in self.env:
            alloca = self._alloca_in_entry(
                _CSTR,
                name=f"{tgt_name}.addr",
            )
            self.env[tgt_name] = (alloca, _CSTR, StrType(name="str"))

        cond_bb = fn.append_basic_block(name=self._fresh("comp.str.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("comp.str.body"))
        step_bb = fn.append_basic_block(name=self._fresh("comp.str.step_bb"))
        end_bb = fn.append_basic_block(name=self._fresh("comp.str.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("comp.str.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("comp.str.cond.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        lo_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("comp.str.lo"),
        )
        hi = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("comp.str.hi.i64"),
        )
        hi_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [hi],
            name=self._fresh("comp.str.hi"),
        )
        ch = self.builder.call(
            self.runtime["py_str_slice"],
            [iter_val, lo_box, hi_box, one_box],
            name=self._fresh("comp.str.ch"),
        )
        alloca, _, _ = self.env[tgt_name]
        self.builder.store(ch, alloca)
        self._emit_comprehension_after_bind(
            kind,
            container,
            generators,
            tuple_unpacks,
            idx,
            elt_expr,
            key_expr,
            val_expr,
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("comp.str.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("comp.str.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
