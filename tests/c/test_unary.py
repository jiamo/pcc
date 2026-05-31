import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestUnary(unittest.TestCase):
    def test_negate(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 5;
                return -a;
            }
            ''', llvmdump=True)
        assert ret == -5

    def test_logical_not_zero(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 0;
                return !a;
            }
            ''', llvmdump=True)
        assert ret == 1

    def test_logical_not_nonzero(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 42;
                return !a;
            }
            ''', llvmdump=True)
        assert ret == 0

    def test_bitwise_not(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 0;
                return ~a;
            }
            ''', llvmdump=True)
        assert ret == -1  # ~0 == all bits set == -1 in two's complement


if __name__ == '__main__':
    unittest.main()
