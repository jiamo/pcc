import os
import sys

this_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(this_dir)
sys.path.insert(0, parent_dir)

from pcc.evaluater.c_evaluator import CEvaluator


def _evaluate(source):
    return CEvaluator().evaluate(source, optimize=False)


def test_unsigned_char_return_is_zero_extended():
    source = r"""
        typedef unsigned char lu_byte;

        lu_byte f(void) { return 200; }

        int main(void) {
            int x = f();
            return x == 200 ? 0 : x;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_loads_from_struct_array_and_pointer():
    source = r"""
        typedef unsigned char lu_byte;

        struct S { lu_byte x; };

        int main(void) {
            struct S s;
            lu_byte a[1];
            lu_byte *p = &s.x;
            int sx, ax, px;

            s.x = 200;
            a[0] = 201;

            sx = s.x;
            ax = a[0];
            px = *p;

            return (sx == 200 && ax == 201 && px == 200) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_cast_to_unsigned_char_pointer_keeps_unsigned_load():
    source = r"""
        #define cast(t, exp) ((t)(exp))
        typedef unsigned char lu_byte;

        int main(void) {
            unsigned long raw = 0;
            lu_byte *tag = cast(lu_byte*, &raw);
            int x;

            *tag = 200;
            x = *cast(lu_byte*, &raw);

            return x == 200 ? 0 : x;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_promotes_to_signed_int_for_compare():
    source = r"""
        typedef unsigned char lu_byte;

        int ge(int reg, lu_byte nvarstack) {
            return reg >= nvarstack;
        }

        int main(void) {
            return ge(-1, 1) == 0 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_int_converts_to_signed_long_when_long_can_hold_it():
    source = r"""
        int main(void) {
            unsigned int u = 1U;
            long x = -2;
            return (x < u) ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0


def test_size_t_still_uses_unsigned_comparison_at_same_rank():
    source = r"""
        #include <stddef.h>

        int main(void) {
            size_t u = 1;
            long x = -2;
            return (x < u) ? 1 : 0;
        }
    """

    assert _evaluate(source) == 0


def test_unsigned_char_pointer_arithmetic_uses_positive_offset():
    source = r"""
        typedef unsigned char lu_byte;

        int main(void) {
            int a[256];
            int i;
            lu_byte idx = 200;
            int *p = a;

            for (i = 0; i < 256; i++) {
                a[i] = i;
            }

            return *(p + idx) == 200 ? 0 : 1;
        }
    """

    assert _evaluate(source) == 0
