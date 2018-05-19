import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestSimpleFunc(unittest.TestCase):

    def test_init_assign_func_call(self):
        pcc = CEvaluator()

        ret = pcc.evaluate('''
            int f(int x){
                return 4;
            }

            int main(){
                int a = 3;
                int b = f(3);
                if (b > a){
                    b += 3;
                }
                return b - a ;
            }
            ''', llvmdump=True)

        print("The answer is %d" % ret)
