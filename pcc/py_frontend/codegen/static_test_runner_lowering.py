"""Static pcc/pytest test-runner lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.llvm_capi.compat import ir

from ..py_ast import (
    Assign,
    Attr,
    AugAssign,
    Call,
    ClassDef,
    DynType,
    ExceptHandler,
    Expr,
    ExprStmt,
    FuncDef,
    FuncType,
    IntLit,
    IntType,
    ListExpr,
    Name,
    NoneType,
    SourceSpan,
    Stmt,
    Try,
    TupleExpr,
    Type,
)


_I32 = ir.IntType(32)
_I64 = ir.IntType(64)


class StaticTestRunnerLoweringMixin:
    def _decorator_matches_any(self, dec: Expr, names: tuple[str, ...]) -> bool:
        qn = self._decorator_qualname(dec)
        return qn in names

    def _is_fixture_func(self, fd: FuncDef) -> bool:
        for dec in self._func_decorators(fd):
            if self._decorator_matches_any(
                dec,
                (
                    "fixture",
                    "pytest.fixture",
                    "pcc.test_runner.fixture",
                ),
            ):
                return True
        return False

    def _fixture_funcdefs(self) -> dict[str, FuncDef]:
        fixtures: dict[str, FuncDef] = {}
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef) and self._is_fixture_func(stmt):
                fixtures[stmt.name] = stmt
        return fixtures

    def _top_level_funcdef(self, name: str) -> Optional[FuncDef]:
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef) and stmt.name == name:
                return stmt
        return None

    def _parametrize_rows(self, fd: FuncDef) -> tuple[tuple[Expr, ...], ...]:
        rows_expr: Optional[Expr] = None
        for dec in self._func_decorators(fd):
            if not isinstance(dec, Call):
                continue
            qn = self._decorator_qualname(dec)
            if qn in ("parametrize", "pcc.test_runner.parametrize"):
                if len(dec.args) >= 1:
                    rows_expr = dec.args[0]
                    break
            elif qn == "pytest.mark.parametrize":
                if len(dec.args) >= 2:
                    rows_expr = dec.args[1]
                    break
        if rows_expr is None:
            return ()
        if not isinstance(rows_expr, (ListExpr, TupleExpr)):
            raise NotImplementedError(
                "pcc pytest parametrize requires a literal list/tuple of rows"
            )
        rows: list[tuple[Expr, ...]] = []
        for row in rows_expr.elems:
            if isinstance(row, (ListExpr, TupleExpr)):
                rows.append(tuple(row.elems))
            else:
                rows.append((row,))
        return tuple(rows)

    def _func_type_for_def(self, fd: FuncDef) -> FuncType:
        params = tuple(
            arg.annotation if isinstance(arg.annotation, Type) else DynType(name="dyn")
            for arg in fd.args
            if arg.name != ""
        )
        ret_ty = fd.return_ty if isinstance(fd.return_ty, Type) else NoneType(name="None")
        return FuncType(name="callable", params=params, ret=ret_ty)

    def _call_expr_for_funcdef(
        self,
        fd: FuncDef,
        args: tuple[Expr, ...],
        span: SourceSpan,
    ) -> Call:
        ret_ty = fd.return_ty if isinstance(fd.return_ty, Type) else NoneType(name="None")
        return Call(
            span=span,
            ty=ret_ty,
            func=Name(span=span, ty=self._func_type_for_def(fd), ident=fd.name),
            args=args,
            kwargs=(),
        )

    def _fixture_arg_exprs(
        self,
        fd: FuncDef,
        fixtures: dict[str, FuncDef],
        span: SourceSpan,
    ) -> tuple[Expr, ...]:
        args: list[Expr] = []
        for arg in fd.args:
            if arg.name == "":
                continue
            fixture_fd = fixtures.get(arg.name)
            if fixture_fd is None:
                raise NotImplementedError(
                    f"pcc pytest fixture {arg.name!r} is not defined"
                )
            args.append(self._call_expr_for_funcdef(fixture_fd, (), span))
        return tuple(args)

    def _static_test_call_exprs(
        self,
        fd: FuncDef,
        fixtures: dict[str, FuncDef],
        span: SourceSpan,
    ) -> tuple[Call, ...]:
        rows = self._parametrize_rows(fd)
        if rows:
            return tuple(self._call_expr_for_funcdef(fd, row, span) for row in rows)
        return (
            self._call_expr_for_funcdef(
                fd,
                self._fixture_arg_exprs(fd, fixtures, span),
                span,
            ),
        )

    def _emit_static_test_runner(
        self,
        funcs: tuple[FuncDef, ...],
        span: SourceSpan,
        *,
        exit_on_failure: bool,
    ) -> None:
        if not funcs:
            return
        fixtures = self._fixture_funcdefs()
        calls: list[Call] = []
        for fd in funcs:
            calls.extend(self._static_test_call_exprs(fd, fixtures, span))
        self._emit_static_test_runner_calls(
            tuple(calls),
            span,
            exit_on_failure=exit_on_failure,
        )

    def _emit_static_test_runner_calls(
        self,
        calls: tuple[Call, ...],
        span: SourceSpan,
        *,
        exit_on_failure: bool,
    ) -> None:
        if not calls:
            return
        int_ty = IntType(name="int")
        suffix = self._fresh("pytest_runner").replace(".", "_")
        passed_name = f"__pcc_{suffix}_passed"
        failed_name = f"__pcc_{suffix}_failed"
        passed_ref = Name(span=span, ty=int_ty, ident=passed_name)
        failed_ref = Name(span=span, ty=int_ty, ident=failed_name)
        one = IntLit(span=span, ty=int_ty, value=1)

        stmts: list[Stmt] = [
            Assign(
                span=span,
                targets=(passed_ref,),
                value=IntLit(span=span, ty=int_ty, value=0),
                annotation=int_ty,
            ),
            Assign(
                span=span,
                targets=(failed_ref,),
                value=IntLit(span=span, ty=int_ty, value=0),
                annotation=int_ty,
            ),
        ]
        for call in calls:
            stmts.append(
                Try(
                    span=span,
                    body=(
                        ExprStmt(span=span, expr=call),
                        AugAssign(
                            span=span,
                            target=passed_ref,
                            op="+=",
                            value=one,
                        ),
                    ),
                    handlers=(
                        ExceptHandler(
                            span=span,
                            exc_type=Name(
                                span=span,
                                ty=DynType(name="dyn"),
                                ident="AssertionError",
                            ),
                            name=None,
                            body=(
                                AugAssign(
                                    span=span,
                                    target=failed_ref,
                                    op="+=",
                                    value=one,
                                ),
                            ),
                        ),
                    ),
                    else_body=(),
                    finally_body=(),
                )
            )

        self._emit_stmts(tuple(stmts))
        passed = self._emit_expr_as_i64(passed_ref)
        failed = self._emit_expr_as_i64(failed_ref)
        fmt = self._ptr_to_cstr(
            self._cstr_global("%ld passed, %ld failed\n", ".fmt_pcc_pytest")
        )
        self.builder.call(self._printf, [fmt, passed, failed])

        if exit_on_failure:
            is_fail = self.builder.icmp_signed(
                "!=",
                failed,
                ir.Constant(_I64, 0),
                name=self._fresh("pytest.failed"),
            )
            fail_bb = self.current_function.append_basic_block(
                name=self._fresh("pytest.exit")
            )
            cont_bb = self.current_function.append_basic_block(
                name=self._fresh("pytest.cont")
            )
            self.builder.cbranch(is_fail, fail_bb, cont_bb)
            self.builder.position_at_end(fail_bb)
            self.builder.call(self.runtime["py_process_exit"], [failed])
            if isinstance(self.current_function.function_type.return_type, ir.IntType):
                self.builder.ret(self.builder.trunc(failed, _I32))
            else:
                self.builder.ret_void()
            self.builder.position_at_end(cont_bb)

    def _run_tests_literal_funcdefs(self, expr: Call) -> Optional[tuple[FuncDef, ...]]:
        if expr.kwargs or len(expr.args) != 1:
            return None
        func = expr.func
        is_run_tests = False
        if isinstance(func, Name) and func.ident == "run_tests":
            is_run_tests = True
        elif isinstance(func, Attr):
            is_run_tests = (
                self._decorator_qualname(func) == "pcc.test_runner.run_tests"
            )
        if not is_run_tests:
            return None
        tests_expr = expr.args[0]
        if not isinstance(tests_expr, (ListExpr, TupleExpr)):
            return None
        funcs: list[FuncDef] = []
        for item in tests_expr.elems:
            if not isinstance(item, Name):
                return None
            fd = self._top_level_funcdef(item.ident)
            if fd is None:
                return None
            funcs.append(fd)
        return tuple(funcs)

    def _is_unittest_main_call(self, expr: Call) -> bool:
        if expr.args or expr.kwargs:
            return False
        func = expr.func
        return (
            isinstance(func, Attr)
            and func.name == "main"
            and isinstance(func.obj, Name)
            and func.obj.ident == "unittest"
        )

    def _is_unittest_testcase_base(self, base: Expr) -> bool:
        if isinstance(base, Name):
            return base.ident == "TestCase"
        return (
            isinstance(base, Attr)
            and base.name == "TestCase"
            and isinstance(base.obj, Name)
            and base.obj.ident == "unittest"
        )

    def _unittest_discovered_case_calls(self, span: SourceSpan) -> tuple[Call, ...]:
        """``ClassName().test_x()`` call exprs for every ``test*`` method on a
        top-level ``unittest.TestCase`` subclass, in source order.

        ``unittest.main()`` discovers tests by reflecting over the running
        ``__main__`` module, which cannot see pcc-native classes; this static
        mirror plays the same role as the ``pytest.main()`` lowering."""
        dyn = DynType(name="dyn")
        calls: list[Call] = []
        for stmt in self.ast_module.body:
            if not isinstance(stmt, ClassDef):
                continue
            if not any(self._is_unittest_testcase_base(b) for b in stmt.bases):
                continue
            for item in stmt.body:
                if not isinstance(item, FuncDef) or not item.name.startswith("test"):
                    continue
                inst = Call(
                    span=span,
                    ty=dyn,
                    func=Name(span=span, ty=dyn, ident=stmt.name),
                    args=(),
                    kwargs=(),
                )
                calls.append(
                    Call(
                        span=span,
                        ty=dyn,
                        func=Attr(span=span, ty=dyn, obj=inst, name=item.name),
                        args=(),
                        kwargs=(),
                    )
                )
        return tuple(calls)

    def _is_pytest_main_call(self, expr: Call) -> bool:
        if expr.kwargs:
            return False
        func = expr.func
        return (
            isinstance(func, Attr)
            and func.name == "main"
            and isinstance(func.obj, Name)
            and func.obj.ident == "pytest"
        )

    def _pytest_discovered_funcdefs(self) -> tuple[FuncDef, ...]:
        funcs: list[FuncDef] = []
        for stmt in self.ast_module.body:
            if isinstance(stmt, FuncDef) and stmt.name.startswith("test_"):
                funcs.append(stmt)
        return tuple(funcs)

    def _maybe_emit_static_test_runner_stmt(self, expr: Call) -> bool:
        funcs = self._run_tests_literal_funcdefs(expr)
        if funcs is not None:
            self._emit_static_test_runner(
                funcs,
                expr.span,
                exit_on_failure=False,
            )
            return True
        if self._is_pytest_main_call(expr):
            self._emit_static_test_runner(
                self._pytest_discovered_funcdefs(),
                expr.span,
                exit_on_failure=True,
            )
            return True
        if self._is_unittest_main_call(expr):
            case_calls = self._unittest_discovered_case_calls(expr.span)
            if case_calls:
                self._emit_static_test_runner_calls(
                    case_calls,
                    expr.span,
                    exit_on_failure=True,
                )
                return True
        return False

    # -- Expression statement -----------------------------------------

