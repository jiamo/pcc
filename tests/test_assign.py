import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator

# TODO  If is complext should finish the basic

import unittest


class TestFor(unittest.TestCase):
    def test_simple(self):
        if __name__ == '__main__':
            # Evaluate some code.
            pcc = CEvaluator()
            ret = pcc.evaluate('''
                int main(){
                    int a = 3;
                    int b = 4;

                    a += b;

                    return a - b ;
                }
                ''', llvmdump=True)

            print("The answer is %d" % ret)


if __name__ == "__main__":
    unittest.main()
