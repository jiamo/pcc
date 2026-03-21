"""Advanced feature tests: nested structs, typedef structs, array decay, pointer subtraction."""
import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestNestedStruct(unittest.TestCase):
    def test_nested_struct_access(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                struct {
                    int a;
                    struct { int x; int y; } inner;
                } s;
                s.a = 1;
                s.inner.x = 10;
                s.inner.y = 20;
                return s.a + s.inner.x + s.inner.y;
            }
        ''')
        assert ret == 31


class TestTypedefStruct(unittest.TestCase):
    def test_typedef_struct(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            typedef struct { int x; int y; } Point;
            int main(){
                Point p;
                p.x = 3;
                p.y = 4;
                return p.x + p.y;
            }
        ''')
        assert ret == 7


class TestArrayDecay(unittest.TestCase):
    def test_array_to_pointer_in_call(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            void fill(int *a, int n, int v){
                int i;
                for(i = 0; i < n; i++){
                    *(a + i) = v;
                }
            }
            int main(){
                int a[5] = {0, 0, 0, 0, 0};
                fill(a, 5, 7);
                return a[0] + a[4];
            }
        ''', optimize=False)
        assert ret == 14

    def test_array_sum_via_ptr(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int sum(int *arr, int n){
                int s = 0;
                int i;
                for(i = 0; i < n; i++){
                    s += *(arr + i);
                }
                return s;
            }
            int main(){
                int a[4] = {10, 20, 30, 40};
                return sum(a, 4);
            }
        ''', optimize=False)
        assert ret == 100


class TestPtrSubtraction(unittest.TestCase):
    def test_ptr_minus_ptr(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a[5] = {1, 2, 3, 4, 5};
                int *p = &a[0];
                int *q = &a[3];
                return q - p;
            }
        ''', optimize=False)
        assert ret == 3


class TestEnumBitwise(unittest.TestCase):
    def test_enum_flags(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            enum { READ = 1, WRITE = 2, EXEC = 4 };
            int main(){
                int perms = READ | WRITE | EXEC;
                return perms;
            }
        ''')
        assert ret == 7


class TestArrayLength(unittest.TestCase):
    def test_sizeof_array_div(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a[10];
                return sizeof(a) / sizeof(int);
            }
        ''')
        assert ret == 10


class TestPowerFunction(unittest.TestCase):
    def test_power_2_10(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int power(int base, int exp){
                int r = 1;
                int i;
                for(i = 0; i < exp; i++){
                    r *= base;
                }
                return r;
            }
            int main(){ return power(2, 10); }
        ''')
        assert ret == 1024


if __name__ == '__main__':
    unittest.main()
