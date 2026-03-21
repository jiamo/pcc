import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestNamedStruct(unittest.TestCase):
    def test_named_struct_reuse(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                struct Point { int x; int y; };
                struct Point p;
                p.x = 3;
                p.y = 4;
                return p.x + p.y;
            }
        ''')
        assert ret == 7

    def test_two_instances(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                struct Vec { int a; int b; };
                struct Vec v1;
                struct Vec v2;
                v1.a = 10;
                v2.a = 20;
                return v1.a + v2.a;
            }
        ''')
        assert ret == 30


if __name__ == '__main__':
    unittest.main()
