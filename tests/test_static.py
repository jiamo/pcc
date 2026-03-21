import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestStaticLocal(unittest.TestCase):
    def test_static_counter(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int counter(){
                static int n = 0;
                n++;
                return n;
            }
            int main(){
                counter();
                counter();
                return counter();
            }
        ''', optimize=False)
        assert ret == 3

    def test_static_accumulator(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int acc(int x){
                static int sum = 0;
                sum += x;
                return sum;
            }
            int main(){
                acc(10);
                acc(20);
                return acc(30);
            }
        ''', optimize=False)
        assert ret == 60

    def test_separate_statics(self):
        """Each function has its own static variable."""
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int fa(){ static int n = 0; n += 1; return n; }
            int fb(){ static int n = 0; n += 10; return n; }
            int main(){
                fa(); fa();
                fb();
                return fa() + fb();
            }
        ''', optimize=False)
        assert ret == 23  # fa returns 3, fb returns 20


if __name__ == '__main__':
    unittest.main()
