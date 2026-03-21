import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestDoWhile(unittest.TestCase):
    def test_do_while_basic(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int i = 0;
                int sum = 0;
                do {
                    sum += i;
                    i++;
                } while(i < 5);
                return sum;
            }
            ''', llvmdump=True)
        assert ret == 10  # 0+1+2+3+4

    def test_do_while_runs_once(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int x = 0;
                do {
                    x = 42;
                } while(0);
                return x;
            }
            ''', llvmdump=True)
        assert ret == 42

    def test_do_while_with_break(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int i = 0;
                do {
                    if (i == 3) {
                        break;
                    }
                    i++;
                } while(i < 10);
                return i;
            }
            ''', llvmdump=True)
        assert ret == 3


if __name__ == '__main__':
    unittest.main()
