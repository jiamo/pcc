"""CPython fallback call-shape lowering for L1CodeGen."""
from __future__ import annotations

from pcc.llvm_capi.compat import ir

from ..py_ast import Call, Expr, Lambda


_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_CSTR = _I8.as_pointer()


class CpyCallLoweringMixin:
    def _emit_cpython_call_arg(self, expr: Expr) -> tuple[ir.Value, bool]:
        """Emit one argument for a CPython call boundary.

        Lambda values need a real CPython callable wrapper here. The generic
        expression path may prefer pcc-native callable objects, which are not
        valid PyObject callables for libpython APIs such as sorted(key=...).
        """

        if isinstance(expr, Lambda):
            simple = self._maybe_emit_simple_lambda(expr)
            if simple is not None:
                return simple, True
            wrapped = self._maybe_emit_lambda_wrap(expr)
            if wrapped is not None:
                return wrapped, True
            raise NotImplementedError(
                "CPython fallback call cannot pass unsupported lambda"
            )
        v = self._emit_expr(expr)
        return self._marshal_to_cpython(v, expr.ty)

    def _emit_cpy_call_kwdict(
        self,
        fn_val: ir.Value,
        name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(*pos_exprs, **kwargs_expr)`` through a
        CPython kwargs-dict helper."""
        if self._is_starred_unpack(pos_exprs):
            return self._emit_cpy_call_list_kwdict(
                fn_val,
                name_hint,
                pos_exprs[0],
                kwargs_expr,
            )
        n_pos = len(pos_exprs)
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            ca, _ = self._emit_cpython_call_arg(arg)
            pos_vals.append(ca)
        if n_pos == 0:
            pos_argv_ptr = ir.Constant(_CSTR, None)
        else:
            pos_arr_ty = ir.ArrayType(_CSTR, n_pos)
            pos_argv = self._alloca_in_entry(
                pos_arr_ty,
                name=f"cpy.pos.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"pos.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv,
                _CSTR,
                name=self._fresh("pos.p"),
            )
        kw_cpy, kw_owned = self._emit_cpython_call_arg(kwargs_expr)
        result = self.builder.call(
            self.runtime["py_cpy_call_kwdict"],
            [fn_val, ir.Constant(_I64, n_pos), pos_argv_ptr, kw_cpy],
            name=self._fresh(f"cpy.callkwdict.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_arglist(
        self,
        fn_val: ir.Value,
        name_hint: str,
        arg_exprs: tuple[Expr, ...],
    ) -> ir.Value:
        """Dispatch ``callable(pos..., *iters...)`` via ``py_cpy_call_list``."""
        args_list = self._emit_pcc_args_list(arg_exprs, name_hint)
        result = self.builder.call(
            self.runtime["py_cpy_call_list"],
            [fn_val, args_list],
            name=self._fresh(f"cpy.callargs.{name_hint}"),
        )
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_arglist_kwdict(
        self,
        fn_val: ir.Value,
        name_hint: str,
        arg_exprs: tuple[Expr, ...],
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(pos..., *iters..., **mapping)`` via the
        list+kwdict helper."""
        args_list = self._emit_pcc_args_list(arg_exprs, name_hint)
        kw_cpy, kw_owned = self._emit_cpython_call_arg(kwargs_expr)
        result = self.builder.call(
            self.runtime["py_cpy_call_list_kwdict"],
            [fn_val, args_list, kw_cpy],
            name=self._fresh(f"cpy.callargskw.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_list_kwdict(
        self,
        fn_val: ir.Value,
        name_hint: str,
        starred_call: "Call",
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``fn(*args, **kwargs_dict)`` through a dedicated
        helper that converts the pcc container to a CPython tuple."""
        inner = starred_call.args[0]
        iter_val = self._emit_expr(inner)
        kw_cpy, kw_owned = self._emit_cpython_call_arg(kwargs_expr)
        result = self.builder.call(
            self.runtime["py_cpy_call_list_kwdict"],
            [fn_val, iter_val, kw_cpy],
            name=self._fresh(f"cpy.calllistkw.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_kwdict_plus(
        self,
        fn_val: ir.Value,
        name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs: tuple,
        kwargs_expr: Expr,
    ) -> ir.Value:
        """Dispatch ``callable(*pos, k=v, **mapping)`` through a helper
        that merges explicit kwargs into the mapping before the call."""
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            ca, _ = self._emit_cpython_call_arg(arg)
            pos_vals.append(ca)
        if pos_vals:
            pos_arr_ty = ir.ArrayType(_CSTR, len(pos_vals))
            pos_argv = self._alloca_in_entry(
                pos_arr_ty,
                name=f"cpy.posmix.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"posmix.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv,
                _CSTR,
                name=self._fresh("posmix.p"),
            )
        else:
            pos_argv_ptr = ir.Constant(_CSTR, None)

        kw_cpy, kw_owned = self._emit_cpython_call_arg(kwargs_expr)

        if kwargs:
            names_arr_ty = ir.ArrayType(_CSTR, len(kwargs))
            vals_arr_ty = ir.ArrayType(_CSTR, len(kwargs))
            names_arr = self._alloca_in_entry(
                names_arr_ty,
                name=f"cpy.mixn.{name_hint}",
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty,
                name=f"cpy.mixv.{name_hint}",
            )
            kw_vals: list[ir.Value] = []
            kw_owned_flags: list[bool] = []
            for i, (kw_name, kw_expr) in enumerate(kwargs):
                name_gv = self._cstr_global(
                    kw_name,
                    f".cpy.mixkw.{name_hint}.{i}",
                )
                ngep = self.builder.gep(
                    names_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"mixn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)
                ca, is_owned = self._emit_cpython_call_arg(kw_expr)
                kw_vals.append(ca)
                kw_owned_flags.append(is_owned)
                vgep = self.builder.gep(
                    vals_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"mixv.{i}"),
                )
                self.builder.store(ca, vgep)
            names_ptr = self.builder.bitcast(
                names_arr,
                _CSTR,
                name=self._fresh("mixn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr,
                _CSTR,
                name=self._fresh("mixv.p"),
            )
        else:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
            kw_vals = []
            kw_owned_flags = []

        result = self.builder.call(
            self.runtime["py_cpy_call_kwdict_plus"],
            [
                fn_val,
                ir.Constant(_I64, len(pos_vals)),
                pos_argv_ptr,
                ir.Constant(_I64, len(kwargs)),
                names_ptr,
                vals_ptr,
                kw_cpy,
            ],
            name=self._fresh(f"cpy.callmix.{name_hint}"),
        )
        if kw_owned:
            self.builder.call(self.runtime["py_cpy_decref"], [kw_cpy])
        for ca, is_owned in zip(kw_vals, kw_owned_flags):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_call_list(
        self,
        fn_val: ir.Value,
        name_hint: str,
        starred_call: "Call",
    ) -> ir.Value:
        """Dispatch ``fn_val(*iterable)`` through ``py_cpy_call_list``.

        ``starred_call`` is the ``Call(Name("__starred__"), (iterable,))``
        sentinel wrapping the single splat arg."""
        inner = starred_call.args[0]
        iter_val = self._emit_expr(inner)
        # py_cpy_call_list expects the pcc PyObject* directly (it
        # converts to a CPython tuple internally). If we received a
        # plain CPython ref (e.g. iter_val already came from a CPython
        # path), the universal converter inside the helper still
        # accepts CPython containers — but generator-returning inner
        # exprs aren't common enough to route specially.
        result = self.builder.call(
            self.runtime["py_cpy_call_list"],
            [fn_val, iter_val],
            name=self._fresh(f"cpy.calllist.{name_hint}"),
        )
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_func_call(
        self,
        fn_val: ir.Value,
        name_hint: str,
        arg_exprs: tuple[Expr, ...],
    ) -> ir.Value:
        """Dispatch ``fn_val(args)`` via py_cpy_callN for a CPython
        callable already loaded into ``fn_val`` (e.g. from a
        ``from mod import fn`` binding). Args marshal via
        ``_marshal_to_cpython``. Shares the argv path with
        ``_emit_cpy_method_call_src``."""
        kwdict_unpack = self._split_starstar_kwargs_unpack(arg_exprs)
        if kwdict_unpack is not None:
            pos_exprs, kwargs_expr = kwdict_unpack
            if self._has_starred_unpack(pos_exprs):
                return self._emit_cpy_call_arglist_kwdict(
                    fn_val,
                    name_hint,
                    pos_exprs,
                    kwargs_expr,
                )
            if self._is_starred_unpack(pos_exprs):
                return self._emit_cpy_call_list_kwdict(
                    fn_val,
                    name_hint,
                    pos_exprs[0],
                    kwargs_expr,
                )
            return self._emit_cpy_call_kwdict(
                fn_val,
                name_hint,
                pos_exprs,
                kwargs_expr,
            )
        if self._has_starred_unpack(arg_exprs):
            return self._emit_cpy_call_arglist(fn_val, name_hint, arg_exprs)
        if self._is_starred_unpack(arg_exprs):
            return self._emit_cpy_call_list(
                fn_val,
                name_hint,
                arg_exprs[0],
            )
        cpy_args: list[ir.Value] = []
        for arg in arg_exprs:
            cpy_arg, _owned = self._emit_cpython_call_arg(arg)
            cpy_args.append(cpy_arg)
        n = len(cpy_args)
        if n == 0:
            result = self.builder.call(
                self.runtime["py_cpy_call_noargs"],
                [fn_val],
                name=self._fresh(f"cpy.call0.{name_hint}"),
            )
        elif n == 1:
            result = self.builder.call(
                self.runtime["py_cpy_call1"],
                [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{name_hint}"),
            )
        elif n == 2:
            result = self.builder.call(
                self.runtime["py_cpy_call2"],
                [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{name_hint}"),
            )
        elif n == 3:
            result = self.builder.call(
                self.runtime["py_cpy_call3"],
                [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{name_hint}"),
            )
        else:
            ptr_arr_ty = ir.ArrayType(_CSTR, n)
            argv = self._alloca_in_entry(
                ptr_arr_ty,
                name=f"cpy.argv.{name_hint}",
            )
            for i, ca in enumerate(cpy_args):
                gep = self.builder.gep(
                    argv,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"argv.{i}"),
                )
                self.builder.store(ca, gep)
            argv_p = self.builder.gep(
                argv,
                [ir.Constant(_I32, 0), ir.Constant(_I32, 0)],
                inbounds=True,
                name=self._fresh("argv.p"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call_argv"],
                [fn_val, ir.Constant(_I64, n), argv_p],
                name=self._fresh(f"cpy.callN.{name_hint}"),
            )
        # Tag the result as a CPython value so ``print(it)`` and
        # similar downstream operations go through the conversion
        # path rather than treating the PyObject* as a pcc str.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _emit_cpy_method_call_src(
        self,
        mod_val: ir.Value,
        attr_name: str,
        arg_exprs: tuple[Expr, ...],
        kwargs: tuple = (),
    ) -> ir.Value:
        """Lower ``<CPython value>.method(args)`` through py_cpy_getattr
        + py_cpy_callN with scalar → CPython marshalling for typed args
        (int / float / str)."""
        attr_ptr = self._ptr_to_cstr(
            self._cstr_global(attr_name, f".cpy.attr.{attr_name}")
        )
        fn_val = self.builder.call(
            self.runtime["py_cpy_getattr"],
            [mod_val, attr_ptr],
            name=self._fresh(f"cpy.fn.{attr_name}"),
        )

        kwdict_unpack = self._split_starstar_kwargs_unpack(arg_exprs)
        if kwdict_unpack is not None:
            pos_exprs, kwargs_expr = kwdict_unpack
            if kwargs:
                return self._emit_cpy_call_kwdict_plus(
                    fn_val,
                    attr_name,
                    pos_exprs,
                    kwargs,
                    kwargs_expr,
                )
            if self._has_starred_unpack(pos_exprs):
                return self._emit_cpy_call_arglist_kwdict(
                    fn_val,
                    attr_name,
                    pos_exprs,
                    kwargs_expr,
                )
            return self._emit_cpy_call_kwdict(
                fn_val,
                attr_name,
                pos_exprs,
                kwargs_expr,
            )

        if kwargs:
            return self._finish_cpy_call_kw(
                fn_val,
                attr_name,
                arg_exprs,
                kwargs,
            )

        if self._has_starred_unpack(arg_exprs):
            return self._emit_cpy_call_arglist(fn_val, attr_name, arg_exprs)
        if self._is_starred_unpack(arg_exprs):
            return self._emit_cpy_call_list(
                fn_val,
                attr_name,
                arg_exprs[0],
            )

        # Marshal each arg from its pcc native form to a CPython PyObject*.
        # ``owned`` parallel tracks whether we created the CPython ref
        # (and therefore must decref after the call).
        cpy_args: list[ir.Value] = []
        owned: list[bool] = []
        for arg in arg_exprs:
            cpy_arg, is_owned = self._emit_cpython_call_arg(arg)
            cpy_args.append(cpy_arg)
            owned.append(is_owned)

        n = len(cpy_args)
        if n == 0:
            result = self.builder.call(
                self.runtime["py_cpy_call_noargs"],
                [fn_val],
                name=self._fresh(f"cpy.call0.{attr_name}"),
            )
        elif n == 1:
            result = self.builder.call(
                self.runtime["py_cpy_call1"],
                [fn_val, cpy_args[0]],
                name=self._fresh(f"cpy.call1.{attr_name}"),
            )
        elif n == 2:
            result = self.builder.call(
                self.runtime["py_cpy_call2"],
                [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call2.{attr_name}"),
            )
        elif n == 3:
            result = self.builder.call(
                self.runtime["py_cpy_call3"],
                [fn_val] + cpy_args,
                name=self._fresh(f"cpy.call3.{attr_name}"),
            )
        else:
            # Build an alloca argv[n] array and dispatch via
            # py_cpy_call_argv (PyObject_Call over a fresh tuple). The
            # runtime helper steals each argv[i] ref, so we do NOT
            # decref the owned args afterwards — only borrowed args
            # need a fresh ref (py_cpy_from_* produces one already).
            ptr_arr_ty = ir.ArrayType(_CSTR, n)
            argv = self._alloca_in_entry(
                ptr_arr_ty,
                name=f"cpy.argv.{attr_name}",
            )
            for i, (ca, is_owned) in enumerate(zip(cpy_args, owned)):
                if not is_owned:
                    # Caller-owned borrowed ref — promote to owned via
                    # ``py_cpy_incref`` so ``py_cpy_call_argv``'s
                    # ref-stealing via PyTuple_SetItem doesn't double-
                    # free the caller's handle. The bumped ref is
                    # balanced by the PyTuple_SetItem steal inside
                    # the helper.
                    self.builder.call(
                        self.runtime["py_cpy_incref"],
                        [ca],
                    )
                idx0 = ir.Constant(_I32, 0)
                idx = ir.Constant(_I32, i)
                slot = self.builder.gep(
                    argv, [idx0, idx], inbounds=True, name=self._fresh(f"argv.{i}")
                )
                self.builder.store(ca, slot)
            # Decay the array pointer to a ``ptr`` for the varargs call.
            argv_ptr = self.builder.bitcast(
                argv,
                _CSTR,
                name=self._fresh("argv.ptr"),
            )
            result = self.builder.call(
                self.runtime["py_cpy_call_argv"],
                [fn_val, ir.Constant(_I64, n), argv_ptr],
                name=self._fresh(f"cpy.calln.{attr_name}"),
            )
            # py_cpy_call_argv stole each owned ref; skip the decref
            # loop below.
            self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
            if not hasattr(self, "_cpy_values"):
                self._cpy_values = set()
            self._cpy_values.add(result)
            return result

        # Release only the CPython args we owned (native scalars we
        # boxed). Borrowed DynType/CPython values keep their
        # caller-owned ref.
        for ca, is_owned in zip(cpy_args, owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])

        # Mark the result as a CPython value so downstream print/str go
        # through the conversion path.
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

    def _finish_cpy_call_kw(
        self,
        fn_val: ir.Value,
        name_hint: str,
        pos_exprs: tuple[Expr, ...],
        kwargs: tuple,
    ) -> ir.Value:
        """Dispatch a CPython callable with mixed positional + keyword
        arguments through ``py_cpy_call_kw``. Positional refs are stolen
        into the tuple; keyword refs are borrowed by PyDict_SetItem so
        we still decref our owned kw values after."""
        n_pos = len(pos_exprs)
        n_kw = len(kwargs)
        pos_vals: list[ir.Value] = []
        for arg in pos_exprs:
            ca, _ = self._emit_cpython_call_arg(arg)
            pos_vals.append(ca)
        kw_vals: list[ir.Value] = []
        kw_owned: list[bool] = []
        for _name, kv in kwargs:
            ca, is_owned = self._emit_cpython_call_arg(kv)
            kw_vals.append(ca)
            kw_owned.append(is_owned)

        # Build positional argv[n_pos]
        if n_pos == 0:
            pos_argv_ptr = ir.Constant(_CSTR, None)
        else:
            pos_arr_ty = ir.ArrayType(_CSTR, n_pos)
            pos_argv = self._alloca_in_entry(
                pos_arr_ty,
                name=f"cpy.pos.{name_hint}",
            )
            for i, ca in enumerate(pos_vals):
                gep = self.builder.gep(
                    pos_argv,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"pos.{i}"),
                )
                self.builder.store(ca, gep)
            pos_argv_ptr = self.builder.bitcast(
                pos_argv,
                _CSTR,
                name=self._fresh("pos.p"),
            )

        if n_kw == 0:
            names_ptr = ir.Constant(_CSTR, None)
            vals_ptr = ir.Constant(_CSTR, None)
        else:
            names_arr_ty = ir.ArrayType(_CSTR, n_kw)
            vals_arr_ty = ir.ArrayType(_CSTR, n_kw)
            names_arr = self._alloca_in_entry(
                names_arr_ty,
                name=f"cpy.kwn.{name_hint}",
            )
            vals_arr = self._alloca_in_entry(
                vals_arr_ty,
                name=f"cpy.kwv.{name_hint}",
            )
            for i, (kwn, _kv) in enumerate(kwargs):
                name_gv = self._cstr_global(
                    kwn,
                    f".cpy.kwname.{name_hint}.{i}.{kwn}",
                )
                ngep = self.builder.gep(
                    names_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"kwn.{i}"),
                )
                self.builder.store(self._ptr_to_cstr(name_gv), ngep)
                vgep = self.builder.gep(
                    vals_arr,
                    [ir.Constant(_I32, 0), ir.Constant(_I32, i)],
                    inbounds=True,
                    name=self._fresh(f"kwv.{i}"),
                )
                self.builder.store(kw_vals[i], vgep)
            names_ptr = self.builder.bitcast(
                names_arr,
                _CSTR,
                name=self._fresh("kwn.p"),
            )
            vals_ptr = self.builder.bitcast(
                vals_arr,
                _CSTR,
                name=self._fresh("kwv.p"),
            )

        result = self.builder.call(
            self.runtime["py_cpy_call_kw"],
            [
                fn_val,
                ir.Constant(_I64, n_pos),
                pos_argv_ptr,
                ir.Constant(_I64, n_kw),
                names_ptr,
                vals_ptr,
            ],
            name=self._fresh(f"cpy.callkw.{name_hint}"),
        )
        # kw_vals are borrowed by PyDict_SetItemString (refcount
        # incremented by CPython); decref any we owned.
        for ca, is_owned in zip(kw_vals, kw_owned):
            if is_owned:
                self.builder.call(self.runtime["py_cpy_decref"], [ca])
        self.builder.call(self.runtime["py_cpy_decref"], [fn_val])
        if not hasattr(self, "_cpy_values"):
            self._cpy_values = set()
        self._cpy_values.add(result)
        return result

