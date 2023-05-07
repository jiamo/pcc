import sys

sys.path.insert(0, "../pcc")
from pcc.evaluater.c_evaluator import CEvaluator

import unittest


class TestNestedLoopEvaluation(unittest.TestCase):

    def test_nested_loop_evaluation(self):
        pcc = CEvaluator()

        ret = pcc.evaluate('''
            int main() {
                int outer_sum = 0;
                int inner_sum = 0;
                int i, j;

                for (i = 1; i <= 10; i++) {
                    outer_sum += i;

                    for (j = 1; j <= i; j++) {
                        inner_sum += j;
                        if (j == 5) {
                            i++;
                        }
                    }
                }

                return outer_sum + inner_sum;
            }
            ''', llvmdump=False)

        print("The result is %d" % ret)
        assert (ret == 163)


if __name__ == '__main__':
    unittest.main()