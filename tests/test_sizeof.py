import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestSizeof(unittest.TestCase):
    def test_sizeof_int(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                return sizeof(int);
            }
            """,
            llvmdump=True,
        )
        assert ret == 4  # int is i32 (standard C)

    def test_sizeof_double(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                return sizeof(double);
            }
            """,
            llvmdump=True,
        )
        assert ret == 8

    def test_sizeof_variable(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 42;
                return sizeof(x);
            }
            """,
            llvmdump=True,
        )
        assert ret == 4  # int is i32

    def test_sizeof_struct_pointer_array_field(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            struct S {
                char buf[1024];
            };

            int main(){
                struct S *p = 0;
                return sizeof(p->buf);
            }
            """,
            llvmdump=True,
        )
        assert ret == 1024

    def test_sizeof_union_with_array_member(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            typedef union {
                long long align;
                char buf[16];
            } U;

            int main(){
                return sizeof(U);
            }
            """,
            llvmdump=True,
        )
        assert ret == 16

    def test_typedef_scalar_pointer_uses_underlying_element_type(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            typedef unsigned int U32;

            int main(){
                U32 values[2] = {1u, 2u};
                const U32 *p = values;
                return p[1];
            }
            """,
            llvmdump=True,
        )
        assert ret == 2

    def test_sizeof_string_literal_in_static_const_expr(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                static const unsigned long szloc = sizeof("local") - 1;
                return (int)szloc;
            }
            """,
            llvmdump=True,
        )
        assert ret == 5


if __name__ == "__main__":
    unittest.main()
