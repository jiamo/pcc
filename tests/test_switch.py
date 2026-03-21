import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestSwitch(unittest.TestCase):
    def test_switch_case(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 2;
                int result = 0;
                switch(x){
                    case 1:
                        result = 10;
                        break;
                    case 2:
                        result = 20;
                        break;
                    case 3:
                        result = 30;
                        break;
                }
                return result;
            }
            """,
            llvmdump=True,
        )
        assert ret == 20

    def test_switch_default(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 99;
                int result = 0;
                switch(x){
                    case 1:
                        result = 10;
                        break;
                    case 2:
                        result = 20;
                        break;
                    default:
                        result = 42;
                        break;
                }
                return result;
            }
            """,
            llvmdump=True,
        )
        assert ret == 42

    def test_switch_first_case(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 1;
                int result = 0;
                switch(x){
                    case 1:
                        result = 100;
                        break;
                    case 2:
                        result = 200;
                        break;
                }
                return result;
            }
            """,
            llvmdump=True,
        )
        assert ret == 100

    def test_switch_grouped_cases_share_body(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 2;
                int result = 0;
                switch(x){
                    case 1:
                    case 2:
                    case 3:
                        result = 42;
                        break;
                    default:
                        result = 99;
                        break;
                }
                return result;
            }
            """,
            llvmdump=True,
        )
        assert ret == 42

    def test_switch_fallthrough(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            """
            int main(){
                int x = 1;
                int result = 0;
                switch(x){
                    case 1:
                        result += 10;
                    case 2:
                        result += 20;
                        break;
                    default:
                        result = 99;
                        break;
                }
                return result;
            }
            """,
            llvmdump=True,
        )
        assert ret == 30

    def test_switch_grouped_escaped_char_cases(self):
        pcc = CEvaluator()
        ret = pcc.evaluate(
            r"""
            int main(){
                int c = '\n';
                switch(c){
                    case '\n':
                    case '\r':
                        return 0;
                    default:
                        return 1;
                }
            }
            """,
            llvmdump=True,
        )
        assert ret == 0


if __name__ == "__main__":
    unittest.main()
