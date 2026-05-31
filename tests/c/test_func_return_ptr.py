import os
import sys
import ctypes

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestFuncReturnPtr(unittest.TestCase):
    def test_simple(self):
        pcc = CEvaluator()

        ret = pcc.evaluate('''
            int* swap(int *x, int *y){
                int tmp;
                tmp = *x;
                *x = *y;
                *y = tmp;
                return x;
            }

            int main(){
                int a = 50;
                int b = 4;
                int *c;
                c = swap(&a, &b);
                return *c - b ;
            }
            ''', llvmdump=True)


        assert ret == -46

