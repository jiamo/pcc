"""pcc.unsafe intrinsic lowering helpers for L1CodeGen."""
from __future__ import annotations

import platform
import sys
from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    BoolType,
    Call,
    Expr,
    FloatType,
    IntLit,
    IntType,
    Name,
    StrLit,
    UnaryOp,
)


_VOID = ir.VoidType()
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
_CSTR = _I8.as_pointer()


UNSAFE_INTRINSICS = frozenset(
    {
        "malloc",
        "cstr",
        "global_addr",
        "global_load_ptr",
        "global_store_ptr",
        "define_global_i8",
        "define_global_i32",
        "define_global_header",
        "define_global_ptr_null",
        "define_global_ptr_to_global",
        "define_global_cstr",
        "define_global_ptr_array",
        "define_global_null_ptr_array",
        "define_global_i32_array",
        "calloc",
        "realloc",
        "free",
        "ptr_add",
        "ptr_diff",
        "null",
        "ptr_eq",
        "ptr_is_null",
        "is_tagged_int",
        "tag_int",
        "untag_int",
        "load_i64",
        "load_i32",
        "load_i8",
        "load_ptr",
        "load_f64",
        "store_i64",
        "store_i32",
        "store_i8",
        "store_ptr",
        "store_f64",
        "memset",
        "memcpy",
        "memmove",
        "write",
        "strlen",
        "getenv",
        "setenv",
        "unsetenv",
        "access",
        "stat_kind",
        "stat_mtime",
        "target_sys_platform",
        "target_platform_machine",
        "call_ptr1",
        "call_void_ptr1",
        "call_ptr2",
    }
)


class UnsafeIntrinsicMixin:
    _UNSAFE_INTRINSICS = UNSAFE_INTRINSICS

    def _is_unsafe_intrinsic(self, name: str) -> bool:
        return name in self._UNSAFE_INTRINSICS

    def _unsafe_intrinsic_for_name(self, name: str) -> Optional[str]:
        if name in self.env:
            return None
        if name in getattr(self, "_module_globals", {}):
            return None
        return getattr(self, "_unsafe_bindings", {}).get(name)

    def _unsafe_expect_arity(
        self,
        intrinsic: str,
        expr: Call,
        n_args: int,
    ) -> None:
        if expr.kwargs or len(expr.args) != n_args:
            raise NotImplementedError(
                f"pcc.unsafe.{intrinsic} expects {n_args} positional args"
            )

    def _unsafe_ptr_arg(self, expr: Expr) -> ir.Value:
        ptr = self._emit_expr(expr)
        if isinstance(ptr.type, ir.PointerType):
            if self._ir_type_matches(ptr.type, _CSTR):
                return ptr
            return self.builder.bitcast(
                ptr,
                _CSTR,
                name=self._fresh("unsafe.ptr.cast"),
            )
        # A small recovery path for late-stage mismatches between the
        # value-level LLVM type and the Python-level object-flow intent.
        # Some high-level pointer variables in pcc-Python runtime ports are
        # intentionally unannotated; if inference briefly loses the opaque
        # object abstraction and emits a plain integer, this lets us keep
        # moving instead of hard-failing at compile time.
        if isinstance(ptr.type, ir.IntType):
            expr_ty = getattr(expr, "ty", None)
            if isinstance(expr_ty, (IntType, FloatType, BoolType)):
                raise NotImplementedError(
                    "pcc.unsafe pointer argument must be a pointer-typed value"
                )
            if ptr.type.width == 64:
                return self.builder.inttoptr(ptr, _CSTR)
            if ptr.type.width < 64:
                return self.builder.inttoptr(self.builder.zext(ptr, _I64), _CSTR)
            return self.builder.inttoptr(self.builder.trunc(ptr, _I64), _CSTR)
        raise NotImplementedError(
            "pcc.unsafe pointer argument must be a pointer-typed value"
        )

    def _unsafe_i64_arg(self, expr: Expr) -> ir.Value:
        return self._to_int64(self._emit_expr(expr), expr.ty)

    def _unsafe_i32_arg(self, expr: Expr) -> ir.Value:
        return self.builder.trunc(
            self._unsafe_i64_arg(expr),
            _I32,
            name=self._fresh("unsafe.i64.to.i32"),
        )

    def _unsafe_f64_arg(self, expr: Expr) -> ir.Value:
        return self._to_double(self._emit_expr(expr), expr.ty)

    def _unsafe_cstr_literal_arg(self, expr: Expr) -> ir.Value:
        if not isinstance(expr, StrLit):
            raise NotImplementedError("pcc.unsafe.cstr only accepts a string literal")
        gv = self._cstr_global(
            expr.value,
            self._fresh(".unsafe.cstr"),
        )
        return self._ptr_to_cstr(gv)

    def _unsafe_symbol_literal_arg(self, expr: Expr) -> str:
        if not isinstance(expr, StrLit):
            raise NotImplementedError(
                "pcc.unsafe global intrinsics require a string literal symbol"
            )
        symbol = expr.value
        if not symbol:
            raise NotImplementedError("empty external symbol name")

        def is_alpha_code(c: int) -> bool:
            return (97 <= c <= 122) or (65 <= c <= 90)

        def is_alnum_code(c: int) -> bool:
            return is_alpha_code(c) or (48 <= c <= 57)

        first = ord(symbol[0])
        if not (first == 95 or is_alpha_code(first)):
            raise NotImplementedError(f"invalid external symbol {symbol!r}")
        for ch in symbol[1:]:
            c = ord(ch)
            if not (c == 95 or is_alnum_code(c)):
                raise NotImplementedError(f"invalid external symbol {symbol!r}")
        return symbol

    def _unsafe_const_i64_arg(self, expr: Expr) -> int:
        if isinstance(expr, IntLit):
            return int(expr.value)
        if (
            isinstance(expr, UnaryOp)
            and expr.op == "-"
            and isinstance(expr.operand, IntLit)
        ):
            return -int(expr.operand.value)
        raise NotImplementedError(
            "pcc.unsafe global definition intrinsics require integer literals"
        )

    def _define_global(
        self,
        name: str,
        value_ty: ir.Type,
        initializer: ir.Constant,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            gv = existing
            gv.linkage = ""
        elif existing is None:
            gv = ir.GlobalVariable(self.module, value_ty, name=name)
        else:
            raise NotImplementedError(f"{name!r} already declared as non-global")
        gv.initializer = initializer
        return gv

    def _get_or_declare_global_for_initializer(
        self,
        name: str,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        if existing is not None:
            raise NotImplementedError(f"{name!r} already declared as non-global")
        gv = ir.GlobalVariable(self.module, _I8, name=name)
        gv.linkage = "external"
        return gv

    def _define_unsafe_global_intrinsic(
        self,
        intrinsic: str,
        expr: Call,
    ) -> bool:
        if intrinsic == "define_global_i8":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            value = self._unsafe_const_i64_arg(expr.args[1])
            self._define_global(
                name,
                _I8,
                ir.Constant(_I8, value & 0xFF),
            )
            return True
        if intrinsic == "define_global_i32":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            value = self._unsafe_const_i64_arg(expr.args[1])
            self._define_global(name, _I32, ir.Constant(_I32, value))
            return True
        if intrinsic == "define_global_header":
            self._unsafe_expect_arity(intrinsic, expr, 4)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            refcount = self._unsafe_const_i64_arg(expr.args[1])
            type_tag = self._unsafe_const_i64_arg(expr.args[2])
            flags = self._unsafe_const_i64_arg(expr.args[3])
            header_ty = ir.LiteralStructType([_I64, _I32, _I32])
            init = ir.Constant(
                header_ty,
                [
                    ir.Constant(_I64, refcount),
                    ir.Constant(_I32, type_tag),
                    ir.Constant(_I32, flags),
                ],
            )
            self._define_global(name, header_ty, init)
            return True
        if intrinsic == "define_global_ptr_null":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            self._define_global(name, _CSTR, ir.Constant(_CSTR, None))
            return True
        if intrinsic == "define_global_ptr_to_global":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            target = self._unsafe_symbol_literal_arg(expr.args[1])
            target_gv = self._get_or_declare_global_for_initializer(target)
            self._define_global(name, _CSTR, ir.Constant(_CSTR, target_gv))
            return True
        if intrinsic == "define_global_cstr":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            if not isinstance(expr.args[1], StrLit):
                raise NotImplementedError(
                    "pcc.unsafe.define_global_cstr requires a string literal"
                )
            payload = self._utf8_byte_values(expr.args[1].value) + [0]
            arr_ty = ir.ArrayType(_I8, len(payload))
            init = ir.Constant(arr_ty, [ir.Constant(_I8, b) for b in payload])
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_ptr_array":
            if expr.kwargs or len(expr.args) < 1:
                raise NotImplementedError(
                    "pcc.unsafe.define_global_ptr_array expects a name"
                )
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            targets = [self._unsafe_symbol_literal_arg(arg) for arg in expr.args[1:]]
            arr_ty = ir.ArrayType(_CSTR, len(targets))
            init = ir.Constant(
                arr_ty,
                [
                    ir.Constant(
                        _CSTR,
                        self._get_or_declare_global_for_initializer(t),
                    )
                    for t in targets
                ],
            )
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_null_ptr_array":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            n_items = self._unsafe_const_i64_arg(expr.args[1])
            arr_ty = ir.ArrayType(_CSTR, n_items)
            init = ir.Constant(
                arr_ty, [ir.Constant(_CSTR, None) for _ in range(n_items)]
            )
            self._define_global(name, arr_ty, init)
            return True
        if intrinsic == "define_global_i32_array":
            if expr.kwargs or len(expr.args) < 1:
                raise NotImplementedError(
                    "pcc.unsafe.define_global_i32_array expects a name"
                )
            name = self._unsafe_symbol_literal_arg(expr.args[0])
            values = [self._unsafe_const_i64_arg(arg) for arg in expr.args[1:]]
            arr_ty = ir.ArrayType(_I32, len(values))
            init = ir.Constant(arr_ty, [ir.Constant(_I32, v) for v in values])
            self._define_global(name, arr_ty, init)
            return True
        return False

    def _maybe_define_unsafe_global_stmt(self, stmt: "ExprStmt") -> bool:
        expr = stmt.expr
        if not isinstance(expr, Call) or not isinstance(expr.func, Name):
            return False
        intrinsic = self._unsafe_intrinsic_for_name(expr.func.ident)
        if intrinsic is None:
            return False
        return self._define_unsafe_global_intrinsic(intrinsic, expr)

    def _declare_external_global(
        self,
        name: str,
        value_ty: ir.Type,
    ) -> ir.GlobalVariable:
        existing = self.module.globals.get(name)
        if isinstance(existing, ir.GlobalVariable):
            return existing
        if existing is not None:
            raise NotImplementedError(f"{name!r} already declared as non-global")
        gv = ir.GlobalVariable(self.module, value_ty, name=name)
        gv.linkage = "external"
        return gv

    def _unsafe_typed_addr(
        self,
        base: ir.Value,
        offset: ir.Value,
        pointee: ir.Type,
    ) -> ir.Value:
        byte_addr = self.builder.gep(
            base,
            [offset],
            name=self._fresh("unsafe.addr"),
        )
        ptr_ty = pointee.as_pointer()
        if self._ir_type_matches(byte_addr.type, ptr_ty):
            return byte_addr
        return self.builder.bitcast(
            byte_addr,
            ptr_ty,
            name=self._fresh("unsafe.addr.cast"),
        )

    def _target_sys_platform_text(self) -> str:
        if sys.platform == "darwin":
            return "darwin"
        if sys.platform.startswith("linux"):
            return "linux"
        if sys.platform.startswith("win"):
            return "win32"
        if sys.platform.startswith("freebsd"):
            return "freebsd"
        return "unknown"

    def _target_machine_text(self) -> str:
        machine = platform.machine()
        if machine == "aarch64":
            return "aarch64"
        if machine == "arm64":
            return "arm64"
        if machine == "x86_64" or machine == "AMD64":
            return "x86_64"
        if machine == "i386" or machine == "i686":
            return "i386"
        if machine == "arm":
            return "arm"
        if machine == "ppc64":
            return "ppc64"
        if machine == "ppc":
            return "ppc"
        return machine

    def _stat_layout(self) -> tuple[int, int, int, int]:
        """Return struct stat size/mode/mtime offsets for host libc.

        This is deliberately a codegen/platform intrinsic boundary: the
        pcc-Python runtime source stays structure-agnostic while the compiler
        owns the ABI constants that differ between Darwin and Linux.
        """
        machine = self._target_machine_text()
        if sys.platform == "darwin":
            return 144, 4, 48, 56
        if sys.platform.startswith("linux"):
            if machine == "x86_64":
                return 144, 24, 88, 96
            # glibc aarch64 uses a different first-field order from x86_64
            # but keeps the POSIX timespec pair in the same trailing region.
            if machine == "aarch64" or machine == "arm64":
                return 128, 16, 88, 96
        return 144, 24, 88, 96

    def _emit_unsafe_stat_call(self, path: ir.Value) -> tuple[ir.Value, ir.Value]:
        size, _mode_off, _mtime_sec_off, _mtime_nsec_off = self._stat_layout()
        stat_ty = ir.ArrayType(_I8, size)
        slot = self.builder.alloca(stat_ty, name=self._fresh("unsafe.stat.buf"))
        zero = ir.Constant(_I64, 0)
        buf = self.builder.gep(
            slot,
            [zero, zero],
            inbounds=True,
            name=self._fresh("unsafe.stat.ptr"),
        )
        stat_fn = self._declare_external_function(
            "stat",
            _I32,
            [_CSTR, _CSTR],
        )
        rc = self.builder.call(
            stat_fn,
            [path, buf],
            name=self._fresh("unsafe.stat.rc"),
        )
        return buf, rc

    def _emit_unsafe_stat_kind(self, expr: Call) -> ir.Value:
        self._unsafe_expect_arity("stat_kind", expr, 1)
        _size, mode_off, _mtime_sec_off, _mtime_nsec_off = self._stat_layout()
        path = self._unsafe_ptr_arg(expr.args[0])
        path_i = self.builder.ptrtoint(
            path,
            _I64,
            name=self._fresh("unsafe.stat.path.i"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            path_i,
            ir.Constant(_I64, 0),
            name=self._fresh("unsafe.stat.path.null"),
        )
        buf, rc = self._emit_unsafe_stat_call(path)
        failed = self.builder.icmp_signed(
            "!=",
            rc,
            ir.Constant(_I32, 0),
            name=self._fresh("unsafe.stat.failed"),
        )
        bad = self.builder.or_(
            is_null,
            failed,
            name=self._fresh("unsafe.stat.bad"),
        )
        mode_addr = self._unsafe_typed_addr(
            buf,
            ir.Constant(_I64, mode_off),
            _I32,
        )
        mode = self.builder.load(
            mode_addr,
            name=self._fresh("unsafe.stat.mode"),
            align=1,
        )
        masked = self.builder.and_(
            mode,
            ir.Constant(_I32, 0o170000),
            name=self._fresh("unsafe.stat.mode.mask"),
        )
        is_reg = self.builder.icmp_signed(
            "==",
            masked,
            ir.Constant(_I32, 0o100000),
            name=self._fresh("unsafe.stat.isreg"),
        )
        is_dir = self.builder.icmp_signed(
            "==",
            masked,
            ir.Constant(_I32, 0o040000),
            name=self._fresh("unsafe.stat.isdir"),
        )
        dir_or_other = self.builder.select(
            is_dir,
            ir.Constant(_I64, 2),
            ir.Constant(_I64, 3),
            name=self._fresh("unsafe.stat.kind.dir"),
        )
        typed_kind = self.builder.select(
            is_reg,
            ir.Constant(_I64, 1),
            dir_or_other,
            name=self._fresh("unsafe.stat.kind.typed"),
        )
        return self.builder.select(
            bad,
            ir.Constant(_I64, 0),
            typed_kind,
            name=self._fresh("unsafe.stat.kind"),
        )

    def _emit_unsafe_stat_mtime(self, expr: Call) -> ir.Value:
        self._unsafe_expect_arity("stat_mtime", expr, 1)
        _size, _mode_off, mtime_sec_off, mtime_nsec_off = self._stat_layout()
        path = self._unsafe_ptr_arg(expr.args[0])
        path_i = self.builder.ptrtoint(
            path,
            _I64,
            name=self._fresh("unsafe.mtime.path.i"),
        )
        is_null = self.builder.icmp_unsigned(
            "==",
            path_i,
            ir.Constant(_I64, 0),
            name=self._fresh("unsafe.mtime.path.null"),
        )
        buf, rc = self._emit_unsafe_stat_call(path)
        failed = self.builder.icmp_signed(
            "!=",
            rc,
            ir.Constant(_I32, 0),
            name=self._fresh("unsafe.mtime.failed"),
        )
        bad = self.builder.or_(
            is_null,
            failed,
            name=self._fresh("unsafe.mtime.bad"),
        )
        sec_addr = self._unsafe_typed_addr(
            buf,
            ir.Constant(_I64, mtime_sec_off),
            _I64,
        )
        nsec_addr = self._unsafe_typed_addr(
            buf,
            ir.Constant(_I64, mtime_nsec_off),
            _I64,
        )
        sec = self.builder.load(
            sec_addr,
            name=self._fresh("unsafe.mtime.sec"),
            align=1,
        )
        nsec = self.builder.load(
            nsec_addr,
            name=self._fresh("unsafe.mtime.nsec"),
            align=1,
        )
        sec_f = self.builder.sitofp(
            sec,
            _DOUBLE,
            name=self._fresh("unsafe.mtime.sec.f"),
        )
        nsec_f = self.builder.sitofp(
            nsec,
            _DOUBLE,
            name=self._fresh("unsafe.mtime.nsec.f"),
        )
        frac = self.builder.fdiv(
            nsec_f,
            ir.Constant(_DOUBLE, 1.0e9),
            name=self._fresh("unsafe.mtime.frac"),
        )
        value = self.builder.fadd(
            sec_f,
            frac,
            name=self._fresh("unsafe.mtime.value"),
        )
        return self.builder.select(
            bad,
            ir.Constant(_DOUBLE, float("nan")),
            value,
            name=self._fresh("unsafe.mtime"),
        )

    def _emit_unsafe_intrinsic_call(
        self,
        intrinsic: str,
        expr: Call,
    ) -> ir.Value:
        if self._define_unsafe_global_intrinsic(intrinsic, expr):
            return self._emit_none_literal()
        if intrinsic == "cstr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            return self._unsafe_cstr_literal_arg(expr.args[0])
        if intrinsic == "target_sys_platform":
            self._unsafe_expect_arity(intrinsic, expr, 0)
            gv, _n = self._cstr_literal(self._target_sys_platform_text())
            return self._ptr_to_cstr(gv)
        if intrinsic == "target_platform_machine":
            self._unsafe_expect_arity(intrinsic, expr, 0)
            gv, _n = self._cstr_literal(self._target_machine_text())
            return self._ptr_to_cstr(gv)
        if intrinsic == "global_addr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _I8)
            if self._ir_type_matches(gv.type, _CSTR):
                return gv
            return self.builder.bitcast(
                gv,
                _CSTR,
                name=self._fresh("unsafe.global.addr"),
            )
        if intrinsic == "global_load_ptr":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _CSTR)
            return self.builder.load(
                gv,
                name=self._fresh("unsafe.global.load.ptr"),
            )
        if intrinsic == "global_store_ptr":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            symbol = self._unsafe_symbol_literal_arg(expr.args[0])
            gv = self._declare_external_global(symbol, _CSTR)
            self.builder.store(self._unsafe_ptr_arg(expr.args[1]), gv)
            return self._emit_none_literal()
        if intrinsic == "malloc":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            malloc_fn = self._declare_external_function(
                "malloc",
                _CSTR,
                [_I64],
            )
            return self.builder.call(
                malloc_fn,
                [self._unsafe_i64_arg(expr.args[0])],
                name=self._fresh("unsafe.malloc"),
            )
        if intrinsic == "calloc":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            calloc_fn = self._declare_external_function(
                "calloc",
                _CSTR,
                [_I64, _I64],
            )
            return self.builder.call(
                calloc_fn,
                [
                    self._unsafe_i64_arg(expr.args[0]),
                    self._unsafe_i64_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.calloc"),
            )
        if intrinsic == "realloc":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            realloc_fn = self._declare_external_function(
                "realloc",
                _CSTR,
                [_CSTR, _I64],
            )
            return self.builder.call(
                realloc_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i64_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.realloc"),
            )
        if intrinsic == "free":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            free_fn = self._declare_external_function(
                "free",
                _VOID,
                [_CSTR],
            )
            self.builder.call(
                free_fn,
                [self._unsafe_ptr_arg(expr.args[0])],
            )
            return self._emit_none_literal()
        if intrinsic == "ptr_add":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            return self.builder.gep(
                self._unsafe_ptr_arg(expr.args[0]),
                [self._unsafe_i64_arg(expr.args[1])],
                name=self._fresh("unsafe.ptr.add"),
            )
        if intrinsic == "ptr_diff":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            lhs_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[0]),
                _I64,
                name=self._fresh("unsafe.ptr.diff.l"),
            )
            rhs_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[1]),
                _I64,
                name=self._fresh("unsafe.ptr.diff.r"),
            )
            return self.builder.sub(
                lhs_i,
                rhs_i,
                name=self._fresh("unsafe.ptr.diff"),
            )
        if intrinsic == "null":
            self._unsafe_expect_arity(intrinsic, expr, 0)
            return ir.Constant(_CSTR, None)
        if intrinsic in ("ptr_eq", "ptr_is_null", "is_tagged_int"):
            if intrinsic == "ptr_eq":
                self._unsafe_expect_arity(intrinsic, expr, 2)
                lhs_i = self.builder.ptrtoint(
                    self._unsafe_ptr_arg(expr.args[0]),
                    _I64,
                    name=self._fresh("unsafe.ptr.eq.l"),
                )
                rhs_i = self.builder.ptrtoint(
                    self._unsafe_ptr_arg(expr.args[1]),
                    _I64,
                    name=self._fresh("unsafe.ptr.eq.r"),
                )
                return self.builder.icmp_unsigned(
                    "==",
                    lhs_i,
                    rhs_i,
                    name=self._fresh("unsafe.ptr.eq"),
                )
            self._unsafe_expect_arity(intrinsic, expr, 1)
            ptr_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[0]),
                _I64,
                name=self._fresh(f"unsafe.{intrinsic}.i"),
            )
            if intrinsic == "ptr_is_null":
                return self.builder.icmp_unsigned(
                    "==",
                    ptr_i,
                    ir.Constant(_I64, 0),
                    name=self._fresh("unsafe.ptr.null"),
                )
            tagged = self.builder.and_(
                ptr_i,
                ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.bit"),
            )
            return self.builder.icmp_unsigned(
                "==",
                tagged,
                ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tagged"),
            )
        if intrinsic == "tag_int":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            value = self._unsafe_i64_arg(expr.args[0])
            shifted = self.builder.shl(
                value,
                ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.int.shift"),
            )
            tagged = self.builder.or_(
                shifted,
                ir.Constant(_I64, 1),
                name=self._fresh("unsafe.tag.int.bits"),
            )
            return self.builder.inttoptr(
                tagged,
                _CSTR,
                name=self._fresh("unsafe.tag.int.ptr"),
            )
        if intrinsic == "untag_int":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            ptr_i = self.builder.ptrtoint(
                self._unsafe_ptr_arg(expr.args[0]),
                _I64,
                name=self._fresh("unsafe.untag.int.bits"),
            )
            return self.builder.ashr(
                ptr_i,
                ir.Constant(_I64, 1),
                name=self._fresh("unsafe.untag.int"),
            )
        if intrinsic in ("load_i64", "load_i32", "load_i8", "load_ptr", "load_f64"):
            self._unsafe_expect_arity(intrinsic, expr, 2)
            base = self._unsafe_ptr_arg(expr.args[0])
            offset = self._unsafe_i64_arg(expr.args[1])
            if intrinsic == "load_i64":
                addr = self._unsafe_typed_addr(base, offset, _I64)
                return self.builder.load(
                    addr,
                    name=self._fresh("unsafe.load.i64"),
                    align=1,
                )
            if intrinsic == "load_i32":
                addr = self._unsafe_typed_addr(base, offset, _I32)
                raw = self.builder.load(
                    addr,
                    name=self._fresh("unsafe.load.i32"),
                    align=1,
                )
                return self.builder.sext(
                    raw,
                    _I64,
                    name=self._fresh("unsafe.i32.to.i64"),
                )
            if intrinsic == "load_i8":
                addr = self._unsafe_typed_addr(base, offset, _I8)
                raw = self.builder.load(
                    addr,
                    name=self._fresh("unsafe.load.i8"),
                    align=1,
                )
                return self.builder.sext(
                    raw,
                    _I64,
                    name=self._fresh("unsafe.i8.to.i64"),
                )
            if intrinsic == "load_f64":
                addr = self._unsafe_typed_addr(base, offset, _DOUBLE)
                return self.builder.load(
                    addr,
                    name=self._fresh("unsafe.load.f64"),
                    align=1,
                )
            addr = self._unsafe_typed_addr(base, offset, _CSTR)
            return self.builder.load(
                addr,
                name=self._fresh("unsafe.load.ptr"),
                align=1,
            )
        if intrinsic in (
            "store_i64",
            "store_i32",
            "store_i8",
            "store_ptr",
            "store_f64",
        ):
            self._unsafe_expect_arity(intrinsic, expr, 3)
            base = self._unsafe_ptr_arg(expr.args[0])
            offset = self._unsafe_i64_arg(expr.args[1])
            if intrinsic == "store_i64":
                addr = self._unsafe_typed_addr(base, offset, _I64)
                self.builder.store(
                    self._unsafe_i64_arg(expr.args[2]),
                    addr,
                    align=1,
                )
                return self._emit_none_literal()
            if intrinsic == "store_i32":
                addr = self._unsafe_typed_addr(base, offset, _I32)
                raw = self.builder.trunc(
                    self._unsafe_i64_arg(expr.args[2]),
                    _I32,
                    name=self._fresh("unsafe.i64.to.i32"),
                )
                self.builder.store(raw, addr, align=1)
                return self._emit_none_literal()
            if intrinsic == "store_i8":
                addr = self._unsafe_typed_addr(base, offset, _I8)
                raw = self.builder.trunc(
                    self._unsafe_i64_arg(expr.args[2]),
                    _I8,
                    name=self._fresh("unsafe.i64.to.i8"),
                )
                self.builder.store(raw, addr, align=1)
                return self._emit_none_literal()
            if intrinsic == "store_f64":
                addr = self._unsafe_typed_addr(base, offset, _DOUBLE)
                self.builder.store(
                    self._unsafe_f64_arg(expr.args[2]),
                    addr,
                    align=1,
                )
                return self._emit_none_literal()
            addr = self._unsafe_typed_addr(base, offset, _CSTR)
            self.builder.store(
                self._unsafe_ptr_arg(expr.args[2]),
                addr,
                align=1,
            )
            return self._emit_none_literal()
        if intrinsic == "memset":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            memset_fn = self._declare_external_function(
                "memset",
                _CSTR,
                [_CSTR, _I32, _I64],
            )
            return self.builder.call(
                memset_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i32_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.memset"),
            )
        if intrinsic in ("memcpy", "memmove"):
            self._unsafe_expect_arity(intrinsic, expr, 3)
            copy_fn = self._declare_external_function(
                intrinsic,
                _CSTR,
                [_CSTR, _CSTR, _I64],
            )
            return self.builder.call(
                copy_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh(f"unsafe.{intrinsic}"),
            )
        if intrinsic == "write":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            write_fn = self._declare_external_function(
                "write",
                _I64,
                [_I32, _CSTR, _I64],
            )
            return self.builder.call(
                write_fn,
                [
                    self._unsafe_i32_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i64_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.write"),
            )
        if intrinsic == "strlen":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            strlen_fn = self._declare_external_function(
                "strlen",
                _I64,
                [_CSTR],
            )
            return self.builder.call(
                strlen_fn,
                [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.strlen"),
            )
        if intrinsic == "getenv":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            getenv_fn = self._declare_external_function(
                "getenv",
                _CSTR,
                [_CSTR],
            )
            return self.builder.call(
                getenv_fn,
                [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.getenv"),
            )
        if intrinsic == "setenv":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            setenv_fn = self._declare_external_function(
                "setenv",
                _I32,
                [_CSTR, _CSTR, _I32],
            )
            raw = self.builder.call(
                setenv_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_i32_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.setenv.i32"),
            )
            return self.builder.sext(
                raw,
                _I64,
                name=self._fresh("unsafe.setenv"),
            )
        if intrinsic == "unsetenv":
            self._unsafe_expect_arity(intrinsic, expr, 1)
            unsetenv_fn = self._declare_external_function(
                "unsetenv",
                _I32,
                [_CSTR],
            )
            raw = self.builder.call(
                unsetenv_fn,
                [self._unsafe_ptr_arg(expr.args[0])],
                name=self._fresh("unsafe.unsetenv.i32"),
            )
            return self.builder.sext(
                raw,
                _I64,
                name=self._fresh("unsafe.unsetenv"),
            )
        if intrinsic == "access":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            access_fn = self._declare_external_function(
                "access",
                _I32,
                [_CSTR, _I32],
            )
            raw = self.builder.call(
                access_fn,
                [
                    self._unsafe_ptr_arg(expr.args[0]),
                    self._unsafe_i32_arg(expr.args[1]),
                ],
                name=self._fresh("unsafe.access.i32"),
            )
            return self.builder.sext(
                raw,
                _I64,
                name=self._fresh("unsafe.access"),
            )
        if intrinsic == "stat_kind":
            return self._emit_unsafe_stat_kind(expr)
        if intrinsic == "stat_mtime":
            return self._emit_unsafe_stat_mtime(expr)
        if intrinsic == "call_ptr1":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            fnty = ir.FunctionType(_CSTR, [_CSTR])
            callee = self.builder.bitcast(
                self._unsafe_ptr_arg(expr.args[0]),
                fnty.as_pointer(),
                name=self._fresh("unsafe.call.ptr1.fn"),
            )
            return self.builder.call(
                callee,
                [self._unsafe_ptr_arg(expr.args[1])],
                name=self._fresh("unsafe.call.ptr1"),
            )
        if intrinsic == "call_void_ptr1":
            self._unsafe_expect_arity(intrinsic, expr, 2)
            fnty = ir.FunctionType(ir.VoidType(), [_CSTR])
            callee = self.builder.bitcast(
                self._unsafe_ptr_arg(expr.args[0]),
                fnty.as_pointer(),
                name=self._fresh("unsafe.call.void.ptr1.fn"),
            )
            return self.builder.call(
                callee,
                [self._unsafe_ptr_arg(expr.args[1])],
            )
        if intrinsic == "call_ptr2":
            self._unsafe_expect_arity(intrinsic, expr, 3)
            fnty = ir.FunctionType(_CSTR, [_CSTR, _CSTR])
            callee = self.builder.bitcast(
                self._unsafe_ptr_arg(expr.args[0]),
                fnty.as_pointer(),
                name=self._fresh("unsafe.call.ptr2.fn"),
            )
            return self.builder.call(
                callee,
                [
                    self._unsafe_ptr_arg(expr.args[1]),
                    self._unsafe_ptr_arg(expr.args[2]),
                ],
                name=self._fresh("unsafe.call.ptr2"),
            )
        raise NotImplementedError(f"unknown pcc.unsafe intrinsic {intrinsic!r}")
