"""Parity corpus for SCCPPass (subset)."""

import pytest
import unittest

from pcc.ir_passes.sccp import SCCPPass, sccp_module_text
from pcc.ir_passes.parity import assert_ir_parity, run_pcc_ir_pass


from pcc.passes.llvm_text_pipeline import find_opt_binary

_OPT = find_opt_binary()


class SCCPTests(unittest.TestCase):
    def test_constants_fold_through_add(self):
        ir = """
        define i32 @f() {
        entry:
          %a = add i32 3, 4
          %b = add i32 %a, 5
          ret i32 %b
        }
        """
        out, changed = sccp_module_text(ir)
        self.assertTrue(changed)
        self.assertIn("ret i32 12", out)

    def test_const_icmp(self):
        ir = """
        define i1 @f() {
        entry:
          %r = icmp slt i32 3, 5
          ret i1 %r
        }
        """
        out, _ = sccp_module_text(ir)
        self.assertIn("ret i1 true", out)

    def test_select_cond_becomes_true(self):
        ir = """
        define i32 @f(i32 %x, i32 %y) {
        entry:
          %c = icmp slt i32 3, 5
          %r = select i1 %c, i32 %x, i32 %y
          ret i32 %r
        }
        """
        out, _ = sccp_module_text(ir)
        self.assertIn("select i1 true", out)

    def test_args_stay_overdefined(self):
        ir = """
        define i32 @f(i32 %x) {
        entry:
          %a = add i32 %x, 0
          ret i32 %a
        }
        """
        out, changed = sccp_module_text(ir)
        self.assertFalse(changed)

    def test_pass_integration(self):
        ir = """
        define i32 @f() {
        entry:
          %a = mul i32 2, 3
          ret i32 %a
        }
        """
        p = SCCPPass()
        out, _ = run_pcc_ir_pass(ir, p)
        self.assertIn("ret i32 6", out)


_CORPUS: list[tuple[str, str]] = [
    ("const_add", """
define i32 @f() { entry:
  %a = add i32 10, 20
  ret i32 %a
}
"""),
    ("const_sub", """
define i32 @f() { entry:
  %a = sub i32 100, 30
  ret i32 %a
}
"""),
    ("const_mul", """
define i32 @f() { entry:
  %a = mul i32 6, 7
  ret i32 %a
}
"""),
    ("const_and", """
define i32 @f() { entry:
  %a = and i32 15, 240
  ret i32 %a
}
"""),
    ("const_or", """
define i32 @f() { entry:
  %a = or i32 15, 240
  ret i32 %a
}
"""),
    ("const_xor", """
define i32 @f() { entry:
  %a = xor i32 170, 85
  ret i32 %a
}
"""),
    ("const_shl", """
define i32 @f() { entry:
  %a = shl i32 1, 4
  ret i32 %a
}
"""),
    ("const_lshr", """
define i32 @f() { entry:
  %a = lshr i32 256, 3
  ret i32 %a
}
"""),
    ("const_ashr_neg", """
define i32 @f() { entry:
  %a = ashr i32 -8, 2
  ret i32 %a
}
"""),
    ("const_icmp_slt", """
define i1 @f() { entry:
  %a = icmp slt i32 3, 5
  ret i1 %a
}
"""),
    ("const_icmp_eq_false", """
define i1 @f() { entry:
  %a = icmp eq i32 1, 2
  ret i1 %a
}
"""),
    ("const_icmp_sgt_signed", """
define i1 @f() { entry:
  %a = icmp sgt i32 -1, 1
  ret i1 %a
}
"""),
    ("chain_to_const", """
define i32 @f() { entry:
  %a = add i32 1, 2
  %b = mul i32 %a, 4
  %c = sub i32 %b, 1
  ret i32 %c
}
"""),
    ("arg_keeps_overdefined", """
define i32 @f(i32 %x) { entry:
  %a = add i32 %x, 1
  ret i32 %a
}
"""),
]


@pytest.mark.pcc_gate(unavailable=None if _OPT else "matching LLVM opt not installed")
class CorpusParityTests(unittest.TestCase):
    """SCCP folds constant-valued chains; our output must match
    upstream's on every case in ``_CORPUS``."""

    def _parity(self, ir: str, tag: str) -> None:
        report = assert_ir_parity(ir, SCCPPass(), "sccp")
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
