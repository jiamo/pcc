import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest


def test_printf(capsys):
    pcc = CEvaluator()

    pcc.evaluate('''
        int main(){
            printf("helloworld");
            return 0;
        }
        ''', llvmdump=True)
    # it seem can't work to catch llvm output
    out, err = capsys.readouterr()
    assert "helloworld" in out
    sys.stdout.write(out)
    sys.stderr.write(err)
