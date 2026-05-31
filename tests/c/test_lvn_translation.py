from pcc.ast import c_ast
from pcc.parse.c_parser import CParser
from pcc.passes import PassContext, PassPipeline
from pcc.passes.propagation import LocalValueNumberingPass


def _transformed_function(code: str):
    ast = CParser().parse(code)
    ctx = PassContext()
    PassPipeline.minimal().run_high_tier(ast, ctx)
    transformed = LocalValueNumberingPass().run(ast, ctx) or ast
    return next(ext for ext in transformed.ext if isinstance(ext, c_ast.FuncDef))


def test_lvn_reuses_prior_decl_expression_within_same_block():
    func = _transformed_function(
        """
        int f(int x, int y) {
            int a = x + y;
            int b = x + y;
            return b;
        }
        """
    )

    second_decl = func.body.block_items[1]
    assert isinstance(second_decl, c_ast.Decl)
    assert isinstance(second_decl.init, c_ast.ID)
    assert second_decl.init.name == "a"


def test_lvn_invalidates_when_operand_changes():
    func = _transformed_function(
        """
        int f(int x, int y) {
            int a = x + y;
            x = 3;
            int b = x + y;
            return b;
        }
        """
    )

    third_stmt = func.body.block_items[2]
    assert isinstance(third_stmt, c_ast.Decl)
    assert isinstance(third_stmt.init, c_ast.BinaryOp)
    assert third_stmt.init.op == "+"


def test_lvn_reuses_prior_assignment_expression():
    func = _transformed_function(
        """
        int f(int x, int y) {
            int a = 0;
            int b = 0;
            a = x + y;
            b = x + y;
            return b;
        }
        """
    )

    fourth_stmt = func.body.block_items[3]
    assert isinstance(fourth_stmt, c_ast.Assignment)
    assert isinstance(fourth_stmt.rvalue, c_ast.ID)
    assert fourth_stmt.rvalue.name == "a"


def test_lvn_does_not_cache_pointer_dereference_across_store():
    func = _transformed_function(
        """
        int f(int *p) {
            int x;
            int y;
            x = *p;
            *p = 0;
            y = *p;
            return x != y;
        }
        """
    )

    fifth_stmt = func.body.block_items[4]
    assert isinstance(fifth_stmt, c_ast.Assignment)
    assert isinstance(fifth_stmt.rvalue, c_ast.UnaryOp)
    assert fifth_stmt.rvalue.op == "*"


def test_lvn_invalidates_bindings_when_loop_body_mutates_dependency():
    func = _transformed_function(
        """
        int f(int *arr, int n) {
            int *p = arr;
            int *first = p;
            for (int i = 0; i < n; i++) p++;
            int *second = p;
            return first == second;
        }
        """
    )

    fourth_stmt = func.body.block_items[3]
    assert isinstance(fourth_stmt, c_ast.Decl)
    assert isinstance(fourth_stmt.init, c_ast.ID)
    assert fourth_stmt.init.name == "p"


def test_lvn_does_not_reuse_values_across_different_declared_types():
    func = _transformed_function(
        """
        typedef int T0;
        typedef long T1;
        int f(void *p) {
            T0 *p0 = p;
            T1 *p1 = p;
            return p0 == (void *) p1;
        }
        """
    )

    second_decl = func.body.block_items[1]
    assert isinstance(second_decl, c_ast.Decl)
    assert isinstance(second_decl.init, c_ast.ID)
    assert second_decl.init.name == "p"


def test_lvn_does_not_reuse_integer_constant_across_different_types():
    func = _transformed_function(
        """
        int f(void) {
            int a = 1;
            long b = 1;
            return a + (int) b;
        }
        """
    )

    second_decl = func.body.block_items[1]
    assert isinstance(second_decl, c_ast.Decl)
    assert isinstance(second_decl.init, c_ast.Constant)
    assert second_decl.init.value == "1"
