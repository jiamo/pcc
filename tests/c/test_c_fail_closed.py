import pytest

from pcc.codegen.c_codegen import LLVMCodeGenerator, SemanticError
from pcc.parse.c_parser import CParser


def _generate(source: str) -> str:
    generator = LLVMCodeGenerator()
    generator.generate_code(CParser().parse(source))
    return str(generator.module)


def test_static_assert_rejects_nonconstant_condition_instead_of_disappearing():
    source = """
        int runtime_value(void) { return 1; }
        _Static_assert(runtime_value(), "must be constant");
        int main(void) { return 0; }
    """

    with pytest.raises(SemanticError, match="_Static_assert condition"):
        _generate(source)


def test_global_array_rejects_nonconstant_element_instead_of_zero_filling():
    source = """
        int runtime_value(void) { return 1; }
        static int values[1] = {runtime_value()};
        int main(void) { return values[0]; }
    """

    with pytest.raises(ValueError, match="compile-time constant"):
        _generate(source)
