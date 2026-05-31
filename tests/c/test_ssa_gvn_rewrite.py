from pcc.ast import c_ast
from pcc.parse.c_parser import CParser
from pcc.passes import PassContext
from pcc.passes.ssa_gvn_rewrite import SSAGVNRewritePass


_PARSER = CParser(lex_optimize=True, yacc_debug=False, yacc_optimize=True)


def _run_rewrite(source: str):
    ast = _PARSER.parse(source)
    ctx = PassContext()
    out = SSAGVNRewritePass().run(ast, ctx)
    return ast, out, ctx


def test_ssa_gvn_rewrite_reuses_dominating_value_in_return():
    ast, out, ctx = _run_rewrite(
        """
        int f(int a, int b, int flag) {
            int x = a + b;
            if (flag) {
                return a + b;
            }
            return x;
        }
        """
    )

    assert out is ast
    func = ast.ext[0]
    nested_return = func.body.block_items[1].iftrue.block_items[0]
    assert isinstance(nested_return, c_ast.Return)
    assert isinstance(nested_return.expr, c_ast.ID)
    assert nested_return.expr.name == "x"
    assert ctx.stats["ssa.gvn.redundant_values"] == 1
    assert ctx.stats["ssa_gvn_rewrite.rewrite_return"] == 1


def test_ssa_gvn_rewrite_reuses_dominating_value_in_nested_decl():
    ast, out, ctx = _run_rewrite(
        """
        int f(int a, int b, int flag) {
            int x = a + b;
            if (flag) {
                int y = a + b;
                return y;
            }
            return x;
        }
        """
    )

    assert out is ast
    func = ast.ext[0]
    nested_decl = func.body.block_items[1].iftrue.block_items[0]
    assert isinstance(nested_decl, c_ast.Decl)
    assert isinstance(nested_decl.init, c_ast.ID)
    assert nested_decl.init.name == "x"
    assert ctx.stats["ssa_gvn_rewrite.rewrite_decl"] == 1


def test_ssa_gvn_rewrite_reuses_dominating_value_in_assignment():
    ast, out, ctx = _run_rewrite(
        """
        int f(int a, int b, int flag) {
            int x = a + b;
            int y = 0;
            if (flag) {
                y = a + b;
            }
            return y + x;
        }
        """
    )

    assert out is ast
    func = ast.ext[0]
    nested_assign = func.body.block_items[2].iftrue.block_items[0]
    assert isinstance(nested_assign, c_ast.Assignment)
    assert isinstance(nested_assign.rvalue, c_ast.ID)
    assert nested_assign.rvalue.name == "x"
    assert ctx.stats["ssa_gvn_rewrite.rewrite_assign"] == 1


def test_ssa_gvn_rewrite_skips_when_dominating_variable_no_longer_holds_leader():
    ast, out, ctx = _run_rewrite(
        """
        int f(int a, int b, int flag) {
            int x = a + b;
            x = 0;
            if (flag) {
                return a + b;
            }
            return x;
        }
        """
    )

    assert out is None
    func = ast.ext[0]
    nested_return = func.body.block_items[2].iftrue.block_items[0]
    assert isinstance(nested_return.expr, c_ast.BinaryOp)
    assert "ssa_gvn_rewrite.rewrite_return" not in ctx.stats


def test_ssa_gvn_rewrite_skips_return_when_replacement_type_would_narrow():
    ast, out, ctx = _run_rewrite(
        """
        int f(short a, short b, int flag) {
            short x = a + b;
            if (flag) {
                return a + b;
            }
            return x;
        }
        """
    )

    assert out is None
    func = ast.ext[0]
    nested_return = func.body.block_items[1].iftrue.block_items[0]
    assert isinstance(nested_return.expr, c_ast.BinaryOp)
    assert "ssa_gvn_rewrite.rewrite_return" not in ctx.stats
