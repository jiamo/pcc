import sys
import os
this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator
import unittest

class TestStruct(unittest.TestCase):

    def test_struct(self):
        # Evaluate some code.
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            struct {
                int x;
                int y;
            } Point;

            int main() {
                struct Point p;
                p.x = 4;
                p.y = 4;
                int distance_squared = p.x * p.x + p.y * p.y;
                return distance_squared;
            }
            ''', llvmdump=True)

        assert (ret == 32)

    def test_incomplete_struct_member_access_errors(self):
        pcc = CEvaluator()
        with self.assertRaisesRegex(ValueError, "incomplete struct"):
            pcc.evaluate(
                '''
                struct Opaque;

                int read_x(struct Opaque *p) {
                    return p->x;
                }
                '''
            )

if __name__ == '__main__':
    unittest.main()
