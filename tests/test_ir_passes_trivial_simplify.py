"""Parity tests for TrivialArithIdentitiesPass.

Purpose: satisfy the Phase 1 exit criterion in
``docs/plans/all-pass-llvm-ir-1to1-master-plan.md`` — "one simple
local rewrite pass can run end-to-end under the new framework and be
checked against upstream."

Upstream reference:
- ``/tmp/llvm-src/llvm-20.1.8.src/lib/Analysis/InstructionSimplify.cpp``

Each case exercises an identity that upstream ``opt -passes=instsimplify``
handles. We assert that our pass produces a module with the same
opcode histogram / CFG shape as upstream for this subset. The pass is
a documented subset, so full textual equality is not required.
"""

import shutil
import unittest

from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass
from pcc.ir_passes.trivial_simplify import (
    TrivialArithIdentitiesPass,
    _simplify_ir_text,
)


_OPT_AVAILABLE = shutil.which("opt") is not None


_ADD_ZERO_IR = """
define i32 @f(i32 %x) {
entry:
  %1 = add i32 %x, 0
  ret i32 %1
}
"""


_MUL_ONE_IR = """
define i32 @f(i32 %x) {
entry:
  %1 = mul i32 %x, 1
  ret i32 %1
}
"""


_AND_ALL_ONES_IR = """
define i32 @f(i32 %x) {
entry:
  %1 = and i32 %x, -1
  ret i32 %1
}
"""


_SHIFT_ZERO_IR = """
define i32 @f(i32 %x) {
entry:
  %1 = shl i32 %x, 0
  %2 = lshr i32 %1, 0
  %3 = ashr i32 %2, 0
  ret i32 %3
}
"""


_CHAIN_IR = """
define i32 @f(i32 %x) {
entry:
  %a = add i32 %x, 0
  %b = add i32 %a, 0
  %c = add i32 %b, 0
  ret i32 %c
}
"""


_NO_REWRITE_IR = """
define i32 @f(i32 %x, i32 %y) {
entry:
  %1 = add i32 %x, %y
  ret i32 %1
}
"""


class SimplifyKernelTests(unittest.TestCase):
    def test_add_zero_is_folded(self):
        out, changed = _simplify_ir_text(_ADD_ZERO_IR)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)
        self.assertIn("ret i32 %x", out)

    def test_mul_one_is_folded(self):
        out, changed = _simplify_ir_text(_MUL_ONE_IR)
        self.assertTrue(changed)
        self.assertNotIn("mul i32", out)

    def test_and_neg_one_is_folded(self):
        out, changed = _simplify_ir_text(_AND_ALL_ONES_IR)
        self.assertTrue(changed)
        self.assertNotIn("and i32", out)

    def test_zero_shifts_are_folded(self):
        out, changed = _simplify_ir_text(_SHIFT_ZERO_IR)
        self.assertTrue(changed)
        self.assertNotIn("shl ", out)
        self.assertNotIn("lshr ", out)
        self.assertNotIn("ashr ", out)

    def test_identity_chain_collapses_fully(self):
        out, changed = _simplify_ir_text(_CHAIN_IR)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)
        self.assertIn("ret i32 %x", out)

    def test_no_rewrite_leaves_ir_unchanged(self):
        out, changed = _simplify_ir_text(_NO_REWRITE_IR)
        self.assertFalse(changed)
        self.assertEqual(out, _NO_REWRITE_IR)


class RunPassTests(unittest.TestCase):
    def test_pass_produces_rewritten_ir_on_change(self):
        p = TrivialArithIdentitiesPass()
        out, _ = run_pcc_ir_pass(_ADD_ZERO_IR, p)
        self.assertNotIn("add i32", out)
        self.assertIsNotNone(p.rewritten_ir)

    def test_pass_leaves_attribute_none_when_no_change(self):
        p = TrivialArithIdentitiesPass()
        out, _ = run_pcc_ir_pass(_NO_REWRITE_IR, p)
        self.assertIsNone(p.rewritten_ir)
        # Structure should be preserved.
        self.assertIn("add i32", out)


@unittest.skipUnless(_OPT_AVAILABLE, "requires LLVM 'opt' on PATH")
class UpstreamParityTests(unittest.TestCase):
    """Parity against ``opt -passes=instsimplify`` on the subset we cover."""

    def _assert_parity(self, ir_text: str) -> None:
        report = assert_ir_parity(
            ir_text, TrivialArithIdentitiesPass(), "instsimplify"
        )
        self.assertTrue(
            report.is_equivalent,
            f"pcc and opt disagree on:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )

    def test_add_zero_matches_instsimplify(self):
        self._assert_parity(_ADD_ZERO_IR)

    def test_mul_one_matches_instsimplify(self):
        self._assert_parity(_MUL_ONE_IR)

    def test_and_all_ones_matches_instsimplify(self):
        self._assert_parity(_AND_ALL_ONES_IR)

    def test_shift_zero_matches_instsimplify(self):
        self._assert_parity(_SHIFT_ZERO_IR)

    def test_identity_chain_matches_instsimplify(self):
        self._assert_parity(_CHAIN_IR)

    def test_no_rewrite_matches_instsimplify(self):
        # Even where nothing changes, the harness should report equivalent.
        self._assert_parity(_NO_REWRITE_IR)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
