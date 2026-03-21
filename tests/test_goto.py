import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestGoto(unittest.TestCase):
    def test_goto_forward(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 0;
                goto skip;
                x = 99;
            skip:
                x = 42;
                return x;
            }
            """,
            llvmdump=True,
        )
        assert ret == 42

    def test_goto_backward(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 0;
            loop:
                x = x + 1;
                if (x < 5) {
                    goto loop;
                }
                return x;
            }
            """,
            llvmdump=True,
        )
        assert ret == 5

    def test_while_with_labels_and_continue_reaches_afterloop(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main() {
                int value = 1;
                int *p = &value;
                int *curr;
                while ((curr = p) != 0) {
                    if (*curr)
                        goto remove;
                    else if (*curr == 2) {
                        goto remain;
                    }
                    else {
                        goto remove;
                    }
                remove:
                    p = 0;
                    continue;
                remain:
                    p = 0;
                    continue;
                }
                return p == 0;
            }
            """,
            llvmdump=True,
        )
        assert ret == 1


if __name__ == "__main__":
    unittest.main()
