import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestStructArrayMember(unittest.TestCase):
    def test_struct_with_array(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                struct { int data[3]; int len; } s;
                s.len = 3;
                s.data[0] = 10;
                s.data[1] = 20;
                s.data[2] = 30;
                return s.data[0] + s.data[1] + s.data[2];
            }
        ''', optimize=False)
        assert ret == 60

    def test_struct_array_loop(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                struct { int v[5]; } s;
                int i;
                for(i = 0; i < 5; i++) s.v[i] = i * i;
                int sum = 0;
                for(i = 0; i < 5; i++) sum += s.v[i];
                return sum;
            }
        ''', optimize=False)
        assert ret == 30  # 0+1+4+9+16


class TestPointerCast(unittest.TestCase):
    def test_cast_int_ptr_to_char_ptr(self):
        """Cast between pointer types."""
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int x = 65;
                char *p = (char*)&x;
                char c = *p;
                int result = c;
                return result;
            }
        ''', optimize=False)
        assert ret == 65  # little-endian: first byte is LSB


class TestXorSwap(unittest.TestCase):
    def test_xor_swap(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 5; int b = 9;
                a ^= b; b ^= a; a ^= b;
                return a * 10 + b;
            }
        ''')
        assert ret == 95


class TestRecursiveFill(unittest.TestCase):
    def test_recursive_array_fill(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            void fill(int *a, int i, int n){
                if(i >= n) return;
                a[i] = i * i;
                fill(a, i + 1, n);
            }
            int main(){
                int a[5];
                fill(a, 0, 5);
                return a[0]+a[1]+a[2]+a[3]+a[4];
            }
        ''', optimize=False)
        assert ret == 30  # 0+1+4+9+16


if __name__ == '__main__':
    unittest.main()
