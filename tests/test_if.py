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
            int main(){
                int a = 3;
                int b = 4;

                if (b > a){
                    b += 3;
                }
                return b - a ;
            }
            ''', llvmdump=True)

        print("The answer is %d" % ret)


# TODO  If is complext should finish the basic
if __name__ == '__main__':
    # Evaluate some code.
    unittest.main()
    # This is a good point to self start main
    # print(pcc.evaluate('main()'))
