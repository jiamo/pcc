"""Tests for the sparse constant lattice."""

import unittest

from pcc.ir_passes.constant_lattice import (
    LatticeValue,
    evaluate_binary,
    evaluate_compare,
    meet,
)


def const(v: int, w: int = 32) -> LatticeValue:
    return LatticeValue.const(v, w)


class MeetTests(unittest.TestCase):
    def test_top_absorbs_nothing(self):
        self.assertEqual(meet(LatticeValue.top(), const(5)), const(5))
        self.assertEqual(meet(const(5), LatticeValue.top()), const(5))

    def test_overdefined_absorbs_all(self):
        self.assertTrue(meet(LatticeValue.overdefined(), const(5)).is_overdefined())
        self.assertTrue(meet(const(5), LatticeValue.overdefined()).is_overdefined())

    def test_same_const_stays(self):
        self.assertEqual(meet(const(5), const(5)), const(5))

    def test_different_consts_drop_to_overdefined(self):
        self.assertTrue(meet(const(5), const(6)).is_overdefined())


class BinaryTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(evaluate_binary("add", const(2), const(3)).constant, 5)

    def test_add_wraps(self):
        # 0xFFFFFFFF + 1 overflows to 0 in 32-bit.
        v = evaluate_binary("add", const(0xFFFFFFFF), const(1))
        self.assertEqual(v.constant, 0)

    def test_sdiv_sign(self):
        v = evaluate_binary("sdiv", const(-10), const(3))
        # -10 / 3 = -3 (truncation toward zero)
        w = v.bit_width or 32
        from pcc.ir_passes.constant_lattice import _to_signed
        self.assertEqual(_to_signed(v.constant, w), -3)

    def test_udiv_by_zero_becomes_overdefined(self):
        v = evaluate_binary("udiv", const(10), const(0))
        self.assertTrue(v.is_overdefined())

    def test_and_or_xor(self):
        self.assertEqual(evaluate_binary("and", const(0xF0), const(0x0F)).constant, 0)
        self.assertEqual(evaluate_binary("or", const(0xF0), const(0x0F)).constant, 0xFF)
        self.assertEqual(evaluate_binary("xor", const(0xFF), const(0x0F)).constant, 0xF0)

    def test_shifts(self):
        self.assertEqual(evaluate_binary("shl", const(1), const(4)).constant, 16)
        self.assertEqual(evaluate_binary("lshr", const(16), const(2)).constant, 4)
        from pcc.ir_passes.constant_lattice import _to_signed
        v = evaluate_binary("ashr", const(0xFFFFFFFF), const(1))
        self.assertEqual(_to_signed(v.constant, 32), -1)

    def test_top_propagates(self):
        self.assertTrue(evaluate_binary("add", LatticeValue.top(), const(1)).is_top())

    def test_overdefined_propagates(self):
        self.assertTrue(
            evaluate_binary("add", LatticeValue.overdefined(), const(1)).is_overdefined()
        )


class CompareTests(unittest.TestCase):
    def test_eq_ne(self):
        self.assertEqual(evaluate_compare("eq", const(5), const(5)).constant, 1)
        self.assertEqual(evaluate_compare("eq", const(5), const(6)).constant, 0)
        self.assertEqual(evaluate_compare("ne", const(5), const(6)).constant, 1)

    def test_unsigned_vs_signed(self):
        # 0xFFFFFFFF as unsigned is large, as signed is -1
        self.assertEqual(
            evaluate_compare("ult", const(1), const(0xFFFFFFFF)).constant, 1
        )
        self.assertEqual(
            evaluate_compare("slt", const(1), const(0xFFFFFFFF)).constant, 0
        )
        self.assertEqual(
            evaluate_compare("sgt", const(1), const(0xFFFFFFFF)).constant, 1
        )

    def test_compare_is_one_bit_result(self):
        v = evaluate_compare("eq", const(5), const(5))
        self.assertEqual(v.bit_width, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
