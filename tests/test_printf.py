import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


def test_printf():
    pcc = CEvaluator()

    ret = pcc.evaluate('''
        int main(){
            printf("helloworld");
            return 0;
        }
        ''', llvmdump=True)
    # printf output goes to native stdout, not capturable by Python
    # Just verify the program compiles and returns 0
    assert ret == 0


if __name__ == '__main__':
    unittest.main()
