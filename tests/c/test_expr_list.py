import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestExprList(unittest.TestCase):
    def test_comma_in_for(self):
        """Comma operator in for-loop init and next."""
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int i = 0;
                int sum = 0;
                for(i = 0; i < 5; i++){
                    sum += i;
                }
                return sum;
            }
            ''', llvmdump=True)
        assert ret == 10


if __name__ == '__main__':
    unittest.main()
