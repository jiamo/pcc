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
        ret = pcc.evaluate('''
            int main(){
                return sizeof(int);
            }
            ''', llvmdump=True)
        assert ret == 8  # our int is i64

    def test_sizeof_double(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                return sizeof(double);
            }
            ''', llvmdump=True)
        assert ret == 8

    def test_sizeof_variable(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int x = 42;
                return sizeof(x);
            }
            ''', llvmdump=True)
        assert ret == 8


if __name__ == '__main__':
    unittest.main()
