import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestIf(unittest.TestCase):
    def test_assignment(self):
        pcc = CEvaluator()

        ret = pcc.evaluate('''
            int main(int c){
                int a = 5;
                int b = 4;
                return b - c ;
            }
            ''', llvmdump=True, args=[7])

        print("The answer is %d" % ret)
        assert ret == -3


# TODO  If is complext should finish the basic
if __name__ == '__main__':
    # Evaluate some code.
    unittest.main()
    # This is a good point to self start main
    # print(pcc.evaluate('main()'))
