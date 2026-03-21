import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.codegen.c_codegen import LLVMCodeGenerator
from pcc.parse.c_parser import CParser
import unittest


class TestChar(unittest.TestCase):
    def test_char_constant(self):
        """Test char constant codegen produces i8."""
        cg = LLVMCodeGenerator()
        p = CParser()
        ast = p.parse('''
            int main(){
                char c = 'A';
                return 0;
            }
        ''')
        cg.generate_code(ast)
        ir_str = str(cg.module)
        # Should contain i8 type for char
        assert 'i8' in ir_str

    def test_char_escape_constant(self):
        """Test char constant with escape like '\\n'."""
        cg = LLVMCodeGenerator()
        p = CParser()
        ast = p.parse(r'''
            int main(){
                char c = '\n';
                return 0;
            }
        ''')
        cg.generate_code(ast)
        ir_str = str(cg.module)
        assert 'i8' in ir_str


if __name__ == '__main__':
    unittest.main()
