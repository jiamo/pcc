from pcc.llvm_capi.compat import ir
from pcc.py_frontend import py_ast as pa
from pcc.py_frontend.codegen.expr_helper_lowering import ExprHelperLoweringMixin


class _NoSextBuilder:
    def sext(self, value, target_ty, name=""):
        raise AssertionError("i64 value should not be sign-extended to i64")


class _FakeCodegen(ExprHelperLoweringMixin):
    def __init__(self):
        self.builder = _NoSextBuilder()
        self.module = None
        self.runtime = {}

    def _emit_expr(self, expr):
        # Deliberately allocate a fresh i64 type object. Self-hosted pcc stages
        # can see this as shape-equal but not identity-equal to the module-level
        # _I64 constant inside expr_helper_lowering.py.
        return ir.Constant(ir.IntType(64), 7)

    def _ir_type_matches(self, actual, expected):
        if actual is expected:
            return True
        if isinstance(actual, ir.IntType) and isinstance(expected, ir.IntType):
            return actual.width == expected.width
        return False


def test_emit_expr_as_i64_accepts_shape_equal_i64_type():
    expr = pa.Name(
        span=pa.SourceSpan("<test>", 1, 1, 1, 1),
        ty=pa.IntType("int", 64, True),
        ident="value",
    )

    out = _FakeCodegen()._emit_expr_as_i64(expr)

    assert isinstance(out.type, ir.IntType)
    assert out.type.width == 64
