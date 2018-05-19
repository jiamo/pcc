import sys
import  os
this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator


import unittest


class TestReturnArray(unittest.TestCase):


    # def _assert_body(self, toplevel, expected):
    #     """Assert the flattened body of the given toplevel function"""
    #     self.assertIsInstance(toplevel, FunctionAST)
    #     self.assertEqual(self._flatten(toplevel.body), expected)

    def test_array(self):
        # Evaluate some code.
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int i = 1;
                int j = 1;
                int a[100];
                int len = 100;
                int len2 = 10;
                int sum =  0 ;

                for(i = 0; i < len ; i++){
                    a[i] = i + 1;
                }

                for(i = 0; i < len ; i++){
                    sum +=  a[i];
                }

                return sum ;
            }
            ''', llvmdump=True)

        print("The answer is %d"%ret)
        assert (ret == 5050)

if __name__ == '__main__':
    unittest.main()

