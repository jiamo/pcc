import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestSimpleFunc(unittest.TestCase):
    def test_simple(self):
        pcc = CEvaluator()

        ret = pcc.evaluate('''
            int f(int x){
                if (x < 1){
                    return 1;
                }
                else {
                    return x * f(x - 1);
                }
            }
            
            int main(){
                int a = 5;
                int b;
                b = f(a);
                return b ;
            }
            ''', llvmdump=True)

        print("The answer is %d" % ret)

if __name__ == '__main__':
    # Evaluate some code.
    unittest.main()