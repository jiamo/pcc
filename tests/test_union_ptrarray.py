"""Tests for union, typedef pointer, array of pointers, sizeof(char)."""
import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)
from pcc.evaluater.c_evaluator import CEvaluator
import unittest


class TestUnion(unittest.TestCase):
    def test_union_int(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                union { int i; double d; } u;
                u.i = 42;
                return u.i;
            }
        ''', optimize=False)
        assert ret == 42

    def test_union_write_read(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                union { int a; int b; } u;
                u.a = 99;
                return u.b;
            }
        ''', optimize=False)
        assert ret == 99  # same memory


class TestTypedefPointer(unittest.TestCase):
    def test_typedef_intptr(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            typedef int* intptr;
            int main(){
                int x = 42;
                intptr p = &x;
                return *p;
            }
        ''', optimize=False)
        assert ret == 42


class TestArrayOfPointers(unittest.TestCase):
    def test_ptr_array(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('''
            int main(){
                int a = 1; int b = 2; int c = 3;
                int *arr[3];
                arr[0] = &a;
                arr[1] = &b;
                arr[2] = &c;
                return *arr[0] + *arr[1] + *arr[2];
            }
        ''', optimize=False)
        assert ret == 6


class TestSizeofChar(unittest.TestCase):
    def test_sizeof_char(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('int main(){ return sizeof(char); }')
        assert ret == 1

    def test_sizeof_int(self):
        pcc = CEvaluator()
        ret = pcc.evaluate('int main(){ return sizeof(int); }')
        assert ret == 4  # int is i32 (standard C)


if __name__ == '__main__':
    unittest.main()
