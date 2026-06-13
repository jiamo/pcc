"""List, dict, and tuple literal lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Call,
    DictExpr,
    DynType,
    Expr,
    FuncType,
    ListExpr,
    ListType,
    Name,
    TupleExpr,
    Type,
)
from . import marshal
from .runtime_abi import declare_runtime_global


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


class LiteralLoweringMixin:
    def _emit_str_literal(self, value: str) -> ir.Value:
        existing = self._str_obj_pool.get(value)
        if existing is None:
            data = self._utf8_byte_values(value)
            body = data + [0]
            data_ty = ir.ArrayType(_I8, len(body))
            obj_ty = ir.LiteralStructType(
                [
                    _I64,
                    _I32,
                    _I32,
                    _I64,
                    _I64,
                    _I64,
                    data_ty,
                ]
            )
            self._str_counter += 1
            gv = ir.GlobalVariable(
                self.module,
                obj_ty,
                name=f".pystr.obj.{self._str_counter}",
            )
            gv.linkage = "internal"
            gv.global_constant = False
            gv.initializer = ir.Constant(
                obj_ty,
                [
                    ir.Constant(_I64, 1),
                    ir.Constant(_I32, 4),
                    ir.Constant(_I32, 1),
                    ir.Constant(_I64, len(data)),
                    ir.Constant(_I64, -1),
                    ir.Constant(_I64, -1),
                    ir.Constant(data_ty, body),
                ],
            )
            self._str_obj_pool[value] = gv
            existing = gv
        return existing

    def _emit_bytes_literal(self, value: bytes) -> ir.Value:
        self._str_counter += 1
        body = [int(b) & 0xFF for b in value] + [0]
        arr_ty = ir.ArrayType(_I8, len(body))
        gv = ir.GlobalVariable(
            self.module, arr_ty, name=f".pybytes.{self._str_counter}"
        )
        gv.linkage = "internal"
        gv.global_constant = True
        gv.initializer = ir.Constant(arr_ty, body)
        zero = ir.Constant(_I32, 0)
        ptr = self.builder.gep(
            gv, [zero, zero], inbounds=True, name=self._fresh("pybytes.ptr")
        )
        return self.builder.call(
            self.runtime["py_bytes_new"],
            [ptr, ir.Constant(_I64, len(value))],
            name=self._fresh("bytes.new"),
        )

    def _emit_none_literal(self) -> ir.Value:
        gv = declare_runtime_global(self.module, "py_None")
        return self.builder.load(gv, name=self._fresh("none"))

    def _emit_int_literal_object(self, value: int) -> ir.Value:
        if -(1 << 63) <= value <= (1 << 63) - 1:
            return self.builder.call(
                self.runtime["py_int_from_i64"],
                [ir.Constant(_I64, value)],
                name=self._fresh("print.int.lit"),
            )
        gv, _ = self._cstr_literal(str(value))
        return self.builder.call(
            self.runtime["py_int_from_cstr"],
            [self._ptr_to_cstr(gv), ir.Constant(_I32, 10)],
            name=self._fresh("print.int.lit.big"),
        )

    def _emit_literal_str(self, s: str) -> ir.Value:
        return self._emit_str_literal(s)

    def _emit_expr_with_native_callable_values(self, expr: Expr) -> ir.Value:
        old = self._prefer_native_callable_values
        self._prefer_native_callable_values = True
        value = self._emit_expr(expr)
        self._prefer_native_callable_values = old
        return value

    def _enter_container_temp_root(
        self,
        value: ir.Value,
        label: str,
    ) -> ir.Value:
        """Root a container while its literal elements are still being built.

        List/dict/tuple literals create the container first and then populate
        it. During that construction window the container is an SSA temporary,
        not yet stored into the assignment target's GC frame slot. Tracing
        backends can run from another real thread, so protect the temporary
        through the existing frame-root ABI until the literal is complete.
        """
        if not isinstance(value.type, ir.PointerType):
            return ir.Constant(_CSTR, None)
        slot = self._alloca_in_entry(
            _CSTR,
            name=label + ".tmp.root",
            init_null=True,
        )
        rooted_value = value
        if rooted_value.type != _CSTR:
            rooted_value = self.builder.bitcast(
                value,
                _CSTR,
                name=self._fresh(label + ".tmp.ptr"),
            )
        persistent_thread_root = (
            self.current_func_def is not None
            and getattr(self, "_runtime_threads_enabled", False)
        )
        if persistent_thread_root:
            old_rooted_value = self.builder.load(
                slot,
                name=self._fresh(label + ".tmp.old"),
            )
            self.builder.call(
                self.runtime["pcc_gc_unpin"],
                [old_rooted_value],
            )
        self.builder.call(self.runtime["pcc_gc_pin"], [rooted_value])
        self.builder.call(
            self.runtime["pcc_gc_store_root"],
            [
                self._as_gc_ptr(slot, name=self._fresh(label + ".tmp.root.ptr")),
                rooted_value,
            ],
        )
        if persistent_thread_root:
            root_name = self._fresh(label + ".tmp.root.local")
            self.env[root_name] = (slot, _CSTR, DynType(name="dyn"))
            self._pinned_gc_rooted_local_names.add(root_name)
            # Route through the per-slot registry (not a raw entry enter) so
            # every function exit emits the balancing frame_leave for this
            # slot; cleanup no longer leaves roots by name.
            self._ensure_local_gc_frame_root(
                root_name,
                slot,
                _CSTR,
                self._gc_one_slot_frame_map(),
            )
        else:
            self._emit_current_gc_frame_enter_lifo(
                self._gc_one_slot_frame_map(),
                slot,
            )
        return slot

    def _leave_container_temp_root(self, slot: ir.Value) -> None:
        if isinstance(slot, ir.Constant):
            return
        slot_ptr = self._as_gc_ptr(
            slot,
            name=self._fresh("container.tmp.root.ptr"),
        )
        pinned = self.builder.call(
            self.runtime["pcc_gc_load_ptr"],
            [ir.Constant(_CSTR, None), slot_ptr],
            name=self._fresh("container.tmp.unpin"),
        )
        if (
            self.current_func_def is not None
            and getattr(self, "_runtime_threads_enabled", False)
        ):
            return
        self.builder.call(
            self.runtime["pcc_gc_store_root"],
            [
                slot_ptr,
                ir.Constant(_CSTR, None),
            ],
        )
        self._emit_gc_frame_leave_lifo_for_slot(slot)
        self.builder.call(self.runtime["pcc_gc_unpin"], [pinned])

    def _emit_list_literal(self, expr: ListExpr) -> ir.Value:
        has_exact_int_boundary = any(
            self._int_expr_needs_exact_object_boundary(el) for el in expr.elems
        )
        if has_exact_int_boundary and not any(
            self._expr_looks_cpython(
                el.args[0]
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident == "*"
                    and len(el.args) == 1
                )
                else el
            )
            for el in expr.elems
        ):
            n_val = ir.Constant(_I64, len(expr.elems))
            lst = self.builder.call(
                self.runtime["py_list_new"],
                [n_val],
                name=self._fresh("list.new"),
            )
            lst_root = self._enter_container_temp_root(lst, "list")
            for el in expr.elems:
                if (
                    isinstance(el, Call)
                    and isinstance(el.func, Name)
                    and el.func.ident == "*"
                    and len(el.args) == 1
                ):
                    inner = self._emit_expr(el.args[0])
                    self.builder.call(
                        self.runtime["py_list_extend"],
                        [lst, inner],
                    )
                    continue
                v_obj = self._emit_expr_as_pcc_object(el)
                self.builder.call(
                    self.runtime["py_list_append"],
                    [lst, v_obj],
                )
                if self._container_store_temp_needs_release(el, el.ty, False):
                    self._gc_release(
                        v_obj,
                        self._release_expr_label("container", el),
                    )
            self._leave_container_temp_root(lst_root)
            return lst

        ops: list[tuple[str, ir.Value, Type, bool, Expr, bool]] = []
        cpy_values = self._cpy_values
        cpy_extend = False
        # ListType/FuncType are canonical across compiled-module boundaries;
        # callable materialization is therefore driven by the inferred type,
        # not by a syntax-only list-of-function-names exception.
        prefer_native_callables = isinstance(expr.ty, ListType) and isinstance(
            expr.ty.elem,
            FuncType,
        )
        for el in expr.elems:
            if (
                isinstance(el, Call)
                and isinstance(el.func, Name)
                and el.func.ident == "*"
                and len(el.args) == 1
            ):
                inner = self._emit_expr(el.args[0])
                is_cpy = inner in cpy_values
                pinned = False
                if isinstance(inner.type, ir.PointerType) and not is_cpy:
                    self.builder.call(self.runtime["pcc_gc_pin"], [inner])
                    pinned = True
                ops.append(
                    ("extend", inner, el.args[0].ty, is_cpy, el.args[0], pinned)
                )
                if is_cpy:
                    cpy_extend = True
                continue
            if prefer_native_callables:
                v = self._emit_expr_with_native_callable_values(el)
            else:
                valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
                    el.ty,
                    el,
                )
                if valueclass_payload is not None:
                    v = valueclass_payload
                else:
                    v = self._emit_expr(el)
            is_cpy = v in cpy_values
            pinned = False
            if isinstance(v.type, ir.PointerType) and not is_cpy:
                self.builder.call(self.runtime["pcc_gc_pin"], [v])
                pinned = True
            ops.append(("append", v, el.ty, is_cpy, el, pinned))
        # Build a real CPython list when the literal SPREADS a cpy iterable
        # (``cpy_extend``) OR when EVERY element is a cpy value (e.g. ``[a, a]``
        # of numpy arrays passed to ``np.concatenate([...])``): bridging those
        # cpy elements to pcc objects would round-trip a real CPython object
        # cpy->pcc->cpy and lose it, so ``_emit_cpython_list_ops`` keeps them
        # borrowed. A MIXED literal — at least one native element, e.g.
        # ``[p, x, p]`` with a native ``str`` ``p`` — is a native pcc list: the
        # path below builds ``py_list_new``/``py_list_append`` and bridges only
        # the cpy elements via ``py_cpy_to_pcc_obj``. (A native element cannot be
        # kept cpy without a native->cpy round-trip anyway, so its presence
        # signals native-list intent.) Inert for all-native lists / no cpy
        # values => bootstrap unaffected.
        cpy_all_elems = bool(ops) and all(op[3] for op in ops)
        cpy_any = cpy_extend or cpy_all_elems
        if cpy_any:
            result = self._emit_cpython_list_ops(
                [(k, v, t) for (k, v, t, _, _, _) in ops]
            )
            for _op_kind, value, _value_ty, _is_cpy, _src_expr, pinned in ops:
                if pinned:
                    self.builder.call(self.runtime["pcc_gc_unpin"], [value])
            return result
        n_val = ir.Constant(_I64, len(expr.elems))
        lst = self.builder.call(
            self.runtime["py_list_new"],
            [n_val],
            name=self._fresh("list.new"),
        )
        lst_root = self._enter_container_temp_root(lst, "list")
        for op_kind, value, value_ty, is_cpy, src_expr, pinned in ops:
            if op_kind == "extend":
                self.builder.call(
                    self.runtime["py_list_extend"],
                    [lst, value],
                )
                if pinned:
                    self.builder.call(self.runtime["pcc_gc_unpin"], [value])
                continue
            v_obj = self._emit_value_as_pcc_object_or_bridge(
                value,
                value_ty,
                "cpy.list.elem" if is_cpy else "list.elem",
                consume_valueclass_payload_fields=
                    self._valueclass_payload_expr_fields_are_owned(src_expr),
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [lst, v_obj],
            )
            if self._container_store_temp_needs_release(src_expr, value_ty, is_cpy):
                self._gc_release(
                    v_obj,
                    self._release_expr_label("container", src_expr),
                )
            if pinned:
                self.builder.call(self.runtime["pcc_gc_unpin"], [value])
        self._leave_container_temp_root(lst_root)
        return lst

    def _emit_dict_literal_with_splat(self, expr: DictExpr) -> ir.Value:
        # ``{**m, "k": v, **n}`` — the lift encodes each ``**mapping`` as a pair
        # whose key is the sentinel Name("**"). Build a fresh dict, merging a
        # splat via py_dict_update and setting an ordinary pair via py_dict_set,
        # in source order (CPython semantics: later entries win).
        d = self.builder.call(
            self.runtime["py_dict_new"], [], name=self._fresh("dict.new")
        )
        dict_root = self._enter_container_temp_root(d, "dict")
        for k_expr, v_expr in expr.pairs:
            if isinstance(k_expr, Name) and k_expr.ident == "**":
                m_obj = self._emit_expr_as_pcc_object(v_expr)
                self.builder.call(
                    self.runtime["py_dict_update"],
                    [d, m_obj],
                    name=self._fresh("dict.splat.update"),
                )
                if self._container_store_temp_needs_release(
                    v_expr, v_expr.ty, False
                ):
                    self._gc_release(
                        m_obj,
                        self._release_expr_label("container", v_expr),
                    )
            else:
                k_obj = self._emit_expr_as_pcc_object(k_expr)
                v_obj = self._emit_expr_as_pcc_object(v_expr)
                self.builder.call(self.runtime["py_dict_set"], [d, k_obj, v_obj])
                # py_dict_set borrows (balanced store); owned key/value
                # temps must be released here, mirroring py_list_append.
                if self._container_store_temp_needs_release(
                    k_expr, k_expr.ty, False
                ):
                    self._gc_release(
                        k_obj,
                        self._release_expr_label("container", k_expr),
                    )
                if self._container_store_temp_needs_release(
                    v_expr, v_expr.ty, False
                ):
                    self._gc_release(
                        v_obj,
                        self._release_expr_label("container", v_expr),
                    )
        self._leave_container_temp_root(dict_root)
        return d

    def _emit_dict_literal(self, expr: DictExpr) -> ir.Value:
        # ``{**m, ...}`` — the lift encodes a ``**mapping`` splat as a pair whose
        # key is the sentinel Name("**"). Route to the splat-aware builder so the
        # sentinel isn't emitted as a real key lookup (which raised a runtime
        # NameError "name '**' is not defined").
        if any(
            isinstance(k_expr, Name) and k_expr.ident == "**"
            for k_expr, _v_expr in expr.pairs
        ):
            return self._emit_dict_literal_with_splat(expr)
        has_exact_int_boundary = any(
            self._int_expr_needs_exact_object_boundary(k_expr)
            or self._int_expr_needs_exact_object_boundary(v_expr)
            for k_expr, v_expr in expr.pairs
        )
        if has_exact_int_boundary and not any(
            self._expr_looks_cpython(k_expr) or self._expr_looks_cpython(v_expr)
            for k_expr, v_expr in expr.pairs
        ):
            d = self.builder.call(
                self.runtime["py_dict_new"],
                [],
                name=self._fresh("dict.new"),
            )
            dict_root = self._enter_container_temp_root(d, "dict")
            for k_expr, v_expr in expr.pairs:
                k_obj = self._emit_expr_as_pcc_object(k_expr)
                v_obj = self._emit_expr_as_pcc_object(v_expr)
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [d, k_obj, v_obj],
                )
                # py_dict_set borrows (balanced store); owned key/value
                # temps must be released here, mirroring py_list_append.
                if self._container_store_temp_needs_release(
                    k_expr, k_expr.ty, False
                ):
                    self._gc_release(
                        k_obj,
                        self._release_expr_label("container", k_expr),
                    )
                if self._container_store_temp_needs_release(
                    v_expr, v_expr.ty, False
                ):
                    self._gc_release(
                        v_obj,
                        self._release_expr_label("container", v_expr),
                    )
            self._leave_container_temp_root(dict_root)
            return d

        items: list[
            tuple[ir.Value, Type, bool, ir.Value, Type, bool, Expr, Expr]
        ] = []
        has_cpy_key = False
        cpy_values = self._cpy_values
        for k_expr, v_expr in expr.pairs:
            k_payload = self._maybe_emit_valueclass_constructor_payload(
                k_expr.ty,
                k_expr,
            )
            if k_payload is not None:
                k = k_payload
            else:
                k = self._emit_expr(k_expr)
            v_payload = self._maybe_emit_valueclass_constructor_payload(
                v_expr.ty,
                v_expr,
            )
            if v_payload is not None:
                v = v_payload
            else:
                v = self._emit_expr_with_native_callable_values(v_expr)
            k_is_cpy = k in cpy_values
            v_is_cpy = v in cpy_values
            items.append(
                (k, k_expr.ty, k_is_cpy, v, v_expr.ty, v_is_cpy, k_expr, v_expr)
            )
            if k_is_cpy:
                has_cpy_key = True
        if has_cpy_key:
            return self._emit_cpython_dict_items(
                [(k, k_ty, v, v_ty) for (k, k_ty, _, v, v_ty, _, _, _) in items]
            )
        d = self.builder.call(
            self.runtime["py_dict_new"],
            [],
            name=self._fresh("dict.new"),
        )
        dict_root = self._enter_container_temp_root(d, "dict")
        for k, k_ty, k_is_cpy, v, v_ty, v_is_cpy, k_expr, v_expr in items:
            k_obj = self._emit_value_as_pcc_object_or_bridge(
                k,
                k_ty,
                "dict.key",
                consume_valueclass_payload_fields=
                    self._valueclass_payload_expr_fields_are_owned(k_expr),
            )
            v_obj = self._emit_value_as_pcc_object_or_bridge(
                v,
                v_ty,
                "cpy.dict.val" if v_is_cpy else "dict.val",
                consume_valueclass_payload_fields=
                    self._valueclass_payload_expr_fields_are_owned(v_expr),
            )
            self.builder.call(
                self.runtime["py_dict_set"],
                [d, k_obj, v_obj],
            )
            # py_dict_set borrows (balanced store); owned key/value temps
            # must be released here, mirroring the list-literal path.
            if self._container_store_temp_needs_release(k_expr, k_ty, k_is_cpy):
                self._gc_release(
                    k_obj,
                    self._release_expr_label("container", k_expr),
                )
            if self._container_store_temp_needs_release(v_expr, v_ty, v_is_cpy):
                self._gc_release(
                    v_obj,
                    self._release_expr_label("container", v_expr),
                )
        self._leave_container_temp_root(dict_root)
        return d

    def _emit_dict_builtin(self, expr: Call) -> ir.Value:
        if len(expr.args) > 1:
            raise NotImplementedError(
                f"dict() takes at most 1 positional arg at L2; got {len(expr.args)}"
            )
        out = self.builder.call(
            self.runtime["py_dict_new"],
            [],
            name=self._fresh("dict.new"),
        )
        out_root = self._enter_container_temp_root(out, "dict")
        if len(expr.args) == 0:
            for kw_name, kw_expr in expr.kwargs:
                key_obj = self._emit_str_literal(kw_name)
                val_obj = self._emit_expr_as_pcc_object(kw_expr)
                self.builder.call(
                    self.runtime["py_dict_set"],
                    [out, key_obj, val_obj],
                )
            self._leave_container_temp_root(out_root)
            return out

        src_expr = expr.args[0]
        src_val = self._emit_expr(src_expr)
        src_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            src_val,
            src_expr.ty,
        )
        n_val = self.builder.call(
            self.runtime["py_obj_len"],
            [src_obj],
            name=self._fresh("dict.src.len"),
        )
        idx_slot = self._alloca_in_entry(_I64, name="dict.src.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)

        fn = self.current_function
        cond_bb = fn.append_basic_block(name=self._fresh("dict.from.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("dict.from.body"))
        step_bb = fn.append_basic_block(name=self._fresh("dict.from.step"))
        end_bb = fn.append_basic_block(name=self._fresh("dict.from.end"))
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("dict.src.idx"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("dict.src.more"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        idx_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [cur],
            name=self._fresh("dict.src.idx.box"),
        )
        pair_obj = self.builder.call(
            self.runtime["py_obj_getitem"],
            [src_obj, idx_box],
            name=self._fresh("dict.src.pair"),
        )
        zero_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("dict.pair.zero"),
        )
        one_box = self.builder.call(
            self.runtime["py_int_from_i64"],
            [ir.Constant(_I64, 1)],
            name=self._fresh("dict.pair.one"),
        )
        key_obj = self.builder.call(
            self.runtime["py_obj_getitem"],
            [pair_obj, zero_box],
            name=self._fresh("dict.pair.key"),
        )
        val_obj = self.builder.call(
            self.runtime["py_obj_getitem"],
            [pair_obj, one_box],
            name=self._fresh("dict.pair.val"),
        )
        self.builder.call(
            self.runtime["py_dict_set"],
            [out, key_obj, val_obj],
        )
        if not self._builder_block_is_terminated():
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        cur2 = self.builder.load(idx_slot, name=self._fresh("dict.src.idx2"))
        nxt = self.builder.add(
            cur2,
            ir.Constant(_I64, 1),
            name=self._fresh("dict.src.next"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)
        for kw_name, kw_expr in expr.kwargs:
            key_obj = self._emit_str_literal(kw_name)
            val_obj = self._emit_expr_as_pcc_object(kw_expr)
            self.builder.call(
                self.runtime["py_dict_set"],
                [out, key_obj, val_obj],
            )
        self._leave_container_temp_root(out_root)
        return out

    def _emit_tuple_literal(self, expr: TupleExpr) -> ir.Value:
        has_splat = any(
            isinstance(el, Call)
            and isinstance(el.func, Name)
            and el.func.ident in ("*", "__starred__")
            and len(el.args) == 1
            for el in expr.elems
        )
        if not has_splat:
            has_exact_int_boundary = any(
                self._int_expr_needs_exact_object_boundary(el) for el in expr.elems
            )
            if has_exact_int_boundary and not any(
                self._expr_looks_cpython(el) for el in expr.elems
            ):
                n = len(expr.elems)
                n_val = ir.Constant(_I64, n)
                tup = self.builder.call(
                    self.runtime["py_tuple_new"],
                    [n_val],
                    name=self._fresh("tup.new"),
                )
                tup_root = self._enter_container_temp_root(tup, "tuple")
                for i, el in enumerate(expr.elems):
                    v_obj = self._emit_expr_as_pcc_object(el)
                    idx = ir.Constant(_I64, i)
                    self.builder.call(
                        self.runtime["py_tuple_set_item"],
                        [tup, idx, v_obj],
                    )
                    if self._container_store_temp_needs_release(
                        el,
                        el.ty,
                        v_obj in self._cpy_values,
                    ):
                        self._gc_release(
                            v_obj,
                            self._release_expr_label("container", el),
                        )
                self._leave_container_temp_root(tup_root)
                return tup

            ops: list[tuple[str, ir.Value, Type, bool, bool]] = []
            for el in expr.elems:
                valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
                    el.ty,
                    el,
                )
                if valueclass_payload is not None:
                    v = valueclass_payload
                else:
                    v = self._emit_expr(el)
                is_cpy = v in self._cpy_values
                pinned = False
                if isinstance(v.type, ir.PointerType) and not is_cpy:
                    self.builder.call(self.runtime["pcc_gc_pin"], [v])
                    pinned = True
                ops.append(("append", v, el.ty, is_cpy, pinned))
            n = len(ops)
            n_val = ir.Constant(_I64, n)
            tup = self.builder.call(
                self.runtime["py_tuple_new"],
                [n_val],
                name=self._fresh("tup.new"),
            )
            tup_root = self._enter_container_temp_root(tup, "tuple")
            for i, (_op_kind, value, value_ty, is_cpy, pinned) in enumerate(ops):
                v_obj = self._emit_value_as_pcc_object_or_bridge(
                    value,
                    value_ty,
                    "cpy.tup.elem" if is_cpy else "tup.elem",
                    consume_valueclass_payload_fields=
                        self._valueclass_payload_expr_fields_are_owned(expr.elems[i]),
                )
                idx = ir.Constant(_I64, i)
                self.builder.call(
                    self.runtime["py_tuple_set_item"],
                    [tup, idx, v_obj],
                )
                if self._container_store_temp_needs_release(
                    expr.elems[i], value_ty, is_cpy
                ):
                    self._gc_release(
                        v_obj,
                        self._release_expr_label("container", expr.elems[i]),
                    )
                if pinned:
                    self.builder.call(self.runtime["pcc_gc_unpin"], [value])
            self._leave_container_temp_root(tup_root)
            return tup

        ops: list[tuple[str, ir.Value, Type, bool, bool]] = []
        cpy_extend = False
        for el in expr.elems:
            if (
                isinstance(el, Call)
                and isinstance(el.func, Name)
                and el.func.ident in ("*", "__starred__")
                and len(el.args) == 1
            ):
                inner = self._emit_expr(el.args[0])
                is_cpy = inner in self._cpy_values
                pinned = False
                if isinstance(inner.type, ir.PointerType) and not is_cpy:
                    self.builder.call(self.runtime["pcc_gc_pin"], [inner])
                    pinned = True
                ops.append(("extend", inner, el.args[0].ty, is_cpy, pinned))
                if is_cpy:
                    cpy_extend = True
                continue
            valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
                el.ty,
                el,
            )
            if valueclass_payload is not None:
                v = valueclass_payload
            else:
                v = self._emit_expr(el)
            is_cpy = v in self._cpy_values
            pinned = False
            if isinstance(v.type, ir.PointerType) and not is_cpy:
                self.builder.call(self.runtime["pcc_gc_pin"], [v])
                pinned = True
            ops.append(("append", v, el.ty, is_cpy, pinned))
        if cpy_extend:
            result = self._emit_cpython_tuple_ops(
                [(kind, value, ty) for kind, value, ty, _, _ in ops]
            )
            for _op_kind, value, _value_ty, _is_cpy, pinned in ops:
                if pinned:
                    self.builder.call(self.runtime["pcc_gc_unpin"], [value])
            return result
        lst = self.builder.call(
            self.runtime["py_list_new"],
            [ir.Constant(_I64, 0)],
            name=self._fresh("tup.splat.list"),
        )
        lst_root = self._enter_container_temp_root(lst, "tuple.splat.list")
        for op_kind, value, value_ty, is_cpy, pinned in ops:
            if op_kind == "extend":
                self.builder.call(
                    self.runtime["py_list_extend"],
                    [lst, value],
                )
                if pinned:
                    self.builder.call(self.runtime["pcc_gc_unpin"], [value])
                continue
            v_obj = self._emit_value_as_pcc_object_or_bridge(
                value,
                value_ty,
                "cpy.tup.splat.elem" if is_cpy else "tup.splat.elem",
            )
            self.builder.call(
                self.runtime["py_list_append"],
                [lst, v_obj],
            )
            if pinned:
                self.builder.call(self.runtime["pcc_gc_unpin"], [value])
        n_val = self.builder.call(
            self.runtime["py_list_len"],
            [lst],
            name=self._fresh("tup.splat.len"),
        )
        tup = self.builder.call(
            self.runtime["py_tuple_new"],
            [n_val],
            name=self._fresh("tup.splat.new"),
        )
        tup_root = self._enter_container_temp_root(tup, "tuple.splat")
        fn = self.current_function
        idx_slot = self._alloca_in_entry(_I64, name="tup.splat.idx.addr")
        self.builder.store(ir.Constant(_I64, 0), idx_slot)
        cond_bb = fn.append_basic_block(name=self._fresh("tup.sp.cond"))
        body_bb = fn.append_basic_block(name=self._fresh("tup.sp.body"))
        step_bb = fn.append_basic_block(name=self._fresh("tup.sp.step"))
        end_bb = fn.append_basic_block(name=self._fresh("tup.sp.end"))
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cur = self.builder.load(idx_slot, name=self._fresh("tup.sp.i"))
        cond = self.builder.icmp_signed(
            "<",
            cur,
            n_val,
            name=self._fresh("tup.sp.i1"),
        )
        self.builder.cbranch(cond, body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        elem = self.builder.call(
            self.runtime["py_list_get"],
            [lst, cur],
            name=self._fresh("tup.sp.elem"),
        )
        self.builder.call(
            self.runtime["py_tuple_set_item"],
            [tup, cur, elem],
        )
        self.builder.branch(step_bb)
        self.builder.position_at_end(step_bb)
        nxt = self.builder.add(
            cur,
            ir.Constant(_I64, 1),
            name=self._fresh("tup.sp.inc"),
        )
        self.builder.store(nxt, idx_slot)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)
        self._leave_container_temp_root(tup_root)
        self._leave_container_temp_root(lst_root)
        return tup

    def _emit_cpython_list_ops(
        self,
        ops: list[tuple[str, ir.Value, Type]],
    ) -> ir.Value:
        list_ctor = self._load_cpython_builtin("list")
        lst = self.builder.call(
            self.runtime["py_cpy_call_noargs"],
            [list_ctor],
            name=self._fresh("cpy.list"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [list_ctor])
        self._mark_cpy_value(lst)
        append_fn = None
        extend_fn = None
        for op_kind, value, value_ty in ops:
            cpy_value, owned = self._marshal_to_cpython(value, value_ty)
            if op_kind == "append":
                if append_fn is None:
                    append_fn = self._emit_cpy_attr(lst, "append")
                result = self.builder.call(
                    self.runtime["py_cpy_call1"],
                    [append_fn, cpy_value],
                    name=self._fresh("cpy.list.append"),
                )
            else:
                if extend_fn is None:
                    extend_fn = self._emit_cpy_attr(lst, "extend")
                result = self.builder.call(
                    self.runtime["py_cpy_call1"],
                    [extend_fn, cpy_value],
                    name=self._fresh("cpy.list.extend"),
                )
            if owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_value])
            self.builder.call(self.runtime["py_cpy_decref"], [result])
        if append_fn is not None:
            self.builder.call(self.runtime["py_cpy_decref"], [append_fn])
        if extend_fn is not None:
            self.builder.call(self.runtime["py_cpy_decref"], [extend_fn])
        return lst

    def _emit_cpython_tuple_ops(
        self,
        ops: list[tuple[str, ir.Value, Type]],
    ) -> ir.Value:
        lst = self._emit_cpython_list_ops(ops)
        tuple_ctor = self._load_cpython_builtin("tuple")
        tup = self.builder.call(
            self.runtime["py_cpy_call1"],
            [tuple_ctor, lst],
            name=self._fresh("cpy.tuple"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [tuple_ctor])
        self.builder.call(self.runtime["py_cpy_decref"], [lst])
        return self._mark_cpy_value(tup)

    def _emit_cpython_dict_items(
        self,
        items: list[tuple[ir.Value, Type, ir.Value, Type]],
    ) -> ir.Value:
        dict_ctor = self._load_cpython_builtin("dict")
        d = self.builder.call(
            self.runtime["py_cpy_call_noargs"],
            [dict_ctor],
            name=self._fresh("cpy.dict"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [dict_ctor])
        self._mark_cpy_value(d)
        for k_val, k_ty, v_val, v_ty in items:
            cpy_key, key_owned = self._marshal_to_cpython(k_val, k_ty)
            cpy_val, val_owned = self._marshal_to_cpython(v_val, v_ty)
            self.builder.call(
                self.runtime["py_cpy_setitem"],
                [d, cpy_key, cpy_val],
            )
            if key_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_key])
            if val_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [cpy_val])
        return d
