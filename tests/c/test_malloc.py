import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


def test_malloc():
    pcc = CEvaluator()

    out = pcc.evaluate('''
        int main(){
            int *a;
            a = malloc(8);
            *a = 4;
            return *a;
        }
        ''', llvmdump=True)
    # it seem can't work to catch llvm output
    assert out == 4;
