import sys
import os
this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator

import unittest

import unittest

class TestCevluatar(unittest.TestCase):


    def test_simple(self):
        pcc = CEvaluator()

        # kalei.evaluate('def binary: 1 (x y) y')
        ret = pcc.evaluate('''
            int add(int x, int y){
                return x + y;
            }
            int main(){
                int a = 3;
                int b = 4;
                return add(a, b);
            }
            ''', llvmdump=True)

        print("The answer is {}".format(ret))
        assert (ret == 7)
        # This is a good point to self start main
        # print(pcc.evaluate('main()'))



if __name__ == '__main__':
    # Evaluate some code
    # if __name__ == '__main__':
    unittest.main()
