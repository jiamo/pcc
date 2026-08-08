"""Parity corpus for BDCEPass."""

import pytest
import unittest

from pcc.ir_passes.bdce import BDCEPass, bdce_module_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class BDCETests(unittest.TestCase):
    def test_unused_chain_removed(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          ret i32 %x
        }
        """
        out, changed = bdce_module_text(ir)
        self.assertTrue(changed)
        self.assertNotIn("add i32", out)
        self.assertNotIn("mul i32", out)

    def test_demanded_chain_preserved(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 1
          %b = mul i32 %a, 2
          ret i32 %b
        }
        """
        _, changed = bdce_module_text(ir)
        self.assertFalse(changed)


_CORPUS: list[tuple[str, str]] = [
    ("dead_branch", """
define i32 @f(i32 %x) { entry:
  %dead = add i32 %x, 1
  ret i32 %x
}
"""),
    ("demanded_branch", """
define i32 @f(i32 %x) { entry:
  %a = mul i32 %x, 2
  ret i32 %a
}
"""),
    ("partial_chain_dead", """
define i32 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  %c = sub i32 %b, 3
  ret i32 %x
}
"""),
    ("store_demanded", """
define void @f(ptr %p, i32 %x) { entry:
  %a = add i32 %x, 1
  store i32 %a, ptr %p
  ret void
}
"""),
    ("mixed_demanded_dead", """
define i32 @f(i32 %x) { entry:
  %live = add i32 %x, 1
  %dead = mul i32 %x, 2
  ret i32 %live
}
"""),
]


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class CorpusParityTests(unittest.TestCase):
    def _parity(self, ir: str, tag: str) -> None:
        report = assert_ir_parity(ir, BDCEPass(), "bdce")
        self.assertTrue(
            report.is_equivalent,
            f"[{tag}] mismatch:\n---pcc---\n{report.pcc_ir}\n"
            f"---opt---\n{report.opt_ir}\n---diff---\n{report.diff}",
        )

    def test_each_case_matches_upstream(self):
        for tag, body in _CORPUS:
            with self.subTest(case=tag):
                self._parity(body, tag)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
