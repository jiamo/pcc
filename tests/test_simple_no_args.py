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
                int func(){
                    int a = 3;
                    int b = 4;

                    if (b > a){
                        b += 3;
                    }
                    return b - a ;
                }

                int main(){
                    return func();
                }
            ''', llvmdump=True)
        assert ret == 4
        print("The answer is %d" % ret)

